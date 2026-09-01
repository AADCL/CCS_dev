from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..app_icons import app_icon, asset_icon
from ..data_source import DeviceDataSource
from ..models import ConnectionStatus, DeviceSnapshot, SystemOverview
from ..styles import ThemeMode, ThemePalette, theme_palette
from ..system_status import (
    SubsystemId,
    SubsystemState,
    SubsystemStatus,
    SystemRuntimeStatusStore,
)
from ..version import __version__


RUNTIME_ICON_NAMES = {
    SubsystemId.NTP: "time",
    SubsystemId.MQTT_BROKER: "mqttbroker",
    SubsystemId.MQTT_SUBSCRIBER: "mqtt",
    SubsystemId.UDP_TELEMETRY: "UDP",
    SubsystemId.SRT_FFMPEG: "camera",
}


class CardIcon(QLabel):
    def __init__(
        self,
        label: str,
        *,
        icon_name: str | None = None,
        icon_file: str | None = None,
        size: int,
    ) -> None:
        super().__init__()
        self.icon_name = icon_name
        self.icon_file = icon_file
        self.icon_size = QSize(size, size)
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.setFixedSize(self.icon_size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(label)
        self.setAccessibleName(f"{label}图标")
        self.setProperty("appIconName", icon_name or icon_file or "")
        self._refresh_icon()

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if self.icon_file is not None:
            icon = asset_icon(self.icon_file)
            self.setProperty("appIconMode", "static")
        else:
            icon = app_icon(self.icon_name or "", self.theme_palette)
            self.setProperty("appIconMode", self.theme_palette.mode.value)
        pixmap = icon.pixmap(self.icon_size)
        self.setPixmap(pixmap)
        self.setVisible(not pixmap.isNull())


class MetricCard(QFrame):
    def __init__(
        self,
        label: str,
        value: str,
        *,
        icon_name: str | None = None,
        icon_file: str | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("metric")
        self.setMinimumSize(150, 92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QGridLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(3)
        self.icon_label = CardIcon(
            label, icon_name=icon_name, icon_file=icon_file, size=28
        )
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.caption_label = QLabel(label)
        self.caption_label.setObjectName("metricLabel")
        layout.addWidget(self.icon_label, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.value_label, 0, 1)
        layout.addWidget(self.caption_label, 1, 1)
        layout.setColumnStretch(1, 1)

    def set_theme(self, palette: ThemePalette) -> None:
        self.icon_label.set_theme(palette)


class RuntimeStatusCard(QFrame):
    def __init__(self, status: SubsystemStatus, icon_name: str | None = None) -> None:
        super().__init__()
        self.setObjectName("runtimeStatusCard")
        self.setMinimumSize(190, 92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(5)
        header = QGridLayout()
        self.icon_label = (
            CardIcon(status.display_name, icon_name=icon_name, size=24)
            if icon_name is not None
            else None
        )
        self.indicator = QLabel("●")
        self.indicator.setObjectName("runtimeStatusIndicator")
        self.title = QLabel(status.display_name)
        self.title.setObjectName("metricLabel")
        self.state_label = QLabel()
        self.state_label.setObjectName("runtimeStatusState")
        column = 0
        if self.icon_label is not None:
            header.addWidget(self.icon_label, 0, column)
            column += 1
        header.addWidget(self.indicator, 0, column)
        header.addWidget(self.title, 0, column + 1)
        header.addWidget(
            self.state_label, 0, column + 2, Qt.AlignmentFlag.AlignRight
        )
        header.setColumnStretch(column + 1, 1)
        layout.addLayout(header)
        self.message = QLabel()
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.update_status(status)

    def set_theme(self, palette: ThemePalette) -> None:
        if self.icon_label is not None:
            self.icon_label.set_theme(palette)

    def update_status(self, status: SubsystemStatus) -> None:
        labels = {
            SubsystemState.STARTING: "启动中",
            SubsystemState.HEALTHY: "正常",
            SubsystemState.DEGRADED: "降级",
            SubsystemState.DISABLED: "未启用",
            SubsystemState.ERROR: "故障",
        }
        self.setProperty("state", status.state.value)
        self.indicator.setProperty("state", status.state.value)
        self.state_label.setProperty("state", status.state.value)
        self.state_label.setText(labels[status.state])
        self.message.setText(status.message)
        self.setToolTip(
            f"{status.display_name}\n{status.message}\n更新时间 "
            f"{status.updated_at.astimezone().strftime('%H:%M:%S')}"
        )
        for widget in (self, self.indicator, self.state_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class HomePage(QWidget):
    def __init__(
        self, source: DeviceDataSource, overview: SystemOverview, map_repository=None,
        task_repository=None, status_store: SystemRuntimeStatusStore | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.overview = overview
        self.map_repository = map_repository
        self.task_repository = task_repository
        self.status_store = status_store
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.task_value_labels: dict[str, QLabel] = {}
        self._build()
        self.update_devices(source.snapshots())
        source.devices_updated.connect(self.update_devices)
        if map_repository is not None:
            map_repository.maps_updated.connect(self.update_maps)
        if task_repository is not None:
            task_repository.tasks_updated.connect(self.update_tasks)
            task_repository.execution_updated.connect(self.update_tasks)
            self.update_tasks()
        if status_store is not None:
            status_store.status_changed.connect(self.update_subsystem_status)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        content = QWidget()
        content.setObjectName("pageContent")
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(30, 26, 30, 30)
        self.content_layout.setSpacing(24)
        scroll.setWidget(content)

        eyebrow = QLabel("WELCOME BACK / SYSTEM OVERVIEW")
        eyebrow.setObjectName("eyebrow")
        self.content_layout.addWidget(eyebrow)
        title = QLabel("多异构智能体指挥与控制系统")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        self.content_layout.addWidget(title)
        version = QLabel(f"当前版本  v{__version__}")
        version.setObjectName("versionLabel")
        self.content_layout.addWidget(version)

        overview_title = QLabel("指控系统全览状态")
        overview_title.setObjectName("sectionTitle")
        self.content_layout.addWidget(overview_title)
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setHorizontalSpacing(12)
        self.metrics_grid.setVerticalSpacing(12)
        self.online_card = MetricCard(
            "在线设备数", "0", icon_file="devices_online.svg"
        )
        self.offline_card = MetricCard(
            "离线设备数", "0", icon_file="devices_offline.svg"
        )
        map_count = len(self.map_repository.maps()) if self.map_repository is not None else len(self.overview.maps)
        self.maps_card = MetricCard(
            "本地地图数量", str(map_count), icon_name="mapstorage"
        )
        task_count = self.task_repository.execution_count() if self.task_repository else self.overview.task_execution_count
        self.tasks_card = MetricCard(
            "任务执行次数", str(task_count), icon_name="tasks"
        )
        self.metric_cards = [self.online_card, self.offline_card, self.maps_card, self.tasks_card]
        self.content_layout.addLayout(self.metrics_grid)

        runtime_title = QLabel("指控平台子系统状态")
        runtime_title.setObjectName("sectionTitle")
        self.content_layout.addWidget(runtime_title)
        self.runtime_grid = QGridLayout()
        self.runtime_grid.setHorizontalSpacing(12)
        self.runtime_grid.setVerticalSpacing(12)
        self.runtime_cards: dict[object, RuntimeStatusCard] = {}
        if self.status_store is not None:
            for status in self.status_store.statuses():
                self.runtime_cards[status.subsystem_id] = RuntimeStatusCard(
                    status, RUNTIME_ICON_NAMES.get(status.subsystem_id)
                )
        self.content_layout.addLayout(self.runtime_grid)

        task_title = QLabel("任务执行总结")
        task_title.setObjectName("sectionTitle")
        self.content_layout.addWidget(task_title)
        task_panel = QFrame()
        task_panel.setObjectName("summaryPanel")
        self.task_grid = QGridLayout(task_panel)
        self.task_grid.setContentsMargins(18, 18, 18, 18)
        self.task_grid.setHorizontalSpacing(32)
        self.task_grid.setVerticalSpacing(14)
        task = self.overview.last_task
        self.task_fields = [
            self._field("title", "上次任务标题", task.title),
            self._field("type", "上次任务类型", task.task_type),
            self._field("started", "开始时间", task.started_at.astimezone().strftime("%Y-%m-%d %H:%M")),
            self._field("ended", "结束时间", task.ended_at.astimezone().strftime("%Y-%m-%d %H:%M")),
            self._field("status", "执行状态", task.status),
        ]
        self.content_layout.addWidget(task_panel)
        self.content_layout.addStretch()
        self._apply_responsive_layout(1200)

    def _field(self, key: str, label: str, value: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        text = QLabel(value)
        text.setObjectName("summaryValue")
        text.setWordWrap(True)
        self.task_value_labels[key] = text
        layout.addWidget(caption)
        layout.addWidget(text)
        return widget

    def update_devices(self, devices: object) -> None:
        snapshots = list(devices)
        online = sum(device.connection_status == ConnectionStatus.ONLINE for device in snapshots)
        offline = len(snapshots) - online
        self.online_card.value_label.setText(str(online))
        self.offline_card.value_label.setText(str(offline))

    def update_maps(self, maps: object) -> None:
        self.maps_card.value_label.setText(str(len(list(maps))))

    def update_tasks(self, _event: object = None) -> None:
        if self.task_repository is None:
            return
        self.tasks_card.value_label.setText(str(self.task_repository.execution_count()))
        execution = self.task_repository.latest_execution()
        if execution is None:
            values = {"title": "暂无执行记录", "type": "地图轨迹任务", "started": "--", "ended": "--", "status": "未执行"}
        else:
            task = self.task_repository.task_by_id(execution.task_id)
            terminal = execution.status.value in {"completed", "stopped", "failed", "cancelled"}
            values = {
                "title": task.name if task else execution.task_id,
                "type": "地图轨迹任务",
                "started": (execution.scheduled_at or execution.created_at).astimezone().strftime("%Y-%m-%d %H:%M"),
                "ended": execution.updated_at.astimezone().strftime("%Y-%m-%d %H:%M") if terminal else "执行中",
                "status": execution.status.value,
            }
        for key, value in values.items():
            self.task_value_labels[key].setText(value)

    def update_subsystem_status(self, status: SubsystemStatus) -> None:
        card = self.runtime_cards.get(status.subsystem_id)
        if card is not None:
            card.update_status(status)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        for card in self.metric_cards:
            card.set_theme(palette)
        for card in self.runtime_cards.values():
            card.set_theme(palette)
        self.update()

    def _apply_responsive_layout(self, width: int) -> None:
        metric_columns = 4 if width >= 1120 else 2 if width >= 620 else 1
        for index, card in enumerate(self.metric_cards):
            self.metrics_grid.addWidget(card, index // metric_columns, index % metric_columns)
        runtime_columns = 3 if width >= 1080 else 2 if width >= 620 else 1
        for index, card in enumerate(self.runtime_cards.values()):
            self.runtime_grid.addWidget(card, index // runtime_columns, index % runtime_columns)
        task_columns = 2 if width >= 700 else 1
        for index, field in enumerate(self.task_fields):
            self.task_grid.addWidget(field, index // task_columns, index % task_columns)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())
