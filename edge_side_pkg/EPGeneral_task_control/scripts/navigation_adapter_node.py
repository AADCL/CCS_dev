#!/usr/bin/env python3
"""ROS entry point for the configurable ground-robot navigation adapter."""
from __future__ import absolute_import

import rospy

from epgeneral_task_control.config import load_config
from epgeneral_task_control.scout_adapter import NavigationAdapter
from epgeneral_task_control.msg import TaskExecutionCommand, TaskExecutionFeedback


def main():
    rospy.init_node("epgeneral_navigation_task_adapter")
    task_config = rospy.get_param("~task_config_file", "")
    device_config = rospy.get_param("~device_config_file", "")
    if not task_config or not device_config:
        raise rospy.ROSInitException("task and device config paths are required")
    config = load_config(task_config, device_config)
    adapter_config = config.get("adapter", {})
    required = (
        "active_map_state_file", "navigation_launch_package",
        "navigation_launch_file", "navigation_map_root", "navigation_map_yaml",
        "odom_topic", "navigation_action", "zero_velocity_topic",
    )
    missing = [key for key in required if not adapter_config.get(key)]
    if missing:
        raise rospy.ROSInitException(
            "navigation adapter config is missing: %s" % ", ".join(missing))
    merged = dict(config)
    merged.update(adapter_config)
    adapter = NavigationAdapter(
        rospy, merged, TaskExecutionCommand, TaskExecutionFeedback)
    adapter.start()
    rospy.loginfo("navigation task adapter started")
    rospy.spin()


if __name__ == "__main__":
    main()
