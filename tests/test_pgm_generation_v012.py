import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

from ccs_monitor.map_building import write_binary_pcd
from ccs_monitor.map_fusion import MapFusionRepository, MapFusionRunner
from ccs_monitor.pgm_map import (
    PcdToPgmGenerator,
    PcdToPgmOptions,
    PgmMapError,
    PgmMapLoader,
)
from ccs_monitor.point_cloud import MapPointCloudLoader
from tests.test_point_cloud import write_ascii_pcd


ROOT = Path(__file__).resolve().parent.parent


def load_example(name: str):
    path = ROOT / "examples" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PcdToPgmTests(unittest.TestCase):
    def test_projection_height_filter_unknown_pixels_and_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pcd"
            write_ascii_pcd(source, [(0, 0, 0.5), (1, 1, 0.5), (2, 2, 5.0)])
            result = PcdToPgmGenerator().generate(
                source,
                root / "map.pgm",
                root / "map.yaml",
                PcdToPgmOptions(
                    resolution=1.0, min_z=0.0, max_z=1.0, padding_m=0.0,
                ),
            )
            self.assertEqual(result.selected_points, 2)
            self.assertEqual(result.occupied_cells, 2)
            pixels = PgmMapLoader().load_pgm(root / "map.pgm")
            np.testing.assert_array_equal(pixels, [[205, 0], [0, 205]])
            payload = yaml.safe_load((root / "map.yaml").read_text(encoding="utf-8"))
            self.assertEqual(payload["image"], "map.pgm")
            self.assertEqual(payload["origin"], [0.0, 0.0, 0.0])
            self.assertEqual(payload["negate"], 0)

    def test_free_cells_minimum_count_and_inflation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pcd"
            write_ascii_pcd(source, [(0, 0, 0), (0, 0, 0), (1, 1, 0)])
            result = PcdToPgmGenerator().generate(
                source,
                root / "map.pgm",
                root / "map.yaml",
                PcdToPgmOptions(
                    resolution=1.0, min_z=0.0, max_z=0.0, padding_m=1.0,
                    min_points_per_cell=2, inflation_radius_m=1.0, empty_cell="free",
                ),
            )
            self.assertEqual(result.occupied_cells, 5)
            pixels = PgmMapLoader().load_pgm(root / "map.pgm")
            self.assertEqual(int(np.sum(pixels == 0)), 5)
            self.assertTrue(np.all(np.isin(pixels, (0, 254))))

    def test_invalid_ranges_empty_selection_and_grid_limit_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pcd"
            write_ascii_pcd(source, [(0, 0, 0), (100, 100, 0)])
            with self.assertRaises(PgmMapError):
                PcdToPgmGenerator().generate(
                    source, root / "a.pgm", root / "a.yaml",
                    PcdToPgmOptions(min_z=2, max_z=1),
                )
            with self.assertRaises(PgmMapError):
                PcdToPgmGenerator().generate(
                    source, root / "b.pgm", root / "b.yaml",
                    PcdToPgmOptions(min_z=1, max_z=2),
                )
            with self.assertRaises(PgmMapError):
                PcdToPgmGenerator(max_grid_cells=4).generate(
                    source, root / "c.pgm", root / "c.yaml",
                    PcdToPgmOptions(resolution=1, min_z=0, max_z=0, padding_m=0),
                )

    def test_numpy_ransac_plugin_aligns_a_secondary_cloud(self):
        plugin = load_example("map_fusion_ransac.py")
        rng = np.random.default_rng(7)
        primary = rng.uniform((-2, -1, -0.5), (2, 1, 0.5), size=(80, 3))
        secondary = primary + np.asarray((0.12, -0.08, 0.03))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, output = root / "a.pcd", root / "b.pcd", root / "out.pcd"
            write_binary_pcd(first, primary.astype(np.float32))
            write_binary_pcd(second, secondary.astype(np.float32))
            result = plugin.fuse_maps(
                [str(first), str(second)],
                "map",
                [
                    {"source_id": "a", "is_primary": True,
                     "translation_m": [0, 0, 0], "rotation_rpy_deg": [0, 0, 0]},
                    {"source_id": "b", "is_primary": False,
                     "translation_m": [0, 0, 0], "rotation_rpy_deg": [0, 0, 0]},
                ],
                str(output),
                {
                    "voxel_size_m": 0.02, "max_sample_points": 1000,
                    "iterations": 200, "inlier_distance_m": 0.2,
                    "min_inlier_ratio": 0.5, "random_seed": 11,
                },
            )
            self.assertGreater(result["point_count"], 0)
            loaded = MapPointCloudLoader().load(output)
            self.assertLess(loaded.bounds.width, 4.2)
            self.assertIn("RANSAC", result["message"])

    def test_ransac_plugin_passes_repository_import_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapFusionRepository(root / "registry.json", root / "assets")
            imported = repository.import_algorithm(
                ROOT / "examples" / "map_fusion_ransac.py",
                MapFusionRunner(timeout_seconds=30),
            )
            self.assertEqual(imported.algorithm_id, "numpy_ransac_registration")


@unittest.skipUnless(importlib.util.find_spec("open3d") is not None, "open3d is not installed")
class Open3dPluginTests(unittest.TestCase):
    def test_open3d_plugin_imports_and_fuses(self):
        plugin = load_example("map_fusion_open3d_icp.py")
        rng = np.random.default_rng(9)
        primary = rng.normal(size=(100, 3))
        secondary = primary + np.asarray((0.03, -0.02, 0.01))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, output = root / "a.pcd", root / "b.pcd", root / "out.pcd"
            write_binary_pcd(first, primary.astype(np.float32))
            write_binary_pcd(second, secondary.astype(np.float32))
            result = plugin.fuse_maps(
                [str(first), str(second)], "map",
                [
                    {"source_id": "a", "is_primary": True,
                     "translation_m": [0, 0, 0], "rotation_rpy_deg": [0, 0, 0]},
                    {"source_id": "b", "is_primary": False,
                     "translation_m": [0, 0, 0], "rotation_rpy_deg": [0, 0, 0]},
                ], str(output), plugin.DEFAULT_OPTIONS,
            )
            self.assertGreater(result["point_count"], 0)
            self.assertIn("ICP", result["message"])

    def test_open3d_plugin_passes_repository_import_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapFusionRepository(root / "registry.json", root / "assets")
            imported = repository.import_algorithm(
                ROOT / "examples" / "map_fusion_open3d_icp.py",
                MapFusionRunner(timeout_seconds=60),
            )
            self.assertEqual(imported.algorithm_id, "open3d_point_to_point_icp")


if __name__ == "__main__":
    unittest.main()
