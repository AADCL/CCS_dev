import tempfile
import unittest
from pathlib import Path

from mqtav.config import ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]
DEVICE_CONFIG = ROOT.parent / "epgeneral_device_config" / "config" / "device.yaml"


class ConfigTests(unittest.TestCase):
    def write_config(self, content):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.yaml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_sample_configuration_loads_and_expands_topics(self):
        config = load_config(ROOT / "config" / "config.yaml", DEVICE_CONFIG)
        self.assertEqual(config.client_id, "mqtav-UAV_001")
        self.assertEqual(config.topic("status"), "mqtav/UAV_001/status")
        self.assertFalse(config.ros.mission.enabled)

    def test_invalid_ground_station_ip_is_rejected(self):
        content = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "mqtt.ground_station_ip"):
            load_config(self.write_config(content.replace("192.168.20.10", "not-an-ip")), DEVICE_CONFIG)

    def test_enabled_mission_requires_field_path(self):
        content = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        content = content.replace("enabled: false", "enabled: true").replace('    field_path: "data"\n', "")
        with self.assertRaisesRegex(ConfigError, "ros.mission.field_path"):
            load_config(self.write_config(content), DEVICE_CONFIG)

    def test_wildcard_topic_is_rejected(self):
        content = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "wildcards"):
            load_config(self.write_config(content.replace("mqtav/{device_id}/status", "mqtav/#")), DEVICE_CONFIG)

    def test_invalid_shared_device_schema_is_rejected(self):
        device_path = self.write_config('schema_version: 2\ndevice:\n  id: "UAV_001"\n  ip: "192.168.151.250"\n')
        with self.assertRaisesRegex(ConfigError, "schema_version"):
            load_config(ROOT / "config" / "config.yaml", device_path)
