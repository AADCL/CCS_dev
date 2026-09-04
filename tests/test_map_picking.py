import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np


DEPS = all(importlib.util.find_spec(name) is not None for name in ("numpy", "PySide6", "vispy"))

if DEPS:
    from PySide6.QtWidgets import QApplication

    from ccs_monitor.models import FrameTransform, MapBounds, MapDefinition, PgmMapMetadata, PoseTelemetry
    from ccs_monitor.pages.map_page import (
        MAP_VIEWER_SETTINGS, PointCloudViewer, coordinate_tick_spacing,
        cursor_anchored_camera_center,
        fixed_width_font, pick_mode_zoom_distance,
        transform_pose_by_binding,
        unproject_screen_to_plane,
    )


class HomogeneousTransform:
    def imap(self, value):
        _x, _y, depth, _w = value
        cartesian = np.asarray((10.0, 20.0, 10.0 if depth == 0.0 else -10.0))
        w = 2.0 if depth == 0.0 else 0.5
        return np.append(cartesian * w, w)


@unittest.skipUnless(DEPS, "map picking dependencies are unavailable")
class MapPickingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unprojection_performs_homogeneous_divide(self):
        self.assertEqual(unproject_screen_to_plane(HomogeneousTransform(), 100, 200), (10.0, 20.0))

    def test_pick_mode_wheel_zoom_is_bounded(self):
        self.assertLess(pick_mode_zoom_distance(100.0, 120), 100.0)
        self.assertGreater(pick_mode_zoom_distance(100.0, -120), 100.0)
        self.assertEqual(pick_mode_zoom_distance(0.1, 120), 0.1)

    def test_cursor_anchor_compensates_camera_center(self):
        self.assertEqual(
            cursor_anchored_camera_center((10.0, 20.0, 3.0), (4.0, 8.0), (3.0, 6.0)),
            (11.0, 22.0, 3.0),
        )

    def test_cursor_coordinates_can_be_disabled_per_viewer(self):
        viewer = PointCloudViewer()
        self.assertTrue(viewer.cursor_coordinates_enabled)
        self.assertFalse(viewer.cursor_check.isHidden())
        viewer.cursor_coordinate.setVisible(True)
        viewer.set_cursor_coordinates_enabled(False)
        self.assertFalse(viewer.cursor_coordinates_enabled)
        self.assertTrue(viewer.cursor_check.isHidden())
        self.assertTrue(viewer.cursor_coordinate.isHidden())

    def test_loaded_pgm_remains_below_device_visuals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "map.pgm").write_text("P2\n2 2\n255\n0 255\n255 0\n", encoding="ascii")
            yaml_path = root / "map.yaml"
            yaml_path.write_text(
                "image: map.pgm\nresolution: 0.1\norigin: [0, 0, 0]\n"
                "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.2\n",
                encoding="utf-8",
            )
            metadata = PgmMapMetadata(
                "map.pgm", "map.yaml", 0.1, 0, 0, 0, 2, 2, False, 0.65, 0.2
            )
            viewer = PointCloudViewer()
            viewer.load_pgm_layer(MapDefinition("map-1", "Map", pgm=metadata), yaml_path)
            self.assertEqual(viewer._pgm_visual.order, viewer.MAP_LAYER_ORDER)
            self.assertLess(viewer._pgm_visual.order, viewer._marker_visual.order)

    def test_relocalization_picker_refresh_does_not_reset_direction(self):
        viewer = PointCloudViewer()
        viewer.set_relocalization_picker("UGV_001")
        viewer._camera.azimuth = 47.0
        viewer._camera.center = (4.0, 5.0, 0.0)
        viewer.set_relocalization_picker("UGV_001")
        self.assertEqual(viewer._camera.azimuth, 47.0)
        self.assertEqual(tuple(viewer._camera.center), (4.0, 5.0, 0.0))

    def test_grid_drives_sparse_coordinates_and_property_controls(self):
        viewer = PointCloudViewer()
        original = (MAP_VIEWER_SETTINGS.grid_visible, MAP_VIEWER_SETTINGS.grid_spacing)
        try:
            MAP_VIEWER_SETTINGS.grid_visible = True
            MAP_VIEWER_SETTINGS.grid_spacing = 1.0
            viewer.current_map = MapDefinition(
                "relocalization-map", "relocalization-map",
                bounds=MapBounds(-10.0, -8.0, -1.0, 12.0, 9.0, 2.0),
            )
            viewer.set_selected_device_pose(None)
            viewer.set_device_trail([])
            viewer._apply_display_settings()
            viewer.set_relocalization_picker("UGV_001")

            self.assertFalse(hasattr(viewer, "coordinate_check"))
            self.assertFalse(hasattr(viewer, "tick_spacing_input"))
            self.assertEqual(viewer.grid_spacing_label.text(), "分辨率")
            self.assertEqual(viewer.grid_opacity_label.text(), "透明度")
            self.assertTrue(viewer._grid_visual.visible)
            self.assertTrue(viewer._coordinate_visual.visible)
            self.assertGreater(len(viewer._grid_visual.pos), 0)
            self.assertGreater(len(viewer._coordinate_visual.text), 0)
            self.assertEqual(coordinate_tick_spacing(1.0, 22.0), 5.0)

            MAP_VIEWER_SETTINGS.grid_visible = False
            viewer._apply_display_settings()
            self.assertFalse(viewer.grid_spacing_input.isEnabled())
            self.assertFalse(viewer.grid_opacity_input.isEnabled())
            self.assertFalse(viewer.grid_spacing_label.isEnabled())
            self.assertFalse(viewer.grid_opacity_label.isEnabled())
            self.assertFalse(viewer._coordinate_visual.visible)
        finally:
            MAP_VIEWER_SETTINGS.grid_visible, MAP_VIEWER_SETTINGS.grid_spacing = original
            viewer.close()

    def test_fixed_width_map_log_font_always_has_valid_size(self):
        font = fixed_width_font()
        self.assertTrue(font.pointSize() > 0 or font.pixelSize() > 0)

    def test_relocalization_reticle_overlays_map_viewport_and_tracks_resize(self):
        viewer = PointCloudViewer()
        viewer.resize(800, 600)
        viewer.show()
        self.app.processEvents()
        self.assertTrue(viewer._relocalization_reticle.isHidden())
        viewer.set_relocalization_picker("UGV_001")
        self.app.processEvents()
        self.assertFalse(viewer._relocalization_reticle.isHidden())
        self.assertEqual(viewer._relocalization_reticle.size(), viewer._map_overlay.size())
        self.assertGreater(viewer._relocalization_reticle.width(), 0)
        self.assertGreater(viewer._relocalization_reticle.height(), 0)
        image = viewer._map_overlay.grab().toImage()
        color = image.pixelColor(image.width() // 2 - 10, image.height() // 2)
        self.assertGreater(color.red(), 200)
        self.assertGreater(color.red(), color.green() + 40)
        self.assertGreater(color.red(), color.blue() + 40)

        viewer.resize(960, 720)
        self.app.processEvents()
        self.assertEqual(viewer._relocalization_reticle.size(), viewer._map_overlay.size())
        viewer.set_relocalization_picker(None)
        self.assertTrue(viewer._relocalization_reticle.isHidden())
        viewer.close()

    def test_binding_composes_translation_and_full_orientation(self):
        half = math.sqrt(0.5)
        transformed = transform_pose_by_binding(
            PoseTelemetry(1.0, 0.0, 0.0, 0.0, 30.0, 0.0),
            FrameTransform(10.0, 20.0, 0.0, 0.0, 0.0, half, half),
        )
        np.testing.assert_allclose(
            (transformed.x, transformed.y, transformed.z), (10.0, 21.0, 0.0), atol=1e-8,
        )
        np.testing.assert_allclose(
            (transformed.roll, transformed.pitch, transformed.yaw), (0.0, 30.0, 90.0), atol=1e-8,
        )

    def test_relocalization_pose_uses_center_and_screen_up(self):
        viewer = PointCloudViewer()
        viewer._relocalization_picker_enabled = True
        viewer._relocalization_device_id = "UGV_001"
        native = viewer._canvas.native
        native.resize(400, 300)
        viewer._screen_to_map = lambda x, y, _width, _height: (x / 10.0, -y / 10.0)
        viewer._screen_to_plane = lambda x, y: (x / 10.0, -y / 10.0)
        x, y, yaw = viewer.relocalization_pose("UGV_001")
        self.assertAlmostEqual(x, native.width() / 20.0)
        self.assertAlmostEqual(y, -native.height() / 20.0)
        self.assertAlmostEqual(yaw, math.pi / 2.0)

    def test_relocalization_pose_allows_direction_sample_outside_map(self):
        viewer = PointCloudViewer()
        viewer._relocalization_picker_enabled = True
        viewer._relocalization_device_id = "UGV_001"
        viewer._canvas.native.resize(400, 300)
        viewer._screen_to_map = lambda *_args: (12.0, 8.0)
        viewer._screen_to_plane = lambda *_args: (12.0, 18.0)
        self.assertEqual(
            viewer.relocalization_pose("UGV_001"),
            (12.0, 8.0, math.pi / 2.0),
        )
        self.assertIsNone(viewer.relocalization_pose("UGV_003"))

    def test_relocalization_pose_rejects_invalid_center_or_direction(self):
        viewer = PointCloudViewer()
        viewer._relocalization_picker_enabled = True
        viewer._relocalization_device_id = "UGV_001"
        viewer._canvas.native.resize(400, 300)
        viewer._screen_to_map = lambda *_args: None
        viewer._screen_to_plane = lambda *_args: (12.0, 18.0)
        self.assertIsNone(viewer.relocalization_pose("UGV_001"))

        viewer._screen_to_map = lambda *_args: (12.0, 8.0)
        viewer._screen_to_plane = lambda *_args: (12.0, 8.0)
        self.assertIsNone(viewer.relocalization_pose("UGV_001"))
        viewer._screen_to_plane = lambda *_args: (float("nan"), 18.0)
        self.assertIsNone(viewer.relocalization_pose("UGV_001"))

    def test_screen_world_round_trip_respects_real_map_bounds(self):
        viewer = PointCloudViewer()
        viewer.resize(960, 540)
        viewer.current_map = MapDefinition(
            "wide-map", "wide-map",
            bounds=MapBounds(-120.0, 35.0, -2.0, 280.0, 95.0, 8.0),
        )
        viewer.reset_view()
        viewer.set_interaction_mode("pick")
        self.app.processEvents()
        transform = viewer._view.scene.transform
        native = viewer._canvas.native
        for expected in ((-100.0, 40.0), (25.0, 60.0), (250.0, 90.0)):
            projected = np.asarray(transform.map((*expected, 0.0, 1.0)), dtype=np.float64)
            projected = projected[:3] / projected[3]
            actual = viewer._screen_to_map(projected[0], projected[1], native.width(), native.height())
            self.assertIsNotNone(actual)
            np.testing.assert_allclose(actual, expected, atol=1e-4)

    def test_rotated_pgm_center_and_footprint_are_used(self):
        yaw = math.pi / 2
        pgm = PgmMapMetadata(
            "map.pgm", "map.yaml", 0.5, 10.0, 20.0, yaw,
            40, 20, False, 0.65, 0.196,
        )
        viewer = PointCloudViewer()
        viewer.current_map = MapDefinition("grid", "grid", pgm=pgm)
        viewer.layer_mode = "grid"
        viewer.reset_view()
        expected_center = (5.0, 30.0)
        np.testing.assert_allclose(tuple(viewer._camera.center)[:2], expected_center, atol=1e-8)
        self.assertTrue(viewer._contains_map_point(5.0, 30.0))
        self.assertFalse(viewer._contains_map_point(15.0, 30.0))


if __name__ == "__main__":
    unittest.main()
