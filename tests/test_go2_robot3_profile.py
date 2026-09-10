"""QRD_003 profile contracts and freshness checks that protect task startup."""
import ast
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "edge_side_pkg/deploy/go2_robot3"


def config(name):
    return yaml.safe_load((PROFILE / "config" / (name + ".yaml")).read_text(encoding="utf-8"))


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, PROFILE / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


READINESS = load_helper("ccs_ros_readiness")
SNTP = load_helper("ccs_sntp_sync")


def diagnostics(stamp=101.0, enabled="false", low_age="0.1", sport_age="0.1"):
    values = [SimpleNamespace(key=key, value=value) for key, value in (
        ("motion_enabled", enabled), ("low_state_age_sec", low_age), ("sport_state_age_sec", sport_age))]
    return SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(to_sec=lambda: stamp)),
                           status=[SimpleNamespace(name="GO2 SDK bridge", values=values)])


class Go2Robot3ProfileTests(unittest.TestCase):
    def test_identity_and_station_are_consistent(self):
        self.assertEqual(config("device")["device"], {"id": "QRD_003", "ip": "192.168.50.112"})
        for name in ("map_stream", "relocalization", "task_control"):
            self.assertEqual(config(name)["network"]["ground_station_ip"], "192.168.50.101")
        self.assertEqual(config("epgeneral_mqtav")["mqtt"]["ground_station_ip"], "192.168.50.101")
        self.assertEqual(config("udp_telemetry")["network"]["destination_host"], "192.168.50.101")

    def test_platform_device_contract_is_not_stale(self):
        payload = yaml.safe_load((ROOT / "config" / "devices.json").read_text(encoding="utf-8"))
        devices = {item["device_id"]: item for item in payload["devices"]}
        self.assertEqual(devices["QRD_002"]["relocalization_profile"], "go2_native")
        self.assertEqual(devices["QRD_003"]["relocalization_profile"], "go2_native")
        self.assertEqual(
            devices["QRD_003"]["status_cards"],
            ["livox_driver", "fastlio2", "mapping_mode"],
        )

    def test_mqtt_separates_periodic_connection_from_latched_armed_state(self):
        ros = config("epgeneral_mqtav")["ros"]
        self.assertEqual(ros["connection"], {"topic": "/go2/state/low_state",
                         "message_type": "go2_control/Go2LowState", "timeout_seconds": 3.0})
        self.assertEqual(ros["state"]["topic"], "/go2/control/enabled")
        self.assertEqual(ros["state"]["mapping"]["armed"], "data")
        self.assertEqual(ros["mission"]["topic"], "/qrd/QRD_003/task_status")

    def test_telemetry_source_keeps_formal_wire_descriptor(self):
        descriptors = config("udp_telemetry")["descriptors"]
        self.assertEqual(len(descriptors), 8)
        imu = next(item for item in descriptors if item["name"] == "imu")
        self.assertEqual(imu["source"]["topic"], "/go2/imu")
        self.assertEqual(imu["display_name"], "MID360 IMU")
        self.assertEqual(config("map_stream")["ros"]["inputs"]["imu"]["topic"], "/livox/imu")

    def test_video_matches_observed_usb2_rgb_profile(self):
        video = config("video")
        self.assertEqual([video[key] for key in ("output_width", "output_height", "framerate",
                         "srt_port", "srt_latency_ms", "bitrate_kbps")], [640, 480, 15, 9000, 120, 2500])
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn('CAMERA_SERIAL="339222070647"', script)
        for argument in ("color_fps:=15", "enable_depth:=false", "enable_infra1:=false",
                         "enable_infra2:=false", "enable_gyro:=false", "enable_accel:=false", "publish_tf:=false"):
            self.assertIn(argument, script)

    def test_on_demand_mapping_navigation_share_exclusive_lock(self):
        mapping = config("map_stream")
        relocation = config("relocalization")
        lock = "lock_file:=/home/unitree/ccs_edge_ws/run/go2_stack.lock"
        self.assertIn(lock, mapping["integrations"]["fast_lio"]["launch_args"])
        self.assertIn(lock, relocation["ros"]["stages"][0]["args"])
        self.assertEqual(mapping["ros"]["frames"]["map"], "lio_odom")
        self.assertEqual(mapping["artifacts"]["frame"], "odom")
        self.assertEqual(mapping["integrations"]["mapping_prerequisites"]["launch_file"],
                         "mapping_prerequisites_go2_robot3.launch")
        self.assertEqual(config("task_control")["adapter"]["navigation_management"], "attach")

    def test_task_control_has_persistent_safety_and_matching_clock_tolerance(self):
        task = config("task_control")
        adapter = task["adapter"]
        self.assertTrue(adapter["auto_arm_on_schedule"])
        self.assertTrue(adapter["auto_disarm_on_terminal"])
        self.assertEqual(adapter["navigation_reset_service"], "/go2_navigation_supervisor/reset")
        self.assertEqual(adapter["control_enable_service"], "/go2_sdk_bridge_real/enable")
        self.assertEqual(adapter["emergency_stop_state_file"],
                         "/home/unitree/ccs_edge_ws/run/state/go2_task_safety.json")
        self.assertEqual(task["timeouts"]["utc_tolerance_seconds"], 2.0)
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        self.assertIn("--max-offset 2", script)
        self.assertNotIn("--set", script)
        self.assertFalse(list(PROFILE.glob("*.service")))

    def test_startup_gates_task_consumer_and_teardown_uses_fresh_confirmation(self):
        script = (PROFILE / "start_ccs_edge_dev.sh").read_text(encoding="utf-8")
        task_start = script.index("launch 8 epgeneral_task_control")
        self.assertLess(script.index('"${READINESS}" inputs'), task_start)
        self.assertLess(script.index("Fresh disabled state was lost before task startup"), task_start)
        shutdown = script[script.index("shutdown_all() {"):script.index("for setup in")]
        self.assertLess(shutdown.index('stop_process "${PIDS[8]}"'), shutdown.index("data: false"))
        self.assertLess(shutdown.index("data: false"), shutdown.index('"${READINESS}" disabled'))
        self.assertIn('[[ "${ROSCORE_MANAGED}" == true ]]', shutdown)
        self.assertNotIn("killall", script)
        self.assertNotIn("pkill", script)
        self.assertNotIn("bringup.launch", script)
        self.assertNotIn("data: true", script)
        for name in ("mapping_prerequisites_go2_robot3.launch", "mapping_fast_lio.launch",
                     "export_occupancy.launch", "navigation_guard.launch", "navigation.launch"):
            self.assertIn(name, script)

    def test_scripts_are_linux_text_and_parse(self):
        for path in (PROFILE / "scripts").glob("*.py"):
            self.assertNotIn(b"\r", path.read_bytes())
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        script = PROFILE / "start_ccs_edge_dev.sh"
        self.assertNotIn(b"\r", script.read_bytes())
        git = shutil.which("git")
        bash = Path(git).parent.parent / "bin/bash.exe" if git else Path("/nonexistent")
        executable = str(bash) if bash.exists() else shutil.which("bash")
        if not executable:
            self.skipTest("Bash is not installed")
        result = subprocess.run([executable, "-n", str(script)], capture_output=True)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode("utf-8", errors="replace"))

    def test_ros_contract_probe_is_python38_and_cannot_name_production_endpoints(self):
        path = PROFILE / "verify_ros_contract.py"
        source = path.read_text(encoding="utf-8")
        self.assertNotIn(b"\r", path.read_bytes())
        tree = ast.parse(source, filename=str(path), feature_version=(3, 8))
        compile(tree, str(path), "exec")
        for forbidden in ("/go2_sdk_bridge_real", "/go2_navigation_supervisor", "/cmd_vel",
                          "/go2/control/enabled", "/go2/diagnostics", "11311"):
            self.assertNotIn(forbidden, source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "rospy" \
                        and node.func.attr in ("Service", "ServiceProxy", "Publisher", "Subscriber"):
                    self.assertIsInstance(node.args[0], ast.Constant)
                    self.assertTrue(node.args[0].value.startswith("/ccs_probe/"))
        self.assertIn('if os.environ.get("ROS_MASTER_URI") != uri:', source)
        self.assertIn('probe.bind(("127.0.0.1", 11321))', source)
        self.assertIn('os.environ["ROS_NAMESPACE"] = "/ccs_probe"', source)
        self.assertIn('argv=[__file__]', source)
        self.assertIn('"production_service_calls": 0', source)
        self.assertIn('stop_owned_master(master)', source)


class Go2FreshnessTests(unittest.TestCase):
    def test_disabled_requires_post_subscription_diagnostics(self):
        self.assertTrue(READINESS.disabled_diagnostics(diagnostics(), 102.0, 100.0, 3.0))
        self.assertFalse(READINESS.disabled_diagnostics(diagnostics(stamp=99.0), 102.0, 100.0, 3.0))
        self.assertFalse(READINESS.disabled_diagnostics(diagnostics(stamp=98.0), 102.0, 90.0, 3.0))
        self.assertFalse(READINESS.disabled_diagnostics(diagnostics(stamp=103.0), 102.0, 100.0, 3.0))

    def test_disabled_requires_current_dds_and_disabled_motion(self):
        for fields in ({"enabled": "true"}, {"low_age": "4.0"}, {"sport_age": "4.0"},
                       {"low_age": "nan"}, {"sport_age": "-1"}, {"low_age": "unknown"}):
            with self.subTest(fields=fields):
                self.assertFalse(READINESS.disabled_diagnostics(diagnostics(**fields), 102.0, 100.0, 3.0))


class Go2SntpTests(unittest.TestCase):
    def response_socket(self, unsynchronized=False, forged=False):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.getpeername.return_value = ("192.168.50.101", 123)

        def receive(_size):
            packet = bytearray(48)
            packet[0] = 0xE4 if unsynchronized else 0x24
            packet[1] = 2
            request = sock.send.call_args.args[0]
            packet[24:32] = b"\x00" * 8 if forged else request[40:48]
            packet[32:40] = struct.pack("!II", int(SNTP.NTP_DELTA + 1800000001), 0)
            packet[40:48] = packet[32:40]
            return bytes(packet)

        sock.recv.side_effect = receive
        return sock

    def test_sntp_computes_midpoint_offset_without_changing_clock(self):
        with mock.patch.object(SNTP.socket, "socket", return_value=self.response_socket()), \
             mock.patch.object(SNTP.time, "time", side_effect=[1800000000.0, 1800000000.2]):
            result = SNTP.query("192.168.50.101", 3)
        self.assertAlmostEqual(result["offset_seconds"], 0.9, places=5)
        self.assertAlmostEqual(result["round_trip_seconds"], 0.2, places=5)

    def test_sntp_rejects_unsynchronized_and_unrelated_responses(self):
        for options in ({"unsynchronized": True}, {"forged": True}):
            with self.subTest(options=options), \
                 mock.patch.object(SNTP.socket, "socket", return_value=self.response_socket(**options)), \
                 mock.patch.object(SNTP.time, "time", side_effect=[1800000000.0, 1800000000.2]):
                with self.assertRaises(RuntimeError):
                    SNTP.query("192.168.50.101", 3)


if __name__ == "__main__":
    unittest.main()
