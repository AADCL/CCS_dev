# Go2 EDU 端侧监控套件部署指南

本 profile 面向 Ubuntu 20.04、ROS Noetic、Go2 EDU 和 Unitree SDK2。必装包为 `epgeneral_device_config`、`epqrd_go2_bridge`、`epgeneral_mqtav`、`epgeneral_udp_telemetry`；任务状态接入时再安装 `epgeneral_task_control` 和设备运动适配器。遥控建图另需安装 `epgeneral_map_stream` v0.2.0 及目标设备的 SLAM start/stop 适配器。

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

## 2. 配置设备和网络

修改本目录 `config/` 下的 `device.yaml`、`go2.yaml`、`epgeneral_mqtav.yaml` 和 `udp_telemetry.yaml`。`device.id` 与话题中的 `QRD_001` 必须保持一致；如修改 ID，应同步修改所有 prefixed 话题。将 `network_interface` 设为连接 Go2 内网的真实接口，例如 `eth0`。

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
roslaunch "$(rospack find epqrd_go2_bridge)/../deploy/go2_edu/launch/go2_edu_bringup.launch"
```

任务控制默认关闭。确认设备运动适配器已完成安全验证后，增加 `enable_task_control:=true`。

## 4. 验证

```bash
rostopic echo -n 1 /qrd/QRD_001/link/sdk
rostopic hz /qrd/QRD_001/imu
rostopic hz /qrd/QRD_001/odometry
rostopic echo -n 1 /qrd/QRD_001/battery
rostopic echo -n 1 /qrd/QRD_001/diagnostics
rostopic echo -n 1 /qrd/QRD_001/link/udp_tx
sudo tcpdump -ni any udp port 14560
```

`udp_tx=true` 只表示本机 `sendto` 成功；地面站是否在线仍由地面站收到 UDP heartbeat 判定。SportModeState 位置是局部里程计，不等同于地图全局定位。

## 6. 启用 v2 遥控建图

默认 `go2_edu_bringup.launch` 和 `start_ccs_edge_dev.sh` 不启动建图节点，避免在未配置 SLAM 适配器时误报可用。先编辑
`EPGeneral_map_stream/config/mapping.yaml` 的点云/位姿话题、frame、外参、工作目录和
`commands.start`/`commands.stop`，将默认 `/usr/local/bin/ccs-mapping-*` 占位符替换为设备实际命令，确认命令会在会话目录生成 `map.pcd`、`map.pgm` 和 `map.yaml`，再执行：

```bash
roslaunch epgeneral_map_stream epgeneral_map_stream.launch
```

端侧监听 UDP 14561、向地面站 UDP 14562 回传，地面站按 `device.ip` 访问端侧 TCP 14600 下载带短期令牌的成果 ZIP。部署前使用 `rostopic type/hz` 验证 PointCloud2 和位姿话题，并确保端侧与地面站 UTC 已同步。

## 5. 自启动与故障处理

生产部署使用 systemd 启动 `roscore` 和 bringup launch，服务应设置 `Restart=on-failure`、明确 `ROS_MASTER_URI`、`ROS_IP`、`CMAKE_PREFIX_PATH` 并依赖 `network-online.target`。不要以 root 运行 ROS 节点。

- 没有 ROS 话题：检查 `ldd devel/lib/epqrd_go2_bridge/epqrd_go2_bridge_node` 和 SDK2 安装路径。
- `/link/sdk=false`：检查 DDS 网卡、`rt/lowstate`、`rt/sportmodestate` 和状态年龄 diagnostics。
- MQTT 在线但无健康数据：检查 `epgeneral_mqtav` profile 中的 prefixed ROS 话题。
- UDP 只有 heartbeat：检查 descriptor 话题和地面站 descriptor hash 是否一致。
