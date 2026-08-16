import importlib.util
import math
import tempfile
import unittest
from pathlib import Path


DEPS = all(importlib.util.find_spec(name) is not None for name in ("numpy", "PySide6"))

if DEPS:
    from ccs_monitor.models import PgmMapMetadata
    from ccs_monitor.task_map import GridPointValidator


@unittest.skipUnless(DEPS, "task map dependencies are unavailable")
class GridPointValidatorTests(unittest.TestCase):
    def _validator(self, root: Path, yaw: float = 0.0):
        image = root / "map.pgm"
        image.write_bytes(b"P5\n2 2\n255\n" + bytes((255, 0, 205, 127)))
        metadata = PgmMapMetadata(
            "map.pgm", "map.yaml", 1.0, 10.0, 20.0, yaw, 2, 2,
            False, 0.65, 0.196,
        )
        return GridPointValidator(metadata, image)

    def test_y_axis_flip_and_occupancy_classes(self):
        with tempfile.TemporaryDirectory() as temporary:
            validator = self._validator(Path(temporary))
            self.assertEqual(validator.cell(10.5, 20.5), (1, 0))
            self.assertEqual(validator.cell(10.5, 21.5), (0, 0))
            self.assertTrue(validator.is_free(10.5, 21.5))
            self.assertFalse(validator.is_free(11.5, 21.5))
            self.assertFalse(validator.is_free(10.5, 20.5))
            self.assertFalse(validator.is_free(99.0, 99.0))

    def test_rotated_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            validator = self._validator(Path(temporary), math.pi / 2)
            self.assertEqual(validator.cell(9.5, 20.5), (1, 0))
            self.assertEqual(validator.cell(8.5, 20.5), (0, 0))


if __name__ == "__main__":
    unittest.main()
