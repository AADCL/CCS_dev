import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    from ccs_monitor.map_repository import DuplicateMapNameError, MapRepository, MapRepositoryError
    from ccs_monitor.models import MapBuildingResultMetadata, MapCreatorDevice, MapStatus
    from tests.test_point_cloud import write_ascii_pcd
    from tests.test_pgm_map import write_map_yaml


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class MapRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "map_server"
        self.repository = MapRepository(self.root)
        self.device = MapCreatorDevice("UAV-001", "测绘无人机", "UAV")
        self.created_at = datetime(2026, 8, 3, 4, 5, 6, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_import_rename_export_and_trash(self):
        definition = self.repository.create("A 区地图", [self.device], now=self.created_at)
        self.assertEqual(definition.status, MapStatus.WAITING_FOR_PCD)
        self.assertTrue((self.root / "A_区地图_20260803_040506" / "map.json").is_file())

        source = Path(self.temp_dir.name) / "source.pcd"
        write_ascii_pcd(source, [(-1, -2, 0), (3, 4, 2)])
        ready = self.repository.import_pcd(definition.map_id, source)
        self.assertEqual(ready.status, MapStatus.READY)
        self.assertEqual(ready.point_count, 2)
        self.assertEqual(ready.bounds.width, 4)

        renamed = self.repository.rename(definition.map_id, "B 区地图")
        self.assertEqual(renamed.name, "B 区地图")
        self.assertTrue(renamed.directory_name.startswith("B_区地图_20260803_040506"))
        with self.assertRaises(DuplicateMapNameError):
            self.repository.create("B 区地图", [self.device])

        archive_path = Path(self.temp_dir.name) / "map.zip"
        self.repository.export_zip(definition.map_id, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {"map.json", "map.pcd"})

        trash_path = self.repository.delete(definition.map_id)
        self.assertTrue(trash_path.is_dir())
        self.assertEqual(self.repository.maps(), [])

    def test_corrupt_directory_is_isolated_as_error_map(self):
        broken = self.root / "broken_20260803_000000"
        broken.mkdir()
        (broken / "map.json").write_text("{broken", encoding="utf-8")
        maps = self.repository.load_all()
        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0].status, MapStatus.ERROR)

    def test_imports_pgm_and_exports_all_available_layers(self):
        definition = self.repository.create("栅格地图", [self.device], now=self.created_at)
        source = Path(self.temp_dir.name) / "source.pgm"
        source.write_bytes(b"P5\n2 2\n255\n" + bytes((0, 100, 200, 255)))
        yaml_path = Path(self.temp_dir.name) / "source.yaml"
        write_map_yaml(yaml_path)
        updated = self.repository.import_pgm(definition.map_id, yaml_path)
        self.assertEqual(updated.status, MapStatus.READY)
        self.assertEqual(updated.pgm.image_width, 2)
        stored_yaml, stored_image = self.repository.pgm_paths(definition.map_id)
        self.assertTrue(stored_yaml.is_file())
        self.assertTrue(stored_image.is_file())
        archive_path = Path(self.temp_dir.name) / "grid.zip"
        self.repository.export_zip(definition.map_id, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {"map.json", "map.yaml", "map.pgm"})

    def test_invalid_pgm_metadata_isolated_as_error_map(self):
        definition = self.repository.create("异常栅格", [self.device], now=self.created_at)
        metadata_path = self.root / definition.directory_name / "map.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["schema_version"] = 2
        payload["status"] = "ready"
        payload["pgm"] = {
            "image_file": "map.pgm",
            "yaml_file": "map.yaml",
            "resolution": -0.05,
            "origin": [0, 0, 0],
            "image_width": 2,
            "image_height": 2,
            "negate": False,
            "occupied_thresh": 0.65,
            "free_thresh": 0.2,
        }
        metadata_path.write_text(json.dumps(payload), encoding="utf-8")
        (metadata_path.parent / "map.pgm").write_bytes(b"P5\n2 2\n255\n\x00\x00\x00\x00")
        (metadata_path.parent / "map.yaml").write_text("image: map.pgm\n", encoding="utf-8")
        loaded = self.repository.load_all()
        self.assertEqual(loaded[0].status, MapStatus.ERROR)
        self.assertIn("PGM", loaded[0].error_message)

    def test_failed_second_pgm_replace_restores_both_previous_files(self):
        definition = self.repository.create("回滚栅格", [self.device], now=self.created_at)
        first_dir = Path(self.temp_dir.name) / "first"
        first_dir.mkdir()
        (first_dir / "source.pgm").write_bytes(b"P5\n2 2\n255\n" + bytes((0, 1, 2, 3)))
        write_map_yaml(first_dir / "source.yaml")
        self.repository.import_pgm(definition.map_id, first_dir / "source.yaml")
        stored_yaml, stored_image = self.repository.pgm_paths(definition.map_id)
        old_yaml = stored_yaml.read_bytes()
        old_image = stored_image.read_bytes()

        second_dir = Path(self.temp_dir.name) / "second"
        second_dir.mkdir()
        (second_dir / "source.pgm").write_bytes(b"P5\n2 2\n255\n" + bytes((9, 8, 7, 6)))
        write_map_yaml(second_dir / "source.yaml")
        real_replace = os.replace

        def fail_yaml_install(source, target):
            if Path(source).name == ".map.importing.yaml":
                raise OSError("simulated second-file failure")
            return real_replace(source, target)

        with patch("ccs_monitor.map_repository.os.replace", side_effect=fail_yaml_install):
            with self.assertRaises(MapRepositoryError):
                self.repository.import_pgm(definition.map_id, second_dir / "source.yaml")

        self.assertEqual(stored_yaml.read_bytes(), old_yaml)
        self.assertEqual(stored_image.read_bytes(), old_image)

    def test_schema_three_mapping_checkpoint_commit_and_export(self):
        import numpy as np
        from ccs_monitor.map_building import write_binary_pcd

        definition = self.repository.create("实时建图", [self.device], now=self.created_at)
        session_id = "session-001"
        points = np.asarray([(0, 0, 0), (1, 2, 3)], dtype=np.float32)
        payload = {
            "schema_version": 1, "protocol_id": "ccs-map-stream-v1",
            "map_id": definition.map_id, "device_id": self.device.device_id,
            "session_id": session_id, "state": "interrupted", "message": "test",
            "started_at": self.created_at.isoformat(), "ended_at": self.created_at.isoformat(),
            "voxel_size_m": 0.1, "complete_frames": 1, "dropped_frames": 0,
            "received_points": 2, "fused_points": 2,
        }
        self.repository.write_mapping_checkpoint(
            definition.map_id, session_id, payload, points,
            [(1, 0, 0, 0, 0, 0, 0, 1)],
        )
        self.assertEqual(self.repository.interrupted_sessions(definition.map_id)[0]["session_id"], session_id)
        metadata = MapBuildingResultMetadata(
            session_id, self.device.device_id, self.created_at, self.created_at,
            "ccs-map-stream-v1", 0.1, 1, 0, 2, 2,
        )
        committed = self.repository.commit_mapping_result(definition.map_id, session_id, metadata)
        self.assertEqual(committed.point_count, 2)
        self.assertEqual(committed.trajectory_path, "trajectory.csv")
        stored = json.loads((self.root / committed.directory_name / "map.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["schema_version"], 3)
        archive_path = Path(self.temp_dir.name) / "mapping.zip"
        self.repository.export_zip(definition.map_id, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIn("trajectory.csv", archive.namelist())


if __name__ == "__main__":
    unittest.main()
