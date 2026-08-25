import json
import os
import tempfile
import unittest

import yaml

from epgeneral_map_stream.config import build_integration_commands, load_config


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOSITORY = os.path.dirname(os.path.dirname(ROOT))
REPOSITORY_PROFILE = os.path.join(REPOSITORY, "edge_side_pkg", "deploy", "scout_mini")
DEPLOYED_PROFILE = "/home/nvidia/ccs_edge_ws/config/scout_mini"
PROFILE = (REPOSITORY_PROFILE if os.path.isfile(os.path.join(
    REPOSITORY_PROFILE, "config", "map_stream.yaml")) else DEPLOYED_PROFILE)


class ScoutBackendTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(
            os.path.join(PROFILE, "config", "map_stream.yaml") if PROFILE == REPOSITORY_PROFILE
            else os.path.join(PROFILE, "map_stream.yaml"),
            os.path.join(PROFILE, "config", "device.yaml") if PROFILE == REPOSITORY_PROFILE
            else os.path.join(PROFILE, "device.yaml"),
        )
        self.values = {
            "map_id": "map-1", "device_id": "UGV_001", "session_id": "session-1",
            "session_dir": "/tmp/session-1", "pcd_path": "/tmp/session-1/map.pcd",
            "pgm_path": "/tmp/session-1/map.pgm", "yaml_path": "/tmp/session-1/map.yaml",
            "map_name": "20260824_213015",
        }

    def test_commands_use_exact_order_without_source_or_rosservice(self):
        commands = build_integration_commands(self.config, self.values)
        start = commands["start_fast_lio"]
        self.assertEqual(start[-6:], [
            "scout_system_bringup", "fastlio_mapping_scout.launch",
            "scout_tf_manager", "tf_manager.launch",
            "scout_pose_adapter", "pose_adapter.launch",
        ])
        rendered = " ".join(" ".join(value) for value in commands.values()
                            if isinstance(value, list) and value and isinstance(value[0], str))
        self.assertNotIn("source", rendered)
        self.assertNotIn("rosservice", rendered)
        self.assertIn("20260824_213015", commands["generate_pgm"])

    def test_profile_frames_and_artifacts_match_scout(self):
        self.assertEqual(self.config["integration_backend"], "scout_finalize")
        self.assertEqual(self.config["cloud_topic"], "/cloud_registered_body")
        self.assertEqual(self.config["pose_topic"], "/fastlio_odom")
        self.assertEqual(self.config["map_frame"], "odom")
        self.assertEqual(self.config["artifact_frame"], "map")

    def test_ground_station_has_device_specific_frames(self):
        ground_config = os.path.join(REPOSITORY, "config", "map_building.json")
        if not os.path.isfile(ground_config):
            self.skipTest("ground station repository is unavailable")
        with open(ground_config,
                  "r", encoding="utf-8") as stream:
            frames = json.load(stream)["device_frames"]["UGV_001"]
        self.assertEqual(frames, {
            "remote_mapping": "odom", "preview_source": "odom",
            "remote_artifact": "map",
        })

    def test_scripts_do_not_source_workspaces(self):
        for name in ("scout_mapping_stack.sh", "scout_finalize_map.sh"):
            with open(os.path.join(ROOT, "scripts", name), "r", encoding="utf-8") as stream:
                script = stream.read()
            self.assertNotIn("source ", script)
            self.assertNotIn("rosservice", script)


if __name__ == "__main__":
    unittest.main()
