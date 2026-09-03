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

    def test_control_launches_mapping_stack_without_static_tf(self):
        path = PROFILE / "launch" / "manual_mapping_control.launch"
        launch = ElementTree.parse(path).getroot()
        includes = [item.attrib["file"] for item in launch.findall("include")]
        self.assertEqual(includes, [
            "$(find fast_lio_open3d)/launch/mapping_mid360.launch",
            "$(find car_bringup)/launch/fastlio_pipeline.launch",
            "$(find pcl_test)/launch/filter_ground.launch",
            "$(find dynamic_mapping)/launch/dynamic_mapping.launch",
            "$(find ground_air_mapping)/launch/mapping.launch",
        ])
        nodes = {item.attrib["name"]: item for item in launch.findall("node")}
        self.assertEqual(set(nodes), {
            "ground_air_world_tf_owner", "ground_air_start_mapping"})
        world_params = {item.attrib["name"]: item.attrib["value"]
                        for item in nodes["ground_air_world_tf_owner"].findall("param")}
        self.assertEqual(world_params["mode"], "mapping")
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "start_mapping.launch",
            "mapping_system.launch",
            "mapping_coordinate_transforms.launch",
        ):
            self.assertNotIn(forbidden, text)

    def test_relocalization_control_reuses_static_tf(self):
        path = PROFILE / "launch" / "relocalization_control.launch"
        launch = ElementTree.parse(path).getroot()
        includes = [item.attrib["file"] for item in launch.findall("include")]
        self.assertEqual(includes, [
            "$(find fast_lio_open3d)/launch/mapping_mid360.launch",
            "$(find car_bringup)/launch/fastlio_pipeline.launch",
            "$(find ground_air_localization)/launch/localization.launch",
        ])
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("mapping_coordinate_transforms.launch", text)
        nodes = launch.findall("node")
        self.assertEqual([item.attrib["name"] for item in nodes],
                         ["ground_air_start_relocalization"])

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

    def test_startup_runs_coordinate_transform_launch_last(self):
        startup = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        manager_start = "rosrun car_bringup ground_air_stage_manager_node.py"
        transform_start = (
            "start_launch 8 /odom_camera_init_broadcaster "
            "car_bringup mapping_coordinate_transforms.launch")
        self.assertEqual(startup.count(manager_start), 1)
        self.assertEqual(startup.count(transform_start), 1)
        self.assertIn(
            'fail "stage manager 已由其他进程启动，拒绝重复接管"',
            startup)
        self.assertLess(startup.index("start_optional_launch 6 /epgeneral_video_srt"),
                        startup.index(manager_start))
        self.assertLess(startup.index(manager_start), startup.index(transform_start))
        self.assertLess(
            startup.index("for index in 7 8; do"),
            startup.index("for ((index=6; index>=0; index--)); do"))
        self.assertIn(
            'fail "mapping_tf 节点异常退出：/odom_camera_init_broadcaster"',
            startup)
        self.assertIn(
            'fail "mapping_tf 节点异常退出：/base_link_body_broadcaster"',
            startup)
        self.assertIn("rosservice type /ground_air/system/set_stage", startup)
        self.assertIn(
            "ground_air_stage_manager/ccs_session_guard_version", startup)
        manager = (PROFILE / "car_bringup_scripts" /
                   "ground_air_stage_manager_node.py").read_text(encoding="utf-8")
        core = (PROFILE / "car_bringup_scripts" /
                "system_stage_core.py").read_text(encoding="utf-8")
        self.assertNotIn("mapping_coordinate_transforms.launch", manager)
        self.assertNotIn("_resident_transforms", manager)
        self.assertIn("external_tf_required", manager)
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
        self.assertIn("KillMode=mixed", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_deployment_guards_overwritten_runtime_and_final_readiness(self):
        deploy = (PROFILE / "deploy_stage_manager_update.sh").read_text(
            encoding="utf-8"
        )
        for guarded_target in (
            "ground_air_mapping_stack.sh",
            "ground_air_stage_client.py",
        ):
            self.assertIn(
                'verify_known_version "${WS}/src/EPGeneral_map_stream/scripts/'
                + guarded_target
                + '"',
                deploy,
            )
        self.assertIn("roslaunch --nodes", deploy)
        self.assertIn("on-demand launch duplicates startup TF", deploy)
        self.assertIn(
            "rosservice type /ground_air/system/set_stage", deploy
        )
        self.assertIn(
            "systemctl --user show ccs-edge-dev.service -p NRestarts --value",
            deploy,
        )
        self.assertIn('"${restart_count}" == 0', deploy)

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
