import os
import tempfile
import unittest

import yaml

from epgeneral_map_stream.config import ConfigError, build_integration_commands, load_config

try:
    from .test_paths import device_config_path
except ImportError:
    from test_paths import device_config_path


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = device_config_path(PACKAGE)


class ConfigTests(unittest.TestCase):
    def _modified_mapping(self, old, new):
        with open(MAPPING, "r", encoding="utf-8") as stream:
            content = stream.read().replace(old, new)
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".yaml", delete=False)
        temporary.write(content)
        temporary.close()
        self.addCleanup(lambda: os.path.exists(temporary.name) and os.unlink(temporary.name))
        return temporary.name

    def test_sample_config_loads_shared_identity(self):
        config = load_config(MAPPING, DEVICE)
        with open(DEVICE, "r", encoding="utf-8") as stream:
            expected_device = yaml.safe_load(stream)["device"]
        self.assertEqual(config["device_id"], expected_device["id"])
        self.assertEqual(config["device_ip"], expected_device["ip"])
        self.assertEqual(config["protocol_id"], "ccs-map-stream-v2")
        self.assertEqual(config["schema_version"], 3)
        self.assertEqual(config["capability_version"], "0.6.0")
        self.assertEqual(config["input_cloud_topic"], "/livox/lidar")
        self.assertEqual(config["input_imu_topic"], "/livox/imu")
        self.assertEqual(config["input_cloud_message_type"], "livox_ros_driver2/CustomMsg")
        self.assertEqual(config["cloud_topic"], "/lio/cloud_registered_body")
        self.assertEqual(config["pose_topic"], "/lio/odometry")
        self.assertEqual(config["cloud_message_type"], "sensor_msgs/PointCloud2")
        self.assertEqual(config["control_port"], 14561)
        self.assertEqual(config["http_port"], 14600)
        self.assertAlmostEqual(config["sample_window_seconds"], 1.0)
        self.assertAlmostEqual(config["body_from_sensor"]["qw"], 1.0)

    def test_unsupported_lidar_input_is_rejected(self):
        path = self._modified_mapping("livox_ros_driver2/CustomMsg", "std_msgs/String")
        with self.assertRaisesRegex(ConfigError, "CustomMsg"):
            load_config(path, DEVICE)

    def test_zero_extrinsic_quaternion_is_rejected(self):
        path = self._modified_mapping("qw: 1.0", "qw: 0.0")
        with self.assertRaisesRegex(ConfigError, "quaternion"):
            load_config(path, DEVICE)

    def test_decompressed_limit_must_cover_max_points(self):
        path = self._modified_mapping("max_decompressed_bytes: 2400000", "max_decompressed_bytes: 100")
        with self.assertRaisesRegex(ConfigError, "max_frame_points"):
            load_config(path, DEVICE)

    def test_command_template_rejects_shell_style_unknown_fields(self):
        path = self._modified_mapping("pid_path: \"{session_dir}/fast_lio.pid\"",
                                      "pid_path: \"{unknown}/fast_lio.pid\"")
        with self.assertRaisesRegex(ConfigError, "unsupported template"):
            load_config(path, DEVICE)

    def test_integration_commands_are_argument_arrays(self):
        config = load_config(MAPPING, DEVICE)
        values = {
            "map_id": "map-1", "device_id": config["device_id"],
            "session_id": "a" * 32, "session_dir": os.path.join("tmp", "session"),
            "pcd_path": os.path.join("tmp", "session", "map.pcd"),
            "pgm_path": os.path.join("tmp", "session", "map.pgm"),
            "yaml_path": os.path.join("tmp", "session", "map.yaml"),
        }
        commands = build_integration_commands(config, values)
        self.assertIn("--check", commands["check_fast_lio"])
        self.assertEqual(commands["start_fast_lio"][4],
                         os.path.abspath(config["fast_lio_pid_template"].format(**values)))
        self.assertEqual(commands["start_fast_lio"][5],
                         os.path.abspath(config["fast_lio_log_template"].format(**values)))
        self.assertEqual(len(commands["start_fast_lio"]), 7)
        self.assertEqual(commands["start_fast_lio"][6], config["generated_pcd_path"])
        self.assertEqual(commands["stop_fast_lio"][3], config["generated_pcd_path"])
        self.assertEqual(commands["stop_fast_lio"][4], values["pcd_path"])
        self.assertEqual(commands["check_pgm"][-1], config["source_pcd_path"])
        self.assertIn(config["source_yaml_path"], commands["generate_pgm"])
        self.assertIn("config:=/home/nvidia/go2_mid360_nav/catkin_ws/src/go2_map_tools/config/corridor_nav.yaml",
                      commands["generate_pgm"])
