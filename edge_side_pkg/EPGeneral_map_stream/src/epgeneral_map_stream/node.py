import os
import queue
import socket
import threading
import time
import uuid

from .artifacts import (
    ArtifactError, ArtifactHttpServer, CommandRunner, SessionPaths,
    build_archive, file_fingerprint, require_fresh_file, wait_for_stable_artifacts,
    write_binary_pcd,
)
from .config import ConfigError, build_integration_commands, load_config
from .processing import (
    PoseBuffer, PoseSample, ProcessingError, aggregate_window,
    extract_pointcloud2, map_points_to_sensor, preprocess_points, stamp_to_ns,
    sensor_points_to_map, transform_from_pose,
)
from .protocol import ProtocolError, decode_command, encode_envelope


ERROR_CODES = {
    "BUSY", "INVALID_CONFIG", "MAP_ID_MISMATCH", "DEVICE_ID_MISMATCH",
    "SENSOR_UNAVAILABLE", "IMU_UNAVAILABLE", "POSE_UNAVAILABLE",
    "ARTIFACT_STORAGE_UNAVAILABLE",
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
        self.pending_clouds = []
        self.fragment_cache = {}
        self.source_pcd_baseline = None
        self.mapping_started_at_ns = 0


class RosMapStreamNode(object):
    def __init__(self, rospy, config, socket_factory=socket.socket,
                 clock=time.monotonic, message_resolver=None, command_runner=None,
                 artifact_server=None, log_path=None):
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
        self.preview_thread = None
        self.preview_queue = queue.Queue(maxsize=config["max_pending_preview_fragments"])
        self.watchdog_timer = None
        self.running = threading.Event()
        self.lock = threading.RLock()
        self.session = None
        self.state = "standby"
        self.request_cache = {}
        self.sequences = {}
        self.log_path = os.path.abspath(os.path.expanduser(log_path)) if log_path else None
        self.log_lock = threading.Lock()
        self.file_log_throttles = {}

    def _write_log(self, level, message, args):
        if self.log_path is None:
            return
        try:
            rendered = message % args if args else message
        except (TypeError, ValueError):
            rendered = "%s %s" % (message, args)
        try:
            with self.log_lock:
                directory = os.path.dirname(self.log_path)
                if not os.path.isdir(directory):
                    os.makedirs(directory)
                with open(self.log_path, "a", encoding="utf-8") as stream:
                    stream.write("%s %-5s %s\n" % (
                        time.strftime("%Y-%m-%d %H:%M:%S"), level, rendered))
        except OSError:
            pass

    def _log_info(self, message, *args):
        self.rospy.loginfo(message, *args)
        self._write_log("INFO", message, args)

    def _log_warn(self, message, *args):
        self.rospy.logwarn(message, *args)
        self._write_log("WARN", message, args)

    def _log_error(self, message, *args):
        self.rospy.logerr(message, *args)
        self._write_log("ERROR", message, args)

    def _log_warn_throttle(self, seconds, message, key=None):
        self.rospy.logwarn_throttle(seconds, message)
        now = self.clock()
        throttle_key = key or message
        previous = self.file_log_throttles.get(throttle_key)
        if previous is None or now - previous >= seconds:
            self.file_log_throttles[throttle_key] = now
            self._write_log("WARN", message, ())

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
        self.preview_thread = threading.Thread(
            target=self._preview_loop, name="map-preview-pcd")
        self.preview_thread.daemon = True
        self.preview_thread.start()
        self.control_thread = threading.Thread(
            target=self._control_loop, name="ros-map-stream-control")
        self.control_thread.daemon = True
        self.control_thread.start()
        self.watchdog_timer = self.rospy.Timer(self.rospy.Duration(0.1), self._watchdog)
        self.rospy.on_shutdown(self.close)
        self._log_info(
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
                    self._log_error("mapping control socket failed")
                return
            try:
                self.handle_datagram(datagram, address[0])
            except Exception as exc:
                self._log_error("mapping command handling failed: %s", exc)

    def handle_datagram(self, datagram, peer_ip):
        if peer_ip != self.config["ground_station_ip"]:
            self._log_warn("ignored mapping command from unexpected IP %s", peer_ip)
            return
        try:
            command = decode_command(datagram, self.config)
        except ProtocolError as exc:
            self._log_warn("invalid mapping command: %s", exc)
            return
        self._log_command("RX", command["message_type"], command)
        request_id = command["payload"]["request_id"]
        with self.lock:
            cached = self.request_cache.get(request_id)
            if cached and cached["expires_at"] >= self.clock():
                if self._same_request(cached, command):
                    self._log_info(
                        "mapping cache hit command=%s session=%s request=%s",
                        command["message_type"], command["session_id"][:8], request_id[:8])
                    self._send(cached["identity"], cached["destination"],
                               cached["message_type"], cached["payload"])
                else:
                    self._log_warn("ignored conflicting reuse of request ID %s", request_id)
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
        elif command["message_type"] == "stop_mapping":
            self._handle_stop(command)
        elif command["message_type"] == "abort_mapping":
            self._handle_abort(command)
        else:
            self._handle_fragment_ack(command)

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
        restarted = False
        previous_state = ""
        active_session_id = ""
        if active is not None:
            active_session_id = active.identity["session_id"]
            previous_state = active.state
            same_session = (
                command["map_id"] == active.identity["map_id"]
                and command["session_id"] == active.identity["session_id"])
            if not payload.get("restart_active") or not same_session:
                self._send_prepare_rejection(
                    command, destination, "BUSY", "another mapping session is active",
                    previous_state=previous_state, active_session_id=active_session_id)
                return
            if active.state not in ("ready", "starting", "mapping", "error"):
                self._send_prepare_rejection(
                    command, destination, "BUSY",
                    "active session cannot be restarted while artifacts are being finalized",
                    previous_state=previous_state, active_session_id=active_session_id)
                return
            try:
                self._restart_active_session(active)
            except (ArtifactError, RuntimeError) as exc:
                self._send_prepare_rejection(
                    command, destination, "COMMAND_FAILED",
                    "cannot restart active session: %s" % exc,
                    previous_state=previous_state, active_session_id=active_session_id)
                return
            restarted = True
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
        self._log_info(
            "mapping prepare started session=%s restarted=%s previous_state=%s",
            session.identity["session_id"][:8], restarted, previous_state or "none")
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
            "preview_transport": self.config["preview_transport"],
            "fragment_interval_seconds": self.config["sample_window_seconds"],
            "error_code": error_code, "reason": reason,
            "restarted": restarted,
        }
        if restarted:
            result.update({
                "previous_state": previous_state,
                "active_session_id": active_session_id,
            })
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

    def _restart_active_session(self, session):
        self._log_warn(
            "mapping recovery requested session=%s state=%s; discarding live mapping data",
            session.identity["session_id"][:8], session.state)
        with self.lock:
            session.token = uuid.uuid4().hex
        self._unregister(session)
        self._cleanup_fragments(session)
        if session.state in ("starting", "mapping"):
            commands = build_integration_commands(self.config, session.paths.values)
            self._run_command(
                "abort_fast_lio", commands["abort_fast_lio"],
                self.config["fast_lio_stop_timeout_seconds"] + 6.0)
        session.paths.reset()
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"
        self._log_info(
            "mapping recovery cleanup completed session=%s",
            session.identity["session_id"][:8])

    def _readiness_checks(self, required, session):
        checks = []
        known = {"pointcloud", "imu", "pose", "artifact_storage", "map_generation"}
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
                        self.config["input_cloud_topic"],
                        self.config["input_cloud_message_type"], published,
                        self._validate_input_cloud_probe,
                        self.config["prepare_probe_timeout_seconds"] / 2.0)
                    available, code = True, ""
                elif name == "imu":
                    self._probe_topic(
                        self.config["input_imu_topic"],
                        self.config["input_imu_message_type"], published,
                        None,
                        self.config["prepare_probe_timeout_seconds"] / 2.0)
                    available, code = True, ""
                elif name == "pose":
                    self._probe_topic(
                        self.config["pose_topic"],
                        self.config["pose_message_type"], published,
                        self._validate_pose_probe,
                        self.config["prepare_probe_timeout_seconds"] / 2.0)
                    available, code = True, ""
                elif name == "artifact_storage":
                    session.paths.prepare(self.config["min_free_bytes"])
                    available, code = True, ""
                elif name == "map_generation":
                    self.command_runner.check(build_integration_commands(
                        self.config, session.paths.values))
                    available, code = True, ""
                elif name not in known:
                    reason = "required input is not supported"
            except (ArtifactError, ConfigError, ProcessingError, RuntimeError) as exc:
                reason = str(exc)
                code = {
                    "pointcloud": "SENSOR_UNAVAILABLE", "imu": "IMU_UNAVAILABLE",
                    "pose": "IMU_UNAVAILABLE",
                    "artifact_storage": "ARTIFACT_STORAGE_UNAVAILABLE",
                    "map_generation": "MAP_GENERATION_UNAVAILABLE",
                }.get(name, "UNSUPPORTED_INPUT")
            checks.append({"name": name, "available": available,
                           "reason": reason, "error_code": code})
            log_method = self._log_info if available else self._log_warn
            log_method(
                "mapping readiness check=%s available=%s reason=%s",
                name, available, reason or "none")
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

    def _probe_topic(self, topic, expected_type, published, validator, timeout):
        if published is not None and published.get(topic) != expected_type:
            raise RuntimeError("topic is unavailable or has the wrong type: %s" % topic)
        message_class = self._resolve_message(expected_type)
        wait_for_message = getattr(self.rospy, "wait_for_message", None)
        if wait_for_message is not None:
            try:
                message = wait_for_message(
                    topic, message_class, timeout=max(0.1, timeout))
            except Exception as exc:
                raise RuntimeError("topic did not provide fresh data: %s" % exc)
            if validator is not None:
                validator(message)
            frame_id = getattr(getattr(message, "header", None), "frame_id", "")
            self._log_info(
                "mapping topic probe passed topic=%s type=%s frame=%s",
                topic, expected_type, frame_id or "n/a")

    @staticmethod
    def _validate_cloud_fields(message):
        fields = {field.name for field in getattr(message, "fields", [])}
        if fields and not {"x", "y", "z"}.issubset(fields):
            raise ProcessingError("PointCloud2 does not contain x/y/z fields")
        stamp_to_ns(message.header.stamp)

    def _validate_input_cloud_probe(self, message):
        if message.header.frame_id != self.config["input_cloud_frame"]:
            raise ProcessingError("Livox cloud frame_id does not match configured input frame")
        self._validate_cloud_fields(message)

    def _validate_stream_cloud_probe(self, message):
        if message.header.frame_id != self.config["cloud_frame"]:
            raise ProcessingError("FAST_LIO cloud frame_id does not match configured stream frame")
        self._validate_cloud_fields(message)

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
            session.source_pcd_baseline = file_fingerprint(self.config["generated_pcd_path"])
            session.mapping_started_at_ns = time.time_ns()
        self._log_info(
            "mapping source baseline session=%s fingerprint=%s",
            session.identity["session_id"][:8], session.source_pcd_baseline or "missing")
        self._send_session_message(session, "session_status", {
            "state": "starting", "reason": "starting FAST_LIO and waiting for outputs",
            "error_code": ""})
        commands = build_integration_commands(self.config, session.paths.values)
        try:
            self._run_command(
                "start_fast_lio", commands["start_fast_lio"],
                timeout=self.config["fast_lio_startup_timeout_seconds"])
            self._wait_for_fast_lio_outputs()
            self._subscribe_session(session)
        except Exception as exc:
            self._unregister(session)
            try:
                self._run_command(
                    "abort_after_start_failure", commands["abort_fast_lio"],
                    timeout=self.config["fast_lio_stop_timeout_seconds"] + 6.0)
            except Exception:
                pass
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

    def _wait_for_fast_lio_outputs(self):
        deadline = self.clock() + self.config["fast_lio_startup_timeout_seconds"]
        for topic, message_type, validator in (
                (self.config["cloud_topic"], self.config["cloud_message_type"],
                 self._validate_stream_cloud_probe),
                (self.config["pose_topic"], self.config["pose_message_type"],
                 self._validate_pose_probe)):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise RuntimeError("FAST_LIO output startup timed out")
            self._probe_topic(
                topic, message_type, None, validator, remaining)

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

    def _handle_abort(self, command):
        with self.lock:
            session = self.session
            valid = (session is not None
                     and session.state in ("ready", "starting", "mapping", "error")
                     and command["map_id"] == session.identity["map_id"]
                     and command["session_id"] == session.identity["session_id"])
        if not valid:
            self._reject(
                command,
                "mapping session cannot be aborted while artifacts are being finalized",
                "BUSY", self._command_destination(command, self.config["ground_station_ip"]))
            return
        try:
            self._restart_active_session(session)
        except (ArtifactError, RuntimeError) as exc:
            self._reject(command, "cannot abort mapping: %s" % exc,
                         "COMMAND_FAILED", session.destination)
            return
        ack = self._ack_payload(command, True)
        self._cache_response(command, "command_ack", ack, session.destination)
        self._send(session.identity, session.destination, "command_ack", ack)
        self._log_warn("mapping session aborted without artifacts session=%s",
                       session.identity["session_id"][:8])

    def _handle_fragment_ack(self, command):
        payload = command["payload"]
        with self.lock:
            session = self.session
            valid = (session is not None and session.state in ("mapping", "generating")
                     and command["map_id"] == session.identity["map_id"]
                     and command["session_id"] == session.identity["session_id"])
            entry = session.fragment_cache.pop(payload["fragment_id"], None) if valid else None
        if not valid:
            return
        if entry is not None:
            self.artifact_server.unregister(entry["token"], delete=True)
        self._log_info(
            "mapping preview acknowledged session=%s fragment=%d",
            session.identity["session_id"][:8], payload["fragment_id"])

    def _generate_artifact(self, session):
        commands = build_integration_commands(self.config, session.paths.values)
        try:
            self._send_session_message(session, "artifact_status", {
                "state": "generating", "message": "stopping FAST_LIO and saving PCD",
                "reason": ""})
            self._run_command(
                "stop_fast_lio", commands["stop_fast_lio"],
                timeout=self.config["fast_lio_stop_timeout_seconds"] + 6.0)
            fingerprint = require_fresh_file(
                self.config["generated_pcd_path"], session.source_pcd_baseline,
                session.mapping_started_at_ns)
            self._log_info(
                "mapping source freshness passed session=%s fingerprint=%s",
                session.identity["session_id"][:8], fingerprint)
            self._send_session_message(session, "artifact_status", {
                "state": "generating", "message": "generating PGM and map YAML",
                "reason": ""})
            self._run_command(
                "generate_pgm", commands["generate_pgm"],
                timeout=self.config["pgm_generation_timeout_seconds"] + 6.0)
            self._send_session_message(session, "artifact_status", {
                "state": "generating", "message": "validating mapping artifacts",
                "reason": ""})
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
            self._cleanup_fragments(session)
            with self.lock:
                if self.session is session:
                    session.state = "serving"
                    self.state = "serving"
                    self.session = None
                    self.state = "standby"
        except Exception as exc:
            self._send_session_message(session, "artifact_status", {
                "state": "error", "reason": str(exc)})
            self._cleanup_fragments(session)
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
            self._log_warn_throttle(5.0, "pose preprocessing failed: %s" % exc)
            return
        with self.lock:
            if self.session is session and session.token == token:
                session.pose_buffer.add(PoseSample(stamp_ns, transform))
                session.last_pose_at = self.clock()
                pending = session.pending_clouds
                session.pending_clouds = []
            else:
                pending = []
        tolerance_ns = int(self.config["sync_tolerance_seconds"] * 1000000000)
        for cloud_stamp_ns, received_at, cloud_message in pending:
            pose = session.pose_buffer.closest(cloud_stamp_ns, tolerance_ns)
            if pose is not None:
                self._process_cloud(
                    token, session, cloud_message, cloud_stamp_ns, pose, received_at)
            elif stamp_ns > cloud_stamp_ns + tolerance_ns:
                self._log_sync_drop(cloud_stamp_ns, stamp_ns, "pose advanced past cloud")
            else:
                with self.lock:
                    if self.session is session and session.token == token:
                        session.pending_clouds.append(
                            (cloud_stamp_ns, received_at, cloud_message))

    def _cloud_callback(self, token, message):
        now = self.clock()
        with self.lock:
            session = self.session
            if session is None or session.token != token or session.state != "mapping":
                return
            session.last_cloud_at = now
        try:
            self._validate_stream_cloud_probe(message)
            stamp_ns = stamp_to_ns(message.header.stamp)
        except (AttributeError, ProcessingError, ValueError) as exc:
            self._log_warn_throttle(5.0, "cloud preprocessing failed: %s" % exc)
            return
        tolerance_ns = int(self.config["sync_tolerance_seconds"] * 1000000000)
        with self.lock:
            if self.session is not session or session.token != token or session.state != "mapping":
                return
            pose = session.pose_buffer.closest(stamp_ns, tolerance_ns)
            if pose is None:
                newest_stamp = session.pose_buffer.newest_stamp()
                if newest_stamp is not None and newest_stamp > stamp_ns + tolerance_ns:
                    self._log_sync_drop(stamp_ns, newest_stamp, "cloud arrived too late")
                    return
                session.pending_clouds.append((stamp_ns, now, message))
                if len(session.pending_clouds) > 3:
                    dropped_stamp, unused_received, unused_message = session.pending_clouds.pop(0)
                    self._log_sync_drop(
                        dropped_stamp, newest_stamp, "pending cloud queue full")
                return
        self._process_cloud(token, session, message, stamp_ns, pose, now)

    def _process_cloud(self, token, session, message, stamp_ns, pose, received_at):
        try:
            points = extract_pointcloud2(message)
            if self.config["cloud_coordinates"] == "map":
                points = map_points_to_sensor(
                    points, pose.transform, self.config["body_from_sensor"])
            points = preprocess_points(
                points, self.config["min_range_m"],
                self.config["max_range_m"], self.config["voxel_size_m"],
                self.config["max_window_points"])
        except (AttributeError, ProcessingError, ValueError) as exc:
            self._log_warn_throttle(5.0, "cloud preprocessing failed: %s" % exc)
            return
        if not len(points):
            return
        flush = False
        with self.lock:
            if self.session is not session or session.token != token or session.state != "mapping":
                return
            if session.window_started_at is None:
                session.window_started_at = received_at
            remaining = self.config["max_window_points"] - session.window_points
            if remaining > 0:
                points = points[:remaining]
                session.scans.append((points, pose.transform, stamp_ns))
                session.window_points += len(points)
            flush = (received_at - session.window_started_at >= self.config["sample_window_seconds"]
                     or session.window_points >= self.config["max_window_points"])
        if flush:
            self._flush_window(session, token)

    def _log_sync_drop(self, cloud_stamp_ns, pose_stamp_ns, reason):
        delta = "no pose"
        if pose_stamp_ns is not None:
            delta = "%.3f ms" % ((pose_stamp_ns - cloud_stamp_ns) / 1000000.0)
        self._log_warn_throttle(
            5.0, "cloud/pose synchronization dropped cloud: %s; delta=%s" % (
                reason, delta), key="cloud_pose_sync_drop")

    def _flush_window(self, session, token):
        with self.lock:
            if self.session is not session or session.token != token or session.state != "mapping":
                return
            scans = session.scans
            session.scans = []
            session.window_points = 0
            session.window_started_at = None
        try:
            self.preview_queue.put_nowait((session, token, scans))
        except queue.Full:
            self._log_warn_throttle(
                5.0, "preview PCD queue is full; dropping one preview window",
                key="preview_queue_full")

    def _preview_loop(self):
        while self.running.is_set() or not self.preview_queue.empty():
            try:
                session, token, scans = self.preview_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                points, reference_pose = aggregate_window(
                    scans, self.config["body_from_sensor"], self.config["voxel_size_m"],
                    self.config["max_frame_points"])
                if not len(points):
                    continue
                points = sensor_points_to_map(
                    points, reference_pose, self.config["body_from_sensor"])
                with self.lock:
                    if self.session is not session or session.token != token or session.state != "mapping":
                        continue
                    fragment_id = session.frame_id
                    session.frame_id += 1
                path = os.path.join(session.paths.fragment_dir, "%08d.pcd" % fragment_id)
                descriptor = write_binary_pcd(path, points)
                if descriptor["byte_count"] > self.config["max_preview_fragment_bytes"]:
                    raise ArtifactError("preview PCD exceeds configured byte limit")
                route = "/mapping/preview/%s/%08d.pcd" % (
                    session.identity["session_id"], fragment_id)
                http_token, expires_at = self.artifact_server.register(
                    path, self.config["http_token_ttl_seconds"], route=route,
                    content_type="application/octet-stream")
                host = ("[%s]" % self.config["device_ip"]
                        if ":" in self.config["device_ip"] else self.config["device_ip"])
                payload = {
                    "fragment_id": fragment_id,
                    "url": "http://%s:%d%s?token=%s" % (
                        host, self.artifact_server.port, route, http_token),
                    "byte_count": descriptor["byte_count"], "sha256": descriptor["sha256"],
                    "point_count": len(points), "frame_id": self.config["map_frame"],
                    "started_at_ns": scans[0][2], "ended_at_ns": scans[-1][2],
                    "expires_at": expires_at,
                }
                with self.lock:
                    if self.session is not session or session.token != token or session.state != "mapping":
                        self.artifact_server.unregister(http_token, delete=True)
                        continue
                    session.fragment_cache[fragment_id] = {
                        "payload": payload, "token": http_token, "attempts": 1,
                        "last_sent": self.clock(),
                    }
                    while len(session.fragment_cache) > self.config["max_unacked_preview_fragments"]:
                        old_id = sorted(session.fragment_cache)[0]
                        old = session.fragment_cache.pop(old_id)
                        self.artifact_server.unregister(old["token"], delete=True)
                        self._log_warn("discarded unacknowledged preview fragment %d", old_id)
                self._send_session_message(session, "cloud_fragment_ready", payload)
            except (ArtifactError, OSError, ProcessingError, ProtocolError, ValueError) as exc:
                self._log_error("preview PCD generation failed: %s", exc)
            finally:
                self.preview_queue.task_done()

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
                for entry in session.fragment_cache.values():
                    if (entry["attempts"] < 3 and now - entry["last_sent"] >= 1.0):
                        entry["attempts"] += 1
                        entry["last_sent"] = now
                        self._send_session_message(
                            session, "cloud_fragment_ready", entry["payload"])
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
        self._cleanup_fragments(session)
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"

    def _run_command(self, name, arguments, timeout):
        started = self.clock()
        self._log_info("mapping integration start name=%s timeout=%.1fs", name, timeout)
        try:
            output = self.command_runner.run(arguments, timeout=timeout)
        except Exception as exc:
            self._log_error(
                "mapping integration failed name=%s elapsed=%.3fs error=%s",
                name, self.clock() - started, exc)
            raise
        self._log_info(
            "mapping integration completed name=%s elapsed=%.3fs output=%s",
            name, self.clock() - started, output or "<empty>")
        return output

    def _log_command(self, direction, message_type, command):
        payload = command.get("payload", {})
        self._log_info(
            "mapping %s type=%s map=%s session=%s request=%s restart_active=%s",
            direction, message_type, command.get("map_id", "")[:8],
            command.get("session_id", "")[:8],
            str(payload.get("request_id", ""))[:8],
            bool(payload.get("restart_active", False)))

    def _send_prepare_rejection(self, command, destination, error_code, reason,
                                previous_state="", active_session_id=""):
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
        if previous_state:
            payload["previous_state"] = previous_state
        if active_session_id:
            payload["active_session_id"] = active_session_id
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
            if message_type != "session_heartbeat":
                detail = payload.get("state") or payload.get("command") or ""
                accepted = payload.get("accepted")
                if accepted is not None:
                    detail = "%s accepted=%s" % (detail, accepted)
                self._log_info(
                    "mapping TX type=%s session=%s sequence=%d destination=%s:%d detail=%s",
                    message_type, identity["session_id"][:8], sequence,
                    destination[0], destination[1], detail or "none")
        except (AttributeError, OSError, ProtocolError) as exc:
            self._log_error("mapping UDP send failed type=%s: %s", message_type, exc)

    @staticmethod
    def _unregister(session):
        for subscriber in session.subscribers:
            try:
                subscriber.unregister()
            except Exception:
                pass
        session.subscribers = []
        session.pose_buffer.clear()
        session.pending_clouds = []

    def _cleanup_fragments(self, session):
        with self.lock:
            entries = list(session.fragment_cache.values())
            session.fragment_cache.clear()
        if self.artifact_server is None:
            return
        for entry in entries:
            self.artifact_server.unregister(entry["token"], delete=True)

    def close(self):
        if not self.running.is_set() and self.socket is None:
            return
        self.running.clear()
        with self.lock:
            session = self.session
            stop_fast_lio = session is not None and session.state in ("starting", "mapping")
            if session is not None:
                session.token = uuid.uuid4().hex
        if session is not None:
            self._unregister(session)
            self._cleanup_fragments(session)
        if stop_fast_lio:
            try:
                commands = build_integration_commands(self.config, session.paths.values)
                self._run_command(
                    "abort_on_shutdown", commands["abort_fast_lio"],
                    timeout=self.config["fast_lio_stop_timeout_seconds"] + 6.0)
            except Exception as exc:
                self._log_warn("FAST_LIO shutdown cleanup failed: %s", exc)
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
        if self.preview_thread is not None and self.preview_thread.is_alive():
            self.preview_thread.join(timeout=2.0)
        self.preview_thread = None
        if self.generation_thread is not None and self.generation_thread.is_alive():
            self.generation_thread.join(timeout=2.0)
        if self.artifact_server is not None:
            self.artifact_server.close()
        self._log_info("epgeneral_map_stream stopped")


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
        node = RosMapStreamNode(
            rospy, config,
            log_path=os.path.expanduser("~/.ros/ccs_edge_dev/log/map_stream.log"))
        node.start()
    except (ConfigError, OSError, ArtifactError) as exc:
        rospy.logfatal("epgeneral_map_stream startup failed: %s", exc)
        return
    rospy.spin()
