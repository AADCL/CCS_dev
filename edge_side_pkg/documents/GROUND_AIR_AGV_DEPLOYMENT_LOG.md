# 空地 AGV 部署日志

## 2026-09-05 手动启动与建图兼容修复

- 当前 `AGV_001` 已取消上电自启动，`ccs-edge-dev.service` 保持 `disabled`；已有运行栈不因禁用而停止，需要运行时手动启动。
- 当前 manager 和日志均由 `ccs_edge_ws` 管理，manager 发布 guard `2`。旧建图客户端只接受 guard `1`，导致准备阶段报缺少会话保护。
- `epgeneral_map_stream` v0.13.2 同步版本兼容、部署门禁和当前操作说明；逐文件修复未重启常驻服务，端侧聚焦测试最终 25/25，通过实际预检及一次无运动建图闭环。最终服务仍为 `disabled/inactive`，手动启动的基础栈继续运行，stage 已回到 BASE；完整备份、地图校验值和清理结果见 [建图部署日志](GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md)。
- 以下旧批次中的路径、启用状态和测试结果按历史保留，不作为当前上电启动或版本要求。


## 2026-08-31 A8 Mini SRT 视频部署

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

## 2026-08-31 通信服务纳入一键启动

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

## 2026-08-31 基础部署

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
