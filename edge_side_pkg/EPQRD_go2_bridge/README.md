# epqrd_go2_bridge

<!-- epqrd_go2_bridge_VERSION: 0.1.0 -->

`epqrd_go2_bridge` is a read-only Unitree SDK2 bridge for Go2 EDU on Ubuntu 20.04 and ROS Noetic. It does not send motion commands.

The interface was checked against Unitree SDK2 commit `ce14ddccbc29fe6b54ad736c89f01849f0093834`.

## Build

Install Unitree SDK2 to `/opt/unitree_robotics`, then build the catkin workspace:

```bash
sudo apt install ros-noetic-ros-base ros-noetic-diagnostic-msgs ros-noetic-nav-msgs ros-noetic-sensor-msgs
export CMAKE_PREFIX_PATH=/opt/unitree_robotics:${CMAKE_PREFIX_PATH}
catkin_make --force-cmake -DCMAKE_BUILD_TYPE=Release
source devel/setup.bash
```

Configure the DDS network interface in `config/go2.yaml`, then run:

```bash
roslaunch epqrd_go2_bridge epqrd_go2_bridge.launch
rostopic hz /qrd/QRD_001/imu
rostopic echo /qrd/QRD_001/link/sdk
```

The package subscribes to SDK2 `rt/lowstate` and `rt/sportmodestate`. The published odometry is local robot odometry, not a globally referenced map pose. Unitree SDK2 remains an external BSD-3-Clause dependency and is not vendored by this repository.
