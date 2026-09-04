cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_ground_air_control)

find_package(catkin REQUIRED COMPONENTS
  geometry_msgs
  ground_air_msgs
  rosnode
  rospy
  std_msgs
  tf2_ros
)

catkin_python_setup()
catkin_package(CATKIN_DEPENDS
  geometry_msgs
  ground_air_msgs
  rosnode
  rospy
  std_msgs
  tf2_ros
)

catkin_install_python(PROGRAMS
  scripts/ground_air_initial_pose_adapter_node.py
  scripts/ground_air_relocalization_stage_node.py
  scripts/ground_air_stage_manager_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
