import ast
import os
import subprocess
import sys
import unittest

from epgeneral_map_stream import __version__


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CompatibilityTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__, "0.13.2")
        result = subprocess.run(
            [sys.executable, os.path.join(PACKAGE, "scripts", "check_version.py")],
            cwd=PACKAGE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_python_files_parse_as_python36(self):
        for root, unused_dirs, files in os.walk(PACKAGE):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(root, name)
                    with open(path, "r", encoding="utf-8") as stream:
                        source = stream.read()
                    if sys.version_info[:2] <= (3, 6):
                        compile(source, path, "exec")
                    else:
                        ast.parse(source, filename=path, feature_version=(3, 6))

    def test_source_entrypoint_imports_without_catkin_pythonpath(self):
        environment = dict(os.environ)
        package_source = os.path.abspath(os.path.join(PACKAGE, "src"))
        environment["PYTHONPATH"] = os.pathsep.join(
            path for path in sys.path if path and os.path.abspath(path) != package_source
        )
        script = os.path.join(PACKAGE, "scripts", "epgeneral_map_stream_node.py")
        result = subprocess.run(
            [sys.executable, "-c", "import runpy; runpy.run_path(%r)" % script],
            cwd=PACKAGE, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
