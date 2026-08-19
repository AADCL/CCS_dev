from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from PySide6.QtCore import QObject, QProcess, Signal, Slot

from .models import utc_now
from .srt_video import SrtVideoConfigError, load_srt_video_config, protocols_include_srt


class SubsystemId(str, Enum):
    NTP = "ntp"
    MQTT_BROKER = "mqtt_broker"
    MQTT_SUBSCRIBER = "mqtt_subscriber"
    UDP_TELEMETRY = "udp_telemetry"
    MAP_BUILDING = "map_building"
    TASK_CONTROL = "task_control"
    SRT_FFMPEG = "srt_ffmpeg"
    MAP_REPOSITORY = "map_repository"
    TASK_REPOSITORY = "task_repository"


class SubsystemState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    ERROR = "error"


SUBSYSTEM_NAMES = {
    SubsystemId.NTP: "NTP 时间服务器",
    SubsystemId.MQTT_BROKER: "MQTT Broker",
    SubsystemId.MQTT_SUBSCRIBER: "MQTT 数据订阅",
    SubsystemId.UDP_TELEMETRY: "UDP 高频遥测",
    SubsystemId.MAP_BUILDING: "实时建图 / PGM",
    SubsystemId.TASK_CONTROL: "UDP 任务控制",
    SubsystemId.SRT_FFMPEG: "FFmpeg / SRT",
    SubsystemId.MAP_REPOSITORY: "地图仓储",
    SubsystemId.TASK_REPOSITORY: "任务仓储",
}


@dataclass(frozen=True)
class SubsystemStatus:
    subsystem_id: SubsystemId
    state: SubsystemState
    message: str
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def display_name(self) -> str:
        return SUBSYSTEM_NAMES[self.subsystem_id]


class SystemRuntimeStatusStore(QObject):
    status_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._statuses = {
            subsystem_id: SubsystemStatus(
                subsystem_id, SubsystemState.STARTING, "等待服务启动"
            )
            for subsystem_id in SubsystemId
        }

    def status(self, subsystem_id: SubsystemId | str) -> SubsystemStatus:
        return self._statuses[SubsystemId(subsystem_id)]

    def statuses(self) -> tuple[SubsystemStatus, ...]:
        return tuple(self._statuses[item] for item in SubsystemId)

    def update(
        self, subsystem_id: SubsystemId | str, state: SubsystemState | str, message: str
    ) -> SubsystemStatus:
        status = SubsystemStatus(
            SubsystemId(subsystem_id), SubsystemState(state), message.strip() or "无状态说明"
        )
        if self._statuses.get(status.subsystem_id) == status:
            return status
        self._statuses[status.subsystem_id] = status
        self.status_changed.emit(status)
        return status

    @Slot(str, bool)
    def update_ntp(self, message: str, healthy: bool) -> None:
        self.update(SubsystemId.NTP, SubsystemState.HEALTHY if healthy else SubsystemState.ERROR, message)

    @Slot(str, bool)
    def update_mqtt_broker(self, message: str, healthy: bool) -> None:
        self.update(
            SubsystemId.MQTT_BROKER,
            SubsystemState.HEALTHY if healthy else SubsystemState.ERROR,
            message,
        )

    @Slot(str, bool)
    def update_mqtt_subscriber(self, message: str, healthy: bool) -> None:
        self.update(
            SubsystemId.MQTT_SUBSCRIBER,
            SubsystemState.HEALTHY if healthy else SubsystemState.DEGRADED,
            message,
        )

    @Slot(str, bool)
    def update_udp(self, message: str, healthy: bool) -> None:
        self.update(
            SubsystemId.UDP_TELEMETRY,
            SubsystemState.HEALTHY if healthy else SubsystemState.ERROR,
            message,
        )

    @Slot(bool, str)
    def update_mapping(self, healthy: bool, message: str) -> None:
        self.update(
            SubsystemId.MAP_BUILDING,
            SubsystemState.HEALTHY if healthy else SubsystemState.ERROR,
            message,
        )

    @Slot(bool, str)
    def update_task_control(self, healthy: bool, message: str) -> None:
        self.update(
            SubsystemId.TASK_CONTROL,
            SubsystemState.HEALTHY if healthy else SubsystemState.ERROR,
            message,
        )


class SrtCapabilityProbe(QObject):
    def __init__(self, store: SystemRuntimeStatusStore, parent: QObject | None = None) -> None:
        super().__init__(parent or store)
        self.store = store
        self.process: QProcess | None = None
        self._failed = False

    @Slot()
    def start(self) -> None:
        if self.process is not None:
            return
        try:
            config = load_srt_video_config()
        except SrtVideoConfigError as exc:
            self.store.update(SubsystemId.SRT_FFMPEG, SubsystemState.ERROR, str(exc))
            return
        self.store.update(
            SubsystemId.SRT_FFMPEG, SubsystemState.STARTING, "正在检查 FFmpeg SRT 输入能力"
        )
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.errorOccurred.connect(self._on_error)
        self.process.finished.connect(self._on_finished)
        self.process.start(config.ffmpeg_executable, ["-hide_banner", "-protocols"])

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart:
            return
        self._failed = True
        executable = self.process.program() if self.process is not None else "ffmpeg"
        self.store.update(
            SubsystemId.SRT_FFMPEG, SubsystemState.ERROR, f"未找到 FFmpeg：{executable}"
        )

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        process, self.process = self.process, None
        if process is None or self._failed:
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        if exit_code != 0:
            detail = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
            self.store.update(
                SubsystemId.SRT_FFMPEG,
                SubsystemState.ERROR,
                detail or "FFmpeg 协议检查失败",
            )
        elif not protocols_include_srt(output):
            self.store.update(
                SubsystemId.SRT_FFMPEG, SubsystemState.ERROR, "当前 FFmpeg 未启用 SRT 输入协议"
            )
        else:
            self.store.update(
                SubsystemId.SRT_FFMPEG, SubsystemState.HEALTHY, "FFmpeg 已就绪，支持 SRT 输入"
            )
