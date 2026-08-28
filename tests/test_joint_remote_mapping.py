import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ccs_monitor.map_building import MapBuildingEnvelope, write_binary_pcd
from ccs_monitor.map_building_config import load_map_building_config
from ccs_monitor.map_building_services import RemoteMappingJobCoordinator
from ccs_monitor.map_building_v2 import (
    RemoteMappingArtifactResult, RemoteMappingSnapshot, ValidatedArtifact,
)
from ccs_monitor.map_fusion import MapFusionRepository, MapFusionRunner
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.models import (
    DeviceSnapshot, MapBuildingResultMetadata, MapCreatorDevice, MapStatus,
    MapTransform,
)
from ccs_monitor.pages.map_page import MappingSetupDialog, device_display_color


class JointRemoteMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = MapRepository(Path(self.temp.name) / "maps")
        self.definition = self.repository.create(
            "Joint", (
                MapCreatorDevice("UGV_001", "Scout", "UGV"),
                MapCreatorDevice("UGV_003", "WheelTech", "UGV"),
            )
        )
        self.devices = (
            DeviceSnapshot("UGV_001", "Scout", "UGV", ip_address="127.0.0.1"),
            DeviceSnapshot("UGV_003", "WheelTech", "UGV", ip_address="127.0.0.2"),
        )
        self.transforms = (
            MapTransform("UGV_001", True),
            MapTransform("UGV_003", False, (0.0, -1.2, 0.0)),
        )

    def test_dialog_requires_two_devices_and_rejects_roll_pitch(self):
        algorithms = MapFusionRepository(
            Path(self.temp.name) / "fusion.json", Path(self.temp.name) / "algorithms"
        ).algorithms(enabled_only=True)
        dialog = MappingSetupDialog("multi", list(self.devices), algorithms)
        dialog.name_input.setText("Joint")
        first = dialog.device_list.item(0)
        first.setCheckState(Qt.CheckState.Checked)
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.device_list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.start_button.isEnabled())
        secondary_row = next(
            row for row in range(dialog.transform_table.rowCount())
            if dialog.transform_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "UGV_003"
        )
        dialog.transform_table.cellWidget(secondary_row, 4).setValue(1.0)
        with self.assertRaisesRegex(ValueError, "Roll/Pitch"):
            dialog.validate_joint_transforms()

    def test_joint_prepare_fields_and_capability_gate(self):
        sent = []
        config = load_map_building_config()
        fusion_repository = MapFusionRepository(
            Path(self.temp.name) / "fusion.json", Path(self.temp.name) / "algorithms"
        )
        coordinator = RemoteMappingJobCoordinator(
            config, self.repository, MapFusionRunner(),
            lambda raw, ip: sent.append((raw, ip)),
        )
        self.addCleanup(coordinator.shutdown)
        job_id = coordinator.prepare(
            self.definition, self.devices, "UGV_001", self.transforms,
            fusion_repository.default_algorithm(), lambda ip: ("127.0.0.10", 14562),
        )
        self.assertEqual(len(sent), 2)
        protocol = coordinator.coordinators["UGV_001"].protocol
        requests = [protocol.decode(raw) for raw, _ip in sent]
        self.assertEqual({item.payload["job_id"] for item in requests}, {job_id})
        self.assertEqual({item.payload["primary_device_id"] for item in requests}, {"UGV_001"})
        self.assertEqual({item.payload["role"] for item in requests}, {"primary", "secondary"})

        for sequence, (device, capability) in enumerate(zip(self.devices, ("0.12.0", "0.11.0")), 1):
            child = coordinator.coordinators[device.device_id]
            request = next(item for item in requests if item.device_id == device.device_id)
            child.handle(MapBuildingEnvelope(
                self.definition.map_id, device.device_id, child.session.session_id,
                "prepare_result", sequence, sequence,
                {"request_id": request.payload["request_id"], "accepted": True,
                 "checks": [{"name": "pointcloud", "available": True}],
                 "sample_window_seconds": 0.2,
                 "frame_id": config.mapping_frame_for(device.device_id),
                 "capability_version": capability}), device.ip_address)
        with self.assertRaisesRegex(RuntimeError, "0.12.0"):
            coordinator.begin()

    def test_device_color_is_stable_and_distinguishes_test_devices(self):
        self.assertEqual(device_display_color("UGV_001"), device_display_color("ugv_001"))
        self.assertNotEqual(device_display_color("UGV_001"), device_display_color("UGV_003"))

    def test_validated_device_artifacts_are_fused_and_committed_together(self):
        config = load_map_building_config()
        fusion_repository = MapFusionRepository(
            Path(self.temp.name) / "fusion.json", Path(self.temp.name) / "algorithms"
        )
        coordinator = RemoteMappingJobCoordinator(
            config, self.repository, MapFusionRunner(), lambda raw, ip: None,
        )
        coordinator.definition = self.definition
        coordinator.job_id = "joint-job"
        coordinator.primary_device_id = "UGV_001"
        coordinator.algorithm = fusion_repository.default_algorithm()
        coordinator.transforms = {item.source_id: item for item in self.transforms}

        map_id = self.definition.map_id

        class DummyCoordinator:
            def __init__(self, device_id):
                self.snapshot = RemoteMappingSnapshot(
                    map_id, device_id, f"{device_id}-session",
                    "completed", "成果已暂存", datetime.now(timezone.utc),
                    artifact_bytes_received=100, artifact_bytes_total=100,
                )

            def shutdown(self):
                pass

        coordinator.coordinators = {
            "UGV_001": DummyCoordinator("UGV_001"),
            "UGV_003": DummyCoordinator("UGV_003"),
        }
        now = datetime.now(timezone.utc)
        device_results = {}
        for index, device in enumerate(self.devices):
            root = Path(self.temp.name) / device.device_id
            root.mkdir()
            pcd = root / "map.pcd"
            write_binary_pcd(
                pcd, np.asarray(((0, 0, 0), (1, 1, index * 0.1)), dtype=np.float32)
            )
            pgm = root / "map.pgm"
            pgm.write_bytes(b"P5\n2 2\n255\n" + bytes((254, 0, 254, 205)))
            yaml_path = root / "map.yaml"
            yaml_path.write_text(yaml.safe_dump({
                "image": "map.pgm", "resolution": 0.5, "origin": [0.0, 0.0, 0.0],
                "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196,
            }), encoding="utf-8")
            artifact = ValidatedArtifact(
                root, pcd, pgm, yaml_path, "map", now, {}
            )
            metadata = MapBuildingResultMetadata(
                device.device_id + "-session", device.device_id, now, now,
                config.protocol_v2_id, config.voxel_size_m, 3, 0, 10, 2,
            )
            device_results[device.device_id] = RemoteMappingArtifactResult(
                self.definition.map_id, device.device_id, metadata.session_id,
                artifact, metadata,
            )
            coordinator.artifacts[device.device_id] = device_results[device.device_id]
        completed = []
        failures = []
        terminal_updates = []
        navigation_locks = []
        active_when_completed = []
        coordinator.updated.connect(terminal_updates.append)
        coordinator.navigation_locked.connect(navigation_locks.append)
        coordinator.completed.connect(
            lambda definition: (
                completed.append(definition), active_when_completed.append(coordinator.active)
            )
        )
        coordinator.failed.connect(failures.append)
        coordinator._fuse()
        self.assertEqual(failures, [])
        self.assertEqual(len(completed), 1)
        self.assertEqual(active_when_completed, [False])
        self.assertFalse(coordinator.active)
        self.assertIsNone(coordinator.snapshot)
        self.assertEqual(navigation_locks[-1], False)
        self.assertEqual(terminal_updates[-1].state, "completed")
        self.assertFalse(terminal_updates[-1].navigation_locked)
        result = self.repository.map_by_id(self.definition.map_id)
        self.assertEqual(result.status, MapStatus.READY)
        self.assertIsNotNone(result.pgm)
        self.assertEqual(result.build_provenance.primary_source_id, "UGV_001")
        self.assertEqual(result.pgm_fusion.job_id, "joint-job")

        degraded_definition = self.repository.create(
            "Degraded", self.definition.creator_devices
        )
        degraded = RemoteMappingJobCoordinator(
            config, self.repository, MapFusionRunner(), lambda raw, ip: None,
        )
        degraded.definition = degraded_definition
        degraded.job_id = "degraded-job"
        degraded.primary_device_id = "UGV_001"
        degraded.algorithm = fusion_repository.default_algorithm()
        degraded.transforms = {item.source_id: item for item in self.transforms}
        degraded.coordinators = {
            "UGV_001": DummyCoordinator("UGV_001"),
            "UGV_003": DummyCoordinator("UGV_003"),
        }
        degraded.excluded = {"UGV_003"}
        degraded.artifacts = {
            "UGV_001": RemoteMappingArtifactResult(
                degraded_definition.map_id, "UGV_001",
                device_results["UGV_001"].session_id,
                device_results["UGV_001"].artifact,
                device_results["UGV_001"].metadata,
            )
        }
        degraded_completed = []
        degraded.completed.connect(degraded_completed.append)
        degraded._fuse()
        self.assertEqual(len(degraded_completed), 1)
        degraded_result = self.repository.map_by_id(degraded_definition.map_id)
        self.assertEqual(degraded_result.status, MapStatus.READY)
        self.assertEqual(
            degraded_result.build_provenance.excluded_device_ids, ("UGV_003",)
        )


if __name__ == "__main__":
    unittest.main()
