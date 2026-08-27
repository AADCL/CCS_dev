# Changelog

<!-- epgeneral_task_control_VERSION: 0.3.1 -->

## [0.3.1] - 2026-08-26

- 执行前校验实时 `/fastlio_odom`、`map<-odom` TF 和导航地图文件。
- 为定位不可用、地图不匹配和导航启动超时返回明确错误码。

## [0.3.0] - 2026-08-26

- 增加 Scout Mini 导航执行适配器、map<-odom 位姿校验、顺序航点 actionlib 执行和安全停止。
- Scout 执行要求端侧重定位状态为 localized 且任务地图匹配当前地图。

## [0.2.0] - 2026-08-26

- 升级 `ccs-task-control-v2`，增加任务协商、读取、删除、终止和急停消息。
- 增加 `MissionStore` 和 `~/ccs_edge_ws/mission` 任务目录。

## [0.1.0] - 2026-08-13

### Added

- `ccs-task-control-v1` UDP 14563/14564 接收、ACK、心跳、状态和进度。
- zlib/CRC32/分片校验、幂等 request ID、修订约束和 XML 原子持久化。
- 带完整 ID 的接收/执行状态机，以及强类型 ROS command/feedback 适配接口。
- UTC 调度、适配器反馈超时、停止与重启安全清理。
