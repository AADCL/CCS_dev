# Scout Mini 部署日志

## 2026-08-24 计划与环境盘点

- 目标设备：`UGV_001` / `192.168.50.120`
- 端侧：Ubuntu 20.04.6、Jetson ARM64、ROS Noetic、Python 3.8.10
- 既有工作空间：`/home/nvidia/livox_fastlio`、`/home/nvidia/realsense_ws`
- Scout ROS commit：`01e07881cdc566c3a657e288c59a75577992d13e`
- FAST-LIO commit：`7cc4175de6f8ba2edf34bab02a42195b141027e9`
- D435i 序列号：`112322070160`，SRT `srtsink` 已存在
- `can0` 已处于 UP；地面站 ping 可达
- 发现缺项：`python3-paho-mqtt`、`python3-msgpack`
- 发现配置问题：地面站 descriptor 使用 `sensor_pose`，端侧和代码使用 `vision_pose`；本次 profile 统一采用 `vision_pose`

## 部署执行记录

- [x] 创建备份 `~/.deployment_backups/20260824_172540_scout_mini`。
- [x] 安装 `python3-paho-mqtt` 和 `python3-msgpack`。
- [x] 安装 NTP drop-in，`ServerName`/`ServerAddress` 均为 `192.168.50.101`。
- [x] 四个 ROS 包部署到 `~/ccs_edge_ws/src`，profile 部署到 `config/scout_mini`，根脚本权限为 0750。
- [x] 使用 Python 3.8/Release 完成 `catkin_make --force-cmake`。
- [x] 四个包均可由 `rospack find` 解析，统一 launch 列出三个 epgeneral 节点。
- [x] 包内测试：`epgeneral_mqtav` 23 项、`epgeneral_udp_telemetry` 15 项，全部通过。
- [x] 烟测启动 Scout 底盘、D435i 和三个 epgeneral 节点；`/scout_status` 约 50 Hz、`/scout/odom` 约 50 Hz、彩色图像约 30 Hz。
- [x] `/ugv/UGV_001/link/udp_tx=True`，SRT Listener 监听 `0.0.0.0:9000`。
- [x] Ctrl+C 后 ROS Master、受管节点和 PID 文件均已清理。
- [x] 烟测后将启动脚本加强为“关键话题必须收到实际消息”；当前 Mid-360 无数据时会在 30 秒后失败并自动清理，不再误报整机就绪。

## 待处理的设备问题

- Livox 驱动日志报告 `Init lds lidar failed`，`/livox/lidar` 与 `/livox/imu` 当前没有 publisher。已按需求移除 FAST-LIO 启动和 `/Odometry` 就绪检查；仍需检查 Mid-360 供电、网口/地址和 Livox 配置后复验。
- 根因定位：Scout 驱动 `/BMS_status` 原先发布 `ScoutBmsStatus` 默认值，因为 BMS 字段赋值引用了不存在的 `state.*` 且被注释。已改为读取 SDK `GetCommonSensorState().bms_basic_state`，并将 MQTT 映射到实际电压；协议不提供的字段保持为空。
- 本次修复后重新构建 `livox_fastlio` 的 `scout_base` 成功；短时启动 Livox-only launch 验证 `/scout_status` 约 50 Hz，`/BMS_status` 正常发布，实测 `battery_voltage=25.5 V`。该 Scout 协议未提供有效 SOC/电流/温度帧，端侧 MQTT 配置将这些字段保持 `null`，避免发送默认零值。
- `epgeneral_map_stream` 升级到 v0.10.0，新增 Scout `scout_finalize` backend。启动流程依次管理 `fastlio_mapping_scout.launch`、`tf_manager.launch` 和 `pose_adapter.launch`，不在命令包装器中 source 工作空间。
- Scout 停止流程不调用 rosservice，反序发送 SIGINT；验证本次 `scans.pcd` 后以开始时间生成 `map_name`，调用 `finalize_map.py` 并上传 `map` 坐标的 `public_map.pcd`、`map.pgm`、`map.yaml`。
- D435i 彩色图像可用，但驱动报告 `Motion Module failure` 和温度读取错误；需检查 USB 供电/线缆、固件和 IMU 模块。视频链不受影响，D435i IMU 尚未验收。
- 烟测时地面站 TCP 1883 未确认可用，MQTT 节点已发起连接；需在地面站 Broker 启动后验证 presence/heartbeat/status 和 `battery_voltage`。
- UDP 本机 `sendto` 成功不代表地面站已接收；地面站监听启动后仍需确认 descriptor hash 和遥测内容。

## 2026-08-24 `epgeneral_map_stream` 真机部署与验收

- [x] 部署前备份保存于 `~/.deployment_backups/20260824_212132_map_stream`。
- [x] `epgeneral_map_stream` v0.10.0 和 Scout profile 已部署到 `~/ccs_edge_ws`；`scout_livox_base.launch` 已移除常驻 TF manager，根目录一键脚本已增加常驻 map-stream。
- [x] 端侧包测试结果为 62 项通过、3 项因地面站包不在端侧而跳过；`catkin_make --force-cmake -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3` 构建成功。
- [x] 一键脚本 PID `693256` 启动成功；`/epgeneral_map_stream` 在线，UDP `14561` 和 TCP `14600` 正常监听。空闲状态无 `/laserMapping`、`/scout_tf_manager`、`/scout_geometry_tf_publisher` 或 `/scout_pose_adapter`。
- [x] 真机按 FAST-LIO、TF manager、pose adapter 的顺序通过 readiness gate；日志依次记录 `stage=fast_lio`、`stage=tf_manager`、`stage=pose_adapter`。`/cloud_registered_body` 约 10 Hz，`/fastlio_odom` 约 20 Hz。
- [x] 停止流程按反序发送 SIGINT，未调用 rosservice；受管建图节点和 PID 文件全部清理。FAST-LIO 正常退出后，本次 `scans.pcd` 更新为 370110975 字节。
- [x] 真机复验发现 Bash 后台任务会继承忽略 SIGINT 的状态；包装器已在启动监督器和每个 `roslaunch` 前恢复 INT/TERM 默认处理。修复后完整反序停止耗时 4 秒，未触发 30 秒 SIGTERM 阈值，复验 `scans.pcd` 更新为 75869309 字节。
- [x] 使用 `map_name=20260824_213316` 完成真实转换，成果目录为 `~/livox_fastlio/maps/20260824_213316/`。其中 `public_map.pcd` 185055554 字节、`map.pgm` 29499 字节，并包含有效 `map.yaml`、`raw_camera_init.pcd`、`map_raw.pgm`、`map_raw.yaml` 和 `map_metadata.yaml`。
- [x] 验收结束后已停止一键烟测栈；epgeneral、Scout、D435i 节点及 UDP 14561/TCP 14600/SRT 9000 监听均已退出。核对旧 PID 不存在后清理了遗留的运行态 `scout_system.pid`，设备保持停止、可随时手工一键启动。
- [ ] 尚未执行地面站协议级 ACK、实时预览、ZIP 下载和导入验收；需在指控终端相关服务运行后完成。端侧命令、节点、话题、转换和成果完整性已通过真机验收。

密码、私钥和任何认证凭据不写入本日志。
