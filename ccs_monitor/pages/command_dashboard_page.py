from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, QRectF, Signal, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..data_source import DeviceDataSource
from ..app_icons import apply_button_icon
from ..map_repository import MapRepository, MapRepositoryError
from ..models import (
    ConnectionStatus,
    DeviceMapMarker,
    DeviceSnapshot,
    DeviceTelemetrySnapshot,
    HealthStatus,
    MapDefinition,
    MapStatus,
    PoseTelemetry,
    UdpLinkStatus,
)
from ..pgm_map import PgmMapError
from ..point_cloud import PointCloudError
from ..task_conflicts import TaskConflictDetector
from ..task_models import TaskDefinitionStatus
from ..styles import ThemeMode, ThemePalette, theme_palette
from .map_page import PointCloudViewer, bound_map_pose


@dataclass(frozen=True)
class TrendSample:
    timestamp: float
    position: tuple[float, float, float] | None
    attitude: tuple[float, float, float] | None


class TelemetryTrendBuffer:
    def __init__(
        self,
        window_seconds: float = 60.0,
        max_points: int = 1200,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_seconds = float(window_seconds)
        self.max_points = int(max_points)
        self.clock = clock
        self._samples: dict[str, deque[TrendSample]] = defaultdict(
            lambda: deque(maxlen=self.max_points)
        )

    def append(
        self,
        device_id: str,
        telemetry: DeviceTelemetrySnapshot,
        timestamp: float | None = None,
    ) -> bool:
        pose = telemetry.global_pose
        position = None if pose is None else (pose.x, pose.y, pose.z)
        if pose is not None:
            attitude = (pose.roll, pose.pitch, pose.yaw)
        elif telemetry.imu is not None:
            attitude = (telemetry.imu.roll, telemetry.imu.pitch, telemetry.imu.yaw)
        else:
            attitude = None
        if position is None and attitude is None:
            return False
        now = self.clock() if timestamp is None else float(timestamp)
        samples = self._samples[device_id]
        samples.append(TrendSample(now, position, attitude))
        self._prune(samples, now)
        return True

    def series(
        self,
        device_id: str,
        kind: str,
        now: float | None = None,
    ) -> dict[str, list[tuple[float, float]]]:
        if kind not in {"position", "attitude"}:
            raise ValueError(f"未知趋势类型：{kind}")
        current = self.clock() if now is None else float(now)
        samples = self._samples.get(device_id, deque())
        self._prune(samples, current)
        names = ("X", "Y", "Z") if kind == "position" else ("Roll", "Pitch", "Yaw")
        result = {name: [] for name in names}
        for sample in samples:
            values = sample.position if kind == "position" else sample.attitude
            if values is None:
                continue
            x_value = sample.timestamp - current
            for name, value in zip(names, values):
                result[name].append((x_value, value))
        return result

    def trail(self, device_id: str, limit: int = 240) -> list[tuple[float, float, float]]:
        positions = [
            sample.position for sample in self._samples.get(device_id, ()) if sample.position is not None
        ]
        return positions[-limit:]

    def sample_count(self, device_id: str) -> int:
        return len(self._samples.get(device_id, ()))

    def _prune(self, samples: deque[TrendSample], now: float) -> None:
        threshold = now - self.window_seconds
        while samples and samples[0].timestamp < threshold:
            samples.popleft()


class TrapezoidTitle(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 3, self.width() - 8, self.height() - 8)
        inset = min(52.0, rect.width() * 0.12)
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right() - inset, rect.bottom())
        path.lineTo(rect.left() + inset, rect.bottom())
        path.closeSubpath()
        painter.fillPath(path, QColor(self.theme_palette.dashboard_panel))
        painter.setPen(QPen(QColor(self.theme_palette.primary), 1.5))
        painter.drawPath(path)
        inner = rect.adjusted(6, 5, -6, -6)
        inner_inset = max(10.0, inset - 3)
        inner_path = QPainterPath()
        inner_path.moveTo(inner.left(), inner.top())
        inner_path.lineTo(inner.right(), inner.top())
        inner_path.lineTo(inner.right() - inner_inset, inner.bottom())
        inner_path.lineTo(inner.left() + inner_inset, inner.bottom())
        inner_path.closeSubpath()
        painter.setPen(QPen(QColor(self.theme_palette.dashboard_border), 1.0))
        painter.drawPath(inner_path)
        painter.setPen(QColor(self.theme_palette.dashboard_text))
        font = painter.font()
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "指挥与控制系统信息总览")


class ScanOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.phase = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.update()

    def advance(self) -> None:
        self.phase = (self.phase + 0.009) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        color = QColor(self.theme_palette.primary)
        color.setAlpha(65)
        painter.setPen(QPen(color, 1))
        y = int(self.phase * max(1, height - 1))
        painter.drawLine(14, y, max(14, width - 14), y)
        painter.setPen(QPen(QColor(self.theme_palette.primary), 2))
        length = 18
        for x, y0, sx, sy in ((5, 5, 1, 1), (width - 5, 5, -1, 1), (5, height - 5, 1, -1), (width - 5, height - 5, -1, -1)):
            painter.drawLine(x, y0, x + sx * length, y0)
            painter.drawLine(x, y0, x, y0 + sy * length)


class DevicePanelMode(str, Enum):
    SUMMARY = "summary"
    DETAIL = "detail"
    COLLAPSED = "collapsed"


class CollapsibleDevicePanel(QFrame):
    device_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.setObjectName("dashboardSidePanel")
        self.mode = DevicePanelMode.SUMMARY
        self._previous_mode = DevicePanelMode.SUMMARY
        self._expanded_width = 180
        self.devices: list[DeviceSnapshot] = []
        self.selected_device_id: str | None = None
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(10, 10, 10, 10)
        self.root.setSpacing(8)
        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("在线设备")
        self.title.setObjectName("dashboardPanelTitle")
        self.count_label = QLabel("0")
        self.count_label.setObjectName("dashboardCount")
        self.detail_button = QPushButton()
        self.detail_button.setObjectName("dashboardIconButton")
        self.detail_button.setToolTip("展开设备详细摘要")
        self.detail_button.setAccessibleName("展开设备详细摘要")
        self.detail_button.clicked.connect(self.toggle_expanded)
        self.collapse_button = QPushButton()
        self.collapse_button.setObjectName("dashboardIconButton")
        self.collapse_button.setToolTip("完全收起在线设备栏")
        self.collapse_button.setAccessibleName("完全收起在线设备栏")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.count_label)
        header.addWidget(self.detail_button)
        header.addWidget(self.collapse_button)
        self.root.addWidget(self.header_widget)
        self.list = QListWidget()
        self.list.setObjectName("dashboardDeviceList")
        self.list.currentItemChanged.connect(self._selection_changed)
        self.root.addWidget(self.list, 1)
        self._apply_width()

    @property
    def expanded(self) -> bool:
        return self.mode == DevicePanelMode.DETAIL

    def toggle_expanded(self) -> None:
        if self.mode == DevicePanelMode.COLLAPSED:
            self.set_mode(self._previous_mode)
        else:
            self.set_mode(
                DevicePanelMode.SUMMARY
                if self.mode == DevicePanelMode.DETAIL
                else DevicePanelMode.DETAIL
            )

    def toggle_collapsed(self) -> None:
        if self.mode == DevicePanelMode.COLLAPSED:
            self.set_mode(self._previous_mode)
        else:
            self._previous_mode = self.mode
            self.set_mode(DevicePanelMode.COLLAPSED)

    def set_mode(self, mode: DevicePanelMode | str) -> None:
        target = DevicePanelMode(mode)
        splitter = self.parentWidget()
        if target == DevicePanelMode.COLLAPSED and self.mode != DevicePanelMode.COLLAPSED:
            if isinstance(splitter, QSplitter):
                sizes = splitter.sizes()
                index = splitter.indexOf(self)
                if 0 <= index < len(sizes):
                    self._expanded_width = max(165, sizes[index])
        if target != DevicePanelMode.COLLAPSED:
            self._previous_mode = target
        self.mode = target
        self._apply_width()
        self._render()
        QTimer.singleShot(0, self._sync_splitter_width)

    def force_compact(self) -> None:
        if self.mode == DevicePanelMode.DETAIL:
            self.set_mode(DevicePanelMode.SUMMARY)

    def set_devices(self, devices: list[DeviceSnapshot]) -> None:
        self.devices = list(devices)
        valid_ids = {device.device_id for device in self.devices}
        if self.selected_device_id not in valid_ids:
            self.selected_device_id = self.devices[0].device_id if self.devices else None
        self.count_label.setText(f"{len(self.devices):02d}")
        self._render()

    def select_device(self, device_id: str | None) -> None:
        self.selected_device_id = device_id
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == device_id:
                self.list.setCurrentItem(item)
                return
        self.list.clearSelection()
        self.list.setCurrentRow(-1)

    def _render(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for device in self.devices:
            battery = "--" if device.battery_percent is None else f"{device.battery_percent:.0f}%"
            if self.mode == DevicePanelMode.DETAIL:
                text = (
                    f"●  {device.device_name}\n"
                    f"{device.device_id}  |  电量 {battery}\n"
                    f"{device.flight_mode}  |  {device.task_status.value}"
                )
            else:
                text = f"●  {device.device_name}\n{battery}  ·  {device.flight_mode}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, device.device_id)
            item.setToolTip(f"{device.device_name} / {device.device_id}")
            self.list.addItem(item)
            if device.device_id == self.selected_device_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.selected_device_id:
            self.device_selected.emit(self.selected_device_id)

    def _selection_changed(self, current: QListWidgetItem | None, previous) -> None:
        if current is None:
            return
        device_id = current.data(Qt.ItemDataRole.UserRole)
        if device_id != self.selected_device_id:
            self.selected_device_id = device_id
            self.device_selected.emit(device_id)

    def _apply_width(self) -> None:
        collapsed = self.mode == DevicePanelMode.COLLAPSED
        self.title.setVisible(not collapsed)
        self.count_label.setVisible(not collapsed)
        self.detail_button.setVisible(not collapsed)
        self.list.setVisible(not collapsed)
        self.root.setContentsMargins(4 if collapsed else 10, 10, 4 if collapsed else 10, 10)
        if collapsed:
            self.setMinimumWidth(34)
            self.setMaximumWidth(38)
            self.collapse_button.setToolTip("展开在线设备栏")
        elif self.mode == DevicePanelMode.DETAIL:
            self.setMinimumWidth(280)
            self.setMaximumWidth(330)
            self.detail_button.setToolTip("收起设备详细摘要")
        else:
            self.setMinimumWidth(165)
            self.setMaximumWidth(205)
            self.detail_button.setToolTip("展开设备详细摘要")
        self.detail_button.setAccessibleName(self.detail_button.toolTip())
        self.collapse_button.setAccessibleName(self.collapse_button.toolTip())
        self._refresh_icons()

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self._refresh_icons()
        self.update()

    def _refresh_icons(self) -> None:
        apply_button_icon(
            self.detail_button,
            "close" if self.mode == DevicePanelMode.DETAIL else "expand",
            self.theme_palette,
            text="",
        )
        apply_button_icon(
            self.collapse_button,
            "expand" if self.mode == DevicePanelMode.COLLAPSED else "close",
            self.theme_palette,
            text="",
        )

    def _sync_splitter_width(self) -> None:
        splitter = self.parentWidget()
        if not isinstance(splitter, QSplitter):
            return
        sizes = splitter.sizes()
        index = splitter.indexOf(self)
        if index < 0 or len(sizes) < 2:
            return
        if self.mode == DevicePanelMode.COLLAPSED:
            target = 36
        elif self.mode == DevicePanelMode.DETAIL:
            target = max(280, self._expanded_width)
        else:
            target = min(205, max(165, self._expanded_width))
        delta = sizes[index] - target
        sizes[index] = target
        center_index = 1 if index != 1 else 0
        sizes[center_index] = max(1, sizes[center_index] + delta)
        splitter.setSizes(sizes)


# v0.7.0 public name retained for callers that imported it directly.
CollapsibleUavPanel = CollapsibleDevicePanel


class TelemetryChart(QFrame):
    def __init__(self, title: str, names: tuple[str, str, str], unit: str) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._series_names = names
        self.setObjectName("dashboardChartPanel")
        self.setMinimumHeight(148)
        root = QVBoxLayout(self)
        root.setContentsMargins(5, 3, 3, 2)
        root.setSpacing(1)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 3, 0)
        header.setSpacing(7)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("dashboardChartTitle")
        header.addWidget(self.title_label)
        self.unit_label = QLabel(f"{unit} · 60 s")
        self.unit_label.setObjectName("dashboardChartUnit")
        header.addWidget(self.unit_label)
        header.addStretch()
        self.legend_labels: dict[str, QLabel] = {}
        for name, color in zip(names, self.theme_palette.route_colors):
            label = QLabel(f"■ {name}")
            label.setObjectName("dashboardChartLegend")
            label.setStyleSheet(f"color: {color};")
            header.addWidget(label)
            self.legend_labels[name] = label
        root.addLayout(header)

        chart = QChart()
        self.chart = chart
        self.chart_view = QChartView(chart)
        self.chart_view.setObjectName("dashboardChart")
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        root.addWidget(self.chart_view, 1)
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(True)
        chart.setPlotAreaBackgroundBrush(QColor(self.theme_palette.chart_background))
        chart.legend().setVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.layout().setContentsMargins(0, 0, 0, 0)
        self.x_axis = QValueAxis()
        self.x_axis.setRange(-60, 0)
        self.x_axis.setLabelFormat("%.0f")
        self.y_axis = QValueAxis()
        self.y_axis.setRange(-1, 1)
        self.y_axis.setLabelFormat("%.1f")
        axis_font = QFont()
        axis_font.setPointSize(7)
        for axis in (self.x_axis, self.y_axis):
            axis.setLabelsColor(QColor(self.theme_palette.muted))
            axis.setLabelsFont(axis_font)
            axis.setGridLineColor(QColor(self.theme_palette.chart_grid))
        chart.addAxis(self.x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self.y_axis, Qt.AlignmentFlag.AlignLeft)
        self.series: dict[str, QLineSeries] = {}
        for name, color in zip(names, self.theme_palette.route_colors):
            line = QLineSeries()
            line.setName(name)
            line.setColor(QColor(color))
            chart.addSeries(line)
            line.attachAxis(self.x_axis)
            line.attachAxis(self.y_axis)
            self.series[name] = line

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        for name, label in self.legend_labels.items():
            index = self._series_names.index(name)
            label.setStyleSheet(f"color: {palette.route_colors[index]};")
        self.chart.setPlotAreaBackgroundBrush(QColor(palette.chart_background))
        self.x_axis.setLabelsColor(QColor(palette.muted))
        self.y_axis.setLabelsColor(QColor(palette.muted))
        self.x_axis.setGridLineColor(QColor(palette.chart_grid))
        self.y_axis.setGridLineColor(QColor(palette.chart_grid))
        for name, line in self.series.items():
            line.setColor(QColor(palette.route_colors[self._series_names.index(name)]))
        self.update()

    def set_values(self, values: dict[str, list[tuple[float, float]]]) -> None:
        all_y: list[float] = []
        for name, series in self.series.items():
            points = [QPointF(x, y) for x, y in values.get(name, [])]
            series.replace(points)
            all_y.extend(point.y() for point in points)
        if all_y:
            minimum, maximum = min(all_y), max(all_y)
            padding = max((maximum - minimum) * 0.12, 0.5)
            self.y_axis.setRange(minimum - padding, maximum + padding)
        else:
            self.y_axis.setRange(-1, 1)


class TelemetryStatusPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.setObjectName("dashboardSidePanel")
        self.expanded = False
        self.user_collapsed = False
        self.device: DeviceSnapshot | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 10, 8, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("dashboardIconButton")
        self.toggle_button.setToolTip("展开设备实时状态")
        self.toggle_button.setAccessibleName("展开设备实时状态")
        self.toggle_button.clicked.connect(self.toggle_expanded)
        self.title = QLabel("设备实时状态")
        self.title.setObjectName("dashboardPanelTitle")
        header.addWidget(self.toggle_button)
        header.addWidget(self.title)
        header.addStretch()
        root.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("dashboardStatusScroll")
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("dashboardStatusContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 2, 2)
        self.identity = QLabel("尚未选择设备")
        self.identity.setObjectName("dashboardDeviceIdentity")
        self.identity.setWordWrap(True)
        content_layout.addWidget(self.identity)
        fields_widget = QWidget()
        fields = QGridLayout(fields_widget)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setHorizontalSpacing(10)
        fields.setVerticalSpacing(5)
        self.fields: dict[str, QLabel] = {}
        for index, label in enumerate((
            "MQTT", "UDP", "健康", "电量", "任务", "运行模式", "解锁", "FCU",
            "位置 X/Y/Z", "姿态 R/P/Y", "最后数据",
        )):
            caption = QLabel(label)
            caption.setObjectName("dashboardFieldLabel")
            value = QLabel("--")
            value.setObjectName("dashboardFieldValue")
            value.setWordWrap(True)
            fields.addWidget(caption, index, 0)
            fields.addWidget(value, index, 1)
            self.fields[label] = value
        content_layout.addWidget(fields_widget)
        self.position_chart = TelemetryChart("位置数据", ("X", "Y", "Z"), "m")
        self.attitude_chart = TelemetryChart("姿态数据", ("Roll", "Pitch", "Yaw"), "deg")
        content_layout.addWidget(self.position_chart)
        content_layout.addWidget(self.attitude_chart)
        content_layout.addStretch()
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        self._apply_width()

    def toggle_expanded(self) -> None:
        expanded = not self.expanded
        self.user_collapsed = not expanded
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)
        self._apply_width()

    def set_device(self, device: DeviceSnapshot | None) -> None:
        self.device = device
        if device is None:
            self.identity.setText("尚未选择设备")
            for value in self.fields.values():
                value.setText("--")
            return
        self.identity.setText(f"{device.device_name}\n{device.device_id}")
        self.fields["MQTT"].setText("在线" if device.connection_status == ConnectionStatus.ONLINE else "离线")
        self.fields["健康"].setText({
            HealthStatus.NORMAL: "正常",
            HealthStatus.ATTENTION: "需关注",
            HealthStatus.ABNORMAL: "异常",
            HealthStatus.UNKNOWN: "未知",
        }[device.health_status])
        self.fields["电量"].setText("--" if device.battery_percent is None else f"{device.battery_percent:.1f}%")
        self.fields["任务"].setText(device.task_status.value)
        self.fields["运行模式"].setText(device.flight_mode)
        self.fields["解锁"].setText("--" if device.armed is None else "已解锁" if device.armed else "未解锁")
        self.fields["FCU"].setText("--" if device.system_status is None else str(device.system_status))

    def set_telemetry(self, telemetry: DeviceTelemetrySnapshot | None) -> None:
        if telemetry is None:
            self.fields["UDP"].setText("未知")
            self.fields["位置 X/Y/Z"].setText("--")
            self.fields["姿态 R/P/Y"].setText("--")
            self.fields["最后数据"].setText("--")
            return
        self.fields["UDP"].setText({
            UdpLinkStatus.ONLINE: "在线",
            UdpLinkStatus.WARNING: "警告",
            UdpLinkStatus.OFFLINE: "断开",
            UdpLinkStatus.UNKNOWN: "未知",
            UdpLinkStatus.MODULE_ERROR: "模块故障",
        }[telemetry.udp_link_status])
        pose = telemetry.global_pose
        if pose is None:
            self.fields["位置 X/Y/Z"].setText("--")
        else:
            self.fields["位置 X/Y/Z"].setText(f"{pose.x:.2f} / {pose.y:.2f} / {pose.z:.2f}")
        attitude = pose
        if attitude is not None:
            values = (attitude.roll, attitude.pitch, attitude.yaw)
        elif telemetry.imu is not None:
            values = (telemetry.imu.roll, telemetry.imu.pitch, telemetry.imu.yaw)
        else:
            values = None
        self.fields["姿态 R/P/Y"].setText(
            "--" if values is None else f"{values[0]:.1f} / {values[1]:.1f} / {values[2]:.1f}°"
        )
        self.fields["最后数据"].setText(
            "--" if telemetry.last_data_at is None else telemetry.last_data_at.astimezone().strftime("%H:%M:%S")
        )

    def set_trends(
        self,
        position: dict[str, list[tuple[float, float]]],
        attitude: dict[str, list[tuple[float, float]]],
    ) -> None:
        self.position_chart.set_values(position)
        self.attitude_chart.set_values(attitude)

    def _apply_width(self) -> None:
        self.scroll.setVisible(self.expanded)
        self.title.setVisible(self.expanded)
        self.setMinimumWidth(330 if self.expanded else 42)
        self.setMaximumWidth(410 if self.expanded else 46)
        self.toggle_button.setToolTip("收起设备实时状态" if self.expanded else "展开设备实时状态")
        self.toggle_button.setAccessibleName(self.toggle_button.toolTip())
        self._refresh_icon()

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self._refresh_icon()
        self.position_chart.set_theme(palette)
        self.attitude_chart.set_theme(palette)
        self.update()

    def _refresh_icon(self) -> None:
        apply_button_icon(
            self.toggle_button,
            "expand" if self.expanded else "close",
            self.theme_palette,
            text="",
        )


class CollapsibleConsolePanel(QFrame):
    collapsed_changed = Signal(bool)
    COLLAPSED_HEIGHT = 36

    def __init__(self) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.setObjectName("dashboardConsole")
        self.collapsed = False
        self._expanded_height = 165
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 7)
        root.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("COMMAND CONSOLE / 控制台")
        self.title.setObjectName("dashboardPanelTitle")
        self.status_label = QLabel("等待地图与设备数据")
        self.status_label.setObjectName("dashboardConsoleStatus")
        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("dashboardIconButton")
        self.toggle_button.setToolTip("完全收起控制台")
        self.toggle_button.setAccessibleName("完全收起控制台")
        self.toggle_button.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.status_label)
        header.addWidget(self.toggle_button)
        root.addLayout(header)
        self.content_widget = QWidget()
        self.content_layout = QGridLayout(self.content_widget)
        self.content_layout.setContentsMargins(2, 2, 2, 0)
        self.content_layout.setHorizontalSpacing(9)
        self.content_layout.setVerticalSpacing(6)
        root.addWidget(self.content_widget, 1)
        self._refresh_icon()

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        target = bool(collapsed)
        if target == self.collapsed:
            return
        splitter = self.parentWidget()
        if target and isinstance(splitter, QSplitter):
            sizes = splitter.sizes()
            index = splitter.indexOf(self)
            if 0 <= index < len(sizes):
                self._expanded_height = max(110, sizes[index])
        self.collapsed = target
        self._apply_state()
        self.collapsed_changed.emit(self.collapsed)
        QTimer.singleShot(0, self._sync_splitter_size)

    def _apply_state(self) -> None:
        self.content_widget.setVisible(not self.collapsed)
        self.status_label.setVisible(not self.collapsed)
        self.toggle_button.setToolTip("展开控制台" if self.collapsed else "完全收起控制台")
        self.toggle_button.setAccessibleName(self.toggle_button.toolTip())
        self._refresh_icon()
        if self.collapsed:
            self.setMinimumHeight(self.COLLAPSED_HEIGHT)
            self.setMaximumHeight(self.COLLAPSED_HEIGHT)
        else:
            self.setMinimumHeight(110)
            self.setMaximumHeight(16777215)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self._refresh_icon()
        self.update()

    def _refresh_icon(self) -> None:
        apply_button_icon(
            self.toggle_button,
            "close" if self.collapsed else "expand",
            self.theme_palette,
            rotation=90,
            text="",
        )

    def _sync_splitter_size(self) -> None:
        splitter = self.parentWidget()
        if not isinstance(splitter, QSplitter):
            return
        sizes = splitter.sizes()
        index = splitter.indexOf(self)
        if index < 0 or not sizes:
            return
        total = max(sum(sizes), self.COLLAPSED_HEIGHT + 1)
        target = self.COLLAPSED_HEIGHT if self.collapsed else min(
            self._expanded_height, max(110, total - 120)
        )
        sizes[index] = target
        remaining = max(1, total - target)
        other_indices = [item for item in range(len(sizes)) if item != index]
        for other_index in other_indices:
            sizes[other_index] = max(1, remaining // len(other_indices))
        splitter.setSizes(sizes)


class CommandDashboardPage(QWidget):
    fullscreen_requested = Signal(bool)

    def __init__(
        self,
        source: DeviceDataSource,
        repository: MapRepository,
        telemetry_store=None,
        viewer_factory: Callable[[], PointCloudViewer] | None = None,
        task_repository=None,
        execution_service=None,
    ) -> None:
        super().__init__()
        self.setObjectName("commandDashboard")
        self.source = source
        self.repository = repository
        self.telemetry_store = telemetry_store
        self.task_repository = task_repository
        self.execution_service = execution_service
        self.active_execution_id: str | None = None
        self.viewer = viewer_factory() if viewer_factory else PointCloudViewer()
        self.trends = TelemetryTrendBuffer()
        self.devices = source.snapshots()
        self.selected_device_id: str | None = None
        self.selected_map_id: str | None = None
        self.fullscreen = False
        self._active = False
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._build()
        self._connect_sources()
        self._update_devices(self.devices)
        self._update_maps(self.repository.maps())
        self._update_tasks()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self._render_realtime)
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.scan_overlay.advance)
        self._update_clock()

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.trapezoid.set_theme(palette)
        self.scan_overlay.set_theme(palette)
        set_theme = getattr(self.viewer, "set_theme", None)
        if set_theme is not None:
            set_theme(palette)
        self.device_panel.set_theme(palette)
        self.status_panel.set_theme(palette)
        self.console_panel.set_theme(palette)
        for chart in self.findChildren(TelemetryChart):
            chart.set_theme(palette)
        self.update()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 10)
        root.setSpacing(7)
        top = QHBoxLayout()
        self.system_status = QLabel("MQTT --  |  UDP --")
        self.system_status.setObjectName("dashboardSystemStatus")
        self.system_status.setMinimumWidth(205)
        top.addWidget(self.system_status)
        top.addStretch(1)
        self.trapezoid = TrapezoidTitle()
        top.addWidget(self.trapezoid, 4)
        top.addStretch(1)
        right_header = QHBoxLayout()
        self.online_count = QLabel("ONLINE DEVICE 00")
        self.online_count.setObjectName("dashboardOnlineCount")
        self.clock_label = QLabel("--:--:--")
        self.clock_label.setObjectName("dashboardClock")
        right_header.addWidget(self.online_count)
        right_header.addWidget(self.clock_label)
        top.addLayout(right_header)
        root.addLayout(top)

        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setObjectName("dashboardVerticalSplitter")
        self.vertical_splitter.setChildrenCollapsible(False)
        self.upper_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.upper_splitter.setObjectName("dashboardUpperSplitter")
        self.upper_splitter.setChildrenCollapsible(False)
        self.device_panel = CollapsibleDevicePanel()
        self.uav_panel = self.device_panel
        self.device_panel.device_selected.connect(self._select_device)
        self.upper_splitter.addWidget(self.device_panel)

        center = QFrame()
        center.setObjectName("digitalTwinPanel")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(7, 7, 7, 7)
        center_layout.setSpacing(5)
        center_header = QHBoxLayout()
        center_title = QLabel("DIGITAL TWIN / 三维态势")
        center_title.setObjectName("dashboardPanelTitle")
        self.map_state = QLabel("未选择地图")
        self.map_state.setObjectName("dashboardMapState")
        center_header.addWidget(center_title)
        center_header.addStretch()
        center_header.addWidget(self.map_state)
        center_layout.addLayout(center_header)
        stage = QWidget()
        stage_layout = QStackedLayout(stage)
        stage_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(self.viewer)
        self.scan_overlay = ScanOverlay()
        stage_layout.addWidget(self.scan_overlay)
        center_layout.addWidget(stage, 1)
        self.upper_splitter.addWidget(center)

        self.status_panel = TelemetryStatusPanel()
        self.upper_splitter.addWidget(self.status_panel)
        self.upper_splitter.setStretchFactor(0, 0)
        self.upper_splitter.setStretchFactor(1, 1)
        self.upper_splitter.setStretchFactor(2, 0)
        self.upper_splitter.setSizes([180, 800, 44])
        self.vertical_splitter.addWidget(self.upper_splitter)

        self.console_panel = CollapsibleConsolePanel()
        self.console_layout = self.console_panel.content_layout
        self.console_status = self.console_panel.status_label
        self.map_combo = QComboBox()
        self.map_combo.setObjectName("dashboardCombo")
        self.map_combo.currentIndexChanged.connect(self._map_changed)
        self.layer_combo = QComboBox()
        self.layer_combo.setObjectName("dashboardCombo")
        self.layer_combo.addItem("点云", "pointcloud")
        self.layer_combo.addItem("栅格", "grid")
        self.layer_combo.addItem("叠加", "overlay")
        self.layer_combo.setCurrentIndex(2)
        self.layer_combo.currentIndexChanged.connect(self._layer_changed)
        self.task_combo = QComboBox()
        self.task_combo.currentIndexChanged.connect(self._task_changed)
        self.reset_button = QPushButton("复位视角")
        self.reset_button.clicked.connect(self.viewer.reset_view)
        self.fit_button = QPushButton("适配全图")
        self.fit_button.clicked.connect(self.viewer.fit_all)
        self.scan_toggle = QCheckBox("扫描动画")
        self.scan_toggle.setChecked(True)
        self.scan_toggle.toggled.connect(self._scan_toggled)
        self.fullscreen_button = QPushButton("进入全屏")
        self.fullscreen_button.setObjectName("dashboardPrimaryButton")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        self.start_button = QPushButton("开始任务")
        self.start_button.clicked.connect(self._start_task)
        self.stop_button = QPushButton("终止任务")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_task)
        controls = (
            (QLabel("地图"), self.map_combo),
            (QLabel("图层"), self.layer_combo),
            (QLabel("任务"), self.task_combo),
        )
        column = 0
        for caption, control in controls:
            caption.setObjectName("dashboardFieldLabel")
            self.console_layout.addWidget(caption, 0, column)
            self.console_layout.addWidget(control, 0, column + 1)
            column += 2
        self.console_layout.addWidget(self.reset_button, 0, 6)
        self.console_layout.addWidget(self.fullscreen_button, 0, 7)
        self.console_layout.addWidget(self.scan_toggle, 1, 0, 1, 2)
        self.console_layout.addWidget(self.fit_button, 1, 2, 1, 2)
        self.console_layout.addWidget(self.start_button, 1, 6)
        self.console_layout.addWidget(self.stop_button, 1, 7)
        self.vertical_splitter.addWidget(self.console_panel)
        self.vertical_splitter.setStretchFactor(0, 3)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([620, 165])
        root.addWidget(self.vertical_splitter, 1)

    def _connect_sources(self) -> None:
        self.source.devices_updated.connect(self._update_devices)
        self.repository.maps_updated.connect(self._update_maps)
        self.repository.active_map_changed.connect(self._active_map_changed)
        if self.telemetry_store is not None:
            self.telemetry_store.telemetry_updated.connect(self._telemetry_updated)
            self.telemetry_store.module_status_changed.connect(self._module_status_changed)
        if hasattr(self.source, "module_status_changed"):
            self.source.module_status_changed.connect(self._module_status_changed)
        if self.task_repository is not None:
            self.task_repository.tasks_updated.connect(self._update_tasks)
        if self.execution_service is not None:
            self.execution_service.availability_changed.connect(self._task_service_available)
            self.execution_service.execution_updated.connect(self._task_execution_updated)

    def _update_tasks(self, _tasks: object = None) -> None:
        current_id = self.task_combo.currentData() if hasattr(self, "task_combo") else None
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        if self.task_repository is not None:
            for task in self.task_repository.tasks():
                if task.status != TaskDefinitionStatus.ERROR and task.is_ready:
                    self.task_combo.addItem(task.name, task.task_id)
        if not self.task_combo.count():
            self.task_combo.addItem("暂无可执行任务", None)
        index = self.task_combo.findData(current_id)
        self.task_combo.setCurrentIndex(index if index >= 0 else 0)
        self.task_combo.blockSignals(False)
        available = bool(self.execution_service and getattr(self.execution_service, "available", False))
        self.task_combo.setEnabled(self.task_combo.currentData() is not None)
        self.start_button.setEnabled(available and self.task_combo.currentData() is not None)
        self._task_changed()

    def _task_changed(self) -> None:
        self._update_console_status()

    def _task_service_available(self, available: bool, message: str) -> None:
        self.start_button.setEnabled(bool(available) and self.task_combo.currentData() is not None)
        self.start_button.setToolTip("" if available else message)

    def _start_task(self) -> None:
        task = self.task_repository.task_by_id(self.task_combo.currentData()) if self.task_repository else None
        if task is None or self.execution_service is None:
            return
        conflicts = TaskConflictDetector().detect(task.subtasks, task.safety)
        forced_reason = None
        if conflicts:
            reason, accepted = QInputDialog.getText(
                self, "存在未解决冲突", "请输入强制执行原因（留空将取消）："
            )
            if not accepted or not reason.strip():
                return
            forced_reason = reason.strip()
        try:
            snapshot = self.execution_service.execute_devices(
                task, tuple(item.device_id for item in task.subtasks),
                forced_conflict_reason=forced_reason,
            )
        except Exception as exc:
            QMessageBox.critical(self, "任务启动失败", str(exc))
            return
        self.active_execution_id = snapshot.execution_id
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._update_console_status()

    def _stop_task(self) -> None:
        if self.execution_service and self.active_execution_id:
            self.execution_service.stop_execution(self.active_execution_id, "指控大屏终止任务")

    def _task_execution_updated(self, snapshot) -> None:
        selected = self.task_combo.currentData()
        if snapshot.task_id != selected:
            return
        active = snapshot.status.value in {"preparing", "scheduled", "running"}
        self.active_execution_id = snapshot.execution_id if active else None
        self.stop_button.setEnabled(active)
        self.start_button.setEnabled(
            not active and bool(self.execution_service and self.execution_service.available)
        )
        self._update_console_status(snapshot.message)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if self._active:
            self.clock_timer.start(1000)
            self.render_timer.start(100)
            if self.scan_toggle.isChecked():
                self.animation_timer.start(33)
            self._render_realtime()
        else:
            self.clock_timer.stop()
            self.render_timer.stop()
            self.animation_timer.stop()

    def _scan_toggled(self, enabled: bool) -> None:
        self.scan_overlay.setVisible(enabled)
        if enabled and self._active:
            self.animation_timer.start(33)
        else:
            self.animation_timer.stop()

    def set_fullscreen_state(self, enabled: bool) -> None:
        self.fullscreen = bool(enabled)
        self.fullscreen_button.setText("退出全屏" if self.fullscreen else "进入全屏")

    def _update_devices(self, devices: object) -> None:
        self.devices = list(devices)
        online_devices = [
            device for device in self.devices
            if device.connection_status == ConnectionStatus.ONLINE
        ]
        previous = self.selected_device_id
        valid_ids = {device.device_id for device in online_devices}
        if previous not in valid_ids:
            self.selected_device_id = online_devices[0].device_id if online_devices else None
        self.device_panel.set_devices(online_devices)
        self.device_panel.select_device(self.selected_device_id)
        self.online_count.setText(f"ONLINE DEVICE {len(online_devices):02d}")
        self._select_device(self.selected_device_id or "")
        self._update_system_status()

    def _update_maps(self, maps: object) -> None:
        current_id = self.repository.active_map_id()
        available = [
            item for item in maps
            if item.status == MapStatus.READY and (item.pcd_path or item.pgm)
        ]
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        for definition in available:
            layers = "+".join(filter(None, ("PCD" if definition.pcd_path else "", "PGM" if definition.pgm else "")))
            active = "  · 当前激活" if definition.map_id == self.repository.active_map_id() else ""
            self.map_combo.addItem(f"{definition.name}  [{layers}]{active}", definition.map_id)
        index = self.map_combo.findData(current_id)
        if index < 0 and self.map_combo.count():
            index = 0
        self.map_combo.setCurrentIndex(index)
        self.map_combo.blockSignals(False)
        self.selected_map_id = self.repository.active_map_id() or self.map_combo.currentData()
        index = self.map_combo.findData(self.selected_map_id)
        if index >= 0:
            self.map_combo.setCurrentIndex(index)
        self._load_selected_map()

    def _select_device(self, device_id: str) -> None:
        self.selected_device_id = device_id or None
        device = next((item for item in self.devices if item.device_id == device_id), None)
        self.status_panel.set_device(device)
        if (
            device is not None and not self.status_panel.expanded
            and not self.status_panel.user_collapsed and self.width() >= 1000
        ):
            self.status_panel.set_expanded(True)
        telemetry = self.telemetry_store.telemetry(device_id) if self.telemetry_store and device_id else None
        self.status_panel.set_telemetry(telemetry)
        self._render_realtime()

    def _telemetry_updated(self, device_id: str, telemetry: DeviceTelemetrySnapshot) -> None:
        self.trends.append(device_id, telemetry)
        if device_id == self.selected_device_id:
            self.status_panel.set_telemetry(telemetry)

    def _map_changed(self) -> None:
        map_id = self.map_combo.currentData()
        if not map_id:
            return
        try:
            self.repository.set_active_map(map_id)
        except MapRepositoryError as exc:
            QMessageBox.warning(self, "激活地图失败", str(exc))
            return
        self.selected_map_id = map_id
        self._load_selected_map()

    def _active_map_changed(self, definition: object) -> None:
        map_id = getattr(definition, "map_id", None)
        self.selected_map_id = map_id
        index = self.map_combo.findData(map_id)
        if index >= 0 and self.map_combo.currentIndex() != index:
            self.map_combo.blockSignals(True)
            self.map_combo.setCurrentIndex(index)
            self.map_combo.blockSignals(False)
        self._load_selected_map()

    def _layer_changed(self) -> None:
        mode = self.layer_combo.currentData()
        if mode:
            self.viewer.set_layer_mode(mode)
        self._update_console_status()

    def _load_selected_map(self) -> None:
        self.viewer.clear()
        definition = self.repository.map_by_id(self.selected_map_id) if self.selected_map_id else None
        if definition is None:
            self.map_state.setText("无可用地图")
            self.viewer.show_message("请先在地图页面创建并导入 PCD 或 PGM")
            self._update_console_status()
            return
        errors: list[str] = []
        if definition.pcd_path:
            try:
                self.viewer.load_map(definition, self.repository.pcd_path(definition.map_id))
            except (MapRepositoryError, PointCloudError) as exc:
                errors.append(str(exc))
        if definition.pgm:
            try:
                yaml_path, _ = self.repository.pgm_paths(definition.map_id)
                self.viewer.load_pgm_layer(definition, yaml_path)
            except (MapRepositoryError, PgmMapError) as exc:
                errors.append(str(exc))
        mode = self.layer_combo.currentData() or "overlay"
        if mode == "pointcloud" and not definition.pcd_path:
            mode = "grid"
        elif mode == "grid" and not definition.pgm:
            mode = "pointcloud"
        self.viewer.set_layer_mode(mode)
        target_index = self.layer_combo.findData(mode)
        if target_index >= 0:
            self.layer_combo.blockSignals(True)
            self.layer_combo.setCurrentIndex(target_index)
            self.layer_combo.blockSignals(False)
        active_text = "当前激活地图" if definition.map_id == self.repository.active_map_id() else "非激活地图"
        self.map_state.setText(
            f"{definition.name}  ·  {active_text}  ·  {mode.upper()}" + ("  ·  部分图层失败" if errors else "")
        )
        if errors and not (self.viewer.pointcloud_loaded or self.viewer.pgm_loaded):
            self.viewer.show_message("地图加载失败：" + "；".join(errors))
        self._update_console_status()

    def _render_realtime(self) -> None:
        device_id = self.selected_device_id
        if not device_id:
            self.status_panel.set_trends(
                {name: [] for name in ("X", "Y", "Z")},
                {name: [] for name in ("Roll", "Pitch", "Yaw")},
            )
            self.viewer.set_selected_device_pose(None)
            self.viewer.set_device_trail([])
            return
        self.status_panel.set_trends(
            self.trends.series(device_id, "position"),
            self.trends.series(device_id, "attitude"),
        )
        telemetry = self.telemetry_store.telemetry(device_id) if self.telemetry_store else None
        pose = telemetry.global_pose if telemetry else None
        self.viewer.set_selected_device_pose(pose)
        self.viewer.set_device_trail(self.trends.trail(device_id))
        markers: list[DeviceMapMarker] = []
        if self.telemetry_store is not None:
            for device in self.device_panel.devices:
                snapshot = self.telemetry_store.telemetry(device.device_id)
                pose_item = bound_map_pose(
                    self.source, snapshot, device.device_id, self.selected_map_id or ""
                ) or snapshot.global_pose
                if pose_item is not None:
                    markers.append(DeviceMapMarker(
                        device.device_id, device.device_name,
                        pose_item.x, pose_item.y, pose_item.z, "online",
                        device.map_marker_shape, pose_item.yaw,
                    ))
        self.viewer.set_device_markers(markers)
        self._update_console_status()

    def _update_clock(self) -> None:
        self.clock_label.setText(datetime.now().astimezone().strftime("%Y-%m-%d  %H:%M:%S"))

    def _module_status_changed(self, message: str, healthy: bool) -> None:
        self._update_system_status()

    def _update_system_status(self) -> None:
        mqtt = "OK" if getattr(self.source, "module_healthy", True) else "FAULT"
        if self.telemetry_store is None:
            udp = "N/A"
        else:
            udp = "OK" if getattr(self.telemetry_store, "module_healthy", True) else "FAULT"
        self.system_status.setText(f"MQTT {mqtt}  |  UDP {udp}")

    def _update_console_status(self, task_message: str = "") -> None:
        map_name = "未选择地图"
        definition: MapDefinition | None = None
        if self.selected_map_id:
            definition = self.repository.map_by_id(self.selected_map_id)
        if definition:
            map_name = definition.name
        device = next(
            (item for item in self.devices if item.device_id == self.selected_device_id), None
        )
        device_name = device.device_name if device else "未选择设备"
        layer = self.layer_combo.currentText() or "--"
        task_name = self.task_combo.currentText() if self.task_combo.currentData() else "未选择任务"
        suffix = f"  |  {task_message}" if task_message else ""
        self.console_status.setText(
            f"地图 {map_name}  |  设备 {device_name}  |  图层 {layer}  |  任务 {task_name}{suffix}"
        )

    def _toggle_fullscreen(self) -> None:
        self.fullscreen_requested.emit(not self.fullscreen)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() < 1000:
            self.device_panel.force_compact()
            self.status_panel.set_expanded(False)
        elif self.selected_device_id and not self.status_panel.user_collapsed:
            self.status_panel.set_expanded(True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.set_active(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.set_active(False)
        super().hideEvent(event)
