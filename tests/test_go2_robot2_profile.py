"""Static deployment contracts for the physical-source QRD_002 workspace."""

from pathlib import Path
import importlib.util
import unittest
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg" / "deploy" / "go2_robot2"
INTEGRATION = ROOT / "edge_side_pkg" / "EPGeneral_go2_integration"


def config(name):
    return yaml.safe_load((PROFILE / "config" / (name + ".yaml")).read_text(encoding="utf-8"))


class Go2Robot2ProfileTests(unittest.TestCase):
    def test_camera_autoselection_preserves_usb3_profile_and_waits_for_rgb(self):
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn('CAMERA_SERIAL="${CCS_D435_SERIAL:-}"', script)
        self.assertIn('camera_args+=("serial_no:=${CAMERA_SERIAL}")', script)
        self.assertNotIn("device_type:=", script)
        self.assertNotIn("serial_no:=_", script)
        self.assertIn("color_fps:=30", script)
        self.assertEqual(config("video")["framerate"], 30)
        camera = script.index('start_launch 2 --defer-ready "${camera_args[@]}"')
        fresh = script.index('"${READINESS}" camera --timeout 30 --max-age 3')
        ready = script.index('report OK "camera is ready."')
        video = script.index("start_launch 5 epgeneral_video_srt")
        self.assertLess(camera, fresh)
        self.assertLess(fresh, ready)
        self.assertLess(ready, video)
        self.assertIn("inspect ${LOG_DIR}/camera.log", script)

    def test_camera_observer_rejects_stale_and_repeated_frames(self):
        path = PROFILE / "scripts/ccs_ros_readiness.py"
        spec = importlib.util.spec_from_file_location("go2_robot2_readiness", path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        self.assertEqual(helper.topics_for_mode("camera"),
                         {"/camera/color/image_raw": "sensor_msgs/Image"})
        self.assertNotIn("/camera/color/image_raw", helper.topics_for_mode("inputs"))
        samples = {}
        record = lambda stamp, now: helper.record_observation(samples, "rgb", stamp, now, 100, 3)
        self.assertEqual(record(99, 101), 0)
        self.assertEqual(record(101, 101), 1)
        self.assertEqual(record(101, 101.5), 0)
        self.assertEqual(record(102, 102), 1)
        self.assertEqual(record(103, 103), 2)
        self.assertEqual(record(104, 108), 0)

    def test_control_gate_and_owned_sessions_protect_task_consumption(self):
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn('setsid roslaunch "$@"', script)
        self.assertIn('setsid roscore >', script)
        self.assertIn('ps -o ppid=', script)
        task = script.index("start_launch 8 epgeneral_task_control")
        self.assertLess(script.index('"${READINESS}" inputs --timeout 30 --max-age 3'), task)
        self.assertLess(script.index("Fresh disabled state was lost before task startup"), task)
        shutdown = script[script.index("shutdown_all() {"):script.index("[[ -r /opt/ros/noetic/setup.bash ]]")]
        self.assertLess(shutdown.index('stop_process "${PIDS[8]}"'), shutdown.index("data: false"))
        self.assertLess(shutdown.index('"${READINESS}" disabled'), shutdown.index('stop_process "${ROSCORE_PID}"'))
        self.assertNotIn("go2_task_safety.json", shutdown)
        self.assertIn('check_runtime_nodes', script)

    def test_runtime_files_use_lf_and_readonly_check_does_not_start_observers(self):
        for path in [PROFILE / "start_ccs_edge_dev.sh", PROFILE / "scripts/ccs_ros_readiness.py"]:
            content = path.read_bytes()
            self.assertNotIn(b"\r\n", content)
            self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn('export PYTHONDONTWRITEBYTECODE=1', script)
        self.assertIn('if [[ "${CHECK_ONLY}" != true ]]; then\n  run_quiet python3 "${READINESS}" camera', script)
        self.assertLess(script.index('flock -n 9'), script.index('STARTUP_LOG="${LOG_DIR}/startup.log"'))

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
