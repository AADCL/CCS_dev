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
source ~/livox_fastlio/devel/setup.bash --extend
catkin_make --force-cmake -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash --extend
```

部署前备份 `~/ccs_edge_ws`、profile 配置及 `/etc/systemd/timesyncd.conf.d/ccs.conf`。不要在文档或日志中记录 SSH 密码。

## 启动与停止

确认 `can0` 为 UP、D435i 已连接、地面站 MQTT/UDP/NTP 服务可达后运行：

```bash
cd ~/ccs_edge_ws
./start_ccs_edge_dev.sh
```

脚本依次启动 Scout 底盘、Mid-360 Livox 驱动、D435i、MQTT、UDP 遥测、SRT 和常驻 `epgeneral_map_stream`。常驻 profile 不启动 FAST-LIO、TF manager 或 pose adapter；它们只在收到建图指令后启动。驱动修复补丁保存在 `deploy/scout_mini/patches/scout_messenger_bms.patch`。按 `Ctrl+C` 会停止脚本自身管理的 ROS launch、节点和 ROS Master；不会发送运动指令。

建图启动顺序固定为：

```bash
roslaunch scout_system_bringup fastlio_mapping_scout.launch
roslaunch scout_tf_manager tf_manager.launch
roslaunch scout_pose_adapter pose_adapter.launch
```

三个命令继承一键脚本环境，不额外 source 工作空间。停止时按 pose adapter、TF manager、FAST-LIO 反序发送 SIGINT，不调用 rosservice。FAST-LIO 退出写出本次 `scans.pcd` 后执行：

```bash
rosrun scout_map_tools finalize_map.py "$map_name"
```

`map_name` 为开始时间 `YYYYMMDD_HHMMSS`，成果保存在 `~/livox_fastlio/maps/$map_name/`；指控终端通过 UDP 14561/14562 和 TCP 14600 保持既有预览、ACK 与成果下载流程。

如需只启动三个 CCS 功能包，可在 ROS Master 和传感器栈已运行时执行：

```bash
roslaunch /home/nvidia/ccs_edge_ws/launch/scout_mini_bringup.launch
```

日志位于 `~/.ros/ccs_edge_dev_scout_mini/log/`，启动状态为 `startup.log`。

## 验证

```bash
rostopic type /scout_status
rostopic type /BMS_status
rostopic echo -n 1 /BMS_status
ss -lntup | grep -E '14561|14600'
rostopic type /scout/odom
rostopic type /livox/imu
rostopic type /livox/lidar
rostopic type /camera/color/image_raw
rostopic echo -n 1 /ugv/UGV_001/link/udp_tx
rostopic echo -n 1 /ugv/UGV_001/diagnostics
ss -lunp | grep ':9000'
gst-inspect-1.0 srtsink
```

地面站应收到 `mqtav/UGV_001/{presence,heartbeat,status}`、UDP 14560 heartbeat/telemetry，并可使用 SRT Caller 连接端侧 `192.168.50.120:9000`。

## 故障排查

- 时间检查失败：确认地面站提供 NTP，检查 `timedatectl show-timesync` 的 `ServerName` 是否为 `192.168.50.101`。
- MQTT 节点失败：检查 `python3 -c 'import paho.mqtt.client'` 和地面站 TCP 1883。
- UDP 被拒收：检查 descriptor hash；Scout profile 必须使用 `vision_pose`，不能改为 `sensor_pose`。
- 视频无流：检查 `/camera/color/image_raw`、`gst-inspect-1.0 srtsink`、UDP 9000 和地面站 Caller。
- `/scout_status` 或 `/BMS_status` 无数据：检查 `can0`、`scout_livox_base.launch` 日志和 Scout 底盘电源。`/BMS_status` 由 Scout 驱动从 SDK 的 `GetCommonSensorState().bms_basic_state` 填充 SOC、SOH、电压、电流和温度；旧驱动曾把引用不存在的 `state.*` 代码注释掉，因此只发布默认值。
- 电量 MQTT 映射来自 `/BMS_status` 的 `battery_voltage`；Scout Mini 当前协议只可靠提供系统电压，SOC、电流、温度不可用时保持 `null`，不会把默认零值伪造成有效电量。
- 建图启动失败：检查 map stream session 下的 `scout_mapping.log`，确认节点严格按 `/laserMapping`、`/scout_tf_manager`、`/scout_geometry_tf_publisher`、`/scout_pose_adapter` 就绪。
- 成果生成失败：确认 `FAST_LIO/PCD/scans.pcd` 为本次退出后更新，且 `~/livox_fastlio/maps` 可写、剩余空间满足 profile 限制。
