import json
import tempfile
import unittest
from pathlib import Path

from ccs_monitor.mqtt_config import MqttConfigError, load_mqtt_config


class MqttConfigTests(unittest.TestCase):
    def test_loads_monitoring_config_and_topics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mqtt.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "broker": {"bind_host": "0.0.0.0", "port": 1884},
                "qos": 1,
                "topic_root": "mqtav",
                "subscriber_client_id": "test-ground",
                "monitor": {
                    "heartbeat_check_hz": 1,
                    "warning_timeout_seconds": 2,
                    "error_timeout_seconds": 5,
                    "log_capacity": 500,
                },
            }), encoding="utf-8")
            config = load_mqtt_config(path)
        self.assertEqual(config.port, 1884)
        self.assertEqual(config.topics, (
            "mqtav/+/presence", "mqtav/+/heartbeat", "mqtav/+/status",
        ))

    def test_rejects_inverted_timeouts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mqtt.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "broker": {"bind_host": "127.0.0.1", "port": 1884},
                "qos": 1,
                "topic_root": "mqtav",
                "subscriber_client_id": "test-ground",
                "monitor": {
                    "heartbeat_check_hz": 1,
                    "warning_timeout_seconds": 5,
                    "error_timeout_seconds": 2,
                    "log_capacity": 500,
                },
            }), encoding="utf-8")
            with self.assertRaises(MqttConfigError):
                load_mqtt_config(path)


if __name__ == "__main__":
    unittest.main()
