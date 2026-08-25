from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import json
import math
import os
import queue
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlsplit

import msgpack
import numpy as np
from PySide6.QtCore import QObject, Signal

from .map_building import (
    MapBuildingEnvelope,
)
from .map_building_config import MapBuildingConfig
from .models import DeviceSnapshot, MapBounds, MapBuildingResultMetadata, MapDefinition
from .map_repository import MapRepository, MapRepositoryError
from .pgm_map import PgmMapLoader, PgmMapError
from .point_cloud import MapPointCloudLoader, PointCloudError


class RemoteMappingProtocolError(ValueError):
    pass


class ArtifactDownloadError(RuntimeError):
    pass


class ArtifactValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteReadinessCheck:
    name: str
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class RemoteMappingLogEntry:
    timestamp: datetime
    direction: str
    event: str
    summary: str
    level: str = "INFO"


@dataclass(frozen=True)
class RemoteMappingSnapshot:
    map_id: str
    device_id: str
    session_id: str
    state: str
    message: str
    started_at: datetime
    readiness_checks: tuple[RemoteReadinessCheck, ...] = ()
    sample_window_seconds: float | None = None
    frame_id: str = "lio_odom"
    capability_version: str = ""
    complete_frames: int = 0
    dropped_frames: int = 0
    received_points: int = 0
    fused_points: int = 0
    last_data_at: datetime | None = None
    artifact_bytes_received: int = 0
    artifact_bytes_total: int = 0
    error_code: str = ""
    log_entries: tuple[RemoteMappingLogEntry, ...] = ()

    @property
    def navigation_locked(self) -> bool:
        return self.state in {
            "preparing", "ready", "starting", "mapping", "stopping",
            "generating", "downloading", "validating",
        }


@dataclass(frozen=True)
class ArtifactDescriptor:
    url: str
    byte_count: int
    sha256: str
    expires_at: datetime


@dataclass(frozen=True)
class CloudFragmentDescriptor:
    fragment_id: int
    url: str
    byte_count: int
    sha256: str
    point_count: int
    frame_id: str
    source_frame_id: str
    display_from_source: dict[str, float]
    started_at_ns: int
    ended_at_ns: int
    expires_at: datetime


class PreviewAccumulator:
    """Bounded vectorized voxel preview; final map data never depends on it."""

    def __init__(self, voxel_size_m: float, maximum: int) -> None:
        self.voxel_size_m = float(voxel_size_m)
        self.maximum = int(maximum)
        self.received_points = 0
        self._points = np.empty((0, 3), dtype=np.float32)

    def add(self, points: np.ndarray) -> None:
        incoming = np.asarray(points, dtype=np.float32)
        if incoming.ndim != 2 or incoming.shape[1] != 3 or not np.isfinite(incoming).all():
            raise ValueError("PCD 分片必须包含有限 XYZ 点")
        self.received_points += len(incoming)
        combined = np.concatenate((self._points, incoming), axis=0)
        keys = np.floor(combined / self.voxel_size_m).astype(np.int64)
        _unique, indices = np.unique(keys, axis=0, return_index=True)
        combined = combined[np.sort(indices)]
        if len(combined) > self.maximum:
            selected = np.linspace(0, len(combined) - 1, self.maximum, dtype=np.int64)
            combined = combined[selected]
        self._points = np.ascontiguousarray(combined, dtype=np.float32)

    def points(self) -> np.ndarray:
        return self._points.copy()

    def bounds(self) -> MapBounds | None:
        if not len(self._points):
            return None
        minimum = self._points.min(axis=0)
        maximum = self._points.max(axis=0)
        return MapBounds(*minimum.tolist(), *maximum.tolist())


def load_preview_pcd(path: Path, descriptor: CloudFragmentDescriptor) -> np.ndarray:
    with path.open("rb") as stream:
        header: list[str] = []
        while True:
            line = stream.readline()
            if not line or len(header) > 64:
                raise ArtifactValidationError("PCD 分片头部不完整")
            text = line.decode("ascii", "strict").strip()
            header.append(text)
            if text.upper().startswith("DATA "):
                break
        fields = {parts[0].upper(): parts[1:] for parts in (
            line.split() for line in header if line and not line.startswith("#"))}
        if (fields.get("FIELDS") != ["x", "y", "z"]
                or fields.get("SIZE") != ["4", "4", "4"]
                or fields.get("TYPE") != ["F", "F", "F"]
                or fields.get("DATA", [""])[0].lower() != "binary"):
            raise ArtifactValidationError("PCD 分片格式不是二进制 XYZ float32")
        try:
            declared = int(fields["POINTS"][0])
        except (KeyError, IndexError, ValueError) as exc:
            raise ArtifactValidationError("PCD 分片点数无效") from exc
        raw = stream.read()
    if declared != descriptor.point_count or len(raw) != declared * 12:
        raise ArtifactValidationError("PCD 分片数据长度与声明不一致")
    points = np.frombuffer(raw, dtype="<f4").reshape((-1, 3)).copy()
    if not np.isfinite(points).all():
        raise ArtifactValidationError("PCD 分片包含非有限坐标")
    return points


@dataclass(frozen=True)
class ValidatedArtifact:
    root: Path
    pcd_path: Path
    pgm_path: Path
    yaml_path: Path
    frame_id: str
    generated_at: datetime
    file_sha256: dict[str, str] = field(default_factory=dict)


class MapBuildingV2Protocol:
    MESSAGE_TYPES = {
        "prepare_mapping", "prepare_result", "start_mapping", "stop_mapping", "abort_mapping",
        "cloud_fragment_ack",
        "command_ack", "session_heartbeat", "session_status", "cloud_fragment_ready",
        "artifact_status",
    }

    def __init__(self, config: MapBuildingConfig) -> None:
        self.config = config

    def encode(self, envelope: MapBuildingEnvelope) -> bytes:
        self._validate(envelope)
        data = msgpack.packb({
            "schema_version": 2,
            "protocol_id": self.config.protocol_v2_id,
            "map_id": envelope.map_id,
            "device_id": envelope.device_id,
            "session_id": envelope.session_id,
            "message_type": envelope.message_type,
            "sequence": envelope.sequence,
            "sent_at_ns": envelope.sent_at_ns,
            "payload": envelope.payload,
        }, use_bin_type=True)
        if len(data) > self.config.max_datagram_bytes:
            raise RemoteMappingProtocolError("v2 建图数据报超过大小限制")
        return data

    def decode(self, datagram: bytes) -> MapBuildingEnvelope:
        if len(datagram) > self.config.max_datagram_bytes:
            raise RemoteMappingProtocolError("v2 建图数据报超过大小限制")
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except Exception as exc:
            raise RemoteMappingProtocolError(f"v2 MessagePack 解码失败：{exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 2:
            raise RemoteMappingProtocolError("不支持的 v2 schema_version")
        if raw.get("protocol_id") != self.config.protocol_v2_id:
            raise RemoteMappingProtocolError("v2 protocol_id 不匹配")
        try:
            envelope = MapBuildingEnvelope(
                raw["map_id"], raw["device_id"], raw["session_id"], raw["message_type"],
                raw["sequence"], raw["sent_at_ns"], raw.get("payload", {}),
            )
        except KeyError as exc:
            raise RemoteMappingProtocolError(f"v2 信封缺少字段：{exc}") from exc
        self._validate(envelope)
        return envelope

    def _validate(self, envelope: MapBuildingEnvelope) -> None:
        for name, value in (("map_id", envelope.map_id), ("device_id", envelope.device_id),
                            ("session_id", envelope.session_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise RemoteMappingProtocolError(f"{name} 无效")
        if envelope.message_type not in self.MESSAGE_TYPES:
            raise RemoteMappingProtocolError("v2 message_type 无效")
        if not isinstance(envelope.sequence, int) or isinstance(envelope.sequence, bool) or envelope.sequence < 0:
            raise RemoteMappingProtocolError("v2 sequence 无效")
        if not isinstance(envelope.sent_at_ns, int) or envelope.sent_at_ns < 0:
            raise RemoteMappingProtocolError("v2 sent_at_ns 无效")
        if not isinstance(envelope.payload, dict):
            raise RemoteMappingProtocolError("v2 payload 必须是对象")
        getattr(self, f"_validate_{envelope.message_type}")(envelope.payload)

    def _validate_prepare_mapping(self, payload: dict[str, Any]) -> None:
        self._string(payload, "request_id")
        self._string(payload, "return_host")
        self._integer(payload, "return_port", 1, 65535)
        required = payload.get("required_inputs")
        if not isinstance(required, list) or not required or any(
            not isinstance(item, str) or not item.strip() for item in required
        ):
            raise RemoteMappingProtocolError("required_inputs 无效")
        if "restart_active" in payload and not isinstance(payload["restart_active"], bool):
            raise RemoteMappingProtocolError("restart_active 无效")
        if payload.get("preview_transport") != self.config.preview_transport:
            raise RemoteMappingProtocolError("prepare_mapping.preview_transport 无效")
        self._number(payload, "fragment_interval_seconds", 0.1)

    def _validate_prepare_result(self, payload: dict[str, Any]) -> None:
        self._string(payload, "request_id")
        if not isinstance(payload.get("accepted"), bool):
            raise RemoteMappingProtocolError("prepare_result.accepted 无效")
        checks = payload.get("checks")
        if not isinstance(checks, list) or not checks or len(checks) > 64:
            raise RemoteMappingProtocolError("prepare_result.checks 无效")
        for item in checks:
            if not isinstance(item, dict) or not isinstance(item.get("available"), bool):
                raise RemoteMappingProtocolError("准备检查项无效")
            self._string(item, "name")
            if item.get("reason") is not None and not isinstance(item["reason"], str):
                raise RemoteMappingProtocolError("准备检查原因无效")
        if payload["accepted"] != all(bool(item["available"]) for item in checks):
            raise RemoteMappingProtocolError("准备总体结果与检查项不一致")
        self._number(payload, "sample_window_seconds", 0.001)
        self._string(payload, "frame_id")
        self._string(payload, "capability_version")
        if payload.get("preview_transport") != self.config.preview_transport:
            raise RemoteMappingProtocolError("prepare_result.preview_transport 无效")
        self._number(payload, "fragment_interval_seconds", 0.1)
        if "restarted" in payload and not isinstance(payload["restarted"], bool):
            raise RemoteMappingProtocolError("prepare_result.restarted 无效")
        for key in ("previous_state", "active_session_id"):
            if key in payload:
                self._string(payload, key)

    def _validate_start_mapping(self, payload: dict[str, Any]) -> None:
        self._string(payload, "request_id")
        if payload.get("coordinate_contract") != "sensor+map_body+body_sensor":
            raise RemoteMappingProtocolError("坐标契约无效")
        if payload.get("preview_transport") != self.config.preview_transport:
            raise RemoteMappingProtocolError("实时预览传输方式无效")
        self._number(payload, "fragment_interval_seconds", 0.1)

    def _validate_stop_mapping(self, payload: dict[str, Any]) -> None:
        self._string(payload, "request_id")
        self._string(payload, "reason")

    def _validate_abort_mapping(self, payload: dict[str, Any]) -> None:
        self._validate_stop_mapping(payload)

    def _validate_cloud_fragment_ack(self, payload: dict[str, Any]) -> None:
        self._string(payload, "request_id")
        self._integer(payload, "fragment_id", 0)

    def _validate_command_ack(self, payload: dict[str, Any]) -> None:
        self._string(payload, "request_id")
        if payload.get("command") not in {"start_mapping", "stop_mapping", "abort_mapping"}:
            raise RemoteMappingProtocolError("v2 ACK command 无效")
        if not isinstance(payload.get("accepted"), bool):
            raise RemoteMappingProtocolError("v2 ACK accepted 无效")
        if payload.get("reason") is not None and not isinstance(payload["reason"], str):
            raise RemoteMappingProtocolError("v2 ACK reason 无效")

    def _validate_session_heartbeat(self, payload: dict[str, Any]) -> None:
        if payload.get("state") not in {"starting", "mapping", "stopping", "generating", "error"}:
            raise RemoteMappingProtocolError("v2 心跳状态无效")

    def _validate_session_status(self, payload: dict[str, Any]) -> None:
        self._validate_session_heartbeat(payload)
        if payload.get("reason") is not None and not isinstance(payload["reason"], str):
            raise RemoteMappingProtocolError("v2 会话原因无效")

    def _validate_cloud_fragment_ready(self, payload: dict[str, Any]) -> None:
        self._integer(payload, "fragment_id", 0)
        self._string(payload, "url", maximum=4096)
        self._integer(payload, "byte_count", 1, self.config.max_preview_fragment_bytes)
        digest = self._string(payload, "sha256")
        if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            raise RemoteMappingProtocolError("PCD 分片 SHA-256 无效")
        self._integer(payload, "point_count", 1, self.config.max_frame_points)
        self._string(payload, "frame_id")
        self._string(payload, "source_frame_id")
        self._transform(payload.get("display_from_source"), "display_from_source")
        self._string(payload, "expires_at")
        started = self._integer(payload, "started_at_ns", 0)
        ended = self._integer(payload, "ended_at_ns", started)
        if ended < started:
            raise RemoteMappingProtocolError("PCD 分片时间范围无效")

    def _validate_artifact_status(self, payload: dict[str, Any]) -> None:
        if payload.get("state") not in {"generating", "ready", "error"}:
            raise RemoteMappingProtocolError("成果状态无效")
        if payload["state"] == "ready":
            self._string(payload, "url", maximum=4096)
            self._integer(payload, "byte_count", 1, self.config.max_artifact_bytes)
            digest = self._string(payload, "sha256")
            if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                raise RemoteMappingProtocolError("成果 SHA-256 无效")
            self._string(payload, "expires_at")
        if payload.get("reason") is not None and not isinstance(payload["reason"], str):
            raise RemoteMappingProtocolError("成果错误原因无效")

    @staticmethod
    def _string(payload: dict[str, Any], key: str, maximum: int = 256) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise RemoteMappingProtocolError(f"{key} 无效")
        return value

    @staticmethod
    def _integer(payload: dict[str, Any], key: str, minimum: int,
                 maximum: int | None = None) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise RemoteMappingProtocolError(f"{key} 无效")
        if maximum is not None and value > maximum:
            raise RemoteMappingProtocolError(f"{key} 超出范围")
        return value

    @staticmethod
    def _number(payload: dict[str, Any], key: str, minimum: float) -> float:
        value = payload.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RemoteMappingProtocolError(f"{key} 无效")
        number = float(value)
        if not math.isfinite(number) or number < minimum:
            raise RemoteMappingProtocolError(f"{key} 超出范围")
        return number

    @staticmethod
    def _transform(value: Any, name: str) -> None:
        if not isinstance(value, dict):
            raise RemoteMappingProtocolError(f"{name} 无效")
        keys = ("x", "y", "z", "qx", "qy", "qz", "qw")
        if any(
            not isinstance(value.get(key), (int, float))
            or isinstance(value.get(key), bool)
            or not math.isfinite(float(value[key]))
            for key in keys
        ):
            raise RemoteMappingProtocolError(f"{name} 字段无效")
        norm = math.sqrt(sum(float(value[key]) ** 2 for key in ("qx", "qy", "qz", "qw")))
        if norm < 1e-6:
            raise RemoteMappingProtocolError(f"{name} 四元数无效")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ArtifactDownloadError("HTTP 成果下载禁止重定向")


class ArtifactDownloader:
    def __init__(self, config: MapBuildingConfig,
                 opener: Any | None = None) -> None:
        self.config = config
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def validate_descriptor(self, descriptor: ArtifactDescriptor, device_ip: str) -> None:
        parsed = urlsplit(descriptor.url)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.path or not parsed.query:
            raise ArtifactDownloadError("成果 URL 必须是完整 HTTP 地址")
        try:
            expected = ipaddress.ip_address(device_ip.strip())
            actual = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ArtifactDownloadError("设备 IP 或成果 URL 主机不是有效 IP") from exc
        if expected != actual:
            raise ArtifactDownloadError("成果 URL 主机与建图设备 IP 不一致")
        if descriptor.expires_at <= datetime.now(timezone.utc):
            raise ArtifactDownloadError("成果 URL 已过期")
        if not 0 < descriptor.byte_count <= self.config.max_artifact_bytes:
            raise ArtifactDownloadError("成果文件大小超出限制")
        if len(descriptor.sha256) != 64 or any(
            char not in "0123456789abcdefABCDEF" for char in descriptor.sha256
        ):
            raise ArtifactDownloadError("成果 SHA-256 无效")

    def download(self, descriptor: ArtifactDescriptor, device_ip: str, target: Path,
                 progress: Callable[[int, int], None] | None = None) -> Path:
        self.validate_descriptor(descriptor, device_ip)
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(self.config.http_download_attempts):
            existing = target.stat().st_size if target.is_file() else 0
            if existing > descriptor.byte_count:
                target.unlink(missing_ok=True)
                existing = 0
            headers = {"Accept-Encoding": "identity"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            request = urllib.request.Request(descriptor.url, headers=headers, method="GET")
            try:
                response = self.opener.open(request, timeout=self.config.http_connect_timeout_seconds)
                self._set_read_timeout(response)
                status = int(getattr(response, "status", response.getcode()))
                if existing and status != 206:
                    response.close()
                    target.unlink(missing_ok=True)
                    existing = 0
                    request = urllib.request.Request(
                        descriptor.url, headers={"Accept-Encoding": "identity"}, method="GET"
                    )
                    response = self.opener.open(request, timeout=self.config.http_connect_timeout_seconds)
                    self._set_read_timeout(response)
                    status = int(getattr(response, "status", response.getcode()))
                if status not in {200, 206}:
                    raise ArtifactDownloadError(f"HTTP 成果下载返回 {status}")
                mode = "ab" if existing and status == 206 else "wb"
                received = existing if mode == "ab" else 0
                with response, target.open(mode) as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > descriptor.byte_count or received > self.config.max_artifact_bytes:
                            raise ArtifactDownloadError("成果下载超过声明大小")
                        handle.write(chunk)
                        if progress:
                            progress(received, descriptor.byte_count)
                    handle.flush()
                    os.fsync(handle.fileno())
                if received != descriptor.byte_count:
                    raise ArtifactDownloadError("成果下载字节数与声明不一致")
                if _sha256(target) != descriptor.sha256.lower():
                    raise ArtifactDownloadError("成果 ZIP SHA-256 校验失败")
                return target
            except (OSError, urllib.error.URLError, ArtifactDownloadError) as exc:
                if attempt + 1 >= self.config.http_download_attempts:
                    raise ArtifactDownloadError(f"成果下载失败：{exc}") from exc
                time.sleep(min(0.25 * (attempt + 1), 1.0))
        raise ArtifactDownloadError("成果下载失败")

    def _set_read_timeout(self, response: Any) -> None:
        try:
            response.fp.raw._sock.settimeout(self.config.http_read_timeout_seconds)
        except (AttributeError, OSError):
            pass


class ArtifactPackageValidator:
    REQUIRED_ROLES = {"pcd", "pgm", "yaml"}

    def __init__(self, config: MapBuildingConfig,
                 cloud_loader: MapPointCloudLoader | None = None,
                 pgm_loader: PgmMapLoader | None = None) -> None:
        self.config = config
        self.cloud_loader = cloud_loader or MapPointCloudLoader()
        self.pgm_loader = pgm_loader or PgmMapLoader()

    def validate(self, archive: Path, output_root: Path, *, map_id: str,
                 device_id: str, session_id: str) -> ValidatedArtifact:
        shutil.rmtree(output_root, ignore_errors=True)
        output_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                infos = bundle.infolist()
                names = [item.filename for item in infos if not item.is_dir()]
                if len(names) != len(set(names)) or "manifest.json" not in names:
                    raise ArtifactValidationError("成果 ZIP 缺少清单或存在重复文件")
                if sum(item.file_size for item in infos) > self.config.max_artifact_bytes:
                    raise ArtifactValidationError("成果 ZIP 解压大小超出限制")
                for info in infos:
                    self._safe_member(info)
                    if (info.file_size > 10 * 1024 * 1024 and info.compress_size > 0
                            and info.file_size / info.compress_size > 200):
                        raise ArtifactValidationError("成果 ZIP 压缩比异常")
                try:
                    manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
                except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ArtifactValidationError(f"成果清单无效：{exc}") from exc
                files, frame_id, generated_at = self._validate_manifest(
                    manifest, map_id, device_id, session_id
                )
                declared = {"manifest.json", *(item["path"] for item in files.values())}
                if set(names) != declared:
                    raise ArtifactValidationError("成果 ZIP 包含未声明或缺失的文件")
                hashes: dict[str, str] = {}
                extracted: dict[str, Path] = {}
                for role, metadata in files.items():
                    info = bundle.getinfo(metadata["path"])
                    if info.file_size != metadata["byte_count"]:
                        raise ArtifactValidationError(f"{role} 文件大小与清单不一致")
                    target = output_root / role / Path(metadata["path"]).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with bundle.open(info) as source, target.open("wb") as destination:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                            destination.write(chunk)
                    actual = digest.hexdigest()
                    if actual != metadata["sha256"]:
                        raise ArtifactValidationError(f"{role} SHA-256 校验失败")
                    hashes[role] = actual
                    extracted[role] = target
            yaml_payload = extracted["yaml"].read_text(encoding="utf-8")
            import yaml
            parsed_yaml = yaml.safe_load(yaml_payload)
            if not isinstance(parsed_yaml, dict):
                raise ArtifactValidationError("map.yaml 根节点无效")
            parsed_yaml["image"] = os.path.relpath(extracted["pgm"], extracted["yaml"].parent)
            extracted["yaml"].write_text(
                yaml.safe_dump(parsed_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            self.cloud_loader.load(extracted["pcd"])
            self.pgm_loader.load_yaml(extracted["yaml"])
        except ArtifactValidationError:
            raise
        except (OSError, zipfile.BadZipFile, PointCloudError, PgmMapError, ValueError) as exc:
            raise ArtifactValidationError(f"成果包校验失败：{exc}") from exc
        return ValidatedArtifact(
            output_root, extracted["pcd"], extracted["pgm"], extracted["yaml"],
            frame_id, generated_at, hashes,
        )

    @staticmethod
    def _safe_member(info: zipfile.ZipInfo) -> None:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            raise ArtifactValidationError("成果 ZIP 包含非安全路径")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and (unix_mode & 0o170000) == 0o120000:
            raise ArtifactValidationError("成果 ZIP 不允许符号链接")

    def _validate_manifest(self, manifest: Any, map_id: str, device_id: str,
                           session_id: str) -> tuple[dict[str, dict[str, Any]], str, datetime]:
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ArtifactValidationError("成果清单 schema_version 无效")
        for key, expected in (("map_id", map_id), ("device_id", device_id),
                              ("session_id", session_id)):
            if manifest.get(key) != expected:
                raise ArtifactValidationError(f"成果清单 {key} 不匹配")
        frame_id = manifest.get("frame_id")
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ArtifactValidationError("成果清单 frame_id 无效")
        try:
            generated_at = datetime.fromisoformat(str(manifest["generated_at"]).replace("Z", "+00:00"))
            if generated_at.tzinfo is None:
                raise ValueError
        except (KeyError, ValueError) as exc:
            raise ArtifactValidationError("成果清单 generated_at 无效") from exc
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != self.REQUIRED_ROLES:
            raise ArtifactValidationError("成果清单必须且只能声明 PCD、PGM 和 YAML")
        normalized: dict[str, dict[str, Any]] = {}
        for role, item in files.items():
            if not isinstance(item, dict):
                raise ArtifactValidationError(f"{role} 清单项无效")
            path = item.get("path")
            size = item.get("byte_count")
            digest = item.get("sha256")
            if (not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
                    or ".." in PurePosixPath(path).parts or "\\" in path):
                raise ArtifactValidationError(f"{role} 路径无效")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise ArtifactValidationError(f"{role} 大小无效")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest.lower())):
                raise ArtifactValidationError(f"{role} SHA-256 无效")
            normalized[role] = {"path": path, "byte_count": size, "sha256": digest.lower()}
        return normalized, frame_id, generated_at.astimezone(timezone.utc)


def descriptor_from_payload(payload: dict[str, Any]) -> ArtifactDescriptor:
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            raise ValueError
        return ArtifactDescriptor(
            str(payload["url"]), int(payload["byte_count"]), str(payload["sha256"]).lower(),
            expires_at.astimezone(timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RemoteMappingProtocolError(f"成果描述无效：{exc}") from exc


def cloud_fragment_from_payload(payload: dict[str, Any]) -> CloudFragmentDescriptor:
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            raise ValueError
        return CloudFragmentDescriptor(
            int(payload["fragment_id"]), str(payload["url"]), int(payload["byte_count"]),
            str(payload["sha256"]).lower(), int(payload["point_count"]),
            str(payload["frame_id"]), str(payload["source_frame_id"]),
            {key: float(payload["display_from_source"][key])
             for key in ("x", "y", "z", "qx", "qy", "qz", "qw")},
            int(payload["started_at_ns"]),
            int(payload["ended_at_ns"]), expires_at.astimezone(timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RemoteMappingProtocolError(f"PCD 分片描述无效：{exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class _RemoteSession:
    definition: MapDefinition
    device: DeviceSnapshot
    session_id: str
    request_id: str
    accumulator: PreviewAccumulator
    started_at: datetime
    started_monotonic: float
    state: str = "preparing"
    message: str = "正在检查端侧建图条件"
    command: str = "prepare_mapping"
    command_attempts: int = 0
    last_command_at: float = 0.0
    last_sequence: int = -1
    checks: tuple[RemoteReadinessCheck, ...] = ()
    sample_window_seconds: float | None = None
    frame_id: str = "map"
    capability_version: str = ""
    complete_frames: int = 0
    dropped_frames: int = 0
    last_complete_frame_at: float | None = None
    last_heartbeat_at: float | None = None
    last_data_at: datetime | None = None
    artifact_bytes_received: int = 0
    artifact_bytes_total: int = 0
    error_code: str = ""
    generation_started_at: float | None = None
    command_started_at: float = 0.0
    command_deadline_at: float = 0.0
    restart_active: bool = False
    log_entries: list[RemoteMappingLogEntry] = field(default_factory=list)
    queued_fragments: set[int] = field(default_factory=set)
    processed_fragments: dict[int, None] = field(default_factory=dict)
    outbound_sequence: int = 1000


class RemoteMappingCoordinator(QObject):
    updated = Signal(object)
    preview_updated = Signal(str, object, object)
    completed = Signal(object)
    failed = Signal(str)
    navigation_locked = Signal(bool)

    def __init__(self, config: MapBuildingConfig, repository: MapRepository,
                 sender: Callable[[bytes, str], None], *,
                 clock: Callable[[], float] = time.monotonic,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.repository = repository
        self.sender = sender
        self.clock = clock
        self.protocol = MapBuildingV2Protocol(config)
        self.downloader = ArtifactDownloader(config)
        self.validator = ArtifactPackageValidator(config)
        self.session: _RemoteSession | None = None
        self._artifact_thread: Any | None = None
        self._lock = threading.RLock()
        self._preview_queue: queue.Queue[tuple[_RemoteSession, CloudFragmentDescriptor]] = queue.Queue(
            maxsize=config.max_pending_preview_fragments)
        self._preview_stop = threading.Event()
        self._preview_root = Path(tempfile.mkdtemp(prefix="ccs-map-preview-"))
        self._preview_thread = threading.Thread(
            target=self._preview_loop, name="ccs-map-preview", daemon=True)
        self._preview_thread.start()

    @property
    def snapshot(self) -> RemoteMappingSnapshot | None:
        with self._lock:
            return self._snapshot(self.session) if self.session else None

    @property
    def active(self) -> bool:
        snapshot = self.snapshot
        return bool(snapshot and snapshot.navigation_locked)

    def prepare(self, definition: MapDefinition, device: DeviceSnapshot,
                return_host: str, return_port: int) -> str:
        if not device.ip_address:
            raise ValueError("建图设备必须配置 IP 地址")
        with self._lock:
            if self.active:
                raise RuntimeError("单机遥控建图任务正在运行")
            now = datetime.now(timezone.utc)
            session_id = os.urandom(16).hex()
            session = _RemoteSession(
                definition, device, session_id, os.urandom(16).hex(),
                PreviewAccumulator(self.config.voxel_size_m, self.config.max_preview_points),
                now, self.clock(), frame_id=self.config.remote_mapping_frame,
            )
            self.session = session
            self._start_command(
                session, "prepare_mapping", self.config.prepare_command_timeout_seconds
            )
            self._log(session, "LOCAL", "session", "创建遥控建图会话")
            self._send_command(session, return_host=return_host, return_port=return_port, force=True)
            self._emit(session)
            return session_id

    def retry_prepare(self, return_host: str, return_port: int) -> None:
        with self._lock:
            session = self._require({"failed", "ready"})
            session.state = "preparing"
            session.message = "正在重新检查端侧建图条件"
            session.error_code = ""
            session.checks = ()
            session.restart_active = True
            self._start_command(
                session, "prepare_mapping", self.config.recovery_command_timeout_seconds
            )
            self._log(session, "LOCAL", "recovery", "请求清理端侧活动会话并重新协商", "WARN")
            self._send_command(session, return_host=return_host, return_port=return_port, force=True)
            self._emit(session)

    def begin(self) -> None:
        with self._lock:
            session = self._require({"ready"})
            session.state = "starting"
            session.message = "正在下发开始建图指令"
            session.restart_active = False
            self._start_command(
                session, "start_mapping", self.config.start_command_timeout_seconds
            )
            self._send_command(session, force=True)
            self._emit(session)

    def stop_mapping(self, reason: str = "用户结束建图") -> None:
        with self._lock:
            session = self._require({"mapping", "warning"})
            session.state = "stopping"
            session.message = "正在通知端侧结束建图"
            self._start_command(
                session, "stop_mapping", self.config.prepare_command_timeout_seconds
            )
            self._send_command(session, reason=reason, force=True)
            self._emit(session)

    def abort_mapping(self, reason: str = "用户强制结束建图") -> None:
        with self._lock:
            session = self._require({"ready", "starting", "mapping", "warning", "failed"})
            session.state = "aborting"
            session.message = "正在强制结束端侧建图会话"
            self._start_command(
                session, "abort_mapping", self.config.recovery_command_timeout_seconds
            )
            self._send_command(session, reason=reason, force=True)
            self._emit(session)

    def cancel(self, reason: str = "用户取消建图任务") -> None:
        with self._lock:
            session = self.session
            if session is None:
                return
            if session.state in {"ready", "starting", "mapping", "warning", "failed"}:
                try:
                    self._send_abort_once(session, reason)
                except OSError:
                    pass
            session.state = "cancelled"
            session.message = reason
            self._emit(session)
            self.session = None

    def shutdown(self) -> None:
        self.cancel("应用退出")
        self._preview_stop.set()
        self._preview_thread.join(timeout=2.0)
        shutil.rmtree(self._preview_root, ignore_errors=True)
        thread = self._artifact_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def handle(self, envelope: MapBuildingEnvelope, peer_ip: str) -> bool:
        with self._lock:
            session = self.session
            if session is None:
                return False
            if (envelope.map_id != session.definition.map_id
                    or envelope.device_id.casefold() != session.device.device_id.casefold()
                    or envelope.session_id != session.session_id):
                raise RemoteMappingProtocolError("v2 数据报设备或会话标识不一致")
            if peer_ip != session.device.ip_address:
                raise RemoteMappingProtocolError("v2 数据报来源 IP 与设备配置不一致")
            if envelope.sequence <= session.last_sequence:
                return True
            session.last_sequence = envelope.sequence
            payload = envelope.payload
            session.last_heartbeat_at = self.clock()
            self._log(
                session, "RX", envelope.message_type,
                self._response_summary(envelope.message_type, payload),
                "ERROR" if payload.get("state") == "error" or payload.get("accepted") is False else "INFO",
            )
            if envelope.message_type == "prepare_result":
                self._prepare_result(session, payload)
            elif envelope.message_type == "command_ack":
                self._ack(session, payload)
            elif envelope.message_type == "session_status":
                if payload["state"] == "error":
                    self._fail(session, str(payload.get("reason") or "端侧建图错误"), "EDGE_ERROR")
                elif payload["state"] == "starting" and session.state == "starting":
                    session.message = str(payload.get("reason") or "端侧正在启动 FAST_LIO")
                    self._emit(session)
            elif envelope.message_type == "artifact_status":
                self._artifact_status(session, payload)
            elif envelope.message_type == "cloud_fragment_ready":
                self._cloud_fragment(session, payload)
            return True

    def tick(self, return_host: str, return_port: int) -> None:
        with self._lock:
            session = self.session
            if session is None:
                return
            now = self.clock()
            if session.state in {"preparing", "starting", "stopping", "aborting"}:
                if (session.command_attempts < self.config.command_max_attempts
                        and now - session.last_command_at >= self.config.command_retry_seconds):
                    self._send_command(
                        session, return_host=return_host, return_port=return_port
                    )
                if now >= session.command_deadline_at:
                    self._fail(
                        session, f"端侧未响应 {session.command} 指令", "COMMAND_TIMEOUT"
                    )
                return
            if session.state in {"mapping", "warning"}:
                silence = now - (session.last_complete_frame_at or session.started_monotonic)
                heartbeat_silence = now - (session.last_heartbeat_at or session.started_monotonic)
                if (silence >= self.config.error_timeout_seconds
                        and heartbeat_silence >= self.config.heartbeat_timeout_seconds):
                    self._fail(
                        session,
                        f"超过 {self.config.error_timeout_seconds:g} 秒无完整点云且端侧心跳中断",
                        "CLOUD_TIMEOUT",
                    )
                elif silence >= self.config.warning_timeout_seconds and session.state == "mapping":
                    session.state = "warning"
                    session.message = "完整点云暂时中断，端侧会话仍在线"
                    self._log(session, "LOCAL", "CLOUD_WARNING", session.message, "WARN")
                    self._emit(session)
            if (session.state == "generating" and session.generation_started_at is not None
                    and now - session.generation_started_at
                    >= self.config.artifact_generation_timeout_seconds):
                self._fail(session, "端侧成果生成超时", "ARTIFACT_TIMEOUT")

    def _prepare_result(self, session: _RemoteSession, payload: dict[str, Any]) -> None:
        if session.state != "preparing" or payload["request_id"] != session.request_id:
            return
        session.checks = tuple(
            RemoteReadinessCheck(
                str(item["name"]), bool(item["available"]), str(item.get("reason") or "")
            ) for item in payload["checks"]
        )
        session.sample_window_seconds = float(payload["sample_window_seconds"])
        session.frame_id = str(payload["frame_id"])
        session.capability_version = str(payload["capability_version"])
        if payload.get("restarted"):
            previous = str(payload.get("previous_state") or "unknown")
            active_id = str(payload.get("active_session_id") or session.session_id)
            self._log(
                session, "RX", "recovery",
                f"端侧已清理活动会话 {active_id[:8]}（原状态 {previous}）",
            )
        if session.frame_id != self.config.remote_mapping_frame:
            self._fail(
                session,
                f"端侧实时建图坐标系 {session.frame_id} 与要求的"
                f" {self.config.remote_mapping_frame} 不一致",
                "FRAME_MISMATCH",
            )
        elif payload["accepted"]:
            session.restart_active = False
            session.state = "ready"
            session.message = "端侧建图条件已就绪"
            self._emit(session)
        else:
            self._fail(
                session, str(payload.get("reason") or "端侧建图条件不可用"),
                str(payload.get("error_code") or "READINESS_REJECTED"),
            )

    def _ack(self, session: _RemoteSession, payload: dict[str, Any]) -> None:
        if payload["request_id"] != session.request_id or payload["command"] != session.command:
            return
        if not payload["accepted"]:
            self._fail(
                session, str(payload.get("reason") or "端侧拒绝建图指令"),
                "COMMAND_REJECTED",
            )
        elif session.command == "start_mapping" and session.state == "starting":
            session.state = "mapping"
            session.message = "遥控建图中"
            session.last_complete_frame_at = self.clock()
            session.last_heartbeat_at = session.last_complete_frame_at
            self._log(session, "LOCAL", "state", "开始接收实时建图点云")
            self._emit(session)
        elif session.command == "stop_mapping" and session.state == "stopping":
            session.state = "generating"
            session.message = "端侧正在生成 PCD 和 PGM 成果"
            session.generation_started_at = self.clock()
            self._emit(session)
        elif session.command == "abort_mapping" and session.state == "aborting":
            session.state = "aborted"
            session.message = "端侧建图会话已强制结束，可重新开始建图"
            self._log(session, "LOCAL", "aborted", session.message, "WARN")
            self._emit(session)
            self.session = None

    def _cloud_fragment(self, session: _RemoteSession, payload: dict[str, Any]) -> None:
        if session.state not in {"mapping", "warning"}:
            return
        descriptor = cloud_fragment_from_payload(payload)
        expected_mapping_frame = self.config.mapping_frame_for(session.device.device_id)
        expected_source_frame = self.config.preview_source_frame_for(session.device.device_id)
        if descriptor.frame_id != expected_mapping_frame:
            self._fail(session, "PCD 分片坐标系不匹配", "FRAME_MISMATCH")
            return
        if descriptor.source_frame_id != expected_source_frame:
            self._fail(session, "PCD 分片源坐标系不匹配", "FRAME_MISMATCH")
            return
        if descriptor.fragment_id in session.processed_fragments:
            self._send_fragment_ack(session, descriptor.fragment_id)
            return
        if descriptor.fragment_id in session.queued_fragments:
            return
        try:
            self.downloader.validate_descriptor(
                ArtifactDescriptor(descriptor.url, descriptor.byte_count,
                                   descriptor.sha256, descriptor.expires_at),
                session.device.ip_address)
            self._preview_queue.put_nowait((session, descriptor))
            session.queued_fragments.add(descriptor.fragment_id)
        except queue.Full:
            self._log(session, "LOCAL", "preview_backpressure",
                      "PCD 分片下载队列已满，等待端侧重发", "WARN")

    def _preview_loop(self) -> None:
        while not self._preview_stop.is_set() or not self._preview_queue.empty():
            try:
                session, descriptor = self._preview_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            target = self._preview_root / session.session_id / f"{descriptor.fragment_id:08d}.pcd"
            try:
                self.downloader.download(
                    ArtifactDescriptor(descriptor.url, descriptor.byte_count,
                                       descriptor.sha256, descriptor.expires_at),
                    session.device.ip_address, target)
                points = load_preview_pcd(target, descriptor)
                with self._lock:
                    session.queued_fragments.discard(descriptor.fragment_id)
                    if self.session is not session or session.state not in {"mapping", "warning"}:
                        continue
                    session.accumulator.add(points)
                    self._remember_processed_fragment(session, descriptor.fragment_id)
                    session.complete_frames += 1
                    session.last_complete_frame_at = self.clock()
                    session.last_data_at = datetime.now(timezone.utc)
                    session.state = "mapping"
                    session.message = "遥控建图中"
                    preview = session.accumulator.points()
                    bounds = session.accumulator.bounds()
                    self._log(
                        session, "RX", "pcd_fragment",
                        f"分片 {descriptor.fragment_id}，{len(points):,} 点，"
                        f"预览保留 {len(preview):,} 点，"
                        f"{descriptor.source_frame_id}->{descriptor.frame_id} "
                        f"xyz=({descriptor.display_from_source['x']:.3f},"
                        f"{descriptor.display_from_source['y']:.3f},"
                        f"{descriptor.display_from_source['z']:.3f})")
                    self._send_fragment_ack(session, descriptor.fragment_id)
                    self.preview_updated.emit(session.session_id, preview, bounds)
                    self._emit(session)
            except (ArtifactDownloadError, ArtifactValidationError, OSError, ValueError) as exc:
                with self._lock:
                    session.queued_fragments.discard(descriptor.fragment_id)
                    if self.session is session:
                        session.dropped_frames += 1
                        self._log(session, "LOCAL", "fragment_failed", str(exc), "WARN")
            finally:
                target.unlink(missing_ok=True)
                self._preview_queue.task_done()

    def _remember_processed_fragment(
        self, session: _RemoteSession, fragment_id: int
    ) -> None:
        session.processed_fragments[fragment_id] = None
        maximum = max(64, self.config.max_pending_preview_fragments * 16)
        while len(session.processed_fragments) > maximum:
            oldest = next(iter(session.processed_fragments))
            session.processed_fragments.pop(oldest, None)

    def _artifact_status(self, session: _RemoteSession, payload: dict[str, Any]) -> None:
        if payload["state"] == "error":
            self._fail(
                session, str(payload.get("reason") or "端侧成果生成失败"),
                "ARTIFACT_ERROR",
            )
        elif payload["state"] == "generating" and session.state in {"stopping", "generating"}:
            session.state = "generating"
            session.message = str(payload.get("message") or "端侧正在生成建图成果")
            session.generation_started_at = session.generation_started_at or self.clock()
            self._emit(session)
        elif payload["state"] == "ready" and session.state == "generating":
            descriptor = descriptor_from_payload(payload)
            session.state = "downloading"
            session.message = "正在下载端侧建图成果"
            session.artifact_bytes_total = descriptor.byte_count
            self._emit(session)
            self._download(session, descriptor)

    def _download(self, session: _RemoteSession, descriptor: ArtifactDescriptor) -> None:
        import threading
        if self._artifact_thread and self._artifact_thread.is_alive():
            self._fail(session, "已有成果下载任务在运行", "DOWNLOAD_BUSY")
            return

        def run() -> None:
            try:
                root = self.repository.mapping_session_directory(
                    session.definition.map_id, session.session_id, create=True
                )

                def progress(received: int, total: int) -> None:
                    with self._lock:
                        if self.session is session:
                            session.artifact_bytes_received = received
                            session.artifact_bytes_total = total
                            self._emit(session)

                archive = self.downloader.download(
                    descriptor, session.device.ip_address, root / "artifact.zip.part", progress
                )
                with self._lock:
                    if self.session is not session:
                        return
                    session.state = "validating"
                    session.message = "正在校验 PCD、PGM 和 YAML"
                    self._log(session, "LOCAL", "artifact", "成果下载完成，开始完整性校验")
                    self._emit(session)
                artifact = self.validator.validate(
                    archive, root / "validated", map_id=session.definition.map_id,
                    device_id=session.device.device_id, session_id=session.session_id,
                )
                expected_artifact_frame = self.config.artifact_frame_for(
                    session.device.device_id)
                if artifact.frame_id != expected_artifact_frame:
                    raise ArtifactValidationError(
                        f"端侧成果坐标系 {artifact.frame_id} 与要求的源成果坐标系 "
                        f"{expected_artifact_frame} 不一致"
                    )
                # The source artifact remains in the FAST_LIO frame. Once it is
                # accepted, its local origin becomes the final map frame.
                artifact = dataclasses.replace(
                    artifact, frame_id=self.config.final_map_frame
                )
                ended = datetime.now(timezone.utc)
                metadata = MapBuildingResultMetadata(
                    session.session_id, session.device.device_id, session.started_at, ended,
                    self.config.protocol_v2_id, self.config.voxel_size_m,
                    session.complete_frames, session.dropped_frames,
                    session.accumulator.received_points, len(session.accumulator.points()),
                    session.sample_window_seconds, descriptor.sha256,
                    artifact.file_sha256["pcd"], artifact.file_sha256["pgm"],
                    artifact.file_sha256["yaml"],
                )
                definition = self.repository.commit_remote_mapping_artifact(
                    session.definition.map_id, artifact, metadata
                )
            except (ArtifactDownloadError, ArtifactValidationError, MapRepositoryError,
                    OSError, ValueError) as exc:
                with self._lock:
                    if self.session is session:
                        self._fail(session, str(exc), "ARTIFACT_FAILED")
                return
            with self._lock:
                if self.session is not session:
                    return
                session.frame_id = self.config.final_map_frame
                session.state = "completed"
                session.message = "遥控建图成果已保存"
                self._log(session, "LOCAL", "completed", "成果已提交，坐标系切换为 map")
                self._emit(session)
                self.completed.emit(definition)
                self.session = None

        self._artifact_thread = threading.Thread(
            target=run, name="ccs-remote-map-artifact", daemon=True
        )
        self._artifact_thread.start()

    def _send_command(self, session: _RemoteSession, *, return_host: str = "",
                      return_port: int = 0, reason: str = "", force: bool = False) -> None:
        if not force and session.command_attempts >= self.config.command_max_attempts:
            return
        if session.command == "prepare_mapping":
            payload = {
                "request_id": session.request_id,
                "return_host": return_host,
                "return_port": return_port,
                "required_inputs": ["pointcloud", "imu", "artifact_storage", "map_generation"],
                "restart_active": session.restart_active,
                "preview_transport": self.config.preview_transport,
                "fragment_interval_seconds": self.config.fragment_interval_seconds,
            }
        elif session.command == "start_mapping":
            payload = {
                "request_id": session.request_id,
                "coordinate_contract": "sensor+map_body+body_sensor",
                "preview_transport": self.config.preview_transport,
                "fragment_interval_seconds": self.config.fragment_interval_seconds,
            }
        else:
            payload = {"request_id": session.request_id, "reason": reason or "用户结束建图"}
        envelope = MapBuildingEnvelope(
            session.definition.map_id, session.device.device_id, session.session_id,
            session.command, session.command_attempts, time.time_ns(), payload,
        )
        self.sender(self.protocol.encode(envelope), session.device.ip_address)
        session.command_attempts += 1
        session.last_command_at = self.clock()
        self._log(
            session, "TX", session.command,
            f"第 {session.command_attempts} 次下发，request={session.request_id[:8]}"
            + ("，清理并重启活动会话" if session.restart_active else ""),
        )

    def _send_stop_once(self, session: _RemoteSession, reason: str) -> None:
        envelope = MapBuildingEnvelope(
            session.definition.map_id, session.device.device_id, session.session_id,
            "stop_mapping", session.command_attempts + 1, time.time_ns(),
            {"request_id": os.urandom(16).hex(), "reason": reason},
        )
        self.sender(self.protocol.encode(envelope), session.device.ip_address)

    def _send_abort_once(self, session: _RemoteSession, reason: str) -> None:
        envelope = MapBuildingEnvelope(
            session.definition.map_id, session.device.device_id, session.session_id,
            "abort_mapping", session.command_attempts + 1, time.time_ns(),
            {"request_id": os.urandom(16).hex(), "reason": reason},
        )
        self.sender(self.protocol.encode(envelope), session.device.ip_address)

    def _send_fragment_ack(self, session: _RemoteSession, fragment_id: int) -> None:
        session.outbound_sequence += 1
        envelope = MapBuildingEnvelope(
            session.definition.map_id, session.device.device_id, session.session_id,
            "cloud_fragment_ack", session.outbound_sequence, time.time_ns(),
            {"request_id": os.urandom(16).hex(), "fragment_id": fragment_id},
        )
        self.sender(self.protocol.encode(envelope), session.device.ip_address)

    def _fail(self, session: _RemoteSession, message: str, code: str) -> None:
        session.state = "failed"
        session.message = message
        session.error_code = code
        self._log(session, "LOCAL", code, message, "ERROR")
        self._emit(session)
        self.failed.emit(message)

    def _require(self, states: set[str]) -> _RemoteSession:
        if self.session is None or self.session.state not in states:
            raise RuntimeError("当前遥控建图状态不允许此操作")
        return self.session

    @staticmethod
    def _snapshot(session: _RemoteSession) -> RemoteMappingSnapshot:
        return RemoteMappingSnapshot(
            session.definition.map_id, session.device.device_id, session.session_id,
            session.state, session.message, session.started_at, session.checks,
            session.sample_window_seconds, session.frame_id, session.capability_version,
            session.complete_frames, session.dropped_frames,
            session.accumulator.received_points, len(session.accumulator.points()),
            session.last_data_at, session.artifact_bytes_received,
            session.artifact_bytes_total, session.error_code, tuple(session.log_entries),
        )

    def _start_command(self, session: _RemoteSession, command: str, timeout: float) -> None:
        now = self.clock()
        session.command = command
        session.request_id = os.urandom(16).hex()
        session.command_attempts = 0
        session.command_started_at = now
        session.command_deadline_at = now + timeout

    @staticmethod
    def _response_summary(message_type: str, payload: dict[str, Any]) -> str:
        if message_type == "prepare_result":
            state = "通过" if payload.get("accepted") else "拒绝"
            detail = str(payload.get("reason") or "")
            return f"协商{state}" + (f"：{detail}" if detail else "")
        if message_type == "command_ack":
            state = "接受" if payload.get("accepted") else "拒绝"
            detail = str(payload.get("reason") or "")
            return f"{payload.get('command', 'command')} {state}" + (
                f"：{detail}" if detail else ""
            )
        if message_type in {"session_status", "session_heartbeat", "artifact_status"}:
            detail = str(payload.get("reason") or payload.get("message") or "")
            return str(payload.get("state") or message_type) + (f"：{detail}" if detail else "")
        if message_type == "cloud_fragment_ready":
            return f"PCD 分片 {payload.get('fragment_id')}，{payload.get('point_count', 0):,} 点"
        return message_type

    @staticmethod
    def _log(session: _RemoteSession, direction: str, event: str, summary: str,
             level: str = "INFO") -> None:
        session.log_entries.append(RemoteMappingLogEntry(
            datetime.now(timezone.utc), direction, event, summary, level,
        ))
        if len(session.log_entries) > 200:
            del session.log_entries[:-200]

    def _emit(self, session: _RemoteSession) -> None:
        snapshot = self._snapshot(session)
        self.updated.emit(snapshot)
        self.navigation_locked.emit(snapshot.navigation_locked)
