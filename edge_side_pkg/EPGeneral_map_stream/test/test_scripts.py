import os
import shutil
import subprocess
import tempfile
import unittest


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_NAMES = (
    "start_fast_lio.sh", "stop_fast_lio.sh", "abort_fast_lio.sh", "save_map.sh",
    "generate_pgm.sh",
    "scout_mapping_stack.sh", "scout_finalize_map.sh",
)


class ScriptTests(unittest.TestCase):
    def test_wrappers_are_installed_and_do_not_use_eval(self):
        with open(os.path.join(PACKAGE, "CMakeLists.txt"), "r", encoding="utf-8") as stream:
            cmake = stream.read()
        for name in SCRIPT_NAMES:
            path = os.path.join(PACKAGE, "scripts", name)
            self.assertTrue(os.path.isfile(path), name)
            with open(path, "r", encoding="utf-8") as stream:
                source = stream.read()
            self.assertNotIn("eval ", source)
            self.assertIn(name, cmake)
        with open(os.path.join(PACKAGE, "scripts", "stop_fast_lio.sh"),
                  "r", encoding="utf-8") as stream:
            self.assertIn("process-group leader", stream.read())

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash"),
                     "native Bash is required for syntax validation")
    def test_bash_syntax(self):
        for name in SCRIPT_NAMES:
            result = subprocess.run(
                ["bash", "-n", os.path.join(PACKAGE, "scripts", name)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_mapping_prerequisites_starts_map_accumulator(self):
        launch_path = os.path.join(PACKAGE, "launch", "mapping_prerequisites.launch")
        with open(launch_path, "r", encoding="utf-8") as stream:
            launch = stream.read()
        self.assertIn("$(find go2_map_accumulator)/launch/map_accumulator.launch", launch)

        start_path = os.path.join(PACKAGE, "scripts", "start_fast_lio.sh")
        with open(start_path, "r", encoding="utf-8") as stream:
            start = stream.read()
        self.assertIn("/go2_map_accumulator)", start)

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash") or not shutil.which("setsid"),
                     "native Bash and setsid are required")
    def test_fast_lio_starts_before_mapping_prerequisites_and_abort_cleans_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = os.path.join(directory, "bin")
            os.makedirs(binary)
            events = os.path.join(directory, "events.log")

            def executable(name, source):
                path = os.path.join(binary, name)
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write(source)
                os.chmod(path, 0o755)
                return path

            executable("rospack", "#!/usr/bin/env bash\nexit 0\n")
            executable("rosnode", """#!/usr/bin/env bash
events=$(cat "${FAKE_ROS_EVENTS}" 2>/dev/null || true)
if grep -q '^fast_lio ' <<<"${events}"; then
  printf '%s\n' /laserMapping
fi
if grep -q '^epgeneral_map_stream ' <<<"${events}"; then
  printf '%s\n' /go2_tf_manager /go2_pose_adapter /cloud_to_base /cloud_world_to_odom /go2_map_accumulator
fi
""")
            executable("roslaunch", """#!/usr/bin/env bash
if [[ "${1:-}" == "--files" ]]; then exit 0; fi
printf '%s\n' "$*" >>"${FAKE_ROS_EVENTS}"
trap 'exit 0' INT TERM
while true; do sleep 0.2; done
""")
            setup = os.path.join(directory, "setup.bash")
            with open(setup, "w", encoding="utf-8") as stream:
                stream.write('export PATH="%s:$PATH"\n' % binary)
            extrinsics = os.path.join(directory, "extrinsics.yaml")
            with open(extrinsics, "w", encoding="utf-8") as stream:
                stream.write("""frames:
  odom: odom
  robot_init: robot_init
  base_footprint: base_footprint
  base_link: base_link
  lidar_link: lidar_link
  camera_link: camera_link
lio_frames: {world: lio_odom, body: body_lio}
mid360_mount:
  base_link_to_lidar_link: {x: 0.1, y: 0.0, z: 0.2, roll_deg: 0.0, pitch_deg: 30.0, yaw_deg: 0.0}
d435i_mount:
  base_link_to_camera_link: {x: 0.3, y: 0.0, z: 0.1, roll_deg: 0.0, pitch_deg: 0.0, yaw_deg: 0.0}
""")
            start = os.path.join(directory, "start_fast_lio.sh")
            shutil.copy2(os.path.join(PACKAGE, "scripts", "start_fast_lio.sh"), start)
            os.chmod(start, 0o755)
            pid_file = os.path.join(directory, "mapping.pid")
            log_file = os.path.join(directory, "mapping.log")
            existing_pcd = os.path.join(directory, "output", "public_map.pcd")
            os.makedirs(os.path.dirname(existing_pcd))
            with open(existing_pcd, "w", encoding="ascii") as stream:
                stream.write("existing map")
            environment = dict(os.environ, FAKE_ROS_EVENTS=events)
            checked = subprocess.run([
                start, "--check", setup, extrinsics, setup,
                "epgeneral_map_stream", "mapping_prerequisites.launch",
                "fast_lio", "mapping.launch",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                env=environment, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            result = subprocess.run([
                start, setup, extrinsics, "2", setup,
                "epgeneral_map_stream", "mapping_prerequisites.launch",
                "fast_lio", "mapping.launch", pid_file, log_file,
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(events, "r", encoding="utf-8") as stream:
                launches = stream.read().splitlines()
            self.assertEqual(len(launches), 2)
            self.assertTrue(launches[0].startswith("fast_lio "))
            self.assertTrue(launches[1].startswith("epgeneral_map_stream "))
            self.assertTrue(os.path.isfile(pid_file))
            self.assertTrue(os.path.isfile(pid_file + ".ready"))
            with open(existing_pcd, "r", encoding="ascii") as stream:
                self.assertEqual(stream.read(), "existing map")

            aborted = subprocess.run([
                "bash", os.path.join(PACKAGE, "scripts", "abort_fast_lio.sh"),
                pid_file, "2",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                timeout=10)
            self.assertEqual(aborted.returncode, 0, aborted.stderr)
            self.assertFalse(os.path.exists(pid_file))
            self.assertFalse(os.path.exists(pid_file + ".ready"))
            self.assertFalse(os.path.exists(pid_file + ".stopping"))
            with open(log_file, "r", encoding="utf-8") as stream:
                supervisor_log = stream.read()
            self.assertLess(
                supervisor_log.index("stage=fast_lio action=ready"),
                supervisor_log.index("stage=prerequisites action=start"))
            self.assertNotIn("exited unexpectedly", supervisor_log)

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash")
                     or not shutil.which("timeout"),
                     "native Bash and timeout are required")
    def test_save_map_sources_setup_and_calls_exact_service(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = os.path.join(directory, "bin")
            output = os.path.join(directory, "maps", "current", "public_map.pcd")
            event = os.path.join(directory, "service.log")
            os.makedirs(binary)
            os.makedirs(os.path.dirname(output))
            rosservice = os.path.join(binary, "rosservice")
            with open(rosservice, "w", encoding="utf-8") as stream:
                stream.write("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >\"$SAVE_EVENT\"\n")
            os.chmod(rosservice, 0o755)
            setup = os.path.join(directory, "setup.bash")
            with open(setup, "w", encoding="utf-8") as stream:
                stream.write('export PATH="%s:$PATH"\n' % binary)
            environment = dict(os.environ, SAVE_EVENT=event)
            result = subprocess.run([
                "bash", os.path.join(PACKAGE, "scripts", "save_map.sh"),
                setup, "/go2_map_accumulator/save", output, "2",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(event, "r", encoding="utf-8") as stream:
                self.assertEqual(stream.read().strip(),
                                 "call /go2_map_accumulator/save")


if __name__ == "__main__":
    unittest.main()
