from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RELOCALIZATION_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "relocalization.json"


class RelocalizationConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RelocalizationProfileConfig:
    supported: bool
    pose_source: str
    odom_frame: str


@dataclass(frozen=True)
class RelocalizationConfig:
    protocol_id: str
    bind_host: str
    status_port: int
    device_control_port: int
    http_bind_host: str
    http_port: int
    max_datagram_bytes: int
    command_retry_seconds: float
    command_max_attempts: int
    session_timeout_seconds: float
    token_ttl_seconds: float
    max_artifact_bytes: int
    profiles: dict[str, RelocalizationProfileConfig]

    def profile(self, name: str) -> RelocalizationProfileConfig:
        return self.profiles.get(name, self.profiles["disabled"])


def load_relocalization_config(path: Path = DEFAULT_RELOCALIZATION_CONFIG_PATH) -> RelocalizationConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise RelocalizationConfigError("重定位配置 schema_version 必须为 1")
        network, transport = payload["network"], payload["transport"]
        profiles = {
            str(name): RelocalizationProfileConfig(
                bool(value["supported"]), str(value["pose_source"]), str(value["odom_frame"])
            )
            for name, value in payload["profiles"].items()
        }
        config = RelocalizationConfig(
            str(payload["protocol_id"]), str(network["bind_host"]), int(network["status_port"]),
            int(network["device_control_port"]), str(network["http_bind_host"]),
            int(network["http_port"]), int(network["max_datagram_bytes"]),
            float(transport["command_retry_seconds"]), int(transport["command_max_attempts"]),
            float(transport["session_timeout_seconds"]), float(transport["token_ttl_seconds"]),
            int(transport["max_artifact_bytes"]), profiles,
        )
    except RelocalizationConfigError:
        raise
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RelocalizationConfigError(f"重定位配置无效：{exc}") from exc
    if not config.protocol_id or "disabled" not in config.profiles:
        raise RelocalizationConfigError("重定位协议或 disabled profile 缺失")
    if any(not 1 <= port <= 65535 for port in (
        config.status_port, config.device_control_port, config.http_port
    )):
        raise RelocalizationConfigError("重定位端口无效")
    if not 512 <= config.max_datagram_bytes <= 65507:
        raise RelocalizationConfigError("重定位数据报上限无效")
    if config.command_retry_seconds <= 0 or config.command_max_attempts < 1:
        raise RelocalizationConfigError("重定位重试参数无效")
    if config.session_timeout_seconds <= 0 or config.token_ttl_seconds <= 0:
        raise RelocalizationConfigError("重定位超时参数无效")
    for name, profile in config.profiles.items():
        if profile.pose_source not in {"global_pose", "vision_pose"} or not profile.odom_frame:
            raise RelocalizationConfigError(f"重定位 profile {name} 无效")
    return config
