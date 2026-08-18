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


def _mapping(parent, key, path=None):
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError("%s must be a mapping" % (path or key))
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


def _integer(value, name, minimum=1, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError("%s must be an integer >= %d" % (name, minimum))
    if maximum is not None and value > maximum:
        raise ConfigError("%s must not exceed %d" % (name, maximum))
    return value


def _number(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be numeric" % name)
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ConfigError("%s must be finite and %s" % (name, qualifier))
    return number


def seconds_to_ns(value, name, allow_zero=False):
    return int(round(_number(value, name, allow_zero) * 1_000_000_000))


def _ip(value, name, allow_unspecified=False):
    try:
        parsed = ipaddress.ip_address(str(value))
    except ValueError as exc:
        raise ConfigError("%s must be an IP address" % name) from exc
    if parsed.is_unspecified and not allow_unspecified:
        raise ConfigError("%s must not be unspecified" % name)
    return str(parsed)


def _message_type(parent, key, path):
    value = _text(parent, key, path)
    parts = value.split("/")
    if len(parts) != 2 or not all(part.isidentifier() for part in parts):
        raise ConfigError("%s must use package/Message syntax" % path)
    return value


def _field_path(parent, key, path):
    value = _text(parent, key, path)
    if not all(part.isidentifier() for part in value.split(".")):
        raise ConfigError("%s must be a dotted attribute path" % path)
    return value


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


def load_payloads(mapping, device_config):
    if not isinstance(mapping, dict) or not isinstance(device_config, dict):
        raise ConfigError("config roots must be mappings")
    if mapping.get("schema_version") != 1 or device_config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    if mapping.get("protocol_id") != "ccs-map-stream-v1":
        raise ConfigError("protocol_id must be ccs-map-stream-v1")

    device = _mapping(device_config, "device")
    network = _mapping(mapping, "network")
    ros = _mapping(mapping, "ros")
    cloud = _mapping(ros, "cloud", "ros.cloud")
    pose = _mapping(ros, "pose", "ros.pose")
    frames = _mapping(ros, "frames", "ros.frames")
    sync = _mapping(mapping, "sync")
    slicing = _mapping(mapping, "slicing")
    preprocess = _mapping(mapping, "preprocess")
    timeouts = _mapping(mapping, "timeouts")
    limits = _mapping(mapping, "limits")

    control_port = _port(network.get("control_port"), "network.control_port")
    data_port = _port(network.get("data_port"), "network.data_port")
    if control_port == data_port:
        raise ConfigError("network control and data ports must be different")
    max_datagram = _integer(network.get("max_datagram_bytes"), "network.max_datagram_bytes", 512, 1400)

    cloud_type = _text(cloud, "message_type", "ros.cloud.message_type")
    if cloud_type != "sensor_msgs/PointCloud2":
        raise ConfigError("ros.cloud.message_type must be sensor_msgs/PointCloud2")
    pose_type = _message_type(pose, "message_type", "ros.pose.message_type")

    tolerance_ns = seconds_to_ns(sync.get("tolerance_seconds"), "sync.tolerance_seconds")
    if tolerance_ns > 1_000_000_000:
        raise ConfigError("sync.tolerance_seconds must not exceed 1 second")
    pose_buffer_size = _integer(sync.get("pose_buffer_size"), "sync.pose_buffer_size", 2, 10000)
    clock_offset_ns = seconds_to_ns(
        sync.get("max_message_clock_offset_seconds"), "sync.max_message_clock_offset_seconds"
    )
    rollback_ns = seconds_to_ns(
        sync.get("timestamp_rollback_tolerance_seconds"),
        "sync.timestamp_rollback_tolerance_seconds", True,
    )
    if rollback_ns > tolerance_ns:
        raise ConfigError("timestamp rollback tolerance must not exceed sync tolerance")

    minimum_duration_ns = seconds_to_ns(
        slicing.get("min_duration_seconds"), "slicing.min_duration_seconds"
    )
    default_duration_ns = seconds_to_ns(
        slicing.get("default_duration_seconds"), "slicing.default_duration_seconds"
    )
    maximum_duration_ns = seconds_to_ns(
        slicing.get("max_duration_seconds"), "slicing.max_duration_seconds"
    )
    if not minimum_duration_ns <= default_duration_ns <= maximum_duration_ns:
        raise ConfigError("slicing duration must satisfy min <= default <= max")
    late_arrival_ns = seconds_to_ns(
        slicing.get("late_arrival_seconds"), "slicing.late_arrival_seconds", True
    )

    minimum_range = _number(preprocess.get("min_range_m"), "preprocess.min_range_m", True)
    maximum_range = _number(preprocess.get("max_range_m"), "preprocess.max_range_m")
    if minimum_range >= maximum_range:
        raise ConfigError("preprocess range must satisfy min < max")

    input_timeout = _number(timeouts.get("input_timeout_seconds"), "timeouts.input_timeout_seconds")
    command_cache = _number(timeouts.get("command_cache_seconds"), "timeouts.command_cache_seconds")
    clock_skew_ns = seconds_to_ns(
        timeouts.get("clock_skew_tolerance_seconds"), "timeouts.clock_skew_tolerance_seconds"
    )
    start_late_ns = seconds_to_ns(
        timeouts.get("start_late_tolerance_seconds"), "timeouts.start_late_tolerance_seconds", True
    )
    minimum_lead_ns = seconds_to_ns(
        timeouts.get("minimum_start_lead_seconds"), "timeouts.minimum_start_lead_seconds"
    )
    if start_late_ns > minimum_lead_ns:
        raise ConfigError("start late tolerance must not exceed minimum start lead")

    max_participants = _integer(
        limits.get("max_participant_devices"), "limits.max_participant_devices", 2
    )
    max_frame_points = _integer(limits.get("max_frame_points"), "limits.max_frame_points")
    max_decompressed = _integer(
        limits.get("max_decompressed_bytes"), "limits.max_decompressed_bytes"
    )
    if max_decompressed < max_frame_points * 12:
        raise ConfigError("limits.max_decompressed_bytes must cover max_frame_points * 12")
    max_slice_frames = _integer(limits.get("max_slice_frames"), "limits.max_slice_frames")
    max_slice_points = _integer(limits.get("max_slice_points"), "limits.max_slice_points")
    if max_slice_points < max_frame_points:
        raise ConfigError("limits.max_slice_points must cover one max_frame_points frame")
    max_slice_bytes = _integer(limits.get("max_slice_bytes"), "limits.max_slice_bytes")
    if max_slice_bytes < max_decompressed:
        raise ConfigError("limits.max_slice_bytes must cover one decompressed frame")

    return {
        "protocol_id": "ccs-map-stream-v1",
        "device_id": _text(device, "id", "device.id"),
        "device_ip": _ip(_text(device, "ip", "device.ip"), "device.ip"),
        "bind_host": _ip(network.get("bind_host"), "network.bind_host", True),
        "control_port": control_port,
        "ground_station_ip": _ip(network.get("ground_station_ip"), "network.ground_station_ip"),
        "data_port": data_port,
        "max_datagram_bytes": max_datagram,
        "cloud_topic": _text(cloud, "topic", "ros.cloud.topic"),
        "cloud_message_type": cloud_type,
        "pose_topic": _text(pose, "topic", "ros.pose.topic"),
        "pose_message_type": pose_type,
        "pose_position_path": _field_path(pose, "position_path", "ros.pose.position_path"),
        "pose_orientation_path": _field_path(pose, "orientation_path", "ros.pose.orientation_path"),
        "map_frame": _text(frames, "map", "ros.frames.map"),
        "body_frame": _text(frames, "body", "ros.frames.body"),
        "sensor_frame": _text(frames, "sensor", "ros.frames.sensor"),
        "body_from_sensor": _transform(ros.get("body_from_sensor"), "ros.body_from_sensor"),
        "sync_tolerance_ns": tolerance_ns,
        "pose_buffer_size": pose_buffer_size,
        "max_message_clock_offset_ns": clock_offset_ns,
        "timestamp_rollback_tolerance_ns": rollback_ns,
        "default_slice_duration_ns": default_duration_ns,
        "min_slice_duration_ns": minimum_duration_ns,
        "max_slice_duration_ns": maximum_duration_ns,
        "late_arrival_ns": late_arrival_ns,
        "min_range_m": minimum_range,
        "max_range_m": maximum_range,
        "min_voxel_size_m": _number(
            preprocess.get("min_voxel_size_m"), "preprocess.min_voxel_size_m"
        ),
        "max_cloud_rate_hz": _number(
            preprocess.get("max_cloud_rate_hz"), "preprocess.max_cloud_rate_hz"
        ),
        "input_timeout_seconds": input_timeout,
        "command_cache_seconds": command_cache,
        "clock_skew_tolerance_ns": clock_skew_ns,
        "start_late_tolerance_ns": start_late_ns,
        "minimum_start_lead_ns": minimum_lead_ns,
        "max_participant_devices": max_participants,
        "max_frame_points": max_frame_points,
        "max_decompressed_bytes": max_decompressed,
        "max_slice_frames": max_slice_frames,
        "max_slice_points": max_slice_points,
        "max_slice_bytes": max_slice_bytes,
    }


def load_config(mapping_path, device_path):
    return load_payloads(_read_yaml(mapping_path), _read_yaml(device_path))
