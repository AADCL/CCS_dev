import json
import socket
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ccs_monitor.map_building import MapBuildingEnvelope, MapBuildingProtocol, write_binary_pcd
from ccs_monitor.map_building_config import load_map_building_config
from ccs_monitor.map_building_services import MapBuildingService
from ccs_monitor.map_fusion import MapFusionRepository, MapFusionRunner, transform_points
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.models import (
    DeviceSnapshot, MapBuildMode, MapBuildProvenance, MapCreatorDevice,
    MapTransform,
)


class _CaptureSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))


class MapFusionV011Tests(unittest.TestCase):
    def test_transform_uses_primary_from_source_xyz_and_rpy(self):
        points = np.asarray([(1.0, 0.0, 0.0)], dtype=np.float32)
        transformed = transform_points(
            points, MapTransform("secondary", False, (2.0, 3.0, 4.0), (0.0, 0.0, 90.0))
        )
        np.testing.assert_allclose(transformed, [(2.0, 4.0, 4.0)], atol=1e-6)

    def test_builtin_runner_and_imported_example_execute_in_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "algorithms.json"
            algorithms = MapFusionRepository(registry, root / "assets")
            runner = MapFusionRunner(timeout_seconds=10)
            first = root / "first.pcd"
            second = root / "second.pcd"
            output = root / "output.pcd"
            write_binary_pcd(first, np.asarray([(0, 0, 0)], dtype=np.float32))
            write_binary_pcd(second, np.asarray([(0, 0, 0)], dtype=np.float32))
            result = runner.run(
                algorithms.default_algorithm(), [first, second], "map",
                [MapTransform("a", True), MapTransform("b", False, (1, 0, 0))], output,
            )
            self.assertEqual(result["point_count"], 2)
            imported = algorithms.import_algorithm(
                Path(__file__).resolve().parent.parent / "examples" / "map_fusion_plugin_example.py",
                runner,
            )
            self.assertEqual(imported.algorithm_id, "example_concat")
            self.assertTrue(Path(imported.script_path).is_file())

    def test_epgeneral_multi_map_fusion_is_selectable_and_refines_coarse_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            algorithms = MapFusionRepository(root / "algorithms.json", root / "assets")
            algorithm = algorithms.algorithm("epgeneral_multi_map_fusion")
            self.assertIsNotNone(algorithm)
            self.assertTrue(algorithm.enabled)

            target = np.asarray([
                [
                    0.123 + x * 1.3 + y * 0.02,
                    0.234 + y * 0.9 + z * 0.03,
                    0.345 + z * 1.1 + x * 0.01,
                ]
                for x in range(4) for y in range(5) for z in range(5)
            ], dtype=np.float32)
            translation = np.asarray([0.2, -0.1, 0.05], dtype=np.float32)
            primary = root / "primary.pcd"
            secondary = root / "secondary.pcd"
            output = root / "fused.pcd"
            write_binary_pcd(primary, target)
            write_binary_pcd(secondary, target - translation)

            result = MapFusionRunner(timeout_seconds=10).run(
                algorithm, [primary, secondary], "map",
                [MapTransform("primary", True), MapTransform("secondary", False)], output,
            )
            self.assertEqual(result["point_count"], len(target))
            self.assertIn("ICP", result["message"])

    def test_schema_four_empty_map_and_atomic_fusion_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MapRepository(Path(directory) / "maps")
            empty = repository.create_empty("空地图")
            self.assertEqual(empty.creator_devices, ())
            payload = json.loads(
                (repository.root / empty.directory_name / "map.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["schema_version"], 5)
            self.assertEqual(payload["build_provenance"]["mode"], "empty")

            job_id = "fusion-job"
            job_dir = repository.write_fusion_job(job_id, {"job_id": job_id, "state": "running"})
            output = job_dir / "plugin-output.pcd"
            write_binary_pcd(output, np.asarray([(0, 0, 0), (1, 2, 3)], dtype=np.float32))
            provenance = MapBuildProvenance(
                MapBuildMode.FUSION, job_id, "source-a", ("source-a", "source-b"),
                (MapTransform("source-a", True), MapTransform("source-b", False)),
                "builtin_voxel_merge", "1.0.0", "hash",
                started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc),
            )
            fused = repository.commit_fusion_result(
                "融合地图", job_id, output,
                [MapCreatorDevice("UAV-1", "一号机", "UAV")], "map", provenance,
            )
            self.assertEqual(fused.point_count, 2)
            self.assertEqual(fused.build_provenance.mode, MapBuildMode.FUSION)
            self.assertFalse(job_dir.exists())

    def test_corrupt_algorithm_registry_falls_back_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "algorithms.json"
            registry.write_text("{broken", encoding="utf-8")
            repository = MapFusionRepository(registry, root / "assets")
            self.assertTrue(repository.read_only)
            self.assertEqual(repository.default_algorithm().algorithm_id, "builtin_voxel_merge")
            with self.assertRaises(Exception):
                repository.update("builtin_voxel_merge", default_options={"voxel_size_m": 0.2})
            self.assertEqual(registry.read_text(encoding="utf-8"), "{broken")

    def test_interrupted_multi_job_can_be_discovered_and_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MapRepository(root / "maps")
            fusion = MapFusionRepository(root / "algorithms.json", root / "assets")
            creators = (
                MapCreatorDevice("UAV-1", "一号机", "UAV"),
                MapCreatorDevice("UGV-2", "二号车", "UGV"),
            )
            definition = repository.create("恢复联合地图", creators)
            job_id = "recover-job"
            started = datetime.now(timezone.utc).isoformat()
            payload = {
                "schema_version": 1, "protocol_id": "ccs-map-stream-v1",
                "map_id": definition.map_id, "job_id": job_id,
                "primary_device_id": "UAV-1", "state": "interrupted",
                "message": "test", "algorithm_id": "builtin_voxel_merge",
                "started_at": started, "updated_at": started,
                "devices": ["UAV-1", "UGV-2"], "excluded_device_ids": [],
                "transforms": [
                    {"source_id": "UAV-1", "is_primary": True,
                     "translation_m": [0, 0, 0], "rotation_rpy_deg": [0, 0, 0]},
                    {"source_id": "UGV-2", "is_primary": False,
                     "translation_m": [2, 0, 0], "rotation_rpy_deg": [0, 0, 0]},
                ],
            }
            for device_id in ("UAV-1", "UGV-2"):
                repository.write_mapping_job_checkpoint(
                    definition.map_id, job_id, payload, device_id,
                    np.asarray([(0, 0, 0)], dtype=np.float32), (),
                )
            self.assertEqual(repository.interrupted_mapping_jobs(definition.map_id)[0]["job_id"], job_id)
            service = MapBuildingService(load_map_building_config(), repository, fusion)
            saved = service.save_interrupted_job(definition.map_id, job_id)
            self.assertEqual(saved.point_count, 2)
            self.assertEqual(saved.build_provenance.mode, MapBuildMode.MULTI)

    def test_multi_device_start_extensions_and_ack_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(load_map_building_config(), bind_host="127.0.0.1")
            fusion = MapFusionRepository(Path(directory) / "algorithms.json", Path(directory) / "assets")
            repository = MapRepository(Path(directory) / "maps")
            creators = (
                MapCreatorDevice("UAV-1", "一号机", "UAV"),
                MapCreatorDevice("UGV-2", "二号车", "UGV"),
            )
            definition = repository.create("联合地图", creators)
            devices = [
                DeviceSnapshot("UAV-1", "一号机", "UAV", ip_address="127.0.0.1"),
                DeviceSnapshot("UGV-2", "二号车", "UGV", ip_address="127.0.0.2"),
            ]
            service = MapBuildingService(config, repository, fusion)
            capture = _CaptureSocket()
            service._socket = capture
            service.available = True
            job_id = service.start_job(
                definition, devices, "UAV-1",
                [MapTransform("UAV-1", True), MapTransform("UGV-2", False, (1, 0, 0))],
            )
            protocol = MapBuildingProtocol(config)
            starts = [protocol.decode(item[0]) for item in capture.sent]
            self.assertEqual(len(starts), 2)
            self.assertTrue(all(item.payload["job_id"] == job_id for item in starts))
            self.assertEqual({item.payload["role"] for item in starts}, {"primary", "secondary"})
            self.assertEqual(service.current_job_snapshot.state, "negotiating")
            for start, device in zip(starts, devices):
                ack = MapBuildingEnvelope(
                    definition.map_id, device.device_id, start.session_id, "command_ack", 1,
                    time.time_ns(), {"request_id": start.payload["request_id"],
                    "command": "start_mapping", "accepted": True},
                )
                service._handle_datagram(protocol.encode(ack), device.ip_address)
            self.assertEqual(service.current_job_snapshot.state, "mapping")
            service.interrupt_mapping("test complete")


if __name__ == "__main__":
    unittest.main()
