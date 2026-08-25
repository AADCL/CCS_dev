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
    remote_mapping_frame: str
    remote_artifact_frame: str
    final_map_frame: str
    device_frames: dict[str, dict[str, str]]
    bind_host: str
    data_port: int
    device_control_port: int
    max_datagram_bytes: int
    receive_buffer_bytes: int
    cloud_rate_hz: float
    voxel_size_m: float
    compression: str
    point_format: str
    preview_transport: str
    fragment_interval_seconds: float
    command_retry_seconds: float
    command_max_attempts: int
    prepare_command_timeout_seconds: float
    start_command_timeout_seconds: float
    recovery_command_timeout_seconds: float
    frame_timeout_seconds: float
    warning_timeout_seconds: float
    error_timeout_seconds: float
    heartbeat_timeout_seconds: float
    retransmit_delay_seconds: float
    retransmit_max_attempts: int
    artifact_generation_timeout_seconds: float
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float
    max_frame_points: int
    max_decompressed_bytes: int
    max_accumulated_voxels: int
    max_preview_points: int
    max_preview_fragment_bytes: int
    max_pending_preview_fragments: int
    http_download_attempts: int
    max_artifact_bytes: int

    def mapping_frame_for(self, device_id: str) -> str:
        return self.device_frames.get(device_id, {}).get(
            "remote_mapping", self.remote_mapping_frame)

    def preview_source_frame_for(self, device_id: str) -> str:
        return self.device_frames.get(device_id, {}).get(
            "preview_source", self.remote_artifact_frame)

    def artifact_frame_for(self, device_id: str) -> str:
        return self.device_frames.get(device_id, {}).get(
            "remote_artifact", self.remote_artifact_frame)


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
        frames = payload.get("frames", {})
        timeouts = payload["timeouts"]
        limits = payload["limits"]
        config = MapBuildingConfig(
            protocol_id=str(payload["protocol_id"]),
            protocol_v2_id=str(payload.get("protocol_v2_id", "ccs-map-stream-v2")),
            remote_mapping_frame=str(frames.get("remote_mapping", "odom")),
            remote_artifact_frame=str(frames.get("remote_artifact", "lio_odom")),
            final_map_frame=str(frames.get("final_map", "map")),
            device_frames={
                str(device_id): {str(key): str(value) for key, value in values.items()}
                for device_id, values in payload.get("device_frames", {}).items()
            },
            bind_host=str(network["bind_host"]),
            data_port=int(network["data_port"]),
            device_control_port=int(network["device_control_port"]),
            max_datagram_bytes=int(network["max_datagram_bytes"]),
            receive_buffer_bytes=int(network.get("receive_buffer_bytes", 4 * 1024 * 1024)),
            cloud_rate_hz=float(stream["cloud_rate_hz"]),
            voxel_size_m=float(stream["voxel_size_m"]),
            compression=str(stream["compression"]),
            point_format=str(stream["point_format"]),
            preview_transport=str(stream.get("preview_transport", "pcd_fragment_http")),
            fragment_interval_seconds=float(stream.get("fragment_interval_seconds", 1.0)),
            command_retry_seconds=float(timeouts["command_retry_seconds"]),
            command_max_attempts=int(timeouts["command_max_attempts"]),
            prepare_command_timeout_seconds=float(
                timeouts.get("prepare_command_timeout_seconds", 10.0)
            ),
            start_command_timeout_seconds=float(
                timeouts.get("start_command_timeout_seconds", 45.0)
            ),
            recovery_command_timeout_seconds=float(
                timeouts.get("recovery_command_timeout_seconds", 45.0)
            ),
            frame_timeout_seconds=float(timeouts["frame_timeout_seconds"]),
            warning_timeout_seconds=float(timeouts["warning_timeout_seconds"]),
            error_timeout_seconds=float(timeouts["error_timeout_seconds"]),
            heartbeat_timeout_seconds=float(timeouts.get("heartbeat_timeout_seconds", 5.0)),
            retransmit_delay_seconds=float(timeouts.get("retransmit_delay_seconds", 0.35)),
            retransmit_max_attempts=int(timeouts.get("retransmit_max_attempts", 3)),
            artifact_generation_timeout_seconds=float(
                timeouts.get("artifact_generation_timeout_seconds", 600.0)
            ),
            http_connect_timeout_seconds=float(timeouts.get("http_connect_timeout_seconds", 5.0)),
            http_read_timeout_seconds=float(timeouts.get("http_read_timeout_seconds", 30.0)),
            max_frame_points=int(limits["max_frame_points"]),
            max_decompressed_bytes=int(limits["max_decompressed_bytes"]),
            max_accumulated_voxels=int(limits["max_accumulated_voxels"]),
            max_preview_points=int(limits["max_preview_points"]),
            max_preview_fragment_bytes=int(limits.get("max_preview_fragment_bytes", 8 * 1024 ** 2)),
            max_pending_preview_fragments=int(limits.get("max_pending_preview_fragments", 4)),
            http_download_attempts=int(limits.get("http_download_attempts", 3)),
            max_artifact_bytes=int(limits.get("max_artifact_bytes", 4 * 1024 ** 3)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MapBuildingConfigError(f"建图配置字段无效：{exc}") from exc
    if not config.protocol_id or not config.protocol_v2_id:
        raise MapBuildingConfigError("protocol_id 和 protocol_v2_id 不能为空")
    if (not config.remote_mapping_frame or not config.remote_artifact_frame
            or not config.final_map_frame):
        raise MapBuildingConfigError("遥控建图、端侧成果和最终地图坐标系不能为空")
    if config.remote_mapping_frame == config.final_map_frame:
        raise MapBuildingConfigError("遥控建图坐标系和最终地图坐标系必须区分")
    if config.remote_mapping_frame == config.remote_artifact_frame:
        raise MapBuildingConfigError("遥控显示坐标系和端侧成果坐标系必须区分")
    if config.remote_artifact_frame == config.final_map_frame:
        raise MapBuildingConfigError("端侧成果坐标系和最终地图坐标系必须区分")
    for device_id, values in config.device_frames.items():
        if not device_id or not isinstance(values, dict):
            raise MapBuildingConfigError("device_frames 设备配置无效")
        if set(values) - {"remote_mapping", "preview_source", "remote_artifact"}:
            raise MapBuildingConfigError("device_frames 包含未知坐标字段")
        if any(not value for value in values.values()):
            raise MapBuildingConfigError("device_frames 坐标系不能为空")
    if config.protocol_id == config.protocol_v2_id:
        raise MapBuildingConfigError("v1 与 v2 协议 ID 不能相同")
    if not 1 <= config.data_port <= 65535 or not 1 <= config.device_control_port <= 65535:
        raise MapBuildingConfigError("UDP 端口无效")
    if not 512 <= config.max_datagram_bytes <= 65507:
        raise MapBuildingConfigError("max_datagram_bytes 必须在 512 到 65507 之间")
    if not 64 * 1024 <= config.receive_buffer_bytes <= 64 * 1024 * 1024:
        raise MapBuildingConfigError("receive_buffer_bytes 必须在 64 KiB 到 64 MiB 之间")
    if config.compression != "zlib" or config.point_format != "xyz_f32_le":
        raise MapBuildingConfigError("首版仅支持 zlib 和 xyz_f32_le")
    if config.preview_transport != "pcd_fragment_http" or config.fragment_interval_seconds <= 0:
        raise MapBuildingConfigError("v2 实时预览仅支持 pcd_fragment_http 且分片周期必须大于零")
    if config.cloud_rate_hz <= 0 or config.voxel_size_m <= 0:
        raise MapBuildingConfigError("点云速率和体素尺寸必须大于零")
    if not 0 < config.command_retry_seconds or config.command_max_attempts < 1:
        raise MapBuildingConfigError("指令重试参数无效")
    if min(
        config.prepare_command_timeout_seconds,
        config.start_command_timeout_seconds,
        config.recovery_command_timeout_seconds,
    ) <= 0:
        raise MapBuildingConfigError("建图指令超时必须大于零")
    if not 0 < config.frame_timeout_seconds <= config.warning_timeout_seconds < config.error_timeout_seconds:
        raise MapBuildingConfigError("超时参数必须满足 frame <= warning < error")
    if (config.heartbeat_timeout_seconds <= 0 or config.retransmit_delay_seconds <= 0
            or not 1 <= config.retransmit_max_attempts <= 10):
        raise MapBuildingConfigError("心跳或点云重传参数无效")
    if min(
        config.max_frame_points,
        config.max_decompressed_bytes,
        config.max_accumulated_voxels,
        config.max_preview_points,
        config.max_preview_fragment_bytes,
        config.max_pending_preview_fragments,
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
