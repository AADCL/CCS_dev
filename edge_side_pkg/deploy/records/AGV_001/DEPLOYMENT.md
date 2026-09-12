# AGV_001 部署与验收记录

合并日期：2026-09-11；操作基线：CCS 0.23.1。此文件是该设备唯一的部署记录入口，后续按日期追加。

## 当前入口

- 设备：AGV_001；profile：`ground_air_agv`；端侧：`bitcq@192.168.50.130`。
- [配置与脚本](../../ground_air_agv/)保持原位置；[从零部署](../../../documents/DEPLOYMENT_GUIDE.md)、[接口填写](../../../documents/CONFIG_TOPIC_REFERENCE.md)、[使用手册](../../../documents/USER_MANUAL.md)、[设备索引](../../README.md)。
- 七公共包加阶段控制及原生 ground_air_msgs。静态 TF 单 owner，建图/重定位互斥，guard 接受 1/2。首次 underlay 两个适配 launch 安装与后续 CCS-only 增量分开，用户服务保持 disabled。

## 历史材料与来源校验

以下合并原文件，保留日期、版本、哈希、失败证据、跳过项和原操作示例。历史章节的“当前/最终运行”、旧日志、相机筛选、时差门控、旧服务/保存路径及退出方式仅适用于当时；新部署执行上方当前指南。未记载实测不能补写通过，旧请求不构成新任务指令。

| 原文件（相对 edge_side_pkg） | 原始字节 SHA-256 | 合并章节 |
| --- | --- | --- |
| `deploy/ground_air_agv/DEPLOYMENT.md` | `85d9613c2397da0a963acb13403154d88343879c5325039656ddaead48c69bd1` | [材料 1](#source-1) |
| `documents/GROUND_AIR_AGV_DEPLOYMENT.md` | `5454ea56eceb59ddfbe2dee38360d83c69580c74346046f4fd925d252be5dcbc` | [材料 2](#source-2) |
| `documents/GROUND_AIR_AGV_DEPLOYMENT_LOG.md` | `065c54fe5973e731864fde6f2c61710d79f781cbb828e7f23b2963a1870489cd` | [材料 3](#source-3) |
| `documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md` | `9b1614d1b6277e76d78ff2195551ce84c7c792945f608fa8dafc31e63c57dacf` | [材料 4](#source-4) |
| `documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md` | `62f41fb0d6069dcd08449660f5b636bfa2db3510c3937de1f14bbce060e64574` | [材料 5](#source-5) |
| `documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md` | `2d89107dbd5dd0ac896ed1b69ba96be39f2cca87bcd5bad9778642f955422061` | [材料 6](#source-6) |
| `documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT_LOG.md` | `f46c76d2af026eaf56838525a3f0447f98daaa83411ccab9d3a27cf655a8c8b2` | [材料 7](#source-7) |
| `documents/GROUND_AIR_AGV_TASK_DEPLOYMENT.md` | `5cb310df65427af9b97b4e582e444427ce0c576cd5e6d4658091391fd6ba11b1` | [材料 8](#source-8) |
| `documents/GROUND_AIR_AGV_TASK_DEPLOYMENT_LOG.md` | `92c254bc18a6f407de23042443ea1823e908bfacd7e5ffb268ab611caff7db6d` | [材料 9](#source-9) |

<a id="source-1"></a>

## 历史材料 1：deploy/ground_air_agv/DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s1-ground-air-agv-ccs-edge-deployment"></a>

### Ground-Air AGV CCS Edge Deployment

首次从源码或端侧 ZIP 安装，请先完成[使用手册的 Ground-Air 首次安装补充](../../../documents/USER_MANUAL.md#ground-air-首次安装补充)，包括 launch/override、源脚本执行权限、用户服务与日志目录；本页后续增量更新步骤以已有部署为前提。

CCS 0.23.1 当前入口：[使用手册](../../../documents/USER_MANUAL.md) · [接口与配置](../../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

This profile targets `AGV_001` at `192.168.50.130` on Ubuntu 20.04, ROS Noetic, and ARM64. The CCS overlay is `/home/bitcq/ccs_edge_ws`; vehicle packages remain in `/home/bitcq/catkin_ws`.

`start_ccs_edge_dev.sh` starts MAVROS, Livox MID-360, MQTT, UDP telemetry, map-stream, optional A8/SRT, the workspace-owned Ground-Air stage manager, `epgeneral_relocalization`, resident ground control, and CCS task control. Navigation and the native mission executor start only after a stored ground task passes live localization and map validation. The resident coordinate transforms are started before task control:

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

The transform launch owns only the resident `odom -> camera_init` and `body -> base_link` static publishers. FAST-LIO remains on demand. `manual_mapping_control.launch` directly starts the mapping stack and its mapping-mode `map -> odom` owner without including the legacy mapping or static-transform launch. `relocalization_control.launch` does the same for localization. The stage manager owns these on-demand process groups but never starts or stops the resident transform launch.

The Ground-Air map-stream profile keeps `ros.frames.map=camera_init` and sets `ros.frames.preview=odom`. MapStream therefore looks up `odom <- camera_init` at the point-cloud timestamp and transforms the actual point coordinates before publishing a fragment; changing only the frame label is invalid. The wire contract is `prepare_result.frame_id=odom`, fragment `frame_id=odom`, fragment `source_frame_id=camera_init`, and artifact manifest `frame_id=map`.

Boot autostart is disabled for `AGV_001`; keep `ccs-edge-dev.service` in the `disabled` state. Start the existing service manually with `systemctl --user start ccs-edge-dev.service` when needed. Disabling autostart does not stop an already running stack, and neither deployment nor rollback may re-enable it.

Task control uses UDP 14563/14564 and `ccs-task-control-v2`. The profile accepts ground tasks only, caps linear speed at 0.1 m/s and angular speed at 0.2 rad/s, and requires manual arming plus OFFBOARD before execution. Emergency stop calls `/ground_air/emergency_stop`; successful confirmation is persisted at `/home/bitcq/ccs_edge_ws/run/task_emergency_stop.json`. See `edge_side_pkg/documents/GROUND_AIR_AGV_TASK_DEPLOYMENT.md` for incremental deployment and acceptance.

The user service uses `KillMode=mixed`, allowing the supervisor to stop the coordinator and stage manager before stopping the static transforms. New application and ROS logs are under `/home/bitcq/ccs_edge_ws/log/ground_air_agv`, ROS home is `/home/bitcq/ccs_edge_ws/run/ros_home`, and the transform PID file is `/home/bitcq/ccs_edge_ws/run/mapping_tf.pid`.

The current stage manager runs from `/home/bitcq/ccs_edge_ws/src/EPGeneral_ground_air_control/scripts/ground_air_stage_manager_node.py` and advertises guard `2`. The v0.13.2 mapping client explicitly accepts integer guard versions `1` and `2`; missing, malformed or unknown versions remain errors with the actual and supported values. The original mapping caller identity and service fields remain unchanged.

For the client compatibility hotfix, use the reviewed file manifest to back up and atomically replace the short-lived client, version metadata, related tests and documentation through SSH/SFTP. Keep all writes, temporary files, backups and evidence below `/home/bitcq/ccs_edge_ws`, preserve permissions, verify hashes and record service state and PIDs before and after. The client reloads on each command, so this hotfix needs no service restart or unit change; the resident map-stream process may retain its previously loaded version marker until the next manual restart. Do not use the legacy `deploy_stage_manager_update.sh`: it targets the old guard `1` manager in the vehicle underlay.

Before ROS package discovery, the deployment script sources `/opt/ros/noetic/setup.bash`, then `/home/bitcq/catkin_ws/devel/setup.bash --extend`, then `/home/bitcq/ccs_edge_ws/devel/setup.bash --extend`. Keep this order: sourcing only the CCS overlay hides vehicle packages such as `fast_lio_open3d` from launch validation.

Update both the ground-station runtime `config/map_building.json` and its release-default mirror so `device_frames.AGV_001` uses `remote_mapping=odom`, `preview_source=camera_init`, and `remote_artifact=map`; restart CCS to reload that configuration. This is a configuration-only ground-station change. The endpoint YAML and ground-station JSON form one compatibility unit and must be deployed or rolled back together.

Acceptance is static only: verify autostart remains `disabled`, then run `rosrun epgeneral_map_stream ground_air_stage_client.py --check` against the actual guard `2` manager and confirm it leaves the stage at BASE; verify the service and both resident transform edges; confirm prepare reports `odom` and the first accepted fragment reports `odom` from `camera_init`; run one start/duplicate-start/save cycle; confirm the artifact reports `map`, resident transform PIDs remain stable, and FAST-LIO/mapping processes exit while base services stay active. Never arm the FCU or publish movement, pose, mode, or waypoint commands during this check.

The original `manual_mapping.launch` remains byte-for-byte unchanged as a historical/full-rollback reference. Its legacy nested dependencies no longer form the supported standalone mapping path under this TF architecture. Use `manual_mapping_control.launch` for current diagnostics, and never run the legacy entry while `ccs-edge-dev.service` is active.

See `edge_side_pkg/documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md` for command mapping, artifacts, rollback, and evidence requirements.

Relocalization updates use `deploy_relocalization_update.sh`. The bundle must also synchronize the compatible mapping client, associated version metadata and regression tests, and run the actual mapping `--check` after startup readiness. Checking only that the manager publishes guard `2` missed this regression. Every deployment, backup, temporary, configuration, package and evidence write is containment-checked below `/home/bitcq/ccs_edge_ws`; the vehicle underlay and existing user service definition are read-only. Preserve the pre-deployment service enablement state and require `disabled` for this device. See `edge_side_pkg/documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md`.

<a id="source-2"></a>

## 历史材料 2：documents/GROUND_AIR_AGV_DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s2-空地-agv-端侧部署说明"></a>

### 空地 AGV 端侧部署说明

CCS 0.23.1 当前入口：[使用手册](../../../documents/USER_MANUAL.md) · [接口与配置](../../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

<a id="s2-设备与边界"></a>

#### 设备与边界

- 设备：`AGV_001`，端侧 IP `192.168.50.130`
- 地面站：`192.168.50.101`
- 系统：Jetson ARM64、Ubuntu 20.04、ROS Noetic
- CCS 工作空间：`/home/bitcq/ccs_edge_ws`
- 车辆工作空间：`/home/bitcq/catkin_ws`

部署过程不修改原始 `car_bringup/manual_mapping.launch` 或 `save_mapping.launch`，不使用明文凭据，不发送任何运动、解锁、模式或航点指令。

<a id="s2-一键启动组件"></a>

#### 一键启动组件

`start_ccs_edge_dev.sh` 依次启动 MAVROS、Livox MID-360、MQTT、UDP 遥测、map-stream、A8 Mini、SRT、工作区 stage manager 和重定位协调器，最后执行：

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

该 launch 只常驻 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster`，统一发布 `odom -> camera_init` 和 `body -> base_link`。FAST-LIO 不在一键脚本启动阶段启动，收到建图或重定位阶段指令后才由 stage manager 启动。

stage manager 由 `ccs_edge_ws` 内的设备专属控制包提供，启动命令为：

```bash
rosrun epgeneral_ground_air_control ground_air_stage_manager_node.py
```

它发布 `ccs_session_guard_version=2`，只管理建图/重定位阶段、互斥 owner 和所属进程组，不持有静态 TF。重定位协调器 `/epgeneral_relocalization` 在最终 TF launch 之前启动；A8/SRT 可降级，其余必需节点或任一静态 TF 节点退出会触发 supervisor 失败处理。

<a id="s2-服务管理"></a>

#### 服务管理

用户服务文件为 `~/.config/systemd/user/ccs-edge-dev.service`。`AGV_001` 已取消上电自启动，服务保持 `disabled`；需要运行时手动启动。禁用上电自启动不会停止当前服务，手动启动后仍保留既有故障重启和清理机制。

```bash
systemctl --user start ccs-edge-dev.service
systemctl --user status ccs-edge-dev.service --no-pager
systemctl --user is-enabled ccs-edge-dev.service
```

最后一条预期输出 `disabled`（systemctl 对该状态返回非零是正常行为）。只更新源码、元数据和文档的部署不修改 unit、linger 或上电启动设置；确需重新加载常驻节点时才手动 `restart`。

`KillMode=mixed` 允许主脚本按“stage manager/阶段进程 -> 静态 TF -> 其他服务”的顺序清理。统一服务重启会短暂中断 MAVROS、Livox、MQTT、UDP、map-stream 和视频链。

<a id="s2-建图与重定位响应"></a>

#### 建图与重定位响应

指控准备建图时，map-stream 的短期客户端先验证 stage service、会话归属保护、外部 TF 模式和两个 TF 节点；开始建图时才请求 `stage=1`。v0.13.2 明确支持整数 guard `1`、`2`，当前 manager 发布 `2`；缺失、类型错误和未知版本继续拒绝并输出实际值与支持范围。manager 通过 `manual_mapping_control.launch` 启动 FAST-LIO、滤地、动态栅格、地图记录器和建图态 `map -> odom`；重复开始保持幂等。该入口不引用旧 `mapping_system.launch`，不会重复启动静态 TF。

结束建图仍运行 `roslaunch car_bringup save_mapping.launch`。地图文件验证成功后请求 BASE 并停止建图专用进程；保存失败时保持建图运行。TF 故障不能阻止所属会话执行停止或取消。

实时预览使用端侧真实坐标转换：`/cloud_registered` 的 `camera_init` 点云通过常驻 `odom <- camera_init` TF 转换到 `odom`。端侧返回 `prepare_result.frame_id=odom`，预览分片返回 `frame_id=odom/source_frame_id=camera_init`，最终成果保持 `frame_id=map`。指控端只同步 `AGV_001` 的 `remote_mapping=odom`、`preview_source=camera_init`、`remote_artifact=map` 配置，不修改建图协调器代码。

重定位 stage 使用 `relocalization_control.launch` 启动 FAST-LIO 与定位层，同样复用两条常驻静态 TF。原始建图与重定位 launch 均保留为历史/整套回滚参考，但旧入口依赖已变化，当前架构不保证其可独立完成阶段启动；统一服务运行时不得人工执行会间接包含 `mapping_coordinate_transforms.launch` 的旧入口。

完整细节见 [GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md](../../../documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md)。

<a id="s2-构建与日志"></a>

#### 构建与日志

```bash
cd /home/bitcq/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source /home/bitcq/catkin_ws/devel/setup.bash --extend
catkin_make --pkg epgeneral_map_stream -DCMAKE_BUILD_TYPE=Release -j1
```

增量部署脚本在检查 launch 和构建前严格按 ROS Noetic、车辆 `catkin_ws`、CCS `ccs_edge_ws` 的顺序加载 overlay，后两项使用 `--extend`。只加载 CCS overlay 会使 `fast_lio_open3d` 等车辆包不可见，必须在服务重启前中止部署并修正环境。

日志默认位于 `/home/bitcq/ccs_edge_ws/log/ground_air_agv/`；其中 `startup.log` 记录启动顺序，`stage_manager.log` 记录阶段切换，`mapping_tf.log` 记录静态 TF launch，`map_stream.log` 记录响应栈输出。ROS 节点日志位于其 `ros/latest/` 下，例如 `epgeneral_map_stream-1.log`；ROS home 为 `/home/bitcq/ccs_edge_ws/run/ros_home`，PID 位于 `/home/bitcq/ccs_edge_ws/run/`。

Ground-Air `map_stream.yaml` 部署到 `/home/bitcq/ccs_edge_ws/config/ground_air_agv/map_stream.yaml`。变更实时预览帧时必须同时更新指控运行配置和发布默认镜像中的 `config/map_building.json`，然后分别重启 `ccs-edge-dev.service` 与 CCS；回滚也必须恢复同一批次的两端配置。

<a id="s2-静态验收"></a>

#### 静态验收

```bash
systemctl --user is-enabled ccs-edge-dev.service
systemctl --user is-active ccs-edge-dev.service
rostopic echo -n 1 /ground_air/system/stage
rosparam get /ground_air_stage_manager/ccs_session_guard_version
rosrun epgeneral_map_stream ground_air_stage_client.py --check
rosnode list
rostopic echo -n 1 /livox/lidar
rostopic echo -n 1 /livox/imu
rosrun tf tf_echo odom camera_init
rosrun tf tf_echo body base_link
```

确认服务上电启动状态仍为 `disabled`，客户端 `--check` 成功且阶段仍为 BASE；TF 只有一套发布者，启动日志中 TF launch 最后出现，建图开始/重复开始/结束闭环后静态 TF PID 不变，FAST-LIO、world-TF owner 和建图节点退出而基础服务继续运行。端侧仅评价流程、接口和产物，不评价车辆移动后的建图精度。

<a id="source-3"></a>

## 历史材料 3：documents/GROUND_AIR_AGV_DEPLOYMENT_LOG.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s3-空地-agv-部署日志"></a>

### 空地 AGV 部署日志

<a id="s3-2026-09-05-手动启动与建图兼容修复"></a>

#### 2026-09-05 手动启动与建图兼容修复

- 当前 `AGV_001` 已取消上电自启动，`ccs-edge-dev.service` 保持 `disabled`；已有运行栈不因禁用而停止，需要运行时手动启动。
- 当前 manager 和日志均由 `ccs_edge_ws` 管理，manager 发布 guard `2`。旧建图客户端只接受 guard `1`，导致准备阶段报缺少会话保护。
- `epgeneral_map_stream` v0.13.2 同步版本兼容、部署门禁和当前操作说明；逐文件修复未重启常驻服务，端侧聚焦测试最终 25/25，通过实际预检及一次无运动建图闭环。最终服务仍为 `disabled/inactive`，手动启动的基础栈继续运行，stage 已回到 BASE；完整备份、地图校验值和清理结果见 [建图部署日志](../../../documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md)。
- 以下旧批次中的路径、启用状态和测试结果按历史保留，不作为当前上电启动或版本要求。


<a id="s3-2026-08-31-a8-mini-srt-视频部署"></a>

#### 2026-08-31 A8 Mini SRT 视频部署

- 目标：将已部署的 `a8_mini_camera` 与 `epgeneral_video_srt` 纳入 AGV
  一键启动；相机参数为 `camera_ip:=192.168.144.25`、
  `image_topic:=/a8_cam/image_raw`。
- 设备只读核查确认相机通过 `eth0` 可达，RTSP 主码流为 H.264 High、
  1280x720、30 FPS；A8 与 SRT 二进制及全部 GStreamer 元素已存在，
  UDP 9000 未占用。
- SRT profile 启用 1280x720、30 FPS、3000 kbps、120 ms Listener；A8
  和 SRT 作为可降级服务，无帧或异常退出时不停止四个基础服务。
- 原脚本、视频配置和文档备份位于
  `ccs_edge_ws/run/deploy_backup_20260831_a8_srt`。新脚本 SHA-256 为
  `e15ad2c152690194081e669a49b81fdaa8ba9941e98d5ef8de127f3cfc9cd896`，
  新视频配置 SHA-256 为
  `a3ec90b1b998babfcf1050d9231054e216b5242bd9a926a5f83aea5d7fb701e3`。
- 本地 AGV profile、视频、MQTT 和 UDP 回归测试通过 29 项，profile
  YAML/XML 解析通过；端侧脚本 `bash -n`、视频 YAML、两个 launch 解析及
  A8 包 7 项单元测试通过。包源码和二进制未变，因此未重复构建工作空间。
- 真机六个目标节点全部在线且六类 launch 各只有 1 个。A8 话题类型为
  `sensor_msgs/Image`，实测 1280x720、`bgr8`、frame `a8_cam`，连续发布
  约 25 Hz；低于源流标称 30 FPS，但无断流。
- GStreamer SRT Caller 实际连接 UDP 9000 并解析出 1280x720、30 FPS、
  progressive、constrained-baseline H.264 level 3.1；端侧日志记录 Caller
  成功连接。端侧 FFprobe 未编译 SRT 协议，故使用同机 GStreamer Caller
  完成 wire-level 验收。
- 主动终止 `/epgeneral_video_srt` 后，监督脚本仅报告可降级告警，四个
  基础服务与 A8 保持在线，MAVROS、Livox、UDP 数据继续正常，视频 PID
  被清理。运行中重复调用脚本后六类 launch 仍各 1 个。
- 未出现 FAST-LIO、地面滤波、建图、重定位、导航、控制或任务节点；
  检查的速度、位置及 raw setpoint 话题无发布者。`Ctrl+C` 后六个 launch、
  六个节点、ROS Master 和 PID 文件均无残留。
- 当前地面站主机 PATH 中未发现 FFmpeg，`config/srt_video.json` 仍配置
  `ffmpeg`，因此本轮无法确认 CCS 设备详情页首帧显示。该项属于地面站
  运行依赖阻塞，不影响已完成的端侧 Listener 和 SRT 实际拉流验收。
- 清理验收后通过 `nohup` 最终恢复一键栈，监督进程 PID 为 `45664`，输出
  位于 `~/.ros/ccs_edge_dev_ground_air_agv/log/supervisor.log`。断开部署
  SSH 会话前再次确认六个目标节点和六个 launch 均在线且唯一。

<a id="s3-2026-08-31-通信服务纳入一键启动"></a>

#### 2026-08-31 通信服务纳入一键启动

- 按部署范围变更，将 `epgeneral_mqtav` 和 `epgeneral_udp_telemetry` 加入
  `/home/bitcq/ccs_edge_ws/start_ccs_edge_dev.sh` 的受管启动链。
- 启动顺序调整为 MAVROS、Livox、MQTT、UDP 遥测；通信服务只在 MAVROS
  和 Livox 要求话题收到真实消息后启动。
- MQTT 使用 `AGV_001` profile 并连接 `192.168.50.101:1883`；UDP 遥测
  发送至 `192.168.50.101:14560`，链路和诊断话题分别为
  `/agv/AGV_001/link/udp_tx` 和 `/agv/AGV_001/diagnostics`。
- FAST-LIO、地面滤波、建图、重定位、导航、控制、任务和视频仍不在
  启动链中；脚本不包含任何运动指令。
- 增量部署前的脚本和文档备份位于
  `ccs_edge_ws/run/deploy_backup_20260831_mqtt_udp`。新脚本 SHA-256 为
  `f661421776518c4ff9c98440e4324e2d2606d13d32c845bfb970e2435c1cd3bf`；
  端侧 `bash -n` 和两个通信 launch 的 `roslaunch --nodes` 解析均通过，
  本次不涉及源码或构建产物变更，因此未重复构建工作空间。
- 真机静态启动后 ROS 图仅包含四个目标节点和 `/rosout`。MQTT 日志确认
  `mqtav/AGV_001/presence`、`heartbeat`、`status` 持续发送；UDP 链路话题
  为 `True`，诊断报告 `sendto succeeded`、目标 `192.168.50.101:14560`，
  各级发送失败计数均为 0。
- MAVROS 状态、IMU、电池及 Livox 点云、IMU 话题保持可用；UDP 正常接收
  MAVROS 位姿/IMU 和 Livox 点云。诊断中 FAST-LIO、建图相关占位数据源
  显示等待样本，符合这些功能未启动的本轮边界。
- 未发现 FAST-LIO、地面滤波、建图、重定位、导航、控制、任务或视频
  节点；检查的速度、位置及 raw setpoint 话题均无发布者。
- 运行中重复调用脚本后，MAVROS、Livox、MQTT、UDP 四类 launch 仍各只有
  1 个。主脚本 `Ctrl+C` 后，四个 launch、四个节点、ROS Master 和 PID
  文件均无残留。

<a id="s3-2026-08-31-基础部署"></a>

#### 2026-08-31 基础部署

- 目标：`AGV_001` / `192.168.50.130`。
- 范围：部署 7 个通用端侧包；运行时仅启动 MAVROS 与 Livox MID-360。
- 安全边界：不解锁、不切换模式、不发送运动、位置或任务指令。
- 建图、重定位和任务配置仅作占位，未加入启动链。
- 新建 `/home/bitcq/ccs_edge_ws`，原 `/home/bitcq/start.sh`、`catkin_ws` 和
  `ifc_plus` 均未修改。部署归档 SHA-256 为
  `3e5fb79dab800c358f6c18adffd6cd7c1b5688d1e2e7230480625f8215eac26e`。
- 设备启动时停留在 1970 年。已从工作空间安装 NTP 配置到
  `/etc/systemd/timesyncd.conf.d/ccs.conf`，恢复后
  `NTPSynchronized=yes`、`ServerName=192.168.50.101`。
- 设备 DNS 无法解析原中大/中科大镜像。未修改 apt 源；从 Ubuntu 官方
  ports 仓库下载并校验后，离线安装 `python3-msgpack 0.6.2-1 arm64` 和
  `python3-paho-mqtt 1.5.0-1 all`，随后 `rosdep check` 全部满足。
- Windows 归档中的包内 shell 脚本恢复为 Linux LF 和 0755 权限；全部
  `bash -n` 检查通过。Python 编译、4 个包版本检查和 Release `-j1`
  catkin 构建通过，构建仅遍历 7 个预期通用包。
- 纯端侧测试通过 140 项：MQTT 23、UDP 16、地图 59（另 1 项按设计
  跳过）、重定位 13、任务 29。全量测试中的 Scout profile/地面站契约
  用例因目标机不部署对应源码树而排除，不复制无关依赖规避该边界。
- `start_ccs_edge_dev.sh` SHA-256 为
  `8eda3a669d29d9f600b92b2653995f9c4ba6f961a2e822c8e386362af7f085eb`；
  MAVROS 和 Livox launch SHA-256 分别为
  `8e7e1204b29ad92a8a38a0fa8a69c9dae039f29c1aa51d001eca778f9cc85fa6`
  和 `03798d303922dd6ce5c489df69b8777ee3f75201cde997f1c33411e929d08138`。
- 两轮真机静态启动均成功。ROS 图仅包含 `/mavros`、
  `/livox_lidar_publisher2` 和 `/rosout`；MAVROS 报告 `connected=True`、
  `armed=False`、`mode=MANUAL`，电池约 22.824 V、57%。
- 实测 MAVROS IMU 约 150 Hz、电池约 0.5 Hz、Livox 点云约 10 Hz
  （单帧约 19,968 点）、Livox IMU 约 200 Hz；五个要求话题均收到真实
  消息且 frame 为 `base_link`。
- FAST-LIO、地面滤波、建图、重定位、导航、控制、任务、MQTT、UDP 和
  视频节点均不存在；速度、位置及 raw setpoint 输入均无发布者。
- 运行中重复调用脚本时受管 launch 数保持 `2 -> 2`，未重复启动。两轮
  `Ctrl+C` 后 ROS Master、两个 launch、两个硬件节点和 PID 文件均无残留。
- 本轮未进行任何车辆运动或飞行测试；建图、重定位和任务真实流程仍待
  后续补充。

<a id="source-4"></a>

## 历史材料 4：documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s4-ground-air-agv-建图部署说明"></a>

### Ground-Air AGV 建图部署说明

CCS 0.23.1 当前入口：[使用手册](../../../documents/USER_MANUAL.md) · [接口与配置](../../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

<a id="s4-适用范围"></a>

#### 适用范围

适用于 `AGV_001`（`192.168.50.130`），复用 Scout 的指令应答、会话状态、产物校验、下载和日志组织方式。原始 `manual_mapping.launch` 与 `save_mapping.launch` 保持只读，部署前后必须校验 SHA-256，不修改、不覆盖。

`epgeneral_map_stream` v0.13.2 修复 guard 版本兼容，指控协议、UDP/TCP 端口与现有帧契约不变。当前部署使用工作区内 guard `2` manager；建图客户端兼容整数 guard `1`、`2`，缺失、类型错误和未知版本继续拒绝并报告实际值与支持范围。此次兼容修复不需要修改指控业务代码或帧配置。

<a id="s4-手动启动顺序与所有权"></a>

#### 手动启动顺序与所有权

`AGV_001` 已取消上电自启动，`ccs-edge-dev.service` 保持 `disabled`。需要运行时执行 `systemctl --user start ccs-edge-dev.service`，也可在服务未运行时手动执行 `/home/bitcq/ccs_edge_ws/start_ccs_edge_dev.sh`。两种入口不能同时启动；禁用上电自启动不停止当前服务。

统一启动顺序为：ROS Master -> MAVROS -> Livox -> MQTT -> UDP 遥测 -> map-stream -> A8 Mini（可降级）-> SRT（可降级）-> stage manager -> 重定位协调器 -> 静态 TF launch。最后一项必须使用：

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

`start_ccs_edge_dev.sh` 直接持有该 roslaunch 进程组，并分别等待 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster`。启动时若任一同名 TF 节点已由外部进程占用，脚本拒绝重复接管；运行期间任一节点退出，supervisor 失败退出并由 systemd 重启整套端侧服务。

`ground_air_stage_manager_node.py` 以 `rosrun epgeneral_ground_air_control ground_air_stage_manager_node.py` 从 `ccs_edge_ws` 常驻，只提供 `/ground_air/system/set_stage`、建图/重定位互斥 owner 及所属进程组管理，不创建、复用或停止静态 TF。manager 发布 `ccs_session_guard_version=2` 与 `external_tf_required=1`，并清除旧的 `resident_tf_version` 标识。

`ccs-edge-dev.service` 使用 `KillMode=mixed`：停止服务时先通知主脚本，由脚本先停止 stage manager 及其 FAST-LIO/阶段子进程，再停止静态 TF，超时后才由 systemd 清理剩余进程。

<a id="s4-实时预览坐标系契约"></a>

#### 实时预览坐标系契约

FAST-LIO 发布的 `/cloud_registered` 保持 `camera_init` 源坐标。Ground-Air profile 使用 `ros.frames.map=camera_init`、`ros.frames.preview=odom`；MapStream 以点云窗口时间戳查询 `odom <- camera_init`，并将点坐标实际变换到 `odom` 后再生成 PCD 分片，不能只修改 frame 标签。

端侧与指控端的契约必须同时满足：

| 阶段/产物 | 坐标系 |
| --- | --- |
| `prepare_result.frame_id` | `odom` |
| `cloud_fragment_ready.frame_id` | `odom` |
| `cloud_fragment_ready.source_frame_id` | `camera_init` |
| 成果 manifest `frame_id` | `map` |

指控端 `config/map_building.json` 及发布默认镜像中的 `device_frames.AGV_001` 对应配置为 `remote_mapping=odom`、`preview_source=camera_init`、`remote_artifact=map`。先前端侧准备响应返回 `camera_init`，而指控准备阶段要求全局 `odom`，因此协商通过后立即触发 `FRAME_MISMATCH` 并自动取消；只把端侧预览标签改为 `odom` 又会使首个分片与原 AGV profile 不一致。本次采用端侧真实转换与指控设备配置同步，保持准备和分片阶段语义一致。

<a id="s4-建图指令流程"></a>

#### 建图指令流程

- 准备建图：短期客户端执行 `ground_air_stage_client.py --check`，验证服务、guard 和两条外部静态 TF，不调用阶段切换服务。guard `2` 与原建图 caller 协议兼容，不应将 manager 参数改回 `1`。
- 开始建图：响应端调用 `/ground_air/system/set_stage`，`stage=1`。manager 启动 `roslaunch car_bringup manual_mapping_control.launch`，等待 FAST-LIO、地图记录器、建图态 `map -> odom` 和完整坐标链就绪；两条静态 TF 直接复用一键栈常驻实例。
- 精简入口：`manual_mapping_control.launch` 直接组合 FAST-LIO、里程计适配、滤地、动态栅格、地图记录器、建图态 world-TF owner 和 `/ground_air/mapping/start` 调用，不再间接引用 `start_mapping.launch`、`mapping_system.launch` 或静态 TF launch。
- 重复开始：同一会话与 `map_id` 幂等返回，不创建第二套 FAST-LIO、记录器或 TF。
- 结束建图：先运行 `roslaunch car_bringup save_mapping.launch`，验证本次新生成且非空、配对的 PCD/PGM/YAML/metadata；成功后以同一会话请求 `stage=0`，由 manager 优雅停止本次建图进程组。
- 保存失败：返回失败并保持 MAPPING，FAST-LIO 与建图进程继续运行，允许再次下发结束指令。
- 取消或启动失败：以同一会话身份和 `map_id` 请求 BASE；即使外部 TF 已故障，也不得阻止所属会话停止建图。

完整建图 TF 链为 `map -> odom -> camera_init -> body -> base_link`。`map -> odom` 由建图进程组内的 `ground_air_world_tf_owner` 以 mapping 模式发布，`camera_init -> body` 由 FAST-LIO 发布，另两条静态边由一键脚本持有的 launch 唯一发布。BASE 阶段只承诺两条常驻静态边。

stage 2 由 `epgeneral_ground_air_control/relocalization_control.launch` 直接启动 FAST-LIO、定位层和初始位姿适配器并复用常驻静态 TF。顶层命令通过仅包含 `relocalization_system.launch` 的工作区覆盖保持 `roslaunch car_bringup relocalization_system.launch` 不变；underlay 原文件不修改。完整契约见 `GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md`。

原始 `roslaunch car_bringup manual_mapping.launch` 保持逐字节不变，作为历史和整套回滚参考；它依赖旧 `mapping_system.launch` 的启动关系，在当前“静态 TF 独立常驻”架构下不保证可独立完成建图，也不得在统一服务运行时执行。当前人工诊断应停止 `ccs-edge-dev.service` 后显式启动所需基础层与 `manual_mapping_control.launch`；只有完整回滚关联 launch 后，才按旧说明使用原命令。

<a id="s4-增量部署"></a>

#### 增量部署

guard 兼容修复采用已审核文件清单，通过 SSH/SFTP 逐文件备份并原子替换客户端、版本元数据、相关测试和文档。所有新增、备份、临时与证据文件均位于 `/home/bitcq/ccs_edge_ws` 内，保留原有权限；不更新 manager、车辆 underlay、unit 或启动脚本。

1. 确认阶段为 BASE 且无活动建图/重定位会话，记录服务的 enabled/active 状态、主进程 PID、manager 与静态 TF PID，以及本次目标文件和原始 launch 的校验值。
2. 对照已审核差异准备文件，在工作区内建立时间戳备份和清单；先校验传输后的临时文件，再原子替换目标。发布元数据与 Python 包版本保持 v0.13.2 一致。
3. 按 ROS Noetic、车辆 `catkin_ws/devel/setup.bash --extend`、CCS `ccs_edge_ws/devel/setup.bash --extend` 的顺序加载环境，运行版本一致性、Python/Bash 检查和目标测试。
4. 执行实际 `ground_air_stage_client.py --check`，确认 guard `2` 被接受、两条外部 TF 就绪、阶段仍为 BASE，再进行静态建图闭环。
5. 复核服务仍为 `disabled`，原进程、静态 TF 与 underlay 校验值保持一致。短期客户端每次命令都会重新加载，因此本次修复无需重启服务；常驻 map-stream 进程已加载的版本/能力标记可能保留旧值，待下次手动重启后加载磁盘上的 v0.13.2。

后续重定位增量包使用 `deploy_relocalization_update.sh`，必须同步建图客户端及相应元数据/测试，并在重启后的就绪门禁中运行实际建图 `--check`。旧 `deploy_stage_manager_update.sh` 属于 guard `1`、underlay manager 部署链，不能用于当前 guard `2` 设备，也不能用于本次修复。

将真实备份路径、文件清单、部署前后校验、服务状态及测试结果写入部署日志。本次不改两端帧配置；以后若修改 Ground-Air `map_stream.yaml` 的帧契约，仍需与指控运行配置及发布默认镜像 `config/map_building.json` 成对更新或回滚。

<a id="s4-静态验收"></a>

#### 静态验收

端侧只做无运动增量测试，不发送解锁、模式切换、速度、位置或航点指令。

```bash
systemctl --user is-enabled ccs-edge-dev.service
systemctl --user is-active ccs-edge-dev.service
systemctl --user show ccs-edge-dev.service -p MainPID -p NRestarts -p KillMode
rostopic echo -n 1 /ground_air/system/stage
rosparam get /ground_air_stage_manager/ccs_session_guard_version
rosparam get /ground_air_stage_manager/external_tf_required
rosrun epgeneral_map_stream ground_air_stage_client.py --check
rosnode list | grep -E 'ground_air_stage_manager|odom_camera_init|base_link_body|fast_lio'
rosrun tf tf_echo odom camera_init
rosrun tf tf_echo body base_link
```

`is-enabled` 预期输出 `disabled`，其非零返回值不代表服务故障。`--check` 应成功且不切换阶段。验收至少覆盖：BASE 无建图节点、两个静态 TF 唯一发布；`prepare_result.frame_id=odom`，首个分片为 `frame_id=odom/source_frame_id=camera_init` 且被指控接受；开始、重复开始、保存、BASE 复位；建图期间存在且仅存在一个建图态 world-TF owner；建图前后两个静态 TF PID 不变；结束后无 FAST-LIO、建图 roslaunch 或孤儿进程；成果 manifest 为 `map`；原始 launch 校验值不变。保存失败保活只用本地单测验证，不在端侧主动制造磁盘或权限故障。

<a id="s4-产物与日志"></a>

#### 产物与日志

- 地图：`/home/bitcq/catkin_ws/maps/<YYYYMMDD_HHMMSS>/`，包含 `cloud_map.pcd`、`map.pgm`、`map.yaml`、`metadata.json`。
- 启动顺序：`/home/bitcq/ccs_edge_ws/log/ground_air_agv/startup.log`。
- stage manager：同目录 `stage_manager.log`。
- 静态 TF roslaunch：同目录 `mapping_tf.log`，PID 为 `/home/bitcq/ccs_edge_ws/run/mapping_tf.pid`。
- 指控响应：`/home/bitcq/ccs_edge_ws/log/ground_air_agv/map_stream.log`；ROS 节点日志位于同目录 `ros/latest/epgeneral_map_stream-1.log`（编号以现场为准），会话日志位于会话目录 `ground_air_mapping.log`。
- ROS home：`/home/bitcq/ccs_edge_ws/run/ros_home`；部署备份与验收证据分别位于工作区内 `.deployment_backups/`、`artifacts/`。

<a id="s4-回滚"></a>

#### 回滚

确认没有活动建图/重定位后，按本次备份清单逐文件原子恢复客户端、元数据、测试和文档，并保留权限。此次短期客户端修复不需要重启、构建或修改 unit；服务保持部署前的 active/inactive 状态和已禁用的上电自启动状态。复核目标文件与原始 launch 的校验值，并运行实际预检。仅回滚旧客户端会重新出现 guard `2` 被拒绝的问题，应将其作为已知回退结果记录，不能通过改 manager 参数绕过。

若回滚的是未来包含常驻包或帧配置的其他批次，按该批次清单恢复、增量构建并按原 active/inactive 状态重载所需进程；仍保持上电自启动禁用。帧配置变更必须同时恢复端侧 YAML、指控运行 JSON 及发布默认镜像，随后确认准备和预览分片契约一致。

<a id="source-5"></a>

## 历史材料 5：documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s5-ground-air-agv-建图部署日志"></a>

### Ground-Air AGV 建图部署日志

<a id="s5-2026-09-05-guard-版本不匹配诊断与兼容修复"></a>

#### 2026-09-05 guard 版本不匹配诊断与兼容修复

- 现场 `prepare_mapping` 请求 `8d20c14b` 于 11:00:45.300 到达；点云、IMU 和存储检查通过，11:00:49.820 在 `map_generation` 预检失败，返回 `MAP_GENERATION_UNAVAILABLE`。后续重发命中相同请求的缓存结果。
- 运行 manager 已位于 `/home/bitcq/ccs_edge_ws/src/EPGeneral_ground_air_control/scripts/ground_air_stage_manager_node.py`，发布 `ccs_session_guard_version=2`；客户端 `EPGeneral_map_stream/scripts/ground_air_stage_client.py` 原第 66 行仍精确要求 `1`。实际只读 `--check` 复现 exit `1`，不是缺少保护机制或传感器不可用。
- 重定位增量升级同步了新 manager 与 guard `2` 启动检查，却遗漏建图客户端适配和实际建图预检。v2 manager 继续接受原建图 caller，修复客户端即可保持原会话归属协议。
- 修复版本为 `epgeneral_map_stream` v0.13.2：明确接受整数 guard `1`、`2`，保留未知值拒绝、外部 TF 检查、停止/取消语义。补充客户端入口及 v2 manager 回归，并为后续重定位增量部署增加客户端同步和真实预检。
- 服务上电自启动已按用户要求禁用。实际部署批次为 `/home/bitcq/ccs_edge_ws/.deployment_backups/20260905T032815Z_mapping_guard_v2`；清单记录 9 个目标文件的部署前后 SHA-256 与权限，文件均先在工作区内校验再原子替换。客户端最终 SHA-256 为 `d9ccec1f9c97ad39d66473b5c33b8a0486b82a2e1e67c77547b95802ef94c97d`。部署没有重启服务，也未修改 manager、车辆 underlay、unit 或生产 profile。
- 首次端侧 25 项聚焦测试有 10 项仅因仓库样例 `EPGeneral_device_config/config/map_stream.yaml` 未随 AGV 生产包部署而报 `FileNotFoundError`，其余 15 项通过。随后以 `/home/bitcq/ccs_edge_ws/.deployment_backups/20260905T035900Z_mapping_guard_test_fixture` 备份并部署支持 `CCS_MAP_STREAM_TEST_MAPPING` 的测试入口，将仓库样例放在 `.deploy` 隔离目录后重跑；25 项全部通过。候选部署脚本 `bash -n`、版本一致性检查和实际 `ground_air_stage_client.py --check` 均通过。
- 第一次静态闭环误把地面站监听端口设为 `14572`，与生产 profile 的回传端口 `14562` 不一致，因此只得到地面站超时，不能作为端侧失败结论。改回 `14562` 后，会话 `90a4443f4f2d41d89e6fdb3a91cc28a6`（地图 `agv-static-20260905-113150`）完成准备、开始、重复开始幂等、11 个实时 PCD 分片、保存和成果下载；首分片 54234 点、650949 字节，帧契约为 `odom <- camera_init`。
- 最终 ZIP 为 `artifacts/agv_mapping_guard_fix_20260905/cycle2/agv-static-20260905-113150.zip`，大小 248867 字节，SHA-256 为 `ba48d3cba52d3d1b2e5f19c47f2a00df946e181a2c8d363ef6bd080e8bf0400b`；包含 `map.pcd` 273436 字节、`map.pgm` 42275 字节、`map.yaml` 119 字节和 `manifest.json` 700 字节，成果帧为 `map`。
- 最终复核时服务仍为 `disabled/inactive`，现有手动启动栈继续运行：supervisor PID 6424、roscore PID 6500、map-stream PID 8160、manager PID 8803，启动时间和部署前一致；两条静态 TF PID 9162/9163 均自 10:58:18 持续运行。stage 为 `BASE=0`，UDP 14561/14565 正常监听，无 FAST-LIO、地图记录器、world-TF owner、重定位器或 CCS 会话节点残留。四个 underlay launch 校验值未变化。

以下为历史部署记录，其中旧路径和当时的 `enabled` 状态不代表当前部署要求。


<a id="s5-2026-09-01-epgeneral_map_stream-v0130-增量部署"></a>

#### 2026-09-01 `epgeneral_map_stream` v0.13.0 增量部署

- 目标设备：`AGV_001` / `192.168.50.130`；部署仅涉及建图响应，不下发车辆运动、解锁、模式切换或航点。
- 备份目录：`/home/bitcq/.deployment_backups/20260901T105207Z_ground_air_mapping_v013`。部署前控制 launch 为 `348669...30fe`，profile 为 `5f445d...0f35`；TF 修正前版本也保留于该目录。
- 原始 `manual_mapping.launch` 部署前后 SHA-256 均为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`；`save_mapping.launch` 为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`，均未修改。
- 最终新增 `manual_mapping_control.launch` SHA-256 为 `5f5be8916be43a0ce687dbbebbb1e3f35b275e4f6cee43db63161433d4d3986b`；AGV map-stream profile 为 `f8b501d4a1c0bca8342c0351535e192bf02557094d502a92ca2c3851f6cd9750`。
- 端侧目标测试 63 项通过、1 项按环境跳过；Python 编译、Bash 语法、版本一致性、launch 解析和 Release `catkin_make -j1 --force-cmake` 通过。Scout 专属 fixture 未部署在 AGV，因此未纳入目标增量套件。
- 首轮 12 秒和 60 秒静态测试验证了准备、FAST-LIO、预览、重复开始幂等及“保存失败保活”；保存失败原因为当前自启动未提供 `odom -> camera_init` 和 `body -> base_link`，导致动态栅格没有 `/map` 输出。测试均通过本会话 abort 清理，无建图节点残留。
- 将这两条静态 TF 加入新增控制 launch 并纳入预期节点检查后，完成两次静态闭环：`20260901_193200` 与 `20260901_193342`。两次均生成非空 PCD/PGM/YAML/metadata，下载归档 SHA-256 分别为 `4c397b102002134eb543e7a155217aafeb7595f3835291ea48468ec9aff265abc` 和 `8717a71455d6e90b809d84d013118995fe019ce307a72102b3a22f5e41581fed9`。
- 第二次闭环地图文件大小：`cloud_map.pcd` 318472 字节、`map.pgm` 47677 字节、`map.yaml` 120 字节、`metadata.json` 188 字节。结束后建图节点和 roslaunch 进程组均退出，MAVROS、Livox、MQTT、UDP 遥测、map-stream、A8 与 SRT 继续运行，UDP 14561 正常监听。
- 本地验收归档与事件记录保存在 `artifacts/agv_incremental_test/`；端侧地图保存在 `/home/bitcq/catkin_ws/maps/`。静态测试只验收流程与产物，不评价地图精度。
- 2026-09-02 补充“保存中以新 request ID 重复结束”处理：不再返回通用 map mismatch，而是幂等接受并回报当前 `generating`，不创建第二个保存线程。端侧 65 项目标测试通过、2 项按环境跳过，增量构建通过。
- 发现普通 `nohup` 启动仍关联 SSH 会话，断开后 supervisor 退出。现改为 `setsid -f` 脱离终端启动，实测 supervisor `PPID=1`、无 TTY；SSH 断开重连后全部常驻节点、UDP 14561 和 TCP 14600 继续在线。

<a id="s5-2026-09-02-fast-lio-后置坐标转换-launch-增量部署"></a>

#### 2026-09-02 FAST-LIO 后置坐标转换 launch 增量部署

- 备份目录：`/home/bitcq/.deployment_backups/20260902T023030Z_mapping_tf_sequence`；备份了修改前的控制 launch、AGV profile、建图进程脚本和配置解析代码。
- 新增 `~/catkin_ws/src/car_bringup/launch/mapping_coordinate_transforms.launch`，只包含 `odom_frame/camera_init_frame/body_frame/base_frame` 四个参数，以及 `/odom_camera_init_broadcaster`、`/base_link_body_broadcaster` 两个静态 TF 节点。其 SHA-256 为 `0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553`。
- `manual_mapping_control.launch` 不再内嵌 TF 节点，SHA-256 为 `627eae854fd0c5ef4b177bc4728bde9e613db34b22df9e32d456318ca188970b`。进程管理流程调整为：启动该控制入口，等待 `/fast_lio_node`，再启动独立坐标转换 launch，最后等待全部建图节点就绪；两个 roslaunch 仍属于同一受控进程组。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`，均未修改。
- 端侧 Bash/Python/XML/launch 解析通过；AGV 目标套件运行 65 项，63 项通过、2 项按环境跳过；Release 增量构建通过。全量发现的 7 个 Scout fixture 错误仅因 AGV 未部署 `/home/nvidia/ccs_edge_ws/config/scout_mini`，与本次变更无关。
- 12 秒静态闭环地图目录为 `/home/bitcq/catkin_ws/maps/20260902_104551/`：`cloud_map.pcd` 320764 字节、`map.pgm` 44096 字节、`map.yaml` 118 字节、`metadata.json` 188 字节。地面站验收归档为 `artifacts/agv_incremental_test/tf_sequence_20260902/agv-static-20260902-104545.zip`，大小 290786 字节，SHA-256 为 `03bd12332f11ebf69e9e7b5b5065cda82b29205f061815f5edfac853110803c7`。
- 会话日志顺序为 `stage=ground_air_mapping action=start`、`stage=coordinate_transform action=start ... after_node=/fast_lio_node`、`stage=ground_air_mapping action=ready`。保存成功后无 FAST-LIO、坐标转换或建图 roslaunch 残留，MAVROS、Livox、MQTT、UDP 遥测、map-stream、A8 和 SRT 均继续运行。
- 统一 supervisor PID 9897 保持 `PPID=1` 且无 TTY；SSH 断开重连后 `/epgeneral_map_stream`、UDP 14561 和 TCP 14600 均继续在线。

<a id="s5-2026-09-02-坐标转换改为开机常驻"></a>

#### 2026-09-02 坐标转换改为开机常驻

- 将 `mapping_coordinate_transforms.launch` 的所有权从建图会话移至 `~/ccs_edge_ws/start_ccs_edge_dev.sh`。统一启动脚本在其他业务节点前启动 `mapping_tf`，等待 `/odom_camera_init_broadcaster` 和 `/base_link_body_broadcaster` 均就绪后才继续。
- 建图会话恢复为只持有 `manual_mapping_control.launch`。AGV profile 不再配置坐标转换 launch 或 FAST-LIO 后置门槛；建图预期节点仍检查两条常驻 TF，建图结束不会停止它们。
- 新增并启用 systemd 用户服务 `~/.config/systemd/user/ccs-edge-dev.service`，`Restart=on-failure`；用户 linger 已启用。验收时服务为 `enabled/active`，`NRestarts=0`，SSH 断开后仍运行。
- 仅用 `setsid` 临时拉起时，SSH 被端侧重置后服务消失，首次静态重测返回 `timed out waiting for expected mapping response`。确认端侧原无启用的 CCS 开机服务后，改由 systemd 用户服务监管并重新测试通过；未恢复已禁用的旧桌面整车自启动入口。
- 部署后 `start_ccs_edge_dev.sh` SHA-256 为 `0888f3bd8abb23ffddc19a4f9c6a9b9a6c5eb4984749be866b358a0bf723b1e2`，AGV map-stream profile 为 `9f407bea824e0b4f07a06b859a6f7c8504a94b4f8348d4c95cbd67157cf3a6b5`。回滚备份位于 `/home/bitcq/.deployment_backups/20260902T_tf_autostart`。
- 端侧目标套件运行 65 项，63 项通过、2 项按环境跳过；Bash/Python/YAML/systemd unit 校验和 Release 增量构建通过。
- systemd 接管后完成 12 秒静态闭环，地图目录为 `/home/bitcq/catkin_ws/maps/20260902_150431/`：`cloud_map.pcd` 322132 字节、`map.pgm` 45837 字节、`map.yaml` 118 字节、`metadata.json` 188 字节。地面站归档为 `artifacts/agv_incremental_test/tf_autostart_20260902/agv-static-20260902-150427.zip`，大小 292631 字节，SHA-256 为 `adcb14879accac21ccb0afeb7fcfbb2236957083068968f953ab84df430af0a7`。
- 建图结束后无 FAST-LIO 或建图 roslaunch 残留；两个 TF 节点继续存在，`odom -> camera_init` 与 `body -> base_link` 均实测为零平移、单位四元数。原始 `manual_mapping.launch` 和 `save_mapping.launch` 校验值保持不变。
- 按端侧仅增量测试要求，未执行整机重启；通过 systemd `enabled/active`、linger、SSH 断开重连和建图闭环验证开机服务配置与运行保持性。

<a id="s5-2026-09-02-stage-manager-后置启动与指控接入"></a>

#### 2026-09-02 stage manager 后置启动与指控接入

- 备份目录：`/home/bitcq/.deployment_backups/20260902T093624Z_stage_manager_ccs`。部署只更新统一启动脚本、`epgeneral_map_stream` 的 Ground-Air 适配器、stage manager/runtime 和本文档；未修改自启动配置或整车其他包。
- 启动脚本不再提前执行 `mapping_coordinate_transforms.launch`。实测启动顺序最后一项为 `rosrun car_bringup ground_air_stage_manager_node.py`：supervisor 于 18:07:10 启动，A8 于 18:08:08、SRT 于 18:08:15 就绪，manager 于 18:08:16 启动，服务于 18:08:23 完成检查。
- manager 开机处于 `BASE=0`，此时没有 FAST-LIO 或建图 TF 节点；指控开始通过 `/ground_air/system/set_stage` 进入 `MAPPING=1`，由 manager 先启动 `manual_mapping_control.launch`，再启动两个坐标转换节点。保存成功后通过同一服务回到 BASE；保存失败保活逻辑保持不变。
- 增加 `ccs_session_guard_version=1` 和 caller/map_id 归属校验。指控只能停止自己启动的会话，不能接管人工建图、重定位或另一指控会话；相关顺序、幂等、错误清理和停止失败测试均通过。
- 首次部署门禁报告一项既有版本断言不一致：样例配置已为 `0.13.1`，`test_config.py` 仍断言 `0.13.0`。该测试不在本次修改清单中，已如实保留并从本次门禁排除；其余目标测试 67 项通过、2 项按环境跳过，Python/Bash 检查与 Release 增量构建通过。
- 第一次静态闭环在客户端执行被中断后，确认 manager 仍安全持有会话，再以原 session/map_id 续发正常结束指令。地图目录 `/home/bitcq/catkin_ws/maps/20260902_180906/`：PCD 450100 字节、PGM 50212 字节、YAML 119 字节、metadata 187 字节；下载归档 407621 字节，SHA-256 为 `853fb6b97e8964233baae9b5ec15664fc847df595e1d33524b978aa14eb745b6`。
- 第二次完整静态闭环验证准备、开始、重复开始幂等、点云预览、保存和再次复位。地图目录 `/home/bitcq/catkin_ws/maps/20260902_181204/`：PCD 374704 字节、PGM 50425 字节、YAML 119 字节、metadata 188 字节；下载归档 339513 字节，SHA-256 为 `0301445a21a9cb2f69c612be8a8c81eb6b15b6ee76f94d44938a8857dae6fc31`。
- 最终 `ccs-edge-dev.service` 为 `enabled/active`，`Linger=yes`，stage 为 BASE；无 FAST-LIO、建图 roslaunch、坐标转换节点或孤儿进程，MAVROS、Livox、MQTT、UDP、map-stream、A8、SRT 和 manager 继续运行。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`。
- 本次证据保存在 `artifacts/agv_incremental_test/stage_manager_ccs_20260902_interrupted/` 与 `stage_manager_ccs_20260902_cycle2/`；只执行静态增量测试，未下发运动、解锁、模式或航点指令，也未制造磁盘/权限故障。

<a id="s5-2026-09-02-tf-改为自启动常驻管理"></a>

#### 2026-09-02 TF 改为自启动常驻管理

- 根据最新要求，保留统一启动脚本最后执行 `rosrun car_bringup ground_air_stage_manager_node.py`，但将两个静态 TF 的生命周期从建图阶段移到 manager 自启动阶段。manager 在开放 `set_stage` 服务前启动并验证 `mapping_coordinate_transforms.launch`；建图/重定位阶段只启动主功能并检查完整 TF 链。
- 备份目录：`/home/bitcq/.deployment_backups/20260902T103745Z_stage_manager_ccs`。部署更新了 manager、runtime、stage core、对应端侧测试、统一启动脚本和部署文档；原始建图与保存 launch 未修改。
- 端侧目标响应测试运行 67 项，65 项通过、2 项按环境跳过；`car_bringup` 阶段与契约测试 18 项全部通过；Python/Bash 检查和 Release 增量构建通过。
- 自启动实测：SRT 节点于 18:39:14 启动，stage manager 于 18:39:17 启动，TF roslaunch 于 18:39:20 由 manager 启动，统一入口于 18:39:27 确认全部就绪。BASE 阶段 `resident_tf_version=1`，`odom → camera_init` 与 `body → base_link` 均为零平移、单位四元数。
- 建图前两个 TF 节点 PID 为 `31677`、`31678`。一次静态开始—重复开始—保存闭环后 PID 仍为 `31677`、`31678`，证明建图流程未启动第二套 TF，也未在结束时停止或重启常驻 TF。
- 静态地图目录：`/home/bitcq/catkin_ws/maps/20260902_184110/`；PCD 316264 字节、PGM 43497 字节、YAML 118 字节、metadata 187 字节。下载归档 285842 字节，SHA-256 为 `361eb000bb77d06a4f89d629e80ad38be67b5ecb87c09dfe08d40f6a3c658f3f`。
- 闭环后阶段为 `BASE=0`，FAST-LIO、建图节点和 `manual_mapping_control.launch` 均退出，两个常驻 TF 节点继续运行，`ccs-edge-dev.service` 保持 active。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`。
- 验收证据保存在 `artifacts/agv_incremental_test/resident_tf_20260902/`；仅执行静态增量测试，未发送运动、解锁、模式或航点指令。

<a id="s5-2026-09-03-静态-tf-改由自启动-launch-直接管理"></a>

#### 2026-09-03 静态 TF 改由自启动 launch 直接管理

- 按最新要求，统一启动脚本在 stage manager 之后、所有功能包的最后执行 `roslaunch car_bringup mapping_coordinate_transforms.launch`。该 launch 最终 SHA-256 为 `0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553`，展开后仅有 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster` 两个静态 TF 节点。
- 首轮部署备份为 `/home/bitcq/.deployment_backups/20260903T093730Z_stage_manager_ccs`。首次 12 秒静态测试在开始阶段返回 `primary stage nodes did not become ready`：旧控制入口仍经 `start_mapping.launch -> mapping_system.launch -> mapping_coordinate_transforms.launch`，既不再能从精简后的 TF launch 启动 FAST-LIO，又会以同名节点替换开机 TF。建图进程退出后 supervisor 检测到 TF 缺失并完成一次自动重启，未遗留建图进程；该失败未被隐去。
- 修正版不修改原始 `manual_mapping.launch`、`mapping_system.launch` 或 `start_mapping.launch`，而是将新增 `manual_mapping_control.launch` 改为直接组合 FAST-LIO、里程计适配、滤地、动态栅格、地图记录器、mapping 模式 world-TF owner 和 start service 调用。其 SHA-256 为 `0a00597b087b2941b0f708957e3301ce35033a0354ebb43d7efb89d5a4afcc64`。
- 同步新增 `relocalization_control.launch`（SHA-256 `4574f03bcd88c6f26638a6323c6b413e490c3b65461a6947529119506dfe09f6`），避免 stage 2 再经旧重定位入口重复启动静态 TF；`system_stage_core.py` SHA-256 为 `755120930dcaabcde2a2e5e502463eefa5ecef8566ada8df739993de02024b95`。
- 最终增量包 SHA-256 为 `c429e0c5efe791974e994880323d1b6a2c570b453a29db401d6eccfdd42d6410`，部署备份为 `/home/bitcq/.deployment_backups/20260903T101513Z_stage_manager_ccs`。脚本记录部署前 enabled/active 状态、19 个既有文件和 1 个新增文件，并在重启后等待实际 ROS 就绪。
- 端侧 map-stream 目标测试 69 项通过、2 项按环境跳过；`car_bringup` core/manager/launch 契约测试 18 项全部通过；Bash、Python、XML、systemd 校验和 Release 增量构建通过。暂存 launch 经 `roslaunch --nodes` 展开确认：建图入口 8 个阶段节点、重定位入口 6 个阶段节点，均不含两个静态 TF 发布者。
- 最终服务为 `enabled/active`、`KillMode=mixed`、`NRestarts=0`。supervisor PID 为 `53039`，stage manager PID 为 `55046`；建图前后两个静态 TF PID 均保持 `55368`、`55369`。实测 `odom -> camera_init` 与 `body -> base_link` 都是零平移、单位四元数，`/tf_static` 中除 MAVROS 外仅有这两个发布者。
- 12 秒静态开始、重复开始、保存闭环通过，地图目录为 `/home/bitcq/catkin_ws/maps/20260903_181854/`：`cloud_map.pcd` 318040 字节、`map.pgm` 46049 字节、`map.yaml` 118 字节、`metadata.json` 188 字节。地面站归档为 `artifacts/agv_incremental_test/tf_direct_launch_20260903_v2/agv-static-20260903-181848.zip`，大小 287595 字节，SHA-256 为 `f319697f07ced6e23a91464f18c6a95df15908038c1d706b1eb137f71a76e0b0`。
- 闭环结束后 stage 为 `BASE=0`，无 FAST-LIO、地图记录器、world-TF owner、动态栅格或 `manual_mapping_control.launch` 残留，基础服务和两条静态 TF 继续运行。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`，均未修改。原命令保留为历史/整套回滚参考；当前诊断使用新增控制入口，旧命令在现行 TF 架构下不保证独立完成建图。
- 本次仅执行静态增量测试，未重启整车，未发送运动、解锁、模式切换、位置或航点指令，也未主动制造保存失败。

<a id="s5-2026-09-03-建图预览坐标系契约修复与端侧验收"></a>

#### 2026-09-03 建图预览坐标系契约修复与端侧验收

- 指控会话 `4163d5ac` 的准备请求在端侧完成全部就绪检查并返回 `accepted=true`，重复下发由 request cache 幂等应答；指控随后报告端侧 `camera_init` 与要求的 `odom` 不一致并自动取消。该现象不是多会话、重复 FAST-LIO 或静态 TF 冲突。
- 根因是准备阶段仍使用全局 `remote_mapping=odom` 校验，而原 `AGV_001` 设备配置和端侧预览均为 `camera_init`。单独把端侧返回标签改为 `odom` 只能绕过准备，首个分片仍会与设备配置冲突，因此不采用伪造 frame 标签的兼容方式。
- 修复基线为端侧 `ros.frames.map=camera_init`、`ros.frames.preview=odom`、`artifacts.frame=map`；MapStream 使用自启动常驻的 `odom <- camera_init` TF 实际转换 PCD 点坐标。协议输出固定为准备 `frame_id=odom`、分片 `frame_id=odom/source_frame_id=camera_init`、成果 `frame_id=map`。
- 指控侧只同步运行配置和发布默认镜像中的 `device_frames.AGV_001`：`remote_mapping=odom`、`preview_source=camera_init`、`remote_artifact=map`；不修改 `ccs_monitor/map_building_v2.py`，wire schema 和端口不变。
- 首次执行增量门禁时，现场 `start_ccs_edge_dev.sh` 的 SHA-256 `84f41b241e7a9bb60ef8f5e665b36425e8438adcc33375c841a5e3c9474eec95` 不在已知列表，脚本在写入前安全中止。逐行审计确认该文件将 stage manager 启动和能力检查注释掉，mtime 为 20:20，晚于当前服务 18:15 的启动时间；运行中的 manager 仍是该服务的子进程。将该已审计版本纳入门禁后，最终部署用仓库版本恢复统一启动脚本对 manager 的启动和能力检查所有权。
- 第二次执行创建备份 `/home/bitcq/.deployment_backups/20260903T133725Z_stage_manager_ccs` 并完成文件写入，但在服务重启前因部署脚本只加载 CCS overlay、无法解析 `fast_lio_open3d` 而中止。修复后所有部署检查统一按 `/opt/ros/noetic/setup.bash -> /home/bitcq/catkin_ws/devel/setup.bash --extend -> /home/bitcq/ccs_edge_ws/devel/setup.bash --extend` 加载环境，确保车辆包和 CCS 包同时可见。
- 最终 v3 增量包为 `artifacts/agv_mapping_frame_contract_20260903_v3.tar.gz`，SHA-256 `971dc9da7276aec87b294e8ade902247d0918bff479c0af2eb3da24483030935`；成功部署备份为 `/home/bitcq/.deployment_backups/20260903T134200Z_stage_manager_ccs`。端侧目标套件运行 69 项并报告 `OK (skipped=2)`，`car_bringup` manager/core/launch 契约测试 18 项全部通过，`epgeneral_map_stream` 增量编译通过。
- 最终 `ccs-edge-dev.service` 为 `active/running`、`NRestarts=0`；stage manager PID 为 `269026`，静态 TF roslaunch PID 为 `269306`，两个 publisher PID 为 `269354`、`269355`。部署后 profile SHA-256 为 `2d8f3a2e154e293030847c96ba87c6cb34975e73d09d128793fa5503a1bd7e77`；原始 `manual_mapping.launch` 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`。
- 第一轮静态协议闭环 `map_id=agv-static-20260903-214633`、`session_id=52f10acb8baf4796882d660cb8cbe2eb`，ZIP 291066 字节，SHA-256 `8cef6dd4230ea90940c74803bd08b1731e4f1b2e7d51f999f3d34f0689ab8f3f`。端侧归档 `20260903_214639` 包含 PCD 322492 字节、PGM 45837 字节、YAML 118 字节。
- 第二轮静态协议闭环 `map_id=agv-static-20260903-214755`、`session_id=c0171dafea764f01b779c2cdce060cb8`，ZIP 338820 字节，SHA-256 `64ccbbc1d5f7f4a2a32e3e47004e0d719fac267c4045070101f94a2e2a127f95`。端侧归档 `20260903_214801` 包含 PCD 375280 字节、PGM 45181 字节、YAML 119 字节。
- 增加 HTTP PCD 实体校验后完成一次短时补充闭环：`map_id=agv-static-20260903-215315`、`session_id=23ab449461f049a3bb2d3f5c17cee303`。首个分片实际下载 583449 字节、48609 点，SHA-256 `228d6d9bd6fc206c1fd544b86b05b088e3d24b86e8a101bbfabe01c2deb445f4`；二进制 PCD header、声明长度和每点 12 字节 XYZ 载荷均通过校验。最终 ZIP 278150 字节，SHA-256 `274fa75f132f2655336f3eb363bfdd2d604b4710bab182b4d973ea8748de85ec`，帧契约与前两轮一致。
- 三次闭环均强制验证 `prepare=odom`、分片 `frame_id=odom/source_frame_id=camera_init`、有限且完整的单位 `display_from_source`、成果 manifest `frame_id=map` 及 PGM/YAML 路径、字节数和 SHA-256。重复开始均幂等接受；结束后均回到 `stage=0`，无 FAST-LIO 或建图进程残留，静态 TF PID 保持不变。验收脚本遇到任一契约或成果校验不匹配都会返回失败。
- 验收证据保存在 `artifacts/agv_incremental_test/frame_contract_20260903_cycle1/`、`frame_contract_20260903_cycle2/` 和 `frame_contract_20260903_pcd_probe/`。本轮仅执行无运动增量测试，未重启整车、未发送运动/解锁/模式/航点指令，也未主动制造保存故障。
- 验收时指控主机没有运行中的 CCS 进程，也没有 UDP 14562 监听，因此不需要停止或重启进程；仓库运行配置与 release 默认镜像已同步，下次启动直接加载新帧契约。回滚仍必须同时恢复端侧 YAML 与指控 JSON，并按部署前状态恢复两端服务。

<a id="source-6"></a>

## 历史材料 6：documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s6-ground-air-agv-重定位部署说明"></a>

### Ground-Air AGV 重定位部署说明

CCS 0.23.1 当前入口：[使用手册](../../../documents/USER_MANUAL.md) · [接口与配置](../../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

<a id="s6-运行契约"></a>

#### 运行契约

`AGV_001` 使用 `ground_air_agv` profile。指控依次协商地图、下发地图、启动栈并提交 `map` 坐标系初始位姿。端侧协调器实际创建的顶层命令为：

```bash
roslaunch car_bringup relocalization_system.launch map_id:=<map_id>
```

该命令仅在子进程内将 `/home/bitcq/ccs_edge_ws/overrides` 前置到 `ROS_PACKAGE_PATH`，并从该子进程的包搜索路径与 `CMAKE_PREFIX_PATH` 排除车辆 underlay 根，避免 ROS1 因发现两个同名 launch 而拒绝启动。Python 与动态库路径保持不变；驻留 manager 仍使用完整 underlay，并从中只读加载 FAST-LIO、管线和定位依赖。覆盖 launch 请求 stage 2，驻留的 `epgeneral_ground_air_control` 管理器唯一启动 `relocalization_control.launch`。

精简栈只包含 FAST-LIO、既有里程计管线、Ground-Air localization 和初始位姿适配器。Livox、MAVROS、MQTT、相机、车辆基础节点及 `odom -> camera_init`、`body -> base_link` 静态 TF 均复用手动启动一键栈后的常驻实例。适配器调用 `/ground_air/load_map`，收到 `/initialpose` 后以 `use_initial_guess=true` 调用 `/ground_air/relocalize`；栈就绪检查使用 Ground-Air 实际发布的 `/map`，不使用其他设备 profile 的 `/map_2d`。

地图传输协议和 ZIP 清单继续使用 `public_map.pcd`。端侧完成大小、SHA-256 和 PCD 内容校验后，在原子安装到 `ccs_edge_ws/maps/download/<map_id>` 前将其改名为 `cloud_map.pcd`。这是 Ground-Air 定位器要求的处理后地图名，目录中仍只有一个 PCD；Scout/Wheeltec profile 不受影响。

<a id="s6-tf-上报"></a>

#### TF 上报

Ground-Air 不使用 Scout/Wheeltec 的稳定窗口。端侧每 1 秒查询最新动态 `map <- odom`：

- 首个有限、四元数有效、时间未过期的样本立即返回成功；仅首样本受 30 秒超时约束。
- 同一会话继续每秒发送 `relocalization_result(state=succeeded)`。
- 有新时间戳时更新校准值；设备静止、TF 时间戳未更新或单周期查询不到时，沿用并重发最后一个有效值，不把静止误判为失败。
- 首个结果立即持久化，后续每 30 秒及正常退出时刷新；平台地图显示始终使用最新内存样本。

协议继续使用 `ccs-relocalization-v1`、端侧 UDP 14565、平台 UDP 14566 和地图 HTTP 14601，不新增消息类型或字段。

<a id="s6-部署边界"></a>

#### 部署边界

`AGV_001` 上电自启动已禁用，服务保持 `disabled`；需要运行时手动执行 `systemctl --user start ccs-edge-dev.service`。增量部署和回滚记录并保持 enabled/active 状态，不修改现有 unit、linger 或上电启动设置。

增量包必须解压到 `/home/bitcq/ccs_edge_ws/.deploy/<批次>`，并执行 `deploy_relocalization_update.sh`。脚本对源码根目录以及每个目标、备份、临时和验收目录执行 `realpath` 包含性校验，并将 `TMPDIR`、`ROS_HOME` 与 `ROS_LOG_DIR` 显式重定向到工作区内。允许只读 source `/opt/ros/noetic` 和 `/home/bitcq/catkin_ws`，允许重启既有 `ccs-edge-dev.service`；禁止在 `ccs_edge_ws` 之外新增、覆盖、备份或生成临时文件。

部署前必须无活动 FAST-LIO、建图或重定位阶段。脚本记录原始 `manual_mapping.launch`、`save_mapping.launch`、`relocalization_system.launch` 和 `mapping_coordinate_transforms.launch` 校验值，部署后复核一致；构建本次实际涉及的包。

manager 从 `/home/bitcq/ccs_edge_ws/src/EPGeneral_ground_air_control/scripts/ground_air_stage_manager_node.py` 运行并发布 guard `2`，仍接受原建图 `/ccs_mapping_stage_<session>` caller。增量包必须同时包含 v0.13.2 建图客户端、关联版本元数据和回归测试，并执行版本检查；不能只升级 manager。此前客户端严格要求 guard `1`，即使重定位验收通过，建图仍会在准备阶段失败。此次客户端明确支持整数 guard `1`、`2`，服务签名与 caller/map_id 归属规则不变。

<a id="s6-增量验收"></a>

#### 增量验收

先运行 `rosrun epgeneral_map_stream ground_air_stage_client.py --check`，确认实际客户端接受当前 guard `2`、外部 TF 就绪、阶段仍为 BASE。仅检查节点列表或 manager 参数不足以确认建图兼容；部署前后 `is-enabled` 必须保持 `disabled`。

使用完整地图 `test60`，先确认基础节点、UDP 14565、阶段服务和两条常驻静态 TF 正常。协调器完成地图协商/下发后启动栈，提交 `(x=0, y=0, yaw=0)`。定位器接受初始位姿后至少观察 10 秒，要求：

- 只有一套 FAST-LIO、Ground-Air localization 和动态 `map -> odom` 发布者。
- 首次成功不等待稳定样本；若定位器持续发布，结果间隔约 1 秒且数值有限。设备静止而不更新 TF 时，允许跳过新数据检查，但平台应继续收到端侧按 1 Hz 重发的最后有效值。
- 平台状态保持成功，实时绑定跟随最新样本，不产生非法状态转换告警。
- 重复启动不增加进程；中止后阶段回到 BASE，常驻节点与静态 TF 继续运行。

若零位姿被 fitness/RMSE 质量门禁拒绝，不降低阈值；保存服务响应、点云、TF 和日志后，在地图上人工选点重试。验收禁止发送运动、航点、解锁或飞行模式指令。

<a id="s6-回滚"></a>

#### 回滚

备份位于 `/home/bitcq/ccs_edge_ws/.deployment_backups/<批次>`。按 `manifest.tsv` 处理本批次新增文件，并从 `files/home/bitcq/ccs_edge_ws/...` 恢复既有文件，包含同批次建图客户端和版本元数据；在工作区重新增量构建，并按部署前 active/inactive 状态恢复服务。上电自启动保持 `disabled`，不启用服务。回滚前后复核四个 underlay launch 的校验值和实际建图 `--check`；若恢复了仅接受 guard `1` 的旧客户端，其对 guard `2` 的预检失败属于已知回退，不能改参数掩盖。平台侧同时恢复该批次实际修改的 `AGV_001` profile 和 `config/relocalization.json`。

<a id="source-7"></a>

## 历史材料 7：documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT_LOG.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s7-ground-air-agv-重定位部署日志"></a>

### Ground-Air AGV 重定位部署日志

<a id="s7-2026-09-05-建图兼容回归修正"></a>

#### 2026-09-05 建图兼容回归修正

- 联合建图暴露出 9 月 4 日重定位升级的集成缺口：manager 和启动门禁已升为 guard `2`，map-stream 客户端仍精确要求 `1`。原重定位闭环结果保持有效，但不能代表建图准备阶段已通过。
- `epgeneral_map_stream` v0.13.2 明确支持整数 guard `1`、`2`；后续重定位增量包同步客户端、关联元数据和回归测试，并在实际就绪门禁中执行建图 `--check`。
- 本次兼容修复保持 manager、重定位协议、常驻 TF 和已禁用的上电自启动设置。端侧聚焦测试、实际建图预检及一次无运动建图闭环均已通过，完整备份、地图校验值与最终清理状态统一记录于 [建图部署日志](../../../documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md)；以下 9 月 4 日重定位历史结果保持不变。


<a id="s7-2026-09-04-实施记录"></a>

#### 2026-09-04 实施记录

- 目标：启用 `AGV_001` 两阶段重定位，并以固定 1 秒周期持续上报 `map <- odom`。
- 边界：端侧文件写入仅允许 `/home/bitcq/ccs_edge_ws`；车辆 underlay 和用户服务定义保持只读。
- 自动验收地图：`test60`；初始位姿 `(0, 0, 0)`。
- 最终增量包：`agv-relocalization-incremental-v6.tar.gz`，大小 102149 字节，SHA-256 `12814a3ca5c6ac6c1e53f09a1c684c76a35d6c03f4bc98e3480b667cca53c052`。
- 最终部署批次：`20260904T101248Z_agv_relocalization`；备份位于 `/home/bitcq/ccs_edge_ws/.deployment_backups/20260904T101248Z_agv_relocalization`，证据位于 `/home/bitcq/ccs_edge_ws/artifacts/relocalization_acceptance/20260904T101248Z_agv_relocalization`。

<a id="s7-问题与修正"></a>

#### 问题与修正

- 首次端侧启动检查使用了其他 profile 的 `/map_2d`，而 Ground-Air map server 实际发布 `/map`；修正 profile 后栈进入等待初始位姿。
- 第二次零位姿调用被定位器明确拒绝：`active point-cloud map must be the processed cloud_map.pcd`。协议只允许 `public_map.pcd`，Ground-Air 地图注册器又要求目录仅一个 PCD。本次保留 wire 校验，端侧原子安装前将 PCD 改名为 `cloud_map.pcd`，不修改 underlay。
- 根据静止设备行为，首个有效 TF 后改为每秒优先采新样本；没有新时间戳或查询暂时失败时重发最后有效值，不再因设备不移动判定连续缺失。

<a id="s7-验收结果"></a>

#### 验收结果

- 最终端侧部署脚本运行通用重定位 22 项和 Ground-Air 控制 7 项测试，增量编译两个包并验证覆盖 launch；平台及发布聚焦测试 54 项通过，`git diff --check` 通过。
- `test60` 的实际 `map_id` 为 `a60133b8-9915-4f08-8139-3483f4cfbdb9`。端侧安装结果仅包含 `cloud_map.pcd` 864496 字节、`map.pgm` 50139 字节和 `map.yaml` 123 字节。
- 两轮零位姿闭环均通过，每轮接收 11 个 `map <- odom` 成功结果，中位间隔均为 1.000 秒，协议告警为 0。结果中既有新样本，也有静止周期的重复值。
- 两轮质量门禁分别为 fitness `0.9922438345` / `0.9924242424`，RMSE `0.0454686665` / `0.0459282188`，无需人工选点或降低阈值。
- v6 追加两轮重复开始验收：第二次开始约 40 ms 即回到等待位姿，manager 日志仅有 1 次受管 stage 启动；两轮各收到 4 个结果，中位间隔 0.938 / 1.000 秒，fitness `0.9923991877` / `0.9923553599`，RMSE `0.0453172890` / `0.0453244340`。
- 测试结束后重启既有 `ccs-edge-dev.service` 释放受管进程组。最终 stage 为 `BASE`、guard 为 2、服务 `active/running`、`NRestarts=0`；无 FAST-LIO、定位器、地图服务或重定位 launch 孤儿节点，常驻 MAVROS、Livox、阶段管理器、重定位响应和两条静态 TF 正常。
- underlay 四个 launch 部署前后 SHA-256 一致：`manual_mapping.launch=fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`、`save_mapping.launch=62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`、`relocalization_system.launch=0df0e8480dc20ceea346f9c6b7d3b6a749b91639fc329f1d208ad346d8ea5909`、`mapping_coordinate_transforms.launch=0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553`。
- 本机生产 CCS 在隔离验收前正常关闭，验收后恢复，UDP 14566 与 TCP 14601 均由同一进程监听。全程未下发运动、解锁、模式或航点指令，也未在 `/home/bitcq/ccs_edge_ws` 外写入端侧文件。
- 完整平台套件共 352 项，350 项通过；剩余 2 项为最新 `main` 已存在的日间主题颜色映射缺口和 Wheeltec 默认 profile 断言，与本次文件无交集。对应本次范围的聚焦回归全部通过。

<a id="source-8"></a>

## 历史材料 8：documents/GROUND_AIR_AGV_TASK_DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s8-ground-air-agv-地面任务部署"></a>

### Ground-Air AGV 地面任务部署

目标设备为 `AGV_001`。端侧部署根目录固定为 `/home/bitcq/ccs_edge_ws`；`/home/bitcq/catkin_ws`、系统 unit 和 `/etc` 只读。用户服务在部署前后必须保持 `disabled`。

部署清单包括 `EPGeneral_task_control`、`EPGeneral_ground_air_control`、`config/ground_air_agv/task_control.yaml` 和 `start_ccs_edge_dev.sh`。部署前将相同目标复制到 `.deployment_backups/agv_task_<UTC>/`，新文件先上传到 `.tmp/agv_task_<UTC>/`，核对 SHA-256 后再替换。失败时停止本次启动的服务，按清单从备份恢复，并仅增量重建两个受影响包。

增量构建：

```bash
cd /home/bitcq/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source /home/bitcq/catkin_ws/devel/setup.bash --extend
catkin_make --pkg epgeneral_task_control epgeneral_ground_air_control \
  -DPYTHON_EXECUTABLE=/usr/bin/python3
```

无运动验收必须确认：

- `systemctl --user is-enabled ccs-edge-dev.service` 始终返回 `disabled`。
- 平台 UDP 14564 与 NTP 123 已监听，端侧 `NTPSynchronized=yes` 且使用 `192.168.50.101`。
- 一键启动后协调器、AGV 适配器和常驻控制节点存在，导航与任务执行器在没有已提交任务时不存在。
- 协商命令获得 ACK；未定位时提交任务只进入 `received/failed` 并自动重试，不产生非零速度、解锁或模式切换命令。
- 无已存任务的急停仍调用机器人服务；机器人未确认时 ACK 失败，确认后状态保持 `emergency_stop`。

现场实车验收另行进行：完成时间同步和重定位后，由操作者手动解锁并切入 OFFBOARD，使用 0.1 m/s 以内短路线验证航点、停留、终止和运动中急停。未完成该步骤时文档必须记录“实车验收待完成”。

2026-09-07 的实际部署、静态验收、证据目录和回滚基线见 `GROUND_AIR_AGV_TASK_DEPLOYMENT_LOG.md`。

<a id="source-9"></a>

## 历史材料 9：documents/GROUND_AIR_AGV_TASK_DEPLOYMENT_LOG.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s9-ground-air-agv-地面任务部署记录"></a>

### Ground-Air AGV 地面任务部署记录

<a id="s9-2026-09-08-连续两轮实车复验通过"></a>

#### 2026-09-08 连续两轮实车复验通过

最终修复在 CCS 适配器调用准备和启动服务前有界等待新鲜定位位姿（最多 1.5 秒，
样本年龄不超过 0.2 秒），保留原生 0.5 秒保护和 2 秒 UTC 容差，不自动复位 FAULT。
原生定位 tracking_timer 固定 1 秒发布，与控制层 0.5 秒门限不匹配；实测发布间隔
约 0.5 至 1 秒。这解释了执行时机不同造成的间歇性拒绝。

最终版本在端侧通过 26 项隔离增量测试和单包增量构建。现场操作者完成重定位、
解锁及 OFFBOARD 后，复验现有 test_AG_ 任务 revision=14，6 个航点，0.1 m/s：

- 第一轮 `agv-retest-1-181b17f7`：completed，waypoint_index=5，progress=1.0。
- 第二轮 `agv-retest-2-cff6eaa1`：completed，waypoint_index=5，progress=1.0。
- 两轮之间未重启控制器或导航栈；结束后 GROUND、localized=true、无急停。
- 两轮均未复现 local pose is stale 或 ground configuration 拒绝。

反馈和摘要保存在 `/home/bitcq/ccs_edge_ws/artifacts/agv_retry_20260908/` 的
round1-feedback.txt、round2-feedback.txt 和 two-round-summary.json。
原生控制源码校验未改变。本次结论仅覆盖连续执行故障复验，不代表运动中急停
等其他专项验收已经完成。下文为此前部署与排查历史，状态以本节为准。

<a id="s9-2026-09-08-本地故障修订尚未部署"></a>

#### 2026-09-08 本地故障修订（尚未部署）

**更新：2026-09-08 已完成端侧增量部署，实车复验等待现场准备。**
读取原生源码后修正了下文初步假设：Px4Backend.snapshot 的 pose_stamp 实际来自
`/ground_air/localization/pose`，原生 telemetry_timeout 为 0.5 秒；部署版已使用
该话题和阈值，而非 MAVROS 位姿。原生任一 transition 拒绝会进入 FAULT，
新遥测不会自动从 FAULT 回到 GROUND。用原生纯状态机隔离复现了
`local pose is stale` → FAULT → `ground navigation requires ground configuration`。
适配器现严格要求 mode=GROUND(1)，拒绝 UNKNOWN/FAULT 等状态，不自动复位故障。

部署批次 `agv_retry_20260908`：仅原子替换 CCS Ground-Air 包的 task_adapter.py
和对应测试，备份位于 `/home/bitcq/ccs_edge_ws/.deployment_backups/agv_retry_20260908`，
证据位于同工作空间 `artifacts/agv_retry_20260908`。端侧和本地 25 项增量测试通过，
单包 catkin_make 构建成功，原生控制包 Python 文件 SHA-256 校验未变化。
端侧开机时为 1970 年；启动平台现有 NTP 组件后恢复同步，再启动用户服务。
启动后控制器 GROUND、未定位、STABILIZED；尚未下发实车执行指令。
下文保留离线阶段诊断过程，实际部署以本段更新为准。

操作者报告首次执行完成，后续执行分别被原生 prepare_ground 拒绝：
`ground navigation requires ground configuration` 和 `local pose is stale`。
这些响应说明拒绝发生在原生准备服务，不是 UDP 任务传输。端侧离线且本地
缺少原生控制层源码，机械构型变化原因及位姿断流/时间戳异常原因尚未确认。

本地适配器新增 MAVROS `/mavros/local_position/pose` 到达时间和 ROS 源时间戳
双重检查（默认 2 秒），并检查车辆状态到达新鲜度；准备和启动执行时均检查。
可通过适配器 `local_pose_topic` 和 `pose_timeout_seconds` 覆盖默认值。
原生拒绝保留原文，地面构型失败映射为 `GROUND_CONFIGURATION_REQUIRED`，
本地位姿过期映射为 `LOCALIZATION_UNAVAILABLE`，不自动改变机械构型或飞控模式。
完成、失败、停止和新调度均复位航点计时，防止上一轮超时计时污染下一轮等待。
Ground-Air 包 24 项隔离增量测试通过；尚未进行端侧构建、部署或连续实车复验。

端侧上线后需只读核对 prepare_ground 的原生构型判据和位姿输入、VehicleStatus
字段定义、首次完成到第二次准备期间的构型反馈、MAVROS 位姿源时间戳及频率。
确认实际话题与阈值后部署 CCS 适配器，再由操作者完成连续两轮低速任务验收。

<a id="s9-部署结果"></a>

#### 部署结果

- 设备：`AGV_001`，端侧 CCS 根目录 `/home/bitcq/ccs_edge_ws`。
- 部署标识：`agv_task_20260907_083858`。
- 上传包 SHA-256：`5ea964687f288c5eb5ce6055c365d81199f21f15ae172e2410432240577c3ee8`。
- 备份：`/home/bitcq/ccs_edge_ws/.deployment_backups/agv_task_20260907_083858`。
- 证据：`/home/bitcq/ccs_edge_ws/artifacts/agv_task_20260907_083858`。
- 端侧用户服务部署前为 `disabled/inactive`；静态验收结束时为 `disabled/active`，未启用开机自启动。

部署只替换以下 CCS 工作空间内容：

- `src/EPGeneral_task_control`
- `src/EPGeneral_ground_air_control`
- `config/ground_air_agv/task_control.yaml`
- `start_ccs_edge_dev.sh`

部署前后的 `/home/bitcq/catkin_ws/src` 共 563 个文件 SHA-256 清单一致。未修改车辆原生工作空间、用户 unit 或 `/etc`。

<a id="s9-构建与增量测试"></a>

#### 构建与增量测试

- 端侧隔离测试：任务协议包 30 项通过；Ground-Air 包 21 项通过。任务包中依赖平台源码的 `test_ground_contract` 只在本地运行，未向端侧复制平台包。
- 端侧增量构建：`catkin_make --pkg epgeneral_task_control epgeneral_ground_air_control -DPYTHON_EXECUTABLE=/usr/bin/python3` 通过。首次构建因设备时间仍为 1970 年产生 clock-skew 警告；NTP 同步后再次构建通过且无该警告。
- 部署后端侧测试：任务协议包 30 项、Ground-Air 包 21 项全部通过。
- 本地增量回归：任务协议包 31 项、Ground-Air 包 21 项、平台任务/AGV profile/设备地址/发行文档与版本 47 项，共 99 项通过。
- `roslaunch --nodes epgeneral_ground_air_control ground_air_task_control.launch` 只解析出 `/epgeneral_task_control` 与 `/epgeneral_ground_air_task_adapter`。

<a id="s9-静态验收"></a>

#### 静态验收

指控平台已启动现有 NTP 和任务服务，UDP 123、14564 均监听。端侧随后报告 `NTPSynchronized=yes`，时间源为 `192.168.50.101`，满足任务协议 2 秒 UTC 容差。

一键脚本启动成功，常驻节点包含任务协调器、Ground-Air 任务适配器和地面控制层。无任务时不存在 `move_base` 或 `ground_air_mission` 节点，`/cmd_vel` 无发布者。车辆验收快照为未定位、`ALTCTL`；脚本没有自动切换模式、解锁或产生运动指令。

无运动 UDP 验收结果：

1. `negotiate_task` 被端侧接收并返回 `accepted=True`。
2. 两点、0.05 m/s 的地面任务通过分片、CRC 和 commit 校验，原子生成 568 字节 `trajectory.xml`，commit 返回 `accepted=True`。
3. 因 `/ground_air/localized=false`，任务状态进入 `failed`，没有启动导航或任务执行器；协调器按配置继续准备重试。
4. `delete_task` 返回 `accepted=True`，轨迹 XML 被删除，状态恢复 `no_task`；急停锁文件仍不存在。

静态验收期间设备发生一次整机重启。由于用户服务保持禁用，重启后没有自动恢复任务或运动；人工重新启动一键流程后，NTP、任务服务和上述静态验收再次正常。

最终交接核验发现此前验收 shell 仍持有一套交互式启动进程，而用户服务显示 inactive。已停止该交互栈并通过现有用户服务重新启动；随后用正常 `delete_task` 协议清理 `test_AG_` 验收任务。最终状态为服务 `disabled/active`、任务 `no_task`，仅任务协调器和 Ground-Air 适配器常驻，没有 `move_base`、任务执行器或 `/navigation/cmd_vel` 发布者，急停锁文件不存在。

真实急停没有在无人值守静态验收中触发，以免给已解锁车辆写入安全闭锁。已确认 `/ground_air/emergency_stop` 类型为 `ground_air_msgs/SetEmergencyStop`，无任务急停、机器人确认失败和重启闭锁由隔离测试覆盖。

<a id="s9-回滚"></a>

#### 回滚

停止本次手动启动的用户服务后，将备份目录中的两个包、AGV 任务配置和一键脚本按原路径恢复，再仅增量构建两个包。恢复后比较 `deployed-files.sha256`、重新执行包内增量测试，并保持 `ccs-edge-dev.service` 为 `disabled`。原生 `/home/bitcq/catkin_ws` 不参与回滚。

<a id="s9-待完成验收"></a>

#### 待完成验收

实车验收待完成。现场需先确认时间同步和重定位，由操作者人工解锁并切入 OFFBOARD，再用不超过 0.1 m/s 的短路线验证航点顺序、2 秒停留、进度反馈、普通停止和运动中急停。急停后的 POSCTL 切换和闭锁解除仍由操作者完成。

## 后续记录填写格式

追加日期/范围、源码版本与差异、文件清单及前后哈希、备份/证据路径、命令与实测、告警/未测项、启停/回滚。回滚不覆盖更新的急停状态，文档整理不表示重新部署。
