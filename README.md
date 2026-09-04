<p align="center">
  <img src="icons/lab_logo/logo.png" alt="AADCL" width="96">
</p>

<h1 align="center">CCS · 多异构智能体指挥与控制系统</h1>

<p align="center">设备监测 · 联合建图 · 重定位 · 任务编排 · 指控大屏</p>

<p align="center">
  <img alt="版本" src="https://img.shields.io/badge/version-0.23.0-1677ff">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%E2%80%933.13-3776AB">
  <img alt="Qt" src="https://img.shields.io/badge/PySide6-6.8.3-41CD52">
  <img alt="许可证" src="https://img.shields.io/badge/license-Apache--2.0-blue">
</p>

CCS 是面向可信局域网的 PySide6 地面站，连接无人车、无人机、四足机器人等异构设备。
地面站与 ROS 端侧功能包通过独立的 MQTT、UDP、SRT 通道协作；单个通信模块异常时，
本地设备编辑、地图仓储和其他可用页面仍可使用。

**快速入口：** [安装与发布](docs/RELEASING.md) · [使用与部署](docs/USER_GUIDE.md) ·
[端侧指南](edge_side_pkg/README.md) · [通信协议](docs/EDGE_DEVICE_INTERFACES.md) ·
[电池曲线校准](docs/BATTERY_CALIBRATION.md) · [版本记录](CHANGELOG.md)

## 核心能力

| 能力 | 功能 |
| --- | --- |
| 设备与总览 | 设备类型模板、配置持久化、在线状态、位姿/IMU、电量及子系统健康状态 |
| 实时视频 | 按需连接设备 SRT 视频，支持解码能力检测、超时与重试 |
| 地图工作台 | PCD/PGM 导入、三维显示、栅格生成、算法插件、单机及多机联合遥控建图 |
| 重定位 | 地图下发、交互选点、设备与地图坐标绑定、在线位置标记 |
| 任务编排 | 多设备航点、冲突检查、任务下发、统一 UTC 执行与过程日志 |
| 指控大屏 | 地图态势、轨迹和位置/姿态趋势，全屏与面板折叠 |
| 本地部署 | 内置 MQTT/NTP 服务、日夜主题、整目录数据迁移 |

## 选择发布形式

| 形式 | 适合场景 | 运行准备 |
| --- | --- | --- |
| Windows 安装包 | Windows 10/11 x64 日常使用 | 选择可写安装目录；自带 Python、Qt、FFmpeg |
| Ubuntu .run 安装包 | Ubuntu 20.04/22.04 x64 桌面 | 交互或自动安装；自带应用运行依赖 |
| 地面站便携 ZIP | 开发、现场调试、自定义 Python 插件 | 解压后先用 uv 或 pip 安装依赖 |
| 端侧配套 ZIP | ROS 设备部署 | 七个公共 ROS 包；Ground-Air profile 另含专用控制包 |

v0.23.0 新增本地构建能力。产物位于构建机 dist/，具体文件名、校验和安装参数见
[发布指南](docs/RELEASING.md)。[验证记录](docs/RELEASE_VALIDATION.md) 单独列出已验证和待验证平台。

发布默认设备列表为空；内置类型、图标与算法示例保留。安装与卸载默认保留已有
config/、data/，不会把开发机的地图、任务和历史记录带入新安装。

## 快速开始

已获取安装包时，直接运行安装向导或 Ubuntu .run 文件。便携版和源码使用：

    git clone https://github.com/AADCL/CCS_dev.git
    cd CCS_dev
    uv sync --locked
    uv run --no-sync python run.py

已解压便携版时跳过克隆步骤。uv 使用 Python 3.10.19；源码支持 Python 3.10–3.13。
备用 pip 安装命令和 Windows/Ubuntu 辅助脚本见 [环境准备](docs/RELEASING.md#便携版准备与启动)。

源码与便携版的视频功能需要支持 SRT 的系统 FFmpeg。启动前检查：

    ffmpeg -hide_banner -protocols

Input 部分应包含 srt。设备地址、端口、MQTT 和 NTP 参数位于 config/。
创建或修改地面站设备后，还需同步端侧设备身份与通信地址。

## 平台与运行要求

- 发布目标：Windows 10/11 x64，Ubuntu 20.04/22.04 x64 桌面环境。
- 三维地图需要 OpenGL 2.1+ 兼容显卡驱动；建议分辨率 1440×900 以上。
- 软件目录须对当前用户可写，配置、主题、地图、任务与日志随目录保存。
- NTP UDP 123 可能需要管理员配置权限或处理端口占用，安装器不自动修改系统服务。
- 多设备同步任务需要统一授时；设备通道的端口与防火墙说明见 [通信协议](docs/EDGE_DEVICE_INTERFACES.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [发布指南](docs/RELEASING.md) | 构建、离线安装、便携版、升级卸载与迁移 |
| [验证记录](docs/RELEASE_VALIDATION.md) | 构建证据、自动化回归及四系统实机验收状态 |
| [使用与部署指南](docs/USER_GUIDE.md) | 页面操作、地面站与端侧部署、网络配置和故障排查 |
| [端侧包](edge_side_pkg/README.md) | 七个公共 ROS 包、Ground-Air 控制包与设备 profile |
| [Ground-Air AGV 建图部署](edge_side_pkg/documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md) | 自启动 TF、建图响应、静态验收与回滚 |
| [Ground-Air AGV 重定位部署](edge_side_pkg/documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md) | 两阶段重定位、1 Hz TF 采样/缓存重发、工作区边界与回滚 |
| [接口总册](docs/EDGE_DEVICE_INTERFACES.md) | 消息格式、通道与协议约束 |
| [电池曲线校准](docs/BATTERY_CALIBRATION.md) | WheelTech 与 Scout 曲线、平滑、迁移和现场校准 |
| [需求分析](需求分析.md) | 需求基线、平台目标和交付门禁 |
| [开发笔记](docs/DEVELOPMENT_NOTES.md) | 实现约束与维护说明 |

## 开发与构建

    uv run python -m unittest discover -s tests -v

本地构建入口：

    powershell -File scripts/build_release.ps1 -Target all
    bash scripts/build_release.sh all

Windows 需要 Inno Setup 6；Linux 使用 Ubuntu 20.04 构建基线。
构建环境、Docker 命令和第三方组件来源详见 [发布指南](docs/RELEASING.md#本地构建)。

## v0.23.0

新增 Windows/Ubuntu 安装包、地面站便携包与独立端侧配套包；统一安装目录数据路径、
本地主题设置、冻结融合 worker 和随包 FFmpeg；重整发布与使用文档。

端侧协议和各 ROS 包独立版本保持不变。完整变更见 [CHANGELOG](CHANGELOG.md)。

---

CCS 使用 [Apache License 2.0](LICENSE)。发布包同时保留所含第三方组件的独立许可信息。
