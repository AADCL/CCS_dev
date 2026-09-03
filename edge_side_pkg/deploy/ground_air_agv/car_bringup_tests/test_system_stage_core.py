#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if not SCRIPTS.is_dir():
    SCRIPTS = ROOT / "car_bringup_scripts"
MODULE = SCRIPTS / "system_stage_core.py"


def load_core():
    if not MODULE.is_file():
        raise AssertionError("system_stage_core.py is missing")
    spec = importlib.util.spec_from_file_location("system_stage_core", str(MODULE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SystemStageCoreTests(unittest.TestCase):
    def test_base_needs_no_map_and_has_no_commands(self):
        core = load_core()
        request = core.normalize_request(core.BASE, "", 0.0)
        self.assertEqual(core.BASE, request.stage)
        self.assertEqual(90.0, request.timeout)
        self.assertEqual((), core.build_stage_commands(request))

    def test_mapping_requires_safe_nonempty_map_id(self):
        core = load_core()
        for value in ("", "../map", "map id", "/tmp/map"):
            with self.subTest(value=value):
                with self.assertRaises(core.StageError):
                    core.normalize_request(core.MAPPING, value, 30.0)

    def test_invalid_stage_is_rejected(self):
        core = load_core()
        with self.assertRaises(core.StageError):
            core.normalize_request(99, "site_a", 30.0)

    def test_mapping_commands_use_existing_launch_entries(self):
        core = load_core()
        request = core.normalize_request(core.MAPPING, "site_20260902", 45.0)
        commands = core.build_stage_commands(request)
        self.assertEqual(
            (
                (
                    "roslaunch",
                    "car_bringup",
                    "manual_mapping_control.launch",
                    "map_id:=site_20260902",
                ),
            ),
            commands,
        )

    def test_relocalization_commands_forward_map_and_timeout(self):
        core = load_core()
        request = core.normalize_request(core.RELOCALIZATION, "site_a", 60.0)
        commands = core.build_stage_commands(request)
        self.assertEqual(
            (
                (
                    "roslaunch",
                    "car_bringup",
                    "start_relocalization.launch",
                    "map_id:=site_a",
                    "service_wait_timeout:=60.0",
                    "relocalize_timeout:=60.0",
                ),
            ),
            commands,
        )

    def test_complete_external_coordinate_pair_is_reused_without_conflict(self):
        core = load_core()
        topology = core.analyze_active_nodes(
            {"/odom_camera_init_broadcaster", "/base_link_body_broadcaster"}
        )
        self.assertEqual((), topology.conflicts)
        self.assertTrue(topology.coordinate_transforms_ready)

    def test_partial_external_coordinate_pair_is_rejected(self):
        core = load_core()
        topology = core.analyze_active_nodes({"/odom_camera_init_broadcaster"})
        self.assertIn("incomplete coordinate transform pair", topology.conflicts)
        self.assertFalse(topology.coordinate_transforms_ready)

    def test_external_stage_owner_remains_a_conflict(self):
        core = load_core()
        topology = core.analyze_active_nodes(
            {
                "/odom_camera_init_broadcaster",
                "/base_link_body_broadcaster",
                "/fast_lio_node",
            }
        )
        self.assertIn("/fast_lio_node", topology.conflicts)


if __name__ == "__main__":
    unittest.main()
