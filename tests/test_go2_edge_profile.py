import re
import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edge_side_pkg" / "EPQRD_go2_bridge"
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "go2_edu" / "config"


class Go2EdgeProfileTests(unittest.TestCase):
    def test_package_identity_and_external_sdk_dependency(self):
        package = ElementTree.parse(PACKAGE / "package.xml").getroot()
        self.assertEqual(package.findtext("name"), "epqrd_go2_bridge")
        self.assertEqual(package.findtext("version"), "0.1.0")
        cmake = (PACKAGE / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("find_package(unitree_sdk2 REQUIRED)", cmake)
        self.assertIn("CMAKE_CXX_STANDARD 14", cmake)

    def test_device_and_prefixed_topics_are_consistent(self):
        device = yaml.safe_load((PROFILE / "device.yaml").read_text(encoding="utf-8"))["device"]
        bridge = yaml.safe_load((PROFILE / "go2.yaml").read_text(encoding="utf-8"))
        mqtt = yaml.safe_load((PROFILE / "mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(device["id"], "QRD_001")
        self.assertRegex(device["id"], r"^[A-Za-z][A-Za-z0-9_]*$")
        self.assertEqual(bridge["topic_prefix"], "/qrd")
        self.assertEqual(mqtt["ros"]["state"]["topic"], "/qrd/QRD_001/link/sdk")
        sources = {item["name"]: item["source"]["topic"] for item in telemetry["descriptors"]}
        self.assertEqual(sources["global_pose"], "/qrd/QRD_001/odometry")
        self.assertEqual(sources["imu"], "/qrd/QRD_001/imu")

    def test_wire_protocols_remain_unchanged(self):
        mqtt = yaml.safe_load((PROFILE / "mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(mqtt["mqtt"]["topics"]["status"], "mqtav/{device_id}/status")
        self.assertEqual(telemetry["protocol_id"], "ccs-udp-telemetry-v1")

    def test_source_contains_read_only_dds_topics_and_standard_ros_publishers(self):
        source = (PACKAGE / "src" / "go2_bridge_node.cpp").read_text(encoding="utf-8")
        self.assertIn("rt/lowstate", source)
        self.assertIn("rt/sportmodestate", source)
        self.assertNotIn("ChannelPublisher", source)
        for message_type in ("BatteryState", "Imu", "Odometry", "DiagnosticArray"):
            self.assertIn(message_type, source)


if __name__ == "__main__":
    unittest.main()
