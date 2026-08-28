import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ccs_monitor.map_building_v2 import RemoteMappingSnapshot
from ccs_monitor.models import (
    ConnectionStatus,
    DeviceProfile,
    DeviceSnapshot,
    DeviceTelemetrySnapshot,
    MapDefinition,
    MapTransform,
    PoseTelemetry,
    TaskStatus,
)
from ccs_monitor.pages.map_page import (
    MapPage,
    MapOnlineDevicePanel,
    PointCloudViewer,
    device_task_status_text,
    height_rainbow_colors,
    transform_device_pose,
)
from ccs_monitor.styles import ThemeMode, theme_palette


class MapDeviceOverlayMathTests(unittest.TestCase):
    def test_height_colors_are_red_low_and_violet_high(self):
        points = np.asarray(((0, 0, -2), (0, 0, 0), (0, 0, 3)), dtype=np.float32)
        colors = height_rainbow_colors(points)
        np.testing.assert_allclose(colors[0], (1, 0, 0, 1), atol=1e-6)
        np.testing.assert_allclose(colors[-1], (0.5, 0, 1, 1), atol=1e-6)
        self.assertGreater(colors[1, 1] + colors[1, 2], colors[1, 0])

    def test_equal_height_uses_middle_of_rainbow(self):
        points = np.asarray(((0, 0, 4), (1, 2, 4)), dtype=np.float32)
        colors = height_rainbow_colors(points)
        np.testing.assert_allclose(colors[0], colors[1])
        self.assertGreater(colors[0, 1], 0.9)
        self.assertGreater(colors[0, 2], 0.4)

    def test_non_finite_points_receive_transparent_colors(self):
        points = np.asarray(((0, 0, 1), (np.nan, 0, 2)), dtype=np.float32)
        colors = height_rainbow_colors(points)
        np.testing.assert_allclose(colors[1], (0, 0, 0, 0))

    def test_transform_composes_position_and_yaw(self):
        pose = PoseTelemetry(1, 0, 2, 0, 0, 10, 0.1)
        transform = MapTransform(
            "UGV-1", translation_m=(5, 2, 1), rotation_rpy_deg=(0, 0, 90)
        )
        result = transform_device_pose(pose, transform)
        np.testing.assert_allclose((result.x, result.y, result.z), (5, 3, 3), atol=1e-6)
        self.assertAlmostEqual(result.yaw, 100.0)
        self.assertEqual(result.sample_age_seconds, 0.1)

    def test_mapping_status_overrides_general_task_status(self):
        device = DeviceSnapshot(
            "UGV-1", "Vehicle", "UGV", task_status=TaskStatus.STANDBY
        )
        remote = RemoteMappingSnapshot(
            "map-1", "UGV-1", "session-1", "mapping", "running",
            datetime.now(timezone.utc),
        )
        self.assertEqual(device_task_status_text(device), "等待")
        self.assertEqual(device_task_status_text(device, remote), "建图中")

    def test_missing_pose_during_relocalization_keeps_existing_trail(self):
        device = DeviceSnapshot(
            "UGV_001", "Scout", "UGV", connection_status=ConnectionStatus.ONLINE,
            frame_id="odom",
        )
        profile = DeviceProfile(
            "UGV_001", "Scout", "UGV", "127.0.0.1",
            relocalization_profile="scout_mini",
        )
        telemetry = {
            "value": DeviceTelemetrySnapshot(
                "UGV_001", vision_pose=PoseTelemetry(1, 2, 0, 0, 0, 0),
            )
        }
        rendered_trails = []
        device_panel = SimpleNamespace(
            selected_device_id="UGV_001", set_frame_note=lambda *_args: None,
        )
        viewer = SimpleNamespace(
            set_device_markers=lambda *_args: None,
            set_selected_device_pose=lambda *_args: None,
            set_device_trails=lambda trails: rendered_trails.append(dict(trails)),
        )
        detail_page = SimpleNamespace(device_panel=device_panel, viewer=viewer)
        page = SimpleNamespace(
            current_map_id="map-1", detail_page=detail_page,
            page_stack=SimpleNamespace(currentWidget=lambda: detail_page),
            repository=SimpleNamespace(
                map_by_id=lambda _map_id: MapDefinition("map-1", "Map", frame_id="odom")
            ),
            mapping_service=None, devices=[device],
            _latest_telemetry={"ugv_001": telemetry["value"]},
            telemetry_store=None,
            source=SimpleNamespace(profile=lambda _device_id: profile),
            _device_trails={},
        )

        MapPage._refresh_device_overlays(page)
        self.assertEqual(page._device_trails["UGV_001"], [(1, 2, 0)])
        page._latest_telemetry["ugv_001"] = DeviceTelemetrySnapshot("UGV_001")
        MapPage._refresh_device_overlays(page)
        self.assertEqual(page._device_trails["UGV_001"], [(1, 2, 0)])
        self.assertEqual(rendered_trails[-1]["UGV_001"], [(1, 2, 0)])


class MapOnlineDevicePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_online_devices_are_shown_and_mapping_device_is_first(self):
        devices = [
            DeviceSnapshot(
                "A", "Alpha", "UGV", connection_status=ConnectionStatus.ONLINE,
                ip_address="127.0.0.1",
            ),
            DeviceSnapshot(
                "B", "Beta", "UAV", connection_status=ConnectionStatus.ONLINE,
                ip_address="127.0.0.2",
            ),
            DeviceSnapshot(
                "C", "Charlie", "USV", connection_status=ConnectionStatus.WARNING,
                ip_address="127.0.0.3",
            ),
        ]
        remote = RemoteMappingSnapshot(
            "map-1", "B", "session-1", "preparing", "preparing",
            datetime.now(timezone.utc), frame_id="lio_odom",
        )
        panel = MapOnlineDevicePanel()
        panel.set_theme(theme_palette(ThemeMode.DAY))
        self.assertEqual(panel.collapse_button.property("appIconMode"), "day")
        self.assertEqual(panel.collapse_button.property("appIconName"), "close")
        panel.set_devices(devices, remote)
        self.assertEqual([item.device_id for item in panel.devices], ["B", "A"])
        self.assertEqual(set(panel.cards), {"A", "B"})
        self.assertEqual(panel.cards["B"].task_state.text(), "协商")
        self.assertEqual(panel.cards["B"].frame_name.text(), "坐标系 lio_odom")
        panel._active_video_id = "A"
        with patch.object(panel.cards["A"], "stop_video") as stop_previous:
            panel._on_video_requested("B", True)
            stop_previous.assert_called_once_with()
        self.assertEqual(panel._active_video_id, "B")
        panel.stop_videos()
        panel.toggle_collapsed()
        self.assertEqual(panel.collapse_button.property("appIconName"), "expand")
        panel.deleteLater()

    def test_device_visuals_are_configured_above_map_layers(self):
        viewer = PointCloudViewer()
        self.assertLess(viewer.MAP_LAYER_ORDER, viewer.DEVICE_LAYER_ORDER)
        self.assertEqual(viewer._points_visual.order, viewer.MAP_LAYER_ORDER)
        for visual in (
            viewer._marker_visual,
            viewer._device_axis_visual,
            viewer._trail_visual,
        ):
            self.assertEqual(visual.order, viewer.DEVICE_LAYER_ORDER)
        viewer.deleteLater()


if __name__ == "__main__":
    unittest.main()
