from __future__ import annotations

import logging
import sys
from .runtime_paths import resource_root, prepare_storage, configure_logging, application_root
from .installation import installation_lock
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer

from .device_config import DeviceConfigRepository
from .data_source import DEFAULT_CONFIG_PATH
from .main_window import MainWindow
from .map_building_config import MapBuildingConfigError, load_map_building_config
from .map_building_services import MapBuildingService
from .map_repository import MapRepository
from .relocalization_config import RelocalizationConfigError, load_relocalization_config
from .relocalization_services import RelocalizationService
from .mqtt_config import MqttConfigError, default_mqtt_config, load_mqtt_config
from .mqtt_data_source import MqttDeviceSource
from .mqtt_services import MqttMonitoringRuntime
from .ntp_config import NtpConfigError, load_ntp_config
from .ntp_services import NtpServerService
from .udp_config import UdpConfigError, load_udp_config
from .udp_services import UdpMonitoringRuntime
from .udp_store import UdpTelemetryStore
from .task_config import TaskSystemConfigError, load_task_system_config
from .task_repository import TaskRepository
from .task_services import TaskExecutionService
from .system_status import (
    SrtCapabilityProbe, SubsystemId, SubsystemState, SystemRuntimeStatusStore,
)
from .version import __version__


LOGGER = logging.getLogger(__name__)


def configure_application_font(app: QApplication) -> None:
    bundled_font = resource_root() / "ccs_monitor/assets/fonts/NotoSansCJK-Regular.ttc"
    if bundled_font.is_file():
        QFontDatabase.addApplicationFont(str(bundled_font))
    preferred_families = ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "SimHei")
    available = set(QFontDatabase.families())
    for family in preferred_families:
        if family in available:
            app.setFont(QFont(family, 10))
            return

    for font_path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")):
        if not font_path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            app.setFont(QFont(families[0], 10))
            return


def main() -> int:
    app = QApplication(sys.argv)
    configure_application_font(app)
    try:
        prepare_storage()
        configure_logging()
        storage_lock = installation_lock(application_root())
        storage_lock.__enter__()
    except (OSError, RuntimeError) as exc:
        QMessageBox.critical(None, "CCS 启动失败", str(exc))
        return 1
    app.setApplicationName("CCS Device Monitor")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(resource_root() / "ccs_monitor" / "assets" / "ccs_logo.svg")))
    system_status = SystemRuntimeStatusStore(app)
    mqtt_error = None
    try:
        mqtt_config = load_mqtt_config()
    except MqttConfigError as exc:
        mqtt_config = default_mqtt_config()
        mqtt_error = str(exc)
        system_status.update(SubsystemId.MQTT_BROKER, SubsystemState.ERROR, mqtt_error)
        system_status.update(SubsystemId.MQTT_SUBSCRIBER, SubsystemState.ERROR, mqtt_error)
    source = MqttDeviceSource(mqtt_config, DeviceConfigRepository(DEFAULT_CONFIG_PATH))
    ntp_runtime = None
    try:
        ntp_config = load_ntp_config()
        if ntp_config.enabled:
            ntp_runtime = NtpServerService(ntp_config)
        else:
            system_status.update(SubsystemId.NTP, SubsystemState.DISABLED, "NTP Server 已在配置中禁用")
    except NtpConfigError as exc:
        LOGGER.error("NTP Server 配置无效：%s", exc)
        system_status.update(SubsystemId.NTP, SubsystemState.ERROR, str(exc))
    udp_runtime = None
    udp_store = None
    try:
        udp_config = load_udp_config()
        udp_store = UdpTelemetryStore(
            udp_config,
            source.has_device_id,
            source.append_external_log,
            canonical_device_id=lambda device_id: (
                snapshot.device_id if (snapshot := source.device(device_id)) is not None else None
            ),
        )
        udp_runtime = UdpMonitoringRuntime(udp_config, udp_store)
    except UdpConfigError as exc:
        udp_error = str(exc)
        system_status.update(SubsystemId.UDP_TELEMETRY, SubsystemState.ERROR, udp_error)
    else:
        udp_error = None
    map_repository = MapRepository()
    task_repository = TaskRepository()

    def update_map_repository_status(_items=None) -> None:
        maps = map_repository.maps()
        damaged = sum(bool(item.error_message) for item in maps)
        state = SubsystemState.DEGRADED if damaged else SubsystemState.HEALTHY
        message = f"地图仓储可用 · {len(maps)} 张地图"
        if damaged:
            message += f" · {damaged} 张损坏"
        system_status.update(SubsystemId.MAP_REPOSITORY, state, message)

    def update_task_repository_status(_items=None) -> None:
        tasks = task_repository.tasks()
        damaged = sum(bool(item.error_message) for item in tasks)
        state = SubsystemState.DEGRADED if damaged else SubsystemState.HEALTHY
        message = f"任务仓储可用 · {len(tasks)} 个任务"
        if damaged:
            message += f" · {damaged} 个损坏"
        system_status.update(SubsystemId.TASK_REPOSITORY, state, message)

    map_repository.maps_updated.connect(update_map_repository_status)
    task_repository.tasks_updated.connect(update_task_repository_status)
    update_map_repository_status()
    update_task_repository_status()
    task_service = None
    task_error = None
    try:
        task_config = load_task_system_config()
        task_service = TaskExecutionService(
            task_config, task_repository, source.device,
            active_map_id_getter=map_repository.active_map_id,
        )
    except TaskSystemConfigError as exc:
        task_error = str(exc)
        system_status.update(SubsystemId.TASK_CONTROL, SubsystemState.ERROR, task_error)
    mapping_service = None
    mapping_error = None
    try:
        mapping_config = load_map_building_config()
        mapping_service = MapBuildingService(mapping_config, map_repository)
    except MapBuildingConfigError as exc:
        mapping_error = str(exc)
        system_status.update(SubsystemId.MAP_BUILDING, SubsystemState.ERROR, mapping_error)
    relocalization_service = None
    relocalization_error = None
    try:
        relocalization_config = load_relocalization_config()
        relocalization_service = RelocalizationService(
            relocalization_config, map_repository, source
        )
    except RelocalizationConfigError as exc:
        relocalization_error = str(exc)
        system_status.update(SubsystemId.RELOCALIZATION, SubsystemState.ERROR, relocalization_error)
    window = MainWindow(
        source,
        telemetry_store=udp_store,
        map_repository=map_repository,
        mapping_service=mapping_service,
        relocalization_service=relocalization_service,
        task_repository=task_repository,
        task_execution_service=task_service,
        system_status_store=system_status,
    )
    runtime = MqttMonitoringRuntime(mqtt_config, source) if mqtt_error is None else None
    source.module_status_changed.connect(system_status.update_mqtt_subscriber)
    if ntp_runtime is not None:
        ntp_runtime.status_changed.connect(system_status.update_ntp)
        ntp_runtime.status_changed.connect(
            lambda message, healthy: LOGGER.log(
                logging.INFO if healthy else logging.ERROR, message
            )
        )
        app.aboutToQuit.connect(ntp_runtime.stop)
        ntp_runtime.start()
    if mqtt_error:
        source.set_module_status(mqtt_error, False)
    else:
        runtime.broker.status_changed.connect(system_status.update_mqtt_broker)
        app.aboutToQuit.connect(runtime.stop)
        QTimer.singleShot(0, runtime.start)
    if udp_runtime is not None:
        udp_store.module_status_changed.connect(system_status.update_udp)
        app.aboutToQuit.connect(udp_runtime.stop)
        QTimer.singleShot(0, udp_runtime.start)
    elif udp_store is not None and udp_error:
        udp_store.set_module_status(udp_error, False)
    if mapping_service is not None:
        mapping_service.availability_changed.connect(system_status.update_mapping)
        app.aboutToQuit.connect(mapping_service.stop)
        QTimer.singleShot(0, mapping_service.start)
    elif mapping_error:
        window.map_page.detail_page.set_mapping_available(False, mapping_error)
    if relocalization_service is not None:
        relocalization_service.availability_changed.connect(system_status.update_relocalization)
        app.aboutToQuit.connect(relocalization_service.stop)
        QTimer.singleShot(0, relocalization_service.start)
    if task_service is not None:
        task_service.availability_changed.connect(system_status.update_task_control)
        app.aboutToQuit.connect(task_service.stop)
        QTimer.singleShot(0, task_service.start)
    elif task_error:
        window.task_page.set_execution_available(False, task_error)
    srt_probe = SrtCapabilityProbe(system_status)
    QTimer.singleShot(0, srt_probe.start)
    window.show()
    try:
        return app.exec()
    finally:
        storage_lock.__exit__(None, None, None)
