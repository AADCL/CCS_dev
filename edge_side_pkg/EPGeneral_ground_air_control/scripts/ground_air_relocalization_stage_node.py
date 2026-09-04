#!/usr/bin/env python3
"""Acquire the resident Ground-Air relocalization stage for one CCS session."""

import rospy
from ground_air_msgs.srv import SetSystemStage

from epgeneral_ground_air_control.stage_bridge import RelocalizationStageBridge


def main():
    rospy.init_node("ccs_relocalization_stage", anonymous=True)
    map_id = rospy.get_param("~map_id")
    service_wait_timeout = float(rospy.get_param("~service_wait_timeout", 90.0))
    relocalize_timeout = float(rospy.get_param("~relocalize_timeout", 60.0))
    rospy.wait_for_service(
        "/ground_air/system/set_stage", timeout=min(service_wait_timeout, 30.0)
    )
    service = rospy.ServiceProxy(
        "/ground_air/system/set_stage", SetSystemStage, persistent=True
    )
    bridge = RelocalizationStageBridge(
        service, map_id, relocalize_timeout,
        lambda message: rospy.loginfo("%s", message),
    )
    rospy.on_shutdown(bridge.release)
    bridge.acquire()
    rospy.spin()


if __name__ == "__main__":
    main()
