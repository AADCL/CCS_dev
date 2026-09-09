#!/usr/bin/env python3
"""Own the exclusive mapping/navigation slot for a CCS-managed GO2 stack."""
import json
import os
import socket
import time
import uuid
import xmlrpc.client

import rosgraph
import rosnode
import rospy
from std_msgs.msg import String


DEFAULT_CONFLICTS = (
    "/laserMapping", "/go2_map_builder", "/go2_map_accumulator",
    "/go2_ndt_localizer", "/go2_localization_guard", "/move_base",
)


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout_seconds):
        super().__init__()
        self.timeout_seconds = timeout_seconds

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout_seconds
        return connection


def process_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def process_parent_pid(pid, proc_root="/proc"):
    stat_path = os.path.join(proc_root, str(int(pid)), "stat")
    with open(stat_path, "r", encoding="utf-8") as stream:
        stat = stream.read().strip()
    command_end = stat.rfind(")")
    fields = stat[command_end + 2:].split() if command_end >= 0 else []
    if len(fields) < 2:
        raise RuntimeError("cannot parse process stat for pid %s" % pid)
    return int(fields[1])


def node_parent_pid(node_name):
    master = rosgraph.Master(rospy.get_name())
    api_uri = rosnode.get_api_uri(master, node_name, skip_cache=True)
    if not api_uri:
        raise RuntimeError("node API is unavailable: %s" % node_name)
    proxy = xmlrpc.client.ServerProxy(
        api_uri, transport=_TimeoutTransport(1.0), allow_none=True)
    code, message, pid = proxy.getPid(rospy.get_name())
    if code != 1:
        raise RuntimeError("cannot read pid for %s: %s" % (node_name, message))
    return process_parent_pid(pid)


def find_conflicts(node_names, owned_sibling_nodes, launch_parent_pid,
                   parent_lookup=node_parent_pid):
    candidates = sorted(set(node_names).intersection(DEFAULT_CONFLICTS))
    owned_siblings = set(owned_sibling_nodes)
    conflicts = []
    for node_name in candidates:
        if node_name in owned_siblings:
            try:
                if parent_lookup(node_name) == launch_parent_pid:
                    continue
            except Exception:
                # PID lookup failures stay fail-closed and preserve exclusivity.
                pass
        conflicts.append(node_name)
    return conflicts


class StackGuard(object):
    def __init__(self):
        self.mode = rospy.get_param("~mode")
        self.lock_file = os.path.abspath(os.path.expanduser(
            rospy.get_param("~lock_file", "/home/unitree/ccs_edge_ws/run/go2_stack.lock")))
        owned_siblings = rospy.get_param("~owned_sibling_nodes", [])
        if (not isinstance(owned_siblings, list)
                or any(not isinstance(name, str) for name in owned_siblings)):
            raise RuntimeError("owned_sibling_nodes must be a list of node names")
        self.owned_sibling_nodes = tuple(owned_siblings)
        self.launch_parent_pid = os.getppid()
        self.token = uuid.uuid4().hex
        self.publisher = rospy.Publisher("/ccs/go2_stack_owner", String, queue_size=1, latch=True)

    def acquire(self):
        if self.mode not in ("mapping", "navigation"):
            raise RuntimeError("mode must be mapping or navigation")
        current_name = rospy.get_name()
        conflicts = find_conflicts(
            rosnode.get_node_names(),
            self.owned_sibling_nodes,
            self.launch_parent_pid,
        )
        if conflicts:
            raise RuntimeError("conflicting GO2 nodes already run: %s" % ", ".join(conflicts))
        os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r", encoding="utf-8") as stream:
                    previous = json.load(stream)
            except (OSError, ValueError):
                previous = {}
            if process_alive(previous.get("pid")):
                raise RuntimeError(
                    "GO2 stack is owned by %s pid=%s" %
                    (previous.get("mode", "unknown"), previous.get("pid", "unknown")))
            os.unlink(self.lock_file)
        record = {
            "schema_version": 1,
            "mode": self.mode,
            "pid": os.getpid(),
            "launch_parent_pid": self.launch_parent_pid,
            "node": current_name,
            "host": socket.gethostname(),
            "token": self.token,
            "started_at": time.time(),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self.lock_file, flags, 0o600)
        try:
            os.write(descriptor, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        rospy.on_shutdown(self.release)
        self.publisher.publish(String(data=self.mode))
        rospy.loginfo("CCS owns GO2 %s stack; lock=%s", self.mode, self.lock_file)

    def release(self):
        try:
            with open(self.lock_file, "r", encoding="utf-8") as stream:
                current = json.load(stream)
            if current.get("token") == self.token:
                os.unlink(self.lock_file)
        except (OSError, ValueError):
            pass


def main():
    rospy.init_node("ccs_go2_stack_guard")
    guard = StackGuard()
    try:
        guard.acquire()
    except Exception as exc:
        rospy.logfatal("CCS GO2 stack guard rejected startup: %s", exc)
        raise SystemExit(2)
    rospy.spin()


if __name__ == "__main__":
    main()
