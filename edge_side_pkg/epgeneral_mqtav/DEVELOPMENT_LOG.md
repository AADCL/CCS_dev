# epgeneral_mqtav 开发日志

<!-- epgeneral_mqtav_VERSION: 0.4.1 -->

当前版本：`v0.4.1`

## v0.4.1 - 2026-09-02

- 统一从 `epgeneral_device_config` 加载运行配置，并允许无电池源的设备禁用电池订阅。

## v0.3.1 - 2026-08-21

- `HealthState` 在构造时生成 UUID session，所有 MQTT 消息携带该会话值，sequence 继续使用线程安全的单调计数器。
- 指控平台按设备、消息类型和当前 session 校验顺序；端侧进程重启不会与上一启动周期的计数器冲突。

## v0.3.0 - 2026-07-31

- `epgeneral_mqtav` moved under `edge_side_pkg` and now depends on the `epgeneral_device_config` ROS package.
- `config.py` separates shared device identity loading from MQTT/ROS runtime configuration while preserving Python 3.6.9 compatibility.
- `node.py` adds `--device-config-file`; the launch default resolves the installed shared package through `rospkg`.
- The shared identity is validated independently against the ground station `config/devices.json` in the root test suite.
- The `epgeneral_mqtav` deployment archive is regenerated together with the shared configuration package.

## v0.2.2 - 2026-07-31

- 增加控制台日志处理器，使 ROS 启动时可以直接看到配置加载、ROS 订阅、MQTT 连接和发送结果。
- 每次心跳成功发送均输出 `INFO heartbeat_sent`，并附带主题、消息类型和序号；所有日志仍同步写入耐久日志文件。

## v0.2.1 - 2026-07-31

- 修复 `roslaunch` 自动追加 `__name:=...`、`__log:=...` 时被 argparse 拒绝、节点在启动前退出的问题。
- 仅过滤 ROS 私有重映射参数，仍对其他未识别的用户参数保留严格校验。

## v0.2.0 - 2026-07-31

- 调整为 ROS Melodic + Python 3.6.9 兼容实现，去除 `dataclasses`、延迟注解、联合类型和内置泛型等 Python 3.7+ 依赖。
- 配置数据对象改为普通 Python 类，保持 YAML 格式、ROS 订阅和 MQTT JSON 协议不变。
- CMake 改为要求 Python 3.6+，并补充 Melodic 工作空间以 `/usr/bin/python3` 清理重建的部署步骤。

## v0.1.1 - 2026-07-31

- 修复 catkin 使用 Python 2.7 或 Python 3.6 缓存时，导入 `from __future__ import annotations` 立即失败的问题。
- CMake 在配置阶段要求 Python 3.7+，并将 `PYTHON_EXECUTABLE` 固定为发现到的 Python 3 解释器；ROS 脚本固定使用 `/usr/bin/python3`。
- 补充清理旧 `build/`、`devel/` 产物后以 Python 3 重新编译的部署说明和自动化检查。

## v0.1.0 - 2026-07-31

- 创建独立的 ROS1 catkin Python 包，ROS 包、源码目录和 Python 模块统一命名为 `epgeneral_mqtav`。
- 将 ROS 回调、健康快照、MQTT 传输、配置校验和日志持久化拆分为独立模块，使 ROS 消息回调不执行网络发送。
- 默认映射 `mavros_msgs/State` 和 `sensor_msgs/BatteryState`；任务状态采用可选、可配置的 ROS 消息类型和字段路径。
- MQTT 使用 QoS 1、retained presence 和 Last Will；Paho 的重连延迟设为 1 至 60 秒，并在重连后立即发送最新心跳与状态。
- 使用旋转日志处理器，单条记录完成 `flush` 与 `fsync` 后才返回；该策略优先保证断联和异常信息的可读性，代价是额外磁盘 I/O。
- `package.xml` 是版本唯一来源；运行时读取 manifest，版本脚本校验 README、开发日志和修改日志的显式版本标记。
