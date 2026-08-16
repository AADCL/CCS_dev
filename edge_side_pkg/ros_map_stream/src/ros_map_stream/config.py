import ipaddress
import io
import math

import yaml


class ConfigError(ValueError):
    pass


def _read_yaml(path):
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as exc:
        raise ConfigError("cannot read config %s: %s" % (path, exc))
    if not isinstance(value, dict):
        raise ConfigError("config root must be a mapping: %s" % path)
    return value


def _mapping(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError("%s must be a mapping" % key)
    return value


def _text(parent, key, path=None):
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s must be a non-empty string" % (path or key))
    return value.strip()


def _port(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError("%s must be a valid UDP port" % name)
    return value


def _positive_number(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be numeric" % name)
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise ConfigError("%s must be finite and positive" % name)
    return number


def _ip(value, name, allow_unspecified=False):
    try:
        parsed = ipaddress.ip_address(str(value))
    except ValueError as exc:
        raise ConfigError("%s must be an IP address" % name) from exc
    if parsed.is_unspecified and not allow_unspecified:
        raise ConfigError("%s must not be unspecified" % name)
    return str(parsed)


def _transform(value, name):
    if not isinstance(value, dict):
        raise ConfigError("%s must be a mapping" % name)
    result = {}
    for key in ("x", "y", "z", "qx", "qy", "qz", "qw"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise ConfigError("%s.%s must be finite" % (name, key))
        result[key] = float(item)
    norm = math.sqrt(sum(result[key] * result[key] for key in ("qx", "qy", "qz", "qw")))
    if norm < 1e-6:
        raise ConfigError("%s quaternion must not be zero" % name)
    for key in ("qx", "qy", "qz", "qw"):
        result[key] /= norm
    return result


def load_config(mapping_path, device_path):
    mapping = _read_yaml(mapping_path)
    device_config = _read_yaml(device_path)
    if mapping.get("schema_version") != 1 or device_config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    device = _mapping(device_config, "device")
    device_id = _text(device, "id", "device.id")
    device_ip = _ip(_text(device, "ip", "device.ip"), "device.ip")

    network = _mapping(mapping, "network")
    ros = _mapping(mapping, "ros")
    cloud = _mapping(ros, "cloud")
    pose = _mapping(ros, "pose")
    frames = _mapping(ros, "frames")
    sync = _mapping(mapping, "sync")
    preprocess = _mapping(mapping, "preprocess")
    timeouts = _mapping(mapping, "timeouts")
    limits = _mapping(mapping, "limits")

    cloud_type = _text(cloud, "message_type", "ros.cloud.message_type")
    if cloud_type != "sensor_msgs/PointCloud2":
        raise ConfigError("ros.cloud.message_type must be sensor_msgs/PointCloud2")
    pose_type = _text(pose, "message_type", "ros.pose.message_type")
    if pose_type.count("/") != 1:
        raise ConfigError("ros.pose.message_type must use package/Message syntax")

    minimum_range = _positive_number(preprocess.get("min_range_m"), "preprocess.min_range_m", True)
    maximum_range = _positive_number(preprocess.get("max_range_m"), "preprocess.max_range_m")
    if minimum_range >= maximum_range:
        raise ConfigError("preprocess range must satisfy min < max")
    tolerance = _positive_number(sync.get("tolerance_seconds"), "sync.tolerance_seconds")
    if tolerance > 1.0:
        raise ConfigError("sync.tolerance_seconds must not exceed 1 second")
    pose_buffer_size = sync.get("pose_buffer_size")
    if isinstance(pose_buffer_size, bool) or not isinstance(pose_buffer_size, int) or not 2 <= pose_buffer_size <= 10000:
        raise ConfigError("sync.pose_buffer_size is invalid")
    max_points = limits.get("max_frame_points")
    max_decompressed = limits.get("max_decompressed_bytes")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points <= 0:
        raise ConfigError("limits.max_frame_points is invalid")
    if isinstance(max_decompressed, bool) or not isinstance(max_decompressed, int) or max_decompressed < max_points * 12:
        raise ConfigError("limits.max_decompressed_bytes must cover max_frame_points * 12")
    max_datagram = network.get("max_datagram_bytes")
    if isinstance(max_datagram, bool) or not isinstance(max_datagram, int) or not 512 <= max_datagram <= 1400:
        raise ConfigError("network.max_datagram_bytes must be between 512 and 1400")

    return {
        "protocol_id": _text(mapping, "protocol_id"),
        "device_id": device_id,
        "device_ip": device_ip,
        "bind_host": _ip(network.get("bind_host"), "network.bind_host", True),
        "control_port": _port(network.get("control_port"), "network.control_port"),
        "ground_station_ip": _ip(network.get("ground_station_ip"), "network.ground_station_ip"),
        "data_port": _port(network.get("data_port"), "network.data_port"),
        "max_datagram_bytes": max_datagram,
        "cloud_topic": _text(cloud, "topic", "ros.cloud.topic"),
        "cloud_message_type": cloud_type,
        "pose_topic": _text(pose, "topic", "ros.pose.topic"),
        "pose_message_type": pose_type,
        "pose_position_path": _text(pose, "position_path", "ros.pose.position_path"),
        "pose_orientation_path": _text(pose, "orientation_path", "ros.pose.orientation_path"),
        "map_frame": _text(frames, "map", "ros.frames.map"),
        "body_frame": _text(frames, "body", "ros.frames.body"),
        "sensor_frame": _text(frames, "sensor", "ros.frames.sensor"),
        "body_from_sensor": _transform(ros.get("body_from_sensor"), "ros.body_from_sensor"),
        "sync_tolerance_seconds": tolerance,
        "pose_buffer_size": pose_buffer_size,
        "min_range_m": minimum_range,
        "max_range_m": maximum_range,
        "min_voxel_size_m": _positive_number(preprocess.get("min_voxel_size_m"), "preprocess.min_voxel_size_m"),
        "max_cloud_rate_hz": _positive_number(preprocess.get("max_cloud_rate_hz"), "preprocess.max_cloud_rate_hz"),
        "input_timeout_seconds": _positive_number(timeouts.get("input_timeout_seconds"), "timeouts.input_timeout_seconds"),
        "command_cache_seconds": _positive_number(timeouts.get("command_cache_seconds"), "timeouts.command_cache_seconds"),
        "max_frame_points": max_points,
        "max_decompressed_bytes": max_decompressed,
    }
