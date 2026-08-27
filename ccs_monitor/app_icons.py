from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QTransform
from PySide6.QtWidgets import QAbstractButton

from .styles import ThemeMode, ThemePalette


LOGGER = logging.getLogger(__name__)
APP_ICON_SIZE = QSize(18, 18)
APP_ICON_ROOT = Path(__file__).resolve().parents[1] / "icons" / "app_icons"


def _mode_value(theme: ThemeMode | ThemePalette | str) -> ThemeMode:
    return theme.mode if isinstance(theme, ThemePalette) else ThemeMode(theme)


def icon_path(name: str, theme: ThemeMode | ThemePalette | str) -> Path:
    mode = _mode_value(theme)
    return APP_ICON_ROOT / f"{name}_{mode.value}.svg"


def app_icon(
    name: str,
    theme: ThemeMode | ThemePalette | str,
    *,
    rotation: int = 0,
) -> QIcon:
    path = icon_path(name, theme)
    icon = QIcon(str(path))
    if not path.is_file() or icon.isNull():
        LOGGER.warning("Application icon is missing or invalid: %s", path)
        return QIcon()
    normalized_rotation = int(rotation) % 360
    if normalized_rotation == 0:
        return icon
    pixmap = icon.pixmap(64, 64)
    if pixmap.isNull():
        LOGGER.warning("Application icon cannot be rendered: %s", path)
        return QIcon()
    transformed = pixmap.transformed(
        QTransform().rotate(normalized_rotation),
        Qt.TransformationMode.SmoothTransformation,
    )
    return QIcon(transformed)


def apply_button_icon(
    button: QAbstractButton,
    name: str,
    theme: ThemeMode | ThemePalette | str,
    *,
    rotation: int = 0,
    text: str | None = None,
    size: QSize = APP_ICON_SIZE,
) -> None:
    mode = _mode_value(theme)
    button.setIcon(app_icon(name, mode, rotation=rotation))
    button.setIconSize(size)
    if text is not None:
        button.setText(text)
    button.setProperty("appIconName", name)
    button.setProperty("appIconMode", mode.value)
    button.setProperty("appIconRotation", int(rotation) % 360)
