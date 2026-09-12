import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QSettings, Signal, Qt
from PySide6.QtWidgets import QApplication

from ccs_monitor.app import configure_application_font
from ccs_monitor.data_source import SimulatedDeviceSource
from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.device_types import DeviceTypeTemplateRepository
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.pages.command_dashboard_page import CommandDashboardPage, DevicePanelMode
from ccs_monitor.pages.map_page import MapViewerSettings, PointCloudViewer
from ccs_monitor.styles import ThemeMode, build_stylesheet, theme_palette
from ccs_monitor.task_models import TaskExecutionStatus, TaskWaypoint
from ccs_monitor.task_repository import TaskRepository
from tests.test_point_cloud import write_ascii_pcd


class ExecutionDouble(QObject):
    availability_changed = Signal(bool, str)
    execution_updated = Signal(object)
    available = True

    def __init__(self):
        super().__init__()
        self.execute_devices = Mock(return_value=SimpleNamespace(execution_id='execution-1'))
        self.stop_execution = Mock()


class DashboardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_application_font(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        for target, value in (
            ('ccs_monitor.main_window.QSettings', self.settings),
            ('ccs_monitor.pages.map_page.QSettings', self.settings),
        ):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch('ccs_monitor.pages.map_page.MAP_VIEWER_SETTINGS', MapViewerSettings(self.settings))
        self.display_settings = p.start(); self.addCleanup(p.stop)
        p = patch('ccs_monitor.data_source.DeviceTypeTemplateRepository', return_value=DeviceTypeTemplateRepository(self.root/'types.json', self.root/'icons'))
        p.start(); self.addCleanup(p.stop)
        self.source = SimulatedDeviceSource(DeviceConfigRepository(self.root / 'devices.json'))
        self.maps = MapRepository(self.root / 'maps')
        self.tasks = TaskRepository(self.root / 'tasks')
        self.execution = ExecutionDouble()
        def viewer_factory():
            viewer = PointCloudViewer()
            self.fit_action = Mock(wraps=viewer.fit_all)
            self.reset_action = Mock(wraps=viewer.reset_view)
            viewer.fit_all = self.fit_action
            viewer.reset_view = self.reset_action
            return viewer
        self.page = CommandDashboardPage(self.source, self.maps, task_repository=self.tasks, execution_service=self.execution, viewer_factory=viewer_factory)
        self.app.setStyleSheet(build_stylesheet(ThemeMode.NIGHT))
        self.page.resize(1440, 836)
        self.page.show()
        self.events()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.events()

    def events(self):
        for _ in range(4):
            self.app.processEvents()

    def create_map(self, name='Test map', pcd=True, pgm=True):
        definition = self.maps.create_empty(name)
        if pcd:
            path = self.root / 'source.pcd'
            write_ascii_pcd(path, [(0, 0, -1), (1, 1, 0), (2, 2, 1), (3, 3, 2)])
            definition = self.maps.import_pcd(definition.map_id, path)
        if pgm:
            (self.root / 'map.pgm').write_text('P2\n2 2\n255\n0 255\n255 0\n', encoding='ascii')
            path = self.root / 'map.yaml'
            path.write_text('image: map.pgm\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.2\n', encoding='ascii')
            definition = self.maps.import_pgm(definition.map_id, path)
        self.maps.set_active_map(definition.map_id)
        self.events()
        return definition

    def test_title_center_and_controls_accessible_at_supported_sizes(self):
        self.create_map()
        page = self.page
        for mode in (ThemeMode.NIGHT, ThemeMode.DAY):
            self.app.setStyleSheet(build_stylesheet(mode)); page.set_theme(theme_palette(mode))
            for width, height in ((1920, 1080), (1440, 900), (1024, 768), (800, 600)):
                with self.subTest(mode=mode, width=width):
                    page.resize(width, height - 64); self.events()
                    self.assertEqual(page.width(), width)
                    self.assertEqual(page.height(), height - 64)
                    title = page.dashboard_title
                    center = title.mapTo(page.top_bar, title.rect().center()).x()
                    self.assertLessEqual(abs(center - page.top_bar.rect().center().x()), 2)
                    visible = [page.map_combo, page.task_combo, page.start_button, page.stop_button,
                               page.fit_button, page.reset_button, page.fullscreen_button,
                               page.viewer.height_legend.threshold, page.viewer.grid_spacing_input,
                               page.viewer.grid_opacity_input, *page.viewer.layer_buttons.values()]
                    rects = []
                    for widget in visible:
                        self.assertTrue(widget.isVisible(), widget.objectName() or widget.__class__.__name__)
                        rect = QRect(widget.mapTo(page, QPoint()), widget.size())
                        self.assertTrue(page.rect().contains(rect), (widget, rect, page.rect()))
                        self.assertGreater(rect.width(), 0)
                        rects.append(rect)
                    for i, rect in enumerate(rects):
                        for other in rects[i+1:]:
                            self.assertFalse(rect.intersects(other), (rect, other))
                    self.assertFalse(page.layer_combo.isVisible())
                    if width < 1000:
                        self.assertLessEqual(page.upper_splitter.sizes()[2], 46)
                        self.assertGreater(page.upper_splitter.sizes()[1], 500)

    def test_collapsed_console_reflows_after_resize_and_long_header_stays_centered(self):
        page = self.page
        page.resize(1920, 1016)
        self.events()
        page.console_panel.set_collapsed(True)
        page.resize(800, 536)
        self.events()
        page.console_panel.set_collapsed(False)
        self.events()
        needed = page.console_layout.heightForWidth(page.console_panel.width() - 24) + 44
        self.assertGreaterEqual(page.console_panel.height(), needed)
        page.system_status.setText("MQTT CONNECTION FAULT  |  UDP MODULE ERROR")
        page.top_bar._arrange()
        self.events()
        left, title, right = page.top_bar.sections
        self.assertFalse(title.geometry().intersects(left.geometry()))
        self.assertFalse(title.geometry().intersects(right.geometry()))
        self.assertLessEqual(abs(title.geometry().center().x() - page.top_bar.rect().center().x()), 2)

    def test_height_slider_filters_original_points_and_preserves_mode_visibility(self):
        self.create_map()
        viewer = self.page.viewer
        points = np.array([[0, 0, -1], [1, 1, 0], [2, 2, 1], [3, 3, 2]], dtype=np.float32)
        recorded = Mock()
        with patch.object(viewer, '_points_visual', recorded):
            for value, expected in ((0, points), (50, points[:2]), (100, points[:1])):
                viewer.height_legend.threshold.setValue(value)
                viewer._refresh_point_colors()
                np.testing.assert_array_equal(recorded.set_data.call_args.args[0], expected)
                np.testing.assert_array_equal(viewer._point_data, points)
            self.assertEqual(viewer.height_legend.minimum_label.text(), '-1.0m')
            self.assertEqual(viewer.height_legend.maximum_label.text(), '2.0m')
            self.assertIn('-1.0m', viewer.height_legend.threshold.toolTip())
        viewer.layer_buttons['grid'].click(); self.events()
        self.assertFalse(viewer.height_legend.isVisible())
        viewer.layer_buttons['pointcloud'].click(); self.events()
        self.assertTrue(viewer.height_legend.isVisible())
        self.assertEqual(viewer.height_legend.threshold.value(), 100)

    def test_one_layer_control_keeps_map_and_console_in_sync(self):
        dual = self.create_map('dual')
        viewer = self.page.viewer
        for mode, text in [('grid', '栅格'), ('pointcloud', '点云'), ('overlay', '叠加')]:
            viewer.layer_buttons[mode].click()
            self.assertEqual(viewer.layer_mode, mode)
            self.assertEqual(self.page.layer_combo.currentData(), mode)
            self.assertIn(text, self.page.console_status.text())
            self.assertIn(mode.upper(), self.page.map_state.text())
        cloud = self.create_map('cloud', pgm=False)
        raster = self.create_map('raster', pcd=False)
        self.page.layer_combo.setCurrentIndex(self.page.layer_combo.findData('pointcloud'))
        self.maps.set_active_map(raster.map_id)
        self.assertEqual(viewer.layer_mode, 'grid')
        self.assertEqual(self.page.layer_combo.currentData(), 'grid')
        self.page.map_combo.setCurrentIndex(self.page.map_combo.findData(cloud.map_id))
        self.assertEqual(self.maps.active_map_id(), cloud.map_id)
        self.assertEqual(viewer.layer_mode, 'pointcloud')
        self.assertIn('cloud', self.page.map_state.text())
        self.maps.set_active_map(dual.map_id)
        self.assertEqual(self.page.map_combo.currentData(), dual.map_id)

    def test_view_actions_are_connected_once_and_relayout_keeps_settings(self):
        self.create_map()
        viewer = self.page.viewer
        viewer.grid_check.setChecked(True)
        viewer.grid_spacing_input.setValue(.6)
        viewer.grid_opacity_input.setValue(50)
        viewer.devices_check.setChecked(False)
        viewer.trails_check.setChecked(False)
        viewer.height_legend.threshold.setValue(37)
        camera = viewer._camera
        camera.azimuth = 87
        self.reset_action.reset_mock()
        self.page.reset_button.click()
        self.reset_action.assert_called_once_with()
        self.assertNotEqual(camera.azimuth, 87)
        self.fit_action.reset_mock()
        self.page.fit_button.click()
        self.fit_action.assert_called_once_with()
        requested = []
        self.page.fullscreen_requested.connect(requested.append)
        self.page.fullscreen_button.click()
        self.assertEqual(requested, [True])
        for width in (800, 1920, 1024, 1440):
            self.page.resize(width, 700); self.events()
        self.page.console_panel.set_collapsed(True)
        self.page.console_panel.set_collapsed(False)
        self.page.set_theme(theme_palette(ThemeMode.DAY))
        self.assertEqual(viewer.grid_spacing_input.value(), .6)
        self.assertEqual(viewer.grid_opacity_input.value(), 50)
        self.assertFalse(viewer.devices_visible)
        self.assertFalse(viewer.trails_visible)
        self.assertEqual(viewer.height_legend.threshold.value(), 37)

    def prepare_task(self):
        definition = self.create_map()
        devices = self.source.snapshots()[:2]
        task = self.tasks.create('Joint task', definition, devices)
        for index, subtask in enumerate(task.subtasks):
            task = self.tasks.update_subtask(task.task_id, replace(subtask, waypoints=(
                TaskWaypoint('a', index * 20, 0, 1), TaskWaypoint('b', index * 20 + 2, 0, 1))))
        self.page._update_tasks()
        return task

    def test_task_buttons_preserve_device_set_execution_and_terminal_feedback(self):
        task = self.prepare_task()
        self.assertTrue(self.page.start_button.isEnabled())
        self.page.start_button.click()
        self.execution.execute_devices.assert_called_once_with(task, tuple(s.device_id for s in task.subtasks), forced_conflict_reason=None)
        self.assertFalse(self.page.start_button.isEnabled())
        self.assertTrue(self.page.stop_button.isEnabled())
        self.page.stop_button.click()
        self.execution.stop_execution.assert_called_once_with('execution-1', '指控大屏终止任务')
        self.execution.execution_updated.emit(SimpleNamespace(task_id=task.task_id, execution_id='execution-1', status=TaskExecutionStatus.COMPLETED, message='完成'))
        self.assertFalse(self.page.stop_button.isEnabled())
        self.assertTrue(self.page.start_button.isEnabled())
        self.assertIn('完成', self.page.console_status.text())
        self.execution.available = False
        self.execution.availability_changed.emit(False, 'Unavailable')
        self.assertFalse(self.page.start_button.isEnabled())

    def test_conflict_cancel_and_start_failure_do_not_start_or_enable_stop(self):
        self.prepare_task()
        with patch('ccs_monitor.pages.command_dashboard_page.TaskConflictDetector.detect', return_value=[object()]), patch('ccs_monitor.pages.command_dashboard_page.QInputDialog.getText', return_value=('', False)):
            self.page.start_button.click()
        self.execution.execute_devices.assert_not_called()
        self.assertFalse(self.page.stop_button.isEnabled())
        self.execution.execute_devices.side_effect = RuntimeError('test failure')
        with patch('ccs_monitor.pages.command_dashboard_page.QMessageBox.critical') as dialog:
            self.page.start_button.click()
            dialog.assert_called_once()
        self.assertFalse(self.page.stop_button.isEnabled())

    def test_map_failure_and_empty_state_keep_existing_feedback(self):
        self.assertIn('无可用地图', self.page.map_state.text())
        self.assertFalse(self.page.start_button.isEnabled())
        definition = self.create_map(pgm=False)
        self.maps.pcd_path(definition.map_id).write_text('broken', encoding='ascii')
        self.page._load_selected_map()
        self.assertIn('地图加载失败', self.page.viewer.status.text())
        self.assertFalse(self.page.viewer.height_legend.isVisible())

    def test_default_viewer_layout_and_controls_remain_unchanged(self):
        viewer = PointCloudViewer()
        try:
            self.assertFalse(hasattr(viewer, 'dashboard_footer'))
            self.assertEqual(viewer.height_legend.parentWidget(), viewer.display_toolbar)
            self.assertEqual(viewer._canvas.native.minimumHeight(), 320)
            self.assertTrue(viewer.cursor_coordinates_enabled)
        finally:
            viewer.close(); viewer.deleteLater(); self.events()


if __name__ == '__main__':
    unittest.main()
