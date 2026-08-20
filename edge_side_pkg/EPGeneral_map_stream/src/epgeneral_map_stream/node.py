import os
import socket
import threading
import time
import uuid
import zlib

from .artifacts import (
    ArtifactError, ArtifactHttpServer, CommandRunner, SessionPaths,
    build_archive, wait_for_stable_artifacts,
)
from .config import ConfigError, load_config
from .processing import (
    PoseBuffer, PoseSample, ProcessingError, aggregate_window,
    extract_pointcloud2, preprocess_points, stamp_to_ns, transform_from_pose,
)
from .protocol import ProtocolError, decode_command, encode_cloud_chunks, encode_envelope


ERROR_CODES = {
    "BUSY", "INVALID_CONFIG", "MAP_ID_MISMATCH", "DEVICE_ID_MISMATCH",
    "SENSOR_UNAVAILABLE", "POSE_UNAVAILABLE", "ARTIFACT_STORAGE_UNAVAILABLE",
    "MAP_GENERATION_UNAVAILABLE", "UNSUPPORTED_INPUT", "UNSUPPORTED_FORMAT",
    "INTERNAL_ERROR", "COMMAND_FAILED", "ARTIFACT_ERROR",
}


class MappingSession(object):
    def __init__(self, command, destination, paths, pose_buffer_size, clock):
        self.identity = {"map_id": command["map_id"], "session_id": command["session_id"]}
        self.destination = destination
        self.paths = paths
        self.token = uuid.uuid4().hex
        self.state = "preparing"
        self.pose_buffer = PoseBuffer(pose_buffer_size)
        self.subscribers = []
        self.frame_id = 0
        self.last_cloud_at = None
        self.last_pose_at = None
        self.last_heartbeat_at = clock()
        self.ready_at = None
        self.scans = []
        self.window_points = 0
        self.window_started_at = None


class RosMapStreamNode(object):
    def __init__(self, rospy, config, socket_factory=socket.socket,
                 clock=time.monotonic, message_resolver=None, command_runner=None,
                 artifact_server=None):
        self.rospy = rospy
        self.config = config
        self.socket_factory = socket_factory
        self.clock = clock
        self.message_resolver = message_resolver
        self.command_runner = command_runner or CommandRunner(config)
        self.artifact_server = artifact_server
        self.socket = None
        self.control_thread = None
        self.generation_thread = None
        self.watchdog_timer = None
        self.running = threading.Event()
        self.lock = threading.RLock()
        self.session = None
        self.state = "standby"
        self.request_cache = {}
        self.sequences = {}

    def start(self):
        udp_socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.bind((self.config["bind_host"], self.config["control_port"]))
            udp_socket.settimeout(0.2)
            if self.artifact_server is None:
                self.artifact_server = ArtifactHttpServer(
                    self.config["http_bind_host"], self.config["http_port"])
            self.artifact_server.start()
        except (OSError, ArtifactError):
            udp_socket.close()
            raise
        self.socket = udp_socket
        self.running.set()
        self.control_thread = threading.Thread(
            target=self._control_loop, name="ros-map-stream-control")
        self.control_thread.daemon = True
        self.control_thread.start()
        self.watchdog_timer = self.rospy.Timer(self.rospy.Duration(0.1), self._watchdog)
        self.rospy.on_shutdown(self.close)
        self.rospy.loginfo(
            "epgeneral_map_stream v2 standby; UDP %s:%d HTTP %s:%d",
            self.config["bind_host"], self.config["control_port"],
            self.config["http_bind_host"], self.artifact_server.port,
        )

    def _control_loop(self):
        while self.running.is_set():
            try:
                datagram, address = self.socket.recvfrom(self.config["max_datagram_bytes"] + 1)
            except socket.timeout:
                continue
            except OSError:
                if self.running.is_set():
                    self.rospy.logerr("mapping control socket failed")
                return
            try:
                self.handle_datagram(datagram, address[0])
            except Exception as exc:
                self.rospy.logerr("mapping command handling failed: %s", exc)

    def handle_datagram(self, datagram, peer_ip):
        if peer_ip != self.config["ground_station_ip"]:
            self.rospy.logwarn("ignored mapping command from unexpected IP %s", peer_ip)
            return
        try:
            command = decode_command(datagram, self.config)
        except ProtocolError as exc:
            self.rospy.logwarn("invalid mapping command: %s", exc)
            return
        request_id = command["payload"]["request_id"]
        with self.lock:
            cached = self.request_cache.get(request_id)
            if cached and cached["expires_at"] >= self.clock():
                if self._same_request(cached, command):
                    self._send(cached["identity"], cached["destination"],
                               cached["message_type"], cached["payload"])
                else:
                    self.rospy.logwarn("ignored conflicting reuse of request ID %s", request_id)
                return
        if command["device_id"].casefold() != self.config["device_id"].casefold():
            destination = self._command_destination(command, peer_ip)
            if command["message_type"] == "prepare_mapping":
                self._send_prepare_rejection(command, destination, "DEVICE_ID_MISMATCH",
                                             "device ID does not match")
            else:
                self._reject(command, "device ID does not match", "DEVICE_ID_MISMATCH", destination)
            return
        if command["message_type"] == "prepare_mapping":
            self._handle_prepare(command, peer_ip)
        elif command["message_type"] == "start_mapping":
            self._handle_start(command)
        else:
            self._handle_stop(command)

    @staticmethod
    def _same_request(cached, command):
        return (cached["identity"]["map_id"] == command["map_id"]
                and cached["identity"]["session_id"] == command["session_id"]
                and cached["device_id"].casefold() == command["device_id"].casefold()
                and cached["command"] == command["message_type"])

    def _command_destination(self, command, peer_ip):
        if command["message_type"] == "prepare_mapping":
            payload = command["payload"]
            return (payload["return_host"], payload["return_port"])
        with self.lock:
            if self.session is not None:
                return self.session.destination
        return (peer_ip, self.config["data_port"])

    def _handle_prepare(self, command, peer_ip):
        payload = command["payload"]
        destination = (payload["return_host"], payload["return_port"])
        if payload["return_host"] != peer_ip or payload["return_port"] != self.config["data_port"]:
            self._send_prepare_rejection(
                command, (peer_ip, self.config["data_port"]), "INVALID_CONFIG",
                "return address does not match configured ground station")
            return
        with self.lock:
            active = self.session
            if active is not None and active.state in (
                    "starting", "mapping", "stopping", "generating"):
                self._send_prepare_rejection(
                    command, destination, "BUSY", "another mapping session is active")
                return
        try:
            paths = SessionPaths(self.config, {
                "map_id": command["map_id"], "session_id": command["session_id"]})
        except ArtifactError as exc:
            self._send_prepare_rejection(
                command, destination, "ARTIFACT_STORAGE_UNAVAILABLE", str(exc))
            return
        session = MappingSession(
            command, destination, paths, self.config["pose_buffer_size"], self.clock)
        with self.lock:
            self.session = session
            self.state = "preparing"
        checks = self._readiness_checks(payload["required_inputs"], session)
        accepted = all(item["available"] for item in checks)
        reason = "; ".join(item["reason"] for item in checks
                           if not item["available"] and item["reason"])
        error_code = ""
        if not accepted:
            first = next(item for item in checks if not item["available"])
            error_code = first.get("error_code", "INTERNAL_ERROR")
        result = {
            "request_id": payload["request_id"], "accepted": accepted,
            "checks": [{"name": item["name"], "available": item["available"],
                        "reason": item["reason"]} for item in checks],
            "sample_window_seconds": self.config["sample_window_seconds"],
            "frame_id": self.config["map_frame"],
            "capability_version": self.config["capability_version"],
            "error_code": error_code, "reason": reason,
        }
        if accepted:
            with self.lock:
                if self.session is session:
                    session.state = "ready"
                    session.ready_at = self.clock()
                    self.state = "ready"
        else:
            with self.lock:
                if self.session is session:
                    self.session = None
                    self.state = "standby"
        self._cache_response(command, "prepare_result", result, destination)
        self._send(session.identity, destination, "prepare_result", result)

    def _readiness_checks(self, required, session):
        checks = []
        known = {"pointcloud", "pose", "artifact_storage", "map_generation"}
        published = {}
        try:
            published = dict(self.rospy.get_published_topics())
        except Exception:
            pass
        for name in required:
            available, reason, code = False, "", "UNSUPPORTED_INPUT"
            try:
                if name == "pointcloud":
                    self._probe_topic(
                        self.config["cloud_topic"], self.config["cloud_message_type"],
                        published, self._validate_cloud_probe)
                    available, code = True, ""
                elif name == "pose":
                    self._probe_topic(
                        self.config["pose_topic"], self.config["pose_message_type"],
                        published, self._validate_pose_probe)
                    available, code = True, ""
                elif name == "artifact_storage":
                    session.paths.prepare(self.config["min_free_bytes"])
                    available, code = True, ""
                elif name == "map_generation":
                    self.command_runner.check()
                    available, code = True, ""
                elif name not in known:
                    reason = "required input is not supported"
            except (ArtifactError, ConfigError, ProcessingError, RuntimeError) as exc:
                reason = str(exc)
                code = {
                    "pointcloud": "SENSOR_UNAVAILABLE", "pose": "POSE_UNAVAILABLE",
                    "artifact_storage": "ARTIFACT_STORAGE_UNAVAILABLE",
                    "map_generation": "MAP_GENERATION_UNAVAILABLE",
                }.get(name, "UNSUPPORTED_INPUT")
            checks.append({"name": name, "available": available,
                           "reason": reason, "error_code": code})
        return checks

    def _resolve_message(self, message_type):
        resolver = self.message_resolver
        if resolver is None:
            import roslib.message
            resolver = roslib.message.get_message_class
        message_class = resolver(message_type)
        if message_class is None:
            raise ConfigError("configured ROS message class is unavailable")
        return message_class

    def _probe_topic(self, topic, expected_type, published, validator):
        if published.get(topic) != expected_type:
            raise RuntimeError("topic is unavailable or has the wrong type: %s" % topic)
        message_class = self._resolve_message(expected_type)
        wait_for_message = getattr(self.rospy, "wait_for_message", None)
        if wait_for_message is not None:
            try:
                message = wait_for_message(
                    topic, message_class,
                    timeout=max(0.1, self.config["prepare_probe_timeout_seconds"] / 2.0))
            except Exception as exc:
                raise RuntimeError("topic did not provide fresh data: %s" % exc)
            validator(message)

    def _validate_cloud_probe(self, message):
        if message.header.frame_id != self.config["sensor_frame"]:
            raise ProcessingError("cloud frame_id does not match configured sensor frame")
        fields = {field.name for field in getattr(message, "fields", [])}
        if fields and not {"x", "y", "z"}.issubset(fields):
            raise ProcessingError("PointCloud2 does not contain x/y/z fields")
        stamp_to_ns(message.header.stamp)

    def _validate_pose_probe(self, message):
        if message.header.frame_id != self.config["map_frame"]:
            raise ProcessingError("pose frame_id does not match configured map frame")
        child = getattr(message, "child_frame_id", self.config["body_frame"])
        if child and child != self.config["body_frame"]:
            raise ProcessingError("pose child_frame_id does not match configured body frame")
        transform_from_pose(
            message, self.config["pose_position_path"], self.config["pose_orientation_path"])
        stamp_to_ns(message.header.stamp)

    def _handle_start(self, command):
        with self.lock:
            session = self.session
            valid = (session is not None and session.state == "ready"
                     and command["map_id"] == session.identity["map_id"]
                     and command["session_id"] == session.identity["session_id"])
        if not valid:
            self._reject(command, "prepared mapping session does not match",
                         "MAP_ID_MISMATCH", self._command_destination(
                             command, self.config["ground_station_ip"]))
            return
        with self.lock:
            session.state = "starting"
            self.state = "starting"
        try:
            self._subscribe_session(session)
            self.command_runner.run(self.config["start_command"], session.paths.values)
        except Exception as exc:
            self._unregister(session)
            with self.lock:
                if self.session is session:
                    self.session = None
                    self.state = "standby"
            self._reject(command, "cannot start mapping: %s" % exc,
                         "COMMAND_FAILED", session.destination)
            return
        now = self.clock()
        with self.lock:
            session.state = "mapping"
            session.last_cloud_at = now
            session.last_pose_at = now
            session.last_heartbeat_at = now
            self.state = "mapping"
        ack = self._ack_payload(command, True)
        self._cache_response(command, "command_ack", ack, session.destination)
        self._send(session.identity, session.destination, "command_ack", ack)
        self._send_session_message(session, "session_status", {
            "state": "mapping", "reason": "", "error_code": ""})

    def _handle_stop(self, command):
        with self.lock:
            session = self.session
            valid = (session is not None and session.state == "mapping"
                     and command["map_id"] == session.identity["map_id"]
                     and command["session_id"] == session.identity["session_id"])
            if valid:
                session.state = "generating"
                session.token = uuid.uuid4().hex
                session.scans = []
                session.window_points = 0
                self.state = "generating"
        if not valid:
            self._reject(command, "mapping session does not match",
                         "MAP_ID_MISMATCH", self._command_destination(
                             command, self.config["ground_station_ip"]))
            return
        self._unregister(session)
        ack = self._ack_payload(command, True)
        self._cache_response(command, "command_ack", ack, session.destination)
        self._send(session.identity, session.destination, "command_ack", ack)
        self._send_session_message(session, "artifact_status", {
            "state": "generating", "message": "generating PCD, PGM and YAML", "reason": ""})
        self.generation_thread = threading.Thread(
            target=self._generate_artifact, args=(session,), name="map-artifact-generation")
        self.generation_thread.daemon = True
        self.generation_thread.start()

    def _generate_artifact(self, session):
        try:
            self.command_runner.run(self.config["stop_command"], session.paths.values)
            wait_for_stable_artifacts(session.paths, self.config)
            descriptor = build_archive(session.paths, self.config, session.identity)
            token, expires_at = self.artifact_server.register(
                descriptor["path"], self.config["http_token_ttl_seconds"])
            url_host = ("[%s]" % self.config["device_ip"]
                        if ":" in self.config["device_ip"] else self.config["device_ip"])
            payload = {
                "state": "ready",
                "url": "http://%s:%d/mapping/result.zip?token=%s" % (
                    url_host, self.artifact_server.port, token),
                "byte_count": descriptor["byte_count"],
                "sha256": descriptor["sha256"], "expires_at": expires_at,
                "reason": "",
            }
            self._send_session_message(session, "artifact_status", payload)
            with self.lock:
                if self.session is session:
                    session.state = "serving"
                    self.state = "serving"
                    self.session = None
                    self.state = "standby"
        except Exception as exc:
            self._send_session_message(session, "artifact_status", {
                "state": "error", "reason": str(exc)})
            with self.lock:
                if self.session is session:
                    self.session = None
                    self.state = "standby"

    def _subscribe_session(self, session):
        cloud_class = self._resolve_message(self.config["cloud_message_type"])
        pose_class = self._resolve_message(self.config["pose_message_type"])
        token = session.token
        session.subscribers = [
            self.rospy.Subscriber(
                self.config["pose_topic"], pose_class,
                lambda message: self._pose_callback(token, message),
                queue_size=self.config["pose_buffer_size"]),
            self.rospy.Subscriber(
                self.config["cloud_topic"], cloud_class,
                lambda message: self._cloud_callback(token, message),
                queue_size=1, buff_size=16 * 1024 * 1024),
        ]

    def _pose_callback(self, token, message):
        with self.lock:
            session = self.session
            if session is None or session.token != token or session.state not in ("starting", "mapping"):
                return
        try:
            self._validate_pose_probe(message)
            transform = transform_from_pose(
                message, self.config["pose_position_path"], self.config["pose_orientation_path"])
            stamp_ns = stamp_to_ns(message.header.stamp)
        except (AttributeError, ProcessingError) as exc:
            self.rospy.logwarn_throttle(5.0, "pose preprocessing failed: %s" % exc)
            return
        with self.lock:
            if self.session is session and session.token == token:
                session.pose_buffer.add(PoseSample(stamp_ns, transform))
                session.last_pose_at = self.clock()

    def _cloud_callback(self, token, message):
        now = self.clock()
        with self.lock:
            session = self.session
            if session is None or session.token != token or session.state != "mapping":
                return
            session.last_cloud_at = now
        try:
            self._validate_cloud_probe(message)
            stamp_ns = stamp_to_ns(message.header.stamp)
            pose = session.pose_buffer.closest(
                stamp_ns, int(self.config["sync_tolerance_seconds"] * 1000000000))
            if pose is None:
                raise ProcessingError("no pose within synchronization tolerance")
            points = preprocess_points(
                extract_pointcloud2(message), self.config["min_range_m"],
                self.config["max_range_m"], self.config["voxel_size_m"],
                self.config["max_window_points"])
        except (AttributeError, ProcessingError, ValueError) as exc:
            self.rospy.logwarn_throttle(5.0, "cloud preprocessing failed: %s" % exc)
            return
        if not len(points):
            return
        flush = False
        with self.lock:
            if self.session is not session or session.token != token or session.state != "mapping":
                return
            if session.window_started_at is None:
                session.window_started_at = now
            remaining = self.config["max_window_points"] - session.window_points
            if remaining > 0:
                points = points[:remaining]
                session.scans.append((points, pose.transform, stamp_ns))
                session.window_points += len(points)
            flush = (now - session.window_started_at >= self.config["sample_window_seconds"]
                     or session.window_points >= self.config["max_window_points"])
        if flush:
            self._flush_window(session, token)

    def _flush_window(self, session, token):
        with self.lock:
            if self.session is not session or session.token != token or session.state != "mapping":
                return
            scans = session.scans
            session.scans = []
            session.window_points = 0
            session.window_started_at = None
        try:
            points, reference_pose = aggregate_window(
                scans, self.config["body_from_sensor"], self.config["voxel_size_m"],
                self.config["max_frame_points"])
            if not len(points):
                return
            raw = points.tobytes(order="C")
            if len(raw) > self.config["max_decompressed_bytes"]:
                raise ProcessingError("processed window exceeds decompressed byte limit")
            compressed = zlib.compress(raw)
            stamp_ns = scans[-1][2]
            with self.lock:
                if self.session is not session or session.token != token or session.state != "mapping":
                    return
                frame_id = session.frame_id
                session.frame_id += 1
                first_sequence = self._peek_sequence(session.identity["session_id"])
                datagrams = encode_cloud_chunks(self.config, session.identity, first_sequence, {
                    "frame_id": frame_id, "sample_stamp_ns": stamp_ns,
                    "point_count": len(points), "map_from_body": reference_pose,
                    "body_from_sensor": self.config["body_from_sensor"],
                }, compressed)
                self.sequences[session.identity["session_id"]] = first_sequence + len(datagrams)
            for datagram in datagrams:
                with self.lock:
                    if self.session is not session or session.token != token or session.state != "mapping":
                        return
                    self.socket.sendto(datagram, session.destination)
        except (OSError, ProcessingError, ProtocolError, ValueError, zlib.error) as exc:
            self.rospy.logerr("cloud window encoding failed: %s", exc)

    def _watchdog(self, unused_event=None):
        now = self.clock()
        flush = None
        failure = None
        with self.lock:
            for request_id in list(self.request_cache):
                if self.request_cache[request_id]["expires_at"] < now:
                    self.request_cache.pop(request_id, None)
            session = self.session
            if session is not None and session.state == "ready":
                if now - session.ready_at >= self.config["ready_timeout_seconds"]:
                    self.session = None
                    self.state = "standby"
                    session = None
            if session is not None and session.state == "mapping":
                if session.window_started_at is not None and (
                        now - session.window_started_at >= self.config["sample_window_seconds"]):
                    flush = (session, session.token)
                if now - session.last_cloud_at >= self.config["input_timeout_seconds"]:
                    failure = (session, "point cloud topic timed out", "SENSOR_UNAVAILABLE")
                elif now - session.last_pose_at >= self.config["input_timeout_seconds"]:
                    failure = (session, "pose topic timed out", "POSE_UNAVAILABLE")
                elif now - session.last_heartbeat_at >= 1.0:
                    session.last_heartbeat_at = now
                    self._send_session_message(session, "session_heartbeat", {"state": "mapping"})
            elif session is not None and session.state == "generating":
                if now - session.last_heartbeat_at >= 1.0:
                    session.last_heartbeat_at = now
                    self._send_session_message(session, "session_heartbeat", {"state": "generating"})
        if flush is not None:
            self._flush_window(flush[0], flush[1])
        if failure is not None:
            self._fail_session(*failure)
        if self.artifact_server is not None:
            self.artifact_server.cleanup()

    def _fail_session(self, session, reason, error_code):
        with self.lock:
            if self.session is not session:
                return
            session.state = "error"
            session.token = uuid.uuid4().hex
        self._send_session_message(session, "session_status", {
            "state": "error", "reason": reason, "error_code": error_code})
        self._unregister(session)
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"

    def _send_prepare_rejection(self, command, destination, error_code, reason):
        checks = []
        for name in command["payload"].get("required_inputs", ["pointcloud"]):
            checks.append({"name": name, "available": False, "reason": reason})
        payload = {
            "request_id": command["payload"]["request_id"], "accepted": False,
            "checks": checks, "sample_window_seconds": self.config["sample_window_seconds"],
            "frame_id": self.config["map_frame"],
            "capability_version": self.config["capability_version"],
            "error_code": error_code, "reason": reason,
        }
        identity = {"map_id": command["map_id"], "session_id": command["session_id"]}
        self._cache_response(command, "prepare_result", payload, destination)
        self._send(identity, destination, "prepare_result", payload)

    @staticmethod
    def _ack_payload(command, accepted, reason="", error_code=""):
        return {
            "request_id": command["payload"]["request_id"],
            "command": command["message_type"], "accepted": bool(accepted),
            "reason": reason, "error_code": error_code,
        }

    def _reject(self, command, reason, error_code, destination):
        if error_code not in ERROR_CODES:
            error_code = "INTERNAL_ERROR"
        payload = self._ack_payload(command, False, reason, error_code)
        identity = {"map_id": command["map_id"], "session_id": command["session_id"]}
        self._cache_response(command, "command_ack", payload, destination)
        self._send(identity, destination, "command_ack", payload)

    def _cache_response(self, command, message_type, payload, destination):
        with self.lock:
            self.request_cache[command["payload"]["request_id"]] = {
                "identity": {"map_id": command["map_id"], "session_id": command["session_id"]},
                "device_id": command["device_id"], "command": command["message_type"],
                "message_type": message_type, "payload": payload, "destination": destination,
                "expires_at": self.clock() + self.config["command_cache_seconds"],
            }

    def _send_session_message(self, session, message_type, payload):
        self._send(session.identity, session.destination, message_type, payload)

    def _peek_sequence(self, session_id):
        return self.sequences.get(session_id, 0)

    def _send(self, identity, destination, message_type, payload):
        with self.lock:
            sequence = self._peek_sequence(identity["session_id"])
            self.sequences[identity["session_id"]] = sequence + 1
        try:
            datagram = encode_envelope(
                self.config, identity, message_type, sequence, payload)
            self.socket.sendto(datagram, destination)
        except (AttributeError, OSError, ProtocolError) as exc:
            self.rospy.logerr("mapping UDP send failed type=%s: %s", message_type, exc)

    @staticmethod
    def _unregister(session):
        for subscriber in session.subscribers:
            try:
                subscriber.unregister()
            except Exception:
                pass
        session.subscribers = []
        session.pose_buffer.clear()

    def close(self):
        if not self.running.is_set() and self.socket is None:
            return
        self.running.clear()
        with self.lock:
            session = self.session
            if session is not None:
                session.token = uuid.uuid4().hex
        if session is not None:
            self._unregister(session)
        with self.lock:
            self.session = None
            self.state = "standby"
        if self.watchdog_timer is not None:
            self.watchdog_timer.shutdown()
            self.watchdog_timer = None
        udp_socket = self.socket
        self.socket = None
        if udp_socket is not None:
            try:
                udp_socket.close()
            except OSError:
                pass
        if self.control_thread is not None and self.control_thread.is_alive():
            if self.control_thread is not threading.current_thread():
                self.control_thread.join(timeout=2.0)
        self.control_thread = None
        if self.generation_thread is not None and self.generation_thread.is_alive():
            self.generation_thread.join(timeout=2.0)
        if self.artifact_server is not None:
            self.artifact_server.close()
        self.rospy.loginfo("epgeneral_map_stream stopped")


def run():
    import rospy
    import rospkg

    rospy.init_node("epgeneral_map_stream")
    rospack = rospkg.RosPack()
    package_path = rospack.get_path("epgeneral_map_stream")
    device_package_path = rospack.get_path("epgeneral_device_config")
    mapping_path = rospy.get_param(
        "~mapping_config_file", package_path + "/config/mapping.yaml")
    device_path = rospy.get_param(
        "~device_config_file", device_package_path + "/config/device.yaml")
    try:
        config = load_config(mapping_path, device_path)
        node = RosMapStreamNode(rospy, config)
        node.start()
    except (ConfigError, OSError, ArtifactError) as exc:
        rospy.logfatal("epgeneral_map_stream startup failed: %s", exc)
        return
    rospy.spin()
