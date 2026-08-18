from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
import zlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import yaml
from PySide6.QtCore import QObject, Signal

from .map_building import MapBuildingEnvelope, MapBuildingProtocol, MapBuildingProtocolError
from .map_building_config import MapBuildingConfig
from .models import (
    MapBounds, PgmArtifactManifest, PgmDownloadSnapshot, PgmFusionJob,
    PgmFusionSource, PgmMapMetadata, PgmTransform2D,
)
from .pgm_map import PgmMapData, PgmMapError, PgmMapLoader


MAX_PGM_BYTES = 64 * 1024 * 1024
MAX_PGM_CHUNKS = 100_000
MAX_RETRANSMISSION_ROUNDS = 5
PGM_INACTIVITY_SECONDS = 10.0


class PgmFusionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PgmFusionResult:
    metadata: PgmMapMetadata
    clipped_cells: int
    clipped_area_m2: float
    source_cells: int
    output_cells: int


class PgmArtifactProtocol:
    """PGM artifact extension for the shared ccs-map-stream-v1 envelope."""

    def __init__(self, config: MapBuildingConfig) -> None:
        self.config = config
        self.envelope_protocol = MapBuildingProtocol(config)

    def request_artifact(
        self, *, target_map_id: str, device_id: str, source_map_id: str,
        session_id: str, request_id: str, sequence: int, return_host: str,
    ) -> MapBuildingEnvelope:
        return MapBuildingEnvelope(
            target_map_id, device_id, session_id, "request_pgm_artifact", sequence,
            time.time_ns(), {
                "request_id": request_id,
                "source_map_id": source_map_id,
                "return_host": return_host,
                "return_port": self.config.data_port,
                "compression": "zlib",
            },
        )

    def request_chunks(
        self, *, target_map_id: str, device_id: str, source_map_id: str,
        session_id: str, request_id: str, sequence: int, missing_chunks: Iterable[int],
    ) -> MapBuildingEnvelope:
        return MapBuildingEnvelope(
            target_map_id, device_id, session_id, "request_pgm_chunks", sequence,
            time.time_ns(), {
                "request_id": request_id,
                "source_map_id": source_map_id,
                "missing_chunks": sorted(set(int(value) for value in missing_chunks)),
            },
        )

    def encode(self, envelope: MapBuildingEnvelope) -> bytes:
        return self.envelope_protocol.encode(envelope)

    def decode(self, datagram: bytes) -> MapBuildingEnvelope:
        return self.envelope_protocol.decode(datagram)

    @staticmethod
    def manifest(envelope: MapBuildingEnvelope) -> PgmArtifactManifest:
        if envelope.message_type != "pgm_manifest":
            raise MapBuildingProtocolError("消息不是 PGM manifest")
        payload = envelope.payload
        return PgmArtifactManifest(
            device_id=envelope.device_id,
            source_map_id=str(payload["source_map_id"]),
            session_id=envelope.session_id,
            frame_id=str(payload["frame_id"]),
            pgm_format=str(payload["pgm_format"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            resolution=float(payload["resolution"]),
            origin=tuple(float(value) for value in payload["origin"]),
            negate=bool(payload["negate"]),
            occupied_thresh=float(payload["occupied_thresh"]),
            free_thresh=float(payload["free_thresh"]),
            generated_at=datetime.fromtimestamp(int(payload["generated_at_ns"]) / 1e9, timezone.utc),
            uncompressed_size=int(payload["uncompressed_size"]),
            compressed_size=int(payload["compressed_size"]),
            chunk_count=int(payload["chunk_count"]),
            crc32=int(payload["crc32"]),
            sha256=str(payload["sha256"]).lower(),
        )


@dataclass
class _Transfer:
    source: PgmFusionSource
    directory: Path
    session_id: str
    request_id: str
    state: str = "requesting"
    message: str = "正在请求端侧 PGM"
    manifest: PgmArtifactManifest | None = None
    chunks: dict[int, bytes] | None = None
    retries: int = 0
    sequence: int = 0
    last_activity: float = 0.0
    last_request: float = 0.0
    last_sequence: int = -1
    seen_chunk_sequences: set[int] | None = None


class PgmDownloadCoordinator(QObject):
    """Coordinates sequential downloads while MapBuildingService owns the socket."""

    source_updated = Signal(object)
    source_completed = Signal(object)
    failed = Signal(str, str)
    all_completed = Signal(object)

    def __init__(
        self, config: MapBuildingConfig,
        sender: Callable[[MapBuildingEnvelope, str], None], *,
        return_host: str = "0.0.0.0", clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.protocol = PgmArtifactProtocol(config)
        self.sender = sender
        self.return_host = return_host
        self.clock = clock
        self._queue: list[PgmFusionSource] = []
        self._completed: list[PgmFusionSource] = []
        self._current: _Transfer | None = None
        self._target_map_id = ""
        self._job_root: Path | None = None

    @property
    def active(self) -> bool:
        return self._current is not None or bool(self._queue)

    @property
    def completed_sources(self) -> tuple[PgmFusionSource, ...]:
        return tuple(self._completed)

    def start(self, target_map_id: str, sources: Iterable[PgmFusionSource], job_root: str | Path) -> None:
        if self.active:
            raise RuntimeError("已有 PGM 下载任务正在运行")
        remote = [item for item in sources if not item.existing_target_layer]
        if not remote:
            raise ValueError("至少需要一个端侧 PGM 来源")
        if any(not item.device_id or not item.device_ip or not item.source_map_id for item in remote):
            raise ValueError("端侧 PGM 来源缺少设备、IP 或 source_map_id")
        self._target_map_id = target_map_id
        self._job_root = Path(job_root)
        self._job_root.mkdir(parents=True, exist_ok=True)
        self._queue = remote
        self._completed = []
        self._begin_next()

    def resume(self, target_map_id: str, sources: Iterable[PgmFusionSource], job_root: str | Path) -> None:
        self.start(target_map_id, sources, job_root)

    def cancel(self) -> None:
        self._queue.clear()
        self._current = None

    def retry_current(self) -> None:
        transfer = self._current
        if transfer is None or transfer.state != "failed":
            return
        transfer.state = "requesting"
        transfer.message = "正在重新请求端侧 PGM"
        transfer.manifest = None
        transfer.chunks = None
        transfer.retries = 0
        transfer.request_id = uuid.uuid4().hex
        self._send_initial(transfer)

    def remove_current(self) -> None:
        if self._current is None:
            return
        self._current = None
        self._begin_next()

    def handle_envelope(self, envelope: MapBuildingEnvelope, peer_ip: str) -> bool:
        if envelope.message_type not in {
            "command_ack", "pgm_manifest", "pgm_chunk", "pgm_transfer_status"
        }:
            return False
        transfer = self._current
        if transfer is None:
            return False
        if (envelope.map_id != self._target_map_id
                or envelope.device_id.casefold() != str(transfer.source.device_id).casefold()
                or envelope.session_id != transfer.session_id):
            return False
        if peer_ip != transfer.source.device_ip:
            self._fail(transfer, "PGM 数据报来源 IP 与设备配置不一致")
            return True
        if envelope.message_type == "pgm_chunk":
            if transfer.seen_chunk_sequences is None:
                transfer.seen_chunk_sequences = set()
            if envelope.sequence in transfer.seen_chunk_sequences:
                return True
            transfer.seen_chunk_sequences.add(envelope.sequence)
            if len(transfer.seen_chunk_sequences) > MAX_PGM_CHUNKS:
                floor = max(transfer.seen_chunk_sequences) - MAX_PGM_CHUNKS // 2
                transfer.seen_chunk_sequences = {
                    value for value in transfer.seen_chunk_sequences if value >= floor
                }
        else:
            if envelope.sequence <= transfer.last_sequence:
                return True
            transfer.last_sequence = envelope.sequence
        transfer.last_activity = self.clock()
        payload = envelope.payload
        if envelope.message_type == "command_ack":
            if payload.get("request_id") != transfer.request_id:
                return True
            if not payload.get("accepted"):
                reason = str(payload.get("reason") or "端侧版本不支持 PGM 下载")
                self._fail(transfer, reason)
            else:
                if payload.get("command") == "request_pgm_chunks":
                    transfer.state = "retransmitting"
                    transfer.message = "端侧已确认补传请求"
                else:
                    transfer.state = "waiting_manifest"
                    transfer.message = "端侧已确认，等待文件清单"
                self._emit(transfer)
        elif envelope.message_type == "pgm_manifest":
            self._accept_manifest(transfer, envelope)
        elif envelope.message_type == "pgm_chunk":
            self._accept_chunk(transfer, envelope)
        elif payload.get("state") == "error":
            self._fail(transfer, str(payload.get("reason") or "端侧 PGM 传输失败"))
        else:
            missing = self._missing(transfer)
            if not missing:
                self._finalize(transfer)
            elif transfer.manifest is not None:
                transfer.retries += 1
                self._send_missing_requests(transfer, missing)
                transfer.state = "retransmitting"
                transfer.message = f"端侧报告完成，仍缺少 {len(missing)} 个分片"
                self._emit(transfer)
        return True

    def tick(self) -> None:
        transfer = self._current
        if transfer is None or transfer.state in {"failed", "complete"}:
            return
        now = self.clock()
        if transfer.manifest is None:
            if now - transfer.last_activity >= PGM_INACTIVITY_SECONDS:
                self._fail(transfer, "端侧版本不支持 PGM 下载或请求超时")
            elif now - transfer.last_request >= self.config.command_retry_seconds:
                if transfer.retries >= self.config.command_max_attempts:
                    self._fail(transfer, "端侧版本不支持 PGM 下载")
                else:
                    transfer.retries += 1
                    self._send_initial(transfer)
            return
        if now - transfer.last_activity < PGM_INACTIVITY_SECONDS:
            return
        missing = self._missing(transfer)
        if not missing:
            self._finalize(transfer)
        elif transfer.retries >= MAX_RETRANSMISSION_ROUNDS:
            self._fail(transfer, f"PGM 分片补传超过 {MAX_RETRANSMISSION_ROUNDS} 轮")
        else:
            transfer.retries += 1
            transfer.request_id = uuid.uuid4().hex
            self._send_missing_requests(transfer, missing)
            transfer.last_request = transfer.last_activity = now
            transfer.state = "retransmitting"
            transfer.message = f"正在补传 {len(missing)} 个分片（第 {transfer.retries} 轮）"
            self._emit(transfer)

    def _begin_next(self) -> None:
        if not self._queue:
            self.all_completed.emit(tuple(self._completed))
            return
        source = self._queue.pop(0)
        assert self._job_root is not None
        directory = self._job_root / str(source.device_id)
        directory.mkdir(parents=True, exist_ok=True)
        now = self.clock()
        self._current = _Transfer(
            source, directory, uuid.uuid4().hex, uuid.uuid4().hex,
            last_activity=now, last_request=now,
        )
        if self._restore_checkpoint(self._current):
            missing = self._missing(self._current)
            if not missing:
                self._finalize(self._current)
                return
            self._send_missing_requests(self._current, missing)
            self._current.state = "retransmitting"
            self._current.message = f"恢复任务，正在补传 {len(missing)} 个分片"
            self._emit(self._current)
            return
        self._send_initial(self._current)

    def _send_initial(self, transfer: _Transfer) -> None:
        envelope = self.protocol.request_artifact(
            target_map_id=self._target_map_id,
            device_id=str(transfer.source.device_id),
            source_map_id=transfer.source.source_map_id,
            session_id=transfer.session_id,
            request_id=transfer.request_id,
            sequence=self._next_sequence(transfer),
            return_host=self.return_host,
        )
        self.sender(envelope, transfer.source.device_ip)
        transfer.last_request = self.clock()
        self._emit(transfer)

    def _send_missing_requests(self, transfer: _Transfer, missing: list[int]) -> None:
        # Keep each MessagePack envelope below the configured UDP datagram limit.
        batch_size = max(1, min(200, (self.config.max_datagram_bytes - 360) // 6))
        for offset in range(0, len(missing), batch_size):
            transfer.request_id = uuid.uuid4().hex
            envelope = self.protocol.request_chunks(
                target_map_id=self._target_map_id,
                device_id=str(transfer.source.device_id),
                source_map_id=transfer.source.source_map_id,
                session_id=transfer.session_id,
                request_id=transfer.request_id,
                sequence=self._next_sequence(transfer),
                missing_chunks=missing[offset:offset + batch_size],
            )
            self.sender(envelope, transfer.source.device_ip)

    def _accept_manifest(self, transfer: _Transfer, envelope: MapBuildingEnvelope) -> None:
        manifest = self.protocol.manifest(envelope)
        if manifest.source_map_id != transfer.source.source_map_id:
            self._fail(transfer, "端侧 manifest 的 source_map_id 不匹配")
            return
        if manifest.width * manifest.height > MAX_PGM_BYTES:
            self._fail(transfer, "PGM 像素数量超过安全限制")
            return
        transfer.manifest = manifest
        transfer.chunks = {}
        transfer.state = "downloading"
        transfer.message = f"正在下载 {manifest.compressed_size:,} 字节"
        (transfer.directory / "manifest.json").write_text(
            json.dumps(_manifest_payload(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (transfer.directory / "chunks").mkdir(parents=True, exist_ok=True)
        self._write_chunk_state(transfer)
        self._emit(transfer)

    def _accept_chunk(self, transfer: _Transfer, envelope: MapBuildingEnvelope) -> None:
        manifest = transfer.manifest
        chunks = transfer.chunks
        if manifest is None or chunks is None:
            self._fail(transfer, "在 manifest 之前收到 PGM 分片")
            return
        payload = envelope.payload
        if payload["source_map_id"] != manifest.source_map_id or payload["chunk_count"] != manifest.chunk_count:
            self._fail(transfer, "PGM 分片元数据与 manifest 不一致")
            return
        index = int(payload["chunk_index"])
        if index not in chunks:
            chunks[index] = payload["data"]
            temporary = transfer.directory / "chunks" / f".{index}.tmp"
            target = transfer.directory / "chunks" / f"{index}.bin"
            temporary.write_bytes(payload["data"])
            os.replace(temporary, target)
            self._write_chunk_state(transfer)
        transfer.state = "downloading"
        transfer.message = f"已接收 {len(chunks)} / {manifest.chunk_count} 个分片"
        self._emit(transfer)
        if len(chunks) == manifest.chunk_count:
            self._finalize(transfer)

    def _finalize(self, transfer: _Transfer) -> None:
        manifest = transfer.manifest
        chunks = transfer.chunks
        if manifest is None or chunks is None or len(chunks) != manifest.chunk_count:
            return
        compressed = b"".join(chunks[index] for index in range(manifest.chunk_count))
        if len(compressed) != manifest.compressed_size:
            self._fail(transfer, "PGM 压缩大小与 manifest 不一致")
            return
        if zlib.crc32(compressed) & 0xFFFFFFFF != manifest.crc32:
            self._fail(transfer, "PGM CRC32 校验失败")
            return
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(compressed, MAX_PGM_BYTES + 1) + decoder.flush()
        except zlib.error as exc:
            self._fail(transfer, f"PGM zlib 解压失败：{exc}")
            return
        if decoder.unconsumed_tail or len(raw) != manifest.uncompressed_size:
            self._fail(transfer, "PGM 解压尺寸与 manifest 不一致")
            return
        if hashlib.sha256(raw).hexdigest() != manifest.sha256:
            self._fail(transfer, "PGM SHA-256 校验失败")
            return
        image_path = transfer.directory / "artifact.pgm"
        yaml_path = transfer.directory / "artifact.yaml"
        image_path.write_bytes(raw)
        metadata = PgmMapMetadata(
            image_path=image_path.name, yaml_path=yaml_path.name,
            resolution=manifest.resolution,
            origin_x=manifest.origin[0], origin_y=manifest.origin[1],
            origin_yaw=manifest.origin[2], image_width=manifest.width,
            image_height=manifest.height, negate=manifest.negate,
            occupied_thresh=manifest.occupied_thresh, free_thresh=manifest.free_thresh,
        )
        yaml_path.write_text(
            yaml.safe_dump(PgmMapLoader.normalized_yaml(metadata), sort_keys=False),
            encoding="utf-8",
        )
        try:
            loaded = PgmMapLoader().load_yaml(yaml_path)
            if loaded.pixels.shape != (manifest.height, manifest.width):
                raise PgmMapError("PGM 尺寸与 manifest 不一致")
        except PgmMapError as exc:
            image_path.unlink(missing_ok=True)
            yaml_path.unlink(missing_ok=True)
            self._fail(transfer, f"PGM 格式校验失败：{exc}")
            return
        completed = replace(
            transfer.source, pgm_path=str(image_path), yaml_path=str(yaml_path),
            manifest=manifest, artifact_sha256=manifest.sha256,
            source_frame_id=manifest.frame_id,
        )
        self._completed.append(completed)
        transfer.state = "complete"
        transfer.message = "PGM 下载及校验完成"
        self._emit(transfer, artifact_path=str(image_path))
        self.source_completed.emit(completed)
        self._current = None
        self._begin_next()

    def _fail(self, transfer: _Transfer, message: str) -> None:
        transfer.state = "failed"
        transfer.message = message
        self._emit(transfer)
        self.failed.emit(transfer.source.source_id, message)

    def _restore_checkpoint(self, transfer: _Transfer) -> bool:
        path = transfer.directory / "manifest.json"
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("source_map_id") != transfer.source.source_map_id:
                return False
            transfer.session_id = str(payload["session_id"])
            transfer.manifest = PgmArtifactManifest(
                device_id=str(payload["device_id"]), source_map_id=str(payload["source_map_id"]),
                session_id=transfer.session_id, frame_id=str(payload["frame_id"]),
                pgm_format=str(payload["pgm_format"]), width=int(payload["width"]),
                height=int(payload["height"]), resolution=float(payload["resolution"]),
                origin=tuple(float(value) for value in payload["origin"]),
                negate=bool(payload["negate"]), occupied_thresh=float(payload["occupied_thresh"]),
                free_thresh=float(payload["free_thresh"]),
                generated_at=datetime.fromisoformat(str(payload["generated_at"])),
                uncompressed_size=int(payload["uncompressed_size"]),
                compressed_size=int(payload["compressed_size"]), chunk_count=int(payload["chunk_count"]),
                crc32=int(payload["crc32"]), sha256=str(payload["sha256"]),
            )
            transfer.chunks = {}
            for chunk_path in (transfer.directory / "chunks").glob("*.bin"):
                index = int(chunk_path.stem)
                if 0 <= index < transfer.manifest.chunk_count:
                    transfer.chunks[index] = chunk_path.read_bytes()
            transfer.last_activity = self.clock()
            return True
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False

    @staticmethod
    def _write_chunk_state(transfer: _Transfer) -> None:
        path = transfer.directory / "chunk-state.json"
        temporary = transfer.directory / ".chunk-state.tmp"
        manifest = transfer.manifest
        payload = {
            "session_id": transfer.session_id,
            "source_map_id": transfer.source.source_map_id,
            "chunk_count": manifest.chunk_count if manifest else 0,
            "received_chunks": sorted((transfer.chunks or {}).keys()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _missing(transfer: _Transfer) -> list[int]:
        if transfer.manifest is None:
            return []
        chunks = transfer.chunks or {}
        return [index for index in range(transfer.manifest.chunk_count) if index not in chunks]

    @staticmethod
    def _next_sequence(transfer: _Transfer) -> int:
        transfer.sequence += 1
        return transfer.sequence

    def _emit(self, transfer: _Transfer, artifact_path: str | None = None) -> None:
        manifest = transfer.manifest
        chunks = transfer.chunks or {}
        self.source_updated.emit(PgmDownloadSnapshot(
            source_id=transfer.source.source_id,
            device_id=str(transfer.source.device_id),
            source_map_id=transfer.source.source_map_id,
            session_id=transfer.session_id,
            state=transfer.state,
            message=transfer.message,
            received_chunks=len(chunks),
            chunk_count=manifest.chunk_count if manifest else 0,
            received_bytes=sum(len(value) for value in chunks.values()),
            compressed_size=manifest.compressed_size if manifest else 0,
            retransmission_rounds=transfer.retries,
            artifact_path=artifact_path,
        ))


class PgmFusionEngine:
    OCCUPIED = np.uint8(2)
    FREE = np.uint8(1)
    UNKNOWN = np.uint8(0)

    def __init__(self, loader: PgmMapLoader | None = None, *, max_grid_cells: int = 20_000_000) -> None:
        self.loader = loader or PgmMapLoader()
        self.max_grid_cells = max(1, int(max_grid_cells))

    def inspect(
        self, sources: Iterable[PgmFusionSource], target_bounds: MapBounds,
        output_resolution: float | None = None,
    ) -> tuple[float, float]:
        loaded = self._load_sources(sources)
        resolution = self._resolution(loaded, output_resolution)
        outside = sum(self._outside_area(data, source.transform, target_bounds) for source, data in loaded)
        return resolution, outside

    def fuse(
        self, sources: Iterable[PgmFusionSource], target_bounds: MapBounds,
        output_pgm: str | Path, output_yaml: str | Path,
        output_resolution: float | None = None,
    ) -> PgmFusionResult:
        loaded = self._load_sources(sources)
        if len(loaded) < 2:
            raise PgmFusionError("PGM 融合至少需要两个有效图层")
        resolution = self._resolution(loaded, output_resolution)
        width = max(1, int(math.ceil(target_bounds.width / resolution)))
        height = max(1, int(math.ceil(target_bounds.height / resolution)))
        if width * height > self.max_grid_cells:
            raise PgmFusionError(f"输出栅格包含 {width * height} 个像素，超过安全上限")
        output = np.zeros((height, width), dtype=np.uint8)
        x = target_bounds.min_x + (np.arange(width, dtype=np.float64) + 0.5) * resolution
        source_cells = 0
        clipped_area = 0.0
        for source, data in loaded:
            classified = self._classify(data)
            source_cells += classified.size
            clipped_area += self._outside_area(data, source.transform, target_bounds)
            transform_yaw = math.radians(source.transform.yaw_deg)
            total_yaw = transform_yaw + data.metadata.origin_yaw
            cosine, sine = math.cos(total_yaw), math.sin(total_yaw)
            origin_target_x = (
                source.transform.x_m
                + math.cos(transform_yaw) * data.metadata.origin_x
                - math.sin(transform_yaw) * data.metadata.origin_y
            )
            origin_target_y = (
                source.transform.y_m
                + math.sin(transform_yaw) * data.metadata.origin_x
                + math.cos(transform_yaw) * data.metadata.origin_y
            )
            for row in range(height):
                world_y = target_bounds.min_y + (row + 0.5) * resolution
                dx = x - origin_target_x
                dy = world_y - origin_target_y
                local_x = cosine * dx + sine * dy
                local_y = -sine * dx + cosine * dy
                columns = np.floor(local_x / data.metadata.resolution).astype(np.int64)
                rows = np.floor(local_y / data.metadata.resolution).astype(np.int64)
                valid = (
                    (columns >= 0) & (columns < data.metadata.image_width)
                    & (rows >= 0) & (rows < data.metadata.image_height)
                )
                sampled = np.zeros(width, dtype=np.uint8)
                sampled[valid] = classified[rows[valid], columns[valid]]
                output[row] = np.maximum(output[row], sampled)
        pixels = np.full((height, width), 205, dtype=np.uint8)
        pixels[output == self.FREE] = 254
        pixels[output == self.OCCUPIED] = 0
        stored = np.flipud(pixels)
        pgm_target, yaml_target = Path(output_pgm), Path(output_yaml)
        pgm_target.parent.mkdir(parents=True, exist_ok=True)
        metadata = PgmMapMetadata(
            image_path=pgm_target.name, yaml_path=yaml_target.name,
            resolution=resolution, origin_x=target_bounds.min_x,
            origin_y=target_bounds.min_y, origin_yaw=0.0,
            image_width=width, image_height=height, negate=False,
            occupied_thresh=0.65, free_thresh=0.196,
        )
        try:
            pgm_target.write_bytes(f"P5\n{width} {height}\n255\n".encode("ascii") + stored.tobytes())
            yaml_target.write_text(
                yaml.safe_dump(PgmMapLoader.normalized_yaml(metadata), sort_keys=False),
                encoding="utf-8",
            )
        except OSError as exc:
            pgm_target.unlink(missing_ok=True)
            yaml_target.unlink(missing_ok=True)
            raise PgmFusionError(f"融合 PGM 写入失败：{exc}") from exc
        clipped_cells = int(math.ceil(clipped_area / (resolution * resolution))) if clipped_area else 0
        return PgmFusionResult(metadata, clipped_cells, clipped_area, source_cells, width * height)

    def _load_sources(self, sources: Iterable[PgmFusionSource]) -> list[tuple[PgmFusionSource, PgmMapData]]:
        loaded = []
        for source in sources:
            if not source.yaml_path:
                raise PgmFusionError(f"来源 {source.source_id} 缺少 YAML 文件")
            try:
                loaded.append((source, self.loader.load_yaml(source.yaml_path)))
            except PgmMapError as exc:
                raise PgmFusionError(f"来源 {source.source_id} 无效：{exc}") from exc
        return loaded

    @staticmethod
    def _resolution(loaded: list[tuple[PgmFusionSource, PgmMapData]], requested: float | None) -> float:
        finest = min(data.metadata.resolution for _, data in loaded)
        value = finest if requested is None else float(requested)
        if not math.isfinite(value) or value <= 0:
            raise PgmFusionError("输出分辨率必须为有限正数")
        if value + 1e-12 < finest:
            raise PgmFusionError(f"输出分辨率不能细于来源最细分辨率 {finest:g} m/px")
        return value

    def _classify(self, data: PgmMapData) -> np.ndarray:
        logical = np.flipud(data.pixels).astype(np.float64) / 255.0
        occupancy = logical if data.metadata.negate else 1.0 - logical
        result = np.zeros(logical.shape, dtype=np.uint8)
        result[occupancy <= data.metadata.free_thresh] = self.FREE
        result[occupancy >= data.metadata.occupied_thresh] = self.OCCUPIED
        return result

    @staticmethod
    def _outside_area(data: PgmMapData, transform: PgmTransform2D, bounds: MapBounds) -> float:
        width = data.metadata.width_m
        height = data.metadata.height_m
        origin_yaw = data.metadata.origin_yaw
        local = np.asarray([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float64)
        co, so = math.cos(origin_yaw), math.sin(origin_yaw)
        origin_rotation = np.asarray([[co, -so], [so, co]])
        local = local @ origin_rotation.T + [data.metadata.origin_x, data.metadata.origin_y]
        yaw = math.radians(transform.yaw_deg)
        ct, st = math.cos(yaw), math.sin(yaw)
        transformed = local @ np.asarray([[ct, -st], [st, ct]]).T + [transform.x_m, transform.y_m]
        minimum = transformed.min(axis=0)
        maximum = transformed.max(axis=0)
        bbox_area = max(0.0, maximum[0] - minimum[0]) * max(0.0, maximum[1] - minimum[1])
        overlap_x = max(0.0, min(maximum[0], bounds.max_x) - max(minimum[0], bounds.min_x))
        overlap_y = max(0.0, min(maximum[1], bounds.max_y) - max(minimum[1], bounds.min_y))
        return max(0.0, bbox_area - overlap_x * overlap_y)


def pcd_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_payload(manifest: PgmArtifactManifest) -> dict[str, object]:
    return {
        "device_id": manifest.device_id,
        "source_map_id": manifest.source_map_id,
        "session_id": manifest.session_id,
        "frame_id": manifest.frame_id,
        "pgm_format": manifest.pgm_format,
        "width": manifest.width,
        "height": manifest.height,
        "resolution": manifest.resolution,
        "origin": list(manifest.origin),
        "negate": manifest.negate,
        "occupied_thresh": manifest.occupied_thresh,
        "free_thresh": manifest.free_thresh,
        "generated_at": manifest.generated_at.isoformat(),
        "uncompressed_size": manifest.uncompressed_size,
        "compressed_size": manifest.compressed_size,
        "chunk_count": manifest.chunk_count,
        "crc32": manifest.crc32,
        "sha256": manifest.sha256,
    }
