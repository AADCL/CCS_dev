from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import msgpack

from .udp_config import UdpTelemetryConfig


class UdpProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class UdpEnvelope:
    device_id: str
    session_id: str
    message_type: str
    sequence: int
    sent_at_ns: int
    level: int | None
    payload: dict[str, Any]


class UdpTelemetryProtocol:
    def __init__(self, config: UdpTelemetryConfig) -> None:
        self.config = config

    def decode(self, datagram: bytes) -> UdpEnvelope:
        if len(datagram) > self.config.max_datagram_bytes:
            raise UdpProtocolError("数据报超过大小限制")
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except Exception as exc:
            raise UdpProtocolError(f"MessagePack 解码失败：{exc}") from exc
        if not isinstance(raw, dict):
            raise UdpProtocolError("数据报根节点必须是对象")
        if raw.get("schema_version") != 1:
            raise UdpProtocolError("不支持的 schema_version")
        if raw.get("protocol_id") != self.config.protocol_id:
            raise UdpProtocolError("protocol_id 不匹配")
        descriptors = self.config.descriptors_for_hash(str(raw.get("descriptor_hash", "")))
        if descriptors is None:
            raise UdpProtocolError("遥测描述哈希不匹配")
        device_id = raw.get("device_id")
        session_id = raw.get("session_id")
        message_type = raw.get("message_type")
        sequence = raw.get("sequence")
        sent_at_ns = raw.get("sent_at_ns")
        level = raw.get("level")
        payload = raw.get("payload", {})
        if not isinstance(device_id, str) or not device_id.strip():
            raise UdpProtocolError("device_id 无效")
        if not isinstance(session_id, str) or not session_id.strip():
            raise UdpProtocolError("session_id 无效")
        if message_type not in {"heartbeat", "telemetry"}:
            raise UdpProtocolError("message_type 无效")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise UdpProtocolError("sequence 无效")
        if not isinstance(sent_at_ns, int) or isinstance(sent_at_ns, bool) or sent_at_ns < 0:
            raise UdpProtocolError("sent_at_ns 无效")
        if not isinstance(payload, dict):
            raise UdpProtocolError("payload 必须是对象")
        if message_type == "heartbeat":
            if level is not None or payload:
                raise UdpProtocolError("心跳包不能包含 level 或 payload")
        elif level not in {1, 2, 3}:
            raise UdpProtocolError("遥测包 level 无效")
        self._validate_payload(payload, level, descriptors)
        return UdpEnvelope(device_id, session_id, message_type, sequence, sent_at_ns, level, payload)

    def encode(self, envelope: UdpEnvelope) -> bytes:
        raw = {
            "schema_version": 1,
            "protocol_id": self.config.protocol_id,
            "descriptor_hash": self.config.descriptor_hash,
            "device_id": envelope.device_id,
            "session_id": envelope.session_id,
            "message_type": envelope.message_type,
            "sequence": envelope.sequence,
            "sent_at_ns": envelope.sent_at_ns,
            "level": envelope.level,
            "payload": envelope.payload,
        }
        encoded = msgpack.packb(raw, use_bin_type=True)
        if len(encoded) > self.config.max_datagram_bytes:
            raise UdpProtocolError("编码结果超过数据报大小限制")
        return encoded

    def _validate_payload(self, payload: dict[str, Any], level: int | None,
                          descriptors=None) -> None:
        descriptors = descriptors or self.config.descriptors
        for name, value in payload.items():
            if not isinstance(name, str):
                raise UdpProtocolError("payload 键必须是字符串")
            descriptor = next((item for item in descriptors if item.name == name), None)
            if descriptor is None or descriptor.level != level:
                raise UdpProtocolError(f"未知或等级不匹配的数据项：{name}")
            if not isinstance(value, dict):
                raise UdpProtocolError(f"{name} 必须是对象")
            self._validate_finite(value, name)
            valid = value.get("valid", False)
            if not isinstance(valid, bool):
                raise UdpProtocolError(f"{name}.valid 必须是布尔值")
            if descriptor.data_type == "pose" and valid:
                self._require_numbers(value, name, ("x", "y", "z", "roll", "pitch", "yaw"))
            elif descriptor.data_type == "imu" and valid:
                self._require_numbers(
                    value,
                    name,
                    (
                        "roll", "pitch", "yaw", "angular_velocity_x", "angular_velocity_y",
                        "angular_velocity_z", "linear_acceleration_x", "linear_acceleration_y",
                        "linear_acceleration_z",
                    ),
                )
            elif descriptor.data_type in {"pointcloud_status", "availability"}:
                status = value.get("status", "unknown")
                if status not in {"available", "unavailable", "unknown"}:
                    raise UdpProtocolError(f"{name}.status 无效")
            elif descriptor.data_type == "text_status":
                status = value.get("status", "unknown")
                text_value = value.get("value")
                if status not in {"available", "unavailable", "unknown"}:
                    raise UdpProtocolError(f"{name}.status 无效")
                if text_value is not None and (not isinstance(text_value, str) or len(text_value) > 128):
                    raise UdpProtocolError(f"{name}.value 必须是长度不超过 128 的字符串")

    @classmethod
    def _validate_finite(cls, value: Any, path: str) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise UdpProtocolError(f"{path} 包含非有限数值")
        if isinstance(value, dict):
            for key, item in value.items():
                cls._validate_finite(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                cls._validate_finite(item, f"{path}[{index}]")

    @staticmethod
    def _require_numbers(value: dict[str, Any], name: str, fields: tuple[str, ...]) -> None:
        for field in fields:
            item = value.get(field)
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                raise UdpProtocolError(f"{name}.{field} 必须是数值")
