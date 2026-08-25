import hashlib
import json
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "scout_mini"


class ScoutMiniProfileTests(unittest.TestCase):
    def test_identity_and_actual_sensor_topics(self):
        device = yaml.safe_load((PROFILE / "config" / "device.yaml").read_text(encoding="utf-8"))
        mqtt = yaml.safe_load((PROFILE / "config" / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        video = yaml.safe_load((PROFILE / "config" / "video.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "config" / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(device["device"], {"id": "UGV_001", "ip": "192.168.50.120"})
        self.assertEqual(mqtt["mqtt"]["ground_station_ip"], "192.168.50.101")
        self.assertTrue(mqtt["ros"]["state"]["connected_on_message"])
        battery = mqtt["ros"]["battery"]
        self.assertEqual((battery["topic"], battery["message_type"]), ("/BMS_status", "scout_msgs/ScoutBmsStatus"))
        self.assertEqual(battery["mapping"], {"percentage": None, "voltage": "battery_voltage", "current": None})
        self.assertEqual(video["image_topic"], "/camera/color/image_raw")
        mapping = yaml.safe_load((PROFILE / "config" / "map_stream.yaml").read_text(encoding="utf-8"))
        self.assertEqual(mapping["integrations"]["backend"], "scout_finalize")
        sources = {item["name"]: item["source"]["topic"] for item in telemetry["descriptors"]}
        self.assertEqual(sources["vision_pose"], "/scout/odom")
        self.assertEqual(sources["imu"], "/livox/imu")

    def test_descriptor_hash_matches_ground_contract(self):
        edge_package = ROOT / "edge_side_pkg" / "EPGeneral_udp_telemetry"
        sys.path.insert(0, str(edge_package / "src"))
        try:
            from epgeneral_udp_telemetry.config import descriptor_hash, load_config
            edge = load_config(
                str(PROFILE / "config" / "udp_telemetry.yaml"),
                str(PROFILE / "config" / "device.yaml"),
            )
        finally:
            sys.path.pop(0)
        ground = json.loads((ROOT / "config" / "udp_telemetry.json").read_text(encoding="utf-8"))
        self.assertEqual(edge["descriptor_hash"], descriptor_hash(ground["descriptors"]))

    def test_start_script_sources_existing_workspaces_and_all_nodes(self):
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        for setup in ("realsense_ws/devel/setup.bash", "livox_fastlio/devel/setup.bash", "${WORKSPACE}/devel/setup.bash"):
            self.assertIn(setup, script)
        for node in ("scout_livox_base.launch", "D435I.launch", "epgeneral_mqtav", "epgeneral_udp_telemetry", "epgeneral_video_srt", "epgeneral_map_stream"):
            self.assertIn(node, script)
        self.assertIn("ROS_IP_VALUE", script)
        for topic in ("/scout_status", "/BMS_status", "/livox/lidar", "/livox/imu", "/camera/color/image_raw"):
            self.assertIn("wait_for_message " + topic, script)
        self.assertNotIn("FAST-LIO", script)
        self.assertNotIn("/Odometry", script)


if __name__ == "__main__":
    unittest.main()
