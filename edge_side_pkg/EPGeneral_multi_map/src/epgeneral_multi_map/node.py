import socket
import threading
import time
import uuid
import zlib

import numpy as np

from .config import ConfigError
from .models import MappingSession, PoseSample
from .processing import (
    PassThroughSliceProcessor, ProcessingError, extract_pointcloud2,
    preprocess_points, stamp_to_ns, synchronized_frame, transform_from_pose,
    transform_to_payload,
)
from .protocol import ProtocolError, decode_command, encode_cloud_chunks, encode_envelope
from .slicing import SliceError


ERROR_CODES = {
    "BUSY", "INVALID_CONFIG", "MAP_ID_MISMATCH", "DEVICE_ID_MISMATCH",
    "SENSOR_UNAVAILABLE", "POSE_UNAVAILABLE", "UNSUPPORTED_FORMAT", "INTERNAL_ERROR",
    "COLLABORATION_REQUIRED", "PARTICIPANT_SET_INVALID", "CLOCK_UNSYNCED",
    "START_TIME_MISSED", "START_LEAD_TOO_SHORT", "SESSION_MISMATCH",
    "STOP_TIME_MISSED", "INPUT_TIMEOUT", "EMPTY_SLICE", "SLICE_TRUNCATED",
}


class _SystemClock:
    @staticmethod
    def time_ns():
        return time.time_ns()

    @staticmethod
    def monotonic():
        return time.monotonic()


class RosMultiMapNode:
    def __init__(self, rospy, config, socket_factory=socket.socket, clock=None,
                 message_resolver=None, point_reader=None, slice_processor=None):
        self.rospy = rospy
        self.config = config
        self.socket_factory = socket_factory
        self.clock = clock or _SystemClock()
        self.message_resolver = message_resolver
        self.point_reader = point_reader
        self.slice_processor = slice_processor or PassThroughSliceProcessor()
        self.socket = None
        self.control_thread = None
        self.watchdog_thread = None
        self.watchdog_timer = None
        self.running = threading.Event()
        self.lock = threading.RLock()
        self.session = None
        self.state = "standby"
        self.request_cache = {}
        self.sequences = {}

    @property
    def subscription_count(self):
        with self.lock:
            return len(self.session.subscribers) if self.session is not None else 0

    @property
    def control_address(self):
        if self.socket is not None:
            try:
                return self.socket.getsockname()
            except (AttributeError, OSError):
                pass
        return self.config["bind_host"], self.config["control_port"]

    def start(self):
        with self.lock:
            if self.running.is_set():
                return
            udp_socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_socket.bind((self.config["bind_host"], self.config["control_port"]))
            udp_socket.settimeout(0.1)
            self.socket = udp_socket
            self.running.set()
            self.control_thread = threading.Thread(
                target=self._control_loop, name="epgeneral-multi-map-control", daemon=True)
            self.watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="epgeneral-multi-map-watchdog", daemon=True)
            self.control_thread.start()
            self.watchdog_thread.start()

    def _control_loop(self):
        while self.running.is_set():
            try:
                datagram, peer = self.socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                if self.running.is_set():
                    self.rospy.logerr("mapping UDP receive failed")
                break
            self.handle_datagram(datagram, peer[0])

    def _watchdog_loop(self):
        while self.running.is_set():
            time.sleep(0.02)
            if not self.running.is_set():
                break
            self._watchdog()

    def close(self):
        self.running.clear()
        with self.lock:
            session = self.session
        if session is not None:
            self._finish_session(
                session, max(session.start_at_ns + 1, self.clock.time_ns()),
                "INTERNAL_ERROR", "node closed")
        sock = self.socket
        self.socket = None
        if sock is not None:
            try:
                sock.close()
            except (AttributeError, OSError):
                pass
        current = threading.current_thread()
        for thread in (self.control_thread, self.watchdog_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1.0)
        self.control_thread = None
        self.watchdog_thread = None

    def handle_datagram(self, datagram, peer_ip):
        if peer_ip != self.config["ground_station_ip"]:
            self.rospy.logwarn("ignored mapping command from unexpected IP %s", peer_ip)
            return
        try:
            command = decode_command(datagram, self.config)
        except ProtocolError as exc:
            self._reject_incomplete(exc.command, peer_ip, str(exc))
            return
        request_id = command["payload"]["request_id"]
        with self.lock:
            cached = self.request_cache.get(request_id)
            if cached and cached["expires_at"] >= self.clock.monotonic():
                same = (
                    cached["map_id"] == command["map_id"]
                    and cached["session_id"] == command["session_id"]
                    and cached["device_id"].casefold() == command["device_id"].casefold()
                    and cached["payload"]["command"] == command["message_type"]
                )
                if same:
                    self._send_ack(command, cached["payload"], cached["destination"])
                return
        if command["message_type"] == "start_mapping":
            self._handle_start(command, peer_ip)
        else:
            self._handle_stop(command)

    def _reject_incomplete(self, command, peer_ip, reason):
        if not isinstance(command, dict) or command.get("message_type") not in (
                "start_mapping", "stop_mapping"):
            self.rospy.logwarn("invalid mapping command: %s", reason)
            return
        payload = command.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("request_id"), str):
            return
        code = "COLLABORATION_REQUIRED" if command["message_type"] == "start_mapping" else "INVALID_CONFIG"
        self._reject(command, command["message_type"], reason, code,
                     (peer_ip, self.config["data_port"]))

    def _handle_start(self, command, peer_ip):
        payload = command["payload"]
        destination = (payload["return_host"], payload["return_port"])
        if destination != (peer_ip, self.config["data_port"]):
            return self._reject(
                command, "start_mapping", "return address does not match configured ground station",
                "INVALID_CONFIG", (peer_ip, self.config["data_port"]),
            )
        if command["device_id"].casefold() != self.config["device_id"].casefold():
            return self._reject(
                command, "start_mapping", "device ID does not match",
                "DEVICE_ID_MISMATCH", destination,
            )
        with self.lock:
            if self.session is not None:
                return self._reject(
                    command, "start_mapping", "another mapping session is active",
                    "BUSY", destination,
                )
        wall_ns = self.clock.time_ns()
        if abs(command["sent_at_ns"] - wall_ns) > self.config["clock_skew_tolerance_ns"]:
            return self._reject(
                command, "start_mapping", "ground and robot clocks are not synchronized",
                "CLOCK_UNSYNCED", destination,
            )
        if payload["start_at_ns"] < wall_ns:
            return self._reject(
                command, "start_mapping", "shared start time already passed",
                "START_TIME_MISSED", destination,
            )
        if payload["start_at_ns"] < wall_ns + self.config["minimum_start_lead_ns"]:
            return self._reject(
                command, "start_mapping", "shared start lead is too short",
                "START_LEAD_TOO_SHORT", destination,
            )
        try:
            cloud_class, pose_class = self._resolve_input_types()
            self._verify_published_topics()
            session = MappingSession.from_command(command, self.config, destination)
            self._subscribe_session(session, cloud_class, pose_class)
        except (ConfigError, RuntimeError, OSError) as exc:
            code = "POSE_UNAVAILABLE" if "pose" in str(exc).lower() else "SENSOR_UNAVAILABLE"
            return self._reject(command, "start_mapping", str(exc), code, destination)
        except Exception as exc:
            return self._reject(
                command, "start_mapping", "cannot create ROS subscriptions: %s" % exc,
                "INTERNAL_ERROR", destination,
            )
        with self.lock:
            if self.session is not None:
                self._unregister(session)
                return self._reject(
                    command, "start_mapping", "another mapping session is active",
                    "BUSY", destination,
                )
            self.session = session
            self.state = "armed"
        ack = {
            "request_id": payload["request_id"], "command": "start_mapping",
            "accepted": True, "reason": "", "error_code": None,
            "actual_parameters": {
                "cloud_rate_hz": session.cloud_rate_hz,
                "voxel_size_m": session.voxel_size_m,
                "start_at_ns": session.start_at_ns,
                "slice_duration_ns": session.slice_duration_ns,
            },
        }
        self._cache_ack(command, ack, destination)
        self._send_ack(command, ack, destination)
        self._send_session_message(session, "session_status", {
            "state": "starting", "event": "armed", "reason": "", "error_code": None,
            "job_id": session.job_id, "start_at_ns": session.start_at_ns,
        })

    def _resolve_input_types(self):
        resolver = self.message_resolver
        if resolver is None:
            import roslib.message
            resolver = roslib.message.get_message_class
        cloud_class = resolver(self.config["cloud_message_type"])
        pose_class = resolver(self.config["pose_message_type"])
        if cloud_class is None:
            raise ConfigError("configured cloud ROS message class is unavailable")
        if pose_class is None:
            raise ConfigError("configured pose ROS message class is unavailable")
        return cloud_class, pose_class

    def _verify_published_topics(self):
        try:
            published = dict(self.rospy.get_published_topics())
        except Exception as exc:
            raise RuntimeError("cannot query ROS topics: %s" % exc)
        if published.get(self.config["cloud_topic"]) != self.config["cloud_message_type"]:
            raise RuntimeError("cloud topic is unavailable or has the wrong type")
        if published.get(self.config["pose_topic"]) != self.config["pose_message_type"]:
            raise RuntimeError("pose topic is unavailable or has the wrong type")

    def _subscribe_session(self, session, cloud_class, pose_class):
        token = session.token
        session.subscribers = [
            self.rospy.Subscriber(
                self.config["pose_topic"], pose_class,
                lambda message: self._pose_callback(token, message),
                queue_size=self.config["pose_buffer_size"],
            ),
            self.rospy.Subscriber(
                self.config["cloud_topic"], cloud_class,
                lambda message: self._cloud_callback(token, message),
                queue_size=1, buff_size=16 * 1024 * 1024,
            ),
        ]

    def _watchdog(self, unused_event=None):
        monotonic = self.clock.monotonic()
        started_now = False
        with self.lock:
            expired = [key for key, item in self.request_cache.items() if item["expires_at"] < monotonic]
            for key in expired:
                self.request_cache.pop(key, None)
            session = self.session
            if session is None:
                return
            wall_ns = self.clock.time_ns()
            if (session.last_watchdog_wall_ns is not None
                    and wall_ns + self.config["timestamp_rollback_tolerance_ns"]
                    < session.last_watchdog_wall_ns):
                clock_rollback = True
            else:
                clock_rollback = False
                session.last_watchdog_wall_ns = wall_ns
            if self.state == "armed":
                if wall_ns < session.start_at_ns:
                    return
                if wall_ns > session.start_at_ns + self.config["start_late_tolerance_ns"]:
                    missed = True
                else:
                    missed = False
                    session.mapping_started = True
                    session.state = "mapping"
                    session.last_cloud_monotonic = monotonic
                    session.last_pose_monotonic = monotonic
                    session.last_heartbeat_monotonic = monotonic
                    session.last_watchdog_wall_ns = wall_ns
                    self.state = "mapping"
                    started_now = True
            else:
                missed = False
                if self.state not in ("mapping", "stopping"):
                    return
        if clock_rollback:
            self._finish_session(
                session, max(session.start_at_ns + 1, wall_ns),
                "CLOCK_UNSYNCED", "robot wall clock moved backward")
            return
        if missed:
            self._fail_armed(session, "shared start time was missed", "START_TIME_MISSED")
            return
        if started_now:
            self._send_session_message(session, "session_status", {
                "state": "mapping", "event": "mapping_started", "reason": "",
                "error_code": None, "job_id": session.job_id,
            })
        if self.session is session and self.state in ("mapping", "stopping"):
            if (session.last_heartbeat_monotonic is None
                    or monotonic - session.last_heartbeat_monotonic >= 1.0):
                session.last_heartbeat_monotonic = monotonic
                self._send_session_message(session, "session_heartbeat", {
                    "state": session.state, "job_id": session.job_id,
                    "start_at_ns": session.start_at_ns,
                    "slice_duration_ns": session.slice_duration_ns,
                })
            if (session.last_cloud_monotonic is not None
                    and monotonic - session.last_cloud_monotonic > self.config["input_timeout_seconds"]):
                self._finish_session(
                    session, max(session.start_at_ns + 1, wall_ns),
                    "INPUT_TIMEOUT", "cloud input timed out")
                return
            if (session.last_pose_monotonic is not None
                    and monotonic - session.last_pose_monotonic > self.config["input_timeout_seconds"]):
                self._finish_session(
                    session, max(session.start_at_ns + 1, wall_ns),
                    "INPUT_TIMEOUT", "pose input timed out")
                return
            for batch in session.collector.seal_ready(wall_ns):
                self._upload_batch(session, batch)
            if self.session is session and self.state == "stopping" and wall_ns >= session.stop_at_ns:
                self._finish_session(session, session.stop_at_ns)

    def _fail_armed(self, session, reason, error_code):
        with self.lock:
            if self.session is not session:
                return
            session.state = "error"
            session.token = uuid.uuid4().hex
            self.state = "error"
        self._send_session_message(session, "session_status", {
            "state": "error", "event": "start_failed", "reason": reason,
            "error_code": error_code, "job_id": session.job_id,
        })
        self._unregister(session)
        session.collector.clear()
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"

    def _handle_stop(self, command):
        destination = (self.config["ground_station_ip"], self.config["data_port"])
        payload = command["payload"]
        with self.lock:
            session = self.session
            matches = (
                session is not None
                and command["map_id"] == session.identity["map_id"]
                and command["session_id"] == session.identity["session_id"]
                and command["device_id"].casefold() == self.config["device_id"].casefold()
                and payload["job_id"] == session.job_id
            )
        if not matches:
            return self._reject(
                command, "stop_mapping", "stop does not match active session",
                "SESSION_MISMATCH", destination)
        wall_ns = self.clock.time_ns()
        ack = {
            "request_id": payload["request_id"], "command": "stop_mapping",
            "accepted": True, "reason": "", "error_code": None,
            "actual_parameters": {"stop_at_ns": payload["stop_at_ns"]},
        }
        self._cache_ack(command, ack, destination)
        self._send_ack(command, ack, destination)
        with self.lock:
            if self.session is not session:
                return
            session.stop_at_ns = payload["stop_at_ns"]
            session.state = "stopping"
            self.state = "stopping"
        self._send_session_message(session, "session_status", {
            "state": "stopping", "event": "stop_scheduled", "reason": payload["reason"],
            "error_code": None, "job_id": session.job_id,
            "stop_at_ns": session.stop_at_ns,
        })
        if session.stop_at_ns <= wall_ns:
            self._finish_session(
                session, session.stop_at_ns, "STOP_TIME_MISSED",
                "stop command arrived after stop_at_ns")

    def _finish_session(self, session, stop_at_ns, error_code="", reason=""):
        with self.lock:
            if self.session is not session:
                return
            session.state = "error" if error_code else "stopping"
            session.token = uuid.uuid4().hex
            self.state = session.state
        # No further callbacks can enter after the token changes. Flush all complete
        # windows and then the requested incomplete tail.
        flush_time = stop_at_ns + self.config["late_arrival_ns"]
        for batch in session.collector.seal_ready(flush_time):
            self._upload_batch(session, batch)
        batch = session.collector.seal_tail(stop_at_ns, error_tail=bool(error_code))
        if batch is not None:
            self._upload_batch(session, batch)
        final_state = "error" if error_code else "stopped"
        self._send_session_message(session, "session_status", {
            "state": final_state, "event": "session_finished", "reason": reason,
            "error_code": error_code or None, "job_id": session.job_id,
            "stop_at_ns": stop_at_ns,
            "max_uploaded_stamp_ns": session.stats.max_uploaded_stamp_ns,
        })
        self._unregister(session)
        session.collector.clear()
        with self.lock:
            if self.session is session:
                self.session = None
                self.state = "standby"

    def _reject(self, command, command_name, reason, error_code, destination):
        payload = {
            "request_id": command["payload"]["request_id"], "command": command_name,
            "accepted": False, "reason": reason,
            "error_code": error_code if error_code in ERROR_CODES else "INTERNAL_ERROR",
            "actual_parameters": {},
        }
        self._cache_ack(command, payload, destination)
        self._send_ack(command, payload, destination)

    def _cache_ack(self, command, payload, destination):
        with self.lock:
            self.request_cache[payload["request_id"]] = {
                "payload": payload, "destination": destination,
                "map_id": command["map_id"], "session_id": command["session_id"],
                "device_id": command["device_id"],
                "expires_at": self.clock.monotonic() + self.config["command_cache_seconds"],
            }

    def _send_ack(self, command, payload, destination):
        self._send(
            {"map_id": command["map_id"], "session_id": command["session_id"]},
            destination, "command_ack", payload,
        )

    def _send_session_message(self, session, message_type, payload):
        self._send(session.identity, session.destination, message_type, payload)

    def _send(self, identity, destination, message_type, payload):
        with self.lock:
            sequence = self.sequences.get(identity["session_id"], 0)
            self.sequences[identity["session_id"]] = sequence + 1
        try:
            datagram = encode_envelope(self.config, identity, message_type, sequence, payload)
            if self.socket is not None:
                self.socket.sendto(datagram, destination)
        except (OSError, ProtocolError) as exc:
            self.rospy.logerr("mapping UDP send failed type=%s: %s", message_type, exc)

    def _unregister(self, session):
        for subscriber in session.subscribers:
            try:
                subscriber.unregister()
            except Exception:
                pass
        session.subscribers[:] = []
        session.pose_buffer.clear()

    def _active_session(self, token, states):
        session = self.session
        if session is None or session.token != token or session.state not in states:
            return None
        return session

    def _valid_message_stamp(self, session, stamp_ns):
        return abs(stamp_ns - self.clock.time_ns()) <= self.config["max_message_clock_offset_ns"]

    def _pose_callback(self, token, message):
        with self.lock:
            session = self._active_session(token, ("starting", "mapping", "stopping"))
        if session is None:
            return
        try:
            if message.header.frame_id != self.config["map_frame"]:
                raise ProcessingError("pose frame_id does not match configured map frame")
            child_frame = getattr(message, "child_frame_id", self.config["body_frame"])
            if child_frame and child_frame != self.config["body_frame"]:
                raise ProcessingError("pose child_frame_id does not match configured body frame")
            stamp_ns = stamp_to_ns(message.header.stamp)
            transform = transform_from_pose(
                message, self.config["pose_position_path"], self.config["pose_orientation_path"])
            sample = PoseSample(
                stamp_ns,
                np.asarray([transform["x"], transform["y"], transform["z"]]),
                np.asarray([transform["qx"], transform["qy"], transform["qz"], transform["qw"]]),
            )
            if not self._valid_message_stamp(session, stamp_ns):
                raise ProcessingError("pose timestamp is outside clock tolerance")
        except (AttributeError, ProcessingError, ValueError) as exc:
            session.stats.dropped_invalid += 1
            self.rospy.logwarn_throttle(5.0, "pose preprocessing failed: %s" % exc)
            return
        with self.lock:
            if self._active_session(token, ("starting", "mapping", "stopping")) is not session:
                return
            session.pose_buffer.add(sample)
            session.last_pose_monotonic = self.clock.monotonic()

    def _cloud_callback(self, token, message):
        with self.lock:
            session = self._active_session(token, ("mapping", "stopping"))
        if session is None:
            return
        try:
            if message.header.frame_id != self.config["sensor_frame"]:
                raise ProcessingError("cloud frame_id does not match configured sensor frame")
            stamp_ns = stamp_to_ns(message.header.stamp)
            if stamp_ns < session.start_at_ns or not self._valid_message_stamp(session, stamp_ns):
                raise ProcessingError("cloud timestamp is outside the active time range")
            if session.stop_at_ns is not None and stamp_ns >= session.stop_at_ns:
                raise ProcessingError("cloud timestamp is at or after stop time")
            previous = session.last_accepted_cloud_stamp_ns
            rollback = self.config["timestamp_rollback_tolerance_ns"]
            if previous is not None and stamp_ns + rollback < previous:
                raise ProcessingError("cloud timestamp rolled backward")
            minimum_interval = int(1_000_000_000 / session.cloud_rate_hz)
            if previous is not None and 0 <= stamp_ns - previous < minimum_interval:
                session.stats.dropped_invalid += 1
                return
            pose_match = session.pose_buffer.match(stamp_ns, self.config["sync_tolerance_ns"])
            if pose_match is None:
                session.stats.dropped_sync += 1
                return
            frame = synchronized_frame(message, stamp_ns, pose_match)
            result = session.collector.add(frame)
        except (AttributeError, ProcessingError, SliceError, ValueError) as exc:
            session.stats.dropped_invalid += 1
            self.rospy.logwarn_throttle(5.0, "cloud preprocessing failed: %s" % exc)
            return
        with self.lock:
            if self._active_session(token, ("mapping", "stopping")) is not session:
                return
            session.last_cloud_monotonic = self.clock.monotonic()
            session.last_accepted_cloud_stamp_ns = (
                stamp_ns if previous is None else max(previous, stamp_ns))
            if result == "late":
                session.stats.dropped_late += 1
            elif result == "truncated":
                session.stats.dropped_resource += 1

    def _prepare_frame(self, session, frame):
        points = extract_pointcloud2(frame.raw_message, self.point_reader)
        points = preprocess_points(
            points, self.config["min_range_m"], self.config["max_range_m"],
            session.voxel_size_m, self.config["max_frame_points"],
        )
        if not len(points):
            return None
        raw = points.tobytes(order="C")
        if len(raw) > self.config["max_decompressed_bytes"]:
            raise ProcessingError("processed cloud exceeds decompressed byte limit")
        return points, zlib.compress(raw)

    def _upload_batch(self, session, batch):
        processed = self.slice_processor.process(batch)
        prepared = []
        for frame in list(processed.frames):
            try:
                item = self._prepare_frame(session, frame)
            except (ProcessingError, ValueError, zlib.error) as exc:
                session.stats.dropped_invalid += 1
                self.rospy.logwarn_throttle(5.0, "cloud preprocessing failed: %s" % exc)
                continue
            if item is not None:
                prepared.append((frame, item[0], item[1]))
        for frame_index, (frame, points, compressed) in enumerate(prepared):
            self._send_prepared_frame(
                session, processed, frame_index, len(prepared), frame, points, compressed)
        self._send_slice_status(session, processed, len(prepared))
        processed.frames[:] = []

    def _send_prepared_frame(self, session, batch, frame_index, frame_count,
                             frame, points, compressed):
        with self.lock:
            frame_id = session.frame_id
            session.frame_id += 1
            first_sequence = self.sequences.get(session.identity["session_id"], 0)
            metadata = {
                "frame_id": frame_id, "sample_stamp_ns": frame.stamp_ns,
                "point_count": len(points),
                "map_from_body": transform_to_payload(frame.map_from_body),
                "body_from_sensor": self.config["body_from_sensor"],
                "job_id": session.job_id, "slice_id": batch.slice_id,
                "slice_start_ns": batch.start_ns, "slice_end_ns": batch.end_ns,
                "frame_index": frame_index, "frame_count": frame_count,
                "partial": batch.partial, "error_tail": batch.error_tail,
                "truncated": batch.truncated,
                "pose_before_stamp_ns": frame.pose_match.before_stamp_ns,
                "pose_after_stamp_ns": frame.pose_match.after_stamp_ns,
                "pose_max_error_ns": frame.pose_match.max_error_ns,
                "pose_interpolated": frame.pose_match.interpolated,
            }
            datagrams = encode_cloud_chunks(
                self.config, session.identity, first_sequence, metadata, compressed)
            self.sequences[session.identity["session_id"]] = first_sequence + len(datagrams)
        sent = 0
        for datagram in datagrams:
            try:
                if self.socket is not None:
                    self.socket.sendto(datagram, session.destination)
                sent += 1
            except OSError as exc:
                self.rospy.logerr("cloud UDP send failed: %s", exc)
                break
        if sent == len(datagrams):
            session.stats.max_uploaded_stamp_ns = max(
                session.stats.max_uploaded_stamp_ns, frame.stamp_ns)

    def _send_slice_status(self, session, batch, frame_count, error_code=None):
        if error_code is None:
            if batch.truncated:
                error_code = "SLICE_TRUNCATED"
            elif frame_count == 0:
                error_code = "EMPTY_SLICE"
        event = "slice_empty" if frame_count == 0 else "slice_complete"
        self._send_session_message(session, "session_status", {
            "state": session.state, "event": event, "reason": "",
            "error_code": error_code, "job_id": session.job_id,
            "slice_id": batch.slice_id, "slice_start_ns": batch.start_ns,
            "slice_end_ns": batch.end_ns, "frame_count": frame_count,
            "partial": batch.partial, "error_tail": batch.error_tail,
            "truncated": batch.truncated,
            "dropped_invalid": session.stats.dropped_invalid,
            "dropped_sync": session.stats.dropped_sync,
            "dropped_late": session.stats.dropped_late,
            "dropped_resource": session.stats.dropped_resource,
        })
