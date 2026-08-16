# 多异构智能体指挥与控制系统

当前地面站版本：**v0.9.1**  
兼容端侧 MQTAV：**v0.3.0**  
端侧 UDP 遥测包：**ros_udp_telemetry v0.2.1**  
端侧视频包：**usb_cam_rtsp v0.1.0**  
端侧任务协调包：**ros_task_control v0.1.0**

本项目是基于 PySide6 的桌面端指挥与控制系统。MQTT 负责设备摘要状态，UDP 14560 负责高频遥测，UDP 14561/14562 负责实时建图，UDP 14563/14564 负责地图轨迹任务下发与执行，RTSP 提供按需视频监控。各通道互相独立，单个模块故障不会阻止本地编辑与其他模块启动。

## v0.9.1 任务编辑界面主题修复

- 任务二级页面的设备栏、地图区、航点编辑、冲突检查和任务日志统一为深色面板。
- 航点表格、表头、列表 viewport、数值输入、选中态、悬停态、滚动区域与 splitter 均使用应用主题色，不再显示系统默认白底。
- 表格采用交替深色行和青绿色选中态，提高航点坐标与任务日志的可读性。

## v0.9.0 地图任务规划与执行

- “任务”页面提供真实任务列表、新建对话框和编辑/执行二级页面。任务绑定一张含 PCD 或 PGM 的地图，可选择多台在线或离线设备。
- 每台设备维护独立子任务，支持点云地面平面选点、ROS PGM 空闲栅格选点、XYZ 精确编辑、增删、排序、默认高度、巡航速度和启动延迟。每个有效子任务包含 2–500 个航点。
- 任务保存在 `data/task_server/<任务名>_<时间>/task.json`；创建与修改进入 `audit.jsonl`，每次执行另存不可变快照和永久 `events.jsonl`。删除任务移入 `.trash`。
- 冲突检查按恒速线性航段计算连续时空最近距离，默认水平 2 m、垂直 1 m、时间裕量 2 s。共同执行会阻止未解决冲突，也可填写原因强制执行并审计。
- `ccs-task-control-v1` 使用 UDP 14563/14564、MessagePack、zlib、CRC32、分片、幂等 request ID 和 ACK 重试。支持单设备下发/执行/停止及多设备预下发后统一 UTC 启动。
- 执行时使用 UDP 14560 全局 ENU 位姿在地图显示真实设备位置；地图可完全收起，让执行日志、交互和进度获得更多空间。
- 首页任务次数和最近任务摘要来自任务仓储；指控大屏任务选择会联动地图，并共享同一执行服务。
- 端侧接口见 `docs/EDGE_DEVICE_INTERFACES.md`。`edge_side_pkg/ros_task_control` v0.1.0 已实现协议接收、XML 持久化、UTC 调度与状态反馈；真实运动由设备专属 ROS 执行适配节点完成。

## v0.8.0 单设备 UDP 实时建图

- 地图详情工具栏新增“建图”。设备选择仅来自该地图创建时登记的设备，严格单选；离线设备可以尝试协商，已删除或缺少 IP 的设备不可启动。
- 地面站监听 UDP 14562，并从同一 socket 向端侧 UDP 14561 重试发送开始/停止指令。协议 ID 为 `ccs-map-stream-v1`，与 14560 遥测完全独立。
- 接收 zlib 压缩、MessagePack 分片的 little-endian float32 XYZ 点云；支持乱序和重复分片，整帧通过 CRC32、大小、有限坐标及会话来源校验后才参与融合。
- 点云按 `map <- body <- sensor` 变换到地图坐标系，再以默认 0.10 m 体素保存运行质心。实时预览最高 5 Hz，正式结果保留全部融合体素。
- 建图状态栏显示协商、建图、链路警告、中断、保存、完成或失败，以及完整帧、丢帧、接收点、融合点和最后数据时间。
- 会话每 5 秒检查点保存到地图目录 `.mapping/<session_id>`。返回列表、切换导航或退出时保留临时 PCD/轨迹；再次进入可保存或丢弃。
- 正常结束仅在新 PCD 解析成功且 schema 3 元数据可写时替换正式 `map.pcd`，失败会恢复旧地图。ZIP 同时包含 `trajectory.csv`。
- 端侧接口完整规范见 `docs/EDGE_DEVICE_INTERFACES.md`。`edge_side_pkg/ros_map_stream` v0.1.0 已实现 `ccs-map-stream-v1`，面向 ROS Melodic/Python 3.6.9，负责建图指令、点云/位姿同步预处理和 UDP 分片上传。

## v0.7.1 大屏交互与布局修复

- 实时图表重命名为“位置数据”和“姿态数据”；标题、单位和缩小后的三轴色标位于同一行，原生图例与轴标题不再占用绘图区。
- 地图页和指控大屏统一使用左键旋转、滚轮缩放、中键按住拖动平移；中键不再触发 VisPy 默认拖动缩放。
- 左栏改为“在线设备”，显示所有 MQTT 在线设备类型，并支持详情、摘要、完全收起三种状态。
- 完全收起设备栏后保留窄边恢复按钮，并将释放宽度交给三维视图；恢复时返回收起前的详情或摘要模式。
- 下方控制台支持完全收起，仅保留标题恢复条；展开时恢复原有地图、图层、视角及任务占位控件。
- 设备栏和控制台状态在当前运行期间保持，切换页面或调整窗口不会重置，重启软件后恢复默认布局。

## v0.7.0 科技化指控大屏

- “指控大屏”现在提供倒梯形系统标题、在线无人机栏、三维数字孪生区、实时状态栏和下部控制台。
- 左栏仅显示 MQTT 在线 UAV，支持摘要/详情模式；无人机离线时自动迁移选择，右栏展示 MQTT、UDP、电量、任务、FCU、位置和姿态。
- 位置 X/Y/Z 与姿态 roll/pitch/yaw 使用 Qt Charts 显示最近 60 秒趋势，每台设备最多保留 1200 个有效 UDP 样本；缺失遥测不会伪造零值。
- 中央视图支持 PCD 点云、ROS PGM 栅格和叠加模式，并显示选中无人机的坐标轴、位置标记和短轨迹。
- 地图编辑模式新增“导入 / 替换 PGM”。导入入口选择标准 ROS map_server YAML，仓储会校验关联 P2/P5 PGM、分辨率、原点和占据阈值。
- 控制台支持地图、图层、复位视角、适配全图、扫描动画和全屏切换。任务选择、开始和终止控件保留位置但明确禁用，不会发出控制指令。
- 点击“进入全屏”隐藏全局导航；按 Esc、再次点击按钮或离开大屏恢复普通窗口。装饰和图表定时器仅在页面可见时运行。

## v0.6.1 修复

- 修复“新建地图”对话框中设备选择区使用 Qt 默认白色视口，导致浅色设备文字不可见、看似空白的问题。
- 设备选择区现在显式使用深色背景、可见的复选框文本与悬停状态，在线、需关注和离线设备均正常显示。

## v0.6.0 地图管理

- 地图页默认显示可搜索的地图卡片，卡片包含名称、创建时间、建图设备、点云状态、点数和 XYZ 范围。
- “新建地图”允许从全部已保存设备中单选或多选建图设备，离线设备同样可以选择。创建后地图进入“等待导入点云”状态。
- 编辑模式支持修改名称、导入或替换 PCD、下载 ZIP 和删除。删除数据会移入 `data/map_server/.trash`，不会立即永久清除。
- 地图数据按 `<地图名>_<YYYYMMDD_HHMMSS>` 保存，每个目录包含 `map.json`，导入后同时包含原始 `map.pcd`。
- 双击地图卡片进入 VisPy 三维视图，支持旋转、缩放、平移、复位视角和适配全部点云。
- pypcd4 支持 ASCII、binary 和 binary-compressed PCD；大点云只对显示副本确定性降采样，原始文件保持不变。
- 三维查看器预留设备 XYZ 标记接口，本版本尚未接入实时设备位置。

## v0.5.1 修复

- 修复 ROS Melodic 工作空间未正确暴露 catkin Python 路径时，`ros_udp_telemetry_node.py` 报 `ModuleNotFoundError: ros_udp_telemetry` 的问题。
- 源码脚本现在会识别同包 `src/ros_udp_telemetry`，可兼容 roslaunch 直接解析源码脚本的部署状态。
- CMake 明确缓存 Python 3.6+ 解释器并注册端侧测试，标准 `catkin_make + source devel/setup.bash` 路径保持优先。

## v0.5.0 功能

### 设备绑定的数据状态卡

- “数据接收状态”改为独立轻量卡片网格，不再将全部状态挤在单个大面板中；800×600 到宽屏自动切换 1、2、3 列。
- 每张卡显示状态名称、主状态、绿/红/灰状态点和数据年龄；Livox 卡额外显示点云接收频率。
- 固定状态目录包括 Livox 驱动、FAST-LIO2 定位、PGM 地图生成、八叉树地图生成、占据栅格图生成和当前建图模式。
- “当前建图模式”显示端侧 ROS 文本值，例如“全局建图”或“增量建图”；消息超时后保留最近值并标记不可用。
- 详情页“编辑状态卡片”可全选、清空或组合选择。保存后立即写入对应设备的 `status_cards`，不同设备可拥有不同卡片集合。
- `devices.json` 升级为 schema 2；读取旧 schema 1 时自动补齐默认六项并原子迁移，不影响设备名称、IP、ping 结果或 MQTT 运行状态。

## v0.4.0 功能

### UDP 高频遥测

- 地面站默认监听 `0.0.0.0:14560/UDP`，端侧以 1 Hz 心跳报告独立 UDP 链路状态。
- 心跳到达后立即在线，超过 2 秒显示警告，超过 5 秒显示断开；超时采用地面站单调时钟。
- 一级数据以 20 Hz 更新全局 ENU 位姿、视觉位姿和 IMU；二级数据以 5 Hz显示点云接收元数据；三级数据以 1 Hz 显示 Livox、FAST-LIO2、Ego-Planner 红绿灰状态。
- MessagePack 信封包含设备 ID、启动会话、等级、序列、时间戳、协议 ID 和描述哈希。它使用 MAVROS/MAVLink 字段语义，但不是原生 MAVLink 帧。
- UDP 数据使用独立快照和 Qt queued signal，只刷新当前详情页，不以 20 Hz 重建设备卡片。
- 详情页删除“地图位置”字段；地图页面及其已有位置模型保持不变。

### 可配置 ROS 采集

- `ros_udp_telemetry` ROS 包按端侧 YAML 动态加载话题和消息类型，默认支持 MAVROS PoseStamped、标准 IMU 和普通 ROS 点分字段路径。
- 高于输出频率的数据按周期平滑：向量取均值，姿态四元数同半球归一化平均；状态量取最新值。
- 低于输出频率时重复最近值并携带数据年龄；未收到过数据时发送 unknown。
- 点云不进入 UDP payload，只发送接收状态、数据年龄和估算频率。
- 地面站和端侧分别部署描述配置，并通过标准化 SHA-256 哈希自动检查一致性。

## v0.3.0 功能

### 设备详情与 RTSP 视频

- 设备完整信息和视频区在宽屏下左右排列，窄窗口自动改为上下排列。
- 视频默认关闭，打开“视频流”开关后拉取 `rtsp://<设备IP>:8554/usb_cam`。
- 关闭开关、返回设备列表、切换设备、切换主导航或退出程序时停止播放器并释放资源。
- 视频状态包括已关闭、连接中、播放中、无媒体和播放失败；失败后需手动重新打开。
- 使用 PySide6 Qt Multimedia 的 `QMediaPlayer`/`QVideoWidget` 解码 RTSP，不改变 MQTT 在线、健康和任务状态。
- 设备 IP 来自 `config/devices.json`，IPv6 地址会自动生成带方括号的 RTSP URL。

### MQTT 设备监测

- `amqtt` 在 `0.0.0.0:1883` 提供匿名明文 Broker，Paho 订阅 `mqtav/+/presence`、`heartbeat` 和 `status`。
- 合法心跳立即在线；超过 2 秒进入需关注，超过 5 秒进入离线/error。
- 实时更新电量、FCU 健康、飞行模式、任务、解锁、MAVLink 状态、电池电压和电流。
- 每台设备保存最近 500 条 info/warning/error 日志。
- MQTT 模式启动时设备运行数据为离线/未知，不使用模拟遥测。

### 其他页面

- 首页展示在线/离线设备数、地图数、任务次数和上次任务摘要。
- 设备页支持搜索、类型/状态筛选、新建设备、批量删除和详情跳转。
- 地图页保留本地 XY 网格和设备点位；当前协议不包含视频或位置坐标。
- 任务页面仍显示“功能开发中”；指控大屏已在 v0.7.0 接入实时监控。

## 环境要求

### 地面站

- Python 3.10+
- PySide6 6.6+
- `amqtt`、`paho-mqtt`、PyYAML、MessagePack
- NumPy、VisPy、pypcd4；三维显示需要可用的 OpenGL 驱动
- Windows 或支持 Qt 6 Multimedia 的桌面系统

### 端侧

- Ubuntu 18.04
- ROS Melodic
- Python 3.6.9（MQTAV）
- GStreamer 1.0、`ros-melodic-usb-cam`、GStreamer RTSP Server

## 地面站安装与启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

Linux/macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

最低窗口尺寸为 800×600。地面站防火墙需要允许 TCP 1883、UDP 14560 和 UDP 14562；端侧需允许 UDP 14561。视频播放时还需要允许端侧 RTSP TCP 8554 及媒体连接。若 OpenGL 不可用，设备监控等其他页面仍可运行，地图详情会显示渲染故障信息。

## 地图创建、保存与下载

1. 进入“地图”，点击“新建地图”，填写名称并选择至少一台建图设备。
2. 点击“编辑”，选择一张地图后使用“导入 / 替换 PCD”写入点云，或选择“导入 / 替换 PGM”并打开 ROS map_server YAML。
3. 双击卡片复原地图；鼠标左键拖动旋转、滚轮缩放、中键按住拖动平移。指控大屏使用相同操作，并可切换点云、栅格和叠加图层。
4. 使用“下载地图”导出 ZIP。压缩包包含 `map.json` 及已导入的 `map.pcd`、`map.yaml`、`map.pgm`。
5. 实时建图时，进入地图详情并点击“建图”，选择创建地图时登记的一台设备。已有 PCD 会先确认，点击“结束建图”后保存最终融合点云。

地图根目录为 `data/map_server`。`map.json` 当前写入 schema 3，并兼容读取 schema 1/2；schema 3 在 PCD/PGM 元数据之外记录最近建图设备、会话、起止时间、协议、体素尺寸、帧/点统计和轨迹文件。中断结果保存在 `.mapping`，正式提交前不会覆盖旧 PCD。不要在程序运行时手工修改活动地图目录；需要恢复误删地图时，可关闭程序后将对应目录从 `.trash` 移回 `map_server` 并避免目录重名。

## 指控大屏使用

1. 先在“地图”页面导入 PCD 或 PGM/YAML；就绪地图会自动进入大屏地图选择框。
2. 所有 MQTT 在线设备都会进入左侧列表。选择设备后，右侧状态和 UDP 趋势切换到该设备；左栏可切换详情、摘要或完全收起。
3. 使用“点云 / 栅格 / 叠加”切换中央图层；UDP 全局位姿按当前地图本地 ENU 坐标直接绘制，本版本不执行跨地图坐标变换。
4. 下方控制台可收起为标题条，为三维视图释放高度。无在线设备、无有效地图或 UDP 数据中断时，页面保留导航和明确状态，不生成模拟位置或任务结果。

## 设备与 MQTT 配置

地面站设备档案在 `config/devices.json`。每台设备可配置独立状态卡片：

```json
{
  "schema_version": 2,
  "devices": [{
    "device_id": "UAV_001",
    "status_cards": ["livox_driver", "fastlio2", "mapping_mode"]
  }]
}
```

卡片 ID 必须来自内置目录，重复或未知 ID 会被配置仓储拒绝。端侧共享身份在 `edge_side_pkg/edge_device_config/config/device.yaml`：

```yaml
schema_version: 1
device:
  id: "UAV_001"
  ip: "192.168.151.250"
```

两份配置中的 ID 和 IP 必须完全一致；根测试会自动检查。地面站新建设备后，需要由部署人员将对应身份同步到端侧共享配置。手工 ping 的可用性只代表最近测试结果，不代表 MQTT 在线状态。

MQTT 地面站配置在 `config/mqtt.json`，默认 Broker 地址为 `0.0.0.0:1883`，QoS 为 1，心跳检查频率为 1 Hz。首版只适合可信局域网，不提供认证、TLS、ACL 或下行控制。

UDP 地面站配置位于 `config/udp_telemetry.json`。`descriptors` 中每项包含唯一 `name`、中文 `display_name`、`type` 和等级；固定等级频率为 20/5/1 Hz。端侧对应配置位于 `edge_side_pkg/ros_udp_telemetry/config/telemetry.yaml`，额外包含 ROS topic、message type、字段根路径和新鲜度阈值。修改描述项后必须同时更新两端，否则地面站会因描述哈希不一致拒收数据。

实时建图配置位于 `config/map_building.json`，默认控制端口 14561、数据端口 14562、5 Hz 点云、0.10 m 体素、500 ms 指令重试、1 秒分片超时、2 秒链路警告和 5 秒中断。修改包长或资源上限时必须同步端侧实现。所有端侧交互契约统一维护在 `docs/EDGE_DEVICE_INTERFACES.md`。

## 端侧部署

端侧包位于 `edge_side_pkg`，可整体复制到 catkin 工作空间：

```bash
sudo apt update
sudo apt install python3-paho-mqtt python3-yaml python3-msgpack python3-numpy python3-catkin-pkg python3-rospkg \
  ros-melodic-mavros ros-melodic-mavros-extras ros-melodic-usb-cam \
  ros-melodic-cv-bridge ros-melodic-image-transport ros-melodic-sensor-msgs ros-melodic-nav-msgs \
  ros-melodic-geometry-msgs \
  libgstreamer1.0-dev libgstrtspserver-1.0-dev \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
mkdir -p ~/catkin_ws/src
cp -r /path/to/CCS_dev/edge_side_pkg ~/catkin_ws/src/edge_side_pkg
cd ~/catkin_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch usb_cam_rtsp usb_cam_rtsp.launch
roslaunch ros_udp_telemetry ros_udp_telemetry.launch destination_host:=192.168.151.100
roslaunch ros_map_stream ros_map_stream.launch
roslaunch ros_task_control ros_task_control.launch
```

首次部署或更新 `ros_udp_telemetry` 后必须重新构建并加载当前终端环境：

```bash
cd ~/c3po_ctrl_ws
source /opt/ros/melodic/setup.bash
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
python3 -c "import ros_udp_telemetry; print(ros_udp_telemetry.__version__)"
roslaunch ros_udp_telemetry ros_udp_telemetry.launch destination_host:=192.168.151.100
```

导入检查应输出 `0.2.1`。建议将 `source ~/c3po_ctrl_ws/devel/setup.bash` 写入启动终端使用的 shell 环境；通过 systemd、tmux 或启动脚本运行时，也必须在同一命令上下文中 source。

将 `destination_host` 替换为地面站内网 IP。可用 `ss -ulnp | grep 14560` 检查端侧发送配置，并在地面站查看 UDP 链路、最后心跳和最后数据时间。若显示“模块故障”，检查 14560 端口占用；若显示哈希不匹配，同步两端 descriptor；若只有心跳无遥测，使用 `rostopic hz` 检查 YAML 中的话题。

`usb_cam_rtsp` 默认读取 `/dev/video0`，以 640×480、30 FPS、2000 kbps H.264 推送到 `rtsp://<device.ip>:8554/usb_cam`。参数在 `edge_side_pkg/usb_cam_rtsp/config/video.yaml` 中修改。使用 GStreamer 验证：

```bash
gst-launch-1.0 rtspsrc location=rtsp://127.0.0.1:8554/usb_cam latency=100 \
  ! rtph264depay ! avdec_h264 ! autovideosink
```

若相机帧没有到达，检查 `rostopic hz /usb_cam/image_raw` 和设备权限；若缺少 `x264enc`，安装 `gstreamer1.0-plugins-ugly`。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m compileall -q ccs_monitor run.py tests
```

地面站测试覆盖地图仓储、ASCII PCD、范围计算、ZIP 导出、UDP schema/协议/状态机/界面、RTSP 生命周期、MQTT 状态机和配置对齐。binary 与 binary-compressed PCD 由 pypcd4 解析，真实 OpenGL 交互需在桌面环境验证。MQTAV 测试：

```bash
cd edge_side_pkg/MQTAV
PYTHONPATH=src python3 -m unittest discover -s test -v
PYTHONPATH=src python3 scripts/check_version.py
```

ROS/GStreamer 的真实编译、模拟图像发布和摄像头 RTSP 验证需要在 Ubuntu 18.04 + ROS Melodic 目标环境执行。

UDP 端侧纯 Python 测试：

```bash
cd edge_side_pkg/ros_udp_telemetry
PYTHONPATH=src python3 -m unittest discover -s test -v
```

ROS 集成验证需在 Melodic 环境启动模拟 PoseStamped、Imu 和 PointCloud2 发布器，再确认地面站以 20/5/1 Hz 刷新对应区域。

实时建图端侧测试与版本检查：

```bash
cd edge_side_pkg/ros_map_stream
PYTHONPATH=src python3 -m unittest discover -s test -v
PYTHONPATH=src python3 scripts/check_version.py
```

真机联调前确认 `/livox/lidar` 与 `/Odometry` 的类型、频率和 frame ID 与 `config/mapping.yaml` 一致，并放行端侧 UDP 14561 与地面站 UDP 14562。

任务协调端侧测试与版本检查：

```bash
cd edge_side_pkg/ros_task_control
PYTHONPATH=src python3 -m unittest discover -s test -v
PYTHONPATH=src python3 scripts/check_version.py
```

运行任务前需要启动实现 `TaskExecutionCommand/TaskExecutionFeedback` 的设备控制适配节点，配置 NTP，并放行端侧 UDP 14563 与地面站 UDP 14564。

## 目录结构

```text
CCS_dev/
├── ccs_monitor/                   # 地面站 PySide6 应用
├── config/                        # 设备、MQTT、遥测 UDP 与建图 UDP 配置
├── data/map_server/               # 地图元数据、PCD 与 .trash 回收目录
├── edge_side_pkg/
│   ├── edge_device_config/        # 共享设备 ID/IP，v0.1.0
│   ├── MQTAV/                     # ROS1 MQTT 遥测包，v0.3.0
│   ├── usb_cam_rtsp/              # USB 相机 RTSP 推流包，v0.1.0
│   ├── ros_udp_telemetry/          # ROS/MAVROS UDP 遥测包，v0.2.1
│   ├── ros_map_stream/             # ROS 实时建图上行包，v0.1.0
│   ├── ros_task_control/            # ROS 任务接收与执行协调包，v0.1.0
│   ├── MQTAV.zip                  # 端侧部署归档
│   └── README.md
├── docs/DEVELOPMENT_NOTES.md
├── docs/EDGE_DEVICE_INTERFACES.md # 端侧交互协议总册
├── tests/
├── CHANGELOG.md
└── requirements.txt
```

项目版本采用三段式定义。每次代码更新需同步地面站版本常量、README、开发笔记、CHANGELOG 和 `docs/EDGE_DEVICE_INTERFACES.md`；端侧 ROS 包维护各自的 `package.xml` 版本和包内文档。
