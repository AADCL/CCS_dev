from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class MqttProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class MqttEnvelope:
    schema_version: str
    message_type: str
    timestamp: datetime
    device_id: str
    ip_address: str
    sequence: int | None = None


@dataclass(frozen=True)
class MqttPresenceEvent(MqttEnvelope):
    status: str = "offline"


@dataclass(frozen=True)
class MqttHeartbeatEvent(MqttEnvelope):
    pass


@dataclass(frozen=True)
class MqttStatusEvent(MqttEnvelope):
    fcu_connected: bool | None = None
    armed: bool | None = None
    system_status: int | None = None
    flight_mode: str = "unknown"
    battery_percentage: float | None = None
    battery_voltage: float | None = None
    battery_current: float | None = None
    mission_status: str = "unknown"


MqttEvent = MqttPresenceEvent | MqttHeartbeatEvent | MqttStatusEvent


class MqttMessageParser:
    def __init__(self, topic_root: str = "mqtav") -> None:
        self.topic_root = topic_root.strip("/")

    def parse(self, topic: str, payload: bytes | str) -> MqttEvent:
        topic_device_id, topic_kind = self._parse_topic(topic)
        try:
            text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise MqttProtocolError(f"MQTT payload 不是有效 UTF-8 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise MqttProtocolError("MQTT payload 根节点必须是对象")
        if data.get("schema_version") != "1.0":
            raise MqttProtocolError("仅支持 MQTT schema_version 1.0")
        message_type = self._required_string(data, "message_type")
        if message_type != topic_kind:
            raise MqttProtocolError("主题类型与 message_type 不一致")
        timestamp = self._timestamp(self._required_string(data, "timestamp"))
        device = self._required_mapping(data, "device")
        device_id = self._required_string(device, "id")
        ip_address = self._required_string(device, "ip")
        if device_id != topic_device_id:
            raise MqttProtocolError("主题 device_id 与 payload 不一致")
        common = dict(
            schema_version="1.0",
            message_type=message_type,
            timestamp=timestamp,
            device_id=device_id,
            ip_address=ip_address,
        )
        if message_type == "presence":
            status = self._required_string(data, "status")
            if status not in {"online", "offline"}:
                raise MqttProtocolError("presence status 必须为 online 或 offline")
            return MqttPresenceEvent(**common, status=status)
        sequence = data.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise MqttProtocolError("sequence 必须是非负整数")
        common["sequence"] = sequence
        if message_type == "heartbeat":
            return MqttHeartbeatEvent(**common)
        if message_type != "status":
            raise MqttProtocolError(f"不支持的 message_type：{message_type}")
        health = self._required_mapping(data, "health")
        battery = self._required_mapping(health, "battery")
        return MqttStatusEvent(
            **common,
            fcu_connected=self._optional_bool(health.get("fcu_connected"), "health.fcu_connected"),
            armed=self._optional_bool(health.get("armed"), "health.armed"),
            system_status=self._optional_int(health.get("system_status"), "health.system_status"),
            flight_mode=self._optional_string(health.get("flight_mode"), "health.flight_mode", "unknown"),
            battery_percentage=self._battery_percentage(battery.get("percentage")),
            battery_voltage=self._optional_number(battery.get("voltage"), "health.battery.voltage"),
            battery_current=self._optional_number(battery.get("current"), "health.battery.current"),
            mission_status=self._optional_string(health.get("mission_status"), "health.mission_status", "unknown"),
        )

    def _parse_topic(self, topic: str) -> tuple[str, str]:
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != self.topic_root or not parts[1]:
            raise MqttProtocolError("MQTT 主题格式无效")
        if parts[2] not in {"presence", "heartbeat", "status"}:
            raise MqttProtocolError("MQTT 主题类型无效")
        return parts[1], parts[2]

    @staticmethod
    def _required_mapping(data: dict[str, Any], name: str) -> dict[str, Any]:
        value = data.get(name)
        if not isinstance(value, dict):
            raise MqttProtocolError(f"{name} 必须是对象")
        return value

    @staticmethod
    def _required_string(data: dict[str, Any], name: str) -> str:
        value = data.get(name)
        if not isinstance(value, str) or not value:
            raise MqttProtocolError(f"{name} 必须是非空字符串")
        return value

    @staticmethod
    def _timestamp(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MqttProtocolError("timestamp 必须是 ISO 8601 时间") from exc
        if parsed.tzinfo is None:
            raise MqttProtocolError("timestamp 必须包含时区")
        return parsed

    @staticmethod
    def _optional_bool(value: Any, name: str) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise MqttProtocolError(f"{name} 必须是布尔值或 null")
        return value

    @staticmethod
    def _optional_int(value: Any, name: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise MqttProtocolError(f"{name} 必须是整数或 null")
        return value

    @staticmethod
    def _optional_number(value: Any, name: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MqttProtocolError(f"{name} 必须是数字或 null")
        return float(value)

    @staticmethod
    def _optional_string(value: Any, name: str, default: str) -> str:
        if value is None:
            return default
        if not isinstance(value, str):
            raise MqttProtocolError(f"{name} 必须是字符串或 null")
        return value or default

    @classmethod
    def _battery_percentage(cls, value: Any) -> float | None:
        percentage = cls._optional_number(value, "health.battery.percentage")
        if percentage is not None and not 0 <= percentage <= 100:
            raise MqttProtocolError("电量百分比必须在 0 到 100 之间")
        return percentage
