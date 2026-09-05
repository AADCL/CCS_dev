# 端侧功能包

`edge_side_pkg` 是指控端维护的端侧 ROS 源码与设备部署资料目录。公共部署物包含七个 ROS 包；Ground-Air 另部署一个设备专属控制包。`deploy` 和 `documents` 始终保留在指控端。

## 固定部署包

| 目录 | ROS 包 | 版本 | 职责 |
| --- | --- | --- | --- |
| `EPGeneral_device_config` | `epgeneral_device_config` | 0.1.1 | 设备身份及六类公共运行配置 |
| `EPGeneral_map_stream` | `epgeneral_map_stream` | 0.13.2 | 遥控建图、预览和成果服务 |
| `epgeneral_mqtav` | `epgeneral_mqtav` | 0.4.1 | MQTT presence、heartbeat 和状态 |
| `EPGeneral_relocalization` | `epgeneral_relocalization` | 0.3.0 | 地图下载与重定位协调 |
| `EPGeneral_ground_air_control` | `epgeneral_ground_air_control` | 0.1.0 | Ground-Air 建图/重定位阶段独占与初始位姿适配 |
| `EPGeneral_task_control` | `epgeneral_task_control` | 0.4.4 | 任务接收、调度与执行协调 |
| `EPGeneral_udp_telemetry` | `epgeneral_udp_telemetry` | 0.3.1 | 分级 UDP 实时遥测 |
| `EPGeneral_video_srt` | `epgeneral_video_srt` | 0.1.1 | ROS 图像到 SRT Listener |

功能包内部不保存运行 YAML。统一配置位于 `EPGeneral_device_config/config/`：

- `device.yaml`
- `epgeneral_mqtav.yaml`
- `udp_telemetry.yaml`
- `video.yaml`
- `map_stream.yaml`
- `relocalization.yaml`
- `task_control.yaml`

## 指控端资料

- `deploy/<profile>/`：设备 profile、设备适配 launch、一键启动脚本和系统配置原件。部署时只选择性复制其中内容，不能把 `deploy` 目录整体放入 catkin `src`。
- `documents/`：部署指南、实际部署记录、回滚步骤和配置校验信息，不保存另一套运行配置。
- Ground-Air AGV 的手动启动、常驻 TF、guard 1/2 兼容和建图静态验收以 `documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md` 为准；该设备上电自启动保持禁用。
- Ground-Air AGV 的两阶段重定位、1 Hz TF 采样/缓存重发和工作区边界以 `documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md` 为准。

## 部署

在指控端准备临时发布目录，将所选 profile 的同名 YAML 覆盖到临时副本的 `EPGeneral_device_config/config/`，然后复制以下八个目录：

```bash
EDGE_SRC=/path/to/CCS_dev/edge_side_pkg
STAGING=$(mktemp -d)
mkdir -p "${STAGING}/src"
for package in \
  EPGeneral_device_config EPGeneral_map_stream epgeneral_mqtav \
  EPGeneral_relocalization EPGeneral_ground_air_control EPGeneral_task_control \
  EPGeneral_udp_telemetry EPGeneral_video_srt
do
  cp -a "${EDGE_SRC}/${package}" "${STAGING}/src/"
done

PROFILE=scout_mini
cp "${EDGE_SRC}/deploy/${PROFILE}/config/"*.yaml \
  "${STAGING}/src/EPGeneral_device_config/config/"
```

将 `${STAGING}/src/` 复制到端侧 catkin 工作空间后重新构建。设备专项启动脚本、launch 或 NTP 配置按对应 profile 的部署文档安装到工作空间约定位置，不保留 `deploy/<profile>` 目录层级。

地面站修改设备 ID 后，必须同步更新端侧 `device.yaml` 并重启 MQTT、UDP、建图、重定位、任务和视频节点。协议字段、端口与安全边界以根目录 `docs/EDGE_DEVICE_INTERFACES.md` 为准。
