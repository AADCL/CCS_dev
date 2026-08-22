import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.models import ConnectionStatus, DeviceLogLevel, HealthStatus, TaskStatus
from ccs_monitor.mqtt_config import MqttMonitoringConfig
from ccs_monitor.mqtt_data_source import MqttDeviceSource, normalize_mission_status


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class MqttDeviceSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        repository = DeviceConfigRepository(Path(self.directory.name) / "devices.json")
        self.clock = FakeClock()
        self.config = MqttMonitoringConfig(
            "127.0.0.1", 1884, 1, "mqtav", 1.0, 2.0, 5.0, 500, "test-ground",
        )
        self.source = MqttDeviceSource(
            self.config,
            repository,
            clock=self.clock,
            wall_clock=lambda: datetime(2026, 7, 31, tzinfo=timezone.utc),
            start_watchdog=False,
        )
        self.device_id = "UAV-017"
        self.ip = "192.168.20.17"

    def tearDown(self):
        self.directory.cleanup()

    def message(self, kind, sequence=None, health=None, device_id=None, ip=None, session_id=None):
        device_id = device_id or self.device_id
        body = {
            "schema_version": "1.0",
            "message_type": kind,
            "timestamp": "2026-07-31T09:30:00Z",
            "device": {"id": device_id, "ip": ip or self.ip},
        }
        if sequence is not None:
            body["sequence"] = sequence
        if session_id is not None:
            body["session_id"] = session_id
        if kind == "presence":
            body["status"] = "online"
        if health is not None:
            body["health"] = health
        self.source.process_message(f"mqtav/{device_id}/{kind}", json.dumps(body).encode())

    def test_startup_heartbeat_timeouts_and_recovery(self):
        device = self.source.device(self.device_id)
        self.assertEqual(device.connection_status, ConnectionStatus.OFFLINE)
        self.assertIsNone(device.battery_percent)
        self.assertEqual(device.health_status, HealthStatus.UNKNOWN)

        self.message("heartbeat", 1)
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.ONLINE)
        self.clock.value = 2.1
        self.source.check_heartbeats()
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.WARNING)
        self.source.check_heartbeats()
        self.clock.value = 5.1
        self.source.check_heartbeats()
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.OFFLINE)
        self.source.check_heartbeats()
        levels = [entry.level for entry in self.source.logs(self.device_id)]
        self.assertEqual(levels.count(DeviceLogLevel.WARNING), 1)
        self.assertEqual(levels.count(DeviceLogLevel.ERROR), 1)

        self.message("heartbeat", 2)
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.ONLINE)
        self.assertIn("恢复", self.source.logs(self.device_id)[-1].message)

    def test_status_maps_all_runtime_fields_and_rejects_stale_sequence(self):
        health = {
            "fcu_connected": False,
            "armed": True,
            "system_status": 4,
            "flight_mode": "AUTO.LOITER",
            "battery": {"percentage": 61.25, "voltage": 15.2, "current": 3.8},
            "mission_status": "executing",
        }
        self.message("status", 10, health)
        device = self.source.device(self.device_id)
        self.assertEqual(device.health_status, HealthStatus.ATTENTION)
        self.assertEqual(device.task_status, TaskStatus.EXECUTING)
        self.assertEqual(device.flight_mode, "AUTO.LOITER")
        self.assertTrue(device.armed)
        self.assertEqual(device.battery_percent, 61.25)
        self.assertEqual(device.battery_voltage, 15.2)
        self.assertEqual(device.battery_current, 3.8)

        stale = dict(health, flight_mode="MANUAL")
        self.message("status", 9, stale)
        self.assertEqual(self.source.device(self.device_id).flight_mode, "AUTO.LOITER")
        self.assertEqual(self.source.logs(self.device_id)[-1].level, DeviceLogLevel.WARNING)

    def test_new_edge_session_accepts_reset_heartbeat_and_status_sequences(self):
        health = {
            "fcu_connected": True, "armed": False, "system_status": 3,
            "flight_mode": "AUTO", "battery": {"percentage": 50},
            "mission_status": "standby",
        }
        self.message("heartbeat", 50, session_id="boot-a")
        self.message("status", 51, health, session_id="boot-a")
        self.message("heartbeat", 1, session_id="boot-b")
        self.message("status", 2, dict(health, flight_mode="MANUAL"), session_id="boot-b")
        device = self.source.device(self.device_id)
        self.assertEqual(device.connection_status, ConnectionStatus.ONLINE)
        self.assertEqual(device.flight_mode, "MANUAL")
        self.assertFalse(any(
            "乱序" in entry.message for entry in self.source.logs(self.device_id)
        ))
        self.message("status", 52, dict(health, flight_mode="STALE"), session_id="boot-a")
        self.assertEqual(self.source.device(self.device_id).flight_mode, "MANUAL")

    def test_legacy_online_presence_resets_sequence_window(self):
        self.message("heartbeat", 20)
        self.message("presence")
        self.message("heartbeat", 1)
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.ONLINE)

    def test_qos_duplicate_is_ignored_without_out_of_order_warning(self):
        self.message("heartbeat", 1, session_id="boot-a")
        before = len(self.source.logs(self.device_id))
        self.message("heartbeat", 1, session_id="boot-a")
        self.assertEqual(len(self.source.logs(self.device_id)), before)

    def test_unknown_device_is_ignored_and_log_buffer_is_bounded(self):
        warnings = []
        self.source.protocol_warning.connect(warnings.append)
        self.message("heartbeat", 1, device_id="UAV-999")
        self.assertTrue(warnings)
        for sequence in range(501):
            self.source.append_external_log(
                self.device_id, DeviceLogLevel.INFO, f"external event {sequence}"
            )
        self.assertEqual(len(self.source.logs(self.device_id)), 500)

    def test_presence_offline_enters_warning_then_error(self):
        payload = {
            "schema_version": "1.0", "message_type": "presence",
            "timestamp": "2026-07-31T09:30:00Z",
            "device": {"id": self.device_id, "ip": self.ip}, "status": "offline",
        }
        self.source.process_message(
            f"mqtav/{self.device_id}/presence", json.dumps(payload).encode(),
        )
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.WARNING)
        self.clock.value = 5.1
        self.source.check_heartbeats()
        self.assertEqual(self.source.device(self.device_id).connection_status, ConnectionStatus.OFFLINE)

    def test_mission_aliases(self):
        self.assertEqual(normalize_mission_status("running"), TaskStatus.EXECUTING)
        self.assertEqual(normalize_mission_status("standby"), TaskStatus.STANDBY)
        self.assertEqual(normalize_mission_status("paused"), TaskStatus.PAUSED)
        self.assertEqual(normalize_mission_status("succeeded"), TaskStatus.COMPLETED)
        self.assertEqual(normalize_mission_status("custom"), TaskStatus.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
