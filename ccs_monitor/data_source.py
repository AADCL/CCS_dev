from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from .device_config import DeviceConfigRepository
from .device_types import DeviceTypeTemplateRepository
from .models import (
    ConnectionStatus,
    DeviceAvailability,
    DeviceLogEntry,
    DeviceLogLevel,
    DeviceProfile,
    DeviceSnapshot,
    DeviceTypeTemplate,
    HealthStatus,
    LocalizationStatus,
    MapDefinition,
    MapMarkerShape,
    SystemOverview,
    TaskExecutionSummary,
    TaskStatus,
    utc_now,
)


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "devices.json"


class DeviceDataSource(Protocol):
    devices_updated: Signal
    logs_changed: Signal

    @property
    def config_error(self) -> str | None: ...
    @property
    def read_only(self) -> bool: ...

    def snapshots(self) -> list[DeviceSnapshot]: ...
    def refresh(self) -> None: ...
    def create_device(self, profile: DeviceProfile) -> DeviceSnapshot: ...
    def delete_devices(self, device_ids: set[str]) -> None: ...
    def update_device(self, original_device_id: str, profile: DeviceProfile) -> DeviceSnapshot: ...
    def device(self, device_id: str) -> DeviceSnapshot | None: ...
    def profile(self, device_id: str) -> DeviceProfile | None: ...
    def logs(self, device_id: str) -> list[DeviceLogEntry]: ...
    def has_device_id(self, device_id: str) -> bool: ...
    def append_external_log(self, device_id: str, level: DeviceLogLevel, message: str) -> None: ...
    def clear_device_logs(self, device_id: str) -> None: ...
    def update_device_status_cards(self, device_id: str, status_card_ids: tuple[str, ...] | None) -> None: ...
    def upsert_device_map_binding(self, device_id: str, binding) -> None: ...
    def remove_device_map_binding(self, device_id: str, map_id: str) -> None: ...
    def device_type_templates(self) -> list[DeviceTypeTemplate]: ...
    def device_type_template(self, type_id: str) -> DeviceTypeTemplate | None: ...
    def create_device_type_template(self, template: DeviceTypeTemplate, icon_source=None) -> DeviceTypeTemplate: ...
    def update_device_type_template(self, template: DeviceTypeTemplate, icon_source=None) -> DeviceTypeTemplate: ...
    def delete_device_type_template(self, type_id: str) -> None: ...


class SimulatedDeviceSource(QObject):
    """Config-backed static profiles merged with simulated runtime telemetry."""

    devices_updated = Signal(object)
    logs_changed = Signal(str)

    def __init__(
        self,
        repository: DeviceConfigRepository | None = None,
        type_repository: DeviceTypeTemplateRepository | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.type_repository = type_repository or DeviceTypeTemplateRepository()
        self._templates = self.type_repository.load()
        self.repository = repository or DeviceConfigRepository(DEFAULT_CONFIG_PATH)
        self.repository.valid_device_types = lambda: {item.type_id for item in self._templates}
        self._profiles = self.repository.load()
        self.type_repository.referenced_type_ids = lambda: {item.device_type for item in self._profiles}
        self._runtime = self._runtime_fixtures()
        self._devices = self._merge_profiles(self._profiles)
        self._logs = {device.device_id: self._build_logs(device) for device in self._devices}

    @property
    def config_error(self) -> str | None:
        return self.repository.error_message or self.type_repository.error_message

    @property
    def read_only(self) -> bool:
        return self.repository.read_only or self.type_repository.read_only

    def snapshots(self) -> list[DeviceSnapshot]:
        return list(self._devices)

    def refresh(self) -> None:
        self._devices = [replace(device, updated_at=utc_now()) for device in self._devices]
        self.devices_updated.emit(self.snapshots())

    def create_device(self, profile: DeviceProfile) -> DeviceSnapshot:
        self._profiles = self.repository.create(profile)
        self._devices = self._merge_profiles(self._profiles)
        created = self.device(profile.device_id)
        if created is None:
            raise RuntimeError("设备创建后未能重新加载")
        self._logs[created.device_id] = self._build_logs(created)
        self.devices_updated.emit(self.snapshots())
        return created

    def delete_devices(self, device_ids: set[str]) -> None:
        self._profiles = self.repository.delete(device_ids)
        folded_ids = {device_id.casefold() for device_id in device_ids}
        self._devices = self._merge_profiles(self._profiles)
        self._logs = {
            device_id: entries
            for device_id, entries in self._logs.items()
            if device_id.casefold() not in folded_ids
        }
        self.devices_updated.emit(self.snapshots())

    def update_device(self, original_device_id: str, profile: DeviceProfile) -> DeviceSnapshot:
        original_folded = original_device_id.casefold()
        previous = self.device(original_device_id)
        self._profiles = self.repository.update(original_device_id, profile)
        updated_profile = next(
            item for item in self._profiles
            if item.device_id.casefold() == profile.device_id.strip().casefold()
        )
        if previous is not None and original_folded == updated_profile.device_id.casefold():
            updated = self._apply_profile_presentation(
                replace(
                    previous,
                    device_name=updated_profile.device_name,
                    device_type=updated_profile.device_type,
                    ip_address=updated_profile.ip_address,
                    availability=updated_profile.availability,
                    last_tested_at=updated_profile.last_tested_at,
                ),
                updated_profile,
            )
            self._devices = [
                updated if item.device_id.casefold() == original_folded else item
                for item in self._devices
            ]
        else:
            self._devices = [
                item for item in self._devices if item.device_id.casefold() != original_folded
            ]
            updated = self._snapshot_from_profile(updated_profile)
            self._devices.append(updated)
            entries = self._logs.pop(original_device_id, [])
            self._logs[updated.device_id] = entries
            self._device_id_changed(original_device_id, updated.device_id)
        self.devices_updated.emit(self.snapshots())
        return updated

    def device(self, device_id: str) -> DeviceSnapshot | None:
        folded_id = device_id.casefold()
        return next((device for device in self._devices if device.device_id.casefold() == folded_id), None)

    def profile(self, device_id: str) -> DeviceProfile | None:
        folded_id = device_id.casefold()
        return next(
            (profile for profile in self._profiles if profile.device_id.casefold() == folded_id),
            None,
        )

    def logs(self, device_id: str) -> list[DeviceLogEntry]:
        return list(self._logs.get(device_id, []))

    def has_device_id(self, device_id: str) -> bool:
        return self.repository.contains_id(device_id)

    def append_external_log(self, device_id: str, level: DeviceLogLevel, message: str) -> None:
        entries = self._logs.setdefault(device_id, [])
        entries.append(DeviceLogEntry(utc_now(), level, message))
        self.logs_changed.emit(device_id)

    def clear_device_logs(self, device_id: str) -> None:
        entries = self._logs.get(device_id)
        if entries is not None:
            entries.clear()
        self.logs_changed.emit(device_id)

    def _device_id_changed(self, _old_device_id: str, _new_device_id: str) -> None:
        pass

    def update_device_status_cards(self, device_id: str, status_card_ids: tuple[str, ...] | None) -> None:
        self._profiles = self.repository.update_status_cards(device_id, status_card_ids)
        profile = next(item for item in self._profiles if item.device_id.casefold() == device_id.casefold())
        self._devices = [
            self._apply_profile_presentation(device, profile)
            if device.device_id.casefold() == device_id.casefold() else device
            for device in self._devices
        ]
        self.devices_updated.emit(self.snapshots())

    def upsert_device_map_binding(self, device_id: str, binding) -> None:
        self._profiles = self.repository.upsert_map_binding(device_id, binding)
        self.devices_updated.emit(self.snapshots())

    def remove_device_map_binding(self, device_id: str, map_id: str) -> None:
        self._profiles = self.repository.remove_map_binding(device_id, map_id)
        self.devices_updated.emit(self.snapshots())

    def set_device_active_map(self, device_id: str, map_id: str | None) -> None:
        self._profiles = self.repository.set_active_map(device_id, map_id)
        self.devices_updated.emit(self.snapshots())

    def device_type_templates(self) -> list[DeviceTypeTemplate]:
        return list(self._templates)

    def device_type_template(self, type_id: str) -> DeviceTypeTemplate | None:
        return next((item for item in self._templates if item.type_id.casefold() == type_id.casefold()), None)

    def create_device_type_template(self, template: DeviceTypeTemplate, icon_source=None) -> DeviceTypeTemplate:
        created = self.type_repository.create(template, icon_source)
        self._templates = self.type_repository.all()
        self._devices = self._refresh_presentations()
        self.devices_updated.emit(self.snapshots())
        return created

    def update_device_type_template(self, template: DeviceTypeTemplate, icon_source=None) -> DeviceTypeTemplate:
        updated = self.type_repository.update(template, icon_source)
        self._templates = self.type_repository.all()
        self._devices = self._refresh_presentations()
        self.devices_updated.emit(self.snapshots())
        return updated

    def delete_device_type_template(self, type_id: str) -> None:
        self.type_repository.delete(type_id)
        self._templates = self.type_repository.all()
        self._devices = self._refresh_presentations()
        self.devices_updated.emit(self.snapshots())

    def _merge_profiles(self, profiles: list[DeviceProfile]) -> list[DeviceSnapshot]:
        return [self._snapshot_from_profile(profile) for profile in profiles]

    def _refresh_presentations(self) -> list[DeviceSnapshot]:
        profiles = {item.device_id.casefold(): item for item in self._profiles}
        return [self._apply_profile_presentation(device, profiles[device.device_id.casefold()]) for device in self._devices]

    def _apply_profile_presentation(self, device: DeviceSnapshot, profile: DeviceProfile) -> DeviceSnapshot:
        template = self.device_type_template(profile.device_type)
        resolved_cards = profile.status_card_ids if profile.status_card_ids is not None else (
            template.default_status_card_ids if template is not None else ()
        )
        return replace(
            device,
            status_card_ids=resolved_cards,
            device_type_name=template.display_name if template else profile.device_type,
            device_icon_path=template.icon_path if template else None,
            map_marker_shape=template.map_marker_shape if template else MapMarkerShape.SPHERE,
            status_cards_inherited=profile.status_card_ids is None,
            srt_port=profile.srt_port,
            srt_latency_ms=profile.srt_latency_ms,
        )

    def _snapshot_from_profile(self, profile: DeviceProfile) -> DeviceSnapshot:
        template = self.device_type_template(profile.device_type)
        resolved_cards = profile.status_card_ids if profile.status_card_ids is not None else (
            template.default_status_card_ids if template is not None else ()
        )
        runtime = self._runtime.get(profile.device_id, {})
        connection = runtime.get(
            "connection_status",
            ConnectionStatus.ONLINE
            if profile.availability == DeviceAvailability.AVAILABLE
            else ConnectionStatus.OFFLINE,
        )
        localization = runtime.get("localization_status", LocalizationStatus.UNKNOWN)
        battery = runtime.get("battery_percent")
        health = calculate_health(connection, localization, battery)
        return DeviceSnapshot(
            device_id=profile.device_id,
            device_name=profile.device_name,
            device_type=profile.device_type,
            battery_percent=battery,
            localization_status=localization,
            task_status=runtime.get("task_status", TaskStatus.STANDBY),
            connection_status=connection,
            updated_at=utc_now(),
            position_x=runtime.get("position_x"),
            position_y=runtime.get("position_y"),
            frame_id=runtime.get("frame_id"),
            ip_address=profile.ip_address,
            availability=profile.availability,
            last_tested_at=profile.last_tested_at,
            health_status=health,
            status_card_ids=resolved_cards,
            device_type_name=template.display_name if template else profile.device_type,
            device_icon_path=template.icon_path if template else None,
            map_marker_shape=template.map_marker_shape if template else MapMarkerShape.SPHERE,
            status_cards_inherited=profile.status_card_ids is None,
            srt_port=profile.srt_port,
            srt_latency_ms=profile.srt_latency_ms,
        )

    @staticmethod
    def _runtime_fixtures() -> dict[str, dict[str, object]]:
        return {
            "UGV-042": {"battery_percent": 87, "localization_status": LocalizationStatus.FIXED, "task_status": TaskStatus.EXECUTING, "connection_status": ConnectionStatus.ONLINE, "position_x": 12.0, "position_y": 8.5, "frame_id": "factory_map"},
            "UAV-017": {"battery_percent": 64, "localization_status": LocalizationStatus.FIXED, "task_status": TaskStatus.EXECUTING, "connection_status": ConnectionStatus.ONLINE, "position_x": -18.0, "position_y": 15.0, "frame_id": "outdoor_map"},
            "AMR-008": {"battery_percent": 42, "localization_status": LocalizationStatus.SEARCHING, "task_status": TaskStatus.STANDBY, "connection_status": ConnectionStatus.WARNING, "position_x": -6.0, "position_y": -4.0, "frame_id": "warehouse_map"},
            "USV-003": {"battery_percent": 19, "localization_status": LocalizationStatus.LOST, "task_status": TaskStatus.PAUSED, "connection_status": ConnectionStatus.WARNING, "position_x": 28.0, "position_y": -12.0, "frame_id": "outdoor_map"},
            "UGV-031": {"battery_percent": 96, "localization_status": LocalizationStatus.FIXED, "task_status": TaskStatus.COMPLETED, "connection_status": ConnectionStatus.ONLINE, "position_x": -9.5, "position_y": 6.0, "frame_id": "factory_map"},
            "AMR-012": {"battery_percent": None, "localization_status": LocalizationStatus.UNKNOWN, "task_status": TaskStatus.UNKNOWN, "connection_status": ConnectionStatus.OFFLINE},
        }

    @staticmethod
    def _build_logs(device: DeviceSnapshot) -> list[DeviceLogEntry]:
        now = utc_now()
        entries = [
            DeviceLogEntry(now - timedelta(minutes=12), DeviceLogLevel.INFO, "设备状态快照已加载"),
            DeviceLogEntry(now - timedelta(minutes=7), DeviceLogLevel.INFO, f"连接地址 {device.ip_address} 已注册"),
        ]
        if device.health_status != HealthStatus.NORMAL:
            entries.append(DeviceLogEntry(now - timedelta(minutes=4), DeviceLogLevel.WARNING, "设备健康状态需要关注"))
        if device.connection_status == ConnectionStatus.OFFLINE or device.localization_status == LocalizationStatus.LOST:
            entries.append(DeviceLogEntry(now - timedelta(minutes=1), DeviceLogLevel.ERROR, "设备连接或定位状态异常"))
        else:
            entries.append(DeviceLogEntry(now - timedelta(minutes=1), DeviceLogLevel.INFO, "运行状态检查完成"))
        return entries


def calculate_health(
    connection_status: ConnectionStatus,
    localization_status: LocalizationStatus,
    battery_percent: float | None,
) -> HealthStatus:
    if connection_status == ConnectionStatus.OFFLINE or localization_status == LocalizationStatus.LOST:
        return HealthStatus.ABNORMAL
    if connection_status == ConnectionStatus.WARNING or (
        battery_percent is not None and battery_percent < 25
    ):
        return HealthStatus.ATTENTION
    return HealthStatus.NORMAL


def simulated_overview() -> SystemOverview:
    maps = (
        MapDefinition("map-001", "总装车间", "factory_map", 60.0, 40.0),
        MapDefinition("map-002", "仓储区域", "warehouse_map", 48.0, 36.0),
        MapDefinition("map-003", "室外试验场", "outdoor_map", 100.0, 70.0),
    )
    last_task = TaskExecutionSummary(
        title="A 区联合巡检",
        task_type="多设备协同巡检",
        started_at=datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 30, 10, 42, tzinfo=timezone.utc),
        status="已完成",
    )
    return SystemOverview(maps=maps, task_execution_count=26, last_task=last_task)
