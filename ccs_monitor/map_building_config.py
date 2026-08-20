from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAP_BUILDING_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "map_building.json"


class MapBuildingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MapBuildingConfig:
    protocol_id: str
    protocol_v2_id: str
    bind_host: str
    data_port: int
    device_control_port: int
    max_datagram_bytes: int
    cloud_rate_hz: float
    voxel_size_m: float
    compression: str
    point_format: str
    command_retry_seconds: float
    command_max_attempts: int
    frame_timeout_seconds: float
    warning_timeout_seconds: float
    error_timeout_seconds: float
    artifact_generation_timeout_seconds: float
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float
    max_frame_points: int
    max_decompressed_bytes: int
    max_accumulated_voxels: int
    max_preview_points: int
    http_download_attempts: int
    max_artifact_bytes: int


def load_map_building_config(path: Path = DEFAULT_MAP_BUILDING_CONFIG_PATH) -> MapBuildingConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MapBuildingConfigError(f"无法读取建图配置：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MapBuildingConfigError("建图配置 schema_version 必须为 1")
    try:
        network = payload["network"]
        stream = payload["stream"]
        timeouts = payload["timeouts"]
        limits = payload["limits"]
        config = MapBuildingConfig(
            protocol_id=str(payload["protocol_id"]),
            protocol_v2_id=str(payload.get("protocol_v2_id", "ccs-map-stream-v2")),
            bind_host=str(network["bind_host"]),
            data_port=int(network["data_port"]),
            device_control_port=int(network["device_control_port"]),
            max_datagram_bytes=int(network["max_datagram_bytes"]),
            cloud_rate_hz=float(stream["cloud_rate_hz"]),
            voxel_size_m=float(stream["voxel_size_m"]),
            compression=str(stream["compression"]),
            point_format=str(stream["point_format"]),
            command_retry_seconds=float(timeouts["command_retry_seconds"]),
            command_max_attempts=int(timeouts["command_max_attempts"]),
            frame_timeout_seconds=float(timeouts["frame_timeout_seconds"]),
            warning_timeout_seconds=float(timeouts["warning_timeout_seconds"]),
            error_timeout_seconds=float(timeouts["error_timeout_seconds"]),
            artifact_generation_timeout_seconds=float(
                timeouts.get("artifact_generation_timeout_seconds", 600.0)
            ),
            http_connect_timeout_seconds=float(timeouts.get("http_connect_timeout_seconds", 5.0)),
            http_read_timeout_seconds=float(timeouts.get("http_read_timeout_seconds", 30.0)),
            max_frame_points=int(limits["max_frame_points"]),
            max_decompressed_bytes=int(limits["max_decompressed_bytes"]),
            max_accumulated_voxels=int(limits["max_accumulated_voxels"]),
            max_preview_points=int(limits["max_preview_points"]),
            http_download_attempts=int(limits.get("http_download_attempts", 3)),
            max_artifact_bytes=int(limits.get("max_artifact_bytes", 4 * 1024 ** 3)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MapBuildingConfigError(f"建图配置字段无效：{exc}") from exc
    if not config.protocol_id or not config.protocol_v2_id:
        raise MapBuildingConfigError("protocol_id 和 protocol_v2_id 不能为空")
    if config.protocol_id == config.protocol_v2_id:
        raise MapBuildingConfigError("v1 与 v2 协议 ID 不能相同")
    if not 1 <= config.data_port <= 65535 or not 1 <= config.device_control_port <= 65535:
        raise MapBuildingConfigError("UDP 端口无效")
    if not 512 <= config.max_datagram_bytes <= 65507:
        raise MapBuildingConfigError("max_datagram_bytes 必须在 512 到 65507 之间")
    if config.compression != "zlib" or config.point_format != "xyz_f32_le":
        raise MapBuildingConfigError("首版仅支持 zlib 和 xyz_f32_le")
    if config.cloud_rate_hz <= 0 or config.voxel_size_m <= 0:
        raise MapBuildingConfigError("点云速率和体素尺寸必须大于零")
    if not 0 < config.command_retry_seconds or config.command_max_attempts < 1:
        raise MapBuildingConfigError("指令重试参数无效")
    if not 0 < config.frame_timeout_seconds <= config.warning_timeout_seconds < config.error_timeout_seconds:
        raise MapBuildingConfigError("超时参数必须满足 frame <= warning < error")
    if min(
        config.max_frame_points,
        config.max_decompressed_bytes,
        config.max_accumulated_voxels,
        config.max_preview_points,
    ) <= 0:
        raise MapBuildingConfigError("资源限制必须大于零")
    if min(
        config.artifact_generation_timeout_seconds,
        config.http_connect_timeout_seconds,
        config.http_read_timeout_seconds,
    ) <= 0:
        raise MapBuildingConfigError("成果生成与 HTTP 超时必须大于零")
    if not 1 <= config.http_download_attempts <= 20:
        raise MapBuildingConfigError("http_download_attempts 必须在 1 到 20 之间")
    if not 1024 <= config.max_artifact_bytes <= 16 * 1024 ** 3:
        raise MapBuildingConfigError("max_artifact_bytes 必须在 1 KiB 到 16 GiB 之间")
    return config
