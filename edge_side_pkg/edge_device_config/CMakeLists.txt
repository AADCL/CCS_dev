cmake_minimum_required(VERSION 3.0.2)
project(edge_device_config)

find_package(catkin REQUIRED)
catkin_package()

install(DIRECTORY config
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
