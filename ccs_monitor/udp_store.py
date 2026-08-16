from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .models import (
    DeviceLogLevel,
    DeviceTelemetrySnapshot,
    ImuTelemetry,
    PointCloudTelemetry,
    PoseTelemetry,
    SensorStatusTelemetry,
    TelemetryAvailability,
    UdpLinkStatus,
    utc_now,
)
from .udp_config import UdpTelemetryConfig
from .udp_protocol import UdpEnvelope, UdpProtocolError, UdpTelemetryProtocol


LOGGER = logging.getLogger(__name__)


@dataclass
class UdpHeartbeatTracker:
    session_id: str | None = None
    last_heartbeat_monotonic: float | None = None
    warned: bool = False
    errored: bool = False


class UdpTelemetryStore(QObject):
    telemetry_updated = Signal(str, object)
    udp_link_updated = Signal(str, object)
    protocol_warning = Signal(str)
    log_recorded = Signal(str)
    module_status_changed = Signal(str, bool)

    def __init__(
        self,
        config: UdpTelemetryConfig,
        known_device: Callable[[str], bool],
        log_sink: Callable[[str, DeviceLogLevel, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = utc_now,
        start_watchdog: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.protocol = UdpTelemetryProtocol(config)
        self._known_device = known_device
        self._log_sink = log_sink
        self._clock = clock
        self._wall_clock = wall_clock
        self._snapshots: dict[str, DeviceTelemetrySnapshot] = {}
        self._trackers: dict[str, UdpHeartbeatTracker] = {}
        self._sequences: dict[tuple[str, str, str, int | None], int] = {}
        self.module_message = "UDP 遥测模块尚未启动"
        self.module_healthy = False
        self._module_failed = False
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self.check_heartbeats)
        if start_watchdog:
            self._watchdog.start(1000)

    def telemetry(self, device_id: str) -> DeviceTelemetrySnapshot:
        default_status = UdpLinkStatus.MODULE_ERROR if self._module_failed else UdpLinkStatus.UNKNOWN
        sensor_statuses = tuple(
            SensorStatusTelemetry(item.name, item.display_name)
            for item in sorted(self.config.descriptors, key=lambda value: value.name)
            if item.data_type in {"availability", "text_status"}
        )
        return self._snapshots.get(
            device_id,
            DeviceTelemetrySnapshot(
                device_id=device_id,
                udp_link_status=default_status,
                sensor_statuses=sensor_statuses,
                module_message=self.module_message,
            ),
        )

    @Slot(bytes, str, int)
    def process_datagram(self, datagram: bytes, peer_host: str = "", peer_port: int = 0) -> None:
        try:
            event = self.protocol.decode(datagram)
        except UdpProtocolError as exc:
            self._warn(f"UDP 数据报已丢弃（{peer_host}:{peer_port}）：{exc}")
            return
        if not self._known_device(event.device_id):
            self._warn(f"忽略未登记 UDP 设备：{event.device_id}")
            return
        tracker = self._trackers.setdefault(event.device_id, UdpHeartbeatTracker())
        if tracker.session_id != event.session_id:
            self._clear_device_sequences(event.device_id)
            tracker.session_id = event.session_id
        key = (event.device_id, event.session_id, event.message_type, event.level)
        previous = self._sequences.get(key)
        if previous is not None and event.sequence <= previous:
            self._warn(f"丢弃乱序 UDP 帧：{event.device_id} {event.message_type} sequence={event.sequence}")
            return
        self._sequences[key] = event.sequence
        if event.message_type == "heartbeat":
            self._handle_heartbeat(event, tracker)
        else:
            self._handle_telemetry(event)

    def _handle_heartbeat(self, event: UdpEnvelope, tracker: UdpHeartbeatTracker) -> None:
        was_degraded = tracker.warned or tracker.errored
        tracker.last_heartbeat_monotonic = self._clock()
        tracker.warned = False
        tracker.errored = False
        now = self._wall_clock()
        snapshot = replace(
            self.telemetry(event.device_id),
            udp_link_status=UdpLinkStatus.ONLINE,
            last_heartbeat_at=now,
            module_message=self.module_message,
        )
        self._snapshots[event.device_id] = snapshot
        self.udp_link_updated.emit(event.device_id, snapshot.udp_link_status)
        self.telemetry_updated.emit(event.device_id, snapshot)
        if was_degraded:
            self._log(event.device_id, DeviceLogLevel.INFO, "UDP 心跳恢复，链路已重新连接")

    def _handle_telemetry(self, event: UdpEnvelope) -> None:
        snapshot = self.telemetry(event.device_id)
        changes: dict[str, object] = {"last_data_at": self._wall_clock(), "module_message": self.module_message}
        statuses = {item.name: item for item in snapshot.sensor_statuses}
        for name, value in event.payload.items():
            descriptor = self.config.descriptor(name)
            if descriptor is None:
                continue
            if descriptor.data_type == "pose":
                parsed = self._pose(value)
                if name == "global_pose":
                    changes["global_pose"] = parsed
                elif name == "vision_pose":
                    changes["vision_pose"] = parsed
            elif descriptor.data_type == "imu":
                changes["imu"] = self._imu(value)
            elif descriptor.data_type == "pointcloud_status":
                changes["pointcloud"] = PointCloudTelemetry(
                    availability=self._availability(value),
                    estimated_hz=self._optional_float(value.get("estimated_hz")),
                    sample_age_seconds=self._optional_float(value.get("sample_age_seconds")),
                )
            elif descriptor.data_type == "availability":
                statuses[name] = SensorStatusTelemetry(
                    name=name,
                    display_name=descriptor.display_name,
                    availability=self._availability(value),
                    sample_age_seconds=self._optional_float(value.get("sample_age_seconds")),
                )
            elif descriptor.data_type == "text_status":
                statuses[name] = SensorStatusTelemetry(
                    name=name,
                    display_name=descriptor.display_name,
                    availability=self._availability(value),
                    sample_age_seconds=self._optional_float(value.get("sample_age_seconds")),
                    value=str(value["value"]) if value.get("value") is not None else None,
                )
        changes["sensor_statuses"] = tuple(statuses[name] for name in sorted(statuses))
        updated = replace(snapshot, **changes)
        self._snapshots[event.device_id] = updated
        self.telemetry_updated.emit(event.device_id, updated)

    @Slot()
    def check_heartbeats(self) -> None:
        now = self._clock()
        for device_id, tracker in self._trackers.items():
            if tracker.last_heartbeat_monotonic is None:
                continue
            elapsed = now - tracker.last_heartbeat_monotonic
            snapshot = self.telemetry(device_id)
            if elapsed > self.config.error_timeout_seconds and not tracker.errored:
                tracker.warned = True
                tracker.errored = True
                updated = replace(snapshot, udp_link_status=UdpLinkStatus.OFFLINE)
                self._snapshots[device_id] = updated
                self.udp_link_updated.emit(device_id, updated.udp_link_status)
                self.telemetry_updated.emit(device_id, updated)
                self._log(device_id, DeviceLogLevel.ERROR, f"UDP 心跳中断超过 {self.config.error_timeout_seconds:g}s，链路已断开")
            elif elapsed > self.config.warning_timeout_seconds and not tracker.warned:
                tracker.warned = True
                updated = replace(snapshot, udp_link_status=UdpLinkStatus.WARNING)
                self._snapshots[device_id] = updated
                self.udp_link_updated.emit(device_id, updated.udp_link_status)
                self.telemetry_updated.emit(device_id, updated)
                self._log(device_id, DeviceLogLevel.WARNING, f"UDP 心跳中断超过 {self.config.warning_timeout_seconds:g}s")

    @Slot(str, bool)
    def set_module_status(self, message: str, healthy: bool) -> None:
        self.module_message = message
        self.module_healthy = healthy
        self._module_failed = not healthy
        self.module_status_changed.emit(message, healthy)
        if healthy:
            return
        for device_id, snapshot in list(self._snapshots.items()):
            updated = replace(snapshot, udp_link_status=UdpLinkStatus.MODULE_ERROR, module_message=message)
            self._snapshots[device_id] = updated
            self.telemetry_updated.emit(device_id, updated)

    def _clear_device_sequences(self, device_id: str) -> None:
        self._sequences = {key: value for key, value in self._sequences.items() if key[0] != device_id}

    def _log(self, device_id: str, level: DeviceLogLevel, message: str) -> None:
        if self._log_sink is not None:
            self._log_sink(device_id, level, message)
        self.log_recorded.emit(device_id)

    def _warn(self, message: str) -> None:
        LOGGER.warning(message)
        self.protocol_warning.emit(message)

    @staticmethod
    def _pose(value: dict[str, object]) -> PoseTelemetry | None:
        if not value.get("valid"):
            return None
        return PoseTelemetry(
            *(float(value[name]) for name in ("x", "y", "z", "roll", "pitch", "yaw")),
            sample_age_seconds=float(value.get("sample_age_seconds", 0.0)),
        )

    @staticmethod
    def _imu(value: dict[str, object]) -> ImuTelemetry | None:
        if not value.get("valid"):
            return None
        names = (
            "roll", "pitch", "yaw", "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
            "linear_acceleration_x", "linear_acceleration_y", "linear_acceleration_z",
        )
        return ImuTelemetry(
            *(float(value[name]) for name in names),
            sample_age_seconds=float(value.get("sample_age_seconds", 0.0)),
        )

    @staticmethod
    def _availability(value: dict[str, object]) -> TelemetryAvailability:
        try:
            return TelemetryAvailability(str(value.get("status", "unknown")))
        except ValueError:
            return TelemetryAvailability.UNKNOWN

    @staticmethod
    def _optional_float(value: object) -> float | None:
        return None if value is None else float(value)
