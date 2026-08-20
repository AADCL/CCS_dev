# 更新记录

<!-- epgeneral_map_stream_VERSION: 0.2.0 -->

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
