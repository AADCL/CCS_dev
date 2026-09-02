import datetime
import socket
import threading
import time

from .config import ConfigError, load_config
from .protocol import ProtocolError, decode, encode, signature
from .storage import StorageError, TrajectoryStore, decode_trajectory
from .mission_store import MissionStore


ACK_COMMANDS = {"negotiate_task", "read_task", "task_prepare", "task_commit", "execute_task",
                "terminate_task", "emergency_stop", "delete_task"}


class Transfer(object):
    def __init__(self, command, now):
        payload = command["payload"]
        self.identity = {key: command[key] for key in ("task_id", "subtask_id", "device_id")}
        self.revision = payload["revision"]
        self.chunk_count = payload["chunk_count"]
        self.compressed_bytes = payload["compressed_bytes"]
        self.raw_bytes = payload["raw_bytes"]
        self.crc32 = payload["crc32"]
        self.chunks = {}
        self.updated_at = now


class Execution(object):
    def __init__(self, command, trajectory, scheduled_at):
        self.identity = {key: command[key] for key in ("task_id", "subtask_id", "device_id", "execution_id")}
        self.revision = command["payload"]["revision"]
        self.request_id = command["request_id"]
        self.trajectory = trajectory
        self.scheduled_at = scheduled_at
        self.state = "scheduling"
        self.last_feedback_at = time.monotonic()
        self.last_heartbeat_at = 0.0
        self.last_waypoint = None
        self.adapter_request_ids = {self.request_id}

    def persisted(self):
        result = dict(self.identity)
        result.update({"revision": self.revision, "request_id": self.request_id,
                       "xml_path": self.trajectory["xml_path"], "frame_id": self.trajectory["frame_id"],
                       "map_id": self.trajectory.get("map_id", ""),
                       "scheduled_at": self.scheduled_at, "state": self.state})
        return result


class Preparation(object):
    def __init__(self, record, request_id, now):
        self.identity = {key: str(record.get(key, "")) for key in ("task_id", "subtask_id", "device_id")}
        self.revision = int(record["revision"])
        self.request_id = str(request_id)
        self.record = dict(record)
        self.state = "received"
        self.last_feedback_at = now
        self.last_publish_at = 0.0
        self.error_code = ""
        self.message = "task stored; navigation preparation pending"


class RosTaskControlNode(object):
    def __init__(self, rospy, config, command_class, feedback_class, socket_factory=socket.socket,
                 clock=time.monotonic, wall_clock=time.time, store=None, status_class=None):
        self.rospy = rospy
        self.config = config
        self.command_class = command_class
        self.feedback_class = feedback_class
        self.status_class = status_class
        self.socket_factory = socket_factory
        self.clock = clock
        self.wall_clock = wall_clock
        self.store = store or TrajectoryStore(config["storage_directory"])
        self.mission_store = MissionStore(config["storage_directory"])
        self.lock = threading.RLock()
        self.running = threading.Event()
        self.socket = None
        self.thread = None
        self.timer = None
        self.publisher = None
        self.subscriber = None
        self.status_publisher = None
        self.transfer = None
        self.execution = None
        self.preparation = None
        self.pending_cleanup = None
        self.pending_execute = None
        self.ack_cache = {}
        self.sequence = 0
        self.state = "no_task"

    def _set_state(self, state):
        self.state = str(state)
        if self.status_publisher is not None:
            message = self.status_class()
            message.data = self.state
            self.status_publisher.publish(message)
        self.rospy.loginfo("task ROS status state=%s", self.state)

    def start(self):
        udp_socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.bind((self.config["bind_host"], self.config["control_port"]))
            udp_socket.settimeout(0.2)
        except OSError:
            udp_socket.close()
            raise
        self.socket = udp_socket
        self.publisher = self.rospy.Publisher(self.config["command_topic"], self.command_class, queue_size=10, latch=False)
        if self.status_class is not None:
            self.status_publisher = self.rospy.Publisher(
                self.config["status_topic"], self.status_class, queue_size=1, latch=True)
            self._set_state(self.state)
        self.subscriber = self.rospy.Subscriber(self.config["feedback_topic"], self.feedback_class, self.feedback_callback, queue_size=20)
        self._recover_execution()
        self._recover_preparation()
        self.running.set()
        self.thread = threading.Thread(target=self._control_loop, name="ros-task-control-udp")
        self.thread.daemon = True
        self.thread.start()
        self.timer = self.rospy.Timer(self.rospy.Duration(0.1), self.watchdog)
        self.rospy.on_shutdown(self.close)
        self.rospy.loginfo("epgeneral_task_control listening on %s:%d; status target %s:%d",
                           self.config["bind_host"], self.config["control_port"],
                           self.config["ground_station_ip"], self.config["status_port"])

    def _recover_execution(self):
        record = self.store.load_execution()
        if not isinstance(record, dict) or record.get("state") not in ("scheduling", "scheduled", "running"):
            return
        action = "STOP" if record.get("state") == "running" else "CANCEL"
        try:
            self._publish_command(action, record, record.get("request_id", "recovery"))
            self.rospy.logwarn("recovered interrupted execution %s; published %s", record.get("execution_id"), action)
        finally:
            self.store.clear_execution()

    def _recover_preparation(self):
        record = self.mission_store.latest(self.config["device_id"])
        if not isinstance(record, dict) or record.get("state") in ("no_task", "emergency_stop"):
            return
        metadata = self.store.load(record.get("task_id", ""), record.get("subtask_id", ""))
        if metadata is None or int(metadata.get("revision", 0)) != int(record.get("revision", 0)):
            return
        self._begin_preparation(dict(metadata, map_id=record.get("map_id", "")), "recovery")

    def _control_loop(self):
        while self.running.is_set():
            udp_socket = self.socket
            if udp_socket is None:
                return
            try:
                datagram, address = udp_socket.recvfrom(self.config["max_datagram_bytes"] + 1)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self.handle_datagram(datagram, address[0])
            except Exception as exc:
                self.rospy.logerr("task command handling failed: %s", exc)

    def handle_datagram(self, datagram, peer_ip):
        if peer_ip != self.config["ground_station_ip"]:
            self.rospy.logwarn("ignored task command from unexpected IP %s", peer_ip)
            return
        try:
            command = decode(datagram, self.config)
        except ProtocolError as exc:
            self.rospy.logwarn("invalid task command: %s", exc)
            return
        if command["device_id"].casefold() != self.config["device_id"].casefold():
            self._ack(command, False, "device ID does not match", "UNKNOWN_TASK")
            return
        self.rospy.loginfo("received %s task=%s subtask=%s execution=%s request=%s",
                           command["message_type"], command["task_id"], command["subtask_id"],
                           command["execution_id"], command["request_id"])
        with self.lock:
            if command["message_type"] != "task_chunk" and self._repeat(command):
                return
            handlers = {"negotiate_task": self._negotiate, "read_task": self._read_task,
                        "task_prepare": self._prepare, "task_chunk": self._chunk, "task_commit": self._commit,
                        "execute_task": self._execute, "terminate_task": self._terminate,
                        "emergency_stop": self._emergency_stop, "delete_task": self._delete_task}
            handlers[command["message_type"]](command)

    def _negotiate(self, command):
        status = self.mission_store.status(command["task_id"], command["device_id"])
        self._ack(command, True, cache=True)
        self._send(command, "task_summary", status)

    def _read_task(self, command):
        record = self.mission_store.load(command["task_id"], command["device_id"])
        self._ack(command, bool(record), "task not found" if record is None else "", "UNKNOWN_TASK" if record is None else None)
        if record is not None:
            self._send(command, "task_transfer_prepare", {"revision": record.get("revision", 0), "payload": record})

    def _delete_task(self, command):
        if self.execution is not None:
            self._ack(command, False, "cannot delete an active execution", "BUSY")
            return
        record = self._stored_record(command)
        if record is not None:
            self._cache(command, None)
            self._begin_cleanup(command, record, "delete")
            return
        self._complete_cleanup(command, "delete")

    def _terminate(self, command):
        execution = self.execution
        if execution is None or not self._matches_execution(command, execution):
            self._ack(command, False, "execution does not match", "EXECUTION_CONFLICT")
            return
        if execution.state in ("scheduling", "scheduled"):
            self._cancel(command)
        elif execution.state == "running":
            self._stop(command)
        else:
            self._ack(command, False, "execution is not active", "EXECUTION_CONFLICT")

    def _emergency_stop(self, command):
        record = self.execution.persisted() if self.execution is not None else self._stored_record(command)
        if self.execution is not None:
            self.execution = None
            self.store.clear_execution()
            self.pending_execute = None
        self._set_state("emergency_stop")
        self._send(command, "task_status", {
            "state": "emergency_stop", "message": "emergency stop accepted",
            "error_code": None,
        })
        self._ack(command, True)
        if record is not None:
            self._begin_cleanup(command, record, "emergency")
        else:
            self._complete_cleanup(command, "emergency")

    def _repeat(self, command):
        cached = self.ack_cache.get(command["request_id"])
        if cached is None:
            return False
        if cached["expires_at"] < self.clock():
            self.ack_cache.pop(command["request_id"], None)
            return False
        if cached["signature"] == signature(command):
            if cached.get("payload") is not None:
                self._send(command, "command_ack", cached["payload"])
            return True
        self.rospy.logwarn("ignored conflicting reuse of request ID %s", command["request_id"])
        payload = self._ack_payload(command, False, "request ID was reused with different content", "INTERNAL_ERROR")
        self._send(command, "command_ack", payload)
        return True

    def _prepare(self, command):
        payload = command["payload"]
        try:
            revision = self._integer(payload, "revision", 1)
            chunk_count = self._integer(payload, "chunk_count", 1, self.config["max_chunks"])
            compressed = self._integer(payload, "compressed_bytes", 1, self.config["max_compressed_bytes"])
            raw_bytes = self._integer(payload, "raw_bytes", 1, self.config["max_raw_bytes"])
            crc32 = self._integer(payload, "crc32", 0, 0xFFFFFFFF)
            if payload.get("compression") != "zlib" or payload.get("encoding") != "json-utf8":
                raise ValueError("unsupported transfer encoding")
            if self.execution is not None and self._same_subtask(command, self.execution.identity):
                raise RuntimeError("active execution protects this trajectory")
            if self.transfer is not None and not self._same_subtask(command, self.transfer.identity):
                raise RuntimeError("another task transfer is active")
            existing = self.store.load(command["task_id"], command["subtask_id"])
            if existing is not None and revision < existing["revision"]:
                self._ack(command, False, "trajectory revision is older than stored revision", "REVISION_MISMATCH")
                return
            if existing is not None and revision == existing["revision"] and crc32 != existing["crc32"]:
                self._ack(command, False, "stored revision has different content", "REVISION_MISMATCH")
                return
            normalized = dict(command)
            normalized["payload"] = dict(payload, revision=revision, chunk_count=chunk_count,
                                          compressed_bytes=compressed, raw_bytes=raw_bytes, crc32=crc32)
            self.transfer = Transfer(normalized, self.clock())
            self._set_state("receiving")
        except RuntimeError as exc:
            self._ack(command, False, str(exc), "BUSY")
            return
        except (KeyError, TypeError, ValueError) as exc:
            self._ack(command, False, str(exc), "INVALID_WAYPOINT")
            return
        self._ack(command, True)
        self.rospy.loginfo("accepted trajectory transfer revision=%d chunks=%d compressed=%d",
                           revision, chunk_count, compressed)

    def _chunk(self, command):
        payload = command["payload"]
        with self.lock:
            transfer = self.transfer
            if transfer is None or not self._same_subtask(command, transfer.identity):
                return
            try:
                revision = self._integer(payload, "revision", 1)
                count = self._integer(payload, "chunk_count", 1, self.config["max_chunks"])
                index = self._integer(payload, "chunk_index", 0, count - 1)
                crc32 = self._integer(payload, "crc32", 0, 0xFFFFFFFF)
                data = payload["data"]
                if not isinstance(data, bytes) or revision != transfer.revision or count != transfer.chunk_count or crc32 != transfer.crc32:
                    return
                if index not in transfer.chunks:
                    if sum(len(item) for item in transfer.chunks.values()) + len(data) > transfer.compressed_bytes:
                        return
                    transfer.chunks[index] = data
                transfer.updated_at = self.clock()
            except (KeyError, TypeError, ValueError):
                return

    def _commit(self, command):
        payload = command["payload"]
        transfer = self.transfer
        try:
            revision = self._integer(payload, "revision", 1)
            count = self._integer(payload, "chunk_count", 1, self.config["max_chunks"])
            crc32 = self._integer(payload, "crc32", 0, 0xFFFFFFFF)
        except (KeyError, TypeError, ValueError) as exc:
            self._ack(command, False, str(exc), "INVALID_WAYPOINT")
            return
        existing = self.store.load(command["task_id"], command["subtask_id"])
        if transfer is None:
            if existing and existing["revision"] == revision and existing["crc32"] == crc32:
                self._ack(command, True)
                if (self.preparation is None or self.preparation.revision != revision or
                        not self._same_subtask(command, self.preparation.identity)):
                    self._begin_preparation(existing, command["request_id"])
            else:
                self._ack(command, False, "no matching transfer", "UNKNOWN_TASK")
            return
        if not self._same_subtask(command, transfer.identity) or (revision, count, crc32) != (transfer.revision, transfer.chunk_count, transfer.crc32):
            self._ack(command, False, "commit metadata does not match transfer", "REVISION_MISMATCH")
            return
        missing = [index for index in range(count) if index not in transfer.chunks]
        if missing:
            self._ack(command, True, missing=missing, cache=False)
            return
        compressed = b"".join(transfer.chunks[index] for index in range(count))
        if len(compressed) != transfer.compressed_bytes:
            self._ack(command, False, "compressed size does not match prepare", "CRC_ERROR")
            return
        identity = dict(transfer.identity, revision=revision)
        try:
            trajectory = decode_trajectory(compressed, crc32, self.config, identity, transfer.raw_bytes)
            metadata = self.store.commit(trajectory, crc32)
            self.mission_store.save(trajectory, "received")
        except (StorageError, OSError) as exc:
            self._set_state("error")
            error_text = str(exc)
            if "frame_id" in error_text:
                error_code = "MAP_FRAME_MISMATCH"
            elif any(word in error_text for word in ("CRC32", "zlib", "compressed", "decompressed", "raw size")):
                error_code = "CRC_ERROR"
            else:
                error_code = "INVALID_WAYPOINT"
            self._ack(command, False, error_text, error_code)
            self.transfer = None
            self._set_state("ready" if existing else "no_task")
            return
        self.transfer = None
        self._set_state("received")
        self._ack(command, True)
        self._begin_preparation(metadata, command["request_id"])
        self.rospy.loginfo("trajectory XML committed revision=%d waypoints=%d path=%s",
                           revision, len(trajectory["waypoints"]), metadata["xml_path"])

    def _execute(self, command):
        if not command["execution_id"]:
            self._ack(command, False, "execution ID is required", "EXECUTION_CONFLICT")
            return
        if self.execution is not None:
            self._ack(command, False, "another execution is active", "BUSY")
            return
        try:
            revision = self._integer(command["payload"], "revision", 1)
            scheduled_at = self._parse_utc(command["payload"].get("scheduled_at"))
        except (KeyError, TypeError, ValueError) as exc:
            self._ack(command, False, str(exc), "CLOCK_UNSYNCED")
            return
        now = self.wall_clock()
        message_time = command["sent_at_ns"] / 1000000000.0
        if abs(now - message_time) > self.config["utc_tolerance_seconds"] or scheduled_at <= now:
            self._ack(command, False, "UTC clock is not synchronized or schedule is in the past", "CLOCK_UNSYNCED")
            return
        if scheduled_at - now <= self.config["adapter_feedback_seconds"]:
            self._ack(command, False, "schedule leaves insufficient time for adapter confirmation", "CLOCK_UNSYNCED")
            return
        trajectory = self.store.load(command["task_id"], command["subtask_id"])
        if trajectory is None:
            self._ack(command, False, "trajectory is not stored", "UNKNOWN_TASK")
            return
        if trajectory["revision"] != revision or trajectory["device_id"].casefold() != self.config["device_id"].casefold():
            self._ack(command, False, "stored trajectory revision does not match", "REVISION_MISMATCH")
            return
        preparation = self.preparation
        if (preparation is None or preparation.state != "ready" or
                preparation.revision != revision or not self._same_subtask(command, preparation.identity)):
            self._ack(command, False, "navigation is not prepared for this trajectory", "NAVIGATION_NOT_READY")
            return
        if hasattr(self.publisher, "get_num_connections") and self.publisher.get_num_connections() < 1:
            self._ack(command, False, "ROS execution adapter is unavailable", "INTERNAL_ERROR")
            return
        execution = Execution(command, trajectory, scheduled_at)
        execution.last_feedback_at = self.clock()
        self.execution = execution
        self.pending_execute = command
        self._set_state("scheduling")
        self.store.save_execution(execution.persisted())
        self._cache(command, None)
        self._publish_command("SCHEDULE", execution.persisted(), command["request_id"])
        self.rospy.loginfo("execution %s submitted to ROS adapter for UTC %.6f", command["execution_id"], scheduled_at)

    def _cancel(self, command):
        execution = self.execution
        if execution is None or not self._matches_execution(command, execution):
            self._ack(command, False, "execution does not match", "EXECUTION_CONFLICT")
            return
        if execution.state not in ("scheduling", "scheduled"):
            self._ack(command, False, "execution is not cancellable", "EXECUTION_CONFLICT")
            return
        self._publish_command("CANCEL", execution.persisted(), command["request_id"])
        execution.adapter_request_ids.add(command["request_id"])
        self._ack(command, True)

    def _stop(self, command):
        execution = self.execution
        if execution is None or not self._matches_execution(command, execution):
            self._ack(command, False, "execution does not match", "EXECUTION_CONFLICT")
            return
        if execution.state != "running":
            self._ack(command, False, "execution is not running", "EXECUTION_CONFLICT")
            return
        self._publish_command("STOP", execution.persisted(), command["request_id"])
        execution.adapter_request_ids.add(command["request_id"])
        self._ack(command, True)

    def _begin_preparation(self, record, request_id):
        record = dict(record)
        payload = self.store.load_payload(record.get("task_id", ""), record.get("subtask_id", ""))
        if payload is not None:
            record["map_id"] = payload.get("map_id", record.get("map_id", ""))
            if self.mission_store.load(record["task_id"], record["device_id"]) is None:
                self.mission_store.save(payload, "received")
        preparation = Preparation(record, request_id, self.clock())
        self.preparation = preparation
        self._set_state("received")
        preparation.last_publish_at = self.clock()
        self._publish_command("PREPARE", preparation.record, preparation.request_id)

    def _stored_record(self, identity):
        preparation = self.preparation
        if preparation is not None and self._same_subtask(identity, preparation.identity):
            return preparation.record
        return self.store.load(identity["task_id"], identity["subtask_id"])

    def _begin_cleanup(self, command, record, kind):
        self.preparation = None
        self.pending_cleanup = {
            "command": command, "record": dict(record), "kind": kind,
            "last_publish_at": self.clock(),
        }
        self._publish_command("UNLOAD", record, command["request_id"])

    def _handle_cleanup_feedback(self, message):
        cleanup = self.pending_cleanup
        if cleanup is None or str(message.state).lower() != "unloaded":
            return False
        command = cleanup["command"]
        record = cleanup["record"]
        try:
            matches = (
                str(message.request_id) == command["request_id"] and
                str(message.task_id).casefold() == command["task_id"].casefold() and
                str(message.subtask_id).casefold() == command["subtask_id"].casefold() and
                str(message.device_id).casefold() == command["device_id"].casefold() and
                int(message.revision) == int(record.get("revision", 0))
            )
        except (AttributeError, TypeError, ValueError):
            return False
        if not matches:
            return False
        self._complete_cleanup(command, cleanup["kind"])
        return True

    def _complete_cleanup(self, command, kind):
        self.pending_cleanup = None
        self.preparation = None
        self.mission_store.delete(command["task_id"], command["device_id"])
        self.store.delete(command["task_id"], command["subtask_id"])
        self._set_state("no_task")
        if kind == "delete":
            self._ack(command, True)
        else:
            self._send(command, "task_status", {
                "state": "no_task", "message": "emergency cleanup completed",
                "error_code": None,
            })

    def _handle_preparation_feedback(self, message):
        preparation = self.preparation
        if preparation is None:
            return False
        try:
            matches = (
                str(message.request_id) == preparation.request_id and
                not str(message.execution_id) and
                str(message.task_id).casefold() == preparation.identity["task_id"].casefold() and
                str(message.subtask_id).casefold() == preparation.identity["subtask_id"].casefold() and
                str(message.device_id).casefold() == preparation.identity["device_id"].casefold() and
                int(message.revision) == preparation.revision
            )
        except (AttributeError, TypeError, ValueError):
            return False
        if not matches:
            return False
        state = str(message.state).lower()
        if state not in ("preparing", "ready", "failed"):
            return False
        preparation.last_feedback_at = self.clock()
        preparation.last_publish_at = preparation.last_feedback_at
        preparation.message = str(message.message)
        preparation.error_code = str(message.error_code)
        if state == "ready":
            preparation.state = "ready"
            self.mission_store.update_state(
                preparation.identity["task_id"], preparation.identity["device_id"], "ready")
            self._set_state("ready")
        elif state == "failed":
            preparation.state = "failed"
            self.mission_store.update_state(
                preparation.identity["task_id"], preparation.identity["device_id"], "failed")
            self._set_state("failed")
        else:
            preparation.state = "preparing"
            self.mission_store.update_state(
                preparation.identity["task_id"], preparation.identity["device_id"], "received")
            self._set_state("received")
        self._send(dict(preparation.identity, request_id=preparation.request_id), "task_summary", {
            "state": "received" if state == "preparing" else state,
            "revision": preparation.revision,
            "message": preparation.message,
            "error_code": preparation.error_code or None,
        })
        return True

    def feedback_callback(self, message):
        with self.lock:
            if self._handle_cleanup_feedback(message):
                return
            if self._handle_preparation_feedback(message):
                return
            execution = self.execution
            if execution is None or not self._feedback_matches(message, execution):
                return
            state = str(message.state).lower()
            allowed = {"scheduled", "running", "completed", "stopped", "failed"}
            if state not in allowed:
                return
            try:
                progress = float(message.progress)
                waypoint_index = int(message.waypoint_index)
                waypoint_count = int(message.waypoint_count)
                position = (float(message.position.x), float(message.position.y), float(message.position.z))
                import math
                if (not math.isfinite(progress) or not all(math.isfinite(item) for item in position) or
                        progress < 0.0 or progress > 1.0 or waypoint_count < 0 or
                        waypoint_index < -1 or (waypoint_count and waypoint_index >= waypoint_count)):
                    return
            except (AttributeError, TypeError, ValueError):
                return
            execution.last_feedback_at = self.clock()
            if execution.state == "scheduling" and state != "scheduled":
                if state == "failed":
                    self._status(execution, "failed", message.message or "adapter rejected execution",
                                 message.error_code or "INTERNAL_ERROR")
                    self._finish_scheduling(False, message.message or "adapter rejected execution", message.error_code or "INTERNAL_ERROR")
                return
            transitions = {"scheduled": {"scheduled", "running", "failed", "stopped"},
                           "running": {"running", "completed", "stopped", "failed"}}
            if execution.state != "scheduling" and state not in transitions.get(execution.state, set()):
                return
            if execution.state == "scheduling":
                execution.state = "scheduled"
                self._set_state("scheduled")
                self.store.save_execution(execution.persisted())
                self._finish_scheduling(True)
            else:
                execution.state = state
                self._set_state(state)
                self.store.save_execution(execution.persisted())
            self._status(execution, state, message.message, message.error_code)
            if state == "running":
                marker = (int(message.waypoint_index), int(message.waypoint_count))
                if marker != execution.last_waypoint:
                    execution.last_waypoint = marker
                    self._progress(execution, message)
            if state in ("completed", "stopped", "failed"):
                self._release_execution()

    def _finish_scheduling(self, accepted, reason="", error_code=None):
        command = self.pending_execute
        if command is None:
            return
        payload = self._ack_payload(command, accepted, reason, error_code)
        self._cache(command, payload)
        self._send(command, "command_ack", payload)
        self.pending_execute = None
        if not accepted:
            self._release_execution()

    def watchdog(self, unused_event=None):
        with self.lock:
            self._watchdog_locked()

    def _watchdog_locked(self):
        now = self.clock()
        for request_id, cached in list(self.ack_cache.items()):
            if cached["expires_at"] < now:
                self.ack_cache.pop(request_id, None)
        cleanup = self.pending_cleanup
        if cleanup is not None and now - cleanup["last_publish_at"] >= self.config["preparation_retry_seconds"]:
            cleanup["last_publish_at"] = now
            self._publish_command("UNLOAD", cleanup["record"], cleanup["command"]["request_id"])
        transfer = self.transfer
        if transfer is not None and now - transfer.updated_at > self.config["transfer_seconds"]:
            self.rospy.logwarn("trajectory transfer timed out and was discarded")
            existing = self.store.load(transfer.identity["task_id"], transfer.identity["subtask_id"])
            self.transfer = None
            self._set_state("ready" if existing is not None else "no_task")
        preparation = self.preparation
        if preparation is not None and preparation.state != "ready":
            if now - preparation.last_publish_at >= self.config["preparation_retry_seconds"]:
                preparation.last_publish_at = now
                preparation.state = "received"
                self._set_state("received")
                self._publish_command("PREPARE", preparation.record, preparation.request_id)
        execution = self.execution
        if execution is None:
            return
        timeout = self.config["adapter_feedback_seconds"] if execution.state == "scheduling" else self.config["execution_feedback_seconds"]
        if now - execution.last_feedback_at > timeout:
            if execution.state == "scheduling":
                self._finish_scheduling(False, "ROS execution adapter feedback timed out", "INTERNAL_ERROR")
            else:
                execution.adapter_request_ids.add("watchdog-stop")
                self._publish_command("STOP", execution.persisted(), "watchdog-stop")
                self._status(execution, "failed", "ROS execution feedback timed out", "INTERNAL_ERROR")
                self._release_execution()
            return
        if execution.state in ("scheduled", "running") and now - execution.last_heartbeat_at >= 1.0:
            execution.last_heartbeat_at = now
            self._send(execution.identity, "task_heartbeat", {"state": execution.state})
            self.rospy.loginfo("task heartbeat execution=%s state=%s", execution.identity["execution_id"], execution.state)

    def _status(self, execution, state, message="", error_code=""):
        self._send(execution.identity, "task_status", {"state": state, "message": str(message),
                                                        "error_code": str(error_code) if error_code else None})
        self.rospy.loginfo("execution %s state=%s message=%s", execution.identity["execution_id"], state, message)

    def _progress(self, execution, message):
        self._send(execution.identity, "waypoint_progress", {
            "state": execution.state, "waypoint_index": int(message.waypoint_index),
            "waypoint_count": int(message.waypoint_count), "progress": float(message.progress),
            "position": {"x": float(message.position.x), "y": float(message.position.y), "z": float(message.position.z)},
            "error_code": str(message.error_code) if message.error_code else None, "message": str(message.message),
        })

    def _ack(self, command, accepted, reason="", error_code=None, missing=None, cache=True):
        payload = self._ack_payload(command, accepted, reason, error_code, missing)
        if cache:
            self._cache(command, payload)
        self._send(command, "command_ack", payload)
        self.rospy.loginfo("sent %s ACK request=%s accepted=%s", command["message_type"], command["request_id"], accepted)

    @staticmethod
    def _ack_payload(command, accepted, reason="", error_code=None, missing=None):
        return {"accepted": bool(accepted), "command": command["message_type"], "reason": str(reason),
                "error_code": error_code, "missing_chunks": list(missing or [])}

    def _cache(self, command, payload):
        self.ack_cache[command["request_id"]] = {"signature": signature(command), "payload": payload,
                                                   "expires_at": self.clock() + self.config["ack_cache_seconds"]}

    def _send(self, identity, message_type, payload):
        self.sequence += 1
        request_id = identity.get("request_id", "status-%d" % self.sequence)
        datagram = encode(self.config, identity, message_type, self.sequence, payload, request_id)
        udp_socket = self.socket
        if udp_socket is None:
            return
        try:
            udp_socket.sendto(datagram, (self.config["ground_station_ip"], self.config["status_port"]))
        except OSError as exc:
            self.rospy.logerr("task UDP send failed type=%s: %s", message_type, exc)

    def _publish_command(self, action, record, request_id):
        message = self.command_class()
        message.action = getattr(message, action)
        for key in ("task_id", "subtask_id", "device_id", "execution_id"):
            setattr(message, key, str(record.get(key, "")))
        message.request_id = str(request_id)
        message.revision = int(record.get("revision", 0))
        message.xml_path = str(record.get("xml_path", ""))
        message.frame_id = str(record.get("frame_id", ""))
        message.map_id = str(record.get("map_id", ""))
        seconds = float(record.get("scheduled_at", 0.0))
        message.scheduled_at = self.rospy.Time.from_sec(seconds)
        self.publisher.publish(message)

    @staticmethod
    def _integer(payload, key, minimum, maximum=None):
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
            raise ValueError("%s is out of range" % key)
        return value

    @staticmethod
    def _same_subtask(command, identity):
        return all(command[key].casefold() == str(identity[key]).casefold() for key in ("task_id", "subtask_id", "device_id"))

    @staticmethod
    def _matches_execution(command, execution):
        return RosTaskControlNode._same_subtask(command, execution.identity) and command["execution_id"] == execution.identity["execution_id"]

    @staticmethod
    def _feedback_matches(message, execution):
        return (str(message.request_id) in execution.adapter_request_ids and
                str(message.task_id).casefold() == execution.identity["task_id"].casefold() and
                str(message.subtask_id).casefold() == execution.identity["subtask_id"].casefold() and
                str(message.device_id).casefold() == execution.identity["device_id"].casefold() and
                str(message.execution_id) == execution.identity["execution_id"] and int(message.revision) == execution.revision)

    @staticmethod
    def _parse_utc(value):
        if not isinstance(value, str) or not value:
            raise ValueError("scheduled_at is invalid")
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        if not normalized.endswith("+00:00"):
            raise ValueError("scheduled_at must be UTC")
        text = normalized[:-6]
        parsed = None
        for pattern in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.datetime.strptime(text, pattern)
                break
            except ValueError:
                pass
        if parsed is None:
            raise ValueError("scheduled_at is invalid")
        return (parsed - datetime.datetime(1970, 1, 1)).total_seconds()

    def _release_execution(self):
        self.execution = None
        self.pending_execute = None
        self.store.clear_execution()
        ready = self.preparation is not None and self.preparation.state == "ready"
        self._set_state("ready" if ready else "received")

    def close(self):
        if not self.running.is_set():
            return
        self.running.clear()
        if self.execution is not None:
            action = "STOP" if self.execution.state == "running" else "CANCEL"
            self._publish_command(action, self.execution.persisted(), "shutdown")
        if self.preparation is not None:
            self._publish_command("UNLOAD", self.preparation.record, "shutdown-unload")
        if self.timer is not None:
            self.timer.shutdown()
            self.timer = None
        udp_socket, self.socket = self.socket, None
        if udp_socket is not None:
            try:
                udp_socket.close()
            except OSError:
                pass
        if self.subscriber is not None:
            try:
                self.subscriber.unregister()
            except Exception:
                pass
        if self.thread is not None and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=2.0)
        self.rospy.loginfo("epgeneral_task_control stopped")


def run():
    import rospy
    import rospkg
    from std_msgs.msg import String
    from epgeneral_task_control.msg import TaskExecutionCommand, TaskExecutionFeedback

    rospy.init_node("epgeneral_task_control")
    rospack = rospkg.RosPack()
    device_path = rospack.get_path("epgeneral_device_config")
    task_config = rospy.get_param(
        "~task_config_file", device_path + "/config/task_control.yaml")
    device_config = rospy.get_param("~device_config_file", device_path + "/config/device.yaml")
    try:
        config = load_config(task_config, device_config)
        node = RosTaskControlNode(
            rospy, config, TaskExecutionCommand, TaskExecutionFeedback, status_class=String)
        node.start()
    except (ConfigError, OSError) as exc:
        rospy.logfatal("epgeneral_task_control startup failed: %s", exc)
        return
    rospy.spin()
