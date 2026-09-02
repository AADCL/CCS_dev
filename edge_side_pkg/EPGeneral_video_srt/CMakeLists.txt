cmake_minimum_required(VERSION 3.0.2)
project(epgeneral_video_srt)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(catkin REQUIRED COMPONENTS
  cv_bridge
  epgeneral_device_config
  image_transport
  roscpp
  sensor_msgs
)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GSTREAMER REQUIRED
  gstreamer-1.0
  gstreamer-app-1.0
)

catkin_package(
  CATKIN_DEPENDS cv_bridge epgeneral_device_config image_transport roscpp sensor_msgs
)

include_directories(
  ${catkin_INCLUDE_DIRS}
  ${GSTREAMER_INCLUDE_DIRS}
)
link_directories(${GSTREAMER_LIBRARY_DIRS})
add_definitions(${GSTREAMER_CFLAGS_OTHER})

add_executable(epgeneral_video_srt_node src/epgeneral_video_srt_node.cpp)
target_link_libraries(epgeneral_video_srt_node
  ${catkin_LIBRARIES}
  ${cv_bridge_LIBRARIES}
  ${GSTREAMER_LIBRARIES}
)
add_dependencies(epgeneral_video_srt_node ${catkin_EXPORTED_TARGETS})

install(TARGETS epgeneral_video_srt_node
  RUNTIME DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
install(DIRECTORY launch
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
