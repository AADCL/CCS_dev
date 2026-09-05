import math
import unittest
from datetime import datetime, timezone

from ccs_monitor.device_map_context import (
    resolve_device_map_context, resolve_local_odom_pose,
)
from ccs_monitor.models import (
    DeviceAvailability, DeviceMapBinding, DeviceProfile, DeviceTelemetrySnapshot,
    FrameTransform, PoseTelemetry,
)


class FakeSource:
    def __init__(self, profile):
        self.value = profile

    def profile(self, _device_id):
        return self.value


class DeviceMapContextTests(unittest.TestCase):
    def profile(self, active="map-1"):
        binding = DeviceMapBinding(
            "map-1", "map", "odom",
            FrameTransform(10, 20, 0, 0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)),
            datetime.now(timezone.utc), "vision_pose",
        )
        return DeviceProfile(
            "UGV_001", "Scout", "UGV", "127.0.0.1", DeviceAvailability.AVAILABLE,
            relocalization_profile="scout_mini", map_bindings=(binding,), active_map_id=active,
        )

    def test_bound_pose_and_persisted_success_are_resolved(self):
        telemetry = DeviceTelemetrySnapshot(
            "UGV_001", vision_pose=PoseTelemetry(1, 0, 0, 0, 0, 0),
        )
        context = resolve_device_map_context(FakeSource(self.profile()), None, telemetry, "UGV_001")
        self.assertEqual(context.localization_text, "重定位成功")
        self.assertAlmostEqual(context.map_pose.x, 10)
        self.assertAlmostEqual(context.map_pose.y, 21)
        self.assertAlmostEqual(context.map_pose.yaw, 90)

    def test_missing_active_map_and_stale_pose_do_not_fabricate_map_pose(self):
        telemetry = DeviceTelemetrySnapshot(
            "UGV_001", vision_pose=PoseTelemetry(1, 0, 0, 0, 0, 0, 2.1),
        )
        no_map = resolve_device_map_context(FakeSource(self.profile(None)), None, telemetry, "UGV_001")
        self.assertEqual(no_map.localization_text, "未知空间")
        self.assertIsNone(no_map.map_pose)
        stale = resolve_device_map_context(FakeSource(self.profile()), None, telemetry, "UGV_001")
        self.assertIsNone(stale.map_pose)

    def test_local_odom_pose_uses_profile_specific_field(self):
        vision = PoseTelemetry(1, 2, 3, 4, 5, 6)
        global_pose = PoseTelemetry(10, 20, 30, 40, 50, 60)
        telemetry = DeviceTelemetrySnapshot(
            "UGV_001", global_pose=global_pose, vision_pose=vision,
        )
        self.assertIs(
            resolve_local_odom_pose(FakeSource(self.profile()), telemetry, "UGV_001"),
            vision,
        )
        ground_air = DeviceProfile(
            "AGV_001", "Ground-Air", "AGV", "127.0.0.3",
            relocalization_profile="ground_air_agv",
        )
        self.assertIs(
            resolve_local_odom_pose(FakeSource(ground_air), telemetry, "AGV_001"),
            vision,
        )
        go2 = DeviceProfile(
            "QRD_001", "Go2", "QRD", "127.0.0.2",
            relocalization_profile="go2_edu",
        )
        self.assertIs(
            resolve_local_odom_pose(FakeSource(go2), telemetry, "QRD_001"),
            global_pose,
        )


if __name__ == "__main__":
    unittest.main()
