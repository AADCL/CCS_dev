import importlib.util
import socket
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


DEPS = all(importlib.util.find_spec(name) is not None for name in ("PySide6", "msgpack"))

if DEPS:
    from ccs_monitor.models import DeviceSnapshot, MapCreatorDevice, MapDefinition, MapStatus
    from ccs_monitor.task_config import load_task_system_config
    from ccs_monitor.task_models import TaskWaypoint
    from ccs_monitor.task_protocol import TaskEnvelope, TaskProtocol
    from ccs_monitor.task_repository import TaskRepository
    from ccs_monitor.task_services import TaskExecutionService


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@unittest.skipUnless(DEPS, "task protocol dependencies are unavailable")
class TaskProtocolServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = TaskRepository(Path(self.temp.name) / "tasks")
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        map_definition = MapDefinition(
            "map-1", "地图", created_at=now, updated_at=now,
            creator_devices=(MapCreatorDevice("UAV-1", "设备", "UAV"),),
            status=MapStatus.READY, pcd_path="map.pcd", directory_name="map",
        )
        self.device = DeviceSnapshot("UAV-1", "设备", "UAV", ip_address="127.0.0.1")
        task = self.repository.create("任务", map_definition, [self.device], now=now)
        subtask = replace(task.subtasks[0], waypoints=(
            TaskWaypoint("a", 0, 0, 1), TaskWaypoint("b", 2, 0, 1),
        ))
        self.task = self.repository.update_subtask(task.task_id, subtask)

    def tearDown(self):
        self.temp.cleanup()

    def test_subtask_encoding_round_trip(self):
        protocol = TaskProtocol(load_task_system_config())
        encoded = protocol.encode_subtask(self.task, self.task.subtasks[0])
        decoded = protocol.decode_subtask(encoded.compressed, encoded.crc32)
        self.assertEqual(decoded["revision"], 1)
        self.assertEqual(len(decoded["waypoints"]), 2)

    def test_localhost_prepare_commit_and_execute(self):
        status_port, control_port = free_port(), free_port()
        config = replace(
            load_task_system_config(), bind_host="127.0.0.1", status_port=status_port,
            device_control_port=control_port, retry_seconds=0.05, max_attempts=3,
        )
        protocol = TaskProtocol(config)
        service = TaskExecutionService(config, self.repository, lambda value: self.device if value == self.device.device_id else None)
        edge = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        edge.bind(("127.0.0.1", control_port))
        edge.settimeout(2)
        service.start()
        snapshot = service.execute_subtask(self.task, self.device.device_id)

        prepare_raw, _ = edge.recvfrom(4096)
        prepare = protocol.decode(prepare_raw)
        self.assertEqual(prepare.message_type, "task_prepare")
        edge.sendto(protocol.encode(TaskEnvelope(
            prepare.task_id, prepare.subtask_id, prepare.device_id, prepare.execution_id,
            "command_ack", prepare.request_id, 100, time.time_ns(),
            {"accepted": True, "command": "task_prepare"},
        )), ("127.0.0.1", status_port))

        messages = []
        commit = None
        deadline = time.time() + 2
        while time.time() < deadline and commit is None:
            raw, _ = edge.recvfrom(4096)
            message = protocol.decode(raw)
            messages.append(message)
            if message.message_type == "task_commit":
                commit = message
        self.assertTrue(any(item.message_type == "task_chunk" for item in messages))
        edge.sendto(protocol.encode(TaskEnvelope(
            commit.task_id, commit.subtask_id, commit.device_id, commit.execution_id,
            "command_ack", commit.request_id, 101, time.time_ns(),
            {"accepted": True, "command": "task_commit"},
        )), ("127.0.0.1", status_port))

        execute = None
        deadline = time.time() + 2
        while time.time() < deadline and execute is None:
            raw, _ = edge.recvfrom(4096)
            message = protocol.decode(raw)
            if message.message_type == "execute_task":
                execute = message
        self.assertIsNotNone(execute)
        edge.sendto(protocol.encode(TaskEnvelope(
            execute.task_id, execute.subtask_id, execute.device_id, execute.execution_id,
            "command_ack", execute.request_id, 102, time.time_ns(),
            {"accepted": True, "command": "execute_task"},
        )), ("127.0.0.1", status_port))
        deadline = time.time() + 2
        while time.time() < deadline:
            records = self.repository.executions(self.task.task_id)
            if records and records[0].status.value == "scheduled":
                break
            time.sleep(0.02)
        self.assertEqual(self.repository.executions(self.task.task_id)[0].status.value, "scheduled")
        service.stop()
        edge.close()


if __name__ == "__main__":
    unittest.main()
