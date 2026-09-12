# CCS 端侧功能包

配套产品 **CCS 0.24.0**。本目录维护 ROS1 设备侧通信、建图、重定位和任务协调源码，以及六套设备部署资料。运行基线为 Ubuntu 20.04、ROS Noetic、Python 3；视频节点使用 C++、OpenCV 和 GStreamer。ROS2 不属于当前可运行交付。

**文档入口：** [从零部署指南](documents/DEPLOYMENT_GUIDE.md) · [配置话题与服务清单](documents/CONFIG_TOPIC_REFERENCE.md) · [按设备 ID 的部署记录](deploy/README.md) · [完整使用手册](documents/USER_MANUAL.md) · [其他工作空间接入与配置说明](documents/EXTERNAL_WORKSPACE_INTEGRATION.md) · [设备内接口与配置参考](documents/INTERFACE_REFERENCE.md) · [地面站通信协议](../docs/EDGE_DEVICE_INTERFACES.md)

## 功能包与边界

| 目录 / ROS 包名 | 独立版本 | 职责 |
| --- | --- | --- |
| [EPGeneral_device_config](EPGeneral_device_config/README.md) / `epgeneral_device_config` | 0.1.1 | 设备身份及七份共享 YAML；无常驻节点 |
| [epgeneral_mqtav](epgeneral_mqtav/README.md) / `epgeneral_mqtav` | 0.4.1 | MQTT presence、heartbeat、摘要状态 |
| [EPGeneral_udp_telemetry](EPGeneral_udp_telemetry/README.md) / `epgeneral_udp_telemetry` | 0.3.1 | 20/5/1 Hz 遥测、数据来源诊断 |
| [EPGeneral_video_srt](EPGeneral_video_srt/README.md) / `epgeneral_video_srt` | 0.1.1 | ROS raw/compressed 图像编码为 SRT Listener |
| [EPGeneral_map_stream](EPGeneral_map_stream/README.md) / `epgeneral_map_stream` | 0.13.2 | 遥控建图、联合会话、点云预览及成果下载 |
| [EPGeneral_relocalization](EPGeneral_relocalization/README.md) / `epgeneral_relocalization` | 0.4.0 | 地图下载、定位栈协调、初始位姿及 TF/健康结果 |
| [EPGeneral_task_control](EPGeneral_task_control/README.md) / `epgeneral_task_control` | 0.5.1 | 任务接收、UTC 调度、导航/原生任务适配与持久急停确认 |
| [EPGeneral_ground_air_control](EPGeneral_ground_air_control/README.md) / `epgeneral_ground_air_control` | 0.2.0 | Ground-Air 阶段互斥、地图加载、初始位姿及地面任务桥接 |
| [EPGeneral_go2_integration](EPGeneral_go2_integration/README.md) / `epgeneral_go2_integration` | 0.1.2 | GO2 建图/导航互斥、原生定位导航栈 launch 适配 |

产品版本与 ROS 包版本独立。设备按 profile 选择公共包及专用集成包。Go2 Robot2/Robot3 的专项适配不改变 UDP 网络协议；其他设备继续使用各自 profile 和默认行为。

## 设备能力

| profile | 工作空间 | 建图后端 | 重定位 | 一键脚本中的任务 / 视频 |
| --- | --- | --- | --- | --- |
| `go2_edu` | `/home/nvidia/ccs_edge_ws` | `go2_accumulator` | 配置禁用 | 任务不启动；视频启动 |
| `go2_robot2` | `/home/unitree/ccs_edge_ws` | `go2_accumulator` | 原生 Go2 定位栈及健康门控 | attach 导航适配器及急停确认；视频启动 |
| `go2_robot3` | `/home/unitree/ccs_edge_ws` | `go2_accumulator` | GO2 原生定位栈及健康门控 | attach 导航适配器与持久急停；D435i RGB 640×480/15 FPS |
| `scout_mini` | `/home/nvidia/ccs_edge_ws` | `scout_finalize` | Scout 定位栈 | 导航适配器；D435i 视频 |
| `wheeltec_r550p` | `/home/nrc19/ccs_edge_ws` | `managed_finalize` | Wheeltec 定位栈 | 导航适配器；无相机，不启动视频 |
| `ground_air_agv` | `/home/bitcq/ccs_edge_ws` | `ground_air_service` | 阶段管理及连续 TF 回报 | 地面任务与急停桥接；视频允许降级 |

“目录包含功能包”不代表设备已经具备对应驱动或算法。外部工作空间必须提供 profile 约定的消息、话题、服务及 launch。Ground-Air 上电自启动保持禁用，仅按部署指南手动启动用户服务。

## 目录与配置

- 九个包目录是可构建源码；发布 ZIP 包含全部九包。常规设备选择七个公共包；Ground-Air 增加专用控制包及外部 `ground_air_msgs` 依赖；Go2 Robot2/Robot3 增加 `EPGeneral_go2_integration`，不安装 Ground-Air 控制包，分别部署八包。
- `deploy/<profile>/` 保存设备配置原件、启动脚本和适配 launch。按指南选择性安装，不把整个 `deploy` 放进 catkin `src`。
- `documents/` 保存本手册、接口参考、专项指南和历史验收记录，不是运行配置目录。
- `EPGeneral_device_config/config/` 保存 `device.yaml`、`epgeneral_mqtav.yaml`、`udp_telemetry.yaml`、`video.yaml`、`map_stream.yaml`、`relocalization.yaml`、`task_control.yaml`。

有两种配置入口，不能混用：单包 launch 默认读取上述包内目录；六套设备一键脚本通过参数显式读取 `<工作空间>/config/<profile>/`。修改前先确认实际启动命令。默认 YAML 是结构示例，混合了不同设备路径，不能直接作为完整设备 profile 使用。

## 最短部署路径

1. 阅读[从零部署指南](documents/DEPLOYMENT_GUIDE.md)，完成设备盘点、独立身份/profile 和备份；用接口清单核对 underlay。
2. 在指控端准备 staging，选择公共七包；Ground-Air 或 Go2 Robot2/Robot3 增加各自专用第八包。将选定 profile YAML 放入 staging 的共享配置包。
3. 将源码安装到设备 CCS 工作空间并构建；按设备指南另行安装运行配置、脚本、launch 和授时配置。
4. 对齐设备 ID/IP、地面站地址、ROS 数据源、地图状态路径和 TF；执行配置检查后启动。
5. 验证 ROS 输入、端口、日志及地面站接收结果。配置修改后重启对应节点，当前不支持热重载。

具体命令及单包操作见手册，参数定义见接口参考。不要并行运行一键栈和重复的单包节点。

## 设备专项指南

- [GO2 部署与恢复经验](documents/GO2_DEPLOYMENT_LESSONS.md)：锁存溯源、幂等关闭、RPC 防护、限时联合传输和现场确认边界。
- [Go2 EDU](deploy/go2_edu/DEPLOYMENT.md)
- [Go2 Robot2 / QRD_002](deploy/go2_robot2/DEPLOYMENT.md)
- [Go2 Robot3 / QRD_003](deploy/go2_robot3/DEPLOYMENT.md)
- [Scout Mini](documents/SCOUT_MINI_DEPLOYMENT.md)
- [Wheeltec R550P](documents/WHEELTEC_R550P_DEPLOYMENT.md)
- [Ground-Air 基础部署](documents/GROUND_AIR_AGV_DEPLOYMENT.md)
- [Ground-Air 建图](documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md)
- [Ground-Air 重定位](documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md)

所有专项旧路径已跳转到 `deploy/records/<设备ID>/DEPLOYMENT.md`，原文按来源合并并保留 SHA-256，后续记录只追加到该文件。历史事实不代表当前源码已重新部署。2026-09-12 两台适配器退出修复已部署，用户后续确认落地测试完成；普通卸载后恢复准备的后续修订、QRD_003 其他存储/启动锁改动仍未有部署证据。接口及操作以实际源码身份和本次实测为准。
