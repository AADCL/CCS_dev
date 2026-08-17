"""Python 3.6 compatible executable orchestration for mqtav."""

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .durable_logging import build_logger
from .mqtt_client import MqttPublisher
from .ros_bridge import RosBridge
from .state import HealthState
from .version import get_version


def default_config_path():
    source_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
    if source_path.is_file():
        return source_path


def default_device_config_path():
    source_path = Path(__file__).resolve().parents[3] / "EPGeneral_device_config" / "config" / "device.yaml"
    if source_path.is_file():
        return source_path
    try:
        import rospkg

        return Path(rospkg.RosPack().get_path("epgeneral_device_config")) / "config" / "device.yaml"
    except (ImportError, OSError, RuntimeError):
        return source_path
    try:
        import rospkg

        return Path(rospkg.RosPack().get_path("epgeneral_mqtav")) / "config" / "config.yaml"
    except (ImportError, OSError, RuntimeError):
        return source_path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Publish MAVROS health telemetry to MQTT")
    parser.add_argument("--config-file", default=str(default_config_path()), help="path to config.yaml")
    parser.add_argument(
        "--device-config-file",
        default=str(default_device_config_path()),
        help="path to shared edge device.yaml",
    )
    parser.add_argument("--log-dir", default="", help="directory for durable rotating logs")
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # roslaunch appends private remappings such as __name:=mqtav and
    # __log:=/home/user/.ros/log/...; they are consumed by ROS, not argparse.
    ros_args = [arg for arg in raw_args if arg.startswith("__") and ":=" in arg]
    filtered_args = [arg for arg in raw_args if arg not in ros_args]
    return parser.parse_args(filtered_args)


def run(argv=None, rospy_module=None):
    args = parse_args(argv)
    logger = build_logger(args.log_dir or None)
    try:
        config = load_config(args.config_file, args.device_config_file)
    except ConfigError as exc:
        logger.error(
            "configuration_invalid path=%s device_config=%s error=%s",
            args.config_file,
            args.device_config_file,
            exc,
        )
        print("mqtav configuration error: {0}".format(exc), file=sys.stderr)
        return 2
    logger.info("configuration_loaded path=%s device_config=%s", args.config_file, args.device_config_file)

    try:
        if rospy_module is None:
            import rospy as rospy_module
        rospy_module.init_node(config.ros.node_name, anonymous=False)
        health = HealthState(config.device)
        publisher = MqttPublisher(config, health, logger)
        bridge = RosBridge(config, health, logger, rospy_module)
        logger.info(
            "mqtav_starting version=%s config=%s device_id=%s",
            get_version(),
            args.config_file,
            config.device.device_id,
        )
        bridge.start()
        publisher.start()
        rospy_module.Timer(rospy_module.Duration(1.0 / config.mqtt.heartbeat_hz), lambda _event: publisher.publish_heartbeat())
        rospy_module.Timer(rospy_module.Duration(1.0 / config.mqtt.telemetry_hz), lambda _event: publisher.publish_status())
        rospy_module.on_shutdown(publisher.stop)
        rospy_module.spin()
        return 0
    except Exception:
        logger.exception("mqtav_fatal_error")
        return 1
