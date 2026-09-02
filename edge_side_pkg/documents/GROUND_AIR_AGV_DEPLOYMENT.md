# 空地 AGV 端侧部署说明

## 设备与边界

- 设备：`AGV_001`（金城空地无人机），端侧 IP `192.168.50.130`
- 地面站：`192.168.50.101`
- 系统：Jetson ARM64、Ubuntu 20.04、ROS Noetic
- CCS 工作空间：`/home/bitcq/ccs_edge_ws`
- 只读 underlay：`/opt/ros/noetic`、`/home/bitcq/catkin_ws`

本轮一键启动包含 MAVROS、Livox MID-360 驱动、MQTT 状态上报、UDP
遥测、A8 Mini 相机和 SRT 视频。不使用或修改 `/home/bitcq/start.sh`，
不启动 FAST-LIO、地面滤波、动态建图、重定位、导航、控制或任务节点。

## 部署内容

`ccs_edge_ws/src` 部署 7 个通用包：设备配置、MQTT、UDP 遥测、SRT 视频、
地图流、重定位和任务控制。Go2 专属桥接包不部署。除日志和必要的系统
NTP 配置外，新增配置、脚本、任务、地图占位目录和运行 PID 均位于
`ccs_edge_ws`。

MAVROS 使用系统二进制包和稳定串口：

```text
/dev/serial/by-id/usb-CUAV_PX4_CUAV_Nora_0-if00:57600
```

Livox 使用 `catkin_ws` 中已有的 `livox_ros_driver2`，启动
`launch_ROS1/msg_MID360.launch` 并设置 `msg_frame_id=base_link`。

A8 Mini 使用只读 underlay 中的 `a8_mini_camera`，实际 launch 参数名为
`camera_ip` 和 `image_topic`。相机地址为 `192.168.144.25`，ROS 图像话题
为 `/a8_cam/image_raw`。SRT 将图像编码为 1280x720、30 FPS、3000 kbps
baseline H.264/MPEG-TS，并在 `0.0.0.0:9000/UDP` 监听，延迟 120 ms。

## 构建与启动

```bash
cd /home/bitcq/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source /home/bitcq/catkin_ws/devel/setup.bash --extend
catkin_make -j1 --force-cmake -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3
./start_ccs_edge_dev.sh
```

脚本要求 systemd-timesyncd 已与 `192.168.50.101` 同步。日志位于
`~/.ros/ccs_edge_dev_ground_air_agv/log`，PID 位于 `ccs_edge_ws/run`。

## 静态验收

确认四个基础节点及 `/a8_mini_camera`、`/epgeneral_video_srt` 在线，并检查：

```bash
rostopic echo -n 1 /mavros/state
rostopic echo -n 1 /mavros/imu/data
rostopic echo -n 1 /mavros/battery
rostopic echo -n 1 /livox/lidar
rostopic echo -n 1 /livox/imu
rostopic echo -n 1 /agv/AGV_001/link/udp_tx
rostopic echo -n 1 /agv/AGV_001/diagnostics
rostopic type /a8_cam/image_raw
rostopic hz /a8_cam/image_raw
```

地面站 MQTT broker 应收到 `mqtav/AGV_001/presence`、
`mqtav/AGV_001/heartbeat` 和 `mqtav/AGV_001/status`；UDP 遥测发送至
`192.168.50.101:14560`。

地面站以 Caller 连接
`srt://192.168.50.130:9000?mode=caller&transtype=live&latency=120000`。
A8/SRT 属于可降级服务：30 秒无图像或视频节点异常时记录告警，但不会
停止 MAVROS、Livox、MQTT 和 UDP；相机恢复后会自动继续出流。

验收期间禁止解锁、模式切换、起飞、降落、非零速度、位置设定点和任务
目标。按 `Ctrl+C` 后检查六个节点和脚本管理的 ROS Master 均已清理。

## 后续占位

建图、重定位和任务 profile 已创建，但没有加入启动链。后续获得完整流程
后，必须重新核对传感器外参、地图产物、TF、适配器、安全停止和真实设备
验收要求，不能直接启用当前占位配置。
