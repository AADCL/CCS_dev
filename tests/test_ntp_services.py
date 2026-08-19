import json
import os
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ccs_monitor.ntp_config import NtpConfigError, NtpServerConfig, load_ntp_config
from ccs_monitor.ntp_services import NTP_UNIX_EPOCH_OFFSET, NtpServerService, encode_ntp_timestamp


def unused_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class NtpConfigTests(unittest.TestCase):
    def test_loads_valid_config(self):
        config = load_ntp_config()
        self.assertTrue(config.enabled)
        self.assertEqual((config.bind_host, config.port), ("0.0.0.0", 123))
        self.assertEqual((config.stratum, config.reference_id), (1, "CCS"))

    def test_rejects_invalid_reference_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ntp.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "server": {
                    "enabled": True,
                    "bind_host": "0.0.0.0",
                    "port": 123,
                    "stratum": 1,
                    "precision": -20,
                    "reference_id": "TOO-LONG",
                },
            }), encoding="utf-8")
            with self.assertRaises(NtpConfigError):
                load_ntp_config(path)


class NtpServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def test_answers_ntp_v4_request_and_stops_cleanly(self):
        port = unused_udp_port()
        now = 1_787_058_400.25
        config = NtpServerConfig(True, "127.0.0.1", port, 1, -20, "CCS")
        service = NtpServerService(config, clock=lambda: now)
        statuses = []
        service.status_changed.connect(lambda message, healthy: statuses.append((message, healthy)))
        service.start()
        self.assertTrue(self.wait_until(lambda: any(healthy for _, healthy in statuses)))

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(1.0)
        request = bytearray(48)
        request[0] = (4 << 3) | 3
        request[2] = 6
        request[40:48] = encode_ntp_timestamp(now - 0.1)
        client.sendto(request, ("127.0.0.1", port))
        response, _ = client.recvfrom(512)
        client.close()

        self.assertEqual(len(response), 48)
        self.assertEqual(response[0] & 0x07, 4)
        self.assertEqual((response[0] >> 3) & 0x07, 4)
        self.assertEqual(response[1], 1)
        self.assertEqual(response[12:16], b"CCS\0")
        self.assertEqual(response[24:32], request[40:48])
        transmit_seconds, _ = struct.unpack("!II", response[40:48])
        self.assertEqual(transmit_seconds - NTP_UNIX_EPOCH_OFFSET, int(now))

        service.stop()
        self.assertFalse(service._thread.is_alive())

    def test_port_conflict_reports_failure(self):
        occupied = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        service = NtpServerService(
            NtpServerConfig(True, "127.0.0.1", port, 1, -20, "CCS")
        )
        statuses = []
        service.status_changed.connect(lambda message, healthy: statuses.append((message, healthy)))
        try:
            service.start()
            self.assertTrue(self.wait_until(lambda: any(not healthy for _, healthy in statuses)))
            self.assertIn("失败", statuses[-1][0])
        finally:
            service.stop()
            occupied.close()


if __name__ == "__main__":
    unittest.main()
