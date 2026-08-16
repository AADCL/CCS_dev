from __future__ import annotations

import math
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import msgpack
import numpy as np

from .map_building_config import MapBuildingConfig
from .models import MapBounds, utc_now


class MapBuildingProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class MapBuildingEnvelope:
    map_id: str
    device_id: str
    session_id: str
    message_type: str
    sequence: int
    sent_at_ns: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class MapBuildingSessionSnapshot:
    map_id: str
    device_id: str
    session_id: str
    state: str
    message: str
    started_at: datetime
    ended_at: datetime | None = None
    complete_frames: int = 0
    dropped_frames: int = 0
    received_points: int = 0
    fused_points: int = 0
    last_data_at: datetime | None = None


class MapBuildingProtocol:
    MESSAGE_TYPES = {
        "start_mapping", "stop_mapping", "command_ack", "session_heartbeat",
        "cloud_chunk", "session_status",
    }

    def __init__(self, config: MapBuildingConfig) -> None:
        self.config = config

    def encode(self, envelope: MapBuildingEnvelope) -> bytes:
        self._validate_envelope(envelope)
        raw = {
            "schema_version": 1,
            "protocol_id": self.config.protocol_id,
            "map_id": envelope.map_id,
            "device_id": envelope.device_id,
            "session_id": envelope.session_id,
            "message_type": envelope.message_type,
            "sequence": envelope.sequence,
            "sent_at_ns": envelope.sent_at_ns,
            "payload": envelope.payload,
        }
        encoded = msgpack.packb(raw, use_bin_type=True)
        if len(encoded) > self.config.max_datagram_bytes:
            raise MapBuildingProtocolError("建图数据报超过大小限制")
        return encoded

    def decode(self, datagram: bytes) -> MapBuildingEnvelope:
        if len(datagram) > self.config.max_datagram_bytes:
            raise MapBuildingProtocolError("建图数据报超过大小限制")
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except Exception as exc:
            raise MapBuildingProtocolError(f"MessagePack 解码失败：{exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise MapBuildingProtocolError("不支持的建图 schema_version")
        if raw.get("protocol_id") != self.config.protocol_id:
            raise MapBuildingProtocolError("建图 protocol_id 不匹配")
        try:
            envelope = MapBuildingEnvelope(
                map_id=raw["map_id"],
                device_id=raw["device_id"],
                session_id=raw["session_id"],
                message_type=raw["message_type"],
                sequence=raw["sequence"],
                sent_at_ns=raw["sent_at_ns"],
                payload=raw.get("payload", {}),
            )
        except KeyError as exc:
            raise MapBuildingProtocolError(f"建图信封缺少字段：{exc}") from exc
        self._validate_envelope(envelope)
        return envelope

    def _validate_envelope(self, envelope: MapBuildingEnvelope) -> None:
        for name, value in (
            ("map_id", envelope.map_id),
            ("device_id", envelope.device_id),
            ("session_id", envelope.session_id),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise MapBuildingProtocolError(f"{name} 无效")
        if envelope.message_type not in self.MESSAGE_TYPES:
            raise MapBuildingProtocolError("message_type 无效")
        if not isinstance(envelope.sequence, int) or isinstance(envelope.sequence, bool) or envelope.sequence < 0:
            raise MapBuildingProtocolError("sequence 无效")
        if not isinstance(envelope.sent_at_ns, int) or envelope.sent_at_ns < 0:
            raise MapBuildingProtocolError("sent_at_ns 无效")
        if not isinstance(envelope.payload, dict):
            raise MapBuildingProtocolError("payload 必须是对象")
        validator = getattr(self, f"_validate_{envelope.message_type}")
        validator(envelope.payload)

    def _validate_start_mapping(self, payload: dict[str, Any]) -> None:
        self._require_string(payload, "request_id")
        self._require_string(payload, "return_host")
        self._require_int(payload, "return_port", 1, 65535)
        self._require_number(payload, "cloud_rate_hz", 0.001)
        self._require_number(payload, "voxel_size_m", 0.001)
        if payload.get("compression") != "zlib" or payload.get("point_format") != "xyz_f32_le":
            raise MapBuildingProtocolError("开始指令压缩或点格式无效")
        if payload.get("coordinate_contract") != "sensor+map_body+body_sensor":
            raise MapBuildingProtocolError("坐标契约无效")

    def _validate_stop_mapping(self, payload: dict[str, Any]) -> None:
        self._require_string(payload, "request_id")
        self._require_string(payload, "reason")

    def _validate_command_ack(self, payload: dict[str, Any]) -> None:
        self._require_string(payload, "request_id")
        if payload.get("command") not in {"start_mapping", "stop_mapping"}:
            raise MapBuildingProtocolError("ACK command 无效")
        if not isinstance(payload.get("accepted"), bool):
            raise MapBuildingProtocolError("ACK accepted 必须是布尔值")
        if payload.get("reason") is not None and not isinstance(payload.get("reason"), str):
            raise MapBuildingProtocolError("ACK reason 无效")

    def _validate_session_heartbeat(self, payload: dict[str, Any]) -> None:
        if payload.get("state") not in {"starting", "mapping", "stopping", "stopped", "error"}:
            raise MapBuildingProtocolError("会话心跳状态无效")

    def _validate_session_status(self, payload: dict[str, Any]) -> None:
        self._validate_session_heartbeat(payload)
        if payload.get("reason") is not None and not isinstance(payload.get("reason"), str):
            raise MapBuildingProtocolError("会话状态原因无效")

    def _validate_cloud_chunk(self, payload: dict[str, Any]) -> None:
        self._require_int(payload, "frame_id", 0)
        chunk_count = self._require_int(payload, "chunk_count", 1, 4096)
        self._require_int(payload, "chunk_index", 0, chunk_count - 1)
        self._require_int(payload, "frame_crc32", 0, 0xFFFFFFFF)
        self._require_int(payload, "sample_stamp_ns", 0)
        self._require_int(payload, "point_count", 1, self.config.max_frame_points)
        data = payload.get("data")
        if not isinstance(data, bytes) or not data:
            raise MapBuildingProtocolError("cloud_chunk.data 必须是非空二进制")
        self._validate_transform(payload.get("map_from_body"), "map_from_body")
        self._validate_transform(payload.get("body_from_sensor"), "body_from_sensor")

    def _validate_transform(self, value: Any, name: str) -> None:
        if not isinstance(value, dict):
            raise MapBuildingProtocolError(f"{name} 必须是对象")
        numbers = []
        for key in ("x", "y", "z", "qx", "qy", "qz", "qw"):
            item = value.get(key)
            if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)):
                raise MapBuildingProtocolError(f"{name}.{key} 必须是有限数值")
            numbers.append(float(item))
        norm = math.sqrt(sum(item * item for item in numbers[3:]))
        if norm < 1e-6:
            raise MapBuildingProtocolError(f"{name} 四元数无效")

    @staticmethod
    def _require_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise MapBuildingProtocolError(f"{key} 无效")
        return value

    @staticmethod
    def _require_int(payload: dict[str, Any], key: str, minimum: int, maximum: int | None = None) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise MapBuildingProtocolError(f"{key} 无效")
        if maximum is not None and value > maximum:
            raise MapBuildingProtocolError(f"{key} 超出范围")
        return value

    @staticmethod
    def _require_number(payload: dict[str, Any], key: str, minimum: float) -> float:
        value = payload.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise MapBuildingProtocolError(f"{key} 无效")
        number = float(value)
        if not math.isfinite(number) or number < minimum:
            raise MapBuildingProtocolError(f"{key} 超出范围")
        return number


@dataclass
class _PendingFrame:
    created_at: float
    point_count: int
    crc32: int
    sample_stamp_ns: int
    chunk_count: int
    map_from_body: dict[str, float]
    body_from_sensor: dict[str, float]
    chunks: dict[int, bytes] = field(default_factory=dict)


class CloudFrameAssembler:
    def __init__(self, config: MapBuildingConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.clock = clock
        self._frames: dict[int, _PendingFrame] = {}

    def push(self, envelope: MapBuildingEnvelope) -> tuple[np.ndarray, tuple[Any, ...]] | None:
        if envelope.message_type != "cloud_chunk":
            raise MapBuildingProtocolError("仅 cloud_chunk 可进入帧重组器")
        payload = envelope.payload
        frame_id = int(payload["frame_id"])
        metadata = (
            int(payload["point_count"]), int(payload["frame_crc32"]),
            int(payload["sample_stamp_ns"]), int(payload["chunk_count"]),
            payload["map_from_body"], payload["body_from_sensor"],
        )
        pending = self._frames.get(frame_id)
        if pending is None:
            pending = _PendingFrame(self.clock(), *metadata)
            self._frames[frame_id] = pending
        elif (
            pending.point_count, pending.crc32, pending.sample_stamp_ns, pending.chunk_count,
            pending.map_from_body, pending.body_from_sensor,
        ) != metadata:
            self._frames.pop(frame_id, None)
            raise MapBuildingProtocolError("同一 frame_id 的分片元数据不一致")
        pending.chunks.setdefault(int(payload["chunk_index"]), payload["data"])
        if len(pending.chunks) != pending.chunk_count:
            return None
        compressed = b"".join(pending.chunks[index] for index in range(pending.chunk_count))
        self._frames.pop(frame_id, None)
        if zlib.crc32(compressed) & 0xFFFFFFFF != pending.crc32:
            raise MapBuildingProtocolError("点云帧 CRC32 校验失败")
        expected_bytes = pending.point_count * 12
        if expected_bytes > self.config.max_decompressed_bytes:
            raise MapBuildingProtocolError("点云帧解压尺寸超过限制")
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(compressed, self.config.max_decompressed_bytes + 1)
            raw += decoder.flush()
        except zlib.error as exc:
            raise MapBuildingProtocolError(f"点云 zlib 解压失败：{exc}") from exc
        if decoder.unconsumed_tail or len(raw) != expected_bytes:
            raise MapBuildingProtocolError("点云解压尺寸与 point_count 不一致")
        points = np.frombuffer(raw, dtype="<f4").reshape((-1, 3)).astype(np.float64)
        if not np.isfinite(points).all():
            raise MapBuildingProtocolError("点云包含非有限坐标")
        transformed = transform_sensor_points(points, pending.map_from_body, pending.body_from_sensor)
        pose = pending.map_from_body
        trajectory = (
            pending.sample_stamp_ns,
            *(float(pose[key]) for key in ("x", "y", "z", "qx", "qy", "qz", "qw")),
        )
        return transformed.astype(np.float32), trajectory

    def expire(self) -> int:
        threshold = self.clock() - self.config.frame_timeout_seconds
        expired = [frame_id for frame_id, frame in self._frames.items() if frame.created_at < threshold]
        for frame_id in expired:
            self._frames.pop(frame_id, None)
        return len(expired)


class VoxelMapAccumulator:
    def __init__(self, voxel_size_m: float, max_voxels: int, max_preview_points: int) -> None:
        self.voxel_size_m = float(voxel_size_m)
        self.max_voxels = int(max_voxels)
        self.max_preview_points = int(max_preview_points)
        self._voxels: dict[tuple[int, int, int], tuple[np.ndarray, int]] = {}
        self.received_points = 0

    def add(self, points: np.ndarray) -> int:
        array = np.asarray(points, dtype=np.float64)
        if array.ndim != 2 or array.shape[1] != 3 or not np.isfinite(array).all():
            raise ValueError("点云必须是有限 XYZ 数组")
        self.received_points += len(array)
        keys = np.floor(array / self.voxel_size_m).astype(np.int64)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        for index, key_values in enumerate(unique):
            selected = array[inverse == index]
            key = tuple(int(value) for value in key_values)
            total = selected.sum(axis=0)
            count = len(selected)
            previous = self._voxels.get(key)
            if previous is not None:
                total = total + previous[0]
                count += previous[1]
            elif len(self._voxels) >= self.max_voxels:
                raise ValueError("累计体素数量超过配置限制")
            self._voxels[key] = (total, count)
        return len(self._voxels)

    def points(self) -> np.ndarray:
        if not self._voxels:
            return np.empty((0, 3), dtype=np.float32)
        ordered = sorted(self._voxels)
        return np.asarray(
            [self._voxels[key][0] / self._voxels[key][1] for key in ordered],
            dtype=np.float32,
        )

    def preview(self) -> np.ndarray:
        points = self.points()
        if len(points) <= self.max_preview_points:
            return points
        indices = np.linspace(0, len(points) - 1, self.max_preview_points, dtype=np.int64)
        return points[indices]

    def bounds(self) -> MapBounds | None:
        points = self.points()
        if not len(points):
            return None
        minimum = points.min(axis=0)
        maximum = points.max(axis=0)
        return MapBounds(*minimum.tolist(), *maximum.tolist())


def transform_sensor_points(
    points: np.ndarray,
    map_from_body: dict[str, float],
    body_from_sensor: dict[str, float],
) -> np.ndarray:
    body = _transform_matrix(map_from_body)
    sensor = _transform_matrix(body_from_sensor)
    rotation = (body @ sensor)[:3, :3]
    translation = (body @ sensor)[:3, 3]
    return np.asarray(points, dtype=np.float64) @ rotation.T + translation


def _transform_matrix(value: dict[str, float]) -> np.ndarray:
    quaternion = np.asarray([value[key] for key in ("qx", "qy", "qz", "qw")], dtype=np.float64)
    quaternion /= np.linalg.norm(quaternion)
    x, y, z, w = quaternion
    rotation = np.asarray([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = [value[key] for key in ("x", "y", "z")]
    return matrix


def write_binary_pcd(path: str | Path, points: np.ndarray) -> Path:
    target = Path(path)
    array = np.asarray(points, dtype="<f4")
    if array.ndim != 2 or array.shape[1] != 3 or not len(array) or not np.isfinite(array).all():
        raise ValueError("PCD 写入需要非空有限 XYZ 点云")
    header = (
        "VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
        f"WIDTH {len(array)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {len(array)}\nDATA binary\n"
    ).encode("ascii")
    with target.open("wb") as handle:
        handle.write(header)
        handle.write(array.tobytes(order="C"))
    return target
