import re
import unittest
from pathlib import Path

from epgeneral_mqtav.node import parse_args


ROOT = Path(__file__).resolve().parents[1]


class PythonRuntimeTests(unittest.TestCase):
    def test_cmake_requires_python_36_or_newer(self):
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("find_program(epgeneral_mqtav_PYTHON3_EXECUTABLE NAMES python3.6 python3)", cmake)
        self.assertIn("find_package(PythonInterp 3.6 REQUIRED)", cmake)
        self.assertIn("set(PYTHON_EXECUTABLE", cmake)

    def test_ros_executables_use_python3_directly(self):
        for script_name in ("epgeneral_mqtav_node.py", "check_version.py"):
            first_line = (ROOT / "scripts" / script_name).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first_line, "#!/usr/bin/python3")

    def test_arg_parser_ignores_roslaunch_private_remappings(self):
        args = parse_args([
            "--config-file", "/tmp/config.yaml",
            "--device-config-file", "/tmp/device.yaml",
            "--log-dir", "/tmp/epgeneral_mqtav-log",
            "__name:=epgeneral_mqtav",
            "__log:=/tmp/ros.log",
        ])
        self.assertEqual(args.config_file, "/tmp/config.yaml")
        self.assertEqual(args.device_config_file, "/tmp/device.yaml")
        self.assertEqual(args.log_dir, "/tmp/epgeneral_mqtav-log")
