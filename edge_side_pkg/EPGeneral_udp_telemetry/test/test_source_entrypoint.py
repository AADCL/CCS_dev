import os
import subprocess
import sys
import unittest


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SourceEntrypointTests(unittest.TestCase):
    def test_source_script_imports_package_without_catkin_pythonpath(self):
        environment = dict(os.environ)
        package_source = os.path.abspath(os.path.join(PACKAGE, "src"))
        dependency_paths = [
            path for path in sys.path
            if path and os.path.abspath(path) != package_source
        ]
        environment["PYTHONPATH"] = os.pathsep.join(dependency_paths)
        script = os.path.join(PACKAGE, "scripts", "epgeneral_udp_telemetry_node.py")
        command = [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path(%r)" % script,
        ]
        result = subprocess.run(
            command,
            cwd=PACKAGE,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cmake_pins_python_and_registers_tests(self):
        with open(os.path.join(PACKAGE, "CMakeLists.txt"), "r", encoding="utf-8") as stream:
            cmake = stream.read()
        self.assertIn('set(PYTHON_EXECUTABLE "${PYTHON_EXECUTABLE}" CACHE FILEPATH', cmake)
        self.assertIn("catkin_add_nosetests(test)", cmake)


if __name__ == "__main__":
    unittest.main()
