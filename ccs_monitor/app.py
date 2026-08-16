from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from .device_config import DeviceConfigRepository
from .data_source import DEFAULT_CONFIG_PATH
from .main_window import MainWindow
from .map_building_config import MapBuildingConfigError, load_map_building_config
from .map_building_services import MapBuildingService
from .map_repository import MapRepository
from .mqtt_config import MqttConfigError, default_mqtt_config, load_mqtt_config
from .mqtt_data_source import MqttDeviceSource
from .mqtt_services import MqttMonitoringRuntime
from .udp_config import UdpConfigError, load_udp_config
from .udp_services import UdpMonitoringRuntime
from .udp_store import UdpTelemetryStore
from .task_config import TaskSystemConfigError, load_task_system_config
from .task_repository import TaskRepository
from .task_services import TaskExecutionService
from .version import __version__


def configure_application_font(app: QApplication) -> None:
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
    app.setApplicationName("CCS Device Monitor")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "ccs_logo.svg")))
    mqtt_error = None
    try:
        mqtt_config = load_mqtt_config()
    except MqttConfigError as exc:
        mqtt_config = default_mqtt_config()
        mqtt_error = str(exc)
    source = MqttDeviceSource(mqtt_config, DeviceConfigRepository(DEFAULT_CONFIG_PATH))
    udp_runtime = None
    udp_store = None
    try:
        udp_config = load_udp_config()
        udp_store = UdpTelemetryStore(
            udp_config,
            source.has_device_id,
            source.append_external_log,
        )
        udp_runtime = UdpMonitoringRuntime(udp_config, udp_store)
    except UdpConfigError as exc:
        udp_error = str(exc)
    else:
        udp_error = None
    map_repository = MapRepository()
    task_repository = TaskRepository()
    task_service = None
    task_error = None
    try:
        task_config = load_task_system_config()
        task_service = TaskExecutionService(task_config, task_repository, source.device)
    except TaskSystemConfigError as exc:
        task_error = str(exc)
    mapping_service = None
    mapping_error = None
    try:
        mapping_config = load_map_building_config()
        mapping_service = MapBuildingService(mapping_config, map_repository)
    except MapBuildingConfigError as exc:
        mapping_error = str(exc)
    window = MainWindow(
        source,
        telemetry_store=udp_store,
        map_repository=map_repository,
        mapping_service=mapping_service,
        task_repository=task_repository,
        task_execution_service=task_service,
    )
    runtime = MqttMonitoringRuntime(mqtt_config, source) if mqtt_error is None else None
    if mqtt_error:
        source.set_module_status(mqtt_error, False)
    else:
        app.aboutToQuit.connect(runtime.stop)
        QTimer.singleShot(0, runtime.start)
    if udp_runtime is not None:
        app.aboutToQuit.connect(udp_runtime.stop)
        QTimer.singleShot(0, udp_runtime.start)
    elif udp_store is not None and udp_error:
        udp_store.set_module_status(udp_error, False)
    if mapping_service is not None:
        app.aboutToQuit.connect(mapping_service.stop)
        QTimer.singleShot(0, mapping_service.start)
    elif mapping_error:
        window.map_page.detail_page.set_mapping_available(False, mapping_error)
    if task_service is not None:
        app.aboutToQuit.connect(task_service.stop)
        QTimer.singleShot(0, task_service.start)
    elif task_error:
        window.task_page.set_execution_available(False, task_error)
    window.show()
    return app.exec()
