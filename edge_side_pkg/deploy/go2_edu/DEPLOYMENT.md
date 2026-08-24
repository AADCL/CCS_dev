# Go2 EDU 端侧监控套件部署指南

本 profile 面向 Ubuntu 20.04、ROS Noetic、Go2 EDU 和 Unitree SDK2。遥控建图需安装 `epgeneral_map_stream` v0.9.1、FAST_LIO、map accumulator、`tf2_ros` 和 PGM 生成包。建图启动链会先拉起 accumulator，停止建图再保存和验证 PCD；指控平台配套 v0.18.3。

v0.8.0 已于 2026-08-24 部署到 `192.168.50.100`：按 ROS、Go2 MID360、edge workspace 顺序重建成功，53 项端侧测试通过（2 项地面站契约环境跳过）。短时启动 MID360、FAST_LIO 和四个坐标转换节点后，已按 `/lio/cloud_registered` 实际时间戳验证 `odom <- lio_odom` 可查询；abort 后未生成成果，PID、Livox 和建图节点均清理。

v0.8.1 已于同日增量部署：取消 start 阶段的零时间戳 TF 查询，避免转换树尚未连通时拒绝启动；端侧重建、53 项测试和版本一致性检查通过。v0.8.0 完整备份位于 `~/.deployment_backups/20260824_map_stream_081_deferred_tf/EPGeneral_map_stream_v080`。

## 1. 安装 SDK 与 ROS 依赖

```bash
sudo apt update
sudo apt install -y ros-noetic-ros-base ros-noetic-diagnostic-msgs \
  ros-noetic-nav-msgs ros-noetic-sensor-msgs python3-yaml python3-msgpack \
  python3-paho-mqtt cmake g++ build-essential libyaml-cpp-dev libeigen3-dev \
  libboost-all-dev libfmt-dev
git clone https://github.com/unitreerobotics/unitree_sdk2.git
git -C unitree_sdk2 checkout ce14ddccbc29fe6b54ad736c89f01849f0093834
cd unitree_sdk2 && mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=/opt/unitree_robotics
make -j2 && sudo make install
```

本 profile 已按 SDK2 commit `ce14ddccbc29fe6b54ad736c89f01849f0093834` 核对接口。SDK2 使用 BSD-3-Clause 许可证；升级前应在 Go2 EDU 上重新完成构建和实机验证。本仓库不复制 SDK2 源码。

`epqrd_go2_bridge` v0.2.0 完整发布 LowState 和 SportModeState。完整 ROS 话题清单、消息字段和订阅示例见 `EPQRD_go2_bridge/docs/ROS_TOPIC_INTERFACES.md`；profile 默认分别以 100 Hz 和 50 Hz 发布两类完整状态。

## 2. 配置设备和网络

修改本目录 `config/` 下的 `device.yaml`、`go2.yaml`、`epgeneral_mqtav.yaml` 和 `udp_telemetry.yaml`。`device.id` 与话题中的 `QRD_001` 必须保持一致；如修改 ID，应同步修改所有 prefixed 话题。当前 `QRD_001` 的 Go2 内网为 `eth1`（`192.168.123.199/24`）；换机部署时必须用 `ip -br address` 核对 `network_interface`。官方 bringup 和启动脚本会直接传入本目录配置，不再读取各功能包的通用默认配置。

```bash
ip -br address
ping <Go2 控制器地址>
sudo ufw allow 1883/tcp
sudo ufw allow 14560/udp
sudo ufw allow 14561/udp
sudo ufw allow 14600/tcp
```

DDS 依赖局域网组播。若 SDK 链路持续 offline，检查网卡、路由、防火墙和是否存在多个同网段接口。

## 3. 编译与启动

```bash
export CMAKE_PREFIX_PATH=/opt/unitree_robotics:${CMAKE_PREFIX_PATH}
cd ~/catkin_ws
catkin_make --force-cmake -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch "$(rospack find epqrd_go2_bridge)/../deploy/go2_edu/launch/go2_edu_bringup.launch" \
  ground_station_ip:=192.168.50.101
```

使用一键脚本时可通过环境变量覆盖地面站地址和 profile 目录：

```bash
CCS_GROUND_STATION_IP=192.168.50.101 \
CCS_EDGE_PROFILE_CONFIG_DIR=/path/to/go2_edu/config \
  ./start_ccs_edge_dev.sh
```

若将脚本单独安装到工作空间根目录，须同步安装 profile 配置；脚本默认从
`${CCS_EDGE_WORKSPACE}/config/go2_edu` 读取：

```bash
install -d -m 0750 ~/ccs_edge_ws/config/go2_edu
install -m 0640 edge_side_pkg/deploy/go2_edu/config/*.yaml \
  ~/ccs_edge_ws/config/go2_edu/
install -m 0750 edge_side_pkg/deploy/go2_edu/start_ccs_edge_dev.sh \
  ~/ccs_edge_ws/start_ccs_edge_dev.sh
```

任务控制默认关闭。确认设备运动适配器已完成安全验证后，增加 `enable_task_control:=true`。

## 4. 验证

```bash
rostopic echo -n 1 /qrd/QRD_001/link/sdk
rostopic hz /qrd/QRD_001/imu
rostopic hz /qrd/QRD_001/odometry
rostopic echo -n 1 /qrd/QRD_001/battery
rostopic echo -n 1 /qrd/QRD_001/diagnostics
rostopic hz /qrd/QRD_001/low_state/motors
rostopic hz /qrd/QRD_001/sport_mode/kinematics
rostopic echo -n 1 /qrd/QRD_001/sport_mode/status
rostopic echo -n 1 /qrd/QRD_001/link/udp_tx
sudo tcpdump -ni any udp port 14560
```

`udp_tx=true` 只表示本机 `sendto` 成功；地面站是否在线仍由地面站收到 UDP heartbeat 判定。SportModeState 位置是局部里程计，不等同于地图全局定位。

## 6. 启用 v2 遥控建图

默认 `go2_edu_bringup.launch` 和 `start_ccs_edge_dev.sh` 不启动建图节点，避免在未配置 SLAM 适配器时误报可用。先编辑
`EPGeneral_map_stream/config/mapping.yaml` 的 Livox 输入、FAST_LIO 输出、frame、外参、工作目录和
`integrations.fast_lio`/`integrations.map_accumulator`/`integrations.pgm`。Go2 profile 的 FAST_LIO setup 使用
`~/ccs_edge_ws/devel/setup.bash`，该 overlay 必须包含导航工作区；包内
`fast_lio_mapping.launch`。确认 `artifacts.accumulator_pcd_path` 指向
`/home/nvidia/go2_mid360_nav/maps/current/public_map.pcd`，`artifacts.source_pcd_path` 指向 PGM 工具读取的
公开 PCD，并确认 PGM launch 成功退出时生成 `map.pgm` 和 `map.yaml`，再执行：

```bash
roslaunch epgeneral_map_stream epgeneral_map_stream.launch
```

端侧监听 UDP 14561、向地面站 UDP 14562 回传，地面站按 `device.ip` 访问端侧 TCP 14600 下载实时 PCD 和带短期令牌的成果 ZIP。部署前使用 `rostopic type/hz` 验证 `/livox/lidar`、`/livox/imu`、`/lio/cloud_registered_body` 和 `/lio/odometry`，执行 `bash -n` 检查五个包装器，并确保端侧与地面站 UTC 已同步。停止时应确认日志顺序为 save、freshness、stop、PGM，且 manifest 的 `session_id` 等于当前会话。

## 5. 自启动与故障处理

生产部署使用 systemd 启动 `roscore` 和 bringup launch，服务应设置 `Restart=on-failure`、明确 `ROS_MASTER_URI`、`ROS_IP`、`CMAKE_PREFIX_PATH` 并依赖 `network-online.target`。不要以 root 运行 ROS 节点。

- 没有 ROS 话题：检查 `ldd devel/lib/epqrd_go2_bridge/epqrd_go2_bridge_node` 和 SDK2 安装路径。
- `/link/sdk=false`：检查 DDS 网卡、`rt/lowstate`、`rt/sportmodestate` 和状态年龄 diagnostics。
- MQTT 在线但无健康数据：检查 `epgeneral_mqtav` profile 中的 prefixed ROS 话题。
- UDP 只有 heartbeat：检查 descriptor 话题和地面站 descriptor hash 是否一致。
