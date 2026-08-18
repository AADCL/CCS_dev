# edge_side_pkg

`edge_side_pkg` 是配合地面站指挥控制系统部署到端侧设备的 ROS 功能包目录。它不是单独的 ROS 包，而是便于一起复制到 catkin 工作空间的部署容器。

命名规则：通用包目录使用 `EPGeneral_<function>`，ROS/catkin 包名使用全小写 `epgeneral_<function>`。设备专属扩展使用 `EPDQUAV_`、`EPUGV_`、`EPQRD_`、`EPDATUGV_` 或 `EPAGUAV_` 目录前缀，对应 ROS 前缀为 `epdquav_`、`epugv_`、`epqrd_`、`epdatugv_`、`epaguav_`。

地面站当前为 v0.13.1；已保存地图的 PCD/PGM 同步融合完全在地面站本地执行，不修改端侧协议或包版本。v0.13.0 在 `ccs-map-stream-v1` 中定义的可选 PGM 文件服务仍尚未由 `epgeneral_map_stream` v0.1.0 实现。

本次新增独立版本的端侧 `epgeneral_task_control` v0.1.0，不修改既有协议字段或其他端侧包版本。

## 包含内容

- `EPQRD_go2_bridge` / `epqrd_go2_bridge` v0.1.0：将 Go2 EDU Unitree SDK2 状态转换为带设备前缀的标准 ROS 电池、IMU、里程计、心跳和诊断话题。
- `deploy/go2_edu`：Go2 EDU 基础监控套件配置、统一 bringup 和部署指南。

- `epgeneral_device_config` v0.1.0：保存端侧设备 ID/IP 的共享配置。地面站 `config/devices.json` 必须有同 ID 且 IP 相同的记录。
- `mqtav` v0.3.0：订阅 MAVROS 状态和电池信息，并向地面站 MQTT Broker 发布 presence、heartbeat、status。
- `epgeneral_usb_cam_rtsp` v0.1.0：启动 ROS USB 摄像头图像链路，并通过 GStreamer 提供 `rtsp://<device.ip>:8554/usb_cam`。
- `epgeneral_udp_telemetry` v0.2.1：按配置订阅 MAVROS/ROS 位姿、IMU、点云、地图生成状态和建图模式，以 20/5/1 Hz 向地面站 UDP 14560 发送分级遥测。
- `epgeneral_map_stream` v0.1.0：监听 UDP 14561 建图指令，同步预处理 PointCloud2 与位姿，并向地面站 UDP 14562 上传分片点云、同步位姿和静态外参。
- `epgeneral_task_control` v0.1.0：监听 UDP 14563 任务指令，原子保存 XML，通过 ROS 强类型接口协调执行，并向 UDP 14564 回传状态和进度。
- `EPGeneral_mqtav.zip`：包含 mqtav 与共享配置包的部署归档。

## v0.8.0 实时建图接口

地面站与 `epgeneral_map_stream` v0.1.0 已实现 `ccs-map-stream-v1`：地面站向端侧 UDP 14561 发送开始/停止建图指令，并在 UDP 14562 接收同步位姿、外参和分片 XYZ 点云。`epgeneral_udp_telemetry` 仍只负责 UDP 14560 状态遥测，建图点云不进入遥测协议。

双方实现遵循根目录 `docs/EDGE_DEVICE_INTERFACES.md`，包括 MessagePack 信封、1400 字节数据报上限、CRC32、zlib、`map <- body <- sensor` 坐标约定、ACK 幂等和超时处理。

## v0.11.0 联合建图兼容扩展

地面站可同时为多台设备建立独立 `ccs-map-stream-v1` 会话，并在 `start_mapping` 中增加可选 `job_id`、`role` 和 `primary_device_id`。`epgeneral_map_stream` v0.1.0 会忽略额外字段，仍可作为单机或独立子会话上传点云；主从外参、实时预览和最终插件融合均由地面站处理。后续端侧版本可读取这些字段以展示联合任务关系，但不得改变现有点云载荷和坐标契约。

## v0.9.0 任务接口状态

地面站新增 `ccs-task-control-v1`，向端侧 UDP 14563 下发轨迹任务，并在 UDP 14564 接收 ACK、任务心跳、状态和航点进度。完整字段、时钟约束与错误码见根目录 `docs/EDGE_DEVICE_INTERFACES.md`。

`epgeneral_task_control` v0.1.0 已实现该协议、XML 轨迹持久化、ID 状态机、UTC 调度及 ROS command/feedback 边界。它不直接操作 MAVROS；真实运动必须由设备专属控制节点实现包内消息接口。其余端侧包版本保持不变。

## 部署

```bash
mkdir -p ~/catkin_ws/src
cp -r edge_side_pkg ~/catkin_ws/src/
cd ~/catkin_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

修改 `epgeneral_device_config/config/device.yaml` 后，使用 `roslaunch epgeneral_mqtav epgeneral_mqtav.launch`、`roslaunch epgeneral_usb_cam_rtsp epgeneral_usb_cam_rtsp.launch`、`roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch destination_host:=<地面站IP>`、`roslaunch epgeneral_map_stream epgeneral_map_stream.launch` 和 `roslaunch epgeneral_task_control epgeneral_task_control.launch` 启动对应功能。任务网络、XML 目录和 ROS 适配话题位于 `epgeneral_task_control/config/task_control.yaml`。

每次新增或更新 Python ROS 包后，执行 `catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3` 并在启动 roslaunch 的同一终端执行 `source devel/setup.bash`。

端侧默认面向 Ubuntu 18.04、ROS Melodic、Python 3.6.9 和可信局域网，不提供 MQTT/RTSP/UDP 认证、TLS、录制、可靠重传或下行控制。
