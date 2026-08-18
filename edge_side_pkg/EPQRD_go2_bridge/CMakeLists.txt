cmake_minimum_required(VERSION 3.10)
project(epqrd_go2_bridge)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(catkin REQUIRED COMPONENTS diagnostic_msgs nav_msgs roscpp sensor_msgs std_msgs)
find_package(unitree_sdk2 REQUIRED)

catkin_package(CATKIN_DEPENDS diagnostic_msgs nav_msgs roscpp sensor_msgs std_msgs)

include_directories(${catkin_INCLUDE_DIRS})
add_executable(epqrd_go2_bridge_node src/go2_bridge_node.cpp)
target_link_libraries(epqrd_go2_bridge_node ${catkin_LIBRARIES} unitree_sdk2)
add_dependencies(epqrd_go2_bridge_node ${catkin_EXPORTED_TARGETS})

install(TARGETS epqrd_go2_bridge_node RUNTIME DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})
install(DIRECTORY config launch DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
catkin_install_python(PROGRAMS scripts/check_version.py DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})
