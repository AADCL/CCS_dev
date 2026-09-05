import json
import re
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "ground_air_agv"
CONTROL = ROOT / "edge_side_pkg" / "EPGeneral_ground_air_control"


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
            "map": "camera_init", "preview": "odom",
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
        path = CONTROL / "launch" / "relocalization_control.launch"
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
                         ["ground_air_initial_pose_adapter"])

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
        manager_start = (
            "rosrun epgeneral_ground_air_control ground_air_stage_manager_node.py"
        )
        relocalization_start = (
            "start_launch 8 /epgeneral_relocalization epgeneral_relocalization "
            "epgeneral_relocalization.launch"
        )
        transform_start = (
            "start_launch 9 /odom_camera_init_broadcaster "
            "car_bringup mapping_coordinate_transforms.launch")
        self.assertEqual(startup.count(manager_start), 1)
        self.assertEqual(startup.count(relocalization_start), 1)
        self.assertEqual(startup.count(transform_start), 1)
        self.assertIn(
            'fail "stage manager 已由其他进程启动，拒绝重复接管"',
            startup)
        self.assertLess(startup.index("start_optional_launch 6 /epgeneral_video_srt"),
                        startup.index(manager_start))
        self.assertLess(startup.index(manager_start), startup.index(relocalization_start))
        self.assertLess(startup.index(relocalization_start), startup.index(transform_start))
        self.assertLess(
            startup.index("for index in 8 7 9; do"),
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
        manager = (CONTROL / "scripts" /
                   "ground_air_stage_manager_node.py").read_text(encoding="utf-8")
        core = (CONTROL / "src" / "epgeneral_ground_air_control" /
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
        self.assertIn(
            'verify_known_version "${WS}/config/ground_air_agv/map_stream.yaml"',
            deploy,
        )
        self.assertIn(
            'add edge_side_pkg/deploy/ground_air_agv/config/map_stream.yaml '
            '"${WS}/config/ground_air_agv/map_stream.yaml" 644',
            deploy,
        )
        device_setup = (
            "source /home/bitcq/catkin_ws/devel/setup.bash --extend"
        )
        edge_setup = 'source "${WS}/devel/setup.bash" --extend'
        self.assertIn(device_setup, deploy)
        self.assertIn(edge_setup, deploy)
        self.assertLess(deploy.index(device_setup), deploy.index(edge_setup))
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
        expected = {
            "remote_mapping": "odom",
            "preview_source": "camera_init",
            "remote_artifact": "map",
        }
        paths = (
            ROOT / "config" / "map_building.json",
            ROOT / "release" / "defaults" / "config" / "map_building.json",
        )
        for path in paths:
            with self.subTest(path=path):
                ground = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(ground["device_frames"]["AGV_001"], expected)


    def test_identity_matches_ground_station(self):
        device = yaml.safe_load((PROFILE / "config" / "device.yaml").read_text(encoding="utf-8"))["device"]
        ground = json.loads((ROOT / "config" / "devices.json").read_text(encoding="utf-8"))
        record = next(item for item in ground["devices"] if item["device_id"] == "AGV_001")
        self.assertEqual(device, {"id": "AGV_001", "ip": "192.168.50.130"})
        self.assertEqual(record["ip_address"], device["ip"])
        self.assertEqual(record["relocalization_profile"], "ground_air_agv")

    def test_relocalization_profile_and_scoped_override_contract(self):
        relocalization = yaml.safe_load(
            (PROFILE / "config" / "relocalization.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(relocalization["enabled"])
        self.assertEqual(relocalization["backend"], "ground_air_agv")
        self.assertEqual(relocalization["storage"]["pcd_filename"], "cloud_map.pcd")
        self.assertEqual(relocalization["ros"]["map_topic"], "/map")
        self.assertEqual(relocalization["tf_reporting"], {
            "continuous": True,
            "interval_seconds": 1.0,
            "persist_interval_seconds": 30.0,
        })
        stage = relocalization["ros"]["stages"][0]
        self.assertEqual(stage["package"], "car_bringup")
        self.assertEqual(stage["launch"], "relocalization_system.launch")
        self.assertEqual(
            stage["ros_package_path_prepend"],
            "/home/bitcq/ccs_edge_ws/overrides",
        )
        self.assertEqual(
            stage["ros_package_path_exclude"],
            ["/home/bitcq/catkin_ws/src"],
        )
        self.assertEqual(
            stage["cmake_prefix_path_exclude"],
            ["/home/bitcq/catkin_ws/devel"],
        )
        override = (
            PROFILE / "overrides" / "car_bringup" / "launch" /
            "relocalization_system.launch"
        ).read_text(encoding="utf-8")
        self.assertIn("ground_air_relocalization_stage_node.py", override)

    def test_relocalization_deployment_writes_only_inside_edge_workspace(self):
        deploy = (PROFILE / "deploy_relocalization_update.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("require_workspace_path", deploy)
        self.assertIn("write target escapes ccs_edge_ws", deploy)
        self.assertIn('BACKUP="${WS}/.deployment_backups/', deploy)
        self.assertIn('EVIDENCE="${WS}/artifacts/', deploy)
        self.assertIn('TEMP_ROOT="${WS}/.tmp/', deploy)
        self.assertIn('export TMPDIR="${TEMP_ROOT}"', deploy)
        self.assertIn('ROS_HOME="${WS}/run/ros_home"', deploy)
        self.assertIn('ROS_LOG_DIR="${WS}/log/ground_air_agv/ros"', deploy)
        self.assertIn('scoped_ros_package_path="${WS}/overrides:', deploy)
        self.assertIn('scoped_cmake_prefix_path="${WS}/devel:', deploy)
        self.assertNotIn('install -m 644 "${ROOT}" "${UNDERLAY}', deploy)
        self.assertNotIn("systemctl --user daemon-reload", deploy)
        self.assertNotIn("systemctl --user enable", deploy)

    def test_relocalization_deploys_and_checks_mapping_compatibility(self):
        deploy = (PROFILE / "deploy_relocalization_update.sh").read_text(
            encoding="utf-8"
        )
        manifest = re.search(r"for relative in (.*?)\s*; do", deploy, re.DOTALL)
        self.assertIsNotNone(manifest, "mapping compatibility files must be bundled")
        relative_files = manifest.group(1).replace("\\\n", "").split()
        required = {
            "scripts/ground_air_stage_client.py", "scripts/check_version.py",
            "package.xml", "setup.py", "src/epgeneral_map_stream/__init__.py",
            "src/epgeneral_map_stream/config.py", "README.md", "CHANGELOG.md",
            "test/test_ground_air_stage_client.py", "test/test_config.py",
            "test/test_version_and_entrypoint.py", "test/test_paths.py",
        }
        self.assertTrue(required.issubset(relative_files))
        self.assertEqual(len(relative_files), len(set(relative_files)))
        package = ROOT / "edge_side_pkg" / "EPGeneral_map_stream"
        for relative in relative_files:
            with self.subTest(source=relative):
                self.assertTrue((package / relative).is_file())
        self.assertIn(
            'add "edge_side_pkg/EPGeneral_map_stream/${relative}" '
            '"${WS}/src/EPGeneral_map_stream/${relative}" "${mode}"', deploy)
        self.assertIn(
            "python3 -m unittest test_ground_air_stage_client test_config "
            "test_version_and_entrypoint", deploy)
        self.assertIn("python3 -m unittest test_control test_launch_contract", deploy)
        self.assertIn(
            'python3 "${WS}/src/EPGeneral_map_stream/scripts/check_version.py"',
            deploy)
        readiness = deploy[deploy.index("READY=false"):deploy.index(
            'if [[ "${READY}" != true ]]')]
        self.assertRegex(
            readiness,
            r'if python3 "\$\{WS\}/src/EPGeneral_map_stream/scripts/'
            r'ground_air_stage_client\.py" --check [^\n]+; then\n'
            r'\s+READY=true\n\s+break\n\s+fi',
        )
        self.assertIn("mapping-client-preflight.log", deploy)
        self.assertIn(
            'add edge_side_pkg/EPGeneral_device_config/config/map_stream.yaml '
            '"${TEMP_ROOT}/fixtures/map_stream.yaml" 644', deploy)
        self.assertIn(
            'CCS_MAP_STREAM_TEST_MAPPING="${TEMP_ROOT}/fixtures/map_stream.yaml" '
            'PYTHONPATH=', deploy)
        self.assertTrue((ROOT / "edge_side_pkg" / "EPGeneral_device_config" /
                         "config" / "map_stream.yaml").is_file())

    def test_relocalization_deployment_preserves_boot_enablement(self):
        deploy = (PROFILE / "deploy_relocalization_update.sh").read_text(
            encoding="utf-8"
        )
        capture = (
            'enablement_before="$(systemctl --user is-enabled '
            'ccs-edge-dev.service || true)"'
        )
        self.assertIn(capture, deploy)
        self.assertLess(deploy.index(capture), deploy.index("install -m"))
        self.assertIn(
            '[[ "${enablement_after}" == "${enablement_before}" ]] || {',
            deploy)
        self.assertIn("service-enablement.before", deploy)
        self.assertIn("service-enablement.after", deploy)
        after_restart = deploy[deploy.index(
            "systemctl --user restart ccs-edge-dev.service"):]
        self.assertEqual(after_restart.count("\nverify_service_enablement\n"), 2)
        for forbidden in ("daemon-reload", " enable ", " disable ",
                          "enable-linger", "disable-linger"):
            self.assertNotIn(forbidden, deploy)

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
