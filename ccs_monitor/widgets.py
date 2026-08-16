from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

from .models import ConnectionStatus, DeviceSnapshot, LocalizationStatus, TaskStatus
from .styles import ThemeMode, ThemePalette, theme_palette


STATUS_TEXT = {
    ConnectionStatus.ONLINE: "在线",
    ConnectionStatus.OFFLINE: "离线",
    ConnectionStatus.WARNING: "需关注",
    LocalizationStatus.FIXED: "已定位",
    LocalizationStatus.SEARCHING: "定位中",
    LocalizationStatus.LOST: "定位丢失",
    LocalizationStatus.UNKNOWN: "未知",
    TaskStatus.EXECUTING: "执行中",
    TaskStatus.STANDBY: "待机",
    TaskStatus.PAUSED: "已暂停",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.UNKNOWN: "未知",
}


class TypeBadge(QWidget):
    def __init__(self, device_type: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device_type = device_type[:4]
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.setFixedSize(58, 58)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.theme_palette.primary), 1))
        painter.setBrush(QBrush(QColor(self.theme_palette.primary_soft)))
        painter.drawRoundedRect(1, 1, 56, 56, 7, 7)
        painter.setPen(QPen(QColor(self.theme_palette.focus)))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.device_type)


class DeviceCard(QFrame):
    clicked = Signal(str)
    double_clicked = Signal(str)
    selection_changed = Signal(str, bool)

    def __init__(self, device: DeviceSnapshot, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device = device
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.type_badge: TypeBadge | None = None
        self.status_label: QLabel | None = None
        self.battery_bar: QProgressBar | None = None
        self.edit_mode = False
        self.setObjectName("deviceCard")
        self.setProperty("selected", False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(242)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        head = QGridLayout()
        head.setHorizontalSpacing(12)
        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("选择设备")
        self.checkbox.setVisible(False)
        self.checkbox.toggled.connect(
            lambda checked: self.selection_changed.emit(self.device.device_id, checked)
        )
        head.addWidget(self.checkbox, 0, 3, 2, 1, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.type_badge = TypeBadge(self.device.device_type)
        head.addWidget(self.type_badge, 0, 0, 2, 1)
        name = QLabel(self.device.device_name)
        name.setObjectName("deviceName")
        head.addWidget(name, 0, 1)
        ident = QLabel(self.device.device_id)
        ident.setObjectName("deviceId")
        head.addWidget(ident, 1, 1)
        status = QLabel(STATUS_TEXT[self.device.connection_status])
        status.setObjectName("statusPill")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setStyleSheet(self._status_style(self.device.connection_status))
        self.status_label = status
        head.addWidget(status, 0, 2, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        root.addWidget(self._battery_row())
        details = QGridLayout()
        details.setHorizontalSpacing(20)
        details.setVerticalSpacing(4)
        self._add_detail(details, 0, 0, "定位状态", STATUS_TEXT[self.device.localization_status])
        self._add_detail(details, 0, 1, "任务状态", STATUS_TEXT[self.device.task_status])
        self._add_detail(details, 1, 0, "飞行模式", self.device.flight_mode)
        self._add_detail(details, 1, 1, "数据更新时间", self.device.updated_at.astimezone().strftime("%H:%M:%S"))
        root.addLayout(details)

    def _battery_row(self) -> QWidget:
        wrapper = QWidget()
        layout = QGridLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("电量")
        label.setObjectName("fieldLabel")
        value = "--" if self.device.battery_percent is None else f"{self.device.battery_percent:g}%"
        amount = QLabel(value)
        amount.setObjectName("fieldValue")
        bar = QProgressBar()
        bar.setObjectName("lowBattery" if self.device.battery_percent is not None and self.device.battery_percent < 25 else "batteryBar")
        self.battery_bar = bar
        bar.setRange(0, 100)
        bar.setValue(round(self.device.battery_percent or 0))
        bar.setTextVisible(False)
        layout.addWidget(label, 0, 0)
        layout.addWidget(bar, 1, 0)
        layout.addWidget(amount, 0, 1, 2, 1, Qt.AlignmentFlag.AlignRight)
        return wrapper

    @staticmethod
    def _add_detail(layout: QGridLayout, row: int, column: int, label_text: str, value_text: str) -> None:
        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        value = QLabel(value_text)
        value.setObjectName("fieldValue")
        layout.addWidget(label, row * 2, column)
        layout.addWidget(value, row * 2 + 1, column)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        if self.type_badge is not None:
            self.type_badge.set_theme(palette)
        if self.status_label is not None:
            self.status_label.setStyleSheet(self._status_style(self.device.connection_status))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _status_style(self, status: ConnectionStatus) -> str:
        colors = {
            ConnectionStatus.ONLINE: (self.theme_palette.primary_soft, self.theme_palette.good),
            ConnectionStatus.WARNING: ("#FFF2D4" if self.theme_palette.mode == ThemeMode.DAY else "#44351E", self.theme_palette.warning),
            ConnectionStatus.OFFLINE: (self.theme_palette.surface_alt, self.theme_palette.muted),
        }
        background, foreground = colors[status]
        return f"background: {background}; color: {foreground};"

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_edit_mode(self, enabled: bool, checked: bool = False) -> None:
        self.edit_mode = enabled
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(checked)
        self.checkbox.blockSignals(False)
        self.checkbox.setVisible(enabled)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.edit_mode:
            self.clicked.emit(self.device.device_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if not self.edit_mode:
            self.double_clicked.emit(self.device.device_id)
        super().mouseDoubleClickEvent(event)
