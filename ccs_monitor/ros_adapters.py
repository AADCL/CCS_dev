"""Optional ROS conversion boundaries.

The concrete ROS message type is intentionally not assumed. Deployments can pass
their message objects to these converters or subclass the source protocol.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    ConnectionStatus,
    DeviceAvailability,
    DeviceSnapshot,
    HealthStatus,
    LocalizationStatus,
    TaskStatus,
)


def _value(message: Any, name: str, default: Any = None) -> Any:
    return getattr(message, name, default)


def _enum(enum_type: type, value: Any, default: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value).lower())
    except ValueError:
        return default


def snapshot_from_message(message: Any) -> DeviceSnapshot:
    """Convert a ROS-like object with the project's canonical field names."""
    raw_battery = _value(message, "battery_percent")
    battery = None if raw_battery is None else max(0, min(100, int(raw_battery)))
    raw_x = _value(message, "position_x")
    raw_y = _value(message, "position_y")
    has_position = raw_x is not None and raw_y is not None
    return DeviceSnapshot(
        device_id=str(_value(message, "device_id", "unknown")),
        device_name=str(_value(message, "device_name", "未命名设备")),
        device_type=str(_value(message, "device_type", "DEVICE")).upper(),
        battery_percent=battery,
        localization_status=_enum(LocalizationStatus, _value(message, "localization_status"), LocalizationStatus.UNKNOWN),
        task_status=_enum(TaskStatus, _value(message, "task_status"), TaskStatus.UNKNOWN),
        connection_status=_enum(ConnectionStatus, _value(message, "connection_status"), ConnectionStatus.OFFLINE),
        updated_at=_value(message, "updated_at", datetime.now(timezone.utc)),
        position_x=float(raw_x) if has_position else None,
        position_y=float(raw_y) if has_position else None,
        frame_id=str(_value(message, "frame_id")) if has_position and _value(message, "frame_id") else None,
        ip_address=str(_value(message, "ip_address", "")),
        availability=_enum(DeviceAvailability, _value(message, "availability"), DeviceAvailability.UNKNOWN),
        last_tested_at=_value(message, "last_tested_at"),
        health_status=_enum(HealthStatus, _value(message, "health_status"), HealthStatus.NORMAL),
    )


class Ros1DeviceAdapter:
    """ROS1 hook; import rospy and subscribe in the deployment-specific subclass."""

    ros_version = "ros1"

    def convert(self, message: Any) -> DeviceSnapshot:
        return snapshot_from_message(message)


class Ros2DeviceAdapter:
    """ROS2 hook; import rclpy and subscribe in the deployment-specific subclass."""

    ros_version = "ros2"

    def convert(self, message: Any) -> DeviceSnapshot:
        return snapshot_from_message(message)
