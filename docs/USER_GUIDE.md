# 使用、部署与故障排查

安装包和便携版入口见 [发布指南](RELEASING.md)。以下源码及端侧操作命令以各自软件根目录为工作目录。

## 环境要求

### 地面站

- Windows 10/11，或带桌面环境的 Linux。
- Python 3.10–3.13、PySide6 6.8.3；仓库的 `.python-version` 默认选择 Python 3.10。
- `pyproject.toml` 声明 amqtt、paho-mqtt、PyYAML、MessagePack、NumPy、VisPy、pypcd4 和 Open3D 等直接依赖；`requirements.txt` 仅作为传统 pip 的兼容入口。
- 三维地图需要 OpenGL 2.1+ 兼容驱动；视频播放需要 `PATH` 中带 libsrt 的系统 FFmpeg，可用 `ffmpeg -protocols` 检查输入协议 `srt`。
- 最低窗口尺寸 800×600，建议 1440×900 或更高。

### 端侧

- Ubuntu 20.04、ROS Noetic、Python 3、MAVROS。
- GStreamer 1.16+、SRT 插件、OpenCV、cv_bridge、image_transport 及相关 ROS 消息包。
- 视频推流需要已有的 `sensor_msgs/Image` 或 `sensor_msgs/CompressedImage` 话题；实时建图需要 PointCloud2 和同步位姿来源。

### 网络

| 端口 | 方向 | 用途 |
| --- | --- | --- |
| TCP 1883 | 端侧 → 地面站 | MQTT 状态与心跳 |
| UDP 123 | 端侧 → 地面站 | 离线 NTP 时间同步 |
| UDP 9000 | 地面站 → 端侧 | SRT H.264/MPEG-TS 视频 |
| UDP 14560 | 端侧 → 地面站 | 高频遥测与心跳 |
| UDP 14561 | 地面站 → 端侧 | 实时建图控制 |
| UDP 14562 | 双向 | 建图状态、PCD 分片描述符与确认 |
| TCP 动态 | 指控平台 → 端侧 | v2 建图 ZIP 成果 HTTP 下载（端侧返回带短期令牌的 URL） |
| UDP 14563 | 地面站 → 端侧 | 任务下发与控制 |
| UDP 14564 | 端侧 → 地面站 | 任务 ACK、状态和进度 |
| UDP 14565 | 地面站 → 端侧 | 重定位协商、地图描述、启动与初始位姿 |
| UDP 14566 | 端侧 → 地面站 | 重定位状态、心跳和结果 |
| TCP 14601 | 端侧 → 地面站 | 带令牌和 Range 的重定位地图 ZIP 下载 |

系统面向可信局域网，不提供 MQTT、SRT 或其他 UDP 通道的加密认证。多设备同步任务要求地面站和端侧通过 NTP 对齐 UTC 时间。

当前配套端侧 `epgeneral_map_stream` v0.13.2。联合模式由地面站为各设备创建独立 v2 会话，要求至少两台设备并指定主设备；端侧回传联合作业身份，地面站按外参融合 PCD 和 PGM。Scout 建图仍按 FAST-LIO、pointcloud mapper、TF manager、pose adapter 顺序启动；Go2 继续使用 accumulator backend；Ground-Air AGV 使用原生 mapping/save service backend。
Ground-Air AGV 已禁用上电自启动，需要时手动启动 `ccs-edge-dev.service`；两条静态 TF 由一键脚本最后直接启动，建图/重定位使用不含静态 TF 的精简控制入口。v0.13.2 建图客户端兼容 guard `1`、`2`，避免重定位升级后的准备阶段版本误拒绝。部署、人工诊断限制与回滚见 [专项说明](../edge_side_pkg/documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md)。

地图详情页左侧显示可收起的在线设备栏，集中展示任务/建图状态、电量、坐标系和单路可控视频。Scout/WheelTech 的本地 odom 位姿取 UDP `vision_pose`，Go2 等设备取 `global_pose`；地图态势优先使用已持久化的 `map <- odom` 重定位绑定，未绑定时仅按同坐标系或与设备 ID 精确匹配的建图外参显示。所有地图点云按高度使用低红高紫的 rainbow 色谱。

## 部署方法

### 1. 获取并部署地面站

推荐使用 `uv`。`uv sync --locked` 会根据 `.python-version` 创建或复用 `.venv`，并严格按
`uv.lock` 安装依赖；后续启动不需要手动激活虚拟环境。

```powershell
git clone https://github.com/AADCL/CCS_dev.git
cd CCS_dev
uv sync --locked
winget install --id Gyan.FFmpeg -e
ffmpeg -hide_banner -protocols
uv run python run.py
```

Linux：

```bash
git clone https://github.com/AADCL/CCS_dev.git
cd CCS_dev
uv sync --locked
sudo apt install ffmpeg
ffmpeg -hide_banner -protocols
uv run python run.py
```

无法使用 `uv` 时，仍可通过 `python -m venv .venv`、激活环境并执行
`python -m pip install -r requirements.txt` 完成传统安装，之后使用 `python run.py` 启动。

启动前核对：

- `config/devices.json`：设备 ID、名称、类型、IP 或 `.local` mDNS 主机名，以及设备级状态卡覆盖。
- `config/device_types.json`：类型显示名称、图标路径、地图形状和默认状态卡片。
- `config/mqtt.json`：MQTT Broker 与心跳阈值。
- `config/udp_telemetry.json`：UDP 14560 描述项与分级频率。
- `config/map_building.json`：实时建图 14561/14562 参数。
- `config/map_fusion_algorithms.json`：融合算法、默认参数、启用状态和脚本指纹。
- `config/task_system.json`：任务系统 14563/14564 参数。
- `config/srt_video.json`：系统 FFmpeg 命令、显示尺寸、探测/连接超时和重试参数。带目录的 FFmpeg 路径必须相对软件根目录。
- `config/ntp.json`：内置 NTP Server 的监听地址、端口、层级和参考时钟标识。

`ffmpeg -protocols` 的 Input 列表必须包含 `srt`；不同发行版提供的 FFmpeg 构建选项可能不同。

设备页的“设备地址”接受 IPv4、IPv6 或以 `.local` 结尾的 mDNS 主机名，例如
`nrc17.local`。程序优先使用操作系统名称解析器，并在系统未注册 `.local` 解析时
通过 multicast DNS 主动查询；“测试连接”会先显示解析后的 IP，再执行 Ping。
解析结果缓存 10 秒，并用于 MQTT、建图、任务、重定位和 PGM 数据来源校验。

放行地面站 TCP 1883、UDP 123、14560、14562、14564，然后启动。Windows 自带
`W32Time` 若正在占用 UDP 123，需要在管理员 PowerShell 中停止并禁用该服务，再为
Private/LocalSubnet 放行 UDP 123，由 CCS 内置 NTP Server 独占端口：

```powershell
Stop-Service W32Time -Force
Set-Service W32Time -StartupType Disabled
New-NetFirewallRule -DisplayName "CCS NTP Server (Private LAN)" `
  -Direction Inbound -Action Allow -Protocol UDP -LocalPort 123 `
  -Profile Private -RemoteAddress LocalSubnet
uv run python run.py
```

首次启动会读取设备配置，并初始化地图和任务数据目录。

### 2. 从零部署端侧

完整步骤以[端侧使用手册](../edge_side_pkg/documents/USER_MANUAL.md)为准；跨工作空间的 ROS、TF、文件接口和七类配置逐参数说明见[接口参考](../edge_side_pkg/documents/INTERFACE_REFERENCE.md)。

端侧 ZIP 包含八个 ROS 源码包。常规设备选择七个公共包；Ground-Air 另加专用控制包及外部 ground_air_msgs 依赖。deploy、documents 不放入 catkin src，按所选 profile 安装运行配置、脚本和适配 launch。

单包 launch 默认读取 epgeneral_device_config/config；设备一键脚本实际读取 <CCS工作空间>/config/<profile>。修改前确认入口、备份实际文件，修改后重启对应节点。默认 YAML 混有不同设备示例，部署必须选择并核对 profile。

公共顺序为 Noetic → 设备 underlay → CCS overlay，Scout 还需要按脚本顺序加载 RealSense/navigation/Livox 工作空间。设置设备身份、各网络目标、ROS 输入、外参、地图状态路径及 NTP 后再构建和启动。

- Go2：重定位功能禁用，一键脚本不启动任务包。
- Scout：启用导航适配器和 D435i/SRT，启动时等待硬件实际消息。
- Wheeltec：启用导航适配器，无相机，不启动视频，停止流程发送零速度。
- Ground-Air：按[专项指南](../edge_side_pkg/documents/GROUND_AIR_AGV_DEPLOYMENT.md)手动启动用户服务，保持开机自启动 disabled；静态 TF 与阶段进程分别管理，一键脚本不启动任务包。

按需单包调试应停止重复的一键节点并显式传入配置，例如：

~~~bash
CFG=/home/nvidia/ccs_edge_ws/config/scout_mini
roslaunch epgeneral_mqtav epgeneral_mqtav.launch \
  device_config_file:="$CFG/device.yaml" config_file:="$CFG/epgeneral_mqtav.yaml"
~~~

设备身份必须与地面站设备表一致；仅设置 CCS_GROUND_STATION_IP 不会改写所有 YAML。网络、授时、单包使用及升级回滚命令详见手册。

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

- 启动后默认进入首页，查看带语义图标和明确文字的在线/离线设备、地图数、任务执行次数、最近任务和各子系统运行状态。
- 子系统卡片区分启动中、正常、降级、未启用和故障；FFmpeg/SRT 表示地面站解码能力，不表示端侧当前正在推流。
- NTP、MQTT Broker、MQTT 数据订阅、UDP 高频遥测和 FFmpeg/SRT 图标随日间/夜间主题切换；主题切换不改变卡片状态或统计数据。
- 在线状态来自 MQTT；地图和任务摘要来自本地持久化仓储。

### 设备页面

- 使用搜索与类型/状态条件筛选设备。
- 点击“类型模板”新增或编辑设备类型，上传 PNG/JPEG/SVG 图标，选择箭头、长方体、球体或原点地图形状，并绑定默认功能卡片。被设备引用的模板不能删除。
- 点击“新建”从类型模板中选择类型，填写名称、ID、IP、SRT 端口、延迟和电池估算 profile，测试连接后保存；ID 会检查重复并统一为大写。电池 profile 可选择不估算、Scout Mini 或 WheelTech R550P，曲线与校准方法见 [设备电池曲线校准](BATTERY_CALIBRATION.md)。
- 点击“编辑”后可批量选择删除设备；编辑状态下双击设备卡可修改名称、类型、ID、IP、SRT 端口和延迟。修改 IP 必须重新测试，修改 ID 后必须同步端侧配置。
- 设备 ID 修改会级联当前地图创建引用和未归档任务，任务修订与下发状态失效；历史执行快照保持不变。
- 详情页右上角显示 MQTT 卡片，正文显示 UDP、电量、任务、运行模式、位姿、IMU、点云和设备状态卡。
- 详情页的状态卡可跟随类型模板动态更新，也可切换为设备自定义覆盖；空自定义表示明确不显示卡片。
- 紧凑日志表支持 info/warning/error 筛选和清除当前设备日志，并记录 MQTT、UDP、传感器及 SRT 状态变化；视频开关按需启动系统 FFmpeg SRT Caller，离开页面立即终止进程并取消重试。

### 地图页面

- 点击“新建地图”后选择单机建图、多机建图或空地图。单机只需选择一台设备并自动使用默认内置算法；多机至少选择两台设备，并指定主设备、融合算法及“主坐标系 <- 从坐标系”的 XYZ/RPY 外参。
- 点击“地图融合”选择至少两张有效 PCD 地图、主地图、各从地图外参和算法，成功后创建独立融合地图；默认只融合 PCD。
- “地图融合”弹窗可勾选“同步融合 PGM 图”。勾选后所有源地图都必须携带有效 PGM，系统使用同一 X/Y/Yaw 外参同步融合栅格，并将 PCD、PGM、YAML 一并绑定到新地图。
- 点击“PGM 融合”选择一张有效 PCD 目标地图，添加端侧来源并填写各自 `source_map_id` 和“目标 PCD frame <- 来源 PGM frame”的 X/Y/Yaw 外参。至少需要两个图层，可将目标地图已有 PGM 作为单位变换来源。
- PGM 下载与实时建图互斥。输出分辨率不得细于来源最细值；若来源超出目标 PCD XY 边界，确认后才会裁剪并原子替换目标 PGM。
- 点击“融合算法”可导入标准 `.py` 插件、设置默认算法和 JSON 参数。`examples/` 提供直接拼接、NumPy RANSAC 和 Open3D ICP 示例；导入代码属于受信任本地代码并在独立进程运行。
- 编辑模式支持重命名、导入/替换 PCD 或 PGM、从 PCD 生成 PGM、下载 ZIP 和删除。生成参数包含分辨率、高度范围、留白、点数阈值、障碍膨胀、空白区语义及 ROS 阈值。
- 双击卡片进入三维详情：左键旋转、滚轮缩放、右键拖动快速平移。任务页和指控大屏使用相同的三维操作速度。网格开启时自动显示稀疏坐标刻度；“分辨率”和“透明度”随网格开关启用或禁用，光标坐标仍可独立开关。
- 点击“重新建图”可统一发起单机或多机任务。实时预览使用内置体素融合，结束时由所选算法生成正式 PCD。
- 单个从设备掉线时任务进入降级态，可剔除后继续或中止全部；主设备掉线必须中止。中断结果保存在 `.mapping`，离线融合临时结果保存在 `.fusion`。

### 任务页面

- 新建任务默认使用指控大屏当前激活地图；地图选择框标注激活状态，所有状态设备均可选择且在线设备优先显示。
- 为每台设备在点云或 free 栅格中选点，并在表格中修改 XYZ、增删或调整顺序。
- 左侧设备以卡片显示端侧状态、revision 和任务点数量，创建、读取、删除子任务按钮位于对应卡片内部；未选择设备时右侧点列表收起。
- 选点入口位于右侧点列表中间的“开始选点”按钮，地图工具栏只负责图层和浏览；设置默认高度、巡航速度、启动延迟和冲突阈值，每个有效子任务需要 2–500 个航点。
- 子任务可单独保存、下发和执行；共同执行会先下发最新修订，再按统一 UTC 时间启动。
- 冲突应通过修改轨迹或时间消解；强制执行必须填写原因并写入审计日志。

地图页和任务页只读展示全局激活地图状态。激活地图持久化在 `data/map_server/active_map.json`，只有指控大屏的地图选择器可以修改它；任务绑定地图可以与全局激活地图不同。点云高度光标保留 `z <=` 当前阈值的点，滑块向下调整时隐藏更高点云。

### 指控大屏

- 顶栏左侧显示 MQTT/UDP 状态，中间显示系统标题，右侧显示在线设备数和时钟；扫描动画已移除。左侧显示 MQTT 在线设备，中间显示 PCD/PGM/叠加地图，右侧显示由同一份本地 odom 位姿生成的状态和位置/姿态趋势；没有有效 odom 时两组数据同时显示无数据。
- 下方控制台选择地图与任务，可共同开始或终止任务。

Scout 在任务文件完整提交后校验当前进程产生的 `localized` 状态、实时 `/fastlio_odom` 和 `map<-odom` TF，并启动常驻导航栈；`/move_base` 就绪后端侧才报告任务 `ready`。执行、完成和常规停止复用该进程，删除、急停或节点关闭才卸载导航。定位暂时不可用时保留任务并自动重试准备。
- 设备栏和控制台可收起；进入全屏后按 Esc 恢复。

## QA

### PowerShell 不允许激活虚拟环境

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

也可直接使用 `.\.venv\Scripts\python.exe`，无需激活。

### Python 依赖安装失败

确认使用 64 位 Python 3.10–3.13，再执行 `python -m pip install --upgrade pip setuptools wheel`。不要在 ROS Melodic 的 Python 3.6 环境安装地面站依赖。

### roslaunch 提示 `ModuleNotFoundError`

```bash
cd ~/catkin_ws
source /opt/ros/noetic/setup.bash
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

### SRT 无画面

先执行 `ffmpeg -protocols`，确认 Input 列表包含 `srt`。再检查设备地址（IP 或 `.local` mDNS）、UDP 9000、防火墙、设备页端口/延迟，以及 ROS 图像话题的实际类型和频率。端侧必须启动 `epgeneral_video_srt v0.1.1`，并可用下列命令验证本机 Listener：

```bash
ffplay "srt://127.0.0.1:9000?mode=caller&transtype=live&latency=120000"
```

缺少 `x264enc` 时安装 `gstreamer1.0-plugins-ugly`；缺少 `h264parse` 或 `srtsink` 时安装 `gstreamer1.0-plugins-bad`。地面站区分 FFmpeg 缺失、未启用 SRT、连接超时、端侧拒绝和解码失败。

### 地图黑屏或 OpenGL 错误

更新显卡驱动并确认 OpenGL 2.1+。远程桌面、虚拟机和 offscreen 环境可能无法创建上下文；非三维页面仍可使用。

### PCD/PGM 导入失败

PCD 必须包含有限 XYZ；PGM 必须为 P2/P5，并由有效 ROS map_server YAML 指定 image、resolution、origin 和阈值。失败不会覆盖旧地图。

从 PCD 生成 PGM 时，确认高度范围内存在点，并避免使用过小分辨率生成超大栅格。未命中区域默认是 unknown，可在生成对话框改为 free；生成失败不会替换已有 PGM。

### PGM 下载提示端侧版本不支持

当前端侧 `epgeneral_map_stream` v0.13.2 已实现实时 PCD 分片、最终 PCD/PGM/YAML 成果 ZIP、短期令牌 HTTP 服务和联合作业身份回传。Scout 使用本地时间 `YYYYMMDD_HHMMSS` 作为固定 `map_name`，同一名称贯穿 pointcloud mapper、`filtered_camera_init.pcd`、`finalize_map.py --replace-raw` 和成果 manifest；Go2 继续使用 accumulator backend，Ground-Air AGV 使用原生 mapping/save service backend。

### 融合算法无法导入或执行失败

插件必须定义 `PLUGIN_API_VERSION = 1`、唯一 `ALGORITHM_ID` 和 `fuse_maps()`，并生成有效 XYZ PCD。检查插件依赖是否安装在地面站 Python 环境。超时、崩溃和无效输出不会覆盖正式地图，临时任务会保留在 `data/map_server/.fusion` 或地图的 `.mapping` 目录。

导入后的脚本保存在 `data/map_fusion_algorithms`，注册表只记录该目录内的相对文件名。换机部署时必须同时携带 `config/map_fusion_algorithms.json` 和该算法目录；旧版 Windows/POSIX 绝对路径会在启动时按文件名迁移到当前安装目录，找不到对应脚本或 SHA-256 不一致时会禁用外部算法并保留原配置供排查。

Open3D ICP 示例需要 `open3d>=0.18`。RANSAC/ICP 均假设用户外参已提供合理的粗对齐；提示重叠率或 fitness 过低时，应先检查主从坐标方向与 XYZ/RPY 外参。

### 任务无法下发或同步执行

确认设备和 IP 有效、UDP 14563/14564 可达、epgeneral_task_control 已启动、子任务已保存且无未处理冲突。共同执行还需两端 NTP 同步；地图变化后必须重新复核航点。

### 误删地图或任务

删除内容会进入对应数据目录的 `.trash`。关闭程序后可人工移回原目录，但目录名不得冲突。

