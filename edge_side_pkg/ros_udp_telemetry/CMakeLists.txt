cmake_minimum_required(VERSION 3.0.2)
project(ros_udp_telemetry)

if(NOT PYTHON_EXECUTABLE OR PYTHON_EXECUTABLE MATCHES "(^|/)python2(\\.7)?$")
  find_program(ROS_UDP_PYTHON3_EXECUTABLE NAMES python3.6 python3)
  if(ROS_UDP_PYTHON3_EXECUTABLE)
    set(PYTHON_EXECUTABLE "${ROS_UDP_PYTHON3_EXECUTABLE}" CACHE FILEPATH "ROS UDP Python interpreter" FORCE)
  endif()
endif()
find_package(PythonInterp 3.6 REQUIRED)
if(PYTHON_VERSION_STRING VERSION_LESS "3.6")
  message(FATAL_ERROR "ros_udp_telemetry requires Python 3.6 or newer")
endif()
set(PYTHON_EXECUTABLE "${PYTHON_EXECUTABLE}" CACHE FILEPATH "ROS UDP Python interpreter" FORCE)

find_package(catkin REQUIRED COMPONENTS rospy roslib geometry_msgs nav_msgs sensor_msgs std_msgs edge_device_config)

catkin_python_setup()
catkin_package(CATKIN_DEPENDS rospy roslib geometry_msgs nav_msgs sensor_msgs std_msgs edge_device_config)

catkin_install_python(PROGRAMS scripts/ros_udp_telemetry_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})

install(DIRECTORY config launch
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})

if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test)
endif()
