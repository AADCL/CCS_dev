from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import posixpath
from urllib.parse import urlsplit

from ccs_monitor import runtime_paths
from ccs_monitor.installation import MARKER, install_tree, uninstall_tree, managed_path
from ccs_monitor.external_process import external_environment
from scripts.release_documentation import document_links

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ccs_release_builder", ROOT / "scripts/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class RuntimePathsTests(unittest.TestCase):
    def test_source_root_is_independent_of_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                self.assertEqual(runtime_paths.application_root(), ROOT)
            finally:
                os.chdir(previous)

    def test_frozen_data_and_resources_are_separate(self):
        with tempfile.TemporaryDirectory(prefix="CCS 中文 ") as directory:
            root = Path(directory)
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(root / "_internal"), create=True), patch.object(sys, "executable", str(root / "CCS.exe")):
                self.assertEqual(runtime_paths.application_root(), root.resolve())
                self.assertEqual(runtime_paths.resource_root(), root / "_internal")
                runtime_paths.prepare_storage()
                self.assertTrue((root / "config").is_dir())
                command = runtime_paths.fusion_worker_command(root / "a.json", root / "b.json")
                self.assertIn("ccs-map-fusion-worker", command[0])
                self.assertNotIn("-m", command)
                self.assertFalse((root / "_internal/data").exists())

    def test_unwritable_storage_has_actionable_error(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "可写"):
                runtime_paths.prepare_storage()

    def test_external_library_path_is_restored(self):
        with patch("ccs_monitor.external_process.is_frozen", return_value=True), patch.object(sys, "platform", "linux"), patch.dict(os.environ, {"LD_LIBRARY_PATH": "/ccs/_internal", "LD_LIBRARY_PATH_ORIG": "/system/lib"}):
            self.assertEqual(external_environment()["LD_LIBRARY_PATH"], "/system/lib")
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/ccs/_internal")

class InstallationTests(unittest.TestCase):
    def payload(self, parent: Path, name: str, files: dict[str, str]) -> Path:
        root = parent / name
        for relative, text in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        builder.inventory(root, "installer")
        return root

    def test_upgrade_and_uninstall_preserve_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "安装 目录"
            first = self.payload(root, "one", {"CCS": "v1", "_internal/stale": "old", "config/devices.json": "default", "data/icon.svg": "default icon"})
            install_tree(first, target)
            (target / "config/devices.json").write_text("my devices")
            (target / "data/map.pcd").write_text("my map")
            second = self.payload(root, "two", {"CCS": "v2", "config/devices.json": "new default", "config/new.json": "new"})
            install_tree(second, target)
            self.assertEqual((target / "CCS").read_text(), "v2")
            self.assertFalse((target / "_internal/stale").exists())
            self.assertEqual((target / "config/devices.json").read_text(), "my devices")
            self.assertEqual((target / "data/map.pcd").read_text(), "my map")
            self.assertEqual((target / "config/new.json").read_text(), "new")
            with patch("ccs_monitor.installation.desktop_file", return_value=root / "none.desktop"):
                uninstall_tree(target)
            self.assertFalse((target / "CCS").exists())
            self.assertEqual((target / "config/devices.json").read_text(), "my devices")
            self.assertTrue((target / "data/map.pcd").exists())
            install_tree(first, target)
            self.assertEqual((target / "config/devices.json").read_text(), "my devices")

    def test_failed_upgrade_rolls_back_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "installed"
            one = self.payload(root, "one", {"CCS": "v1", "config/devices.json": "user"})
            two = self.payload(root, "two", {"CCS": "v2", "config/devices.json": "new"})
            install_tree(one, target)
            original = shutil.copy2
            def fail(source, destination, *args, **kwargs):
                if Path(source).name == "CCS":
                    raise OSError("disk full")
                return original(source, destination, *args, **kwargs)
            with patch("ccs_monitor.installation.shutil.copy2", side_effect=fail):
                with self.assertRaises(OSError):
                    install_tree(two, target)
            self.assertEqual((target / "CCS").read_text(), "v1")
            self.assertEqual((target / "config/devices.json").read_text(), "user")
            self.assertTrue((target / MARKER).is_file())

    def test_corrupt_payload_cannot_modify_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self.payload(root, "payload", {"CCS": "valid"})
            (payload / "CCS").write_text("corrupt")
            with self.assertRaisesRegex(ValueError, "Corrupt"):
                install_tree(payload, root / "target")
            self.assertFalse((root / "target").exists())

    def test_rejects_unmanaged_directory_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.payload(root, "payload", {"CCS": "v1"})
            other = root / "documents"
            other.mkdir()
            (other / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                install_tree(source, other)
            for relative in ("../victim", "/etc/file", "C:/Windows/file", "config/../../victim"):
                with self.assertRaises(ValueError):
                    managed_path(other, relative)
            self.assertEqual((other / "keep.txt").read_text(), "keep")

class ReleaseContentsTests(unittest.TestCase):
    def test_script_staging_normalizes_crlf_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in (".sh", ".py"):
                source = root / ("source" + suffix)
                content = b"#!/usr/bin/env bash\r\nprintf 'ok\\n'\r\n"
                source.write_bytes(content)
                target = root / "staged" / source.name
                builder.copy_file(source, target)
                self.assertEqual(target.read_bytes(), content.replace(b"\r\n", b"\n"))
                self.assertEqual(source.read_bytes(), content)

    def assert_documentation_links(self, archive):
        names = set(archive.namelist())
        for name in names:
            if name.endswith((".sh", ".py")):
                self.assertNotIn(b"\r\n", archive.read(name), name)
            if not name.endswith(".md"):
                continue
            for link in document_links(archive.read(name).decode("utf-8")):
                parts = urlsplit(link)
                if parts.scheme or parts.netloc or not parts.path:
                    continue
                from urllib.parse import unquote
                target = posixpath.normpath(posixpath.join(
                    posixpath.dirname(name), unquote(parts.path)))
                self.assertTrue(target in names or any(
                    item.startswith(target.rstrip("/") + "/") for item in names),
                    f"{name}: {link}")

    def test_portable_uses_clean_defaults_and_contains_no_live_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "dist"
            output.mkdir()
            artifact = builder.portable(root, output)
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
                base = f"CCS-{builder.VERSION}/"
                devices = json.loads(archive.read(base + "config/devices.json"))
                self.assertEqual(devices["devices"], [])
                self.assertIn(base + "uv.lock", names)
                self.assertIn(base + "scripts/setup_env.sh", names)
                self.assertFalse(any("/edge_side_pkg/" in n or "/map_server/" in n or "/task_server/" in n or "/__pycache__/" in n for n in names))
                self.assertIn(base + "ccs_monitor/runtime_paths.py", names)
                self.assertIn(base + "release-manifest.json", names)
                self.assertIn(base + "docs/edge/documents/INTERFACE_REFERENCE.md", names)
                self.assertIn(base + "docs/edge/documents/USER_MANUAL.md", names)
                self.assert_documentation_links(archive)

    def test_edge_has_exactly_eight_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "dist"
            output.mkdir()
            artifact = builder.edge(root, output)
            with zipfile.ZipFile(artifact) as archive:
                packages = {
                    Path(name).parts[2]
                    for name in archive.namelist()
                    if len(Path(name).parts) == 4
                    and Path(name).parts[1] == "edge_side_pkg"
                    and Path(name).name == "package.xml"
                }
                self.assertEqual(packages, set(builder.EDGE_PACKAGES))
                self.assertTrue(any(n.endswith("epgeneral_video_srt_node.cpp") for n in archive.namelist()))
                self.assertTrue(any(n.endswith("ccs-edge-dev.service") for n in archive.namelist()))
                base = f"CCS-{builder.VERSION}-edge/edge_side_pkg/"
                for document in ("INTERFACE_REFERENCE.md", "USER_MANUAL.md"):
                    self.assertIn(base + "documents/" + document, archive.namelist())
                for package in builder.EDGE_PACKAGES:
                    self.assertIn(base + package + "/README.md", archive.namelist())
                self.assert_documentation_links(archive)

    def test_theme_ini_migrates_once_and_is_portable(self):
        from PySide6.QtCore import QSettings
        from ccs_monitor.styles import local_settings, load_theme_mode, save_theme_mode, ThemeMode
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            legacy = QSettings(str(root / "legacy.ini"), QSettings.Format.IniFormat)
            legacy.setValue("appearance/theme", ThemeMode.DAY.value)
            legacy.sync()
            original = QSettings
            def settings(*args):
                return legacy if args == ("CCS", "CCS Device Monitor") else original(*args)
            with patch("ccs_monitor.runtime_paths.application_root", return_value=root), patch("ccs_monitor.runtime_paths.is_frozen", return_value=False), patch("ccs_monitor.styles.QSettings", side_effect=settings) as factory:
                factory.Format = original.Format
                self.assertEqual(load_theme_mode(), ThemeMode.DAY)
                save_theme_mode(ThemeMode.NIGHT)
                legacy.setValue("appearance/theme", ThemeMode.DAY.value)
                self.assertEqual(load_theme_mode(), ThemeMode.NIGHT)
                self.assertTrue((root / "config/appearance.ini").is_file())

if __name__ == "__main__":
    unittest.main()
