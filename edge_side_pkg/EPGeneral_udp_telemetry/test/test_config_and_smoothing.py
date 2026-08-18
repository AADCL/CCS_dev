import os
import sys
import tempfile
import time
import unittest


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PACKAGE, "src"))

from epgeneral_udp_telemetry.config import ConfigError, descriptor_hash, load_config
from epgeneral_udp_telemetry.smoothing import TelemetrySampler, average_quaternions


class ConfigTests(unittest.TestCase):
    def test_default_config_and_hash(self):
        root = os.path.dirname(PACKAGE)
        config = load_config(
            os.path.join(PACKAGE, "config", "telemetry.yaml"),
            os.path.join(root, "EPGeneral_device_config", "config", "device.yaml"),
        )
        self.assertTrue(config["device_id"])
        self.assertEqual(config["destination_port"], 14560)
        self.assertEqual(config["descriptor_hash"], descriptor_hash(config["descriptors"]))

    def test_invalid_device_ip_is_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as stream:
            stream.write("schema_version: 1\ndevice:\n  id: TEST\n  ip: invalid\n")
            path = stream.name
        try:
            with self.assertRaises(ConfigError):
                load_config(os.path.join(PACKAGE, "config", "telemetry.yaml"), path)
        finally:
            os.unlink(path)


class SmoothingTests(unittest.TestCase):
    def test_quaternion_antipodes_average_to_same_orientation(self):
        value = average_quaternions([(0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0, -1.0)])
        self.assertAlmostEqual(value[3], 1.0)

    def test_pose_window_average_and_latest_replay(self):
        descriptor = {"name": "pose", "type": "pose", "source": {}}
        sampler = TelemetrySampler(descriptor)
        sampler.add({"x": 1.0, "y": 2.0, "z": 3.0, "quaternion": (0.0, 0.0, 0.0, 1.0)}, 10.0)
        sampler.add({"x": 3.0, "y": 4.0, "z": 5.0, "quaternion": (0.0, 0.0, 0.0, 1.0)}, 10.01)
        first = sampler.snapshot(10.05)
        second = sampler.snapshot(10.10)
        self.assertEqual(first["x"], 2.0)
        self.assertEqual(second["x"], 2.0)
        self.assertAlmostEqual(second["sample_age_seconds"], 0.09)

    def test_pointcloud_contains_metadata_only(self):
        descriptor = {"name": "cloud", "type": "pointcloud_status", "source": {"timeout_seconds": 1.0}}
        sampler = TelemetrySampler(descriptor)
        sampler.touch(1.0)
        sampler.touch(1.1)
        payload = sampler.snapshot(1.2)
        self.assertEqual(payload["status"], "available")
        self.assertIn("estimated_hz", payload)
        self.assertNotIn("points", payload)

    def test_text_status_uses_latest_value_and_reports_staleness(self):
        descriptor = {"name": "mode", "type": "text_status", "source": {"timeout_seconds": 2.0}}
        sampler = TelemetrySampler(descriptor)
        sampler.add({"value": "全局建图"}, 1.0)
        sampler.add({"value": "增量建图"}, 1.2)
        fresh = sampler.snapshot(1.5)
        stale = sampler.snapshot(3.3)
        self.assertEqual(fresh["value"], "增量建图")
        self.assertEqual(fresh["status"], "available")
        self.assertEqual(stale["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
