import ipaddress
import io
import math
import os
import string

import yaml


class ConfigError(ValueError):
    pass


COMMAND_FIELDS = {
    "map_id", "device_id", "session_id", "session_dir",
    "pcd_path", "pgm_path", "yaml_path",
}


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
        raise ConfigError("%s must be a valid port" % name)
    return value


def _positive_number(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be numeric" % name)
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise ConfigError("%s must be finite and positive" % name)
    return number


def _positive_integer(value, name, minimum=1, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError("%s is invalid" % name)
    if maximum is not None and value > maximum:
        raise ConfigError("%s is out of range" % name)
    return value


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


def _template_fields(value, name):
    try:
        fields = [field for unused_literal, field, unused_spec, unused_conversion
                  in string.Formatter().parse(value) if field]
    except ValueError as exc:
        raise ConfigError("%s has an invalid template" % name) from exc
    if any(field not in COMMAND_FIELDS for field in fields):
        raise ConfigError("%s uses an unsupported template field" % name)
    return fields


def _command(value, name):
    if not isinstance(value, list) or not value:
        raise ConfigError("%s must be a non-empty argument list" % name)
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ConfigError("%s[%d] must be a non-empty string" % (name, index))
        _template_fields(item, "%s[%d]" % (name, index))
        result.append(item)
    return result


def _artifact_template(value, name):
    text = _text({"value": value}, "value", name)
    fields = _template_fields(text, name)
    if "session_dir" not in fields:
        raise ConfigError("%s must be located below {session_dir}" % name)
    return text


def load_config(mapping_path, device_path):
    mapping = _read_yaml(mapping_path)
    device_config = _read_yaml(device_path)
    if mapping.get("schema_version") != 2:
        raise ConfigError("mapping schema_version must be 2")
    if device_config.get("schema_version") != 1:
        raise ConfigError("device schema_version must be 1")
    device = _mapping(device_config, "device")
    device_id = _text(device, "id", "device.id")
    device_ip = _ip(_text(device, "ip", "device.ip"), "device.ip")

    network = _mapping(mapping, "network")
    http = _mapping(mapping, "http")
    ros = _mapping(mapping, "ros")
    cloud = _mapping(ros, "cloud")
    pose = _mapping(ros, "pose")
    frames = _mapping(ros, "frames")
    sync = _mapping(mapping, "sync")
    preprocess = _mapping(mapping, "preprocess")
    timeouts = _mapping(mapping, "timeouts")
    limits = _mapping(mapping, "limits")
    artifacts = _mapping(mapping, "artifacts")
    commands = _mapping(mapping, "commands")

    protocol_id = _text(mapping, "protocol_id")
    if protocol_id != "ccs-map-stream-v2":
        raise ConfigError("protocol_id must be ccs-map-stream-v2")
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
    pose_buffer_size = _positive_integer(sync.get("pose_buffer_size"), "sync.pose_buffer_size", 2, 10000)
    max_points = _positive_integer(limits.get("max_frame_points"), "limits.max_frame_points")
    max_window_points = _positive_integer(limits.get("max_window_points"), "limits.max_window_points")
    if max_window_points < max_points:
        raise ConfigError("limits.max_window_points must cover max_frame_points")
    max_decompressed = _positive_integer(limits.get("max_decompressed_bytes"), "limits.max_decompressed_bytes")
    if max_decompressed < max_points * 12:
        raise ConfigError("limits.max_decompressed_bytes must cover max_frame_points * 12")
    workspace_root = os.path.abspath(os.path.expanduser(_text(artifacts, "workspace_root", "artifacts.workspace_root")))

    return {
        "schema_version": 2,
        "protocol_id": protocol_id,
        "capability_version": "0.2.0",
        "device_id": device_id,
        "device_ip": device_ip,
        "bind_host": _ip(network.get("bind_host"), "network.bind_host", True),
        "control_port": _port(network.get("control_port"), "network.control_port"),
        "ground_station_ip": _ip(network.get("ground_station_ip"), "network.ground_station_ip"),
        "data_port": _port(network.get("data_port"), "network.data_port"),
        "max_datagram_bytes": _positive_integer(network.get("max_datagram_bytes"), "network.max_datagram_bytes", 512, 1400),
        "http_bind_host": _ip(http.get("bind_host"), "http.bind_host", True),
        "http_port": _port(http.get("port"), "http.port"),
        "http_token_ttl_seconds": _positive_number(http.get("token_ttl_seconds"), "http.token_ttl_seconds"),
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
        "sample_window_seconds": _positive_number(preprocess.get("sample_window_seconds"), "preprocess.sample_window_seconds"),
        "min_range_m": minimum_range,
        "max_range_m": maximum_range,
        "voxel_size_m": _positive_number(preprocess.get("voxel_size_m"), "preprocess.voxel_size_m"),
        "prepare_probe_timeout_seconds": _positive_number(timeouts.get("prepare_probe_timeout_seconds"), "timeouts.prepare_probe_timeout_seconds"),
        "ready_timeout_seconds": _positive_number(timeouts.get("ready_timeout_seconds"), "timeouts.ready_timeout_seconds"),
        "input_timeout_seconds": _positive_number(timeouts.get("input_timeout_seconds"), "timeouts.input_timeout_seconds"),
        "command_cache_seconds": _positive_number(timeouts.get("command_cache_seconds"), "timeouts.command_cache_seconds"),
        "command_timeout_seconds": _positive_number(timeouts.get("command_timeout_seconds"), "timeouts.command_timeout_seconds"),
        "artifact_generation_timeout_seconds": _positive_number(timeouts.get("artifact_generation_timeout_seconds"), "timeouts.artifact_generation_timeout_seconds"),
        "artifact_poll_seconds": _positive_number(timeouts.get("artifact_poll_seconds"), "timeouts.artifact_poll_seconds"),
        "artifact_stable_polls": _positive_integer(timeouts.get("artifact_stable_polls"), "timeouts.artifact_stable_polls", 2, 100),
        "max_frame_points": max_points,
        "max_window_points": max_window_points,
        "max_decompressed_bytes": max_decompressed,
        "max_artifact_bytes": _positive_integer(limits.get("max_artifact_bytes"), "limits.max_artifact_bytes", 1024, 16 * 1024 ** 3),
        "min_free_bytes": _positive_integer(limits.get("min_free_bytes"), "limits.min_free_bytes", 1024),
        "command_output_bytes": _positive_integer(limits.get("command_output_bytes"), "limits.command_output_bytes", 256, 1024 * 1024),
        "workspace_root": workspace_root,
        "pcd_template": _artifact_template(artifacts.get("pcd_path"), "artifacts.pcd_path"),
        "pgm_template": _artifact_template(artifacts.get("pgm_path"), "artifacts.pgm_path"),
        "yaml_template": _artifact_template(artifacts.get("yaml_path"), "artifacts.yaml_path"),
        "start_command": _command(commands.get("start"), "commands.start"),
        "stop_command": _command(commands.get("stop"), "commands.stop"),
    }
