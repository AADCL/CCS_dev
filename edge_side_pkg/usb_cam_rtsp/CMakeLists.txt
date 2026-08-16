cmake_minimum_required(VERSION 3.0.2)
project(usb_cam_rtsp)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(catkin REQUIRED COMPONENTS
  cv_bridge
  edge_device_config
  image_transport
  roscpp
  sensor_msgs
)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GSTREAMER REQUIRED
  gstreamer-1.0
  gstreamer-app-1.0
  gstreamer-rtsp-server-1.0
)

catkin_package(
  CATKIN_DEPENDS cv_bridge edge_device_config image_transport roscpp sensor_msgs
)

include_directories(
  ${catkin_INCLUDE_DIRS}
  ${GSTREAMER_INCLUDE_DIRS}
)
link_directories(${GSTREAMER_LIBRARY_DIRS})
add_definitions(${GSTREAMER_CFLAGS_OTHER})

add_executable(usb_cam_rtsp_node src/usb_cam_rtsp_node.cpp)
target_link_libraries(usb_cam_rtsp_node ${catkin_LIBRARIES} ${GSTREAMER_LIBRARIES})
add_dependencies(usb_cam_rtsp_node ${catkin_EXPORTED_TARGETS})

install(TARGETS usb_cam_rtsp_node
  RUNTIME DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
install(DIRECTORY config launch
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
