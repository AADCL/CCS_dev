# edge_side_pkg

`edge_side_pkg` 是配合地面站指挥控制系统部署到端侧设备的 ROS 功能包目录。它不是单独的 ROS 包，而是便于一起复制到 catkin 工作空间的部署容器。

命名规则：通用包目录使用 `EPGeneral_<function>`，ROS/catkin 包名使用全小写 `epgeneral_<function>`。设备专属扩展使用 `EPDQUAV_`、`EPUGV_`、`EPQRD_`、`EPDATUGV_` 或 `EPAGUAV_` 目录前缀，对应 ROS 前缀为 `epdquav_`、`epugv_`、`epqrd_`、`epdatugv_`、`epaguav_`。

地面站当前为 v0.18.1；视频链路要求端侧使用 `epgeneral_video_srt` v0.1.0。`epgeneral_map_stream` v0.6.0 使用 HTTP PCD 分片实时预览、成果新鲜度校验、无成果强制结束与成果 ZIP 服务。

地面站修改设备 ID 后，必须同步修改端侧共享 `device.yaml` 并重启 MQTT、UDP、建图、任务和视频节点。v0.15.1 只修复地面站融合算法的可迁移路径；v0.15.0 的展示与本地引用调整以及现有端侧协议字段、功能包版本均保持不变。

本次建图修复将 `epgeneral_map_stream` 升级至 v0.6.0；`epgeneral_mqtav` 保持 v0.3.1，其他端侧包版本和协议保持不变。

## 包含内容

- `EPQRD_go2_bridge` / `epqrd_go2_bridge` v0.1.0：将 Go2 EDU Unitree SDK2 状态转换为带设备前缀的标准 ROS 电池、IMU、里程计、心跳和诊断话题。
- `deploy/go2_edu`：Go2 EDU 基础监控套件配置、统一 bringup 和部署指南。
  bringup 与一键脚本会显式向各节点传入同一 profile；安装脚本时需同步安装
  `config/*.yaml`，并用 `ground_station_ip`/`CCS_GROUND_STATION_IP` 设置地面站地址。

- `epgeneral_device_config` v0.1.0：保存端侧设备 ID/IP 的共享配置。地面站 `config/devices.json` 必须有同 ID 且 IP 相同的记录。
- `epgeneral_mqtav` v0.3.1：订阅 ROS 状态和电池信息，并向地面站 MQTT Broker 发布带启动 session 的 presence、heartbeat、status。
- `epgeneral_video_srt` v0.1.0：订阅配置的 ROS 原始或压缩图像话题，并通过 GStreamer 以 SRT Listener 输出 baseline H.264/MPEG-TS，默认 UDP 9000。
- `epgeneral_udp_telemetry` v0.2.1：按配置订阅 MAVROS/ROS 位姿、IMU、点云、地图生成状态和建图模式，以 20/5/1 Hz 向地面站 UDP 14560 发送分级遥测。
- `epgeneral_map_stream` v0.6.0：检查 Livox 点云/IMU，控制和恢复 FAST_LIO 会话，通过 TCP 14600 提供实时 PCD 分片和最终 PCD/PGM/YAML ZIP，并拒绝当前 session 未更新的旧源 PCD。
- `epgeneral_task_control` v0.1.0：监听 UDP 14563 任务指令，原子保存 XML，通过 ROS 强类型接口协调执行，并向 UDP 14564 回传状态和进度。
- `epgeneral_mqtav.zip`：包含 `epgeneral_mqtav` 与共享配置包的部署归档。

## v0.8.0 实时建图接口

地面站 v0.18.1 与 `epgeneral_map_stream` v0.6.0 使用独立 `ccs-map-stream-v2`：UDP 14561/14562 传输控制、状态、PCD 描述符与 ACK，实时分片和最终 ZIP 均通过端侧 TCP 14600 下载。

双方实现遵循根目录 `docs/EDGE_DEVICE_INTERFACES.md`，包括 MessagePack 信封、1400 字节数据报上限、CRC32、zlib、`map <- body <- sensor` 坐标约定、ACK 幂等和超时处理。

## v0.11.0 联合建图兼容扩展

历史 v1 后端支持多设备独立会话及可选 `job_id`、`role`、`primary_device_id` 字段；v0.5.0 端侧运行路径使用 v2 单机协商，不读取这些 v1 扩展字段。

## v0.9.0 任务接口状态

地面站新增 `ccs-task-control-v1`，向端侧 UDP 14563 下发轨迹任务，并在 UDP 14564 接收 ACK、任务心跳、状态和航点进度。完整字段、时钟约束与错误码见根目录 `docs/EDGE_DEVICE_INTERFACES.md`。

`epgeneral_task_control` v0.1.0 已实现该协议、XML 轨迹持久化、ID 状态机、UTC 调度及 ROS command/feedback 边界。它不直接操作 MAVROS；真实运动必须由设备专属控制节点实现包内消息接口。其余端侧包版本保持不变。

## 部署

```bash
mkdir -p ~/catkin_ws/src
cp -r edge_side_pkg ~/catkin_ws/src/
cd ~/catkin_ws
source /opt/ros/noetic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

修改 `epgeneral_device_config/config/device.yaml` 后，使用 `roslaunch epgeneral_mqtav epgeneral_mqtav.launch`、`roslaunch epgeneral_video_srt epgeneral_video_srt.launch`、`roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch destination_host:=<地面站IP>`、`roslaunch epgeneral_map_stream epgeneral_map_stream.launch` 和 `roslaunch epgeneral_task_control epgeneral_task_control.launch` 启动对应功能。视频启动前执行 `gst-inspect-1.0 srtsink`，并放行 UDP 9000。

每次新增或更新 Python ROS 包后，执行 `catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3` 并在启动 roslaunch 的同一终端执行 `source devel/setup.bash`。

端侧视频运行基线为 Ubuntu 20.04、ROS Noetic、GStreamer 1.16+；系统运行于可信局域网，不提供 MQTT/SRT/UDP 认证、TLS、录制或通用可靠重传。
