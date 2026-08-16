from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..data_source import DeviceDataSource
from ..models import ConnectionStatus, DeviceSnapshot, SystemOverview
from ..version import __version__


class MetricCard(QFrame):
    def __init__(self, label: str, value: str) -> None:
        super().__init__()
        self.setObjectName("metric")
        self.setMinimumSize(140, 86)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(caption)


class HomePage(QWidget):
    def __init__(
        self, source: DeviceDataSource, overview: SystemOverview, map_repository=None,
        task_repository=None,
    ) -> None:
        super().__init__()
        self.source = source
        self.overview = overview
        self.map_repository = map_repository
        self.task_repository = task_repository
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
        self.online_card = MetricCard("在线设备数", "0")
        self.offline_card = MetricCard("离线设备数", "0")
        map_count = len(self.map_repository.maps()) if self.map_repository is not None else len(self.overview.maps)
        self.maps_card = MetricCard("本地地图数量", str(map_count))
        task_count = self.task_repository.execution_count() if self.task_repository else self.overview.task_execution_count
        self.tasks_card = MetricCard("任务执行次数", str(task_count))
        self.metric_cards = [self.online_card, self.offline_card, self.maps_card, self.tasks_card]
        self.content_layout.addLayout(self.metrics_grid)

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

    def _apply_responsive_layout(self, width: int) -> None:
        metric_columns = 4 if width >= 1120 else 2 if width >= 620 else 1
        for index, card in enumerate(self.metric_cards):
            self.metrics_grid.addWidget(card, index // metric_columns, index % metric_columns)
        task_columns = 2 if width >= 700 else 1
        for index, field in enumerate(self.task_fields):
            self.task_grid.addWidget(field, index // task_columns, index % task_columns)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())
