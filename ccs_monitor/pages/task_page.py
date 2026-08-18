from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..data_source import DeviceDataSource
from ..map_repository import MapRepository, MapRepositoryError
from ..models import DeviceMapMarker, MapDefinition, MapMarkerShape, MapStatus
from ..task_conflicts import TaskConflictDetector
from ..task_map import GridPointValidator
from ..task_models import (
    DeviceSubtask, TaskDefinition, TaskDefinitionStatus, TaskEventLevel,
    TaskSafetySettings, TaskWaypoint,
)
from ..task_repository import TaskRepository, TaskRepositoryError, map_fingerprint
from ..widgets import NoButtonDoubleSpinBox
from .map_page import PointCloudViewer


class NewTaskDialog(QDialog):
    def __init__(self, maps: list[MapDefinition], devices: list, parent=None) -> None:
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
                self.map_combo.addItem(definition.name, definition.map_id)
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
        for device in devices:
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

    def __init__(self, task: TaskDefinition, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("deviceCard")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel(task.name)
        title.setObjectName("cardTitle")
        status = QLabel("可执行" if task.is_ready else "草稿")
        status.setObjectName("statusPill")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(status)
        layout.addLayout(header)
        layout.addWidget(QLabel(f"地图 {task.map_name}  ·  设备 {len(task.subtasks)} 台"))
        layout.addWidget(QLabel(
            f"有效子任务 {sum(item.is_valid for item in task.subtasks)} / {len(task.subtasks)}  ·  "
            f"更新 {task.updated_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
        ))
        actions = QHBoxLayout()
        actions.addStretch()
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        open_button = QPushButton("打开")
        open_button.setObjectName("primaryButton")
        delete.clicked.connect(lambda: self.delete_requested.emit(task.task_id))
        open_button.clicked.connect(lambda: self.open_requested.emit(task.task_id))
        actions.addWidget(delete)
        actions.addWidget(open_button)
        layout.addLayout(actions)


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
        self.viewer = viewer_factory() if viewer_factory else PointCloudViewer()
        self.detector = TaskConflictDetector()
        self._build()
        self.viewer.map_point_picked.connect(self._add_picked_point)
        if telemetry_store is not None:
            telemetry_store.telemetry_updated.connect(self._telemetry_updated)
        if execution_service is not None:
            execution_service.transfer_updated.connect(self._transfer_updated)
            execution_service.execution_updated.connect(self._execution_updated)
        self.set_execution_available(
            bool(execution_service and getattr(execution_service, "available", False)),
            getattr(execution_service, "module_message", "UDP 任务服务未配置"),
        )

    def _build(self) -> None:
        self.setObjectName("taskEditorPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 18)
        header = QHBoxLayout()
        back = QPushButton("返回任务列表")
        back.clicked.connect(self.back_requested)
        self.title = QLabel("任务编辑")
        self.title.setObjectName("pageTitle")
        self.map_toggle = QPushButton("收起地图")
        self.map_toggle.clicked.connect(self._toggle_map)
        self.run_all = QPushButton("共同执行")
        self.run_all.setObjectName("primaryButton")
        self.run_all.clicked.connect(self._execute_all)
        self.stop_button = QPushButton("终止执行")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_execution)
        header.addWidget(back)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.map_toggle)
        header.addWidget(self.run_all)
        header.addWidget(self.stop_button)
        root.addLayout(header)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("taskEditorMainSplitter")
        self.device_list = QListWidget()
        self.device_list.setObjectName("taskDeviceList")
        self.device_list.setMinimumWidth(190)
        self.device_list.currentRowChanged.connect(self._device_changed)
        self.main_splitter.addWidget(self.device_list)

        self.map_panel = QFrame()
        self.map_panel.setObjectName("taskEditorPanel")
        map_layout = QVBoxLayout(self.map_panel)
        map_toolbar = QHBoxLayout()
        self.layer_combo = QComboBox()
        self.layer_combo.addItem("点云选点", "pointcloud")
        self.layer_combo.addItem("栅格选点", "grid")
        self.pick_toggle = QPushButton("开始选点")
        self.pick_toggle.setCheckable(True)
        self.pick_toggle.toggled.connect(self._pick_toggled)
        map_toolbar.addWidget(self.layer_combo)
        map_toolbar.addWidget(self.pick_toggle)
        map_toolbar.addStretch()
        map_layout.addLayout(map_toolbar)
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
        self.deliver_button = QPushButton("下发当前设备")
        self.deliver_button.clicked.connect(self._deliver_current)
        self.run_one = QPushButton("执行当前设备")
        self.run_one.clicked.connect(self._execute_current)
        save = QPushButton("保存子任务")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_current)
        save_row.addWidget(self.deliver_button)
        save_row.addWidget(self.run_one)
        save_row.addWidget(save)
        right_layout.addLayout(save_row)
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
        log_layout.addWidget(QLabel("任务日志与交互数据"))
        self.log_list = QListWidget()
        self.log_list.setObjectName("taskAuditList")
        log_layout.addWidget(self.log_list)
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

    def set_task(self, task: TaskDefinition) -> None:
        self.task = task
        self.drafts = {item.subtask_id: item for item in task.subtasks}
        self.title.setText(f"{task.name}  ·  {task.map_name}")
        self.horizontal_clearance.setValue(task.safety.horizontal_distance_m)
        self.vertical_clearance.setValue(task.safety.vertical_distance_m)
        self.time_margin.setValue(task.safety.time_margin_seconds)
        self.device_list.clear()
        for item in task.subtasks:
            self.device_list.addItem(f"{item.device_name}\n{len(item.waypoints)} 点 · r{item.revision}")
        self._load_map(task)
        self._load_logs()
        if self.device_list.count():
            self.device_list.setCurrentRow(0)
        self._recalculate_conflicts()

    def set_map_reviewed(self, reviewed: bool) -> None:
        self.map_reviewed = bool(reviewed)
        self._update_execution_controls()

    def set_execution_available(self, available: bool, message: str = "") -> None:
        self.execution_service_available = bool(available)
        tooltip = message or "UDP 任务服务不可用"
        for button in (self.deliver_button, self.run_one, self.run_all):
            button.setEnabled(bool(available) and self.map_reviewed)
            button.setToolTip("" if available else tooltip)

    def _update_execution_controls(self) -> None:
        available = bool(self.execution_service and getattr(self.execution_service, "available", False))
        enabled = available and self.map_reviewed
        for button in (self.deliver_button, self.run_one, self.run_all):
            button.setEnabled(enabled)
        if not self.map_reviewed:
            for button in (self.deliver_button, self.run_one, self.run_all):
                button.setToolTip("地图图层已变化，需要人工复核后才能下发或执行")

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

    def _device_changed(self, row: int) -> None:
        if not self.task or not 0 <= row < len(self.task.subtasks):
            return
        previous = self._current()
        if previous is not None:
            self.drafts[previous.subtask_id] = replace(
                previous,
                default_altitude_m=self.default_z.value(),
                cruise_speed_mps=self.speed.value(),
                start_delay_seconds=self.delay.value(),
                layer_mode=str(self.layer_combo.currentData()),
            )
        subtask = self.drafts[self.task.subtasks[row].subtask_id]
        self.current_subtask_id = subtask.subtask_id
        self.default_z.setValue(subtask.default_altitude_m)
        self.speed.setValue(subtask.cruise_speed_mps)
        self.delay.setValue(subtask.start_delay_seconds)
        index = self.layer_combo.findData(subtask.layer_mode)
        self.layer_combo.setCurrentIndex(max(0, index))
        self._render_waypoints(subtask)
        self._render_paths()

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

    def _add_picked_point(self, x: float, y: float) -> None:
        subtask = self._current()
        if subtask is None:
            return
        mode = str(self.layer_combo.currentData())
        if mode == "grid" and (self.grid_validator is None or not self.grid_validator.is_free(x, y)):
            QMessageBox.warning(self, "无法创建任务点", "该位置不是占据栅格中的空闲区域")
            return
        self._append_waypoint(x, y, self.default_z.value(), mode)

    def _add_manual(self) -> None:
        self._append_waypoint(0.0, 0.0, self.default_z.value(), str(self.layer_combo.currentData()))

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
            start_delay_seconds=self.delay.value(), layer_mode=str(self.layer_combo.currentData()),
        )
        if len(subtask.waypoints) < 2:
            QMessageBox.warning(self, "无法保存", "子任务至少需要两个任务点")
            return False
        try:
            self.task = self.repository.update_subtask(self.task.task_id, subtask)
            self.repository.update_safety(self.task.task_id, self._settings())
            self.task = self.repository.task_by_id(self.task.task_id)
            self.drafts = {item.subtask_id: item for item in self.task.subtasks}
            self._device_changed(self.device_list.currentRow())
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

    def _execute_current(self) -> None:
        if not self._can_execute() or not self._save_current():
            return
        try:
            snapshot = self.execution_service.execute_subtask(self.task, self._current().device_id)
            self.active_execution_id = snapshot.execution_id
        except Exception as exc:
            QMessageBox.critical(self, "任务执行失败", str(exc))

    def _execute_all(self) -> None:
        if not self._can_execute() or not self.task:
            return
        current = self._current()
        if current is not None:
            self.drafts[current.subtask_id] = replace(
                current, default_altitude_m=self.default_z.value(),
                cruise_speed_mps=self.speed.value(), start_delay_seconds=self.delay.value(),
                layer_mode=str(self.layer_combo.currentData()),
            )
        pending = tuple(self.drafts[item.subtask_id] for item in self.task.subtasks)
        if any(not item.is_valid for item in pending):
            QMessageBox.warning(self, "无法共同执行", "每台设备的子任务都必须包含 2 到 500 个任务点")
            return
        try:
            task_id = self.task.task_id
            for subtask in pending:
                self.repository.update_subtask(task_id, subtask)
            self.repository.update_safety(task_id, self._settings())
            self.task = self.repository.task_by_id(task_id)
            self.drafts = {item.subtask_id: item for item in self.task.subtasks}
            self.set_task(self.task)
        except TaskRepositoryError as exc:
            QMessageBox.critical(self, "任务保存失败", str(exc))
            return
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
        except Exception as exc:
            QMessageBox.critical(self, "共同执行失败", str(exc))

    def _can_execute(self) -> bool:
        if not self.execution_service:
            QMessageBox.warning(self, "任务服务不可用", "UDP 任务服务未配置")
            return False
        if not self.map_reviewed:
            QMessageBox.warning(self, "地图需要复核", "地图图层已更新，请重新打开任务并确认复核。")
            return False
        return True

    def _stop_execution(self) -> None:
        if self.execution_service and self.active_execution_id:
            self.execution_service.stop_execution(self.active_execution_id)

    def _toggle_map(self) -> None:
        self.map_collapsed = not self.map_collapsed
        self.map_panel.setVisible(not self.map_collapsed)
        self.map_toggle.setText("展开地图" if self.map_collapsed else "收起地图")

    def _load_logs(self) -> None:
        self.log_list.clear()
        if not self.task:
            return
        for event in self.repository.audit_events(self.task.task_id)[-500:]:
            self.log_list.addItem(f"{event.timestamp.astimezone().strftime('%H:%M:%S')} [{event.level.value}] {event.message}")

    def _transfer_updated(self, task_id, device_id, state) -> None:
        if self.task and task_id == self.task.task_id:
            self.log_list.addItem(f"{device_id} 下发状态：{state}")

    def _execution_updated(self, snapshot) -> None:
        if self.task and snapshot.task_id == self.task.task_id:
            active = snapshot.status.value in {"preparing", "scheduled", "running"}
            self.active_execution_id = snapshot.execution_id if active else None
            self.stop_button.setEnabled(active)
            self.log_list.addItem(f"执行 {snapshot.execution_id[:8]}：{snapshot.message}")

    def _telemetry_updated(self, device_id, telemetry) -> None:
        if not self.task or device_id not in {item.device_id for item in self.task.subtasks}:
            return
        markers = []
        for subtask in self.task.subtasks:
            snapshot = self.telemetry_store.telemetry(subtask.device_id)
            pose = snapshot.global_pose
            if pose is not None and (pose.sample_age_seconds is None or pose.sample_age_seconds <= 2.0):
                device = self.source.device(subtask.device_id)
                markers.append(DeviceMapMarker(
                    subtask.device_id, subtask.device_name, pose.x, pose.y, pose.z,
                    snapshot.udp_link_status.value,
                    device.map_marker_shape if device else MapMarkerShape.SPHERE,
                    pose.yaw,
                ))
        self.viewer.set_execution_markers(markers)


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
        self.tasks = task_repository.tasks()
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
        self.search.setPlaceholderText("按任务名称搜索")
        self.search.textChanged.connect(self._render)
        new_button = QPushButton("新建任务")
        new_button.setObjectName("primaryButton")
        new_button.clicked.connect(self._create)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search)
        header.addWidget(new_button)
        layout.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("taskListScroll")
        self.scroll.viewport().setObjectName("taskListViewport")
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container.setObjectName("taskListContainer")
        self.grid = QGridLayout(self.container)
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
        if execution_service is not None:
            execution_service.availability_changed.connect(self.set_execution_available)
        self._render()

    def set_execution_available(self, available: bool, message: str = "") -> None:
        self.editor.set_execution_available(available, message)

    def set_active(self, active: bool) -> None:
        if not active:
            self.editor.viewer.set_interaction_mode("browse")

    def _render(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        query = self.search.text().strip().casefold()
        filtered = [item for item in self.tasks if not query or query in item.name.casefold()]
        columns = 3 if self.width() >= 1180 else 2 if self.width() >= 760 else 1
        for index, task in enumerate(filtered):
            card = TaskCard(task)
            card.open_requested.connect(self.show_task)
            card.delete_requested.connect(self._delete)
            self.grid.addWidget(card, index // columns, index % columns)
        if not filtered:
            empty = QLabel("尚未创建任务" if not self.tasks else "没有匹配的任务")
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(empty, 0, 0, 1, columns)

    def _create(self) -> None:
        dialog = NewTaskDialog(self.map_repository.maps(), self.source.snapshots(), self)
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
        if task is None or task.status == TaskDefinitionStatus.ERROR:
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
        self.tasks = list(tasks)
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.stack.currentWidget() == self.list_page:
            self._render()
