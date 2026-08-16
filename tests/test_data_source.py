import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ccs_monitor.data_source import SimulatedDeviceSource, calculate_health
from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.models import (
    ConnectionStatus,
    DeviceAvailability,
    DeviceProfile,
    HealthStatus,
    LocalizationStatus,
)


class DeviceDataSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = DeviceConfigRepository(Path(self.temp_dir.name) / "devices.json")
        self.source = SimulatedDeviceSource(repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_delete_emit_merged_snapshot(self):
        profile = DeviceProfile(
            "UGV-099", "New device", "UGV", "127.0.0.1",
            DeviceAvailability.AVAILABLE, datetime.now(timezone.utc),
        )
        created = self.source.create_device(profile)
        self.assertEqual(created.ip_address, "127.0.0.1")
        self.assertEqual(created.connection_status, ConnectionStatus.ONLINE)
        self.assertTrue(self.source.logs(created.device_id))
        self.source.delete_devices({created.device_id})
        self.assertIsNone(self.source.device(created.device_id))

    def test_health_rules(self):
        self.assertEqual(
            calculate_health(ConnectionStatus.OFFLINE, LocalizationStatus.FIXED, 90),
            HealthStatus.ABNORMAL,
        )
        self.assertEqual(
            calculate_health(ConnectionStatus.ONLINE, LocalizationStatus.FIXED, 20),
            HealthStatus.ATTENTION,
        )
        self.assertEqual(
            calculate_health(ConnectionStatus.ONLINE, LocalizationStatus.FIXED, 90),
            HealthStatus.NORMAL,
        )

    def test_status_card_update_is_persisted_without_replacing_runtime_snapshot(self):
        before = self.source.device("UGV-042")
        self.source.update_device_status_cards("UGV-042", ("livox_driver", "mapping_mode"))
        after = self.source.device("UGV-042")
        self.assertEqual(after.status_card_ids, ("livox_driver", "mapping_mode"))
        self.assertEqual(after.battery_percent, before.battery_percent)
        reloaded = DeviceConfigRepository(self.source.repository.path).load()
        profile = next(item for item in reloaded if item.device_id == "UGV-042")
        self.assertEqual(profile.status_card_ids, ("livox_driver", "mapping_mode"))


if __name__ == "__main__":
    unittest.main()
