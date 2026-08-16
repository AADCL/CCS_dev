from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .models import DEVICE_STATUS_CARD_DEFINITIONS, DeviceAvailability, DeviceProfile
from .ping_service import PingResult, PingWorker


class NewDeviceDialog(QDialog):
    def __init__(self, id_exists: Callable[[str], bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.id_exists = id_exists
        self._tested_ip: str | None = None
        self._ping_result: PingResult | None = None
        self._last_tested_at: datetime | None = None
        self._worker: PingWorker | None = None
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
        self.type_input.addItems(["UGV", "UAV", "AMR", "USV"])
        self.id_input = QLineEdit()
        self.id_input.setObjectName("deviceIdInput")
        self.id_input.setPlaceholderText("例如：UGV-001")
        self.ip_input = QLineEdit()
        self.ip_input.setObjectName("deviceIpInput")
        self.ip_input.setPlaceholderText("例如：192.168.1.10")
        form.addRow("设备名称", self.name_input)
        form.addRow("设备类型", self.type_input)
        form.addRow("设备 ID", self.id_input)
        form.addRow("设备 IP", self.ip_input)
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
            device_type=self.type_input.currentText(),
            ip_address=self.ip_input.text().strip(),
            availability=(
                DeviceAvailability.AVAILABLE
                if self._ping_result.reachable
                else DeviceAvailability.UNAVAILABLE
            ),
            last_tested_at=self._last_tested_at,
        )


class StatusCardEditorDialog(QDialog):
    def __init__(self, selected_ids: tuple[str, ...], parent: QWidget | None = None) -> None:
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

    def _set_all(self, checked: bool) -> None:
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(checked)

    def selected_ids(self) -> tuple[str, ...]:
        return tuple(
            definition.card_id
            for definition in DEVICE_STATUS_CARD_DEFINITIONS
            if self.checkboxes[definition.card_id].isChecked()
        )
