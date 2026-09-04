import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from ccs_monitor.app_icons import app_icon, asset_icon, asset_icon_path, icon_path, lab_logo_path
from ccs_monitor.runtime_paths import resource_root
from ccs_monitor.styles import ThemeMode, theme_palette
from ccs_monitor.widgets import CardIcon
from ccs_monitor.pages.home_page import MetricCard


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_required_theme_icons_exist_and_load(self):
        for name in (
            "back", "expand", "close", "home", "device", "map", "mission", "bev", "upload",
            "mapStorage", "taskStorage", "realTimeMapping", "UDPtask", "localization", "tasks", "time", "mqttbroker", "mqtt", "UDP", "camera",
        ):
            for mode in (ThemeMode.DAY, ThemeMode.NIGHT):
                self.assertTrue(icon_path(name, mode).is_file())
                self.assertFalse(app_icon(name, mode).pixmap(QSize(28, 28)).isNull())
        self.assertFalse(app_icon("indoor", ThemeMode.DAY).isNull())
        self.assertFalse(app_icon("outdoor", ThemeMode.NIGHT).isNull())
        for filename in ("devices_online.svg", "devices_offline.svg", "devices_warning.svg"):
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
            for name in ("mapStorage", "taskStorage", "realTimeMapping", "UDPtask", "localization",
                         "device", "camera", "mqtt", "mqttbroker", "tasks", "time", "UDP"):
                for mode in ThemeMode:
                    self.assertFalse(app_icon(name, mode).pixmap(28, 28).isNull())
            self.assertFalse(QPixmap(str(lab_logo_path())).isNull())
        finally:
            os.chdir(original)


    def test_application_assets_have_exact_names_single_svg_and_render(self):
        root = icon_path("mapStorage", ThemeMode.DAY).parent
        names = {path.name for path in root.iterdir()}
        for name in ("mapStorage", "taskStorage", "realTimeMapping", "UDPtask", "localization",
                     "device", "camera", "mqtt", "mqttbroker", "tasks", "time", "UDP"):
            for mode in ThemeMode:
                filename = f"{name}_{mode.value}.svg"
                self.assertIn(filename, names)
                path = root / filename
                self.assertEqual(ET.parse(path).getroot().tag, "{http://www.w3.org/2000/svg}svg")
                self.assertTrue(QSvgRenderer(str(path)).isValid(), filename)
        self.assertFalse(any(name.startswith(("mapstorage_", "taskStrorage_")) for name in names))
        for filename in ("devices_online.svg", "devices_offline.svg", "devices_warning.svg"):
            self.assertEqual(asset_icon_path(filename).parent, root)
            self.assertIn(filename, names)
            ET.parse(root / filename)
            self.assertTrue(QSvgRenderer(str(root / filename)).isValid())
            self.assertFalse(asset_icon(filename).pixmap(28, 28).isNull())
        logo = QPixmap(str(lab_logo_path()))
        self.assertFalse(logo.isNull())
        self.assertGreater(logo.width(), 0)
        self.assertGreater(logo.height(), 0)

    def test_product_logo_is_self_contained_and_renders_at_runtime_sizes(self):
        path = resource_root() / "ccs_monitor" / "assets" / "ccs_logo.svg"
        root = ET.parse(path).getroot()
        tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}

        self.assertEqual(root.attrib.get("viewBox"), "0 0 128 128")
        self.assertTrue({"title", "desc"}.issubset(tags))
        self.assertTrue(QSvgRenderer(str(path)).isValid())
        self.assertFalse(tags.intersection({"image", "text", "style", "filter"}))

        icon = QIcon(str(path))
        for size in (16, 28, 256):
            pixmap = icon.pixmap(size, size)
            self.assertFalse(pixmap.isNull(), f"CCS logo failed at {size}px")
            image = pixmap.toImage()
            visible_pixels = sum(
                image.pixelColor(x, y).alpha() > 0
                for y in range(image.height())
                for x in range(image.width())
            )
            self.assertGreater(visible_pixels, size * size // 2)

    def test_missing_or_invalid_icons_warn_without_removing_card_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "invalid_day.svg").write_text("not an SVG", encoding="utf-8")
            with patch("ccs_monitor.app_icons.APP_ICON_ROOT", root):
                for name in ("missing", "invalid"):
                    with self.assertLogs("ccs_monitor.app_icons", level="WARNING"):
                        card = MetricCard("测试统计", "42", icon_name=name)
                        card.set_theme(theme_palette(ThemeMode.DAY))
                    self.assertIsInstance(card.icon_label, CardIcon)
                    self.assertTrue(card.icon_label.isHidden())
                    self.assertEqual(card.caption_label.text(), "测试统计")
                    self.assertEqual(card.value_label.text(), "42")
                    self.assertEqual(card.icon_label.accessibleName(), "测试统计图标")
                    self.assertEqual(card.icon_label.toolTip(), "测试统计")


if __name__ == "__main__":
    unittest.main()
