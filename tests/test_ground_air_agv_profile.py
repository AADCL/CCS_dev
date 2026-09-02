import json
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "ground_air_agv"


class GroundAirAgvProfileTests(unittest.TestCase):
    def test_identity_backend_and_frames(self):
        device = yaml.safe_load((PROFILE / "config" / "device.yaml").read_text(
            encoding="utf-8"))
        mapping = yaml.safe_load((PROFILE / "config" / "map_stream.yaml").read_text(
            encoding="utf-8"))
        self.assertEqual(device["device"], {"id": "AGV_001", "ip": "192.168.50.130"})
        self.assertTrue(mapping["deployment"]["enabled"])
        self.assertEqual(mapping["integrations"]["backend"], "ground_air_service")
        self.assertEqual(mapping["ros"]["frames"], {
            "map": "camera_init", "preview": "camera_init",
            "body": "body", "sensor": "body",
        })

    def test_control_launch_reuses_start_mapping_without_base_services(self):
        launch = ElementTree.parse(
            PROFILE / "launch" / "manual_mapping_control.launch").getroot()
        self.assertEqual(launch.findall("node"), [])
        include = launch.find("include")
        self.assertIn("start_mapping.launch", include.attrib["file"])
        args = {item.attrib["name"]: item.attrib["value"]
                for item in include.findall("arg")}
        self.assertEqual(args["start_mavros"], "false")
        self.assertEqual(args["start_stack"], "true")

    def test_coordinate_transform_launch_contains_only_requested_static_tfs(self):
        launch = ElementTree.parse(
            PROFILE / "launch" / "mapping_coordinate_transforms.launch").getroot()
        args = {item.attrib["name"]: item.attrib["default"]
                for item in launch.findall("arg")}
        self.assertEqual(args, {
            "odom_frame": "odom",
            "camera_init_frame": "camera_init",
            "body_frame": "body",
            "base_frame": "base_link",
        })
        nodes = {item.attrib["name"]: item.attrib
                 for item in launch.findall("node")}
        self.assertEqual(set(nodes), {
            "odom_camera_init_broadcaster", "base_link_body_broadcaster"})
        self.assertEqual(nodes["odom_camera_init_broadcaster"]["args"],
                         "0 0 0 0 0 0 $(arg odom_frame) $(arg camera_init_frame)")
        self.assertEqual(nodes["base_link_body_broadcaster"]["args"],
                         "0 0 0 0 0 0 $(arg body_frame) $(arg base_frame)")
        self.assertTrue(all(item["pkg"] == "tf2_ros" for item in nodes.values()))

    def test_startup_owns_coordinate_transforms_before_map_stream(self):
        startup = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        tf_start = "start_launch 7 /odom_camera_init_broadcaster"
        map_stream_start = "start_launch 4 /epgeneral_map_stream"
        self.assertIn(tf_start, startup)
        self.assertIn("wait_for_node /base_link_body_broadcaster", startup)
        self.assertLess(startup.index(tf_start), startup.index(map_stream_start))
        mapping = yaml.safe_load((PROFILE / "config" / "map_stream.yaml").read_text(
            encoding="utf-8"))
        ground_air = mapping["integrations"]["ground_air"]
        self.assertNotIn("coordinate_transform_launch", ground_air)
        self.assertNotIn("fast_lio_ready_node", ground_air)

    def test_systemd_service_owns_boot_startup(self):
        unit = (PROFILE / "ccs-edge-dev.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/home/bitcq/ccs_edge_ws/start_ccs_edge_dev.sh", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_ground_station_frame_contract_matches_profile(self):
        ground = json.loads((ROOT / "config" / "map_building.json").read_text(
            encoding="utf-8"))
        self.assertEqual(ground["device_frames"]["AGV_001"], {
            "remote_mapping": "camera_init",
            "preview_source": "camera_init",
            "remote_artifact": "map",
        })


if __name__ == "__main__":
    unittest.main()
