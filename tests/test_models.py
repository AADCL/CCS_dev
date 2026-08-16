import unittest
from datetime import datetime, timedelta, timezone

from ccs_monitor.models import (
    ConnectionStatus,
    DeviceSnapshot,
    LocalizationStatus,
    MapDefinition,
    SystemOverview,
    TaskExecutionSummary,
    TaskStatus,
)


class DeviceSnapshotTests(unittest.TestCase):
    def test_valid_snapshot(self):
        device = DeviceSnapshot("A-1", "Alpha", "UGV", 70, LocalizationStatus.FIXED, TaskStatus.STANDBY, ConnectionStatus.ONLINE)
        self.assertEqual(device.device_id, "A-1")
        self.assertFalse(device.is_stale)

    def test_invalid_battery_is_rejected(self):
        with self.assertRaises(ValueError):
            DeviceSnapshot("A-1", "Alpha", "UGV", 101)

    def test_stale_snapshot(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=20)
        device = DeviceSnapshot("A-1", "Alpha", "UGV", updated_at=old)
        self.assertTrue(device.is_stale)

    def test_position_requires_complete_pair(self):
        with self.assertRaises(ValueError):
            DeviceSnapshot("A-1", "Alpha", "UGV", position_x=3.0)

    def test_position_is_available_with_frame(self):
        device = DeviceSnapshot(
            "A-1", "Alpha", "UGV", position_x=3.0, position_y=-2.0, frame_id="map"
        )
        self.assertTrue(device.has_position)

    def test_position_without_frame_is_not_mappable(self):
        device = DeviceSnapshot("A-1", "Alpha", "UGV", position_x=3.0, position_y=-2.0)
        self.assertFalse(device.has_position)


if __name__ == "__main__":
    unittest.main()
