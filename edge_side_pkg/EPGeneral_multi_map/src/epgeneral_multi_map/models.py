import uuid
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class PoseSample:
    stamp_ns: int
    translation: np.ndarray
    rotation_xyzw: np.ndarray

    def __post_init__(self):
        if isinstance(self.stamp_ns, bool) or not isinstance(self.stamp_ns, int) or self.stamp_ns < 0:
            raise ValueError("pose stamp_ns is invalid")
        translation = np.asarray(self.translation, dtype=np.float64)
        rotation = np.asarray(self.rotation_xyzw, dtype=np.float64)
        if translation.shape != (3,) or not np.isfinite(translation).all():
            raise ValueError("pose translation must contain three finite values")
        if rotation.shape != (4,) or not np.isfinite(rotation).all():
            raise ValueError("pose quaternion must contain four finite values")
        norm = float(np.linalg.norm(rotation))
        if norm < 1e-12:
            raise ValueError("pose quaternion must not be zero")
        object.__setattr__(self, "translation", translation.copy())
        object.__setattr__(self, "rotation_xyzw", rotation / norm)


@dataclass(frozen=True)
class PoseMatch:
    transform: np.ndarray
    before_stamp_ns: int
    after_stamp_ns: int
    max_error_ns: int
    interpolated: bool


@dataclass
class SynchronizedFrame:
    stamp_ns: int
    raw_message: object
    raw_point_count: int
    raw_bytes: int
    map_from_body: np.ndarray
    pose_match: PoseMatch


@dataclass
class SliceBatch:
    slice_id: int
    start_ns: int
    end_ns: int
    frames: list
    partial: bool = False
    error_tail: bool = False
    truncated: bool = False
    dropped_invalid: int = 0
    dropped_sync: int = 0
    dropped_late: int = 0
    dropped_resource: int = 0


@dataclass
class SessionStats:
    dropped_invalid: int = 0
    dropped_sync: int = 0
    dropped_late: int = 0
    dropped_resource: int = 0
    max_uploaded_stamp_ns: int = 0


@dataclass
class MappingSession:
    identity: dict
    job_id: str
    participant_device_ids: tuple
    start_at_ns: int
    slice_duration_ns: int
    destination: tuple
    token: str
    pose_buffer: object
    collector: object
    stats: SessionStats
    cloud_rate_hz: float
    voxel_size_m: float
    state: str = "starting"
    stop_at_ns: object = None
    mapping_started: bool = False
    last_cloud_monotonic: object = None
    last_pose_monotonic: object = None
    last_heartbeat_monotonic: object = None
    last_accepted_cloud_stamp_ns: object = None
    last_watchdog_wall_ns: object = None
    subscribers: list = field(default_factory=list)
    frame_id: int = 0

    @classmethod
    def from_command(cls, command, config, destination):
        from .slicing import SliceCollector
        from .time_sync import PoseBuffer

        payload = command["payload"]
        return cls(
            {"map_id": command["map_id"], "session_id": command["session_id"]},
            payload["job_id"], tuple(payload["participant_device_ids"]),
            payload["start_at_ns"], payload["slice_duration_ns"], destination,
            uuid.uuid4().hex, PoseBuffer(config["pose_buffer_size"]),
            SliceCollector(
                payload["start_at_ns"], payload["slice_duration_ns"],
                config["late_arrival_ns"], {
                    "max_slice_frames": config["max_slice_frames"],
                    "max_slice_points": config["max_slice_points"],
                    "max_slice_bytes": config["max_slice_bytes"],
                },
            ), SessionStats(),
            min(float(payload["cloud_rate_hz"]), config["max_cloud_rate_hz"]),
            max(float(payload["voxel_size_m"]), config["min_voxel_size_m"]),
        )
