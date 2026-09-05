#!/usr/bin/env python3
"""Own mapping/relocalization roslaunch process groups inside ccs_edge_ws."""

import os
import signal
import subprocess
import threading
import time

import rosnode
import rospy
import tf2_ros
from ground_air_msgs.srv import SetSystemStage, SetSystemStageResponse
from std_msgs.msg import String, UInt8

from epgeneral_ground_air_control.system_stage_core import (
    BASE,
    MAPPING,
    RELOCALIZATION,
    StageError,
    analyze_active_nodes,
    build_stage_commands,
    normalize_request,
)
from epgeneral_ground_air_control.system_stage_runtime import StageController


_PRIMARY_NODES = {
    MAPPING: {
        "/fast_lio_node",
        "/ground_air_map_recorder",
        "/ground_air_world_tf_owner",
    },
    RELOCALIZATION: {
        "/fast_lio_node",
        "/ground_air_map_manager",
        "/ground_air_global_relocalizer",
        "/ground_air_world_tf_owner",
        "/ground_air_initial_pose_adapter",
    },
}
_STAGE_EDGES = (
    ("odom", "camera_init"),
    ("camera_init", "body"),
    ("body", "base_link"),
)
_CCS_OWNER_PREFIXES = ("/ccs_mapping_stage_", "/ccs_relocalization_stage_")


class RosProcessBackend:
    def __init__(self, tf_buffer, poll_interval=0.1, stop_timeout=8.0):
        self.tf_buffer = tf_buffer
        self.poll_interval = poll_interval
        self.stop_timeout = stop_timeout

    def find_conflicts(self, managed_stage_active):
        try:
            active = set(rosnode.get_node_names())
        except rosnode.ROSNodeIOException as error:
            raise RuntimeError("cannot query ROS nodes: {}".format(error))
        topology = analyze_active_nodes(active)
        conflicts = list(topology.conflicts)
        if not topology.coordinate_transforms_ready:
            conflicts.append("coordinate transform pair is not ready")
        if managed_stage_active:
            return [
                conflict for conflict in conflicts
                if "coordinate transform" in conflict
            ]
        return conflicts

    def start(self, command):
        rospy.loginfo("starting managed stage process: %s", " ".join(command))
        return subprocess.Popen(list(command), start_new_session=True)

    def _wait(self, process, timeout, predicate):
        deadline = time.monotonic() + max(0.1, float(timeout))
        while not rospy.is_shutdown() and time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            try:
                if predicate():
                    return True
            except (rosnode.ROSNodeIOException, tf2_ros.TransformException):
                pass
            time.sleep(self.poll_interval)
        return False

    def wait_primary(self, stage, process, timeout):
        required = _PRIMARY_NODES[stage]
        return self._wait(
            process, min(float(timeout), 30.0),
            lambda: required.issubset(set(rosnode.get_node_names())),
        )

    def wait_stage_transforms(self, stage, process, timeout):
        required_edges = list(_STAGE_EDGES)
        if stage == MAPPING:
            required_edges.insert(0, ("map", "odom"))

        def ready():
            return all(
                self.tf_buffer.can_transform(
                    parent, child, rospy.Time(0), rospy.Duration(0.05)
                )
                for parent, child in required_edges
            )

        return self._wait(process, min(float(timeout), 30.0), ready)

    def stop(self, process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=self.stop_timeout)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                raise RuntimeError(
                    "managed process group {} did not stop cleanly".format(process.pid)
                )


class StageManagerNode:
    def __init__(self):
        self._lock = threading.RLock()
        self._stage_pub = rospy.Publisher(
            "/ground_air/system/stage", UInt8, queue_size=1, latch=True
        )
        self._detail_pub = rospy.Publisher(
            "/ground_air/system/stage_detail", String, queue_size=1, latch=True
        )
        tf_buffer = tf2_ros.Buffer(cache_time=rospy.Duration(10.0))
        self._tf_listener = tf2_ros.TransformListener(tf_buffer)
        self._controller = StageController(RosProcessBackend(tf_buffer))
        self._shutting_down = False
        rospy.set_param("~ccs_session_guard_version", 2)
        rospy.set_param("~external_tf_required", 1)
        self._publish("base stage active; coordinate transforms are externally managed")
        self._service = rospy.Service(
            "/ground_air/system/set_stage", SetSystemStage, self._handle_stage
        )
        self._monitor = rospy.Timer(rospy.Duration(0.5), self._monitor_children)
        rospy.on_shutdown(self.shutdown)

    def _publish(self, detail):
        self._stage_pub.publish(UInt8(data=self._controller.active_stage))
        self._detail_pub.publish(String(data=detail))

    @staticmethod
    def _ccs_owner(request):
        caller = getattr(request, "_connection_header", {}).get("callerid", "")
        return caller if caller.startswith(_CCS_OWNER_PREFIXES) else ""

    def _handle_stage(self, request):
        with self._lock:
            try:
                normalized = normalize_request(
                    request.stage, request.map_id, request.timeout
                )
            except (StageError, TypeError, ValueError) as error:
                message = "stage request rejected: {}".format(error)
                self._publish(message)
                return SetSystemStageResponse(
                    False, message, self._controller.active_stage
                )
            result = self._controller.transition(
                normalized, build_stage_commands(normalized),
                owner=self._ccs_owner(request),
            )
            self._publish(result.message)
            log = rospy.loginfo if result.success else rospy.logerr
            log("system stage request: %s", result.message)
            return SetSystemStageResponse(
                result.success, result.message, result.active_stage
            )

    def _monitor_children(self, _event):
        with self._lock:
            exited = [
                child for child in self._controller.children
                if child.poll() is not None
            ]
            if not exited:
                return
            codes = ", ".join(str(child.returncode) for child in exited)
            self._controller.shutdown()
            message = (
                "managed stage exited unexpectedly (codes: {}); "
                "base stage active".format(codes)
            )
            rospy.logerr(message)
            self._publish(message)

    def shutdown(self):
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self._controller.shutdown()
            self._publish("stage manager stopped; base stage active")


def main():
    rospy.init_node("ground_air_stage_manager")
    StageManagerNode()
    rospy.loginfo("ccs_edge_ws Ground-Air stage manager is ready")
    rospy.spin()


if __name__ == "__main__":
    main()
