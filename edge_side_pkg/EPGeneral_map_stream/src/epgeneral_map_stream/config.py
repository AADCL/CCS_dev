import ipaddress
import io
import math
import os
import re
import string

import yaml


class ConfigError(ValueError):
    pass


TEMPLATE_FIELDS = {
    "map_id", "device_id", "session_id", "session_dir",
    "pcd_path", "pgm_path", "yaml_path", "map_name",
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
    if mapping.get("schema_version") != 6:
        raise ConfigError("mapping schema_version must be 6")
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
    backend = integrations.get("backend", "go2_accumulator")
    if backend not in ("go2_accumulator", "scout_finalize", "managed_finalize"):
        raise ConfigError(
            "integrations.backend must be go2_accumulator, scout_finalize or managed_finalize")
    prerequisites = _mapping(
        integrations, "mapping_prerequisites",
        "integrations.mapping_prerequisites")
    fast_lio = _mapping(integrations, "fast_lio", "integrations.fast_lio")
    map_accumulator = _mapping(
        integrations, "map_accumulator", "integrations.map_accumulator")
    pgm = _mapping(integrations, "pgm", "integrations.pgm")
    integration_profile = integrations.get(
        "scout" if backend == "scout_finalize" else "managed", {})
    if backend in ("scout_finalize", "managed_finalize") and not isinstance(
            integration_profile, dict):
        raise ConfigError("managed finalize integration must be a mapping")
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
    preview_transform_timeout = _positive_number(
        sync.get("preview_transform_timeout_seconds"),
        "sync.preview_transform_timeout_seconds")
    if preview_transform_timeout > 5.0:
        raise ConfigError("sync.preview_transform_timeout_seconds must not exceed 5 seconds")
    map_frame = _text(frames, "map", "ros.frames.map")
    preview_frame = _text(frames, "preview", "ros.frames.preview")
    if backend == "go2_accumulator" and map_frame == preview_frame:
        raise ConfigError("ros.frames.map and ros.frames.preview must be different")
    map_accumulator_service = _text(
        map_accumulator, "service", "integrations.map_accumulator.service")
    if re.match(r"^/[A-Za-z0-9_/]+$", map_accumulator_service) is None:
        raise ConfigError("integrations.map_accumulator.service must be an absolute ROS service")
    map_save_timeout = _positive_number(
        map_accumulator.get("save_timeout_seconds"),
        "integrations.map_accumulator.save_timeout_seconds")
    if map_save_timeout > 600.0:
        raise ConfigError("integrations.map_accumulator.save_timeout_seconds must not exceed 600 seconds")
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
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    script_root = os.path.join(package_root, "scripts")

    return {
        "schema_version": 6,
        "protocol_id": protocol_id,
        "capability_version": "0.11.0",
        "integration_backend": backend,
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
        "map_frame": map_frame,
        "preview_frame": preview_frame,
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
        "map_accumulator_setup_file": os.path.abspath(os.path.expanduser(
            _text(map_accumulator, "setup_file",
                  "integrations.map_accumulator.setup_file"))),
        "map_accumulator_service": map_accumulator_service,
        "map_accumulator_save_timeout_seconds": map_save_timeout,
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
        "save_map_script": os.path.join(script_root, "save_map.sh"),
        "generate_pgm_script": os.path.join(script_root, "generate_pgm.sh"),
        "scout_mapping_script": os.path.join(script_root, "scout_mapping_stack.sh"),
        "scout_finalize_script": os.path.join(script_root, "scout_finalize_map.sh"),
        "sync_tolerance_seconds": tolerance,
        "pose_buffer_size": pose_buffer_size,
        "preview_transform_timeout_seconds": preview_transform_timeout,
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
        "accumulator_pcd_path": os.path.abspath(os.path.expanduser(
            _text(artifacts, "accumulator_pcd_path",
                  "artifacts.accumulator_pcd_path"))),
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
        "artifact_frame": str(artifacts.get("frame", map_frame)).strip(),
        "scout_fast_lio_package": str(integration_profile.get("fast_lio_package", "")).strip(),
        "scout_fast_lio_launch": str(integration_profile.get("fast_lio_launch", "")).strip(),
        "scout_mapper_package": str(integration_profile.get("mapper_package", "")).strip(),
        "scout_mapper_launch": str(integration_profile.get("mapper_launch", "")).strip(),
        "scout_tf_package": str(integration_profile.get("tf_package", "")).strip(),
        "scout_tf_launch": str(integration_profile.get("tf_launch", "")).strip(),
        "scout_pose_package": str(integration_profile.get("pose_package", "")).strip(),
        "scout_pose_launch": str(integration_profile.get("pose_launch", "")).strip(),
        "scout_finalize_package": str(integration_profile.get("finalize_package", "")).strip(),
        "scout_finalize_executable": str(integration_profile.get("finalize_executable", "")).strip(),
        "scout_filtered_pcd_filename": str(
            integration_profile.get("filtered_pcd_filename", "")).strip(),
        "scout_map_root": os.path.abspath(os.path.expanduser(
            str(integration_profile.get("map_root", "")))),
        "managed_fast_lio_node": str(
            integration_profile.get("fast_lio_node", "/laserMapping")).strip(),
        "managed_mapper_node": str(integration_profile.get(
            "mapper_node", "/scout_pointcloud_mapper")).strip(),
        "managed_tf_node": str(integration_profile.get(
            "tf_node", "/scout_tf_manager")).strip(),
        "managed_geometry_tf_node": str(integration_profile.get(
            "geometry_tf_node", "/scout_geometry_tf_publisher")).strip(),
        "managed_pose_node": str(integration_profile.get(
            "pose_node", "/scout_pose_adapter")).strip(),
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


def scout_filtered_pcd_path(config, map_name):
    if not isinstance(map_name, str) or not re.fullmatch(r"[0-9]{8}_[0-9]{6}", map_name):
        raise ConfigError("Scout map_name must use YYYYMMDD_HHMMSS")
    filename = config.get("scout_filtered_pcd_filename", "")
    if (not filename or filename in (".", "..")
            or filename != os.path.basename(filename)):
        raise ConfigError("integrations.scout.filtered_pcd_filename must be a file name")
    map_root = config.get("scout_map_root", "")
    map_directory = os.path.abspath(os.path.join(map_root, map_name))
    filtered_path = os.path.abspath(os.path.join(map_directory, filename))
    if os.path.dirname(filtered_path) != map_directory:
        raise ConfigError("Scout filtered PCD path escapes the map directory")
    return filtered_path


def build_integration_commands(config, values):
    context = command_context(config, values)
    if config["integration_backend"] in ("scout_finalize", "managed_finalize"):
        required = (
            "scout_fast_lio_package", "scout_fast_lio_launch", "scout_mapper_package",
            "scout_mapper_launch", "scout_tf_package",
            "scout_tf_launch", "scout_pose_package", "scout_pose_launch",
            "scout_finalize_package", "scout_finalize_executable",
            "scout_filtered_pcd_filename", "scout_map_root",
        )
        missing = [name for name in required if not config.get(name)]
        if missing:
            raise ConfigError("managed finalize fields are missing: %s" % ", ".join(missing))
        launch_args = [
            config["scout_fast_lio_package"], config["scout_fast_lio_launch"],
            config["scout_mapper_package"], config["scout_mapper_launch"],
            config["scout_tf_package"], config["scout_tf_launch"],
            config["scout_pose_package"], config["scout_pose_launch"],
        ]
        node_args = []
        if config["integration_backend"] == "managed_finalize":
            node_args = [
                config["managed_fast_lio_node"], config["managed_mapper_node"],
                config["managed_tf_node"], config["managed_geometry_tf_node"],
                config["managed_pose_node"],
            ]
            if any(not re.match(r"^/[A-Za-z0-9_/]+$", name) for name in node_args):
                raise ConfigError("managed finalize node names must be absolute ROS names")
        scout_filtered_pcd_path(config, context.get("map_name"))
        return {
            "checks": [[config["scout_mapping_script"], "--check"] + launch_args + node_args, [
                config["scout_finalize_script"], "--check",
                config["scout_finalize_package"], config["scout_finalize_executable"],
                config["scout_map_root"],
            ]],
            "start_fast_lio": [
                config["scout_mapping_script"], "--start", context["fast_lio_pid_path"],
                context["fast_lio_log_path"], str(config["fast_lio_startup_timeout_seconds"]),
                str(config["fast_lio_stop_timeout_seconds"]), context["map_name"],
            ] + launch_args + node_args,
            "stop_fast_lio": [
                config["scout_mapping_script"], "--stop", context["fast_lio_pid_path"],
                str(config["fast_lio_stop_timeout_seconds"]),
            ],
            "abort_fast_lio": [
                config["scout_mapping_script"], "--abort", context["fast_lio_pid_path"],
                str(config["fast_lio_stop_timeout_seconds"]),
            ],
            "generate_pgm": [
                config["scout_finalize_script"], config["scout_finalize_package"],
                config["scout_finalize_executable"], context["map_name"],
                config["scout_map_root"],
                context["pcd_path"], context["pgm_path"], context["yaml_path"],
                context["pgm_log_path"], str(config["pgm_generation_timeout_seconds"]),
            ],
        }
    fast_args = [item.format(**context) for item in config["fast_lio_launch_args"]]
    pgm_args = [item.format(**context) for item in config["pgm_launch_args"]]
    commands = {
        "check_fast_lio": [
            config["start_fast_lio_script"], "--check",
            config["prerequisite_setup_file"], config["extrinsics_file"],
            config["fast_lio_setup_file"],
            "epgeneral_map_stream", config["prerequisite_launch_file"],
            config["fast_lio_package"], config["fast_lio_launch_file"],
        ],
        "check_save_map": [
            config["save_map_script"], "--check",
            config["map_accumulator_setup_file"],
            config["map_accumulator_service"], config["accumulator_pcd_path"],
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
        ] + fast_args,
        "save_map": [
            config["save_map_script"], config["map_accumulator_setup_file"],
            config["map_accumulator_service"], config["accumulator_pcd_path"],
            str(config["map_accumulator_save_timeout_seconds"]),
        ],
        "stop_fast_lio": [
            config["stop_fast_lio_script"], config["fast_lio_setup_file"],
            context["fast_lio_pid_path"], config["accumulator_pcd_path"],
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
    commands["checks"] = [commands[name] for name in (
        "check_fast_lio", "check_save_map", "check_pgm")]
    return commands
