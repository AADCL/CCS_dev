import hashlib
import ipaddress
import io
import json

import yaml


LEVEL_RATES = {1: 20.0, 2: 5.0, 3: 1.0}
ALLOWED_TYPES = {"pose", "imu", "pointcloud_status", "availability", "text_status"}


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


def load_config(telemetry_path, device_path):
    telemetry = _read_yaml(telemetry_path)
    device_config = _read_yaml(device_path)
    if telemetry.get("schema_version") != 1 or device_config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    device = device_config.get("device")
    if not isinstance(device, dict) or not device.get("id") or not device.get("ip"):
        raise ConfigError("device config requires device.id and device.ip")
    try:
        ipaddress.ip_address(device["ip"])
    except ValueError as exc:
        raise ConfigError("device.ip is invalid") from exc
    network = telemetry.get("network")
    descriptors = telemetry.get("descriptors")
    if not isinstance(network, dict) or not isinstance(descriptors, list):
        raise ConfigError("telemetry config requires network and descriptors")
    try:
        port = int(network["destination_port"])
        max_bytes = int(network.get("max_datagram_bytes", 16384))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError("invalid network settings") from exc
    if not 1 <= port <= 65535 or not 512 <= max_bytes <= 65507:
        raise ConfigError("invalid UDP port or datagram limit")
    names = set()
    normalized = []
    for index, item in enumerate(descriptors):
        if not isinstance(item, dict):
            raise ConfigError("descriptor %d must be a mapping" % index)
        name = item.get("name")
        data_type = item.get("type")
        level = item.get("level")
        source = item.get("source")
        if not isinstance(name, str) or not name or name in names:
            raise ConfigError("descriptor name is empty or duplicated: %s" % name)
        if data_type not in ALLOWED_TYPES or level not in LEVEL_RATES:
            raise ConfigError("descriptor %s has invalid type or level" % name)
        file_source = isinstance(source, dict) and source.get("kind") == "pgm_file"
        if not isinstance(item.get("display_name"), str) or not isinstance(source, dict) or (
                not file_source and not source.get("topic")):
            raise ConfigError("descriptor %s requires display_name and a valid source" % name)
        if file_source and (data_type != "availability" or not source.get("state_file")
                            or not source.get("map_root")):
            raise ConfigError("descriptor %s pgm_file source is invalid" % name)
        if data_type in {"pose", "imu", "text_status"} and not source.get("message_type"):
            raise ConfigError("descriptor %s requires source.message_type" % name)
        names.add(name)
        normalized.append(item)
    return {
        "device_id": str(device["id"]),
        "device_ip": str(device["ip"]),
        "protocol_id": str(telemetry.get("protocol_id", "")),
        "destination_host": str(network.get("destination_host", "")),
        "destination_port": port,
        "max_datagram_bytes": max_bytes,
        "descriptors": normalized,
        "descriptor_hash": descriptor_hash(normalized),
    }


def descriptor_hash(descriptors):
    common = []
    for item in sorted(descriptors, key=lambda value: value["name"]):
        common.append({
            "display_name": item["display_name"],
            "level": item["level"],
            "name": item["name"],
            "type": item["type"],
        })
    encoded = json.dumps(common, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
