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

    def test_startup_runs_stage_manager_last(self):
        startup = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        manager_start = "rosrun car_bringup ground_air_stage_manager_node.py"
        self.assertEqual(startup.count(manager_start), 1)
        self.assertNotIn("start_launch 7 /odom_camera_init_broadcaster", startup)
        self.assertLess(startup.index("start_optional_launch 6 /epgeneral_video_srt"),
                        startup.index(manager_start))
        self.assertIn("rosservice type /ground_air/system/set_stage", startup)
        manager = (PROFILE / "car_bringup_scripts" /
                   "ground_air_stage_manager_node.py").read_text(encoding="utf-8")
        core = (PROFILE / "car_bringup_scripts" /
                "system_stage_core.py").read_text(encoding="utf-8")
        self.assertIn("self._resident_transforms = self._backend.start", manager)
        self.assertIn("resident_tf_version", manager)
        self.assertLess(
            manager.index("self._resident_transforms = self._backend.start"),
            manager.index("self._service = rospy.Service"))
        self.assertIn("self._backend.stop(self._resident_transforms)", manager)
        self.assertNotIn("mapping_coordinate_transforms.launch", core)
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


if __name__ == "__main__":
    unittest.main()
