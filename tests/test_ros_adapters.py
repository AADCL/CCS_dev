import unittest

from ccs_monitor.models import ConnectionStatus, LocalizationStatus, TaskStatus
from ccs_monitor.ros_adapters import Ros1DeviceAdapter, Ros2DeviceAdapter


class Message:
    device_id = "ROS-01"
    device_name = "ROS device"
    device_type = "ugv"
    battery_percent = 88
    localization_status = "fixed"
    task_status = "executing"
    connection_status = "online"
    position_x = 12.5
    position_y = -4.0
    frame_id = "factory_map"


class RosAdapterTests(unittest.TestCase):
    def test_both_adapters_share_canonical_model(self):
        for adapter in (Ros1DeviceAdapter(), Ros2DeviceAdapter()):
            snapshot = adapter.convert(Message())
            self.assertEqual(snapshot.device_type, "UGV")
            self.assertEqual(snapshot.localization_status, LocalizationStatus.FIXED)
            self.assertEqual(snapshot.task_status, TaskStatus.EXECUTING)
            self.assertEqual(snapshot.connection_status, ConnectionStatus.ONLINE)
            self.assertTrue(snapshot.has_position)
            self.assertEqual(snapshot.frame_id, "factory_map")

    def test_missing_fields_have_safe_defaults(self):
        snapshot = Ros2DeviceAdapter().convert(object())
        self.assertEqual(snapshot.device_id, "unknown")
        self.assertIsNone(snapshot.battery_percent)
        self.assertFalse(snapshot.has_position)

    def test_partial_position_is_ignored(self):
        class PartialMessage:
            device_id = "ROS-02"
            device_name = "Partial"
            position_x = 2.0

        snapshot = Ros2DeviceAdapter().convert(PartialMessage())
        self.assertIsNone(snapshot.position_x)
        self.assertIsNone(snapshot.position_y)



if __name__ == "__main__":
    unittest.main()
