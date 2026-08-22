import os
import shutil
import subprocess
import unittest


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_NAMES = (
    "start_fast_lio.sh", "stop_fast_lio.sh", "abort_fast_lio.sh", "generate_pgm.sh",
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


if __name__ == "__main__":
    unittest.main()
