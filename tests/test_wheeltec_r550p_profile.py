import json
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "wheeltec_r550p"


class WheeltecR550pProfileTests(unittest.TestCase):
    def test_identity_and_actual_topics(self):
        device = yaml.safe_load((PROFILE / "config" / "device.yaml").read_text(encoding="utf-8"))
        mqtt = yaml.safe_load((PROFILE / "config" / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "config" / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        video = yaml.safe_load((PROFILE / "config" / "video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(device["device"], {"id": "UGV_003", "ip": "192.168.50.122"})
        self.assertEqual(mqtt["ros"]["state"]["topic"], "/odom")
        self.assertEqual(mqtt["ros"]["battery"]["topic"], "/PowerVoltage")
        self.assertEqual(mqtt["ros"]["battery"]["mapping"]["voltage"], "data")
        sources = {item["name"]: item["source"]["topic"] for item in telemetry["descriptors"]}
        self.assertEqual(sources["vision_pose"], "/fastlio_odom")
        self.assertEqual(sources["imu"], "/livox/imu")
        self.assertEqual(sources["fastlio2"], "/Odometry")
        self.assertFalse(video["enabled"])

    def test_managed_mapping_backend_uses_wheeltec_nodes(self):
        package = ROOT / "edge_side_pkg" / "EPGeneral_map_stream"
        sys.path.insert(0, str(package / "src"))
        try:
            from epgeneral_map_stream.config import build_integration_commands, load_config
            config = load_config(
                str(PROFILE / "config" / "map_stream.yaml"),
                str(PROFILE / "config" / "device.yaml"),
            )
            commands = build_integration_commands(config, {
                "session_dir": "/tmp/wheeltec-session",
                "map_name": "20260827_120000",
                "pcd_path": "/tmp/wheeltec-session/map.pcd",
                "pgm_path": "/tmp/wheeltec-session/map.pgm",
                "yaml_path": "/tmp/wheeltec-session/map.yaml",
            })
        finally:
            sys.path.pop(0)
        self.assertEqual(config["integration_backend"], "managed_finalize")
        self.assertEqual(config["managed_mapper_node"], "/wheeltec_pointcloud_mapper")
        self.assertEqual(commands["start_fast_lio"][-5:], [
            "/laserMapping", "/wheeltec_pointcloud_mapper", "/wheeltec_tf_manager",
            "/wheeltec_geometry_tf_publisher", "/wheeltec_pose_adapter",
        ])

    def test_relocalization_task_and_ground_profiles_are_consistent(self):
        relocalization = yaml.safe_load(
            (PROFILE / "config" / "relocalization.yaml").read_text(encoding="utf-8"))
        task = yaml.safe_load((PROFILE / "config" / "task_control.yaml").read_text(encoding="utf-8"))
        ground = json.loads((ROOT / "config" / "relocalization.json").read_text(encoding="utf-8"))
        devices = json.loads((ROOT / "config" / "devices.json").read_text(encoding="utf-8"))
        wheeltec = next(item for item in devices["devices"] if item["device_id"] == "UGV_003")
        self.assertEqual(relocalization["backend"], "wheeltec_r550p")
        self.assertTrue(ground["profiles"]["wheeltec_r550p"]["supported"])
        self.assertEqual(wheeltec["relocalization_profile"], "wheeltec_r550p")
        self.assertEqual(task["adapter"]["navigation_launch_package"], "wheeltec_navigation")
        self.assertEqual(task["adapter"]["zero_velocity_topic"], "/cmd_vel")

    def test_launch_and_one_click_script_disable_video_by_default(self):
        launch = ElementTree.parse(PROFILE / "launch" / "wheeltec_r550p_bringup.launch").getroot()
        args = {item.attrib["name"]: item.attrib.get("default") for item in launch.findall("arg")}
        self.assertEqual(args["enable_video"], "false")
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        for value in (
            "/home/nrc19/ccs_edge_ws", "/home/nrc19/livox_fastlio/devel/setup.bash",
            "wheeltec_livox_base.launch", "/PowerVoltage", "/livox/lidar",
            "navigation_task_control.launch", "publish_zero_velocity",
        ):
            self.assertIn(value, script)
        self.assertNotIn("start_launch video", script)


if __name__ == "__main__":
    unittest.main()
