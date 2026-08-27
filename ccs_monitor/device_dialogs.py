from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .models import (
    DEVICE_STATUS_CARD_DEFINITIONS, DeviceAvailability, DeviceProfile,
    DeviceTypeTemplate, MapMarkerShape,
)
from .device_types import DeviceTypeConfigError
from .ping_service import PingResult, PingWorker
from .widgets import NoButtonSpinBox


class NewDeviceDialog(QDialog):
    def __init__(self, id_exists: Callable[[str], bool], parent: QWidget | None = None,
                 templates: list[DeviceTypeTemplate] | None = None) -> None:
        super().__init__(parent)
        self.id_exists = id_exists
        self._tested_ip: str | None = None
        self._ping_result: PingResult | None = None
        self._last_tested_at: datetime | None = None
        self._worker: PingWorker | None = None
        self.templates = templates or [DeviceTypeTemplate(item, item) for item in ("UGV", "UAV", "AMR", "USV")]
        self.setWindowTitle("新建设备")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        title = QLabel("新建设备")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        self.name_input = QLineEdit()
        self.name_input.setObjectName("deviceNameInput")
        self.name_input.setPlaceholderText("例如：巡检车 Alpha")
        self.type_input = QComboBox()
        self.type_input.setObjectName("deviceTypeInput")
        for template in self.templates:
            self.type_input.addItem(f"{template.display_name}（{template.type_id}）", template.type_id)
        self.id_input = QLineEdit()
        self.id_input.setObjectName("deviceIdInput")
        self.id_input.setPlaceholderText("例如：UGV-001")
        self.ip_input = QLineEdit()
        self.ip_input.setObjectName("deviceIpInput")
        self.ip_input.setPlaceholderText("例如：192.168.1.10")
        self.srt_port_input = NoButtonSpinBox()
        self.srt_port_input.setRange(1, 65535)
        self.srt_port_input.setValue(9000)
        self.srt_latency_input = NoButtonSpinBox()
        self.srt_latency_input.setRange(20, 8000)
        self.srt_latency_input.setValue(120)
        self.srt_latency_input.setSuffix(" ms")
        form.addRow("设备名称", self.name_input)
        form.addRow("设备类型", self.type_input)
        form.addRow("设备 ID", self.id_input)
        form.addRow("设备 IP", self.ip_input)
        form.addRow("SRT 端口", self.srt_port_input)
        form.addRow("SRT 延迟", self.srt_latency_input)
        root.addLayout(form)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.test_button.setObjectName("testConnectionButton")
        self.test_button.clicked.connect(self._start_ping)
        self.test_result = QLabel("尚未测试")
        self.test_result.setObjectName("pingResult")
        self.test_result.setProperty("state", "idle")
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        root.addLayout(test_row)

        self.validation_label = QLabel()
        self.validation_label.setObjectName("validationError")
        self.validation_label.setWordWrap(True)
        root.addWidget(self.validation_label)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        self.create_button = QPushButton("创建")
        self.create_button.setObjectName("primaryButton")
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self._accept_if_valid)
        actions.addWidget(cancel_button)
        actions.addWidget(self.create_button)
        root.addLayout(actions)

        self.ip_input.textChanged.connect(self._invalidate_test)

    def _validate_fields(self) -> str | None:
        name = self.name_input.text().strip()
        device_id = self.id_input.text().strip().upper()
        ip_address = self.ip_input.text().strip()
        if not name or not device_id or not ip_address:
            return "设备名称、设备 ID 和设备 IP 均不能为空"
        if self.id_exists(device_id):
            return f"设备 ID {device_id} 已存在"
        try:
            ipaddress.ip_address(ip_address)
        except ValueError:
            return "请输入有效的 IPv4 或 IPv6 地址"
        return None

    def _invalidate_test(self) -> None:
        self._tested_ip = None
        self._ping_result = None
        self._last_tested_at = None
        self.create_button.setEnabled(False)
        self.test_result.setText("尚未测试")
        self.test_result.setProperty("state", "idle")
        self.test_result.style().unpolish(self.test_result)
        self.test_result.style().polish(self.test_result)
        self.validation_label.clear()

    def _start_ping(self) -> None:
        error = self._validate_fields()
        if error:
            self.validation_label.setText(error)
            return
        ip_address = self.ip_input.text().strip()
        self.validation_label.clear()
        self.test_button.setEnabled(False)
        self.create_button.setEnabled(False)
        self.test_result.setText("正在测试连接...")
        worker = PingWorker(ip_address)
        worker.signals.finished.connect(self._handle_ping_result)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _handle_ping_result(self, result: PingResult) -> None:
        self.test_button.setEnabled(True)
        if result.ip_address != self.ip_input.text().strip():
            return
        self._tested_ip = result.ip_address
        self._ping_result = result
        self._last_tested_at = datetime.now(timezone.utc)
        self.test_result.setText(result.message)
        self.test_result.setProperty("state", "success" if result.reachable else "warning")
        self.test_result.style().unpolish(self.test_result)
        self.test_result.style().polish(self.test_result)
        self.create_button.setEnabled(True)

    def _accept_if_valid(self) -> None:
        error = self._validate_fields()
        if error:
            self.validation_label.setText(error)
            self.create_button.setEnabled(False)
            return
        if self._ping_result is None or self._tested_ip != self.ip_input.text().strip():
            self.validation_label.setText("请先完成当前 IP 的连接测试")
            self.create_button.setEnabled(False)
            return
        self.accept()

    def profile(self) -> DeviceProfile:
        if self._ping_result is None or self._last_tested_at is None:
            raise RuntimeError("设备尚未完成连接测试")
        return DeviceProfile(
            device_id=self.id_input.text().strip().upper(),
            device_name=self.name_input.text().strip(),
            device_type=str(self.type_input.currentData()),
            ip_address=self.ip_input.text().strip(),
            availability=(
                DeviceAvailability.AVAILABLE
                if self._ping_result.reachable
                else DeviceAvailability.UNAVAILABLE
            ),
            last_tested_at=self._last_tested_at,
            srt_port=self.srt_port_input.value(),
            srt_latency_ms=self.srt_latency_input.value(),
            relocalization_profile=(
                "scout_mini" if str(self.type_input.currentData()).upper() == "UGV"
                else "go2_edu" if str(self.type_input.currentData()).upper() == "QRD"
                else "disabled"
            ),
        )


class EditDeviceDialog(NewDeviceDialog):
    def __init__(
        self, profile: DeviceProfile, id_exists: Callable[[str], bool],
        parent: QWidget | None = None,
        templates: list[DeviceTypeTemplate] | None = None,
    ) -> None:
        self.original_profile = profile
        super().__init__(id_exists, parent, templates)
        self.setWindowTitle("编辑设备")
        title = self.findChild(QLabel, "dialogTitle")
        if title is not None:
            title.setText("编辑设备")
        self.create_button.setText("保存")
        self.name_input.setText(profile.device_name)
        type_index = self.type_input.findData(profile.device_type)
        if type_index >= 0:
            self.type_input.setCurrentIndex(type_index)
        self.id_input.setText(profile.device_id)
        self.ip_input.setText(profile.ip_address)
        self.srt_port_input.setValue(profile.srt_port)
        self.srt_latency_input.setValue(profile.srt_latency_ms)
        if profile.last_tested_at is not None:
            self._tested_ip = profile.ip_address
            self._ping_result = PingResult(
                profile.ip_address,
                profile.availability == DeviceAvailability.AVAILABLE,
                "沿用最近一次连接测试",
            )
            self._last_tested_at = profile.last_tested_at
            self.test_result.setText("沿用最近一次连接测试")
            self.test_result.setProperty("state", "success")
            self.create_button.setEnabled(self._validate_fields() is None)

    def profile(self) -> DeviceProfile:
        profile = super().profile()
        return DeviceProfile(
            profile.device_id, profile.device_name, profile.device_type,
            profile.ip_address, profile.availability, profile.last_tested_at,
            self.original_profile.status_card_ids, profile.srt_port,
            profile.srt_latency_ms,
            self.original_profile.relocalization_profile,
            self.original_profile.map_bindings,
            self.original_profile.active_map_id,
        )


class StatusCardEditorDialog(QDialog):
    def __init__(self, selected_ids: tuple[str, ...], parent: QWidget | None = None,
                 inherited: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑数据状态卡片")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.checkboxes: dict[str, QCheckBox] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        title = QLabel("数据接收状态卡片")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel("选择该设备详情页需要展示的状态。配置将保存到 devices.json。")
        description.setObjectName("muted")
        description.setWordWrap(True)
        root.addWidget(description)
        self.inherit_checkbox = QCheckBox("跟随设备类型模板")
        self.inherit_checkbox.setChecked(inherited)
        self.inherit_checkbox.toggled.connect(self._set_custom_enabled)
        root.addWidget(self.inherit_checkbox)
        self.source_label = QLabel()
        self.source_label.setObjectName("muted")
        root.addWidget(self.source_label)
        selected = set(selected_ids)
        for definition in DEVICE_STATUS_CARD_DEFINITIONS:
            checkbox = QCheckBox(definition.display_name)
            checkbox.setChecked(definition.card_id in selected)
            checkbox.setObjectName("statusCardOption")
            self.checkboxes[definition.card_id] = checkbox
            root.addWidget(checkbox)
        selection_actions = QHBoxLayout()
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(True))
        clear_all = QPushButton("清空")
        clear_all.clicked.connect(lambda: self._set_all(False))
        selection_actions.addWidget(select_all)
        selection_actions.addWidget(clear_all)
        selection_actions.addStretch()
        root.addLayout(selection_actions)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)
        self._set_custom_enabled(not inherited)

    def _set_custom_enabled(self, custom_enabled: bool) -> None:
        enabled = not self.inherit_checkbox.isChecked()
        self.source_label.setText(
            "当前来源：设备自定义" if enabled else "当前来源：设备类型模板（模板修改后自动更新）"
        )
        for checkbox in self.checkboxes.values():
            checkbox.setEnabled(enabled)

    def _set_all(self, checked: bool) -> None:
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(checked)

    def selected_ids(self) -> tuple[str, ...]:
        return tuple(
            definition.card_id
            for definition in DEVICE_STATUS_CARD_DEFINITIONS
            if self.checkboxes[definition.card_id].isChecked()
        )

    def status_card_override(self) -> tuple[str, ...] | None:
        return None if self.inherit_checkbox.isChecked() else self.selected_ids()


class DeviceTypeTemplateDialog(QDialog):
    """Create and edit persistent type-level presentation defaults."""

    def __init__(self, source, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.templates = source.device_type_templates()
        self.current_id: str | None = None
        self.pending_icon: str | None = None
        self.setWindowTitle("设备类型模板")
        self.setModal(True)
        self.resize(820, 560)
        self.setMinimumSize(700, 500)
        self._build()
        self._reload()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(18)
        left = QVBoxLayout()
        left.addWidget(QLabel("设备类型"))
        self.template_list = QListWidget()
        self.template_list.setObjectName("secondaryList")
        self.template_list.setMinimumWidth(220)
        self.template_list.currentItemChanged.connect(self._select_template)
        left.addWidget(self.template_list, 1)
        add_button = QPushButton("新增模板")
        add_button.clicked.connect(self._new_template)
        left.addWidget(add_button)
        root.addLayout(left)

        editor = QVBoxLayout()
        title = QLabel("模板设置")
        title.setObjectName("dialogTitle")
        editor.addWidget(title)
        form = QFormLayout()
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("例如 UAV_HEAVY")
        self.name_input = QLineEdit()
        self.shape_input = QComboBox()
        self.shape_input.addItem("箭头", MapMarkerShape.ARROW)
        self.shape_input.addItem("立方体", MapMarkerShape.CUBE)
        self.shape_input.addItem("球体", MapMarkerShape.SPHERE)
        form.addRow("类型 ID", self.id_input)
        form.addRow("显示名称", self.name_input)
        form.addRow("地图显示", self.shape_input)
        editor.addLayout(form)
        icon_row = QHBoxLayout()
        self.icon_preview = QLabel("无图标")
        self.icon_preview.setObjectName("iconPreview")
        self.icon_preview.setFixedSize(88, 88)
        self.icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_row.addWidget(self.icon_preview)
        upload = QPushButton("上传图标")
        upload.clicked.connect(self._choose_icon)
        remove = QPushButton("移除图标")
        remove.clicked.connect(self._remove_icon)
        icon_row.addWidget(upload)
        icon_row.addWidget(remove)
        icon_row.addStretch()
        editor.addLayout(icon_row)
        editor.addWidget(QLabel("默认功能卡片"))
        self.card_checks: dict[str, QCheckBox] = {}
        for definition in DEVICE_STATUS_CARD_DEFINITIONS:
            checkbox = QCheckBox(definition.display_name)
            self.card_checks[definition.card_id] = checkbox
            editor.addWidget(checkbox)
        editor.addStretch()
        actions = QHBoxLayout()
        delete = QPushButton("删除模板")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self._delete)
        save = QPushButton("保存模板")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(delete)
        actions.addStretch()
        actions.addWidget(close)
        actions.addWidget(save)
        editor.addLayout(actions)
        root.addLayout(editor, 1)

    def _reload(self, select_id: str | None = None) -> None:
        self.templates = self.source.device_type_templates()
        self.template_list.clear()
        for template in self.templates:
            item = QListWidgetItem(f"{template.display_name}\n{template.type_id}")
            item.setData(Qt.ItemDataRole.UserRole, template.type_id)
            if template.icon_path:
                item.setIcon(QIcon(template.icon_path))
            self.template_list.addItem(item)
        target = select_id or self.current_id
        index = next((i for i, item in enumerate(self.templates) if item.type_id == target), 0)
        if self.templates:
            self.template_list.setCurrentRow(index)

    def _select_template(self, current, previous=None) -> None:
        if current is None:
            return
        type_id = current.data(Qt.ItemDataRole.UserRole)
        template = self.source.device_type_template(type_id)
        if template is None:
            return
        self.current_id = template.type_id
        self.pending_icon = template.icon_path
        self.id_input.setText(template.type_id)
        self.id_input.setReadOnly(True)
        self.name_input.setText(template.display_name)
        self.shape_input.setCurrentIndex(max(0, self.shape_input.findData(template.map_marker_shape)))
        for card_id, checkbox in self.card_checks.items():
            checkbox.setChecked(card_id in template.default_status_card_ids)
        self._show_icon(template.icon_path)

    def _new_template(self) -> None:
        self.template_list.clearSelection()
        self.current_id = None
        self.pending_icon = None
        self.id_input.clear()
        self.id_input.setReadOnly(False)
        self.name_input.clear()
        self.shape_input.setCurrentIndex(2)
        for checkbox in self.card_checks.values():
            checkbox.setChecked(True)
        self._show_icon(None)

    def _choose_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择设备类型图标", "", "图标 (*.png *.jpg *.jpeg *.svg)")
        if path:
            self.pending_icon = path
            self._show_icon(path)

    def _remove_icon(self) -> None:
        self.pending_icon = None
        self._show_icon(None)

    def _show_icon(self, path: str | None) -> None:
        if path and not QIcon(path).isNull():
            self.icon_preview.setText("")
            self.icon_preview.setPixmap(QIcon(path).pixmap(72, 72))
        else:
            self.icon_preview.setPixmap(QIcon().pixmap(1, 1))
            self.icon_preview.setText("无图标")

    def _template_from_form(self) -> DeviceTypeTemplate:
        return DeviceTypeTemplate(
            self.id_input.text(), self.name_input.text(),
            None if self.pending_icon is None else (
                self.source.device_type_template(self.current_id).icon_path
                if self.current_id and self.pending_icon == self.source.device_type_template(self.current_id).icon_path
                else None
            ),
            self.shape_input.currentData(),
            tuple(card_id for card_id, checkbox in self.card_checks.items() if checkbox.isChecked()),
        )

    def _save(self) -> None:
        try:
            template = self._template_from_form()
            existing = self.source.device_type_template(self.current_id) if self.current_id else None
            icon_source = self.pending_icon if self.pending_icon and (existing is None or self.pending_icon != existing.icon_path) else None
            saved = (self.source.update_device_type_template(template, icon_source)
                     if self.current_id else self.source.create_device_type_template(template, icon_source))
            self.current_id = saved.type_id
            self._reload(saved.type_id)
        except (DeviceTypeConfigError, ValueError) as exc:
            QMessageBox.critical(self, "模板保存失败", str(exc))

    def _delete(self) -> None:
        if not self.current_id:
            return
        if QMessageBox.question(self, "删除模板", f"确定删除类型模板 {self.current_id}？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.source.delete_device_type_template(self.current_id)
            self.current_id = None
            self._reload()
        except DeviceTypeConfigError as exc:
            QMessageBox.critical(self, "模板删除失败", str(exc))
