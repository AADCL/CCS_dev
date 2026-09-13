"""Focused regressions for dashboard presentation and saved-task overlays."""
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest

from ccs_monitor.dashboard_presentation import battery_color, DEVICE_SNAPSHOT_ROLE
from ccs_monitor.data_source import simulated_overview
from ccs_monitor.device_colors import device_display_color
from ccs_monitor.main_window import MainWindow
from ccs_monitor.models import ConnectionStatus, DeviceTelemetrySnapshot, HealthStatus, UdpLinkStatus
from ccs_monitor.styles import ThemeMode, theme_palette
from ccs_monitor.task_conflicts import TaskConflictDetector
from ccs_monitor.task_models import TaskWaypoint, TaskDefinitionStatus
from tests import test_dashboard_layout as fixture


class DashboardPolishTests(unittest.TestCase):
    setUpClass = classmethod(fixture.DashboardLayoutTests.setUpClass.__func__)
    setUp = fixture.DashboardLayoutTests.setUp
    tearDown = fixture.DashboardLayoutTests.tearDown
    events = fixture.DashboardLayoutTests.events
    create_map = fixture.DashboardLayoutTests.create_map
    prepare_task = fixture.DashboardLayoutTests.prepare_task

    def paths(self, task):
        return {s.device_id: [(p.x, p.y, p.z) for p in s.waypoints] for s in task.subtasks}

    def assert_empty_overlay(self):
        viewer = self.page.viewer
        self.assertEqual(viewer._task_paths, {})
        self.assertEqual(viewer._task_conflicts, [])
        self.assertFalse(any(v.visible for v in viewer._task_path_visuals + viewer._task_point_visuals))

    def test_saved_task_coordinates_conflicts_and_base_layers(self):
        task = self.prepare_task()
        # Two crossing routes at z=0 must retain their original map coordinates.
        for subtask in task.subtasks:
            task = self.tasks.update_subtask(task.task_id, replace(subtask, waypoints=(
                TaskWaypoint('a', -0.5, 0, 0), TaskWaypoint('b', 1.5, 0, 0))))
        viewer = self.page.viewer
        expected = self.paths(task)
        conflicts = TaskConflictDetector().detect(task.subtasks, task.safety)
        self.assertTrue(conflicts)
        self.assertEqual(viewer._task_conflicts, [(c.x, c.y, c.z) for c in conflicts])
        for mode in ('grid', 'pointcloud', 'overlay'):
            viewer.layer_buttons[mode].click()
            for height in (0, 50, 100):
                viewer.height_legend.threshold.setValue(height)
                self.assertEqual(viewer._task_paths, expected)
                self.assertTrue(all(v.visible for v in viewer._task_path_visuals[:2]))
                for visual, coordinates in zip(viewer._task_path_visuals, expected.values()):
                    np.testing.assert_allclose(visual.pos, coordinates)
        self.page._select_device(task.subtasks[0].device_id)
        self.assertEqual(viewer._task_paths, expected)
        self.execution.execute_devices.assert_not_called()
        self.execution.stop_execution.assert_not_called()

    def test_switch_update_delete_and_invalid_task_clear_old_visuals(self):
        first = self.prepare_task()
        definition = self.maps.map_by_id(first.map_id)
        second = self.tasks.create('Single device', definition, self.source.snapshots()[:1])
        second = self.tasks.update_subtask(second.task_id, replace(second.subtasks[0], waypoints=(
            TaskWaypoint('c', 0, 1, 0), TaskWaypoint('d', 2, 1, 0))))
        self.page.task_combo.setCurrentIndex(self.page.task_combo.findData(second.task_id))
        self.assertEqual(self.page.viewer._task_paths, self.paths(second))
        self.assertFalse(self.page.viewer._task_path_visuals[1].visible)
        updated = replace(second, status=TaskDefinitionStatus.ERROR)
        with patch.object(self.tasks, 'task_by_id', return_value=updated):
            self.page._task_changed()
            self.assert_empty_overlay()
        self.page._task_changed()
        self.assertEqual(self.page.viewer._task_paths, self.paths(second))
        self.tasks.delete(second.task_id)
        self.assertEqual(self.page.viewer._task_paths, self.paths(first))
        self.tasks.delete(first.task_id)
        self.assert_empty_overlay()
        self.page.set_theme(theme_palette(ThemeMode.DAY))
        self.assert_empty_overlay()

    def test_map_mismatch_load_failure_and_partial_success(self):
        task = self.prepare_task()
        other = self.create_map('Other map', pgm=False)
        self.assert_empty_overlay()
        self.assertEqual(self.page.task_combo.currentData(), task.task_id)
        self.assertEqual(self.maps.active_map_id(), other.map_id)
        for _ in range(3):
            self.page._update_console_status('执行反馈')
            self.assertTrue(self.page.console_status.text().startswith(f'任务属于〈{task.map_name}〉，请切换地图'))
        self.maps.set_active_map(task.map_id)
        self.assertEqual(self.page.viewer._task_paths, self.paths(task))
        self.maps.pcd_path(task.map_id).write_text('broken', encoding='ascii')
        self.page._load_selected_map()
        self.assertFalse(self.page.viewer.pointcloud_loaded)
        self.assertTrue(self.page.viewer.pgm_loaded)
        self.assertEqual(self.page.viewer._task_paths, self.paths(task))
        yaml_path, _ = self.maps.pgm_paths(task.map_id)
        yaml_path.write_text('invalid', encoding='ascii')
        self.page._load_selected_map()
        self.assert_empty_overlay()
        self.assertIn('地图加载失败', self.page.viewer.status.text())
        self.execution.execute_devices.assert_not_called()

    def test_single_pcd_single_pgm_and_empty_task(self):
        for pcd, pgm in ((True, False), (False, True)):
            definition = self.create_map(f'Layers {pcd} {pgm}', pcd=pcd, pgm=pgm)
            task = self.tasks.create('Route', definition, self.source.snapshots()[:1])
            task = self.tasks.update_subtask(task.task_id, replace(task.subtasks[0], waypoints=(
                TaskWaypoint('a', 0, 0, 0), TaskWaypoint('b', 0.1, 0.1, 0))))
            self.page.task_combo.setCurrentIndex(self.page.task_combo.findData(task.task_id))
            self.assertEqual(self.page.viewer._task_paths, self.paths(task))
            empty = replace(task, subtasks=())
            with patch.object(self.tasks, 'task_by_id', return_value=empty):
                self.page._task_changed()
                self.assert_empty_overlay()
            self.page._task_changed()
            self.assertEqual(self.page.viewer._task_paths, self.paths(task))
            self.tasks.delete(task.task_id)
            self.assert_empty_overlay()
        self.execution.execute_devices.assert_not_called()

    def test_async_resume_preserves_current_task_and_rejects_stale_overlay(self):
        task = self.prepare_task()
        viewer = self.page.viewer
        viewer.suspend_static()
        viewer.resume_static()
        deadline = time.monotonic() + 5
        while viewer._suspended and time.monotonic() < deadline:
            self.events()
            QTest.qWait(10)
        self.assertFalse(viewer._suspended, 'Static map resume timed out')
        self.assertEqual(viewer._task_paths, self.paths(task))
        for mode in (ThemeMode.DAY, ThemeMode.NIGHT):
            self.page.set_theme(theme_palette(mode))
            self.assertEqual(viewer._task_paths, self.paths(task))
        viewer.suspend_static()
        # Queue an old result, then invalidate its generation with another map.
        old_result = dict(generation=viewer._load_generation, definition=viewer.current_map, errors=[])
        self.create_map('New map', pgm=False)
        viewer.static_loaded.emit(old_result)
        self.assert_empty_overlay()
        self.maps.set_active_map(task.map_id)
        self.page.set_active(False)
        self.page.set_active(True)
        deadline = time.monotonic() + 5
        while viewer._suspended and time.monotonic() < deadline:
            self.events()
            QTest.qWait(10)
        self.assertFalse(viewer._suspended)
        self.assertEqual(viewer._task_paths, self.paths(task))

    def test_telemetry_and_runtime_task_updates_do_not_recompute_conflicts(self):
        task = self.prepare_task()
        self.page.set_active(True)
        with patch('ccs_monitor.pages.command_dashboard_page.TaskConflictDetector.detect', wraps=TaskConflictDetector().detect) as detect:
            for _ in range(20):
                self.page._render_realtime()
                self.page._update_tasks()
            detect.assert_not_called()
            self.tasks.update_subtask(task.task_id, replace(task.subtasks[0], cruise_speed_mps=2.0))
            detect.assert_called_once()
        self.execution.execute_devices.assert_not_called()

    def test_uploaded_icons_fallback_and_reused_selection(self):
        panel = self.page.device_panel
        template = self.source.snapshots()[0]
        svg = self.root / 'uploaded.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44"><rect width="44" height="44" fill="#fa1234"/></svg>')
        png = self.root / 'uploaded.png'
        pixmap = QPixmap(44, 44)
        pixmap.fill(QColor('#12fa34'))
        self.assertTrue(pixmap.save(str(png)))
        invalid = self.root / 'invalid.svg'
        invalid.write_text('invalid')
        devices = [replace(template, device_id=f'test-{i}', device_name='轮趣WheelTech R550P 02' * 2,
                           device_icon_path=str(path), connection_status=ConnectionStatus.ONLINE)
                   for i, path in enumerate((svg, png, invalid, self.root / 'missing.svg'))]
        panel.set_devices(devices)
        self.events()
        for i, color in ((0, '#fa1234'), (1, '#12fa34')):
            image = panel.card_delegate.device_icon(devices[i]).pixmap(44, 44).toImage()
            self.assertEqual(image.pixelColor(22, 22).name(), color)
        for device in devices[2:]:
            self.assertFalse(panel.card_delegate.device_icon(device).pixmap(44, 44).isNull())
        selected = []
        panel.device_selected.connect(selected.append)
        item = panel.list.item(1)
        QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=panel.list.visualItemRect(item).center())
        self.assertEqual(selected, [devices[1].device_id])
        scroll = panel.list.verticalScrollBar().value()
        for i in range(20):
            devices[1] = replace(devices[1], battery_percent=i)
            panel.set_devices(devices)
        self.assertIs(panel.list.item(1), item)
        self.assertEqual(panel.list.currentItem(), item)
        self.assertEqual(panel.list.verticalScrollBar().value(), scroll)
        self.assertEqual(selected, [devices[1].device_id])
        self.assertEqual(item.data(DEVICE_SNAPSHOT_ROLE).battery_percent, 19)
        devices[1] = replace(devices[1], device_icon_path=str(svg))
        panel.set_devices(devices)
        self.assertEqual(panel.card_delegate.device_icon(devices[1]).pixmap(44, 44).toImage().pixelColor(22, 22).name(), '#fa1234')
        self.assertEqual(item.toolTip(), f'{devices[1].device_name} / {devices[1].device_id}')
        QTest.keyClick(panel.list, Qt.Key.Key_Down)
        self.assertEqual(panel.list.currentItem().data(Qt.ItemDataRole.UserRole), devices[2].device_id)
        self.assertEqual(selected[-1], devices[2].device_id)

    def test_battery_boundaries_and_status_updates_preserve_values(self):
        panel = self.page.status_panel
        device = self.source.snapshots()[0]
        for mode in ThemeMode:
            palette = theme_palette(mode)
            panel.set_theme(palette)
            for value in (None, 0, 24.9, 25, 58.9, 100):
                panel.set_device(replace(device, battery_percent=value))
                self.assertEqual(panel.battery.value, value)
                self.assertEqual(panel.fields['电量'].text(), '--' if value is None else f'{value:.1f}%')
                self.assertEqual(battery_color(value, palette), palette.dashboard_muted if value is None else palette.error if value < 25 else palette.good)
            for health, text in ((HealthStatus.NORMAL, '正常'), (HealthStatus.ATTENTION, '需关注'),
                                 (HealthStatus.ABNORMAL, '异常'), (HealthStatus.UNKNOWN, '未知')):
                panel.set_device(replace(device, health_status=health, connection_status=ConnectionStatus.ONLINE))
                self.assertEqual(panel.fields['健康'].text(), text)
                self.assertEqual(panel.fields['MQTT'].text(), '在线')
                self.assertIn(device_display_color(device.device_id), panel.identity.styleSheet())
            panel.set_device(replace(device, connection_status=ConnectionStatus.OFFLINE))
            self.assertEqual(panel.fields['MQTT'].text(), '离线')
            for status, text in ((UdpLinkStatus.ONLINE, '在线'), (UdpLinkStatus.WARNING, '警告'),
                                 (UdpLinkStatus.OFFLINE, '断开'), (UdpLinkStatus.UNKNOWN, '未知'),
                                 (UdpLinkStatus.MODULE_ERROR, '模块故障')):
                panel.set_telemetry(DeviceTelemetrySnapshot(device.device_id, udp_link_status=status))
                self.assertEqual(panel.fields['UDP'].text(), text)
            panel.set_telemetry(None)
            self.assertEqual(panel.fields['UDP'].text(), '未知')
            panel.set_device(None)
            self.assertIsNone(panel.battery.value)
            self.assertTrue(all(value.text() == '--' for value in panel.fields.values()))

    def test_main_window_labels_icons_and_long_device_at_all_sizes(self):
        self.prepare_task()
        window = MainWindow(self.source, simulated_overview(), map_repository=self.maps,
                            task_repository=self.tasks)
        try:
            window.show()
            window.set_current_page(4)
            page = window.command_page
            for mode in ThemeMode:
                window.apply_theme(mode, persist=False)
                self.assertTrue(all(not button.icon().isNull() for button in window.nav_buttons))
                for width, height in ((1920, 1080), (1440, 900), (1024, 768), (800, 600)):
                    window.resize(width, height)
                    self.events()
                    long_name = '轮趣WheelTech R550P 02 / ' * 8
                    page.current_device.setText(long_name)
                    self.assertEqual(page.current_device.toolTip(), long_name)
                    rectangles = []
                    for label, control in page.console_fields:
                        self.assertGreaterEqual(label.contentsRect().width(), label.fontMetrics().horizontalAdvance(label.text()))
                        self.assertGreaterEqual(label.contentsRect().height(), label.fontMetrics().height())
                        for widget in (label, control):
                            rect = QRect(widget.mapTo(page, QPoint()), widget.size())
                            self.assertTrue(widget.isVisible())
                            self.assertTrue(page.rect().contains(rect), (widget, rect, page.rect()))
                            rectangles.append(rect)
                    for i, rect in enumerate(rectangles):
                        self.assertFalse(any(rect.intersects(other) for other in rectangles[i+1:]))
                    self.assertEqual(page.map_combo.height(), page.task_combo.height())
                    self.assertEqual(page.map_combo.height(), 36)
                    self.assertEqual(page.map_combo.width(), 330)
                    self.assertEqual(page.task_combo.width(), 220)
                    self.assertLessEqual(abs(page.dashboard_title.mapTo(page.top_bar, page.dashboard_title.rect().center()).x() - page.top_bar.rect().center().x()), 2)
                    for button in (page.fit_button, page.reset_button, page.start_button, page.stop_button, page.fullscreen_button):
                        self.assertFalse(button.icon().isNull())
            page.set_fullscreen_state(True)
            self.assertEqual(page.fullscreen_button.property('appIconName'), 'exit_fullscreen')
            page.set_fullscreen_state(False)
            self.assertEqual(page.fullscreen_button.property('appIconName'), 'fullscreen')
        finally:
            window.close()
            window.deleteLater()
            self.events()


if __name__ == '__main__':
    unittest.main()
