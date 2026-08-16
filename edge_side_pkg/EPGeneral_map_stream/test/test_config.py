import os
import tempfile
import unittest

from epgeneral_map_stream.config import ConfigError, load_config


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = os.path.join(os.path.dirname(PACKAGE), "epgeneral_device_config", "config", "device.yaml")


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
        self.assertEqual(config["device_id"], "UAV_001")
        self.assertEqual(config["cloud_message_type"], "sensor_msgs/PointCloud2")
        self.assertEqual(config["control_port"], 14561)
        self.assertAlmostEqual(config["body_from_sensor"]["qw"], 1.0)

    def test_non_pointcloud2_input_is_rejected(self):
        path = self._modified_mapping("sensor_msgs/PointCloud2", "livox_ros_driver/CustomMsg")
        with self.assertRaisesRegex(ConfigError, "PointCloud2"):
            load_config(path, DEVICE)

    def test_zero_extrinsic_quaternion_is_rejected(self):
        path = self._modified_mapping("qw: 1.0", "qw: 0.0")
        with self.assertRaisesRegex(ConfigError, "quaternion"):
            load_config(path, DEVICE)

    def test_decompressed_limit_must_cover_max_points(self):
        path = self._modified_mapping("max_decompressed_bytes: 2400000", "max_decompressed_bytes: 100")
        with self.assertRaisesRegex(ConfigError, "max_frame_points"):
            load_config(path, DEVICE)
