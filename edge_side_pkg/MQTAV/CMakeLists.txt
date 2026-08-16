cmake_minimum_required(VERSION 3.0.2)
project(mqtav)

# ROS Melodic selects Python 2 by default. catkin build may preload that
# interpreter in its per-package CMake cache, so replace a Python 2 cache
# value before FindPythonInterp validates the required Python 3.6 runtime.
if(NOT PYTHON_EXECUTABLE OR PYTHON_EXECUTABLE MATCHES "(^|/)python2(\\.7)?$")
  find_program(MQTAV_PYTHON3_EXECUTABLE NAMES python3.6 python3)
  if(MQTAV_PYTHON3_EXECUTABLE)
    set(PYTHON_EXECUTABLE "${MQTAV_PYTHON3_EXECUTABLE}" CACHE FILEPATH "MQTAV Python interpreter" FORCE)
  endif()
endif()

# MQTAV is intentionally built with Python 3.6+.
find_package(PythonInterp 3.6 REQUIRED)
if(PYTHON_VERSION_STRING VERSION_LESS "3.6")
  message(FATAL_ERROR "MQTAV requires Python 3.6 or newer; set PYTHON_EXECUTABLE to /usr/bin/python3")
endif()
set(PYTHON_EXECUTABLE "${PYTHON_EXECUTABLE}" CACHE FILEPATH "MQTAV Python interpreter" FORCE)

find_package(catkin REQUIRED COMPONENTS
  edge_device_config
  mavros_msgs
  roslib
  rospy
  sensor_msgs
  std_msgs
)

catkin_python_setup()

catkin_package(CATKIN_DEPENDS edge_device_config mavros_msgs roslib rospy sensor_msgs std_msgs)

catkin_install_python(PROGRAMS
  scripts/mqtav_node.py
  scripts/check_version.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY config launch
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test)
endif()
