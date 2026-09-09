#!/usr/bin/env python3
"""Validate installed GO2 configuration and launch dependencies without launching."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

PACKAGES = (
    "epgeneral_device_config", "epgeneral_mqtav", "epgeneral_udp_telemetry",
    "epgeneral_video_srt", "epgeneral_map_stream", "epgeneral_relocalization",
    "epgeneral_task_control", "epgeneral_go2_integration",
)


def validate_profile(profile, workspace, nav_workspace, device_ip, station_ip):
    import yaml
    config = {name: yaml.safe_load((profile / (name + ".yaml")).read_text(encoding="utf-8"))
              for name in ("device", "epgeneral_mqtav", "udp_telemetry", "video",
                           "map_stream", "relocalization", "task_control")}
    master = urlparse(os.environ.get("ROS_MASTER_URI", "http://127.0.0.1:11311"))
    if master.scheme != "http" or master.hostname not in ("localhost", "127.0.0.1", device_ip):
        raise ValueError("GO2 startup requires a ROS master on this device")
    if config["device"]["device"] != {"id": "QRD_003", "ip": device_ip}:
        raise ValueError("GO2 profile identity does not match startup environment")
    for name in ("map_stream", "relocalization", "task_control"):
        if config[name]["network"]["ground_station_ip"] != station_ip:
            raise ValueError(name + " ground station does not match startup environment")
    if config["epgeneral_mqtav"]["mqtt"]["ground_station_ip"] != station_ip:
        raise ValueError("MQTT ground station does not match startup environment")
    if config["udp_telemetry"]["network"]["destination_host"] != station_ip:
        raise ValueError("UDP ground station does not match startup environment")
    expected_connection = {"topic": "/go2/state/low_state", "message_type": "go2_control/Go2LowState",
                           "timeout_seconds": 3.0}
    if config["epgeneral_mqtav"]["ros"].get("connection") != expected_connection:
        raise ValueError("MQTT connection freshness must use native periodic low state")
    if config["task_control"]["timeouts"]["utc_tolerance_seconds"] != 2.0:
        raise ValueError("Task UTC tolerance must match the two-second startup time check")
    adapter = config["task_control"]["adapter"]
    if adapter["navigation_management"] != "attach":
        raise ValueError("Task adapter must attach to relocalization-owned navigation")
    expected_paths = {
        "emergency_stop_state_file": workspace / "run/state/go2_task_safety.json",
        "active_map_state_file": workspace / "run/state/relocalization.json",
        "navigation_map_root": workspace / "maps/download",
    }
    for key, expected in expected_paths.items():
        if Path(adapter[key]) != expected:
            raise ValueError("Unexpected task path: " + key)
    mapping = config["map_stream"]
    extrinsics = nav_workspace / "src/go2_core/config/extrinsics.yaml"
    if Path(mapping["integrations"]["mapping_prerequisites"]["extrinsics_file"]) != extrinsics:
        raise ValueError("Mapping extrinsics must reference this native workspace")
    yaml.safe_load(extrinsics.read_text(encoding="utf-8"))
    video = config["video"]
    if tuple(video[key] for key in ("output_width", "output_height", "framerate", "srt_port",
                                   "srt_latency_ms", "bitrate_kbps")) != (640, 480, 15, 9000, 120, 2500):
        raise ValueError("Video must match the observed USB 2.1 RGB profile")
    for name, value in config.items():
        serialized = json.dumps(value, ensure_ascii=False)
        if any(old in serialized for old in ("QRD_002", "192.168.50.111", "go2_robot2", "/vendor/", "/bin/ccs_go2")):
            raise ValueError("Historical deployment reference in " + name)
    return config


def validate_packages(workspace):
    import rospkg
    import roslib.message
    import msgpack
    import paho.mqtt.client
    from std_srvs.srv import SetBool, Trigger
    packages = rospkg.RosPack()
    src = (workspace / "src").resolve()
    for name in PACKAGES:
        path = Path(packages.get_path(name))
        if path.is_symlink() or src not in path.resolve().parents:
            raise ValueError(name + " must resolve to a physical directory under workspace/src")
        print("package {}={}".format(name, path))
    for type_name in ("go2_control/Go2LowState", "livox_ros_driver2/CustomMsg",
                      "epgeneral_task_control/TaskExecutionCommand", "sensor_msgs/Image"):
        if roslib.message.get_message_class(type_name) is None:
            raise ValueError("Missing generated ROS message: " + type_name)
    print("service contract reset={} md5={} enable={} md5={}".format(
        Trigger._type, Trigger._md5sum, SetBool._type, SetBool._md5sum))


def validate_launch(arguments):
    import roslaunch
    import roslib.packages
    path = roslaunch.rlutil.resolve_launch_arguments(arguments)[0]
    remappings = [arg for arg in arguments if ":=" in arg]
    launch = roslaunch.config.load_config_default([(path, remappings)], None, verbose=False)
    for node in launch.nodes:
        if not roslib.packages.find_node(node.package, node.type):
            raise ValueError("Missing executable {}/{}".format(node.package, node.type))
    print("launch {}: {} node executables resolved".format(path, len(launch.nodes)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--nav-workspace", type=Path)
    parser.add_argument("--device-ip", default="192.168.50.112")
    parser.add_argument("--station-ip", default="192.168.50.101")
    parser.add_argument("--launch", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.launch:
            validate_launch(args.launch)
        else:
            if not all((args.profile, args.workspace, args.nav_workspace)):
                parser.error("profile, workspace and nav-workspace are required")
            validate_profile(args.profile, args.workspace, args.nav_workspace, args.device_ip, args.station_ip)
            validate_packages(args.workspace)
        return 0
    except Exception as exc:
        print("GO2 preflight failed: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
