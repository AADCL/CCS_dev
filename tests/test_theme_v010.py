from __future__ import annotations

import re
import unittest

from ccs_monitor.styles import BASE_STYLE, ThemeMode, build_stylesheet, theme_palette


class DayThemeTests(unittest.TestCase):
    def test_all_base_style_colors_are_translated_for_day_theme(self) -> None:
        night_colors = {item.lower() for item in re.findall(r"#[0-9a-fA-F]{6}", BASE_STYLE)}
        day_style = build_stylesheet(ThemeMode.DAY).lower()
        palette_colors = {
            value.lower() for value in vars(theme_palette(ThemeMode.DAY)).values()
            if isinstance(value, str) and value.startswith("#")
        }
        allowed_status_colors = {"#fff2d4", "#ffe2e6", "#ffd4da"}
        remaining = {color for color in night_colors if color in day_style and color not in palette_colors | allowed_status_colors}
        self.assertEqual(remaining, set())

    def test_selected_device_card_uses_day_palette(self) -> None:
        palette = theme_palette(ThemeMode.DAY)
        style = build_stylesheet(ThemeMode.DAY)
        self.assertIn(f"background: {palette.selected_background}", style)
        self.assertIn(f"color: {palette.text_strong}", style)
