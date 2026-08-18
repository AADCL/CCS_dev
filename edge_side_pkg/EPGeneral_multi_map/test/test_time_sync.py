import unittest

import numpy as np

from epgeneral_multi_map.models import PoseSample
from epgeneral_multi_map.time_sync import PoseBuffer


def sample(stamp, translation=(0, 0, 0), rotation=(0, 0, 0, 1)):
    return PoseSample(stamp, np.asarray(translation), np.asarray(rotation))


class TimeSyncTests(unittest.TestCase):
    def test_bracketing_samples_interpolate_translation_and_shortest_rotation(self):
        buffer = PoseBuffer(10)
        buffer.add(sample(0, (0, 0, 0), (0, 0, 0, 1)))
        buffer.add(sample(2_000_000_000, (2, 0, 0), (0, 0, 1, 0)))
        match = buffer.match(1_000_000_000, 1_100_000_000)
        np.testing.assert_allclose(match.transform[:3, 3], [1, 0, 0], atol=1e-6)
        np.testing.assert_allclose(match.transform[:3, :3] @ [1, 0, 0], [0, 1, 0], atol=1e-6)
        self.assertTrue(match.interpolated)
        self.assertEqual(match.before_stamp_ns, 0)
        self.assertEqual(match.after_stamp_ns, 2_000_000_000)
        self.assertEqual(match.max_error_ns, 1_000_000_000)

    def test_exact_and_nearest_samples_are_not_marked_interpolated(self):
        buffer = PoseBuffer(3)
        buffer.add(sample(10, (1, 0, 0)))
        buffer.add(sample(30, (3, 0, 0)))
        exact = buffer.match(10, 0)
        nearest = buffer.match(25, 6)
        self.assertFalse(exact.interpolated)
        self.assertEqual((exact.before_stamp_ns, exact.after_stamp_ns), (10, 10))
        self.assertFalse(nearest.interpolated)
        self.assertEqual((nearest.before_stamp_ns, nearest.after_stamp_ns), (30, 30))

    def test_out_of_order_pose_is_sorted_and_outside_tolerance_is_rejected(self):
        buffer = PoseBuffer(3)
        buffer.add(sample(30, (3, 0, 0)))
        buffer.add(sample(10, (1, 0, 0)))
        buffer.add(sample(20, (2, 0, 0)))
        self.assertIsNone(buffer.match(100, 5))
        self.assertEqual(buffer.stamps(), (10, 20, 30))

    def test_duplicate_is_rejected_and_capacity_removes_oldest(self):
        buffer = PoseBuffer(2)
        self.assertTrue(buffer.add(sample(20)))
        self.assertFalse(buffer.add(sample(20)))
        self.assertTrue(buffer.add(sample(10)))
        self.assertTrue(buffer.add(sample(30)))
        self.assertEqual(buffer.stamps(), (20, 30))

    def test_antipodal_quaternion_uses_shortest_equivalent_rotation(self):
        buffer = PoseBuffer(2)
        buffer.add(sample(0, rotation=(0, 0, 0, 1)))
        buffer.add(sample(10, rotation=(0, 0, 0, -1)))
        match = buffer.match(5, 5)
        np.testing.assert_allclose(match.transform[:3, :3], np.eye(3), atol=1e-7)

    def test_invalid_pose_and_negative_tolerance_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "quaternion"):
            sample(0, rotation=(0, 0, 0, 0))
        buffer = PoseBuffer(2)
        buffer.add(sample(0))
        with self.assertRaisesRegex(ValueError, "tolerance"):
            buffer.match(0, -1)

    def test_clear_removes_all_samples(self):
        buffer = PoseBuffer(2)
        buffer.add(sample(1))
        buffer.clear()
        self.assertEqual(buffer.stamps(), ())
        self.assertIsNone(buffer.match(1, 0))


if __name__ == "__main__":
    unittest.main()
