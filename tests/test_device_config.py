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
    def test_schema_five_migrates_active_map_and_retired_cards(self):
        self.path.write_text(json.dumps({
            "schema_version": 5,
            "devices": [{
                "device_id": "UGV_001", "device_name": "Scout", "device_type": "UGV",
                "ip_address": "127.0.0.1",
                "status_cards": ["fastlio2", "octomap_mapping", "occupancy_grid_mapping"],
                "relocalization_profile": "scout_mini", "map_bindings": [],
            }],
        }), encoding="utf-8")
        profiles = self.repository.load()
        self.assertEqual(profiles[0].status_card_ids, ("fastlio2",))
        self.repository.set_active_map("UGV_001", "map-1")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 7)
        self.assertEqual(saved["devices"][0]["active_map_id"], "map-1")

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
        self.assertEqual(payload["schema_version"], 7)
        self.assertEqual(payload["devices"][0]["srt_port"], 9000)
        self.assertEqual(payload["devices"][0]["srt_latency_ms"], 120)
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

    def test_mdns_address_is_accepted_normalized_and_persisted(self):
        self.repository.load()
        profiles = self.repository.create(
            DeviceProfile("UGV-099", "mDNS vehicle", "UGV", "NRC17.LOCAL.")
        )
        created = next(item for item in profiles if item.device_id == "UGV-099")
        self.assertEqual(created.ip_address, "nrc17.local")
        reloaded = DeviceConfigRepository(self.path).load()
        self.assertEqual(
            next(item for item in reloaded if item.device_id == "UGV-099").ip_address,
            "nrc17.local",
        )

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
        self.assertEqual(migrated["schema_version"], 7)
        selected = ("fastlio2", "mapping_mode")
        updated = self.repository.update_status_cards("uav-001", selected)
        self.assertEqual(updated[0].status_card_ids, selected)
        reloaded = DeviceConfigRepository(self.path).load()
        self.assertEqual(reloaded[0].status_card_ids, selected)

    def test_schema_six_infers_battery_profile_and_schema_seven_keeps_independent_value(self):
        legacy = {
            "schema_version": 6,
            "devices": [{
                "device_id": "UGV-001", "device_name": "Scout", "device_type": "UGV",
                "ip_address": "127.0.0.1", "relocalization_profile": "scout_mini",
            }],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        migrated = self.repository.load()[0]
        self.assertEqual(migrated.battery_profile, "scout_mini")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["schema_version"], 7)
        self.assertEqual(stored["devices"][0]["battery_profile"], "scout_mini")
        stored["devices"][0]["battery_profile"] = "wheeltec_r550p"
        self.path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(DeviceConfigRepository(self.path).load()[0].battery_profile, "wheeltec_r550p")

    def test_schema_three_migrates_srt_defaults_and_schema_four_persists_values(self):
        legacy = {
            "schema_version": 3,
            "devices": [{
                "device_id": "UAV-001", "device_name": "Legacy", "device_type": "UAV",
                "ip_address": "127.0.0.1", "status_cards": None,
            }],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        profiles = self.repository.load()
        self.assertEqual((profiles[0].srt_port, profiles[0].srt_latency_ms), (9000, 120))
        migrated = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], 7)
        custom = DeviceProfile("UGV-900", "Custom", "UGV", "127.0.0.2",
                               srt_port=19000, srt_latency_ms=350)
        self.repository.create(custom)
        reloaded = DeviceConfigRepository(self.path).load()
        saved = next(item for item in reloaded if item.device_id == "UGV-900")
        self.assertEqual((saved.srt_port, saved.srt_latency_ms), (19000, 350))

    def test_schema_two_migrates_srt_defaults(self):
        legacy = {
            "schema_version": 2,
            "devices": [{
                "device_id": "AMR-002", "device_name": "Legacy 2", "device_type": "AMR",
                "ip_address": "127.0.0.4", "status_cards": [],
            }],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        profile = self.repository.load()[0]
        self.assertEqual((profile.srt_port, profile.srt_latency_ms), (9000, 120))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schema_version"], 7)

    def test_srt_port_and_latency_are_validated(self):
        self.repository.load()
        with self.assertRaises(ValueError):
            self.repository.create(DeviceProfile("UAV-900", "Bad", "UAV", "127.0.0.2", srt_port=0))
        with self.assertRaises(ValueError):
            self.repository.create(DeviceProfile("UAV-901", "Bad", "UAV", "127.0.0.3", srt_latency_ms=19))

    def test_unknown_or_duplicate_status_card_is_rejected(self):
        self.repository.load()
        with self.assertRaises(ValueError):
            self.repository.update_status_cards("UGV-042", ("unknown",))
        with self.assertRaises(ValueError):
            self.repository.update_status_cards("UGV-042", ("fastlio2", "fastlio2"))


if __name__ == "__main__":
    unittest.main()
