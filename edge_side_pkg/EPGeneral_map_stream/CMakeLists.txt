cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_map_stream)

if(NOT PYTHON_EXECUTABLE OR PYTHON_EXECUTABLE MATCHES "(^|/)python2(\\.7)?$")
  find_program(epgeneral_map_stream_PYTHON3_EXECUTABLE NAMES python3.6 python3)
  if(epgeneral_map_stream_PYTHON3_EXECUTABLE)
    set(PYTHON_EXECUTABLE "${epgeneral_map_stream_PYTHON3_EXECUTABLE}" CACHE FILEPATH "ROS map stream Python interpreter" FORCE)
  endif()
endif()
find_package(PythonInterp 3.6 REQUIRED)
if(PYTHON_VERSION_STRING VERSION_LESS "3.6")
  message(FATAL_ERROR "epgeneral_map_stream requires Python 3.6 or newer")
endif()
set(PYTHON_EXECUTABLE "${PYTHON_EXECUTABLE}" CACHE FILEPATH "ROS map stream Python interpreter" FORCE)

find_package(catkin REQUIRED COMPONENTS rospy roslib sensor_msgs nav_msgs epgeneral_device_config)

catkin_python_setup()
catkin_package(CATKIN_DEPENDS rospy roslib sensor_msgs nav_msgs epgeneral_device_config)

catkin_install_python(PROGRAMS scripts/epgeneral_map_stream_node.py scripts/check_version.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})

install(PROGRAMS scripts/start_fast_lio.sh scripts/stop_fast_lio.sh
  scripts/abort_fast_lio.sh scripts/generate_pgm.sh
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/scripts)

install(DIRECTORY config launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})

if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test)
endif()
