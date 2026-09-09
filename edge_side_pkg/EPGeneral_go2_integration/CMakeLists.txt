cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_go2_integration)
find_package(catkin REQUIRED)
catkin_package()
catkin_install_python(PROGRAMS scripts/go2_stack_guard.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})
install(DIRECTORY launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})

if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test/test_go2_stack_guard.py)
endif()
