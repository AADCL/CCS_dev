# 配置话题、类型与服务填写清单

适用 CCS 0.24.0 当前源码，更新于 2026-09-12。先使用本清单对照设备接口，再查[完整参数参考](INTERFACE_REFERENCE.md)填写超时、路径和约束；从零安装见[部署指南](DEPLOYMENT_GUIDE.md)。下面数值来自六个 profile 的实际 YAML，表示当前配置要求，不表示每个话题都已完成实机验收。

## 填写方法

1. 在相应阶段由外部驱动/算法提供数据，用 rostopic type/info、rosmsg show、带超时的 echo 和 hz 核实绝对话题名、消息类、字段、header.frame_id、时间戳与频率。区分常驻硬件和按需算法：空闲时无算法话题可以是预期行为。
2. 可配置类型写成 package/Message，大小写精确，不带 .msg；动态字段用点分路径，例如 Odometry 的 pose.pose.position，PoseStamped 的 pose.position。null 表示未知，不填伪造值。
3. 表中“代码固定”表示只可通过 YAML 改名字，不能凭空增加 message_type 改掉订阅类型；不同设备消息需真实适配节点。话题名相同但 MD5 不同仍不能通信。
4. 时间用秒，位姿/外参平移用米，角速度用 rad/s，四元数按 x/y/z/w；不得只换 frame 名称冒充坐标变换。source 中的字段值及单位以真实驱动为准。
5. 修改实际运行 config/profile，重启受影响节点；新设备同步更新平台 ID/IP、正式 UDP 描述和设备外参。默认共享配置混有示例路径，不能直接整套上机。

## 七份 YAML 的责任

| 文件 | ROS 配置位置 | 谁提供/消费 | 填写注意 |
| --- | --- | --- | --- |
| device.yaml | 无 ROS 话题 | 所有 CCS 通信包读取 ID/IP | schema_version=1，device.id/device.ip 对齐平台 |
| epgeneral_mqtav.yaml | ros.connection/state/battery/mission | 外部或任务节点 → MQTT | topic/message_type/mapping；mission 的 String.data 是任务摘要 JSON |
| udp_telemetry.yaml | descriptors[].source | 外部 → 遥测 | pose/imu/text_status 为动态消息；availability/pointcloud_status 用 AnyMsg 到达时间 |
| video.yaml | image_topic/image_message_type | 相机 → SRT | 仅 Image 或 CompressedImage；参数 enabled 不控制 C++ 启停 |
| map_stream.yaml | ros.inputs/stream/frames；integrations | 原始探测、预览输入、外部保存接口 | backend 对应真正的外部工具；输入与预览不能混用 |
| relocalization.yaml | ros.*_topic、frames、stages | 定位器发布 initialpose，接收地图/健康/TF | enabled 实际控制本包；旧 localized 文件不等于实时定位 |
| task_control.yaml | ros.*_topic、adapter.* | 协调器 ↔ 适配器 ↔ 底盘/导航 | 内部消息/固定类型/服务类型均要匹配；状态文件和定位共用 |

## 通用类型和语义

- MQTT 的 ros.connection 可选；配置后以独立周期消息及 timeout_seconds 判断 connected。QRD_003 使用 /go2/state/low_state，3 秒超时；armed 仍从锁存 /go2/control/enabled 的 Bool.data 读取。不要把只在变化时发布的锁存 Bool 当周期心跳。QRD_002 当前未配置 connection，保留旧行为，不把 Robot3 修复写成已同步。
- BatteryState.percentage 是 0..1 比例，voltage 为 V、current 为 A；没有百分比时 mapping.percentage=null，不能把电压当百分比。设备状态中的 mode/fault_code 保留其驱动语义。
- UDP 的 source.message_type 只适用于实际解码的源。AnyMsg 仅说明近期有消息：收到 Bool(false) 仍可显示来源可用，不能据此判断定位成功或运动已使能。正式 descriptor 的 name/display_name/type/level 参与协议契约，source 则连接本地驱动；不得为改 IMU 来源私自更改显示名而破坏严格哈希。
- pgm_file 的 topic 是契约占位名称，无 ROS 发布者/订阅者；实际读取 state_file 和 map_root。map_root、重定位 storage、任务 active_map_state_file 必须对齐。
- 任务 command/feedback 为 epgeneral_task_control/TaskExecutionCommand 和 TaskExecutionFeedback；协调器发布 command/接收 feedback，适配器相反。status_topic 为锁存 std_msgs/String 摘要，可给 MQTT mission。
- 导航 action 基名通常 /move_base，类型 move_base_msgs/MoveBaseAction。对应 /goal、/result、/feedback 为 MoveBaseActionGoal/Result/Feedback；/status 为 actionlib_msgs/GoalStatusArray，/cancel 为 actionlib_msgs/GoalID。停车话题为 geometry_msgs/Twist，不应把它填成 action 基名。
- /tf、/tf_static 均为 tf2_msgs/TFMessage（后者锁存）。重定位需要 map <- odom；原生 Go2 还要求新鲜 /localization/ok=True，临时单位 TF 不足以证明定位。
- UDP launch 默认输出 /epgeneral_udp_telemetry/link/udp_tx（锁存 std_msgs/Bool）和 /epgeneral_udp_telemetry/diagnostics（diagnostic_msgs/DiagnosticArray）；根脚本可重命名，如原生 Go2 的 /qrd/QRD_003/link/udp_tx、/qrd/QRD_003/diagnostics。这是输出，不能作为驱动输入填写。
- 外部保存服务由 underlay 提供，本仓库的保存脚本动态调用，无 .srv 类型选择配置。上线前记录 rosservice type 与 rossrv show；无参数调用 {} 及 success 响应不自动说明它就是 Trigger。不得根据相似名称猜类型。

## 六套实际配置

以下每行给出完整配置键、当前值、类型和方向；未启用/未配置项不会凭表格产生节点。所有路径均相对于各 profile 的 config。文件接口、参数和 action 已明确标识，不作为普通话题。


### QRD_001 / go2_edu

[配置原件](../deploy/go2_edu/config/) · [部署记录](../deploy/records/QRD_001/DEPLOYMENT.md)

| 文件与完整键 | 当前接口名 | ROS 类型 / 接口类别 | 方向、字段与生效条件 |
| --- | --- | --- | --- |
| `epgeneral_mqtav.yaml: ros.state.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；字段 {"connected": null, "armed": null, "system_status": null, "mode": null} |
| `udp_telemetry.yaml: descriptors[global_pose].source.topic` | `/lio/odometry` | nav_msgs/Odometry | 接收；pose；字段 {"position": "pose.pose.position", "orientation": "pose.pose.orientation"} |
| `udp_telemetry.yaml: descriptors[imu].source.topic` | `/livox/imu` | sensor_msgs/Imu | 接收；imu；字段 {"orientation": "orientation", "angular_velocity": "angular_velocity", "linear_acceleration": "linear_acceleration"} |
| `udp_telemetry.yaml: descriptors[livox_pointcloud].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；pointcloud_status；到达超时 1.0s |
| `udp_telemetry.yaml: descriptors[livox_driver].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[fastlio2].source.topic` | `/lio/odometry` | AnyMsg；外部 nav_msgs/Odometry | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[pgm_mapping].source.topic` | `/ccs/relocalization/pgm_file` | 文件接口，无 ROS 类型 | 不订阅；state_file=/home/nvidia/.ros/ccs_edge_dev/state/relocalization.json；map_root=/home/nvidia/go2_mid360_nav/maps/ccs_download |
| `video.yaml: image_topic` | `/camera/color/image_raw` | sensor_msgs/Image（image_message_type） | 接收；输出 640×480@30；外部相机先就绪 |
| `map_stream.yaml: ros.inputs.lidar.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.inputs.imu.topic` | `/livox/imu` | sensor_msgs/Imu（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.stream.cloud.topic` | `/lio/cloud_registered_body` | sensor_msgs/PointCloud2（message_type） | 接收；建图运行时预览；frame=body_lio；coordinates=sensor |
| `map_stream.yaml: ros.stream.pose.topic` | `/lio/odometry` | nav_msgs/Odometry（message_type） | 接收；建图运行时预览；字段 pose.pose.position/pose.pose.orientation |
| `map_stream.yaml: integrations.map_accumulator.service` | `/go2_map_accumulator/save` | 外部 ROS 服务；现场核验 .srv | go2_accumulator 实际调用；其他 backend 为兼容占位，不据此要求启动 Go2 |
| `relocalization.yaml: ros.initial_pose_topic` | `/initialpose` | geometry_msgs/PoseWithCovarianceStamped（代码固定） | 发布初始位姿，定位器订阅；当前 enabled=false |
| `relocalization.yaml: ros.map_topic` | `/map_2d` | nav_msgs/OccupancyGrid（外部地图约定） | 地图话题就绪检查，内容另验；当前 enabled=false |
| `relocalization.yaml: ros.localization_health_topic` | `/localization/ok` | std_msgs/Bool（代码固定） | 接收；必须为新鲜真值；当前 enabled=false |
| `task_control.yaml: ros.command_topic` | `/epgeneral_task_control/execution_command` | epgeneral_task_control/TaskExecutionCommand（代码固定） | 协调器→适配器；根脚本未启动任务 |
| `task_control.yaml: ros.feedback_topic` | `/epgeneral_task_control/execution_feedback` | epgeneral_task_control/TaskExecutionFeedback（代码固定） | 适配器→协调器；根脚本未启动任务 |
| `task_control.yaml: ros.status_topic` | `/qrd/QRD_001/task_status` | std_msgs/String（代码固定） | 协调器发布，锁存摘要；根脚本未启动任务 |

建图坐标：map=`lio_odom`，preview=`odom`，body=`body_lio`，sensor=`body_lio`；最终 artifacts.frame=`lio_odom`。外参取本机标定，不能复制本表所属设备的标定用于其他设备。

### QRD_002 / go2_robot2

[配置原件](../deploy/go2_robot2/config/) · [部署记录](../deploy/records/QRD_002/DEPLOYMENT.md)

| 文件与完整键 | 当前接口名 | ROS 类型 / 接口类别 | 方向、字段与生效条件 |
| --- | --- | --- | --- |
| `epgeneral_mqtav.yaml: ros.state.topic` | `/go2/control/enabled` | std_msgs/Bool（message_type） | 接收；字段 {"connected": null, "armed": "data", "system_status": null, "mode": null} |
| `epgeneral_mqtav.yaml: ros.battery.topic` | `/go2/battery_state` | sensor_msgs/BatteryState（message_type） | 接收；字段 {"percentage": "percentage", "voltage": "voltage", "current": "current"} |
| `epgeneral_mqtav.yaml: ros.mission.topic` | `/qrd/QRD_002/task_status` | std_msgs/String（message_type） | 接收；字段 {"value": "data"} |
| `udp_telemetry.yaml: descriptors[global_pose].source.topic` | `/odom_nav` | nav_msgs/Odometry | 接收；pose；字段 {"position": "pose.pose.position", "orientation": "pose.pose.orientation"} |
| `udp_telemetry.yaml: descriptors[imu].source.topic` | `/go2/imu` | sensor_msgs/Imu | 接收；imu；字段 {"orientation": "orientation", "angular_velocity": "angular_velocity", "linear_acceleration": "linear_acceleration"} |
| `udp_telemetry.yaml: descriptors[livox_pointcloud].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；pointcloud_status；到达超时 1.0s |
| `udp_telemetry.yaml: descriptors[livox_driver].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[fastlio2].source.topic` | `/odom_nav` | AnyMsg；外部 nav_msgs/Odometry | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[localization].source.topic` | `/localization/ok` | AnyMsg；外部 std_msgs/Bool | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[chassis].source.topic` | `/go2/diagnostics` | AnyMsg；外部 diagnostic_msgs/DiagnosticArray | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[pgm_mapping].source.topic` | `/ccs/relocalization/pgm_file` | 文件接口，无 ROS 类型 | 不订阅；state_file=/home/unitree/ccs_edge_ws/run/state/relocalization.json；map_root=/home/unitree/ccs_edge_ws/maps/download |
| `video.yaml: image_topic` | `/camera/color/image_raw` | sensor_msgs/Image（image_message_type） | 接收；输出 640×480@30；外部相机先就绪 |
| `map_stream.yaml: ros.inputs.lidar.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.inputs.imu.topic` | `/livox/imu` | sensor_msgs/Imu（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.stream.cloud.topic` | `/lio/cloud_registered_body` | sensor_msgs/PointCloud2（message_type） | 接收；建图运行时预览；frame=body_lio；coordinates=sensor |
| `map_stream.yaml: ros.stream.pose.topic` | `/lio/odometry` | nav_msgs/Odometry（message_type） | 接收；建图运行时预览；字段 pose.pose.position/pose.pose.orientation |
| `map_stream.yaml: integrations.map_accumulator.service` | `/go2_map_accumulator/save_map` | 外部 ROS 服务；现场核验 .srv | go2_accumulator 实际调用；其他 backend 为兼容占位，不据此要求启动 Go2 |
| `relocalization.yaml: ros.initial_pose_topic` | `/initialpose` | geometry_msgs/PoseWithCovarianceStamped（代码固定） | 发布初始位姿，定位器订阅；按需定位 |
| `relocalization.yaml: ros.map_topic` | `/map_2d` | nav_msgs/OccupancyGrid（外部地图约定） | 地图话题就绪检查，内容另验；按需定位 |
| `relocalization.yaml: ros.localization_health_topic` | `/localization/ok` | std_msgs/Bool（代码固定） | 接收；必须为新鲜真值；按需定位 |
| `task_control.yaml: ros.command_topic` | `/epgeneral_task_control/execution_command` | epgeneral_task_control/TaskExecutionCommand（代码固定） | 协调器→适配器 |
| `task_control.yaml: ros.feedback_topic` | `/epgeneral_task_control/execution_feedback` | epgeneral_task_control/TaskExecutionFeedback（代码固定） | 适配器→协调器 |
| `task_control.yaml: ros.status_topic` | `/qrd/QRD_002/task_status` | std_msgs/String（代码固定） | 协调器发布，锁存摘要 |
| `task_control.yaml: adapter.navigation_action` | `/move_base` | move_base_msgs/MoveBaseAction（代码固定） | action 客户端→导航服务器 |
| `task_control.yaml: adapter.odom_topic` | `/odom_nav` | nav_msgs/Odometry（代码固定） | 接收 pose.pose.position/orientation |
| `task_control.yaml: adapter.zero_velocity_topic` | `/cmd_vel_nav` | geometry_msgs/Twist（代码固定） | 发布零速度，配置中的停车接口 |
| `task_control.yaml: adapter.localization_ok_topic` | `/localization/ok` | std_msgs/Bool（代码固定） | 接收 data，新鲜且为 true |
| `task_control.yaml: adapter.control_enabled_topic` | `/go2/control/enabled` | std_msgs/Bool（代码固定） | 接收 data；锁存控制状态 |
| `task_control.yaml: adapter.control_diagnostics_topic` | `/go2/diagnostics` | diagnostic_msgs/DiagnosticArray（代码固定） | 接收新鲜诊断；GO2 SDK bridge / motion_enabled |
| `task_control.yaml: adapter.navigation_reset_service` | `/go2_navigation_supervisor/reset` | std_srvs/Trigger（代码固定） | 调用，success 必须 true；不是 Empty |
| `task_control.yaml: adapter.control_enable_service` | `/go2_sdk_bridge_real/enable` | std_srvs/SetBool（代码固定） | 调用 data=true/false；再确认真实状态 |

建图坐标：map=`lio_odom`，preview=`odom`，body=`body_lio`，sensor=`body_lio`；最终 artifacts.frame=`odom`。外参取本机标定，不能复制本表所属设备的标定用于其他设备。

### QRD_003 / go2_robot3

[配置原件](../deploy/go2_robot3/config/) · [部署记录](../deploy/records/QRD_003/DEPLOYMENT.md)

| 文件与完整键 | 当前接口名 | ROS 类型 / 接口类别 | 方向、字段与生效条件 |
| --- | --- | --- | --- |
| `epgeneral_mqtav.yaml: ros.connection.topic` | `/go2/state/low_state` | go2_control/Go2LowState（message_type） | 接收；周期消息；超时 3.0s |
| `epgeneral_mqtav.yaml: ros.state.topic` | `/go2/control/enabled` | std_msgs/Bool（message_type） | 接收；字段 {"connected": null, "armed": "data", "system_status": null, "mode": null} |
| `epgeneral_mqtav.yaml: ros.battery.topic` | `/go2/battery_state` | sensor_msgs/BatteryState（message_type） | 接收；字段 {"percentage": "percentage", "voltage": "voltage", "current": "current"} |
| `epgeneral_mqtav.yaml: ros.mission.topic` | `/qrd/QRD_003/task_status` | std_msgs/String（message_type） | 接收；字段 {"value": "data"} |
| `udp_telemetry.yaml: descriptors[global_pose].source.topic` | `/odom_nav` | nav_msgs/Odometry | 接收；pose；字段 {"position": "pose.pose.position", "orientation": "pose.pose.orientation"} |
| `udp_telemetry.yaml: descriptors[imu].source.topic` | `/go2/imu` | sensor_msgs/Imu | 接收；imu；字段 {"orientation": "orientation", "angular_velocity": "angular_velocity", "linear_acceleration": "linear_acceleration"} |
| `udp_telemetry.yaml: descriptors[livox_pointcloud].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；pointcloud_status；到达超时 1.0s |
| `udp_telemetry.yaml: descriptors[livox_driver].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[fastlio2].source.topic` | `/odom_nav` | AnyMsg；外部 nav_msgs/Odometry | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[localization].source.topic` | `/localization/ok` | AnyMsg；外部 std_msgs/Bool | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[chassis].source.topic` | `/go2/diagnostics` | AnyMsg；外部 diagnostic_msgs/DiagnosticArray | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[pgm_mapping].source.topic` | `/ccs/relocalization/pgm_file` | 文件接口，无 ROS 类型 | 不订阅；state_file=/home/unitree/ccs_edge_ws/run/state/relocalization.json；map_root=/home/unitree/ccs_edge_ws/maps/download |
| `video.yaml: image_topic` | `/camera/color/image_raw` | sensor_msgs/Image（image_message_type） | 接收；输出 640×480@15；外部相机先就绪 |
| `map_stream.yaml: ros.inputs.lidar.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.inputs.imu.topic` | `/livox/imu` | sensor_msgs/Imu（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.stream.cloud.topic` | `/lio/cloud_registered_body` | sensor_msgs/PointCloud2（message_type） | 接收；建图运行时预览；frame=body_lio；coordinates=sensor |
| `map_stream.yaml: ros.stream.pose.topic` | `/lio/odometry` | nav_msgs/Odometry（message_type） | 接收；建图运行时预览；字段 pose.pose.position/pose.pose.orientation |
| `map_stream.yaml: integrations.map_accumulator.service` | `/go2_map_accumulator/save_map` | 外部 ROS 服务；现场核验 .srv | go2_accumulator 实际调用；其他 backend 为兼容占位，不据此要求启动 Go2 |
| `relocalization.yaml: ros.initial_pose_topic` | `/initialpose` | geometry_msgs/PoseWithCovarianceStamped（代码固定） | 发布初始位姿，定位器订阅；按需定位 |
| `relocalization.yaml: ros.map_topic` | `/map_2d` | nav_msgs/OccupancyGrid（外部地图约定） | 地图话题就绪检查，内容另验；按需定位 |
| `relocalization.yaml: ros.localization_health_topic` | `/localization/ok` | std_msgs/Bool（代码固定） | 接收；必须为新鲜真值；按需定位 |
| `task_control.yaml: ros.command_topic` | `/epgeneral_task_control/execution_command` | epgeneral_task_control/TaskExecutionCommand（代码固定） | 协调器→适配器 |
| `task_control.yaml: ros.feedback_topic` | `/epgeneral_task_control/execution_feedback` | epgeneral_task_control/TaskExecutionFeedback（代码固定） | 适配器→协调器 |
| `task_control.yaml: ros.status_topic` | `/qrd/QRD_003/task_status` | std_msgs/String（代码固定） | 协调器发布，锁存摘要 |
| `task_control.yaml: adapter.navigation_action` | `/move_base` | move_base_msgs/MoveBaseAction（代码固定） | action 客户端→导航服务器 |
| `task_control.yaml: adapter.odom_topic` | `/odom_nav` | nav_msgs/Odometry（代码固定） | 接收 pose.pose.position/orientation |
| `task_control.yaml: adapter.zero_velocity_topic` | `/cmd_vel_nav` | geometry_msgs/Twist（代码固定） | 发布零速度，配置中的停车接口 |
| `task_control.yaml: adapter.localization_ok_topic` | `/localization/ok` | std_msgs/Bool（代码固定） | 接收 data，新鲜且为 true |
| `task_control.yaml: adapter.control_enabled_topic` | `/go2/control/enabled` | std_msgs/Bool（代码固定） | 接收 data；锁存控制状态 |
| `task_control.yaml: adapter.control_diagnostics_topic` | `/go2/diagnostics` | diagnostic_msgs/DiagnosticArray（代码固定） | 接收新鲜诊断；GO2 SDK bridge / motion_enabled |
| `task_control.yaml: adapter.navigation_reset_service` | `/go2_navigation_supervisor/reset` | std_srvs/Trigger（代码固定） | 调用，success 必须 true；不是 Empty |
| `task_control.yaml: adapter.control_enable_service` | `/go2_sdk_bridge_real/enable` | std_srvs/SetBool（代码固定） | 调用 data=true/false；再确认真实状态 |

建图坐标：map=`lio_odom`，preview=`odom`，body=`body_lio`，sensor=`body_lio`；最终 artifacts.frame=`odom`。外参取本机标定，不能复制本表所属设备的标定用于其他设备。

### AGV_001 / ground_air_agv

[配置原件](../deploy/ground_air_agv/config/) · [部署记录](../deploy/records/AGV_001/DEPLOYMENT.md)

| 文件与完整键 | 当前接口名 | ROS 类型 / 接口类别 | 方向、字段与生效条件 |
| --- | --- | --- | --- |
| `epgeneral_mqtav.yaml: ros.state.topic` | `/mavros/state` | mavros_msgs/State（message_type） | 接收；字段 {"connected": "connected", "armed": "armed", "system_status": "system_status", "mode": "mode"} |
| `epgeneral_mqtav.yaml: ros.battery.topic` | `/mavros/battery` | sensor_msgs/BatteryState（message_type） | 接收；字段 {"percentage": "percentage", "voltage": "voltage", "current": "current"} |
| `udp_telemetry.yaml: descriptors[global_pose].source.topic` | `/mavros/local_position/pose` | geometry_msgs/PoseStamped | 接收；pose；字段 {"position": "pose.position", "orientation": "pose.orientation"} |
| `udp_telemetry.yaml: descriptors[vision_pose].source.topic` | `/Odometry` | nav_msgs/Odometry | 接收；pose；字段 {"position": "pose.pose.position", "orientation": "pose.pose.orientation"} |
| `udp_telemetry.yaml: descriptors[imu].source.topic` | `/mavros/imu/data` | sensor_msgs/Imu | 接收；imu；字段 {"orientation": "orientation", "angular_velocity": "angular_velocity", "linear_acceleration": "linear_acceleration"} |
| `udp_telemetry.yaml: descriptors[livox_pointcloud].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；pointcloud_status；到达超时 1.0s |
| `udp_telemetry.yaml: descriptors[livox_driver].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[fastlio2].source.topic` | `/Odometry` | AnyMsg；外部 nav_msgs/Odometry | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[pgm_mapping].source.topic` | `/ground_air/mapping/status` | AnyMsg；外部类型须现场核验 | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[octomap_mapping].source.topic` | `/octomap_binary` | AnyMsg；外部 octomap_msgs/Octomap | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[occupancy_grid_mapping].source.topic` | `/map` | AnyMsg；外部 nav_msgs/OccupancyGrid | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[mapping_mode].source.topic` | `/mapping_mode` | std_msgs/String | 接收；text_status；到达超时 3.0s；字段 {"value": "data"} |
| `video.yaml: image_topic` | `/a8_cam/image_raw` | sensor_msgs/Image（image_message_type） | 接收；输出 1280×720@30；外部相机先就绪 |
| `map_stream.yaml: ros.inputs.lidar.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；prepare 原始探测；frame=base_link |
| `map_stream.yaml: ros.inputs.imu.topic` | `/livox/imu` | sensor_msgs/Imu（message_type） | 接收；prepare 原始探测；frame=base_link |
| `map_stream.yaml: ros.stream.cloud.topic` | `/cloud_registered` | sensor_msgs/PointCloud2（message_type） | 接收；建图运行时预览；frame=camera_init；coordinates=map |
| `map_stream.yaml: ros.stream.pose.topic` | `/Odometry` | nav_msgs/Odometry（message_type） | 接收；建图运行时预览；字段 pose.pose.position/pose.pose.orientation |
| `map_stream.yaml: integrations.map_accumulator.service` | `/ground_air/mapping/save` | 外部 ROS 服务；现场核验 .srv | go2_accumulator 实际调用；其他 backend 为兼容占位，不据此要求启动 Go2 |
| `relocalization.yaml: ros.initial_pose_topic` | `/initialpose` | geometry_msgs/PoseWithCovarianceStamped（代码固定） | 发布初始位姿，定位器订阅；按需定位 |
| `relocalization.yaml: ros.map_topic` | `/map` | nav_msgs/OccupancyGrid（外部地图约定） | 地图话题就绪检查，内容另验；按需定位 |
| `task_control.yaml: ros.command_topic` | `/epgeneral_task_control/execution_command` | epgeneral_task_control/TaskExecutionCommand（代码固定） | 协调器→适配器 |
| `task_control.yaml: ros.feedback_topic` | `/epgeneral_task_control/execution_feedback` | epgeneral_task_control/TaskExecutionFeedback（代码固定） | 适配器→协调器 |
| `task_control.yaml: ros.status_topic` | `/epgeneral_task_control/task_status` | std_msgs/String（代码固定） | 协调器发布，锁存摘要 |
| `task_control.yaml: adapter.localization_param` | `/ground_air/localized` | ROS 参数 bool（代码固定） | 读取参数，不是话题 |
| `task_control.yaml: adapter.vehicle_status_topic` | `/ground_air/vehicle_status` | ground_air_msgs/VehicleStatus（代码固定） | 接收底盘真实状态 |
| `task_control.yaml: adapter.mission_status_topic` | `/ground_air/mission/status` | ground_air_msgs/MissionStatus（代码固定） | 接收任务状态/反馈 |
| `task_control.yaml: adapter.prepare_ground_service` | `/ground_air/prepare_ground` | std_srvs/Trigger（代码固定） | 调用并检查结果 |
| `task_control.yaml: adapter.mission_submit_service` | `/ground_air/mission/submit` | ground_air_msgs/SubmitMission（代码固定） | 调用并检查结果 |
| `task_control.yaml: adapter.mission_start_service` | `/ground_air/mission/start` | std_srvs/Trigger（代码固定） | 调用并检查结果 |
| `task_control.yaml: adapter.mission_cancel_service` | `/ground_air/mission/cancel` | std_srvs/Trigger（代码固定） | 调用并检查结果 |
| `task_control.yaml: adapter.emergency_stop_service` | `/ground_air/emergency_stop` | ground_air_msgs/SetEmergencyStop（代码固定） | 调用；保留持久锁存 |

建图坐标：map=`camera_init`，preview=`odom`，body=`body`，sensor=`body`；最终 artifacts.frame=`map`。外参取本机标定，不能复制本表所属设备的标定用于其他设备。

### UGV_001 / scout_mini

[配置原件](../deploy/scout_mini/config/) · [部署记录](../deploy/records/UGV_001/DEPLOYMENT.md)

| 文件与完整键 | 当前接口名 | ROS 类型 / 接口类别 | 方向、字段与生效条件 |
| --- | --- | --- | --- |
| `epgeneral_mqtav.yaml: ros.state.topic` | `/scout_status` | scout_msgs/ScoutStatus（message_type） | 接收；字段 {"connected": null, "armed": null, "system_status": "fault_code", "mode": "control_mode"} |
| `epgeneral_mqtav.yaml: ros.battery.topic` | `/BMS_status` | scout_msgs/ScoutBmsStatus（message_type） | 接收；字段 {"percentage": null, "voltage": "battery_voltage", "current": null} |
| `udp_telemetry.yaml: descriptors[vision_pose].source.topic` | `/scout/odom` | nav_msgs/Odometry | 接收；pose；字段 {"position": "pose.pose.position", "orientation": "pose.pose.orientation"} |
| `udp_telemetry.yaml: descriptors[imu].source.topic` | `/livox/imu` | sensor_msgs/Imu | 接收；imu；字段 {"orientation": "orientation", "angular_velocity": "angular_velocity", "linear_acceleration": "linear_acceleration"} |
| `udp_telemetry.yaml: descriptors[livox_pointcloud].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；pointcloud_status；到达超时 1.0s |
| `udp_telemetry.yaml: descriptors[livox_driver].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[fastlio2].source.topic` | `/Odometry` | AnyMsg；外部类型须现场核验 | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[pgm_mapping].source.topic` | `/ccs/relocalization/pgm_file` | 文件接口，无 ROS 类型 | 不订阅；state_file=/home/nvidia/.ros/ccs_edge_dev/state/relocalization.json；map_root=/home/nvidia/livox_fastlio/maps/ccs_download |
| `video.yaml: image_topic` | `/camera/color/image_raw` | sensor_msgs/Image（image_message_type） | 接收；输出 640×480@30；外部相机先就绪 |
| `map_stream.yaml: ros.inputs.lidar.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.inputs.imu.topic` | `/livox/imu` | sensor_msgs/Imu（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.stream.cloud.topic` | `/cloud_registered_body` | sensor_msgs/PointCloud2（message_type） | 接收；建图运行时预览；frame=body；coordinates=sensor |
| `map_stream.yaml: ros.stream.pose.topic` | `/fastlio_odom` | nav_msgs/Odometry（message_type） | 接收；建图运行时预览；字段 pose.pose.position/pose.pose.orientation |
| `map_stream.yaml: integrations.map_accumulator.service` | `/unused_scout_map_service` | 外部 ROS 服务；现场核验 .srv | go2_accumulator 实际调用；其他 backend 为兼容占位，不据此要求启动 Go2 |
| `relocalization.yaml: ros.initial_pose_topic` | `/initialpose` | geometry_msgs/PoseWithCovarianceStamped（代码固定） | 发布初始位姿，定位器订阅；按需定位 |
| `relocalization.yaml: ros.map_topic` | `/map_2d` | nav_msgs/OccupancyGrid（外部地图约定） | 地图话题就绪检查，内容另验；按需定位 |
| `task_control.yaml: ros.command_topic` | `/epgeneral_task_control/execution_command` | epgeneral_task_control/TaskExecutionCommand（代码固定） | 协调器→适配器 |
| `task_control.yaml: ros.feedback_topic` | `/epgeneral_task_control/execution_feedback` | epgeneral_task_control/TaskExecutionFeedback（代码固定） | 适配器→协调器 |
| `task_control.yaml: ros.status_topic` | `/epgeneral_task_control/task_status` | std_msgs/String（代码固定） | 协调器发布，锁存摘要 |
| `task_control.yaml: adapter.navigation_action` | `/move_base` | move_base_msgs/MoveBaseAction（代码固定） | action 客户端→导航服务器 |
| `task_control.yaml: adapter.odom_topic` | `/fastlio_odom` | nav_msgs/Odometry（代码固定） | 接收 pose.pose.position/orientation |
| `task_control.yaml: adapter.zero_velocity_topic` | `/cmd_vel` | geometry_msgs/Twist（代码固定） | 发布零速度，配置中的停车接口 |

建图坐标：map=`odom`，preview=`odom`，body=`base_link`，sensor=`body`；最终 artifacts.frame=`map`。外参取本机标定，不能复制本表所属设备的标定用于其他设备。

### UGV_003 / wheeltec_r550p

[配置原件](../deploy/wheeltec_r550p/config/) · [部署记录](../deploy/records/UGV_003/DEPLOYMENT.md)

| 文件与完整键 | 当前接口名 | ROS 类型 / 接口类别 | 方向、字段与生效条件 |
| --- | --- | --- | --- |
| `epgeneral_mqtav.yaml: ros.state.topic` | `/odom` | nav_msgs/Odometry（message_type） | 接收；字段 {"connected": null, "armed": null, "system_status": null, "mode": null} |
| `epgeneral_mqtav.yaml: ros.battery.topic` | `/PowerVoltage` | std_msgs/Float32（message_type） | 接收；字段 {"percentage": null, "voltage": "data", "current": null} |
| `udp_telemetry.yaml: descriptors[vision_pose].source.topic` | `/fastlio_odom` | nav_msgs/Odometry | 接收；pose；字段 {"position": "pose.pose.position", "orientation": "pose.pose.orientation"} |
| `udp_telemetry.yaml: descriptors[imu].source.topic` | `/livox/imu` | sensor_msgs/Imu | 接收；imu；字段 {"orientation": "orientation", "angular_velocity": "angular_velocity", "linear_acceleration": "linear_acceleration"} |
| `udp_telemetry.yaml: descriptors[livox_pointcloud].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；pointcloud_status；到达超时 1.0s |
| `udp_telemetry.yaml: descriptors[livox_driver].source.topic` | `/livox/lidar` | AnyMsg；外部 livox_ros_driver2/CustomMsg | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[fastlio2].source.topic` | `/Odometry` | AnyMsg；外部类型须现场核验 | 接收；availability；到达超时 3.0s |
| `udp_telemetry.yaml: descriptors[pgm_mapping].source.topic` | `/ccs/relocalization/pgm_file` | 文件接口，无 ROS 类型 | 不订阅；state_file=/home/nrc19/.ros/ccs_edge_dev_wheeltec_r550p/state/relocalization.json；map_root=/home/nrc19/livox_fastlio/maps/ccs_download |
| `video.yaml: image_topic` | `/camera/image_raw` | sensor_msgs/Image（image_message_type） | 接收；输出 640×480@30；根脚本不启动视频 |
| `map_stream.yaml: ros.inputs.lidar.topic` | `/livox/lidar` | livox_ros_driver2/CustomMsg（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.inputs.imu.topic` | `/livox/imu` | sensor_msgs/Imu（message_type） | 接收；prepare 原始探测；frame=livox_frame |
| `map_stream.yaml: ros.stream.cloud.topic` | `/cloud_registered_body` | sensor_msgs/PointCloud2（message_type） | 接收；建图运行时预览；frame=body；coordinates=sensor |
| `map_stream.yaml: ros.stream.pose.topic` | `/fastlio_odom` | nav_msgs/Odometry（message_type） | 接收；建图运行时预览；字段 pose.pose.position/pose.pose.orientation |
| `map_stream.yaml: integrations.map_accumulator.service` | `/unused_wheeltec_map_service` | 外部 ROS 服务；现场核验 .srv | go2_accumulator 实际调用；其他 backend 为兼容占位，不据此要求启动 Go2 |
| `relocalization.yaml: ros.initial_pose_topic` | `/initialpose` | geometry_msgs/PoseWithCovarianceStamped（代码固定） | 发布初始位姿，定位器订阅；按需定位 |
| `relocalization.yaml: ros.map_topic` | `/map_2d` | nav_msgs/OccupancyGrid（外部地图约定） | 地图话题就绪检查，内容另验；按需定位 |
| `task_control.yaml: ros.command_topic` | `/epgeneral_task_control/execution_command` | epgeneral_task_control/TaskExecutionCommand（代码固定） | 协调器→适配器 |
| `task_control.yaml: ros.feedback_topic` | `/epgeneral_task_control/execution_feedback` | epgeneral_task_control/TaskExecutionFeedback（代码固定） | 适配器→协调器 |
| `task_control.yaml: ros.status_topic` | `/epgeneral_task_control/task_status` | std_msgs/String（代码固定） | 协调器发布，锁存摘要 |
| `task_control.yaml: adapter.navigation_action` | `/move_base` | move_base_msgs/MoveBaseAction（代码固定） | action 客户端→导航服务器 |
| `task_control.yaml: adapter.odom_topic` | `/fastlio_odom` | nav_msgs/Odometry（代码固定） | 接收 pose.pose.position/orientation |
| `task_control.yaml: adapter.zero_velocity_topic` | `/cmd_vel` | geometry_msgs/Twist（代码固定） | 发布零速度，配置中的停车接口 |

建图坐标：map=`odom`，preview=`odom`，body=`base_link`，sensor=`body`；最终 artifacts.frame=`map`。外参取本机标定，不能复制本表所属设备的标定用于其他设备。

## 配置外的固定接口与验收

| 适用 | 接口 | 类型 | 用途 |
| --- | --- | --- | --- |
| 原生 Go2 | /epgeneral_navigation_task_adapter/reset_emergency_stop | std_srvs/Trigger | 人工清锁，不使能；无执行/准备/控制过渡且有新鲜 disabled 证据才允许 |
| Ground-Air | /ground_air/localization/pose | geometry_msgs/PoseStamped | 任务适配器默认读取，可选 adapter.local_pose_topic 覆盖 |
| Ground-Air | /ground_air/system/stage | std_msgs/UInt8，锁存 | 0基础/1建图/2定位，状态不是服务 |
| Ground-Air | /ground_air/system/stage_detail | std_msgs/String，锁存 | 阶段诊断 |
| Ground-Air | /ground_air/system/set_stage | ground_air_msgs/SetSystemStage | caller/map_id 归属和 guard 契约 |
| Ground-Air | /ground_air/load_map | ground_air_msgs/LoadMap | 定位地图加载 |
| Ground-Air | /ground_air/relocalize | ground_air_msgs/Relocalize | initialpose 适配，use_initial_guess=true |

以下命令以已 source 同一 master 的 GO2_3 为例。只读查询不启动节点；带超时避免按需节点未运行时一直等待。类型/类可在离线核验，数据只能在已授权阶段采样。

~~~bash
rostopic type /go2/state/low_state
rosmsg show go2_control/Go2LowState
timeout 5 rostopic echo -n 1 /go2/control/enabled
timeout 5 rostopic echo -n 1 /go2/diagnostics
rostopic type /camera/color/image_raw
timeout 10 rostopic hz -w 30 /camera/color/image_raw
rosservice type /go2_sdk_bridge_real/enable
rossrv show std_srvs/SetBool
rossrv md5 std_srvs/Trigger
~~~

验收记录至少包含：profile 与配置键、实际 topic/service、类型/MD5、消息字段、frame、频率/新鲜度、发布者、检查时的生命周期阶段、命令及结论。相机 GO2_3 独立 readiness 还要求两帧递增且年龄≤3秒；只看到节点或一次 echo 不足以判 ready。SRT 参数为 UDP9000、120ms、2500kbps，帧率15；USB2.1/Right MIPI 历史告警不自动算已修复。

急停复位步骤见[使用手册](USER_MANUAL.md#go2-人工急停复位)。禁止通过接口联调命令意外使能、发布非零速度、运动目标或清除安全文件。
