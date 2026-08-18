import copy
import os
import unittest

import yaml

from epgeneral_multi_map.config import ConfigError, load_config, load_payloads


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(PACKAGE, "config", "multi_mapping.yaml")
DEVICE = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "device.yaml"
)


def _yaml(path):
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def valid_mapping_payload():
    return copy.deepcopy(_yaml(CONFIG))


def valid_device_payload():
    return copy.deepcopy(_yaml(DEVICE))


class ConfigTests(unittest.TestCase):
    def test_sample_config_targets_noetic_and_shared_identity(self):
        config = load_config(CONFIG, DEVICE)
        self.assertEqual(config["device_id"], "UAV_001")
        self.assertEqual(config["cloud_message_type"], "sensor_msgs/PointCloud2")
        self.assertEqual(config["pose_message_type"], "nav_msgs/Odometry")
        self.assertEqual(config["control_port"], 14561)
        self.assertEqual(config["data_port"], 14562)
        self.assertEqual(config["default_slice_duration_ns"], 5_000_000_000)
        self.assertEqual(config["late_arrival_ns"], 200_000_000)
        self.assertEqual(config["max_slice_frames"], 50)
        self.assertAlmostEqual(config["body_from_sensor"]["qw"], 1.0)

    def test_invalid_slice_duration_range_is_rejected(self):
        payload = valid_mapping_payload()
        payload["slicing"]["min_duration_seconds"] = 10.0
        payload["slicing"]["max_duration_seconds"] = 1.0
        with self.assertRaisesRegex(ConfigError, "slicing duration"):
            load_payloads(payload, valid_device_payload())

    def test_non_pointcloud2_input_is_rejected(self):
        payload = valid_mapping_payload()
        payload["ros"]["cloud"]["message_type"] = "livox_ros_driver/CustomMsg"
        with self.assertRaisesRegex(ConfigError, "PointCloud2"):
            load_payloads(payload, valid_device_payload())

    def test_pose_type_must_use_package_message_syntax(self):
        payload = valid_mapping_payload()
        payload["ros"]["pose"]["message_type"] = "Odometry"
        with self.assertRaisesRegex(ConfigError, "package/Message"):
            load_payloads(payload, valid_device_payload())

    def test_zero_extrinsic_quaternion_is_rejected(self):
        payload = valid_mapping_payload()
        payload["ros"]["body_from_sensor"].update(qx=0.0, qy=0.0, qz=0.0, qw=0.0)
        with self.assertRaisesRegex(ConfigError, "quaternion"):
            load_payloads(payload, valid_device_payload())

    def test_resource_limits_cover_one_full_frame(self):
        payload = valid_mapping_payload()
        payload["limits"]["max_decompressed_bytes"] = 100
        with self.assertRaisesRegex(ConfigError, "max_frame_points"):
            load_payloads(payload, valid_device_payload())

        payload = valid_mapping_payload()
        payload["limits"]["max_slice_points"] = 100
        with self.assertRaisesRegex(ConfigError, "max_slice_points"):
            load_payloads(payload, valid_device_payload())

    def test_negative_lateness_is_rejected(self):
        payload = valid_mapping_payload()
        payload["slicing"]["late_arrival_seconds"] = -0.1
        with self.assertRaisesRegex(ConfigError, "late_arrival_seconds"):
            load_payloads(payload, valid_device_payload())

    def test_network_ports_must_be_distinct(self):
        payload = valid_mapping_payload()
        payload["network"]["data_port"] = payload["network"]["control_port"]
        with self.assertRaisesRegex(ConfigError, "different"):
            load_payloads(payload, valid_device_payload())

    def test_invalid_shared_device_ip_is_rejected(self):
        device = valid_device_payload()
        device["device"]["ip"] = "not-an-ip"
        with self.assertRaisesRegex(ConfigError, "device.ip"):
            load_payloads(valid_mapping_payload(), device)


if __name__ == "__main__":
    unittest.main()
