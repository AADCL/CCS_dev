# 更新记录

## v0.3.0 - 2026-09-04

- 新增 `ground_air_agv` backend 和工作区级 launch 覆盖环境。
- Ground-Air 以固定 1 秒周期上报 `map <- odom`，首个有效样本立即成功；之后若设备静止、时间戳未更新或单周期查询失败，则重发最后有效样本。
- 地图 ZIP 继续严格校验 `public_map.pcd`；可按 profile 在原子安装前改名为 Ground-Air 定位器要求的 `cloud_map.pcd`。
- 同会话在等待位姿或已定位状态重复开始时复用仍在运行的栈，并取消旧 TF 监测后等待新的初始位姿。
- 连续结果首次立即持久化，之后最多每 30 秒及正常退出时刷新，Scout/Wheeltec 稳定窗口保持不变。

## v0.2.3 - 2026-09-02

- 将运行配置迁移至 `epgeneral_device_config`，重定位协议与状态机保持不变。
