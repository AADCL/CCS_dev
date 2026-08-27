# Scout Mini 部署日志

## 2026-08-27 v0.4.3 航点可达性修复部署

- 执行 `4111b255...` 从 08:22:16 接收命令至 08:22:46 发送首个 goal，30 秒延迟来自平台旧统一启动提前量；平台 v0.22.2 已改为 3 秒。
- 前两个航点成功，第三个 waypoint 2 `(-0.5, 0.5)` 被 `move_base` 状态 4 中止，原始文本为 `Failed to find a valid plan. Even after executing recovery behaviors.`。该点 PGM 值为 205，属于未知区，而 GlobalPlanner 配置 `allow_unknown=false`。
- 端侧 v0.4.3 在 ready 前按 map YAML/PGM 拒绝地图外、未知和占用目标，返回 `WAYPOINT_NOT_TRAVERSABLE`；运行期 action 失败保留状态文本并返回 `NAVIGATION_PLAN_FAILED` 等明确错误码。
- 部署前备份位于 `~/.deployment_backups/20260827T083703Z_v043_waypoint_validation`。本地 30 项端侧测试及 10 项平台回归通过；Scout 29 项纯端侧测试、真实地图校验、版本检查和 Release catkin 构建通过。
- 一键栈 PID `19617` 于 `2026-08-27T08:42:30Z` 启动成功，任务节点、适配器及 UDP 14563 在线。重启后重定位按安全规则进入 standby，未恢复导航或运动。
- 本轮部署验收未发送航点或非零速度。重新执行前必须完成重定位，并将 waypoint 2 改选到 PGM 已知自由空间后重新保存下发。

## 2026-08-27 v0.4.2 TF listener 修复部署

- 根因：系统 `/fastlio_odom` 和实时 `map<-odom` 均正常，但 Scout 导航适配器只创建 `tf2_ros.Buffer`，没有创建并持有 `tf2_ros.TransformListener`；因此适配器未订阅 `/tf`、`/tf_static`，私有 TF buffer 永久为空。
- 修复后 `/scout_navigation_task_adapter` 明确订阅 `/tf` 和 `/tf_static`，能够读取实时 map 位姿。端侧包升级为 v0.4.2，平台继续为 v0.22.1，任务 UDP 协议和端口不变。
- 部署前备份位于 `~/.deployment_backups/20260827T074609Z_v042_tf_listener`。本地 28 项端侧测试及 10 项平台回归通过；Scout 上 28 项端侧测试、版本检查和 Release catkin 构建通过。依赖地面站 `ccs_monitor` 的契约测试不在 Scout 运行。
- 为保留 15:38 完成的定位栈，仅热重启导航适配器。修复后导航进程和地图服务常驻，任务 revision 8 于 15:54:00 自动由 `received` 进入 `ready`。
- 验收时没有 `mission/active_execution.json`，未发送 `/move_base/goal` 航点或非零速度，未执行真实车辆运动。

## 2026-08-27 v0.4.1 执行会话修复部署

- 根因确认：Scout 日志中不存在执行命令和 `mission/active_execution.json`；“设备已有执行会话”来自平台在端侧尚未 `ready` 时提前创建 `_device_execution` 内存锁。任务页执行按钮还会重复保存并增加 revision，使刚就绪的 revision 立即失效。
- 平台 v0.22.1 改为仅对已下发且端侧 `ready` 的当前 revision 创建执行会话；执行按钮不再隐式保存。Scout 任务控制 v0.4.1 将 TF callback 异常转换为结构化失败，并将未定位状态归类为 `LOCALIZATION_UNAVAILABLE`。
- 部署前备份位于 `~/.deployment_backups/20260827T072350Z_v041_execution_session_fix`。端侧 25 项初始增量测试及补丁后的 10 项 Scout 适配器测试通过，Python 编译、版本一致性检查和 Release catkin 增量构建通过。
- 一键栈 PID `34654` 于 `2026-08-27T07:31:48Z` 启动成功；`/epgeneral_task_control`、`/scout_navigation_task_adapter` 在线，UDP 14563 正常监听，安装版本为 v0.4.1。
- 历史任务恢复准备因当前未重新定位而返回 `LOCALIZATION_UNAVAILABLE`；最新任务控制和适配器日志无 `bad callback`、Traceback 或 TF2 未捕获异常。
- 验收时不存在活动执行文件、`move_base`/TEB 节点和 `/move_base/goal`。本轮未发送生产非零目标，未执行真实车辆运动。

## 2026-08-27 `epgeneral_map_stream` v0.11.0 增量部署

- map-stream 增加 `scout_pointcloud_mapper` 四阶段流程；FAST-LIO 使用 `rviz:=false`，mapper 使用会话开始时固化的 `map_name:=YYYYMMDD_HHMMSS`，pose adapter 继续使用真机现有 `pose_adapter.launch`。
- 部署前备份位于 `~/.deployment_backups/20260827T044122Z_map_stream_v011`；仅覆盖 `~/ccs_edge_ws/src/EPGeneral_map_stream` 和 `config/scout_mini/map_stream.yaml`，未修改 Scout 工具源码和一键脚本。
- 端侧 Bash 语法、v0.11.0 版本一致性和 52 项受影响模块测试通过；2 项依赖地面站仓库的测试在端侧跳过。Python 3/Release `catkin_make --force-cmake` 构建成功。
- 一键栈启动后 `/epgeneral_map_stream` 在线，UDP 14561/TCP 14600 正常监听；空闲状态无 FAST-LIO、mapper、TF 或 pose 重复节点。
- 真机验收 `map_name=20260827_124539`：supervisor 严格按 `/laserMapping`、`/scout_pointcloud_mapper`、TF manager、pose adapter 就绪；mapper 参数输出路径为 `~/livox_fastlio/maps/20260827_124539/filtered_camera_init.pcd`。预览点云约 10 Hz，`/fastlio_odom` 约 20 Hz。
- 正常停止耗时 4 秒，先退出 mapper 后 filtered PCD 从 924348 字节刷新为 1027596 字节；四阶段节点和 PID 文件全部清理，未调用 rosservice。
- finalize 使用同一个 `map_name` 执行 `finalize_map.py 20260827_124539 --replace-raw`。metadata 中名称一致，filtered/raw PCD 均为 1027596 字节且 SHA-256 同为 `6ce568e457ab12d944020bf69a3fc58726b4ed5310821f625643365a8a328416`；`public_map.pcd`、PGM/YAML 和 metadata 均生成成功。
- 验收结束后已停止一键烟测栈；epgeneral、Scout、D435i 节点、相关监听端口和运行态 PID 文件无残留。验收地图保留在 `~/livox_fastlio/maps/20260827_124539/`。

## 2026-08-27 v0.4.0 源码增量

- 仓库已完成任务 commit 后导航准备与常驻生命周期改造，平台版本 v0.22.0、任务控制包 v0.4.0。
- 已部署至 `nvidia@192.168.50.120`；旧源码、profile、任务数据和 devel 产物备份于 `~/.deployment_backups/20260827T033036Z_v040_nav_resident`。
- 上传 SHA-256 校验一致；端侧 24 项纯端侧测试、Python 编译、消息/launch 检查、隔离 catkin Release 构建及主工作区 Release 构建通过。依赖指控平台 `ccs_monitor` 的 `test_ground_contract` 不在端侧运行，已由地面站增量测试覆盖。
- 一键栈启动成功：任务协调节点、Scout 导航适配器、重定位、地图、底盘和传感器节点在线，UDP 14563 正常监听，安装版本检查为 v0.4.0。
- 历史任务恢复后因重定位为 `standby`、`/fastlio_odom` 无发布者且缺少 `map<-odom` TF，端侧按 5 秒周期安全重试准备并保持 failed；未启动 `navigation_teb`/`move_base`，`/move_base/goal` 不存在。
- 本轮未发送生产非零目标，未执行真实车辆运动；需由操作人员重新定位后，再通过正常任务下发验证导航常驻和执行闭环。

## v0.21.1 任务失败修复

- 根因：执行 `cd3533b6...` 时重定位状态文件仍为历史 `localized`，但 `/fastlio_odom` 无发布者且 `map` frame 不存在；`move_base` 未崩溃，只是在导航准备截止时被适配器停止。
- 修复：任务控制 v0.3.1 增加实时里程计/TF/地图文件预检并返回明确错误码；重定位 v0.2.2 在进程重启时清除历史 `localized`；平台 v0.21.1 保留端侧失败详情。
- 部署验收必须先重新完成 Scout 重定位；本轮不向生产 `/move_base/goal` 发送非零目标。
- 2026-08-26 18:20 UTC 已完成部署前备份：`~/.deployment_backups/20260826T102034Z_v0211_task_fix`。
- 端侧增量测试通过：任务控制 Scout 适配器 6 项、重定位核心 13 项；Release `catkin_make --force-cmake -DCMAKE_BUILD_TYPE=Release` 遍历 7 个包成功。
- 重启一键栈后确认任务控制/重定位/Scout 适配器在线、UDP 14563 监听、`/move_base/goal` 未创建；状态文件已自动降级为 `standby`。当前 `/fastlio_odom` 和 `map<-odom` 不存在，符合未重新定位的安全状态。

## v0.21.0 Scout task adapter deployment

- 目标设备：`UGV_001` / `192.168.50.120`；端侧任务包：`epgeneral_task_control v0.3.0`。
- 部署前备份已保存到 `~/.deployment_backups/20260826T080048Z_task_control_v030`；日志未记录 SSH 密码。
- 新任务包和 Scout profile 已上传到 `~/ccs_edge_ws`；任务包 18 项端侧增量测试、版本检查、Python 语法检查和 Release catkin 增量构建通过。
- Scout navigation 工作区的 Release 配置在 ROS2 `livox_ros_driver2` 处缺少 `ament_cmake_auto`；运行时不使用该工作区内未构建的同名底盘/雷达包。一键脚本改为先 source navigation、再 source `livox_fastlio`，由后者覆盖 `scout_base`、`livox_ros_driver2` 和运行时 `scout_navigation`，并增加两个节点的可执行文件预检。
- 增量验收：纯 Python 测试、版本检查、catkin 构建、launch 解析、`/fastlio_odom`/TF/`/cmd_vel`/`/move_base` 检查和隔离 action server 目标截获。
- 已确认重定位状态 schema 2、`localized`、活动地图 `d0ad7c7f-b391-48c5-8f24-c5059e9a1a01`；`scout_navigation/navigation_teb.launch` 在既有 devel 环境中可发现并解析，`/fastlio_odom` 类型为 `nav_msgs/Odometry`，`/cmd_vel` 类型为 `geometry_msgs/Twist`。
- 修复任务入口脚本手工前置源码目录导致 `epgeneral_task_control.msg` 被遮蔽的问题；catkin devel relay 现在可同时加载业务代码和生成消息。
- 2026-08-26 17:40 一键栈启动成功：底盘、Livox、D435i、七个 CCS/适配器节点在线，任务包版本检查通过，UDP 14563 正常监听。按需导航未启动，`/move_base/goal` 不存在。
- 启动修复备份位于 `~/.deployment_backups/20260826T093431Z_scout_startup_overlay_fix`。关键故障日志归档于 `~/.ros/ccs_edge_dev_scout_mini/log/archive/20260826_startup_repair/`；清理 266 个历史 ROS 日志项后，`~/.ros/log` 从 64 MiB 降至 5.7 MiB，仅保留当前运行目录、`latest` 和当前 mqtav 日志目录。
- 本次未向生产 `/move_base/goal` 发送目标，未发送非零 `/cmd_vel`，未执行真实车辆运动。

## 2026-08-25 v0.19.1 重复重定位增量部署

- `epgeneral_relocalization 0.2.1` 的 12 项端侧测试、Python 3.6 语法检查和 catkin 增量构建通过；部署前备份位于 `~/.deployment_backups/20260825_214615_v0191_relocalization`。
- 一键栈已重启，`/epgeneral_relocalization`、`/epgeneral_udp_telemetry` 和 UDP 14565 在线；`/Odometry`、`/scout/odom`、`/livox/imu` 均可用。
- 活动地图 `d0ad7c7f-b391-48c5-8f24-c5059e9a1a01` 的 PCD、PGM、YAML 完整。使用现有单位绑定和当前原点 odom 执行真实重复重定位，依次收到 `map_ready`、`starting`、`awaiting_pose`、`relocalizing`、`succeeded`。
- 端侧 schema 2 状态和地面站 schema 6 绑定均已覆盖为单位 `map <- odom`，frame 为 `map/odom`，更新时间约为 `2026-08-25T13:54:07Z`；两端数值逐字段一致。
- 部署后日志中没有新增启动或重定位错误；检查到的 XmlRpc/Bad file descriptor 记录发生在本次部署前的 21:20 旧进程退出阶段。

## 2026-08-25 v0.19.0 遥测与活动地图增量部署

- `epgeneral_udp_telemetry 0.3.0`、`epgeneral_relocalization 0.2.0` 的 23 项端侧测试、Python 编译和 catkin 构建通过；备份位于 `~/.deployment_backups/20260825_194552_v019_telemetry`，脚本备份位于 `20260825_195419_v019_scripts`。
- 一键栈已重启，两个常驻节点和 UDP 14565 正常；`/scout/odom`、`/livox/imu` 可用。重启后尚未启动重定位 FAST_LIO 栈，因此 `/Odometry` 和 FAST_LIO2 状态当前不可用，符合状态定义。
- 地面站已写入 `data/battery_history/UGV_001.json`，现场分钟电压中位数为 `24.9 V`；曲线未标定，界面保持“待标定”。
- PGM 状态将在下一次地图协商写入活动 map ID 后验收；本次未执行完整放电或真实重复重定位，不宣称这两项已完成。

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
