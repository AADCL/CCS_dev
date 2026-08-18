#!/usr/bin/env python3
import os

from epgeneral_multi_map.config import load_config
from epgeneral_multi_map.node import RosMultiMapNode


def run():
    import rospkg
    import rospy

    rospy.init_node("epgeneral_multi_map")
    packages = rospkg.RosPack()
    package_path = packages.get_path("epgeneral_multi_map")
    device_path = packages.get_path("epgeneral_device_config")
    mapping_file = rospy.get_param(
        "~mapping_config_file", os.path.join(package_path, "config", "multi_mapping.yaml"))
    device_file = rospy.get_param(
        "~device_config_file", os.path.join(device_path, "config", "device.yaml"))
    node = RosMultiMapNode(rospy, load_config(mapping_file, device_file))
    rospy.on_shutdown(node.close)
    node.start()
    rospy.loginfo("epgeneral_multi_map is listening on UDP %s:%d",
                  node.config["bind_host"], node.config["control_port"])
    rospy.spin()


if __name__ == "__main__":
    run()
