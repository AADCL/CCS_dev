import importlib.util
import socket
import tempfile
import threading
import time
import unittest
import zlib
from dataclasses import replace
from pathlib import Path


DEPS_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("PySide6", "msgpack", "numpy"))

if DEPS_AVAILABLE:
    import numpy as np

    from ccs_monitor.map_building import MapBuildingEnvelope, MapBuildingProtocol
    from ccs_monitor.map_building_config import load_map_building_config
    from ccs_monitor.map_building_services import MapBuildingService
    from ccs_monitor.map_repository import MapRepository
    from ccs_monitor.models import DeviceSnapshot, MapCreatorDevice


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@unittest.skipUnless(DEPS_AVAILABLE, "mapping service dependencies are not installed")
class MapBuildingServiceTests(unittest.TestCase):
    def test_localhost_negotiation_stream_and_commit(self):
        data_port = free_udp_port()
        control_port = free_udp_port()
        config = replace(
            load_map_building_config(),
            bind_host="127.0.0.1",
            data_port=data_port,
            device_control_port=control_port,
            command_retry_seconds=0.05,
            command_max_attempts=3,
            warning_timeout_seconds=0.5,
            error_timeout_seconds=1.0,
        )
        protocol = MapBuildingProtocol(config)
        with tempfile.TemporaryDirectory() as directory:
            repository = MapRepository(Path(directory) / "maps")
            creator = MapCreatorDevice("UAV-001", "测试设备", "UAV")
            definition = repository.create("测试地图", [creator])
            device = DeviceSnapshot("UAV-001", "测试设备", "UAV", ip_address="127.0.0.1")
            service = MapBuildingService(config, repository)
            edge = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            edge.bind(("127.0.0.1", control_port))
            edge.settimeout(2.0)
            service.start()
            self.assertTrue(service.available)
            session_id = service.start_mapping(definition, device)

            start_raw, _ = edge.recvfrom(4096)
            start = protocol.decode(start_raw)
            self.assertEqual(start.message_type, "start_mapping")
            ack = MapBuildingEnvelope(
                definition.map_id, device.device_id, session_id, "command_ack", 1, time.time_ns(),
                {"request_id": start.payload["request_id"], "command": "start_mapping", "accepted": True},
            )
            edge.sendto(protocol.encode(ack), ("127.0.0.1", data_port))

            points = np.asarray([(0, 0, 0), (1, 2, 3)], dtype="<f4")
            compressed = zlib.compress(points.tobytes())
            crc = zlib.crc32(compressed) & 0xFFFFFFFF
            identity = {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
            cloud = MapBuildingEnvelope(
                definition.map_id, device.device_id, session_id, "cloud_chunk", 2, time.time_ns(),
                {
                    "frame_id": 1, "chunk_count": 1, "chunk_index": 0,
                    "frame_crc32": crc, "sample_stamp_ns": 1, "point_count": 2,
                    "map_from_body": identity, "body_from_sensor": identity, "data": compressed,
                },
            )
            edge.sendto(protocol.encode(cloud), ("127.0.0.1", data_port))
            deadline = time.time() + 2
            while time.time() < deadline:
                snapshot = service.current_snapshot
                if snapshot and snapshot.complete_frames == 1:
                    break
                time.sleep(0.02)
            self.assertEqual(service.current_snapshot.complete_frames, 1)

            service.stop_mapping()
            stop_raw, _ = edge.recvfrom(4096)
            stop = protocol.decode(stop_raw)
            while stop.message_type != "stop_mapping":
                stop_raw, _ = edge.recvfrom(4096)
                stop = protocol.decode(stop_raw)
            stop_ack = MapBuildingEnvelope(
                definition.map_id, device.device_id, session_id, "command_ack", 3, time.time_ns(),
                {"request_id": stop.payload["request_id"], "command": "stop_mapping", "accepted": True},
            )
            edge.sendto(protocol.encode(stop_ack), ("127.0.0.1", data_port))
            deadline = time.time() + 3
            while time.time() < deadline and service.active:
                time.sleep(0.02)
            self.assertFalse(service.active)
            committed = repository.map_by_id(definition.map_id)
            self.assertEqual(committed.point_count, 2)
            self.assertIsNotNone(committed.last_mapping)
            service.stop()
            edge.close()

    def test_bind_failure_only_disables_service(self):
        port = free_udp_port()
        occupied = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        occupied.bind(("127.0.0.1", port))
        config = replace(load_map_building_config(), bind_host="127.0.0.1", data_port=port)
        with tempfile.TemporaryDirectory() as directory:
            service = MapBuildingService(config, MapRepository(Path(directory) / "maps"))
            service.start()
            self.assertFalse(service.available)
            self.assertIn("绑定失败", service.module_message)
        occupied.close()


if __name__ == "__main__":
    unittest.main()
