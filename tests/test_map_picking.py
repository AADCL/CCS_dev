import importlib.util
import math
import unittest

import numpy as np


DEPS = all(importlib.util.find_spec(name) is not None for name in ("numpy", "PySide6", "vispy"))

if DEPS:
    from PySide6.QtWidgets import QApplication

    from ccs_monitor.models import MapBounds, MapDefinition, PgmMapMetadata
    from ccs_monitor.pages.map_page import (
        PointCloudViewer, pick_mode_zoom_distance, unproject_screen_to_plane,
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
