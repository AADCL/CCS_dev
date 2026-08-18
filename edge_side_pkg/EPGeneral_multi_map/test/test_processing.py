import unittest
from types import SimpleNamespace

import numpy as np

from epgeneral_multi_map.models import PoseMatch, SliceBatch
from epgeneral_multi_map.processing import (
    PassThroughSliceProcessor,
    extract_pointcloud2,
    preprocess_points,
    stamp_to_ns,
    synchronized_frame,
    transform_from_pose,
)


class ProcessingTests(unittest.TestCase):
    def test_existing_range_voxel_and_limit_semantics_are_preserved(self):
        points = np.asarray([
            [np.nan, 0, 0], [0.1, 0, 0], [1.01, 0, 0],
            [1.02, 0, 0], [2.0, 0, 0], [200.0, 0, 0],
        ])
        result = preprocess_points(points, 0.3, 100.0, 0.1, 2)
        np.testing.assert_allclose(result, [[1.01, 0, 0], [2.0, 0, 0]])
        self.assertEqual(result.dtype, np.dtype("<f4"))

    def test_existing_point_limit_uses_uniform_indices_not_head_truncation(self):
        points = np.asarray([[1.1, 0, 0], [2.1, 0, 0], [3.1, 0, 0], [4.1, 0, 0]])
        result = preprocess_points(points, 0.3, 100.0, 0.1, 2)
        np.testing.assert_allclose(result[:, 0], [1.1, 4.1])

    def test_pointcloud2_reader_extracts_only_xyz(self):
        def reader(unused_message, field_names, skip_nans):
            self.assertEqual(field_names, ("x", "y", "z"))
            self.assertFalse(skip_nans)
            return [(1, 2, 3, 99), (4, 5, 6, 100)]

        points = extract_pointcloud2(object(), reader)
        np.testing.assert_array_equal(points, [[1, 2, 3], [4, 5, 6]])
        self.assertEqual(points.dtype, np.float32)

    def test_pose_paths_are_read_and_quaternion_is_normalized(self):
        message = SimpleNamespace(pose=SimpleNamespace(pose=SimpleNamespace(
            position=SimpleNamespace(x=1, y=2, z=3),
            orientation=SimpleNamespace(x=0, y=0, z=0, w=2),
        )))
        result = transform_from_pose(message, "pose.pose.position", "pose.pose.orientation")
        self.assertEqual(result, {
            "x": 1.0, "y": 2.0, "z": 3.0,
            "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
        })

    def test_stamp_and_synchronized_frame_keep_raw_message_until_upload(self):
        stamp = SimpleNamespace(secs=2, nsecs=3)
        self.assertEqual(stamp_to_ns(stamp), 2_000_000_003)
        message = SimpleNamespace(data=b"1234", width=2, height=3)
        match = PoseMatch(np.eye(4), 1, 2, 1, True)
        frame = synchronized_frame(message, 10, match)
        self.assertIs(frame.raw_message, message)
        self.assertEqual(frame.raw_point_count, 6)
        self.assertEqual(frame.raw_bytes, 4)
        np.testing.assert_array_equal(frame.map_from_body, np.eye(4))

    def test_pass_through_processor_preserves_batch_identity(self):
        batch = SliceBatch(0, 0, 5, [])
        self.assertIs(PassThroughSliceProcessor().process(batch), batch)


if __name__ == "__main__":
    unittest.main()
