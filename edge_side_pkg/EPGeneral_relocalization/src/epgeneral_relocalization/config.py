from __future__ import absolute_import

import io

import yaml


class ConfigError(ValueError):
    pass


def load_config(path, device_path):
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        with io.open(device_path, "r", encoding="utf-8") as stream:
            device_data = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as exc:
        raise ConfigError("configuration cannot be read: %s" % exc)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    device = device_data.get("device", device_data) if isinstance(device_data, dict) else {}
    device_id = device.get("id") or device.get("device_id")
    if not device_id:
        raise ConfigError("device id is missing")
    try:
        network, storage, ros, stability = (
            data["network"], data["storage"], data["ros"], data["tf_stability"])
        result = {
            "protocol_id": str(data["protocol_id"]), "enabled": bool(data["enabled"]),
            "backend": str(data["backend"]), "device_id": str(device_id),
            "bind_host": str(network["bind_host"]), "control_port": int(network["control_port"]),
            "ground_station_ip": str(network["ground_station_ip"]),
            "status_port": int(network["status_port"]),
            "max_datagram_bytes": int(network["max_datagram_bytes"]),
            "map_root": str(storage["map_root"]),
            "active_map_state_file": str(storage.get(
                "active_map_state_file", "~/.ros/ccs_edge_dev/state/relocalization.json")),
            "max_artifact_bytes": int(storage["max_artifact_bytes"]),
            "download_timeout_seconds": float(storage["download_timeout_seconds"]),
            "map_frame": str(ros["map_frame"]), "odom_frame": str(ros["odom_frame"]),
            "initial_pose_topic": str(ros["initial_pose_topic"]),
            "map_topic": str(ros["map_topic"]),
            "startup_timeout_seconds": float(ros["startup_timeout_seconds"]),
            "stages": list(ros["stages"]),
            "tf_timeout_seconds": float(stability["timeout_seconds"]),
            "tf_sample_hz": float(stability["sample_hz"]),
            "tf_sample_count": int(stability["sample_count"]),
            "translation_tolerance_m": float(stability["translation_tolerance_m"]),
            "yaw_tolerance_deg": float(stability["yaw_tolerance_deg"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError("configuration field is invalid: %s" % exc)
    if result["backend"] not in ("scout_mini", "wheeltec_r550p", "go2_edu"):
        raise ConfigError("backend is unsupported")
    if any(not 1 <= result[key] <= 65535 for key in ("control_port", "status_port")):
        raise ConfigError("network port is invalid")
    if not 512 <= result["max_datagram_bytes"] <= 65507:
        raise ConfigError("max_datagram_bytes is invalid")
    if result["backend"] in ("scout_mini", "wheeltec_r550p") and not result["stages"]:
        raise ConfigError("relocalization stages are empty")
    if result["max_artifact_bytes"] <= 0 or result["download_timeout_seconds"] <= 0:
        raise ConfigError("storage limits are invalid")
    if (result["startup_timeout_seconds"] <= 0 or result["tf_timeout_seconds"] <= 0
            or result["tf_sample_hz"] <= 0 or result["tf_sample_count"] < 2
            or result["translation_tolerance_m"] <= 0
            or result["yaw_tolerance_deg"] <= 0):
        raise ConfigError("ROS readiness or TF stability limits are invalid")
    for stage in result["stages"]:
        if (not isinstance(stage, dict) or not stage.get("name")
                or not stage.get("package") or not stage.get("launch")
                or not isinstance(stage.get("args", []), list)):
            raise ConfigError("relocalization stage is invalid")
    return result
