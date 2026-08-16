from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MQTT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "mqtt.json"


class MqttConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MqttMonitoringConfig:
    bind_host: str
    port: int
    qos: int
    topic_root: str
    heartbeat_check_hz: float
    warning_timeout_seconds: float
    error_timeout_seconds: float
    log_capacity: int
    subscriber_client_id: str

    @property
    def topics(self) -> tuple[str, str, str]:
        return tuple(f"{self.topic_root}/+/{suffix}" for suffix in ("presence", "heartbeat", "status"))


def default_mqtt_config() -> MqttMonitoringConfig:
    return MqttMonitoringConfig(
        bind_host="0.0.0.0",
        port=1883,
        qos=1,
        topic_root="mqtav",
        heartbeat_check_hz=1.0,
        warning_timeout_seconds=2.0,
        error_timeout_seconds=5.0,
        log_capacity=500,
        subscriber_client_id="ccs-ground-station",
    )


def load_mqtt_config(path: str | Path = DEFAULT_MQTT_CONFIG_PATH) -> MqttMonitoringConfig:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MqttConfigError(f"MQTT 配置读取失败：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MqttConfigError("MQTT 配置 schema_version 必须为 1")
    broker = _mapping(payload.get("broker"), "broker")
    monitor = _mapping(payload.get("monitor"), "monitor")
    config = MqttMonitoringConfig(
        bind_host=_string(broker.get("bind_host"), "broker.bind_host"),
        port=_integer(broker.get("port"), "broker.port"),
        qos=_integer(payload.get("qos"), "qos"),
        topic_root=_string(payload.get("topic_root"), "topic_root").strip("/"),
        heartbeat_check_hz=_number(monitor.get("heartbeat_check_hz"), "monitor.heartbeat_check_hz"),
        warning_timeout_seconds=_number(monitor.get("warning_timeout_seconds"), "monitor.warning_timeout_seconds"),
        error_timeout_seconds=_number(monitor.get("error_timeout_seconds"), "monitor.error_timeout_seconds"),
        log_capacity=_integer(monitor.get("log_capacity"), "monitor.log_capacity"),
        subscriber_client_id=_string(payload.get("subscriber_client_id"), "subscriber_client_id"),
    )
    if not 1 <= config.port <= 65535:
        raise MqttConfigError("broker.port 必须在 1 到 65535 之间")
    if config.qos not in (0, 1):
        raise MqttConfigError("qos 仅支持 0 或 1")
    if "+" in config.topic_root or "#" in config.topic_root or not config.topic_root:
        raise MqttConfigError("topic_root 不能为空或包含 MQTT 通配符")
    if config.heartbeat_check_hz <= 0:
        raise MqttConfigError("heartbeat_check_hz 必须大于 0")
    if not 0 < config.warning_timeout_seconds < config.error_timeout_seconds:
        raise MqttConfigError("心跳 warning 超时必须小于 error 超时")
    if config.log_capacity < 1:
        raise MqttConfigError("log_capacity 必须大于 0")
    return config


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MqttConfigError(f"{name} 必须是对象")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MqttConfigError(f"{name} 必须是非空字符串")
    return value.strip()


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MqttConfigError(f"{name} 必须是整数")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MqttConfigError(f"{name} 必须是数字")
    return float(value)
