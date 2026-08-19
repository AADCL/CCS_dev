from __future__ import annotations

from .data_source import DeviceDataSource
from .map_repository import MapRepository
from .models import DeviceLogLevel, DeviceProfile, DeviceSnapshot
from .task_repository import TaskRepository


class DeviceMigrationError(RuntimeError):
    pass


class DeviceReferenceMigrationCoordinator:
    def __init__(
        self,
        source: DeviceDataSource,
        map_repository: MapRepository,
        task_repository: TaskRepository,
        mapping_service=None,
        task_execution_service=None,
        telemetry_store=None,
    ) -> None:
        self.source = source
        self.map_repository = map_repository
        self.task_repository = task_repository
        self.mapping_service = mapping_service
        self.task_execution_service = task_execution_service
        self.telemetry_store = telemetry_store

    def update_device(
        self, original_device_id: str, profile: DeviceProfile
    ) -> DeviceSnapshot:
        original_profile = self.source.profile(original_device_id)
        if original_profile is None:
            raise DeviceMigrationError(f"设备不存在：{original_device_id}")
        identity_changed = original_device_id.casefold() != profile.device_id.casefold()
        if identity_changed and self._device_is_active(original_device_id):
            raise DeviceMigrationError("设备正在执行建图或任务，结束活动会话后才能修改设备 ID")

        map_originals = ()
        task_originals = ()
        source_changed = False
        try:
            if (
                identity_changed or original_profile.device_name != profile.device_name
                or original_profile.device_type != profile.device_type
            ):
                map_originals = self.map_repository.update_device_reference(original_device_id, profile)
            if (
                identity_changed or original_profile.device_name != profile.device_name
                or original_profile.device_type != profile.device_type
                or original_profile.ip_address != profile.ip_address
            ):
                task_originals = self.task_repository.update_device_reference(original_device_id, profile)
            updated = self.source.update_device(original_device_id, profile)
            source_changed = True
            if identity_changed and self.telemetry_store is not None:
                self.telemetry_store.remove_device(original_device_id)
            self.task_repository.audit_device_reference_update(
                (item.task_id for item in task_originals), original_device_id, updated.device_id
            )
            message = "设备基本信息已更新"
            if identity_changed:
                message += f"；设备 ID 已由 {original_device_id} 修改，请同步更新端侧配置"
            self.source.append_external_log(updated.device_id, DeviceLogLevel.WARNING, message)
            return updated
        except Exception as exc:
            rollback_errors: list[str] = []
            if source_changed:
                try:
                    self.source.update_device(profile.device_id, original_profile)
                except Exception as rollback_exc:
                    rollback_errors.append(f"设备配置回滚失败：{rollback_exc}")
            try:
                self.task_repository.restore_definitions(task_originals)
            except Exception as rollback_exc:
                rollback_errors.append(f"任务回滚失败：{rollback_exc}")
            try:
                self.map_repository.restore_definitions(map_originals)
            except Exception as rollback_exc:
                rollback_errors.append(f"地图回滚失败：{rollback_exc}")
            detail = f"设备信息更新失败：{exc}"
            if rollback_errors:
                detail += "；" + "；".join(rollback_errors)
            raise DeviceMigrationError(detail) from exc

    def _device_is_active(self, device_id: str) -> bool:
        mapping_active = bool(
            self.mapping_service is not None
            and getattr(self.mapping_service, "device_active", lambda _value: False)(device_id)
        )
        task_active = bool(
            self.task_execution_service is not None
            and getattr(self.task_execution_service, "device_active", lambda _value: False)(device_id)
        )
        return mapping_active or task_active
