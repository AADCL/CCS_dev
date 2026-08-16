"""Python 3.6 compatible ROS subscription boundary for MAVROS telemetry."""

from .config import RosTopicConfig


def read_field(message, field_path):
    """Read a dotted ROS message field path without evaluating user input."""
    value = message
    for field in field_path.split("."):
        if not field:
            raise ValueError("field path contains an empty segment")
        if isinstance(value, dict):
            value = value[field]
        else:
            value = getattr(value, field)
    return value


def default_message_resolver(message_type):
    try:
        from roslib.message import get_message_class
    except ImportError as exc:
        raise RuntimeError("ROS roslib is required") from exc
    message_class = get_message_class(message_type)
    if message_class is None:
        raise RuntimeError("ROS message type is unavailable: {0}".format(message_type))
    return message_class


class RosBridge(object):
    def __init__(self, config, health, logger, rospy_module, message_resolver=default_message_resolver):
        self._config = config
        self._health = health
        self._logger = logger
        self._rospy = rospy_module
        self._message_resolver = message_resolver
        self._subscriptions = []

    def _subscribe(self, spec, callback, label):
        message_class = self._message_resolver(spec.message_type)
        self._subscriptions.append(self._rospy.Subscriber(spec.topic, message_class, callback, queue_size=10))
        self._logger.info("ros_subscribed stream=%s topic=%s type=%s", label, spec.topic, spec.message_type)

    def start(self):
        self._subscribe(self._config.ros.state, self._on_state, "state")
        self._subscribe(self._config.ros.battery, self._on_battery, "battery")
        mission = self._config.ros.mission
        if mission.enabled:
            self._subscribe(RosTopicConfig(mission.topic or "", mission.message_type or ""), self._on_mission, "mission")

    def _on_state(self, message):
        self._health.update_state(
            getattr(message, "connected", None),
            getattr(message, "armed", None),
            getattr(message, "system_status", None),
            getattr(message, "mode", None),
        )

    def _on_battery(self, message):
        self._health.update_battery(
            getattr(message, "percentage", None),
            getattr(message, "voltage", None),
            getattr(message, "current", None),
        )

    def _on_mission(self, message):
        try:
            self._health.update_mission(read_field(message, self._config.ros.mission.field_path or ""))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._health.update_mission(None)
            self._logger.warning("mission_status_unavailable error=%s", exc)
