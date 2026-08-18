# 端侧功能包 Alpha 开发计划

## 1. 目标、平台和边界

端侧 Alpha 主线为 Ubuntu 20.04 + ROS1 Noetic + Python 3。现有 Ubuntu 18.04 + ROS Melodic 说明作为旧版维护信息保留，但不作为 Alpha 发布门禁。

Ubuntu 22.04 + ROS2 Humble 和 Ubuntu 24.04 + ROS2 Jazzy 在本阶段只完成协议核心复用、ROS 接口映射和迁移设计，不要求可运行的 ROS2 包。

所有端侧包必须：

- 从 `epgeneral_device_config` 读取唯一设备 ID/IP，不在代码中硬编码身份。
- 保持现有 MQTT、UDP、RTSP v1 wire contract 和端口。
- 通过配置校验、资源上限、来源校验、幂等 request ID、超时和安全清理处理异常。
- 在没有真实传感器或执行器时提供可替换的 ROS 适配边界，便于 localhost/模拟测试。
- 使用 systemd、roslaunch 或等价方式实现可重复启动、停止、日志和重启清理。

## 2. 公共基础包：`epgeneral_device_config`

**当前状态**：已提供 device ID/IP 共享配置。

**Alpha 任务**：

1. 扩展 schema，增加设备能力、可用通信包、传感器话题、默认 frame、静态外参和标定版本。
2. 保持旧 `schema_version: 1` 可读，并以原子替换迁移新字段。
3. 为所有包提供统一 Python/YAML 读取接口和错误信息。
4. 增加地面站 `devices.json` 与端侧 ID/IP/capability 的一致性检查脚本。

**验收**：缺失、重复、非法 IP、未知能力和 frame 配置在启动时明确失败；合法旧配置可迁移且不会丢失身份。

## 3. `epgeneral_mqtav` v0.3.0

**当前状态**：ROS1 MAVROS 状态/电池采集、MQTT presence/heartbeat/status、QoS 1、Last Will、日志和纯 Python 测试已实现；文档同时存在 Melodic/Noetic 基线冲突。

**Alpha 任务**：

1. 在 Ubuntu 20.04/ROS Noetic 上以 Python 3 重新构建并验证 `mavros_msgs/State`、`sensor_msgs/BatteryState`。
2. 验证可选任务状态字段映射、断线重连、retained offline 和异常退出行为。
3. 将部署说明、依赖包名、Python 版本和 launch 参数统一为 Noetic 主线。
4. 保持 MQTT JSON schema 1.0、topic root、QoS、heartbeat/status 频率和设备字段不变。
5. 验证耐久日志在网络断开、进程异常和重启后的可读性。

**验收**：参考设备每秒发布 heartbeat/status；地面站可区分 MQTT 在线、warning、offline/error；设备 IP 不一致只产生告警，不自动改写地面站配置。

## 4. `epgeneral_udp_telemetry` v0.2.1

**当前状态**：动态 ROS 字段、Pose/IMU 平滑、20/5/1 Hz、描述哈希和纯 Python 测试已实现；ROS 动态订阅尚未在 Noetic 目标设备验证。

**Alpha 任务**：

1. 在 Noetic 验证 `roslib.message.get_message_class`、`rospy.AnyMsg`、Pose/IMU/普通 ROS 字段路径。
2. 使用模拟发布器验证全局位姿、视觉位姿、IMU、点云状态、地图状态和 mapping mode。
3. 验证窗口均值、四元数半球归一化、低频最近值复用、数据年龄和 unknown 状态。
4. 验证 descriptor hash 与地面站配置一致；不一致时地面站拒收并给出可诊断错误。
5. 验证 UDP 14560 发送目标、session 重启、sequence 单调性和 16 KiB 上限。

**验收**：20/5/1 Hz 输出与配置一致；输入话题中断后状态变为 unavailable/unknown；端侧重启不会被地面站旧序列状态误判。

## 5. `epgeneral_map_stream` v0.1.0

**当前状态**：`ccs-map-stream-v1`、PointCloud2/位姿同步、zlib/CRC32、分片、资源限制和 session 状态机已通过纯 Python/localhost 测试；真实 ROS 雷达和里程计尚未验证。

**Alpha 任务**：

1. 在 Noetic 验证 PointCloud2、Odometry、header frame、位姿字段路径和静态 `body_from_sensor` 外参。
2. 为每台端侧设备提供独立 session；同一进程禁止错误 map/device/session 复用，重启后必须等待新的 start。
3. 严格执行最大点数、最大解压字节、点云距离、体素、5 Hz 上限和 1400 字节最终数据报限制。
4. 验证整帧 zlib、整帧 CRC32、little-endian XYZ float32、乱序/重复分片和错误来源 IP。
5. 将 start 指定的 return_host/return_port 作为上行目标，不写死地面站地址。
6. stop/error 时释放 ROS subscriber、位姿缓存、发送 socket 和 session 资源，但保留控制 socket 接受下一次 start。
7. 与地面站多 session 测试联调，输出 frame、点数、丢帧和 session 状态日志。

**验收**：参考设备能连续上传点云和同步位姿；话题超时发送 session error；缺片/CRC/非有限点不会产生半帧；重启和重复命令幂等。

## 6. `epgeneral_task_control` v0.1.0

**当前状态**：UDP 14563/14564、任务分片、CRC/zlib、XML 原子保存、UTC 调度、ACK/心跳/状态、强类型 ROS 消息和状态机已实现；真实运动控制适配器缺失。

**Alpha 任务**：

1. 在 Noetic 构建 `TaskExecutionCommand.msg` 和 `TaskExecutionFeedback.msg`，验证 ROS topic 和消息字段。
2. 为一台参考设备实现设备专属运动控制适配器：读取 XML、执行航点、回传 scheduled/running/terminal 状态、航点和位置。
3. `epgeneral_task_control` 只协调协议、持久化和安全状态，不直接解锁飞控、不直接调用 MAVROS。
4. 验证 prepare/chunk/commit 的 task/subtask/revision/device/frame/CRC/航点约束。
5. 验证 execute 的 UTC 容忍范围、NTP 偏差、同设备并发拒绝、cancel/stop 幂等和进程重启安全清理。
6. 适配器反馈超时、错误状态或节点退出时，发布 STOP/CANCEL 并向地面站发送 failed/错误码。
7. 记录 XML、request ID、revision、适配器反馈和最终状态，保证地面站 14564 进度是真值来源。

**验收**：参考设备可完成至少一个两航点任务；地面站收到 1 Hz heartbeat、状态变化和航点进度；用户停止后设备进入安全终态；适配器失联不会继续运动。

## 7. `epgeneral_usb_cam_rtsp` v0.2.0

**当前状态**：ROS USB camera + GStreamer RTSP Server C++ 节点、固定 8554 mount point 和配置文件已存在；真实 Noetic/GStreamer/摄像头未验收。

**Alpha 任务**：

1. 在 Noetic 构建 `cv_bridge`、`image_transport`、OpenCV 和 GStreamer RTSP Server 依赖。
2. 验证配置的 ROS 图像话题、消息类型、输出分辨率、帧率、H.264 编码和 `rtsp://<device.ip>:8554/usb_cam`。
3. 验证无相机、无帧、编码器缺失、RTSP 客户端断开和重复连接的日志和资源释放。
4. 与地面站详情页开关、手动重试、切页和退出行为联调。

**验收**：参考摄像头可稳定输出视频；地面站播放器能打开、显示失败状态并在关闭后释放网络/媒体资源。

## 8. ROS1/ROS2 兼容设计

### 8.1 代码分层

- `core`：协议编码、校验、分片、状态机、存储、时间和资源限制，不能导入 `rospy`。
- `ros1_adapter`：`rospy` subscriber/publisher、catkin、ROS1 `.msg` 和 roslaunch。
- `ros2_adapter`：后续使用 `rclpy`、ament_python、ROS2 IDL 和 launch.py。
- `device_adapter`：传感器、运动控制、相机和飞控的设备专属实现。

### 8.2 ROS2 迁移交付物

Alpha 阶段交付：

- ROS1 `.msg` 到 ROS2 IDL 的字段映射表。
- ROS1 launch/YAML 到 ROS2 launch.py/参数的映射表。
- `TaskExecutionCommand`/`TaskExecutionFeedback` 的 ID、revision、frame、UTC、状态、进度和位置兼容定义。
- Noetic/Humble/Jazzy 的依赖和 Python 版本风险清单。
- 不改变地面站 UDP/MQTT/RTSP wire contract 的迁移方案。

不在 Alpha 阶段声称 ROS2 包已实现或已通过硬件验证。

## 9. 公共部署与运维

1. 使用 `rosdep`、catkin 构建和 `source devel/setup.bash`，禁止混用 Python 2、Python 3.6 和 Noetic Python 环境。
2. 提供每包独立 launch 参数和统一的 device/config/log 目录。
3. 提供 systemd、tmux 或启动脚本示例，确保启动前 source ROS 和工作空间。
4. 使用 NTP 或 chrony 同步地面站和端侧 UTC；任务执行前检查时钟偏差。
5. 放行端侧 UDP 14561/14563、上行 14560/14562/14564、RTSP TCP 8554，以及地面站 TCP 1883、UDP 14560/14562/14564。
6. 使用 `ss`、`rostopic`、`tcpdump`、GStreamer 客户端和包内版本检查脚本提供现场诊断。
7. 进程退出、网络断开、ROS master 重启和配置错误时释放订阅、socket、临时缓存和运动控制资源。

## 10. 端侧测试矩阵

| 层级 | 环境 | 必测内容 |
| --- | --- | --- |
| 纯 Python | Windows/Linux | 配置、协议、状态机、存储、CRC/zlib、版本检查 |
| catkin | Ubuntu 20.04 + Noetic | 全包构建、依赖、Python 导入、launch |
| ROS 模拟 | Noetic | Pose/IMU/PointCloud2/Odometry/String、反馈适配器 |
| UDP localhost | Noetic/Windows 地面站 | 14560、14561/14562、14563/14564、ACK、超时、重启 |
| GStreamer | Noetic + 真实或虚拟摄像头 | RTSP 编码、拉流、断流和重连 |
| 参考设备 | Ubuntu20/Noetic | MQTT、遥测、视频、建图和任务控制全链路 |
| 故障注入 | 所有可用环境 | 错误 IP、缺片、CRC、话题超时、NTP 偏差、适配器失联 |

## 11. 完成定义

端侧包只有在“目标平台可构建、配置可校验、纯 Python/协议测试通过、ROS 话题或设备适配边界通过、错误能够安全清理、README 和版本记录同步”后，才能标记为 Alpha 完成。协议测试通过但真实 ROS/硬件未验证的包必须继续标记为“协议/自动化测试已完成”。
