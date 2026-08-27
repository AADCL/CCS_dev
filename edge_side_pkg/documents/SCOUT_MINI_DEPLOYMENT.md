# 松灵 Scout Mini 端侧部署说明

## 设备与目录

- 设备：`UGV_001`，端侧 IP `192.168.50.120`，地面站 `192.168.50.101`
- 系统：Jetson ARM64、Ubuntu 20.04、ROS Noetic、Python 3.8
- 既有依赖工作空间：`/home/nvidia/livox_fastlio`、`/home/nvidia/realsense_ws`
- CCS 工作空间：`/home/nvidia/ccs_edge_ws`
- profile：`/home/nvidia/ccs_edge_ws/config/scout_mini`

## 安装与构建

```bash
sudo apt update
sudo apt install -y python3-paho-mqtt python3-msgpack
sudo install -d -m 0755 /etc/systemd/timesyncd.conf.d
sudo install -m 0644 config/scout_mini/timesyncd-ccs.conf /etc/systemd/timesyncd.conf.d/ccs.conf
sudo systemctl restart systemd-timesyncd

cd ~/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source ~/realsense_ws/devel/setup.bash
source ~/github_upload/AADCL_UAV_UGV/Scout_mini/devel/setup.bash --extend
source ~/livox_fastlio/devel/setup.bash --extend
catkin_make --force-cmake -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash --extend
```

部署前备份 `~/ccs_edge_ws`、profile 配置及 `/etc/systemd/timesyncd.conf.d/ccs.conf`。不要在文档或日志中记录 SSH 密码。

Scout navigation 工作区包含与 `livox_fastlio` 同名的 `scout_base` 和 `livox_ros_driver2` 源码。环境必须先 source navigation、再 source `livox_fastlio`，确保底盘和 Livox 节点使用已经构建的 `livox_fastlio/devel` 产物；一键脚本会在启动硬件前检查两个节点是否可执行。

## 启动与停止

确认 `can0` 为 UP、D435i 已连接、地面站 MQTT/UDP/NTP 服务可达后运行：

```bash
cd ~/ccs_edge_ws
./start_ccs_edge_dev.sh
```

脚本依次启动 Scout 底盘、Mid-360 Livox 驱动、D435i、MQTT、UDP 遥测、SRT、常驻 `epgeneral_map_stream`、`epgeneral_relocalization` v0.2.2 和 v0.4.3 `epgeneral_task_control`。任务文件 commit 后启动 `scout_navigation/navigation_teb.launch` 并保持到任务删除、急停或节点关闭；执行和常规停止不会重复启停导航。任务适配器通过常驻 TF listener 接收 TF，并在 ready 前校验全部目标点属于 PGM 已知自由空间。按 `Ctrl+C` 会停止脚本自身管理的 ROS launch、节点和 ROS Master；不会自动恢复或发送运动目标。

重定位地图保存到 `~/livox_fastlio/maps/ccs_download/<map_id>/`。确认防火墙允许端侧 UDP 14565、地面站 UDP 14566/TCP 14601；重定位日志位于 `~/.ros/ccs_edge_dev/log/relocalization.log`。真实发布前必须验证地图下载、六阶段启动、`/initialpose`、稳定 `map <- odom` TF、重复重定位和反序清理。

建图启动顺序固定为：

```bash
roslaunch scout_system_bringup fastlio_mapping_scout.launch rviz:=false
roslaunch scout_pointcloud_mapper pointcloud_mapper.launch map_name:="$map_name"
roslaunch scout_tf_manager tf_manager.launch
roslaunch scout_pose_adapter pose_adapter.launch
```

四个命令继承一键脚本环境，不额外 source 工作空间。`map_name` 仅在收到开始建图指令时生成一次，并同时用于 mapper 输出目录、finalize 和 ZIP manifest。停止时先向 pointcloud mapper 发送 SIGINT，等待它刷新 `filtered_camera_init.pcd`，再停止 FAST-LIO、pose adapter 和 TF manager，不调用 rosservice。随后执行：

```bash
rosrun scout_map_tools finalize_map.py "$map_name" --replace-raw
```

`map_name` 为开始时间 `YYYYMMDD_HHMMSS`，停止和转换阶段不得重新计算，也不得使用平台 `map_id` 或 `session_id` 代替。成果保存在 `~/livox_fastlio/maps/$map_name/`，包括 `filtered_camera_init.pcd`、`raw_camera_init.pcd`、`public_map.pcd`、PGM/YAML 和元数据；指控终端通过 UDP 14561/14562 和 TCP 14600 保持既有预览、ACK 与成果下载流程，filtered PCD 不加入下载 ZIP。

如需只启动三个 CCS 功能包，可在 ROS Master 和传感器栈已运行时执行：

```bash
roslaunch /home/nvidia/ccs_edge_ws/launch/scout_mini_bringup.launch
```

日志位于 `~/.ros/ccs_edge_dev_scout_mini/log/`，启动状态为 `startup.log`。

## 验证

### v0.19.1 重复重定位验收

- 重复启动前端侧 schema 2 和地面站设备绑定同时清除旧 TF；失败后不得回退旧变换。
- Scout 新栈必须依次进入 starting、awaiting_pose、relocalizing、localized，新 TF 先写端侧状态文件再返回地面站。
- 2026-08-25 已用活动地图和当前原点位姿完成一次真实重复重定位，新 `map <- odom` 在端侧和地面站均为单位变换且更新时间一致。
- Go2 部署同一状态清理代码，但真实协商和启动请求均必须返回 `UNSUPPORTED_BACKEND`。

### v0.19.0 遥测修复验收

- 本地/Map 位姿源固定为 `/scout/odom`（`odom`），FAST_LIO2 状态单独监测 `/Odometry`（`camera_init`）；不要将两者互换。
- Scout 没有独立底盘 IMU ROS 话题，详情页当前明确使用 Livox `/livox/imu`。
- 活动地图写入 `~/.ros/ccs_edge_dev/state/relocalization.json`，PGM 状态只检查 `~/livox_fastlio/maps/ccs_download/<map_id>/map.pgm` 普通文件。
- 30 V 定义为满电；当前 BMS 仅可靠提供电压，地面站完整放电标定前显示“待标定”，并在 `data/battery_history/UGV_001.json` 留存分钟中位数。

### v0.19.0 现场记录（2026-08-25）

- 已在 `192.168.50.120` 完成 `epgeneral_relocalization` 部署、6 项端侧增量测试、Python 编译、catkin 增量构建和 launch 解析。
- 已验证常驻节点注册、UDP 14565 监听，以及地面站 `192.168.50.101:14566` 与端侧之间的真实协商往返；无本地地图时端侧正确返回 `map_required`。
- 本次未启动传感器和重定位进程组，因此真实地图 ZIP 下载、六阶段 ROS 启动、`/initialpose` 发布、稳定 `map <- odom` TF、地图显示和重复重定位仍为待验收项，不能据此宣称完整硬件验收通过。
- 现场测试结束后已停止临时 ROS 进程并确认 UDP 14565 无残留监听。原一键脚本和统一 bringup 的回滚副本保存在同目录的 `.pre-v019` 文件中。

```bash
rostopic type /scout_status
rostopic type /BMS_status
rostopic echo -n 1 /BMS_status
ss -lntup | grep -E '14561|14600'
rostopic type /scout/odom
rostopic type /Odometry
rostopic type /livox/imu
rostopic type /livox/lidar
rostopic type /camera/color/image_raw
rostopic echo -n 1 /ugv/UGV_001/link/udp_tx
rostopic echo -n 1 /ugv/UGV_001/diagnostics
ss -lunp | grep ':9000'
gst-inspect-1.0 srtsink
```

地面站应收到 `mqtav/UGV_001/{presence,heartbeat,status}`、UDP 14560 heartbeat/telemetry 和任务状态/心跳，并可使用 SRT Caller 连接端侧 `192.168.50.120:9000`。任务下发后需确认端侧依次报告 `received` 和 `ready`，且只有一个与任务地图匹配的导航进程持续运行。任务执行前仍需确认本进程的 localized 状态、实时 `/fastlio_odom`、`map<-odom` TF 和 `/cmd_vel` 订阅者；端侧重启后必须重新定位，且不会恢复历史运动目标。

## 故障排查

- 时间检查失败：确认地面站提供 NTP，检查 `timedatectl show-timesync` 的 `ServerName` 是否为 `192.168.50.101`。
- MQTT 节点失败：检查 `python3 -c 'import paho.mqtt.client'` 和地面站 TCP 1883。
- UDP 被拒收：检查 descriptor hash；Scout profile 必须使用 `vision_pose`，不能改为 `sensor_pose`。
- 视频无流：检查 `/camera/color/image_raw`、`gst-inspect-1.0 srtsink`、UDP 9000 和地面站 Caller。
- `/scout_status` 或 `/BMS_status` 无数据：检查 `can0`、`scout_livox_base.launch` 日志和 Scout 底盘电源。`/BMS_status` 由 Scout 驱动从 SDK 的 `GetCommonSensorState().bms_basic_state` 填充 SOC、SOH、电压、电流和温度；旧驱动曾把引用不存在的 `state.*` 代码注释掉，因此只发布默认值。
- 电量 MQTT 映射来自 `/BMS_status` 的 `battery_voltage`；Scout Mini 当前协议只可靠提供系统电压，SOC、电流、温度不可用时保持 `null`，不会把默认零值伪造成有效电量。
- 建图启动失败：检查 map stream session 下的 `scout_mapping.log`，确认节点严格按 `/laserMapping`、`/scout_pointcloud_mapper`、`/scout_tf_manager`、`/scout_geometry_tf_publisher`、`/scout_pose_adapter` 就绪，并确认 FAST-LIO 参数为 `rviz:=false`、mapper 参数为当前会话 `map_name:=...`。
- 成果生成失败：确认 `~/livox_fastlio/maps/$map_name/filtered_camera_init.pcd` 由本次 mapper 正常退出后更新，finalize 日志使用同一个 `map_name` 和 `--replace-raw`，且地图目录可写、剩余空间满足 profile 限制。
