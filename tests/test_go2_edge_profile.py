import re
import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edge_side_pkg" / "EPQRD_go2_bridge"
DEPLOYMENT = ROOT / "edge_side_pkg" / "deploy" / "go2_edu"
PROFILE = DEPLOYMENT / "config"


class Go2EdgeProfileTests(unittest.TestCase):
    def test_package_identity_and_external_sdk_dependency(self):
        package = ElementTree.parse(PACKAGE / "package.xml").getroot()
        self.assertEqual(package.findtext("name"), "epqrd_go2_bridge")
        self.assertEqual(package.findtext("version"), "0.2.0")
        cmake = (PACKAGE / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("find_package(unitree_sdk2 REQUIRED)", cmake)
        self.assertIn("CMAKE_CXX_STANDARD 14", cmake)
        self.assertIn("generate_messages", cmake)
        self.assertIn("message_runtime", cmake)

    def test_device_and_prefixed_topics_are_consistent(self):
        device = yaml.safe_load((PROFILE / "device.yaml").read_text(encoding="utf-8"))["device"]
        bridge = yaml.safe_load((PROFILE / "go2.yaml").read_text(encoding="utf-8"))
        mqtt = yaml.safe_load((PROFILE / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(device["id"], "QRD_001")
        self.assertRegex(device["id"], r"^[A-Za-z][A-Za-z0-9_]*$")
        self.assertEqual(bridge["topic_prefix"], "/qrd")
        self.assertEqual(bridge["rates"]["low_state_hz"], 100.0)
        self.assertEqual(bridge["rates"]["sport_mode_hz"], 50.0)
        self.assertEqual(mqtt["ros"]["state"]["topic"], "/qrd/QRD_001/link/sdk")
        sources = {item["name"]: item["source"]["topic"] for item in telemetry["descriptors"]}
        self.assertEqual(sources["global_pose"], "/qrd/QRD_001/odometry")
        self.assertEqual(sources["imu"], "/qrd/QRD_001/imu")

    def test_wire_protocols_remain_unchanged(self):
        mqtt = yaml.safe_load((PROFILE / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(mqtt["mqtt"]["topics"]["status"], "mqtav/{device_id}/status")
        self.assertEqual(telemetry["protocol_id"], "ccs-udp-telemetry-v1")

    def test_bringup_uses_profile_configs_and_configurable_ground_station(self):
        launch = ElementTree.parse(DEPLOYMENT / "launch" / "go2_edu_bringup.launch").getroot()
        args = {item.attrib["name"]: item.attrib.get("default") for item in launch.findall("arg")}
        self.assertEqual(args["ground_station_ip"], "192.168.50.101")
        includes = "\n".join(ElementTree.tostring(item, encoding="unicode") for item in launch.findall("include"))
        self.assertIn("udp_telemetry.yaml", includes)
        self.assertIn("device.yaml", includes)
        self.assertIn("$(arg ground_station_ip)", includes)
        self.assertNotIn("192.168.151.100", includes)

    def test_start_script_passes_profile_configs_to_every_service(self):
        script = (DEPLOYMENT / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn('PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-}"', script)
        self.assertIn('PROFILE_CONFIG_DIR="${WORKSPACE}/config/go2_edu"', script)
        for name in ("go2.yaml", "epgeneral_mqtav.yaml", "udp_telemetry.yaml", "device.yaml"):
            self.assertIn('${PROFILE_CONFIG_DIR}/' + name, script)
        self.assertIn('destination_host:="${GROUND_STATION_IP}"', script)

    def test_profile_mqtt_and_udp_target_the_same_ground_station(self):
        mqtt = yaml.safe_load((PROFILE / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        launch = ElementTree.parse(DEPLOYMENT / "launch" / "go2_edu_bringup.launch").getroot()
        ground_station = next(item for item in launch.findall("arg") if item.attrib["name"] == "ground_station_ip")
        self.assertEqual(mqtt["mqtt"]["ground_station_ip"], ground_station.attrib["default"])

    def test_source_contains_read_only_dds_topics_and_standard_ros_publishers(self):
        source = (PACKAGE / "src" / "go2_bridge_node.cpp").read_text(encoding="utf-8")
        self.assertIn("rt/lowstate", source)
        self.assertIn("rt/sportmodestate", source)
        self.assertNotIn("ChannelPublisher", source)
        for message_type in ("BatteryState", "Imu", "Odometry", "DiagnosticArray"):
            self.assertIn(message_type, source)

    def test_all_sdk_state_groups_have_prefixed_publishers_and_documentation(self):
        source = (PACKAGE / "src" / "go2_bridge_node.cpp").read_text(encoding="utf-8")
        guide = (PACKAGE / "docs" / "ROS_TOPIC_INTERFACES.md").read_text(encoding="utf-8")
        topics = (
            "low_state/info", "low_state/imu", "low_state/motors", "low_state/bms",
            "low_state/foot_force", "low_state/wireless_remote", "sport_mode/status",
            "sport_mode/imu", "sport_mode/kinematics", "sport_mode/obstacle_ranges",
            "sport_mode/feet", "sport_mode/path",
        )
        for topic in topics:
            self.assertIn('namespace_ + "/' + topic + '"', source)
            self.assertIn("/qrd/QRD_001/" + topic, guide)

        messages = {path.name for path in (PACKAGE / "msg").glob("*.msg")}
        self.assertEqual(len(messages), 13)
        self.assertIn("MotorStateArray.msg", messages)
        self.assertIn("PathPointArray.msg", messages)


if __name__ == "__main__":
    unittest.main()
