import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ccs_monitor.app_icons import app_icon, asset_icon, asset_icon_path, icon_path
from ccs_monitor.styles import ThemeMode


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_required_theme_icons_exist_and_load(self):
        for name in (
            "back", "expand", "close", "home", "device", "map", "mission", "bev", "upload",
            "mapstorage", "tasks", "time", "mqttbroker", "mqtt", "UDP", "camera",
        ):
            for mode in (ThemeMode.DAY, ThemeMode.NIGHT):
                self.assertTrue(icon_path(name, mode).is_file())
                self.assertFalse(app_icon(name, mode).isNull())
        self.assertFalse(app_icon("indoor", ThemeMode.DAY).isNull())
        self.assertFalse(app_icon("outdoor", ThemeMode.NIGHT).isNull())
        for filename in ("devices_online.svg", "devices_offline.svg"):
            self.assertTrue(asset_icon_path(filename).is_file())
            self.assertFalse(asset_icon(filename).isNull())

    def test_icon_resolution_does_not_depend_on_working_directory(self):
        original = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            self.assertTrue(icon_path("home", ThemeMode.DAY).is_file())
            self.assertFalse(app_icon("upload", ThemeMode.NIGHT).isNull())
            self.assertFalse(app_icon("expand", ThemeMode.NIGHT, rotation=90).isNull())
            self.assertTrue(asset_icon_path("devices_online.svg").is_file())
            self.assertFalse(asset_icon("devices_offline.svg").isNull())
        finally:
            os.chdir(original)


if __name__ == "__main__":
    unittest.main()
