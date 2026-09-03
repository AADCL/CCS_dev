import re
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from ccs_monitor import __version__


class VersionTests(unittest.TestCase):
    def test_version_is_semantic_triplet(self):
        self.assertEqual(__version__, "0.22.9")
        self.assertRegex(__version__, re.compile(r"^\d+\.\d+\.\d+$"))

    def test_project_metadata_matches_runtime(self):
        root = Path(__file__).resolve().parents[1]
        metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', metadata)

    def test_map_stream_package_versions_match(self):
        root = Path(__file__).resolve().parents[1] / "edge_side_pkg" / "EPGeneral_map_stream"
        package_version = ET.parse(root / "package.xml").getroot().findtext("version")
        init_text = (root / "src" / "epgeneral_map_stream" / "__init__.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(package_version, "0.13.1")
        self.assertIn('__version__ = "0.13.1"', init_text)

    def test_relocalization_package_versions_match(self):
        root = Path(__file__).resolve().parents[1] / "edge_side_pkg" / "EPGeneral_relocalization"
        package_version = ET.parse(root / "package.xml").getroot().findtext("version")
        init_text = (root / "src" / "epgeneral_relocalization" / "__init__.py").read_text(
            encoding="utf-8"
        )
        setup_text = (root / "setup.py").read_text(encoding="utf-8")
        self.assertEqual(package_version, "0.2.3")
        self.assertIn('__version__ = "0.2.3"', init_text)
        self.assertIn('version="0.2.3"', setup_text)

    def test_task_control_version_matches_manifest_and_source(self):
        root = Path(__file__).resolve().parents[1] / "edge_side_pkg" / "EPGeneral_task_control"
        package_version = ET.parse(root / "package.xml").getroot().findtext("version")
        init_text = (root / "src" / "epgeneral_task_control" / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(package_version, "0.4.4")
        self.assertIn('__version__ = "0.4.4"', init_text)


if __name__ == "__main__":
    unittest.main()
