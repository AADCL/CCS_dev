import json
import os
import socket
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from ccs_monitor.models import DeviceLogLevel, TelemetryAvailability, UdpLinkStatus
from ccs_monitor.udp_config import UdpConfigError, load_udp_config
from ccs_monitor.udp_protocol import UdpEnvelope, UdpProtocolError, UdpTelemetryProtocol
from ccs_monitor.udp_store import UdpTelemetryStore
from ccs_monitor.udp_services import UdpMonitoringRuntime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parent.parent


class UdpConfigAndProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = load_udp_config(ROOT / "config" / "udp_telemetry.json")
        self.protocol = UdpTelemetryProtocol(self.config)

    def test_descriptor_hash_is_stable(self):
        self.assertEqual(len(self.config.descriptor_hash), 64)
        self.assertEqual(self.config.port, 14560)
        self.assertEqual(self.config.descriptor("global_pose").level, 1)

    def test_ground_and_edge_descriptor_hashes_match(self):
        edge_package = ROOT / "edge_side_pkg" / "EPGeneral_udp_telemetry"
        sys.path.insert(0, str(edge_package / "src"))
        try:
            from epgeneral_udp_telemetry.config import load_config as load_edge_config
            edge = load_edge_config(
                str(edge_package / "config" / "telemetry.yaml"),
                str(ROOT / "edge_side_pkg" / "EPGeneral_device_config" / "config" / "device.yaml"),
            )
        finally:
            sys.path.pop(0)
        self.assertEqual(edge["descriptor_hash"], self.config.descriptor_hash)

    def test_duplicate_descriptor_is_rejected(self):
        payload = json.loads((ROOT / "config" / "udp_telemetry.json").read_text(encoding="utf-8"))
        payload["descriptors"].append(dict(payload["descriptors"][0]))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "udp.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(UdpConfigError):
                load_udp_config(path)

    def test_round_trip_and_strict_payload_validation(self):
        event = UdpEnvelope(
            "UAV_001", "session-a", "telemetry", 1, 123, 1,
            {"global_pose": {
                "valid": True, "x": 1.0, "y": 2.0, "z": 3.0,
                "roll": 4.0, "pitch": 5.0, "yaw": 6.0, "sample_age_seconds": 0.01,
            }},
        )
        decoded = self.protocol.decode(self.protocol.encode(event))
        self.assertEqual(decoded, event)
        invalid = replace(event, payload={"global_pose": dict(event.payload["global_pose"], x=float("nan"))})
        with self.assertRaises(UdpProtocolError):
            self.protocol.decode(self.protocol.encode(invalid))

    def test_hash_mismatch_and_oversize_are_rejected(self):
        event = UdpEnvelope("UAV_001", "session", "heartbeat", 0, 1, None, {})
        encoded = self.protocol.encode(event)
        import msgpack
        raw = msgpack.unpackb(encoded, raw=False)
        raw["descriptor_hash"] = "0" * 64
        with self.assertRaises(UdpProtocolError):
            self.protocol.decode(msgpack.packb(raw, use_bin_type=True))
        with self.assertRaises(UdpProtocolError):
            self.protocol.decode(b"x" * (self.config.max_datagram_bytes + 1))

    def test_text_status_length_is_bounded(self):
        event = UdpEnvelope(
            "UAV_001", "session", "telemetry", 1, 1, 3,
            {"mapping_mode": {"valid": True, "status": "available", "value": "x" * 129}},
        )
        with self.assertRaises(UdpProtocolError):
            self.protocol.decode(self.protocol.encode(event))


class UdpStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = [10.0]
        self.logs = []
        self.config = load_udp_config(ROOT / "config" / "udp_telemetry.json")
        self.protocol = UdpTelemetryProtocol(self.config)
        self.store = UdpTelemetryStore(
            self.config,
            lambda device_id: device_id == "UAV_001",
            lambda device_id, level, message: self.logs.append((device_id, level, message)),
            clock=lambda: self.now[0],
            start_watchdog=False,
        )

    def send(self, message_type, sequence, payload=None, level=None, session="boot-a"):
        event = UdpEnvelope("UAV_001", session, message_type, sequence, 1, level, payload or {})
        self.store.process_datagram(self.protocol.encode(event), "127.0.0.1", 10000)

    def test_heartbeat_warning_error_and_recovery_log_once(self):
        self.send("heartbeat", 0)
        self.assertEqual(self.store.telemetry("UAV_001").udp_link_status, UdpLinkStatus.ONLINE)
        self.now[0] = 12.1
        self.store.check_heartbeats()
        self.store.check_heartbeats()
        self.assertEqual(self.store.telemetry("UAV_001").udp_link_status, UdpLinkStatus.WARNING)
        self.now[0] = 15.1
        self.store.check_heartbeats()
        self.store.check_heartbeats()
        self.assertEqual(self.store.telemetry("UAV_001").udp_link_status, UdpLinkStatus.OFFLINE)
        self.now[0] = 16.0
        self.send("heartbeat", 1)
        levels = [item[1] for item in self.logs]
        self.assertEqual(levels.count(DeviceLogLevel.WARNING), 1)
        self.assertEqual(levels.count(DeviceLogLevel.ERROR), 1)
        self.assertIn(DeviceLogLevel.INFO, levels)

    def test_telemetry_mapping_and_session_sequence_reset(self):
        payload = {
            "livox_pointcloud": {"valid": True, "status": "available", "estimated_hz": 10.0, "sample_age_seconds": 0.1},
        }
        self.send("telemetry", 4, payload, 2)
        snapshot = self.store.telemetry("UAV_001")
        self.assertEqual(snapshot.pointcloud.availability, TelemetryAvailability.AVAILABLE)
        self.assertEqual(snapshot.pointcloud.estimated_hz, 10.0)
        self.send("telemetry", 0, payload, 2, session="boot-b")
        self.assertEqual(self.store.telemetry("UAV_001").pointcloud.estimated_hz, 10.0)

    def test_retired_session_cannot_replace_current_session(self):
        payload = {"global_pose": {
            "valid": True, "x": 1.0, "y": 2.0, "z": 3.0,
            "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
            "sample_age_seconds": 0.0,
        }}
        self.send("telemetry", 10, payload, 1, session="boot-a")
        self.send("telemetry", 0, payload, 1, session="boot-b")
        self.send("telemetry", 11, payload, 1, session="boot-a")
        self.assertEqual(self.store._trackers["UAV_001"].session_id, "boot-b")

    def test_canonical_device_id_makes_case_variant_telemetry_visible(self):
        store = UdpTelemetryStore(
            self.config,
            lambda device_id: device_id.casefold() == "uav_001".casefold(),
            canonical_device_id=lambda _device_id: "UAV_001",
            start_watchdog=False,
        )
        event = UdpEnvelope("uav_001", "boot-a", "telemetry", 0, 1, 1, {
            "global_pose": {
                "valid": True, "x": 4.0, "y": 5.0, "z": 6.0,
                "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
                "sample_age_seconds": 0.0,
            },
        })
        store.process_datagram(self.protocol.encode(event))
        self.assertEqual(store.telemetry("UAV_001").global_pose.x, 4.0)
        self.assertNotIn("uav_001", store._snapshots)

    def test_text_status_mapping(self):
        payload = {
            "mapping_mode": {
                "valid": True,
                "status": "available",
                "value": "增量建图",
                "sample_age_seconds": 0.1,
            },
        }
        self.send("telemetry", 1, payload, 3)
        status = next(item for item in self.store.telemetry("UAV_001").sensor_statuses if item.name == "mapping_mode")
        self.assertEqual(status.value, "增量建图")
        self.assertEqual(status.availability, TelemetryAvailability.AVAILABLE)

    def test_unknown_device_is_ignored(self):
        event = UdpEnvelope("UNKNOWN", "session", "heartbeat", 0, 1, None, {})
        self.store.process_datagram(self.protocol.encode(event))
        self.assertEqual(self.store.telemetry("UNKNOWN").udp_link_status, UdpLinkStatus.UNKNOWN)


class UdpRuntimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_localhost_receive_and_clean_stop(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        config = replace(load_udp_config(ROOT / "config" / "udp_telemetry.json"), bind_host="127.0.0.1", port=port)
        store = UdpTelemetryStore(config, lambda value: value == "UAV_001", start_watchdog=False)
        runtime = UdpMonitoringRuntime(config, store)
        runtime.start()
        deadline = time.monotonic() + 2.0
        while not store.module_healthy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(store.module_healthy, store.module_message)
        event = UdpEnvelope("UAV_001", "integration", "heartbeat", 0, 1, None, {})
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(UdpTelemetryProtocol(config).encode(event), ("127.0.0.1", port))
        sender.close()
        deadline = time.monotonic() + 2.0
        while store.telemetry("UAV_001").udp_link_status != UdpLinkStatus.ONLINE and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(store.telemetry("UAV_001").udp_link_status, UdpLinkStatus.ONLINE)
        runtime.stop()
        self.assertFalse(runtime.receiver.isRunning())


if __name__ == "__main__":
    unittest.main()
