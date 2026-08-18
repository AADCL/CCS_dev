from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.data_source import SimulatedDeviceSource
from ccs_monitor.device_types import DeviceTypeConfigError, DeviceTypeTemplateRepository
from ccs_monitor.models import DeviceProfile, DeviceTypeTemplate, MapMarkerShape


class DeviceTypeTemplateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repository = DeviceTypeTemplateRepository(root / "device_types.json", root / "assets")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_config_creates_defaults(self) -> None:
        templates = self.repository.load()
        self.assertEqual({item.type_id for item in templates}, {"UGV", "UAV", "AMR", "USV"})
        self.assertEqual(json.loads(self.repository.path.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_create_update_and_delete_unreferenced_template(self) -> None:
        self.repository.load()
        created = self.repository.create(DeviceTypeTemplate(
            "ROVER_2", "重型平台", map_marker_shape=MapMarkerShape.CUBE,
            default_status_card_ids=("livox_driver",),
        ))
        self.assertEqual(created.type_id, "ROVER_2")
        updated = self.repository.update(DeviceTypeTemplate(
            "ROVER_2", "重型巡检平台", map_marker_shape=MapMarkerShape.ARROW,
            default_status_card_ids=(),
        ))
        self.assertEqual(updated.map_marker_shape, MapMarkerShape.ARROW)
        self.repository.delete("ROVER_2")
        self.assertIsNone(self.repository.get("ROVER_2"))

    def test_referenced_template_cannot_be_deleted(self) -> None:
        self.repository.referenced_type_ids = lambda: {"UAV"}
        self.repository.load()
        with self.assertRaises(DeviceTypeConfigError):
            self.repository.delete("uav")

    def test_invalid_config_is_read_only_and_not_overwritten(self) -> None:
        self.repository.path.parent.mkdir(parents=True, exist_ok=True)
        self.repository.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.repository.load(), [])
        self.assertTrue(self.repository.read_only)
        self.assertEqual(self.repository.path.read_text(encoding="utf-8"), "{broken")

    def test_invalid_ids_shapes_and_cards_are_rejected(self) -> None:
        self.repository.load()
        with self.assertRaises(ValueError):
            self.repository.create(DeviceTypeTemplate("x", "短 ID"))
        with self.assertRaises(ValueError):
            self.repository.create(DeviceTypeTemplate("VALID", "未知卡片", default_status_card_ids=("missing",)))

    def test_icon_is_copied_and_trashed_with_template(self) -> None:
        self.repository.load()
        source = Path(self.temporary.name) / "icon.png"
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        image.fill(QColor("#1478c9"))
        self.assertTrue(image.save(str(source)))
        created = self.repository.create(DeviceTypeTemplate("TEST_ICON", "图标测试"), source)
        copied = Path(created.icon_path)
        self.assertTrue(copied.exists())
        self.assertNotEqual(copied, source)
        stored = json.loads(self.repository.path.read_text(encoding="utf-8"))
        stored_icon = next(
            item["icon_path"] for item in stored["device_types"] if item["type_id"] == "TEST_ICON"
        )
        self.assertFalse(Path(stored_icon).is_absolute())
        self.repository.delete(created.type_id)
        self.assertFalse(copied.exists())
        self.assertTrue(any((self.repository.asset_dir / ".trash").iterdir()))

    def test_icon_survives_installation_directory_move(self) -> None:
        root = Path(self.temporary.name)
        install = root / "install"
        repository = DeviceTypeTemplateRepository(
            install / "config" / "device_types.json",
            install / "data" / "device_type_assets",
        )
        repository.load()
        source = root / "portable.png"
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        image.fill(QColor("#1478c9"))
        self.assertTrue(image.save(str(source)))
        repository.create(DeviceTypeTemplate("PORTABLE", "可迁移图标"), source)
        relocated = root / "relocated"
        shutil.move(str(install), str(relocated))
        loaded = DeviceTypeTemplateRepository(
            relocated / "config" / "device_types.json",
            relocated / "data" / "device_type_assets",
        )
        templates = loaded.load()
        portable = next(item for item in templates if item.type_id == "PORTABLE")
        self.assertTrue(Path(portable.icon_path).is_file())
        Path(portable.icon_path).resolve().relative_to(relocated.resolve())

    def test_damaged_icon_is_rejected(self) -> None:
        self.repository.load()
        source = Path(self.temporary.name) / "broken.png"
        source.write_bytes(b"not an image")
        with self.assertRaises(DeviceTypeConfigError):
            self.repository.create(DeviceTypeTemplate("BAD_ICON", "损坏图标"), source)


class DeviceInheritanceTests(unittest.TestCase):
    def test_schema_three_null_means_inherit_and_empty_array_is_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            path.write_text(json.dumps({
                "schema_version": 3,
                "devices": [
                    {"device_id": "A-1", "device_name": "A", "device_type": "UAV", "ip_address": "127.0.0.1", "status_cards": None},
                    {"device_id": "B-1", "device_name": "B", "device_type": "UAV", "ip_address": "127.0.0.2", "status_cards": []},
                ],
            }), encoding="utf-8")
            profiles = DeviceConfigRepository(path).load()
            self.assertIsNone(profiles[0].status_card_ids)
            self.assertEqual(profiles[1].status_card_ids, ())

    def test_template_updates_only_inherited_device_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            type_repository = DeviceTypeTemplateRepository(root / "types.json", root / "assets")
            device_repository = DeviceConfigRepository(root / "devices.json")
            source = SimulatedDeviceSource(device_repository, type_repository)
            inherited = source.create_device(DeviceProfile(
                "NEW-1", "继承设备", "UAV", "127.0.0.10", status_card_ids=None
            ))
            source.create_device(DeviceProfile(
                "NEW-2", "覆盖设备", "UAV", "127.0.0.11", status_card_ids=("fastlio2",)
            ))
            template = source.device_type_template("UAV")
            source.update_device_type_template(DeviceTypeTemplate(
                template.type_id, template.display_name, template.icon_path,
                MapMarkerShape.ARROW, ("mapping_mode",),
            ))
            self.assertTrue(inherited.status_cards_inherited)
            self.assertEqual(source.device("NEW-1").status_card_ids, ("mapping_mode",))
            self.assertEqual(source.device("NEW-2").status_card_ids, ("fastlio2",))
