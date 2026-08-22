import json
import unittest

from ccs_monitor.mqtt_protocol import (
    MqttHeartbeatEvent,
    MqttMessageParser,
    MqttPresenceEvent,
    MqttProtocolError,
    MqttStatusEvent,
)


STATUS = {
    "schema_version": "1.0",
    "message_type": "status",
    "timestamp": "2026-07-31T09:30:00.000Z",
    "sequence": 42,
    "session_id": "boot-a",
    "device": {"id": "UAV-001", "ip": "192.168.20.17"},
    "health": {
        "fcu_connected": True,
        "armed": False,
        "system_status": 3,
        "flight_mode": "AUTO.MISSION",
        "battery": {"percentage": 76.5, "voltage": 15.8, "current": 4.2},
        "mission_status": "active",
    },
}


class MqttProtocolTests(unittest.TestCase):
    def setUp(self):
        self.parser = MqttMessageParser()

    def test_parses_mqtav_status_envelope(self):
        event = self.parser.parse("mqtav/UAV-001/status", json.dumps(STATUS))
        self.assertIsInstance(event, MqttStatusEvent)
        self.assertEqual(event.flight_mode, "AUTO.MISSION")
        self.assertEqual(event.battery_percentage, 76.5)
        self.assertEqual(event.system_status, 3)
        self.assertEqual(event.session_id, "boot-a")

    def test_parses_presence_and_heartbeat(self):
        common = {
            "schema_version": "1.0",
            "timestamp": "2026-07-31T09:30:00Z",
            "device": {"id": "UAV-001", "ip": "192.168.20.17"},
            "session_id": "boot-a",
        }
        presence = self.parser.parse(
            "mqtav/UAV-001/presence",
            json.dumps({**common, "message_type": "presence", "status": "online"}),
        )
        heartbeat = self.parser.parse(
            "mqtav/UAV-001/heartbeat",
            json.dumps({**common, "message_type": "heartbeat", "sequence": 9}),
        )
        self.assertIsInstance(presence, MqttPresenceEvent)
        self.assertIsInstance(heartbeat, MqttHeartbeatEvent)
        self.assertEqual(presence.session_id, "boot-a")

    def test_rejects_malformed_mismatched_and_invalid_values(self):
        cases = [
            ("mqtav/UAV-001/status", b"{bad"),
            ("mqtav/UAV-002/status", json.dumps(STATUS)),
            ("mqtav/UAV-001/heartbeat", json.dumps(STATUS)),
            ("mqtav/UAV-001/status", json.dumps({**STATUS, "schema_version": "2.0"})),
        ]
        invalid_battery = json.loads(json.dumps(STATUS))
        invalid_battery["health"]["battery"]["percentage"] = 101
        cases.append(("mqtav/UAV-001/status", json.dumps(invalid_battery)))
        invalid_mode = json.loads(json.dumps(STATUS))
        invalid_mode["health"]["flight_mode"] = 7
        cases.append(("mqtav/UAV-001/status", json.dumps(invalid_mode)))
        for topic, payload in cases:
            with self.subTest(topic=topic, payload=payload):
                with self.assertRaises(MqttProtocolError):
                    self.parser.parse(topic, payload)


if __name__ == "__main__":
    unittest.main()
