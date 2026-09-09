import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DEPS = all(importlib.util.find_spec(name) is not None for name in ("PySide6", "numpy"))

if DEPS:
    from PySide6.QtCore import QObject, QSettings, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget

    from ccs_monitor.app import configure_application_font
    from ccs_monitor.map_fusion import MapFusionRepository
    from ccs_monitor.map_repository import MapRepository
    from ccs_monitor.models import (
        DeviceSnapshot, MapBounds, MapCreatorDevice, MapDefinition, MapStatus, PgmMapMetadata,
    )
    from ccs_monitor.pages.map_page import MapCard, MapPage
    from ccs_monitor.pages.task_page import TaskPage
    from ccs_monitor.styles import ThemeMode, build_qt_palette, build_stylesheet, theme_palette
    from ccs_monitor.task_models import DeviceSubtask, TaskDefinition, TaskDefinitionStatus, TaskWaypoint
    from ccs_monitor.task_repository import TaskRepository, map_fingerprint


if DEPS:
    class _Source(QObject):
        devices_updated = Signal(object)

        def __init__(self, devices):
            super().__init__()
            self.devices = devices

        def snapshots(self):
            return list(self.devices)

        def device(self, device_id):
            return next((item for item in self.devices if item.device_id == device_id), None)


    class _Viewer(QWidget):
        map_point_picked = Signal(float, float)

        def __init__(self):
            super().__init__()
            self.layer_mode = "overlay"
            self.pgm_loaded = False
            self.pointcloud_loaded = False
            self.interaction_mode = "browse"

        def load_map(self, *_args): pass
        def load_pgm_layer(self, *_args): pass
        def clear(self): pass
        def show_message(self, *_args): pass
        def set_theme(self, *_args): pass
        def set_task_paths(self, *_args): pass
        def set_task_conflicts(self, *_args): pass
        def set_execution_markers(self, *_args): pass
        def set_device_trails(self, *_args): pass
        def set_device_markers(self, *_args): pass
        def set_selected_device_pose(self, *_args): pass
        def set_relocalization_picker(self, *_args): pass
        def set_layer_mode(self, mode): self.layer_mode = mode
        def set_interaction_mode(self, mode): self.interaction_mode = mode


@unittest.skipUnless(DEPS, "compact list UI dependencies are unavailable")
class CompactListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_application_font(cls.app)

    def setUp(self):
        self.original_palette = self.app.palette()
        self.original_stylesheet = self.app.styleSheet()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        for module in ("ccs_monitor.pages.task_page", "ccs_monitor.pages.map_page"):
            settings_patch = patch(module + ".QSettings", return_value=self.settings)
            settings_patch.start()
            self.addCleanup(settings_patch.stop)
        self.map_repo = MapRepository(root / "maps")
        self.task_repo = TaskRepository(root / "tasks")
        self.devices = [DeviceSnapshot("UGV-1", "巡检一号", "UGV", ip_address="127.0.0.1")]
        self.source = _Source(self.devices)
        now = datetime(2026, 9, 9, 3, 31, 38, tzinfo=timezone.utc)
        pgm = PgmMapMetadata("map.pgm", "map.yaml", 0.05, 0, 0, 0, 200, 160, False, 0.65, 0.2)
        self.pcd = MapDefinition(
            "pcd", "A_三维巡检地图", created_at=now, updated_at=now,
            creator_devices=(MapCreatorDevice("UGV-1", "巡检一号", "UGV"),),
            status=MapStatus.READY, pcd_path="map.pcd", point_count=296858,
            bounds=MapBounds(0, 0, 0, 25.9, 12.4, 4.0), directory_name="pcd",
        )
        self.pgm = replace(self.pcd, map_id="pgm", name="B_仅二维地图", pcd_path=None,
                           pgm=pgm, bounds=None, point_count=0, directory_name="pgm",
                           created_at=now - timedelta(days=1))
        self.waiting = replace(self.pgm, map_id="waiting", name="C_待导入地图", pgm=None,
                               status=MapStatus.WAITING_FOR_PCD, directory_name="waiting",
                               creator_devices=(MapCreatorDevice("UAV-2", "航测二号", "UAV"),),
                               created_at=now - timedelta(days=2))
        self.error = replace(self.waiting, map_id="error:broken", name="D_损坏地图",
                             status=MapStatus.ERROR, error_message="地图元数据无法读取：字段缺失",
                             directory_name="broken", created_at=now - timedelta(days=3))
        self.combined = replace(self.pcd, map_id="combined", name="E_多设备联合建图_" + "完整名称" * 8,
                                pgm=pgm, creator_devices=self.pcd.creator_devices + self.waiting.creator_devices,
                                directory_name="combined", created_at=now - timedelta(days=4))
        self.map_repo._maps = [self.pcd, self.pgm, self.waiting, self.error, self.combined]
        self.map_repo._active_map_id = self.pcd.map_id
        subtask = DeviceSubtask("subtask", "UGV-1", "巡检一号", "UGV", "127.0.0.1")
        self.draft = TaskDefinition(
            "draft", "A_巡检草稿", self.pcd.map_id, self.pcd.name, "map", map_fingerprint(self.pcd),
            now, now, subtasks=(subtask,), directory_name="draft",
        )
        valid = replace(subtask, waypoints=(TaskWaypoint("a", 0, 0, 1), TaskWaypoint("b", 1, 1, 1)))
        self.ready = replace(self.draft, task_id="ready", name="B_已配置巡检任务_" + "完整名称" * 6,
                             subtasks=(valid,), updated_at=now - timedelta(days=1), directory_name="ready")
        self.task_error = replace(self.draft, task_id="error:task", name="C_损坏任务", map_id="gone",
                                  map_name="已移除地图", subtasks=(), status=TaskDefinitionStatus.ERROR,
                                  error_message="任务元数据无法解析", directory_name="broken",
                                  updated_at=now - timedelta(days=2))
        self.task_repo._tasks = [self.draft, self.ready, self.task_error]
        self.execution = Mock(available=False, module_message="测试环境未连接设备")
        self.maps = MapPage(
            self.source, repository=self.map_repo, viewer_factory=_Viewer,
            fusion_repository=MapFusionRepository(root / "fusion.json", root / "fusion"),
        )
        self.tasks = TaskPage(self.source, self.map_repo, self.task_repo,
                              execution_service=self.execution, viewer_factory=_Viewer)
        self.addCleanup(self._close_pages)

    def _close_pages(self):
        for page in (self.maps, self.tasks):
            page.close()
            page.deleteLater()
        self.app.processEvents()
        self.app.setStyleSheet(self.original_stylesheet)
        self.app.setPalette(self.original_palette)

    @staticmethod
    def _text(widget):
        return " ".join(label.text() for label in widget.findChildren(QLabel))

    @staticmethod
    def _select(combo, value):
        index = combo.findData(value)
        if index < 0:
            raise AssertionError(f"Missing filter/sort option: {value}")
        combo.setCurrentIndex(index)

    def test_map_fields_keep_precision_and_distinguish_missing_layers(self):
        cards = self.maps.map_cards
        pcd = self._text(cards["pcd"])
        self.assertIn("296,858", pcd)
        self.assertIn("25.9", pcd)
        self.assertIn("巡检一号", pcd)
        self.assertIn(self.pcd.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"), pcd)
        pgm = self._text(cards["pgm"])
        self.assertIn("PGM", pgm)
        self.assertNotIn("PCD", cards["pgm"].metric_label.text())
        self.assertNotIn(" 点", pgm)
        self.assertIn("10.0", pgm)
        self.assertIn("8.0", pgm)
        self.assertIn("map.pcd", self._text(cards["waiting"]))
        self.assertIn(self.error.error_message, self._text(cards[self.error.map_id]))
        self.assertIn("文件时间", self._text(cards[self.error.map_id]))
        self.assertIn(self.combined.name, self._text(cards["combined"]))
        self.assertIn("航测二号", self._text(cards["combined"]))

    def test_map_search_filters_sort_and_preserves_hidden_selection(self):
        self.maps._toggle_edit()
        self.maps._set_selected("pcd", True)
        self.maps.search.setText("航测二号")
        self._select(self.maps.status_filter, "waiting")
        self.assertEqual([item.map_id for item in self.maps.filtered_maps()], ["waiting"])
        self.assertEqual(self.maps.selected_map_ids, {"pcd"})
        self.assertIn("1", self.maps.selection_label.text())
        self.maps.search.clear()
        self._select(self.maps.status_filter, "all")
        self._select(self.maps.sort_combo, "oldest")
        self.assertEqual(self.maps.filtered_maps()[0].map_id, "combined")
        self._select(self.maps.sort_combo, "name")
        self.assertEqual(self.maps.filtered_maps()[0].map_id, "pcd")
        self.assertFalse(self.maps.map_cards["pcd"].primary_button.isEnabled())
        self.maps._clear_selection()
        self.assertEqual(self.maps.selected_map_ids, set())

    def test_map_actions_use_record_id_and_cancelled_delete_is_safe(self):
        calls = []
        card = MapCard(self.waiting)
        self.addCleanup(card.deleteLater)
        card.action_requested.connect(lambda map_id, action: calls.append((map_id, action)))
        card.primary_button.click()
        self.assertEqual(calls, [("waiting", "import_pcd")])
        with patch.object(self.maps, "show_detail") as detail:
            self.maps._handle_card_action("pgm", "detail")
            detail.assert_called_once_with("pgm")
        with patch.object(self.map_repo, "delete") as delete, patch(
            "ccs_monitor.pages.map_page.QMessageBox.question", return_value=QMessageBox.StandardButton.No,
        ):
            self.maps._delete_maps(["pcd"])
            delete.assert_not_called()
        with patch.object(self.map_repo, "set_active_map") as activate:
            self.maps.show_detail("pgm")
            activate.assert_not_called()
        self.assertEqual(self.map_repo.active_map_id(), "pcd")

    def test_task_fields_search_filters_and_sort(self):
        cards = {card.task.task_id: card for card in self.tasks.cards}
        self.assertEqual(cards["draft"].status.text(), "草稿")
        self.assertEqual(cards["ready"].status.text(), "配置就绪")
        self.assertEqual(cards["error:task"].status.text(), "加载异常")
        self.assertIn(self.ready.name, self._text(cards["ready"]))
        self.assertIn(self.task_error.error_message, self._text(cards["error:task"]))
        self.assertIn("文件时间", self._text(cards["error:task"]))
        self.assertFalse(cards["error:task"].delete_action.isEnabled())
        self.tasks.search.setText("巡检一号")
        self._select(self.tasks.status_filter, "ready")
        self.assertEqual([card.task.task_id for card in self.tasks.cards], ["ready"])
        self.assertIn("1", self.tasks.count_label.text())
        self.tasks.search.clear()
        self._select(self.tasks.status_filter, "all")
        self._select(self.tasks.sort_combo, "updated_asc")
        self.assertEqual(self.tasks.cards[0].task.task_id, "error:task")
        self._select(self.tasks.sort_combo, "name")
        self.assertEqual(self.tasks.cards[0].task.task_id, "draft")

    def test_task_open_and_cancelled_delete_do_not_execute_or_remove(self):
        self.execution.reset_mock()
        self.tasks.show_task("draft")
        self.assertEqual(self.tasks.editor.task.task_id, "draft")
        for method in ("execute_subtask", "execute_devices", "deliver_subtask", "stop_execution", "emergency_stop"):
            getattr(self.execution, method).assert_not_called()
        self.tasks.show_list()
        with patch.object(self.task_repo, "delete") as delete, patch(
            "ccs_monitor.pages.task_page.QMessageBox.question", return_value=QMessageBox.StandardButton.No,
        ):
            self.tasks._delete("draft")
            delete.assert_not_called()
        self.assertFalse(any("执行" in button.text() for button in self.tasks.list_page.findChildren(QPushButton)))
        self.tasks.compact_button.setChecked(True)
        self.assertTrue(self.settings.value("tasks/compact", False, type=bool))
        self.assertEqual(self.tasks._column_count(), 1)

    def test_wide_dark_and_narrow_light_layouts(self):
        capture = os.environ.get("CCS_CAPTURE_LISTS") == "1"
        output = Path(__file__).resolve().parents[1] / "artifacts"
        if capture:
            output.mkdir(exist_ok=True)
        for mode, width, height, label in (
            (ThemeMode.NIGHT, 1920, 1080, "wide-dark"),
            (ThemeMode.DAY, 800, 600, "narrow-light"),
        ):
            self.app.setPalette(build_qt_palette(mode))
            self.app.setStyleSheet(build_stylesheet(mode))
            for name, page in (("maps", self.maps), ("tasks", self.tasks)):
                page.set_theme(theme_palette(mode))
                page.resize(width, height)
                page.show()
                QTest.qWait(60)
                self.app.processEvents()
                self.assertEqual((page.width(), page.height()), (width, height))
                cards = list(page.map_cards.values()) if name == "maps" else page.cards
                self.assertTrue(cards)
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
                for card in cards:
                    self.assertGreaterEqual(card.width(), 320)
                    self.assertGreaterEqual(card.height(), 145)
                    for field in card.findChildren(QLabel):
                        if field.isVisible():
                            self.assertTrue(
                                field.parentWidget().rect().contains(field.geometry()),
                                f"{name}/{label}: label outside parent: {field.text()}",
                            )
                if capture:
                    self.assertTrue(page.grab().save(str(output / f"compact-{name}-{label}.png")))
                page.hide()


if __name__ == "__main__":
    unittest.main()
