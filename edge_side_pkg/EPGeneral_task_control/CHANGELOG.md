# Changelog

<!-- epgeneral_task_control_VERSION: 0.1.0 -->

## [0.1.0] - 2026-08-13

### Added

- `ccs-task-control-v1` UDP 14563/14564 接收、ACK、心跳、状态和进度。
- zlib/CRC32/分片校验、幂等 request ID、修订约束和 XML 原子持久化。
- 带完整 ID 的接收/执行状态机，以及强类型 ROS command/feedback 适配接口。
- UTC 调度、适配器反馈超时、停止与重启安全清理。
