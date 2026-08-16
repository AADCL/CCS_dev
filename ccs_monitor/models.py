from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ConnectionStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    WARNING = "warning"


class LocalizationStatus(str, Enum):
    FIXED = "fixed"
    SEARCHING = "searching"
    LOST = "lost"
    UNKNOWN = "unknown"


class TaskStatus(str, Enum):
    EXECUTING = "executing"
    STANDBY = "standby"
    PAUSED = "paused"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class DeviceAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    ATTENTION = "attention"
    ABNORMAL = "abnormal"


class DeviceLogLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class UdpLinkStatus(str, Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    WARNING = "warning"
    OFFLINE = "offline"
    MODULE_ERROR = "module_error"


class TelemetryAvailability(str, Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MapStatus(str, Enum):
    WAITING_FOR_PCD = "waiting_for_pcd"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class DeviceStatusCardDefinition:
    card_id: str
    display_name: str
    value_kind: str = "availability"


DEVICE_STATUS_CARD_DEFINITIONS = (
    DeviceStatusCardDefinition("livox_driver", "Livox 驱动状态"),
    DeviceStatusCardDefinition("fastlio2", "FAST-LIO2 定位状态"),
    DeviceStatusCardDefinition("pgm_mapping", "PGM 地图生成状态"),
    DeviceStatusCardDefinition("octomap_mapping", "八叉树地图生成状态"),
    DeviceStatusCardDefinition("occupancy_grid_mapping", "占据栅格图生成状态"),
    DeviceStatusCardDefinition("mapping_mode", "当前建图模式", "text"),
)
DEVICE_STATUS_CARD_CATALOG = {item.card_id: item for item in DEVICE_STATUS_CARD_DEFINITIONS}
DEFAULT_DEVICE_STATUS_CARDS = tuple(item.card_id for item in DEVICE_STATUS_CARD_DEFINITIONS)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DeviceSnapshot:
    device_id: str
    device_name: str
    device_type: str
    battery_percent: float | None = None
    localization_status: LocalizationStatus = LocalizationStatus.UNKNOWN
    task_status: TaskStatus = TaskStatus.UNKNOWN
    connection_status: ConnectionStatus = ConnectionStatus.OFFLINE
    updated_at: datetime = field(default_factory=utc_now)
    position_x: float | None = None
    position_y: float | None = None
    frame_id: str | None = None
    ip_address: str = ""
    availability: DeviceAvailability = DeviceAvailability.UNKNOWN
    last_tested_at: datetime | None = None
    health_status: HealthStatus = HealthStatus.NORMAL
    flight_mode: str = "unknown"
    armed: bool | None = None
    system_status: int | None = None
    battery_voltage: float | None = None
    battery_current: float | None = None
    mission_status_raw: str = "unknown"
    last_heartbeat_at: datetime | None = None
    status_card_ids: tuple[str, ...] = DEFAULT_DEVICE_STATUS_CARDS

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if not self.device_name:
            raise ValueError("device_name must not be empty")
        if self.battery_percent is not None and not 0 <= self.battery_percent <= 100:
            raise ValueError("battery_percent must be between 0 and 100")
        if (self.position_x is None) != (self.position_y is None):
            raise ValueError("position_x and position_y must be provided together")

    @property
    def is_stale(self) -> bool:
        age = (utc_now() - self.updated_at).total_seconds()
        return age > 15

    @property
    def has_position(self) -> bool:
        return self.position_x is not None and self.position_y is not None and bool(self.frame_id)


@dataclass(frozen=True)
class DeviceProfile:
    device_id: str
    device_name: str
    device_type: str
    ip_address: str
    availability: DeviceAvailability = DeviceAvailability.UNKNOWN
    last_tested_at: datetime | None = None
    status_card_ids: tuple[str, ...] = DEFAULT_DEVICE_STATUS_CARDS


@dataclass(frozen=True)
class DeviceLogEntry:
    timestamp: datetime
    level: DeviceLogLevel
    message: str


@dataclass(frozen=True)
class PoseTelemetry:
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float
    sample_age_seconds: float = 0.0


@dataclass(frozen=True)
class ImuTelemetry:
    roll: float
    pitch: float
    yaw: float
    angular_velocity_x: float
    angular_velocity_y: float
    angular_velocity_z: float
    linear_acceleration_x: float
    linear_acceleration_y: float
    linear_acceleration_z: float
    sample_age_seconds: float = 0.0


@dataclass(frozen=True)
class PointCloudTelemetry:
    availability: TelemetryAvailability = TelemetryAvailability.UNKNOWN
    estimated_hz: float | None = None
    sample_age_seconds: float | None = None


@dataclass(frozen=True)
class SensorStatusTelemetry:
    name: str
    display_name: str
    availability: TelemetryAvailability = TelemetryAvailability.UNKNOWN
    sample_age_seconds: float | None = None
    value: str | None = None


@dataclass(frozen=True)
class DeviceTelemetrySnapshot:
    device_id: str
    udp_link_status: UdpLinkStatus = UdpLinkStatus.UNKNOWN
    global_pose: PoseTelemetry | None = None
    vision_pose: PoseTelemetry | None = None
    imu: ImuTelemetry | None = None
    pointcloud: PointCloudTelemetry | None = None
    sensor_statuses: tuple[SensorStatusTelemetry, ...] = ()
    last_heartbeat_at: datetime | None = None
    last_data_at: datetime | None = None
    module_message: str = "UDP 遥测模块尚未启动"


@dataclass(frozen=True)
class MapDefinition:
    map_id: str
    name: str
    frame_id: str = "map"
    width_m: float = 0.0
    height_m: float = 0.0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    creator_devices: tuple["MapCreatorDevice", ...] = ()
    status: MapStatus = MapStatus.WAITING_FOR_PCD
    pcd_path: str | None = None
    point_count: int = 0
    bounds: "MapBounds | None" = None
    pgm: "PgmMapMetadata | None" = None
    last_mapping: "MapBuildingResultMetadata | None" = None
    trajectory_path: str | None = None
    directory_name: str = ""
    error_message: str | None = None


@dataclass(frozen=True)
class MapCreatorDevice:
    device_id: str
    device_name: str
    device_type: str


@dataclass(frozen=True)
class MapBounds:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def depth(self) -> float:
        return self.max_z - self.min_z


@dataclass(frozen=True)
class MapBuildingResultMetadata:
    session_id: str
    device_id: str
    started_at: datetime
    ended_at: datetime
    protocol_id: str
    voxel_size_m: float
    complete_frames: int
    dropped_frames: int
    received_points: int
    fused_points: int


@dataclass(frozen=True)
class PgmMapMetadata:
    image_path: str
    yaml_path: str
    resolution: float
    origin_x: float
    origin_y: float
    origin_yaw: float
    image_width: int
    image_height: int
    negate: bool
    occupied_thresh: float
    free_thresh: float

    @property
    def width_m(self) -> float:
        return self.image_width * self.resolution

    @property
    def height_m(self) -> float:
        return self.image_height * self.resolution


@dataclass(frozen=True)
class DeviceMapMarker:
    device_id: str
    device_name: str
    x: float
    y: float
    z: float
    status: str = "unknown"


@dataclass(frozen=True)
class TaskExecutionSummary:
    title: str
    task_type: str
    started_at: datetime
    ended_at: datetime
    status: str


@dataclass(frozen=True)
class SystemOverview:
    maps: tuple[MapDefinition, ...]
    task_execution_count: int
    last_task: TaskExecutionSummary
