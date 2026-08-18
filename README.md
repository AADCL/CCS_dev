# 多异构智能体指挥与控制系统

当前地面站版本：**v0.13.1**

端侧包版本：mqtav **v0.3.0**、epgeneral_udp_telemetry **v0.2.1**、epgeneral_usb_cam_rtsp **v0.1.0**、epgeneral_map_stream **v0.1.0**、epgeneral_multi_map **v0.1.0**、epgeneral_task_control **v0.1.0**

## 功能介绍

本仓库包含基于 PySide6 的多异构智能体指挥与控制地面站，以及部署到 ROS 端侧设备的配套功能包。系统面向可信局域网中的无人机、无人车、移动机器人和无人船。

- **系统总览**：统计在线/离线设备、地图和任务执行记录。
- **设备管理**：支持搜索、筛选、新建、批量删除、详情查看、设备类型模板和配置持久化。类型模板统一管理预览图标、地图形状与默认功能卡片。
- **实时监测**：通过 MQTT 更新连接、电量、任务和健康状态，通过 UDP 14560 接收 20/5/1 Hz 分级位姿、IMU、点云及传感器状态。
- **视频监控**：按需拉取 `rtsp://<设备IP>:8554/usb_cam` H.264 视频流。
- **地图系统**：支持 PCD/PGM 创建、导入、编辑、下载和三维复原，可从点云生成 ROS PGM，并提供离线点云融合、端侧 PGM 下载与二维栅格融合、Python 算法插件及 UDP 14561/14562 单机/多机实时建图。
- **任务系统**：支持 PCD/PGM 选点、多设备子任务、XYZ 编辑、轨迹冲突检查、持久化、UDP 14563/14564 下发与同步执行。
- **指控大屏**：集中展示在线设备、三维地图、位置/姿态趋势和任务状态，支持全屏及面板折叠。
- **端侧配套**：`edge_side_pkg` 提供共享身份、MQTT 遥测、UDP 遥测、RTSP 推流、实时建图和任务协调包。

各通信通道互相独立。单个模块故障不会阻止其他页面或本地编辑功能运行。完整端侧协议见 [EDGE_DEVICE_INTERFACES.md](docs/EDGE_DEVICE_INTERFACES.md)。

## 目录结构

```text
CCS_dev/
├── ccs_monitor/                   # 地面站 PySide6 应用
├── config/                        # 设备、设备类型、MQTT、遥测与控制配置
├── data/device_type_assets/       # 设备类型图标及 .trash 回收目录
├── data/map_fusion_algorithms/    # 用户导入的地图融合算法
├── data/map_server/               # 地图元数据、PCD 与 .trash 回收目录
├── examples/                      # 地图融合插件等扩展示例
├── edge_side_pkg/
│   ├── epgeneral_device_config/        # 共享设备 ID/IP，v0.1.0
│   ├── mqtav/                     # ROS1 MQTT 遥测包，v0.3.0
│   ├── epgeneral_usb_cam_rtsp/              # USB 相机 RTSP 推流包，v0.1.0
│   ├── epgeneral_udp_telemetry/          # ROS/MAVROS UDP 遥测包，v0.2.1
│   ├── epgeneral_map_stream/             # ROS 实时建图上行包，v0.1.0
│   ├── EPGeneral_multi_map/               # Noetic 端侧联合切片上传包，v0.1.0
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
- `requirements.txt` 中的 amqtt、paho-mqtt、PyYAML、MessagePack、NumPy、VisPy、pypcd4 和 Open3D。
- 三维地图需要 OpenGL 2.1+ 兼容驱动；RTSP 播放依赖 Qt Multimedia 的 FFmpeg 后端。
- 最低窗口尺寸 800×600，建议 1440×900 或更高。

### 端侧

- 主线：Ubuntu 20.04、ROS1 Noetic、Python 3.8+；旧包的 Ubuntu 18.04/ROS Melodic 部署仅作为维护基线。
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

- `config/devices.json`：设备 ID、名称、类型、IP 和设备级状态卡覆盖。
- `config/device_types.json`：类型显示名称、图标路径、地图形状和默认状态卡片。
- `config/mqtt.json`：MQTT Broker 与心跳阈值。
- `config/udp_telemetry.json`：UDP 14560 描述项与分级频率。
- `config/map_building.json`：实时建图 14561/14562 参数。
- `config/map_fusion_algorithms.json`：融合算法、默认参数、启用状态和脚本指纹。
- `config/task_system.json`：任务系统 14563/14564 参数。

放行地面站 TCP 1883、UDP 14560、14562、14564，然后启动：

```powershell
python run.py
```

首次启动会读取设备配置，并初始化地图和任务数据目录。

### 2. 从零部署端侧

在已安装 ROS1 Noetic 的 Ubuntu 20.04 上执行：

```bash
sudo apt update
sudo apt install python3-paho-mqtt python3-yaml python3-msgpack python3-numpy \
  python3-catkin-pkg python3-rospkg ros-noetic-mavros ros-noetic-mavros-extras \
  ros-noetic-usb-cam ros-noetic-cv-bridge ros-noetic-image-transport \
  ros-noetic-sensor-msgs ros-noetic-nav-msgs ros-noetic-geometry-msgs \
  libgstreamer1.0-dev libgstrtspserver-1.0-dev gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav

mkdir -p ~/catkin_ws/src
cp -r /path/to/CCS_dev/edge_side_pkg/* ~/catkin_ws/src/
cd ~/catkin_ws
source /opt/ros/noetic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

将 `epgeneral_device_config/config/device.yaml` 的 ID/IP 与地面站 `config/devices.json` 对齐，并检查：

- `mqtav/config/config.yaml` 的地面站 MQTT 地址。
- `epgeneral_udp_telemetry/config/telemetry.yaml` 的 ROS 话题和 descriptor。
- `epgeneral_usb_cam_rtsp/config/video.yaml` 的摄像头、分辨率、帧率和码率。
- `epgeneral_map_stream/config/mapping.yaml` 的点云、位姿、外参和网络参数。
- `EPGeneral_multi_map/config/multi_mapping.yaml` 的联合时间窗、点云/位姿话题、外参、超时和资源参数；它与 `epgeneral_map_stream` 互斥，不能同时占用 UDP 14561。
- `epgeneral_task_control/config/task_control.yaml` 的端口、XML 目录和 command/feedback 话题。

端侧放行 TCP 8554、UDP 14561、14563。每个新终端先执行：

```bash
source /opt/ros/noetic/setup.bash
source ~/catkin_ws/devel/setup.bash
```

按需启动：

```bash
roslaunch epgeneral_mqtav epgeneral_mqtav.launch
roslaunch epgeneral_usb_cam_rtsp epgeneral_usb_cam_rtsp.launch
roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch destination_host:=<地面站IP>
roslaunch epgeneral_map_stream epgeneral_map_stream.launch
# 联合切片端侧改用下一行，不能与上一行同时启动：
roslaunch epgeneral_multi_map epgeneral_multi_map.launch
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
- 点击“类型模板”新增或编辑设备类型，上传 PNG/JPEG/SVG 图标，选择箭头、立方体或球体地图形状，并绑定默认功能卡片。被设备引用的模板不能删除。
- 点击“新建”从类型模板中选择类型，填写名称、ID 和 IP，测试连接后保存；ID 会检查重复并统一为大写。
- 点击“编辑”批量选择并删除设备；双击设备卡进入详情。
- 详情页显示 MQTT、UDP、电量、任务、飞行模式、位姿、IMU、点云和设备状态卡。
- 详情页的状态卡可跟随类型模板动态更新，也可切换为设备自定义覆盖；空自定义表示明确不显示卡片。
- 日志支持 info/warning/error 筛选；视频开关按需连接 RTSP，离开页面自动释放播放器。

### 地图页面

- 点击“新建地图”后选择单机建图、多机建图或空地图。单机选择一台设备后立即协商；多机至少选择两台设备，并指定主设备及“主坐标系 <- 从坐标系”的 XYZ/RPY 外参。
- 点击“地图融合”选择至少两张有效 PCD 地图、主地图、各从地图外参和算法，成功后创建独立融合地图；默认只融合 PCD。
- “地图融合”弹窗可勾选“同步融合 PGM 图”。勾选后所有源地图都必须携带有效 PGM，系统使用同一 X/Y/Yaw 外参同步融合栅格，并将 PCD、PGM、YAML 一并绑定到新地图。
- 点击“PGM 融合”选择一张有效 PCD 目标地图，添加端侧来源并填写各自 `source_map_id` 和“目标 PCD frame <- 来源 PGM frame”的 X/Y/Yaw 外参。至少需要两个图层，可将目标地图已有 PGM 作为单位变换来源。
- PGM 下载与实时建图互斥。输出分辨率不得细于来源最细值；若来源超出目标 PCD XY 边界，确认后才会裁剪并原子替换目标 PGM。
- 点击“融合算法”可导入标准 `.py` 插件、设置默认算法和 JSON 参数。`examples/` 提供直接拼接、NumPy RANSAC 和 Open3D ICP 示例；导入代码属于受信任本地代码并在独立进程运行。
- 编辑模式支持重命名、导入/替换 PCD 或 PGM、从 PCD 生成 PGM、下载 ZIP 和删除。生成参数包含分辨率、高度范围、留白、点数阈值、障碍膨胀、空白区语义及 ROS 阈值。
- 双击卡片进入三维详情：左键旋转、滚轮缩放、右键拖动快速平移。任务页和指控大屏使用相同的三维操作速度。
- 点击“重新建图”可统一发起单机或多机任务。实时预览使用内置体素融合，结束时由所选算法生成正式 PCD。
- 单个从设备掉线时任务进入降级态，可剔除后继续或中止全部；主设备掉线必须中止。中断结果保存在 `.mapping`，离线融合临时结果保存在 `.fusion`。

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

### 类型图标无法上传或地图仍显示圆点

图标仅支持可正常解码的 PNG、JPEG、SVG，单文件不超过 5 MiB。成功上传后会复制到 `data/device_type_assets`；不要只保留外部源文件。三维环境不支持 OpenGL 或某个 mesh 创建失败时，地图会自动回退为圆点，不影响遥测与任务运行。

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

从 PCD 生成 PGM 时，确认高度范围内存在点，并避免使用过小分辨率生成超大栅格。未命中区域默认是 unknown，可在生成对话框改为 free；生成失败不会替换已有 PGM。

### PGM 下载提示端侧版本不支持

当前真实端侧 `epgeneral_map_stream` 尚未实现 PGM 文件服务。地面站请求 `request_pgm_artifact` 后若未收到 ACK，会显示版本不支持或超时。真实设备联调前需按 `docs/EDGE_DEVICE_INTERFACES.md` 实现 manifest、zlib 分片、补传和校验，并确保 UDP 14561/14562 可达。

### 融合算法无法导入或执行失败

插件必须定义 `PLUGIN_API_VERSION = 1`、唯一 `ALGORITHM_ID` 和 `fuse_maps()`，并生成有效 XYZ PCD。检查插件依赖是否安装在地面站 Python 环境。超时、崩溃和无效输出不会覆盖正式地图，临时任务会保留在 `data/map_server/.fusion` 或地图的 `.mapping` 目录。

Open3D ICP 示例需要 `open3d>=0.18`。RANSAC/ICP 均假设用户外参已提供合理的粗对齐；提示重叠率或 fitness 过低时，应先检查主从坐标方向与 XYZ/RPY 外参。

### 任务无法下发或同步执行

确认设备和 IP 有效、UDP 14563/14564 可达、epgeneral_task_control 已启动、子任务已保存且无未处理冲突。共同执行还需两端 NTP 同步；地图变化后必须重新复核航点。

### 误删地图或任务

删除内容会进入对应数据目录的 `.trash`。关闭程序后可人工移回原目录，但目录名不得冲突。

## 版本记录

完整新增、调整、修复和删除内容见 [CHANGELOG.md](CHANGELOG.md)。README 仅保留摘要。

**v0.13.1 · 2026-08-18**
<small>扩展已保存地图融合流程，可使用相同外参同步融合源地图携带的 PGM 并原子创建多图层地图。</small>

**v0.13.0 · 2026-08-17**
<small>新增端侧 ROS PGM 产物下载、断点分片恢复、二维栅格融合及绑定既有 PCD 地图的原子提交流程。</small>

**v0.12.0 · 2026-08-17**
<small>新增 RANSAC/Open3D 融合插件样例、PCD 转 ROS PGM 和无按钮数字输入框。</small>

**v0.11.1 · 2026-08-17**
<small>统一提高地图页、任务页和指控大屏的右键拖动平移速度。</small>

**v0.11.0 · 2026-08-17**
<small>新增可扩展地图融合算法、离线 PCD 融合和多设备 UDP 联合建图。</small>

**v0.10.1 · 2026-08-17**
<small>统一夜间主题下类型模板弹窗、下拉菜单和其他二级页面的原生 Qt 背景。</small>

**v0.10.0 · 2026-08-17**
<small>新增设备类型模板、共享图标与地图形状，并系统修复日间主题页面配色。</small>

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
