import subprocess
import sys
import unittest
from pathlib import Path

from epgeneral_mqtav.version import get_version


ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_manifest_is_runtime_version_source(self):
        self.assertEqual(get_version(ROOT / "package.xml"), "0.3.0")

    def test_document_version_check_passes(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_version.py")],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
