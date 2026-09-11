import math
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from ccs_monitor.models import (
    ConnectionStatus, DeviceSnapshot, DeviceProfile, DeviceMapBinding, FrameTransform,
    DeviceTelemetrySnapshot, PoseTelemetry, MapCreatorDevice, MapDefinition, MapStatus,
)
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.task_models import EdgeTaskStatus, TaskWaypoint
from ccs_monitor.task_config import load_task_system_config
from ccs_monitor.task_repository import TaskRepository
from ccs_monitor.task_services import TaskExecutionService
from ccs_monitor.task_protocol import TaskEnvelope
from ccs_monitor.pages.task_page import TaskEditorPage
from ccs_monitor.trajectory_store import DeviceTrajectoryStore
from tests.test_task_page_ui import _Viewer


class Source(QObject):
    devices_updated = Signal(object)

    def __init__(self, devices, profiles=()):
        super().__init__()
        self.devices = {item.device_id: item for item in devices}
        self.profiles = {item.device_id: item for item in profiles}
        self.alive = {item.device_id: 1000.0 for item in devices}
        self.disconnected = {}

    def device(self, key):
        return self.devices.get(key)

    def profile(self, key):
        return self.profiles.get(key)

    def snapshots(self):
        return list(self.devices.values())

    def connection_timing(self, key):
        return self.alive.get(key), self.disconnected.get(key)


class MultiDeviceTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = TaskRepository(Path(self.temp.name) / "tasks")
        self.maps = MapRepository(Path(self.temp.name) / "maps")
        self.definition = MapDefinition("map-1", "Map", status=MapStatus.READY,
            pcd_path="map.pcd", directory_name="map",
            creator_devices=(MapCreatorDevice("D1", "One", "UGV"),))
        self.maps._maps = [self.definition]
        self.maps._active_map_id = "map-1"
        self.devices = [DeviceSnapshot(f"D{i}", f"Device {i}", "UGV", ip_address="127.0.0.1",
                        connection_status=ConnectionStatus.ONLINE) for i in (1, 2)]
        self.source = Source(self.devices)
        task = self.repository.create("Multi", self.definition, self.devices)
        for i, subtask in enumerate(task.subtasks):
            self.repository.update_subtask(task.task_id, replace(subtask,
                waypoints=(TaskWaypoint("a", i * 100, 0, 0), TaskWaypoint("b", i * 100 + 5, 0, 0))))
        self.task_id = task.task_id
        self.now = [0.0]
        self.service = TaskExecutionService(load_task_system_config(), self.repository, self.source.device,
                                           clock=lambda: self.now[0], active_map_id_getter=lambda: "map-1")
        self.service.available = True
        self.sent = []
        outer = self
        class Socket:
            def sendto(self, raw, address):
                outer.sent.append(outer.service.protocol.decode(raw))
        self.service._socket = Socket()
        self.sequence = 0

    def test_repository_notifications_run_after_transaction_unlock(self):
        results = []
        def on_update(_tasks):
            worker = threading.Thread(target=lambda: results.append(self.repository.tasks()))
            worker.start()
            worker.join(1)
            results.append(not worker.is_alive())
        self.repository.tasks_updated.connect(on_update)
        first = self.task().subtasks[0]
        self.repository.update_subtask(self.task_id, replace(first, cruise_speed_mps=2.0))
        self.assertTrue(results[-1], "repository lock held during callback")

    def task(self):
        return self.repository.task_by_id(self.task_id)

    def command(self, device_id, kind):
        return next(item for item in reversed(self.sent) if item.device_id == device_id and item.message_type == kind)

    def ack(self, command, accepted=True, **changes):
        self.sequence += 1
        envelope = TaskEnvelope(command.task_id, command.subtask_id, command.device_id,
            command.execution_id, "command_ack", command.request_id, self.sequence, time.time_ns(),
            {"accepted": accepted, "command": command.message_type, "reason": "test rejected"})
        self.service._handle_ack(replace(envelope, **changes))

    def ready(self, command, **changes):
        self.sequence += 1
        envelope = TaskEnvelope(command.task_id, command.subtask_id, command.device_id, "",
            "task_summary", command.request_id, self.sequence, time.time_ns(),
            {"state": "ready", "revision": command.payload["revision"], "message": "navigation ready"})
        self.service._handle_summary(replace(envelope, **changes))

    def complete(self, device_id, ready_first=False):
        self.ack(self.command(device_id, "task_prepare"))
        commit = self.command(device_id, "task_commit")
        if ready_first:
            self.ready(commit)
            self.assertFalse(next(item for item in self.task().subtasks if item.device_id == device_id).edge_ready)
            self.ack(commit)
        else:
            self.ack(commit)
            self.ready(commit)
        return commit

    def editor(self):
        editor = TaskEditorPage(self.repository, self.maps, self.source, self.service, viewer_factory=_Viewer)
        editor.set_task(self.task())
        self.addCleanup(editor.deleteLater)
        return editor

    def test_sequential_and_batch_deliveries_keep_both_devices_ready(self):
        self.service.deliver_subtask(self.task(), self.task().subtasks[0])
        self.complete("D1")
        self.service.deliver_subtask(self.task(), self.task().subtasks[1])
        self.complete("D2", ready_first=True)
        self.assertTrue(all(item.edge_ready for item in self.task().subtasks))
        self.service.deliver_task(self.task())
        self.assertFalse(any(item.edge_ready for item in self.task().subtasks))
        self.complete("D2")
        self.complete("D1")
        self.assertTrue(all(item.edge_ready for item in self.task().subtasks))

    def test_sync_button_enables_start_only_after_all_ready_and_start_keeps_revisions(self):
        editor = self.editor()
        editor._deliver_all()
        self.assertFalse(editor.run_all.isEnabled())
        self.complete("D1")
        self.assertFalse(editor.run_all.isEnabled())
        self.complete("D2")
        self.assertTrue(editor.run_all.isEnabled())
        revisions = [item.revision for item in self.task().subtasks]
        editor._execute_all()
        self.assertEqual([item.revision for item in self.task().subtasks], revisions)
        starts = [item for item in self.sent if item.message_type == "execute_task"]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0].payload["scheduled_at"], starts[1].payload["scheduled_at"])

    def test_rejection_and_old_ready_cannot_pollute_retry(self):
        self.service.deliver_task(self.task())
        self.complete("D1")
        old = self.command("D2", "task_prepare")
        self.ack(old, accepted=False)
        self.assertTrue(self.task().subtasks[0].edge_ready)
        self.assertFalse(self.service.transfer_active(self.task_id, "D2"))
        self.service.deliver_subtask(self.task(), self.task().subtasks[1])
        self.ready(old)
        self.assertFalse(self.task().subtasks[1].edge_ready)
        self.complete("D2")
        self.assertTrue(all(item.edge_ready for item in self.task().subtasks))

    def test_wrong_device_command_and_subtask_ack_do_not_consume_request(self):
        self.service.deliver_task(self.task())
        prepare = self.command("D1", "task_prepare")
        for changes in ({"device_id": "D2"}, {"subtask_id": "wrong"}, {"execution_id": "wrong"},
                        {"payload": {"accepted": True, "command": "execute_task"}}):
            self.ack(prepare, **changes)
            self.assertIn(prepare.request_id, self.service._pending)
        self.complete("D1")

    def test_old_commit_and_timeout_do_not_mark_edited_revision_failed(self):
        for late_ack in (True, False):
            self.service.deliver_subtask(self.task(), self.task().subtasks[0])
            self.ack(self.command("D1", "task_prepare"))
            commit = self.command("D1", "task_commit")
            original = self.task().subtasks[0]
            self.repository.update_subtask(self.task_id, replace(original, cruise_speed_mps=original.cruise_speed_mps + 1))
            if late_ack:
                self.ack(commit, accepted=False)
            else:
                self.now[0] += 300
                self.service._tick()
            current = self.task().subtasks[0]
            self.assertEqual(current.edge_status, EdgeTaskStatus.NO_TASK)
            self.assertIsNone(current.delivered_revision)
            self.assertFalse(self.service.transfer_active(self.task_id, "D1"))

    def test_repeated_ready_does_not_reschedule_started_task(self):
        self.service.deliver_task(self.task())
        commit = self.complete("D1")
        self.complete("D2")
        execution = self.service.execute_devices(self.task(), ("D1", "D2"))
        self.ready(commit)
        self.assertEqual(len([item for item in self.sent if item.message_type == "execute_task"]), 2)
        self.assertEqual(self.service._executions[execution.execution_id].scheduled_at, execution.scheduled_at)

    def test_reentry_queries_preparation_until_current_ready_without_extending_timeout(self):
        self.service.deliver_subtask(self.task(), self.task().subtasks[0])
        self.ack(self.command("D1", "task_prepare"))
        commit = self.command("D1", "task_commit")
        self.ack(commit)
        self.service._clear_transfer(self.task_id, "D1")  # Simulate a restarted UI service.
        self.service.negotiate_subtask(self.task(), self.task().subtasks[0])
        query = self.command("D1", "negotiate_task")
        self.ack(query)
        self.ready(query, payload={"state": "received", "revision": query.payload["revision"]})
        self.ready(commit)
        self.assertFalse(self.task().subtasks[0].edge_ready)
        deadline = self.service._confirmations[(self.task_id, "D1")].deadline
        self.now[0] = 2
        self.service._tick()
        fresh = self.command("D1", "negotiate_task")
        self.assertNotEqual(fresh.request_id, query.request_id)
        self.assertEqual(self.service._confirmations[(self.task_id, "D1")].deadline, deadline)
        self.ack(fresh)
        self.ready(fresh)
        self.assertTrue(self.task().subtasks[0].edge_ready)

    def test_navigation_timeout_cleans_up_and_allows_retry(self):
        self.service.deliver_subtask(self.task(), self.task().subtasks[0])
        self.ack(self.command("D1", "task_prepare"))
        commit = self.command("D1", "task_commit")
        self.ack(commit)
        self.now[0] = 60
        self.service._tick()
        self.assertFalse(self.service.transfer_active(self.task_id, "D1"))
        self.assertEqual(self.task().subtasks[0].edge_status, EdgeTaskStatus.FAILED)
        self.ready(commit)
        self.assertFalse(self.task().subtasks[0].edge_ready)
        self.service.deliver_subtask(self.task(), self.task().subtasks[0])
        self.complete("D1")

    def test_unchanged_save_preserves_ready_and_edit_only_invalidates_one_device(self):
        self.service.deliver_task(self.task())
        self.complete("D1")
        self.complete("D2")
        task = self.task()
        self.repository.update_subtask(task.task_id, task.subtasks[0])
        self.assertEqual(self.task().subtasks, task.subtasks)
        self.repository.update_subtask(task.task_id, replace(task.subtasks[0], cruise_speed_mps=2))
        self.assertFalse(self.task().subtasks[0].edge_ready)
        self.assertTrue(self.task().subtasks[1].edge_ready)

    def test_other_device_draft_survives_single_save_and_sync_preflight_is_atomic(self):
        editor = self.editor()
        second = self.task().subtasks[1]
        edited = replace(second, cruise_speed_mps=2.5)
        editor.drafts[second.subtask_id] = edited
        editor._open_subtask(0)
        self.assertTrue(editor._save_current())
        self.assertEqual(editor.drafts[second.subtask_id].cruise_speed_mps, 2.5)
        editor.drafts[second.subtask_id] = replace(edited, waypoints=())
        sent_before = len(self.sent)
        with patch("ccs_monitor.pages.task_page.QMessageBox.critical"):
            editor._deliver_all()
        self.assertEqual(len(self.sent), sent_before)

    def test_runtime_update_merges_with_latest_definition_and_sibling(self):
        first, second = self.task().subtasks
        changed = replace(first, cruise_speed_mps=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(self.repository.update_subtask, self.task_id, changed)
            b = pool.submit(self.repository.update_edge_status, self.task_id,
                           replace(second, edge_status=EdgeTaskStatus.READY, edge_revision=second.revision))
            a.result()
            b.result()
        self.assertEqual(self.task().subtasks[0].cruise_speed_mps, 2)
        self.assertEqual(self.task().subtasks[1].edge_status, EdgeTaskStatus.READY)

    def test_recent_events_are_bounded_and_cached(self):
        self.repository.recent_events(self.task_id)
        for i in range(520):
            self.repository.append_audit(self.task_id, "test", str(i))
        with patch.object(self.repository, "_read_events", side_effect=AssertionError("history reread")):
            recent = self.repository.recent_events(self.task_id)
        self.assertEqual(len(recent), 500)
        self.assertEqual(recent[-1].message, "519")


class TrajectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1000.0]
        transform = FrameTransform(10, 2, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5))
        binding = DeviceMapBinding("map-1", "map", "odom", transform, datetime.now(timezone.utc))
        profiles = [DeviceProfile("D1", "One", "UGV", "127.0.0.1", active_map_id="map-1",
                                  map_bindings=(binding,))]
        self.source = Source([DeviceSnapshot("D1", "One", "UGV", connection_status=ConnectionStatus.ONLINE)], profiles)
        self.store = self.make_store()

    def make_store(self):
        store = DeviceTrajectoryStore(self.source, root=Path(self.temp.name), clock=lambda: self.now[0], start_timers=False)
        self.addCleanup(store.close)
        store._restored.wait(2)
        return store

    def point(self, x, session="one", age=0):
        self.store.observe("D1", DeviceTelemetrySnapshot("D1", global_pose=PoseTelemetry(x, 0, 0, 0, 0, 0, age),
                                                       session_id=session))

    def offline(self):
        self.source.devices["D1"] = replace(self.source.device("D1"), connection_status=ConnectionStatus.OFFLINE)
        self.source.disconnected["D1"] = self.now[0]
        self.store.update_connections(self.source.snapshots())

    def test_unwritable_archive_root_does_not_prevent_service_start(self):
        blocked = Path(self.temp.name) / "blocked"
        blocked.write_text("file", encoding="utf-8")
        store = DeviceTrajectoryStore(self.source, root=blocked, clock=lambda: self.now[0], start_timers=False)
        self.addCleanup(store.close)
        store.flush()
        self.assertIsNotNone(store.last_error)
        self.assertTrue(store._writer.is_alive())

    def test_explicit_clear_removes_connection_archive(self):
        self.point(1)
        self.store.flush()
        directory = self.store._tracks["D1"].directory
        self.store.clear("D1")
        self.store.flush()
        self.assertEqual(self.store.trails("map-1"), {})
        self.assertFalse(directory.exists())

    def test_display_deduplication_preserves_every_archive_sample(self):
        for value in (0, 0.001, 0.002, 0.003):
            self.point(value)
        self.store.flush()
        self.assertEqual(len(self.store.trails("map-1")["D1"]), 1)
        track = self.store._tracks["D1"]
        records = [json.loads(line) for file in track.directory.glob("chunk-*.jsonl")
                   for line in file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 4)
        self.assertAlmostEqual(records[-1][3], 2.003)

    def test_wrong_json_record_types_do_not_stop_restore_or_future_writes(self):
        self.point(1)
        self.store.close()
        track = self.store._tracks["D1"]
        file = next(track.directory.glob("chunk-*.jsonl"))
        with file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({str(i): i for i in range(6)}) + "\n")
            handle.write('"123456"\n')
        self.store = self.make_store()
        self.point(2)
        self.store.flush()
        self.assertIsNone(self.store.last_error)
        self.assertTrue(self.store._writer.is_alive())
        self.assertGreaterEqual(len(self.store.trails("map-1")["D1"]), 2)

    def test_map_coordinates_segments_and_map_isolation(self):
        self.point(1)
        self.point(2)
        points = self.store.trails("map-1")["D1"]
        self.assertAlmostEqual(points[0][0], 10)
        self.assertAlmostEqual(points[0][1], 3)
        self.assertEqual(self.store.trails("other-map"), {})
        self.point(3, session="two")
        self.assertTrue(any(math.isnan(point[0]) for point in self.store.trails("map-1")["D1"]))
        count = len(self.store._tracks["D1"].points)
        self.point(99, age=3)
        self.point(math.nan)
        self.assertEqual(len(self.store._tracks["D1"].points), count)

    def test_short_disconnect_resumes_and_expiry_removes_memory_and_disk(self):
        self.point(1)
        self.point(2)
        connection = self.store._tracks["D1"].connection_id
        self.offline()
        self.now[0] += 119.9
        self.store.tick()
        self.assertIn("D1", self.store.trails("map-1"))
        self.source.devices["D1"] = replace(self.source.device("D1"), connection_status=ConnectionStatus.ONLINE)
        self.source.alive["D1"] = self.now[0]
        self.source.disconnected.clear()
        self.store.update_connections(self.source.snapshots())
        self.point(3)
        self.assertEqual(self.store._tracks["D1"].connection_id, connection)
        self.offline()
        self.now[0] += 120
        self.store.tick()
        self.store.flush()
        self.assertEqual(self.store.trails("map-1"), {})
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_restart_recovers_unexpired_points_and_tolerates_partial_tail(self):
        self.point(1)
        self.point(2)
        self.store.flush()
        file = next(Path(self.temp.name).glob("*/chunk-*.jsonl"))
        with file.open("a") as handle:
            handle.write('{"partial":')
        self.store.close()
        self.now[0] += 10
        self.source.devices["D1"] = replace(self.source.device("D1"), connection_status=ConnectionStatus.OFFLINE)
        restored = self.make_store()
        self.assertEqual(len(restored.trails("map-1")["D1"]), 2)
        restored.close()
        self.now[0] += 120
        expired = self.make_store()
        expired.flush()
        self.assertEqual(expired.trails("map-1"), {})

    def test_long_connection_keeps_start_and_end_with_bounded_memory(self):
        for i in range(11000):
            self.point(i)
            if i % 1000 == 0:
                self.store.flush()
        points = self.store._tracks["D1"].points
        self.assertLessEqual(len(points), 10000)
        self.assertAlmostEqual(points[0][3], 2)
        self.assertAlmostEqual(points[-1][3], 11001)
        self.store.flush()
        lines = sum(len(file.read_text().splitlines()) for file in Path(self.temp.name).glob("*/chunk-*.jsonl"))
        self.assertEqual(lines, 11000)
        self.assertIsNone(self.store.last_error)

    def test_failed_write_retains_pending_and_reports_error(self):
        self.point(1)
        with patch("ccs_monitor.trajectory_store.os.fsync", side_effect=OSError("disk full")):
            self.store.flush()
        self.assertIn("disk full", self.store.last_error)
        self.assertTrue(self.store._tracks["D1"].pending)
        self.store.flush()
        self.assertFalse(self.store._tracks["D1"].pending)


if __name__ == "__main__":
    unittest.main()


class TrajectoryViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def viewer(self):
        from types import SimpleNamespace
        from ccs_monitor.pages.map_page import PointCloudViewer
        viewer = PointCloudViewer(canvas_factory=lambda: SimpleNamespace(native=None))
        self.addCleanup(viewer.deleteLater)
        return viewer

    def test_independent_persisted_layer_switch_does_not_remove_points(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QSettings
        with tempfile.TemporaryDirectory() as root:
            settings = QSettings(str(Path(root) / "settings.ini"), QSettings.Format.IniFormat)
            with patch("ccs_monitor.pages.map_page.QSettings", return_value=settings):
                task, dashboard = self.viewer(), self.viewer()
                task.set_trail_settings_key("task")
                dashboard.set_trail_settings_key("dashboard")
                self.assertTrue(task.trails_visible and dashboard.trails_visible)
                visual = SimpleNamespace(visible=True)
                points = ((1, 2, 3), (2, 3, 4))
                task._trail_visuals["D1"] = visual
                task._trail_visibility["D1"] = True
                task._trail_inputs["D1"] = points
                task.trails_check.setChecked(False)
                self.assertFalse(visual.visible)
                self.assertIs(task._trail_inputs["D1"], points)
                self.assertTrue(dashboard.trails_visible)
                reopened = self.viewer()
                reopened.set_trail_settings_key("task")
                self.assertFalse(reopened.trails_visible)
                task.trails_check.setChecked(True)
                self.assertTrue(visual.visible)

    def test_hidden_viewer_rejects_inflight_map_restore(self):
        from concurrent.futures import Future
        from unittest.mock import Mock
        import numpy as np
        viewer = self.viewer()
        viewer.current_map = MapDefinition("map-1", "Map")
        viewer._pcd_path = "map.pcd"
        viewer._point_data = np.ones((100, 3), dtype=np.float32)
        viewer.suspend_static()
        self.assertEqual(len(viewer._point_data), 0)
        pending = Future()
        pending.set_running_or_notify_cancel()
        with patch("ccs_monitor.pages.map_page._STATIC_MAP_READERS.submit", return_value=pending):
            viewer.resume_static()
        generation = viewer._load_generation
        viewer.suspend_static()
        viewer.load_map = Mock()
        pending.set_result(dict(generation=generation, definition=viewer.current_map, errors=[], pcd=object()))
        self.app.processEvents()
        viewer.load_map.assert_not_called()
        self.assertTrue(viewer._suspended)
        self.assertIsNone(viewer._resume_future)
