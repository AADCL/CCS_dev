import os
import unittest

from epgeneral_map_stream.config import build_integration_commands, load_config


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOSITORY = os.path.dirname(os.path.dirname(PACKAGE))
REPOSITORY_PROFILE = os.path.join(
    REPOSITORY, "edge_side_pkg", "deploy", "ground_air_agv", "config")
DEPLOYED_PROFILE = "/home/bitcq/ccs_edge_ws/config/ground_air_agv"
PROFILE = (REPOSITORY_PROFILE if os.path.isfile(os.path.join(
    REPOSITORY_PROFILE, "map_stream.yaml")) else DEPLOYED_PROFILE)


class GroundAirBackendTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(
            os.path.join(PROFILE, "map_stream.yaml"),
            os.path.join(PROFILE, "device.yaml"),
        )
        self.values = {
            "map_id": "map-1", "device_id": "AGV_001",
            "session_id": "a" * 32,
            "session_dir": "/tmp/ground-air-session",
            "pcd_path": "/tmp/ground-air-session/map.pcd",
            "pgm_path": "/tmp/ground-air-session/map.pgm",
            "yaml_path": "/tmp/ground-air-session/map.yaml",
            "map_name": "20260901_190000",
        }

    def test_profile_uses_ground_air_service_and_actual_frames(self):
        self.assertEqual(self.config["integration_backend"], "ground_air_service")
        self.assertEqual(self.config["device_id"], "AGV_001")
        self.assertEqual(self.config["cloud_topic"], "/cloud_registered")
        self.assertEqual(self.config["pose_topic"], "/Odometry")
        self.assertEqual(self.config["map_frame"], "camera_init")
        self.assertEqual(self.config["artifact_frame"], "map")

    def test_commands_use_control_launch_and_original_save_launch(self):
        commands = build_integration_commands(self.config, self.values)
        self.assertTrue(commands["start_fast_lio"][0].endswith(
            "ground_air_mapping_stack.sh"))
        self.assertIn("manual_mapping_control.launch", commands["checks"][0])
        self.assertNotIn("manual_mapping_control.launch", commands["start_fast_lio"])
        self.assertIn("a" * 32, commands["start_fast_lio"])
        self.assertNotIn(
            "mapping_coordinate_transforms.launch", commands["start_fast_lio"])
        self.assertEqual(len(commands["checks"]), 2)
        self.assertIn("20260901_190000", commands["start_fast_lio"])
        self.assertIn("a" * 32, commands["stop_fast_lio"])
        self.assertIn("20260901_190000", commands["abort_fast_lio"])
        self.assertTrue(commands["save_map"][0].endswith(
            "ground_air_save_mapping.sh"))
        self.assertEqual(commands["save_map"][2:4], [
            "car_bringup", "save_mapping.launch"])
        self.assertIn("/ground_air_map_recorder", " ".join(commands["start_fast_lio"]))
        self.assertEqual(
            commands["ground_air_map_directory"],
            os.path.abspath(
                "/home/bitcq/catkin_ws/maps/20260901_190000"))

    def test_new_scripts_do_not_kill_shared_ros_nodes(self):
        for name in ("ground_air_mapping_stack.sh", "ground_air_save_mapping.sh"):
            with open(os.path.join(PACKAGE, "scripts", name),
                      "r", encoding="utf-8") as stream:
                text = stream.read()
            self.assertNotIn("rosnode kill", text)
        with open(os.path.join(PACKAGE, "scripts", "ground_air_mapping_stack.sh"),
                  "r", encoding="utf-8") as stream:
            mapping_text = stream.read()
        self.assertIn("ground_air_stage_client.py", mapping_text)
        self.assertNotIn("kill -", mapping_text)
        self.assertNotIn("mapping_coordinate_transforms.launch", mapping_text)
        with open(os.path.join(PACKAGE, "scripts", "ground_air_save_mapping.sh"),
                  "r", encoding="utf-8") as stream:
            text = stream.read()
        self.assertIn('roslaunch "${PACKAGE_NAME}" "${LAUNCH_FILE}"', text)
        self.assertIn("saved artifact is missing or empty", text)


if __name__ == "__main__":
    unittest.main()
