import json
import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "ground_air_agv"


class GroundAirAgvProfileTests(unittest.TestCase):
    def test_identity_matches_ground_station(self):
        device = yaml.safe_load((PROFILE / "config" / "device.yaml").read_text(encoding="utf-8"))["device"]
        ground = json.loads((ROOT / "config" / "devices.json").read_text(encoding="utf-8"))
        record = next(item for item in ground["devices"] if item["device_id"] == "AGV_001")
        self.assertEqual(device, {"id": "AGV_001", "ip": "192.168.50.130"})
        self.assertEqual(record["ip_address"], device["ip"])
        self.assertEqual(record["relocalization_profile"], "disabled")

    def test_profile_uses_actual_mavros_and_livox_topics(self):
        mqtt = yaml.safe_load((PROFILE / "config" / "epgeneral_mqtav.yaml").read_text(encoding="utf-8"))
        telemetry = yaml.safe_load((PROFILE / "config" / "udp_telemetry.yaml").read_text(encoding="utf-8"))
        self.assertEqual(mqtt["mqtt"]["ground_station_ip"], "192.168.50.101")
        self.assertEqual(mqtt["ros"]["state"]["topic"], "/mavros/state")
        self.assertEqual(mqtt["ros"]["battery"]["topic"], "/mavros/battery")
        sources = {item["name"]: item["source"]["topic"] for item in telemetry["descriptors"]}
        self.assertEqual(sources["global_pose"], "/mavros/local_position/pose")
        self.assertEqual(sources["imu"], "/mavros/imu/data")
        self.assertEqual(sources["livox_pointcloud"], "/livox/lidar")

    def test_placeholder_features_are_disabled_and_have_no_task_adapter(self):
        mapping = yaml.safe_load((PROFILE / "config" / "map_stream.yaml").read_text(encoding="utf-8"))
        relocalization = yaml.safe_load((PROFILE / "config" / "relocalization.yaml").read_text(encoding="utf-8"))
        task = yaml.safe_load((PROFILE / "config" / "task_control.yaml").read_text(encoding="utf-8"))
        video = yaml.safe_load((PROFILE / "config" / "video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(mapping["deployment"], {"state": "placeholder", "enabled": False})
        self.assertFalse(relocalization["enabled"])
        self.assertEqual(task["deployment"], {"state": "placeholder", "enabled": False})
        self.assertNotIn("adapter", task)
        self.assertEqual(video["deployment"], {"state": "active", "enabled": True})
        self.assertEqual(video["camera_model"], "SIYI A8 Mini")
        self.assertEqual(video["image_topic"], "/a8_cam/image_raw")
        self.assertEqual(video["image_message_type"], "sensor_msgs/Image")
        self.assertEqual((video["output_width"], video["output_height"]), (1280, 720))
        self.assertEqual((video["framerate"], video["bitrate_kbps"]), (30, 3000))
        self.assertEqual((video["srt_port"], video["srt_latency_ms"]), (9000, 120))

    def test_bringup_contains_only_mavros_and_livox(self):
        launch = ElementTree.parse(PROFILE / "launch" / "ground_air_agv_bringup.launch").getroot()
        includes = [item.attrib["file"] for item in launch.findall("include")]
        self.assertEqual(includes, ["$(dirname)/mavros_base.launch", "$(dirname)/livox_mid360_base.launch"])
        combined = "\n".join(
            (PROFILE / "launch" / name).read_text(encoding="utf-8")
            for name in ("mavros_base.launch", "livox_mid360_base.launch")
        )
        self.assertIn("mavros)/launch/px4.launch", combined)
        self.assertIn("livox_ros_driver2)/launch_ROS1/msg_MID360.launch", combined)
        for forbidden in ("fast_lio", "filter_ground", "dynamic_mapping", "epgeneral_"):
            self.assertNotIn(forbidden, combined.lower())

    def test_managed_script_starts_four_required_and_two_optional_services(self):
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn("LAUNCH_NAMES=(mavros livox mqtav udp_telemetry a8_camera video_srt)", script)
        self.assertIn(
            "NODE_NAMES=(/mavros /livox_lidar_publisher2 /epgeneral_mqtav /epgeneral_udp_telemetry /a8_mini_camera /epgeneral_video_srt)",
            script,
        )
        self.assertIn("OPTIONAL=(false false false false true true)", script)
        self.assertIn('"${LAUNCH_DIR}/mavros_base.launch"', script)
        self.assertIn('"${LAUNCH_DIR}/livox_mid360_base.launch"', script)
        self.assertIn("epgeneral_mqtav epgeneral_mqtav.launch", script)
        self.assertIn("epgeneral_udp_telemetry epgeneral_udp_telemetry.launch", script)
        self.assertIn('destination_host:="192.168.50.101"', script)
        self.assertIn('link_status_topic:="/agv/AGV_001/link/udp_tx"', script)
        self.assertIn('diagnostics_topic:="/agv/AGV_001/diagnostics"', script)
        self.assertIn("a8_mini_camera a8_mini_camera.launch", script)
        self.assertIn("camera_ip:=192.168.144.25 image_topic:=/a8_cam/image_raw", script)
        self.assertIn("epgeneral_video_srt epgeneral_video_srt.launch", script)
        self.assertIn('video_config_file:="${PROFILE_CONFIG_DIR}/video.yaml"', script)
        self.assertIn("A8 Mini 30 秒内无图像，视频链降级等待恢复；基础服务继续运行", script)
        self.assertIn("可降级节点异常退出，基础服务继续运行", script)
        for forbidden in ("rostopic pub", "cmd_vel", "arming", "set_mode", "takeoff", "land"):
            self.assertNotIn(forbidden, script.lower())


if __name__ == "__main__":
    unittest.main()
