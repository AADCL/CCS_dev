from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .data_source import DeviceDataSource, simulated_overview
from .map_repository import MapRepository
from .models import SystemOverview
from .pages import CommandDashboardPage, DevicesPage, HomePage, MapPage, TaskPage
from .task_repository import TaskRepository
from .styles import (
    ThemeMode,
    build_qt_palette,
    build_stylesheet,
    load_theme_mode,
    save_theme_mode,
    theme_palette,
)
from .system_status import SystemRuntimeStatusStore
from .version import __version__


class MainWindow(QMainWindow):
    PAGE_NAMES = ("首页", "设备", "地图", "任务", "指控大屏")
    theme_changed = Signal(object)

    def __init__(
        self,
        source: DeviceDataSource,
        overview: SystemOverview | None = None,
        telemetry_store=None,
        map_repository: MapRepository | None = None,
        mapping_service=None,
        relocalization_service=None,
        task_repository: TaskRepository | None = None,
        task_execution_service=None,
        system_status_store: SystemRuntimeStatusStore | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.overview = overview or simulated_overview()
        self.telemetry_store = telemetry_store
        self.map_repository = map_repository or MapRepository()
        self.mapping_service = mapping_service
        self.relocalization_service = relocalization_service
        self.task_repository = task_repository or TaskRepository()
        self.task_execution_service = task_execution_service
        self.system_status_store = system_status_store
        self.dashboard_fullscreen = False
        self._dashboard_was_maximized = False
        self._theme_settings = QSettings("CCS", "CCS Device Monitor")
        self.theme_mode = load_theme_mode(self._theme_settings)
        self.theme_palette = theme_palette(self.theme_mode)
        self.setObjectName("mainWindow")
        self.setWindowIcon(QIcon(str(self._logo_path())))
        self.setWindowTitle(f"多异构智能体指挥与控制系统 · v{__version__}")
        self.setMinimumSize(800, 600)
        self.resize(1280, 820)
        QApplication.instance().setPalette(build_qt_palette(self.theme_mode))
        QApplication.instance().setStyleSheet(build_stylesheet(self.theme_mode))
        self._build()
        self.apply_theme(self.theme_mode, persist=False)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.navigation = QFrame()
        self.navigation.setObjectName("navigation")
        nav_layout = QHBoxLayout(self.navigation)
        nav_layout.setContentsMargins(22, 10, 22, 10)
        nav_layout.setSpacing(6)
        brand_icon = QLabel()
        brand_icon.setObjectName("brandIcon")
        brand_icon.setPixmap(QIcon(str(self._logo_path())).pixmap(28, 28))
        brand_icon.setToolTip("CCS 多异构智能体指挥与控制系统")
        nav_layout.addWidget(brand_icon)
        brand = QLabel("CCS")
        brand.setObjectName("brand")
        nav_layout.addWidget(brand)
        nav_layout.addSpacing(22)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, name in enumerate(self.PAGE_NAMES):
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setProperty("pageIndex", index)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            nav_layout.addWidget(button)
        nav_layout.addStretch()
        self.theme_toggle_button = QPushButton()
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        nav_layout.addWidget(self.theme_toggle_button)
        version = QLabel(f"v{__version__}")
        version.setObjectName("navVersion")
        nav_layout.addWidget(version)
        layout.addWidget(self.navigation)

        self.pages = QStackedWidget()
        self.home_page = HomePage(
            self.source, self.overview, self.map_repository, self.task_repository,
            self.system_status_store,
        )
        self.devices_page = DevicesPage(
            self.source, self.telemetry_store, self.map_repository, self.task_repository,
            self.mapping_service, self.task_execution_service, self.relocalization_service,
        )
        self.map_page = MapPage(
            self.source,
            self.overview,
            self.map_repository,
            mapping_service=self.mapping_service,
            relocalization_service=self.relocalization_service,
            telemetry_store=self.telemetry_store,
        )
        self.task_page = TaskPage(
            self.source,
            self.map_repository,
            self.task_repository,
            self.task_execution_service,
            self.telemetry_store,
        )
        self.command_page = CommandDashboardPage(
            self.source,
            self.map_repository,
            self.telemetry_store,
            task_repository=self.task_repository,
            execution_service=self.task_execution_service,
        )
        self.command_page.fullscreen_requested.connect(self.set_dashboard_fullscreen)
        if self.mapping_service is not None:
            self.mapping_service.remote_navigation_locked.connect(self._set_mapping_navigation_lock)
        for page in (self.home_page, self.devices_page, self.map_page, self.task_page, self.command_page):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.nav_group.idClicked.connect(self.set_current_page)
        self.set_current_page(0)

    def _toggle_theme(self) -> None:
        next_mode = ThemeMode.DAY if self.theme_mode == ThemeMode.NIGHT else ThemeMode.NIGHT
        self.apply_theme(next_mode)

    def apply_theme(self, mode: ThemeMode | str, persist: bool = True) -> None:
        self.theme_mode = ThemeMode(mode)
        self.theme_palette = theme_palette(self.theme_mode)
        QApplication.instance().setPalette(build_qt_palette(self.theme_mode))
        QApplication.instance().setStyleSheet(build_stylesheet(self.theme_mode))
        for page in (self.home_page, self.devices_page, self.map_page, self.task_page, self.command_page):
            set_theme = getattr(page, "set_theme", None)
            if set_theme is not None:
                set_theme(self.theme_palette)
        self.theme_toggle_button.setText(
            "切换夜间" if self.theme_mode == ThemeMode.DAY else "切换日间"
        )
        self.theme_toggle_button.setToolTip(
            "当前为日间主题，点击切换至夜间主题"
            if self.theme_mode == ThemeMode.DAY
            else "当前为夜间主题，点击切换至日间主题"
        )
        if persist:
            save_theme_mode(self.theme_mode, self._theme_settings)
        self.theme_changed.emit(self.theme_palette)
        self.update()

    def set_current_page(self, index: int) -> None:
        if not 0 <= index < self.pages.count():
            return
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote is not None and remote.navigation_locked and index != 2:
            self.nav_buttons[2].setChecked(True)
            return
        if index != 1:
            self.devices_page.stop_video()
        self.map_page.set_active(index == 2)
        self.task_page.set_active(index == 3)
        self.command_page.set_active(index == 4)
        if index != 4 and self.dashboard_fullscreen:
            self.set_dashboard_fullscreen(False)
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def set_dashboard_fullscreen(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled and self.current_page_index != 4:
            return
        if enabled == self.dashboard_fullscreen:
            return
        self.dashboard_fullscreen = enabled
        self.command_page.set_fullscreen_state(enabled)
        self.navigation.setVisible(not enabled)
        if enabled:
            self._dashboard_was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self._dashboard_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()

    def closeEvent(self, event) -> None:  # noqa: N802
        remote = self.mapping_service.current_remote_snapshot if self.mapping_service else None
        if remote is not None and remote.navigation_locked:
            answer = QMessageBox.question(
                self,
                "遥控建图进行中",
                "关闭应用将取消当前遥控建图任务，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.mapping_service.cancel_remote_mapping("用户关闭应用")
        self.devices_page.stop_video()
        self.map_page.set_active(False)
        self.task_page.set_active(False)
        self.command_page.set_active(False)
        super().closeEvent(event)

    def _set_mapping_navigation_lock(self, locked: bool) -> None:
        for index, button in enumerate(self.nav_buttons):
            button.setEnabled(not locked or index == 2)
        if locked and self.current_page_index != 2:
            self.set_current_page(2)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.dashboard_fullscreen:
            self.set_dashboard_fullscreen(False)
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _logo_path() -> Path:
        return Path(__file__).resolve().parent / "assets" / "ccs_logo.svg"

    @property
    def current_page_index(self) -> int:
        return self.pages.currentIndex()
