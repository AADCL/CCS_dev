import json
import os
import tempfile
import time
import unittest
import zlib
from types import SimpleNamespace

import msgpack

from epgeneral_task_control.config import load_config
from epgeneral_task_control.node import RosTaskControlNode
from epgeneral_task_control.storage import TrajectoryStore


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_CONFIG = os.path.join(PACKAGE, "config", "task_control.yaml")
DEVICE_CONFIG = os.path.join(PACKAGE, "test", "fixtures", "device.yaml")


class FakeSocket(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, destination):
        self.sent.append((data, destination))


class FakeCommand(object):
    SCHEDULE, CANCEL, STOP = 1, 2, 3

    def __init__(self):
        self.scheduled_at = None


class FakePublisher(object):
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)

    def get_num_connections(self):
        return 1


class FakeTimer(object):
    def shutdown(self): pass


class FakeSubscriber(object):
    def unregister(self): pass


class FakeTime(object):
    @staticmethod
    def from_sec(value):
        return value


class FakeRospy(object):
    Time = FakeTime

    def __init__(self):
        self.logs = []

    def Publisher(self, unused_topic, unused_class, **unused_kwargs):
        self.publisher = FakePublisher()
        return self.publisher

    def Subscriber(self, unused_topic, unused_class, unused_callback, **unused_kwargs): return FakeSubscriber()
    def Timer(self, unused_duration, unused_callback): return FakeTimer()
    def Duration(self, seconds): return seconds
    def on_shutdown(self, unused_callback): pass

    def loginfo(self, message, *args): self.logs.append(("info", message % args if args else message))
    def logwarn(self, message, *args): self.logs.append(("warning", message % args if args else message))
    def logerr(self, message, *args): self.logs.append(("error", message % args if args else message))


def pack(config, message_type, request_id, payload, execution_id="", sent_at=None):
    return msgpack.packb({
        "schema_version": 2, "protocol_id": config["protocol_id"], "task_id": "task-1",
        "subtask_id": "sub-1", "device_id": config["device_id"], "execution_id": execution_id,
        "message_type": message_type, "request_id": request_id, "sequence": 1,
        "sent_at_ns": int((sent_at if sent_at is not None else time.time()) * 1000000000), "payload": payload,
    }, use_bin_type=True)


def task_payload(device_id):
    return {"schema_version": 2, "task_id": "task-1", "task_name": "巡检", "map_id": "map-1",
            "frame_id": "map", "subtask_id": "sub-1", "device_id": device_id, "revision": 1,
            "cruise_speed_mps": 1.0, "start_delay_seconds": 0.0, "waypoints": [
                {"index": 0, "waypoint_id": "a", "x": 0.0, "y": 0.0, "z": 1.0},
                {"index": 1, "waypoint_id": "b", "x": 1.0, "y": 0.0, "z": 1.0}]}


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = dict(load_config(TASK_CONFIG, DEVICE_CONFIG))
        self.config["storage_directory"] = self.temp.name
        self.rospy = FakeRospy()
        self.node = RosTaskControlNode(self.rospy, self.config, FakeCommand, object,
                                       store=TrajectoryStore(self.temp.name))
        self.node.socket = FakeSocket()
        self.node.publisher = FakePublisher()

    def tearDown(self):
        self.temp.cleanup()

    def messages(self):
        return [msgpack.unpackb(item[0], raw=False) for item in self.node.socket.sent]

    def deliver(self, split=True):
        raw = json.dumps(task_payload(self.config["device_id"]), sort_keys=True, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(raw)
        crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
        chunks = [compressed[:len(compressed) // 2], compressed[len(compressed) // 2:]] if split else [compressed]
        prepare = {"revision": 1, "chunk_count": len(chunks), "compressed_bytes": len(compressed),
                   "raw_bytes": len(raw), "crc32": crc32, "compression": "zlib", "encoding": "json-utf8"}
        self.node.handle_datagram(pack(self.config, "task_prepare", "prepare", prepare), self.config["ground_station_ip"])
        for index in reversed(range(len(chunks))):
            payload = {"revision": 1, "chunk_count": len(chunks), "chunk_index": index, "crc32": crc32, "data": chunks[index]}
            self.node.handle_datagram(pack(self.config, "task_chunk", "prepare", payload), self.config["ground_station_ip"])
        return crc32, len(chunks)

    def test_transfer_missing_chunks_commit_and_duplicate(self):
        raw = json.dumps(task_payload(self.config["device_id"]), sort_keys=True, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(raw)
        crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
        prepare = {"revision": 1, "chunk_count": 2, "compressed_bytes": len(compressed), "raw_bytes": len(raw),
                   "crc32": crc32, "compression": "zlib", "encoding": "json-utf8"}
        self.node.handle_datagram(pack(self.config, "task_prepare", "p", prepare), self.config["ground_station_ip"])
        commit = {"revision": 1, "chunk_count": 2, "crc32": crc32}
        self.node.handle_datagram(pack(self.config, "task_commit", "c1", commit), self.config["ground_station_ip"])
        self.assertEqual(self.messages()[-1]["payload"]["missing_chunks"], [0, 1])
        for index, data in enumerate((compressed[:len(compressed)//2], compressed[len(compressed)//2:])):
            self.node.handle_datagram(pack(self.config, "task_chunk", "p", {
                "revision": 1, "chunk_count": 2, "chunk_index": index, "crc32": crc32, "data": data,
            }), self.config["ground_station_ip"])
        datagram = pack(self.config, "task_commit", "c2", commit)
        self.node.handle_datagram(datagram, self.config["ground_station_ip"])
        self.assertTrue(self.messages()[-1]["payload"]["accepted"])
        sent = len(self.node.socket.sent)
        self.node.handle_datagram(datagram, self.config["ground_station_ip"])
        self.assertEqual(len(self.node.socket.sent), sent + 1)
        self.assertIsNotNone(self.node.store.load("task-1", "sub-1"))

    def test_execute_waits_for_scheduled_feedback_then_reports_progress(self):
        crc32, count = self.deliver()
        self.node.handle_datagram(pack(self.config, "task_commit", "commit", {
            "revision": 1, "chunk_count": count, "crc32": crc32}), self.config["ground_station_ip"])
        before = len(self.node.socket.sent)
        schedule = time.time() + 5.0
        self.node.handle_datagram(pack(self.config, "execute_task", "execute", {
            "revision": 1, "scheduled_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(schedule)) + "+00:00",
        }, execution_id="exec-1"), self.config["ground_station_ip"])
        self.assertEqual(len(self.node.socket.sent), before)
        self.assertEqual(self.node.publisher.messages[-1].action, FakeCommand.SCHEDULE)
        feedback = SimpleNamespace(request_id="execute", task_id="task-1", subtask_id="sub-1",
                                   device_id=self.config["device_id"], execution_id="exec-1", revision=1,
                                   state="scheduled", waypoint_index=-1, waypoint_count=2, progress=0.0,
                                   position=SimpleNamespace(x=0, y=0, z=0), error_code="", message="ready")
        self.node.feedback_callback(feedback)
        types = [item["message_type"] for item in self.messages()[before:]]
        self.assertEqual(types, ["command_ack", "task_status"])
        feedback.state = "running"
        feedback.waypoint_index = 0
        feedback.progress = 0.1
        self.node.feedback_callback(feedback)
        self.assertEqual(self.messages()[-1]["message_type"], "waypoint_progress")
        feedback.state = "completed"
        feedback.progress = 1.0
        self.node.feedback_callback(feedback)
        self.assertIsNone(self.node.execution)

    def test_wrong_source_and_stale_feedback_are_ignored(self):
        self.node.handle_datagram(pack(self.config, "task_prepare", "p", {}), "192.0.2.1")
        self.assertEqual(self.node.socket.sent, [])

    def test_scheduling_timeout_returns_rejected_ack(self):
        crc32, count = self.deliver()
        self.node.handle_datagram(pack(self.config, "task_commit", "commit", {
            "revision": 1, "chunk_count": count, "crc32": crc32}), self.config["ground_station_ip"])
        self.node.handle_datagram(pack(self.config, "execute_task", "execute", {
            "revision": 1, "scheduled_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 5)) + "+00:00",
        }, execution_id="exec-timeout"), self.config["ground_station_ip"])
        self.node.execution.last_feedback_at -= self.config["adapter_feedback_seconds"] + 1
        self.node.watchdog()
        self.assertFalse(self.messages()[-1]["payload"]["accepted"])
        self.assertIsNone(self.node.execution)

    def test_running_execution_blocks_transfer_and_stop_is_forwarded(self):
        crc32, count = self.deliver()
        self.node.handle_datagram(pack(self.config, "task_commit", "commit", {
            "revision": 1, "chunk_count": count, "crc32": crc32}), self.config["ground_station_ip"])
        self.node.handle_datagram(pack(self.config, "execute_task", "execute", {
            "revision": 1, "scheduled_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 8)) + "+00:00",
        }, execution_id="exec-stop"), self.config["ground_station_ip"])
        feedback = SimpleNamespace(request_id="execute", task_id="task-1", subtask_id="sub-1",
                                   device_id=self.config["device_id"], execution_id="exec-stop", revision=1,
                                   state="scheduled", waypoint_index=-1, waypoint_count=2, progress=0.0,
                                   position=SimpleNamespace(x=0, y=0, z=0), error_code="", message="ready")
        self.node.feedback_callback(feedback)
        feedback.state = "running"
        feedback.waypoint_index = 0
        self.node.feedback_callback(feedback)
        prepare = {"revision": 2, "chunk_count": 1, "compressed_bytes": 1, "raw_bytes": 1,
                   "crc32": 1, "compression": "zlib", "encoding": "json-utf8"}
        self.node.handle_datagram(pack(self.config, "task_prepare", "blocked", prepare), self.config["ground_station_ip"])
        self.assertEqual(self.messages()[-1]["payload"]["error_code"], "BUSY")
        self.node.handle_datagram(pack(self.config, "terminate_task", "stop", {"reason": "done"},
                                       execution_id="exec-stop"), self.config["ground_station_ip"])
        self.assertEqual(self.node.publisher.messages[-1].action, FakeCommand.STOP)
        self.assertTrue(self.messages()[-1]["payload"]["accepted"])
        feedback.request_id = "stop"
        feedback.state = "stopped"
        self.node.feedback_callback(feedback)
        self.assertIsNone(self.node.execution)

    def test_recovery_publishes_stop_and_clears_record(self):
        record = {"task_id": "task-1", "subtask_id": "sub-1", "device_id": self.config["device_id"],
                  "execution_id": "old", "revision": 1, "request_id": "old-request", "xml_path": "old.xml",
                  "frame_id": "map", "scheduled_at": time.time() - 5, "state": "running"}
        self.node.store.save_execution(record)
        self.node._recover_execution()
        self.assertEqual(self.node.publisher.messages[-1].action, FakeCommand.STOP)
        self.assertIsNone(self.node.store.load_execution())

    def test_emergency_stop_reports_states_and_removes_executable_trajectory(self):
        crc32, count = self.deliver()
        self.node.handle_datagram(pack(self.config, "task_commit", "commit", {
            "revision": 1, "chunk_count": count, "crc32": crc32,
        }), self.config["ground_station_ip"])
        self.assertIsNotNone(self.node.store.load("task-1", "sub-1"))
        self.node.handle_datagram(pack(
            self.config, "emergency_stop", "emergency", {"reason": "test"}
        ), self.config["ground_station_ip"])
        messages = self.messages()
        states = [item["payload"]["state"] for item in messages if item["message_type"] == "task_status"]
        self.assertEqual(states[-2:], ["emergency_stop", "no_task"])
        self.assertIsNone(self.node.store.load("task-1", "sub-1"))
        status = self.node.mission_store.status("task-1", self.config["device_id"])
        self.assertEqual(status["state"], "no_task")


if __name__ == "__main__":
    unittest.main()
