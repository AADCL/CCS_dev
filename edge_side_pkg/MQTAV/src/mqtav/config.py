"""Python 3.6 compatible configuration loading and validation for MQTAV."""

import ipaddress
from pathlib import Path


class ConfigError(ValueError):
    """Raised when config.yaml cannot safely describe a node deployment."""


class DeviceConfig(object):
    def __init__(self, device_id, ip_address):
        self.device_id = device_id
        self.ip_address = ip_address


class MqttConfig(object):
    def __init__(
        self,
        ground_station_ip,
        port,
        client_id_prefix,
        qos,
        keepalive_seconds,
        heartbeat_hz,
        telemetry_hz,
        topics,
    ):
        self.ground_station_ip = ground_station_ip
        self.port = port
        self.client_id_prefix = client_id_prefix
        self.qos = qos
        self.keepalive_seconds = keepalive_seconds
        self.heartbeat_hz = heartbeat_hz
        self.telemetry_hz = telemetry_hz
        self.topics = topics


class RosTopicConfig(object):
    def __init__(self, topic, message_type):
        self.topic = topic
        self.message_type = message_type


class MissionConfig(object):
    def __init__(self, enabled, topic=None, message_type=None, field_path=None):
        self.enabled = enabled
        self.topic = topic
        self.message_type = message_type
        self.field_path = field_path


class RosConfig(object):
    def __init__(self, node_name, state, battery, mission):
        self.node_name = node_name
        self.state = state
        self.battery = battery
        self.mission = mission


class AppConfig(object):
    def __init__(self, device, mqtt, ros):
        self.device = device
        self.mqtt = mqtt
        self.ros = ros

    def topic(self, name):
        return self.mqtt.topics[name].format(device_id=self.device.device_id)

    @property
    def client_id(self):
        return "{0}{1}".format(self.mqtt.client_id_prefix, self.device.device_id)


def _mapping(value, path):
    if not isinstance(value, dict):
        raise ConfigError("{0} must be a mapping".format(path))
    return value


def _string(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0} must be a non-empty string".format(path))
    return value.strip()


def _integer(value, path, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError("{0} must be an integer from {1} to {2}".format(path, minimum, maximum))
    return value


def _frequency(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= 100:
        raise ConfigError("{0} must be a number greater than 0 and no greater than 100".format(path))
    return float(value)


def _ip(value, path):
    text = _string(value, path)
    try:
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        raise ConfigError("{0} must be a valid IPv4 or IPv6 address".format(path)) from exc


def _topic_config(value, path):
    data = _mapping(value, path)
    topic = _string(data.get("topic"), "{0}.topic".format(path))
    message_type = _string(data.get("message_type"), "{0}.message_type".format(path))
    if not topic.startswith("/"):
        raise ConfigError("{0}.topic must start with '/'".format(path))
    if message_type.count("/") != 1:
        raise ConfigError("{0}.message_type must use package/Message syntax".format(path))
    return RosTopicConfig(topic, message_type)


def _load_yaml(path):
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError("PyYAML is required; install python3-yaml") from exc
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError("cannot read {0}: {1}".format(path, exc)) from exc
    return _mapping(data, "root")


def load_device_config(path):
    """Load the shared edge-side device identity file."""
    data = _load_yaml(Path(path))
    if data.get("schema_version") != 1:
        raise ConfigError("device config schema_version must be 1")
    device_data = _mapping(data.get("device"), "device")
    return DeviceConfig(
        _string(device_data.get("id"), "device.id"),
        _ip(device_data.get("ip"), "device.ip"),
    )


def load_config(path, device_config_path):
    """Load MQTAV and shared device configs and reject unsafe deployments."""
    data = _load_yaml(Path(path))
    device = load_device_config(device_config_path)

    mqtt_data = _mapping(data.get("mqtt"), "mqtt")
    topics_data = _mapping(mqtt_data.get("topics"), "mqtt.topics")
    topics = {}
    for name in ("presence", "heartbeat", "status"):
        topic = _string(topics_data.get(name), "mqtt.topics.{0}".format(name))
        if "+" in topic or "#" in topic:
            raise ConfigError("mqtt.topics.{0} must not contain MQTT wildcards".format(name))
        try:
            topics[name] = topic.format(device_id=device.device_id)
        except (KeyError, ValueError) as exc:
            raise ConfigError("mqtt.topics.{0} only supports {{device_id}}".format(name)) from exc
    mqtt = MqttConfig(
        _ip(mqtt_data.get("ground_station_ip"), "mqtt.ground_station_ip"),
        _integer(mqtt_data.get("port"), "mqtt.port", 1, 65535),
        _string(mqtt_data.get("client_id_prefix"), "mqtt.client_id_prefix"),
        _integer(mqtt_data.get("qos"), "mqtt.qos", 0, 1),
        _integer(mqtt_data.get("keepalive_seconds"), "mqtt.keepalive_seconds", 1, 3600),
        _frequency(mqtt_data.get("heartbeat_hz"), "mqtt.heartbeat_hz"),
        _frequency(mqtt_data.get("telemetry_hz"), "mqtt.telemetry_hz"),
        topics,
    )

    ros_data = _mapping(data.get("ros"), "ros")
    mission_data = _mapping(ros_data.get("mission", {"enabled": False}), "ros.mission")
    enabled = mission_data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("ros.mission.enabled must be true or false")
    if enabled:
        mission_topic = _topic_config(mission_data, "ros.mission")
        mission = MissionConfig(
            True,
            mission_topic.topic,
            mission_topic.message_type,
            _string(mission_data.get("field_path"), "ros.mission.field_path"),
        )
    else:
        mission = MissionConfig(False)
    ros = RosConfig(
        _string(ros_data.get("node_name"), "ros.node_name"),
        _topic_config(ros_data.get("state"), "ros.state"),
        _topic_config(ros_data.get("battery"), "ros.battery"),
        mission,
    )
    return AppConfig(device, mqtt, ros)
