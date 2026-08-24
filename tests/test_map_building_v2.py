from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ccs_monitor.map_building import MapBuildingEnvelope
from ccs_monitor.map_building_config import load_map_building_config
from ccs_monitor.map_building_v2 import (
    ArtifactDescriptor, ArtifactDownloader, ArtifactPackageValidator,
    ArtifactValidationError, MapBuildingV2Protocol, RemoteMappingCoordinator,
    RemoteMappingProtocolError, cloud_fragment_from_payload,
)
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.pgm_map import PgmMapLoader
from ccs_monitor.models import (
    DeviceSnapshot, MapBuildingResultMetadata, MapCreatorDevice,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pcd() -> bytes:
    return (
        b"VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
        b"COUNT 1 1 1\nWIDTH 2\nHEIGHT 1\nPOINTS 2\nDATA ascii\n"
        b"0 0 0\n1 2 0.5\n"
    )


def _artifact_bytes(map_id: str, device_id: str, session_id: str) -> bytes:
    pcd = _pcd()
    pgm = b"P5\n2 2\n255\n" + bytes((0, 254, 205, 254))
    map_yaml = yaml.safe_dump({
        "image": "map.pgm", "resolution": 0.1,
        "origin": [0.0, 0.0, 0.0], "negate": 0,
        "occupied_thresh": 0.65, "free_thresh": 0.196,
    }, sort_keys=False).encode()
    files = {"pcd": ("map.pcd", pcd), "pgm": ("map.pgm", pgm),
             "yaml": ("map.yaml", map_yaml)}
    manifest = {
        "schema_version": 1, "map_id": map_id, "device_id": device_id,
        "session_id": session_id, "frame_id": "lio_odom",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            role: {"path": name, "byte_count": len(data), "sha256": _sha(data)}
            for role, (name, data) in files.items()
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.values():
            archive.writestr(name, data)
    return output.getvalue()


class _Response(io.BytesIO):
    def __init__(self, data: bytes, status: int) -> None:
        super().__init__(data)
        self.status = status

    def getcode(self) -> int:
        return self.status


class _Opener:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        range_value = request.headers.get("Range")
        if range_value:
            offset = int(range_value.split("=")[1].split("-")[0])
            return _Response(self.data[offset:], 206)
        return _Response(self.data, 200)


class MapBuildingV2ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_map_building_config()
        self.protocol = MapBuildingV2Protocol(self.config)

    def test_frame_lifecycle_configuration(self):
        self.assertEqual(self.config.remote_mapping_frame, "odom")
        self.assertEqual(self.config.remote_artifact_frame, "lio_odom")
        self.assertEqual(self.config.final_map_frame, "map")

    def test_prepare_result_round_trip_and_consistency(self):
        envelope = MapBuildingEnvelope(
            "map-1", "UAV-1", "session-1", "prepare_result", 1, 1,
            {"request_id": "request-1", "accepted": True,
             "checks": [{"name": "pointcloud", "available": True}],
             "sample_window_seconds": 1.0, "frame_id": "odom",
             "capability_version": "0.8.0", "preview_transport": "pcd_fragment_http",
             "fragment_interval_seconds": 1.0},
        )
        self.assertEqual(self.protocol.decode(self.protocol.encode(envelope)), envelope)
        invalid = replace(envelope, payload={**envelope.payload, "accepted": False})
        with self.assertRaises(RemoteMappingProtocolError):
            self.protocol.encode(invalid)

    def test_artifact_ready_requires_bounded_hash_and_expiry(self):
        envelope = MapBuildingEnvelope(
            "map-1", "UAV-1", "session-1", "artifact_status", 2, 2,
            {"state": "ready", "url": "http://127.0.0.1:8080/result.zip?token=x",
             "byte_count": 100, "sha256": "0" * 64,
             "expires_at": "2026-08-20T10:00:00+00:00"},
        )
        self.protocol.decode(self.protocol.encode(envelope))
        with self.assertRaises(RemoteMappingProtocolError):
            self.protocol.encode(replace(envelope, payload={**envelope.payload, "sha256": "bad"}))

    def test_cloud_fragment_records_source_to_display_transform(self):
        payload = {
            "fragment_id": 1, "url": "http://127.0.0.1:14600/preview.pcd?token=x",
            "byte_count": 100, "sha256": "0" * 64, "point_count": 2,
            "frame_id": "odom", "source_frame_id": "lio_odom",
            "display_from_source": {
                "x": 1.0, "y": 2.0, "z": 0.0,
                "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
            },
            "started_at_ns": 1, "ended_at_ns": 2,
            "expires_at": "2030-01-01T00:00:00+00:00",
        }
        envelope = MapBuildingEnvelope(
            "map-1", "UAV-1", "session-1", "cloud_fragment_ready", 2, 2, payload)
        decoded = self.protocol.decode(self.protocol.encode(envelope))
        descriptor = cloud_fragment_from_payload(decoded.payload)
        self.assertEqual(descriptor.frame_id, "odom")
        self.assertEqual(descriptor.source_frame_id, "lio_odom")
        self.assertEqual(descriptor.display_from_source["x"], 1.0)
        invalid = replace(envelope, payload={key: value for key, value in payload.items()
                                             if key != "display_from_source"})
        with self.assertRaises(RemoteMappingProtocolError):
            self.protocol.encode(invalid)


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_map_building_config()

    def test_range_resume_and_sha256(self):
        data = b"remote-map-artifact"
        opener = _Opener(data)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.zip.part"
            target.write_bytes(data[:5])
            descriptor = ArtifactDescriptor(
                "http://127.0.0.1:8080/result.zip?token=x", len(data), _sha(data),
                datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            ArtifactDownloader(self.config, opener).download(
                descriptor, "127.0.0.1", target
            )
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(opener.requests[0].headers["Range"], "bytes=5-")

    def test_validate_and_commit_complete_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapRepository(root / "maps")
            definition = repository.create(
                "Remote", (MapCreatorDevice("UAV-1", "Device", "UAV"),)
            )
            archive = root / "result.zip"
            archive.write_bytes(_artifact_bytes(definition.map_id, "UAV-1", "session-1"))
            artifact = ArtifactPackageValidator(self.config).validate(
                archive, root / "validated", map_id=definition.map_id,
                device_id="UAV-1", session_id="session-1",
            )
            self.assertEqual(artifact.frame_id, "lio_odom")
            artifact = replace(artifact, frame_id=self.config.final_map_frame)
            metadata = MapBuildingResultMetadata(
                "session-1", "UAV-1", datetime.now(timezone.utc),
                datetime.now(timezone.utc), self.config.protocol_v2_id, 0.1,
                2, 0, 2, 2, 0.2, _sha(archive.read_bytes()),
                artifact.file_sha256["pcd"], artifact.file_sha256["pgm"],
                artifact.file_sha256["yaml"],
            )
            committed = repository.commit_remote_mapping_artifact(
                definition.map_id, artifact, metadata
            )
            self.assertEqual(committed.point_count, 2)
            self.assertIsNotNone(committed.pgm)
            self.assertEqual(committed.last_mapping.protocol_id, "ccs-map-stream-v2")
            self.assertTrue(repository.pcd_path(definition.map_id).is_file())
            yaml_path = repository.pgm_paths(definition.map_id)[0]
            self.assertTrue(yaml_path.is_file())
            self.assertEqual(PgmMapLoader().load_yaml(yaml_path).pixels.shape, (2, 2))
            self.assertEqual(committed.last_mapping.yaml_sha256, _sha(yaml_path.read_bytes()))

    def test_rejects_undeclared_and_traversal_members(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("manifest.json", "{}")
                bundle.writestr("../escape.pcd", b"bad")
            with self.assertRaises(ArtifactValidationError):
                ArtifactPackageValidator(self.config).validate(
                    archive, Path(directory) / "out", map_id="map-1",
                    device_id="UAV-1", session_id="session-1",
                )


class CoordinatorTests(unittest.TestCase):
    def test_prepare_failure_retry_and_ready_gate(self):
        config = load_map_building_config()
        sent = []
        with tempfile.TemporaryDirectory() as directory:
            repository = MapRepository(Path(directory) / "maps")
            definition = repository.create(
                "Remote", (MapCreatorDevice("UAV-1", "Device", "UAV"),)
            )
            device = DeviceSnapshot(
                "UAV-1", "Device", "UAV", ip_address="127.0.0.1"
            )
            coordinator = RemoteMappingCoordinator(
                config, repository, lambda raw, ip: sent.append((raw, ip))
            )
            session_id = coordinator.prepare(definition, device, "127.0.0.1", 14562)
            request = coordinator.protocol.decode(sent[-1][0])
            self.assertEqual(
                request.payload["required_inputs"],
                ["pointcloud", "imu", "artifact_storage", "map_generation"],
            )
            rejected = MapBuildingEnvelope(
                definition.map_id, device.device_id, session_id, "prepare_result", 1, 1,
                {"request_id": request.payload["request_id"], "accepted": False,
                 "checks": [{"name": "pointcloud", "available": False,
                             "reason": "topic unavailable"}],
                 "sample_window_seconds": 0.2, "frame_id": "odom",
                 "capability_version": "0.1.0", "error_code": "POINTCLOUD_MISSING",
                 "reason": "pointcloud unavailable"},
            )
            coordinator.handle(rejected, "127.0.0.1")
            self.assertEqual(coordinator.snapshot.state, "failed")
            coordinator.retry_prepare("127.0.0.1", 14562)
            request = coordinator.protocol.decode(sent[-1][0])
            self.assertTrue(request.payload["restart_active"])
            accepted = replace(
                rejected, sequence=2,
                payload={"request_id": request.payload["request_id"], "accepted": True,
                         "checks": [{"name": "pointcloud", "available": True}],
                         "sample_window_seconds": 0.2, "frame_id": "odom",
                         "capability_version": "0.1.0", "restarted": True,
                         "previous_state": "mapping", "active_session_id": session_id},
            )
            coordinator.handle(accepted, "127.0.0.1")
            self.assertEqual(coordinator.snapshot.state, "ready")
            self.assertTrue(any(item.event == "recovery"
                                for item in coordinator.snapshot.log_entries))
            coordinator.begin()
            self.assertEqual(coordinator.snapshot.state, "starting")

    def test_start_waits_for_deadline_and_accepts_late_ack(self):
        config = load_map_building_config()
        now = [10.0]
        sent = []
        with tempfile.TemporaryDirectory() as directory:
            repository = MapRepository(Path(directory) / "maps")
            definition = repository.create(
                "Remote", (MapCreatorDevice("UAV-1", "Device", "UAV"),))
            device = DeviceSnapshot("UAV-1", "Device", "UAV", ip_address="127.0.0.1")
            coordinator = RemoteMappingCoordinator(
                config, repository, lambda raw, ip: sent.append((raw, ip)),
                clock=lambda: now[0])
            session_id = coordinator.prepare(definition, device, "127.0.0.1", 14562)
            prepare = coordinator.protocol.decode(sent[-1][0])
            coordinator.handle(MapBuildingEnvelope(
                definition.map_id, device.device_id, session_id, "prepare_result", 1, 1,
                {"request_id": prepare.payload["request_id"], "accepted": True,
                 "checks": [{"name": "pointcloud", "available": True}],
                 "sample_window_seconds": 0.2, "frame_id": "odom",
                 "capability_version": "0.4.0"}), "127.0.0.1")
            coordinator.begin()
            for unused in range(config.command_max_attempts + 2):
                now[0] += config.command_retry_seconds
                coordinator.tick("127.0.0.1", 14562)
            self.assertEqual(coordinator.snapshot.state, "starting")
            start = coordinator.protocol.decode(sent[-1][0])
            coordinator.handle(MapBuildingEnvelope(
                definition.map_id, device.device_id, session_id, "command_ack", 2, 2,
                {"request_id": start.payload["request_id"], "command": "start_mapping",
                 "accepted": True}), "127.0.0.1")
            self.assertEqual(coordinator.snapshot.state, "mapping")

    def test_protocol_log_is_bounded_to_200_entries(self):
        config = load_map_building_config()
        with tempfile.TemporaryDirectory() as directory:
            repository = MapRepository(Path(directory) / "maps")
            definition = repository.create(
                "Remote", (MapCreatorDevice("UAV-1", "Device", "UAV"),))
            device = DeviceSnapshot("UAV-1", "Device", "UAV", ip_address="127.0.0.1")
            coordinator = RemoteMappingCoordinator(config, repository, lambda raw, ip: None)
            coordinator.prepare(definition, device, "127.0.0.1", 14562)
            for index in range(205):
                coordinator._log(coordinator.session, "LOCAL", "test", str(index))
            self.assertEqual(len(coordinator.snapshot.log_entries), 200)
            self.assertEqual(coordinator.snapshot.log_entries[-1].summary, "204")

    def test_processed_fragment_deduplication_cache_is_bounded(self):
        config = load_map_building_config()
        with tempfile.TemporaryDirectory() as directory:
            repository = MapRepository(Path(directory) / "maps")
            definition = repository.create(
                "Remote", (MapCreatorDevice("UAV-1", "Device", "UAV"),))
            device = DeviceSnapshot("UAV-1", "Device", "UAV", ip_address="127.0.0.1")
            coordinator = RemoteMappingCoordinator(config, repository, lambda raw, ip: None)
            coordinator.prepare(definition, device, "127.0.0.1", 14562)
            maximum = max(64, config.max_pending_preview_fragments * 16)
            for fragment_id in range(maximum + 10):
                coordinator._remember_processed_fragment(coordinator.session, fragment_id)
            self.assertEqual(len(coordinator.session.processed_fragments), maximum)
            self.assertNotIn(0, coordinator.session.processed_fragments)
            self.assertIn(maximum + 9, coordinator.session.processed_fragments)
            coordinator.shutdown()


if __name__ == "__main__":
    unittest.main()
