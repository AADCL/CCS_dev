cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_relocalization)

find_package(catkin REQUIRED COMPONENTS epgeneral_device_config geometry_msgs map_server rospy tf2_ros)
catkin_python_setup()
catkin_package(CATKIN_DEPENDS epgeneral_device_config geometry_msgs map_server rospy tf2_ros)

catkin_install_python(PROGRAMS
  scripts/epgeneral_relocalization_node.py
  scripts/check_version.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
install(DIRECTORY launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
