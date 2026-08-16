# 多异构智能体指挥与控制系统

当前地面站版本：**v0.9.1**  
端侧包版本：mqtav **v0.3.0**、epgeneral_udp_telemetry **v0.2.1**、epgeneral_usb_cam_rtsp **v0.1.0**、epgeneral_map_stream **v0.1.0**、epgeneral_task_control **v0.1.0**

## 功能介绍

本仓库包含基于 PySide6 的多异构智能体指挥与控制地面站，以及部署到 ROS 端侧设备的配套功能包。系统面向可信局域网中的无人机、无人车、移动机器人和无人船。

- **系统总览**：统计在线/离线设备、地图和任务执行记录。
- **设备管理**：支持搜索、筛选、新建、批量删除、详情查看和配置持久化。
- **实时监测**：通过 MQTT 更新连接、电量、任务和健康状态，通过 UDP 14560 接收 20/5/1 Hz 分级位姿、IMU、点云及传感器状态。
- **视频监控**：按需拉取 `rtsp://<设备IP>:8554/usb_cam` H.264 视频流。
- **地图系统**：支持 PCD/PGM 创建、导入、编辑、下载、三维复原，以及 UDP 14561/14562 单设备实时建图和点云融合。
- **任务系统**：支持 PCD/PGM 选点、多设备子任务、XYZ 编辑、轨迹冲突检查、持久化、UDP 14563/14564 下发与同步执行。
- **指控大屏**：集中展示在线设备、三维地图、位置/姿态趋势和任务状态，支持全屏及面板折叠。
- **端侧配套**：`edge_side_pkg` 提供共享身份、MQTT 遥测、UDP 遥测、RTSP 推流、实时建图和任务协调包。

各通信通道互相独立。单个模块故障不会阻止其他页面或本地编辑功能运行。完整端侧协议见 [EDGE_DEVICE_INTERFACES.md](docs/EDGE_DEVICE_INTERFACES.md)。

## 目录结构

```text
CCS_dev/
├── ccs_monitor/                   # 地面站 PySide6 应用
├── config/                        # 设备、MQTT、遥测 UDP 与建图 UDP 配置
├── data/map_server/               # 地图元数据、PCD 与 .trash 回收目录
├── edge_side_pkg/
│   ├── epgeneral_device_config/        # 共享设备 ID/IP，v0.1.0
│   ├── mqtav/                     # ROS1 MQTT 遥测包，v0.3.0
│   ├── epgeneral_usb_cam_rtsp/              # USB 相机 RTSP 推流包，v0.1.0
│   ├── epgeneral_udp_telemetry/          # ROS/MAVROS UDP 遥测包，v0.2.1
│   ├── epgeneral_map_stream/             # ROS 实时建图上行包，v0.1.0
│   ├── epgeneral_task_control/            # ROS 任务接收与执行协调包，v0.1.0
│   ├── EPGeneral_mqtav.zip                  # 端侧部署归档
│   └── README.md
├── docs/DEVELOPMENT_NOTES.md
├── docs/EDGE_DEVICE_INTERFACES.md # 端侧交互协议总册
├── tests/
├── CHANGELOG.md
└── requirements.txt
```

## 环境要求

### 地面站

- Windows 10/11，或带桌面环境及 Qt 6 Multimedia 支持的 Linux。
- Python 3.10+、PySide6 6.6+。
- `requirements.txt` 中的 amqtt、paho-mqtt、PyYAML、MessagePack、NumPy、VisPy 和 pypcd4。
- 三维地图需要 OpenGL 2.1+ 兼容驱动；RTSP 播放依赖 Qt Multimedia 的 FFmpeg 后端。
- 最低窗口尺寸 800×600，建议 1440×900 或更高。

### 端侧

- Ubuntu 18.04、ROS Melodic、Python 3.6.9、MAVROS。
- GStreamer 1.0、GStreamer RTSP Server、usb_cam、cv_bridge、image_transport 及相关 ROS 消息包。
- USB 摄像头默认 `/dev/video0`；实时建图需要 PointCloud2 和同步位姿来源。

### 网络

| 端口 | 方向 | 用途 |
| --- | --- | --- |
| TCP 1883 | 端侧 → 地面站 | MQTT 状态与心跳 |
| TCP 8554 | 地面站 → 端侧 | RTSP H.264 视频 |
| UDP 14560 | 端侧 → 地面站 | 高频遥测与心跳 |
| UDP 14561 | 地面站 → 端侧 | 实时建图控制 |
| UDP 14562 | 端侧 → 地面站 | 建图点云数据 |
| UDP 14563 | 地面站 → 端侧 | 任务下发与控制 |
| UDP 14564 | 端侧 → 地面站 | 任务 ACK、状态和进度 |

系统面向可信局域网，不提供 MQTT、RTSP 或 UDP 加密认证。多设备同步任务要求地面站和端侧通过 NTP 对齐 UTC 时间。

## 部署方法

### 1. 获取并部署地面站

```powershell
git clone https://github.com/AADCL/CCS_dev.git
cd CCS_dev
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Linux：

```bash
git clone https://github.com/AADCL/CCS_dev.git
cd CCS_dev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

启动前核对：

- `config/devices.json`：设备 ID、名称、类型、IP 和状态卡片。
- `config/mqtt.json`：MQTT Broker 与心跳阈值。
- `config/udp_telemetry.json`：UDP 14560 描述项与分级频率。
- `config/map_building.json`：实时建图 14561/14562 参数。
- `config/task_system.json`：任务系统 14563/14564 参数。

放行地面站 TCP 1883、UDP 14560、14562、14564，然后启动：

```powershell
python run.py
```

首次启动会读取设备配置，并初始化地图和任务数据目录。

### 2. 从零部署端侧

在已安装 ROS Melodic 的 Ubuntu 18.04 上执行：

```bash
sudo apt update
sudo apt install python3-paho-mqtt python3-yaml python3-msgpack python3-numpy \
  python3-catkin-pkg python3-rospkg ros-melodic-mavros ros-melodic-mavros-extras \
  ros-melodic-usb-cam ros-melodic-cv-bridge ros-melodic-image-transport \
  ros-melodic-sensor-msgs ros-melodic-nav-msgs ros-melodic-geometry-msgs \
  libgstreamer1.0-dev libgstrtspserver-1.0-dev gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav

mkdir -p ~/catkin_ws/src
cp -r /path/to/CCS_dev/edge_side_pkg/* ~/catkin_ws/src/
cd ~/catkin_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

将 `epgeneral_device_config/config/device.yaml` 的 ID/IP 与地面站 `config/devices.json` 对齐，并检查：

- `mqtav/config/config.yaml` 的地面站 MQTT 地址。
- `epgeneral_udp_telemetry/config/telemetry.yaml` 的 ROS 话题和 descriptor。
- `epgeneral_usb_cam_rtsp/config/video.yaml` 的摄像头、分辨率、帧率和码率。
- `epgeneral_map_stream/config/mapping.yaml` 的点云、位姿、外参和网络参数。
- `epgeneral_task_control/config/task_control.yaml` 的端口、XML 目录和 command/feedback 话题。

端侧放行 TCP 8554、UDP 14561、14563。每个新终端先执行：

```bash
source /opt/ros/melodic/setup.bash
source ~/catkin_ws/devel/setup.bash
```

按需启动：

```bash
roslaunch epgeneral_mqtav epgeneral_mqtav.launch
roslaunch epgeneral_usb_cam_rtsp epgeneral_usb_cam_rtsp.launch
roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch destination_host:=<地面站IP>
roslaunch epgeneral_map_stream epgeneral_map_stream.launch
roslaunch epgeneral_task_control epgeneral_task_control.launch
```

`epgeneral_task_control` 不直接操作 MAVROS。真实运动还需设备控制节点实现该包的 command/feedback 消息接口。

### 3. 部署后验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q ccs_monitor run.py tests
```

端侧包可在包目录执行：

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
```

使用 `rostopic hz` 检查 ROS 数据源，使用 `ss -lntup` 检查网络端口，并在设备详情确认 MQTT 和 UDP 心跳持续刷新。

## 使用方法

### 首页

- 启动后默认进入首页，查看在线/离线设备、地图数、任务执行次数和最近任务。
- 在线状态来自 MQTT；地图和任务摘要来自本地持久化仓储。

### 设备页面

- 使用搜索与类型/状态条件筛选设备。
- 点击“新建”填写名称、类型、ID 和 IP，测试连接后保存；ID 会检查重复并统一为大写。
- 点击“编辑”批量选择并删除设备；双击设备卡进入详情。
- 详情页显示 MQTT、UDP、电量、任务、飞行模式、位姿、IMU、点云和设备状态卡。
- 日志支持 info/warning/error 筛选；视频开关按需连接 RTSP，离开页面自动释放播放器。

### 地图页面

- 点击“新建地图”，填写名称并选择建图设备。
- 编辑模式支持重命名、导入/替换 PCD 或 PGM、下载 ZIP 和删除。
- 双击卡片进入三维详情：左键旋转、滚轮缩放、中键平移。
- 点击“建图”选择一台登记设备实时接收并融合点云；结束后原子保存 PCD。
- 中断结果保存在 `.mapping`，再次进入时可保存或丢弃。

### 任务页面

- 新建任务时单选地图、多选设备。
- 为每台设备在点云或 free 栅格中选点，并在表格中修改 XYZ、增删或调整顺序。
- 设置默认高度、巡航速度、启动延迟和冲突阈值；每个有效子任务需要 2–500 个航点。
- 子任务可单独保存、下发和执行；共同执行会先下发最新修订，再按统一 UTC 时间启动。
- 冲突应通过修改轨迹或时间消解；强制执行必须填写原因并写入审计日志。

### 指控大屏

- 左侧显示 MQTT 在线设备，中间显示 PCD/PGM/叠加地图，右侧显示状态和位置/姿态趋势。
- 下方控制台选择地图与任务，可共同开始或终止任务。
- 设备栏和控制台可收起；进入全屏后按 Esc 恢复。

## QA

### PowerShell 不允许激活虚拟环境

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

也可直接使用 `.\.venv\Scripts\python.exe`，无需激活。

### Python 依赖安装失败

确认使用 64 位 Python 3.10+，再执行 `python -m pip install --upgrade pip setuptools wheel`。不要在 ROS Melodic 的 Python 3.6 环境安装地面站依赖。

### roslaunch 提示 `ModuleNotFoundError`

```bash
cd ~/catkin_ws
source /opt/ros/melodic/setup.bash
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

systemd、tmux 或启动脚本也必须在同一 shell 中 source `devel/setup.bash`。

### MQTT 或 UDP 模块故障

使用 Windows `Get-NetTCPConnection`/`Get-NetUDPEndpoint` 或 Linux `ss -lntup` 检查端口占用；确认防火墙和端侧目标 IP。单个端口绑定失败不会阻止其他模块运行。

### 设备在线但遥测不更新

MQTT 与 UDP 是独立链路。检查 `destination_host`、ROS 话题频率和 UDP 最后心跳；出现 descriptor 哈希不匹配时，同步地面站 JSON 与端侧 YAML 的名称、类型和等级。

### 新建设备后端侧无法连接

新建设备不会自动修改端侧配置。将 ID/IP 同步到 `epgeneral_device_config/config/device.yaml` 并重启端侧节点。

### RTSP 无画面

检查设备 IP、TCP 8554、`/dev/video0` 权限和 `rostopic hz /usb_cam/image_raw`。可用 GStreamer 验证：

```bash
gst-launch-1.0 rtspsrc location=rtsp://127.0.0.1:8554/usb_cam latency=100 \
  ! rtph264depay ! avdec_h264 ! autovideosink
```

缺少 `x264enc` 时安装 `gstreamer1.0-plugins-ugly`。

### 地图黑屏或 OpenGL 错误

更新显卡驱动并确认 OpenGL 2.1+。远程桌面、虚拟机和 offscreen 环境可能无法创建上下文；非三维页面仍可使用。

### PCD/PGM 导入失败

PCD 必须包含有限 XYZ；PGM 必须为 P2/P5，并由有效 ROS map_server YAML 指定 image、resolution、origin 和阈值。失败不会覆盖旧地图。

### 任务无法下发或同步执行

确认设备和 IP 有效、UDP 14563/14564 可达、epgeneral_task_control 已启动、子任务已保存且无未处理冲突。共同执行还需两端 NTP 同步；地图变化后必须重新复核航点。

### 误删地图或任务

删除内容会进入对应数据目录的 `.trash`。关闭程序后可人工移回原目录，但目录名不得冲突。

## 版本记录

完整新增、调整、修复和删除内容见 [CHANGELOG.md](CHANGELOG.md)。README 仅保留摘要。

**v0.9.1 · 2026-08-12**
<small>统一任务二级页面的深色主题与组件渲染。</small>

**v0.9.0 · 2026-08-12**
<small>新增地图任务规划、冲突检查、UDP 下发与多设备同步执行。</small>

**v0.8.0 · 2026-08-05**
<small>新增单设备 UDP 实时建图、点云融合和 PCD 原子提交。</small>

**v0.7.1 · 2026-08-04**
<small>优化大屏图表、面板折叠和三维视图中键平移。</small>

**v0.7.0 · 2026-08-04**
<small>新增科技化指控大屏、实时趋势和 PCD/PGM 图层。</small>

**v0.6.1 · 2026-08-03**
<small>修复新建地图对话框设备列表主题。</small>

**v0.6.0 · 2026-08-03**
<small>新增持久化地图管理、PCD 导入和三维复原。</small>

**v0.5.1 · 2026-08-02**
<small>修复 ROS Melodic 源码部署时 Python 包无法导入。</small>

**v0.5.0 · 2026-08-02**
<small>新增设备绑定的数据状态卡。</small>

**v0.4.0 · 2026-08-01**
<small>新增 UDP 高频遥测和 ROS 可配置采集包。</small>

**v0.3.0 · 2026-07-31**
<small>新增设备详情 RTSP 视频和端侧包统一目录。</small>

**v0.2.0 · 2026-07-31**
<small>新增 MQTT 智能体实时状态监测。</small>

**v0.1.0 · 2026-07-30**
<small>新增设备持久化管理、详情页和日志筛选。</small>

**v0.0.2 · 2026-07-30**
<small>升级为首页、设备、地图、任务和指控大屏多页面应用。</small>

**v0.0.1 · 2026-07-30**
<small>完成首版设备监控、搜索筛选和模拟数据源。</small>
