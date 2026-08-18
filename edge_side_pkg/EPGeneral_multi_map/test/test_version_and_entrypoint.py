import ast
import os
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

from epgeneral_multi_map import __version__


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionAndEntrypointTests(unittest.TestCase):
    def test_version_is_consistent(self):
        xml_version = ET.parse(os.path.join(PACKAGE, "package.xml")).findtext("version")
        self.assertEqual(__version__, "0.1.0")
        self.assertEqual(xml_version, __version__)
        output = subprocess.check_output(
            [sys.executable, os.path.join(PACKAGE, "scripts", "check_version.py")],
            text=True,
        )
        self.assertIn("version 0.1.0 is consistent", output)

    def test_python_files_parse_as_python38(self):
        paths = []
        for root in (os.path.join(PACKAGE, "src"), os.path.join(PACKAGE, "scripts")):
            for folder, unused_dirs, files in os.walk(root):
                paths.extend(os.path.join(folder, name) for name in files if name.endswith(".py"))
        self.assertTrue(any(path.endswith("epgeneral_multi_map_node.py") for path in paths))
        for path in paths:
            with open(path, "r", encoding="utf-8") as stream:
                ast.parse(stream.read(), filename=path, feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
