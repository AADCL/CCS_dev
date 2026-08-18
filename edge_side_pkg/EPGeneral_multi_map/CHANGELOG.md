# Changelog

## 0.1.0 - 2026-08-18

### Added

- 独立 ROS1 Noetic/Python 3.8+ 包 `epgeneral_multi_map`。
- 严格联合 start/stop 指令、请求幂等和统一绝对时间状态机。
- 通用位姿消息字段路径、排序缓存、平移插值与最短弧四元数 SLERP。
- 可配置固定时间窗、迟到宽限、空片、截断片、停止尾片和错误尾片。
- 复用既有点云距离过滤、体素降采样、频率及均匀限点语义。
- 扩展 `cloud_chunk` 切片/位姿依据元数据、1400 字节动态分片和上传后原始引用释放。
- 输入超时、时钟回退、迟到停止和统一 standby 复位。
- roslaunch 入口、版本检查、Python 3.8 语法、localhost UDP 和当前地面站重组契约测试。

### Changed

- 目录、ROS 包、Python 模块、类、配置、launch 和入口统一使用 `multi` 正确拼写；协议、端口和功能行为不变。

### Not included

- 地面站代码改动、端侧地图融合、实时配准算法、新预处理/滤波、TF-only 位姿、磁盘 PCD/切片、UDP 重传。
- Ubuntu 20.04/ROS Noetic Catkin 与双机器人实机验收仍待现场完成。
