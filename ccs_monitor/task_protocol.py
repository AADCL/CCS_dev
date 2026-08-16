from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass
from typing import Any

import msgpack

from .task_config import TaskSystemConfig
from .task_models import DeviceSubtask, TaskDefinition


class TaskProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class TaskEnvelope:
    task_id: str
    subtask_id: str
    device_id: str
    execution_id: str
    message_type: str
    request_id: str
    sequence: int
    sent_at_ns: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class EncodedSubtask:
    compressed: bytes
    chunks: tuple[bytes, ...]
    crc32: int
    raw_bytes: int


class TaskProtocol:
    MESSAGE_TYPES = {
        "task_prepare", "task_chunk", "task_commit", "execute_task",
        "cancel_execution", "stop_task", "command_ack", "task_heartbeat",
        "task_status", "waypoint_progress",
    }

    def __init__(self, config: TaskSystemConfig) -> None:
        self.config = config

    def encode(self, envelope: TaskEnvelope) -> bytes:
        self._validate(envelope)
        data = msgpack.packb({
            "schema_version": 1, "protocol_id": self.config.protocol_id,
            "task_id": envelope.task_id, "subtask_id": envelope.subtask_id,
            "device_id": envelope.device_id, "execution_id": envelope.execution_id,
            "message_type": envelope.message_type, "request_id": envelope.request_id,
            "sequence": envelope.sequence, "sent_at_ns": envelope.sent_at_ns,
            "payload": envelope.payload,
        }, use_bin_type=True)
        if len(data) > self.config.max_datagram_bytes:
            raise TaskProtocolError("任务数据报超过大小限制")
        return data

    def decode(self, datagram: bytes) -> TaskEnvelope:
        if len(datagram) > self.config.max_datagram_bytes:
            raise TaskProtocolError("任务数据报超过大小限制")
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except Exception as exc:
            raise TaskProtocolError(f"MessagePack 解码失败：{exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise TaskProtocolError("任务信封 schema 无效")
        if raw.get("protocol_id") != self.config.protocol_id:
            raise TaskProtocolError("任务协议 ID 不匹配")
        try:
            envelope = TaskEnvelope(
                str(raw["task_id"]), str(raw["subtask_id"]), str(raw["device_id"]),
                str(raw.get("execution_id", "")), str(raw["message_type"]),
                str(raw["request_id"]), int(raw["sequence"]), int(raw["sent_at_ns"]),
                raw.get("payload", {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskProtocolError(f"任务信封字段无效：{exc}") from exc
        self._validate(envelope)
        return envelope

    def encode_subtask(self, task: TaskDefinition, subtask: DeviceSubtask) -> EncodedSubtask:
        payload = {
            "schema_version": 1, "task_id": task.task_id, "task_name": task.name,
            "map_id": task.map_id, "frame_id": task.frame_id,
            "subtask_id": subtask.subtask_id, "device_id": subtask.device_id,
            "revision": subtask.revision, "cruise_speed_mps": subtask.cruise_speed_mps,
            "start_delay_seconds": subtask.start_delay_seconds,
            "waypoints": [
                {"index": index, "waypoint_id": point.waypoint_id, "x": point.x, "y": point.y, "z": point.z}
                for index, point in enumerate(subtask.waypoints)
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(raw)
        if len(compressed) > self.config.max_compressed_bytes:
            raise TaskProtocolError("压缩后的子任务超过大小限制")
        chunks = tuple(
            compressed[index:index + self.config.chunk_payload_bytes]
            for index in range(0, len(compressed), self.config.chunk_payload_bytes)
        )
        return EncodedSubtask(compressed, chunks, zlib.crc32(compressed) & 0xFFFFFFFF, len(raw))

    def decode_subtask(self, compressed: bytes, expected_crc32: int) -> dict[str, Any]:
        if len(compressed) > self.config.max_compressed_bytes:
            raise TaskProtocolError("压缩子任务超过大小限制")
        if zlib.crc32(compressed) & 0xFFFFFFFF != expected_crc32:
            raise TaskProtocolError("子任务 CRC32 校验失败")
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(compressed, self.config.max_compressed_bytes * 8)
            raw += decoder.flush()
            payload = json.loads(raw.decode("utf-8"))
        except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskProtocolError(f"子任务解压失败：{exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise TaskProtocolError("子任务内容 schema 无效")
        return payload

    def _validate(self, envelope: TaskEnvelope) -> None:
        for name, value in (("task_id", envelope.task_id), ("subtask_id", envelope.subtask_id),
                            ("device_id", envelope.device_id), ("request_id", envelope.request_id)):
            if not value or len(value) > 128:
                raise TaskProtocolError(f"{name} 无效")
        if envelope.message_type not in self.MESSAGE_TYPES:
            raise TaskProtocolError("任务消息类型无效")
        if envelope.sequence < 0 or envelope.sent_at_ns < 0 or not isinstance(envelope.payload, dict):
            raise TaskProtocolError("任务序列、时间或 payload 无效")
        self._validate_finite(envelope.payload)
        if envelope.message_type == "task_chunk":
            if not isinstance(envelope.payload.get("data"), bytes):
                raise TaskProtocolError("task_chunk.data 必须为二进制")
            count = envelope.payload.get("chunk_count")
            index = envelope.payload.get("chunk_index")
            if not isinstance(count, int) or not isinstance(index, int) or count < 1 or not 0 <= index < count:
                raise TaskProtocolError("任务分片索引无效")
        if envelope.message_type == "command_ack" and not isinstance(envelope.payload.get("accepted"), bool):
            raise TaskProtocolError("ACK accepted 必须为布尔值")

    @classmethod
    def _validate_finite(cls, value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise TaskProtocolError("payload 包含非有限数值")
        if isinstance(value, dict):
            for item in value.values():
                cls._validate_finite(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._validate_finite(item)

