# 更新记录

## v0.3.0 - 2026-08-25

- 新增 `pgm_file` 状态源，读取活动地图 ID 并安全检查普通 `map.pgm`，回包携带 `map_id`。
- Scout 独立监测 `/Odometry` 作为 FAST_LIO2 状态，同时保留 `/scout/odom` 位姿；Go2 统一使用 prefixed odometry/IMU。
- profile 删除 octomap、occupancy-grid 和 mapping-mode 上传；`ccs-udp-telemetry-v1` 线格式保持不变。

## v0.2.2 - 2026-08-22

### 新增

- diagnostics 增加逐来源接收、有效、拒绝、样本年龄以及逐等级发送、失败、字节和序列统计。
- 启动时输出设备、session、目标、descriptor hash、分级频率和所有订阅映射，并限频汇总来源状态。

### 调整

- 保持 `ccs-udp-telemetry-v1`、UDP 14560、descriptor hash 和 ROS 话题配置兼容。

### 修复

- Pose/IMU 样本在平滑前拒绝 `NaN/Inf`、非数值字段和无效四元数。
- 单个 descriptor 映射、平均或快照异常时仅发送该项 `valid=false`，不再使整个 Level 1 报文失效。

## v0.2.1 - 2026-08-02

### 新增

- 新增源码入口无 catkin PYTHONPATH 的独立导入测试。
- 新增 catkin 包内 nose 测试注册和部署导入检查说明。

### 调整

- CMake 显式缓存 Python 3.6+ 解释器。

### 修复

- 修复 roslaunch 解析源码脚本时 `ModuleNotFoundError: epgeneral_udp_telemetry`。
- 修复 Melodic Python 解释器缓存可能导致模块未生成到正确 devel 路径的问题。

### 删除

- 无。

## v0.2.0 - 2026-08-02

### 新增

- 新增 PGM、八叉树和占据栅格地图生成状态描述。
- 新增 `text_status` 数据类型、ROS 字段映射和当前建图模式采集。
- 新增文本最近值复用、超时状态和对应单元测试。

### 调整

- 默认三级状态目录与地面站 v0.5.0 六类状态卡对齐。
- 增加 `std_msgs` 构建和运行依赖。

### 修复

- 修复状态数据只能表达可用性、无法显示当前模式文本的问题。

### 删除

- 删除默认 Ego-Planner 状态描述。

## v0.1.0 - 2026-08-01

### 新增

- 新增共享设备身份和独立遥测 YAML 加载。
- 新增 MessagePack UDP 信封、会话 ID、描述哈希和分级 sequence。
- 新增 Pose、IMU 动态 ROS 订阅与点分字段映射。
- 新增 20/5/1 Hz 分级发送、1 Hz 心跳、窗口均值、四元数平均和最近值复用。
- 新增点云接收元数据及 Livox、FAST-LIO2、Ego-Planner 话题新鲜度状态。
- 新增 launch、单元测试、部署与故障排查文档。

### 调整

- 无，首个版本。

### 修复

- 无，首个版本。

### 删除

- 无。
