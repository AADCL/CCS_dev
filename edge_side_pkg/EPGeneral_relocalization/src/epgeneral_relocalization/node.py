from __future__ import absolute_import

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import socket
import sys
import threading
import tempfile
import time
from datetime import datetime

from .artifacts import ArtifactError, download, install_archive, validate_map_directory
from .config import ConfigError, load_config
from .protocol import Protocol, ProtocolError
from .ros_bridge import RosBridge, RosIntegrationError, StackManager


def build_logger(log_dir=None):
    directory = os.path.expanduser(log_dir or "~/.ros/ccs_edge_dev/log")
    os.makedirs(directory, mode=0o750, exist_ok=True)
    logger = logging.getLogger("epgeneral_relocalization")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            os.path.join(directory, "relocalization.log"), maxBytes=5 * 1024 * 1024,
            backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.addHandler(logging.StreamHandler(sys.stdout))
    return logger


class RelocalizationNode(object):
    def __init__(self, config, rospy_module, logger, socket_factory=socket.socket):
        self.config = config
        self.rospy = rospy_module
        self.logger = logger
        self.protocol = Protocol(config["protocol_id"], config["max_datagram_bytes"])
        self.socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((config["bind_host"], config["control_port"]))
        self.socket.settimeout(0.5)
        self.stack = StackManager(config, logger)
        self.ros = RosBridge(config, rospy_module, logger) if config["enabled"] else None
        self.lock = threading.RLock()
        self.running = False
        self.thread = None
        self.sequence = 0
        self.state = "standby"
        self.identity = None
        self.map_dir = None
        self.response_cache = {}
        self.operation_generation = 0
        self.persisted_state = self._read_active_state()
        if self.persisted_state is not None:
            if not config["enabled"]:
                self._write_active_state(self.persisted_state["map_id"], "unsupported")
            elif self.persisted_state.get("schema_version") == 1:
                self._write_active_state(self.persisted_state["map_id"], "standby")
            elif self.persisted_state.get("status") == "localized":
                self._invalidate_persisted_localization()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, name="relocalization-udp")
        self.thread.daemon = True
        self.thread.start()
        self.rospy.Timer(self.rospy.Duration(1.0), self._heartbeat)
        self.logger.info("relocalization_ready backend=%s enabled=%s port=%s",
                         self.config["backend"], self.config["enabled"], self.config["control_port"])

    def stop(self):
        self.running = False
        try:
            self.socket.close()
        except OSError:
            pass
        if self.ros is not None:
            self.ros.cancel_monitor()
        self.stack.stop()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _invalidate_persisted_localization(self):
        # A persisted TF is not valid proof that the ROS localization stack
        # survived a process or vehicle restart.
        self._write_active_state(self.persisted_state["map_id"], "standby")
        self.persisted_state = self._read_active_state()

    def _receive_loop(self):
        while self.running and not self.rospy.is_shutdown():
            try:
                data, peer = self.socket.recvfrom(self.config["max_datagram_bytes"] + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            if peer[0] != self.config["ground_station_ip"]:
                self.logger.warning("relocalization_source_rejected peer=%s", peer[0])
                continue
            try:
                message = self.protocol.decode(data)
                self._handle(message)
            except ProtocolError as exc:
                self.logger.warning("relocalization_protocol_rejected error=%s", exc)
            except Exception:
                self.logger.exception("relocalization_command_failed")

    def _handle(self, message):
        if message["device_id"] != self.config["device_id"]:
            return
        request_id = message["request_id"]
        cached = self.response_cache.get(request_id)
        if cached is not None:
            self._send(message, cached[0], cached[1])
            return
        kind = message["message_type"]
        if kind == "negotiate":
            self._negotiate(message)
            return
        if self.identity is None or any(
                message[key] != self.identity[key] for key in ("map_id", "device_id", "session_id")):
            self._reply(message, "command_error", {"state": "error", "reason": "SESSION_MISMATCH"})
            return
        if kind == "map_offer":
            self._download_map(message)
        elif kind == "start_stack":
            self._start_stack(message)
        elif kind == "initial_pose":
            self._initial_pose(message)
        else:
            self._reply(message, "command_error", {"state": "error", "reason": "INVALID_COMMAND"})

    def _negotiate(self, message):
        with self.lock:
            new_identity = {key: message[key] for key in ("map_id", "device_id", "session_id")}
            if self.identity is not None and self.identity["session_id"] != message["session_id"]:
                self.operation_generation += 1
                if self.ros is not None:
                    self.ros.cancel_monitor()
                if self.identity["map_id"] != message["map_id"]:
                    self.stack.stop()
                    self.state = "standby"
            self.identity = new_identity
            self.map_dir = os.path.join(os.path.expanduser(self.config["map_root"]), message["map_id"])
            if not self.config["enabled"] or self.config["backend"] != "scout_mini":
                self.state = "standby"
                self._write_active_state(message["map_id"], "unsupported")
                self._reply(message, "negotiation_status", {
                    "state": "unsupported", "reason": "UNSUPPORTED_BACKEND"})
                return
            try:
                validate_map_directory(self.map_dir, message["map_id"])
            except ArtifactError:
                self.state = "map_required"
                self._write_active_state(message["map_id"], "map_required")
                self._reply(message, "negotiation_status", {"state": "map_required"})
            else:
                persisted = self._read_active_state()
                has_persisted_tf = bool(
                    persisted is not None
                    and persisted.get("map_id") == message["map_id"]
                    and persisted.get("status") == "localized"
                    and persisted.get("map_from_odom") is not None
                )
                response_state = "localized" if has_persisted_tf else "map_ready"
                self.state = response_state
                if has_persisted_tf:
                    payload = dict(persisted)
                    payload.pop("schema_version", None)
                    payload["state"] = "localized"
                else:
                    self._write_active_state(message["map_id"], "map_ready")
                    payload = {"state": "map_ready"}
                self._reply(message, "negotiation_status", payload)

    def _write_active_map(self, map_id):
        self._write_active_state(map_id, "standby")

    def _read_active_state(self):
        path = os.path.abspath(os.path.expanduser(self.config["active_map_state_file"]))
        try:
            with open(path, "r") as stream:
                value = json.load(stream)
            if not isinstance(value, dict) or not isinstance(value.get("map_id"), str):
                return None
            if value.get("schema_version") == 1:
                return {"schema_version": 1, "map_id": value["map_id"], "status": "standby"}
            if value.get("schema_version") != 2:
                return None
            transform = value.get("map_from_odom")
            if transform is not None:
                keys = ("x", "y", "z", "qx", "qy", "qz", "qw")
                if not isinstance(transform, dict) or any(key not in transform for key in keys):
                    return None
                transform = dict((key, float(transform[key])) for key in keys)
                if not all(math.isfinite(item) for item in transform.values()):
                    return None
                norm = math.sqrt(sum(transform[key] * transform[key]
                                     for key in ("qx", "qy", "qz", "qw")))
                if abs(norm - 1.0) > 1e-3:
                    return None
                value["map_from_odom"] = transform
            return value
        except (IOError, OSError, TypeError, ValueError):
            return None

    def _write_active_state(self, map_id, status, transform=None, localized_at=None):
        path = os.path.abspath(os.path.expanduser(self.config["active_map_state_file"]))
        directory = os.path.dirname(path)
        os.makedirs(directory, mode=0o750, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", dir=directory, prefix=".relocalization-", suffix=".tmp", delete=False)
        temporary = handle.name
        try:
            payload = {"schema_version": 2, "map_id": map_id, "status": status}
            if transform is not None:
                payload.update({
                    "map_frame": self.config["map_frame"],
                    "odom_frame": self.config["odom_frame"],
                    "localized_at": localized_at or datetime.utcnow().isoformat() + "Z",
                    "map_from_odom": dict(transform),
                })
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temporary, path)
            self.persisted_state = payload
        finally:
            try:
                handle.close()
            except Exception:
                pass
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _download_map(self, message):
        if self.state not in ("map_required", "error"):
            self._reply(message, "command_error", {"state": "error", "reason": "INVALID_STATE"})
            return
        self.state = "downloading"
        self._write_active_state(message["map_id"], "downloading")
        self._reply(message, "download_status", {"state": "downloading"})
        thread = threading.Thread(target=self._download_worker, args=(message,), name="map-download")
        thread.daemon = True
        thread.start()

    def _download_worker(self, message):
        payload = message["payload"]
        root = os.path.abspath(os.path.expanduser(self.config["map_root"]))
        os.makedirs(root, mode=0o750, exist_ok=True)
        partial = os.path.join(root, ".download-%s.zip.part" % message["session_id"])
        try:
            download(
                payload["url"], partial, self.config["ground_station_ip"],
                int(payload["byte_count"]), str(payload["sha256"]),
                self.config["download_timeout_seconds"], self.config["max_artifact_bytes"])
            self.state = "verifying"
            self._write_active_state(message["map_id"], "verifying")
            self._reply(message, "download_status", {"state": "verifying"})
            self.map_dir = install_archive(
                partial, self.config["map_root"], message["map_id"],
                self.config["max_artifact_bytes"])
            os.unlink(partial)
            self.state = "map_ready"
            self._write_active_state(message["map_id"], "map_ready")
            self._reply(message, "download_status", {"state": "ready"})
        except (ArtifactError, KeyError, TypeError, ValueError, OSError) as exc:
            self.state = "error"
            self._write_active_state(message["map_id"], "error")
            self.logger.error("map_download_failed map=%s error=%s", message["map_id"], exc)
            self._reply(message, "download_status", {"state": "error", "reason": str(exc)})

    def _start_stack(self, message):
        if not self.config["enabled"] or self.config["backend"] != "scout_mini":
            self._write_active_state(message["map_id"], "unsupported")
            self._reply(message, "command_error", {
                "state": "error", "reason": "UNSUPPORTED_BACKEND"})
            return
        if self.state not in ("map_ready", "localized", "error") or not self.map_dir:
            self._reply(message, "command_error", {"state": "error", "reason": "MAP_NOT_READY"})
            return
        try:
            validate_map_directory(self.map_dir, message["map_id"])
        except ArtifactError as exc:
            self.state = "map_required"
            self._write_active_state(message["map_id"], "map_required")
            self._reply(message, "stack_status", {"state": "error", "reason": str(exc)})
            return
        replace_existing = bool(message.get("payload", {}).get("replace_existing", False))
        if replace_existing or self.state == "localized":
            if self.ros is not None:
                self.ros.cancel_monitor()
        self._write_active_state(message["map_id"], "starting")
        self.operation_generation += 1
        generation = self.operation_generation
        self.state = "starting"
        self._reply(message, "stack_status", {"state": "starting"})
        thread = threading.Thread(
            target=self._start_worker, args=(message, generation), name="relocalization-stack")
        thread.daemon = True
        thread.start()

    def _start_worker(self, message, generation):
        try:
            self.stack.start(message["map_id"], self.map_dir)
            if generation != self.operation_generation:
                return
            self.state = "awaiting_pose"
            self._write_active_state(message["map_id"], "awaiting_pose")
            self._reply(message, "stack_status", {"state": "awaiting_pose"})
        except (RosIntegrationError, OSError, KeyError, ValueError) as exc:
            if generation != self.operation_generation:
                return
            self.state = "error"
            self._write_active_state(message["map_id"], "error")
            self.logger.error("relocalization_stack_failed error=%s", exc)
            self._reply(message, "stack_status", {"state": "error", "reason": str(exc)})

    def _initial_pose(self, message):
        if not self.config["enabled"] or self.config["backend"] != "scout_mini":
            self._write_active_state(message["map_id"], "unsupported")
            self._reply(message, "command_error", {
                "state": "error", "reason": "UNSUPPORTED_BACKEND"})
            return
        if self.state not in ("awaiting_pose", "localized", "error") or self.ros is None:
            self._reply(message, "command_error", {"state": "error", "reason": "STACK_NOT_READY"})
            return
        payload = message["payload"]
        try:
            x, y, yaw = float(payload["x"]), float(payload["y"]), float(payload["yaw"])
        except (KeyError, TypeError, ValueError) as exc:
            self._reply(message, "command_error", {"state": "error", "reason": "INVALID_POSE: %s" % exc})
            return
        self.state = "relocalizing"
        self._write_active_state(message["map_id"], "relocalizing")
        generation = self.operation_generation
        self._reply(message, "relocalization_result", {"state": "relocalizing"})
        self.ros.publish_and_monitor(
            x, y, yaw, payload.get("covariance", {}),
            lambda success, transform, reason: self._tf_result(
                message, generation, success, transform, reason))

    def _tf_result(self, message, generation, success, transform, reason):
        if generation != self.operation_generation:
            self.logger.info("stale_relocalization_result_ignored generation=%s", generation)
            return
        if success:
            self.state = "localized"
            values = dict(zip(("x", "y", "z", "qx", "qy", "qz", "qw"), transform))
            try:
                self._write_active_state(message["map_id"], "localized", values)
            except OSError as exc:
                self.state = "error"
                self._write_active_state(message["map_id"], "error")
                self._reply(message, "relocalization_result", {
                    "state": "failed", "reason": "TF_STATE_WRITE_FAILED: %s" % exc})
                return
            self._reply(message, "relocalization_result", {
                "state": "succeeded", "map_frame": self.config["map_frame"],
                "odom_frame": self.config["odom_frame"], "map_from_odom": values})
        else:
            self.state = "error"
            self._write_active_state(message["map_id"], "error")
            self._reply(message, "relocalization_result", {
                "state": "failed", "reason": reason})

    def _heartbeat(self, unused_event):
        if self.identity is not None:
            self._send(self.identity, "session_heartbeat", {"state": self.state})

    def _reply(self, request, message_type, payload):
        self.response_cache[request["request_id"]] = (message_type, payload)
        if len(self.response_cache) > 128:
            self.response_cache.pop(next(iter(self.response_cache)))
        self._send(request, message_type, payload)

    def _send(self, request, message_type, payload):
        with self.lock:
            self.sequence += 1
            message = {
                "map_id": request["map_id"], "device_id": request["device_id"],
                "session_id": request["session_id"], "request_id": request.get("request_id", "heartbeat"),
                "message_type": message_type, "sequence": self.sequence,
                "sent_at_ns": time.time_ns(), "payload": dict(payload),
            }
            message["payload"]["request_id"] = request.get("request_id", "heartbeat")
            try:
                self.socket.sendto(
                    self.protocol.encode(message),
                    (self.config["ground_station_ip"], self.config["status_port"]))
            except OSError as exc:
                self.logger.warning("relocalization_send_failed error=%s", exc)


def default_paths():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return (os.path.join(root, "config", "relocalization.yaml"),
            os.path.abspath(os.path.join(root, "..", "EPGeneral_device_config", "config", "device.yaml")))


def run(argv=None, rospy_module=None):
    config_default, device_default = default_paths()
    parser = argparse.ArgumentParser(description="CCS edge relocalization coordinator")
    parser.add_argument("--config-file", default=config_default)
    parser.add_argument("--device-config-file", default=device_default)
    parser.add_argument("--log-dir", default="")
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args([item for item in raw if not (item.startswith("__") and ":=" in item)])
    logger = build_logger(args.log_dir or None)
    try:
        config = load_config(args.config_file, args.device_config_file)
    except ConfigError as exc:
        logger.error("configuration_invalid error=%s", exc)
        return 2
    try:
        if rospy_module is None:
            import rospy as rospy_module
        rospy_module.init_node("epgeneral_relocalization", anonymous=False)
        node = RelocalizationNode(config, rospy_module, logger)
        rospy_module.on_shutdown(node.stop)
        node.start()
        rospy_module.spin()
        return 0
    except Exception:
        logger.exception("epgeneral_relocalization_fatal")
        return 1
