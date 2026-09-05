# WheelTech R550P CCS 端侧部署

CCS 0.23.1 当前入口：[使用手册](../../documents/USER_MANUAL.md) · [接口与配置](../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

本 profile 对应 `UGV_003`（`192.168.50.122`），运行于 Ubuntu 20.04、ROS Noetic 和 Jetson NX。CCS 工作空间为 `/home/nrc19/ccs_edge_ws`，并以 Overlay 方式只读依赖 `/home/nrc19/livox_fastlio` 中已验证的 WheelTech、Livox 和 FAST-LIO 包。

## 工作空间

部署以下 ROS 包到 `ccs_edge_ws/src`：

- `EPGeneral_device_config`
- `epgeneral_mqtav`
- `EPGeneral_udp_telemetry`
- `EPGeneral_video_srt`
- `EPGeneral_map_stream`
- `EPGeneral_relocalization`
- `EPGeneral_task_control`

只部署端侧七包 allowlist，不复制 `deploy` 与 `documents`。安装 `python3-msgpack`、`python3-paho-mqtt` 后执行：

```bash
cd /home/nrc19/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source /home/nrc19/livox_fastlio/devel/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make -j1 --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
```

profile 配置安装到 `/home/nrc19/ccs_edge_ws/config/wheeltec_r550p`，一键脚本安装到工作空间根目录。系统时间必须由 `192.168.50.101` 提供 NTP。

## 启动

```bash
cd /home/nrc19/ccs_edge_ws
./start_ccs_edge_dev.sh
```

脚本启动底盘、Livox、MQTT、UDP 遥测、建图协调、重定位协调和任务控制。设备未安装摄像头，视频包只编译部署，不由脚本启动。按 `Ctrl+C` 时脚本先发布零速度，再停止自己管理的进程。

运行日志位于 `/home/nrc19/.ros/ccs_edge_dev_wheeltec_r550p/log`。地图和下载地图仍位于 `/home/nrc19/livox_fastlio/maps`，这是 Overlay 布局的明确例外。

## 静态验证

```bash
rostopic hz /livox/lidar
rostopic hz /livox/imu
rostopic hz /odom
rostopic hz /imu
rostopic echo -n 1 /PowerVoltage
rosnode list
```

不得在无人值守或未准备急停时发送非零 `/cmd_vel`。本次部署只执行静态检查、零速度保护和协议链路验证。
