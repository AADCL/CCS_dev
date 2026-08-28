import importlib.util
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    from ccs_monitor.models import DeviceSnapshot, MapCreatorDevice, MapDefinition, MapStatus
    from ccs_monitor.task_conflicts import TaskConflictDetector
    from ccs_monitor.task_models import DeviceSubtask, TaskSafetySettings, TaskWaypoint
    from ccs_monitor.task_repository import TaskRepository


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class TaskSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = TaskRepository(Path(self.temp.name) / "tasks")
        self.now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        self.map = MapDefinition(
            "map-1", "测试地图", created_at=self.now, updated_at=self.now,
            creator_devices=(MapCreatorDevice("UAV-1", "一号机", "UAV"),),
            status=MapStatus.READY, pcd_path="map.pcd", directory_name="map-dir",
        )
        self.devices = [
            DeviceSnapshot("UAV-1", "一号机", "UAV", ip_address="127.0.0.1"),
            DeviceSnapshot("UAV-2", "二号机", "UAV", ip_address="127.0.0.2"),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_create_update_reload_audit_and_trash(self):
        task = self.repository.create("联合巡检", self.map, self.devices, now=self.now)
        subtask = replace(task.subtasks[0], waypoints=(
            TaskWaypoint("a", 0, 0, 1), TaskWaypoint("b", 10, 0, 1),
        ))
        updated = self.repository.update_subtask(task.task_id, subtask)
        self.assertEqual(updated.subtasks[0].revision, 1)
        self.assertIsNone(updated.subtasks[0].delivered_revision)
        self.assertGreaterEqual(len(self.repository.audit_events(task.task_id)), 2)
        reloaded = TaskRepository(self.repository.root).task_by_id(task.task_id)
        self.assertEqual(reloaded.subtasks[0].waypoints[1].x, 10)
        trash = self.repository.delete(task.task_id)
        self.assertTrue(trash.is_dir())

    def test_event_appends_notify_without_deleting_history(self):
        task = self.repository.create("联合巡检", self.map, self.devices, now=self.now)
        notified = []
        self.repository.events_updated.connect(notified.append)
        self.repository.append_audit(task.task_id, "edge_state", "ready")
        self.repository.append_execution_event(
            task.task_id, "execution-1", "task_status", "running",
            device_id="UAV-1",
        )
        self.assertEqual(notified, [task.task_id, task.task_id])
        self.assertEqual(self.repository.audit_events(task.task_id)[-1].message, "ready")
        self.assertEqual(
            self.repository.execution_events(task.task_id, "execution-1")[-1].message,
            "running",
        )

    def test_conflict_detector_respects_time_altitude_and_delay(self):
        first = DeviceSubtask("s1", "A", "A", "UAV", "127.0.0.1", waypoints=(
            TaskWaypoint("a1", -5, 0, 1), TaskWaypoint("a2", 5, 0, 1),
        ), cruise_speed_mps=1)
        second = DeviceSubtask("s2", "B", "B", "UAV", "127.0.0.2", waypoints=(
            TaskWaypoint("b1", 0, -5, 1), TaskWaypoint("b2", 0, 5, 1),
        ), cruise_speed_mps=1)
        detector = TaskConflictDetector()
        self.assertEqual(len(detector.detect((first, second), TaskSafetySettings())), 1)
        high = replace(second, waypoints=(TaskWaypoint("b1", 0, -5, 5), TaskWaypoint("b2", 0, 5, 5)))
        self.assertEqual(detector.detect((first, high), TaskSafetySettings()), ())
        delayed = replace(second, start_delay_seconds=20)
        self.assertEqual(detector.detect((first, delayed), TaskSafetySettings()), ())


if __name__ == "__main__":
    unittest.main()
