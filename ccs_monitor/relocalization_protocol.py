from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import msgpack

from .relocalization_config import RelocalizationConfig


class RelocalizationProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class RelocalizationEnvelope:
    map_id: str
    device_id: str
    session_id: str
    request_id: str
    message_type: str
    sequence: int
    sent_at_ns: int
    payload: dict[str, Any]


class RelocalizationProtocol:
    MESSAGE_TYPES = {
        "negotiate", "negotiation_status", "map_offer", "download_status",
        "start_stack", "stack_status", "initial_pose", "relocalization_result",
        "session_heartbeat", "command_error",
    }

    def __init__(self, config: RelocalizationConfig) -> None:
        self.config = config

    def encode(self, envelope: RelocalizationEnvelope) -> bytes:
        self._validate(envelope)
        data = msgpack.packb({
            "schema_version": 1, "protocol_id": self.config.protocol_id,
            "map_id": envelope.map_id, "device_id": envelope.device_id,
            "session_id": envelope.session_id, "request_id": envelope.request_id,
            "message_type": envelope.message_type, "sequence": envelope.sequence,
            "sent_at_ns": envelope.sent_at_ns, "payload": envelope.payload,
        }, use_bin_type=True)
        if len(data) > self.config.max_datagram_bytes:
            raise RelocalizationProtocolError("重定位数据报超过大小限制")
        return data

    def decode(self, data: bytes) -> RelocalizationEnvelope:
        if len(data) > self.config.max_datagram_bytes:
            raise RelocalizationProtocolError("重定位数据报超过大小限制")
        try:
            raw = msgpack.unpackb(data, raw=False, strict_map_key=True)
        except Exception as exc:
            raise RelocalizationProtocolError(f"MessagePack 解码失败：{exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise RelocalizationProtocolError("重定位信封 schema 无效")
        if raw.get("protocol_id") != self.config.protocol_id:
            raise RelocalizationProtocolError("重定位协议 ID 不匹配")
        try:
            envelope = RelocalizationEnvelope(
                str(raw["map_id"]), str(raw["device_id"]), str(raw["session_id"]),
                str(raw["request_id"]), str(raw["message_type"]), int(raw["sequence"]),
                int(raw["sent_at_ns"]), raw.get("payload", {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RelocalizationProtocolError(f"重定位信封字段无效：{exc}") from exc
        self._validate(envelope)
        return envelope

    def _validate(self, envelope: RelocalizationEnvelope) -> None:
        for name in ("map_id", "device_id", "session_id", "request_id"):
            value = getattr(envelope, name)
            if not value or len(value) > 128:
                raise RelocalizationProtocolError(f"{name} 无效")
        if envelope.message_type not in self.MESSAGE_TYPES:
            raise RelocalizationProtocolError("重定位 message_type 无效")
        if envelope.sequence < 0 or envelope.sent_at_ns < 0 or not isinstance(envelope.payload, dict):
            raise RelocalizationProtocolError("重定位序列、时间或 payload 无效")
        self._finite(envelope.payload)

    @classmethod
    def _finite(cls, value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise RelocalizationProtocolError("重定位 payload 包含非有限数值")
        if isinstance(value, dict):
            for item in value.values():
                cls._finite(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._finite(item)
