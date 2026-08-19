import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ccs_monitor.data_source import SimulatedDeviceSource, simulated_overview
from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.device_migration import DeviceMigrationError, DeviceReferenceMigrationCoordinator
from ccs_monitor.map_fusion import MapFusionRepository
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.models import DeviceProfile, MapCreatorDevice
from ccs_monitor.pages.home_page import HomePage
from ccs_monitor.pages.map_page import MappingSetupDialog
from ccs_monitor.system_status import (
    SubsystemId, SubsystemState, SystemRuntimeStatusStore,
)
from ccs_monitor.task_models import TaskExecutionSnapshot, TaskExecutionStatus
from ccs_monitor.task_repository import TaskRepository
from ccs_monitor.models import utc_now
from tests.test_point_cloud import write_ascii_pcd


class V015SystemAndMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.source = SimulatedDeviceSource(DeviceConfigRepository(root / "devices.json"))
        self.maps = MapRepository(root / "maps")
        self.tasks = TaskRepository(root / "tasks")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_home_runtime_cards_follow_central_status_store(self):
        store = SystemRuntimeStatusStore()
        page = HomePage(
            self.source, simulated_overview(), self.maps, self.tasks, store
        )
        self.assertEqual(len(page.runtime_cards), len(SubsystemId))
        status = store.update(
            SubsystemId.NTP, SubsystemState.HEALTHY,
            "NTP Server 运行中 · UDP 123 · stratum 1",
        )
        card = page.runtime_cards[SubsystemId.NTP]
        self.assertEqual(card.property("state"), "healthy")
        self.assertEqual(card.message.text(), status.message)
        page._apply_responsive_layout(800)
        page.close()

    def test_device_id_migrates_current_map_and_task_but_not_execution_snapshot(self):
        device = self.source.device("UGV-042")
        definition = self.maps.create(
            "Migration map",
            (MapCreatorDevice(device.device_id, device.device_name, device.device_type),),
        )
        source_pcd = Path(self.temp_dir.name) / "migration.pcd"
        write_ascii_pcd(source_pcd, ((0, 0, 0), (1, 1, 0)))
        definition = self.maps.import_pcd(definition.map_id, source_pcd)
        task = self.tasks.create("Migration task", definition, (device,))
        execution = TaskExecutionSnapshot(
            "execution-old", task.task_id, (device.device_id,),
            TaskExecutionStatus.COMPLETED, utc_now(),
        )
        self.tasks.create_execution(task.task_id, execution)
        original = self.source.profile(device.device_id)
        changed = DeviceProfile(
            "UGV-142", "Updated vehicle", original.device_type, "127.0.0.42",
            original.availability, original.last_tested_at, original.status_card_ids,
            original.srt_port, original.srt_latency_ms,
        )
        coordinator = DeviceReferenceMigrationCoordinator(
            self.source, self.maps, self.tasks
        )
        updated = coordinator.update_device(device.device_id, changed)
        self.assertEqual(updated.device_id, "UGV-142")
        self.assertIsNone(self.source.device("UGV-042"))
        self.assertEqual(
            self.maps.map_by_id(definition.map_id).creator_devices[0].device_id,
            "UGV-142",
        )
        migrated_task = self.tasks.task_by_id(task.task_id)
        self.assertEqual(migrated_task.subtasks[0].device_id, "UGV-142")
        self.assertEqual(migrated_task.subtasks[0].revision, 1)
        self.assertIsNone(migrated_task.subtasks[0].delivered_revision)
        archived = self.tasks.executions(task.task_id)
        self.assertEqual(archived[0].device_ids, ("UGV-042",))
        self.assertEqual(
            self.tasks.audit_events(task.task_id)[-1].event_type,
            "device_identity_migrated",
        )

    def test_single_mapping_hides_algorithm_selection(self):
        root = Path(self.temp_dir.name)
        algorithms = MapFusionRepository(
            root / "algorithms.json", root / "algorithm-assets"
        ).algorithms(enabled_only=True)
        single = MappingSetupDialog("single", self.source.snapshots(), algorithms)
        multi = MappingSetupDialog("multi", self.source.snapshots(), algorithms)
        self.assertIsNone(single.algorithm_combo)
        self.assertTrue(single.algorithm_id())
        self.assertIsNotNone(multi.algorithm_combo)
        single.close()
        multi.close()

    def test_active_device_blocks_id_change(self):
        original = self.source.profile("UGV-042")
        changed = DeviceProfile(
            "UGV-242", original.device_name, original.device_type, original.ip_address,
            original.availability, original.last_tested_at, original.status_card_ids,
            original.srt_port, original.srt_latency_ms,
        )
        coordinator = DeviceReferenceMigrationCoordinator(
            self.source, self.maps, self.tasks,
            mapping_service=SimpleNamespace(device_active=lambda _device_id: True),
        )
        with self.assertRaises(DeviceMigrationError):
            coordinator.update_device("UGV-042", changed)
        self.assertIsNotNone(self.source.device("UGV-042"))

    def test_failed_task_migration_restores_map_reference(self):
        device = self.source.device("UGV-042")
        definition = self.maps.create(
            "Rollback map",
            (MapCreatorDevice(device.device_id, device.device_name, device.device_type),),
        )
        original = self.source.profile(device.device_id)
        changed = DeviceProfile(
            "UGV-342", original.device_name, original.device_type, original.ip_address,
            original.availability, original.last_tested_at, original.status_card_ids,
            original.srt_port, original.srt_latency_ms,
        )
        coordinator = DeviceReferenceMigrationCoordinator(self.source, self.maps, self.tasks)
        with patch.object(self.tasks, "update_device_reference", side_effect=RuntimeError("write failed")):
            with self.assertRaises(DeviceMigrationError):
                coordinator.update_device(device.device_id, changed)
        restored = self.maps.map_by_id(definition.map_id)
        self.assertEqual(restored.creator_devices[0].device_id, "UGV-042")
        self.assertIsNotNone(self.source.device("UGV-042"))


if __name__ == "__main__":
    unittest.main()
