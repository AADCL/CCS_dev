# Changelog

<!-- epgeneral_task_control_VERSION: 0.4.4 -->

## [0.4.4] - 2026-09-02

- 将运行配置迁移至 `epgeneral_device_config`，任务协议与执行状态机保持不变。

## [0.4.3] - 2026-08-27

- 在导航准备阶段校验任务点对应的 PGM 栅格，拒绝地图外、障碍区和未知区目标。
- 保留 `move_base` action 状态及文本，并区分规划失败、目标拒绝、抢占和其他 action 失败。

## [0.4.2] - 2026-08-27

- 创建并持有 `tf2_ros.TransformListener`，使 Scout 导航适配器实际订阅 `/tf` 和 `/tf_static`。
- 修复系统存在实时 `map<-odom` 变换但适配器私有 TF buffer 始终为空的问题。

## [0.4.1] - 2026-08-27

- 捕获 `map<-odom` TF 查询和位姿转换异常并反馈 `LOCALIZATION_UNAVAILABLE`。
- 防止导航准备失败以未捕获 ROS callback 异常结束，保证平台能够收到失败状态并释放执行等待。

## [0.4.0] - 2026-08-27

- 任务文件提交后异步启动并常驻 Scout 导航栈，导航就绪后才进入 `ready`。
- 执行、完成、失败和常规停止复用导航进程；删除、急停及关闭执行安全卸载。
- 增加内部 `PREPARE/UNLOAD` 命令、准备重试和导航进程退出监控。

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
