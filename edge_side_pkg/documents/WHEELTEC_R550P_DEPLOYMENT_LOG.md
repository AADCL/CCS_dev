# WheelTech R550P 部署日志

## 2026-08-28 `epgeneral_map_stream` v0.12.0 联合建图部署验证

- `UGV_003` 作为 `UGV_001` 的联合建图从设备；旧 v0.11.0 包和 `config/wheeltec_r550p/map_stream.yaml` 已备份到 `~/.deployment_backups/20260828T033407Z_map_stream_v012`，未修改只读外部工作空间 `/home/nrc19/livox_fastlio`。
- v0.12.0 归档 SHA-256 为 `43c506d171f7264a63cc61f651657489444c0150e67d312556767cc0a9dd4c43`。部署时恢复 Linux LF 和脚本 0755 权限；版本检查、43 项端侧增量测试、catkin 增量构建、launch 解析和 Bash 语法检查通过，其中 1 项依赖地面站验证器的成果测试按设计跳过。
- 根管理脚本受控重启时清理了此前的重定位/导航子进程。重启后 `/epgeneral_map_stream` 使用 v0.12.0，UDP 14561 和 TCP 14600 正常监听；空闲及验证结束后无 FAST-LIO、mapper、TF manager 或 pose adapter 建图子节点残留。
- 与主设备 `UGV_001` 完成无运动静态联合建图，外参方向 `UGV_001 map <- UGV_003 map`，XYZ `(0,-1.2,0)`、RPY `(0,0,0)`；本设备收到 16 个有效预览分片后正常停止并生成 PCD/PGM/YAML 成果。
- 地面站下载、校验并联合融合 27,477 点，未剔除设备，原子提交到临时地图仓储且未设为 active map。验证全程监听 `/cmd_vel` 无输出，未发送导航目标或初始位姿。

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

## 建图坐标系错误修复（2026-08-27）

- 首次实机测试中，协商完成后开始建图即出现 `LOCAL FRAME_MISMATCH / PCD 分片源坐标系不匹配`。端侧日志持续记录 `source=odom target=odom`，确认点云已按 WheelTech profile 正确变换到 `odom`，并非 FAST-LIO frame 或外参错误。
- 根因是地面站 `config/map_building.json` 缺少 `UGV_003` 的设备级 frame 配置，因而回退到默认 `preview_source=lio_odom`。现已增加 `remote_mapping=odom`、`preview_source=odom`、`remote_artifact=map`。
- 修复前先对遗留会话发送 `abort_mapping`。已确认 `/wheeltec_pointcloud_mapper`、`/laserMapping`、`/wheeltec_tf_manager` 和 `/wheeltec_pose_adapter` 退出，仅常驻 `/epgeneral_map_stream`；未执行成果 finalize。
- 地面站协调器增加 `FRAME_MISMATCH` 自动中止保护：协商结果、分片 `frame_id` 或 `source_frame_id` 不一致时保留原始错误码和协议日志，进入现有可重试的 `abort_mapping` 状态机；abort ACK 后提示端侧已自动中止并清除活动会话。
- 地面站配置只在启动时读取。已确认原进程命令为 `python.exe run.py`，并重启为新进程，使 `UGV_003` 配置生效；端侧代码、配置及线上协议均未修改。
- 新增配置及协调器测试，覆盖 `odom -> odom` 分片接受、协商 frame 错误自动 abort、分片 source frame 错误自动 abort、相同 request ID 重试，以及 ACK 后保留 `FRAME_MISMATCH`。地图协议、建图服务、Scout 和 Go2 回归共 33 项通过，`compileall` 与 `git diff --check` 通过。
- 新配置下“接收并显示至少 3 个 PCD 分片”的静态实机复验尚未完成。自动化环境无法可靠操作原生 Qt 窗口，因此未采用屏幕坐标点击，以避免误触 stop/finalize；复验时应使用“强制结束”，不得发送非零 `/cmd_vel`。

## 未验证项

- 实车前进、后退、转向和任何非零速度控制。
- 真实初始位姿下的 NDT 收敛和全局定位结果。
- move_base/TEB 导航目标执行。
- 摄像头采集及 SRT 视频流。
- 设备能够访问 GitHub 后的完整 `rosdep update`。
- 修复后的地面站累计接收至少 3 个 `odom -> odom` PCD 分片并显示点数增长。
