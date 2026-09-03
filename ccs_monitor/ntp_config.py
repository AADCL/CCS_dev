from __future__ import annotations

import json
from dataclasses import dataclass
from .runtime_paths import application_root
from pathlib import Path
from typing import Any


DEFAULT_NTP_CONFIG_PATH = application_root() / "config" / "ntp.json"


class NtpConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NtpServerConfig:
    enabled: bool
    bind_host: str
    port: int
    stratum: int
    precision: int
    reference_id: str


def load_ntp_config(path: str | Path = DEFAULT_NTP_CONFIG_PATH) -> NtpServerConfig:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NtpConfigError(f"NTP 配置读取失败：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise NtpConfigError("NTP 配置 schema_version 必须为 1")
    server = _mapping(payload.get("server"), "server")
    config = NtpServerConfig(
        enabled=_boolean(server.get("enabled"), "server.enabled"),
        bind_host=_string(server.get("bind_host"), "server.bind_host"),
        port=_integer(server.get("port"), "server.port"),
        stratum=_integer(server.get("stratum"), "server.stratum"),
        precision=_integer(server.get("precision"), "server.precision"),
        reference_id=_string(server.get("reference_id"), "server.reference_id"),
    )
    if not 1 <= config.port <= 65535:
        raise NtpConfigError("server.port 必须在 1 到 65535 之间")
    if not 1 <= config.stratum <= 15:
        raise NtpConfigError("server.stratum 必须在 1 到 15 之间")
    if not -128 <= config.precision <= 127:
        raise NtpConfigError("server.precision 必须在 -128 到 127 之间")
    try:
        reference_id = config.reference_id.encode("ascii")
    except UnicodeEncodeError as exc:
        raise NtpConfigError("server.reference_id 必须为 ASCII") from exc
    if not 1 <= len(reference_id) <= 4:
        raise NtpConfigError("server.reference_id 长度必须为 1 到 4 个 ASCII 字符")
    return config


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NtpConfigError(f"{name} 必须是对象")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NtpConfigError(f"{name} 必须是非空字符串")
    return value.strip()


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NtpConfigError(f"{name} 必须是整数")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise NtpConfigError(f"{name} 必须是布尔值")
    return value
