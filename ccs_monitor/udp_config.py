from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from .runtime_paths import application_root
from pathlib import Path


DEFAULT_UDP_CONFIG_PATH = application_root() / "config" / "udp_telemetry.json"
ALLOWED_TYPES = {"pose", "imu", "pointcloud_status", "availability", "text_status"}
LEVEL_RATES = {1: 20, 2: 5, 3: 1}


class UdpConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TelemetryDescriptor:
    name: str
    display_name: str
    data_type: str
    level: int

    def canonical(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "level": self.level,
            "name": self.name,
            "type": self.data_type,
        }


@dataclass(frozen=True)
class UdpTelemetryConfig:
    protocol_id: str
    bind_host: str
    port: int
    warning_timeout_seconds: float
    error_timeout_seconds: float
    max_datagram_bytes: int
    descriptors: tuple[TelemetryDescriptor, ...]
    accepted_descriptor_sets: tuple[tuple[TelemetryDescriptor, ...], ...] = ()

    @property
    def descriptor_hash(self) -> str:
        return self.hash_descriptors(self.descriptors)

    @staticmethod
    def hash_descriptors(descriptors: tuple[TelemetryDescriptor, ...]) -> str:
        canonical = [item.canonical() for item in sorted(descriptors, key=lambda value: value.name)]
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def descriptor(self, name: str) -> TelemetryDescriptor | None:
        return next((item for item in self.descriptors if item.name == name), None)

    def any_descriptor(self, name: str) -> TelemetryDescriptor | None:
        for descriptors in (self.descriptors, *self.accepted_descriptor_sets):
            match = next((item for item in descriptors if item.name == name), None)
            if match is not None:
                return match
        return None

    def descriptors_for_hash(self, descriptor_hash: str) -> tuple[TelemetryDescriptor, ...] | None:
        for descriptors in (self.descriptors, *self.accepted_descriptor_sets):
            if self.hash_descriptors(descriptors) == descriptor_hash:
                return descriptors
        return None


def load_udp_config(path: Path = DEFAULT_UDP_CONFIG_PATH) -> UdpTelemetryConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UdpConfigError(f"无法读取 UDP 遥测配置：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise UdpConfigError("UDP 配置 schema_version 必须为 1 或 2")
    network = payload.get("network")
    monitor = payload.get("monitor")
    raw_descriptors = payload.get("descriptors")
    if not isinstance(network, dict) or not isinstance(monitor, dict) or not isinstance(raw_descriptors, list):
        raise UdpConfigError("UDP 配置缺少 network、monitor 或 descriptors")
    def parse_descriptors(values, path="descriptors") -> tuple[TelemetryDescriptor, ...]:
        if not isinstance(values, list):
            raise UdpConfigError(f"{path} 必须是数组")
        parsed: list[TelemetryDescriptor] = []
        names: set[str] = set()
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                raise UdpConfigError(f"{path}[{index}] 必须是对象")
            name, display_name = raw.get("name"), raw.get("display_name")
            data_type, level = raw.get("type"), raw.get("level")
            if not isinstance(name, str) or not name.strip() or name in names:
                raise UdpConfigError(f"{path}[{index}] 名称为空或重复")
            if not isinstance(display_name, str) or not display_name.strip():
                raise UdpConfigError(f"{path}[{index}] display_name 无效")
            if data_type not in ALLOWED_TYPES or level not in LEVEL_RATES:
                raise UdpConfigError(f"{path}[{index}] 类型或等级无效")
            names.add(name)
            parsed.append(TelemetryDescriptor(name, display_name, data_type, level))
        return tuple(parsed)

    descriptors = parse_descriptors(raw_descriptors)
    accepted = tuple(
        parse_descriptors(value, f"accepted_descriptor_sets[{index}]")
        for index, value in enumerate(payload.get("accepted_descriptor_sets", []))
    )
    try:
        config = UdpTelemetryConfig(
            protocol_id=str(payload["protocol_id"]),
            bind_host=str(network["bind_host"]),
            port=int(network["port"]),
            warning_timeout_seconds=float(monitor["warning_timeout_seconds"]),
            error_timeout_seconds=float(monitor["error_timeout_seconds"]),
            max_datagram_bytes=int(network.get("max_datagram_bytes", 16384)),
            descriptors=descriptors, accepted_descriptor_sets=accepted,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UdpConfigError(f"UDP 配置字段无效：{exc}") from exc
    if not config.protocol_id or not 1 <= config.port <= 65535:
        raise UdpConfigError("UDP protocol_id 或端口无效")
    if not 0 < config.warning_timeout_seconds < config.error_timeout_seconds:
        raise UdpConfigError("UDP 心跳阈值必须满足 0 < warning < error")
    if not 512 <= config.max_datagram_bytes <= 65507:
        raise UdpConfigError("max_datagram_bytes 必须在 512 到 65507 之间")
    return config
