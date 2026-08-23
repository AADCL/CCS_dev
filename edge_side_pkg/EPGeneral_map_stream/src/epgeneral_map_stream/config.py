import ipaddress
import io
import math
import os
import string

import yaml


class ConfigError(ValueError):
    pass


TEMPLATE_FIELDS = {
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
        if (isinstance(item, bool) or not isinstance(item, (int, float))
                or not math.isfinite(float(item))):
            raise ConfigError("%s.%s must be finite" % (name, key))
        result[key] = float(item)
    norm = math.sqrt(sum(result[key] * result[key]
                         for key in ("qx", "qy", "qz", "qw")))
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
    if any(field not in TEMPLATE_FIELDS for field in fields):
        raise ConfigError("%s uses an unsupported template field" % name)
    return fields


def _template(value, name, require_session_dir=False):
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ConfigError("%s must be a non-empty string" % name)
    fields = _template_fields(text, name)
    if require_session_dir and "session_dir" not in fields:
        raise ConfigError("%s must be located below {session_dir}" % name)
    return text


def _template_list(value, name):
    if not isinstance(value, list):
        raise ConfigError("%s must be a list" % name)
    result = []
    for index, item in enumerate(value):
        result.append(_template(item, "%s[%d]" % (name, index)))
    return result


def _topic(parent, name, expected_type=None, with_frame=True):
    data = _mapping(parent, name, "ros.%s" % name)
    message_type = _text(data, "message_type", "ros.%s.message_type" % name)
    if expected_type is not None and message_type != expected_type:
        raise ConfigError("ros.%s.message_type must be %s" % (name, expected_type))
    result = {
        "topic": _text(data, "topic", "ros.%s.topic" % name),
        "message_type": message_type,
    }
    if with_frame:
        result["frame"] = _text(data, "frame", "ros.%s.frame" % name)
    return result


def load_config(mapping_path, device_path):
    mapping = _read_yaml(mapping_path)
    device_config = _read_yaml(device_path)
    if mapping.get("schema_version") != 4:
        raise ConfigError("mapping schema_version must be 4")
    if device_config.get("schema_version") != 1:
        raise ConfigError("device schema_version must be 1")
    device = _mapping(device_config, "device")
    device_id = _text(device, "id", "device.id")
    device_ip = _ip(_text(device, "ip", "device.ip"), "device.ip")

    network = _mapping(mapping, "network")
    http = _mapping(mapping, "http")
    ros = _mapping(mapping, "ros")
    inputs = _mapping(ros, "inputs", "ros.inputs")
    stream = _mapping(ros, "stream", "ros.stream")
    frames = _mapping(ros, "frames", "ros.frames")
    integrations = _mapping(mapping, "integrations")
    prerequisites = _mapping(
        integrations, "mapping_prerequisites",
        "integrations.mapping_prerequisites")
    fast_lio = _mapping(integrations, "fast_lio", "integrations.fast_lio")
    pgm = _mapping(integrations, "pgm", "integrations.pgm")
    sync = _mapping(mapping, "sync")
    preprocess = _mapping(mapping, "preprocess")
    timeouts = _mapping(mapping, "timeouts")
    limits = _mapping(mapping, "limits")
    artifacts = _mapping(mapping, "artifacts")

    protocol_id = _text(mapping, "protocol_id")
    if protocol_id != "ccs-map-stream-v2":
        raise ConfigError("protocol_id must be ccs-map-stream-v2")
    lidar = _topic(inputs, "lidar")
    if lidar["message_type"] not in (
            "sensor_msgs/PointCloud2", "livox_ros_driver2/CustomMsg"):
        raise ConfigError(
            "ros.inputs.lidar.message_type must be sensor_msgs/PointCloud2 "
            "or livox_ros_driver2/CustomMsg")
    imu = _topic(inputs, "imu", "sensor_msgs/Imu")
    cloud = _topic(stream, "cloud", "sensor_msgs/PointCloud2")
    pose = _topic(stream, "pose", "nav_msgs/Odometry", with_frame=False)
    pose_data = _mapping(stream, "pose", "ros.stream.pose")
    cloud_data = _mapping(stream, "cloud", "ros.stream.cloud")
    cloud_coordinates = _text(cloud_data, "coordinates", "ros.stream.cloud.coordinates")
    if cloud_coordinates not in ("map", "sensor"):
        raise ConfigError("ros.stream.cloud.coordinates must be map or sensor")

    minimum_range = _positive_number(
        preprocess.get("min_range_m"), "preprocess.min_range_m", True)
    maximum_range = _positive_number(
        preprocess.get("max_range_m"), "preprocess.max_range_m")
    if minimum_range >= maximum_range:
        raise ConfigError("preprocess range must satisfy min < max")
    tolerance = _positive_number(sync.get("tolerance_seconds"), "sync.tolerance_seconds")
    if tolerance > 1.0:
        raise ConfigError("sync.tolerance_seconds must not exceed 1 second")
    pose_buffer_size = _positive_integer(
        sync.get("pose_buffer_size"), "sync.pose_buffer_size", 2, 10000)
    max_points = _positive_integer(limits.get("max_frame_points"), "limits.max_frame_points")
    max_window_points = _positive_integer(
        limits.get("max_window_points"), "limits.max_window_points")
    if max_window_points < max_points:
        raise ConfigError("limits.max_window_points must cover max_frame_points")
    max_decompressed = _positive_integer(
        limits.get("max_decompressed_bytes"), "limits.max_decompressed_bytes")
    if max_decompressed < max_points * 12:
        raise ConfigError("limits.max_decompressed_bytes must cover max_frame_points * 12")
    workspace_root = os.path.abspath(os.path.expanduser(
        _text(artifacts, "workspace_root", "artifacts.workspace_root")))
    package_root = os.path.abspath(os.path.dirname(os.path.dirname(mapping_path)))
    script_root = os.path.join(package_root, "scripts")

    return {
        "schema_version": 4,
        "protocol_id": protocol_id,
        "capability_version": "0.7.2",
        "device_id": device_id,
        "device_ip": device_ip,
        "bind_host": _ip(network.get("bind_host"), "network.bind_host", True),
        "control_port": _port(network.get("control_port"), "network.control_port"),
        "ground_station_ip": _ip(
            network.get("ground_station_ip"), "network.ground_station_ip"),
        "data_port": _port(network.get("data_port"), "network.data_port"),
        "max_datagram_bytes": _positive_integer(
            network.get("max_datagram_bytes"), "network.max_datagram_bytes", 512, 1400),
        "http_bind_host": _ip(http.get("bind_host"), "http.bind_host", True),
        "http_port": _port(http.get("port"), "http.port"),
        "http_token_ttl_seconds": _positive_number(
            http.get("token_ttl_seconds"), "http.token_ttl_seconds"),
        "input_cloud_topic": lidar["topic"],
        "input_cloud_message_type": lidar["message_type"],
        "input_cloud_frame": lidar["frame"],
        "input_imu_topic": imu["topic"],
        "input_imu_message_type": imu["message_type"],
        "input_imu_frame": imu["frame"],
        "cloud_topic": cloud["topic"],
        "cloud_message_type": cloud["message_type"],
        "cloud_frame": cloud["frame"],
        "cloud_coordinates": cloud_coordinates,
        "pose_topic": pose["topic"],
        "pose_message_type": pose["message_type"],
        "pose_position_path": _text(
            pose_data, "position_path", "ros.stream.pose.position_path"),
        "pose_orientation_path": _text(
            pose_data, "orientation_path", "ros.stream.pose.orientation_path"),
        "map_frame": _text(frames, "map", "ros.frames.map"),
        "body_frame": _text(frames, "body", "ros.frames.body"),
        "sensor_frame": _text(frames, "sensor", "ros.frames.sensor"),
        "body_from_sensor": _transform(
            ros.get("body_from_sensor"), "ros.body_from_sensor"),
        "prerequisite_setup_file": os.path.abspath(os.path.expanduser(
            _text(prerequisites, "setup_file",
                  "integrations.mapping_prerequisites.setup_file"))),
        "prerequisite_launch_file": _text(
            prerequisites, "launch_file",
            "integrations.mapping_prerequisites.launch_file"),
        "extrinsics_file": os.path.abspath(os.path.expanduser(
            _text(prerequisites, "extrinsics_file",
                  "integrations.mapping_prerequisites.extrinsics_file"))),
        "prerequisite_startup_timeout_seconds": _positive_number(
            prerequisites.get("startup_timeout_seconds"),
            "integrations.mapping_prerequisites.startup_timeout_seconds"),
        "fast_lio_setup_file": os.path.abspath(os.path.expanduser(
            _text(fast_lio, "setup_file", "integrations.fast_lio.setup_file"))),
        "fast_lio_package": _text(
            fast_lio, "package", "integrations.fast_lio.package"),
        "fast_lio_launch_file": _text(
            fast_lio, "launch_file", "integrations.fast_lio.launch_file"),
        "fast_lio_launch_args": _template_list(
            fast_lio.get("launch_args"), "integrations.fast_lio.launch_args"),
        "fast_lio_startup_timeout_seconds": _positive_number(
            fast_lio.get("startup_timeout_seconds"),
            "integrations.fast_lio.startup_timeout_seconds"),
        "fast_lio_stop_timeout_seconds": _positive_number(
            fast_lio.get("stop_timeout_seconds"),
            "integrations.fast_lio.stop_timeout_seconds"),
        "fast_lio_pid_template": _template(
            fast_lio.get("pid_path"), "integrations.fast_lio.pid_path", True),
        "fast_lio_log_template": _template(
            fast_lio.get("log_path"), "integrations.fast_lio.log_path", True),
        "pgm_setup_file": os.path.abspath(os.path.expanduser(
            _text(pgm, "setup_file", "integrations.pgm.setup_file"))),
        "pgm_package": _text(pgm, "package", "integrations.pgm.package"),
        "pgm_launch_file": _text(pgm, "launch_file", "integrations.pgm.launch_file"),
        "pgm_launch_args": _template_list(
            pgm.get("launch_args"), "integrations.pgm.launch_args"),
        "pgm_generation_timeout_seconds": _positive_number(
            pgm.get("generation_timeout_seconds"),
            "integrations.pgm.generation_timeout_seconds"),
        "pgm_log_template": _template(
            pgm.get("log_path"), "integrations.pgm.log_path", True),
        "start_fast_lio_script": os.path.join(script_root, "start_fast_lio.sh"),
        "stop_fast_lio_script": os.path.join(script_root, "stop_fast_lio.sh"),
        "abort_fast_lio_script": os.path.join(script_root, "abort_fast_lio.sh"),
        "generate_pgm_script": os.path.join(script_root, "generate_pgm.sh"),
        "sync_tolerance_seconds": tolerance,
        "pose_buffer_size": pose_buffer_size,
        "sample_window_seconds": _positive_number(
            preprocess.get("sample_window_seconds"), "preprocess.sample_window_seconds"),
        "preview_transport": _text(
            preprocess, "preview_transport", "preprocess.preview_transport"),
        "min_range_m": minimum_range,
        "max_range_m": maximum_range,
        "voxel_size_m": _positive_number(
            preprocess.get("voxel_size_m"), "preprocess.voxel_size_m"),
        "prepare_probe_timeout_seconds": _positive_number(
            timeouts.get("prepare_probe_timeout_seconds"),
            "timeouts.prepare_probe_timeout_seconds"),
        "integration_check_timeout_seconds": _positive_number(
            timeouts.get("integration_check_timeout_seconds"),
            "timeouts.integration_check_timeout_seconds"),
        "ready_timeout_seconds": _positive_number(
            timeouts.get("ready_timeout_seconds"), "timeouts.ready_timeout_seconds"),
        "input_timeout_seconds": _positive_number(
            timeouts.get("input_timeout_seconds"), "timeouts.input_timeout_seconds"),
        "command_cache_seconds": _positive_number(
            timeouts.get("command_cache_seconds"), "timeouts.command_cache_seconds"),
        "artifact_generation_timeout_seconds": _positive_number(
            pgm.get("generation_timeout_seconds"),
            "integrations.pgm.generation_timeout_seconds") + 30.0,
        "artifact_poll_seconds": _positive_number(
            timeouts.get("artifact_poll_seconds"), "timeouts.artifact_poll_seconds"),
        "artifact_stable_polls": _positive_integer(
            timeouts.get("artifact_stable_polls"), "timeouts.artifact_stable_polls", 2, 100),
        "max_frame_points": max_points,
        "max_window_points": max_window_points,
        "max_decompressed_bytes": max_decompressed,
        "max_artifact_bytes": _positive_integer(
            limits.get("max_artifact_bytes"), "limits.max_artifact_bytes",
            1024, 16 * 1024 ** 3),
        "min_free_bytes": _positive_integer(
            limits.get("min_free_bytes"), "limits.min_free_bytes", 1024),
        "command_output_bytes": _positive_integer(
            limits.get("command_output_bytes"), "limits.command_output_bytes",
            256, 1024 * 1024),
        "max_preview_fragment_bytes": _positive_integer(
            limits.get("max_preview_fragment_bytes"),
            "limits.max_preview_fragment_bytes", 1024, 1024 ** 3),
        "max_pending_preview_fragments": _positive_integer(
            limits.get("max_pending_preview_fragments"),
            "limits.max_pending_preview_fragments", 1, 64),
        "max_unacked_preview_fragments": _positive_integer(
            limits.get("max_unacked_preview_fragments"),
            "limits.max_unacked_preview_fragments", 1, 256),
        "workspace_root": workspace_root,
        "generated_pcd_path": os.path.abspath(os.path.expanduser(
            _text(artifacts, "generated_pcd_path", "artifacts.generated_pcd_path"))),
        "source_pcd_path": os.path.abspath(os.path.expanduser(
            _text(artifacts, "source_pcd_path", "artifacts.source_pcd_path"))),
        "source_pgm_path": os.path.abspath(os.path.expanduser(
            _text(artifacts, "source_pgm_path", "artifacts.source_pgm_path"))),
        "source_yaml_path": os.path.abspath(os.path.expanduser(
            _text(artifacts, "source_yaml_path", "artifacts.source_yaml_path"))),
        "archive_root": os.path.abspath(os.path.expanduser(
            _text(artifacts, "archive_root", "artifacts.archive_root"))),
        "pcd_template": _template(
            artifacts.get("pcd_path"), "artifacts.pcd_path", True),
        "pgm_template": _template(
            artifacts.get("pgm_path"), "artifacts.pgm_path", True),
        "yaml_template": _template(
            artifacts.get("yaml_path"), "artifacts.yaml_path", True),
    }


def command_context(config, values):
    context = dict(values)
    session_dir = os.path.abspath(values["session_dir"])
    for key, template_key in (
            ("fast_lio_pid_path", "fast_lio_pid_template"),
            ("fast_lio_log_path", "fast_lio_log_template"),
            ("pgm_log_path", "pgm_log_template")):
        path = os.path.abspath(config[template_key].format(**values))
        try:
            inside = os.path.commonpath([path, session_dir]) == session_dir
        except (AttributeError, ValueError):
            inside = path == session_dir or path.startswith(session_dir + os.sep)
        if not inside:
            raise ConfigError("%s escapes session directory" % template_key)
        context[key] = path
    return context


def build_integration_commands(config, values):
    context = command_context(config, values)
    fast_args = [item.format(**context) for item in config["fast_lio_launch_args"]]
    pgm_args = [item.format(**context) for item in config["pgm_launch_args"]]
    return {
        "check_fast_lio": [
            config["start_fast_lio_script"], "--check",
            config["prerequisite_setup_file"], config["extrinsics_file"],
            config["fast_lio_setup_file"],
            "epgeneral_map_stream", config["prerequisite_launch_file"],
            config["fast_lio_package"], config["fast_lio_launch_file"],
            config["generated_pcd_path"],
        ],
        "check_pgm": [
            config["generate_pgm_script"], "--check", config["pgm_setup_file"],
            config["pgm_package"], config["pgm_launch_file"],
            config["source_pcd_path"],
        ],
        "start_fast_lio": [
            config["start_fast_lio_script"],
            config["prerequisite_setup_file"], config["extrinsics_file"],
            str(config["prerequisite_startup_timeout_seconds"]),
            config["fast_lio_setup_file"],
            "epgeneral_map_stream", config["prerequisite_launch_file"],
            config["fast_lio_package"], config["fast_lio_launch_file"],
            context["fast_lio_pid_path"], context["fast_lio_log_path"],
            config["generated_pcd_path"],
        ] + fast_args,
        "stop_fast_lio": [
            config["stop_fast_lio_script"], config["fast_lio_setup_file"],
            context["fast_lio_pid_path"], config["generated_pcd_path"],
            context["pcd_path"],
            str(config["fast_lio_stop_timeout_seconds"]),
        ],
        "abort_fast_lio": [
            config["abort_fast_lio_script"], context["fast_lio_pid_path"],
            str(config["fast_lio_stop_timeout_seconds"]),
        ],
        "generate_pgm": [
            config["generate_pgm_script"], config["pgm_setup_file"],
            config["pgm_package"], config["pgm_launch_file"],
            context["pcd_path"], context["pgm_path"], context["yaml_path"],
            context["pgm_log_path"], str(config["pgm_generation_timeout_seconds"]),
            config["source_pcd_path"], config["source_pgm_path"],
            config["source_yaml_path"], config["archive_root"],
        ] + pgm_args,
    }
