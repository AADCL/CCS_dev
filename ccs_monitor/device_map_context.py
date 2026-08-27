from __future__ import annotations

import math
from dataclasses import dataclass

from .models import PoseTelemetry, RelocalizationStatus
from .relocalization_services import STATUS_TEXT


MAP_MODE_TEXT = {
    "empty": "空地图",
    "imported": "导入地图",
    "single": "单机遥控建图",
    "multi": "多机联合建图",
    "fusion": "地图融合",
}


@dataclass(frozen=True)
class DeviceMapContext:
    map_id: str | None
    localization_text: str
    local_pose: PoseTelemetry | None
    map_pose: PoseTelemetry | None
    pose_message: str


def _rotate(qx, qy, qz, qw, vector):
    # Quaternion-vector rotation without a GUI/numpy dependency.
    vx, vy, vz = vector
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + qy * tz - qz * ty,
        vy + qw * ty + qz * tx - qx * tz,
        vz + qw * tz + qx * ty - qy * tx,
    )


def transform_pose(pose: PoseTelemetry, transform) -> PoseTelemetry:
    px, py, pz = _rotate(
        transform.qx, transform.qy, transform.qz, transform.qw,
        (pose.x, pose.y, pose.z),
    )
    roll, pitch, yaw = map(math.radians, (pose.roll, pose.pitch, pose.yaw))
    cr, sr, cp, sp, cy, sy = (
        math.cos(roll / 2), math.sin(roll / 2), math.cos(pitch / 2),
        math.sin(pitch / 2), math.cos(yaw / 2), math.sin(yaw / 2),
    )
    ix, iy = sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy
    iz, iw = cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy
    qx = transform.qw * ix + transform.qx * iw + transform.qy * iz - transform.qz * iy
    qy = transform.qw * iy - transform.qx * iz + transform.qy * iw + transform.qz * ix
    qz = transform.qw * iz + transform.qx * iy - transform.qy * ix + transform.qz * iw
    qw = transform.qw * iw - transform.qx * ix - transform.qy * iy - transform.qz * iz
    out_roll = math.atan2(2 * (qw * qx + qy * qz), 1 - 2 * (qx * qx + qy * qy))
    sin_pitch = max(-1.0, min(1.0, 2 * (qw * qy - qz * qx)))
    out_pitch = math.asin(sin_pitch)
    out_yaw = math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
    return PoseTelemetry(
        px + transform.x, py + transform.y, pz + transform.z,
        math.degrees(out_roll), math.degrees(out_pitch), math.degrees(out_yaw),
        pose.sample_age_seconds,
    )


def resolve_device_map_context(source, relocalization_service, telemetry, device_id: str) -> DeviceMapContext:
    profile = source.profile(device_id)
    if profile is None:
        return DeviceMapContext(None, "未知空间", None, None, "设备配置不存在")
    local_source = "vision_pose" if profile.relocalization_profile == "scout_mini" else "global_pose"
    local_pose = getattr(telemetry, local_source, None) if telemetry is not None else None
    if local_pose is not None and local_pose.sample_age_seconds > 2.0:
        local_pose = None
    map_id = profile.active_map_id
    if not map_id:
        return DeviceMapContext(None, "未知空间", local_pose, None, "尚未设置活动地图")
    binding = next((item for item in profile.map_bindings if item.map_id == map_id), None)
    snapshot = relocalization_service.snapshot(map_id, device_id) if relocalization_service else None
    if snapshot is not None and snapshot.session_id:
        localization_text = STATUS_TEXT[snapshot.status]
    elif binding is not None:
        localization_text = STATUS_TEXT[RelocalizationStatus.SUCCEEDED]
    elif profile.relocalization_profile == "go2_edu":
        localization_text = STATUS_TEXT[RelocalizationStatus.UNSUPPORTED]
    else:
        localization_text = STATUS_TEXT[RelocalizationStatus.UNKNOWN_SPACE]
    if binding is None:
        return DeviceMapContext(map_id, localization_text, local_pose, None, "活动地图尚无重定位绑定")
    bound_source = getattr(telemetry, binding.pose_source, None) if telemetry is not None else None
    if bound_source is None or bound_source.sample_age_seconds > 2.0:
        return DeviceMapContext(map_id, localization_text, local_pose, None, "本地位姿缺失或已超时")
    return DeviceMapContext(
        map_id, localization_text, local_pose,
        transform_pose(bound_source, binding.map_from_odom),
        f"{binding.odom_frame} -> {binding.map_frame}",
    )


def map_mode_text(map_repository, mapping_service, map_id: str | None, device_id: str) -> str:
    if not map_id:
        return "模式未知"
    job = mapping_service.current_job_snapshot if mapping_service else None
    if job is not None and job.map_id == map_id and any(
        item.device_id.casefold() == device_id.casefold() for item in job.device_sessions
    ):
        return "单机遥控建图" if len(job.device_sessions) == 1 else "多机联合建图"
    definition = map_repository.map_by_id(map_id) if map_repository else None
    provenance = definition.build_provenance if definition is not None else None
    return MAP_MODE_TEXT.get(provenance.mode.value, "模式未知") if provenance else "模式未知"
