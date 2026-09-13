"""Dashboard-only painting; device values and selection stay with their owners."""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QStyle, QStyledItemDelegate

from .app_icons import app_icon


DEVICE_SNAPSHOT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def battery_color(value, palette):
    return palette.dashboard_muted if value is None else palette.error if value < 25 else palette.good


def paint_battery(painter, rect, value, palette):
    """An unknown reading has no fill; zero remains a known, low battery."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(battery_color(value, palette))
    body = rect.adjusted(1, 1, -4, -1)
    painter.setPen(QPen(color, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body, 2, 2)
    painter.fillRect(QRectF(body.right() + 1, body.center().y() - 2, 2, 4), color)
    if value is not None and value > 0:
        fill = body.adjusted(3, 3, -3, -3)
        fill.setWidth(fill.width() * max(0, min(100, value)) / 100)
        painter.fillRect(fill, color)
    painter.restore()


class BatteryLabel(QLabel):
    """Preserve the QLabel text API used by the telemetry status panel."""

    def __init__(self, palette):
        super().__init__("--")
        self.theme_palette = palette
        self.value = None
        self.setMinimumHeight(30)
        self.setContentsMargins(50, 0, 0, 0)

    def set_value(self, value):
        self.value = value
        self.setText("--" if value is None else f"{value:.1f}%")
        self.setAccessibleName(f"电量 {self.text()}")
        self.setToolTip("电量未知" if value is None else f"电量 {self.text()}")
        self.update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        paint_battery(painter, QRectF(0, (self.height() - 20) / 2, 42, 20), self.value, self.theme_palette)


class StatusLabel(QLabel):
    def __init__(self, palette):
        super().__init__("--")
        self.theme_palette = palette
        self.setText("--")

    def setText(self, text):  # noqa: N802
        super().setText(text)
        palette = self.theme_palette
        color = (palette.good if text in {"在线", "正常"} else
                 palette.warning if text in {"警告", "需关注"} else
                 palette.error if text in {"离线", "断开", "异常", "模块故障"} else
                 palette.dashboard_muted)
        self.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.setToolTip(text)


class DeviceCardDelegate(QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__(panel.list)
        self.panel = panel
        self.icons = {}
        self._fallback_icons = {}

    def cache_icons(self, devices):
        # Uploaded assets use unique paths; stat also catches replacement in place.
        paths = {str(device.device_icon_path or "") for device in devices}
        self.icons = {key: value for key, value in self.icons.items() if key in paths}
        for path in paths:
            try:
                stat = Path(path).stat() if path else None
                stamp = (stat.st_mtime_ns, stat.st_size) if stat else None
            except OSError:
                stamp = None
            cached = self.icons.get(path)
            if cached is not None and cached[0] == stamp:
                continue
            icon = QIcon(path) if stamp is not None else QIcon()
            if icon.pixmap(44, 44).isNull():
                icon = QIcon()
            self.icons[path] = (stamp, icon)

    def device_icon(self, device):
        cached = self.icons.get(str(device.device_icon_path or ""))
        if cached is not None and not cached[1].isNull():
            return cached[1]
        mode = self.panel.theme_palette.mode
        if mode not in self._fallback_icons:
            self._fallback_icons[mode] = app_icon("device", mode)
        return self._fallback_icons[mode]

    def sizeHint(self, option, index):  # noqa: N802
        line = max(18, option.fontMetrics.height())
        rows = 6 if self.panel.expanded else 5
        return QSize(120, line * rows + 32)

    def paint(self, painter, option, index):
        device = index.data(DEVICE_SNAPSHOT_ROLE)
        if device is None:
            return super().paint(painter, option, index)
        palette = self.panel.theme_palette
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(1, 3, -1, -3)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(palette.primary if selected else palette.dashboard_border), 1))
        painter.setBrush(QColor(palette.selected_background if selected else palette.hover_background if hover else palette.dashboard_panel))
        painter.drawRoundedRect(rect, 6, 6)
        if selected:
            painter.fillRect(QRectF(rect.left(), rect.top() + 8, 3, rect.height() - 16), QColor(palette.primary))
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(palette.focus), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 4, 4)
        x, y = rect.left() + 10, rect.top() + 10
        self.device_icon(device).paint(painter, int(x), int(y), 44, 44)
        font = QFont(option.font)
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(palette.dashboard_text))
        width = max(1, rect.right() - (x + 54) - 8)
        text_layout = QTextLayout(device.device_name, font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text_layout.setTextOption(text_option)
        text_layout.beginLayout()
        for row in range(2):
            line = text_layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            if row == 1:
                remaining = device.device_name[line.textStart():]
                painter.drawText(QRectF(x + 54, y + row * 18, width, 18), Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(remaining, Qt.TextElideMode.ElideRight, int(width)))
            else:
                line.draw(painter, QPointF(x + 54, y))
        text_layout.endLayout()
        font.setBold(False)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(palette.dashboard_muted))
        painter.drawText(QRectF(x + 54, y + 38, width, 18), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(device.device_id, Qt.TextElideMode.ElideRight, int(width)))
        bottom = rect.bottom() - 11
        if self.panel.expanded:
            painter.drawText(QRectF(x, bottom - 16, rect.width() - 20, 18), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(f"任务  {device.task_status.value}", Qt.TextElideMode.ElideRight, int(rect.width() - 20)))
            bottom -= 20
        painter.drawText(QRectF(x, bottom - 16, rect.width() - 20, 18), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(f"运行模式  {device.flight_mode}", Qt.TextElideMode.ElideRight, int(rect.width() - 20)))
        paint_battery(painter, QRectF(x, bottom - 39, 30, 15), device.battery_percent, palette)
        painter.setPen(QColor(battery_color(device.battery_percent, palette)))
        battery = "--" if device.battery_percent is None else f"{device.battery_percent:.0f}%"
        painter.drawText(QRectF(x + 38, bottom - 40, rect.width() - 60, 18), Qt.AlignmentFlag.AlignVCenter, battery)
        painter.restore()
