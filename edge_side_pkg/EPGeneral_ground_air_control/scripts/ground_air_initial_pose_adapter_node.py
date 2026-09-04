#!/usr/bin/env python3
"""Load the selected map and translate /initialpose into Ground-Air services."""

import rospy
from geometry_msgs.msg import PoseWithCovarianceStamped
from ground_air_msgs.srv import LoadMap, Relocalize

from epgeneral_ground_air_control.initial_pose_adapter import InitialPoseAdapter


def main():
    rospy.init_node("ground_air_initial_pose_adapter")
    map_id = rospy.get_param("~map_id")
    wait_timeout = float(rospy.get_param("~service_wait_timeout", 90.0))
    relocalize_timeout = float(rospy.get_param("~relocalize_timeout", 60.0))
    rospy.wait_for_service("/ground_air/load_map", timeout=wait_timeout)
    rospy.wait_for_service("/ground_air/relocalize", timeout=wait_timeout)
    adapter = InitialPoseAdapter(
        map_id,
        rospy.ServiceProxy("/ground_air/load_map", LoadMap),
        rospy.ServiceProxy("/ground_air/relocalize", Relocalize),
        relocalize_timeout,
        lambda message: rospy.loginfo("%s", message),
    )
    adapter.load()

    def handle(message):
        try:
            adapter.handle_initial_pose(message)
        except Exception as error:
            rospy.logerr("Ground-Air relocalization failed: %s", error)

    rospy.Subscriber(
        "/initialpose", PoseWithCovarianceStamped, handle, queue_size=1
    )
    rospy.spin()


if __name__ == "__main__":
    main()
