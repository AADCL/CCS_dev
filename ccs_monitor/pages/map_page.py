from __future__ import annotations

from pathlib import Path
from typing import Callable
import math
import json
import threading
import uuid
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QEvent, QSettings, QTimer, Signal, Qt, QObject
from PySide6.QtGui import QColor, QFontDatabase, QLinearGradient, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QButtonGroup,
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
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QStackedLayout,
    QStackedWidget,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..data_source import DeviceDataSource
from ..map_repository import MapRepository, MapRepositoryError
from ..map_building import MapBuildingSessionSnapshot
from ..map_building_v2 import RemoteMappingSnapshot
from ..map_fusion import MapFusionError, MapFusionRepository, MapFusionRunner
from ..models import (
    ConnectionStatus,
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
    PgmDownloadSnapshot,
    PgmFusionJob,
    PgmFusionProvenance,
    PgmFusionSource,
    PgmTransform2D,
    PoseTelemetry,
    RelocalizationStatus,
    TaskStatus,
    UdpLinkStatus,
)
from ..pgm_map import PcdToPgmOptions, PgmMapError, PgmMapLoader
from ..pgm_fusion import PgmFusionEngine, PgmFusionError, pcd_sha256
from ..point_cloud import MapPointCloudLoader, PointCloudError
from ..relocalization_artifacts import RelocalizationArtifactError
from ..styles import ThemeMode, ThemePalette, theme_palette
from ..srt_video import SrtVideoWidget
from ..widgets import NoButtonDoubleSpinBox, NoButtonSpinBox


STATUS_TEXT = {
    MapStatus.WAITING_FOR_PCD: "等待导入地图",
    MapStatus.READY: "地图已就绪",
    MapStatus.ERROR: "地图数据异常",
}

MAP_PAN_DRAG_SPEED = 3.0

RAINBOW_STOPS = np.asarray((
    (1.0, 0.0, 0.0, 1.0),
    (1.0, 1.0, 0.0, 1.0),
    (0.0, 1.0, 0.0, 1.0),
    (0.0, 1.0, 1.0, 1.0),
    (0.0, 0.0, 1.0, 1.0),
    (0.5, 0.0, 1.0, 1.0),
), dtype=np.float32)


def height_rainbow_colors(
    points: np.ndarray,
    minimum_z: float | None = None,
    maximum_z: float | None = None,
) -> np.ndarray:
    """Return red-low to violet-high per-point colors for finite XYZ points."""
    array = np.asarray(points, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("点云必须为 Nx3 数组")
    if not len(array):
        return np.empty((0, 4), dtype=np.float32)
    finite = np.isfinite(array).all(axis=1)
    colors = np.zeros((len(array), 4), dtype=np.float32)
    if not finite.any():
        return colors
    z_values = array[finite, 2]
    low = float(np.min(z_values)) if minimum_z is None else float(minimum_z)
    high = float(np.max(z_values)) if maximum_z is None else float(maximum_z)
    if not math.isfinite(low) or not math.isfinite(high):
        low, high = float(np.min(z_values)), float(np.max(z_values))
    if high <= low:
        normalized = np.full(len(z_values), 0.5, dtype=np.float32)
    else:
        normalized = np.clip((z_values - low) / (high - low), 0.0, 1.0)
    scaled = normalized * (len(RAINBOW_STOPS) - 1)
    lower = np.floor(scaled).astype(np.intp)
    upper = np.minimum(lower + 1, len(RAINBOW_STOPS) - 1)
    ratio = (scaled - lower).reshape(-1, 1)
    colors[finite] = RAINBOW_STOPS[lower] * (1.0 - ratio) + RAINBOW_STOPS[upper] * ratio
    return colors


def _rpy_rotation_matrix(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    roll, pitch, yaw = np.radians((roll_deg, pitch_deg, yaw_deg))
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.asarray((
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp, cp * sr, cp * cr),
    ), dtype=np.float64)


def transform_device_pose(pose: PoseTelemetry, transform: MapTransform) -> PoseTelemetry:
    """Apply target-frame-from-source rigid transform to a UDP device pose."""
    outer = _rpy_rotation_matrix(*transform.rotation_rpy_deg)
    inner = _rpy_rotation_matrix(pose.roll, pose.pitch, pose.yaw)
    position = outer @ np.asarray((pose.x, pose.y, pose.z), dtype=np.float64)
    position += np.asarray(transform.translation_m, dtype=np.float64)
    rotation = outer @ inner
    pitch = math.asin(float(np.clip(-rotation[2, 0], -1.0, 1.0)))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = 0.0
        yaw = math.atan2(-rotation[0, 1], rotation[1, 1])
    return PoseTelemetry(
        float(position[0]), float(position[1]), float(position[2]),
        math.degrees(roll), math.degrees(pitch), math.degrees(yaw),
        pose.sample_age_seconds,
    )


def transform_pose_by_binding(pose: PoseTelemetry, transform) -> PoseTelemetry:
    qx, qy, qz, qw = transform.qx, transform.qy, transform.qz, transform.qw
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    matrix = np.asarray((
        (1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)),
        (2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)),
        (2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)),
    ))
    position = matrix @ np.asarray((pose.x, pose.y, pose.z))
    rotation = matrix @ _rpy_rotation_matrix(pose.roll, pose.pitch, pose.yaw)
    pitch = math.asin(float(np.clip(-rotation[2, 0], -1.0, 1.0)))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = 0.0
        yaw = math.atan2(-rotation[0, 1], rotation[1, 1])
    return PoseTelemetry(
        float(position[0] + transform.x), float(position[1] + transform.y),
        float(position[2] + transform.z),
        math.degrees(roll), math.degrees(pitch), math.degrees(yaw),
        pose.sample_age_seconds,
    )


def bound_map_pose(source, telemetry, device_id: str, map_id: str) -> PoseTelemetry | None:
    profile = source.profile(device_id)
    binding = next((
        item for item in getattr(profile, "map_bindings", ()) if item.map_id == map_id
    ), None)
    if binding is None or telemetry is None:
        return None
    pose = getattr(telemetry, binding.pose_source, None)
    if pose is None or (
        pose.sample_age_seconds is not None and pose.sample_age_seconds > 2.0
    ):
        return None
    return transform_pose_by_binding(pose, binding.map_from_odom)


def device_task_status_text(
    device: DeviceSnapshot,
    remote: RemoteMappingSnapshot | None = None,
) -> str:
    if remote is not None and remote.device_id.casefold() == device.device_id.casefold():
        return {
            "preparing": "协商", "ready": "准备建图", "starting": "准备建图",
            "mapping": "建图中", "stopping": "成果处理中",
            "generating": "成果处理中", "downloading": "成果处理中",
            "validating": "成果处理中", "completed": "建图完成",
            "warning": "建图异常", "failed": "建图失败", "aborting": "正在取消",
            "aborted": "已取消", "cancelled": "已取消",
        }.get(remote.state, "等待")
    return {
        TaskStatus.STANDBY: "等待", TaskStatus.EXECUTING: "执行任务",
        TaskStatus.PAUSED: "已暂停", TaskStatus.COMPLETED: "已完成",
        TaskStatus.UNKNOWN: "未知",
    }.get(device.task_status, "未知")


class MapViewerSettings(QObject):
    changed = Signal()

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("CCS", "CCS Device Monitor")
        self.grid_visible = self._bool("map_viewer/grid_visible", True)
        self.grid_spacing = self._float("map_viewer/grid_spacing", 1.0, 0.01, 1000.0)
        self.grid_opacity = self._float("map_viewer/grid_opacity", 35.0, 10.0, 100.0)
        self.coordinates_visible = self._bool("map_viewer/coordinates_visible", True)
        self.tick_spacing = self._float("map_viewer/tick_spacing", 5.0, 0.01, 1000.0)
        self.cursor_visible = self._bool("map_viewer/cursor_visible", True)

    def update(self, **values) -> None:
        changed = False
        for name, value in values.items():
            if not hasattr(self, name) or getattr(self, name) == value:
                continue
            setattr(self, name, value)
            self.settings.setValue(f"map_viewer/{name}", value)
            changed = True
        if changed:
            self.settings.sync()
            self.changed.emit()

    def _bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _float(self, key: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self.settings.value(key, default))
        except (TypeError, ValueError):
            value = default
        return min(maximum, max(minimum, value))


MAP_VIEWER_SETTINGS = MapViewerSettings()


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


def unproject_screen_to_plane(
    transform: object,
    x: float,
    y: float,
    plane_z: float = 0.0,
) -> tuple[float, float] | None:
    """Convert canvas coordinates to a horizontal map plane."""

    def cartesian(value: object) -> np.ndarray | None:
        mapped = np.asarray(value, dtype=np.float64).reshape(-1)
        if mapped.size < 3 or not np.isfinite(mapped).all():
            return None
        if mapped.size >= 4:
            w = mapped[3]
            if abs(w) <= 1e-12:
                return None
            mapped = mapped[:3] / w
        else:
            mapped = mapped[:3]
        return mapped if np.isfinite(mapped).all() else None

    near = cartesian(transform.imap((x, y, 0.0, 1.0)))
    far = cartesian(transform.imap((x, y, 1.0, 1.0)))
    if near is None or far is None:
        return None
    direction = far - near
    if abs(direction[2]) <= 1e-12:
        return None
    distance = (float(plane_z) - near[2]) / direction[2]
    if distance < 0.0:
        return None
    intersection = near + distance * direction
    if not np.isfinite(intersection).all():
        return None
    return float(intersection[0]), float(intersection[1])


def pick_mode_zoom_distance(distance: float, wheel_delta: int) -> float:
    if not wheel_delta:
        return float(distance)
    factor = 0.85 if wheel_delta > 0 else 1.0 / 0.85
    return float(np.clip(float(distance) * factor, 0.1, 1_000_000.0))


def cursor_anchored_camera_center(center, before, after) -> tuple[float, float, float]:
    value = np.asarray(center, dtype=np.float64).copy()
    value[:2] += np.asarray(before, dtype=np.float64) - np.asarray(after, dtype=np.float64)
    return float(value[0]), float(value[1]), float(value[2])


class MiddlePanTurntableCameraMixin:
    """Provide deterministic map panning and top-down yaw gestures."""

    pan_speed_multiplier = MAP_PAN_DRAG_SPEED
    planar_rotation = False
    rotation_degrees_per_pixel = 0.5

    def viewbox_mouse_event(self, event) -> None:
        event_type = getattr(event, "type", "")
        mouse_event = getattr(event, "mouse_event", event)
        position = np.asarray(getattr(mouse_event, "pos", (0.0, 0.0)), dtype=np.float64)
        button = getattr(event, "button", getattr(mouse_event, "button", None))
        buttons = tuple(getattr(event, "buttons", ()))
        if self.interactive and event_type == "mouse_press" and button == 2:
            self._ccs_pan_start = (position[:2].copy(), tuple(self.center))
            event.handled = True
            return
        if self.interactive and self.planar_rotation and event_type == "mouse_press" and button == 1:
            self._ccs_rotation_start = (position[:2].copy(), float(self.azimuth))
            event.handled = True
            return
        if (
            not event.handled
            and self.interactive
            and event_type == "mouse_move"
            and 2 in buttons
        ):
            start = getattr(self, "_ccs_pan_start", None)
            if start is None:
                press_event = getattr(event, "press_event", None)
                if press_event is None:
                    press_event = getattr(mouse_event, "press_event", None)
                press_position = getattr(press_event, "pos", position)
                start = (np.asarray(press_position, dtype=np.float64)[:2], tuple(self.center))
                self._ccs_pan_start = start
            self.center = calculate_turntable_pan(
                self,
                start[0],
                position,
                self._viewbox.size,
                start[1],
                self.pan_speed_multiplier,
            )
            self.view_changed()
            event.handled = True
            return
        if (
            not event.handled
            and self.interactive
            and self.planar_rotation
            and event_type == "mouse_move"
            and 1 in buttons
        ):
            start = getattr(self, "_ccs_rotation_start", None)
            if start is None:
                press_event = getattr(event, "press_event", None)
                if press_event is None:
                    press_event = getattr(mouse_event, "press_event", None)
                press_position = getattr(press_event, "pos", position)
                start = (np.asarray(press_position, dtype=np.float64)[:2], float(self.azimuth))
                self._ccs_rotation_start = start
            self.azimuth = start[1] + float(position[0] - start[0][0]) * self.rotation_degrees_per_pixel
            self.elevation = 90
            self.view_changed()
            event.handled = True
            return
        if event_type == "mouse_release":
            if button == 2:
                self._ccs_pan_start = None
            elif button == 1:
                self._ccs_rotation_start = None
        super().viewbox_mouse_event(event)


class MapCard(QFrame):
    double_clicked = Signal(str)
    selection_changed = Signal(str, bool)

    def __init__(self, definition: MapDefinition, active: bool = False) -> None:
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
        status = QLabel(("当前激活 · " if active else "") + STATUS_TEXT[definition.status])
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
            ("single", "单机遥控建图", "创建任务后先与端侧协商点云、位姿和成果能力"),
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
                spin = NoButtonDoubleSpinBox()
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
        default_algorithm = next((item for item in algorithms if item.is_default), None)
        if default_algorithm is None and algorithms:
            default_algorithm = algorithms[0]
        self._default_algorithm_id = default_algorithm.algorithm_id if default_algorithm else ""
        self.algorithm_combo: QComboBox | None = None
        if mode == "multi":
            self.algorithm_combo = QComboBox()
        for algorithm in algorithms:
            if self.algorithm_combo is not None:
                self.algorithm_combo.addItem(
                    f"{algorithm.display_name} · v{algorithm.version}", algorithm.algorithm_id
                )
                if algorithm.is_default:
                    self.algorithm_combo.setCurrentIndex(self.algorithm_combo.count() - 1)
        if self.algorithm_combo is not None:
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
        self.start_button = buttons.addButton("创建建图任务", QDialogButtonBox.ButtonRole.AcceptRole)
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
        valid_algorithm = bool(self._default_algorithm_id) if self.mode == "single" else (
            self.algorithm_combo is not None and self.algorithm_combo.currentData() is not None
        )
        valid = valid_name and valid_devices and valid_algorithm
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
        if self.mode == "single":
            return self._default_algorithm_id
        return str(self.algorithm_combo.currentData()) if self.algorithm_combo is not None else ""


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
        self.sync_pgm = QCheckBox("同步融合 PGM 图")
        self.sync_pgm.setToolTip("使用相同主从外参融合所选地图携带的 ROS PGM，并绑定到新点云地图")
        form.addRow("栅格图层", self.sync_pgm)
        root.addLayout(form)
        root.addWidget(QLabel("选择至少两张具有有效 PCD 的地图"))
        self.map_list = QListWidget()
        for definition in maps:
            layers = "PCD + PGM" if definition.pgm else "仅 PCD"
            item = QListWidgetItem(
                f"{definition.name} · {definition.point_count:,} 点 · {definition.frame_id} · {layers}"
            )
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
        self.sync_pgm.toggled.connect(self._validate)
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
        selected_ids = self.selected_map_ids()
        selected_maps = [item for item in self.maps if item.map_id in selected_ids]
        pgm_missing = self.sync_pgm.isChecked() and any(item.pgm is None for item in selected_maps)
        valid = (
            bool(self.name_input.text().strip()) and len(selected_ids) >= 2
            and self.primary_combo.currentData() is not None
            and self.algorithm_combo.currentData() is not None
            and not pgm_missing
        )
        self.fuse_button.setEnabled(valid)
        if pgm_missing:
            message = "同步融合 PGM 要求所有选中的地图都携带有效 PGM 图层"
        else:
            message = "请填写名称、选择至少两张地图并指定主地图"
        self.validation.setText("" if valid else message)

    def job(self) -> MapFusionJob:
        return MapFusionJob(
            uuid.uuid4().hex, self.name_input.text().strip(), self.selected_map_ids(),
            str(self.primary_combo.currentData()), self.transform_table.transforms(),
            str(self.algorithm_combo.currentData()), self.sync_pgm.isChecked(),
        )


class PgmFusionDialog(QDialog):
    start_requested = Signal(object)
    retry_requested = Signal()
    remove_requested = Signal()

    def __init__(self, maps: list[MapDefinition], devices: list[DeviceSnapshot],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.maps = maps
        self.devices = devices
        self._running = False
        self.setWindowTitle("端侧 PGM 下载与融合")
        self.setMinimumSize(900, 620)
        root = QVBoxLayout(self)
        title = QLabel("PGM 栅格融合")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        note = QLabel("外参方向：目标 PCD frame <- 来源 PGM frame；至少两个图层且至少一个来自端侧。")
        note.setObjectName("muted")
        root.addWidget(note)
        form = QFormLayout()
        self.target_combo = QComboBox()
        for definition in maps:
            self.target_combo.addItem(f"{definition.name} · {definition.frame_id}", definition.map_id)
        self.include_existing = QCheckBox("将目标地图已有 PGM 作为单位变换来源")
        self.resolution = NoButtonDoubleSpinBox()
        self.resolution.setRange(0.001, 1000.0)
        self.resolution.setDecimals(3)
        self.resolution.setValue(0.05)
        self.resolution.setSuffix(" m/px")
        self.resolution_user_modified = False
        form.addRow("目标点云地图", self.target_combo)
        form.addRow("已有图层", self.include_existing)
        form.addRow("输出分辨率", self.resolution)
        root.addLayout(form)
        self.table = QTableWidget(len(devices), 7)
        self.table.setHorizontalHeaderLabels(("选择", "来源设备", "source_map_id", "X (m)", "Y (m)", "Yaw (deg)", "状态"))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._controls = {}
        for row, device in enumerate(devices):
            selected = QCheckBox()
            selected.setEnabled(bool(device.ip_address))
            selected.setToolTip("" if device.ip_address else "设备缺少有效 IP，不能下载")
            name = QLabel(f"{device.device_name}\n{device.device_id} · {device.ip_address or '无 IP'}")
            source_map = QLineEdit()
            source_map.setPlaceholderText("端侧地图 ID")
            x, y, yaw = self._transform_input(), self._transform_input(), self._transform_input()
            status = QLabel("待选择" if device.ip_address else "缺少 IP")
            for column, widget in enumerate((selected, name, source_map, x, y, yaw, status)):
                self.table.setCellWidget(row, column, widget)
            self._controls[device.device_id] = (selected, source_map, x, y, yaw, status)
            selected.toggled.connect(self._validate)
            source_map.textChanged.connect(self._validate)
        root.addWidget(self.table, 1)
        self.progress = QLabel("尚未开始下载")
        self.progress.setObjectName("muted")
        root.addWidget(self.progress)
        self.validation = QLabel()
        self.validation.setObjectName("validationError")
        root.addWidget(self.validation)
        actions = QHBoxLayout()
        self.retry_button = QPushButton("重试当前来源")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self.retry_requested)
        self.remove_button = QPushButton("移除当前来源")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self.remove_requested)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.start_button = QPushButton("开始下载并融合")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._start)
        actions.addWidget(cancel)
        actions.addWidget(self.start_button)
        root.addLayout(actions)
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        self.include_existing.toggled.connect(self._validate)
        self.resolution.valueChanged.connect(self._resolution_edited)
        self._target_changed()

    @staticmethod
    def _transform_input() -> NoButtonDoubleSpinBox:
        control = NoButtonDoubleSpinBox()
        control.setRange(-1_000_000.0, 1_000_000.0)
        control.setDecimals(3)
        return control

    def target(self) -> MapDefinition:
        map_id = str(self.target_combo.currentData())
        return next(item for item in self.maps if item.map_id == map_id)

    def selected_remote_sources(self) -> tuple[PgmFusionSource, ...]:
        result = []
        for device in self.devices:
            selected, source_map, x, y, yaw, _ = self._controls[device.device_id]
            if selected.isChecked():
                result.append(PgmFusionSource(
                    source_id=f"device:{device.device_id}", source_map_id=source_map.text().strip(),
                    transform=PgmTransform2D(x.value(), y.value(), yaw.value()),
                    device_id=device.device_id, device_name=device.device_name,
                    device_ip=device.ip_address,
                ))
        return tuple(result)

    def output_resolution(self) -> float:
        return self.resolution.value()

    def set_minimum_resolution(self, value: float) -> None:
        self.resolution.blockSignals(True)
        self.resolution.setMinimum(value)
        if not self.resolution_user_modified or self.resolution.value() < value:
            self.resolution.setValue(value)
        self.resolution.blockSignals(False)

    def _resolution_edited(self) -> None:
        self.resolution_user_modified = True

    def update_snapshot(self, snapshot: PgmDownloadSnapshot) -> None:
        controls = self._controls.get(snapshot.device_id)
        if controls:
            controls[-1].setText(snapshot.message)
        self.progress.setText(
            f"{snapshot.message} · {snapshot.received_chunks}/{snapshot.chunk_count} 分片 · "
            f"补传 {snapshot.retransmission_rounds} 轮"
        )
        failed = snapshot.state == "failed"
        self.retry_button.setEnabled(failed)
        self.remove_button.setEnabled(failed)

    def set_finishing(self, message: str) -> None:
        self.progress.setText(message)
        self.retry_button.setEnabled(False)
        self.remove_button.setEnabled(False)

    def _target_changed(self) -> None:
        target = self.target()
        self.include_existing.setEnabled(target.pgm is not None)
        if target.pgm is None:
            self.include_existing.setChecked(False)
        self._validate()

    def _validate(self) -> None:
        remote = self.selected_remote_sources()
        valid = bool(remote) and len(remote) + int(self.include_existing.isChecked()) >= 2
        valid = valid and all(item.source_map_id for item in remote)
        self.start_button.setEnabled(not self._running and valid)
        self.validation.setText("" if valid else "至少选择一个端侧来源、合计两个图层，并填写全部 source_map_id")

    def _start(self) -> None:
        self._running = True
        self.start_button.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.include_existing.setEnabled(False)
        for controls in self._controls.values():
            for control in controls[:-1]:
                control.setEnabled(False)
        self.start_requested.emit(self)


class PgmGenerationDialog(QDialog):
    def __init__(self, definition: MapDefinition, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.definition = definition
        self.setWindowTitle("从 PCD 生成 PGM")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        title = QLabel(f"生成占据栅格 · {definition.name}")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel("点云按 XY 平面投影；未命中区域默认保持未知。")
        description.setObjectName("muted")
        root.addWidget(description)
        form = QFormLayout()
        self.resolution = self._double(0.001, 1000.0, 0.05, 3, " m/px")
        minimum_z = definition.bounds.min_z if definition.bounds else 0.0
        maximum_z = definition.bounds.max_z if definition.bounds else 2.0
        depth = maximum_z - minimum_z
        default_min_z = minimum_z + (0.15 if depth >= 0.15 else 0.0)
        self.min_z = self._double(-1_000_000.0, 1_000_000.0, default_min_z, 3, " m")
        self.max_z = self._double(-1_000_000.0, 1_000_000.0, maximum_z, 3, " m")
        self.padding = self._double(0.0, 10_000.0, 0.5, 3, " m")
        self.min_points = NoButtonSpinBox()
        self.min_points.setRange(1, 1_000_000)
        self.min_points.setValue(1)
        self.inflation = self._double(0.0, 10_000.0, 0.0, 3, " m")
        self.empty_cell = QComboBox()
        self.empty_cell.addItem("未知（unknown）", "unknown")
        self.empty_cell.addItem("空闲（free）", "free")
        self.free_threshold = self._double(0.0, 1.0, 0.196, 3, "")
        self.occupied_threshold = self._double(0.0, 1.0, 0.65, 3, "")
        for label, control in (
            ("分辨率", self.resolution),
            ("最低投影高度", self.min_z),
            ("最高投影高度", self.max_z),
            ("地图边缘留白", self.padding),
            ("单栅格最少点数", self.min_points),
            ("障碍膨胀半径", self.inflation),
            ("未命中栅格", self.empty_cell),
            ("空闲阈值", self.free_threshold),
            ("占据阈值", self.occupied_threshold),
        ):
            form.addRow(label, control)
        root.addLayout(form)
        self.validation = QLabel()
        self.validation.setObjectName("validationError")
        root.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.generate_button = buttons.addButton("生成 PGM", QDialogButtonBox.ButtonRole.AcceptRole)
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        for control in (
            self.resolution, self.min_z, self.max_z, self.padding, self.min_points,
            self.inflation, self.free_threshold, self.occupied_threshold,
        ):
            control.valueChanged.connect(self._validate)
        self._validate()

    @staticmethod
    def _double(minimum: float, maximum: float, value: float,
                decimals: int, suffix: str) -> NoButtonDoubleSpinBox:
        control = NoButtonDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSuffix(suffix)
        return control

    def _validate(self) -> None:
        message = ""
        if self.min_z.value() > self.max_z.value():
            message = "最低投影高度不能大于最高投影高度"
        elif self.free_threshold.value() >= self.occupied_threshold.value():
            message = "空闲阈值必须小于占据阈值"
        self.validation.setText(message)
        self.generate_button.setEnabled(not message)

    def options(self) -> PcdToPgmOptions:
        return PcdToPgmOptions(
            resolution=self.resolution.value(),
            min_z=self.min_z.value(),
            max_z=self.max_z.value(),
            padding_m=self.padding.value(),
            min_points_per_cell=self.min_points.value(),
            inflation_radius_m=self.inflation.value(),
            empty_cell=str(self.empty_cell.currentData()),
            occupied_thresh=self.occupied_threshold.value(),
            free_thresh=self.free_threshold.value(),
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


class RainbowHeightBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(82, 8)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        for index, color in enumerate(RAINBOW_STOPS):
            gradient.setColorAt(index / (len(RAINBOW_STOPS) - 1), QColor.fromRgbF(*color))
        painter.fillRect(self.rect(), gradient)


class HeightColorLegend(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.minimum_label = QLabel("")
        self.minimum_label.setObjectName("muted")
        self.bar = RainbowHeightBar()
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(0, 100)
        self.threshold.setValue(0)
        self.threshold.setFixedWidth(72)
        self.threshold.setToolTip("显示该高度以下点云")
        self.maximum_label = QLabel("")
        self.maximum_label.setObjectName("muted")
        layout.addWidget(self.minimum_label)
        layout.addWidget(self.bar)
        layout.addWidget(self.threshold)
        layout.addWidget(self.maximum_label)
        self.setVisible(False)

    def set_range(self, minimum_z: float | None, maximum_z: float | None) -> None:
        visible = minimum_z is not None and maximum_z is not None
        self.setVisible(visible)
        if visible:
            self.minimum_label.setText(f"{minimum_z:.1f}m")
            self.maximum_label.setText(f"{maximum_z:.1f}m")
            self.threshold.setToolTip("滑块从左向右移动，隐藏更高点云")


class MapDeviceCard(QFrame):
    selected = Signal(str)
    video_requested = Signal(str, bool)
    map_download_requested = Signal(str)
    relocalization_requested = Signal(str)

    def __init__(self, device: DeviceSnapshot) -> None:
        super().__init__()
        self.setObjectName("mapOnlineDeviceCard")
        self.device = device
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 10)
        layout.setSpacing(6)
        self.name_button = QPushButton()
        self.name_button.setObjectName("mapDeviceSelectButton")
        self.name_button.setCheckable(True)
        self.name_button.clicked.connect(lambda: self.selected.emit(self.device.device_id))
        layout.addWidget(self.name_button)
        self.identity = QLabel()
        self.identity.setObjectName("muted")
        self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        self.task_state = QLabel()
        self.task_state.setObjectName("statusPill")
        layout.addWidget(self.task_state)
        self.localization_state = QLabel("定位状态：未知空间")
        self.localization_state.setObjectName("statusPill")
        self.localization_state.setWordWrap(True)
        layout.addWidget(self.localization_state)
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.download_map_button = QPushButton("下发地图")
        self.download_map_button.clicked.connect(
            lambda: self.map_download_requested.emit(self.device.device_id)
        )
        self.relocalization_button = QPushButton("启动重定位")
        self.relocalization_button.setObjectName("primaryButton")
        self.relocalization_button.clicked.connect(
            lambda: self.relocalization_requested.emit(self.device.device_id)
        )
        action_row.addWidget(self.download_map_button)
        action_row.addWidget(self.relocalization_button)
        layout.addLayout(action_row)
        battery_row = QHBoxLayout()
        battery_row.setSpacing(7)
        self.battery = QProgressBar()
        self.battery.setRange(0, 100)
        self.battery.setTextVisible(False)
        self.battery.setFixedHeight(8)
        self.battery_text = QLabel("--")
        self.battery_text.setObjectName("muted")
        battery_row.addWidget(self.battery, 1)
        battery_row.addWidget(self.battery_text)
        layout.addLayout(battery_row)
        self.frame_name = QLabel()
        self.frame_name.setObjectName("muted")
        self.frame_name.setWordWrap(True)
        layout.addWidget(self.frame_name)
        self.frame_note = QLabel()
        self.frame_note.setObjectName("mapFrameNote")
        self.frame_note.setWordWrap(True)
        self.frame_note.setVisible(False)
        layout.addWidget(self.frame_note)
        self.video = SrtVideoWidget(parent=self)
        self.video.set_collapsible(True)
        self.video.setMinimumWidth(320)
        self.video.stream_switch.toggled.connect(
            lambda enabled: self.video_requested.emit(self.device.device_id, bool(enabled))
        )
        layout.addWidget(self.video)
        self.update_snapshot(device, None)

    def update_snapshot(
        self,
        device: DeviceSnapshot,
        remote: RemoteMappingSnapshot | None,
        relocalization=None,
        map_complete: bool = False,
    ) -> None:
        self.device = device
        self.name_button.setText(device.device_name)
        type_name = device.device_type_name or device.device_type
        self.identity.setText(f"类型 {type_name}  ·  ID {device.device_id}")
        self.task_state.setText(device_task_status_text(device, remote))
        if device.battery_percent is None:
            self.battery.setValue(0)
            self.battery.setEnabled(False)
            self.battery_text.setText("--")
        else:
            battery = min(100, max(0, round(device.battery_percent)))
            self.battery.setEnabled(True)
            self.battery.setValue(battery)
            self.battery.setObjectName("lowBattery" if battery < 20 else "")
            self.battery.style().unpolish(self.battery)
            self.battery.style().polish(self.battery)
            self.battery_text.setText(f"{battery}%")
        frame_id = (
            remote.frame_id
            if remote is not None and remote.device_id.casefold() == device.device_id.casefold()
            else device.frame_id
        )
        self.frame_name.setText(f"坐标系 {frame_id or '未上报'}")
        self.video.set_device(device)
        status = getattr(relocalization, "status", RelocalizationStatus.UNKNOWN_SPACE)
        message = getattr(relocalization, "message", "未知空间")
        self.localization_state.setText(f"定位状态：{message}")
        busy = bool(
            remote is not None and remote.device_id.casefold() == device.device_id.casefold()
            and remote.state in {"preparing", "starting", "mapping", "warning", "degraded", "saving"}
        )
        can_download = bool(getattr(relocalization, "can_download", False))
        can_start = bool(getattr(relocalization, "can_start", False))
        can_submit = bool(getattr(relocalization, "can_submit_pose", False))
        self.download_map_button.setEnabled(map_complete and can_download and not busy)
        self.relocalization_button.setEnabled((can_start or can_submit) and not busy)
        labels = {
            RelocalizationStatus.STACK_STARTING: "启动中...",
            RelocalizationStatus.AWAITING_POSE: "开始重定位",
            RelocalizationStatus.RELOCALIZING: "重定位中...",
            RelocalizationStatus.SUCCEEDED: "重新定位",
        }
        if status == RelocalizationStatus.FAILED:
            label = "重新开始重定位" if can_submit else "重新启动重定位"
        else:
            label = labels.get(status, "启动重定位")
        self.relocalization_button.setText(label)
        if status == RelocalizationStatus.UNSUPPORTED:
            self.download_map_button.setEnabled(False)
            self.relocalization_button.setEnabled(False)

    def set_selected(self, selected: bool) -> None:
        self.name_button.blockSignals(True)
        self.name_button.setChecked(selected)
        self.name_button.blockSignals(False)
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_frame_note(self, note: str) -> None:
        self.frame_note.setText(note)
        self.frame_note.setVisible(bool(note))

    def stop_video(self) -> None:
        self.video.stop_stream()


class MapOnlineDevicePanel(QFrame):
    device_selected = Signal(str)
    map_download_requested = Signal(str)
    relocalization_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mapOnlineDevicePanel")
        self.devices: list[DeviceSnapshot] = []
        self.remote: RemoteMappingSnapshot | None = None
        self.selected_device_id: str | None = None
        self.cards: dict[str, MapDeviceCard] = {}
        self._frame_notes: dict[str, str] = {}
        self._active_video_id: str | None = None
        self._relocalization_snapshots: dict[str, object] = {}
        self._map_complete = False
        self._expanded_width = 380
        self._collapsed = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("在线设备")
        self.title.setObjectName("panelTitle")
        self.count = QLabel("0")
        self.count.setObjectName("muted")
        self.collapse_button = QPushButton("‹")
        self.collapse_button.setObjectName("mapDeviceCollapseButton")
        self.collapse_button.setToolTip("收起在线设备")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.count)
        header.addWidget(self.collapse_button)
        root.addWidget(self.header)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("mapOnlineDeviceScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName("mapOnlineDeviceContainer")
        self.card_layout = QVBoxLayout(self.container)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(8)
        self.card_layout.addStretch()
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)
        self.empty = QLabel("暂无在线设备")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty)
        self.setMinimumWidth(360)
        self.setMaximumWidth(460)

    def set_devices(
        self,
        devices: list[DeviceSnapshot],
        remote: RemoteMappingSnapshot | None = None,
        relocalization_snapshots: dict[str, object] | None = None,
        map_complete: bool = False,
    ) -> None:
        if relocalization_snapshots is not None:
            self._relocalization_snapshots = dict(relocalization_snapshots)
        self._map_complete = bool(map_complete)
        online = [item for item in devices if item.connection_status == ConnectionStatus.ONLINE]
        remote_id = remote.device_id.casefold() if remote is not None else ""
        online.sort(key=lambda item: (
            item.device_id.casefold() != remote_id,
            item.device_name.casefold(), item.device_id.casefold(),
        ))
        self.devices = online
        self.remote = remote
        valid = {item.device_id for item in online}
        for device_id in list(self.cards):
            if device_id in valid:
                continue
            card = self.cards.pop(device_id)
            card.stop_video()
            self.card_layout.removeWidget(card)
            card.deleteLater()
            if self._active_video_id == device_id:
                self._active_video_id = None
        if self.selected_device_id not in valid:
            self.selected_device_id = online[0].device_id if online else None
        for item in online:
            card = self.cards.get(item.device_id)
            if card is None:
                card = MapDeviceCard(item)
                card.selected.connect(self.select_device)
                card.video_requested.connect(self._on_video_requested)
                card.map_download_requested.connect(self.map_download_requested)
                card.relocalization_requested.connect(self.relocalization_requested)
                self.cards[item.device_id] = card
                self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            card.update_snapshot(
                item, remote, self._relocalization_snapshots.get(item.device_id.casefold()),
                self._map_complete,
            )
            card.set_selected(item.device_id == self.selected_device_id)
            card.set_frame_note(self._frame_notes.get(item.device_id, ""))
        for index, item in enumerate(online):
            self.card_layout.insertWidget(index, self.cards[item.device_id])
        self.count.setText(str(len(online)))
        self.empty.setVisible(not online)
        self.scroll.setVisible(bool(online) and not self._collapsed)
        if self.selected_device_id:
            self.device_selected.emit(self.selected_device_id)

    def select_device(self, device_id: str) -> None:
        if device_id == self.selected_device_id:
            return
        self.selected_device_id = device_id
        for card_id, card in self.cards.items():
            card.set_selected(card_id == device_id)
        self.device_selected.emit(device_id)

    def set_frame_note(self, device_id: str, note: str) -> None:
        self._frame_notes[device_id] = note
        card = self.cards.get(device_id)
        if card is not None:
            card.set_frame_note(note)

    def set_relocalization_snapshot(self, snapshot) -> None:
        self._relocalization_snapshots[snapshot.device_id.casefold()] = snapshot
        card = self.cards.get(snapshot.device_id)
        if card is not None:
            card.update_snapshot(card.device, self.remote, snapshot, self._map_complete)

    def select_remote_device(self, remote: RemoteMappingSnapshot) -> None:
        if remote.device_id in self.cards:
            self.select_device(remote.device_id)

    def _on_video_requested(self, device_id: str, enabled: bool) -> None:
        if not enabled:
            if self._active_video_id == device_id:
                self._active_video_id = None
            return
        previous = self._active_video_id
        self._active_video_id = device_id
        if previous and previous != device_id and previous in self.cards:
            self.cards[previous].stop_video()

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        splitter = self.parentWidget()
        if self._collapsed:
            if isinstance(splitter, QSplitter):
                sizes = splitter.sizes()
                index = splitter.indexOf(self)
                if 0 <= index < len(sizes):
                    self._expanded_width = max(360, sizes[index])
            self.stop_videos()
            self.title.setVisible(False)
            self.count.setVisible(False)
            self.scroll.setVisible(False)
            self.empty.setVisible(False)
            self.setMinimumWidth(36)
            self.setMaximumWidth(40)
            self.collapse_button.setText("›")
            self.collapse_button.setToolTip("展开在线设备")
        else:
            self.title.setVisible(True)
            self.count.setVisible(True)
            self.scroll.setVisible(bool(self.devices))
            self.empty.setVisible(not self.devices)
            self.setMinimumWidth(360)
            self.setMaximumWidth(460)
            self.collapse_button.setText("‹")
            self.collapse_button.setToolTip("收起在线设备")
        if isinstance(splitter, QSplitter):
            total = max(sum(splitter.sizes()), splitter.width())
            width = 38 if self._collapsed else min(self._expanded_width, 460)
            splitter.setSizes([width, max(1, total - width)])

    def stop_videos(self) -> None:
        for card in self.cards.values():
            card.stop_video()
        self._active_video_id = None


class RelocalizationReticle(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#ef4444"))
        cx, cy = self.width() // 2, self.height() // 2
        painter.drawLine(cx - 18, cy, cx + 18, cy)
        painter.drawLine(cx, cy - 18, cx, cy + 18)
        painter.drawLine(cx, cy - 30, cx - 7, cy - 20)
        painter.drawLine(cx, cy - 30, cx + 7, cy - 20)
        painter.drawEllipse(cx - 4, cy - 4, 8, 8)


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
        self._pgm_data = None
        self._grid_visual = None
        self._coordinate_visual = None
        self._device_axis_visual = None
        self._trail_visual = None
        self._camera = None
        self.layer_mode = "overlay"
        self.devices_visible = True
        self.pointcloud_loaded = False
        self.pgm_loaded = False
        self.selected_device_pose: PoseTelemetry | None = None
        self.device_trail: tuple[tuple[float, float, float], ...] = ()
        self.interaction_mode = "browse"
        self._relocalization_picker_enabled = False
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
        self.display_toolbar = QFrame()
        self.display_toolbar.setObjectName("mapViewerToolbar")
        controls = QHBoxLayout(self.display_toolbar)
        controls.setContentsMargins(8, 5, 8, 5)
        controls.setSpacing(7)
        self.layer_group = QButtonGroup(self)
        self.layer_group.setExclusive(True)
        self.layer_buttons: dict[str, QPushButton] = {}
        for mode, label in (("pointcloud", "点云"), ("grid", "PGM"), ("overlay", "叠加")):
            button = QPushButton(label)
            button.setObjectName("mapLayerSegment")
            button.setCheckable(True)
            button.setProperty("segmentMode", mode)
            button.clicked.connect(lambda _checked=False, value=mode: self.set_layer_mode(value))
            self.layer_group.addButton(button)
            self.layer_buttons[mode] = button
        self.layer_buttons["overlay"].setChecked(True)
        self.grid_check = QCheckBox("网格")
        self.grid_spacing_input = NoButtonDoubleSpinBox()
        self.grid_spacing_input.setRange(0.01, 1000.0)
        self.grid_spacing_input.setDecimals(2)
        self.grid_spacing_input.setSuffix(" m")
        self.grid_opacity_input = NoButtonSpinBox()
        self.grid_opacity_input.setRange(10, 100)
        self.grid_opacity_input.setSuffix(" %")
        self.coordinate_check = QCheckBox("坐标")
        self.tick_spacing_input = NoButtonDoubleSpinBox()
        self.tick_spacing_input.setRange(0.01, 1000.0)
        self.tick_spacing_input.setDecimals(2)
        self.tick_spacing_input.setSuffix(" m")
        self.cursor_check = QCheckBox("光标坐标")
        self.devices_check = QCheckBox("设备")
        self.devices_check.setChecked(True)
        self.height_legend = HeightColorLegend()
        self.height_legend.threshold.valueChanged.connect(lambda _value: self._refresh_point_colors())
        for widget in (
            QLabel("图层"), *self.layer_buttons.values(), self.grid_check,
            self.grid_spacing_input, self.grid_opacity_input, self.coordinate_check,
            self.tick_spacing_input, self.cursor_check, self.devices_check,
        ):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(self.height_legend)
        layout.addWidget(self.display_toolbar)
        self._map_overlay = QWidget()
        self._map_overlay_layout = QStackedLayout(self._map_overlay)
        self._map_overlay_layout.setContentsMargins(0, 0, 0, 0)
        self._map_overlay_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack = QStackedWidget()
        self._map_overlay_layout.addWidget(self._stack)
        self._relocalization_reticle = RelocalizationReticle()
        self._map_overlay_layout.addWidget(self._relocalization_reticle)
        self._map_overlay_layout.setCurrentWidget(self._stack)
        layout.addWidget(self._map_overlay)
        self.cursor_coordinate = QLabel("")
        self.cursor_coordinate.setObjectName("mapCursorCoordinate")
        self.cursor_coordinate.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.cursor_coordinate.setVisible(False)
        layout.addWidget(self.cursor_coordinate)
        self.status = QLabel("尚未加载点云")
        self.status.setObjectName("viewerStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self._stack.addWidget(self.status)
        try:
            self._initialize_canvas(canvas_factory)
        except Exception as exc:
            self.status.setText(f"三维渲染不可用：{exc}\n请检查 VisPy 与 OpenGL 环境")
        MAP_VIEWER_SETTINGS.changed.connect(self._apply_display_settings)
        self.grid_check.toggled.connect(
            lambda value: MAP_VIEWER_SETTINGS.update(grid_visible=bool(value))
        )
        self.grid_spacing_input.valueChanged.connect(
            lambda value: MAP_VIEWER_SETTINGS.update(grid_spacing=float(value))
        )
        self.grid_opacity_input.valueChanged.connect(
            lambda value: MAP_VIEWER_SETTINGS.update(grid_opacity=float(value))
        )
        self.coordinate_check.toggled.connect(
            lambda value: MAP_VIEWER_SETTINGS.update(coordinates_visible=bool(value))
        )
        self.tick_spacing_input.valueChanged.connect(
            lambda value: MAP_VIEWER_SETTINGS.update(tick_spacing=float(value))
        )
        self.cursor_check.toggled.connect(
            lambda value: MAP_VIEWER_SETTINGS.update(cursor_visible=bool(value))
        )
        self.devices_check.toggled.connect(self.set_devices_layer_visible)
        self._apply_display_settings()

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
        native.setMouseTracking(True)
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
        self._grid_visual = scene.visuals.Line(parent=self._view.scene)
        self._coordinate_visual = scene.visuals.Text(
            parent=self._view.scene, font_size=8, anchor_x="center", anchor_y="center"
        )
        scene.visuals.XYZAxis(parent=self._view.scene)

    def load_map(self, definition: MapDefinition, pcd_path: str | Path) -> None:
        self.current_map = definition
        try:
            data = self.loader.load(pcd_path, sample_for_render=True)
            if self._points_visual is None:
                raise PointCloudError("VisPy/OpenGL 渲染器未初始化")
            self._point_data = np.asarray(data.points, dtype=np.float32)
            self.pointcloud_loaded = True
            self._refresh_point_colors()
            self.set_layer_mode(self.layer_mode)
            self._render_coordinate_grid()
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

            self._pgm_data = data
            if self._pgm_visual is None:
                self._pgm_visual = scene.visuals.Image(
                    data.rgba(0.55 if self.layer_mode == "overlay" else 1.0),
                    parent=self._view.scene, method="subdivide"
                )
            else:
                self._pgm_visual.set_data(
                    data.rgba(0.55 if self.layer_mode == "overlay" else 1.0)
                )
            transform = scene.transforms.MatrixTransform()
            transform.scale((data.metadata.resolution, data.metadata.resolution, 1.0))
            transform.rotate(math.degrees(data.metadata.origin_yaw), (0, 0, 1))
            transform.translate((data.metadata.origin_x, data.metadata.origin_y, -0.04))
            self._pgm_visual.transform = transform
            self.pgm_loaded = True
            self.set_layer_mode(self.layer_mode)
            self._render_coordinate_grid()
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
        for name, button in self.layer_buttons.items():
            button.blockSignals(True)
            button.setChecked(name == mode)
            button.blockSignals(False)
        if self._points_visual is not None:
            self._points_visual.visible = self.pointcloud_loaded and mode in {"pointcloud", "overlay"}
        if self._pgm_visual is not None:
            self._pgm_visual.visible = self.pgm_loaded and mode in {"grid", "overlay"}
            if self._pgm_data is not None:
                self._pgm_visual.set_data(
                    self._pgm_data.rgba(0.55 if mode == "overlay" else 1.0)
                )
        self._update_layer_controls()

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
        self._pgm_data = None
        self.selected_device_pose = None
        self.device_trail = ()
        self.markers = ()
        self.height_legend.set_range(None, None)
        self.show_message("尚未加载点云")
        self._render_coordinate_grid()
        self._update_layer_controls()

    def set_live_points(self, points: np.ndarray, bounds=None) -> None:
        array = np.asarray(points, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != 3:
            raise ValueError("实时点云必须为 Nx3 数组")
        array = array[np.isfinite(array).all(axis=1)]
        if self._live_points_visual is not None:
            self._live_point_data = array
            self._refresh_point_colors()
            native = getattr(self._canvas, "native", None)
            if native is not None:
                self._stack.setCurrentWidget(native)
            self._update_layer_controls()
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
            self._render_coordinate_grid(bounds)

    def clear_live_points(self) -> None:
        self._live_view_initialized = False
        self._live_point_data = np.empty((0, 3), dtype=np.float32)
        self._height_minimum = None
        self._height_maximum = None
        self.set_relocalization_picker(False)
        if self._live_points_visual is not None:
            self._live_points_visual.set_data(np.empty((0, 3), dtype=np.float32))
        self._refresh_point_colors()

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
            local_x = pgm.width_m / 2
            local_y = pgm.height_m / 2
            cosine, sine = math.cos(pgm.origin_yaw), math.sin(pgm.origin_yaw)
            center = (
                pgm.origin_x + cosine * local_x - sine * local_y,
                pgm.origin_y + sine * local_x + cosine * local_y,
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

    def set_relocalization_picker(self, enabled: bool) -> None:
        enabled = bool(enabled)
        was_enabled = self._relocalization_picker_enabled
        self._relocalization_picker_enabled = enabled
        self._relocalization_reticle.setVisible(bool(enabled))
        if enabled:
            self._map_overlay_layout.setCurrentWidget(self._relocalization_reticle)
            self._relocalization_reticle.raise_()
        else:
            self._map_overlay_layout.setCurrentWidget(self._stack)
        if self._camera is not None:
            self._camera.planar_rotation = enabled
        if enabled and not was_enabled and self._camera is not None:
            self.interaction_mode = "browse"
            self._camera.interactive = True
            self._camera.elevation = 90
            self._camera.azimuth = 0
            self._camera.view_changed()

    def relocalization_pose(self) -> tuple[float, float, float] | None:
        native = getattr(self._canvas, "native", None)
        if not self._relocalization_picker_enabled or native is None:
            return None
        width, height = native.width(), native.height()
        center = self._screen_to_map(width / 2.0, height / 2.0, width, height)
        above = self._screen_to_plane(width / 2.0, max(0.0, height / 2.0 - 40.0))
        if center is None or above is None:
            return None
        values = np.asarray((*center, *above), dtype=np.float64)
        if not np.isfinite(values).all():
            return None
        dx, dy = above[0] - center[0], above[1] - center[1]
        if math.hypot(dx, dy) <= 1e-9:
            return None
        return center[0], center[1], math.atan2(dy, dx)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        native = getattr(self._canvas, "native", None)
        if (
            watched is native
            and event.type() == QEvent.Type.Wheel
            and self._camera is not None
        ):
            before = self._screen_to_plane(event.position().x(), event.position().y())
            self._camera.distance = pick_mode_zoom_distance(
                self._camera.distance, event.angleDelta().y())
            self._camera.view_changed()
            after = self._screen_to_plane(event.position().x(), event.position().y())
            if before is not None and after is not None:
                self._camera.center = cursor_anchored_camera_center(
                    self._camera.center, before, after)
                self._camera.view_changed()
            return True
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
        if watched is native and event.type() == QEvent.Type.MouseMove:
            if MAP_VIEWER_SETTINGS.cursor_visible:
                point = self._screen_to_map(
                    event.position().x(), event.position().y(), watched.width(), watched.height()
                )
                if point is not None:
                    self.cursor_coordinate.setText(f"X {point[0]:.2f} m    Y {point[1]:.2f} m")
                    self.cursor_coordinate.setVisible(True)
            return False
        if watched is native and event.type() == QEvent.Type.Leave:
            self.cursor_coordinate.setVisible(False)
        return super().eventFilter(watched, event)

    def _apply_display_settings(self) -> None:
        controls = (
            (self.grid_check, MAP_VIEWER_SETTINGS.grid_visible),
            (self.grid_spacing_input, MAP_VIEWER_SETTINGS.grid_spacing),
            (self.grid_opacity_input, int(MAP_VIEWER_SETTINGS.grid_opacity)),
            (self.coordinate_check, MAP_VIEWER_SETTINGS.coordinates_visible),
            (self.tick_spacing_input, MAP_VIEWER_SETTINGS.tick_spacing),
            (self.cursor_check, MAP_VIEWER_SETTINGS.cursor_visible),
        )
        for control, value in controls:
            control.blockSignals(True)
            if isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            else:
                control.setValue(value)
            control.blockSignals(False)
        self.grid_spacing_input.setEnabled(MAP_VIEWER_SETTINGS.grid_visible)
        self.grid_opacity_input.setEnabled(MAP_VIEWER_SETTINGS.grid_visible)
        self.tick_spacing_input.setEnabled(MAP_VIEWER_SETTINGS.coordinates_visible)
        if not MAP_VIEWER_SETTINGS.cursor_visible:
            self.cursor_coordinate.setVisible(False)
        self._render_coordinate_grid()

    def _update_layer_controls(self) -> None:
        has_points = self.pointcloud_loaded or len(self._live_point_data) > 0
        self.layer_buttons["pointcloud"].setEnabled(has_points)
        self.layer_buttons["grid"].setEnabled(self.pgm_loaded)
        self.layer_buttons["overlay"].setEnabled(has_points and self.pgm_loaded)
        self.height_legend.setVisible(
            has_points and self.layer_mode in {"pointcloud", "overlay"}
        )

    def _refresh_point_colors(self) -> None:
        point_sets = [
            values for values in (self._point_data, self._live_point_data)
            if len(values)
        ]
        finite_z = [
            values[np.isfinite(values).all(axis=1), 2]
            for values in point_sets
        ]
        finite_z = [values for values in finite_z if len(values)]
        if not finite_z:
            self.height_legend.set_range(None, None)
            return
        minimum_z = min(float(values.min()) for values in finite_z)
        maximum_z = max(float(values.max()) for values in finite_z)
        threshold = maximum_z - (maximum_z - minimum_z) * self.height_legend.threshold.value() / 100.0
        point_data = self._point_data
        live_data = self._live_point_data
        if maximum_z > minimum_z:
            point_data = point_data[point_data[:, 2] <= threshold] if len(point_data) else point_data
            live_data = live_data[live_data[:, 2] <= threshold] if len(live_data) else live_data
        if self._points_visual is not None and self.pointcloud_loaded:
            self._points_visual.set_data(
                point_data,
                # VisPy reduces face_color even when there are no positions; use
                # one transparent color for the fully clipped state.
                face_color=(
                    height_rainbow_colors(point_data, minimum_z, maximum_z)
                    if len(point_data) else (0.0, 0.0, 0.0, 0.0)
                ),
                edge_width=0, size=2.2,
            )
        if self._live_points_visual is not None:
            self._live_points_visual.set_data(
                live_data,
                face_color=(
                    height_rainbow_colors(live_data, minimum_z, maximum_z)
                    if len(live_data) else (0.0, 0.0, 0.0, 0.0)
                ),
                edge_width=0, size=2.5,
            )
        self.height_legend.set_range(minimum_z, maximum_z)
        self.height_legend.threshold.setToolTip(f"显示 {threshold:.1f}m 以下点云（z ≤ 高度上限）")
        self._update_layer_controls()

    def _map_xy_bounds(self):
        if self.current_map and self.current_map.bounds:
            bounds = self.current_map.bounds
            return bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y
        if self.current_map and self.current_map.pgm:
            pgm = self.current_map.pgm
            corners = []
            cosine, sine = math.cos(pgm.origin_yaw), math.sin(pgm.origin_yaw)
            for x, y in ((0, 0), (pgm.width_m, 0), (0, pgm.height_m), (pgm.width_m, pgm.height_m)):
                corners.append((pgm.origin_x + cosine * x - sine * y,
                                pgm.origin_y + sine * x + cosine * y))
            return (min(p[0] for p in corners), max(p[0] for p in corners),
                    min(p[1] for p in corners), max(p[1] for p in corners))
        if len(self._live_point_data):
            return (float(self._live_point_data[:, 0].min()), float(self._live_point_data[:, 0].max()),
                    float(self._live_point_data[:, 1].min()), float(self._live_point_data[:, 1].max()))
        return None

    def _render_coordinate_grid(self, bounds=None) -> None:
        if self._grid_visual is None or self._coordinate_visual is None:
            return
        xy = None
        if bounds is not None:
            xy = bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y
        xy = xy or self._map_xy_bounds()
        if xy is None or not MAP_VIEWER_SETTINGS.grid_visible:
            self._grid_visual.set_data(pos=np.empty((0, 3), dtype=np.float32))
            self._coordinate_visual.visible = False
            return
        min_x, max_x, min_y, max_y = xy
        spacing = MAP_VIEWER_SETTINGS.grid_spacing
        padding = spacing
        first_x = math.floor((min_x - padding) / spacing) * spacing
        last_x = math.ceil((max_x + padding) / spacing) * spacing
        first_y = math.floor((min_y - padding) / spacing) * spacing
        last_y = math.ceil((max_y + padding) / spacing) * spacing
        x_values = np.arange(first_x, last_x + spacing * 0.5, spacing)
        y_values = np.arange(first_y, last_y + spacing * 0.5, spacing)
        if len(x_values) + len(y_values) > 4000:
            factor = math.ceil((len(x_values) + len(y_values)) / 4000)
            x_values, y_values = x_values[::factor], y_values[::factor]
        segments = []
        for x in x_values:
            segments.extend(((x, first_y, -0.02), (x, last_y, -0.02)))
        for y in y_values:
            segments.extend(((first_x, y, -0.02), (last_x, y, -0.02)))
        alpha = MAP_VIEWER_SETTINGS.grid_opacity / 100.0
        color = (0.5, 0.5, 0.5, alpha)
        self._grid_visual.set_data(
            pos=np.asarray(segments, dtype=np.float32), color=color,
            connect="segments", width=1.0,
        )
        if not MAP_VIEWER_SETTINGS.coordinates_visible:
            self._coordinate_visual.visible = False
            return
        tick = MAP_VIEWER_SETTINGS.tick_spacing
        tx = np.arange(math.ceil(first_x / tick) * tick, last_x + tick * 0.5, tick)
        ty = np.arange(math.ceil(first_y / tick) * tick, last_y + tick * 0.5, tick)
        if len(tx) + len(ty) > 300:
            factor = math.ceil((len(tx) + len(ty)) / 300)
            tx, ty = tx[::factor], ty[::factor]
        positions = [(x, first_y, 0.01) for x in tx] + [(first_x, y, 0.01) for y in ty]
        labels = [f"{x:g}" for x in tx] + [f"{y:g}" for y in ty]
        self._coordinate_visual.text = labels
        self._coordinate_visual.pos = np.asarray(positions, dtype=np.float32)
        self._coordinate_visual.color = self.theme_palette.muted
        self._coordinate_visual.visible = bool(labels)

    def _screen_to_map(self, x: float, y: float, width: int, height: int) -> tuple[float, float] | None:
        if self.current_map is None or width <= 0 or height <= 0:
            return None
        if self._view is not None and self._camera is not None:
            try:
                transform = self._view.scene.transform
                point = unproject_screen_to_plane(transform, x, y)
                if point is not None:
                    return point if self._contains_map_point(*point) else None
            except Exception:
                return None
        return None

    def _screen_to_plane(self, x: float, y: float) -> tuple[float, float] | None:
        if self._view is None or self._camera is None:
            return None
        try:
            return unproject_screen_to_plane(self._view.scene.transform, x, y)
        except Exception:
            return None

    def _contains_map_point(self, x: float, y: float) -> bool:
        if self.current_map is None:
            return False
        if self.layer_mode == "grid" and self.current_map.pgm is not None:
            pgm = self.current_map.pgm
            dx, dy = x - pgm.origin_x, y - pgm.origin_y
            cosine, sine = math.cos(pgm.origin_yaw), math.sin(pgm.origin_yaw)
            local_x = cosine * dx + sine * dy
            local_y = -sine * dx + cosine * dy
            epsilon = max(pgm.resolution * 1e-6, 1e-9)
            return (
                -epsilon <= local_x <= pgm.width_m + epsilon
                and -epsilon <= local_y <= pgm.height_m + epsilon
            )
        if self.current_map.bounds is not None:
            bounds = self.current_map.bounds
            epsilon = max(bounds.width, bounds.height, 1.0) * 1e-9
            return (
                bounds.min_x - epsilon <= x <= bounds.max_x + epsilon
                and bounds.min_y - epsilon <= y <= bounds.max_y + epsilon
            )
        if self.current_map.pgm is not None:
            pgm = self.current_map.pgm
            dx, dy = x - pgm.origin_x, y - pgm.origin_y
            cosine, sine = math.cos(pgm.origin_yaw), math.sin(pgm.origin_yaw)
            local_x = cosine * dx + sine * dy
            local_y = -sine * dx + cosine * dy
            return 0.0 <= local_x <= pgm.width_m and 0.0 <= local_y <= pgm.height_m
        return False

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

    def set_devices_layer_visible(self, visible: bool) -> None:
        self.devices_visible = bool(visible)
        self.devices_check.blockSignals(True)
        self.devices_check.setChecked(self.devices_visible)
        self.devices_check.blockSignals(False)
        if self._marker_visual is not None:
            self._marker_visual.visible = self.devices_visible
        for visual in self._shape_visuals:
            visual.visible = self.devices_visible
        if self._device_axis_visual is not None:
            self._device_axis_visual.visible = self.devices_visible
        if self._trail_visual is not None:
            self._trail_visual.visible = self.devices_visible

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
                visual = self._create_marker_mesh(marker)
                visual.visible = self.devices_visible
                self._shape_visuals.append(visual)
            except Exception:
                fallback.append((marker.x, marker.y, marker.z))
        positions = np.asarray(fallback, dtype=np.float32)
        if not len(positions):
            positions = np.empty((0, 3), dtype=np.float32)
        self._marker_visual.set_data(positions, face_color=self.theme_palette.warning, edge_color=self.theme_palette.text_strong, size=12)
        self._marker_visual.visible = self.devices_visible

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
        self._device_axis_visual.visible = self.devices_visible

    def set_device_trail(self, positions: list[tuple[float, float, float]] | tuple[tuple[float, float, float], ...]) -> None:
        self.device_trail = tuple(positions)
        if self._trail_visual is None:
            return
        points = np.asarray(self.device_trail, dtype=np.float32)
        if len(points) < 2:
            points = np.empty((0, 3), dtype=np.float32)
        self._trail_visual.set_data(pos=points, color=self.theme_palette.primary_strong, width=2.0)
        self._trail_visual.visible = self.devices_visible

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
        self._refresh_point_colors()
        if self._task_paths:
            self.set_task_paths(self._task_paths)
        if self._task_conflicts:
            self.set_task_conflicts(self._task_conflicts)
        self._render_markers()
        self.set_selected_device_pose(self.selected_device_pose)
        self.set_device_trail(self.device_trail)
        self.set_devices_layer_visible(self.devices_visible)
        self._render_coordinate_grid()
        self.status.update()
        self.update()


class MapDetailPage(QWidget):
    back_requested = Signal()
    reload_requested = Signal()
    export_requested = Signal()
    mapping_requested = Signal()
    mapping_cancel_requested = Signal()
    device_selected = Signal(str)
    map_download_requested = Signal(str)
    relocalization_requested = Signal(str)

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
        self.cancel_mapping_button = QPushButton("强制结束")
        self.cancel_mapping_button.setObjectName("dangerButton")
        self.cancel_mapping_button.clicked.connect(self.mapping_cancel_requested)
        self.cancel_mapping_button.setVisible(False)
        for button in (
            reset, fit, reload_button, export_button,
            self.cancel_mapping_button, self.mapping_button,
        ):
            toolbar.addWidget(button)
        root.addLayout(toolbar)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self.mapping_status = QFrame()
        self.mapping_status.setObjectName("mappingStatus")
        status_layout = QVBoxLayout(self.mapping_status)
        status_layout.setContentsMargins(10, 7, 10, 8)
        status_layout.setSpacing(6)
        status_summary = QHBoxLayout()
        status_summary.setSpacing(10)
        self.mapping_state = QLabel("实时建图未启动")
        self.mapping_state.setObjectName("statusPill")
        self.mapping_metrics = QLabel("完整帧 0  ·  丢帧 0  ·  接收点 0  ·  融合点 0")
        self.mapping_metrics.setObjectName("muted")
        status_summary.addWidget(self.mapping_state)
        self.mapping_elapsed = QLabel("时长 00:00")
        self.mapping_elapsed.setObjectName("muted")
        status_summary.addWidget(self.mapping_elapsed)
        status_summary.addWidget(self.mapping_metrics, 1)
        status_layout.addLayout(status_summary)
        self.mapping_log = QPlainTextEdit()
        self.mapping_log.setObjectName("mappingProtocolLog")
        self.mapping_log.setReadOnly(True)
        self.mapping_log.setUndoRedoEnabled(False)
        self.mapping_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.mapping_log.setMaximumBlockCount(200)
        self.mapping_log.setFixedHeight(112)
        self.mapping_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.mapping_log.setVisible(False)
        status_layout.addWidget(self.mapping_log)
        self.mapping_status.setVisible(False)
        root.addWidget(self.mapping_status)
        self.readiness_details = QLabel("")
        self.readiness_details.setObjectName("muted")
        self.readiness_details.setWordWrap(True)
        self.readiness_details.setVisible(False)
        root.addWidget(self.readiness_details)
        self.relocalization_log = QPlainTextEdit()
        self.relocalization_log.setObjectName("mappingProtocolLog")
        self.relocalization_log.setReadOnly(True)
        self.relocalization_log.setMaximumBlockCount(200)
        self.relocalization_log.setFixedHeight(96)
        self.relocalization_log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.relocalization_log.setVisible(False)
        root.addWidget(self.relocalization_log)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.device_panel = MapOnlineDevicePanel()
        self.device_panel.device_selected.connect(self.device_selected)
        self.device_panel.map_download_requested.connect(self.map_download_requested)
        self.device_panel.relocalization_requested.connect(self.relocalization_requested)
        self.viewer = viewer_factory() if viewer_factory else PointCloudViewer()
        self.content_splitter.addWidget(self.device_panel)
        self.content_splitter.addWidget(self.viewer)
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setSizes([380, 900])
        root.addWidget(self.content_splitter, 1)
        self._started_at = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

    def set_theme(self, palette: ThemePalette) -> None:
        set_theme = getattr(self.viewer, "set_theme", None)
        if set_theme is not None:
            set_theme(palette)
        self.update()

    def set_devices(
        self,
        devices: list[DeviceSnapshot],
        remote: RemoteMappingSnapshot | None = None,
        relocalization_snapshots: dict[str, object] | None = None,
        map_complete: bool = False,
    ) -> None:
        self.device_panel.set_devices(
            devices, remote, relocalization_snapshots, map_complete
        )

    def update_relocalization(self, snapshot) -> None:
        self.device_panel.set_relocalization_snapshot(snapshot)
        if snapshot.device_id == self.device_panel.selected_device_id:
            self._set_protocol_log(self.relocalization_log, snapshot.logs)

    @staticmethod
    def _set_protocol_log(widget: QPlainTextEdit, lines) -> None:
        values = tuple(lines)
        widget.setPlainText("\n".join(values))
        widget.setVisible(bool(values))
        if not values:
            return
        scrollbar = widget.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        QTimer.singleShot(0, lambda: scrollbar.setValue(scrollbar.maximum()))

    def stop_videos(self) -> None:
        self.device_panel.stop_videos()

    def set_map(
        self,
        definition: MapDefinition,
        pcd_path: Path | None,
        pgm_yaml_path: Path | None = None,
        active: bool = False,
    ) -> None:
        self.definition = definition
        self.title.setText(f"{definition.name}  · 当前激活地图" if active else definition.name)
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
        self.mapping_log.setVisible(False)
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

    def update_remote_mapping(self, snapshot: RemoteMappingSnapshot) -> None:
        self.mapping_status.setVisible(True)
        self.mapping_state.setText(snapshot.message)
        log_lines = []
        for entry in snapshot.log_entries:
            stamp = entry.timestamp.astimezone().strftime("%H:%M:%S.%f")[:-3]
            log_lines.append(
                f"[{stamp}] {entry.direction:<5} {entry.event:<18} {entry.summary}"
            )
        self._set_protocol_log(self.mapping_log, log_lines)
        last_data = (
            snapshot.last_data_at.astimezone().strftime("%H:%M:%S")
            if snapshot.last_data_at else "--"
        )
        progress = ""
        if snapshot.artifact_bytes_total:
            percent = snapshot.artifact_bytes_received * 100.0 / snapshot.artifact_bytes_total
            progress = f"  ·  下载 {percent:.1f}%"
        self.mapping_metrics.setText(
            f"完整帧 {snapshot.complete_frames}  ·  丢帧 {snapshot.dropped_frames}  ·  "
            f"接收点 {snapshot.received_points:,}  ·  预览点 {snapshot.fused_points:,}  ·  "
            f"最后数据 {last_data}{progress}"
        )
        checks = []
        check_labels = {
            "pointcloud": "Livox 点云", "imu": "Livox IMU",
            "pose": "位姿", "artifact_storage": "成果存储",
            "map_generation": "地图生成",
        }
        for item in snapshot.readiness_checks:
            state = "可用" if item.available else "不可用"
            name = check_labels.get(item.name, item.name)
            checks.append(f"{name} {state}{'：' + item.reason if item.reason else ''}")
        summary = "  ·  ".join(checks)
        if snapshot.sample_window_seconds is not None:
            summary += ("  ·  " if summary else "") + (
                f"端侧采样窗口 {snapshot.sample_window_seconds:g} s"
            )
        if snapshot.capability_version:
            summary += f"  ·  能力版本 {snapshot.capability_version}"
        self.readiness_details.setText(summary)
        self.readiness_details.setVisible(bool(summary))
        labels = {
            "preparing": "正在协商", "ready": "开始建图",
            "starting": "正在启动", "mapping": "结束建图",
            "warning": "结束建图", "stopping": "正在结束",
            "generating": "正在生成", "downloading": "正在下载",
            "validating": "正在校验", "failed": "重新协商",
            "cancelled": "重新建图", "aborted": "重新建图", "completed": "重新建图",
        }
        self.mapping_button.setText(labels.get(snapshot.state, "重新建图"))
        self.mapping_button.setEnabled(
            snapshot.state in {"ready", "mapping", "warning", "failed", "aborted"}
        )
        self.cancel_mapping_button.setVisible(snapshot.state in {
            "ready", "starting", "mapping", "warning", "failed", "aborting"
        })
        self.cancel_mapping_button.setEnabled(snapshot.state != "aborting")
        self._started_at = snapshot.started_at
        self._refresh_elapsed()
        if snapshot.navigation_locked:
            self._elapsed_timer.start()
        else:
            self._elapsed_timer.stop()

    def _refresh_elapsed(self) -> None:
        if self._started_at:
            seconds = max(0, int((datetime.now().astimezone() - self._started_at.astimezone()).total_seconds()))
            self.mapping_elapsed.setText(f"时长 {seconds // 60:02d}:{seconds % 60:02d}")


class MapPage(QWidget):
    fusion_finished = Signal(object, object)
    pgm_generation_finished = Signal(str, object, object)
    pgm_fusion_finished = Signal(object, object)

    def __init__(
        self,
        source: DeviceDataSource,
        overview=None,
        repository: MapRepository | None = None,
        viewer_factory: Callable[[], PointCloudViewer] | None = None,
        mapping_service=None,
        relocalization_service=None,
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
        self.relocalization_service = relocalization_service
        self.fusion_repository = (
            fusion_repository
            or getattr(mapping_service, "fusion_repository", None)
            or MapFusionRepository()
        )
        self.fusion_runner = fusion_runner or getattr(mapping_service, "fusion_runner", None) or MapFusionRunner()
        self._fusion_thread: threading.Thread | None = None
        self._pgm_thread: threading.Thread | None = None
        self._pgm_fusion_thread: threading.Thread | None = None
        self._pgm_fusion_dialog: PgmFusionDialog | None = None
        self._pgm_fusion_job: PgmFusionJob | None = None
        self._pgm_fusion_include_existing = False
        self.pgm_fusion_engine = PgmFusionEngine()
        self.telemetry_store = telemetry_store
        self._remote_trail: list[tuple[float, float, float]] = []
        self._trail_device_id: str | None = None
        self._latest_telemetry: dict[str, object] = {}
        self._selected_remote_session_id: str | None = None
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._build(viewer_factory)
        self._render_cards()
        self.repository.maps_updated.connect(self._on_maps_updated)
        self.repository.active_map_changed.connect(self._on_active_map_changed)
        self.source.devices_updated.connect(self._on_devices_updated)
        self.fusion_finished.connect(self._on_fusion_finished)
        self.pgm_generation_finished.connect(self._on_pgm_generation_finished)
        self.pgm_fusion_finished.connect(self._on_pgm_fusion_finished)
        if self.mapping_service is not None:
            self.mapping_service.session_updated.connect(self._on_mapping_updated)
            self.mapping_service.preview_updated.connect(self._on_mapping_preview)
            self.mapping_service.completed.connect(self._on_mapping_completed)
            self.mapping_service.failed.connect(self._on_mapping_failed)
            self.mapping_service.availability_changed.connect(self._on_mapping_availability)
            self.mapping_service.degraded.connect(self._on_mapping_degraded)
            self.mapping_service.pgm_source_updated.connect(self._on_pgm_source_updated)
            self.mapping_service.pgm_download_failed.connect(self._on_pgm_download_failed)
            self.mapping_service.pgm_download_completed.connect(self._on_pgm_download_completed)
            self.mapping_service.remote_updated.connect(self._on_remote_mapping_updated)
        if self.relocalization_service is not None:
            self.relocalization_service.snapshot_updated.connect(self._on_relocalization_updated)
        if self.telemetry_store is not None:
            self.telemetry_store.telemetry_updated.connect(self._on_remote_telemetry)
        self._device_render_timer = QTimer(self)
        self._device_render_timer.setInterval(100)
        self._device_render_timer.timeout.connect(self._refresh_device_overlays)
        self._device_render_timer.start()

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
        self.detail_page.mapping_cancel_requested.connect(self._cancel_remote_mapping)
        self.detail_page.device_selected.connect(self._on_map_device_selected)
        self.detail_page.map_download_requested.connect(self._download_relocalization_map)
        self.detail_page.relocalization_requested.connect(self._handle_relocalization_action)
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
        self.pgm_fusion_button = QPushButton("PGM 融合")
        self.pgm_fusion_button.clicked.connect(self._open_pgm_fusion)
        self.algorithm_button = QPushButton("融合算法")
        self.algorithm_button.clicked.connect(self._open_algorithm_manager)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self._toggle_edit)
        header.addWidget(self.new_button)
        header.addWidget(self.fusion_button)
        header.addWidget(self.pgm_fusion_button)
        header.addWidget(self.algorithm_button)
        header.addWidget(self.edit_button)
        layout.addLayout(header)

        self.action_bar = QHBoxLayout()
        self.action_bar.addStretch()
        self.rename_button = QPushButton("修改名称")
        self.import_button = QPushButton("导入 / 替换 PCD")
        self.import_pgm_button = QPushButton("导入 / 替换 PGM")
        self.generate_pgm_button = QPushButton("从 PCD 生成 PGM")
        self.export_button = QPushButton("下载地图")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        for button in (
            self.rename_button, self.import_button, self.import_pgm_button,
            self.generate_pgm_button,
            self.export_button, self.delete_button,
        ):
            button.setVisible(False)
            button.setEnabled(False)
            self.action_bar.addWidget(button)
        self.rename_button.clicked.connect(self._rename_selected)
        self.import_button.clicked.connect(self._import_selected)
        self.import_pgm_button.clicked.connect(self._import_pgm_selected)
        self.generate_pgm_button.clicked.connect(self._generate_pgm_selected)
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
            card = MapCard(definition, definition.map_id == self.repository.active_map_id())
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
            if len(selected_devices) != 1:
                raise ValueError("v0.16.0 只支持单机遥控建图")
            self._remote_trail.clear()
            self.mapping_service.prepare_remote_mapping(definition, selected_devices[0])
        except (MapRepositoryError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "地图创建失败", str(exc))

    def _open_algorithm_manager(self) -> None:
        snapshot = self.mapping_service.current_job_snapshot if self.mapping_service else None
        active = (snapshot.algorithm_id,) if snapshot else ()
        FusionAlgorithmDialog(
            self.fusion_repository, self, active_algorithm_ids=active
        ).exec()

    def _open_pgm_fusion(self) -> None:
        if self.mapping_service is None or not self.mapping_service.available:
            message = self.mapping_service.module_message if self.mapping_service else "UDP 建图模块未配置"
            QMessageBox.warning(self, "PGM 融合不可用", message)
            return
        if self.mapping_service.active:
            QMessageBox.information(self, "PGM 融合", "实时建图或 PGM 下载任务正在运行")
            return
        candidates = [
            item for item in self.maps
            if item.status == MapStatus.READY and item.pcd_path and item.bounds
        ]
        if not candidates:
            QMessageBox.warning(self, "PGM 融合", "至少需要一张具有有效 PCD 的目标地图")
            return
        dialog = PgmFusionDialog(candidates, self.devices, self)
        self._pgm_fusion_dialog = dialog
        dialog.start_requested.connect(self._start_pgm_fusion_download)
        dialog.retry_requested.connect(self.mapping_service.retry_pgm_download)
        dialog.remove_requested.connect(self.mapping_service.remove_failed_pgm_source)
        dialog.rejected.connect(self._cancel_pgm_fusion)
        dialog.exec()
        if self._pgm_fusion_dialog is dialog:
            self._pgm_fusion_dialog = None

    def _start_pgm_fusion_download(self, dialog: PgmFusionDialog) -> None:
        target = dialog.target()
        try:
            fingerprint = self.repository.pcd_fingerprint(target.map_id)
            job_id = uuid.uuid4().hex
            sources = dialog.selected_remote_sources()
            job = PgmFusionJob(
                job_id, target.map_id, target.frame_id, fingerprint,
                sources, dialog.output_resolution(),
            )
            root = self.repository.write_pgm_fusion_job(target.map_id, job_id, {
                "schema_version": 1, "job_id": job_id, "state": "downloading",
                "target_map_id": target.map_id, "target_frame_id": target.frame_id,
                "target_pcd_sha256": fingerprint,
                "include_existing_pgm": dialog.include_existing.isChecked(),
                "output_resolution": dialog.output_resolution(),
                "sources": [
                    {
                        "source_id": item.source_id, "device_id": item.device_id,
                        "source_map_id": item.source_map_id,
                        "device_ip": item.device_ip,
                        "transform": item.transform.__dict__,
                    }
                    for item in sources
                ],
                "created_at": job.created_at.isoformat(),
            })
            self._pgm_fusion_job = job
            self._pgm_fusion_include_existing = dialog.include_existing.isChecked()
            dialog.set_finishing("正在按设备顺序下载 PGM…")
            self.mapping_service.start_pgm_download(target.map_id, sources, root)
        except (MapRepositoryError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(dialog, "PGM 下载失败", str(exc))
            dialog.reject()

    def _on_pgm_source_updated(self, snapshot: object) -> None:
        if self._pgm_fusion_dialog and isinstance(snapshot, PgmDownloadSnapshot):
            self._pgm_fusion_dialog.update_snapshot(snapshot)

    def _on_pgm_download_failed(self, source_id: str, message: str) -> None:
        dialog = self._pgm_fusion_dialog
        if dialog:
            dialog.set_finishing(f"来源 {source_id} 失败：{message}。可重试或移除。")
            dialog.retry_button.setEnabled(True)
            dialog.remove_button.setEnabled(True)

    def _on_pgm_download_completed(self, completed: object) -> None:
        dialog, job = self._pgm_fusion_dialog, self._pgm_fusion_job
        if dialog is None or job is None:
            return
        remote_sources = tuple(completed) if isinstance(completed, tuple) else ()
        sources = list(remote_sources)
        target = self.repository.map_by_id(job.target_map_id)
        if target is None or target.bounds is None:
            QMessageBox.critical(dialog, "PGM 融合失败", "目标地图或 PCD 边界已失效")
            return
        if self._pgm_fusion_include_existing and target.pgm:
            image_path, yaml_path = self.repository.pgm_paths(target.map_id)
            sources.append(PgmFusionSource(
                source_id=f"target:{target.map_id}", source_map_id=target.map_id,
                transform=PgmTransform2D(), source_frame_id=target.frame_id,
                pgm_path=str(image_path), yaml_path=str(yaml_path),
                artifact_sha256=pcd_sha256(image_path),
                existing_target_layer=True,
            ))
        if len(sources) < 2 or not remote_sources:
            QMessageBox.warning(dialog, "PGM 融合", "移除失败来源后不足两个有效图层，无法融合")
            return
        try:
            finest, outside = self.pgm_fusion_engine.inspect(sources, target.bounds, None)
            dialog.set_minimum_resolution(finest)
            resolution = dialog.output_resolution()
        except PgmFusionError as exc:
            QMessageBox.critical(dialog, "PGM 融合失败", str(exc))
            return
        summary = f"有效来源：{len(sources)}\n输出分辨率：{resolution:g} m/px"
        if outside > 1e-9:
            summary += f"\n约 {outside:.3f} m² 位于目标 PCD XY 边界之外，将被裁剪。是否继续？"
            answer = QMessageBox.question(
                dialog, "确认裁剪", summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                dialog.set_finishing("已取消裁剪提交，临时下载结果已保留")
                return
        else:
            QMessageBox.information(dialog, "下载完成", summary)
        final_job = PgmFusionJob(
            job.job_id, job.target_map_id, job.target_frame_id,
            job.target_pcd_sha256, tuple(sources), resolution, job.created_at,
        )
        self._pgm_fusion_job = final_job
        dialog.set_finishing("正在融合栅格并原子提交…")
        self._pgm_fusion_thread = threading.Thread(
            target=self._run_pgm_fusion, args=(final_job,),
            name="ccs-pgm-fusion", daemon=True,
        )
        self._pgm_fusion_thread.start()

    def _run_pgm_fusion(self, job: PgmFusionJob) -> None:
        try:
            target = self.repository.map_by_id(job.target_map_id)
            if target is None or target.bounds is None:
                raise MapRepositoryError("目标地图不存在或缺少 PCD 边界")
            if self.repository.pcd_fingerprint(job.target_map_id) != job.target_pcd_sha256:
                raise MapRepositoryError("目标 PCD 已变化，必须重新融合")
            root = self.repository.pgm_fusion_job_directory(job.target_map_id, job.job_id)
            result = self.pgm_fusion_engine.fuse(
                job.sources, target.bounds, root / "fused.pgm", root / "fused.yaml",
                job.output_resolution,
            )
            provenance = PgmFusionProvenance(
                job.job_id, job.target_pcd_sha256, job.sources, result.metadata.resolution,
                clipped_cells=result.clipped_cells, clipped_area_m2=result.clipped_area_m2,
            )
            definition = self.repository.commit_pgm_fusion_result(
                job.target_map_id, job.job_id, root / "fused.yaml", provenance,
            )
            self.pgm_fusion_finished.emit(definition, None)
        except Exception as exc:
            self.pgm_fusion_finished.emit(None, str(exc))

    def _on_pgm_fusion_finished(self, definition: object, error: object) -> None:
        dialog = self._pgm_fusion_dialog
        if error:
            if dialog:
                dialog.set_finishing(f"融合失败：{error}，临时结果已保留")
            QMessageBox.critical(dialog or self, "PGM 融合失败", str(error))
            return
        if dialog:
            dialog.accept()
        QMessageBox.information(self, "PGM 融合", "融合 PGM 已绑定到目标点云地图")
        self._pgm_fusion_job = None

    def _cancel_pgm_fusion(self) -> None:
        if self.mapping_service and self.mapping_service.pgm_download_active:
            self.mapping_service.cancel_pgm_download()

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
        self.pgm_fusion_button.setEnabled(False)
        self.fusion_button.setText("PCD + PGM 融合中…" if job.sync_pgm else "融合中…")
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
                "sync_pgm": job.sync_pgm,
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
            output_pgm_yaml = None
            pgm_provenance = None
            if job.sync_pgm:
                output_cloud = self.repository.loader.load(output)
                transforms = {item.source_id: item for item in job.transforms}
                pgm_sources = []
                for source_map in maps:
                    if source_map.pgm is None:
                        raise MapFusionError(f"源地图“{source_map.name}”缺少有效 PGM 图层")
                    yaml_path, image_path = self.repository.pgm_paths(source_map.map_id)
                    transform = transforms[source_map.map_id]
                    pgm_sources.append(PgmFusionSource(
                        source_id=f"map:{source_map.map_id}",
                        source_map_id=source_map.map_id,
                        transform=PgmTransform2D(
                            transform.translation_m[0], transform.translation_m[1],
                            transform.rotation_rpy_deg[2],
                        ),
                        source_frame_id=source_map.frame_id,
                        pgm_path=str(image_path), yaml_path=str(yaml_path),
                        artifact_sha256=pcd_sha256(image_path),
                    ))
                pgm_output = directory / "plugin-output.pgm"
                output_pgm_yaml = directory / "plugin-output.yaml"
                pgm_result = self.pgm_fusion_engine.fuse(
                    pgm_sources, output_cloud.bounds, pgm_output, output_pgm_yaml, None,
                )
                pgm_provenance = PgmFusionProvenance(
                    job.job_id, pcd_sha256(output), tuple(pgm_sources),
                    pgm_result.metadata.resolution,
                    clipped_cells=pgm_result.clipped_cells,
                    clipped_area_m2=pgm_result.clipped_area_m2,
                )
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
                primary.frame_id, provenance, output_pgm_yaml=output_pgm_yaml,
                pgm_provenance=pgm_provenance,
            )
            self.fusion_finished.emit(definition, None)
        except Exception as exc:
            self.fusion_finished.emit(None, str(exc))

    def _on_fusion_finished(self, definition: object, error: object) -> None:
        self.fusion_button.setEnabled(not self.edit_mode)
        self.pgm_fusion_button.setEnabled(not self.edit_mode)
        self.fusion_button.setText("地图融合")
        if error:
            QMessageBox.critical(self, "地图融合失败", f"{error}\n临时输入已保留，可调整算法后重试。")
        elif isinstance(definition, MapDefinition):
            layers = "PCD 与 PGM" if definition.pgm else "PCD"
            QMessageBox.information(self, "地图融合", f"融合地图“{definition.name}”已创建（{layers}）")
            self.show_detail(definition.map_id)

    def _toggle_edit(self) -> None:
        self.edit_mode = not self.edit_mode
        self.selected_map_ids.clear()
        self.edit_button.setText("取消编辑" if self.edit_mode else "编辑")
        self.new_button.setEnabled(not self.edit_mode)
        self.fusion_button.setEnabled(not self.edit_mode)
        self.pgm_fusion_button.setEnabled(not self.edit_mode)
        self.algorithm_button.setEnabled(not self.edit_mode)
        for button in (
            self.rename_button, self.import_button, self.import_pgm_button,
            self.generate_pgm_button,
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
        self.generate_pgm_button.setEnabled(bool(editable and selected and selected.pcd_path))
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

    def _generate_pgm_selected(self) -> None:
        map_id = self._selected_id()
        definition = self.repository.map_by_id(map_id) if map_id else None
        if definition is None or not definition.pcd_path:
            return
        if self._pgm_thread and self._pgm_thread.is_alive():
            QMessageBox.information(self, "生成 PGM", "已有 PGM 生成任务正在运行")
            return
        if definition.pgm is not None:
            answer = QMessageBox.question(
                self,
                "替换 PGM",
                "该地图已有 PGM 图层。确定使用点云生成结果替换吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        dialog = PgmGenerationDialog(definition, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.generate_pgm_button.setEnabled(False)
        self.generate_pgm_button.setText("生成中…")
        self._pgm_thread = threading.Thread(
            target=self._run_pgm_generation,
            args=(definition.map_id, dialog.options()),
            name="ccs-pgm-generation",
            daemon=True,
        )
        self._pgm_thread.start()

    def _run_pgm_generation(self, map_id: str, options: PcdToPgmOptions) -> None:
        try:
            definition = self.repository.generate_pgm(map_id, options)
            self.pgm_generation_finished.emit(map_id, definition, None)
        except Exception as exc:
            self.pgm_generation_finished.emit(map_id, None, str(exc))

    def _on_pgm_generation_finished(self, map_id: str, definition: object, error: object) -> None:
        self.generate_pgm_button.setText("从 PCD 生成 PGM")
        selected = self.repository.map_by_id(map_id)
        self.generate_pgm_button.setEnabled(
            self.edit_mode and self._selected_id() == map_id and bool(selected and selected.pcd_path)
        )
        if error:
            QMessageBox.critical(self, "PGM 生成失败", str(error))
        elif isinstance(definition, MapDefinition):
            QMessageBox.information(self, "生成 PGM", "PGM 栅格图层已生成并保存")

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
                for device in self.source.snapshots():
                    profile = self.source.profile(device.device_id)
                    if profile is not None and profile.active_map_id == map_id:
                        self.source.set_device_active_map(device.device_id, None)
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
        if map_id != self.current_map_id:
            self._remote_trail.clear()
            self._trail_device_id = None
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
        self.detail_page.set_map(definition, pcd_path, pgm_yaml_path, self.repository.active_map_id() == map_id)
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote is not None and remote.map_id != map_id:
            remote = None
        relocalization_snapshots = {}
        map_complete = False
        if self.relocalization_service is not None:
            map_complete = self.relocalization_service.map_complete(map_id)
            relocalization_snapshots = {
                item.device_id.casefold(): self.relocalization_service.snapshot(map_id, item.device_id)
                for item in self.devices
            }
        self.detail_page.set_devices(
            self.devices, remote, relocalization_snapshots, map_complete
        )
        self.page_stack.setCurrentWidget(self.detail_page)
        self._refresh_device_overlays()
        self._offer_interrupted_session(map_id)
        if self.relocalization_service is not None and self.relocalization_service.available:
            for item in self.devices:
                if item.connection_status != ConnectionStatus.ONLINE:
                    continue
                try:
                    self.relocalization_service.negotiate(map_id, item.device_id)
                except (RuntimeError, ValueError):
                    continue

    def show_list(self) -> None:
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote is not None and remote.navigation_locked:
            QMessageBox.information(self, "遥控建图进行中", "请先结束或取消建图任务")
            return
        if self.mapping_service is not None and self.mapping_service.active:
            self.mapping_service.interrupt_mapping("返回地图列表")
        self.detail_page.stop_videos()
        self.detail_page.viewer.set_relocalization_picker(False)
        self.detail_page.viewer.clear()
        self.current_map_id = None
        self.page_stack.setCurrentWidget(self.list_page)

    def set_active(self, active: bool) -> None:
        if not active:
            self.detail_page.stop_videos()
        if (not active and self.mapping_service is not None and self.mapping_service.active
                and not self.mapping_service.remote.active):
            self.mapping_service.interrupt_mapping("切换主导航")

    def _toggle_mapping(self) -> None:
        if self.mapping_service is None or not self.current_map_id:
            return
        remote = self.mapping_service.current_remote_snapshot
        if remote is not None:
            try:
                if remote.state == "ready":
                    self.mapping_service.begin_remote_mapping()
                elif remote.state in {"mapping", "warning"}:
                    self.mapping_service.stop_remote_mapping()
                elif remote.state == "failed":
                    self.mapping_service.retry_remote_preparation()
            except RuntimeError as exc:
                QMessageBox.warning(self, "遥控建图", str(exc))
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
        dialog = MappingSetupDialog(
            mode, candidates, self.fusion_repository.algorithms(enabled_only=True),
            name=definition.name, name_editable=False, parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_ids = set(dialog.selected_device_ids())
        selected_devices = [item for item in candidates if item.device_id in selected_ids]
        try:
            if len(selected_devices) != 1:
                raise ValueError("v0.16.0 只支持单机遥控建图")
            self._remote_trail.clear()
            self.mapping_service.prepare_remote_mapping(definition, selected_devices[0])
        except (RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "建图启动失败", str(exc))

    def _on_mapping_updated(self, snapshot: MapBuildingSessionSnapshot) -> None:
        if snapshot.map_id == self.current_map_id:
            self.detail_page.update_mapping(snapshot)

    def _on_mapping_preview(self, session_id: str, points: object, bounds: object) -> None:
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote and remote.session_id == session_id and remote.map_id == self.current_map_id:
            self.detail_page.viewer.set_live_points(points, bounds)
            return
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

    def _on_remote_mapping_updated(self, snapshot: RemoteMappingSnapshot) -> None:
        if snapshot.map_id == self.current_map_id:
            self.detail_page.update_remote_mapping(snapshot)
            self.detail_page.set_devices(self.devices, snapshot)
            if snapshot.session_id != self._selected_remote_session_id:
                self._selected_remote_session_id = snapshot.session_id
                self.detail_page.device_panel.select_remote_device(snapshot)

    def _cancel_remote_mapping(self) -> None:
        if self.mapping_service is None:
            return
        remote = self.mapping_service.current_remote_snapshot
        if remote is not None:
            answer = QMessageBox.question(
                self, "强制结束建图",
                "将立即停止端侧 FAST_LIO 并丢弃本次实时建图数据，不生成 PCD/PGM 成果。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.mapping_service.abort_remote_mapping()
            except RuntimeError as exc:
                QMessageBox.warning(self, "强制结束建图", str(exc))
                return
        else:
            self.mapping_service.cancel_remote_mapping()
        self._remote_trail.clear()
        self.detail_page.viewer.clear_live_points()
        self.detail_page.viewer.set_selected_device_pose(None)
        self.detail_page.viewer.set_device_trail([])

    def _on_remote_telemetry(self, device_id: str, snapshot: object) -> None:
        self._latest_telemetry[device_id.casefold()] = snapshot

    def _on_map_device_selected(self, device_id: str) -> None:
        if device_id != self._trail_device_id:
            self._trail_device_id = device_id
            self._remote_trail.clear()
            self.detail_page.viewer.set_device_trail([])
        self._refresh_device_overlays()
        if self.relocalization_service is not None and self.current_map_id is not None:
            snapshot = self.relocalization_service.snapshot(self.current_map_id, device_id)
            self.detail_page.update_relocalization(snapshot)
            self.detail_page.viewer.set_relocalization_picker(
                snapshot.can_submit_pose and snapshot.status in {
                    RelocalizationStatus.AWAITING_POSE, RelocalizationStatus.FAILED,
                }
            )

    def _download_relocalization_map(self, device_id: str) -> None:
        if self.relocalization_service is None or self.current_map_id is None:
            return
        try:
            self.relocalization_service.download_map(self.current_map_id, device_id)
        except (RelocalizationArtifactError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "地图下发失败", str(exc))

    def _handle_relocalization_action(self, device_id: str) -> None:
        if self.relocalization_service is None or self.current_map_id is None:
            return
        snapshot = self.relocalization_service.snapshot(self.current_map_id, device_id)
        try:
            if snapshot.can_submit_pose:
                pose = self.detail_page.viewer.relocalization_pose()
                if pose is None:
                    raise RuntimeError("无法从当前视图解算初始位姿")
                self.relocalization_service.submit_initial_pose(
                    self.current_map_id, device_id, *pose
                )
            else:
                self.relocalization_service.start_stack(self.current_map_id, device_id)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "重定位操作失败", str(exc))

    def _on_relocalization_updated(self, snapshot) -> None:
        if snapshot.map_id != self.current_map_id:
            return
        self.detail_page.update_relocalization(snapshot)
        picker = (
            snapshot.device_id == self.detail_page.device_panel.selected_device_id
            and snapshot.status in {
                RelocalizationStatus.AWAITING_POSE,
                RelocalizationStatus.FAILED,
            }
            and snapshot.can_submit_pose
        )
        self.detail_page.viewer.set_relocalization_picker(picker)
        if snapshot.status == RelocalizationStatus.SUCCEEDED:
            self._refresh_device_overlays()

    def _refresh_device_overlays(self) -> None:
        if (
            self.current_map_id is None
            or self.page_stack.currentWidget() != self.detail_page
        ):
            return
        definition = self.repository.map_by_id(self.current_map_id)
        if definition is None:
            return
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote is not None and remote.map_id != self.current_map_id:
            remote = None
        online = [
            item for item in self.devices
            if item.connection_status == ConnectionStatus.ONLINE
        ]
        selected_id = self.detail_page.device_panel.selected_device_id
        markers: list[DeviceMapMarker] = []
        selected_pose: PoseTelemetry | None = None
        for device in online:
            snapshot = self._latest_telemetry.get(device.device_id.casefold())
            if snapshot is None and self.telemetry_store is not None:
                snapshot = self.telemetry_store.telemetry(device.device_id)
            profile = self.source.profile(device.device_id)
            binding = next((
                item for item in getattr(profile, "map_bindings", ())
                if item.map_id == definition.map_id
            ), None)
            pose_source = binding.pose_source if binding is not None else "global_pose"
            pose = getattr(snapshot, pose_source, None)
            source_frame = (
                binding.odom_frame
                if binding is not None else remote.frame_id
                if remote is not None and remote.device_id.casefold() == device.device_id.casefold()
                else device.frame_id
            )
            note = ""
            transformed: PoseTelemetry | None = None
            values = () if pose is None else (
                pose.x, pose.y, pose.z, pose.roll, pose.pitch, pose.yaw,
            )
            if pose is None or float(getattr(pose, "sample_age_seconds", 999.0)) > 2.0:
                note = "UDP 全局位姿缺失或已超时"
            elif not all(math.isfinite(float(value)) for value in values):
                note = "UDP 全局位姿包含无效值"
            elif not source_frame:
                note = "未上报位姿坐标系"
            elif binding is not None:
                transformed = transform_pose_by_binding(pose, binding.map_from_odom)
                note = f"{binding.odom_frame} -> {binding.map_frame} · 已重定位"
            elif source_frame.casefold() == definition.frame_id.casefold():
                transformed = pose
                note = f"已定位到 {definition.frame_id}"
            else:
                transforms = (
                    definition.build_provenance.transforms
                    if definition.build_provenance is not None else ()
                )
                matches = [
                    item for item in transforms
                    if item.source_id.casefold() == device.device_id.casefold()
                ]
                if len(matches) == 1:
                    transformed = transform_device_pose(pose, matches[0])
                    note = f"{source_frame} -> {definition.frame_id}"
                elif len(matches) > 1:
                    note = f"存在重复的 {source_frame} -> {definition.frame_id} 变换"
                else:
                    note = f"未配置 {source_frame} -> {definition.frame_id} 变换"
            self.detail_page.device_panel.set_frame_note(device.device_id, note)
            if transformed is None:
                continue
            markers.append(DeviceMapMarker(
                device.device_id, device.device_name,
                transformed.x, transformed.y, transformed.z,
                status=device.task_status.value,
                marker_shape=device.map_marker_shape,
                yaw=transformed.yaw,
            ))
            if device.device_id == selected_id:
                selected_pose = transformed
        self.detail_page.viewer.set_device_markers(markers)
        self.detail_page.viewer.set_selected_device_pose(selected_pose)
        if selected_id != self._trail_device_id:
            self._trail_device_id = selected_id
            self._remote_trail.clear()
        if selected_pose is not None:
            position = (selected_pose.x, selected_pose.y, selected_pose.z)
            if not self._remote_trail or math.dist(self._remote_trail[-1], position) >= 0.02:
                self._remote_trail.append(position)
                if len(self._remote_trail) > 10000:
                    del self._remote_trail[:-10000]
        self.detail_page.viewer.set_device_trail(self._remote_trail)

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
            device_count = len(job.get("devices", job.get("device_sessions", ())))
            if device_count <= 1:
                try:
                    self.mapping_service.save_interrupted_job(
                        map_id, str(job["job_id"]),
                        self.fusion_repository.default_algorithm().algorithm_id,
                    )
                    self.show_detail(map_id)
                except (MapRepositoryError, MapFusionError, RuntimeError, ValueError) as exc:
                    QMessageBox.critical(self, "临时单机建图处理失败", str(exc))
                return
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

    def _on_active_map_changed(self, definition: object) -> None:
        self._render_cards()
        if self.current_map_id and self.page_stack.currentWidget() is self.detail_page:
            current = self.repository.map_by_id(self.current_map_id)
            if current:
                self.detail_page.set_map(current, self._map_pcd_path(current), self._map_pgm_path(current), current.map_id == self.repository.active_map_id())

    def _map_pcd_path(self, definition: MapDefinition):
        try:
            return self.repository.pcd_path(definition.map_id) if definition.pcd_path else None
        except MapRepositoryError:
            return None

    def _map_pgm_path(self, definition: MapDefinition):
        try:
            return self.repository.pgm_paths(definition.map_id)[0] if definition.pgm else None
        except MapRepositoryError:
            return None

    def _on_devices_updated(self, devices: object) -> None:
        self.devices = list(devices)
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote is not None and remote.map_id != self.current_map_id:
            remote = None
        snapshots = {}
        complete = False
        if self.relocalization_service is not None and self.current_map_id is not None:
            complete = self.relocalization_service.map_complete(self.current_map_id)
            snapshots = {
                item.device_id.casefold(): self.relocalization_service.snapshot(
                    self.current_map_id, item.device_id
                )
                for item in self.devices
            }
        self.detail_page.set_devices(self.devices, remote, snapshots, complete)
        self._refresh_device_overlays()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.detail_page.stop_videos()
        self._device_render_timer.stop()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.page_stack.currentWidget() == self.list_page:
            self._render_cards()
