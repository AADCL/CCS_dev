import importlib.util
import inspect
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ccs_monitor.map_building import write_binary_pcd
from ccs_monitor.map_fusion import MapFusionRepository, MapFusionRunner
from ccs_monitor.point_cloud import MapPointCloudLoader


ROOT = Path(__file__).resolve().parent
PLUGIN_PATH = ROOT / "map_fusion_online_incremental_icp.py"
CONFIG_PATH = ROOT / "map_fusion_online_incremental_icp.json"
README_PATH = ROOT / "README.md"
OPEN3D_AVAILABLE = importlib.util.find_spec("open3d") is not None


def load_plugin():
    if not PLUGIN_PATH.is_file():
        raise AssertionError("plugin file has not been implemented")
    spec = importlib.util.spec_from_file_location("map_fusion_online_incremental_icp", PLUGIN_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("plugin module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def transform(source_id="primary", is_primary=True, translation=(0, 0, 0), rotation=(0, 0, 0)):
    return {
        "source_id": source_id,
        "is_primary": is_primary,
        "translation_m": list(translation),
        "rotation_rpy_deg": list(rotation),
    }


class PluginContractTests(unittest.TestCase):
    def test_plugin_metadata_and_function_signature_match_api_v1(self):
        plugin = load_plugin()
        self.assertEqual(plugin.PLUGIN_API_VERSION, 1)
        self.assertEqual(plugin.ALGORITHM_ID, "open3d_online_incremental_icp")
        self.assertEqual(plugin.DISPLAY_NAME, "Open3D 在线增量多尺度 ICP")
        self.assertEqual(plugin.VERSION, "0.1.0")
        self.assertEqual(
            list(inspect.signature(plugin.fuse_maps).parameters),
            [
                "pcd_files", "primary_frame", "transforms_to_primary",
                "output_pcd", "options",
            ],
        )

    def test_json_template_exactly_matches_literal_defaults(self):
        plugin = load_plugin()
        self.assertTrue(CONFIG_PATH.is_file())
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload, plugin.DEFAULT_OPTIONS)


class OptionValidationTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()

    def test_missing_values_use_defaults_and_override_does_not_mutate_inputs(self):
        supplied = {"min_fitness": 0.5, "max_iterations": [30, 20, 10]}
        snapshot = json.loads(json.dumps(supplied))
        parsed = self.plugin._validated_options(supplied)
        self.assertEqual(supplied, snapshot)
        self.assertEqual(parsed["min_fitness"], 0.5)
        self.assertEqual(parsed["max_iterations"], [30, 20, 10])
        self.assertEqual(parsed["voxel_sizes_m"], [0.4, 0.2, 0.1])
        self.assertIsNot(parsed, self.plugin.DEFAULT_OPTIONS)

    def test_unknown_option_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知参数"):
            self.plugin._validated_options({"voxl_size": 0.1})

    def test_scale_arrays_must_have_matching_lengths_and_descend(self):
        invalid = (
            {"voxel_sizes_m": [0.4, 0.2]},
            {"voxel_sizes_m": [0.2, 0.4, 0.1]},
            {"max_correspondence_distances_m": [0.6, 0.3]},
            {"max_iterations": [60, 40]},
            {"voxel_sizes_m": [0.4] * 6,
             "max_correspondence_distances_m": [0.6] * 6,
             "max_iterations": [10] * 6},
        )
        for options in invalid:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.plugin._validated_options(options)

    def test_boolean_nonfinite_and_out_of_range_values_are_rejected(self):
        invalid = (
            {"normal_max_nn": True},
            {"relative_fitness": math.nan},
            {"relative_rmse": 0.0},
            {"normal_radius_multiplier": 1.0},
            {"min_fitness": 1.1},
            {"max_inlier_rmse_m": -0.1},
            {"max_output_points": 0},
            {"max_iterations": [60, False, 30]},
        )
        for options in invalid:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.plugin._validated_options(options)


class InputAndSingleMapTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()

    def call_fuse(self, *args):
        try:
            return self.plugin.fuse_maps(*args)
        except NotImplementedError as exc:
            self.fail("fuse_maps is not implemented: %s" % exc)

    def test_empty_or_mismatched_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.pcd"
            with self.assertRaisesRegex(ValueError, "PCD 与外参数量不一致"):
                self.call_fuse([], "map", [], output, {})
            with self.assertRaisesRegex(ValueError, "PCD 与外参数量不一致"):
                self.call_fuse(["one.pcd"], "map", [], output, {})

    def test_exactly_one_unit_primary_transform_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pcd"
            output = root / "output.pcd"
            write_binary_pcd(source, np.asarray([(0, 0, 0), (1, 2, 3)], dtype=np.float32))
            invalid = (
                [transform(is_primary=False)],
                [transform("a"), transform("b")],
                [transform(translation=(0.01, 0, 0))],
                [transform(rotation=(0, 0, 0.1))],
            )
            files = ([source], [source, source], [source], [source])
            for pcd_files, transforms in zip(files, invalid):
                with self.subTest(transforms=transforms):
                    with self.assertRaisesRegex(ValueError, "单位变换的主坐标系"):
                        self.call_fuse(pcd_files, "map", transforms, output, {})

    def test_single_primary_map_writes_valid_output_without_open3d(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pcd"
            output = root / "output.pcd"
            write_binary_pcd(source, np.asarray([(0, 0, 0), (1, 2, 3)], dtype=np.float32))
            result = self.call_fuse(
                [source], "map", [transform()], output, {"output_voxel_size_m": 0.05}
            )
            cloud = MapPointCloudLoader().load(output)
            self.assertEqual(cloud.point_count, 2)
            self.assertEqual(result["point_count"], 2)
            self.assertEqual(result["source_count"], 1)
            self.assertEqual(result["registered_source_count"], 0)
            self.assertEqual(result["primary_frame"], "map")
            self.assertEqual(result["registrations"], [])

    def test_failed_output_limit_keeps_existing_target_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pcd"
            output = root / "output.pcd"
            write_binary_pcd(source, np.asarray([(0, 0, 0), (1, 2, 3)], dtype=np.float32))
            output.write_bytes(b"existing-map")
            with self.assertRaisesRegex(ValueError, "max_output_points"):
                self.call_fuse(
                    [source], "map", [transform()], output,
                    {"output_voxel_size_m": 0.05, "max_output_points": 1},
                )
            self.assertEqual(output.read_bytes(), b"existing-map")
            self.assertEqual(list(root.glob("*.tmp.pcd")), [])

    def test_existing_repository_import_validation_accepts_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapFusionRepository(root / "algorithms.json", root / "assets")
            try:
                imported = repository.import_algorithm(
                    PLUGIN_PATH, MapFusionRunner(timeout_seconds=10)
                )
            except Exception as exc:
                self.fail("existing repository rejected the plugin: %s" % exc)
            self.assertEqual(imported.algorithm_id, "open3d_online_incremental_icp")
            self.assertTrue(Path(imported.script_path).is_file())

class RegistrationContractTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()

    def test_missing_open3d_has_actionable_error(self):
        self.assertTrue(
            hasattr(self.plugin, "_import_open3d"),
            "lazy Open3D import helper is not implemented",
        )
        real_import = __import__

        def reject_open3d(name, *args, **kwargs):
            if name == "open3d":
                raise ImportError("test missing dependency")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_open3d):
            with self.assertRaisesRegex(RuntimeError, "open3d>=0.18"):
                self.plugin._import_open3d()

    def test_rotation_angle_reports_rigid_transform_magnitude(self):
        self.assertTrue(
            hasattr(self.plugin, "_rotation_angle_deg"),
            "rotation metric helper is not implemented",
        )
        angle = math.radians(5.0)
        transform_matrix = np.eye(4)
        transform_matrix[:3, :3] = (
            (math.cos(angle), -math.sin(angle), 0.0),
            (math.sin(angle), math.cos(angle), 0.0),
            (0.0, 0.0, 1.0),
        )
        self.assertAlmostEqual(
            self.plugin._rotation_angle_deg(transform_matrix), 5.0, places=6
        )


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_install_import_parameters_and_runtime_boundary(self):
        self.assertTrue(README_PATH.is_file(), "README has not been implemented")
        text = README_PATH.read_text(encoding="utf-8")
        required = (
            "open3d>=0.18,<1",
            "map_fusion_online_incremental_icp.py",
            "map_fusion_online_incremental_icp.json",
            "主坐标系 <- 源坐标系",
            "JSON 参数",
            "fitness",
            "inlier RMSE",
            "不是点云切片实时回调",
            "python -m unittest discover -s examples/0818",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


@unittest.skipUnless(OPEN3D_AVAILABLE, "open3d is not installed on this machine")
class Open3DRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()
        grid_a = np.linspace(0.0, 1.4, 24)
        grid_b = np.linspace(0.0, 0.9, 19)
        a, b = np.meshgrid(grid_a, grid_b)
        floor = np.column_stack((a.ravel(), b.ravel(), np.zeros(a.size)))
        wall_x = np.column_stack((np.zeros(a.size), a.ravel(), b.ravel()))
        wall_y = np.column_stack((a.ravel(), np.zeros(a.size), b.ravel()))
        self.primary_points = np.asarray(
            np.concatenate((floor, wall_x, wall_y), axis=0), dtype=np.float32
        )
        self.options = {
            "voxel_sizes_m": [0.12, 0.06, 0.03],
            "max_correspondence_distances_m": [0.25, 0.12, 0.06],
            "max_iterations": [80, 60, 40],
            "min_registration_points": 20,
            "min_fitness": 0.5,
            "max_inlier_rmse_m": 0.05,
            "max_residual_translation_m": 0.2,
            "max_residual_rotation_deg": 5.0,
            "output_voxel_size_m": 0.03,
        }

    @staticmethod
    def source_for_residual(target, translation, rotation_deg):
        angle = math.radians(rotation_deg)
        rotation = np.asarray((
            (math.cos(angle), -math.sin(angle), 0.0),
            (math.sin(angle), math.cos(angle), 0.0),
            (0.0, 0.0, 1.0),
        ))
        return (np.asarray(target) - np.asarray(translation)) @ rotation

    def write_inputs(self, root, secondary_points):
        primary = root / "primary.pcd"
        secondary = root / "secondary.pcd"
        write_binary_pcd(primary, self.primary_points)
        write_binary_pcd(secondary, np.asarray(secondary_points, dtype=np.float32))
        return primary, secondary

    def test_small_residual_converges_and_returns_quality_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secondary_points = self.source_for_residual(
                self.primary_points, (0.04, -0.03, 0.02), 2.0
            )
            primary, secondary = self.write_inputs(root, secondary_points)
            output = root / "output.pcd"
            result = self.plugin.fuse_maps(
                [primary, secondary], "map",
                [transform(), transform("secondary", False)],
                output, self.options,
            )
            self.assertTrue(output.is_file())
            self.assertEqual(result["registered_source_count"], 1)
            self.assertEqual(len(result["registrations"]), 1)
            metrics = result["registrations"][0]
            self.assertEqual(metrics["source_index"], 1)
            self.assertGreaterEqual(metrics["fitness"], self.options["min_fitness"])
            self.assertLessEqual(metrics["inlier_rmse_m"], self.options["max_inlier_rmse_m"])
            self.assertLessEqual(metrics["residual_translation_m"], 0.08)
            self.assertLessEqual(metrics["residual_rotation_deg"], 3.0)

    def test_low_overlap_fails_without_replacing_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary, secondary = self.write_inputs(root, self.primary_points + 5.0)
            output = root / "output.pcd"
            output.write_bytes(b"existing-map")
            with self.assertRaisesRegex(ValueError, "fitness"):
                self.plugin.fuse_maps(
                    [primary, secondary], "map",
                    [transform(), transform("secondary", False)],
                    output, self.options,
                )
            self.assertEqual(output.read_bytes(), b"existing-map")
            self.assertEqual(list(root.glob("*.tmp.pcd")), [])

    def test_residual_translation_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secondary_points = self.source_for_residual(
                self.primary_points, (0.04, -0.03, 0.02), 2.0
            )
            primary, secondary = self.write_inputs(root, secondary_points)
            options = dict(self.options, max_residual_translation_m=0.01)
            with self.assertRaisesRegex(ValueError, "残差平移"):
                self.plugin.fuse_maps(
                    [primary, secondary], "map",
                    [transform(), transform("secondary", False)],
                    root / "output.pcd", options,
                )

    def test_multiple_secondary_maps_return_ordered_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary.pcd"
            first = root / "first.pcd"
            second = root / "second.pcd"
            write_binary_pcd(primary, self.primary_points)
            write_binary_pcd(first, self.source_for_residual(
                self.primary_points, (0.03, -0.02, 0.01), 1.5
            ).astype(np.float32))
            write_binary_pcd(second, self.source_for_residual(
                self.primary_points, (-0.02, 0.03, -0.01), -1.0
            ).astype(np.float32))
            result = self.plugin.fuse_maps(
                [primary, first, second], "map",
                [transform(), transform("first", False), transform("second", False)],
                root / "output.pcd", self.options,
            )
            self.assertEqual(result["registered_source_count"], 2)
            self.assertEqual(
                [item["source_index"] for item in result["registrations"]], [1, 2]
            )
            self.assertEqual(
                [item["source_id"] for item in result["registrations"]],
                ["first", "second"],
            )


if __name__ == "__main__":
    unittest.main()
