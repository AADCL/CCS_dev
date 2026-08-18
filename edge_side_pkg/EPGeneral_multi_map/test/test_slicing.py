import unittest

import numpy as np

from epgeneral_multi_map.models import PoseMatch, SynchronizedFrame
from epgeneral_multi_map.slicing import SliceCollector, SliceError


def frame(stamp, points=1, raw_bytes=12):
    match = PoseMatch(np.eye(4), stamp, stamp, 0, False)
    return SynchronizedFrame(stamp, object(), points, raw_bytes, np.eye(4), match)


def collector(max_frames=50, max_points=1000, max_bytes=10000):
    return SliceCollector(10_000, 5_000, 200, {
        "max_slice_frames": max_frames,
        "max_slice_points": max_points,
        "max_slice_bytes": max_bytes,
    })


class SlicingTests(unittest.TestCase):
    def test_absolute_windows_are_deterministic(self):
        value = collector()
        self.assertEqual(value.slice_id_for(10_000), 0)
        self.assertEqual(value.slice_id_for(14_999), 0)
        self.assertEqual(value.slice_id_for(15_000), 1)
        self.assertEqual(value.window_for(1), (15_000, 20_000))
        with self.assertRaisesRegex(SliceError, "precedes"):
            value.slice_id_for(9_999)

    def test_window_waits_for_lateness_then_drops_expired_frame(self):
        value = collector()
        value.add(frame(14_900))
        self.assertEqual(value.seal_ready(15_199), [])
        sealed = value.seal_ready(15_200)
        self.assertEqual(sealed[0].slice_id, 0)
        self.assertEqual(value.add(frame(14_950)), "late")

    def test_resource_overflow_truncates_only_current_window(self):
        value = collector(max_frames=1)
        self.assertEqual(value.add(frame(10_100)), "accepted")
        self.assertEqual(value.add(frame(10_200)), "truncated")
        self.assertEqual(value.add(frame(10_300)), "truncated")
        self.assertEqual(value.add(frame(15_100)), "accepted")
        batches = value.seal_ready(20_200)
        self.assertTrue(batches[0].truncated)
        self.assertEqual(batches[0].dropped_resource, 2)
        self.assertFalse(batches[1].truncated)

    def test_point_and_byte_limits_are_enforced(self):
        value = collector(max_points=3, max_bytes=30)
        self.assertEqual(value.add(frame(10_100, points=2, raw_bytes=20)), "accepted")
        self.assertEqual(value.add(frame(10_200, points=2, raw_bytes=5)), "truncated")
        other = collector(max_points=10, max_bytes=20)
        self.assertEqual(other.add(frame(10_100, points=1, raw_bytes=21)), "truncated")

    def test_empty_elapsed_window_is_returned_for_status_reporting(self):
        batches = collector().seal_ready(15_200)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].frames, [])
        self.assertEqual((batches[0].start_ns, batches[0].end_ns), (10_000, 15_000))

    def test_only_previous_grace_and_current_windows_are_pending(self):
        value = collector()
        value.add(frame(14_900))
        value.add(frame(15_100))
        self.assertEqual(value.pending_slice_ids(), (0, 1))
        value.seal_ready(15_200)
        self.assertEqual(value.pending_slice_ids(), (1,))

    def test_third_pending_window_is_rejected_without_growth(self):
        value = collector()
        value.add(frame(10_100))
        value.add(frame(15_100))
        with self.assertRaisesRegex(SliceError, "two pending"):
            value.add(frame(20_100))
        self.assertEqual(value.pending_slice_ids(), (0, 1))

    def test_tail_filters_stop_boundary_and_marks_partial_error(self):
        value = collector()
        value.add(frame(10_100))
        value.add(frame(14_999))
        value.add(frame(15_000))
        tail = value.seal_tail(14_500, error_tail=True)
        self.assertEqual([item.stamp_ns for item in tail.frames], [10_100])
        self.assertEqual(tail.end_ns, 14_500)
        self.assertTrue(tail.partial)
        self.assertTrue(tail.error_tail)
        self.assertIsNone(collector().seal_tail(15_000, error_tail=False))

    def test_clear_releases_pending_frames(self):
        value = collector()
        value.add(frame(10_100))
        value.clear()
        self.assertEqual(value.pending_slice_ids(), ())


if __name__ == "__main__":
    unittest.main()
