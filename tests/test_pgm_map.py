import tempfile
import unittest
from pathlib import Path

import numpy as np

from ccs_monitor.pgm_map import PgmMapError, PgmMapLoader


def write_map_yaml(path: Path, image_name: str = "source.pgm") -> None:
    path.write_text(
        f"image: {image_name}\n"
        "resolution: 0.05\n"
        "origin: [-1.0, -2.0, 0.25]\n"
        "negate: 0\n"
        "occupied_thresh: 0.65\n"
        "free_thresh: 0.2\n",
        encoding="utf-8",
    )


class PgmMapLoaderTests(unittest.TestCase):
    def test_loads_p2_and_builds_ros_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.pgm").write_text(
                "P2\n# demo\n3 2\n255\n0 127 255\n255 127 0\n", encoding="ascii"
            )
            write_map_yaml(root / "map.yaml")
            data = PgmMapLoader().load_yaml(root / "map.yaml")
            self.assertEqual(data.pixels.shape, (2, 3))
            self.assertEqual(data.metadata.image_width, 3)
            self.assertAlmostEqual(data.metadata.width_m, 0.15)
            self.assertEqual(data.rgba().shape, (2, 3, 4))

    def test_loads_p5_without_skipping_whitespace_valued_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "binary.pgm"
            path.write_bytes(b"P5\n2 2\n255\n" + bytes((10, 32, 100, 255)))
            pixels = PgmMapLoader().load_pgm(path)
            np.testing.assert_array_equal(pixels, [[10, 32], [100, 255]])

    def test_rejects_invalid_yaml_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.pgm").write_text("P2\n1 1\n255\n0\n", encoding="ascii")
            yaml_path = root / "map.yaml"
            write_map_yaml(yaml_path)
            yaml_path.write_text(
                yaml_path.read_text(encoding="utf-8").replace("free_thresh: 0.2", "free_thresh: 0.8"),
                encoding="utf-8",
            )
            with self.assertRaises(PgmMapError):
                PgmMapLoader().load_yaml(yaml_path)


if __name__ == "__main__":
    unittest.main()
