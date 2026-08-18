from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette


class ThemeMode(str, Enum):
    DAY = "day"
    NIGHT = "night"


@dataclass(frozen=True)
class ThemePalette:
    mode: ThemeMode
    background: str
    page_background: str
    surface: str
    surface_alt: str
    elevated: str
    border: str
    border_strong: str
    text: str
    text_strong: str
    muted: str
    primary: str
    primary_soft: str
    primary_strong: str
    focus: str
    input_background: str
    selected_background: str
    hover_background: str
    chart_background: str
    chart_grid: str
    dashboard_background: str
    dashboard_panel: str
    dashboard_border: str
    dashboard_text: str
    dashboard_muted: str
    good: str
    warning: str
    error: str
    route_colors: tuple[str, str, str, str, str]


_PALETTES = {
    ThemeMode.NIGHT: ThemePalette(
        mode=ThemeMode.NIGHT,
        background="#050B14",
        page_background="#07111F",
        surface="#0B1828",
        surface_alt="#0F2134",
        elevated="#122A42",
        border="#1D3A55",
        border_strong="#2D5B7D",
        text="#EAF5FF",
        text_strong="#FFFFFF",
        muted="#8AA6BD",
        primary="#38BDF8",
        primary_soft="#123B5A",
        primary_strong="#0EA5E9",
        focus="#67D3FF",
        input_background="#0A1726",
        selected_background="#123C5D",
        hover_background="#15324D",
        chart_background="#071522",
        chart_grid="#21415C",
        dashboard_background="#040B14",
        dashboard_panel="#091827",
        dashboard_border="#1F5874",
        dashboard_text="#ECFAFF",
        dashboard_muted="#7895A9",
        good="#50D890",
        warning="#F6C453",
        error="#FF7180",
        route_colors=("#38D6FF", "#F6C453", "#F06DAA", "#78AFFF", "#9CEB75"),
    ),
    ThemeMode.DAY: ThemePalette(
        mode=ThemeMode.DAY,
        background="#F5F9FD",
        page_background="#EAF3FA",
        surface="#FFFFFF",
        surface_alt="#F0F6FB",
        elevated="#E4F0F8",
        border="#C8D9E8",
        border_strong="#8FB6D2",
        text="#18354D",
        text_strong="#0B2238",
        muted="#5D7489",
        primary="#1478C9",
        primary_soft="#DCEEFF",
        primary_strong="#0B5FA5",
        focus="#2D9BE8",
        input_background="#FFFFFF",
        selected_background="#D9EDFF",
        hover_background="#E4F2FF",
        chart_background="#F7FBFF",
        chart_grid="#C5D9E8",
        dashboard_background="#E7F2FA",
        dashboard_panel="#FFFFFF",
        dashboard_border="#77A9C9",
        dashboard_text="#0B2942",
        dashboard_muted="#55758C",
        good="#168A58",
        warning="#B56B00",
        error="#C4374E",
        route_colors=("#087FBA", "#B56B00", "#C04D89", "#3479C5", "#4B9C36"),
    ),
}


def theme_palette(mode: ThemeMode | str) -> ThemePalette:
    return _PALETTES[ThemeMode(mode)]


def load_theme_mode(settings: QSettings | None = None) -> ThemeMode:
    settings = settings or QSettings("CCS", "CCS Device Monitor")
    raw = settings.value("appearance/theme", ThemeMode.NIGHT.value)
    try:
        return ThemeMode(str(raw))
    except ValueError:
        return ThemeMode.NIGHT


def save_theme_mode(mode: ThemeMode | str, settings: QSettings | None = None) -> None:
    settings = settings or QSettings("CCS", "CCS Device Monitor")
    settings.setValue("appearance/theme", ThemeMode(mode).value)
    settings.sync()


def _replace_style_colors(style: str, palette: ThemePalette) -> str:
    replacements = {
        "#0b1118": palette.page_background,
        "#0b131b": palette.page_background,
        "#0e1720": palette.background,
        "#050b10": palette.dashboard_background,
        "#05080c": palette.dashboard_background,
        "#071018": palette.chart_background,
        "#071118": palette.chart_background,
        "#081119": palette.dashboard_background,
        "#070d13": palette.chart_background,
        "#0d151d": palette.surface_alt,
        "#0d161f": palette.input_background,
        "#101c26": palette.surface_alt,
        "#111820": palette.surface_alt,
        "#12241f": palette.selected_background,
        "#125a5d": palette.primary_strong,
        "#15313c": palette.dashboard_border,
        "#153b45": palette.hover_background,
        "#162633": palette.hover_background,
        "#173642": palette.dashboard_border,
        "#1b2835": palette.surface_alt,
        "#1b5360": palette.dashboard_border,
        "#243b4c": palette.hover_background,
        "#25323d": palette.border,
        "#268392": palette.border_strong,
        "#2a4b59": palette.border_strong,
        "#2bc4b8": palette.primary,
        "#2c7967": palette.border_strong,
        "#385266": palette.border_strong,
        "#5e6b79": palette.muted,
        "#647383": palette.muted,
        "#6a5125": palette.warning,
        "#708496": palette.border_strong,
        "#724046": palette.error,
        "#8e9cad": palette.muted,
        "#91a3b5": palette.muted,
        "#9aead1": palette.primary_strong,
        "#edb64d": palette.warning,
        "#ee4f9a": palette.error,
        "#ef8585": palette.error,
        "#efc477": palette.warning,
        "#f0b84a": palette.warning,
        "#09131b": palette.dashboard_panel,
        "#0a1d25": palette.input_background,
        "#0b1624": palette.page_background,
        "#101923": palette.surface,
        "#111a24": palette.surface,
        "#111d27": palette.surface_alt,
        "#121d28": palette.surface_alt,
        "#13202b": palette.hover_background,
        "#141d26": palette.input_background,
        "#15222d": palette.surface_alt,
        "#17232e": palette.hover_background,
        "#172733": palette.hover_background,
        "#1b2a38": palette.elevated,
        "#1e2b39": palette.border,
        "#1e3040": palette.border,
        "#223140": palette.border,
        "#223142": palette.border,
        "#223442": palette.border,
        "#263747": palette.border,
        "#263847": palette.border,
        "#2a3d4d": palette.border_strong,
        "#2b4051": palette.border_strong,
        "#2c4051": palette.border_strong,
        "#1f2d3a": palette.border,
        "#1c2a36": palette.border,
        "#1d2d39": palette.border,
        "#182733": palette.border,
        "#355267": palette.border_strong,
        "#4a6b83": palette.border_strong,
        "#4b687d": palette.border_strong,
        "#4b7187": palette.border_strong,
        "#506779": palette.border_strong,
        "#68d9d1": palette.primary,
        "#6fe6db": palette.primary,
        "#56d9cf": palette.focus,
        "#2b6359": palette.border_strong,
        "#2b7f88": palette.border_strong,
        "#286b72": palette.border_strong,
        "#0c4146": palette.primary_soft,
        "#0e2831": palette.primary_soft,
        "#10343b": palette.primary_soft,
        "#0d222b": palette.hover_background,
        "#0d2530": palette.primary_soft,
        "#37c5a0": palette.primary,
        "#35e0cf": palette.primary,
        "#2ccabd": palette.primary,
        "#8de2c7": palette.focus,
        "#91e7cb": palette.focus,
        "#7be0bd": palette.focus,
        "#a0ead2": palette.focus,
        "#e7edf5": palette.text,
        "#e5edf5": palette.text,
        "#e8eef5": palette.text,
        "#dce6ef": palette.text,
        "#dfe8f2": palette.text,
        "#f4f7fb": palette.text_strong,
        "#f5f8fc": palette.text_strong,
        "#f0f5fa": palette.text_strong,
        "#f1f5fa": palette.text_strong,
        "#e9eff6": palette.text,
        "#cdd8e3": palette.text,
        "#dbe5ef": palette.text,
        "#dcebf2": palette.text,
        "#f2f8f6": palette.text_strong,
        "#dffffb": palette.text_strong,
        "#e8ffff": palette.text_strong,
        "#efffff": palette.text_strong,
        "#bdfbf4": palette.text_strong,
        "#c4fff7": palette.text_strong,
        "#c9fbf5": palette.text_strong,
        "#d3e8ef": palette.text,
        "#8290a2": palette.muted,
        "#8492a5": palette.muted,
        "#76879a": palette.muted,
        "#68788a": palette.muted,
        "#6f8194": palette.muted,
        "#73869a": palette.muted,
        "#728298": palette.muted,
        "#93a2b2": palette.muted,
        "#9fb0c1": palette.muted,
        "#a9beca": palette.muted,
        "#bac6d2": palette.text,
        "#607f90": palette.muted,
        "#7e9baa": palette.muted,
        "#37c5a0": palette.primary,
        "#17352f": palette.primary_soft,
        "#174238": palette.primary_soft,
        "#17453b": palette.primary_soft,
        "#20564b": palette.selected_background,
        "#24564d": palette.selected_background,
        "#205849": palette.primary_strong,
        "#e4a653": palette.warning,
        "#e8b35e": palette.warning,
        "#f0bd70": palette.warning,
        "#ef9a9a": palette.error,
        "#f3a0a0": palette.error,
        "#342816": "#FFF2D4" if palette.mode == ThemeMode.DAY else "#3A2A12",
        "#392d18": "#FFF2D4" if palette.mode == ThemeMode.DAY else "#3A2A12",
        "#3a2023": "#FFE2E6" if palette.mode == ThemeMode.DAY else "#421C27",
        "#512a2f": "#FFD4DA" if palette.mode == ThemeMode.DAY else "#5A2632",
    }
    replacements = {key: value for key, value in replacements.items()}
    return re.sub(
        r"#[0-9A-Fa-f]{6}",
        lambda match: replacements.get(match.group(0).lower(), match.group(0)),
        style,
    )


def build_stylesheet(mode: ThemeMode | str) -> str:
    return _replace_style_colors(BASE_STYLE, theme_palette(mode))


def build_qt_palette(mode: ThemeMode | str) -> QPalette:
    """Cover native popup/container surfaces that Qt stylesheets do not own."""
    colors = theme_palette(mode)
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: colors.background,
        QPalette.ColorRole.WindowText: colors.text,
        QPalette.ColorRole.Base: colors.input_background,
        QPalette.ColorRole.AlternateBase: colors.surface_alt,
        QPalette.ColorRole.ToolTipBase: colors.elevated,
        QPalette.ColorRole.ToolTipText: colors.text_strong,
        QPalette.ColorRole.Text: colors.text,
        QPalette.ColorRole.Button: colors.surface_alt,
        QPalette.ColorRole.ButtonText: colors.text,
        QPalette.ColorRole.BrightText: colors.text_strong,
        QPalette.ColorRole.Highlight: colors.selected_background,
        QPalette.ColorRole.HighlightedText: colors.text_strong,
        QPalette.ColorRole.PlaceholderText: colors.muted,
        QPalette.ColorRole.Link: colors.primary,
    }
    for role, value in roles.items():
        palette.setColor(QPalette.ColorGroup.All, role, QColor(value))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors.muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors.muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(colors.surface_alt))
    return palette


BASE_STYLE = """
QWidget {
    color: #e7edf5;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget#root, QWidget#pageContent {
    background: #0b1118;
}
QMenu, QComboBoxPrivateContainer {
    color: #dfe8f2;
    background: #111a24;
    border: 1px solid #2a3d4d;
    padding: 2px;
}
QMenu::item {
    background: transparent;
    padding: 7px 18px;
}
QMenu::item:selected {
    color: #f2f8f6;
    background: #20564b;
}
QDialog QListWidget, QDialog QTreeWidget, QDialog QTableWidget,
QListWidget#secondaryList {
    color: #dfe8f2;
    background: #111a24;
    alternate-background-color: #15222d;
    border: 1px solid #263747;
    outline: none;
    selection-color: #f2f8f6;
    selection-background-color: #20564b;
}
QDialog QListWidget::item, QDialog QTreeWidget::item,
QListWidget#secondaryList::item {
    background: transparent;
    padding: 7px 8px;
}
QDialog QListWidget::item:hover, QDialog QTreeWidget::item:hover,
QListWidget#secondaryList::item:hover {
    background: #172733;
}
QDialog QListWidget::item:selected, QDialog QTreeWidget::item:selected,
QListWidget#secondaryList::item:selected {
    color: #f2f8f6;
    background: #20564b;
}
QWidget#deviceGrid, QWidget#mapGrid {
    background: #0b1118;
}
QFrame#navigation {
    background: #0e1720;
    border-bottom: 1px solid #1f2d3a;
}
QLabel#brand {
    color: #8de2c7;
    font-family: Consolas, monospace;
    font-size: 21px;
    font-weight: 700;
}
QLabel#brandIcon {
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
}
QLabel#navVersion {
    color: #68788a;
    font-family: Consolas, monospace;
}
QPushButton#themeToggleButton {
    min-width: 84px;
    padding: 7px 10px;
    color: #8de2c7;
    background: #101923;
    border: 1px solid #2b6359;
    border-radius: 5px;
    font-weight: 600;
}
QPushButton#themeToggleButton:hover {
    color: #f4f7fb;
    background: #17352f;
    border-color: #37c5a0;
}
QPushButton#navButton {
    min-width: 58px;
    padding: 9px 12px;
    color: #8e9cad;
    background: transparent;
    border: none;
    border-radius: 5px;
    font-weight: 600;
}
QPushButton#navButton:hover {
    color: #e8eef5;
    background: #17232e;
}
QPushButton#navButton:checked {
    color: #91e7cb;
    background: #17352f;
    border-bottom: 2px solid #37c5a0;
}
QLabel#eyebrow {
    color: #73869a;
    font-size: 11px;
    font-weight: 600;
}
QLabel#heroTitle {
    color: #f4f7fb;
    font-size: 29px;
    font-weight: 700;
}
QLabel#versionLabel {
    color: #7be0bd;
    font-family: Consolas, 'Microsoft YaHei';
    font-weight: 600;
}
QLabel#pageTitle {
    color: #f4f7fb;
    font-size: 23px;
    font-weight: 700;
}
QLabel#sectionTitle {
    color: #e9eff6;
    font-size: 16px;
    font-weight: 650;
}
QLabel#panelTitle {
    color: #dce6ef;
    font-size: 13px;
    font-weight: 650;
}
QLabel#muted, QLabel#emptyState {
    color: #8290a2;
}
QLabel#emptyState {
    padding: 70px 20px;
}
QLabel#connectedLabel {
    color: #7be0bd;
    font-weight: 600;
}
QLabel#moduleErrorLabel {
    color: #ef9a9a;
    font-weight: 600;
}
QLabel#configErrorBanner {
    color: #f0bd70;
    background: #342816;
    border: 1px solid #6a5125;
    border-radius: 5px;
    padding: 9px 12px;
}
QLabel#validationError {
    color: #ef8585;
    min-height: 18px;
}
QLabel#dialogTitle {
    color: #f4f7fb;
    font-size: 20px;
    font-weight: 700;
}
QLabel#metricValue {
    color: #f5f8fc;
    font-size: 21px;
    font-weight: 700;
}
QLabel#metricLabel, QLabel#fieldLabel {
    color: #8492a5;
    font-size: 11px;
}
QLabel#summaryValue {
    color: #e8eef5;
    font-size: 14px;
    font-weight: 600;
}
QLabel#statusCardValue {
    color: #f0f5fa;
    font-size: 18px;
    font-weight: 650;
}
QLabel#placeholderText {
    color: #76879a;
    font-size: 19px;
    font-weight: 600;
}
QFrame#metric, QFrame#summaryPanel, QFrame#sidePanel {
    background: #111a24;
    border: 1px solid #1e2b39;
    border-radius: 7px;
}
QFrame#dataStatusCard {
    background: #101923;
    border: 1px solid #223140;
    border-radius: 7px;
}
QFrame#dataStatusCard:hover {
    border-color: #355267;
    background: #121d28;
}
QFrame#videoPanel {
    background: #111a24;
    border: 1px solid #1e2b39;
    border-radius: 7px;
}
QWidget#videoStack, QVideoWidget#videoOutput {
    background: #05080c;
    border: 1px solid #1c2a36;
    border-radius: 4px;
}
QLabel#videoStatus {
    color: #8290a2;
    font-size: 14px;
    font-weight: 600;
}
QLabel#videoUrl {
    color: #6f8194;
    font-family: Consolas, monospace;
    font-size: 11px;
}
QCheckBox#videoSwitch {
    color: #cdd8e3;
    spacing: 7px;
}
QFrame#deviceCard {
    background: #111a24;
    border: 1px solid #223142;
    border-radius: 7px;
}
QFrame#deviceCard[selected='true'] {
    border: 1px solid #37c5a0;
    background: #12241f;
}
QFrame#deviceCard[selected='true'] QLabel#deviceName,
QFrame#deviceCard[selected='true'] QLabel#fieldValue {
    color: #f1f5fa;
}
QFrame#deviceCard:hover {
    border: 1px solid #4a6b83;
}
QFrame#mapCard {
    background: #111a24;
    border: 1px solid #223142;
    border-radius: 7px;
}
QFrame#mapCard:hover {
    background: #13202b;
    border-color: #4a6b83;
}
QLabel#mapName {
    color: #f1f5fa;
    font-size: 16px;
    font-weight: 650;
}
QLabel#mapStatus {
    padding: 4px 7px;
    border-radius: 4px;
    color: #efc477;
    background: #392d18;
    font-size: 11px;
    font-weight: 650;
}
QLabel#mapStatus[state='ready'] {
    color: #91e7cb;
    background: #17352f;
}
QLabel#mapStatus[state='error'] {
    color: #f3a0a0;
    background: #3a2023;
}
QLabel#viewerStatus {
    color: #93a2b2;
    background: #070d13;
    border: 1px solid #223442;
    border-radius: 5px;
    padding: 24px;
}
QLabel#deviceName {
    color: #f1f5fa;
    font-size: 16px;
    font-weight: 650;
}
QLabel#deviceId {
    color: #728298;
    font-family: Consolas, monospace;
    font-size: 11px;
}
QLabel#fieldValue {
    color: #dbe5ef;
    font-size: 12px;
    font-weight: 600;
}
QLabel#statusPill {
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 650;
}
QLineEdit, QComboBox {
    background: #111a24;
    border: 1px solid #263747;
    border-radius: 5px;
    padding: 8px 10px;
    color: #dfe8f2;
    min-height: 18px;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #37c5a0;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px;
    height: 0px;
    border: none;
}
QComboBox QAbstractItemView {
    background: #111a24;
    color: #dfe8f2;
    border: 1px solid #2a3d4d;
    outline: none;
    selection-color: #f2f8f6;
    selection-background-color: #24564d;
}
QComboBox QAbstractItemView::item {
    background: #111a24;
    min-height: 24px;
    padding: 3px 8px;
}
QComboBox QAbstractItemView::item:hover,
QComboBox QAbstractItemView::item:selected {
    color: #f2f8f6;
    background: #20564b;
}
QPushButton {
    background: #1b2a38;
    border: 1px solid #2b4051;
    border-radius: 5px;
    padding: 8px 14px;
    color: #e5edf5;
    font-weight: 600;
}
QPushButton:hover {
    background: #243b4c;
    border-color: #4b687d;
}
QPushButton:disabled {
    color: #5e6b79;
    background: #141d26;
    border-color: #25323d;
}
QPushButton#refreshButton {
    color: #8de2c7;
    border-color: #2b6359;
}
QPushButton#primaryButton {
    color: #a0ead2;
    background: #174238;
    border-color: #2c7967;
}
QPushButton#primaryButton:hover {
    background: #205849;
}
QPushButton#dangerButton {
    color: #f3a0a0;
    background: #3a2023;
    border-color: #724046;
}
QPushButton#dangerButton:hover {
    background: #512a2f;
}
QPushButton#primaryButton:disabled, QPushButton#dangerButton:disabled {
    color: #5e6b79;
    background: #141d26;
    border-color: #25323d;
}
QPushButton#backButton {
    color: #9fb0c1;
    background: transparent;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #506779;
    border-radius: 3px;
    background: #0e1720;
}
QCheckBox::indicator:checked {
    background: #37c5a0;
    border-color: #7be0bd;
}
QProgressBar {
    background: #1b2835;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
}
QProgressBar::chunk {
    background: #37c5a0;
    border-radius: 3px;
}
QProgressBar#lowBattery::chunk {
    background: #e4a653;
    border-radius: 3px;
}
QLabel#pingResult[state='success'] {
    color: #7be0bd;
    font-weight: 600;
}
QLabel#pingResult[state='warning'] {
    color: #e4a653;
    font-weight: 600;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea#mapDeviceScroll {
    background: #0e1720;
    border: 1px solid #263747;
    border-radius: 5px;
}
QScrollArea#taskListScroll, QWidget#taskListViewport, QWidget#taskListContainer {
    background: #0b131b;
}
QScrollArea#taskListScroll {
    border: 1px solid #1e3040;
}
QWidget#taskEditorPage {
    background: #0b1118;
}
QFrame#taskEditorPanel {
    background: #101923;
    border: 1px solid #223442;
    border-radius: 6px;
}
QFrame#taskEditorPanel QLabel {
    background: transparent;
    border: none;
}
QWidget#taskEditorPage QDoubleSpinBox {
    color: #dfe8f2;
    background: #111a24;
    border: 1px solid #2a3d4d;
    border-radius: 4px;
    padding: 5px 22px 5px 8px;
    min-height: 18px;
}
QWidget#taskEditorPage QDoubleSpinBox:focus {
    border-color: #37c5a0;
}
QWidget#taskEditorPage QDoubleSpinBox:disabled {
    color: #647383;
    background: #111820;
}
QWidget#taskEditorPage QDoubleSpinBox::up-button,
QWidget#taskEditorPage QDoubleSpinBox::down-button {
    width: 0px;
    height: 0px;
    border: none;
}
QListWidget#taskDeviceList, QListWidget#taskConflictList,
QListWidget#taskAuditList, QTableWidget#taskWaypointTable {
    color: #dce6ef;
    background: #0d161f;
    alternate-background-color: #101c26;
    border: 1px solid #263847;
    border-radius: 4px;
    outline: none;
    selection-color: #f2f8f6;
    selection-background-color: #20564b;
}
QListWidget#taskDeviceList::item, QListWidget#taskConflictList::item,
QListWidget#taskAuditList::item {
    padding: 7px 8px;
    border-bottom: 1px solid #182733;
}
QListWidget#taskDeviceList::item:hover, QListWidget#taskConflictList::item:hover,
QListWidget#taskAuditList::item:hover {
    background: #172733;
}
QListWidget#taskDeviceList::item:selected, QListWidget#taskConflictList::item:selected,
QListWidget#taskAuditList::item:selected {
    color: #9aead1;
    background: #17453b;
    border-left: 2px solid #37c5a0;
}
QTableWidget#taskWaypointTable::item {
    padding: 5px 7px;
    border-right: 1px solid #1d2d39;
    border-bottom: 1px solid #1d2d39;
}
QTableWidget#taskWaypointTable::item:selected {
    color: #f2f8f6;
    background: #20564b;
}
QTableWidget#taskWaypointTable QHeaderView::section {
    color: #91a3b5;
    background: #15222d;
    border: none;
    border-right: 1px solid #2a3d4d;
    border-bottom: 1px solid #2a3d4d;
    padding: 6px;
    font-weight: 600;
}
QWidget#taskEditorPage QAbstractScrollArea::corner {
    background: #0d161f;
}
QWidget#taskEditorPage QScrollBar:vertical {
    background: #0d161f;
    width: 10px;
    margin: 0;
}
QWidget#taskEditorPage QScrollBar:horizontal {
    background: #0d161f;
    height: 10px;
    margin: 0;
}
QWidget#taskEditorPage QScrollBar::handle:vertical,
QWidget#taskEditorPage QScrollBar::handle:horizontal {
    background: #385266;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QWidget#taskEditorPage QScrollBar::handle:vertical:hover,
QWidget#taskEditorPage QScrollBar::handle:horizontal:hover {
    background: #4b7187;
}
QWidget#taskEditorPage QScrollBar::add-line,
QWidget#taskEditorPage QScrollBar::sub-line,
QWidget#taskEditorPage QScrollBar::add-page,
QWidget#taskEditorPage QScrollBar::sub-page {
    background: transparent;
    border: none;
    width: 0;
    height: 0;
}
QSplitter#taskEditorMainSplitter::handle,
QSplitter#taskEditorLowerSplitter::handle {
    background: #0b1118;
    width: 7px;
    height: 7px;
}
QSplitter#taskEditorMainSplitter::handle:hover,
QSplitter#taskEditorLowerSplitter::handle:hover {
    background: #2a4b59;
}
QWidget#mapDeviceViewport, QWidget#mapDeviceSelector {
    background: #0e1720;
}
QScrollArea#mapDeviceScroll QCheckBox {
    color: #dfe8f2;
    background: #0e1720;
    padding: 6px 4px;
}
QScrollArea#mapDeviceScroll QCheckBox:hover {
    background: #162633;
}
QScrollArea#mapDeviceScroll QRadioButton {
    color: #dfe8f2;
    background: transparent;
    padding: 6px 4px;
}
QScrollArea#mapDeviceScroll QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #708496;
    border-radius: 7px;
    background: #0b1118;
}
QScrollArea#mapDeviceScroll QRadioButton::indicator:checked {
    border-color: #8de2c7;
    background: #37c5a0;
}
QFrame#deviceSelectCard {
    background: #111d27;
    border: 1px solid #263747;
    border-radius: 5px;
}
QFrame#deviceSelectCard:hover {
    border-color: #37c5a0;
}
QFrame#mappingStatus {
    background: #0e1720;
    border: 1px solid #263747;
    border-radius: 5px;
}
QListWidget#sideList {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget#logList {
    background: #0e1720;
    border: 1px solid #223442;
    border-radius: 5px;
    outline: none;
}
QListWidget#logList::item {
    border-bottom: 1px solid #1c2a36;
    padding: 10px 12px;
}
QListWidget#sideList::item {
    color: #bac6d2;
    border-bottom: 1px solid #1c2a36;
    padding: 10px 6px;
}
QListWidget#sideList::item:selected {
    color: #8de2c7;
    background: #17352f;
    border-left: 2px solid #37c5a0;
}
QGraphicsView#mapCanvas {
    border: 1px solid #223442;
    border-radius: 5px;
}
QSplitter#mapSplitter::handle {
    background: #0b1118;
    width: 6px;
}
QScrollBar:vertical {
    background: #0d151d;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2c4051;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
}
QWidget#commandDashboard {
    background: #050b10;
}
QLabel#dashboardSystemStatus, QLabel#dashboardOnlineCount, QLabel#dashboardClock {
    color: #68d9d1;
    font-family: Consolas, 'Microsoft YaHei';
    font-size: 11px;
    font-weight: 600;
}
QLabel#dashboardClock {
    color: #dcebf2;
}
QFrame#dashboardSidePanel, QFrame#digitalTwinPanel, QFrame#dashboardConsole {
    background: #09131b;
    border: 1px solid #1b5360;
    border-radius: 3px;
}
QFrame#digitalTwinPanel {
    border-color: #268392;
}
QFrame#dashboardConsole {
    border-top: 2px solid #2ccabd;
}
QLabel#dashboardPanelTitle {
    color: #bdfbf4;
    font-family: Consolas, 'Microsoft YaHei';
    font-size: 12px;
    font-weight: 700;
}
QLabel#dashboardCount {
    color: #f0b84a;
    font-family: Consolas, monospace;
    font-size: 12px;
    font-weight: 700;
}
QPushButton#dashboardIconButton {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    color: #6fe6db;
    background: #0e2831;
    border: 1px solid #2b7f88;
}
QPushButton#dashboardIconButton:hover {
    background: #153b45;
    border-color: #56d9cf;
}
QListWidget#dashboardUavList, QListWidget#dashboardDeviceList {
    background: #071018;
    border: 1px solid #173642;
    outline: none;
}
QListWidget#dashboardUavList::item, QListWidget#dashboardDeviceList::item {
    color: #a9beca;
    border-bottom: 1px solid #15313c;
    padding: 9px 7px;
}
QListWidget#dashboardUavList::item:hover, QListWidget#dashboardDeviceList::item:hover {
    color: #e8ffff;
    background: #0d222b;
}
QListWidget#dashboardUavList::item:selected, QListWidget#dashboardDeviceList::item:selected {
    color: #dffffb;
    background: #10343b;
    border-left: 3px solid #35e0cf;
}
QLabel#dashboardMapState {
    color: #edb64d;
    font-family: Consolas, 'Microsoft YaHei';
    font-size: 11px;
}
QScrollArea#dashboardStatusScroll, QWidget#dashboardStatusContent,
QScrollArea#dashboardStatusScroll QWidget#qt_scrollarea_viewport {
    background: #081119;
    border: none;
}
QLabel#dashboardDeviceIdentity {
    color: #efffff;
    background: #0d2530;
    border-left: 3px solid #ee4f9a;
    padding: 8px;
    font-size: 14px;
    font-weight: 700;
}
QLabel#dashboardFieldLabel {
    color: #607f90;
    font-size: 10px;
}
QLabel#dashboardFieldValue {
    color: #d3e8ef;
    font-family: Consolas, 'Microsoft YaHei';
    font-size: 11px;
    font-weight: 600;
}
QFrame#dashboardChartPanel {
    background: #071018;
    border: 1px solid #173642;
}
QChartView#dashboardChart {
    background: #071018;
    border: none;
}
QLabel#dashboardChartTitle {
    color: #c9fbf5;
    font-size: 10px;
    font-weight: 700;
}
QLabel#dashboardChartUnit {
    color: #607f90;
    font-size: 8px;
}
QLabel#dashboardChartLegend {
    font-family: Consolas, monospace;
    font-size: 8px;
    font-weight: 600;
}
QComboBox#dashboardCombo {
    min-width: 150px;
    color: #c9fbf5;
    background: #0a1d25;
    border-color: #286b72;
}
QPushButton#dashboardPrimaryButton {
    color: #c4fff7;
    background: #0c4146;
    border: 1px solid #2bc4b8;
}
QPushButton#dashboardPrimaryButton:hover {
    background: #125a5d;
}
QWidget#commandDashboard QPushButton:disabled {
    color: #5e6b79;
    background: #111820;
    border-color: #25323d;
}
QLabel#dashboardConsoleStatus {
    color: #7e9baa;
    font-family: Consolas, 'Microsoft YaHei';
    font-size: 11px;
}
QSplitter#dashboardUpperSplitter::handle, QSplitter#dashboardVerticalSplitter::handle {
    background: #071118;
    width: 5px;
    height: 5px;
}
"""


APP_STYLE = build_stylesheet(ThemeMode.NIGHT)
