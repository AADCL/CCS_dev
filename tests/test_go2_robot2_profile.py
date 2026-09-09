"""Static deployment contracts for the physical-source QRD_002 workspace."""

from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "go2_robot2"
INTEGRATION = ROOT / "edge_side_pkg" / "EPGeneral_go2_integration"


def config(name):
    return yaml.safe_load((PROFILE / "config" / (name + ".yaml")).read_text(encoding="utf-8"))


class Go2Robot2ProfileTests(unittest.TestCase):
    def test_identity_and_ground_station_are_consistent(self):
        self.assertEqual(config("device")["device"], {"id": "QRD_002", "ip": "192.168.50.111"})
        for name in ("map_stream", "relocalization", "task_control"):
            self.assertEqual(config(name)["network"]["ground_station_ip"], "192.168.50.101")
        self.assertEqual(config("epgeneral_mqtav")["mqtt"]["ground_station_ip"], "192.168.50.101")
        self.assertEqual(config("udp_telemetry")["network"]["destination_host"], "192.168.50.101")

    def test_chassis_imu_does_not_change_algorithm_input_or_wire_label(self):
        descriptors = config("udp_telemetry")["descriptors"]
        self.assertEqual(len(descriptors), 8)
        imu = next(item for item in descriptors if item["name"] == "imu")
        self.assertEqual(imu["source"]["topic"], "/go2/imu")
        self.assertEqual(imu["display_name"], "MID360 IMU")
        self.assertEqual(config("map_stream")["ros"]["inputs"]["imu"]["topic"], "/livox/imu")

    def test_mapping_preview_and_artifact_frames_remain_distinct(self):
        mapping = config("map_stream")
        self.assertEqual(mapping["ros"]["frames"]["map"], "lio_odom")
        self.assertEqual(mapping["artifacts"]["frame"], "odom")
        self.assertEqual(mapping["integrations"]["map_accumulator"]["service"], "/go2_map_accumulator/save_map")
        tree = ET.parse(ROOT / "edge_side_pkg" / "EPGeneral_map_stream" / "launch" / "mapping_prerequisites_go2_robot2.launch")
        accumulator = tree.find("./node[@name='go2_map_accumulator']")
        self.assertIsNotNone(accumulator)
        self.assertEqual(accumulator.find("./param[@name='output_path']").get("value"), "$(arg output_path)")
        self.assertEqual(accumulator.find("./param[@name='input_cloud']").get("value"), "/cloud_registered_odom")

    def test_native_navigation_receives_map_and_extrinsics_without_drivers(self):
        tree = ET.parse(INTEGRATION / "launch" / "navigation.launch")
        includes = tree.findall("./include")
        self.assertEqual(len(includes), 4)
        self.assertFalse(any("go2_control" in item.get("file") or "livox_ros_driver2" in item.get("file") for item in includes))
        for package in ("go2_localization", "go2_navigation"):
            include = next(item for item in includes if "$(find " + package + ")" in item.get("file"))
            self.assertEqual(include.find("./arg[@name='map_name']").get("value"), "$(arg map_name)")
            self.assertEqual(include.find("./arg[@name='map_root']").get("value"), "$(arg map_root)")
        core = next(item for item in includes if "$(find go2_core)" in item.get("file"))
        self.assertEqual(core.find("./arg[@name='extrinsics']").get("value"), "$(arg extrinsics_file)")

    def test_relocalization_guard_precedes_navigation_and_requires_health(self):
        ros = config("relocalization")["ros"]
        self.assertEqual([stage["launch"] for stage in ros["stages"]], ["navigation_guard.launch", "navigation.launch"])
        self.assertEqual(ros["localization_health_topic"], "/localization/ok")
        self.assertGreater(ros["localization_health_timeout_seconds"], 0)
        self.assertIn("map_name:={map_id}", ros["stages"][1]["args"])
        self.assertIn("map_root:={map_root}", ros["stages"][1]["args"])

    def test_task_profile_is_attach_with_persistent_safety(self):
        adapter = config("task_control")["adapter"]
        self.assertEqual(adapter["navigation_management"], "attach")
        self.assertTrue(adapter["auto_arm_on_schedule"])
        self.assertTrue(adapter["auto_disarm_on_terminal"])
        self.assertEqual(adapter["navigation_reset_service"], "/go2_navigation_supervisor/reset")
        self.assertEqual(adapter["control_enable_service"], "/go2_sdk_bridge_real/enable")
        self.assertEqual(adapter["control_diagnostics_topic"], "/go2/diagnostics")
        self.assertEqual(adapter["emergency_stop_state_file"], "/home/unitree/ccs_edge_ws/run/state/go2_task_safety.json")

    def test_root_script_uses_direct_launches_and_owned_teardown(self):
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn("--check|--preflight", script)
        self.assertIn("ROSCORE_MANAGED=false", script)
        self.assertIn('MANAGED=(false', script)
        self.assertIn("start_launch 0 livox_ros_driver2 msg_MID360.launch", script)
        self.assertIn("start_launch 1 go2_control control.launch", script)
        self.assertIn("CCS_GO2_USE_REAL_SDK:-true", script)
        self.assertNotIn("exec ", script.replace('exec 9>', ''))
        self.assertNotIn("/bin/ccs_go2", script)
        self.assertNotIn("bringup.launch", script)
        self.assertNotIn("data: true", script)
        self.assertNotIn("killall", script)
        self.assertNotIn("pkill", script)
        self.assertIn("stop_process", script)
        self.assertIn("scripts/ccs_sntp_sync.py", script)
        self.assertNotIn("--set", script)

    def test_clock_unit_uses_workspace_scripts(self):
        unit = (PROFILE / "ccs-sntp-sync.service").read_text(encoding="utf-8")
        self.assertIn("/home/unitree/ccs_edge_ws/scripts/ccs_sntp_sync.py", unit)
        self.assertNotIn("/bin/ccs_sntp_sync.py", unit)
        self.assertIn("--set", unit)

    def test_all_profile_launches_are_valid_xml(self):
        for path in (INTEGRATION / "launch").glob("*.launch"):
            with self.subTest(path=path.name):
                self.assertEqual(ET.parse(path).getroot().tag, "launch")


if __name__ == "__main__":
    unittest.main()
