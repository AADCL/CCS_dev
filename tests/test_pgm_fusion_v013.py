import hashlib
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from ccs_monitor.map_building import MapBuildingEnvelope
from ccs_monitor.map_building_config import load_map_building_config
from ccs_monitor.map_repository import MapRepository, MapRepositoryError
from ccs_monitor.models import (
    MapBounds, MapBuildMode, MapBuildProvenance, MapCreatorDevice,
    PgmFusionProvenance, PgmFusionSource, PgmTransform2D,
)
from ccs_monitor.pgm_fusion import PgmArtifactProtocol, PgmDownloadCoordinator, PgmFusionEngine
from ccs_monitor.pgm_map import PgmMapLoader
from tests.test_point_cloud import write_ascii_pcd


def write_layer(root: Path, name: str, pixels: list[list[int]], *, resolution: float = 1.0,
                origin=(0.0, 0.0, 0.0), negate: int = 0) -> tuple[Path, Path]:
    image = root / f"{name}.pgm"
    array = np.asarray(pixels, dtype=np.uint8)
    image.write_bytes(
        f"P5\n{array.shape[1]} {array.shape[0]}\n255\n".encode("ascii") + array.tobytes()
    )
    metadata = {
        "image": image.name, "resolution": resolution, "origin": list(origin),
        "negate": negate, "occupied_thresh": 0.65, "free_thresh": 0.196,
    }
    yaml_path = root / f"{name}.yaml"
    yaml_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    return image, yaml_path


class PgmFusionEngineTests(unittest.TestCase):
    def test_inverse_sampling_and_occupied_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_a, yaml_a = write_layer(root, "a", [[0, 254]])
            image_b, yaml_b = write_layer(root, "b", [[205, 0]])
            sources = (
                PgmFusionSource("a", "a", pgm_path=str(image_a), yaml_path=str(yaml_a)),
                PgmFusionSource("b", "b", pgm_path=str(image_b), yaml_path=str(yaml_b)),
            )
            result = PgmFusionEngine().fuse(
                sources, MapBounds(0, 0, 0, 2, 1, 1), root / "out.pgm", root / "out.yaml", 1.0,
            )
            pixels = PgmMapLoader().load_pgm(root / "out.pgm")
            self.assertEqual(pixels.tolist(), [[0, 0]])
            self.assertEqual(result.metadata.resolution, 1.0)

    def test_translation_rotation_and_resolution_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, yaml_path = write_layer(root, "source", [[0]], resolution=0.5)
            source = PgmFusionSource(
                "a", "a", PgmTransform2D(1.0, 0.0, 90.0),
                pgm_path=str(image), yaml_path=str(yaml_path),
            )
            engine = PgmFusionEngine()
            with self.assertRaisesRegex(Exception, "不能细于"):
                engine.fuse(
                    (source, source), MapBounds(0, 0, 0, 2, 2, 1),
                    root / "bad.pgm", root / "bad.yaml", 0.25,
                )
            resolution, outside = engine.inspect((source, source), MapBounds(0, 0, 0, 2, 2, 1))
            self.assertEqual(resolution, 0.5)
            self.assertGreaterEqual(outside, 0.0)


class PgmArtifactTests(unittest.TestCase):
    def setUp(self):
        self.config = load_map_building_config()
        self.sent = []
        self.clock_value = 10.0

    def test_protocol_and_out_of_order_duplicate_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = PgmFusionSource(
                "device:D1", "edge-map", PgmTransform2D(),
                device_id="D1", device_name="Device", device_ip="127.0.0.1",
            )
            coordinator = PgmDownloadCoordinator(
                self.config, lambda envelope, ip: self.sent.append((envelope, ip)),
                return_host="127.0.0.1", clock=lambda: self.clock_value,
            )
            completed = []
            coordinator.all_completed.connect(lambda values: completed.extend(values))
            coordinator.start("target", (source,), Path(directory) / "job")
            request = self.sent[-1][0]
            self.assertEqual(request.message_type, "request_pgm_artifact")
            ack = MapBuildingEnvelope(
                "target", "D1", request.session_id, "command_ack", 1, 1,
                {"request_id": request.payload["request_id"], "command": "request_pgm_artifact", "accepted": True},
            )
            self.assertTrue(coordinator.handle_envelope(ack, "127.0.0.1"))
            raw = b"P5\n2 1\n255\n" + bytes((0, 254))
            compressed = zlib.compress(raw)
            chunks = (compressed[:len(compressed) // 2], compressed[len(compressed) // 2:])
            manifest_payload = {
                "source_map_id": "edge-map", "frame_id": "map", "pgm_format": "P5",
                "width": 2, "height": 1, "resolution": 0.05, "origin": [0.0, 0.0, 0.0],
                "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196,
                "generated_at_ns": 1, "uncompressed_size": len(raw),
                "compressed_size": len(compressed), "chunk_count": 2,
                "crc32": zlib.crc32(compressed) & 0xFFFFFFFF,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            manifest = MapBuildingEnvelope(
                "target", "D1", request.session_id, "pgm_manifest", 2, 2, manifest_payload,
            )
            coordinator.protocol.envelope_protocol._validate_envelope(manifest)
            coordinator.handle_envelope(manifest, "127.0.0.1")
            for sequence, index in ((3, 1), (4, 1), (5, 0)):
                envelope = MapBuildingEnvelope(
                    "target", "D1", request.session_id, "pgm_chunk", sequence, sequence,
                    {"source_map_id": "edge-map", "chunk_count": 2,
                     "chunk_index": index, "data": chunks[index]},
                )
                coordinator.handle_envelope(envelope, "127.0.0.1")
            self.assertEqual(len(completed), 1)
            self.assertTrue(Path(completed[0].pgm_path).is_file())
            self.assertFalse(coordinator.active)

    def test_manifest_size_limit_and_old_endpoint_timeout(self):
        protocol = PgmArtifactProtocol(self.config)
        envelope = protocol.request_artifact(
            target_map_id="target", device_id="D1", source_map_id="edge",
            session_id="session", request_id="request", sequence=1, return_host="127.0.0.1",
        )
        self.assertEqual(protocol.decode(protocol.encode(envelope)).payload["source_map_id"], "edge")


class PgmFusionRepositoryTests(unittest.TestCase):
    def test_schema_five_commit_and_pcd_fingerprint_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapRepository(root / "maps")
            definition = repository.create("target", [MapCreatorDevice("D1", "Device", "UGV")])
            pcd = root / "source.pcd"
            write_ascii_pcd(pcd, [(0, 0, 0), (2, 1, 1)])
            ready = repository.import_pcd(definition.map_id, pcd)
            image, yaml_path = write_layer(root, "fused", [[0, 254]])
            fingerprint = repository.pcd_fingerprint(ready.map_id)
            source = PgmFusionSource("device:D1", "edge", device_id="D1")
            provenance = PgmFusionProvenance("job", fingerprint, (source,), 1.0)
            repository.write_pgm_fusion_job(ready.map_id, "job", {"job_id": "job", "state": "running"})
            updated = repository.commit_pgm_fusion_result(ready.map_id, "job", yaml_path, provenance)
            self.assertIsNotNone(updated.pgm_fusion)
            self.assertEqual(updated.pgm_fusion.target_pcd_sha256, fingerprint)
            self.assertEqual(repository.interrupted_pgm_fusion_jobs(ready.map_id), [])
            stored = yaml.safe_load((repository.root / updated.directory_name / "map.yaml").read_text())
            self.assertEqual(stored["image"], "map.pgm")
            write_ascii_pcd(repository.pcd_path(ready.map_id), [(0, 0, 0), (3, 1, 1)])
            with self.assertRaisesRegex(MapRepositoryError, "PCD 已变化"):
                repository.commit_pgm_fusion_result(ready.map_id, "job2", yaml_path, provenance)

    def test_offline_fusion_commits_pcd_and_pgm_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapRepository(root / "maps")
            output = root / "merged.pcd"
            write_ascii_pcd(output, [(0, 0, 0), (2, 1, 1)])
            _, yaml_path = write_layer(root, "merged", [[0, 254]])
            job_id = "sync-pgm-job"
            repository.write_fusion_job(job_id, {"job_id": job_id, "state": "running"})
            source = PgmFusionSource(
                "map:a", "a", source_frame_id="map", artifact_sha256="1" * 64,
            )
            pgm_provenance = PgmFusionProvenance(
                job_id, hashlib.sha256(output.read_bytes()).hexdigest(), (source,), 1.0,
            )
            definition = repository.commit_fusion_result(
                "merged", job_id, output, (), "map",
                MapBuildProvenance(MapBuildMode.FUSION, job_id),
                output_pgm_yaml=yaml_path, pgm_provenance=pgm_provenance,
            )
            map_root = repository.root / definition.directory_name
            self.assertTrue((map_root / "map.pcd").is_file())
            self.assertTrue((map_root / "map.pgm").is_file())
            self.assertTrue((map_root / "map.yaml").is_file())
            self.assertIsNotNone(definition.pgm)
            self.assertEqual(definition.pgm_fusion.job_id, job_id)


if __name__ == "__main__":
    unittest.main()
