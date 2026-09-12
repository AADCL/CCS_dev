# QRD_001 部署与验收记录

合并日期：2026-09-11；操作基线：CCS 0.23.1。此文件是该设备唯一的部署记录入口，后续按日期追加。

## 当前入口

- 设备：QRD_001；profile：`go2_edu`；端侧：`nvidia@192.168.50.100`。
- [配置与脚本](../../go2_edu/)保持原位置；[从零部署](../../../documents/DEPLOYMENT_GUIDE.md)、[接口填写](../../../documents/CONFIG_TOPIC_REFERENCE.md)、[使用手册](../../../documents/USER_MANUAL.md)、[设备索引](../../README.md)。
- legacy：重定位 disabled，根脚本不启动任务。不能套用原生 Robot2/Robot3 服务与日志路径。

## 历史材料与来源校验

以下合并原文件，保留日期、版本、哈希、失败证据、跳过项和原操作示例。历史章节的“当前/最终运行”、旧日志、相机筛选、时差门控、旧服务/保存路径及退出方式仅适用于当时；新部署执行上方当前指南。未记载实测不能补写通过，旧请求不构成新任务指令。

| 原文件（相对 edge_side_pkg） | 原始字节 SHA-256 | 合并章节 |
| --- | --- | --- |
| `deploy/go2_edu/DEPLOYMENT.md` | `f4939e9edd00cad234a6693439feaea58fe177762362fa6366507ee66a096f17` | [材料 1](#source-1) |

<a id="source-1"></a>

## 历史材料 1：deploy/go2_edu/DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s1-go2-edu-端侧部署"></a>

### Go2 EDU 端侧部署

CCS 0.23.1 当前入口：[使用手册](../../../documents/USER_MANUAL.md) · [接口与配置](../../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

本目录是指控端保存的 Go2 EDU profile 原件，不应整体复制到端侧。Go2 不再依赖 `EPQRD_go2_bridge`；在线、位姿、IMU 和点云分别使用 Livox/LIO 原生 ROS 话题。

<a id="s1-发布内容"></a>

#### 发布内容

端侧 catkin `src` 固定部署：

- `EPGeneral_device_config`
- `EPGeneral_map_stream`
- `epgeneral_mqtav`
- `EPGeneral_relocalization`
- `EPGeneral_task_control`
- `EPGeneral_udp_telemetry`
- `EPGeneral_video_srt`

在指控端临时发布副本中，将本目录 `config/*.yaml` 覆盖到 `EPGeneral_device_config/config/`。安装到端侧的配置目录只保留这些 YAML，不保留 `deploy/go2_edu` 层级。

<a id="s1-数据源"></a>

#### 数据源

- 在线状态：`/livox/lidar`，3 秒无消息判定离线。
- 位姿：`/lio/odometry`。
- IMU：`/livox/imu`。
- 点云与 Livox 可用性：`/livox/lidar`。
- 电池、armed、system status、机器人模式：无可确认 ROS 数据源，保持未知。
- 重定位：共享协调器已支持 `go2_edu`，但本目录的旧设备模板没有可验证的定位 stage，因此仍保持禁用并返回 `UNSUPPORTED_BACKEND`；设备专项 profile 配齐定位 launch 和健康话题后才能启用。

MQTT topic、`ccs-udp-telemetry-v1`、`ccs-map-stream-v2`、任务和重定位 wire 协议均不改变。

<a id="s1-安装"></a>

#### 安装

```bash
sudo install -D -m 0644 config/timesyncd-ccs.conf \
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

<a id="s1-验证与回滚"></a>

#### 验证与回滚

- 地面站收到 `mqtav/QRD_001/{presence,heartbeat,status}`，其中电池和机器人专有状态为空。
- UDP descriptor 使用 `/lio/odometry`、`/livox/imu`、`/livox/lidar`，不出现 `/qrd/`。
- SRT Listener 使用 profile 的 `video.yaml`。
- 建图节点使用 profile 的 `map_stream.yaml`。
- 回滚时恢复部署前七包、集中配置、启动脚本和 timesyncd 配置的备份；不要恢复已废弃的 bridge 包。

## 后续记录填写格式

追加日期/范围、源码版本与差异、文件清单及前后哈希、备份/证据路径、命令与实测、告警/未测项、启停/回滚。回滚不覆盖更新的急停状态，文档整理不表示重新部署。
