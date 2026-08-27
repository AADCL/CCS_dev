from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QHeaderView,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import (
    DEVICE_STATUS_CARD_CATALOG,
    DeviceStatusCardDefinition,
    DeviceAvailability,
    DeviceLogEntry,
    DeviceLogLevel,
    DeviceSnapshot,
    DeviceTelemetrySnapshot,
    HealthStatus,
    ImuTelemetry,
    PoseTelemetry,
    SensorStatusTelemetry,
    TelemetryAvailability,
    UdpLinkStatus,
)
from ..device_map_context import map_mode_text, resolve_device_map_context
from ..device_dialogs import StatusCardEditorDialog
from ..srt_video import SrtVideoWidget, build_srt_url
from ..widgets import STATUS_TEXT
from ..styles import ThemeMode, ThemePalette, theme_palette


AVAILABILITY_TEXT = {
    DeviceAvailability.AVAILABLE: "可用",
    DeviceAvailability.UNAVAILABLE: "不可用",
    DeviceAvailability.UNKNOWN: "未知",
}

HEALTH_TEXT = {
    HealthStatus.UNKNOWN: "未知",
    HealthStatus.NORMAL: "正常",
    HealthStatus.ATTENTION: "需关注",
    HealthStatus.ABNORMAL: "异常",
}

UDP_LINK_TEXT = {
    UdpLinkStatus.UNKNOWN: "未知",
    UdpLinkStatus.ONLINE: "在线",
    UdpLinkStatus.WARNING: "心跳延迟",
    UdpLinkStatus.OFFLINE: "已断开",
    UdpLinkStatus.MODULE_ERROR: "模块故障",
}

class DataStatusCard(QFrame):
    def __init__(self, definition: DeviceStatusCardDefinition) -> None:
        super().__init__()
        self.definition = definition
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._status = None
        self._pointcloud = None
        self.setObjectName("dataStatusCard")
        self.setMinimumSize(190, 112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(7)
        header = QHBoxLayout()
        self.indicator = QLabel("●")
        self.indicator.setFixedWidth(14)
        self.title = QLabel(definition.display_name)
        self.title.setObjectName("fieldLabel")
        self.title.setWordWrap(True)
        header.addWidget(self.indicator)
        header.addWidget(self.title, 1)
        layout.addLayout(header)
        self.value = QLabel("等待数据")
        self.value.setObjectName("statusCardValue")
        self.value.setWordWrap(True)
        layout.addWidget(self.value)
        self.meta = QLabel("尚未收到状态帧")
        self.meta.setObjectName("muted")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)
        layout.addStretch()

    def update_status(self, status, pointcloud=None) -> None:
        self._status = status
        self._pointcloud = pointcloud
        availability = status.availability if status is not None else TelemetryAvailability.UNKNOWN
        color = {
            TelemetryAvailability.AVAILABLE: self.theme_palette.good,
            TelemetryAvailability.UNAVAILABLE: self.theme_palette.error,
            TelemetryAvailability.UNKNOWN: self.theme_palette.muted,
        }[availability]
        self.indicator.setStyleSheet(f"color: {color}; font-size: 17px;")
        if self.definition.value_kind == "text":
            self.value.setText(status.value if status is not None and status.value else "模式未知")
        else:
            self.value.setText({
                TelemetryAvailability.AVAILABLE: "运行正常",
                TelemetryAvailability.UNAVAILABLE: "不可用",
                TelemetryAvailability.UNKNOWN: "等待数据",
            }[availability])
        meta_parts: list[str] = []
        if status is not None and status.sample_age_seconds is not None:
            meta_parts.append(f"数据年龄 {status.sample_age_seconds:.1f}s")
        if status is not None and status.value and self.definition.value_kind != "text":
            meta_parts.append(status.value)
        if self.definition.card_id == "livox_driver" and pointcloud is not None:
            if pointcloud.estimated_hz is not None:
                meta_parts.append(f"点云 {pointcloud.estimated_hz:.1f} Hz")
            if pointcloud.availability == TelemetryAvailability.UNAVAILABLE:
                meta_parts.append("点云接收超时")
        self.meta.setText(" · ".join(meta_parts) if meta_parts else "尚未收到状态帧")

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.update_status(self._status, self._pointcloud)


class DeviceDetailPage(QWidget):
    back_requested = Signal()
    status_cards_changed = Signal(str, object)
    clear_logs_requested = Signal(str)
    log_event_requested = Signal(str, object, str)

    def __init__(self, source=None, map_repository=None, mapping_service=None,
                 relocalization_service=None) -> None:
        super().__init__()
        self.source = source
        self.map_repository = map_repository
        self.mapping_service = mapping_service
        self.relocalization_service = relocalization_service
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.device: DeviceSnapshot | None = None
        self.entries: list[DeviceLogEntry] = []
        self.detail_layout_mode = "wide"
        self.info_column_count = 0
        self.telemetry_column_count = 0
        self.status_card_column_count = 0
        self.pending_telemetry: DeviceTelemetrySnapshot | None = None
        self._telemetry_dirty = False
        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.setInterval(50)
        self.telemetry_timer.timeout.connect(self._render_telemetry)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)
        content = QWidget()
        content.setObjectName("pageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 22, 28, 28)
        layout.setSpacing(18)
        scroll.setWidget(content)

        header = QHBoxLayout()
        back = QPushButton("返回设备列表")
        back.setObjectName("backButton")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        header.addStretch()
        self.mqtt_card = QFrame()
        self.mqtt_card.setObjectName("mqttStatusCard")
        mqtt_layout = QVBoxLayout(self.mqtt_card)
        mqtt_layout.setContentsMargins(12, 8, 12, 8)
        mqtt_layout.setSpacing(2)
        self.mqtt_status = QLabel("MQTT · 未知")
        self.mqtt_status.setObjectName("mqttStatusTitle")
        self.mqtt_heartbeat = QLabel("最后心跳 --")
        self.mqtt_heartbeat.setObjectName("muted")
        mqtt_layout.addWidget(self.mqtt_status)
        mqtt_layout.addWidget(self.mqtt_heartbeat)
        header.addWidget(self.mqtt_card, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        self.title = QLabel("设备详情")
        self.title.setObjectName("pageTitle")
        layout.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("muted")
        layout.addWidget(self.subtitle)

        telemetry_title = QLabel("高频遥测")
        telemetry_title.setObjectName("sectionTitle")
        layout.addWidget(telemetry_title)
        self.telemetry_grid = QGridLayout()
        self.telemetry_grid.setContentsMargins(0, 0, 0, 0)
        self.telemetry_grid.setHorizontalSpacing(12)
        self.telemetry_grid.setVerticalSpacing(12)
        self.global_pose_panel, self.global_pose_values = self._telemetry_panel(
            "重定位位姿 · Map", ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
        )
        self.vision_pose_panel, self.vision_pose_values = self._telemetry_panel(
            "本地位姿 · odom", ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
        )
        self.imu_panel, self.imu_values = self._telemetry_panel(
            "IMU 信息", ("Roll", "Pitch", "Yaw", "角速度 X/Y/Z", "线加速度 X/Y/Z")
        )
        self.telemetry_panels = [self.global_pose_panel, self.vision_pose_panel, self.imu_panel]
        layout.addLayout(self.telemetry_grid)

        reception_header = QHBoxLayout()
        reception_title = QLabel("数据接收状态")
        reception_title.setObjectName("sectionTitle")
        reception_header.addWidget(reception_title)
        reception_header.addStretch()
        self.edit_status_cards_button = QPushButton("编辑状态卡片")
        self.edit_status_cards_button.setObjectName("secondaryButton")
        self.edit_status_cards_button.clicked.connect(self._edit_status_cards)
        reception_header.addWidget(self.edit_status_cards_button)
        layout.addLayout(reception_header)
        self.status_card_grid = QGridLayout()
        self.status_card_grid.setContentsMargins(0, 0, 0, 0)
        self.status_card_grid.setHorizontalSpacing(12)
        self.status_card_grid.setVerticalSpacing(12)
        self.status_cards: dict[str, DataStatusCard] = {}
        self.status_empty = QLabel("当前设备未配置数据状态卡片")
        self.status_empty.setObjectName("emptyState")
        self.status_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_empty.setMinimumHeight(90)
        layout.addLayout(self.status_card_grid)

        info_title = QLabel("设备完整信息")
        info_title.setObjectName("sectionTitle")
        layout.addWidget(info_title)
        self.detail_grid = QGridLayout()
        self.detail_grid.setContentsMargins(0, 0, 0, 0)
        self.detail_grid.setHorizontalSpacing(16)
        self.detail_grid.setVerticalSpacing(16)
        self.info_panel = QFrame()
        self.info_panel.setObjectName("summaryPanel")
        self.info_grid = QGridLayout(self.info_panel)
        self.info_grid.setContentsMargins(18, 18, 18, 18)
        self.info_grid.setHorizontalSpacing(28)
        self.info_grid.setVerticalSpacing(14)
        field_names = (
            "设备名称", "设备类型", "设备 ID", "设备 IP", "最近可用状态",
            "任务状态", "电量", "定位状态", "健康状态",
            "运行模式", "电池电压", "电池电流",
            "原始任务状态", "最后心跳", "UDP 链路状态", "UDP 最后心跳",
            "UDP 最后数据", "最后连接测试", "数据更新时间",
            "SRT 端口", "SRT 延迟", "SRT 地址",
        )
        self.fields: dict[str, QLabel] = {}
        self.field_widgets: list[QWidget] = []
        for field_name in field_names:
            widget, value_label = self._field(field_name)
            self.fields[field_name] = value_label
            self.field_widgets.append(widget)
        self.video_panel = SrtVideoWidget()
        self.video_panel.stream_event.connect(self._record_video_event)
        self.detail_grid.addWidget(self.info_panel, 0, 0)
        self.detail_grid.addWidget(self.video_panel, 0, 1)
        layout.addLayout(self.detail_grid)

        logs_header = QHBoxLayout()
        logs_title = QLabel("设备日志")
        logs_title.setObjectName("sectionTitle")
        logs_header.addWidget(logs_title)
        logs_header.addStretch()
        self.log_filter = QComboBox()
        self.log_filter.setObjectName("logFilter")
        self.log_filter.addItem("全部等级", None)
        self.log_filter.addItem("info", DeviceLogLevel.INFO)
        self.log_filter.addItem("warning", DeviceLogLevel.WARNING)
        self.log_filter.addItem("error", DeviceLogLevel.ERROR)
        self.log_filter.currentIndexChanged.connect(self._render_logs)
        logs_header.addWidget(self.log_filter)
        self.clear_logs_button = QPushButton("清除日志")
        self.clear_logs_button.setObjectName("dangerButton")
        self.clear_logs_button.clicked.connect(self._clear_logs)
        logs_header.addWidget(self.clear_logs_button)
        layout.addLayout(logs_header)
        self.log_list = QTreeWidget()
        self.log_list.setObjectName("logList")
        self.log_list.setColumnCount(3)
        self.log_list.setHeaderLabels(("时间", "等级", "消息"))
        self.log_list.setRootIsDecorated(False)
        self.log_list.setAlternatingRowColors(True)
        self.log_list.setMinimumHeight(155)
        self.log_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.log_list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.log_list.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.log_list)
        self._apply_responsive_layout(1100)

    @staticmethod
    def _telemetry_panel(title_text: str, field_names: tuple[str, ...]) -> tuple[QFrame, dict[str, QLabel]]:
        panel = QFrame()
        panel.setObjectName("summaryPanel")
        panel.setMinimumHeight(164)
        panel.setMinimumWidth(0)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel(title_text)
        title.setObjectName("metricLabel")
        layout.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        values: dict[str, QLabel] = {}
        for index, name in enumerate(field_names):
            caption = QLabel(name)
            caption.setObjectName("fieldLabel")
            caption.setWordWrap(True)
            caption.setMinimumWidth(0)
            value = QLabel("--")
            value.setObjectName("summaryValue")
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            values[name] = value
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(caption)
            cell.addWidget(value)
            if "X/Y/Z" in name:
                grid.addLayout(cell, 1 + sum("X/Y/Z" in item for item in field_names[:index]), 0, 1, 3)
            else:
                row, column = divmod(index, 3)
                grid.addLayout(cell, row, column)
        layout.addLayout(grid)
        layout.addStretch()
        return panel, values

    @staticmethod
    def _field(name: str) -> tuple[QWidget, QLabel]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        caption = QLabel(name)
        caption.setObjectName("fieldLabel")
        value = QLabel("--")
        value.setObjectName("summaryValue")
        value.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(value)
        return widget, value

    def set_device(self, device: DeviceSnapshot, entries: list[DeviceLogEntry]) -> None:
        if self.device is None or self.device.device_id != device.device_id:
            self.pending_telemetry = None
            self._telemetry_dirty = False
        self.device = device
        self.entries = entries
        self.title.setText(device.device_name)
        self.subtitle.setText(f"{device.device_type}  ·  {device.device_id}")
        self.video_panel.set_device(device)
        self._ensure_status_cards()
        values = {
            "设备名称": device.device_name,
            "设备类型": device.device_type,
            "设备 ID": device.device_id,
            "设备 IP": device.ip_address or "--",
            "最近可用状态": AVAILABILITY_TEXT[device.availability],
            "任务状态": STATUS_TEXT[device.task_status],
            "电量": self._battery_text(device),
            "定位状态": "未知空间",
            "健康状态": HEALTH_TEXT[device.health_status],
            "运行模式": device.flight_mode,
            "电池电压": "--" if device.battery_voltage is None else f"{device.battery_voltage:g} V",
            "电池电流": "--" if device.battery_current is None else f"{device.battery_current:g} A",
            "原始任务状态": device.mission_status_raw,
            "最后心跳": device.last_heartbeat_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if device.last_heartbeat_at else "未收到",
            "UDP 链路状态": UDP_LINK_TEXT[self.pending_telemetry.udp_link_status] if self.pending_telemetry else "未知",
            "UDP 最后心跳": self._format_datetime(self.pending_telemetry.last_heartbeat_at) if self.pending_telemetry else "未收到",
            "UDP 最后数据": self._format_datetime(self.pending_telemetry.last_data_at) if self.pending_telemetry else "未收到",
            "最后连接测试": device.last_tested_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if device.last_tested_at else "未测试",
            "数据更新时间": device.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "SRT 端口": f"{device.srt_port}/UDP",
            "SRT 延迟": f"{device.srt_latency_ms} ms",
            "SRT 地址": (
                build_srt_url(device.ip_address, device.srt_port, device.srt_latency_ms)
                if device.ip_address else "--"
            ),
        }
        for name, value in values.items():
            self.fields[name].setText(value)
        self._update_map_context()
        mqtt_online = device.connection_status.value == "online"
        self.mqtt_card.setProperty("state", "healthy" if mqtt_online else "error")
        self.mqtt_status.setText(f"MQTT · {STATUS_TEXT[device.connection_status]}")
        self.mqtt_heartbeat.setText(
            "最后心跳 " + (
                device.last_heartbeat_at.astimezone().strftime("%H:%M:%S")
                if device.last_heartbeat_at else "未收到"
            )
        )
        self.mqtt_card.style().unpolish(self.mqtt_card)
        self.mqtt_card.style().polish(self.mqtt_card)
        self.log_filter.setCurrentIndex(0)
        self._render_logs()
        self._render_status_cards()

    def set_telemetry(self, telemetry: DeviceTelemetrySnapshot) -> None:
        if (
            self.device is not None
            and telemetry.device_id.casefold() != self.device.device_id.casefold()
        ):
            return
        self.pending_telemetry = telemetry
        self._telemetry_dirty = True
        if self.isVisible() and not self.telemetry_timer.isActive():
            self.telemetry_timer.start()

    def set_logs(self, entries: list[DeviceLogEntry]) -> None:
        self.entries = entries
        self._render_logs()

    def _render_telemetry(self) -> None:
        telemetry = self.pending_telemetry
        if telemetry is None or not self._telemetry_dirty:
            return
        self._telemetry_dirty = False
        self.fields["UDP 链路状态"].setText(UDP_LINK_TEXT[telemetry.udp_link_status])
        self.fields["UDP 最后心跳"].setText(self._format_datetime(telemetry.last_heartbeat_at))
        self.fields["UDP 最后数据"].setText(self._format_datetime(telemetry.last_data_at))
        context = self._map_context()
        self._render_pose(self.global_pose_values, context.map_pose if context else telemetry.global_pose)
        self._render_pose(self.vision_pose_values, context.local_pose if context else telemetry.vision_pose)
        self._render_imu(telemetry.imu)
        self._update_map_context(context)
        self._render_status_cards()

    def _map_context(self):
        if self.device is None or self.source is None:
            return None
        return resolve_device_map_context(
            self.source, self.relocalization_service, self.pending_telemetry,
            self.device.device_id,
        )

    def _update_map_context(self, context=None) -> None:
        context = context or self._map_context()
        if context is not None:
            self.fields["定位状态"].setText(context.localization_text)

    @staticmethod
    def _battery_text(device) -> str:
        if device.battery_percent is not None:
            return f"{device.battery_percent:g}%"
        if device.battery_voltage is not None and device.device_type == "UGV":
            return "待标定"
        return "--"

    @staticmethod
    def _render_pose(labels: dict[str, QLabel], pose: PoseTelemetry | None) -> None:
        values = None if pose is None else (pose.x, pose.y, pose.z, pose.roll, pose.pitch, pose.yaw)
        for name, value in zip(("X", "Y", "Z", "Roll", "Pitch", "Yaw"), values or (None,) * 6):
            unit = " m" if name in {"X", "Y", "Z"} else "°"
            labels[name].setText("--" if value is None else f"{value:.3f}{unit}")

    def _render_imu(self, imu: ImuTelemetry | None) -> None:
        if imu is None:
            for label in self.imu_values.values():
                label.setText("--")
            return
        self.imu_values["Roll"].setText(f"{imu.roll:.3f}°")
        self.imu_values["Pitch"].setText(f"{imu.pitch:.3f}°")
        self.imu_values["Yaw"].setText(f"{imu.yaw:.3f}°")
        self.imu_values["角速度 X/Y/Z"].setText(
            f"{imu.angular_velocity_x:.3f} / {imu.angular_velocity_y:.3f} / {imu.angular_velocity_z:.3f} rad/s"
        )
        self.imu_values["线加速度 X/Y/Z"].setText(
            f"{imu.linear_acceleration_x:.3f} / {imu.linear_acceleration_y:.3f} / {imu.linear_acceleration_z:.3f} m/s²"
        )

    def _ensure_status_cards(self) -> None:
        desired = self.device.status_card_ids if self.device is not None else ()
        if tuple(self.status_cards) == desired:
            return
        while self.status_card_grid.count():
            item = self.status_card_grid.takeAt(0)
            if item.widget() and item.widget() is not self.status_empty:
                item.widget().deleteLater()
        self.status_cards = {
            card_id: DataStatusCard(DEVICE_STATUS_CARD_CATALOG[card_id])
            for card_id in desired
        }
        self._layout_status_cards(self.width())

    def _layout_status_cards(self, width: int) -> None:
        columns = 3 if width >= 1180 else 2 if width >= 720 else 1
        self.status_card_column_count = columns
        if not self.status_cards:
            self.status_card_grid.addWidget(self.status_empty, 0, 0, 1, columns)
            self.status_empty.show()
        else:
            self.status_empty.hide()
            for index, card in enumerate(self.status_cards.values()):
                self.status_card_grid.addWidget(card, index // columns, index % columns)
            for column in range(3):
                self.status_card_grid.setColumnStretch(column, 1 if column < columns else 0)

    def _render_status_cards(self) -> None:
        telemetry = self.pending_telemetry
        statuses = {} if telemetry is None else {item.name: item for item in telemetry.sensor_statuses}
        pointcloud = None if telemetry is None else telemetry.pointcloud
        profile = self.source.profile(self.device.device_id) if self.source and self.device else None
        active_map_id = profile.active_map_id if profile else None
        if "pgm_mapping" in statuses:
            status = statuses["pgm_mapping"]
            if not active_map_id or status.map_id != active_map_id:
                statuses["pgm_mapping"] = SensorStatusTelemetry(
                    "pgm_mapping", "PGM 地图状态", TelemetryAvailability.UNKNOWN,
                    status.sample_age_seconds, "状态来源未关联当前地图", status.map_id,
                )
        if self.device is not None:
            statuses["mapping_mode"] = SensorStatusTelemetry(
                "mapping_mode", "当前建图模式", TelemetryAvailability.AVAILABLE,
                value=map_mode_text(
                    self.map_repository, self.mapping_service, active_map_id,
                    self.device.device_id,
                ), map_id=active_map_id,
            )
        for card_id, card in self.status_cards.items():
            card.update_status(statuses.get(card_id), pointcloud)

    def _edit_status_cards(self) -> None:
        if self.device is None:
            return
        dialog = StatusCardEditorDialog(
            self.device.status_card_ids, self, inherited=self.device.status_cards_inherited
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.status_cards_changed.emit(self.device.device_id, dialog.status_card_override())

    def set_status_card_edit_enabled(self, enabled: bool) -> None:
        self.edit_status_cards_button.setEnabled(enabled)

    @staticmethod
    def _format_datetime(value) -> str:
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value else "未收到"

    def _render_logs(self) -> None:
        self.log_list.clear()
        selected_level = self.log_filter.currentData()
        filtered = [entry for entry in self.entries if selected_level is None or entry.level == selected_level]
        colors = {
            DeviceLogLevel.INFO: QColor(self.theme_palette.primary),
            DeviceLogLevel.WARNING: QColor(self.theme_palette.warning),
            DeviceLogLevel.ERROR: QColor(self.theme_palette.error),
        }
        for entry in filtered:
            item = QTreeWidgetItem((
                entry.timestamp.astimezone().strftime("%H:%M:%S"),
                entry.level.value.upper(),
                entry.message,
            ))
            for column in range(3):
                item.setForeground(column, colors[entry.level])
            self.log_list.addTopLevelItem(item)
        if not filtered:
            item = QTreeWidgetItem(("--", "--", "当前筛选条件下暂无日志"))
            for column in range(3):
                item.setForeground(column, QColor(self.theme_palette.muted))
            self.log_list.addTopLevelItem(item)

    def _clear_logs(self) -> None:
        if self.device is not None:
            self.clear_logs_requested.emit(self.device.device_id)

    def _record_video_event(self, state: str, message: str) -> None:
        if self.device is None:
            return
        level = (
            DeviceLogLevel.ERROR if state == "error"
            else DeviceLogLevel.WARNING if state == "retrying"
            else DeviceLogLevel.INFO
        )
        self.log_event_requested.emit(self.device.device_id, level, message)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        for card in self.status_cards.values():
            card.set_theme(palette)
        self._render_logs()
        self.update()

    def _apply_responsive_layout(self, width: int) -> None:
        telemetry_columns = 3 if width >= 1200 else 2 if width >= 950 else 1
        self.telemetry_column_count = telemetry_columns
        for index, panel in enumerate(self.telemetry_panels):
            self.telemetry_grid.addWidget(panel, index // telemetry_columns, index % telemetry_columns)
        for column in range(3):
            self.telemetry_grid.setColumnStretch(column, 1 if column < telemetry_columns else 0)
        self._layout_status_cards(width)
        wide = width >= 1000
        self.detail_layout_mode = "wide" if wide else "stacked"
        if wide:
            self.detail_grid.addWidget(self.info_panel, 0, 0)
            self.detail_grid.addWidget(self.video_panel, 0, 1)
        else:
            self.detail_grid.addWidget(self.info_panel, 0, 0)
            self.detail_grid.addWidget(self.video_panel, 1, 0)
        self.detail_grid.setColumnStretch(0, 1)
        self.detail_grid.setColumnStretch(1, 1 if wide else 0)
        columns = 2 if (width / 2 if wide else width) >= 430 else 1
        self.info_column_count = columns
        for index, widget in enumerate(self.field_widgets):
            self.info_grid.addWidget(widget, index // columns, index % columns)
        self.detail_grid.activate()
        self.info_grid.activate()

    def stop_video(self) -> None:
        self.video_panel.stop_stream()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop_video()
        self.telemetry_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.device is not None:
            self._telemetry_dirty = self.pending_telemetry is not None
            self.telemetry_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())
