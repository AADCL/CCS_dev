import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
EDGE_ROOT = ROOT / "edge_side_pkg"
DEPLOYMENT = EDGE_ROOT / "deploy" / "go2_edu"
PROFILE = DEPLOYMENT / "config"


class Go2EdgeProfileTests(unittest.TestCase):
    def test_obsolete_bridge_is_absent(self):
        self.assertFalse((EDGE_ROOT / "EPQRD_go2_bridge").exists())
        self.assertFalse((EDGE_ROOT / "ros_udp_telemetry").exists())
        self.assertFalse((PROFILE / "go2.yaml").exists())
        for path in (
            DEPLOYMENT / "start_ccs_edge_dev.sh",
            DEPLOYMENT / "launch" / "go2_edu_bringup.launch",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("epqrd_go2_bridge", text)
            self.assertNotIn("go2_bridge", text)

    def test_profile_contains_all_shared_runtime_configs(self):
        required = {
            "device.yaml",
            "epgeneral_mqtav.yaml",
            "udp_telemetry.yaml",
            "video.yaml",
            "map_stream.yaml",
            "relocalization.yaml",
            "task_control.yaml",
        }
        self.assertTrue(required.issubset({path.name for path in PROFILE.iterdir()}))

    def test_mqtav_uses_livox_freshness_without_battery_source(self):
        mqtt = yaml.safe_load(
            (PROFILE / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        state = mqtt["ros"]["state"]
        self.assertEqual(state["topic"], "/livox/lidar")
        self.assertEqual(state["message_type"], "livox_ros_driver2/CustomMsg")
        self.assertTrue(state["connected_on_message"])
        self.assertEqual(set(state["mapping"].values()), {None})
        self.assertEqual(mqtt["ros"]["battery"], {"enabled": False})

    def test_udp_uses_native_livox_and_lio_topics(self):
        telemetry = yaml.safe_load(
            (PROFILE / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        sources = {
            item["name"]: item["source"].get("topic")
            for item in telemetry["descriptors"]
        }
        self.assertEqual(sources["global_pose"], "/lio/odometry")
        self.assertEqual(sources["imu"], "/livox/imu")
        self.assertEqual(sources["livox_pointcloud"], "/livox/lidar")
        self.assertNotIn("/qrd/", "\n".join(value or "" for value in sources.values()))

    def test_wire_protocols_remain_unchanged(self):
        mqtt = yaml.safe_load(
            (PROFILE / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load(
            (PROFILE / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            mqtt["mqtt"]["topics"]["status"], "mqtav/{device_id}/status")
        self.assertEqual(telemetry["protocol_id"], "ccs-udp-telemetry-v1")

    def test_bringup_uses_profile_configs(self):
        launch = ElementTree.parse(
            DEPLOYMENT / "launch" / "go2_edu_bringup.launch").getroot()
        includes = "\n".join(
            ElementTree.tostring(item, encoding="unicode")
            for item in launch.findall("include")
        )
        for name in (
            "device.yaml",
            "epgeneral_mqtav.yaml",
            "udp_telemetry.yaml",
            "video.yaml",
            "map_stream.yaml",
            "relocalization.yaml",
            "task_control.yaml",
        ):
            self.assertIn(name, includes)
        self.assertNotIn("/qrd/", includes)

    def test_start_script_passes_profile_configs(self):
        script = (DEPLOYMENT / "start_ccs_edge_dev.sh").read_text(
            encoding="utf-8")
        for name in (
            "device.yaml",
            "epgeneral_mqtav.yaml",
            "udp_telemetry.yaml",
            "video.yaml",
            "map_stream.yaml",
            "relocalization.yaml",
            "task_control.yaml",
        ):
            self.assertIn(name, script)
        for name in (
            "device.yaml",
            "epgeneral_mqtav.yaml",
            "udp_telemetry.yaml",
            "video.yaml",
            "map_stream.yaml",
            "relocalization.yaml",
        ):
            self.assertIn("${PROFILE_CONFIG_DIR}/" + name, script)
        self.assertNotIn("/qrd/", script)


if __name__ == "__main__":
    unittest.main()
