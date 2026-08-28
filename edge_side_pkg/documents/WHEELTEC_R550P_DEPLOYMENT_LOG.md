# WheelTech R550P 部署日志

## 约束与基线

- 部署目标：`UGV_003` / `192.168.50.122`，执行日期 2026-08-27（Asia/Shanghai）。
- 系统：Ubuntu 20.04.6、ROS Noetic、Jetson Xavier NX（aarch64）。
- 登录用户：`nrc19`；本文不记录登录密码。
- 不发送非零 `/cmd_vel`，不发送导航目标，不启动视频，不替换正式地图。
- `/home/nrc19/livox_fastlio` 作为只读外部设备栈；CCS Overlay 位于 `/home/nrc19/ccs_edge_ws`。

## 部署流程

| 时间 | 阶段 | 命令/操作 | 结果 |
|---|---|---|---|
| 19:00 前 | 设备盘点 | 检查系统、ROS、串口、网络、摄像头、工作空间和地图 | `/dev/wheeltec_controller -> ttyACM0`；无 `/dev/video*`；`factory_a` 和 `wheeltec_install_test` 保持不变 |
| 20:37 | 外部栈核验 | `rospack find`、检查 WheelTech launch 和节点名 | 7 个 WheelTech 依赖包及 3 个正式入口均存在；底盘节点为 `/wheeltec_robot` |
| 20:43 | Overlay 安装 | 创建 `ccs_edge_ws/src`，复制 7 个 CCS 包、profile 和根目录脚本 | 未部署 `EPQRD_go2_bridge`；原生 `bash -n` 通过 |
| 20:46 | 依赖安装 | `apt-get install python3-msgpack python3-paho-mqtt` | 两个缺失依赖安装成功 |
| 20:48 | rosdep | `rosdep install --from-paths src --ignore-src -r -y` | 设备未初始化 rosdep；`rosdep init` 因 `raw.githubusercontent.com` DNS 解析失败，未联网完成 |
| 20:50 | 构建 | `catkin_make -j1 --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3` | 成功遍历并构建 7 个包，视频 GStreamer C++ 节点编译成功 |
| 20:52 | NTP | 安装 `timesyncd-ccs.conf` 并重启 `systemd-timesyncd` | `NTPSynchronized=yes`，`ServerName/Address=192.168.50.101` |
| 20:53 | 静态解析 | `rospack find`、4 次 `roslaunch --files` | 7 个 CCS 包均可发现；建图、重定位、导航及统一 bringup 均可解析 |
| 20:54 | 一键启动 | `/home/nrc19/ccs_edge_ws/start_ccs_edge_dev.sh` | 底盘/Livox 和 6 个非视频 CCS 服务全部启动 |
| 20:59 | 建图链 | 地面站发送 `prepare_mapping`、`start_mapping`、`abort_mapping` | prepare/start/abort 均接受并到达 `mapping`；未执行 stop/finalize，未生成正式地图 |
| 21:02 | 重定位链 | `factory_a` negotiate + `start_stack` | 到达 `map_ready -> starting -> awaiting_pose`；未发送初始位姿 |
| 21:05 | 安全退出 | 向一键脚本发送 `SIGTERM` 并监听 `/cmd_vel` | 捕获全零 Twist；ROS Master 停止且 PID 目录为空 |

## 自动化测试

- 本地 WheelTech profile：4/4 通过；任务控制：30/30 通过；重定位：13/13 通过。
- Python `compileall`、YAML/XML 解析以及设备端两个 shell 脚本的 `bash -n` 通过。
- 本地全量 `unittest discover`：268 项中 265 项通过，3 项失败。失败均为本次改动前已存在的问题：默认 `UAV_001` 的端/地 IP 不一致、无 OpenGL 上下文导致取色失败、日间主题颜色表不完整。
- 设备端 `catkin_make run_tests`：134 项中 125 项通过、9 项导入/fixture 错误。原因是 CCS-only Overlay 按方案不包含地面站 `ccs_monitor`，也不安装 Scout profile；包运行构建本身成功。
- Scout 兼容命令已恢复原 8 个 launch 参数；WheelTech `managed_finalize` 单独追加 5 个节点名。Windows 运行 Scout 路径测试仍会因 `os.path.abspath` 将 Linux 路径加盘符而失败，Linux 设备不存在该差异。

## 实机静态验收

- `/livox/lidar`：`livox_ros_driver2/CustomMsg`，约 10.0 Hz。
- `/livox/imu`：`sensor_msgs/Imu`，约 200.0 Hz。
- `/odom`、`/imu`：分别为 `nav_msgs/Odometry`、`sensor_msgs/Imu`，均约 20.0 Hz。
- `/PowerVoltage`：`std_msgs/Float32`，采样值 22.562 V；未估算电量百分比。
- 基础栈未启动 FAST-LIO 时 `/Odometry` 不发布；重定位链启动后 `/fastlio_odom` 和 TF 可用。
- MQTT 日志持续发送 `mqtav/UGV_003/status` 和 `mqtav/UGV_003/heartbeat`。
- UDP 控制端口 14561/14563/14565 和地图 HTTP 端口 14600 正常监听。
- 建图链在收到 abort 后的关闭窗口报告一次点云超时，随后返回 abort accepted 并清理全部建图节点。临时目录 `20260827_205912` 未作为地图保留，已移动到日志状态目录的 `aborted_maps` 下。
- `factory_a` 的 `/map_2d` 成功加载：223 x 188，分辨率 0.05 m；`odom -> base_link` TF 连续可读。
- 重定位状态停留在 `awaiting_pose`，任务适配器据此返回 `Scout is not localized on a usable map`，符合“无活动地图拒绝任务”的保护要求。
- 设备无摄像头，因此视频包已编译但从未启动；全程未执行实车运动。

## 工作空间外变更

- 安装 apt 包：`python3-msgpack`、`python3-paho-mqtt`。
- 新增 `/etc/systemd/timesyncd.conf.d/ccs-edge.conf`，NTP 固定为 `192.168.50.101`。
- 在 `/home/nrc19/livox_fastlio/maps/ccs_download/factory_a` 放置 `factory_a` 的三个只读副本，用于重定位链静态验收。
- ROS 运行日志、PID、状态及中止建图样本位于 `/home/nrc19/.ros/ccs_edge_dev_wheeltec_r550p`，未写入工作空间。

## 未验证项

- 实车前进、后退、转向和任何非零速度控制。
- 真实初始位姿下的 NDT 收敛和全局定位结果。
- move_base/TEB 导航目标执行。
- 摄像头采集及 SRT 视频流。
- 设备能够访问 GitHub 后的完整 `rosdep update`。
