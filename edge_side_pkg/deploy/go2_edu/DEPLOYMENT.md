# Go2 EDU 端侧部署

本目录是指控端保存的 Go2 EDU profile 原件，不应整体复制到端侧。Go2 不再依赖 `EPQRD_go2_bridge`；在线、位姿、IMU 和点云分别使用 Livox/LIO 原生 ROS 话题。

## 发布内容

端侧 catkin `src` 固定部署：

- `EPGeneral_device_config`
- `EPGeneral_map_stream`
- `epgeneral_mqtav`
- `EPGeneral_relocalization`
- `EPGeneral_task_control`
- `EPGeneral_udp_telemetry`
- `EPGeneral_video_srt`

在指控端临时发布副本中，将本目录 `config/*.yaml` 覆盖到 `EPGeneral_device_config/config/`。安装到端侧的配置目录只保留这些 YAML，不保留 `deploy/go2_edu` 层级。

## 数据源

- 在线状态：`/livox/lidar`，3 秒无消息判定离线。
- 位姿：`/lio/odometry`。
- IMU：`/livox/imu`。
- 点云与 Livox 可用性：`/livox/lidar`。
- 电池、armed、system status、机器人模式：无可确认 ROS 数据源，保持未知。
- 重定位：profile 保持禁用，返回 `UNSUPPORTED_BACKEND`。

MQTT topic、`ccs-udp-telemetry-v1`、`ccs-map-stream-v2`、任务和重定位 wire 协议均不改变。

## 安装

```bash
sudo install -D -m 0644 timesyncd-ccs.conf \
  /etc/systemd/timesyncd.conf.d/ccs.conf
sudo systemctl restart systemd-timesyncd

sudo install -d -m 0750 /home/nvidia/ccs_edge_ws/config/go2_edu
sudo install -m 0640 config/*.yaml \
  /home/nvidia/ccs_edge_ws/config/go2_edu/
sudo install -m 0750 start_ccs_edge_dev.sh \
  /home/nvidia/ccs_edge_ws/start_ccs_edge_dev.sh
```

重新执行 `catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3` 并 source 工作空间。确认以下话题存在且有新数据：

```bash
rostopic hz /livox/lidar
rostopic hz /livox/imu
rostopic hz /lio/odometry
```

然后启动：

```bash
/home/nvidia/ccs_edge_ws/start_ccs_edge_dev.sh
```

脚本检查七份 profile YAML，启动 Livox、MQTAV、UDP 遥测、SRT、建图和重定位协调节点；任务协调仍由 `enable_task_control` 或设备任务能力显式启用。按 `Ctrl+C` 安全停止脚本管理的进程。

## 验证与回滚

- 地面站收到 `mqtav/QRD_001/{presence,heartbeat,status}`，其中电池和机器人专有状态为空。
- UDP descriptor 使用 `/lio/odometry`、`/livox/imu`、`/livox/lidar`，不出现 `/qrd/`。
- SRT Listener 使用 profile 的 `video.yaml`。
- 建图节点使用 profile 的 `map_stream.yaml`。
- 回滚时恢复部署前七包、集中配置、启动脚本和 timesyncd 配置的备份；不要恢复已废弃的 bridge 包。
