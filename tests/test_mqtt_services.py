import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.models import ConnectionStatus
from ccs_monitor.mqtt_config import MqttMonitoringConfig
from ccs_monitor.mqtt_data_source import MqttDeviceSource
from ccs_monitor.mqtt_services import MqttBrokerService, MqttMonitoringRuntime


def unused_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class MqttServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def test_real_broker_subscriber_and_clean_shutdown(self):
        import paho.mqtt.client as mqtt

        with tempfile.TemporaryDirectory() as directory:
            config = MqttMonitoringConfig(
                "127.0.0.1", unused_port(), 1, "mqtav", 1.0, 2.0, 5.0, 500,
                f"test-ground-{time.time_ns()}",
            )
            repository = DeviceConfigRepository(Path(directory) / "devices.json")
            source = MqttDeviceSource(config, repository, start_watchdog=False)
            runtime = MqttMonitoringRuntime(config, source)
            runtime.start()
            try:
                self.assertTrue(self.wait_until(
                    lambda: source.module_healthy and "订阅" in source.module_status_message,
                ))
                publisher = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"test-publisher-{time.time_ns()}",
                )
                publisher.connect("127.0.0.1", config.port, 10)
                publisher.loop_start()
                common = {
                    "schema_version": "1.0",
                    "timestamp": "2026-07-31T09:30:00Z",
                    "device": {"id": "UAV-017", "ip": "192.168.20.17"},
                }
                heartbeat = {**common, "message_type": "heartbeat", "sequence": 1}
                status = {
                    **common,
                    "message_type": "status",
                    "sequence": 1,
                    "health": {
                        "fcu_connected": True,
                        "armed": False,
                        "system_status": 3,
                        "flight_mode": "AUTO.MISSION",
                        "battery": {"percentage": 76.5, "voltage": 15.8, "current": 4.2},
                        "mission_status": "active",
                    },
                }
                publisher.publish("mqtav/UAV-017/heartbeat", json.dumps(heartbeat), qos=1).wait_for_publish()
                publisher.publish("mqtav/UAV-017/status", json.dumps(status), qos=1).wait_for_publish()
                self.assertTrue(self.wait_until(
                    lambda: source.device("UAV-017").connection_status == ConnectionStatus.ONLINE
                    and source.device("UAV-017").battery_percent == 76.5,
                ))
                publisher.disconnect()
                publisher.loop_stop()
            finally:
                runtime.stop()
            self.assertFalse(runtime.broker._thread.is_alive())

    def test_port_conflict_reports_failure_without_raising(self):
        port = unused_port()
        occupied = socket.socket()
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", port))
        occupied.listen(1)
        config = MqttMonitoringConfig(
            "127.0.0.1", port, 1, "mqtav", 1.0, 2.0, 5.0, 500, "test-ground",
        )
        service = MqttBrokerService(config)
        statuses = []
        service.status_changed.connect(lambda message, healthy: statuses.append((message, healthy)))
        try:
            service.start()
            self.assertTrue(self.wait_until(lambda: any(not healthy for _, healthy in statuses)))
            self.assertFalse(statuses[-1][1])
            self.assertIn("失败", statuses[-1][0])
        finally:
            service.stop()
            occupied.close()

    def test_immediate_stop_does_not_leave_broker_thread(self):
        config = MqttMonitoringConfig(
            "127.0.0.1", unused_port(), 1, "mqtav", 1.0, 2.0, 5.0, 500, "test-ground",
        )
        service = MqttBrokerService(config)
        service.start()
        service.stop()
        self.assertFalse(service._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
