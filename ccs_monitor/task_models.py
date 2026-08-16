from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .models import utc_now


class TaskDefinitionStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ERROR = "error"


class TaskExecutionStatus(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskEventLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class TaskWaypoint:
    waypoint_id: str
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class DeviceSubtask:
    subtask_id: str
    device_id: str
    device_name: str
    device_type: str
    ip_address: str
    layer_mode: str = "pointcloud"
    waypoints: tuple[TaskWaypoint, ...] = ()
    default_altitude_m: float = 1.0
    cruise_speed_mps: float = 1.0
    start_delay_seconds: float = 0.0
    revision: int = 0
    delivered_revision: int | None = None

    @property
    def is_valid(self) -> bool:
        return 2 <= len(self.waypoints) <= 500

    @property
    def is_delivered(self) -> bool:
        return self.delivered_revision == self.revision and self.revision > 0


@dataclass(frozen=True)
class TaskSafetySettings:
    horizontal_distance_m: float = 2.0
    vertical_distance_m: float = 1.0
    time_margin_seconds: float = 2.0


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    name: str
    map_id: str
    map_name: str
    frame_id: str
    map_fingerprint: str
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    subtasks: tuple[DeviceSubtask, ...] = ()
    safety: TaskSafetySettings = field(default_factory=TaskSafetySettings)
    status: TaskDefinitionStatus = TaskDefinitionStatus.DRAFT
    directory_name: str = ""
    error_message: str | None = None

    @property
    def is_ready(self) -> bool:
        return bool(self.subtasks) and all(item.is_valid for item in self.subtasks)


@dataclass(frozen=True)
class TaskConflict:
    conflict_id: str
    first_device_id: str
    second_device_id: str
    first_segment_index: int
    second_segment_index: int
    time_seconds: float
    horizontal_distance_m: float
    vertical_distance_m: float
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class TaskEvent:
    timestamp: datetime
    event_type: str
    message: str
    level: TaskEventLevel = TaskEventLevel.INFO
    task_id: str = ""
    execution_id: str | None = None
    device_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskExecutionSnapshot:
    execution_id: str
    task_id: str
    device_ids: tuple[str, ...]
    status: TaskExecutionStatus
    created_at: datetime
    scheduled_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)
    message: str = ""
    device_states: tuple[tuple[str, str], ...] = ()
    forced_conflict_reason: str | None = None

