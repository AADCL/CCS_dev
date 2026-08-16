# 修改日志

<!-- MQTAV_VERSION: 0.3.0 -->

当前版本：`v0.3.0`

## [0.3.0] - 2026-07-31

### Added

- Moved the package into the ground station `edge_side_pkg` deployment tree.
- Added `edge_device_config` as the shared source of device ID and IP.
- Added `--device-config-file` and the corresponding roslaunch argument.

### Changed

- Device identity is loaded from the shared `device.yaml`; MQTT and ROS settings remain in `config.yaml`.
- Package version changed from `0.2.2` to `0.3.0`.

### Removed

- Removed the duplicated device identity block from MQTAV's runtime configuration.

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 的记录方式，并使用语义化版本。

## [0.2.2] - 2026-07-31

### Added

- 增加控制台关键日志和每次心跳发送的 `INFO heartbeat_sent` 记录。

## [0.2.1] - 2026-07-31

### Fixed

- 忽略 `roslaunch` 注入的 `__name:=...` 和 `__log:=...` 私有重映射参数，避免节点参数解析失败。

## [0.2.0] - 2026-07-31

### Changed

- 调整为 ROS Melodic 与 Python 3.6.9 兼容运行时；构建仍须显式指定 `/usr/bin/python3`，避免 Melodic 默认 Python 2.7。
- 移除 Python 3.7+ 的语法与标准库依赖，配置、状态、MQTT、ROS 适配和测试均可由 Python 3.6 解析。

## [0.1.1] - 2026-07-31

### Fixed

- 强制 ROS Noetic 构建使用 Python 3.7+，防止工作空间复用 Python 2.7/Python 3.6 缓存导致节点导入失败。
- 将 ROS 节点和版本校验脚本的解释器固定为 `/usr/bin/python3`。

## [0.1.0] - 2026-07-31

### Added

- 新增 MAVROS 到 MQTT 的无人机机载健康信息上报节点。
- 新增 MQTT 连接、1 Hz 心跳、QoS 1 状态上报、在线 presence 和异常离线 Last Will。
- 新增 YAML 配置校验、可选任务状态映射、断线自动重连和耐久旋转日志。
- 新增 Ubuntu 20.04 ROS Noetic 部署文档、单元测试和版本一致性检查。
