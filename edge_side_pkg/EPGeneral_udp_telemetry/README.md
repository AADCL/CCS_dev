# epgeneral_udp_telemetry

版本：v0.3.0。该 ROS Melodic/Noetic 包将 ROS 话题和受限地图文件状态转换为 CCS MessagePack UDP 遥测。

## 依赖与安装

- Ubuntu 18.04、ROS Melodic、Python 3.6.9
- `rospy`、`roslib`、`geometry_msgs`、`nav_msgs`、`sensor_msgs`
- `python3-yaml`、`python3-msgpack`
- 同工作空间中的 `epgeneral_device_config`

```bash
sudo apt update
sudo apt install python3-yaml python3-msgpack ros-melodic-geometry-msgs \
  ros-melodic-nav-msgs ros-melodic-sensor-msgs
cd ~/catkin_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

更新包后建议强制刷新 CMake 缓存并验证导入：

```bash
cd ~/c3po_ctrl_ws
source /opt/ros/melodic/setup.bash
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
python3 -c "import epgeneral_udp_telemetry; print(epgeneral_udp_telemetry.__version__)"
```

预期版本为 `0.3.0`。如果导入仍失败，检查当前终端的 `echo $ROS_PACKAGE_PATH` 和 `python3 -c 'import sys; print(sys.path)'` 是否包含该工作空间的 devel 路径。源码入口也提供同包 `src` 回退，但标准部署仍应完成 build 和 source。

## 配置

设备 ID/IP 来自 `epgeneral_device_config/config/device.yaml`。`config/telemetry.yaml` 设置地面站 IP、UDP 14560 端口以及每个数据项的名称、类型、等级和 ROS 来源。

- Level 1：20 Hz，`pose` 或 `imu`。
- Level 2：5 Hz，`pointcloud_status`，只发送接收状态、数据年龄和估算频率。
- Level 3：1 Hz，`availability` 按话题新鲜度判断可用性，`text_status` 读取并复用最近文本值。
- Pose 的 `source.mapping.position/orientation` 和 IMU 的三个 mapping 使用点分属性路径，可适配 PoseStamped、Odometry 或其他同结构消息。
- `name/display_name/type/level` 必须与地面站 `config/udp_telemetry.json` 完全一致，否则描述哈希校验会拒绝数据。
- `source.kind: pgm_file` 读取活动地图状态文件，只检查配置地图根目录下非符号链接的 `map.pgm`，不订阅 ROS 话题。

默认全局位姿为 `/mavros/local_position/pose` 的本地 ENU 米制坐标；姿态输出为 roll/pitch/yaw 角度。默认三级话题包括 Livox、FAST-LIO2、PGM、OctoMap、OccupancyGrid 和 `/mapping_mode`，均应按实际部署修改。

## 启动与验证

```bash
roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch destination_host:=192.168.151.100
```

可覆盖参数：

- `telemetry_config_file`
- `device_config_file`
- `destination_host`
- `destination_port`

验证话题和网络：

```bash
rostopic hz /mavros/local_position/pose
rostopic hz /mavros/imu/data
rostopic echo /epgeneral_udp_telemetry/diagnostics
sudo tcpdump -ni any udp port 14560
```

高频输入在每个发送窗口求均值；四元数先统一符号再归一化平均。`NaN/Inf`、非数值字段和无效四元数不会进入窗口，单项异常只产生该项 `valid=false`。低频输入会重复最近值并附带 `sample_age_seconds`。若地面站只有心跳而没有某项数据，检查 diagnostics 中对应 source 的 `accepted_count/rejected_count/last_rejection_reason`，再核对 topic、message type 和 mapping；若显示描述哈希不一致，同步两端描述配置。UDP 仅用于可信内网，不提供认证、加密、重传或拥塞控制。

节点在 `~link_status_topic` 发布 latched `std_msgs/Bool`。`~diagnostics_topic` 保留 `epgeneral_udp_telemetry/udp_tx`，并增加 `epgeneral_udp_telemetry/source/<name>`：前者报告目标、session、descriptor hash 和各等级发送统计，后者报告 ROS 来源、接收/有效/拒绝计数、最近样本年龄及拒绝原因。本机 `sendto` 成功不证明地面站已经收到数据；端到端状态仍由地面站 heartbeat 超时判断。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
```

纯 Python 测试覆盖配置、描述哈希、非法样本隔离、四元数平均、窗口均值、低频复用和点云元数据。动态 ROS 类型加载及实际 20/5/1 Hz 调度需在 ROS Melodic/Noetic 环境运行 launch 验证。
