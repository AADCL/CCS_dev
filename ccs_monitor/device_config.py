from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .device_address import normalize_device_address
from .models import (
    DEFAULT_DEVICE_STATUS_CARDS,
    DEVICE_STATUS_CARD_CATALOG,
    DeviceAvailability,
    DeviceMapBinding,
    DeviceProfile,
    FrameTransform,
)


SCHEMA_VERSION = 7
RETIRED_STATUS_CARDS = {"octomap_mapping", "occupancy_grid_mapping"}
RELOCALIZATION_PROFILES = {
    "scout_mini", "wheeltec_r550p", "ground_air_agv", "go2_edu", "disabled"
}
RELOCALIZATION_PROFILES = {"scout_mini", "wheeltec_r550p", "go2_edu", "disabled"}
BATTERY_PROFILES = {"scout_mini", "wheeltec_r550p", "disabled"}


class DeviceConfigError(RuntimeError):
    pass


class DuplicateDeviceIdError(DeviceConfigError):
    pass


def default_device_profiles() -> list[DeviceProfile]:
    tested_at = datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc)
    return [
        DeviceProfile("UGV-042", "巡检车 Alpha", "UGV", "192.168.10.42", DeviceAvailability.AVAILABLE, tested_at),
        DeviceProfile("UAV-017", "测绘无人机 Delta", "UAV", "192.168.20.17", DeviceAvailability.AVAILABLE, tested_at),
        DeviceProfile("AMR-008", "搬运机器人 Beta", "AMR", "192.168.30.8", DeviceAvailability.AVAILABLE, tested_at),
        DeviceProfile("USV-003", "水面平台 Gamma", "USV", "192.168.40.3", DeviceAvailability.AVAILABLE, tested_at),
        DeviceProfile("UGV-031", "巡检车 Echo", "UGV", "192.168.10.31", DeviceAvailability.AVAILABLE, tested_at),
        DeviceProfile("AMR-012", "仓储机器人 Zeta", "AMR", "192.168.30.12", DeviceAvailability.UNAVAILABLE, tested_at),
    ]


class DeviceConfigRepository:
    def __init__(self, path: str | Path, valid_device_types=None) -> None:
        self.path = Path(path)
        self.valid_device_types = valid_device_types
        self.read_only = False
        self.error_message: str | None = None
        self._profiles: list[DeviceProfile] = []

    def load(self) -> list[DeviceProfile]:
        self.read_only = False
        self.error_message = None
        if not self.path.exists():
            profiles = default_device_profiles()
            self._write(profiles)
            self._profiles = profiles
            return list(profiles)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
            profiles = self._parse_payload(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.read_only = True
            self.error_message = f"设备配置读取失败：{exc}"
            self._profiles = []
            return []
        self._profiles = profiles
        if schema_version in {1, 2, 3, 4, 5, 6}:
            self._write(profiles)
        return list(profiles)

    def create(self, profile: DeviceProfile) -> list[DeviceProfile]:
        self._ensure_writable()
        normalized = self._validate_profile(profile)
        if any(item.device_id.casefold() == normalized.device_id.casefold() for item in self._profiles):
            raise DuplicateDeviceIdError(f"设备 ID {normalized.device_id} 已存在")
        updated = [*self._profiles, normalized]
        self._write(updated)
        self._profiles = updated
        return list(updated)

    def delete(self, device_ids: set[str]) -> list[DeviceProfile]:
        self._ensure_writable()
        normalized_ids = {device_id.casefold() for device_id in device_ids}
        updated = [profile for profile in self._profiles if profile.device_id.casefold() not in normalized_ids]
        self._write(updated)
        self._profiles = updated
        return list(updated)

    def update(self, original_device_id: str, profile: DeviceProfile) -> list[DeviceProfile]:
        self._ensure_writable()
        original = original_device_id.strip().casefold()
        if not any(item.device_id.casefold() == original for item in self._profiles):
            raise DeviceConfigError(f"设备 ID {original_device_id} 不存在")
        normalized = self._validate_profile(profile)
        if any(
            item.device_id.casefold() == normalized.device_id.casefold()
            and item.device_id.casefold() != original
            for item in self._profiles
        ):
            raise DuplicateDeviceIdError(f"设备 ID {normalized.device_id} 已存在")
        updated = [
            normalized if item.device_id.casefold() == original else item
            for item in self._profiles
        ]
        self._write(updated)
        self._profiles = updated
        return list(updated)

    def update_status_cards(self, device_id: str, status_card_ids: tuple[str, ...] | None) -> list[DeviceProfile]:
        self._ensure_writable()
        folded_id = device_id.strip().casefold()
        normalized_cards = None if status_card_ids is None else self._validate_status_cards(status_card_ids)
        if not any(profile.device_id.casefold() == folded_id for profile in self._profiles):
            raise DeviceConfigError(f"设备 ID {device_id} 不存在")
        updated = [
            self._replace_profile(profile, status_card_ids=normalized_cards)
            if profile.device_id.casefold() == folded_id else profile
            for profile in self._profiles
        ]
        self._write(updated)
        self._profiles = updated
        return list(updated)

    def contains_id(self, device_id: str) -> bool:
        normalized = device_id.strip().casefold()
        return any(profile.device_id.casefold() == normalized for profile in self._profiles)

    def upsert_map_binding(self, device_id: str, binding: DeviceMapBinding) -> list[DeviceProfile]:
        self._ensure_writable()
        folded_id = device_id.strip().casefold()
        binding = self._validate_binding(binding)
        if not any(profile.device_id.casefold() == folded_id for profile in self._profiles):
            raise DeviceConfigError(f"设备 ID {device_id} 不存在")
        updated: list[DeviceProfile] = []
        for profile in self._profiles:
            if profile.device_id.casefold() != folded_id:
                updated.append(profile)
                continue
            bindings = [item for item in profile.map_bindings if item.map_id != binding.map_id]
            bindings.append(binding)
            updated.append(self._replace_profile(profile, map_bindings=tuple(bindings)))
        self._write(updated)
        self._profiles = updated
        return list(updated)

    def remove_map_binding(self, device_id: str, map_id: str) -> list[DeviceProfile]:
        self._ensure_writable()
        folded_id = device_id.strip().casefold()
        normalized_map_id = map_id.strip()
        if not normalized_map_id:
            raise ValueError("地图 ID 不能为空")
        if not any(profile.device_id.casefold() == folded_id for profile in self._profiles):
            raise DeviceConfigError(f"设备 ID {device_id} 不存在")
        updated = [
            self._replace_profile(
                profile,
                map_bindings=tuple(
                    binding for binding in profile.map_bindings
                    if binding.map_id != normalized_map_id
                ),
            )
            if profile.device_id.casefold() == folded_id else profile
            for profile in self._profiles
        ]
        self._write(updated)
        self._profiles = updated
        return list(updated)

    def set_active_map(self, device_id: str, map_id: str | None) -> list[DeviceProfile]:
        self._ensure_writable()
        folded_id = device_id.strip().casefold()
        if not any(profile.device_id.casefold() == folded_id for profile in self._profiles):
            raise DeviceConfigError(f"设备 ID {device_id} 不存在")
        normalized = map_id.strip() if map_id is not None else None
        if normalized == "":
            normalized = None
        updated = [
            self._replace_profile(profile, active_map_id=normalized)
            if profile.device_id.casefold() == folded_id else profile
            for profile in self._profiles
        ]
        self._write(updated)
        self._profiles = updated
        return list(updated)

    @staticmethod
    def _replace_profile(profile: DeviceProfile, **changes) -> DeviceProfile:
        values = {
            "device_id": profile.device_id, "device_name": profile.device_name,
            "device_type": profile.device_type, "ip_address": profile.ip_address,
            "availability": profile.availability, "last_tested_at": profile.last_tested_at,
            "status_card_ids": profile.status_card_ids, "srt_port": profile.srt_port,
            "srt_latency_ms": profile.srt_latency_ms,
            "relocalization_profile": profile.relocalization_profile,
            "map_bindings": profile.map_bindings, "active_map_id": profile.active_map_id,
            "battery_profile": profile.battery_profile,
        }
        values.update(changes)
        return DeviceProfile(**values)

    def _ensure_writable(self) -> None:
        if self.read_only:
            raise DeviceConfigError(self.error_message or "设备配置当前为只读状态")

    def _parse_payload(self, payload: Any) -> list[DeviceProfile]:
        if not isinstance(payload, dict):
            raise ValueError("根节点必须是对象")
        schema_version = payload.get("schema_version")
        if schema_version not in {1, 2, 3, 4, 5, 6, SCHEMA_VERSION}:
            raise ValueError(f"不支持的 schema_version：{payload.get('schema_version')}")
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list):
            raise ValueError("devices 必须是数组")
        profiles: list[DeviceProfile] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_devices):
            if not isinstance(item, dict):
                raise ValueError(f"devices[{index}] 必须是对象")
            try:
                last_tested = item.get("last_tested_at")
                profile = DeviceProfile(
                    device_id=str(item["device_id"]),
                    device_name=str(item["device_name"]),
                    device_type=str(item["device_type"]),
                    ip_address=str(item["ip_address"]),
                    availability=DeviceAvailability(str(item.get("availability", "unknown"))),
                    last_tested_at=datetime.fromisoformat(last_tested) if last_tested else None,
                    status_card_ids=(
                        None
                        if schema_version >= 3 and item.get("status_cards") is None
                        else self._validate_status_cards(tuple(item.get("status_cards", DEFAULT_DEVICE_STATUS_CARDS)))
                    ),
                    srt_port=int(item.get("srt_port", 9000)),
                    srt_latency_ms=int(item.get("srt_latency_ms", 120)),
                    relocalization_profile=str(item.get("relocalization_profile", "disabled")),
                    map_bindings=tuple(
                        self._parse_binding(value)
                        for value in item.get("map_bindings", [])
                    ),
                    active_map_id=(str(item["active_map_id"]) if item.get("active_map_id") else None),
                    battery_profile=str(item.get(
                        "battery_profile",
                        item.get("relocalization_profile", "disabled")
                        if item.get("relocalization_profile") in BATTERY_PROFILES
                        else "disabled",
                    )),
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f"devices[{index}] 字段无效：{exc}") from exc
            profile = self._validate_profile(profile)
            folded_id = profile.device_id.casefold()
            if folded_id in seen_ids:
                raise ValueError(f"设备 ID 重复：{profile.device_id}")
            seen_ids.add(folded_id)
            profiles.append(profile)
        return profiles

    def _validate_profile(self, profile: DeviceProfile) -> DeviceProfile:
        device_id = profile.device_id.strip().upper()
        device_name = profile.device_name.strip()
        device_type = profile.device_type.strip().upper()
        ip_address = normalize_device_address(profile.ip_address)
        if not device_id or not device_name or not device_type or not ip_address:
            raise ValueError("设备名称、类型、ID 和地址均不能为空")
        valid_types = self.valid_device_types() if self.valid_device_types is not None else {"UGV", "UAV", "AMR", "USV"}
        if device_type.casefold() not in {item.casefold() for item in valid_types}:
            raise ValueError(f"不支持的设备类型：{device_type}")
        if not 1 <= profile.srt_port <= 65535:
            raise ValueError("SRT 端口必须在 1–65535 之间")
        if not 20 <= profile.srt_latency_ms <= 8000:
            raise ValueError("SRT 延迟必须在 20–8000 ms 之间")
        relocalization_profile = profile.relocalization_profile.strip().lower()
        if relocalization_profile not in RELOCALIZATION_PROFILES:
            raise ValueError(f"未知重定位 profile：{profile.relocalization_profile}")
        battery_profile = profile.battery_profile.strip().lower()
        if battery_profile not in BATTERY_PROFILES:
            raise ValueError(f"未知电池 profile：{profile.battery_profile}")
        bindings = tuple(self._validate_binding(item) for item in profile.map_bindings)
        if len({item.map_id for item in bindings}) != len(bindings):
            raise ValueError("map_bindings 包含重复地图 ID")
        return DeviceProfile(
            device_id=device_id,
            device_name=device_name,
            device_type=device_type,
            ip_address=ip_address,
            availability=profile.availability,
            last_tested_at=profile.last_tested_at,
            status_card_ids=(None if profile.status_card_ids is None else DeviceConfigRepository._validate_status_cards(profile.status_card_ids)),
            srt_port=profile.srt_port,
            srt_latency_ms=profile.srt_latency_ms,
            relocalization_profile=relocalization_profile,
            map_bindings=bindings,
            active_map_id=profile.active_map_id.strip() if profile.active_map_id else None,
            battery_profile=battery_profile,
        )

    @staticmethod
    def _parse_binding(value: Any) -> DeviceMapBinding:
        if not isinstance(value, dict) or not isinstance(value.get("map_from_odom"), dict):
            raise ValueError("map_bindings 项无效")
        transform = value["map_from_odom"]
        try:
            return DeviceMapBinding(
                map_id=str(value["map_id"]), map_frame=str(value["map_frame"]),
                odom_frame=str(value["odom_frame"]),
                map_from_odom=FrameTransform(**{
                    key: float(transform[key])
                    for key in ("x", "y", "z", "qx", "qy", "qz", "qw")
                }),
                localized_at=datetime.fromisoformat(str(value["localized_at"])),
                pose_source=str(value.get("pose_source", "global_pose")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"map_bindings 字段无效：{exc}") from exc

    @staticmethod
    def _validate_binding(binding: DeviceMapBinding) -> DeviceMapBinding:
        if not binding.map_id.strip() or not binding.map_frame.strip() or not binding.odom_frame.strip():
            raise ValueError("地图绑定 ID 和 frame 不能为空")
        if binding.pose_source not in {"global_pose", "vision_pose"}:
            raise ValueError("地图绑定 pose_source 无效")
        values = tuple(getattr(binding.map_from_odom, key) for key in (
            "x", "y", "z", "qx", "qy", "qz", "qw"))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("地图绑定变换包含非有限数值")
        norm = math.sqrt(sum(value * value for value in values[3:]))
        if abs(norm - 1.0) > 1e-3:
            raise ValueError("地图绑定四元数必须归一化")
        if binding.localized_at.tzinfo is None:
            raise ValueError("地图绑定时间必须包含时区")
        return binding

    @staticmethod
    def _validate_status_cards(status_card_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(status_card_ids, tuple):
            raise ValueError("status_cards 必须是数组")
        normalized: list[str] = []
        for card_id in status_card_ids:
            if card_id in RETIRED_STATUS_CARDS:
                continue
            if not isinstance(card_id, str) or card_id not in DEVICE_STATUS_CARD_CATALOG:
                raise ValueError(f"未知的数据状态卡片：{card_id}")
            if card_id in normalized:
                raise ValueError(f"数据状态卡片重复：{card_id}")
            normalized.append(card_id)
        return tuple(normalized)

    def _write(self, profiles: list[DeviceProfile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "devices": [self._serialize_profile(profile) for profile in profiles],
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, self.path)
        except OSError as exc:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            raise DeviceConfigError(f"设备配置写入失败：{exc}") from exc

    @staticmethod
    def _serialize_profile(profile: DeviceProfile) -> dict[str, Any]:
        return {
            "device_id": profile.device_id,
            "device_name": profile.device_name,
            "device_type": profile.device_type,
            "ip_address": profile.ip_address,
            "availability": profile.availability.value,
            "last_tested_at": profile.last_tested_at.isoformat() if profile.last_tested_at else None,
            "status_cards": None if profile.status_card_ids is None else list(profile.status_card_ids),
            "srt_port": profile.srt_port,
            "srt_latency_ms": profile.srt_latency_ms,
            "relocalization_profile": profile.relocalization_profile,
            "battery_profile": profile.battery_profile,
            "active_map_id": profile.active_map_id,
            "map_bindings": [
                {
                    "map_id": item.map_id,
                    "map_frame": item.map_frame,
                    "odom_frame": item.odom_frame,
                    "pose_source": item.pose_source,
                    "localized_at": item.localized_at.isoformat(),
                    "map_from_odom": {
                        key: getattr(item.map_from_odom, key)
                        for key in ("x", "y", "z", "qx", "qy", "qz", "qw")
                    },
                }
                for item in profile.map_bindings
            ],
        }
