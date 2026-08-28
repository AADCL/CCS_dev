import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DEPS = all(importlib.util.find_spec(name) is not None for name in ("PySide6", "numpy"))

if DEPS:
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QComboBox, QWidget

    from ccs_monitor.map_repository import MapRepository
    from ccs_monitor.models import DeviceSnapshot, MapCreatorDevice, MapDefinition, MapStatus
    from ccs_monitor.pages.task_page import TaskEditorPage
    from ccs_monitor.task_models import EdgeTaskStatus, TaskExecutionSnapshot, TaskExecutionStatus, TaskWaypoint
    from ccs_monitor.task_repository import TaskRepository


class _Source:
    def __init__(self, devices):
        self._devices = devices

    def device(self, device_id):
        return next((item for item in self._devices if item.device_id == device_id), None)


if DEPS:
    class _Viewer(QWidget):
        map_point_picked = Signal(float, float)

        def __init__(self):
            super().__init__()
            self.layer_mode = "overlay"
            self.pgm_loaded = True
            self.pointcloud_loaded = True
            self.interaction_mode = "browse"

        def load_map(self, *_args): pass
        def clear(self): pass
        def show_message(self, *_args): pass
        def set_theme(self, *_args): pass
        def set_task_paths(self, *_args): pass
        def set_task_conflicts(self, *_args): pass
        def set_execution_markers(self, *_args): pass
        def set_layer_mode(self, mode): self.layer_mode = mode
        def set_interaction_mode(self, mode): self.interaction_mode = mode


@unittest.skipUnless(DEPS, "task page UI dependencies are unavailable")
class TaskPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repository = TaskRepository(root / "tasks")
        self.map_repository = MapRepository(root / "maps")
        now = datetime(2026, 8, 27, tzinfo=timezone.utc)
        self.map = MapDefinition(
            "map-1", "地图", created_at=now, updated_at=now,
            creator_devices=(MapCreatorDevice("UAV-1", "一号机", "UAV"),),
            status=MapStatus.READY, pcd_path="map.pcd", directory_name="map",
        )
        self.map_repository._maps = [self.map]
        self.map_repository._active_map_id = self.map.map_id
        self.devices = [DeviceSnapshot("UAV-1", "一号机", "UAV", ip_address="127.0.0.1")]
        self.task = self.repository.create("任务", self.map, self.devices, now=now)
        self.editor = TaskEditorPage(
            self.repository, self.map_repository, _Source(self.devices), viewer_factory=_Viewer,
        )
        self.editor.set_task(self.task)
        self.editor.set_execution_available(True)

    def tearDown(self):
        self.editor.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def _open(self):
        self.editor._device_action(0, "create")
        return self.editor._current()

    def test_only_create_opens_subtask_and_no_selection_combo_exists(self):
        self.assertFalse(hasattr(self.editor, "layer_combo"))
        self.assertEqual(self.editor.findChildren(QComboBox), [])
        self.editor._device_selected(0)
        self.assertIsNone(self.editor.current_subtask_id)
        self.assertFalse(self.editor.right_panel.isVisible())
        self.editor._device_action(0, "read")
        self.assertIsNone(self.editor.current_subtask_id)
        self.editor._device_action(0, "delete")
        self.assertIsNone(self.editor.current_subtask_id)
        self._open()
        self.assertEqual(self.editor.current_subtask_id, self.task.subtasks[0].subtask_id)

    def test_device_card_excludes_edge_status_and_message(self):
        changed = replace(
            self.task.subtasks[0], edge_status=EdgeTaskStatus.FAILED,
            edge_message="端侧原始错误",
        )
        self.editor.set_task(replace(self.task, subtasks=(changed,)))
        text = " ".join(label.text() for label in self.editor.device_cards[0].findChildren(type(self.editor.title)))
        self.assertNotIn("failed", text)
        self.assertNotIn("端侧原始错误", text)

    def test_log_format_clear_and_new_event(self):
        self.repository.append_audit(
            self.task.task_id, "task_summary", "ready: navigation ready", device_id="UAV-1",
        )
        self.app.processEvents()
        self.assertTrue(self.editor.log_view.isReadOnly())
        self.assertEqual(self.editor.log_view.lineWrapMode(), self.editor.log_view.LineWrapMode.NoWrap)
        self.assertIn("UAV-1", self.editor.log_view.toPlainText())
        self.assertIn("ready: navigation ready", self.editor.log_view.toPlainText())
        self.editor._clear_log_view()
        self.assertEqual(self.editor.log_view.toPlainText(), "")
        self.editor._load_logs()
        self.assertEqual(self.editor.log_view.toPlainText(), "")
        self.repository.append_audit(self.task.task_id, "task_status", "running", device_id="UAV-1")
        self.app.processEvents()
        self.assertIn("running", self.editor.log_view.toPlainText())
        scrollbar = self.editor.log_view.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_save_button_state_machine(self):
        subtask = self._open()
        self.assertFalse(self.editor.deliver_button.isEnabled())
        self.editor.drafts[subtask.subtask_id] = replace(
            subtask, waypoints=(TaskWaypoint("a", 0, 0, 1),),
        )
        self.editor._changed()
        self.assertFalse(self.editor.deliver_button.isEnabled())
        self.editor.drafts[subtask.subtask_id] = replace(
            self.editor._current(), waypoints=(
                TaskWaypoint("a", 0, 0, 1), TaskWaypoint("b", 1, 0, 1),
            ),
        )
        self.editor._changed()
        self.assertTrue(self.editor.deliver_button.isEnabled())
        self.editor.pick_toggle.setChecked(True)
        self.assertFalse(self.editor.deliver_button.isEnabled())
        self.editor.pick_toggle.setChecked(False)
        self.assertTrue(self.editor.deliver_button.isEnabled())
        self.editor.set_map_reviewed(False)
        self.assertFalse(self.editor.deliver_button.isEnabled())
        self.editor.set_map_reviewed(True)
        self.editor.set_execution_available(False)
        self.assertFalse(self.editor.deliver_button.isEnabled())

    def test_execute_button_switches_between_run_stop_and_terminal(self):
        subtask = replace(
            self._open(), revision=1, delivered_revision=1, edge_revision=1,
            edge_status=EdgeTaskStatus.READY,
        )
        self.editor.drafts[subtask.subtask_id] = subtask
        self.editor._update_execution_controls()
        self.assertEqual(self.editor.run_one.objectName(), "primaryButton")
        self.assertTrue(self.editor.run_one.isEnabled())
        snapshot = TaskExecutionSnapshot(
            "execution-1", self.task.task_id, (subtask.device_id,),
            TaskExecutionStatus.RUNNING, datetime.now(timezone.utc),
        )
        self.editor._execution_updated(snapshot)
        self.assertEqual(self.editor.run_one.text(), "终止任务")
        self.assertEqual(self.editor.run_one.objectName(), "dangerButton")
        self.editor._execution_updated(replace(snapshot, status=TaskExecutionStatus.STOPPING))
        self.assertFalse(self.editor.run_one.isEnabled())
        self.editor._execution_updated(replace(snapshot, status=TaskExecutionStatus.COMPLETED))
        self.assertEqual(self.editor.run_one.text(), "执行任务")
        self.assertEqual(self.editor.run_one.objectName(), "primaryButton")


if __name__ == "__main__":
    unittest.main()
