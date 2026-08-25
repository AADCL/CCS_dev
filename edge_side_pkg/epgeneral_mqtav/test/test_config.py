import tempfile
import unittest
from pathlib import Path

from epgeneral_mqtav.config import ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]
DEVICE_CONFIG = ROOT.parent / "EPGeneral_device_config" / "config" / "device.yaml"


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

    def test_custom_state_freshness_and_battery_mapping(self):
        content = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        content = content.replace('topic: "/mavros/state"', 'topic: "/scout_status"')
        content = content.replace('message_type: "mavros_msgs/State"', 'message_type: "scout_msgs/ScoutStatus"')
        content = content.replace('    mapping: {connected: "connected", armed: "armed", system_status: "system_status", mode: "mode"}', '    connected_on_message: true\n    timeout_seconds: 3.0\n    mapping: {connected: null, armed: null, system_status: "fault_code", mode: "control_mode"}')
        content = content.replace('topic: "/mavros/battery"', 'topic: "/scout_status"')
        content = content.replace('message_type: "sensor_msgs/BatteryState"', 'message_type: "scout_msgs/ScoutStatus"')
        content = content.replace('    mapping: {percentage: "percentage", voltage: "voltage", current: "current"}', '    mapping: {percentage: null, voltage: "battery_voltage", current: null}')
        config = load_config(self.write_config(content), DEVICE_CONFIG)
        self.assertTrue(config.ros.state.connected_on_message)
        self.assertEqual(config.ros.state.timeout_seconds, 3.0)
        self.assertEqual(config.ros.battery.mapping["voltage"], "battery_voltage")
