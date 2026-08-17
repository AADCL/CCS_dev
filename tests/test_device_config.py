import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ccs_monitor.device_config import (
    DeviceConfigError,
    DeviceConfigRepository,
    DuplicateDeviceIdError,
)
from ccs_monitor.models import DEFAULT_DEVICE_STATUS_CARDS, DeviceAvailability, DeviceProfile


class DeviceConfigRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "devices.json"
        self.repository = DeviceConfigRepository(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_config_creates_defaults(self):
        profiles = self.repository.load()
        self.assertEqual(len(profiles), 6)
        self.assertTrue(self.path.exists())
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 3)
        self.assertIsNone(payload["devices"][0]["status_cards"])

    def test_create_and_delete_are_persisted(self):
        self.repository.load()
        profile = DeviceProfile(
            "ugv-099",
            "New vehicle",
            "ugv",
            "127.0.0.1",
            DeviceAvailability.AVAILABLE,
            datetime.now(timezone.utc),
        )
        profiles = self.repository.create(profile)
        self.assertTrue(any(item.device_id == "UGV-099" for item in profiles))
        reloaded = DeviceConfigRepository(self.path).load()
        self.assertTrue(any(item.device_id == "UGV-099" for item in reloaded))
        remaining = self.repository.delete({"ugv-099"})
        self.assertFalse(any(item.device_id == "UGV-099" for item in remaining))

    def test_duplicate_id_is_case_insensitive(self):
        self.repository.load()
        with self.assertRaises(DuplicateDeviceIdError):
            self.repository.create(DeviceProfile("ugv-042", "Duplicate", "UGV", "127.0.0.1"))

    def test_invalid_ip_is_rejected(self):
        self.repository.load()
        with self.assertRaises(ValueError):
            self.repository.create(DeviceProfile("UGV-099", "Invalid", "UGV", "not-an-ip"))

    def test_corrupt_config_is_read_only_and_not_overwritten(self):
        original = "{ invalid json"
        self.path.write_text(original, encoding="utf-8")
        profiles = self.repository.load()
        self.assertEqual(profiles, [])
        self.assertTrue(self.repository.read_only)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        with self.assertRaises(DeviceConfigError):
            self.repository.delete({"UGV-001"})

    def test_schema_one_is_migrated_and_status_cards_are_persisted(self):
        legacy = {
            "schema_version": 1,
            "devices": [{
                "device_id": "UAV-001",
                "device_name": "Legacy",
                "device_type": "UAV",
                "ip_address": "127.0.0.1",
            }],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        profiles = self.repository.load()
        self.assertEqual(profiles[0].status_card_ids, DEFAULT_DEVICE_STATUS_CARDS)
        migrated = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], 3)
        selected = ("fastlio2", "mapping_mode")
        updated = self.repository.update_status_cards("uav-001", selected)
        self.assertEqual(updated[0].status_card_ids, selected)
        reloaded = DeviceConfigRepository(self.path).load()
        self.assertEqual(reloaded[0].status_card_ids, selected)

    def test_unknown_or_duplicate_status_card_is_rejected(self):
        self.repository.load()
        with self.assertRaises(ValueError):
            self.repository.update_status_cards("UGV-042", ("unknown",))
        with self.assertRaises(ValueError):
            self.repository.update_status_cards("UGV-042", ("fastlio2", "fastlio2"))


if __name__ == "__main__":
    unittest.main()
