#!/usr/bin/env python3
"""ROS entry point for the Ground-Air AGV task adapter."""
import rospy

from epgeneral_task_control.config import load_config
from epgeneral_ground_air_control.task_adapter import GroundAirTaskAdapter
from epgeneral_task_control.msg import TaskExecutionCommand, TaskExecutionFeedback


def main():
    rospy.init_node("epgeneral_ground_air_task_adapter")
    task_config = rospy.get_param("~task_config_file", "")
    device_config = rospy.get_param("~device_config_file", "")
    if not task_config or not device_config:
        raise rospy.ROSInitException("task and device config paths are required")
    config = load_config(task_config, device_config)
    adapter_config = config.get("adapter", {})
    if adapter_config.get("type") != "ground_air":
        raise rospy.ROSInitException("adapter.type must be ground_air")
    merged = dict(config)
    merged.update(adapter_config)
    adapter = GroundAirTaskAdapter(
        rospy, merged, TaskExecutionCommand, TaskExecutionFeedback)
    adapter.start()
    rospy.loginfo("Ground-Air AGV task adapter started")
    rospy.spin()


if __name__ == "__main__":
    main()
