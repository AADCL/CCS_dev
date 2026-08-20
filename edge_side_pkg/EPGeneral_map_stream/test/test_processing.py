import unittest
from types import SimpleNamespace

import numpy as np

from epgeneral_map_stream.processing import (
    PoseBuffer, PoseSample, aggregate_window, preprocess_points, transform_from_pose,
)


class ProcessingTests(unittest.TestCase):
    def test_filter_range_voxel_and_limit(self):
        points = np.asarray([
            [float("nan"), 0, 0], [0.1, 0, 0], [1.01, 0, 0],
            [1.02, 0, 0], [2.0, 0, 0], [200.0, 0, 0],
        ])
        result = preprocess_points(points, 0.3, 100.0, 0.1, 2)
        self.assertEqual(result.dtype, np.dtype("<f4"))
        self.assertEqual(result.shape, (2, 3))
        self.assertTrue(np.isfinite(result).all())

    def test_pose_buffer_selects_nearest_within_tolerance(self):
        buffer = PoseBuffer(3)
        buffer.add(PoseSample(100, {"x": 1}))
        buffer.add(PoseSample(200, {"x": 2}))
        self.assertEqual(buffer.closest(180, 30).transform["x"], 2)
        self.assertIsNone(buffer.closest(300, 30))

    def test_pose_quaternion_is_normalized(self):
        message = SimpleNamespace(
            pose=SimpleNamespace(pose=SimpleNamespace(
                position=SimpleNamespace(x=1, y=2, z=3),
                orientation=SimpleNamespace(x=0, y=0, z=0, w=2),
            ))
        )
        result = transform_from_pose(message, "pose.pose.position", "pose.pose.orientation")
        self.assertEqual(result["qw"], 1.0)

    def test_window_scans_are_reprojected_to_last_sensor_pose(self):
        identity = {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0,
                    "qz": 0.0, "qw": 1.0}
        moved = dict(identity, x=1.0)
        points, pose = aggregate_window([
            (np.asarray([[1.0, 0.0, 0.0]]), identity, 1),
            (np.asarray([[0.0, 0.0, 0.0]]), moved, 2),
        ], identity, 0.01, 10)
        np.testing.assert_allclose(points, [[0.0, 0.0, 0.0]])
        self.assertEqual(pose["x"], 1.0)
