cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_multi_map_fusion)

if(NOT PYTHON_EXECUTABLE OR PYTHON_EXECUTABLE MATCHES "(^|/)python2(\\.7)?$")
  find_program(MULTI_MAP_FUSION_PYTHON3_EXECUTABLE NAMES python3.8 python3.6 python3)
  if(MULTI_MAP_FUSION_PYTHON3_EXECUTABLE)
    set(PYTHON_EXECUTABLE "${MULTI_MAP_FUSION_PYTHON3_EXECUTABLE}" CACHE FILEPATH "Multi-map fusion Python interpreter" FORCE)
  endif()
endif()
find_package(PythonInterp 3.6 REQUIRED)
find_package(catkin REQUIRED)

catkin_python_setup()
catkin_package()

catkin_install_python(PROGRAMS scripts/epgeneral_multi_map_fusion_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})

install(DIRECTORY config launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})

if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test)
endif()
