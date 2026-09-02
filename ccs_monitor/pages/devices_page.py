from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..data_source import DeviceDataSource
from ..device_config import DeviceConfigError
from ..device_dialogs import DeviceTypeTemplateDialog, EditDeviceDialog, NewDeviceDialog
from ..device_migration import DeviceMigrationError, DeviceReferenceMigrationCoordinator
from ..models import ConnectionStatus, DeviceSnapshot
from ..widgets import CardIcon, DeviceCard
from ..styles import ThemeMode, ThemePalette, theme_palette
from .device_detail_page import DeviceDetailPage

if False:  # pragma: no cover - typing-only import without a runtime cycle
    from ..udp_store import UdpTelemetryStore


class DevicesPage(QWidget):
    def __init__(
        self, source: DeviceDataSource, telemetry_store=None, map_repository=None,
        task_repository=None, mapping_service=None, task_execution_service=None,
        relocalization_service=None,
    ) -> None:
        super().__init__()
        self.source = source
        self.telemetry_store = telemetry_store
        self.telemetry_cache: dict[str, object] = {}
        self.map_repository = map_repository
        self.mapping_service = mapping_service
        self.relocalization_service = relocalization_service
        self.migration_coordinator = (
            DeviceReferenceMigrationCoordinator(
                source, map_repository, task_repository, mapping_service,
                task_execution_service, telemetry_store,
            )
            if map_repository is not None and task_repository is not None else None
        )
        self.devices: list[DeviceSnapshot] = source.snapshots()
        self.selected_id: str | None = None
        self.detail_device_id: str | None = None
        self.edit_mode = False
        self.delete_selection: set[str] = set()
        self.card_column_count = 0
        self._reflow_pending = False
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._build()
        self._populate_filters()
        self._render_cards()
        source.devices_updated.connect(self._on_devices_updated)
        if hasattr(source, "logs_changed"):
            source.logs_changed.connect(self._on_logs_changed)
        if telemetry_store is not None:
            telemetry_store.telemetry_updated.connect(self._on_telemetry_updated)
            if not hasattr(source, "logs_changed"):
                telemetry_store.log_recorded.connect(self._on_udp_log_recorded)

        if hasattr(source, "module_status_changed"):
            source.module_status_changed.connect(self._set_module_status)
            self._set_module_status(
                getattr(source, "module_status_message", "MQTT 监测模块正在启动"),
                getattr(source, "module_healthy", False),
            )

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.page_stack = QStackedWidget()
        outer.addWidget(self.page_stack)

        self.list_page = QWidget()
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(16)
        self.page_stack.addWidget(self.list_page)

        self.detail_page = DeviceDetailPage(
            self.source, self.map_repository, self.mapping_service, self.relocalization_service
        )
        self.detail_page.back_requested.connect(self.show_list)
        self.detail_page.status_cards_changed.connect(self._update_status_cards)
        self.detail_page.clear_logs_requested.connect(self.source.clear_device_logs)
        self.detail_page.log_event_requested.connect(self.source.append_external_log)
        self.detail_page.set_status_card_edit_enabled(not self.source.read_only)
        self.detail_page.set_theme(self.theme_palette)
        self.page_stack.addWidget(self.detail_page)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("设备链接与显示")
        title.setObjectName("pageTitle")
        subtitle = QLabel("管理设备档案并查看实时状态与运行详情")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.mqtt_module_card = QFrame()
        self.mqtt_module_card.setObjectName("mqttStatusCard")
        mqtt_layout = QVBoxLayout(self.mqtt_module_card)
        mqtt_layout.setContentsMargins(12, 8, 12, 8)
        mqtt_layout.setSpacing(2)
        self.connection_label = QLabel("MQTT · 正在启动")
        self.connection_label.setObjectName("mqttStatusTitle")
        self.connection_message = QLabel("数据源已加载")
        self.connection_message.setObjectName("muted")
        self.connection_message.setMaximumWidth(300)
        self.connection_message.setWordWrap(True)
        mqtt_header = QHBoxLayout()
        mqtt_header.setSpacing(8)
        self.mqtt_icon = CardIcon("MQTT 状态", icon_name="mqtt", size=24)
        mqtt_header.addWidget(self.mqtt_icon)
        mqtt_header.addWidget(self.connection_label)
        mqtt_header.addStretch()
        mqtt_layout.addLayout(mqtt_header)
        mqtt_layout.addWidget(self.connection_message)
        header.addWidget(self.mqtt_module_card, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.config_banner = QLabel(self.source.config_error or "")
        self.config_banner.setObjectName("configErrorBanner")
        self.config_banner.setWordWrap(True)
        self.config_banner.setVisible(bool(self.source.config_error))
        layout.addWidget(self.config_banner)

        management = QHBoxLayout()
        management.addStretch()
        self.new_button = QPushButton("新建设备")
        self.new_button.setObjectName("primaryButton")
        self.new_button.clicked.connect(self._open_new_device_dialog)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self._toggle_edit_mode)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setVisible(False)
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_selected_devices)
        self.types_button = QPushButton("类型模板")
        self.types_button.clicked.connect(self._open_type_templates)
        management.addWidget(self.types_button)
        management.addWidget(self.new_button)
        management.addWidget(self.edit_button)
        management.addWidget(self.delete_button)
        layout.addLayout(management)
        if self.source.read_only:
            self.new_button.setEnabled(False)
            self.edit_button.setEnabled(False)
        if getattr(getattr(self.source, "type_repository", None), "read_only", False):
            self.types_button.setEnabled(False)

        self.metrics_grid = QGridLayout()
        self.metrics_grid.setHorizontalSpacing(12)
        self.metrics_grid.setVerticalSpacing(12)
        self.metric_icons: list[CardIcon] = []
        self.metric_total, self.total_value = self._metric("设备总数", icon_name="device")
        self.metric_online, self.online_value = self._metric(
            "在线设备", icon_file="devices_online.svg"
        )
        self.metric_alert, self.alert_value = self._metric(
            "需关注", icon_file="devices_warning.svg"
        )
        self.metrics = [self.metric_total, self.metric_online, self.metric_alert]
        layout.addLayout(self.metrics_grid)

        self.toolbar = QGridLayout()
        self.toolbar.setHorizontalSpacing(10)
        self.toolbar.setVerticalSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索设备名称或 ID")
        self.search.setClearButtonEnabled(True)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.textChanged.connect(self._render_cards)
        self.type_filter = self._combo()
        self.type_filter.currentTextChanged.connect(self._render_cards)
        self.status_filter = self._combo()
        self.status_filter.currentTextChanged.connect(self._render_cards)
        self.result_label = QLabel()
        self.result_label.setObjectName("muted")
        self.refresh_button = QPushButton("刷新数据")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.clicked.connect(self.source.refresh)
        layout.addLayout(self.toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_container = QWidget()
        self.card_container.setObjectName("deviceGrid")
        self.card_grid = QGridLayout(self.card_container)
        self.card_grid.setContentsMargins(2, 2, 8, 2)
        self.card_grid.setHorizontalSpacing(14)
        self.card_grid.setVerticalSpacing(14)
        self.scroll.setWidget(self.card_container)
        layout.addWidget(self.scroll, 1)
        self._apply_responsive_layout(1100)

    def _metric(
        self, label_text: str, *, icon_name: str | None = None,
        icon_file: str | None = None,
    ) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("metric")
        frame.setMinimumSize(125, 70)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QGridLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(3)
        icon = CardIcon(label_text, icon_name=icon_name, icon_file=icon_file, size=28)
        self.metric_icons.append(icon)
        value = QLabel("0")
        value.setObjectName("metricValue")
        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        layout.addWidget(icon, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(value, 0, 1)
        layout.addWidget(label, 1, 1)
        layout.setColumnStretch(1, 1)
        return frame, value

    @staticmethod
    def _combo() -> QComboBox:
        combo = QComboBox()
        combo.setMinimumWidth(130)
        return combo

    def _populate_filters(self) -> None:
        selected_type = self.type_filter.currentData()
        selected_status = self.status_filter.currentData()
        self.type_filter.blockSignals(True)
        self.status_filter.blockSignals(True)
        self.type_filter.clear()
        self.type_filter.addItem("全部设备类型", None)
        for device_type in sorted({device.device_type for device in self.devices}):
            self.type_filter.addItem(device_type, device_type)
        self.status_filter.clear()
        self.status_filter.addItem("全部连接状态", None)
        self.status_filter.addItem("在线", ConnectionStatus.ONLINE)
        self.status_filter.addItem("需关注", ConnectionStatus.WARNING)
        self.status_filter.addItem("离线", ConnectionStatus.OFFLINE)
        self.type_filter.setCurrentIndex(max(0, self.type_filter.findData(selected_type)))
        self.status_filter.setCurrentIndex(max(0, self.status_filter.findData(selected_status)))
        self.type_filter.blockSignals(False)
        self.status_filter.blockSignals(False)

    def _on_devices_updated(self, devices: object) -> None:
        self.devices = list(devices)
        self.delete_selection.intersection_update({device.device_id for device in self.devices})
        self._populate_filters()
        self._render_cards()
        if self.detail_device_id:
            detail_device = self.source.device(self.detail_device_id)
            if detail_device:
                self.detail_page.set_device(detail_device, self.source.logs(detail_device.device_id))
            else:
                self.show_list()

    def filtered_devices(self) -> list[DeviceSnapshot]:
        query = self.search.text().strip().lower()
        selected_type = self.type_filter.currentData()
        selected_status = self.status_filter.currentData()
        return [
            device
            for device in self.devices
            if (not query or query in device.device_name.lower() or query in device.device_id.lower())
            and (selected_type is None or device.device_type == selected_type)
            and (selected_status is None or device.connection_status == selected_status)
        ]

    def _render_cards(self) -> None:
        if not hasattr(self, "card_grid"):
            return
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        filtered = self.filtered_devices()
        columns = self._column_count(max(1, self.width() - 56))
        self.card_column_count = columns
        for index, device in enumerate(filtered):
            card = DeviceCard(device)
            card.set_theme(self.theme_palette)
            card.clicked.connect(self._select_device)
            card.double_clicked.connect(self._handle_card_double_click)
            card.selection_changed.connect(self._set_delete_selection)
            card.set_selected(device.device_id == self.selected_id)
            card.set_edit_mode(self.edit_mode, device.device_id in self.delete_selection)
            self.card_grid.addWidget(card, index // columns, index % columns)
            card.show()
        for column in range(columns):
            self.card_grid.setColumnStretch(column, 1)
        self.result_label.setText(f"显示 {len(filtered)} / {len(self.devices)} 台设备")
        self.total_value.setText(str(len(self.devices)))
        self.online_value.setText(str(sum(d.connection_status == ConnectionStatus.ONLINE for d in self.devices)))
        self.alert_value.setText(str(sum(d.connection_status != ConnectionStatus.ONLINE for d in self.devices)))
        if not filtered:
            empty = QLabel("没有匹配的设备\n请调整搜索关键词或筛选条件")
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card_grid.addWidget(empty, 0, 0, 1, columns)

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self.detail_page.set_theme(palette)
        for icon in (*self.metric_icons, self.mqtt_icon):
            icon.set_theme(palette)
        for card in self.card_container.findChildren(DeviceCard):
            card.set_theme(palette)
        self.update()

    def _open_new_device_dialog(self) -> None:
        dialog = NewDeviceDialog(
            self.source.has_device_id, self, templates=self.source.device_type_templates()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.source.create_device(dialog.profile())
        except (DeviceConfigError, ValueError) as exc:
            QMessageBox.critical(self, "设备创建失败", str(exc))

    def _toggle_edit_mode(self) -> None:
        self.edit_mode = not self.edit_mode
        self.delete_selection.clear()
        self.edit_button.setText("取消编辑" if self.edit_mode else "编辑")
        self.delete_button.setVisible(self.edit_mode)
        self.delete_button.setEnabled(False)
        self.new_button.setEnabled(not self.edit_mode and not self.source.read_only)
        self._render_cards()

    def _open_type_templates(self) -> None:
        DeviceTypeTemplateDialog(self.source, self).exec()
        self._populate_filters()
        self._render_cards()

    def _handle_card_double_click(self, device_id: str) -> None:
        if self.edit_mode:
            self._open_edit_device_dialog(device_id)
        else:
            self.show_detail(device_id)

    def _open_edit_device_dialog(self, device_id: str) -> None:
        original = self.source.profile(device_id)
        if original is None:
            return
        dialog = EditDeviceDialog(
            original,
            lambda candidate: (
                candidate.casefold() != device_id.casefold()
                and self.source.has_device_id(candidate)
            ),
            self,
            templates=self.source.device_type_templates(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if self.migration_coordinator is not None:
                updated = self.migration_coordinator.update_device(device_id, dialog.profile())
            else:
                updated = self.source.update_device(device_id, dialog.profile())
            self.selected_id = updated.device_id
            if updated.device_id.casefold() != device_id.casefold():
                QMessageBox.information(
                    self, "设备 ID 已更新",
                    "本地地图和任务引用已迁移。请同步修改端侧设备 ID 后再恢复通信。",
                )
            self._toggle_edit_mode()
        except (DeviceConfigError, DeviceMigrationError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "设备更新失败", str(exc))

    def _set_delete_selection(self, device_id: str, checked: bool) -> None:
        if checked:
            self.delete_selection.add(device_id)
        else:
            self.delete_selection.discard(device_id)
        self.delete_button.setEnabled(bool(self.delete_selection))

    def _delete_selected_devices(self) -> None:
        if not self.delete_selection:
            return
        count = len(self.delete_selection)
        answer = QMessageBox.question(
            self,
            "确认删除设备",
            f"确定永久删除选中的 {count} 台设备吗？此操作会更新本地配置文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.source.delete_devices(set(self.delete_selection))
        except DeviceConfigError as exc:
            QMessageBox.critical(self, "设备删除失败", str(exc))
            return
        if self.edit_mode:
            self._toggle_edit_mode()

    def show_detail(self, device_id: str) -> None:
        if self.edit_mode:
            return
        device = self.source.device(device_id)
        if not device:
            return
        self.detail_device_id = device.device_id
        self.detail_page.set_device(device, self.source.logs(device.device_id))
        telemetry = self.telemetry_cache.get(device.device_id.casefold())
        if telemetry is None and self.telemetry_store is not None:
            telemetry = self.telemetry_store.telemetry(device.device_id)
        if telemetry is not None:
            self.detail_page.set_telemetry(telemetry)
        self.page_stack.setCurrentWidget(self.detail_page)

    def _on_telemetry_updated(self, device_id: str, snapshot: object) -> None:
        self.telemetry_cache[device_id.casefold()] = snapshot
        if self.detail_device_id and device_id.casefold() == self.detail_device_id.casefold():
            self.detail_page.set_telemetry(snapshot)

    def _on_udp_log_recorded(self, device_id: str) -> None:
        if self.detail_device_id and device_id.casefold() == self.detail_device_id.casefold():
            self.detail_page.set_logs(self.source.logs(device_id))

    def _on_logs_changed(self, device_id: str) -> None:
        if self.detail_device_id and device_id.casefold() == self.detail_device_id.casefold():
            self.detail_page.set_logs(self.source.logs(device_id))

    def _update_status_cards(self, device_id: str, status_card_ids: object) -> None:
        try:
            self.source.update_device_status_cards(
                device_id, None if status_card_ids is None else tuple(status_card_ids)
            )
        except (DeviceConfigError, ValueError) as exc:
            QMessageBox.critical(self, "状态卡片保存失败", str(exc))

    def show_list(self) -> None:
        self.detail_page.stop_video()
        self.detail_device_id = None
        self.page_stack.setCurrentWidget(self.list_page)

    def stop_video(self) -> None:
        self.detail_page.stop_video()

    def _apply_responsive_layout(self, width: int) -> None:
        metric_columns = 3 if width >= 650 else 1
        for index, metric in enumerate(self.metrics):
            self.metrics_grid.addWidget(metric, index // metric_columns, index % metric_columns)
        if width >= 900:
            self.toolbar.addWidget(self.search, 0, 0, 1, 2)
            self.toolbar.addWidget(self.type_filter, 0, 2)
            self.toolbar.addWidget(self.status_filter, 0, 3)
            self.toolbar.addWidget(self.result_label, 0, 4)
            self.toolbar.addWidget(self.refresh_button, 0, 5)
        else:
            self.toolbar.addWidget(self.search, 0, 0, 1, 2)
            self.toolbar.addWidget(self.type_filter, 1, 0)
            self.toolbar.addWidget(self.status_filter, 1, 1)
            self.toolbar.addWidget(self.result_label, 2, 0)
            self.toolbar.addWidget(self.refresh_button, 2, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())
        self._schedule_card_reflow()

    @staticmethod
    def _column_count(width: int) -> int:
        return 3 if width >= 1030 else 2 if width >= 650 else 1

    def _schedule_card_reflow(self) -> None:
        if self._column_count(max(1, self.width() - 56)) == self.card_column_count:
            return
        if self._reflow_pending:
            return
        self._reflow_pending = True
        QTimer.singleShot(0, self._run_card_reflow)

    def _run_card_reflow(self) -> None:
        self._reflow_pending = False
        if self._column_count(max(1, self.width() - 56)) != self.card_column_count:
            self._render_cards()

    def _select_device(self, device_id: str) -> None:
        self.selected_id = None if self.selected_id == device_id else device_id
        self._render_cards()

    def _set_module_status(self, message: str, healthy: bool) -> None:
        self.connection_label.setText(f"MQTT · {'正常' if healthy else '故障'}")
        self.connection_message.setText(message)
        self.mqtt_module_card.setProperty("state", "healthy" if healthy else "error")
        self.mqtt_module_card.style().unpolish(self.mqtt_module_card)
        self.mqtt_module_card.style().polish(self.mqtt_module_card)
