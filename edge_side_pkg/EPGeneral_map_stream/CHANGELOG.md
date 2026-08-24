# 更新记录

<!-- epgeneral_map_stream_VERSION: 0.9.1 -->

## v0.9.1 - 2026-08-24

- `mapping_prerequisites.launch` 新增 `go2_map_accumulator/map_accumulator.launch`，确保停止建图调用 `/go2_map_accumulator/save` 前保存节点已运行。
- `start_fast_lio.sh` 将 `/go2_map_accumulator` 纳入启动就绪检查；启动失败或运行中退出时沿用受控进程组清理。

## v0.9.0 - 2026-08-24

- 配置升级为 schema 6，新增 `map_accumulator` setup、保存服务、60 秒超时和固定输出路径。
- 停止建图改为先调用 `/go2_map_accumulator/save`，在 FAST_LIO 仍运行时验证当前 session PCD 新鲜度，再停止进程组并继续 PGM/YAML 与 ZIP 流程。
- `start_fast_lio.sh` 不再接收或删除生成 PCD；保存或新鲜度失败时走 abort 清理并返回成果错误。
- 新增 `save_map.sh` 及服务调用、严格顺序、失败清理和旧文件保护测试。

## v0.8.1 - 2026-08-24

- 移除 `start_mapping` 启动阶段对 `odom <- lio_odom` 的零时间戳可用性探测和日志记录，坐标树短暂未合并不再导致启动失败。
- FAST_LIO 点云、里程计和坐标转换流程就绪后即可返回启动 ACK；TF 只随实际点云窗口按其时间戳查询、记录并用于坐标转换。
- 保持 schema 5、`ccs-map-stream-v2` 和地面站 v0.18.3 兼容。
- 已部署到 `192.168.50.100`，Noetic 重建、53 项端侧测试和版本一致性检查通过。

## v0.8.0 - 2026-08-24

- 配置升级为 schema 5，新增实时预览目标坐标系 `odom` 和 TF 查询超时。
- 端侧按点云窗口参考时间记录 `odom <- lio_odom`，将 PCD 坐标实际转换到 `odom` 后再回传，禁止只改 frame 标签。
- `cloud_fragment_ready` 增加 `source_frame_id` 和 `display_from_source`；FAST_LIO 最终成果继续使用 `lio_odom`，平台验收后切换为 `map`。
- 增加 `tf2_ros` 依赖、TF 可用性启动探测、变换日志和坐标变换回归测试。
- 2026-08-24 在 `192.168.50.100` 完成 Noetic 重建、53 项端侧测试及 MID360 真机 start/abort；按实际点云时间戳查询 `odom <- lio_odom` 成功，清理后无 PID 和节点残留。

## v0.7.2 - 2026-08-23

- 建图栈启动顺序调整为先启动 FAST_LIO 并等待 `/laserMapping`，再启动 TF、位姿和点云坐标转换节点。
- 启动等待上限覆盖 FAST_LIO 与坐标转换两个独立就绪阶段；进程组停止、强制结束和成果保存语义保持不变。

## v0.7.1 - 2026-08-23

- 将 ROS 集成预检从 1.5 秒传感器探测超时中拆分，Go2 端侧使用独立 8 秒上限。
- 压缩 `prepare_result` 中的失败摘要，完整异常保留在端侧日志，避免错误响应超过 1400 字节 UDP 上限。

## v0.7.0 - 2026-08-23

- 建图配置升级为 schema 4，增加坐标转换前置链、外参文件和就绪超时配置。
- 启动 FAST_LIO 前按顺序启动 TF manager、位姿适配器和两路点云坐标适配器，并等待四个 ROS 节点全部注册。
- `start_fast_lio.sh` 增加外参 YAML 严格校验和进程组 supervisor；任一必需组件退出时整组清理，正常停止与强制结束继续共用单一 PID。

## v0.6.0 - 2026-08-22

- 实时预览由 UDP 点云分片改为每秒生成不可变二进制 PCD，使用带令牌 HTTP 下载与 `cloud_fragment_ready/cloud_fragment_ack` 确认。
- ROS 点云窗口进入固定大小后台队列；未确认分片、描述符重发和磁盘文件均设硬上限并可清理。
- 记录 FAST_LIO 启动前源 PCD 指纹，停止后要求文件由当前 session 重新生成，禁止旧 PCD 进入成果 ZIP。
- Go2 MID360 使用包内 FAST_LIO launch 启用退出保存；启动前删除旧固定输出，PGM 前原子发布已验证 PCD，生成失败时恢复原公开地图。
- 2026-08-22 在 `QRD_001` 完成端侧构建与实测：连续 HTTP PCD 分片 ACK、当前 session PCD/PGM/YAML 和 manifest ZIP 均通过校验。

## v0.5.0 - 2026-08-22

- 缓存最近 3 个编码点云帧，响应 `request_cloud_chunks` 选择性补发缺失 UDP 分片。
- 新增 `abort_mapping`，无成果清理活动会话并停止 FAST_LIO，成果生成阶段保持不可中断。
- 补包和强制结束操作写入 ROS 日志及 `map_stream.log`。

## v0.4.1 - 2026-08-22

### 修复

- 修复 FAST_LIO 点云回调偶发早于同时间戳里程计回调时，点云被立即判定为 `no pose within synchronization tolerance` 的问题。
- 点云最多短暂缓存 3 帧等待原 50 ms 窗口内的对应位姿；真正超时或队列溢出时记录原因与 header 时间差。

## v0.4.0 - 2026-08-22

### 新增

- start 收到后立即回传 `session_status=starting`，FAST_LIO 数据就绪后再返回成功 ACK。
- 新增 `abort_fast_lio.sh` 无成果中止路径，并将命令、状态转换、话题探测、子进程输出、耗时和退出码写入 ROS 日志与 `map_stream.log`。

### 调整

- `prepare_mapping` 支持 `restart_active=true`；ready/starting/mapping/error 会话会注销订阅、清空缓存、停止 FAST_LIO、删除 PID 和临时成果后重新准备。
- stopping/generating/serving 阶段拒绝强制重启并返回当前 session、状态和不可中断原因；恢复后的 `prepare_result` 可带 `restarted`、`previous_state`、`active_session_id`。
- `/livox/imu` 仅要求正确消息类型和一条新数据；实时建图使用 `lio_odom`，最终成果使用 `map`。

### 修复

- 修复指控端 start 超时后，端侧残留 mapping 会话导致后续协商持续返回 BUSY 的问题。

## v0.3.1 - 2026-08-21

### 调整

- 适配 Go2 MID360 的 `go2_bringup/10_fastlio_mapping.launch` 和 `/lio/*` 话题。
- 支持将设备固定 PCD 快照到会话目录，并用固定配置生成、归档 PGM/YAML。

## v0.3.0 - 2026-08-21

### 新增

- 新增 Livox 点云/IMU 协商检查，以及 IMU 时间戳、frame 和有限值校验。
- 新增 FAST_LIO 启动/停止与 PGM 生成三个独立 Bash 包装器。
- 开始阶段等待 `/cloud_registered` 和 `/Odometry` 有效数据后才确认建图。

### 调整

- 配置升级为 schema 3，FAST_LIO 与 PGM 工作空间、包、launch、参数和超时均可配置。
- 停止流程调整为 FAST_LIO 保存 PCD、PGM 生成器输出 PGM/YAML、完整性校验与打包。
- 默认集成配置改为不可直接部署的明显占位值，prepare 会提前拒绝。

### 修复

- FAST_LIO map-frame 注册点云先转换回传感器坐标，再沿用平台实时预览坐标契约。

### 删除

- 无。

## v0.2.0 - 2026-08-20

### 新增

- 新增 `ccs-map-stream-v2` 准备协商、逐项能力检查和完整遥控建图状态机。
- 新增配置驱动的采样窗口、跨位姿点云重投影和实时预览分片。
- 新增无 shell start/stop 适配器、PCD/PGM/YAML 校验、manifest ZIP 与 SHA-256。
- 新增短期令牌 HTTP 服务、单段 Range 续传和成果过期清理。

### 调整

- 运行基线统一为 Ubuntu 20.04、ROS Noetic 和 Python 3，同时保持 Python 3.6 语法兼容。
- 停止 ACK 仅表示成果生成已经启动，最终地图不再取自实时预览点云。

### 修复

- 无。

### 删除

- 移除 v1 wire protocol，不提供自动回退。

## v0.1.0 - 2026-08-05

### 新增

- 新增 standby/start/mapping/stop/error 会话状态机和幂等命令 ACK。
- 新增 PointCloud2、可配置位姿字段、最近时间同步、距离过滤和体素降采样。
- 新增 `ccs-map-stream-v1` MessagePack、zlib、CRC32 和 1400 字节动态分片。
- 新增 1 Hz 会话心跳、输入超时、ROS 资源清理、单元测试和 Melodic 部署文档。

### 调整

- 无，首个版本。

### 修复

- 无，首个版本。

### 删除

- 无。
