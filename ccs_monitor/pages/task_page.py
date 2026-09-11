from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Callable

from PySide6.QtCore import QEvent, QSettings, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QSizePolicy, QStackedWidget, QStyle, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..data_source import DeviceDataSource
from ..app_icons import apply_button_icon
from ..map_repository import MapRepository, MapRepositoryError
from ..models import ConnectionStatus, DeviceMapMarker, MapDefinition, MapMarkerShape, MapStatus, utc_now
from ..task_conflicts import TaskConflictDetector
from ..task_map import GridPointValidator
from ..task_models import (
    DeviceSubtask, TaskDefinition, TaskDefinitionStatus, TaskEventLevel,
    TaskSafetySettings, TaskWaypoint,
)
from ..task_repository import TaskRepository, TaskRepositoryError, map_fingerprint
from ..styles import ThemeMode, ThemePalette, theme_palette
from ..widgets import NoButtonDoubleSpinBox
from .map_page import PointCloudViewer, bound_map_pose, fixed_width_font


class NewTaskDialog(QDialog):
    def __init__(self, maps: list[MapDefinition], devices: list, active_map_id: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建任务")
        self.setMinimumSize(620, 520)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("任务名称"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：厂区联合巡检")
        root.addWidget(self.name_input)
        root.addWidget(QLabel("选择地图（单选）"))
        self.map_combo = QComboBox()
        for definition in maps:
            if definition.status == MapStatus.READY and (definition.pcd_path or definition.pgm):
                label = definition.name + ("  · 当前激活地图" if definition.map_id == active_map_id else "")
                self.map_combo.addItem(label, definition.map_id)
        active_index = self.map_combo.findData(active_map_id)
        if active_index >= 0:
            self.map_combo.setCurrentIndex(active_index)
        root.addWidget(self.map_combo)
        root.addWidget(QLabel("选择设备（可多选，离线设备仍可选择）"))
        scroll = QScrollArea()
        scroll.setObjectName("mapDeviceScroll")
        scroll.viewport().setObjectName("mapDeviceViewport")
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setObjectName("mapDeviceSelector")
        layout = QVBoxLayout(container)
        self.device_checks: dict[str, QCheckBox] = {}
        for device in sorted(devices, key=lambda item: (item.connection_status.value != "online", item.device_name.casefold())):
            check = QCheckBox(
                f"{device.device_name}  ·  {device.device_id}  ·  {device.device_type}  ·  {device.connection_status.value}"
            )
            check.toggled.connect(self._validate)
            self.device_checks[device.device_id] = check
            layout.addWidget(check)
        layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)
        self.validation = QLabel()
        self.validation.setObjectName("validationError")
        root.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.create_button = buttons.addButton("创建任务", QDialogButtonBox.ButtonRole.AcceptRole)
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.name_input.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        valid = bool(self.name_input.text().strip() and self.map_combo.count() and any(item.isChecked() for item in self.device_checks.values()))
        self.create_button.setEnabled(valid)
        self.validation.setText("" if valid else "请填写名称、选择地图并至少选择一台设备")

    def selected_device_ids(self) -> tuple[str, ...]:
        return tuple(key for key, value in self.device_checks.items() if value.isChecked())


class TaskCard(QFrame):
    open_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, task: TaskDefinition, active_map: bool = False, parent=None, *, map_exists: bool = True) -> None:
        super().__init__(parent)
        self.task = task
        self.setObjectName("compactListCard")
        self.setProperty("active", False)
        self.setMinimumHeight(153)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(5)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = self._label(task.name, "compactCardTitle")
        self.title.setToolTip(task.name)
        state = self.status_key(task)
        self.status = self._label({"ready": "配置就绪", "draft": "草稿", "error": "加载异常"}[state], "compactCardStatus", wrap=False)
        self.status.setProperty("state", state)
        self.status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header.addWidget(self.title, 1)
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        metadata = QGridLayout()
        metadata.setHorizontalSpacing(8)
        metadata.setVerticalSpacing(2)
        metadata.addWidget(self._label("地图", "compactFieldLabel", wrap=False), 0, 0)
        map_row = QHBoxLayout()
        map_row.setSpacing(6)
        map_name = self._label(task.map_name or "未知地图", "compactFieldValue")
        map_name.setToolTip(task.map_name or "未知地图")
        map_row.addWidget(map_name, 1)
        map_state = "未读取" if state == "error" else "地图不存在" if not map_exists else "当前激活" if active_map else "非激活"
        map_tag = self._label(map_state, "compactActiveTag" if active_map and map_exists else "compactCardError" if not map_exists else "compactFieldLabel", wrap=False)
        map_tag.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        map_row.addWidget(map_tag, 0, Qt.AlignmentFlag.AlignTop)
        metadata.addLayout(map_row, 0, 1)
        metadata.addWidget(self._label("文件时间" if state == "error" else "更新", "compactFieldLabel", wrap=False), 1, 0)
        metadata.addWidget(self._label(task.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"), "compactFieldValue"), 1, 1)
        metadata.setColumnStretch(1, 1)
        layout.addLayout(metadata)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        total = len(task.subtasks)
        values = (
            ("设备", "--" if state == "error" else f"{total} 台"),
            ("有效子任务", "--" if state == "error" else f"{sum(item.is_valid for item in task.subtasks)}/{total}"),
            ("任务点", "--" if state == "error" else f"{sum(len(item.waypoints) for item in task.subtasks):,}"),
        )
        for caption, value in values:
            metric = self._label(f"{caption}  {value}", "compactCardMetric")
            metrics.addWidget(metric, 1)
        layout.addLayout(metrics)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        if state == "error":
            error = self._label(task.error_message or "任务文件无法读取", "compactCardError")
            error.setToolTip(task.error_message or "任务文件无法读取")
            actions.addWidget(error, 1)
        else:
            actions.addStretch()
        self.open_button = QPushButton("查看原因" if state == "error" else "打开任务")
        self.open_button.setObjectName("compactPrimaryButton")
        self.open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation if state == "error" else QStyle.StandardPixmap.SP_ArrowForward))
        self.open_button.setMinimumHeight(28)
        self.open_button.clicked.connect(lambda: self.open_requested.emit(task.task_id))
        self.more_button = QToolButton()
        self.more_button.setObjectName("compactToolButton")
        self.more_button.setText("...")
        self.more_button.setFixedSize(28, 28)
        self.more_button.setToolTip("更多操作")
        self.more_button.setAccessibleName("更多任务操作")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_button)
        self.delete_action = menu.addAction(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "删除任务")
        self.delete_action.setEnabled(state != "error")
        self.delete_action.setToolTip("异常任务无法删除" if state == "error" else "删除任务及其执行日志")
        self.delete_action.triggered.connect(lambda: self.delete_requested.emit(task.task_id))
        self.more_button.setMenu(menu)
        actions.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignBottom)
        actions.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(actions)

    @staticmethod
    def status_key(task: TaskDefinition) -> str:
        if task.status == TaskDefinitionStatus.ERROR:
            return "error"
        return "ready" if task.is_ready else "draft"

    @staticmethod
    def _label(text: str, name: str, *, wrap: bool = True) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName(name)
        label.setWordWrap(wrap)
        label.setMinimumWidth(0)
        if wrap:
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label


class TaskDeviceCard(QFrame):
    selected = Signal(int)
    action_requested = Signal(int, str)

    def __init__(self, row: int, subtask: DeviceSubtask, selected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.row = row
        self.setObjectName("taskDeviceCard")
        self.setProperty("selected", bool(selected))
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 9, 10, 9)
        root.setSpacing(5)
        header = QHBoxLayout()
        name = self.name_label = QLabel(subtask.device_name)
        name.setObjectName("cardTitle")
        header.addWidget(name, 1)
        root.addLayout(header)
        detail = self.detail_label = QLabel(
            f"{subtask.device_id}  ·  {subtask.device_type}\n"
            f"任务点 {len(subtask.waypoints)}  ·  revision {subtask.revision}"
        )
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        root.addWidget(detail)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.buttons = {}
        buttons = QHBoxLayout()
        for label, action in (("创建子任务", "create"), ("读取子任务", "read"), ("删除子任务", "delete")):
            button = QPushButton(label)
            button.setObjectName("taskDeviceAction")
            if action == "read":
                button.setEnabled(subtask.is_delivered and subtask.edge_revision == subtask.revision)
            elif action == "delete":
                button.setEnabled(subtask.edge_status.value not in {"no_task", "failed"})
            button.clicked.connect(lambda _checked=False, value=action: self.action_requested.emit(self.row, value))
            self.buttons[action] = button
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.update_subtask(subtask)

    def update_subtask(self, subtask, state=None):
        self.name_label.setText(subtask.device_name)
        self.detail_label.setText(f"{subtask.device_id}  ·  {subtask.device_type}\n任务点 {len(subtask.waypoints)}  ·  revision {subtask.revision}")
        states = {"preparing": "下发中", "committing": "下发中", "receiving": "下发中",
                  "delivered": "已接收待就绪", "received": "已接收待就绪", "ready": "就绪",
                  "failed": "失败", "no_task": "未下发", "task_exists": "待确认"}
        label = "就绪" if subtask.edge_ready else states.get(state or subtask.edge_status.value, "未就绪")
        self.status_label.setText(label + (f" · {subtask.edge_message}" if subtask.edge_message else ""))
        self.buttons["read"].setEnabled(subtask.is_delivered)
        self.buttons["delete"].setEnabled(subtask.edge_status.value not in {"no_task", "failed"})

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.selected.emit(self.row)
        super().mousePressEvent(event)


class TaskEditorPage(QWidget):
    back_requested = Signal()

    def __init__(self, repository, map_repository, source, execution_service=None, telemetry_store=None, viewer_factory=None) -> None:
        super().__init__()
        self.repository = repository
        self.map_repository = map_repository
        self.source = source
        self.execution_service = execution_service
        self.telemetry_store = telemetry_store
        self.task: TaskDefinition | None = None
        self.current_subtask_id: str | None = None
        self.drafts: dict[str, DeviceSubtask] = {}
        self.conflicts = ()
        self.grid_validator = None
        self.map_collapsed = False
        self.map_reviewed = True
        self.active_execution_id: str | None = None
        self.active_execution_status: str | None = None
        self.execution_service_available = False
        self._log_cleared_before = None
        self._transfer_states = {}
        self._telemetry_dirty = True
        self.trajectory_store = None
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.viewer = viewer_factory() if viewer_factory else PointCloudViewer()
        self.detector = TaskConflictDetector()
        self._build()
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(100)
        self._render_timer.timeout.connect(self._flush_realtime)
        self._render_timer.start()
        for control in (self.default_z, self.speed, self.delay):
            control.valueChanged.connect(self._controls_changed)
        if hasattr(source, "devices_updated"):
            source.devices_updated.connect(lambda _items: self._update_execution_controls())
        self.viewer.map_point_picked.connect(self._add_picked_point)
        if telemetry_store is not None:
            telemetry_store.telemetry_updated.connect(self._telemetry_updated)
        if execution_service is not None:
            execution_service.transfer_updated.connect(self._transfer_updated)
            execution_service.execution_updated.connect(self._execution_updated)
        self.repository.tasks_updated.connect(self._repository_tasks_updated)
        self.repository.event_appended.connect(self._append_event)
        self.set_execution_available(
            bool(execution_service and getattr(execution_service, "available", False)),
            getattr(execution_service, "module_message", "UDP 任务服务未配置"),
        )

    def _build(self) -> None:
        self.setObjectName("taskEditorPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 18)
        header = QHBoxLayout()
        self.back_button = QPushButton("返回")
        self.back_button.setObjectName("backButton")
        self.back_button.setAccessibleName("返回任务列表")
        self.back_button.setToolTip("返回任务列表")
        self.back_button.clicked.connect(self.back_requested)
        apply_button_icon(self.back_button, "back", self.theme_palette, text="返回")
        self.title = QLabel("任务编辑")
        self.title.setObjectName("pageTitle")
        self.sync_deliver = QPushButton("同步下发")
        self.sync_deliver.setObjectName("primaryButton")
        apply_button_icon(self.sync_deliver, "upload", self.theme_palette, text="同步下发")
        self.sync_deliver.clicked.connect(self._deliver_all)
        self.ready_count = QLabel("就绪 0 / 0")
        self.run_all = QPushButton("开始主任务")
        self.run_all.setObjectName("primaryButton")
        self.run_all.clicked.connect(self._execute_all)
        self.stop_button = QPushButton("终止主任务")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_execution)
        self.emergency_button = QPushButton("急停")
        self.emergency_button.setObjectName("dangerButton")
        self.emergency_button.setEnabled(False)
        self.emergency_button.clicked.connect(self._emergency_stop)
        header.addWidget(self.back_button)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.ready_count)
        header.addWidget(self.sync_deliver)
        header.addWidget(self.run_all)
        header.addWidget(self.stop_button)
        header.addWidget(self.emergency_button)
        root.addLayout(header)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("taskEditorMainSplitter")
        left_panel = QFrame()
        left_panel.setMinimumWidth(280)
        left_panel.setObjectName("taskEditorPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("任务设备"))
        self.device_scroll = QScrollArea()
        self.device_scroll.setWidgetResizable(True)
        self.device_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.device_cards_container = QWidget()
        self.device_cards_layout = QVBoxLayout(self.device_cards_container)
        self.device_cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.device_scroll.setWidget(self.device_cards_container)
        left_layout.addWidget(self.device_scroll, 1)
        self.device_cards: list[TaskDeviceCard] = []
        self.selected_row: int | None = None
        self.main_splitter.addWidget(left_panel)

        self.map_panel = QFrame()
        self.map_panel.setObjectName("taskEditorPanel")
        map_layout = QVBoxLayout(self.map_panel)
        map_layout.addWidget(self.viewer, 1)
        self.main_splitter.addWidget(self.map_panel)

        right = QFrame()
        right.setObjectName("taskEditorPanel")
        right.setMinimumWidth(330)
        right_layout = QVBoxLayout(right)
        settings = QGridLayout()
        self.default_z = self._spin(-10000, 10000, 1.0, " m")
        self.speed = self._spin(0.01, 1000, 1.0, " m/s")
        self.delay = self._spin(0, 86400, 0.0, " s")
        for row, (caption, control) in enumerate((
            ("默认高度", self.default_z), ("巡航速度", self.speed), ("启动延迟", self.delay),
        )):
            settings.addWidget(QLabel(caption), row, 0)
            settings.addWidget(control, row, 1)
        right_layout.addLayout(settings)
        self.waypoints = QTableWidget(0, 4)
        self.waypoints.setObjectName("taskWaypointTable")
        self.waypoints.setHorizontalHeaderLabels(("序号", "X", "Y", "Z"))
        self.waypoints.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.waypoints.setAlternatingRowColors(True)
        self.waypoints.verticalHeader().setVisible(False)
        self.waypoints.horizontalHeader().setStretchLastSection(True)
        self.waypoints.itemChanged.connect(self._waypoint_edited)
        right_layout.addWidget(self.waypoints, 1)
        actions = QHBoxLayout()
        for text, slot in (("添加", self._add_manual), ("删除", self._delete_waypoint), ("上移", lambda: self._move(-1)), ("下移", lambda: self._move(1))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        right_layout.addLayout(actions)
        save_row = QHBoxLayout()
        self.pick_toggle = QPushButton("开始选点")
        self.pick_toggle.setCheckable(True)
        self.pick_toggle.toggled.connect(self._pick_toggled)
        self.deliver_button = QPushButton("保存下发")
        apply_button_icon(
            self.deliver_button, "upload", self.theme_palette, text="保存下发",
        )
        self.deliver_button.clicked.connect(self._deliver_current)
        self.run_one = QPushButton("执行任务")
        self.run_one.setObjectName("primaryButton")
        self.run_one.clicked.connect(self._toggle_current_execution)
        save_row.addWidget(self.deliver_button)
        save_row.addWidget(self.pick_toggle)
        save_row.addWidget(self.run_one)
        right_layout.addLayout(save_row)
        self.right_panel = right
        self.right_panel.setVisible(False)
        self.main_splitter.addWidget(right)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([200, 700, 350])
        root.addWidget(self.main_splitter, 3)

        lower = QSplitter(Qt.Orientation.Horizontal)
        lower.setObjectName("taskEditorLowerSplitter")
        conflict_panel = QFrame()
        conflict_panel.setObjectName("taskEditorPanel")
        conflict_layout = QVBoxLayout(conflict_panel)
        safety_row = QHBoxLayout()
        self.horizontal_clearance = self._spin(0.01, 1000, 2.0, " m")
        self.vertical_clearance = self._spin(0.01, 1000, 1.0, " m")
        self.time_margin = self._spin(0.01, 3600, 2.0, " s")
        for text, control in (("水平", self.horizontal_clearance), ("垂直", self.vertical_clearance), ("时间", self.time_margin)):
            safety_row.addWidget(QLabel(text))
            safety_row.addWidget(control)
            control.valueChanged.connect(self._recalculate_conflicts)
        conflict_layout.addLayout(safety_row)
        self.conflict_list = QListWidget()
        self.conflict_list.setObjectName("taskConflictList")
        conflict_layout.addWidget(self.conflict_list)
        lower.addWidget(conflict_panel)
        log_panel = QFrame()
        log_panel.setObjectName("taskEditorPanel")
        log_layout = QVBoxLayout(log_panel)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("任务日志与交互数据"))
        log_header.addStretch()
        self.clear_log_button = QPushButton("清除")
        self.clear_log_button.clicked.connect(self._clear_log_view)
        log_header.addWidget(self.clear_log_button)
        log_layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("mappingProtocolLog")
        self.log_view.setReadOnly(True)
        self.log_view.setUndoRedoEnabled(False)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFont(fixed_width_font())
        log_layout.addWidget(self.log_view)
        lower.addWidget(log_panel)
        lower.setSizes([500, 700])
        root.addWidget(lower, 1)

    @staticmethod
    def _spin(minimum, maximum, value, suffix):
        control = NoButtonDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setValue(value)
        control.setSuffix(suffix)
        return control

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        apply_button_icon(self.back_button, "back", palette, text="返回")
        apply_button_icon(self.deliver_button, "upload", palette, text="保存下发")
        self.viewer.set_theme(palette)
        self.update()

    def set_task(self, task: TaskDefinition) -> None:
        self.pick_toggle.setChecked(False)
        self._transfer_states.clear()
        self.task = task
        self.active_execution_id = None
        self.active_execution_status = None
        self._log_cleared_before = None
        self.drafts = {item.subtask_id: item for item in task.subtasks}
        self.title.setText(f"{task.name}  ·  {task.map_name}")
        active_suffix = "当前激活地图" if self.map_repository.active_map_id() == task.map_id else "非当前激活地图"
        self.title.setText(f"{task.name}  ·  {task.map_name}  ·  {active_suffix}")
        self.horizontal_clearance.setValue(task.safety.horizontal_distance_m)
        self.vertical_clearance.setValue(task.safety.vertical_distance_m)
        self.time_margin.setValue(task.safety.time_margin_seconds)
        while self.device_cards_layout.count():
            item = self.device_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.device_cards = []
        self.selected_row = None
        self.current_subtask_id = None
        self.right_panel.setVisible(False)
        for row, item in enumerate(task.subtasks):
            card = TaskDeviceCard(row, item)
            card.selected.connect(self._device_selected)
            card.action_requested.connect(self._device_action)
            self.device_cards.append(card)
            self.device_cards_layout.addWidget(card)
        self._load_map(task)
        self._load_logs()
        for item in task.subtasks:
            if self.execution_service and getattr(self.execution_service, "available", False):
                try:
                    self.execution_service.negotiate_subtask(task, item)
                except Exception:
                    pass
        self._recalculate_conflicts()
        self._update_execution_controls()

    def _repository_tasks_updated(self, tasks: object) -> None:
        if self.task is None:
            return
        updated = self.repository.task_by_id(self.task.task_id)
        if updated is None:
            return
        self.task = updated
        for item in updated.subtasks:
            draft = self.drafts.get(item.subtask_id)
            if draft is not None:
                self.drafts[item.subtask_id] = replace(
                    draft,
                    delivered_revision=item.delivered_revision,
                    edge_status=item.edge_status,
                    edge_revision=item.edge_revision,
                    edge_message=item.edge_message,
                    edge_updated_at=item.edge_updated_at,
                )
        for card, item in zip(self.device_cards, updated.subtasks):
            card.update_subtask(self.drafts.get(item.subtask_id, item), self._transfer_states.get(item.device_id))
        self._update_execution_controls()

    def set_map_reviewed(self, reviewed: bool) -> None:
        self.map_reviewed = bool(reviewed)
        self._update_execution_controls()

    def refresh_active_map_state(self) -> None:
        if self.task:
            suffix = "当前激活地图" if self.map_repository.active_map_id() == self.task.map_id else "非当前激活地图"
            self.title.setText(f"{self.task.name}  ·  {self.task.map_name}  ·  {suffix}")

    def set_execution_available(self, available: bool, message: str = "") -> None:
        self.execution_service_available = bool(available)
        tooltip = message or "UDP 任务服务不可用"
        for button in (
            self.deliver_button, self.sync_deliver, self.run_one, self.run_all,
            self.stop_button, self.emergency_button, self.pick_toggle,
        ):
            button.setToolTip("" if available else tooltip)
        self._update_execution_controls()

    def _draft_ready(self, subtask) -> bool:
        if self.task is None:
            return False
        saved = next((item for item in self.task.subtasks if item.subtask_id == subtask.subtask_id), None)
        device = self.source.device(subtask.device_id)
        return bool(saved and saved.edge_ready and saved.same_definition(subtask)
                    and device and device.connection_status == ConnectionStatus.ONLINE
                    and not (self.execution_service and self.execution_service.device_active(subtask.device_id)))

    def _update_execution_controls(self) -> None:
        available = bool(self.execution_service_available and self.map_reviewed)
        subtask = self._current()
        active = self.active_execution_id is not None
        stopping = self.active_execution_status == "stopping"
        busy = bool(self.task and self.execution_service and hasattr(self.execution_service, "transfer_active")
                    and any(self.execution_service.transfer_active(self.task.task_id, item.device_id) for item in self.task.subtasks))
        editing = available and subtask is not None and not busy
        picking = self.pick_toggle.isChecked()
        self.deliver_button.setEnabled(bool(editing and not active and not picking and len(subtask.waypoints) >= 2))
        self.sync_deliver.setEnabled(bool(available and self.task and not active and not busy and not picking))
        self.right_panel.setEnabled(not busy and not active)
        self.pick_toggle.setEnabled(editing and not active)
        ready = sum(self._draft_ready(item) for item in self.drafts.values())
        self.ready_count.setText(f"就绪 {ready} / {len(self.drafts)}")
        active_map = bool(self.task and self.map_repository.active_map_id() == self.task.map_id)
        self.run_all.setEnabled(bool(available and active_map and self.drafts and ready == len(self.drafts)
                                    and not active and not busy and not picking))
        self.stop_button.setEnabled(active and not stopping)
        self.emergency_button.setEnabled(active)
        label = "终止任务" if active else "执行任务"
        if self.run_one.text() != label:
            self.run_one.setText(label)
            self.run_one.setObjectName("dangerButton" if active else "primaryButton")
            self.run_one.style().unpolish(self.run_one)
            self.run_one.style().polish(self.run_one)
        self.run_one.setEnabled(bool((active and not stopping) or
                                     (editing and active_map and not active and self._draft_ready(subtask))))
        if not self.map_reviewed:
            for button in (self.deliver_button, self.sync_deliver, self.run_one, self.run_all):
                button.setToolTip("地图图层已变化，需要人工复核后才能下发或执行")

    def _controls_changed(self, *_args):
        self._store_current_controls()
        self._changed()

    def _load_map(self, task: TaskDefinition) -> None:
        definition = self.map_repository.map_by_id(task.map_id)
        if definition is None:
            self.viewer.show_message("任务关联地图不存在")
            return
        self.viewer.clear()
        if definition.pcd_path:
            try:
                self.viewer.load_map(definition, self.map_repository.pcd_path(definition.map_id))
            except Exception:
                pass
        if definition.pgm:
            try:
                yaml_path, image_path = self.map_repository.pgm_paths(definition.map_id)
                self.viewer.load_pgm_layer(definition, yaml_path)
                self.grid_validator = GridPointValidator(definition.pgm, image_path)
            except Exception:
                self.grid_validator = None

    def _set_selected_row(self, row: int) -> None:
        if not self.task or not 0 <= row < len(self.task.subtasks):
            return
        self.selected_row = row
        for index, card in enumerate(self.device_cards):
            card.setProperty("selected", index == row)
            card.style().unpolish(card)
            card.style().polish(card)

    def _store_current_controls(self) -> None:
        current = self._current()
        if current is not None:
            self.drafts[current.subtask_id] = replace(
                current,
                default_altitude_m=self.default_z.value(),
                cruise_speed_mps=self.speed.value(),
                start_delay_seconds=self.delay.value(),
                layer_mode=self._effective_layer_mode(),
            )

    def _device_selected(self, row: int) -> None:
        self._store_current_controls()
        self.pick_toggle.setChecked(False)
        self.current_subtask_id = None
        self.right_panel.setVisible(False)
        self._set_selected_row(row)
        self._update_execution_controls()

    def _open_subtask(self, row: int) -> None:
        if not self.task or not 0 <= row < len(self.task.subtasks):
            return
        self._store_current_controls()
        self.pick_toggle.setChecked(False)
        self._set_selected_row(row)
        subtask = self.drafts[self.task.subtasks[row].subtask_id]
        self.current_subtask_id = subtask.subtask_id
        for control in (self.default_z, self.speed, self.delay):
            control.blockSignals(True)
        self.default_z.setValue(subtask.default_altitude_m)
        self.speed.setValue(subtask.cruise_speed_mps)
        self.delay.setValue(subtask.start_delay_seconds)
        for control in (self.default_z, self.speed, self.delay):
            control.blockSignals(False)
        target_mode = subtask.layer_mode
        if target_mode == "grid" and not self.viewer.pgm_loaded:
            target_mode = "pointcloud"
        self.viewer.set_layer_mode(target_mode)
        self._render_waypoints(subtask)
        self._render_paths()
        self.right_panel.setVisible(True)
        self._update_execution_controls()

    def _device_action(self, row: int, action: str) -> None:
        self._device_selected(row)
        if not self.task or not 0 <= row < len(self.task.subtasks):
            return
        subtask = self.drafts[self.task.subtasks[row].subtask_id]
        if self.task is None or subtask is None:
            return
        if action == "read" and self.execution_service:
            self.execution_service.read_subtask(self.task, subtask)
        elif action == "delete" and self.execution_service:
            self.execution_service.delete_subtask(self.task, subtask)
        elif action == "create":
            self._open_subtask(row)

    def _read_current(self) -> None:
        subtask = self._current()
        if self.task and subtask and self.execution_service:
            self.execution_service.read_subtask(self.task, subtask)

    def _delete_current_remote(self) -> None:
        subtask = self._current()
        if self.task and subtask and self.execution_service:
            self.execution_service.delete_subtask(self.task, subtask)

    def _render_waypoints(self, subtask: DeviceSubtask) -> None:
        self.waypoints.blockSignals(True)
        self.waypoints.setRowCount(len(subtask.waypoints))
        for row, point in enumerate(subtask.waypoints):
            self.waypoints.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            for column, value in enumerate((point.x, point.y, point.z), 1):
                self.waypoints.setItem(row, column, QTableWidgetItem(f"{value:.3f}"))
        self.waypoints.blockSignals(False)

    def _current(self) -> DeviceSubtask | None:
        return self.drafts.get(self.current_subtask_id or "")

    def _pick_toggled(self, enabled: bool) -> None:
        self.viewer.set_interaction_mode("pick" if enabled else "browse")
        self.pick_toggle.setText("结束选点" if enabled else "开始选点")
        self._update_execution_controls()

    def _effective_layer_mode(self) -> str:
        return "grid" if self.viewer.layer_mode == "grid" else "pointcloud"

    def _add_picked_point(self, x: float, y: float) -> None:
        subtask = self._current()
        if subtask is None:
            return
        mode = self._effective_layer_mode()
        if mode == "grid" and (self.grid_validator is None or not self.grid_validator.is_free(x, y)):
            QMessageBox.warning(self, "无法创建任务点", "该位置不是占据栅格中的空闲区域")
            return
        self._append_waypoint(x, y, self.default_z.value(), mode)

    def _add_manual(self) -> None:
        self._append_waypoint(0.0, 0.0, self.default_z.value(), self._effective_layer_mode())

    def _append_waypoint(self, x, y, z, mode) -> None:
        subtask = self._current()
        if subtask is None or len(subtask.waypoints) >= 500:
            return
        point = TaskWaypoint(uuid.uuid4().hex, float(x), float(y), float(z))
        updated = replace(
            subtask, layer_mode=mode, waypoints=(*subtask.waypoints, point),
            default_altitude_m=self.default_z.value(), cruise_speed_mps=self.speed.value(),
            start_delay_seconds=self.delay.value(),
        )
        self.drafts[subtask.subtask_id] = updated
        self._render_waypoints(updated)
        self._changed()

    def _waypoint_edited(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            return
        subtask = self._current()
        if subtask is None:
            return
        try:
            value = float(item.text())
        except ValueError:
            self._render_waypoints(subtask)
            return
        point = subtask.waypoints[item.row()]
        values = [point.x, point.y, point.z]
        values[item.column() - 1] = value
        if subtask.layer_mode == "grid" and self.grid_validator and not self.grid_validator.is_free(values[0], values[1]):
            QMessageBox.warning(self, "坐标无效", "修改后的 XY 不在空闲栅格中")
            self._render_waypoints(subtask)
            return
        points = list(subtask.waypoints)
        points[item.row()] = TaskWaypoint(point.waypoint_id, *values)
        self.drafts[subtask.subtask_id] = replace(subtask, waypoints=tuple(points))
        self._changed()

    def _delete_waypoint(self) -> None:
        subtask = self._current()
        row = self.waypoints.currentRow()
        if subtask is None or row < 0:
            return
        points = list(subtask.waypoints)
        points.pop(row)
        updated = replace(subtask, waypoints=tuple(points))
        self.drafts[subtask.subtask_id] = updated
        self._render_waypoints(updated)
        self._changed()

    def _move(self, direction: int) -> None:
        subtask = self._current()
        row = self.waypoints.currentRow()
        target = row + direction
        if subtask is None or row < 0 or not 0 <= target < len(subtask.waypoints):
            return
        points = list(subtask.waypoints)
        points[row], points[target] = points[target], points[row]
        updated = replace(subtask, waypoints=tuple(points))
        self.drafts[subtask.subtask_id] = updated
        self._render_waypoints(updated)
        self.waypoints.selectRow(target)
        self._changed()

    def _changed(self) -> None:
        self._render_paths()
        self._recalculate_conflicts()
        self._update_execution_controls()

    def _render_paths(self) -> None:
        self.viewer.set_task_paths({
            item.device_id: [(point.x, point.y, point.z) for point in item.waypoints]
            for item in self.drafts.values() if item.waypoints
        })

    def _settings(self) -> TaskSafetySettings:
        return TaskSafetySettings(
            self.horizontal_clearance.value(), self.vertical_clearance.value(), self.time_margin.value()
        )

    def _recalculate_conflicts(self) -> None:
        self.conflicts = self.detector.detect(tuple(self.drafts.values()), self._settings())
        self.conflict_list.clear()
        for item in self.conflicts:
            self.conflict_list.addItem(
                f"{item.first_device_id} / {item.second_device_id}  ·  t={item.time_seconds:.1f}s  ·  "
                f"水平 {item.horizontal_distance_m:.2f}m / 垂直 {item.vertical_distance_m:.2f}m"
            )
        if not self.conflicts:
            self.conflict_list.addItem("未检测到时空冲突")
        self.viewer.set_task_conflicts([(item.x, item.y, item.z) for item in self.conflicts])

    def _save_current(self) -> bool:
        if not self.task:
            return False
        subtask = self._current()
        if subtask is None:
            return False
        subtask = replace(
            subtask, default_altitude_m=self.default_z.value(), cruise_speed_mps=self.speed.value(),
            start_delay_seconds=self.delay.value(), layer_mode=self._effective_layer_mode(),
        )
        if len(subtask.waypoints) < 2:
            QMessageBox.warning(self, "无法保存", "子任务至少需要两个任务点")
            return False
        try:
            self.task = self.repository.update_subtask(self.task.task_id, subtask)
            self.repository.update_safety(self.task.task_id, self._settings())
            self.task = self.repository.task_by_id(self.task.task_id)
            saved = next(item for item in self.task.subtasks if item.subtask_id == subtask.subtask_id)
            self.drafts[subtask.subtask_id] = saved
            if self.selected_row is not None:
                self._open_subtask(self.selected_row)
            self._load_logs()
            return True
        except TaskRepositoryError as exc:
            QMessageBox.critical(self, "任务保存失败", str(exc))
            return False

    def _deliver_current(self) -> None:
        if not self._can_execute() or not self._save_current():
            return
        subtask = self._current()
        try:
            self.execution_service.deliver_subtask(self.task, subtask)
        except Exception as exc:
            QMessageBox.critical(self, "任务下发失败", str(exc))

    def _deliver_all(self) -> None:
        if not self._can_execute() or not self.task:
            return
        self._store_current_controls()
        try:
            pending = tuple(self.drafts[item.subtask_id] for item in self.task.subtasks)
            for item in pending:
                self.repository._validate_subtask(item)
                device = self.source.device(item.device_id)
                if device is None or device.connection_status != ConnectionStatus.ONLINE:
                    raise ValueError(f"设备 {item.device_id} 不在线")
                if self.execution_service.device_active(item.device_id):
                    raise ValueError(f"设备 {item.device_id} 已有执行会话")
            self.task = self.repository.save_subtasks(self.task.task_id, pending)
            self.repository.update_safety(self.task.task_id, self._settings())
            self.drafts = {item.subtask_id: item for item in self.task.subtasks}
            self.execution_service.deliver_task(self.task)
            self._update_execution_controls()
        except Exception as exc:
            QMessageBox.critical(self, "同步下发失败", str(exc))

    def _execute_current(self) -> None:
        if not self._can_execute(require_active_map=True):
            return
        subtask = self._current()
        self._store_current_controls()
        subtask = self._current()
        if subtask is None or not self._draft_ready(subtask):
            detail = subtask.edge_message if subtask is not None else ""
            message = detail or "请先点击“保存下发”，并等待端侧导航准备完成"
            QMessageBox.warning(self, "任务尚未就绪", message)
            return
        try:
            snapshot = self.execution_service.execute_subtask(self.task, subtask.device_id)
            self.active_execution_id = snapshot.execution_id
            self.active_execution_status = snapshot.status.value
            self._update_execution_controls()
        except Exception as exc:
            QMessageBox.critical(self, "任务执行失败", str(exc))

    def _execute_all(self) -> None:
        if not self._can_execute(require_active_map=True) or not self.task:
            return
        current = self._current()
        if current is not None:
            self.drafts[current.subtask_id] = replace(
                current, default_altitude_m=self.default_z.value(),
                cruise_speed_mps=self.speed.value(), start_delay_seconds=self.delay.value(),
                layer_mode=self._effective_layer_mode(),
            )
        pending = tuple(self.drafts[item.subtask_id] for item in self.task.subtasks)
        if any(not item.is_valid for item in pending):
            QMessageBox.warning(self, "无法共同执行", "每台设备的子任务都必须包含 2 到 500 个任务点")
            return
        if any(not self._draft_ready(item) for item in pending):
            QMessageBox.warning(self, "无法开始主任务", "所有设备子任务必须完成下发且端侧导航已就绪")
            return
        self.task = self.repository.task_by_id(self.task.task_id)
        forced_reason = None
        if self.conflicts:
            reason, accepted = QInputDialog.getText(
                self, "存在未解决冲突", "请输入强制执行原因（留空将取消）："
            )
            if not accepted or not reason.strip():
                return
            forced_reason = reason.strip()
            self.repository.append_audit(
                self.task.task_id, "conflict_override", "用户确认强制执行冲突任务",
                level=TaskEventLevel.WARNING,
                payload={"reason": forced_reason, "conflict_count": len(self.conflicts)},
            )
        try:
            snapshot = self.execution_service.execute_devices(
                self.task, tuple(item.device_id for item in self.task.subtasks),
                forced_conflict_reason=forced_reason,
            )
            self.active_execution_id = snapshot.execution_id
            self.active_execution_status = snapshot.status.value
            self._update_execution_controls()
        except Exception as exc:
            QMessageBox.critical(self, "共同执行失败", str(exc))

    def _can_execute(self, require_active_map: bool = False) -> bool:
        if not self.execution_service:
            QMessageBox.warning(self, "任务服务不可用", "UDP 任务服务未配置")
            return False
        if not self.map_reviewed:
            QMessageBox.warning(self, "地图需要复核", "地图图层已更新，请重新打开任务并确认复核。")
            return False
        if require_active_map and self.task and self.map_repository.active_map_id() != self.task.map_id:
            QMessageBox.warning(self, "地图不一致", "任务绑定地图不是当前全局激活地图，无法开始执行。")
            return False
        return True

    def _stop_execution(self) -> None:
        if self.execution_service and self.active_execution_id:
            self.execution_service.stop_execution(self.active_execution_id)

    def _toggle_current_execution(self) -> None:
        if self.active_execution_id:
            self._stop_execution()
        else:
            self._execute_current()

    def _emergency_stop(self) -> None:
        if self.execution_service and self.task:
            self.execution_service.emergency_stop(self.task)
            self.repository.append_audit(
                self.task.task_id, "emergency_stop", "已向全部任务设备发送急停",
                level=TaskEventLevel.WARNING,
            )

    def _load_logs(self) -> None:
        self.log_view.clear()
        if not self.task:
            return
        events = self.repository.recent_events(self.task.task_id)
        if self._log_cleared_before is not None:
            events = [event for event in events if event.timestamp > self._log_cleared_before]
        lines = []
        for event in events[-500:]:
            stamp = event.timestamp.astimezone().strftime("%H:%M:%S.%f")[:-3]
            device = event.device_id or "-"
            execution = event.execution_id[:8] if event.execution_id else "-"
            lines.append(
                f"[{stamp}] {event.level.value.upper():<7} {device:<16} "
                f"{event.event_type:<22} {execution:<8} {event.message}"
            )
        self.log_view.setPlainText("\n".join(lines))
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        QTimer.singleShot(0, lambda: scrollbar.setValue(scrollbar.maximum()))

    def _append_event(self, event) -> None:
        if not self.task or event.task_id != self.task.task_id:
            return
        stamp = event.timestamp.astimezone().strftime("%H:%M:%S.%f")[:-3]
        self.log_view.appendPlainText(
            f"[{stamp}] {event.level.value.upper():<7} {event.device_id or '-':<16} "
            f"{event.event_type:<22} {event.execution_id[:8] if event.execution_id else '-':<8} {event.message}")
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _events_updated(self, task_id: str) -> None:
        if self.task and task_id == self.task.task_id:
            self._load_logs()

    def _clear_log_view(self) -> None:
        self._log_cleared_before = utc_now()
        self.log_view.clear()

    def _transfer_updated(self, task_id, device_id, state) -> None:
        if self.task and task_id == self.task.task_id:
            self._transfer_states[device_id] = state
            self._repository_tasks_updated(self.repository.tasks())

    def _execution_updated(self, snapshot) -> None:
        if self.task and snapshot.task_id == self.task.task_id:
            active = snapshot.status.value in {"preparing", "scheduled", "running", "stopping"}
            self.active_execution_id = snapshot.execution_id if active else None
            self.active_execution_status = snapshot.status.value if active else None
            self._update_execution_controls()

    def _telemetry_updated(self, device_id, telemetry) -> None:
        self._telemetry_dirty = True

    def _flush_realtime(self) -> None:
        if not self.isVisible() or not self.task:
            return
        self._update_execution_controls()
        if not self._telemetry_dirty or self.telemetry_store is None:
            return
        self._telemetry_dirty = False
        markers = []
        for subtask in self.task.subtasks:
            snapshot = self.telemetry_store.telemetry(subtask.device_id)
            pose = bound_map_pose(
                self.source, snapshot, subtask.device_id, self.task.map_id
            )
            if pose is not None and (pose.sample_age_seconds is None or pose.sample_age_seconds <= 2.0):
                device = self.source.device(subtask.device_id)
                markers.append(DeviceMapMarker(
                    subtask.device_id, subtask.device_name, pose.x, pose.y, pose.z,
                    snapshot.udp_link_status.value,
                    device.map_marker_shape if device else MapMarkerShape.SPHERE,
                    pose.yaw,
                ))
        self.viewer.set_execution_markers(markers)
        if self.trajectory_store is not None:
            self.viewer.set_device_trails(self.trajectory_store.trails(self.task.map_id, [item.device_id for item in self.task.subtasks]))


class TaskPage(QWidget):
    def __init__(
        self, source: DeviceDataSource, map_repository: MapRepository, task_repository: TaskRepository,
        execution_service=None, telemetry_store=None, viewer_factory: Callable[[], PointCloudViewer] | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.map_repository = map_repository
        self.repository = task_repository
        self.execution_service = execution_service
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.tasks = task_repository.tasks()
        self.cards: list[TaskCard] = []
        self._grid_columns = 0
        self._render_pending = False
        self._render_generation = 0
        self._restore_scroll_value: int | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.list_page = QWidget()
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 22, 28, 24)
        header = QHBoxLayout()
        title = QLabel("任务系统")
        title.setObjectName("pageTitle")
        self.search = QLineEdit()
        self.search.setObjectName("compactListSearch")
        self.search.setPlaceholderText("搜索任务、地图或设备")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_changed)
        new_button = QPushButton("新建任务")
        new_button.setObjectName("primaryButton")
        new_button.clicked.connect(self._create)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(new_button)
        layout.addLayout(header)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        self.status_filter.setObjectName("compactListFilter")
        self.status_filter.setAccessibleName("任务状态筛选")
        for label, value in (("全部状态", "all"), ("草稿", "draft"), ("配置就绪", "ready"), ("加载异常", "error")):
            self.status_filter.addItem(label, value)
        self.status_filter.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self.status_filter)
        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("compactListSort")
        self.sort_combo.setAccessibleName("任务排序")
        for label, value in (("最近更新", "updated_desc"), ("最早更新", "updated_asc"), ("名称排序", "name")):
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self._filter_changed)
        filters.addWidget(self.sort_combo)
        self.compact_button = QToolButton()
        self.compact_button.setObjectName("compactToolButton")
        self.compact_button.setFixedSize(32, 32)
        self.compact_button.setCheckable(True)
        self.compact_button.setChecked(QSettings("CCS", "CCS").value("tasks/compact", False, type=bool))
        self.compact_button.toggled.connect(self._compact_toggled)
        self._update_view_button()
        filters.addWidget(self.compact_button)
        self.count_label = QLabel()
        self.count_label.setObjectName("compactFieldLabel")
        self.count_label.setMinimumWidth(92)
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        filters.addWidget(self.count_label)
        layout.addLayout(filters)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("taskListScroll")
        self.scroll.viewport().setObjectName("taskListViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName("taskListContainer")
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)
        self.stack.addWidget(self.list_page)
        self.editor = TaskEditorPage(
            task_repository, map_repository, source, execution_service, telemetry_store, viewer_factory
        )
        self.editor.back_requested.connect(self.show_list)
        self.stack.addWidget(self.editor)
        self.repository.tasks_updated.connect(self._tasks_updated)
        self.map_repository.active_map_changed.connect(self._active_map_changed)
        self.map_repository.maps_updated.connect(self._render)
        if execution_service is not None:
            execution_service.availability_changed.connect(self.set_execution_available)
        self.scroll.viewport().installEventFilter(self)
        self._render()

    def set_execution_available(self, available: bool, message: str = "") -> None:
        self.editor.set_execution_available(available, message)

    def _active_map_changed(self, _definition: object) -> None:
        self._render()
        self.editor.refresh_active_map_state()

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.editor.set_theme(palette)
        self._update_view_button()
        self._render()
        self.update()

    def set_active(self, active: bool) -> None:
        method = getattr(self.editor.viewer, "resume_static" if active else "suspend_static", None)
        if method and (not active or self.editor.viewer.isVisible()):
            method()
        if not active:
            self.editor.viewer.set_interaction_mode("browse")

    def _render(self) -> None:
        self._render_pending = False
        self._render_generation += 1
        generation = self._render_generation
        scrollbar = self.scroll.verticalScrollBar()
        scroll_value = scrollbar.value() if self._restore_scroll_value is None else self._restore_scroll_value
        self._restore_scroll_value = scroll_value
        self.container.setUpdatesEnabled(False)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = []
        query = self.search.text().strip().casefold()
        state = self.status_filter.currentData()
        filtered = [
            item for item in self.tasks
            if (state == "all" or TaskCard.status_key(item) == state)
            and (not query or query in " ".join((
                item.name, item.map_name,
                *(value for subtask in item.subtasks for value in (subtask.device_name, subtask.device_id)),
            )).casefold())
        ]
        sort = self.sort_combo.currentData()
        if sort == "name":
            filtered.sort(key=lambda item: (item.name.casefold(), item.task_id))
        else:
            filtered.sort(key=lambda item: (item.updated_at, item.task_id), reverse=sort != "updated_asc")
        columns = self._column_count()
        self._grid_columns = columns
        for column in range(4):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
            self.grid.setColumnMinimumWidth(column, 0)
        self.count_label.setText(f"显示 {len(filtered)} / {len(self.tasks)} 项")
        for index, task in enumerate(filtered):
            card = TaskCard(
                task, task.map_id == self.map_repository.active_map_id(),
                map_exists=task.status == TaskDefinitionStatus.ERROR or self.map_repository.map_by_id(task.map_id) is not None,
            )
            card.open_requested.connect(self.show_task)
            card.delete_requested.connect(self._delete)
            self.cards.append(card)
            self.grid.addWidget(card, index // columns, index % columns)
        if not filtered:
            empty = QLabel("尚未创建任务" if not self.tasks else "没有匹配的任务")
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(empty, 0, 0, 1, columns)
        self.container.setUpdatesEnabled(True)
        self.grid.activate()

        def restore_scroll() -> None:
            if generation == self._render_generation:
                scrollbar.setValue(scroll_value)
                self._restore_scroll_value = None

        QTimer.singleShot(0, restore_scroll)

    def _column_count(self) -> int:
        if self.compact_button.isChecked():
            return 1
        return max(1, min(4, (self.scroll.viewport().width() + 10) // 330))

    def _filter_changed(self) -> None:
        self._restore_scroll_value = 0
        self._render()

    def _update_view_button(self) -> None:
        single_column = self.compact_button.isChecked()
        self.compact_button.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_FileDialogDetailedView if single_column else QStyle.StandardPixmap.SP_FileDialogListView
        ))
        self.compact_button.setToolTip("切换为卡片网格" if single_column else "切换为单列列表")
        self.compact_button.setAccessibleName("单列列表视图")

    def _compact_toggled(self, enabled: bool) -> None:
        QSettings("CCS", "CCS").setValue("tasks/compact", bool(enabled))
        self._update_view_button()
        self._render()

    def _create(self) -> None:
        dialog = NewTaskDialog(
            self.map_repository.maps(), self.source.snapshots(), self.map_repository.active_map_id(), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        map_definition = self.map_repository.map_by_id(str(dialog.map_combo.currentData()))
        selected = set(dialog.selected_device_ids())
        devices = [item for item in self.source.snapshots() if item.device_id in selected]
        try:
            task = self.repository.create(dialog.name_input.text(), map_definition, devices)
            self.show_task(task.task_id)
        except TaskRepositoryError as exc:
            QMessageBox.critical(self, "任务创建失败", str(exc))

    def show_task(self, task_id: str) -> None:
        task = self.repository.task_by_id(task_id)
        if task is None:
            return
        if task.status == TaskDefinitionStatus.ERROR:
            QMessageBox.warning(self, "任务加载异常", task.error_message or "任务文件无法读取")
            return
        current_map = self.map_repository.map_by_id(task.map_id)
        reviewed = True
        if current_map and map_fingerprint(current_map) != task.map_fingerprint:
            reviewed = QMessageBox.question(
                self, "地图已更新",
                "地图图层在任务创建后发生变化。请复核全部任务点；确认已复核后才允许下发和执行。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes
        self.editor.set_task(task)
        self.editor.set_map_reviewed(reviewed)
        self.stack.setCurrentWidget(self.editor)

    def show_list(self) -> None:
        self.editor.viewer.set_interaction_mode("browse")
        self.stack.setCurrentWidget(self.list_page)
        self._render()

    def _delete(self, task_id: str) -> None:
        answer = QMessageBox.question(
            self, "删除任务", "任务和全部执行日志将移入 task_server/.trash，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.repository.delete(task_id)
            except TaskRepositoryError as exc:
                QMessageBox.critical(self, "任务删除失败", str(exc))

    def _tasks_updated(self, tasks) -> None:
        self.tasks = self.repository.tasks()
        if self.isVisible() and self.stack.currentWidget() == self.list_page:
            self._queue_render()

    def showEvent(self, event):
        super().showEvent(event)
        if self.stack.currentWidget() == self.list_page:
            self._queue_render()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.stack.currentWidget() == self.list_page and self._column_count() != self._grid_columns:
            self._queue_render()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            if self._column_count() != self._grid_columns:
                self._queue_render()
        return super().eventFilter(watched, event)

    def _queue_render(self) -> None:
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(0, self._render)
