import ipaddress
import io
import math
import os

import yaml


class ConfigError(ValueError):
    pass


def _read(path, expected_schema=2):
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as exc:
        raise ConfigError("cannot read config %s: %s" % (path, exc))
    if not isinstance(value, dict) or value.get("schema_version") != expected_schema:
        raise ConfigError("config schema_version must be %s: %s" % (expected_schema, path))
    return value


def _map(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError("%s must be a mapping" % key)
    return value


def _text(parent, key, label=None):
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s must be a non-empty string" % (label or key))
    return value.strip()


def _number(value, label, minimum, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be numeric" % label)
    result = int(value) if integer else float(value)
    if (not integer and not math.isfinite(result)) or result < minimum or (maximum is not None and result > maximum):
        raise ConfigError("%s is out of range" % label)
    if integer and result != value:
        raise ConfigError("%s must be an integer" % label)
    return result


def _ip(value, label, unspecified=False):
    try:
        parsed = ipaddress.ip_address(str(value))
    except ValueError as exc:
        raise ConfigError("%s must be an IP address" % label) from exc
    if parsed.version != 4 or (parsed.is_unspecified and not unspecified):
        raise ConfigError("%s must be a usable IPv4 address" % label)
    return str(parsed)


def load_config(task_path, device_path):
    task = _read(task_path)
    device_root = _read(device_path, 1)
    network = _map(task, "network")
    storage = _map(task, "storage")
    ros = _map(task, "ros")
    timeouts = _map(task, "timeouts")
    limits = _map(task, "limits")
    adapter = task.get("adapter", {})
    if adapter and not isinstance(adapter, dict):
        raise ConfigError("adapter must be a mapping")
    device = _map(device_root, "device")
    result = {
        "protocol_id": _text(task, "protocol_id"),
        "device_id": _text(device, "id", "device.id"),
        "device_ip": _ip(_text(device, "ip", "device.ip"), "device.ip"),
        "bind_host": _ip(network.get("bind_host"), "network.bind_host", True),
        "control_port": _number(network.get("control_port"), "network.control_port", 1, 65535, True),
        "ground_station_ip": _ip(network.get("ground_station_ip"), "network.ground_station_ip"),
        "status_port": _number(network.get("status_port"), "network.status_port", 1, 65535, True),
        "max_datagram_bytes": _number(network.get("max_datagram_bytes"), "network.max_datagram_bytes", 512, 1400, True),
        "storage_directory": os.path.abspath(os.path.expanduser(_text(storage, "directory", "storage.directory"))),
        "command_topic": _text(ros, "command_topic", "ros.command_topic"),
        "feedback_topic": _text(ros, "feedback_topic", "ros.feedback_topic"),
        "status_topic": _text(ros, "status_topic", "ros.status_topic"),
        "map_frame": _text(ros, "map_frame", "ros.map_frame"),
        "ack_cache_seconds": _number(timeouts.get("ack_cache_seconds"), "timeouts.ack_cache_seconds", 1.0),
        "transfer_seconds": _number(timeouts.get("transfer_seconds"), "timeouts.transfer_seconds", 0.1),
        "adapter_feedback_seconds": _number(timeouts.get("adapter_feedback_seconds"), "timeouts.adapter_feedback_seconds", 0.1),
        "execution_feedback_seconds": _number(timeouts.get("execution_feedback_seconds"), "timeouts.execution_feedback_seconds", 0.1),
        "utc_tolerance_seconds": _number(timeouts.get("utc_tolerance_seconds"), "timeouts.utc_tolerance_seconds", 0.01),
        "max_waypoints": _number(limits.get("max_waypoints"), "limits.max_waypoints", 2, 500, True),
        "max_compressed_bytes": _number(limits.get("max_compressed_bytes"), "limits.max_compressed_bytes", 1024, integer=True),
        "max_raw_bytes": _number(limits.get("max_raw_bytes"), "limits.max_raw_bytes", 1024, integer=True),
        "max_chunks": _number(limits.get("max_chunks"), "limits.max_chunks", 1, 4096, True),
        "adapter": dict(adapter),
    }
    if adapter:
        adapter_text = (
            "active_map_state_file", "navigation_launch_package", "navigation_launch_file",
            "navigation_map_root", "navigation_map_yaml", "navigation_action",
            "odom_topic", "zero_velocity_topic",
        )
        for key in adapter_text:
            if not isinstance(adapter.get(key), str) or not adapter[key].strip():
                raise ConfigError("adapter.%s must be a non-empty string" % key)
        for key in ("navigation_startup_timeout_seconds", "waypoint_timeout_seconds",
                    "pose_timeout_seconds", "zero_velocity_hz"):
            value = adapter.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ConfigError("adapter.%s must be positive" % key)
        count = adapter.get("zero_velocity_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ConfigError("adapter.zero_velocity_count must be a positive integer")
        result["adapter"] = dict(adapter)
    if result["max_raw_bytes"] < result["max_compressed_bytes"]:
        raise ConfigError("limits.max_raw_bytes must cover max_compressed_bytes")
    return result
