import json
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

from epgeneral_map_stream.config import (
    ConfigError, build_integration_commands, load_config, scout_filtered_pcd_path,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOSITORY = os.path.dirname(os.path.dirname(ROOT))
REPOSITORY_PROFILE = os.path.join(REPOSITORY, "edge_side_pkg", "deploy", "scout_mini")
DEPLOYED_PROFILE = "/home/nvidia/ccs_edge_ws/config/scout_mini"
PROFILE = (REPOSITORY_PROFILE if os.path.isfile(os.path.join(
    REPOSITORY_PROFILE, "config", "map_stream.yaml")) else DEPLOYED_PROFILE)


class ScoutBackendTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(
            os.path.join(PROFILE, "config", "map_stream.yaml") if PROFILE == REPOSITORY_PROFILE
            else os.path.join(PROFILE, "map_stream.yaml"),
            os.path.join(PROFILE, "config", "device.yaml") if PROFILE == REPOSITORY_PROFILE
            else os.path.join(PROFILE, "device.yaml"),
        )
        self.values = {
            "map_id": "map-1", "device_id": "UGV_001", "session_id": "session-1",
            "session_dir": "/tmp/session-1", "pcd_path": "/tmp/session-1/map.pcd",
            "pgm_path": "/tmp/session-1/map.pgm", "yaml_path": "/tmp/session-1/map.yaml",
            "map_name": "20260824_213015",
        }

    def test_commands_use_exact_order_without_source_or_rosservice(self):
        commands = build_integration_commands(self.config, self.values)
        start = commands["start_fast_lio"]
        self.assertEqual(start[6], "20260824_213015")
        self.assertEqual(start[-8:], [
            "scout_system_bringup", "fastlio_mapping_scout.launch",
            "scout_pointcloud_mapper", "pointcloud_mapper.launch",
            "scout_tf_manager", "tf_manager.launch",
            "scout_pose_adapter", "pose_adapter.launch",
        ])
        rendered = " ".join(" ".join(value) for value in commands.values()
                            if isinstance(value, list) and value and isinstance(value[0], str))
        self.assertNotIn("source", rendered)
        self.assertNotIn("rosservice", rendered)
        self.assertEqual(commands["generate_pgm"][3], "20260824_213015")
        self.assertNotIn("map-1", commands["generate_pgm"])
        self.assertNotIn("session-1", commands["generate_pgm"])
        self.assertEqual(
            scout_filtered_pcd_path(self.config, self.values["map_name"]),
            os.path.abspath(
                "/home/nvidia/livox_fastlio/maps/20260824_213015/filtered_camera_init.pcd"))

    def test_profile_frames_and_artifacts_match_scout(self):
        self.assertEqual(self.config["integration_backend"], "scout_finalize")
        self.assertEqual(self.config["cloud_topic"], "/cloud_registered_body")
        self.assertEqual(self.config["pose_topic"], "/fastlio_odom")
        self.assertEqual(self.config["map_frame"], "odom")
        self.assertEqual(self.config["artifact_frame"], "map")
        self.assertEqual(self.config["scout_mapper_package"], "scout_pointcloud_mapper")
        self.assertEqual(self.config["scout_filtered_pcd_filename"],
                         "filtered_camera_init.pcd")

    def test_invalid_or_wrong_identifier_cannot_be_used_as_map_name(self):
        for invalid in ("", "map-1", "session-1", "20260824_21301", "../map"):
            values = dict(self.values, map_name=invalid)
            with self.assertRaisesRegex(ConfigError, "map_name"):
                build_integration_commands(self.config, values)

    def test_ground_station_has_device_specific_frames(self):
        ground_config = os.path.join(REPOSITORY, "config", "map_building.json")
        if not os.path.isfile(ground_config):
            self.skipTest("ground station repository is unavailable")
        with open(ground_config,
                  "r", encoding="utf-8") as stream:
            frames = json.load(stream)["device_frames"]["UGV_001"]
        self.assertEqual(frames, {
            "remote_mapping": "odom", "preview_source": "odom",
            "remote_artifact": "map",
        })

    def test_scripts_do_not_source_workspaces(self):
        for name in ("scout_mapping_stack.sh", "scout_finalize_map.sh"):
            with open(os.path.join(ROOT, "scripts", name), "r", encoding="utf-8") as stream:
                script = stream.read()
            self.assertNotIn("source ", script)
            self.assertNotIn("rosservice", script)
        with open(os.path.join(ROOT, "scripts", "scout_finalize_map.sh"),
                  "r", encoding="utf-8") as stream:
            self.assertIn('"${MAP_NAME}" --replace-raw', stream.read())

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash")
                     or not shutil.which("setsid"),
                     "native Bash and setsid are required")
    def test_supervisor_starts_four_stages_and_flushes_mapper_first(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = os.path.join(directory, "bin")
            os.makedirs(binary)
            events = os.path.join(directory, "events.log")

            def executable(name, source):
                path = os.path.join(binary, name)
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write(source)
                os.chmod(path, 0o755)

            executable("rosnode", r'''#!/usr/bin/env bash
printf '%s\n' /laserMapping /scout_pointcloud_mapper /scout_tf_manager \
  /scout_geometry_tf_publisher /scout_pose_adapter
''')
            executable("roslaunch", r'''#!/usr/bin/env bash
if [[ "${1:-}" == "--files" ]]; then exit 0; fi
case "$1" in
  scout_system_bringup) stage=fast ;;
  scout_pointcloud_mapper) stage=mapper ;;
  scout_tf_manager) stage=tf ;;
  scout_pose_adapter) stage=pose ;;
  *) exit 2 ;;
esac
printf 'start:%s:%s\n' "${stage}" "$*" >>"${SCOUT_TEST_EVENTS}"
trap 'printf "stop:%s\n" "${stage}" >>"${SCOUT_TEST_EVENTS}"; exit 0' INT TERM
while true; do sleep 0.1; done
''')
            script = os.path.join(ROOT, "scripts", "scout_mapping_stack.sh")
            pid_file = os.path.join(directory, "mapping.pid")
            log_file = os.path.join(directory, "mapping.log")
            environment = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                               SCOUT_TEST_EVENTS=events)
            arguments = [
                script, "--start", pid_file, log_file, "2", "2", "20260824_213015",
                "scout_system_bringup", "fastlio_mapping_scout.launch",
                "scout_pointcloud_mapper", "pointcloud_mapper.launch",
                "scout_tf_manager", "tf_manager.launch",
                "scout_pose_adapter", "pose_adapter.launch",
            ]
            started = subprocess.run(arguments, env=environment, timeout=15,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     universal_newlines=True)
            with open(log_file, "r", encoding="utf-8") as stream:
                supervisor_log = stream.read()
            with open(events, "r", encoding="utf-8") as stream:
                event_log = stream.read()
            self.assertEqual(
                started.returncode, 0,
                started.stderr + supervisor_log + "\nEVENTS:\n" + event_log)
            stopped = subprocess.run(
                [script, "--stop", pid_file, "2"], env=environment, timeout=15,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            with open(events, "r", encoding="utf-8") as stream:
                lines = stream.read().splitlines()
            starts = [line for line in lines if line.startswith("start:")]
            stops = [line for line in lines if line.startswith("stop:")]
            self.assertEqual([line.split(":", 2)[1] for line in starts],
                             ["fast", "mapper", "tf", "pose"])
            self.assertIn("rviz:=false", starts[0])
            self.assertIn("map_name:=20260824_213015", starts[1])
            self.assertEqual(stops, ["stop:mapper", "stop:fast", "stop:pose", "stop:tf"])
            self.assertFalse(os.path.exists(pid_file))

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash")
                     or not shutil.which("timeout"),
                     "native Bash and timeout are required")
    def test_finalize_uses_exact_map_name_replace_raw_and_keeps_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = os.path.join(directory, "bin")
            package = os.path.join(directory, "scout_map_tools")
            scripts = os.path.join(package, "scripts")
            map_root = os.path.join(directory, "maps")
            map_name = "20260824_213015"
            map_dir = os.path.join(map_root, map_name)
            targets = os.path.join(directory, "targets")
            os.makedirs(binary)
            os.makedirs(scripts)
            os.makedirs(map_dir)
            with open(os.path.join(scripts, "finalize_map.py"), "w", encoding="ascii") as stream:
                stream.write("#!/usr/bin/env python3\n")
            os.chmod(os.path.join(scripts, "finalize_map.py"), 0o755)
            with open(os.path.join(map_dir, "filtered_camera_init.pcd"), "wb") as stream:
                stream.write(b"filtered")
            rospack = os.path.join(binary, "rospack")
            with open(rospack, "w", encoding="utf-8") as stream:
                stream.write('#!/usr/bin/env bash\nprintf "%s\\n" "${SCOUT_TOOLS_PACKAGE}"\n')
            os.chmod(rospack, 0o755)
            rosrun = os.path.join(binary, "rosrun")
            with open(rosrun, "w", encoding="utf-8") as stream:
                stream.write(r'''#!/usr/bin/env bash
printf '%s\n' "$*" >"${SCOUT_FINALIZE_EVENT}"
map_dir="${SCOUT_MAP_ROOT}/$3"
printf 'raw' >"${map_dir}/raw_camera_init.pcd"
printf 'public' >"${map_dir}/public_map.pcd"
printf 'pgm' >"${map_dir}/map.pgm"
printf 'yaml' >"${map_dir}/map.yaml"
printf 'map_name: %s\n' "$3" >"${map_dir}/map_metadata.yaml"
''')
            os.chmod(rosrun, 0o755)
            event = os.path.join(directory, "finalize.log")
            environment = dict(
                os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                SCOUT_TOOLS_PACKAGE=package, SCOUT_FINALIZE_EVENT=event,
                SCOUT_MAP_ROOT=map_root)
            os.makedirs(targets)
            command = [
                os.path.join(ROOT, "scripts", "scout_finalize_map.sh"),
                "scout_map_tools", "finalize_map.py", map_name, map_root,
                os.path.join(targets, "map.pcd"), os.path.join(targets, "map.pgm"),
                os.path.join(targets, "map.yaml"), os.path.join(directory, "command.log"), "5",
            ]
            result = subprocess.run(command, env=environment, timeout=10,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    universal_newlines=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(event, "r", encoding="utf-8") as stream:
                self.assertEqual(stream.read().strip(),
                                 "scout_map_tools finalize_map.py %s --replace-raw" % map_name)
            self.assertTrue(os.path.isfile(os.path.join(map_dir, "filtered_camera_init.pcd")))
            self.assertEqual(set(os.listdir(targets)), {"map.pcd", "map.pgm", "map.yaml"})


if __name__ == "__main__":
    unittest.main()
