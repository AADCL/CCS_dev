import tempfile
import unittest
from pathlib import Path

import numpy as np

from ccs_monitor.point_cloud import MapPointCloudLoader, PointCloudError


def write_ascii_pcd(path: Path, rows: list[tuple[float, float, float]]) -> None:
    data = "\n".join(f"{x} {y} {z}" for x, y, z in rows)
    path.write_text(
        "VERSION .7\n"
        "FIELDS x y z\n"
        "SIZE 4 4 4\n"
        "TYPE F F F\n"
        "COUNT 1 1 1\n"
        f"WIDTH {len(rows)}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {len(rows)}\n"
        "DATA ascii\n"
        f"{data}\n",
        encoding="ascii",
    )


class PointCloudLoaderTests(unittest.TestCase):
    def test_ascii_bounds_and_deterministic_sampling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pcd"
            rows = [(float(index), float(index * 2), float(-index)) for index in range(10)]
            write_ascii_pcd(path, rows)
            data = MapPointCloudLoader(max_render_points=4).load(path, sample_for_render=True)
            self.assertEqual(data.point_count, 10)
            self.assertEqual(data.points.shape, (4, 3))
            self.assertEqual(data.bounds.min_x, 0.0)
            self.assertEqual(data.bounds.max_y, 18.0)
            np.testing.assert_array_equal(data.points[:, 0], [0.0, 3.0, 6.0, 9.0])

    def test_empty_and_non_finite_clouds_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.pcd"
            write_ascii_pcd(path, [])
            with self.assertRaises(PointCloudError):
                MapPointCloudLoader().load(path)
            write_ascii_pcd(path, [(float("nan"), 0.0, 0.0)])
            with self.assertRaises(PointCloudError):
                MapPointCloudLoader().load(path)


if __name__ == "__main__":
    unittest.main()
