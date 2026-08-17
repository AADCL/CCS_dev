from __future__ import annotations

from pathlib import Path
from typing import Callable
import math
import json
import threading
import uuid
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QEvent, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..data_source import DeviceDataSource
from ..map_repository import MapRepository, MapRepositoryError
from ..map_building import MapBuildingSessionSnapshot
from ..map_fusion import MapFusionError, MapFusionRepository, MapFusionRunner
from ..models import (
    DeviceMapMarker,
    DeviceSnapshot,
    MapCreatorDevice,
    MapBuildMode,
    MapBuildProvenance,
    MapDefinition,
    MapFusionAlgorithm,
    MapFusionJob,
    MapMarkerShape,
    MapStatus,
    MapTransform,
    PoseTelemetry,
    UdpLinkStatus,
)
from ..pgm_map import PgmMapError, PgmMapLoader
from ..point_cloud import MapPointCloudLoader, PointCloudError
from ..styles import ThemeMode, ThemePalette, theme_palette


STATUS_TEXT = {
    MapStatus.WAITING_FOR_PCD: "等待导入地图",
    MapStatus.READY: "地图已就绪",
    MapStatus.ERROR: "地图数据异常",
}

MAP_PAN_DRAG_SPEED = 3.0


def calculate_turntable_pan(
    camera,
    press_position,
    current_position,
    view_size,
    start_center,
    speed_multiplier: float = MAP_PAN_DRAG_SPEED,
) -> tuple[float, float, float]:
    """Translate a TurntableCamera center in its current observation plane."""
    press = np.asarray(press_position, dtype=np.float64)[:2]
    current = np.asarray(current_position, dtype=np.float64)[:2]
    size = np.asarray(view_size, dtype=np.float64)[:2]
    normalization = max(float(np.mean(size)), 1.0)
    distance = (
        (press - current)
        / normalization
        * float(camera.scale_factor)
        * float(speed_multiplier)
    )
    distance[1] *= -1
    dx, dy, dz = camera._dist_to_trans(distance)
    up, forward, right = camera._get_dim_vectors()
    translated = right * dx + forward * dy + up * dz
    flip = camera._flip_factors
    translated = np.asarray(
        (translated[0] * flip[0], translated[1] * flip[1], translated[2] * flip[2]),
        dtype=np.float64,
    )
    center = np.asarray(start_center, dtype=np.float64)
    result = center + translated
    return float(result[0]), float(result[1]), float(result[2])


class MiddlePanTurntableCameraMixin:
    """Replace VisPy button-2 zoom with responsive planar map panning."""

    pan_speed_multiplier = MAP_PAN_DRAG_SPEED

    def viewbox_mouse_event(self, event) -> None:
        if (
            not event.handled
            and self.interactive
            and event.type == "mouse_move"
            and event.press_event is not None
            and 2 in event.buttons
            and not event.mouse_event.modifiers
        ):
            if self._event_value is None or np.asarray(self._event_value).size != 3:
                self._event_value = tuple(self.center)
            self.center = calculate_turntable_pan(
                self,
                event.mouse_event.press_event.pos,
                event.mouse_event.pos,
                self._viewbox.size,
                self._event_value,
                self.pan_speed_multiplier,
            )
            self.view_changed()
            event.handled = True
            return
        super().viewbox_mouse_event(event)


class MapCard(QFrame):
    double_clicked = Signal(str)
    selection_changed = Signal(str, bool)

    def __init__(self, definition: MapDefinition) -> None:
        super().__init__()
        self.definition = definition
        self.setObjectName("mapCard")
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(9)

        header = QHBoxLayout()
        self.checkbox = QCheckBox()
        self.checkbox.setVisible(False)
        self.checkbox.toggled.connect(
            lambda checked: self.selection_changed.emit(self.definition.map_id, checked)
        )
        header.addWidget(self.checkbox)
        name = QLabel(definition.name)
        name.setObjectName("mapName")
        name.setWordWrap(True)
        header.addWidget(name, 1)
        status = QLabel(STATUS_TEXT[definition.status])
        status.setObjectName("mapStatus")
        status.setProperty("state", definition.status.value)
        header.addWidget(status, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        created = definition.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        root.addWidget(self._line("建图时间", created))
        creator_names = "、".join(item.device_name for item in definition.creator_devices) or "元数据不可用"
        root.addWidget(self._line("建图设备", creator_names))
        if definition.bounds:
            size = f"{definition.bounds.width:.1f} × {definition.bounds.height:.1f} × {definition.bounds.depth:.1f} m"
            layers = "PCD + PGM" if definition.pgm else "PCD"
            detail = f"{layers}  ·  范围 {size}  ·  {definition.point_count:,} 点"
        elif definition.pgm:
            detail = (
                f"PGM  ·  范围 {definition.pgm.width_m:.1f} × "
                f"{definition.pgm.height_m:.1f} m"
            )
        elif definition.error_message:
            detail = definition.error_message
        else:
            detail = "尚未导入 map.pcd"
        note = QLabel(detail)
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch()

    @staticmethod
    def _line(label: str, value: str) -> QLabel:
        widget = QLabel(f"{label}  {value}")
        widget.setObjectName("fieldValue")
        widget.setWordWrap(True)
        return widget

    def set_edit_mode(self, enabled: bool, checked: bool = False) -> None:
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(checked)
        self.checkbox.setVisible(enabled)
        self.checkbox.blockSignals(False)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.checkbox.isVisible():
            self.double_clicked.emit(self.definition.map_id)
        super().mouseDoubleClickEvent(event)


class NewMapDialog(QDialog):
    def __init__(self, devices: list[DeviceSnapshot], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建地图")
        self.setMinimumSize(520, 430)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        title = QLabel("创建地图档案")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        root.addWidget(QLabel("地图名称"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：A 区厂房地图")
        root.addWidget(self.name_input)
        root.addWidget(QLabel("选择建图设备（可多选，离线设备仍可选择）"))

        self.device_scroll = QScrollArea()
        self.device_scroll.setObjectName("mapDeviceScroll")
        self.device_scroll.setWidgetResizable(True)
        self.device_scroll.viewport().setObjectName("mapDeviceViewport")
        container = QWidget()
        container.setObjectName("mapDeviceSelector")
        device_layout = QVBoxLayout(container)
        device_layout.setContentsMargins(8, 8, 8, 8)
        self.device_checks: dict[str, QCheckBox] = {}
        for device in devices:
            online_text = {
                "online": "在线", "warning": "需关注", "offline": "离线"
            }[device.connection_status.value]
            check = QCheckBox(
                f"{device.device_name}  ·  {device.device_id}  ·  {device.device_type}  ·  {online_text}"
            )
            check.toggled.connect(self._validate)
            self.device_checks[device.device_id] = check
            device_layout.addWidget(check)
        if not devices:
            device_layout.addWidget(QLabel("当前没有已保存设备"))
        device_layout.addStretch()
        self.device_scroll.setWidget(container)
        root.addWidget(self.device_scroll, 1)

        self.validation = QLabel("")
        self.validation.setObjectName("validationError")
        root.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = buttons.addButton("创建", QDialogButtonBox.ButtonRole.AcceptRole)
        self.create_button.setObjectName("primaryButton")
        self.create_button.setEnabled(False)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        self.name_input.textChanged.connect(self._validate)

    def _validate(self) -> None:
        has_name = bool(self.name_input.text().strip())
        has_device = any(check.isChecked() for check in self.device_checks.values())
        self.create_button.setEnabled(has_name and has_device)
        self.validation.setText("" if has_name and has_device else "请填写地图名称并至少选择一台设备")

    def selected_device_ids(self) -> tuple[str, ...]:
        return tuple(device_id for device_id, check in self.device_checks.items() if check.isChecked())


class MapCreationModeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建地图")
        self.setMinimumWidth(460)
        self.mode: str | None = None
        root = QVBoxLayout(self)
        title = QLabel("选择地图创建方式")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        for mode, name, detail in (
            ("single", "单机建图", "选择一台设备并立即协商实时点云"),
            ("multi", "多机建图", "选择至少两台设备并配置主从坐标外参"),
            ("empty", "空地图", "创建不绑定设备的地图档案，稍后导入或建图"),
        ):
            button = QPushButton(f"{name}\n{detail}")
            button.setMinimumHeight(60)
            button.setProperty("creationMode", mode)
            button.clicked.connect(lambda _checked=False, value=mode: self._select(value))
            root.addWidget(button)
        cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel.rejected.connect(self.reject)
        root.addWidget(cancel)

    def _select(self, mode: str) -> None:
        self.mode = mode
        self.accept()


class TransformTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 7, parent)
        self.setHorizontalHeaderLabels(("坐标系", "X/m", "Y/m", "Z/m", "Roll/°", "Pitch/°", "Yaw/°"))
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 7):
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(180)
        self._primary_id = ""

    def set_sources(self, sources: list[tuple[str, str]], primary_id: str) -> None:
        previous = {item.source_id: item for item in self.transforms()} if self.rowCount() else {}
        self._primary_id = primary_id
        self.setRowCount(len(sources))
        for row, (source_id, display_name) in enumerate(sources):
            item = QTableWidgetItem(f"{display_name} · {source_id}")
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 0, item)
            old = previous.get(source_id)
            values = (*old.translation_m, *old.rotation_rpy_deg) if old else (0.0,) * 6
            for column, value in enumerate(values, 1):
                spin = QDoubleSpinBox()
                spin.setRange(-1_000_000.0, 1_000_000.0)
                spin.setDecimals(4)
                spin.setValue(0.0 if source_id == primary_id else float(value))
                spin.setEnabled(source_id != primary_id)
                spin.setMinimumWidth(84)
                self.setCellWidget(row, column, spin)

    def transforms(self) -> tuple[MapTransform, ...]:
        result = []
        for row in range(self.rowCount()):
            source_id = str(self.item(row, 0).data(Qt.ItemDataRole.UserRole))
            values = tuple(float(self.cellWidget(row, column).value()) for column in range(1, 7))
            result.append(MapTransform(
                source_id, source_id == self._primary_id,
                values[:3], values[3:],
            ))
        return tuple(result)


class MappingSetupDialog(QDialog):
    def __init__(self, mode: str, devices: list[DeviceSnapshot],
                 algorithms: list[MapFusionAlgorithm], *, name: str = "",
                 name_editable: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.devices = list(devices)
        self.setWindowTitle("单机建图" if mode == "single" else "多机联合建图")
        self.setMinimumSize(760 if mode == "multi" else 560, 560 if mode == "multi" else 360)
        root = QVBoxLayout(self)
        title = QLabel("单机实时建图" if mode == "single" else "多设备联合建图")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        form = QFormLayout()
        self.name_input = QLineEdit(name)
        self.name_input.setEnabled(name_editable)
        self.name_input.setPlaceholderText("请输入地图名称")
        form.addRow("地图名称", self.name_input)
        self.algorithm_combo = QComboBox()
        for algorithm in algorithms:
            self.algorithm_combo.addItem(
                f"{algorithm.display_name} · v{algorithm.version}", algorithm.algorithm_id
            )
            if algorithm.is_default:
                self.algorithm_combo.setCurrentIndex(self.algorithm_combo.count() - 1)
        form.addRow("融合算法", self.algorithm_combo)
        root.addLayout(form)
        self.single_device_combo = QComboBox()
        self.device_list = QListWidget()
        self.primary_combo = QComboBox()
        self.transform_table = TransformTable()
        if mode == "single":
            for device in self.devices:
                text = f"{device.device_name} · {device.device_id} · {device.connection_status.value}"
                self.single_device_combo.addItem(text, device.device_id)
                if not device.ip_address:
                    model_item = self.single_device_combo.model().item(self.single_device_combo.count() - 1)
                    if model_item is not None:
                        model_item.setEnabled(False)
            root.addWidget(QLabel("选择建图设备"))
            root.addWidget(self.single_device_combo)
        else:
            root.addWidget(QLabel("选择至少两台设备（离线设备允许协商，缺少 IP 的设备不可选）"))
            for device in self.devices:
                item = QListWidgetItem(
                    f"{device.device_name} · {device.device_id} · {device.connection_status.value}"
                )
                item.setData(Qt.ItemDataRole.UserRole, device.device_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                if not device.ip_address:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.device_list.addItem(item)
            self.device_list.itemChanged.connect(self._selection_changed)
            root.addWidget(self.device_list, 1)
            primary_row = QHBoxLayout()
            primary_row.addWidget(QLabel("主设备"))
            primary_row.addWidget(self.primary_combo, 1)
            root.addLayout(primary_row)
            self.primary_combo.currentIndexChanged.connect(self._refresh_transforms)
            root.addWidget(QLabel("外参方向：主设备坐标系 <- 从设备坐标系"))
            root.addWidget(self.transform_table, 1)
        self.validation = QLabel()
        self.validation.setObjectName("validationError")
        root.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_button = buttons.addButton("开始建图", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.name_input.textChanged.connect(self._validate)
        self.single_device_combo.currentIndexChanged.connect(self._validate)
        self._validate()

    def _selection_changed(self) -> None:
        selected = self.selected_device_ids()
        current = self.primary_combo.currentData()
        self.primary_combo.blockSignals(True)
        self.primary_combo.clear()
        for device in self.devices:
            if device.device_id in selected:
                self.primary_combo.addItem(device.device_name, device.device_id)
        index = self.primary_combo.findData(current)
        if index >= 0:
            self.primary_combo.setCurrentIndex(index)
        self.primary_combo.blockSignals(False)
        self._refresh_transforms()
        self._validate()

    def _refresh_transforms(self) -> None:
        selected = set(self.selected_device_ids())
        sources = [(item.device_id, item.device_name) for item in self.devices if item.device_id in selected]
        self.transform_table.set_sources(sources, str(self.primary_combo.currentData() or ""))

    def _validate(self) -> None:
        valid_name = bool(self.name_input.text().strip())
        if self.mode == "single":
            valid_devices = self.single_device_combo.currentData() is not None
            message = "请填写名称并选择一台具有有效 IP 的设备"
        else:
            valid_devices = len(self.selected_device_ids()) >= 2 and self.primary_combo.currentData() is not None
            message = "请填写名称、选择至少两台设备并指定主设备"
        valid = valid_name and valid_devices and self.algorithm_combo.currentData() is not None
        self.start_button.setEnabled(valid)
        self.validation.setText("" if valid else message)

    def selected_device_ids(self) -> tuple[str, ...]:
        if self.mode == "single":
            value = self.single_device_combo.currentData()
            return (str(value),) if value else ()
        return tuple(
            str(self.device_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.device_list.count())
            if self.device_list.item(row).checkState() == Qt.CheckState.Checked
        )

    def primary_device_id(self) -> str:
        return str(
            self.single_device_combo.currentData()
            if self.mode == "single" else self.primary_combo.currentData()
        )

    def transforms(self) -> tuple[MapTransform, ...]:
        if self.mode == "single":
            return (MapTransform(self.primary_device_id(), True),)
        return self.transform_table.transforms()

    def algorithm_id(self) -> str:
        return str(self.algorithm_combo.currentData())


class MapFusionDialog(QDialog):
    def __init__(self, maps: list[MapDefinition], algorithms: list[MapFusionAlgorithm],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.maps = maps
        self.setWindowTitle("地图融合")
        self.setMinimumSize(820, 650)
        root = QVBoxLayout(self)
        title = QLabel("创建融合地图")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("请输入融合后地图名称")
        form.addRow("新地图名称", self.name_input)
        self.algorithm_combo = QComboBox()
        for algorithm in algorithms:
            self.algorithm_combo.addItem(algorithm.display_name, algorithm.algorithm_id)
            if algorithm.is_default:
                self.algorithm_combo.setCurrentIndex(self.algorithm_combo.count() - 1)
        form.addRow("融合算法", self.algorithm_combo)
        root.addLayout(form)
        root.addWidget(QLabel("选择至少两张具有有效 PCD 的地图"))
        self.map_list = QListWidget()
        for definition in maps:
            item = QListWidgetItem(f"{definition.name} · {definition.point_count:,} 点 · {definition.frame_id}")
            item.setData(Qt.ItemDataRole.UserRole, definition.map_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.map_list.addItem(item)
        self.map_list.itemChanged.connect(self._selection_changed)
        root.addWidget(self.map_list, 1)
        primary_row = QHBoxLayout()
        primary_row.addWidget(QLabel("主地图"))
        self.primary_combo = QComboBox()
        self.primary_combo.currentIndexChanged.connect(self._refresh_transforms)
        primary_row.addWidget(self.primary_combo, 1)
        root.addLayout(primary_row)
        root.addWidget(QLabel("外参方向：主地图坐标系 <- 从地图坐标系"))
        self.transform_table = TransformTable()
        root.addWidget(self.transform_table, 1)
        self.validation = QLabel()
        self.validation.setObjectName("validationError")
        root.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.fuse_button = buttons.addButton("开始融合", QDialogButtonBox.ButtonRole.AcceptRole)
        self.fuse_button.setObjectName("primaryButton")
        self.fuse_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.name_input.textChanged.connect(self._validate)
        self._validate()

    def selected_map_ids(self) -> tuple[str, ...]:
        return tuple(
            str(self.map_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.map_list.count())
            if self.map_list.item(row).checkState() == Qt.CheckState.Checked
        )

    def _selection_changed(self) -> None:
        selected = set(self.selected_map_ids())
        current = self.primary_combo.currentData()
        self.primary_combo.blockSignals(True)
        self.primary_combo.clear()
        for definition in self.maps:
            if definition.map_id in selected:
                self.primary_combo.addItem(definition.name, definition.map_id)
        index = self.primary_combo.findData(current)
        if index >= 0:
            self.primary_combo.setCurrentIndex(index)
        self.primary_combo.blockSignals(False)
        self._refresh_transforms()
        self._validate()

    def _refresh_transforms(self) -> None:
        selected = set(self.selected_map_ids())
        self.transform_table.set_sources(
            [(item.map_id, item.name) for item in self.maps if item.map_id in selected],
            str(self.primary_combo.currentData() or ""),
        )

    def _validate(self) -> None:
        valid = (
            bool(self.name_input.text().strip()) and len(self.selected_map_ids()) >= 2
            and self.primary_combo.currentData() is not None
            and self.algorithm_combo.currentData() is not None
        )
        self.fuse_button.setEnabled(valid)
        self.validation.setText("" if valid else "请填写名称、选择至少两张地图并指定主地图")

    def job(self) -> MapFusionJob:
        return MapFusionJob(
            uuid.uuid4().hex, self.name_input.text().strip(), self.selected_map_ids(),
            str(self.primary_combo.currentData()), self.transform_table.transforms(),
            str(self.algorithm_combo.currentData()),
        )


class FusionAlgorithmDialog(QDialog):
    def __init__(self, repository: MapFusionRepository,
                 parent: QWidget | None = None, *,
                 active_algorithm_ids: tuple[str, ...] = ()) -> None:
        super().__init__(parent)
        self.repository = repository
        self.active_algorithm_ids = active_algorithm_ids
        self.setWindowTitle("融合算法配置")
        self.setMinimumSize(760, 480)
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_current)
        left.addWidget(self.list, 1)
        self.import_button = QPushButton("导入 .py 算法")
        self.import_button.clicked.connect(self._import)
        left.addWidget(self.import_button)
        root.addLayout(left, 1)
        right = QVBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)
        self.enabled = QCheckBox("启用算法")
        self.default = QCheckBox("设为默认算法")
        right.addWidget(self.enabled)
        right.addWidget(self.default)
        right.addWidget(QLabel("默认参数（JSON 对象）"))
        self.options = QPlainTextEdit()
        right.addWidget(self.options, 1)
        actions = QHBoxLayout()
        self.save_button = QPushButton("保存配置")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._save)
        self.delete_button = QPushButton("删除算法")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.save_button)
        actions.addWidget(self.delete_button)
        right.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        right.addWidget(close)
        root.addLayout(right, 2)
        self._reload()
        if repository.read_only:
            self.summary.setText(repository.error_message)
            self.import_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self.delete_button.setEnabled(False)

    def _reload(self) -> None:
        current_id = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.clear()
        for algorithm in self.repository.algorithms():
            suffix = " · 默认" if algorithm.is_default else ""
            item = QListWidgetItem(f"{algorithm.display_name} · v{algorithm.version}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, algorithm.algorithm_id)
            self.list.addItem(item)
            if algorithm.algorithm_id == current_id:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)

    def _current(self) -> MapFusionAlgorithm | None:
        item = self.list.currentItem()
        return self.repository.algorithm(str(item.data(Qt.ItemDataRole.UserRole))) if item else None

    def _show_current(self) -> None:
        algorithm = self._current()
        if algorithm is None:
            return
        self.summary.setText(
            f"ID: {algorithm.algorithm_id}\n脚本: {algorithm.script_path}\nSHA-256: {algorithm.sha256}"
        )
        self.enabled.setChecked(algorithm.enabled)
        self.default.setChecked(algorithm.is_default)
        self.options.setPlainText(json.dumps(algorithm.default_options, ensure_ascii=False, indent=2))
        self.enabled.setEnabled(not algorithm.builtin)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入融合算法", "", "Python 文件 (*.py)")
        if not path:
            return
        try:
            self.repository.import_algorithm(path)
            self._reload()
        except MapFusionError as exc:
            QMessageBox.critical(self, "算法导入失败", str(exc))

    def _save(self) -> None:
        algorithm = self._current()
        if algorithm is None:
            return
        try:
            options = json.loads(self.options.toPlainText() or "{}")
            if not isinstance(options, dict):
                raise ValueError("默认参数必须是 JSON 对象")
            self.repository.update(
                algorithm.algorithm_id, enabled=self.enabled.isChecked(),
                is_default=self.default.isChecked(), default_options=options,
            )
            self._reload()
        except (MapFusionError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "配置保存失败", str(exc))

    def _delete(self) -> None:
        algorithm = self._current()
        if algorithm is None:
            return
        try:
            self.repository.delete(
                algorithm.algorithm_id, active_algorithm_ids=self.active_algorithm_ids
            )
            self._reload()
        except MapFusionError as exc:
            QMessageBox.warning(self, "无法删除算法", str(exc))


class MappingDeviceDialog(QDialog):
    def __init__(
        self,
        definition: MapDefinition,
        devices: list[DeviceSnapshot],
        telemetry_store=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择建图设备")
        self.setMinimumSize(560, 380)
        self._devices = {item.device_id.casefold(): item for item in devices}
        self.radios: dict[str, QRadioButton] = {}
        root = QVBoxLayout(self)
        title = QLabel(f"为“{definition.name}”选择一台建图设备")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        note = QLabel("仅列出创建地图时登记的设备；离线设备允许尝试协商。")
        note.setObjectName("muted")
        root.addWidget(note)
        scroll = QScrollArea()
        scroll.setObjectName("mapDeviceScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("mapDeviceViewport")
        container = QWidget()
        container.setObjectName("mapDeviceSelector")
        rows = QVBoxLayout(container)
        for creator in definition.creator_devices:
            device = self._devices.get(creator.device_id.casefold())
            radio = QRadioButton()
            radio.setMinimumWidth(24)
            radio.setProperty("deviceId", creator.device_id)
            self.radios[creator.device_id] = radio
            frame = QFrame()
            frame.setObjectName("deviceSelectCard")
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row = QHBoxLayout(frame)
            row.addWidget(radio)
            text = QVBoxLayout()
            text.addWidget(QLabel(f"{creator.device_name}  ·  {creator.device_id}  ·  {creator.device_type}"))
            if device is None:
                detail = "设备已从 devices.json 删除"
                enabled = False
            else:
                udp = telemetry_store.telemetry(device.device_id).udp_link_status if telemetry_store else UdpLinkStatus.UNKNOWN
                detail = (
                    f"IP {device.ip_address or '未配置'}  ·  MQTT {device.connection_status.value}"
                    f"  ·  UDP {udp.value}"
                )
                enabled = bool(device.ip_address)
            label = QLabel(detail)
            label.setObjectName("muted")
            label.setWordWrap(True)
            text.addWidget(label)
            row.addLayout(text, 1)
            radio.setEnabled(enabled)
            radio.toggled.connect(self._validate)
            rows.addWidget(frame)
        rows.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.start_button = self.buttons.addButton("开始建图", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._validate()

    def _validate(self) -> None:
        self.start_button.setEnabled(any(item.isChecked() and item.isEnabled() for item in self.radios.values()))

    def selected_device_id(self) -> str | None:
        return next((device_id for device_id, radio in self.radios.items() if radio.isChecked()), None)


class PointCloudViewer(QWidget):
    load_failed = Signal(str)
    map_point_picked = Signal(float, float)

    def __init__(
        self,
        loader: MapPointCloudLoader | None = None,
        pgm_loader: PgmMapLoader | None = None,
        canvas_factory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self.loader = loader or MapPointCloudLoader()
        self.pgm_loader = pgm_loader or PgmMapLoader()
        self.current_map: MapDefinition | None = None
        self.markers: tuple[DeviceMapMarker, ...] = ()
        self._canvas = None
        self._view = None
        self._points_visual = None
        self._live_points_visual = None
        self._marker_visual = None
        self._shape_visuals: list[object] = []
        self._pgm_visual = None
        self._device_axis_visual = None
        self._trail_visual = None
        self._camera = None
        self.layer_mode = "overlay"
        self.pointcloud_loaded = False
        self.pgm_loaded = False
        self.selected_device_pose: PoseTelemetry | None = None
        self.device_trail: tuple[tuple[float, float, float], ...] = ()
        self.interaction_mode = "browse"
        self._task_path_visuals: list[object] = []
        self._task_point_visuals: list[object] = []
        self._conflict_visual = None
        self._point_data = np.empty((0, 3), dtype=np.float32)
        self._task_paths = {}
        self._task_conflicts = []
        self._live_point_data = np.empty((0, 3), dtype=np.float32)
        self._task_paths: dict[str, list[tuple[float, float, float]]] = {}
        self._task_conflicts: list[tuple[float, float, float]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)
        self.status = QLabel("尚未加载点云")
        self.status.setObjectName("viewerStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self._stack.addWidget(self.status)
        try:
            self._initialize_canvas(canvas_factory)
        except Exception as exc:
            self.status.setText(f"三维渲染不可用：{exc}\n请检查 VisPy 与 OpenGL 环境")

    def _initialize_canvas(self, canvas_factory: Callable[[], object] | None) -> None:
        if canvas_factory is None:
            from vispy import app as vispy_app

            vispy_app.use_app("pyside6")
            from vispy import scene

            canvas_factory = lambda: scene.SceneCanvas(
                keys="interactive", bgcolor=self.theme_palette.dashboard_background, show=False
            )
        self._canvas = canvas_factory()
        native = getattr(self._canvas, "native", None)
        if native is None:
            return
        self._stack.addWidget(native)
        self._stack.setCurrentWidget(native)
        native.setMinimumSize(420, 320)
        native.installEventFilter(self)
        central_widget = getattr(self._canvas, "central_widget", None)
        if central_widget is None:
            return
        from vispy import scene

        class MiddlePanTurntableCamera(
            MiddlePanTurntableCameraMixin,
            scene.cameras.TurntableCamera,
        ):
            pass

        self._view = central_widget.add_view()
        self._camera = MiddlePanTurntableCamera(fov=45, elevation=25, azimuth=35)
        self._view.camera = self._camera
        self._points_visual = scene.visuals.Markers(parent=self._view.scene)
        self._live_points_visual = scene.visuals.Markers(parent=self._view.scene)
        self._marker_visual = scene.visuals.Markers(parent=self._view.scene)
        self._device_axis_visual = scene.visuals.Line(parent=self._view.scene)
        self._trail_visual = scene.visuals.Line(parent=self._view.scene)
        self._conflict_visual = scene.visuals.Markers(parent=self._view.scene)
        scene.visuals.XYZAxis(parent=self._view.scene)

    def load_map(self, definition: MapDefinition, pcd_path: str | Path) -> None:
        self.current_map = definition
        try:
            data = self.loader.load(pcd_path, sample_for_render=True)
            if self._points_visual is None:
                raise PointCloudError("VisPy/OpenGL 渲染器未初始化")
            self._point_data = np.asarray(data.points, dtype=np.float32)
            self._points_visual.set_data(
                self._point_data,
                face_color=self.theme_palette.primary,
                edge_width=0,
                size=2.2,
            )
            self.pointcloud_loaded = True
            self.set_layer_mode(self.layer_mode)
            self.reset_view()
            self._render_markers()
            native = getattr(self._canvas, "native", None)
            if native is not None:
                self._stack.setCurrentWidget(native)
        except Exception as exc:
            self.status.setText(f"点云加载失败：{exc}")
            self._stack.setCurrentWidget(self.status)
            self.load_failed.emit(str(exc))
            raise PointCloudError(str(exc)) from exc

    def load_pgm_layer(self, definition: MapDefinition, yaml_path: str | Path) -> None:
        self.current_map = definition
        try:
            data = self.pgm_loader.load_yaml(yaml_path)
            if self._view is None:
                raise PgmMapError("VisPy/OpenGL 渲染器未初始化")
            from vispy import scene

            if self._pgm_visual is None:
                self._pgm_visual = scene.visuals.Image(
                    data.rgba(), parent=self._view.scene, method="subdivide"
                )
            else:
                self._pgm_visual.set_data(data.rgba())
            transform = scene.transforms.MatrixTransform()
            transform.scale((data.metadata.resolution, data.metadata.resolution, 1.0))
            transform.rotate(math.degrees(data.metadata.origin_yaw), (0, 0, 1))
            transform.translate((data.metadata.origin_x, data.metadata.origin_y, -0.04))
            self._pgm_visual.transform = transform
            self.pgm_loaded = True
            self.set_layer_mode(self.layer_mode)
            self.reset_view()
            native = getattr(self._canvas, "native", None)
            if native is not None:
                self._stack.setCurrentWidget(native)
        except Exception as exc:
            self.load_failed.emit(str(exc))
            raise PgmMapError(str(exc)) from exc

    def set_layer_mode(self, mode: str) -> None:
        if mode not in {"pointcloud", "grid", "overlay"}:
            raise ValueError(f"未知地图图层模式：{mode}")
        self.layer_mode = mode
        if self._points_visual is not None:
            self._points_visual.visible = self.pointcloud_loaded and mode in {"pointcloud", "overlay"}
        if self._pgm_visual is not None:
            self._pgm_visual.visible = self.pgm_loaded and mode in {"grid", "overlay"}

    def clear(self) -> None:
        self.current_map = None
        self._point_data = np.empty((0, 3), dtype=np.float32)
        if self._points_visual is not None:
            self._points_visual.set_data(np.empty((0, 3), dtype=np.float32))
        self.clear_live_points()
        if self._marker_visual is not None:
            self._marker_visual.set_data(np.empty((0, 3), dtype=np.float32))
        if self._pgm_visual is not None:
            self._pgm_visual.visible = False
        if self._device_axis_visual is not None:
            self._device_axis_visual.set_data(pos=np.empty((0, 3), dtype=np.float32))
        if self._trail_visual is not None:
            self._trail_visual.set_data(pos=np.empty((0, 3), dtype=np.float32))
        self.pointcloud_loaded = False
        self.pgm_loaded = False
        self.selected_device_pose = None
        self.device_trail = ()
        self.show_message("尚未加载点云")

    def set_live_points(self, points: np.ndarray, bounds=None) -> None:
        array = np.asarray(points, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != 3:
            raise ValueError("实时点云必须为 Nx3 数组")
        if self._live_points_visual is not None:
            self._live_point_data = array
            self._live_points_visual.set_data(
                array, face_color=self.theme_palette.focus, edge_width=0, size=2.5
            )
            native = getattr(self._canvas, "native", None)
            if native is not None:
                self._stack.setCurrentWidget(native)
        if bounds is not None and self._camera is not None:
            center = (
                (bounds.min_x + bounds.max_x) / 2,
                (bounds.min_y + bounds.max_y) / 2,
                (bounds.min_z + bounds.max_z) / 2,
            )
            if not getattr(self, "_live_view_initialized", False):
                self._camera.center = center
                self._camera.distance = max(bounds.width, bounds.height, bounds.depth, 1.0) * 1.8
                self._live_view_initialized = True

    def clear_live_points(self) -> None:
        self._live_view_initialized = False
        if self._live_points_visual is not None:
            self._live_point_data = np.empty((0, 3), dtype=np.float32)
            self._live_points_visual.set_data(np.empty((0, 3), dtype=np.float32))

    def show_message(self, message: str) -> None:
        self.status.setText(message)
        self._stack.setCurrentWidget(self.status)

    def reset_view(self) -> None:
        if self._camera is None or self.current_map is None:
            return
        if self.current_map.bounds is not None:
            bounds = self.current_map.bounds
            center = (
                (bounds.min_x + bounds.max_x) / 2,
                (bounds.min_y + bounds.max_y) / 2,
                (bounds.min_z + bounds.max_z) / 2,
            )
            extent = max(bounds.width, bounds.height, bounds.depth, 1.0)
        elif self.current_map.pgm is not None:
            pgm = self.current_map.pgm
            center = (
                pgm.origin_x + pgm.width_m / 2,
                pgm.origin_y + pgm.height_m / 2,
                0.0,
            )
            extent = max(pgm.width_m, pgm.height_m, 1.0)
        else:
            return
        self._camera.center = center
        self._camera.distance = extent * 1.8
        self._camera.elevation = 25
        self._camera.azimuth = 35

    def fit_all(self) -> None:
        self.reset_view()

    def set_interaction_mode(self, mode: str) -> None:
        if mode not in {"browse", "pick"}:
            raise ValueError("地图交互模式必须为 browse 或 pick")
        self.interaction_mode = mode
        if self._camera is not None:
            self._camera.interactive = mode == "browse"
            if mode == "pick" and self.current_map is not None:
                self.reset_view()
                self._camera.elevation = 90
                self._camera.azimuth = 0

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        native = getattr(self._canvas, "native", None)
        if (
            watched is native
            and self.interaction_mode == "pick"
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = self._screen_to_map(event.position().x(), event.position().y(), watched.width(), watched.height())
            if point is not None:
                self.map_point_picked.emit(*point)
            return True
        return super().eventFilter(watched, event)

    def _screen_to_map(self, x: float, y: float, width: int, height: int) -> tuple[float, float] | None:
        if self.current_map is None or width <= 0 or height <= 0:
            return None
        if self.current_map.bounds is not None:
            bounds = self.current_map.bounds
            min_x, max_x = bounds.min_x, bounds.max_x
            min_y, max_y = bounds.min_y, bounds.max_y
        elif self.current_map.pgm is not None:
            pgm = self.current_map.pgm
            corners = []
            cosine, sine = math.cos(pgm.origin_yaw), math.sin(pgm.origin_yaw)
            for local_x, local_y in ((0, 0), (pgm.width_m, 0), (0, pgm.height_m), (pgm.width_m, pgm.height_m)):
                corners.append((pgm.origin_x + cosine * local_x - sine * local_y,
                                pgm.origin_y + sine * local_x + cosine * local_y))
            min_x, max_x = min(item[0] for item in corners), max(item[0] for item in corners)
            min_y, max_y = min(item[1] for item in corners), max(item[1] for item in corners)
        else:
            return None
        return (
            min_x + min(1.0, max(0.0, x / width)) * (max_x - min_x),
            max_y - min(1.0, max(0.0, y / height)) * (max_y - min_y),
        )

    def set_task_paths(self, paths: dict[str, list[tuple[float, float, float]]]) -> None:
        if self._view is None:
            return
        self._task_paths = dict(paths)
        from vispy import scene
        while len(self._task_path_visuals) < len(paths):
            self._task_path_visuals.append(scene.visuals.Line(parent=self._view.scene))
            self._task_point_visuals.append(scene.visuals.Markers(parent=self._view.scene))
        colors = self.theme_palette.route_colors
        for index, (_, values) in enumerate(paths.items()):
            points = np.asarray(values, dtype=np.float32)
            color = colors[index % len(colors)]
            self._task_path_visuals[index].set_data(pos=points, color=color, width=2.5)
            self._task_point_visuals[index].set_data(points, face_color=color, edge_color=self.theme_palette.text_strong, size=8)
            self._task_path_visuals[index].visible = True
            self._task_point_visuals[index].visible = True
        for index in range(len(paths), len(self._task_path_visuals)):
            self._task_path_visuals[index].visible = False
            self._task_point_visuals[index].visible = False

    def set_task_conflicts(self, positions: list[tuple[float, float, float]]) -> None:
        self._task_conflicts = list(positions)
        if self._conflict_visual is not None:
            points = np.asarray(positions, dtype=np.float32)
            if not len(points):
                points = np.empty((0, 3), dtype=np.float32)
            self._conflict_visual.set_data(points, face_color=self.theme_palette.error, edge_color=self.theme_palette.text_strong, size=13)

    def set_execution_markers(self, markers: list[DeviceMapMarker]) -> None:
        self.set_device_markers(markers)

    def set_device_markers(self, markers: list[DeviceMapMarker] | tuple[DeviceMapMarker, ...]) -> None:
        self.markers = tuple(markers)
        self._render_markers()

    def _render_markers(self) -> None:
        if self._marker_visual is None:
            return
        for visual in self._shape_visuals:
            try:
                visual.parent = None
            except Exception:
                pass
        self._shape_visuals.clear()
        fallback = []
        for marker in self.markers:
            try:
                self._shape_visuals.append(self._create_marker_mesh(marker))
            except Exception:
                fallback.append((marker.x, marker.y, marker.z))
        positions = np.asarray(fallback, dtype=np.float32)
        if not len(positions):
            positions = np.empty((0, 3), dtype=np.float32)
        self._marker_visual.set_data(positions, face_color=self.theme_palette.warning, edge_color=self.theme_palette.text_strong, size=12)

    def _create_marker_mesh(self, marker: DeviceMapMarker):
        from vispy import scene
        from vispy.geometry import create_box, create_sphere
        from vispy.visuals.transforms import MatrixTransform

        if marker.marker_shape == MapMarkerShape.CUBE:
            mesh_data = create_box(width=0.8, height=0.8, depth=0.8)
            visual = scene.visuals.Mesh(
                vertices=mesh_data.get_vertices(), faces=mesh_data.get_faces(),
                color=self.theme_palette.warning, parent=self._view.scene,
            )
        elif marker.marker_shape == MapMarkerShape.ARROW:
            vertices = np.asarray([
                (0.75, 0.0, 0.0), (-0.45, 0.42, 0.0), (-0.25, 0.0, 0.0),
                (-0.45, -0.42, 0.0),
            ], dtype=np.float32)
            faces = np.asarray([(0, 1, 2), (0, 2, 3)], dtype=np.uint32)
            visual = scene.visuals.Mesh(
                vertices=vertices, faces=faces, color=self.theme_palette.warning,
                parent=self._view.scene,
            )
        else:
            mesh_data = create_sphere(rows=8, cols=12, radius=0.45)
            visual = scene.visuals.Mesh(
                vertices=mesh_data.get_vertices(), faces=mesh_data.get_faces(),
                color=self.theme_palette.warning, parent=self._view.scene,
            )
        transform = MatrixTransform()
        transform.rotate(float(marker.yaw), (0, 0, 1))
        transform.translate((marker.x, marker.y, marker.z))
        visual.transform = transform
        return visual

    def set_selected_device_pose(self, pose: PoseTelemetry | None) -> None:
        self.selected_device_pose = pose
        if self._device_axis_visual is None:
            return
        if pose is None:
            self._device_axis_visual.set_data(pos=np.empty((0, 3), dtype=np.float32))
            return
        roll, pitch, yaw = np.radians((pose.roll, pose.pitch, pose.yaw))
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        rotation = np.asarray([
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ], dtype=np.float32)
        origin = np.asarray((pose.x, pose.y, pose.z), dtype=np.float32)
        scale = 1.2
        endpoints = [origin + rotation[:, index] * scale for index in range(3)]
        positions = np.asarray([
            origin, endpoints[0], origin, endpoints[1], origin, endpoints[2]
        ], dtype=np.float32)
        colors = np.asarray([
            self._rgba(self.theme_palette.error), self._rgba(self.theme_palette.error),
            self._rgba(self.theme_palette.good), self._rgba(self.theme_palette.good),
            self._rgba(self.theme_palette.primary), self._rgba(self.theme_palette.primary),
        ], dtype=np.float32)
        self._device_axis_visual.set_data(
            pos=positions, color=colors, connect="segments", width=2.5
        )

    def set_device_trail(self, positions: list[tuple[float, float, float]] | tuple[tuple[float, float, float], ...]) -> None:
        self.device_trail = tuple(positions)
        if self._trail_visual is None:
            return
        points = np.asarray(self.device_trail, dtype=np.float32)
        if len(points) < 2:
            points = np.empty((0, 3), dtype=np.float32)
        self._trail_visual.set_data(pos=points, color=self.theme_palette.primary_strong, width=2.0)

    @staticmethod
    def _rgba(value: str) -> tuple[float, float, float, float]:
        color = QColor(value)
        return color.redF(), color.greenF(), color.blueF(), 1.0

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        if self._canvas is not None:
            try:
                self._canvas.bgcolor = palette.dashboard_background
            except Exception:
                pass
        if self._points_visual is not None and self.pointcloud_loaded:
            self._points_visual.set_data(self._point_data, face_color=palette.primary, edge_width=0, size=2.2)
        if self._live_points_visual is not None and len(self._live_point_data):
            self._live_points_visual.set_data(self._live_point_data, face_color=palette.focus, edge_width=0, size=2.5)
        if self._task_paths:
            self.set_task_paths(self._task_paths)
        if self._task_conflicts:
            self.set_task_conflicts(self._task_conflicts)
        self._render_markers()
        self.set_selected_device_pose(self.selected_device_pose)
        self.set_device_trail(self.device_trail)
        self.status.update()
        self.update()


class MapDetailPage(QWidget):
    back_requested = Signal()
    reload_requested = Signal()
    export_requested = Signal()
    mapping_requested = Signal()

    def __init__(self, viewer_factory: Callable[[], PointCloudViewer] | None = None) -> None:
        super().__init__()
        self.definition: MapDefinition | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(12)
        toolbar = QHBoxLayout()
        back = QPushButton("返回地图列表")
        back.setObjectName("backButton")
        back.clicked.connect(self.back_requested)
        toolbar.addWidget(back)
        self.title = QLabel("地图详情")
        self.title.setObjectName("pageTitle")
        toolbar.addWidget(self.title)
        toolbar.addStretch()
        reset = QPushButton("复位视角")
        reset.clicked.connect(lambda: self.viewer.reset_view())
        fit = QPushButton("适配全部")
        fit.clicked.connect(lambda: self.viewer.fit_all())
        reload_button = QPushButton("重新加载")
        reload_button.clicked.connect(self.reload_requested)
        export_button = QPushButton("下载地图")
        export_button.clicked.connect(self.export_requested)
        self.mapping_button = QPushButton("重新建图")
        self.mapping_button.setObjectName("primaryButton")
        self.mapping_button.clicked.connect(self.mapping_requested)
        for button in (reset, fit, reload_button, export_button, self.mapping_button):
            toolbar.addWidget(button)
        root.addLayout(toolbar)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self.mapping_status = QFrame()
        self.mapping_status.setObjectName("mappingStatus")
        status_layout = QHBoxLayout(self.mapping_status)
        status_layout.setContentsMargins(12, 8, 12, 8)
        self.mapping_state = QLabel("实时建图未启动")
        self.mapping_state.setObjectName("statusPill")
        self.mapping_metrics = QLabel("完整帧 0  ·  丢帧 0  ·  接收点 0  ·  融合点 0")
        self.mapping_metrics.setObjectName("muted")
        status_layout.addWidget(self.mapping_state)
        self.mapping_elapsed = QLabel("时长 00:00")
        self.mapping_elapsed.setObjectName("muted")
        status_layout.addWidget(self.mapping_elapsed)
        status_layout.addWidget(self.mapping_metrics, 1)
        self.mapping_status.setVisible(False)
        root.addWidget(self.mapping_status)
        self.viewer = viewer_factory() if viewer_factory else PointCloudViewer()
        root.addWidget(self.viewer, 1)
        self._started_at = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

    def set_theme(self, palette: ThemePalette) -> None:
        set_theme = getattr(self.viewer, "set_theme", None)
        if set_theme is not None:
            set_theme(palette)
        self.update()

    def set_map(
        self,
        definition: MapDefinition,
        pcd_path: Path | None,
        pgm_yaml_path: Path | None = None,
    ) -> None:
        self.definition = definition
        self.title.setText(definition.name)
        creators = "、".join(item.device_name for item in definition.creator_devices) or "未知"
        bounds = "范围未知"
        if definition.bounds:
            bounds = (
                f"范围 {definition.bounds.width:.1f} × {definition.bounds.height:.1f} × "
                f"{definition.bounds.depth:.1f} m"
            )
        self.info.setText(
            f"建图时间 {definition.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}  ·  "
            f"设备 {creators}  ·  {definition.point_count:,} 点  ·  {bounds}"
        )
        self.viewer.clear()
        errors: list[str] = []
        if pcd_path is not None:
            try:
                self.viewer.load_map(definition, pcd_path)
            except PointCloudError as exc:
                errors.append(str(exc))
        if pgm_yaml_path is not None:
            try:
                self.viewer.load_pgm_layer(definition, pgm_yaml_path)
            except PgmMapError as exc:
                errors.append(str(exc))
        self.viewer.set_layer_mode("overlay")
        if pcd_path is None and pgm_yaml_path is None:
            self.viewer.show_message(definition.error_message or "该地图尚未导入 PCD 点云")
        elif errors and not (self.viewer.pointcloud_loaded or self.viewer.pgm_loaded):
            self.viewer.show_message("地图加载失败：" + "；".join(errors))

    def set_mapping_available(self, available: bool, message: str = "") -> None:
        self.mapping_button.setEnabled(available)
        self.mapping_button.setToolTip(message)

    def update_mapping(self, snapshot: MapBuildingSessionSnapshot) -> None:
        self.mapping_status.setVisible(True)
        active = snapshot.state in {"negotiating", "mapping", "warning", "degraded"}
        self.mapping_button.setText("结束建图" if active else "重新建图")
        self.mapping_button.setEnabled(snapshot.state != "saving")
        self.mapping_state.setText(snapshot.message)
        self.mapping_metrics.setText(
            f"完整帧 {snapshot.complete_frames}  ·  丢帧 {snapshot.dropped_frames}  ·  "
            f"接收点 {snapshot.received_points:,}  ·  融合点 {snapshot.fused_points:,}  ·  "
            f"最后数据 {snapshot.last_data_at.astimezone().strftime('%H:%M:%S') if snapshot.last_data_at else '--'}"
        )
        self._started_at = snapshot.started_at
        self._refresh_elapsed()
        if snapshot.state in {"negotiating", "mapping", "warning", "saving"}:
            self._elapsed_timer.start()
        else:
            self._elapsed_timer.stop()

    def _refresh_elapsed(self) -> None:
        if self._started_at:
            seconds = max(0, int((datetime.now().astimezone() - self._started_at.astimezone()).total_seconds()))
            self.mapping_elapsed.setText(f"时长 {seconds // 60:02d}:{seconds % 60:02d}")


class MapPage(QWidget):
    fusion_finished = Signal(object, object)

    def __init__(
        self,
        source: DeviceDataSource,
        overview=None,
        repository: MapRepository | None = None,
        viewer_factory: Callable[[], PointCloudViewer] | None = None,
        mapping_service=None,
        telemetry_store=None,
        fusion_repository: MapFusionRepository | None = None,
        fusion_runner: MapFusionRunner | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.repository = repository or MapRepository()
        self.maps = self.repository.maps()
        self.devices = source.snapshots()
        self.selected_map_ids: set[str] = set()
        self.edit_mode = False
        self.card_column_count = 0
        self.current_map_id: str | None = None
        self.mapping_service = mapping_service
        self.fusion_repository = (
            fusion_repository
            or getattr(mapping_service, "fusion_repository", None)
            or MapFusionRepository()
        )
        self.fusion_runner = fusion_runner or getattr(mapping_service, "fusion_runner", None) or MapFusionRunner()
        self._fusion_thread: threading.Thread | None = None
        self.telemetry_store = telemetry_store
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._build(viewer_factory)
        self._render_cards()
        self.repository.maps_updated.connect(self._on_maps_updated)
        self.source.devices_updated.connect(self._on_devices_updated)
        self.fusion_finished.connect(self._on_fusion_finished)
        if self.mapping_service is not None:
            self.mapping_service.session_updated.connect(self._on_mapping_updated)
            self.mapping_service.preview_updated.connect(self._on_mapping_preview)
            self.mapping_service.completed.connect(self._on_mapping_completed)
            self.mapping_service.failed.connect(self._on_mapping_failed)
            self.mapping_service.availability_changed.connect(self._on_mapping_availability)
            self.mapping_service.degraded.connect(self._on_mapping_degraded)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.detail_page.set_theme(palette)
        self.update()

    def _build(self, viewer_factory: Callable[[], PointCloudViewer] | None) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page_stack = QStackedWidget()
        root.addWidget(self.page_stack)

        self.list_page = QWidget()
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(14)
        self.page_stack.addWidget(self.list_page)
        self.detail_page = MapDetailPage(viewer_factory)
        self.detail_page.back_requested.connect(self.show_list)
        self.detail_page.reload_requested.connect(self._reload_current_map)
        self.detail_page.export_requested.connect(self._export_current_map)
        self.detail_page.mapping_requested.connect(self._toggle_mapping)
        if self.mapping_service is None:
            self.detail_page.set_mapping_available(False, "UDP 建图模块未配置")
        else:
            self.detail_page.set_mapping_available(
                self.mapping_service.available, self.mapping_service.module_message
            )
        self.page_stack.addWidget(self.detail_page)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("地图管理")
        title.setObjectName("pageTitle")
        subtitle = QLabel("创建、保存、下载并复原本地点云地图")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.new_button = QPushButton("新建地图")
        self.new_button.setObjectName("primaryButton")
        self.new_button.clicked.connect(self._create_map)
        self.fusion_button = QPushButton("地图融合")
        self.fusion_button.clicked.connect(self._open_map_fusion)
        self.algorithm_button = QPushButton("融合算法")
        self.algorithm_button.clicked.connect(self._open_algorithm_manager)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self._toggle_edit)
        header.addWidget(self.new_button)
        header.addWidget(self.fusion_button)
        header.addWidget(self.algorithm_button)
        header.addWidget(self.edit_button)
        layout.addLayout(header)

        self.action_bar = QHBoxLayout()
        self.action_bar.addStretch()
        self.rename_button = QPushButton("修改名称")
        self.import_button = QPushButton("导入 / 替换 PCD")
        self.import_pgm_button = QPushButton("导入 / 替换 PGM")
        self.export_button = QPushButton("下载地图")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        for button in (
            self.rename_button, self.import_button, self.import_pgm_button,
            self.export_button, self.delete_button,
        ):
            button.setVisible(False)
            button.setEnabled(False)
            self.action_bar.addWidget(button)
        self.rename_button.clicked.connect(self._rename_selected)
        self.import_button.clicked.connect(self._import_selected)
        self.import_pgm_button.clicked.connect(self._import_pgm_selected)
        self.export_button.clicked.connect(self._export_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        layout.addLayout(self.action_bar)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("按地图名称搜索")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render_cards)
        self.result_label = QLabel()
        self.result_label.setObjectName("muted")
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.result_label)
        layout.addLayout(search_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_container = QWidget()
        self.card_container.setObjectName("mapGrid")
        self.card_grid = QGridLayout(self.card_container)
        self.card_grid.setContentsMargins(2, 2, 8, 2)
        self.card_grid.setHorizontalSpacing(14)
        self.card_grid.setVerticalSpacing(14)
        self.card_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.card_container)
        layout.addWidget(self.scroll, 1)

    def filtered_maps(self) -> list[MapDefinition]:
        query = self.search.text().strip().casefold()
        return [item for item in self.maps if not query or query in item.name.casefold()]

    def _render_cards(self) -> None:
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        filtered = self.filtered_maps()
        columns = 3 if self.width() >= 1180 else 2 if self.width() >= 760 else 1
        self.card_column_count = columns
        for index, definition in enumerate(filtered):
            card = MapCard(definition)
            card.set_edit_mode(self.edit_mode, definition.map_id in self.selected_map_ids)
            card.selection_changed.connect(self._set_selected)
            card.double_clicked.connect(self.show_detail)
            self.card_grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.card_grid.setColumnStretch(column, 1)
        self.result_label.setText(f"显示 {len(filtered)} / {len(self.maps)} 张地图")
        if not filtered:
            message = "尚未创建地图" if not self.maps else "没有匹配的地图"
            empty = QLabel(message)
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card_grid.addWidget(empty, 0, 0, 1, columns)

    def _create_map(self) -> None:
        mode_dialog = MapCreationModeDialog(self)
        if mode_dialog.exec() != QDialog.DialogCode.Accepted or not mode_dialog.mode:
            return
        if mode_dialog.mode == "empty":
            name, accepted = QInputDialog.getText(self, "创建空地图", "地图名称")
            if not accepted:
                return
            try:
                self.repository.create_empty(name)
            except MapRepositoryError as exc:
                QMessageBox.critical(self, "地图创建失败", str(exc))
            return
        if self.mapping_service is None or not self.mapping_service.available:
            message = self.mapping_service.module_message if self.mapping_service else "UDP 建图模块未配置"
            QMessageBox.warning(self, "建图不可用", message)
            return
        self._create_and_start_mapping(mode_dialog.mode)

    def _create_and_start_mapping(self, mode: str) -> None:
        dialog = MappingSetupDialog(
            mode, self.devices, self.fusion_repository.algorithms(enabled_only=True), parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = set(dialog.selected_device_ids())
        selected_devices = [item for item in self.devices if item.device_id in selected]
        creators = tuple(
            MapCreatorDevice(item.device_id, item.device_name, item.device_type)
            for item in selected_devices
        )
        try:
            definition = self.repository.create(dialog.name_input.text(), creators)
            self.show_detail(definition.map_id)
            self.mapping_service.start_job(
                definition, selected_devices, dialog.primary_device_id(),
                dialog.transforms(), dialog.algorithm_id(),
            )
        except (MapRepositoryError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "地图创建失败", str(exc))

    def _open_algorithm_manager(self) -> None:
        snapshot = self.mapping_service.current_job_snapshot if self.mapping_service else None
        active = (snapshot.algorithm_id,) if snapshot else ()
        FusionAlgorithmDialog(
            self.fusion_repository, self, active_algorithm_ids=active
        ).exec()

    def _open_map_fusion(self) -> None:
        if self._fusion_thread and self._fusion_thread.is_alive():
            QMessageBox.information(self, "地图融合", "已有地图融合任务正在运行")
            return
        candidates = [item for item in self.maps if item.status == MapStatus.READY and item.pcd_path]
        if len(candidates) < 2:
            QMessageBox.warning(self, "无法融合", "至少需要两张具有有效 PCD 的地图")
            return
        dialog = MapFusionDialog(candidates, self.fusion_repository.algorithms(enabled_only=True), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        job = dialog.job()
        self.fusion_button.setEnabled(False)
        self.fusion_button.setText("融合中…")
        self._fusion_thread = threading.Thread(
            target=self._run_offline_fusion, args=(job,), name="ccs-map-fusion", daemon=True,
        )
        self._fusion_thread.start()

    def _run_offline_fusion(self, job: MapFusionJob) -> None:
        try:
            definitions = [self.repository.map_by_id(map_id) for map_id in job.source_map_ids]
            if any(item is None for item in definitions):
                raise MapRepositoryError("融合源地图已被删除")
            maps = [item for item in definitions if item is not None]
            primary = next(item for item in maps if item.map_id == job.primary_map_id)
            algorithm = self.fusion_repository.algorithm(job.algorithm_id)
            if algorithm is None or not algorithm.enabled:
                raise MapFusionError("融合算法不存在或已禁用")
            directory = self.repository.write_fusion_job(job.job_id, {
                "schema_version": 1, "job_id": job.job_id, "state": "running",
                "output_name": job.output_name, "source_map_ids": list(job.source_map_ids),
                "primary_map_id": job.primary_map_id, "algorithm_id": job.algorithm_id,
                "transforms": [
                    {"source_id": item.source_id, "is_primary": item.is_primary,
                     "translation_m": list(item.translation_m),
                     "rotation_rpy_deg": list(item.rotation_rpy_deg)}
                    for item in job.transforms
                ],
            })
            output = directory / "plugin-output.pcd"
            inputs = [self.repository.pcd_path(item.map_id) for item in maps]
            self.fusion_runner.run(algorithm, inputs, primary.frame_id, list(job.transforms), output)
            creators_by_id = {
                creator.device_id: creator for definition in maps for creator in definition.creator_devices
            }
            now = datetime.now(timezone.utc)
            provenance = MapBuildProvenance(
                MapBuildMode.FUSION, job.job_id, job.primary_map_id,
                job.source_map_ids, job.transforms, algorithm.algorithm_id,
                algorithm.version, algorithm.sha256, (), now, datetime.now(timezone.utc),
            )
            definition = self.repository.commit_fusion_result(
                job.output_name, job.job_id, output, creators_by_id.values(),
                primary.frame_id, provenance,
            )
            self.fusion_finished.emit(definition, None)
        except Exception as exc:
            self.fusion_finished.emit(None, str(exc))

    def _on_fusion_finished(self, definition: object, error: object) -> None:
        self.fusion_button.setEnabled(True)
        self.fusion_button.setText("地图融合")
        if error:
            QMessageBox.critical(self, "地图融合失败", f"{error}\n临时输入已保留，可调整算法后重试。")
        elif isinstance(definition, MapDefinition):
            QMessageBox.information(self, "地图融合", f"融合地图“{definition.name}”已创建")
            self.show_detail(definition.map_id)

    def _toggle_edit(self) -> None:
        self.edit_mode = not self.edit_mode
        self.selected_map_ids.clear()
        self.edit_button.setText("取消编辑" if self.edit_mode else "编辑")
        self.new_button.setEnabled(not self.edit_mode)
        self.fusion_button.setEnabled(not self.edit_mode)
        self.algorithm_button.setEnabled(not self.edit_mode)
        for button in (
            self.rename_button, self.import_button, self.import_pgm_button,
            self.export_button, self.delete_button,
        ):
            button.setVisible(self.edit_mode)
            button.setEnabled(False)
        self._render_cards()

    def _set_selected(self, map_id: str, checked: bool) -> None:
        if checked:
            self.selected_map_ids.add(map_id)
        else:
            self.selected_map_ids.discard(map_id)
        one_selected = len(self.selected_map_ids) == 1
        selected = self.repository.map_by_id(next(iter(self.selected_map_ids))) if one_selected else None
        editable = bool(selected and selected.status != MapStatus.ERROR)
        self.rename_button.setEnabled(editable)
        self.import_button.setEnabled(editable)
        self.import_pgm_button.setEnabled(editable)
        self.export_button.setEnabled(editable)
        self.delete_button.setEnabled(bool(self.selected_map_ids))

    def _selected_id(self) -> str | None:
        return next(iter(self.selected_map_ids)) if len(self.selected_map_ids) == 1 else None

    def _rename_selected(self) -> None:
        map_id = self._selected_id()
        definition = self.repository.map_by_id(map_id) if map_id else None
        if not definition:
            return
        name, accepted = QInputDialog.getText(self, "修改地图名称", "地图名称", text=definition.name)
        if not accepted:
            return
        try:
            self.repository.rename(definition.map_id, name)
        except MapRepositoryError as exc:
            QMessageBox.critical(self, "地图重命名失败", str(exc))

    def _import_selected(self) -> None:
        map_id = self._selected_id()
        if not map_id:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "导入 PCD 点云", "", "PCD 点云 (*.pcd)")
        if not filename:
            return
        try:
            self.repository.import_pcd(map_id, filename)
        except MapRepositoryError as exc:
            QMessageBox.critical(self, "PCD 导入失败", str(exc))

    def _export_selected(self) -> None:
        map_id = self._selected_id()
        if map_id:
            self._export_map(map_id)

    def _import_pgm_selected(self) -> None:
        map_id = self._selected_id()
        if not map_id:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入 ROS PGM 地图", "", "ROS 地图 YAML (*.yaml *.yml)"
        )
        if not filename:
            return
        try:
            self.repository.import_pgm(map_id, filename)
        except MapRepositoryError as exc:
            QMessageBox.critical(self, "PGM 导入失败", str(exc))

    def _delete_selected(self) -> None:
        if not self.selected_map_ids:
            return
        reply = QMessageBox.question(
            self,
            "删除地图",
            f"确定将选中的 {len(self.selected_map_ids)} 张地图移入 map_server/.trash 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            for map_id in tuple(self.selected_map_ids):
                self.repository.delete(map_id)
        except MapRepositoryError as exc:
            QMessageBox.critical(self, "地图删除失败", str(exc))
        self.selected_map_ids.clear()
        if self.edit_mode:
            self._toggle_edit()

    def show_detail(self, map_id: str) -> None:
        if self.edit_mode:
            return
        definition = self.repository.map_by_id(map_id)
        if not definition:
            return
        self.current_map_id = map_id
        pcd_path = None
        pgm_yaml_path = None
        if definition.status == MapStatus.READY:
            try:
                pcd_path = self.repository.pcd_path(map_id)
            except MapRepositoryError:
                pcd_path = None
            try:
                pgm_yaml_path, _ = self.repository.pgm_paths(map_id)
            except MapRepositoryError:
                pgm_yaml_path = None
        self.detail_page.set_map(definition, pcd_path, pgm_yaml_path)
        self.detail_page.viewer.set_device_markers([])
        self.page_stack.setCurrentWidget(self.detail_page)
        self._offer_interrupted_session(map_id)

    def show_list(self) -> None:
        if self.mapping_service is not None and self.mapping_service.active:
            self.mapping_service.interrupt_mapping("返回地图列表")
        self.detail_page.viewer.clear()
        self.current_map_id = None
        self.page_stack.setCurrentWidget(self.list_page)

    def set_active(self, active: bool) -> None:
        if not active and self.mapping_service is not None and self.mapping_service.active:
            self.mapping_service.interrupt_mapping("切换主导航")

    def _toggle_mapping(self) -> None:
        if self.mapping_service is None or not self.current_map_id:
            return
        if self.mapping_service.active:
            self.mapping_service.stop_mapping()
            return
        definition = self.repository.map_by_id(self.current_map_id)
        if definition is None:
            return
        if definition.pcd_path:
            answer = QMessageBox.question(
                self, "重新建图", "该地图已有点云。新结果成功保存后将替换旧点云，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        creator_ids = {item.device_id.casefold() for item in definition.creator_devices}
        candidates = [
            item for item in self.devices
            if not creator_ids or item.device_id.casefold() in creator_ids
        ]
        if not candidates:
            QMessageBox.warning(self, "无法开始建图", "没有可用的已保存设备")
            return
        mode = "single"
        if len(candidates) >= 2:
            selected_mode, accepted = QInputDialog.getItem(
                self, "重新建图", "建图模式", ("单机建图", "多机建图"), 0, False,
            )
            if not accepted:
                return
            mode = "multi" if selected_mode == "多机建图" else "single"
        dialog = MappingSetupDialog(
            mode, candidates, self.fusion_repository.algorithms(enabled_only=True),
            name=definition.name, name_editable=False, parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_ids = set(dialog.selected_device_ids())
        selected_devices = [item for item in candidates if item.device_id in selected_ids]
        try:
            self.mapping_service.start_job(
                definition, selected_devices, dialog.primary_device_id(),
                dialog.transforms(), dialog.algorithm_id(),
            )
        except (RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "建图启动失败", str(exc))

    def _on_mapping_updated(self, snapshot: MapBuildingSessionSnapshot) -> None:
        if snapshot.map_id == self.current_map_id:
            self.detail_page.update_mapping(snapshot)

    def _on_mapping_preview(self, session_id: str, points: object, bounds: object) -> None:
        snapshot = self.mapping_service.current_snapshot if self.mapping_service else None
        if snapshot and snapshot.session_id == session_id and snapshot.map_id == self.current_map_id:
            self.detail_page.viewer.set_live_points(points, bounds)

    def _on_mapping_completed(self, definition: MapDefinition) -> None:
        if definition.map_id == self.current_map_id:
            self.detail_page.viewer.clear_live_points()
            self.show_detail(definition.map_id)

    def _on_mapping_failed(self, message: str) -> None:
        if self.page_stack.currentWidget() == self.detail_page:
            self.detail_page.mapping_state.setText(message)

    def _on_mapping_degraded(self, snapshot: object) -> None:
        failed = [item for item in snapshot.device_sessions if item.state == "failed"]
        if not failed or self.page_stack.currentWidget() != self.detail_page:
            return
        box = QMessageBox(self)
        box.setWindowTitle("联合建图链路中断")
        box.setText(snapshot.message)
        failed_session = failed[0]
        if failed_session.device_id.casefold() != snapshot.primary_device_id.casefold():
            keep = box.addButton("剔除该设备并继续", QMessageBox.ButtonRole.AcceptRole)
        else:
            keep = None
        stop = box.addButton("中止全部", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        try:
            if keep is not None and box.clickedButton() == keep:
                self.mapping_service.continue_without_device(failed_session.device_id)
            elif box.clickedButton() == stop:
                self.mapping_service.interrupt_mapping("用户中止降级建图任务")
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "建图状态处理失败", str(exc))

    def _on_mapping_availability(self, available: bool, message: str) -> None:
        self.detail_page.set_mapping_available(available, message)

    def _offer_interrupted_session(self, map_id: str) -> None:
        if self.mapping_service is None or self.mapping_service.active:
            return
        jobs = self.repository.interrupted_mapping_jobs(map_id)
        if jobs:
            job = jobs[0]
            box = QMessageBox(self)
            box.setWindowTitle("发现临时联合建图结果")
            box.setText("检测到多设备建图临时点云。可选择算法融合保存，或丢弃整个临时任务。")
            save_job = box.addButton("选择算法并保存", QMessageBox.ButtonRole.AcceptRole)
            discard_job = box.addButton("丢弃", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("稍后处理", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            try:
                if box.clickedButton() == save_job:
                    algorithms = self.fusion_repository.algorithms(enabled_only=True)
                    labels = [f"{item.display_name} · v{item.version}" for item in algorithms]
                    selected, accepted = QInputDialog.getItem(
                        self, "选择融合算法", "算法", labels, 0, False,
                    )
                    if accepted:
                        algorithm = algorithms[labels.index(selected)]
                        self.mapping_service.save_interrupted_job(
                            map_id, str(job["job_id"]), algorithm.algorithm_id,
                        )
                        self.show_detail(map_id)
                elif box.clickedButton() == discard_job:
                    self.repository.discard_mapping_job(map_id, str(job["job_id"]))
            except (MapRepositoryError, MapFusionError, RuntimeError, ValueError) as exc:
                QMessageBox.critical(self, "临时联合建图处理失败", str(exc))
            return
        sessions = self.repository.interrupted_sessions(map_id)
        if not sessions:
            return
        session = sessions[0]
        box = QMessageBox(self)
        box.setWindowTitle("发现临时建图结果")
        box.setText("检测到上次中断后保留的点云。可保存为正式地图或丢弃临时结果。")
        save = box.addButton("保存临时结果", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("丢弃", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("稍后处理", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        try:
            if box.clickedButton() == save:
                self.mapping_service.save_interrupted_session(map_id, str(session["session_id"]))
                self.show_detail(map_id)
            elif box.clickedButton() == discard:
                self.repository.discard_mapping_session(map_id, str(session["session_id"]))
        except MapRepositoryError as exc:
            QMessageBox.critical(self, "临时结果处理失败", str(exc))

    def _reload_current_map(self) -> None:
        if self.current_map_id:
            self.repository.load_all()
            self.show_detail(self.current_map_id)

    def _export_current_map(self) -> None:
        if self.current_map_id:
            self._export_map(self.current_map_id)

    def _export_map(self, map_id: str) -> None:
        definition = self.repository.map_by_id(map_id)
        if not definition:
            return
        timestamp = definition.created_at.astimezone().strftime("%Y%m%d_%H%M%S")
        default_name = f"{definition.name}_{timestamp}.zip"
        filename, _ = QFileDialog.getSaveFileName(self, "下载地图", default_name, "ZIP 压缩包 (*.zip)")
        if not filename:
            return
        if not filename.lower().endswith(".zip"):
            filename += ".zip"
        try:
            self.repository.export_zip(map_id, filename)
        except MapRepositoryError as exc:
            QMessageBox.critical(self, "地图下载失败", str(exc))

    def _on_maps_updated(self, maps: object) -> None:
        self.maps = list(maps)
        self.selected_map_ids.intersection_update(item.map_id for item in self.maps)
        self._render_cards()

    def _on_devices_updated(self, devices: object) -> None:
        self.devices = list(devices)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.page_stack.currentWidget() == self.list_page:
            self._render_cards()
