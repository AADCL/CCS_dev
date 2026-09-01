import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from ccs_monitor.data_source import SimulatedDeviceSource, simulated_overview
    from ccs_monitor.app import configure_application_font
    from ccs_monitor.device_config import DeviceConfigRepository
    from ccs_monitor.device_dialogs import EditDeviceDialog, NewDeviceDialog
    from ccs_monitor.device_dialogs import StatusCardEditorDialog
    from ccs_monitor.main_window import MainWindow
    from ccs_monitor.map_repository import MapRepository
    from ccs_monitor.pages.map_page import NewMapDialog
    from ccs_monitor.models import (
        ConnectionStatus,
        DeviceLogLevel,
        DeviceSnapshot,
        DeviceTelemetrySnapshot,
        ImuTelemetry,
        PointCloudTelemetry,
        PoseTelemetry,
        SensorStatusTelemetry,
        TelemetryAvailability,
        UdpLinkStatus,
        MapCreatorDevice,
    )
    from ccs_monitor.mqtt_config import MqttMonitoringConfig
    from ccs_monitor.mqtt_data_source import MqttDeviceSource
    from ccs_monitor.ping_service import PingResult
    from ccs_monitor.widgets import DeviceCard
    from ccs_monitor.styles import (
        ThemeMode, build_stylesheet, load_theme_mode, save_theme_mode, theme_palette,
    )
    from ccs_monitor.system_status import (
        SubsystemId, SubsystemState, SystemRuntimeStatusStore,
    )


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_application_font(cls.app)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = DeviceConfigRepository(Path(self.temp_dir.name) / "devices.json")
        self.source = SimulatedDeviceSource(repository)
        self.map_repository = MapRepository(Path(self.temp_dir.name) / "map_server")
        self.system_status_store = SystemRuntimeStatusStore()
        self.window = MainWindow(
            self.source,
            simulated_overview(),
            map_repository=self.map_repository,
            system_status_store=self.system_status_store,
        )
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def test_navigation_contains_five_pages_and_defaults_home(self):
        self.assertEqual(self.window.pages.count(), 5)
        self.assertEqual(self.window.current_page_index, 0)
        self.assertTrue(self.window.nav_buttons[0].isChecked())
        for index in range(5):
            self.window.set_current_page(index)
            self.app.processEvents()
            self.assertEqual(self.window.current_page_index, index)
            self.assertTrue(self.window.nav_buttons[index].isChecked())

    def test_theme_button_switches_theme_without_changing_page(self):
        self.window.set_current_page(4)
        self.window.apply_theme(ThemeMode.NIGHT, persist=False)
        self.app.processEvents()
        self.assertEqual(self.window.theme_mode, ThemeMode.NIGHT)
        self.assertEqual(self.window.theme_toggle_button.text(), "")
        self.assertEqual(self.window.theme_toggle_button.property("appIconName"), "outdoor")
        self.assertEqual(self.window.theme_toggle_button.property("appIconMode"), "night")
        for button, text, icon_name in zip(
            self.window.nav_buttons,
            self.window.PAGE_NAMES,
            self.window.PAGE_ICON_NAMES,
        ):
            self.assertEqual(button.text(), text)
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.property("appIconName"), icon_name)
            self.assertEqual(button.property("appIconMode"), "night")
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Base).name(),
            theme_palette(ThemeMode.NIGHT).input_background.lower(),
        )
        night_style = build_stylesheet(ThemeMode.NIGHT)
        day_style = build_stylesheet(ThemeMode.DAY)
        self.assertNotEqual(night_style, day_style)
        self.window.theme_toggle_button.click()
        self.app.processEvents()
        self.assertEqual(self.window.theme_mode, ThemeMode.DAY)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Base).name(),
            theme_palette(ThemeMode.DAY).input_background.lower(),
        )
        self.assertEqual(self.window.current_page_index, 4)
        self.assertTrue(self.window.nav_buttons[4].isChecked())
        self.assertEqual(self.window.theme_toggle_button.text(), "")
        self.assertEqual(self.window.theme_toggle_button.property("appIconName"), "indoor")
        self.assertEqual(self.window.theme_toggle_button.property("appIconMode"), "day")
        for button in (
            self.window.devices_page.detail_page.back_button,
            self.window.map_page.detail_page.back_button,
            self.window.task_page.editor.back_button,
        ):
            self.assertEqual(button.text(), "返回")
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.property("appIconName"), "back")
            self.assertEqual(button.property("appIconMode"), "day")
        for button, text, icon_name in zip(
            self.window.nav_buttons,
            self.window.PAGE_NAMES,
            self.window.PAGE_ICON_NAMES,
        ):
            self.assertEqual(button.text(), text)
            self.assertEqual(button.property("appIconName"), icon_name)
            self.assertEqual(button.property("appIconMode"), "day")
        deliver_button = self.window.task_page.editor.deliver_button
        deliver_enabled = deliver_button.isEnabled()
        self.assertEqual(deliver_button.text(), "保存下发")
        self.assertEqual(deliver_button.property("appIconName"), "upload")
        self.assertEqual(deliver_button.property("appIconMode"), "day")
        self.window.theme_toggle_button.click()
        self.assertEqual(self.window.theme_mode, ThemeMode.NIGHT)
        self.assertEqual(deliver_button.property("appIconMode"), "night")
        self.assertEqual(deliver_button.isEnabled(), deliver_enabled)

    def test_theme_mode_round_trips_through_settings(self):
        settings_path = Path(self.temp_dir.name) / "theme.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        save_theme_mode(ThemeMode.DAY, settings)
        self.assertEqual(load_theme_mode(settings), ThemeMode.DAY)
        save_theme_mode(ThemeMode.NIGHT, settings)
        self.assertEqual(load_theme_mode(settings), ThemeMode.NIGHT)

    def test_home_metrics_follow_device_updates(self):
        devices = [
            DeviceSnapshot("A", "Alpha", "UGV", connection_status=ConnectionStatus.ONLINE),
            DeviceSnapshot("B", "Beta", "AMR", connection_status=ConnectionStatus.OFFLINE),
        ]
        self.source.devices_updated.emit(devices)
        self.app.processEvents()
        self.assertEqual(self.window.home_page.online_card.value_label.text(), "1")
        self.assertEqual(self.window.home_page.offline_card.value_label.text(), "1")

    def test_home_status_cards_show_icons_and_preserve_state_during_theme_switch(self):
        page = self.window.home_page
        self.window.apply_theme(ThemeMode.NIGHT, persist=False)
        metric_labels = {
            page.online_card: ("在线设备数", "devices_online.svg", "static"),
            page.offline_card: ("离线设备数", "devices_offline.svg", "static"),
            page.maps_card: ("本地地图数量", "mapstorage", "night"),
            page.tasks_card: ("任务执行次数", "tasks", "night"),
        }
        for card, (caption, icon_name, icon_mode) in metric_labels.items():
            self.assertEqual(card.caption_label.text(), caption)
            self.assertFalse(card.icon_label.pixmap().isNull())
            self.assertEqual(card.icon_label.property("appIconName"), icon_name)
            self.assertEqual(card.icon_label.property("appIconMode"), icon_mode)

        expected_runtime_icons = {
            SubsystemId.NTP: "time",
            SubsystemId.MQTT_BROKER: "mqttbroker",
            SubsystemId.MQTT_SUBSCRIBER: "mqtt",
            SubsystemId.UDP_TELEMETRY: "UDP",
            SubsystemId.SRT_FFMPEG: "camera",
        }
        for subsystem_id, icon_name in expected_runtime_icons.items():
            card = page.runtime_cards[subsystem_id]
            self.assertEqual(card.title.text(), card.toolTip().splitlines()[0])
            self.assertIsNotNone(card.icon_label)
            self.assertFalse(card.icon_label.pixmap().isNull())
            self.assertEqual(card.icon_label.property("appIconName"), icon_name)
            self.assertEqual(card.icon_label.property("appIconMode"), "night")

        for subsystem_id in set(SubsystemId) - set(expected_runtime_icons):
            self.assertIsNone(page.runtime_cards[subsystem_id].icon_label)

        status = self.system_status_store.update(
            SubsystemId.NTP, SubsystemState.HEALTHY, "NTP 已同步"
        )
        metric_values = [card.value_label.text() for card in page.metric_cards]
        self.window.apply_theme(ThemeMode.DAY, persist=False)
        self.app.processEvents()
        self.assertEqual(
            [card.value_label.text() for card in page.metric_cards], metric_values
        )
        self.assertEqual(page.runtime_cards[SubsystemId.NTP].message.text(), status.message)
        self.assertEqual(page.runtime_cards[SubsystemId.NTP].property("state"), "healthy")
        self.assertEqual(page.maps_card.icon_label.property("appIconMode"), "day")
        for subsystem_id in expected_runtime_icons:
            self.assertEqual(
                page.runtime_cards[subsystem_id].icon_label.property("appIconMode"), "day"
            )

    def test_device_page_reflows_cards(self):
        page = self.window.devices_page
        page.resize(620, 500)
        page._render_cards()
        self.assertEqual(page.card_column_count, 1)
        page.resize(1200, 700)
        page._render_cards()
        self.assertEqual(page.card_column_count, 3)

    def test_map_page_defaults_to_repository_backed_empty_list(self):
        page = self.window.map_page
        self.assertEqual(page.page_stack.currentWidget(), page.list_page)
        self.assertEqual(page.maps, [])
        self.assertEqual(self.window.home_page.maps_card.value_label.text(), "0")

    def test_map_repository_signal_updates_list_search_and_home_count(self):
        device = self.source.snapshots()[0]
        definition = self.map_repository.create(
            "总装车间点云",
            [MapCreatorDevice(device.device_id, device.device_name, device.device_type)],
        )
        self.app.processEvents()
        page = self.window.map_page
        self.assertEqual(len(page.maps), 1)
        self.assertEqual(self.window.home_page.maps_card.value_label.text(), "1")
        page.search.setText("不存在")
        self.assertEqual(page.filtered_maps(), [])
        page.search.setText("总装")
        self.assertEqual(page.filtered_maps()[0].map_id, definition.map_id)
        page.show_detail(definition.map_id)
        self.assertEqual(page.page_stack.currentWidget(), page.detail_page)
        self.assertIn("尚未导入", page.detail_page.viewer.status.text())
        page.show_list()

    def test_new_map_dialog_device_selector_uses_the_dark_theme(self):
        dialog = NewMapDialog(self.source.snapshots(), self.window)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.device_scroll.objectName(), "mapDeviceScroll")
        self.assertEqual(dialog.device_scroll.viewport().objectName(), "mapDeviceViewport")
        self.assertEqual(len(dialog.device_checks), len(self.source.snapshots()))
        dialog.close()

    def test_minimum_window_size(self):
        self.assertEqual(self.window.minimumWidth(), 800)
        self.assertEqual(self.window.minimumHeight(), 600)

    def test_edit_mode_shows_checkboxes_and_deletes_confirmed_device(self):
        page = self.window.devices_page
        self.window.set_current_page(1)
        page._toggle_edit_mode()
        self.app.processEvents()
        cards = [card for card in page.card_container.findChildren(DeviceCard) if not card.isHidden()]
        self.assertTrue(cards)
        self.assertFalse(cards[0].checkbox.isHidden())
        target_id = cards[0].device.device_id
        cards[0].checkbox.setChecked(True)
        self.assertTrue(page.delete_button.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page._delete_selected_devices()
        self.assertIsNone(self.source.device(target_id))
        self.assertFalse(page.edit_mode)

    def test_new_device_dialog_requires_current_ip_test(self):
        dialog = NewDeviceDialog(self.source.has_device_id, self.window)
        dialog.name_input.setText("Loopback device")
        dialog.id_input.setText("UGV-099")
        dialog.ip_input.setText("127.0.0.1")
        self.assertFalse(dialog.create_button.isEnabled())
        dialog._handle_ping_result(PingResult("127.0.0.1", True, "设备可达"))
        self.assertTrue(dialog.create_button.isEnabled())
        dialog.ip_input.setText("127.0.0.2")
        self.assertFalse(dialog.create_button.isEnabled())

    def test_new_device_dialog_detects_duplicate_id(self):
        dialog = NewDeviceDialog(self.source.has_device_id, self.window)
        dialog.name_input.setText("Duplicate")
        dialog.id_input.setText("ugv-042")
        dialog.ip_input.setText("127.0.0.1")
        self.assertIn("已存在", dialog._validate_fields())

    def test_edit_device_reuses_current_ping_but_ip_change_requires_retest(self):
        profile = self.source.profile("UGV-042")
        dialog = EditDeviceDialog(
            profile,
            lambda candidate: candidate.casefold() != profile.device_id.casefold()
            and self.source.has_device_id(candidate),
            self.window,
            templates=self.source.device_type_templates(),
        )
        self.assertTrue(dialog.create_button.isEnabled())
        dialog.ip_input.setText("127.0.0.50")
        self.app.processEvents()
        self.assertFalse(dialog.create_button.isEnabled())
        dialog.close()

    def test_detail_page_and_log_filter(self):
        page = self.window.devices_page
        page.show_detail("USV-003")
        self.assertEqual(page.page_stack.currentWidget(), page.detail_page)
        self.assertEqual(page.detail_page.fields["设备地址"].text(), "192.168.40.3")
        error_index = page.detail_page.log_filter.findData(DeviceLogLevel.ERROR)
        page.detail_page.log_filter.setCurrentIndex(error_index)
        self.app.processEvents()
        self.assertEqual(page.detail_page.log_list.topLevelItemCount(), 1)
        self.assertEqual(page.detail_page.log_list.topLevelItem(0).text(1), "ERROR")
        page.detail_page.clear_logs_button.click()
        self.app.processEvents()
        self.assertEqual(self.source.logs("USV-003"), [])
        page.show_list()
        self.assertEqual(page.page_stack.currentWidget(), page.list_page)

    def test_detail_page_displays_udp_telemetry_without_map_position(self):
        page = self.window.devices_page
        page.show_detail("UGV-042")
        detail = page.detail_page
        telemetry = DeviceTelemetrySnapshot(
            device_id="UGV-042",
            udp_link_status=UdpLinkStatus.ONLINE,
            global_pose=PoseTelemetry(1, 2, 3, 4, 5, 6),
            vision_pose=PoseTelemetry(7, 8, 9, 10, 11, 12),
            imu=ImuTelemetry(1, 2, 3, 0.1, 0.2, 0.3, 9.1, 9.2, 9.3),
            pointcloud=PointCloudTelemetry(TelemetryAvailability.AVAILABLE, 9.8, 0.1),
            sensor_statuses=(
                SensorStatusTelemetry("livox_driver", "Livox 驱动状态", TelemetryAvailability.AVAILABLE, 0.1),
                SensorStatusTelemetry("fastlio2", "FAST-LIO2 定位状态", TelemetryAvailability.AVAILABLE, 0.2),
                SensorStatusTelemetry("pgm_mapping", "PGM 地图生成状态", TelemetryAvailability.UNAVAILABLE, 3.1),
                SensorStatusTelemetry("octomap_mapping", "八叉树地图生成状态", TelemetryAvailability.UNKNOWN),
                SensorStatusTelemetry("occupancy_grid_mapping", "占据栅格图生成状态", TelemetryAvailability.AVAILABLE, 0.3),
                SensorStatusTelemetry("mapping_mode", "当前建图模式", TelemetryAvailability.AVAILABLE, 0.2, "增量建图"),
            ),
        )
        detail.set_telemetry(telemetry)
        detail._render_telemetry()
        self.app.processEvents()
        self.assertNotIn("地图位置", detail.fields)
        self.assertEqual(detail.fields["UDP 链路状态"].text(), "在线")
        self.assertEqual(detail.global_pose_values["X"].text(), "--")
        self.assertEqual(detail.vision_pose_values["X"].text(), "1.000 m")
        self.assertEqual(len(detail.status_cards), 4)
        self.assertIn("9.8 Hz", detail.status_cards["livox_driver"].meta.text())
        self.assertEqual(detail.status_cards["fastlio2"].value.text(), "运行正常")
        self.assertEqual(detail.status_cards["pgm_mapping"].value.text(), "等待数据")
        self.assertEqual(detail.status_cards["mapping_mode"].value.text(), "模式未知")
        replacement = DeviceTelemetrySnapshot(device_id="ugv-042", udp_link_status=UdpLinkStatus.WARNING)
        page._on_telemetry_updated("ugv-042", replacement)
        self.assertIs(detail.pending_telemetry, replacement)

    def test_detail_page_timer_renders_latest_high_frequency_snapshot(self):
        page = self.window.devices_page
        page.show_detail("UGV-042")
        detail = page.detail_page
        for index in range(20):
            detail.set_telemetry(DeviceTelemetrySnapshot(
                device_id="UGV-042",
                udp_link_status=UdpLinkStatus.ONLINE,
                global_pose=PoseTelemetry(index, 2, 3, 4, 5, 6),
                vision_pose=PoseTelemetry(index + 100, 8, 9, 10, 11, 12),
                imu=ImuTelemetry(index, 2, 3, 0.1, 0.2, 0.3, 9.1, 9.2, 9.3),
            ))
        detail._render_telemetry()
        self.assertEqual(detail.global_pose_values["X"].text(), "--")
        self.assertEqual(detail.vision_pose_values["X"].text(), "19.000 m")
        self.assertEqual(detail.imu_values["Roll"].text(), "19.000°")

    def test_status_card_editor_selection_and_device_binding(self):
        dialog = StatusCardEditorDialog(("fastlio2",), self.window)
        self.assertTrue(dialog.checkboxes["fastlio2"].isChecked())
        dialog.checkboxes["mapping_mode"].setChecked(True)
        self.assertEqual(dialog.selected_ids(), ("fastlio2", "mapping_mode"))
        page = self.window.devices_page
        page.show_detail("UGV-042")
        page._update_status_cards("UGV-042", ("fastlio2", "mapping_mode"))
        self.app.processEvents()
        self.assertEqual(page.detail_page.device.status_card_ids, ("fastlio2", "mapping_mode"))
        self.assertEqual(tuple(page.detail_page.status_cards), ("fastlio2", "mapping_mode"))
        page._update_status_cards("UGV-042", ())
        self.app.processEvents()
        self.assertEqual(page.detail_page.status_cards, {})
        self.assertFalse(page.detail_page.status_empty.isHidden())

    def test_detail_layout_switches_and_navigation_stops_video(self):
        page = self.window.devices_page
        page.show_detail("UAV-017")
        detail = page.detail_page
        detail.resize(900, 700)
        detail._apply_responsive_layout(900)
        self.app.processEvents()
        self.assertEqual(detail.detail_layout_mode, "stacked")
        self.assertFalse(detail.info_panel.geometry().intersects(detail.video_panel.geometry()))
        detail._layout_status_cards(650)
        self.assertEqual(detail.status_card_column_count, 1)
        detail._layout_status_cards(900)
        self.assertEqual(detail.status_card_column_count, 2)
        detail._layout_status_cards(1300)
        self.assertEqual(detail.status_card_column_count, 3)
        detail.resize(1280, 700)
        detail._apply_responsive_layout(1280)
        self.app.processEvents()
        self.assertEqual(detail.detail_layout_mode, "wide")
        self.assertFalse(detail.info_panel.geometry().intersects(detail.video_panel.geometry()))
        with patch.object(page, "stop_video") as stop_video:
            self.window.set_current_page(0)
            stop_video.assert_called_once()

    def test_visible_card_double_click_signal_opens_detail(self):
        page = self.window.devices_page
        self.window.set_current_page(1)
        self.app.processEvents()
        card = next(card for card in page.card_container.findChildren(DeviceCard) if not card.isHidden())
        card.double_clicked.emit(card.device.device_id)
        self.assertEqual(page.page_stack.currentWidget(), page.detail_page)
        self.assertEqual(page.detail_device_id, card.device.device_id)

    def test_corrupt_config_disables_management_actions(self):
        corrupt_path = Path(self.temp_dir.name) / "corrupt.json"
        corrupt_path.write_text("{ broken", encoding="utf-8")
        corrupt_source = SimulatedDeviceSource(DeviceConfigRepository(corrupt_path))
        corrupt_window = MainWindow(corrupt_source, simulated_overview())
        corrupt_window.show()
        corrupt_window.set_current_page(1)
        self.app.processEvents()
        page = corrupt_window.devices_page
        self.assertTrue(page.config_banner.isVisible())
        self.assertFalse(page.new_button.isEnabled())
        self.assertFalse(page.edit_button.isEnabled())
        corrupt_window.close()
        corrupt_window.deleteLater()

    def test_mqtt_module_status_card_and_detail_fields_update(self):
        repository = DeviceConfigRepository(Path(self.temp_dir.name) / "mqtt-devices.json")
        config = MqttMonitoringConfig(
            "127.0.0.1", 1884, 1, "mqtav", 1.0, 2.0, 5.0, 500, "test-ground",
        )
        source = MqttDeviceSource(config, repository, start_watchdog=False)
        window = MainWindow(source, simulated_overview())
        window.show()
        source.set_module_status("MQTT 数据订阅已连接", True)
        status = {
            "schema_version": "1.0",
            "message_type": "status",
            "timestamp": "2026-07-31T09:30:00Z",
            "sequence": 1,
            "device": {"id": "UAV-017", "ip": "192.168.20.17"},
            "health": {
                "fcu_connected": True,
                "armed": True,
                "system_status": 4,
                "flight_mode": "GUIDED",
                "battery": {"percentage": 70.5, "voltage": 15.4, "current": 3.2},
                "mission_status": "running",
            },
        }
        source.process_message("mqtav/UAV-017/status", json.dumps(status).encode())
        window.devices_page.show_detail("UAV-017")
        self.app.processEvents()
        detail = window.devices_page.detail_page
        self.assertIn("订阅已连接", window.devices_page.connection_message.text())
        self.assertEqual(detail.fields["运行模式"].text(), "GUIDED")
        self.assertNotIn("解锁状态", detail.fields)
        self.assertNotIn("MAVLink 系统状态", detail.fields)
        self.assertEqual(detail.fields["电池电压"].text(), "15.4 V")
        self.assertEqual(detail.fields["健康状态"].text(), "正常")
        source.set_module_status("MQTT Broker 启动失败：端口被占用", False)
        self.app.processEvents()
        self.assertEqual(window.devices_page.mqtt_module_card.property("state"), "error")
        window.close()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
