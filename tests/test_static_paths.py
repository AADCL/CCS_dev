from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ccs_monitor.static_paths import StaticPathError, StaticPathResolver


class StaticPathResolverTests(unittest.TestCase):
    def test_resolves_relative_path_without_using_process_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "install" / "config" / "assets.json"
            assets = root / "install" / "data" / "assets"
            asset = assets / "plugin.py"
            asset.parent.mkdir(parents=True)
            asset.write_text("VALUE = 1\n", encoding="utf-8")
            resolver = StaticPathResolver(config, assets)
            previous = Path.cwd()
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            try:
                os.chdir(elsewhere)
                resolved = resolver.resolve("data/assets/plugin.py")
            finally:
                os.chdir(previous)
            self.assertEqual(resolved, asset.resolve())

    def test_rejects_relative_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = StaticPathResolver(
                root / "config" / "assets.json",
                root / "data" / "assets",
            )
            with self.assertRaises(StaticPathError):
                resolver.resolve("../outside.py", allow_missing=True)

    def test_legacy_absolute_path_prefers_current_installation_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_asset = root / "old" / "data" / "assets" / "plugin.py"
            current_asset = root / "current" / "data" / "assets" / "plugin.py"
            old_asset.parent.mkdir(parents=True)
            current_asset.parent.mkdir(parents=True)
            old_asset.write_text("VALUE = 'old'\n", encoding="utf-8")
            current_asset.write_text("VALUE = 'current'\n", encoding="utf-8")
            resolver = StaticPathResolver(
                root / "current" / "config" / "assets.json",
                current_asset.parent,
            )
            self.assertEqual(resolver.resolve(old_asset), current_asset.resolve())


if __name__ == "__main__":
    unittest.main()
