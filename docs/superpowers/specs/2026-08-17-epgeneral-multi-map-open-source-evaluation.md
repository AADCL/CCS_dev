# EPGeneral_multi_map 开源方案检索与筛选

_SOP Step 4 输出，检索日期：2026-08-17_

## 1. 检索目标与边界

目标是为机器人端联合建图采集包寻找可借鉴的成熟实现，重点覆盖：

- ROS1 带时间戳消息的缓存和同步；
- 点云时刻前后位姿查找与插值；
- 基于事件时间的固定窗口和迟到数据处理；
- 有界缓存、拒绝外推和确定性行为。

以下内容不在候选范围内：

- 地面站跨机器人配准和地图融合；
- SLAM 前端、回环检测和图优化；
- GPU、Open3D、PCL Python 绑定或 ROS2 运行时；
- 新的点云滤波、运动补偿或逐点去畸变算法。

因此不选择 LIO-SAM、FAST-LIO、Cartographer 等完整 SLAM 项目作为实现依赖。它们的核心职责和依赖规模超出已确认的软件边界。

## 2. 候选一：ROS message_filters

| 项目 | 结论 |
| --- | --- |
| 项目名称 | ROS message_filters |
| 项目地址 | [ros/ros_comm noetic-devel](https://github.com/ros/ros_comm/tree/noetic-devel/utilities/message_filters) |
| License | BSD |
| 主要语言 | Python、C++ |
| 平台 | ROS1 Noetic；目标平台 Ubuntu 20.04、Python 3.8、C++14 |
| 主要依赖 | catkin、rospy；C++ 部分依赖 roscpp、rosconsole、Boost thread |
| 输入 | 含 header.stamp 的 ROS 消息流 |
| 输出 | 精确/近似同步消息组，或按时间查询的缓存消息 |
| 核心算法 | TimeSynchronizer、ApproximateTimeSynchronizer、Cache、TimeSequencer |
| 自有 SLAM 前端 | 无 |
| GPU | 不需要 |
| 是否修改机器人端 | 若直接采用，需要在新包声明 message_filters 依赖 |
| 核心可抽取性 | 高，接口和缓存语义简单 |
| CCS_dev 兼容程度 | ROS 适配高；位姿插值能力不足 |
| 代码复杂度 | 低 |
| 维护状态 | ROS1 仓库已于 2025-05-31 归档 |

官方 Noetic Python API 提供 Cache.getElemBeforeTime、getElemAfterTime 和 getInterval，也提供基于消息时间戳的 ApproximateTimeSynchronizer。Cache 使用固定数量消息形成环形缓存，并明确不建议为无 header 消息自动分配当前时间。

局限：

- ApproximateTimeSynchronizer 选择已有消息组合，不生成目标时刻的新位姿；
- Python Cache 源码按到达顺序追加消息，不是面向任意乱序时间戳的完整插值缓存；
- 直接把 ROS 类型带入同步核心会降低纯 Python 单元测试的独立性。

结论：借鉴其 Subscriber/Cache 边界、带时间戳输入要求和有界队列语义；不直接使用 ApproximateTimeSynchronizer 代替位姿插值器，也不把 message_filters 设为首版新增运行时依赖。

## 3. 候选二：ROS tf2 TimeCache

| 项目 | 结论 |
| --- | --- |
| 项目名称 | ROS tf2 TimeCache |
| 项目地址 | [ros/geometry2 noetic-devel](https://github.com/ros/geometry2/tree/noetic-devel) |
| License | BSD |
| 主要语言 | C++ |
| 平台 | ROS1 Noetic；Ubuntu 20.04、C++14 |
| 主要依赖 | catkin、geometry_msgs、rostime、tf2_msgs、console_bridge |
| 输入 | 带时间戳的 TransformStamped |
| 输出 | 指定时刻的刚体变换或明确的外推失败 |
| 核心算法 | 排序缓存、前后样本查找、平移线性插值、四元数 SLERP |
| 自有 SLAM 前端 | 无 |
| GPU | 不需要 |
| 是否修改机器人端 | 直接采用会引入 TF 数据模型；本方案不直接采用 |
| 核心可抽取性 | 算法语义高，C++ 实现直接移植价值低 |
| CCS_dev 兼容程度 | 插值语义高；通用位姿消息和 Python 边界需自行实现 |
| 代码复杂度 | 中 |
| 维护状态 | ROS1 仓库已于 2025-05-31 归档 |

TimeCache 的 Noetic 实现具有本需求需要的关键语义：

- 按时间戳排序插入；
- 拒绝过旧或重复数据；
- 查找目标时刻前后的两个样本；
- 禁止无依据的前向或后向外推；
- 平移按时间比例线性插值；
- 旋转使用单位四元数 SLERP。

局限：

- 实现与 tf2 TransformStorage、坐标树和 C++ 类型绑定；
- 需求明确不增加纯 TF 查询模式；
- 新包位姿来源是 YAML 配置的任意 ROS 消息类型，不一定是 TransformStamped。

结论：把 TimeCache 作为位姿插值行为的主要参考，但在新包中以 ROS 无关的 Python 数据结构独立实现。不得把参考算法扩展为纯 TF 输入模式。

## 4. 候选三：Apache Beam 固定时间窗口

| 项目 | 结论 |
| --- | --- |
| 项目名称 | Apache Beam |
| 项目地址 | [Apache Beam](https://github.com/apache/beam) |
| License | Apache License 2.0，仓库另含部分第三方许可证 |
| 主要语言 | Java、Python、Go |
| 平台 | 非 ROS；跨平台分布式批流处理 |
| Python 版本 | 当前主线 setup.py 要求 Python 3.10 及以上 |
| 主要依赖 | grpcio、pyarrow、protobuf、requests、zstandard 等大量依赖 |
| 输入 | 有界或无界的带事件时间数据流 |
| 输出 | 按窗口和触发策略形成的处理结果 |
| 核心算法 | 固定窗口、事件时间、watermark、allowed lateness、trigger |
| 自有 SLAM 前端 | 无 |
| GPU | 不需要 |
| 是否修改机器人端 | 直接采用会显著扩大依赖和运行模型 |
| 核心可抽取性 | 概念高，代码直接抽取价值低 |
| CCS_dev 兼容程度 | 窗口语义高；运行时兼容性低 |
| 代码复杂度 | 很高 |
| 维护状态 | 活跃维护，2026-07 发布 2.75.0 |

Beam 将无限数据流按元素自身时间戳划分为互不重叠的固定窗口，并用 watermark 和 allowed lateness 控制窗口何时完成以及迟到数据何时丢弃。这与本项目“按点云事件时间归片、边界后等待 0.2 秒、超时迟到帧丢弃”的语义高度一致。

局限：

- 完整 Beam SDK 远大于端侧所需；
- 当前主线 Python 最低版本高于正式部署的 Python 3.8；
- 引入 runner、序列化和分布式数据模型会增加启动、内存和依赖风险；
- Beam 的 watermark 是通用流处理估计，本项目已有明确的机器人 wall time 和固定迟到容忍期，不需要通用 runner。

结论：只借鉴 event time、fixed window、allowed lateness 和窗口过期语义；不复制 Beam 实现，不增加 apache-beam 依赖。

## 5. 三方案比较

| 维度 | message_filters | tf2 TimeCache | Apache Beam |
| --- | --- | --- | --- |
| 与 ROS1 Noetic 直接兼容 | 高 | 高 | 低 |
| 通用位姿消息适配 | 中 | 低 | 不适用 |
| 前后位姿插值 | 不提供完整实现 | 高 | 不适用 |
| 绝对固定窗口 | 不提供 | 不提供 | 高 |
| 迟到数据语义 | 队列/slop | 过旧数据拒绝 | 高 |
| Python 3.8 运行时 | 可用 | C++ 库 | 当前主线不可用 |
| 新增依赖规模 | 低 | 中 | 很高 |
| 能否整体移植 | 不建议 | 不建议 | 禁止 |
| 推荐用途 | 缓存接口参考 | 插值行为参考 | 切片语义参考 |

## 6. 推荐方案

推荐采用“现有 CCS_dev 代码为主体、三项目分层借鉴、核心自行实现”的组合方案。

### 推荐原因

- 不改变 Step 3 已确认的软件边界；
- 不增加不必要的运行时依赖；
- 保持纯 Python 核心可在 Python 3.8 及以上测试；
- 位姿消息仍可通过 YAML 动态配置；
- 不引入纯 TF 模式、SLAM 前端或分布式流处理框架；
- 能明确对应每个外部参考的借鉴范围。

### 借鉴部分

- message_filters：带 header.stamp 输入约束、Subscriber/Cache 分层和有界缓存思路；
- tf2 TimeCache：排序插入、前后样本、拒绝外推、平移线性插值和四元数 SLERP；
- Apache Beam：事件时间固定窗口、窗口结束、允许迟到和过期丢弃语义；
- Shoemake 1985：单位四元数球面线性插值的数学依据；
- Dataflow Model 2015：事件时间、watermark、固定窗口和迟到数据的理论背景。

### 不采用部分

- 不直接使用 ApproximateTimeSynchronizer 代替插值；
- 不接入 tf2 Buffer 或新增 TF 查询模式；
- 不安装 Apache Beam；
- 不复制完整第三方状态机、runner 或坐标树；
- 不采用任何候选的 SLAM、配准、滤波或 GPU 能力。

### 需要自行实现部分

- ROS 无关、线程安全、按时间戳有序的 PoseBuffer；
- 位置线性插值与四元数最短弧 SLERP；
- 基于 start_at_ns 的确定性 slice_id 和绝对窗口计算；
- 0.2 秒可配置迟到容忍和过期帧统计；
- 完整片、停止尾片、错误尾片、空片和截断状态；
- 联合任务指令校验、会话状态机和既有 UDP payload 扩展；
- 端侧有界内存和发送后释放策略。

## 7. 风险结论

### License 风险

推荐方案只借鉴公开算法和接口语义，主要代码基于 CCS_dev 现有实现独立编写，因此许可证风险低。

如果后续直接复制 ROS BSD 源码，必须保留对应版权、许可证条件和免责声明。如果直接复制 Apache Beam 源码，还必须遵守 Apache-2.0 的 NOTICE、修改说明和第三方许可证要求。正式计划默认不复制第三方实现。

### 依赖风险

推荐方案不增加 message_filters、tf2 Buffer 或 Apache Beam 运行时依赖，只使用新包本来需要的 rospy、roslib、sensor_msgs、NumPy、PyYAML 和 MessagePack。依赖风险低。

### 接入风险

- ROS1 Noetic 已结束官方支持，相关 ROS1 仓库已归档；必须依靠固定版本、包内测试和目标机验收控制风险。
- 当前地面站尚不能发送联合扩展指令；新包首次只能通过模拟指令和 localhost 契约验证。
- 通用位姿消息的真实字段和频率仍需现场 YAML 配置及 Noetic 实机确认。
- NTP/PTP 时钟质量和双机器人真实时间对齐无法由单机测试替代。

## 8. Step 4 结论

没有候选项目适合整体移植。最小影响方案是：

1. 继续以 EPGeneral_map_stream 的现有自有实现作为源码复用基础；
2. 独立实现 ROS 无关的有序位姿缓冲和插值核心；
3. 采用 tf2 TimeCache 的插值行为作为主要参考；
4. 采用 Beam 的事件时间固定窗口和允许迟到语义；
5. 保持运行时依赖、端口、协议信封和地面站范围不变。

该结论满足功能相关、技术栈兼容、低侵入、依赖可控和借鉴范围明确的 Step 4 门禁。

## Sources

### Academic / Peer-reviewed

- [Shoemake, 1985 — Animating Rotation with Quaternion Curves](https://dl.acm.org/doi/10.1145/325165.325242)
- [Akidau et al., 2015 — The Dataflow Model](https://www.vldb.org/pvldb/vol8/p1792-Akidau.pdf)

### Official / Primary

- [ROS Noetic message_filters Python API](https://docs.ros.org/en/noetic/api/message_filters/html/python/index.html)
- [ros/ros_comm message_filters noetic-devel](https://github.com/ros/ros_comm/tree/noetic-devel/utilities/message_filters)
- [message_filters package.xml](https://raw.githubusercontent.com/ros/ros_comm/noetic-devel/utilities/message_filters/package.xml)
- [message_filters Python source](https://raw.githubusercontent.com/ros/ros_comm/noetic-devel/utilities/message_filters/src/message_filters/__init__.py)
- [ros/geometry2 noetic-devel](https://github.com/ros/geometry2/tree/noetic-devel)
- [tf2 package.xml](https://raw.githubusercontent.com/ros/geometry2/noetic-devel/tf2/package.xml)
- [tf2 TimeCache source](https://raw.githubusercontent.com/ros/geometry2/noetic-devel/tf2/src/cache.cpp)
- [ROS REP-3 target platforms](https://www.ros.org/reps/rep-0003.html)
- [ROS Noetic end-of-life notice](https://www.ros.org/blog/noetic-eol/)
- [Apache Beam repository](https://github.com/apache/beam)
- [Apache Beam model basics](https://beam.apache.org/documentation/basics/)
- [Apache Beam Python setup.py](https://raw.githubusercontent.com/apache/beam/master/sdks/python/setup.py)
- [Apache Beam license](https://raw.githubusercontent.com/apache/beam/master/LICENSE)
- [Apache Beam releases](https://github.com/apache/beam/releases)

