# CCS 端侧功能包

配套产品 **CCS 0.23.1**。本目录维护 ROS1 设备侧通信、建图、重定位和任务协调源码，以及四套设备部署资料。运行基线为 Ubuntu 20.04、ROS Noetic、Python 3；视频节点使用 C++、OpenCV 和 GStreamer。ROS2 不属于当前可运行交付。

**文档入口：** [完整使用手册](documents/USER_MANUAL.md) · [设备内接口与配置参考](documents/INTERFACE_REFERENCE.md) · [地面站通信协议](../docs/EDGE_DEVICE_INTERFACES.md)

## 功能包与边界

| 目录 / ROS 包名 | 独立版本 | 职责 |
| --- | --- | --- |
| [EPGeneral_device_config](EPGeneral_device_config/README.md) / `epgeneral_device_config` | 0.1.1 | 设备身份及七份共享 YAML；无常驻节点 |
| [epgeneral_mqtav](epgeneral_mqtav/README.md) / `epgeneral_mqtav` | 0.4.1 | MQTT presence、heartbeat、摘要状态 |
| [EPGeneral_udp_telemetry](EPGeneral_udp_telemetry/README.md) / `epgeneral_udp_telemetry` | 0.3.1 | 20/5/1 Hz 遥测、数据来源诊断 |
| [EPGeneral_video_srt](EPGeneral_video_srt/README.md) / `epgeneral_video_srt` | 0.1.1 | ROS raw/compressed 图像编码为 SRT Listener |
| [EPGeneral_map_stream](EPGeneral_map_stream/README.md) / `epgeneral_map_stream` | 0.13.2 | 遥控建图、联合会话、点云预览及成果下载 |
| [EPGeneral_relocalization](EPGeneral_relocalization/README.md) / `epgeneral_relocalization` | 0.3.0 | 地图下载、定位栈协调、初始位姿及 TF 结果 |
| [EPGeneral_task_control](EPGeneral_task_control/README.md) / `epgeneral_task_control` | 0.4.4 | 任务接收、UTC 调度、导航适配与反馈 |
| [EPGeneral_ground_air_control](EPGeneral_ground_air_control/README.md) / `epgeneral_ground_air_control` | 0.1.0 | Ground-Air 阶段互斥、地图加载和初始位姿适配 |

产品版本与 ROS 包版本独立。本次更新文档与发行方式，不改变网络协议或包的运行接口。

## 设备能力

| profile | 工作空间 | 建图后端 | 重定位 | 一键脚本中的任务 / 视频 |
| --- | --- | --- | --- | --- |
| `go2_edu` | `/home/nvidia/ccs_edge_ws` | `go2_accumulator` | 配置禁用 | 任务不启动；视频启动 |
| `scout_mini` | `/home/nvidia/ccs_edge_ws` | `scout_finalize` | Scout 定位栈 | 导航适配器；D435i 视频 |
| `wheeltec_r550p` | `/home/nrc19/ccs_edge_ws` | `managed_finalize` | Wheeltec 定位栈 | 导航适配器；无相机，不启动视频 |
| `ground_air_agv` | `/home/bitcq/ccs_edge_ws` | `ground_air_service` | 阶段管理及连续 TF 回报 | 任务不启动；视频允许降级 |

“目录包含功能包”不代表设备已经具备对应驱动或算法。外部工作空间必须提供 profile 约定的消息、话题、服务及 launch。Ground-Air 上电自启动保持禁用，仅按部署指南手动启动用户服务。

## 目录与配置

- 八个包目录是可构建源码；发布 ZIP 包含全部八包。常规设备选择七个公共包，Ground-Air 才增加专用控制包及外部 `ground_air_msgs` 依赖。
- `deploy/<profile>/` 保存设备配置原件、启动脚本和适配 launch。按指南选择性安装，不把整个 `deploy` 放进 catkin `src`。
- `documents/` 保存本手册、接口参考、专项指南和历史验收记录，不是运行配置目录。
- `EPGeneral_device_config/config/` 保存 `device.yaml`、`epgeneral_mqtav.yaml`、`udp_telemetry.yaml`、`video.yaml`、`map_stream.yaml`、`relocalization.yaml`、`task_control.yaml`。

有两种配置入口，不能混用：单包 launch 默认读取上述包内目录；四套设备一键脚本通过参数显式读取 `<工作空间>/config/<profile>/`。修改前先确认实际启动命令。默认 YAML 是结构示例，混合了不同设备路径，不能直接作为完整设备 profile 使用。

## 最短部署路径

1. 阅读[使用手册](documents/USER_MANUAL.md)，选择设备 profile 并核对 underlay 依赖。
2. 在指控端准备 staging，只选公共七包；Ground-Air 增加第八包。将选定 profile YAML 放入 staging 的共享配置包。
3. 将源码安装到设备 CCS 工作空间并构建；按设备指南另行安装运行配置、脚本、launch 和授时配置。
4. 对齐设备 ID/IP、地面站地址、ROS 数据源、地图状态路径和 TF；执行配置检查后启动。
5. 验证 ROS 输入、端口、日志及地面站接收结果。配置修改后重启对应节点，当前不支持热重载。

具体命令及单包操作见手册，参数定义见接口参考。不要并行运行一键栈和重复的单包节点。

## 设备专项指南

- [Go2 EDU](deploy/go2_edu/DEPLOYMENT.md)
- [Scout Mini](documents/SCOUT_MINI_DEPLOYMENT.md)
- [Wheeltec R550P](documents/WHEELTEC_R550P_DEPLOYMENT.md)
- [Ground-Air 基础部署](documents/GROUND_AIR_AGV_DEPLOYMENT.md)
- [Ground-Air 建图](documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md)
- [Ground-Air 重定位](documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md)

带 `DEPLOYMENT_LOG` 的文件记录当时设备上的事实，不代表本次发行已重新完成实机验收。接口及操作文档以随版本源码为准。
