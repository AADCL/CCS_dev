import socket
import threading
import time
import uuid
import zlib

from .config import ConfigError, load_config
from .processing import (
    PoseBuffer,
    PoseSample,
    ProcessingError,
    extract_pointcloud2,
    preprocess_points,
    stamp_to_ns,
    transform_from_pose,
)
from .protocol import ProtocolError, decode_command, encode_cloud_chunks, encode_envelope


ERROR_CODES = {
    "BUSY", "INVALID_CONFIG", "MAP_ID_MISMATCH", "DEVICE_ID_MISMATCH",
    "SENSOR_UNAVAILABLE", "POSE_UNAVAILABLE", "UNSUPPORTED_FORMAT", "INTERNAL_ERROR",
}


class MappingSession(object):
    def __init__(self, command, destination, cloud_rate_hz, voxel_size_m, pose_buffer_size):
        self.identity = {"map_id": command["map_id"], "session_id": command["session_id"]}
        self.destination = destination
        self.start_request_id = command["payload"]["request_id"]
        self.token = uuid.uuid4().hex
        self.state = "starting"
        self.cloud_rate_hz = float(cloud_rate_hz)
        self.voxel_size_m = float(voxel_size_m)
        self.pose_buffer = PoseBuffer(pose_buffer_size)
        self.subscribers = []
        self.frame_id = 0
        self.last_frame_sent_at = 0.0
        self.last_cloud_at = None
        self.last_pose_at = None
        self.last_heartbeat_at = 0.0


class RosMapStreamNode(object):
    def __init__(self, rospy, config, socket_factory=socket.socket, clock=time.monotonic, message_resolver=None):
        self.rospy = rospy
        self.config = config
        self.socket_factory = socket_factory
        self.clock = clock
        self.message_resolver = message_resolver
        self.socket = None
        self.control_thread = None
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
        except OSError:
            udp_socket.close()
            raise
        self.socket = udp_socket
        self.running.set()
        self.control_thread = threading.Thread(target=self._control_loop, name="ros-map-stream-control")
        self.control_thread.daemon = True
        self.control_thread.start()
        self.watchdog_timer = self.rospy.Timer(self.rospy.Duration(0.1), self._watchdog)
        self.rospy.on_shutdown(self.close)
        self.rospy.loginfo(
            "ros_map_stream standby; listening on %s:%d for ground station %s",
            self.config["bind_host"], self.config["control_port"], self.config["ground_station_ip"],
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
        self.rospy.loginfo(
            "received %s map=%s session=%s request=%s",
            command["message_type"], command["map_id"], command["session_id"], command["payload"]["request_id"],
        )
        request_id = command["payload"]["request_id"]
        with self.lock:
            cached = self.request_cache.get(request_id)
            if cached and cached["expires_at"] >= self.clock():
                if (
                    cached["map_id"] == command["map_id"]
                    and cached["session_id"] == command["session_id"]
                    and cached["device_id"].casefold() == command["device_id"].casefold()
                    and cached["payload"]["command"] == command["message_type"]
                ):
                    self._send_ack(command, cached["payload"], cached["destination"])
                    return
                self.rospy.logwarn("ignored conflicting reuse of request ID %s", request_id)
                return
        if command["message_type"] == "start_mapping":
            self._handle_start(command, peer_ip)
        else:
            self._handle_stop(command)

    def _handle_start(self, command, peer_ip):
        payload = command["payload"]
        destination = (payload["return_host"], payload["return_port"])
        if payload["return_host"] != peer_ip or payload["return_port"] != self.config["data_port"]:
            self._reject(command, "start_mapping", "return address does not match configured ground station", "INVALID_CONFIG", (peer_ip, self.config["data_port"]))
            return
        if command["device_id"].casefold() != self.config["device_id"].casefold():
            self._reject(command, "start_mapping", "device ID does not match", "DEVICE_ID_MISMATCH", destination)
            return
        with self.lock:
            if self.session is not None:
                self._reject(command, "start_mapping", "another mapping session is active", "BUSY", destination)
                return
        unavailable = self._unavailable_input()
        if unavailable is not None:
            code, reason = unavailable
            self._reject(command, "start_mapping", reason, code, destination)
            return
        actual_rate = min(float(payload["cloud_rate_hz"]), self.config["max_cloud_rate_hz"])
        actual_voxel = max(float(payload["voxel_size_m"]), self.config["min_voxel_size_m"])
        session = MappingSession(command, destination, actual_rate, actual_voxel, self.config["pose_buffer_size"])
        with self.lock:
            if self.session is not None:
                self._reject(command, "start_mapping", "another mapping session is active", "BUSY", destination)
                return
            self.session = session
            self.state = "starting"
        try:
            self._subscribe_session(session)
        except Exception as exc:
            self._unregister(session)
            with self.lock:
                if self.session is session:
                    self.session = None
                    self.state = "standby"
            self._reject(command, "start_mapping", "cannot create ROS subscriptions: %s" % exc, "INTERNAL_ERROR", destination)
            return
        with self.lock:
            self.state = "mapping"
            session.state = "mapping"
            now = self.clock()
            session.last_heartbeat_at = now
            session.last_cloud_at = now
            session.last_pose_at = now
        ack = {
            "request_id": payload["request_id"],
            "command": "start_mapping",
            "accepted": True,
            "reason": "",
            "error_code": None,
            "actual_parameters": {"cloud_rate_hz": actual_rate, "voxel_size_m": actual_voxel},
        }
        self._cache_ack(command, ack, destination)
        self._send_ack(command, ack, destination)
        self._send_session_message(session, "session_status", {"state": "mapping", "reason": "", "error_code": None})
        self.rospy.loginfo(
            "mapping session accepted map=%s session=%s cloud_rate=%.3f voxel=%.3f",
            command["map_id"], command["session_id"], actual_rate, actual_voxel,
        )

    def _handle_stop(self, command):
        if command["device_id"].casefold() != self.config["device_id"].casefold():
            self._reject(command, "stop_mapping", "device ID does not match", "DEVICE_ID_MISMATCH", (self.config["ground_station_ip"], self.config["data_port"]))
            return
        with self.lock:
            session = self.session
            if session is None or command["map_id"] != session.identity["map_id"] or command["session_id"] != session.identity["session_id"]:
                self._reject(command, "stop_mapping", "mapping session does not match", "MAP_ID_MISMATCH", (self.config["ground_station_ip"], self.config["data_port"]))
                return
            self.state = "stopping"
            session.state = "stopping"
            session.token = uuid.uuid4().hex
            destination = session.destination
        self._unregister(session)
        ack = {
            "request_id": command["payload"]["request_id"],
            "command": "stop_mapping",
            "accepted": True,
            "reason": "",
            "error_code": None,
            "actual_parameters": {},
        }
        self._cache_ack(command, ack, destination)
        self._send_ack(command, ack, destination)
        self._send_session_message(session, "session_status", {"state": "stopped", "reason": command["payload"]["reason"], "error_code": None})
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"
        self.rospy.loginfo("mapping session stopped; node returned to standby")

    def _subscribe_session(self, session):
        resolver = self.message_resolver
        if resolver is None:
            import roslib.message
            resolver = roslib.message.get_message_class
        cloud_class = resolver(self.config["cloud_message_type"])
        pose_class = resolver(self.config["pose_message_type"])
        if cloud_class is None or pose_class is None:
            raise ConfigError("configured ROS message class is unavailable")
        token = session.token
        session.subscribers = [
            self.rospy.Subscriber(
                self.config["pose_topic"], pose_class,
                lambda message: self._pose_callback(token, message), queue_size=self.config["pose_buffer_size"],
            ),
            self.rospy.Subscriber(
                self.config["cloud_topic"], cloud_class,
                lambda message: self._cloud_callback(token, message), queue_size=1, buff_size=16 * 1024 * 1024,
            ),
        ]
        self.rospy.loginfo("subscribed pose topic %s [%s]", self.config["pose_topic"], self.config["pose_message_type"])
        self.rospy.loginfo("subscribed cloud topic %s [%s]", self.config["cloud_topic"], self.config["cloud_message_type"])

    def _unavailable_input(self):
        try:
            published = dict(self.rospy.get_published_topics())
        except Exception as exc:
            return "INTERNAL_ERROR", "cannot query ROS topics: %s" % exc
        cloud_type = published.get(self.config["cloud_topic"])
        pose_type = published.get(self.config["pose_topic"])
        if cloud_type != self.config["cloud_message_type"]:
            return "SENSOR_UNAVAILABLE", "cloud topic is unavailable or has the wrong type"
        if pose_type != self.config["pose_message_type"]:
            return "POSE_UNAVAILABLE", "pose topic is unavailable or has the wrong type"
        return None

    def _pose_callback(self, token, message):
        with self.lock:
            session = self.session
            if session is None or session.token != token or session.state not in ("starting", "mapping"):
                return
        try:
            if message.header.frame_id != self.config["map_frame"]:
                raise ProcessingError("pose frame_id does not match configured map frame")
            child_frame = getattr(message, "child_frame_id", self.config["body_frame"])
            if child_frame and child_frame != self.config["body_frame"]:
                raise ProcessingError("pose child_frame_id does not match configured body frame")
            transform = transform_from_pose(message, self.config["pose_position_path"], self.config["pose_orientation_path"])
            stamp_ns = stamp_to_ns(message.header.stamp)
        except (AttributeError, ProcessingError) as exc:
            self.rospy.logwarn_throttle(5.0, "pose preprocessing failed: %s" % exc)
            return
        with self.lock:
            if self.session is not session or session.token != token or session.state not in ("starting", "mapping"):
                return
            session.pose_buffer.add(PoseSample(stamp_ns, transform))
            session.last_pose_at = self.clock()

    def _cloud_callback(self, token, message):
        now = self.clock()
        with self.lock:
            session = self.session
            if session is None or session.token != token or session.state != "mapping":
                return
            session.last_cloud_at = now
            if now - session.last_frame_sent_at < 1.0 / session.cloud_rate_hz:
                return
        try:
            if message.header.frame_id != self.config["sensor_frame"]:
                raise ProcessingError("cloud frame_id does not match configured sensor frame")
            sample_stamp_ns = stamp_to_ns(message.header.stamp)
            pose = session.pose_buffer.closest(
                sample_stamp_ns, int(self.config["sync_tolerance_seconds"] * 1000000000)
            )
            if pose is None:
                self.rospy.logwarn_throttle(5.0, "cloud dropped: no pose within synchronization tolerance")
                return
            points = extract_pointcloud2(message)
            points = preprocess_points(
                points,
                self.config["min_range_m"],
                self.config["max_range_m"],
                session.voxel_size_m,
                self.config["max_frame_points"],
            )
            if not len(points):
                self.rospy.logwarn_throttle(5.0, "cloud dropped: preprocessing produced no points")
                return
            raw = points.tobytes(order="C")
            if len(raw) > self.config["max_decompressed_bytes"]:
                raise ProcessingError("processed cloud exceeds decompressed byte limit")
            compressed = zlib.compress(raw)
        except (AttributeError, ProcessingError, ValueError, zlib.error) as exc:
            self.rospy.logwarn_throttle(5.0, "cloud preprocessing failed: %s" % exc)
            return
        with self.lock:
            if self.session is not session or session.token != token or session.state != "mapping":
                return
            frame_id = session.frame_id
            session.frame_id += 1
            first_sequence = self._peek_sequence(session.identity["session_id"])
            metadata = {
                "frame_id": frame_id,
                "sample_stamp_ns": sample_stamp_ns,
                "point_count": len(points),
                "map_from_body": pose.transform,
                "body_from_sensor": self.config["body_from_sensor"],
            }
            try:
                datagrams = encode_cloud_chunks(self.config, session.identity, first_sequence, metadata, compressed)
            except ProtocolError as exc:
                self.rospy.logerr("cloud frame encoding failed: %s", exc)
                return
            self.sequences[session.identity["session_id"]] = first_sequence + len(datagrams)
            session.last_frame_sent_at = now
        sent = 0
        for datagram in datagrams:
            with self.lock:
                if self.session is not session or session.token != token or session.state != "mapping":
                    break
                try:
                    self.socket.sendto(datagram, session.destination)
                    sent += 1
                except OSError as exc:
                    self.rospy.logerr("cloud UDP send failed: %s", exc)
                    break
        if sent == len(datagrams):
            self.rospy.loginfo(
                "cloud frame sent frame=%d points=%d chunks=%d raw_bytes=%d compressed_bytes=%d",
                frame_id, len(points), len(datagrams), len(raw), len(compressed),
            )

    def _watchdog(self, unused_event=None):
        now = self.clock()
        with self.lock:
            expired = [key for key, value in self.request_cache.items() if value["expires_at"] < now]
            expired_sessions = set()
            for key in expired:
                cached = self.request_cache.pop(key, None)
                if cached is not None:
                    expired_sessions.add(cached["session_id"])
            active_session_id = self.session.identity["session_id"] if self.session is not None else None
            cached_session_ids = {value["session_id"] for value in self.request_cache.values()}
            for session_id in expired_sessions:
                if session_id != active_session_id and session_id not in cached_session_ids:
                    self.sequences.pop(session_id, None)
            session = self.session
            if session is None or session.state != "mapping":
                return
            heartbeat_due = now - session.last_heartbeat_at >= 1.0
            cloud_timed_out = now - session.last_cloud_at >= self.config["input_timeout_seconds"]
            pose_timed_out = now - session.last_pose_at >= self.config["input_timeout_seconds"]
            if heartbeat_due:
                session.last_heartbeat_at = now
        if cloud_timed_out:
            self._fail_session(session, "point cloud topic timed out", "SENSOR_UNAVAILABLE")
            return
        if pose_timed_out:
            self._fail_session(session, "pose topic timed out", "POSE_UNAVAILABLE")
            return
        if heartbeat_due:
            self._send_session_message(session, "session_heartbeat", {"state": "mapping"})

    def _fail_session(self, session, reason, error_code):
        with self.lock:
            if self.session is not session:
                return
            self.state = "error"
            session.state = "error"
            session.token = uuid.uuid4().hex
        self._send_session_message(session, "session_status", {"state": "error", "reason": reason, "error_code": error_code})
        self._unregister(session)
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"
        self.rospy.logerr("mapping session failed: %s (%s); returned to standby", reason, error_code)

    def _reject(self, command, command_name, reason, error_code, destination):
        if error_code not in ERROR_CODES:
            error_code = "INTERNAL_ERROR"
        payload = {
            "request_id": command["payload"]["request_id"],
            "command": command_name,
            "accepted": False,
            "reason": reason,
            "error_code": error_code,
            "actual_parameters": {},
        }
        self._cache_ack(command, payload, destination)
        self._send_ack(command, payload, destination)
        self.rospy.logwarn("rejected %s: %s (%s)", command_name, reason, error_code)

    def _cache_ack(self, command, payload, destination):
        with self.lock:
            self.request_cache[payload["request_id"]] = {
                "payload": payload,
                "destination": destination,
                "map_id": command["map_id"],
                "session_id": command["session_id"],
                "device_id": command["device_id"],
                "expires_at": self.clock() + self.config["command_cache_seconds"],
            }

    def _send_ack(self, command, payload, destination):
        identity = {"map_id": command["map_id"], "session_id": command["session_id"]}
        self._send(identity, destination, "command_ack", payload)
        self.rospy.loginfo(
            "sent %s ACK request=%s accepted=%s",
            payload["command"], payload["request_id"], payload["accepted"],
        )

    def _send_session_message(self, session, message_type, payload):
        self._send(session.identity, session.destination, message_type, payload)

    def _peek_sequence(self, session_id):
        return self.sequences.get(session_id, 0)

    def _send(self, identity, destination, message_type, payload):
        with self.lock:
            sequence = self._peek_sequence(identity["session_id"])
            self.sequences[identity["session_id"]] = sequence + 1
        try:
            datagram = encode_envelope(self.config, identity, message_type, sequence, payload)
            self.socket.sendto(datagram, destination)
        except (OSError, ProtocolError) as exc:
            self.rospy.logerr("mapping UDP send failed type=%s: %s", message_type, exc)

    def _unregister(self, session):
        for subscriber in session.subscribers:
            try:
                subscriber.unregister()
            except Exception:
                pass
        session.subscribers = []
        session.pose_buffer.clear()

    def close(self):
        if not self.running.is_set():
            return
        self.running.clear()
        with self.lock:
            session = self.session
            if session is not None:
                session.token = uuid.uuid4().hex
        if session is not None:
            self._send_session_message(session, "session_status", {"state": "stopped", "reason": "node shutdown", "error_code": None})
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
        thread = self.control_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.control_thread = None
        self.rospy.loginfo("ros_map_stream stopped")


def run():
    import rospy
    import rospkg

    rospy.init_node("ros_map_stream")
    rospack = rospkg.RosPack()
    package_path = rospack.get_path("ros_map_stream")
    device_package_path = rospack.get_path("edge_device_config")
    mapping_path = rospy.get_param("~mapping_config_file", package_path + "/config/mapping.yaml")
    device_path = rospy.get_param("~device_config_file", device_package_path + "/config/device.yaml")
    try:
        config = load_config(mapping_path, device_path)
        node = RosMapStreamNode(rospy, config)
        node.start()
    except (ConfigError, OSError) as exc:
        rospy.logfatal("ros_map_stream startup failed: %s", exc)
        return
    rospy.spin()
