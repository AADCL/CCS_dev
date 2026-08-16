import unittest

from mqtav.config import DeviceConfig
from mqtav.state import HealthState, normalize_percentage


class StateTests(unittest.TestCase):
    def test_percentage_normalization_handles_ros_fraction_and_unknown(self):
        self.assertEqual(normalize_percentage(0.42), 42.0)
        self.assertEqual(normalize_percentage(42), 42.0)
        self.assertIsNone(normalize_percentage(-1))
        self.assertIsNone(normalize_percentage(None))

    def test_payload_contains_required_health_fields(self):
        health = HealthState(DeviceConfig("UAV-001", "192.168.20.17"))
        health.update_state(True, False, 3, "AUTO.MISSION")
        health.update_battery(0.765, 15.8, 4.2)
        health.update_mission("executing")
        payload = health.payload("status")

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["device"]["id"], "UAV-001")
        self.assertEqual(payload["health"]["fcu_connected"], True)
        self.assertEqual(payload["health"]["battery"]["percentage"], 76.5)
        self.assertEqual(payload["health"]["mission_status"], "executing")
        self.assertEqual(health.payload("heartbeat")["sequence"], payload["sequence"] + 1)

    def test_missing_mavros_values_remain_unknown(self):
        health = HealthState(DeviceConfig("UAV-001", "192.168.20.17"))
        health.update_state(None, None, None, None)
        payload = health.payload("status")
        self.assertIsNone(payload["health"]["fcu_connected"])
        self.assertIsNone(payload["health"]["armed"])
        self.assertEqual(payload["health"]["flight_mode"], "unknown")
