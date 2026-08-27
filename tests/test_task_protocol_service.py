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
    from ccs_monitor.task_models import EdgeTaskStatus, TaskExecutionStatus, TaskWaypoint
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

    def mark_edge_ready(self):
        subtask = self.task.subtasks[0]
        self.repository.mark_delivered(self.task.task_id, subtask.device_id, subtask.revision)
        refreshed = self.repository.task_by_id(self.task.task_id).subtasks[0]
        self.repository.update_edge_status(self.task.task_id, replace(
            refreshed, edge_status=EdgeTaskStatus.READY, edge_revision=refreshed.revision,
        ))
        self.task = self.repository.task_by_id(self.task.task_id)

    def test_subtask_encoding_round_trip(self):
        protocol = TaskProtocol(load_task_system_config())
        encoded = protocol.encode_subtask(self.task, self.task.subtasks[0])
        decoded = protocol.decode_subtask(encoded.compressed, encoded.crc32)
        self.assertEqual(decoded["revision"], 1)
        self.assertEqual(len(decoded["waypoints"]), 2)

    def test_group_start_delay_allows_navigation_startup(self):
        self.assertEqual(load_task_system_config().group_start_delay_seconds, 3.0)

    def test_execution_rejects_non_active_task_map(self):
        config = load_task_system_config()
        service = TaskExecutionService(
            config, self.repository,
            lambda value: self.device if value == self.device.device_id else None,
            active_map_id_getter=lambda: "another-map",
        )
        service.available = True
        service.module_message = "test"
        with self.assertRaisesRegex(RuntimeError, "全局激活地图"):
            service.execute_subtask(self.task, self.device.device_id)

    def test_execution_rejects_unready_device_without_leaving_session_lock(self):
        service = TaskExecutionService(
            load_task_system_config(), self.repository,
            lambda value: self.device if value == self.device.device_id else None,
        )
        service.available = True
        service.module_message = "test"
        with self.assertRaisesRegex(RuntimeError, "尚未保存下发"):
            service.execute_subtask(self.task, self.device.device_id)
        self.assertFalse(service.device_active(self.device.device_id))
        self.assertEqual(service._executions, {})

    def test_stop_waits_for_terminal_device_status(self):
        config = load_task_system_config()
        service = TaskExecutionService(
            config, self.repository,
            lambda value: self.device if value == self.device.device_id else None,
        )
        service.available = True
        service.module_message = "test"
        service._socket = type("Socket", (), {"sendto": lambda *_args: None})()
        self.mark_edge_ready()
        snapshot = service.execute_subtask(self.task, self.device.device_id)
        service.stop_execution(snapshot.execution_id)
        self.assertEqual(service._executions[snapshot.execution_id].status, TaskExecutionStatus.STOPPING)
        service._handle_status(TaskEnvelope(
            self.task.task_id, self.task.subtasks[0].subtask_id, self.device.device_id,
            snapshot.execution_id, "task_status", "status-stop", 200, time.time_ns(),
            {"state": "stopped", "message": "stopped"},
        ))
        self.assertNotIn(snapshot.execution_id, service._executions)
        self.assertEqual(
            self.repository.executions(self.task.task_id)[0].status,
            TaskExecutionStatus.STOPPED,
        )

    def test_failed_status_preserves_device_message_and_error_code(self):
        config = load_task_system_config()
        service = TaskExecutionService(
            config, self.repository,
            lambda value: self.device if value == self.device.device_id else None,
        )
        service.available = True
        service._socket = type("Socket", (), {"sendto": lambda *_args: None})()
        self.mark_edge_ready()
        snapshot = service.execute_subtask(self.task, self.device.device_id)
        service._handle_status(TaskEnvelope(
            self.task.task_id, self.task.subtasks[0].subtask_id, self.device.device_id,
            snapshot.execution_id, "task_status", "status-failed", 201, time.time_ns(),
            {"state": "failed", "message": "实时定位不可用", "error_code": "LOCALIZATION_UNAVAILABLE"},
        ))
        record = self.repository.executions(self.task.task_id)[0]
        self.assertIn("UAV-1", record.message)
        self.assertIn("实时定位不可用", record.message)
        self.assertIn("LOCALIZATION_UNAVAILABLE", record.message)

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
        service.deliver_subtask(self.task, self.task.subtasks[0])

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

        edge.settimeout(0.2)
        with self.assertRaises(socket.timeout):
            edge.recvfrom(4096)
        edge.sendto(protocol.encode(TaskEnvelope(
            commit.task_id, commit.subtask_id, commit.device_id, "",
            "task_summary", "prepare-ready", 102, time.time_ns(),
            {"state": "ready", "revision": self.task.subtasks[0].revision,
             "message": "navigation ready", "error_code": None},
        )), ("127.0.0.1", status_port))
        edge.settimeout(2)

        deadline = time.time() + 2
        while time.time() < deadline:
            refreshed = self.repository.task_by_id(self.task.task_id)
            if refreshed and refreshed.subtasks[0].edge_ready:
                break
            time.sleep(0.02)
        self.task = self.repository.task_by_id(self.task.task_id)
        self.assertTrue(self.task.subtasks[0].edge_ready)
        snapshot = service.execute_subtask(self.task, self.device.device_id)

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
            "command_ack", execute.request_id, 103, time.time_ns(),
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
