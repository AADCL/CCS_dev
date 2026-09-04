from __future__ import annotations

import time
import logging
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QTimer, Signal, Slot

from .device_address import device_address_matches
from .data_source import SimulatedDeviceSource
from .models import (
    ConnectionStatus,
    DeviceLogEntry,
    DeviceLogLevel,
    DeviceProfile,
    DeviceSnapshot,
    HealthStatus,
    LocalizationStatus,
    MapMarkerShape,
    TaskStatus,
    utc_now,
)
from .mqtt_config import MqttMonitoringConfig
from .mqtt_protocol import (
    MqttEvent,
    MqttHeartbeatEvent,
    MqttMessageParser,
    MqttPresenceEvent,
    MqttProtocolError,
    MqttStatusEvent,
)
from .battery_estimation import BatteryEstimator


LOGGER = logging.getLogger(__name__)


@dataclass
class HeartbeatTracker:
    last_heartbeat_monotonic: float | None = None
    disconnect_started_monotonic: float | None = None
    warned: bool = False
    errored: bool = False


class MqttDeviceSource(SimulatedDeviceSource):
    module_status_changed = Signal(str, bool)
    protocol_warning = Signal(str)

    def __init__(
        self,
        config: MqttMonitoringConfig,
        repository=None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = utc_now,
        start_watchdog: bool = True,
        parent=None,
        battery_estimator=None,
    ) -> None:
        self.monitor_config = config
        self._clock = clock
        self._wall_clock = wall_clock
        self._parser = MqttMessageParser(config.topic_root)
        self.battery_estimator = battery_estimator or BatteryEstimator()
        super().__init__(repository=repository, parent=parent)
        self._logs = {
            device.device_id: deque(maxlen=config.log_capacity)
            for device in self._devices
        }
        self._trackers = {device.device_id: HeartbeatTracker() for device in self._devices}
        self._sequences: dict[tuple[str, str], int] = {}
        self._sessions: dict[str, str] = {}
        self._retired_sessions: dict[str, set[str]] = {}
        self.module_status_message = "MQTT 监测模块正在启动"
        self.module_healthy = False
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self.check_heartbeats)
        if start_watchdog:
            interval_ms = max(1, round(1000 / config.heartbeat_check_hz))
            self._watchdog.start(interval_ms)

    def _snapshot_from_profile(self, profile: DeviceProfile) -> DeviceSnapshot:
        template = self.device_type_template(profile.device_type)
        resolved_cards = profile.status_card_ids if profile.status_card_ids is not None else (
            template.default_status_card_ids if template is not None else ()
        )
        return DeviceSnapshot(
            device_id=profile.device_id,
            device_name=profile.device_name,
            device_type=profile.device_type,
            battery_percent=None,
            localization_status=LocalizationStatus.UNKNOWN,
            task_status=TaskStatus.UNKNOWN,
            connection_status=ConnectionStatus.OFFLINE,
            updated_at=self._wall_clock(),
            ip_address=profile.ip_address,
            availability=profile.availability,
            last_tested_at=profile.last_tested_at,
            health_status=HealthStatus.UNKNOWN,
            flight_mode="unknown",
            mission_status_raw="unknown",
            status_card_ids=resolved_cards,
            device_type_name=template.display_name if template else profile.device_type,
            device_icon_path=template.icon_path if template else None,
            map_marker_shape=template.map_marker_shape if template else MapMarkerShape.SPHERE,
            status_cards_inherited=profile.status_card_ids is None,
            srt_port=profile.srt_port,
            srt_latency_ms=profile.srt_latency_ms,
        )

    def create_device(self, profile: DeviceProfile) -> DeviceSnapshot:
        self._profiles = self.repository.create(profile)
        self._devices = self._merge_profiles(self._profiles)
        created = self.device(profile.device_id)
        if created is None:
            raise RuntimeError("设备创建后未能重新加载")
        self._logs[created.device_id] = deque(maxlen=self.monitor_config.log_capacity)
        self._trackers[created.device_id] = HeartbeatTracker()
        self.devices_updated.emit(self.snapshots())
        return created

    def delete_devices(self, device_ids: set[str]) -> None:
        super().delete_devices(device_ids)
        folded = {device_id.casefold() for device_id in device_ids}
        self._trackers = {
            key: value for key, value in self._trackers.items() if key.casefold() not in folded
        }
        self._sequences = {
            key: value for key, value in self._sequences.items() if key[0].casefold() not in folded
        }
        self._sessions = {
            key: value for key, value in self._sessions.items() if key.casefold() not in folded
        }
        self._retired_sessions = {
            key: value for key, value in self._retired_sessions.items()
            if key.casefold() not in folded
        }

    def logs(self, device_id: str) -> list[DeviceLogEntry]:
        return list(self._logs.get(device_id, ()))

    def append_external_log(self, device_id: str, level: DeviceLogLevel, message: str) -> None:
        self._append_log(device_id, level, message)

    def _device_id_changed(self, old_device_id: str, new_device_id: str) -> None:
        old_folded = old_device_id.casefold()
        self._trackers = {
            key: value for key, value in self._trackers.items()
            if key.casefold() != old_folded
        }
        self._trackers[new_device_id] = HeartbeatTracker()
        self._sequences = {
            key: value for key, value in self._sequences.items()
            if key[0].casefold() != old_folded
        }
        self._sessions = {
            key: value for key, value in self._sessions.items()
            if key.casefold() != old_folded
        }
        self._retired_sessions = {
            key: value for key, value in self._retired_sessions.items()
            if key.casefold() != old_folded
        }

    @Slot(str, bytes)
    def process_message(self, topic: str, payload: bytes) -> None:
        try:
            event = self._parser.parse(topic, payload)
        except MqttProtocolError as exc:
            self._warn(f"MQTT 消息已丢弃：{exc}")
            return
        self.handle_event(event)

    def handle_event(self, event: MqttEvent) -> None:
        device = self.device(event.device_id)
        if device is None:
            self._warn(f"忽略未登记设备：{event.device_id}")
            return
        if not device_address_matches(device.ip_address, event.ip_address):
            self._append_log(
                device.device_id,
                DeviceLogLevel.WARNING,
                f"消息地址 {event.ip_address} 与配置地址 {device.ip_address} 不一致或无法解析",
            )
        if not self._update_session(device.device_id, event):
            return
        if event.sequence is not None:
            key = (device.device_id, event.message_type)
            previous = self._sequences.get(key)
            if previous is not None and event.sequence == previous:
                # QoS 1 permits duplicate delivery. It is idempotent and not a
                # protocol ordering fault, so ignore it without flooding logs.
                return
            if previous is not None and event.sequence < previous:
                self._append_log(device.device_id, DeviceLogLevel.WARNING, f"丢弃乱序 {event.message_type} 帧 sequence={event.sequence}")
                return
            self._sequences[key] = event.sequence
        if isinstance(event, MqttPresenceEvent):
            self._handle_presence(device, event)
        elif isinstance(event, MqttHeartbeatEvent):
            self._handle_heartbeat(device, event)
        elif isinstance(event, MqttStatusEvent):
            self._handle_status(device, event)

    def _update_session(self, device_id: str, event: MqttEvent) -> bool:
        session_id = event.session_id
        previous = self._sessions.get(device_id)
        if session_id is not None:
            if previous == session_id:
                return True
            retired = self._retired_sessions.setdefault(device_id, set())
            if session_id in retired:
                return False
            if previous is not None:
                retired.add(previous)
                if len(retired) > 16:
                    retired.pop()
            self._clear_device_sequences(device_id)
            self._sessions[device_id] = session_id
            if previous is not None:
                self._append_log(device_id, DeviceLogLevel.INFO, "MQTT 端侧进程已重启，序列窗口已重置")
            return True
        if isinstance(event, MqttPresenceEvent) and event.status == "online":
            # Compatibility with edge packages predating session_id. A new online
            # presence is the only safe boundary at which their counters may reset.
            self._clear_device_sequences(device_id)
        return True

    def _clear_device_sequences(self, device_id: str) -> None:
        folded = device_id.casefold()
        self._sequences = {
            key: value for key, value in self._sequences.items()
            if key[0].casefold() != folded
        }

    def _handle_presence(self, device: DeviceSnapshot, event: MqttPresenceEvent) -> None:
        tracker = self._trackers[device.device_id]
        if event.status == "online":
            if device.connection_status != ConnectionStatus.ONLINE:
                self._append_log(device.device_id, DeviceLogLevel.INFO, "MQTT presence：设备已连接 Broker")
        else:
            tracker.disconnect_started_monotonic = self._clock()
            tracker.warned = True
            tracker.errored = False
            self._replace_device(replace(device, connection_status=ConnectionStatus.WARNING, updated_at=self._wall_clock()))
            if device.connection_status != ConnectionStatus.WARNING:
                self._append_log(device.device_id, DeviceLogLevel.WARNING, "MQTT presence：设备连接中断")
        self.devices_updated.emit(self.snapshots())

    def _handle_heartbeat(self, device: DeviceSnapshot, event: MqttHeartbeatEvent) -> None:
        tracker = self._trackers[device.device_id]
        first_heartbeat = tracker.last_heartbeat_monotonic is None
        was_degraded = tracker.warned or tracker.errored or (
            tracker.last_heartbeat_monotonic is not None and device.connection_status != ConnectionStatus.ONLINE
        )
        tracker.last_heartbeat_monotonic = self._clock()
        tracker.disconnect_started_monotonic = None
        tracker.warned = False
        tracker.errored = False
        self._replace_device(
            replace(
                device,
                connection_status=ConnectionStatus.ONLINE,
                last_heartbeat_at=self._wall_clock(),
                updated_at=self._wall_clock(),
            )
        )
        if first_heartbeat:
            self._append_log(device.device_id, DeviceLogLevel.INFO, "MQTT 心跳已建立")
        elif was_degraded:
            self._append_log(device.device_id, DeviceLogLevel.INFO, "设备心跳恢复，连接已恢复")
        self.devices_updated.emit(self.snapshots())

    def _handle_status(self, device: DeviceSnapshot, event: MqttStatusEvent) -> None:
        health = (
            HealthStatus.UNKNOWN
            if event.fcu_connected is None
            else HealthStatus.NORMAL if event.fcu_connected else HealthStatus.ATTENTION
        )
        task_status = normalize_mission_status(event.mission_status)
        profile = self.profile(device.device_id)
        estimated = self.battery_estimator.observe(
            device.device_id,
            profile.battery_profile if profile else "disabled",
            event.battery_voltage,
            self._wall_clock(),
            device.connection_status == ConnectionStatus.ONLINE,
        )
        battery_percent = event.battery_percentage
        if battery_percent is None:
            battery_percent = estimated
        if battery_percent is not None and battery_percent < 25:
            health = HealthStatus.ATTENTION
        self._replace_device(
            replace(
                device,
                battery_percent=battery_percent,
                task_status=task_status,
                health_status=health,
                flight_mode=event.flight_mode,
                armed=event.armed,
                system_status=event.system_status,
                battery_voltage=event.battery_voltage,
                battery_current=event.battery_current,
                mission_status_raw=event.mission_status,
                updated_at=self._wall_clock(),
            )
        )
        self.devices_updated.emit(self.snapshots())

    @Slot()
    def check_heartbeats(self) -> None:
        now = self._clock()
        changed = False
        for device_id, tracker in self._trackers.items():
            started = tracker.disconnect_started_monotonic
            if started is None:
                started = tracker.last_heartbeat_monotonic
            if started is None:
                continue
            elapsed = now - started
            device = self.device(device_id)
            if device is None:
                continue
            if elapsed > self.monitor_config.error_timeout_seconds and not tracker.errored:
                tracker.errored = True
                tracker.warned = True
                self._replace_device(replace(device, connection_status=ConnectionStatus.OFFLINE, updated_at=self._wall_clock()))
                self._append_log(device_id, DeviceLogLevel.ERROR, f"心跳中断超过 {self.monitor_config.error_timeout_seconds:g}s，设备离线")
                changed = True
            elif elapsed > self.monitor_config.warning_timeout_seconds and not tracker.warned:
                tracker.warned = True
                self._replace_device(replace(device, connection_status=ConnectionStatus.WARNING, updated_at=self._wall_clock()))
                self._append_log(device_id, DeviceLogLevel.WARNING, f"心跳中断超过 {self.monitor_config.warning_timeout_seconds:g}s")
                changed = True
        if changed:
            self.devices_updated.emit(self.snapshots())

    @Slot(str, bool)
    def set_module_status(self, message: str, healthy: bool) -> None:
        changed = message != self.module_status_message or healthy != self.module_healthy
        self.module_status_message = message
        self.module_healthy = healthy
        self.module_status_changed.emit(message, healthy)
        if changed:
            level = DeviceLogLevel.INFO if healthy else DeviceLogLevel.WARNING
            for device in self._devices:
                self._append_log(device.device_id, level, message)

    def _replace_device(self, updated: DeviceSnapshot) -> None:
        self._devices = [updated if item.device_id == updated.device_id else item for item in self._devices]

    def _append_log(self, device_id: str, level: DeviceLogLevel, message: str) -> None:
        entries = self._logs.setdefault(device_id, deque(maxlen=self.monitor_config.log_capacity))
        if not isinstance(entries, deque):
            entries = deque(entries, maxlen=self.monitor_config.log_capacity)
            self._logs[device_id] = entries
        entries.append(DeviceLogEntry(self._wall_clock(), level, message))
        self.logs_changed.emit(device_id)

    def _warn(self, message: str) -> None:
        LOGGER.warning(message)
        self.protocol_warning.emit(message)


def normalize_mission_status(value: str | None) -> TaskStatus:
    normalized = (value or "unknown").strip().lower()
    aliases = {
        TaskStatus.EXECUTING: {"running", "active", "executing"},
        TaskStatus.STANDBY: {"idle", "standby"},
        TaskStatus.PAUSED: {"paused"},
        TaskStatus.COMPLETED: {"done", "succeeded", "completed"},
    }
    for status, values in aliases.items():
        if normalized in values:
            return status
    return TaskStatus.UNKNOWN
