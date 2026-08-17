import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from ccs_monitor.data_source import SimulatedDeviceSource, simulated_overview
    from ccs_monitor.device_config import DeviceConfigRepository
    from ccs_monitor.main_window import MainWindow
    from ccs_monitor.map_repository import MapRepository
    from ccs_monitor.task_repository import TaskRepository
    from ccs_monitor.models import (
        ConnectionStatus,
        DeviceTelemetrySnapshot,
        ImuTelemetry,
        PoseTelemetry,
    )
    from ccs_monitor.pages.command_dashboard_page import DevicePanelMode, TelemetryTrendBuffer
    from ccs_monitor.pages.map_page import MiddlePanTurntableCameraMixin
    import numpy as np


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class TelemetryTrendBufferTests(unittest.TestCase):
    def test_window_capacity_and_attitude_fallback(self):
        now = [0.0]
        buffer = TelemetryTrendBuffer(clock=lambda: now[0])
        for index in range(1300):
            now[0] = index * 0.05
            telemetry = DeviceTelemetrySnapshot(
                "UAV-1",
                global_pose=PoseTelemetry(index, index + 1, index + 2, 1, 2, 3),
            )
            buffer.append("UAV-1", telemetry)
        self.assertLessEqual(buffer.sample_count("UAV-1"), 1200)
        position = buffer.series("UAV-1", "position")
        self.assertGreater(len(position["X"]), 0)
        self.assertGreaterEqual(position["X"][0][0], -60.0)

        now[0] += 0.1
        fallback = DeviceTelemetrySnapshot(
            "UAV-2",
            imu=ImuTelemetry(10, 20, 30, 0, 0, 0, 0, 0, 0),
        )
        self.assertTrue(buffer.append("UAV-2", fallback))
        self.assertEqual(buffer.series("UAV-2", "attitude")["Yaw"][-1][1], 30)

    def test_missing_pose_and_imu_are_not_fabricated(self):
        buffer = TelemetryTrendBuffer(clock=lambda: 1.0)
        self.assertFalse(buffer.append("UAV-1", DeviceTelemetrySnapshot("UAV-1")))
        self.assertEqual(buffer.sample_count("UAV-1"), 0)


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class MiddlePanCameraTests(unittest.TestCase):
    def test_button_two_drag_uses_fast_pan_without_changing_distance(self):
        class CameraBase:
            def viewbox_mouse_event(self, event):
                self.base_event_called = True

        class Camera(MiddlePanTurntableCameraMixin, CameraBase):
            interactive = True
            scale_factor = 10.0
            center = (0.0, 0.0, 0.0)
            _event_value = None
            _flip_factors = np.asarray((1.0, 1.0, 1.0))
            _viewbox = SimpleNamespace(size=np.asarray((100.0, 100.0)))
            distance = 25.0

            @staticmethod
            def _dist_to_trans(distance):
                return distance[0], distance[1], 0.0

            @staticmethod
            def _get_dim_vectors():
                return (
                    np.asarray((0.0, 0.0, 1.0)),
                    np.asarray((0.0, 1.0, 0.0)),
                    np.asarray((1.0, 0.0, 0.0)),
                )

            def view_changed(self):
                self.changed = True

        press = SimpleNamespace(pos=np.asarray((0.0, 0.0)))
        event = SimpleNamespace(
            handled=False,
            type="mouse_move",
            press_event=press,
            buttons=(2,),
            mouse_event=SimpleNamespace(
                press_event=press,
                pos=np.asarray((10.0, 20.0)),
                modifiers=(),
            ),
        )
        camera = Camera()
        camera.viewbox_mouse_event(event)
        self.assertEqual(camera.center, (-3.0, 6.0, 0.0))
        self.assertEqual(camera.distance, 25.0)
        self.assertTrue(event.handled)
        self.assertTrue(camera.changed)


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class CommandDashboardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source = SimulatedDeviceSource(
            DeviceConfigRepository(Path(self.temp_dir.name) / "devices.json")
        )
        self.maps = MapRepository(Path(self.temp_dir.name) / "map_server")
        self.tasks = TaskRepository(Path(self.temp_dir.name) / "task_server")
        self.window = MainWindow(
            self.source, simulated_overview(), map_repository=self.maps,
            task_repository=self.tasks,
        )
        self.window.show()
        self.window.set_current_page(4)
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def test_all_online_device_types_are_listed_and_task_controls_are_disabled(self):
        expected = [
            device for device in self.source.snapshots()
            if device.connection_status == ConnectionStatus.ONLINE
        ]
        page = self.window.command_page
        self.assertEqual(page.device_panel.list.count(), len(expected))
        self.assertEqual(
            {device.device_type for device in page.device_panel.devices},
            {device.device_type for device in expected},
        )
        self.assertEqual(page.selected_device_id, expected[0].device_id)
        self.assertEqual(page.online_count.text(), f"ONLINE DEVICE {len(expected):02d}")
        self.assertFalse(page.task_combo.isEnabled())
        self.assertFalse(page.start_button.isEnabled())
        self.assertFalse(page.stop_button.isEnabled())

        previous_id = page.selected_device_id
        updated = [
            replace(device, connection_status=ConnectionStatus.OFFLINE)
            if device.device_id == previous_id else device
            for device in self.source.snapshots()
        ]
        self.source.devices_updated.emit(updated)
        self.app.processEvents()
        self.assertNotEqual(page.selected_device_id, previous_id)
        self.assertIn(
            page.selected_device_id,
            {device.device_id for device in updated if device.connection_status == ConnectionStatus.ONLINE},
        )

    def test_page_timers_and_manual_fullscreen_lifecycle(self):
        page = self.window.command_page
        self.assertTrue(page.render_timer.isActive())
        self.assertTrue(page.animation_timer.isActive())
        page.scan_toggle.setChecked(False)
        self.assertFalse(page.animation_timer.isActive())
        self.assertFalse(page.scan_overlay.isVisible())
        page.scan_toggle.setChecked(True)
        self.assertTrue(page.animation_timer.isActive())
        with patch.object(self.window, "showFullScreen"), patch.object(self.window, "showNormal"):
            self.window.set_dashboard_fullscreen(True)
            self.assertTrue(self.window.dashboard_fullscreen)
            self.assertFalse(self.window.navigation.isVisible())
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            self.window.keyPressEvent(event)
            self.assertFalse(self.window.dashboard_fullscreen)
        self.window.set_current_page(0)
        self.app.processEvents()
        self.assertFalse(page.render_timer.isActive())
        self.assertFalse(page.animation_timer.isActive())

    def test_status_panel_responds_to_width_without_overriding_user_choice(self):
        page = self.window.command_page
        self.window.resize(800, 600)
        self.app.processEvents()
        self.assertFalse(page.status_panel.expanded)

        self.window.resize(1440, 900)
        self.app.processEvents()
        self.assertTrue(page.status_panel.expanded)

        page.status_panel.toggle_expanded()
        self.assertTrue(page.status_panel.user_collapsed)
        self.window.resize(1024, 768)
        self.app.processEvents()
        self.window.resize(1440, 900)
        self.app.processEvents()
        self.assertFalse(page.status_panel.expanded)

    def test_chart_header_device_panel_and_console_collapsing(self):
        page = self.window.command_page
        page.status_panel.set_expanded(True)
        self.app.processEvents()
        position_chart = page.status_panel.position_chart
        attitude_chart = page.status_panel.attitude_chart
        self.assertEqual(position_chart.title_label.text(), "位置数据")
        self.assertEqual(attitude_chart.title_label.text(), "姿态数据")
        self.assertFalse(position_chart.chart.legend().isVisible())
        self.assertEqual(tuple(position_chart.legend_labels), ("X", "Y", "Z"))
        self.assertEqual(position_chart.x_axis.titleText(), "")
        self.assertEqual(position_chart.y_axis.titleText(), "")

        page.device_panel.set_mode(DevicePanelMode.DETAIL)
        device_center_width = page.upper_splitter.sizes()[1]
        page.device_panel.toggle_collapsed()
        self.app.processEvents()
        self.assertEqual(page.device_panel.mode, DevicePanelMode.COLLAPSED)
        self.assertLessEqual(page.device_panel.maximumWidth(), 38)
        self.assertLessEqual(page.upper_splitter.sizes()[0], 38)
        self.assertGreaterEqual(page.upper_splitter.sizes()[1], device_center_width)
        self.assertFalse(page.device_panel.list.isVisible())
        self.window.set_current_page(0)
        self.window.set_current_page(4)
        self.assertEqual(page.device_panel.mode, DevicePanelMode.COLLAPSED)
        page.device_panel.toggle_collapsed()
        self.assertEqual(page.device_panel.mode, DevicePanelMode.DETAIL)

        expanded_upper = page.vertical_splitter.sizes()[0]
        page.console_panel.set_collapsed(True)
        self.app.processEvents()
        self.assertTrue(page.console_panel.collapsed)
        self.assertFalse(page.console_panel.content_widget.isVisible())
        self.assertEqual(page.console_panel.maximumHeight(), page.console_panel.COLLAPSED_HEIGHT)
        self.assertGreaterEqual(page.vertical_splitter.sizes()[0], expanded_upper)
        page.console_panel.set_collapsed(False)
        self.app.processEvents()
        self.assertFalse(page.console_panel.collapsed)
        self.assertTrue(page.console_panel.content_widget.isVisible())


if __name__ == "__main__":
    unittest.main()
