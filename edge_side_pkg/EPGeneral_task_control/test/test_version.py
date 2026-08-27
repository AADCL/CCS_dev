import ast
import os
import subprocess
import sys
import unittest

from epgeneral_task_control import __version__

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionTests(unittest.TestCase):
    def test_version_and_python36_syntax(self):
        self.assertEqual(__version__, "0.3.1")
        result = subprocess.run([sys.executable, os.path.join(PACKAGE, "scripts", "check_version.py")],
                                cwd=PACKAGE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for root, unused_dirs, files in os.walk(PACKAGE):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8") as stream:
                    source = stream.read()
                if sys.version_info[:2] <= (3, 6):
                    compile(source, path, "exec")
                else:
                    ast.parse(source, filename=path, feature_version=(3, 6))


if __name__ == "__main__":
    unittest.main()
