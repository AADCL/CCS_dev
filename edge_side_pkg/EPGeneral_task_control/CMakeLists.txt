cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_task_control)

if(NOT PYTHON_EXECUTABLE OR PYTHON_EXECUTABLE MATCHES "(^|/)python2(\\.7)?$")
  find_program(epgeneral_task_control_PYTHON3_EXECUTABLE NAMES python3.6 python3)
  if(epgeneral_task_control_PYTHON3_EXECUTABLE)
    set(PYTHON_EXECUTABLE "${epgeneral_task_control_PYTHON3_EXECUTABLE}" CACHE FILEPATH "ROS task control Python interpreter" FORCE)
  endif()
endif()
find_package(PythonInterp 3.6 REQUIRED)
find_package(catkin REQUIRED COMPONENTS message_generation rospy geometry_msgs std_msgs nav_msgs actionlib move_base_msgs tf2_ros tf2_geometry_msgs epgeneral_device_config)

catkin_python_setup()
add_message_files(FILES TaskExecutionCommand.msg TaskExecutionFeedback.msg)
generate_messages(DEPENDENCIES geometry_msgs)
catkin_package(CATKIN_DEPENDS message_runtime rospy geometry_msgs std_msgs nav_msgs actionlib move_base_msgs tf2_ros tf2_geometry_msgs epgeneral_device_config)

catkin_install_python(PROGRAMS scripts/epgeneral_task_control_node.py
  scripts/navigation_adapter_node.py scripts/scout_navigation_adapter_node.py scripts/check_version.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})
install(DIRECTORY config launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test)
endif()
