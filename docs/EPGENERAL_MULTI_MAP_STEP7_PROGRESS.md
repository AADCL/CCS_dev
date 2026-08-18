# EPGeneral_multi_map Step 7 / Task 1–10 Progress

**状态：** Task 1–10 已完成（代码与可执行自动化范围）  
**日期：** 2026-08-18  
**工作区：** `C:\Users\BM\Desktop\重点研发\CCS_dev-main`

## Task 1：包骨架与严格配置

- 创建独立目录 `edge_side_pkg/EPGeneral_multi_map`，ROS 包名 `epgeneral_multi_map`，版本 0.1.0。
- 配置 Noetic/Python 3.8+、共享 `device.yaml`、点云/通用位姿字段路径、frame、外参、统一时间窗、超时和资源上限。
- 配置专项 9 项通过；旧包未被运行时导入。

## Task 2：协议兼容扩展

- 复用 `ccs-map-stream-v1`、schema 1、UDP 14561/14562、MessagePack、zlib/CRC32、XYZ `<f4` 和 1400 字节上限。
- 严格 start 要求 `job_id/role/primary_device_id/participant_device_ids/start_at_ns/slice_duration_ns`；stop 要求 `job_id/stop_at_ns`。
- 当前地面站可重组含附加切片元数据的帧；生产下行字段仍需后续补齐。

## Task 3：位姿模型、缓存和插值

- 实现有界排序 `PoseBuffer`、重复拒绝、最近值、线性平移及归一化最短弧 SLERP。
- 没有新增 TF-only 模式或 tf2 依赖。

## Task 4：固定窗口和资源限制

- 以统一绝对开始时间计算确定性 slice ID/边界，支持可配置时长、0.2 秒默认迟到宽限、空片、尾片及帧/点/字节限制。
- 当前片截断不会污染下一片；最多保留两个待处理窗口。

## Task 5：复用既有预处理语义

- 保留旧包距离过滤、体素首点、频率和均匀限点行为；没有新增预处理或滤波算法。
- `PassThroughSliceProcessor` 为后续切片级扩展保留边界，首版为空操作。

## Task 6：联合开始和 armed 状态

- 实现来源 IP、设备、参与集合、命令时钟、开始提前量、ROS 话题/类型检查，请求幂等和 `standby→armed→mapping`。
- 错过统一开始时间会释放订阅/缓存并回到 standby。

## Task 7：回调、切片封闭和上传

- 位姿与点云按消息时间戳配准；点云时间戳决定频率限制和 slice ID。
- 上传完整/空/截断片、插值依据和窗口元数据；每个 UDP 数据报不超过 1400 字节。
- 上传后清空 SliceBatch 的原始 PointCloud2 引用；增加 1 Hz session heartbeat，避免 5 秒切片首片前链路误超时。

## Task 8：停止、错误尾片和复位

- 提前 stop 在 `stop_at_ns` 上传 `< stop_at_ns` 的最后不完整尾片；迟到 stop 上报 `STOP_TIME_MISSED`、边界和最大已上传时间。
- 点云与位姿分别执行输入超时；任一路断流触发错误尾片、error 状态、统一释放并回到 standby。
- 覆盖重复 stop、错 session、时钟回退和节点 close。

## Task 9：入口、版本和 UDP 契约

- 新增 roslaunch 入口、Catkin 脚本安装和版本一致性检查。
- localhost 使用一个真实 UDP 节点覆盖 start、armed、mapping、切片、stop、standby；该测试不是同机多机器人验收。
- 当前 `MapBuildingProtocol`/`CloudFrameAssembler` 可解码并重组新包扩展帧。

## Task 10：文档和完整回归

- 新增包 README/CHANGELOG 和 `docs/地面站兼容扩展说明.md`，同步项目 README、CHANGELOG、公共接口、开发说明、端侧计划和需求分析。
- 明确地面站 v0.13.1 既有 `_ActiveJob`、多 session、融合和地图保存不重做；当前只需后续补齐严格联合字段和切片汇总。
- 明确代码/自动化完成与 Noetic/双机器人实机未验证的边界。

## 最终自动化证据

| 检查 | 结果 |
| --- | --- |
| 地面站 `python -m unittest discover -s tests -v` | 147 PASS，2 SKIP（未安装 Open3D，与基线一致） |
| 地面站 `compileall` | PASS |
| 旧 `EPGeneral_map_stream` 当前 Python | 19 PASS |
| 新 `EPGeneral_multi_map` 当前 Python 3.11 | 69 PASS |
| 新包 Python 3.8.8 | 69 PASS |
| 新包 Python 3.8 AST / compileall | PASS |
| 版本、package.xml、launch XML、YAML | PASS |
| 新包 PCD/PGM/bag/数据库产物 | 0 |

不可修改项终检摘要：`ccs_monitor` 48 个非生成文件聚合 SHA-256 为 `2f4b43c4fcd7264c46c69bd3f80328e1d090ec88474f808ca1ce2df0d8cc9738`；`config/map_building.json` SHA-256 为 `6e068d14493fcc9811a115a0ddeea2e97b71b240c078d122b3235812d5ec2667`；旧 `EPGeneral_map_stream` 22 个非生成文件聚合 SHA-256 为 `cabaa2f1a7c6f22d91377bffcc18b9c7d3aa99123ef1e5bce993f89996083602`。本轮实现未编辑这些目标。

## 未完成的环境验收

- 当前 Windows 环境没有 WSL、Ubuntu 20.04、ROS1 Noetic 或 Catkin，因此没有执行 rosdep/catkin_make/roslaunch 实机检查。
- 尚未接入真实 PointCloud2 和现场位姿消息类型，尚未执行两台物理机器人、NTP、局域网丢包和最终地面站融合验收。
- 当前地面站 v0.13.1 生产代码尚未下发全部严格联合字段，不能直接启动新包；后续按兼容扩展说明处理。
- 工作区没有 `.git` 元数据；未 commit、未 push，SOP Step 11 仍需负责人批准且必须在正确 Git 仓库执行。

## 后续命名统一

- 目录、ROS package、Python module、节点类、配置、launch、入口脚本、测试和全部项目文档已统一使用 `multi` 正确拼写。
- 新增仓库级命名回归测试，扫描源文件内容和文件名，防止错误拼写再次出现。
- wire protocol、UDP 14561/14562、配置字段、版本 0.1.0 和运行逻辑均未改变。
