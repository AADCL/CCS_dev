# CCS 端侧其他工作空间接入与配置说明

编写日期：2026-09-07。依据本项目 `edge_side_pkg` 中的共享配置、四套设备 profile、launch、消息定义及接口实现编写。当前交付使用 ROS1 Noetic / Ubuntu 20.04 / Python 3；视频节点为 C++。本文件是源码级接入说明，不代表已经对外部设备完成实机验收。

项目实际源码目录为 `edge_side_pkg`，本文所称“CCS 通用包”指其中的 `EPGeneral_*` 和 `epgeneral_mqtav`；设备运行工作空间通常名为 `ccs_edge_ws`。目录名与 ROS 包名不同，`roslaunch` 使用小写包名，如 `epgeneral_map_stream`。

## 1. 先明确其他工作空间需要交付什么

CCS 通用包负责通信、会话和任务协调，底盘驱动、传感器驱动、建图算法、定位算法及运动执行能力需要设备侧其他工作空间配合。下表的“必需”均指启用相应能力时必需，并非要求所有设备实现全部功能。

| 启用能力 | 外部工作空间应交付 | 对应配置 |
| --- | --- | --- |
| 在线状态和电池摘要 | 可用的状态消息源；需要电池显示时提供电池消息 | `epgeneral_mqtav.yaml` |
| 高频遥测和运行状态 | 所选 descriptor 的位姿、IMU、文本或持续有消息的话题；也可提供地图文件状态 | `udp_telemetry.yaml` |
| 视频 | 持续发布图像的相机驱动或视频转 ROS 节点 | `video.yaml` |
| 建图 | 雷达/IMU、点云/里程计、正确的 TF/外参、可受控启动的算法 launch、地图保存/转换工具 | `map_stream.yaml` |
| 重定位 | 可加载指定地图的定位 launch、初始位姿接收者、栅格地图和 `map <- odom` TF | `relocalization.yaml` |
| 导航任务 | move_base Action 服务端、里程计、TF、速度执行接口；或自行实现通用任务适配器 | `task_control.yaml` |
| Ground-Air 原生任务 | `ground_air_msgs`、原生任务和急停服务、车辆/任务状态、实时定位参数 | Ground-Air profile 与 `EPGeneral_ground_air_control` |

`EPGeneral_device_config` 只提供七份 YAML，没有常驻节点。普通设备按能力使用公共七包；Ground-Air 增加第八个专用控制包。

## 2. 配置文件在哪里改、怎样生效

### 2.1 选择实际运行副本

| 启动方式 | 实际配置来源 | 修改方法 |
| --- | --- | --- |
| 单包 launch 默认入口 | `$(rospack find epgeneral_device_config)/config/` | 修改该目录，或通过 launch 参数显式指定文件 |
| 设备一键脚本 | 通常为 `<CCS工作空间>/config/<profile>/` | 修改实际运行目录；可由 `CCS_EDGE_PROFILE_CONFIG_DIR` 指定 |
| 本仓库部署原件 | `edge_side_pkg/deploy/<profile>/config/` | 用作安装和版本管理原件；只改仓库原件不保证设备立即生效 |

共享模板位于 [EPGeneral_device_config/config](../EPGeneral_device_config/config/)。它混合了 MAVROS、Go2、Scout 的示例：设备 IP 为 `192.168.50.150`，MQTT 地面站为 `192.168.20.10`，部分 UDP 为 `192.168.151.100`，建图/重定位又为 `192.168.50.101`。必须选定实际设备 profile 并统一地址、路径和话题，不能原样整体部署。

各节点在启动时加载配置，当前没有热重载。除 launch 明确暴露的参数外，直接 `rosparam set` 不会修改正在运行的 YAML 驱动节点。修改后重启相关节点；设备身份变化后重启所有相关通信节点。

### 2.2 ROS 工作空间环境

外部消息包必须已经构建，且 CCS 进程能加载其 Python 消息模块。所有互相通信的节点连接同一个 ROS Master；`ROS_IP` 填本机可达地址。Linux 设备示例如下，路径需换成实际值：

```bash
source /opt/ros/noetic/setup.bash
source /home/robot/device_ws/devel/setup.bash --extend
source /home/robot/ccs_edge_ws/devel/setup.bash --extend
export ROS_MASTER_URI=http://192.168.50.120:11311
export ROS_IP=192.168.50.120
rospack find epgeneral_device_config
rospack find epgeneral_task_control
```

同机不同 catkin 工作空间也必须连接同一 Master。跨主机时还要允许 ROS XMLRPC/TCPROS 的动态端口通信，不能只开放 11311。设备运行使用真实时间；任务按 UTC 调度，不能让某个算法 launch 把整套设备意外切换到无 `/clock` 的仿真时间。

## 3. 外部功能包需要发布的 ROS 话题

本节列的是共享模板的原始配置值；实际 profile 差异见第 7 节。“外部 → CCS”指外部包发布、CCS 订阅或探测。可配置名字通常无需强制改外部代码，修改 CCS 对应键即可；但其他消费者和算法自身的输入配置仍须同步。

### 3.1 MQTT 状态摘要：`epgeneral_mqtav.yaml`

| 方向 | 模板话题 | 消息类型 | 配置键及字段映射 | 外部责任与条件 |
| --- | --- | --- | --- | --- |
| 外部 → CCS | `/mavros/state` | `mavros_msgs/State` | `ros.state.topic/message_type`；mapping 为 `connected/armed/system_status/mode` 同名字段 | 状态来源；无 MAVROS 的设备应替换为底盘状态或真实数据新鲜度来源 |
| 外部 → CCS | `/mavros/battery` | `sensor_msgs/BatteryState` | `ros.battery.topic/message_type`；mapping 为 `percentage/voltage/current` | 电池源默认启用；没有真实电池接口时设 `ros.battery.enabled: false` |
| 外部 → CCS | `/mission/status` | `std_msgs/String` | `ros.mission.topic/message_type/field_path: data` | 默认 `enabled: false`，仅启用任务摘要显示时需要 |

`mapping` 的值是消息字段路径，不是常量。对于只有里程计、没有 connected 字段的底盘，可用 `connected_on_message: true` 和 `timeout_seconds: 3.0` 按消息新鲜度判定在线，把不存在的字段显式设为 `null`。此时“在线”仅表示该数据源近期有消息，不代表飞控解锁或导航就绪。电池电压使用 V、电流使用 A；不支持的量应保留未知，不能用常量伪造。

### 3.2 UDP 遥测：`udp_telemetry.yaml`

| descriptor 名称 | 模板话题 | 消息类型/接收方式 | 取值路径或到达超时 |
| --- | --- | --- | --- |
| `global_pose` | `/mavros/local_position/pose` | `geometry_msgs/PoseStamped` | `position: pose.position`；`orientation: pose.orientation` |
| `vision_pose` | `/vision_pose/pose` | `geometry_msgs/PoseStamped` | 同上 |
| `imu` | `/mavros/imu/data` | `sensor_msgs/Imu` | `orientation`、`angular_velocity`、`linear_acceleration` |
| `livox_pointcloud` | `/livox/lidar` | `AnyMsg`，仅监测到达 | `pointcloud_status`，1 秒 |
| `livox_driver` | `/livox/lidar` | `AnyMsg`，仅监测到达 | `availability`，3 秒 |
| `fastlio2` | `/Odometry` | `AnyMsg`，仅监测到达 | `availability`，3 秒 |
| `pgm_mapping` | `/map_pgm` | `AnyMsg`，仅监测到达 | `availability`，3 秒 |
| `octomap_mapping` | `/octomap_binary` | `AnyMsg`，仅监测到达 | `availability`，3 秒 |
| `occupancy_grid_mapping` | `/map` | `AnyMsg`，仅监测到达 | `availability`，3 秒 |
| `mapping_mode` | `/mapping_mode` | `std_msgs/String` | `mapping: {value: data}`，3 秒 |

以上均为外部 → CCS。完整键为 `descriptors[].source.topic/message_type/mapping/timeout_seconds`。`AnyMsg` 是 Python 接收方式，不是外部应定义的一种 ROS 消息；这些监测项不强制指定载荷类型，也不证明算法结果正确。共享配置不能据此推导 `/map_pgm` 的固定消息类型。只发布一次的 latched 地图可能在到达超时后显示不可用，应选择与实际语义一致的持续状态源或 `pgm_file`。

`level: 1/2/3` 对应遥测分级发送，不是要求外部传感器分别按 20/5/1 Hz 发布。外部频率应满足算法需求及新鲜度窗口；不要用降低算法频率的方式匹配网络发送频率。修改来源只需调整 source；修改 `name/display_name/type/level` 会改变 descriptor hash，需同步地面站描述符。

Go2、Scout、Wheeltec profile 的 PGM 项采用 `source.kind: pgm_file`：读取 `state_file` 中的地图身份，再检查 `map_root` 下的地图文件，**不会订阅**其中的 `/ccs/relocalization/pgm_file`。无需额外造一个同名发布节点。

CCS 自己发布以下诊断话题，外部包按需订阅，无需自行提供发布者：

| 话题默认值 | 类型 | 含义 |
| --- | --- | --- |
| `/epgeneral_udp_telemetry/link/udp_tx` | `std_msgs/Bool`，latched | 本机 UDP 发送状态；不是地面站接收确认 |
| `/epgeneral_udp_telemetry/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 数据源及发送诊断 |

### 3.3 视频：`video.yaml`

外部相机节点发布 `image_topic`，模板为 `/camera/image_raw`，`image_message_type` 为 `sensor_msgs/Image`；也支持 `sensor_msgs/CompressedImage`。CCS 负责编码和 SRT 输出，没有视频输出 ROS 话题。

配置 `output_width/output_height/framerate/bitrate_kbps` 调整输出；模板为 640×480、30 fps、2000 kbps，`frame_timeout_seconds: 5.0`。必须实际启动相机驱动；仅设置 `camera_model` 不会启动相机。视频运行环境需要 GStreamer 的 `appsrc`、`videoconvert`、`x264enc`、`h264parse`、`mpegtsmux`、`srtsink`。

顶层 `enabled: false` 并非视频 C++ 节点读取的启停开关。Wheeltec 依靠启动脚本不启动视频及 bringup 的 `enable_video` 控制，不能只改 YAML 的 enabled。

### 3.4 建图：`map_stream.yaml`

| 方向 | 模板话题 | 类型 | 配置键 | 外部责任 |
| --- | --- | --- | --- | --- |
| 外部 → CCS 输入探测/算法 | `/livox/lidar` | `livox_ros_driver2/CustomMsg` | `ros.inputs.lidar.topic/message_type/frame` | 驱动持续发布雷达，模板 frame=`livox_frame` |
| 外部 → CCS 输入探测/算法 | `/livox/imu` | `sensor_msgs/Imu` | `ros.inputs.imu.topic/message_type/frame` | 提供 IMU，模板 frame=`livox_frame` |
| 外部 → CCS 预览 | `/lio/cloud_registered_body` | `sensor_msgs/PointCloud2` | `ros.stream.cloud.topic/message_type/frame/coordinates` | 点云含 x/y/z；模板 frame=`body_lio`，coordinates=`sensor` |
| 外部 → CCS 预览 | `/lio/odometry` | `nav_msgs/Odometry` | `ros.stream.pose.topic/message_type/position_path/orientation_path` | `pose.pose.position`、`pose.pose.orientation`，与点云可按时间配对 |

原始雷达输入与预览点云不是同一个接口：仅有 `/livox/lidar` 不能替代算法输出的 PointCloud2。修改 `ros.inputs` 只修改 CCS 对输入的描述/探测，FAST-LIO 的订阅话题仍需在其参数文件或 remap 中配置。

需要同时核对以下约束：

- 点云和里程计的 `header.stamp` 有效且同一时间基准；模板同步容差 `sync.tolerance_seconds=0.05` 秒，位姿缓存 100 条。
- `ros.frames.map/preview/body/sensor` 与实际坐标语义一致。模板分别为 `lio_odom/odom/body_lio/body_lio`。
- `ros.body_from_sensor` 为代码使用的传感器到机体变换，应按实际标定和位姿语义填写；四元数不能全零。不同设备的外参不可互抄。
- `coordinates: sensor` 的点云需结合外参和位姿变换；`coordinates: map` 的点云已在相应世界坐标中，不能再次按机体系点云转换。
- 预览 frame 不同时提供可查询的 TF；模板预览 TF 查询超时 0.20 秒。改 `frame` 字符串不能替代真正的点坐标转换。
- 模板 `input_timeout_seconds=3.0`、`ready_timeout_seconds=60.0`。超时应覆盖真实启动耗时，不应掩盖缺失输入。

### 3.5 重定位：`relocalization.yaml`

| 方向 | 模板接口 | 类型 | 配置位置与要求 |
| --- | --- | --- | --- |
| CCS → 外部定位栈 | `/initialpose` | `geometry_msgs/PoseWithCovarianceStamped` | `ros.initial_pose_topic`；外部必须已有订阅者，frame 为 `ros.map_frame` |
| 定位栈/map_server → ROS | `/map_2d` | `nav_msgs/OccupancyGrid`（当前 map_server 路径） | `ros.map_topic`；Ground-Air 为 `/map` |
| 外部定位栈 → CCS | TF `map <- odom` | `/tf`、`/tf_static` 的标准 TF 消息 | `ros.map_frame/odom_frame`；提供真实定位变换 |

重定位栈就绪检查当前只检查地图/初始位姿话题可解析及 initialpose 有订阅者，并不完整验证栅格内容，因此联调还应检查地图消息。CCS 发布 initialpose 为非 latched；应先启动接收端再触发重定位。

在 `ros.stages` 按依赖顺序填写外部 `package/launch/args`。支持 `{map_id}`、`{map_dir}`、`{map_root}`、`{map_pcd}`、`{map_yaml}` 替换，不是任意 shell 命令。外部 launch 必须接受实际传入的参数。Scout/Wheeltec 默认稳定判据为 10 Hz、10 个样本、平移波动不超过 0.10 m、yaw 波动不超过 2°，等待上限 30 秒。Ground-Air profile 使用首个有效 TF 后连续回报模式，见其 `tf_reporting`。

## 4. 任务执行需要外部订阅、反馈及运动接口

### 4.1 通用任务契约

| 方向 | 默认话题 | 类型 | 配置键 |
| --- | --- | --- | --- |
| CCS 协调器 → 执行适配器 | `/epgeneral_task_control/execution_command` | `epgeneral_task_control/TaskExecutionCommand` | `ros.command_topic` |
| 执行适配器 → CCS 协调器 | `/epgeneral_task_control/execution_feedback` | `epgeneral_task_control/TaskExecutionFeedback` | `ros.feedback_topic` |
| CCS 协调器 → 可选消费者 | `/epgeneral_task_control/task_status` | `std_msgs/String` | `ros.status_topic` |

可以使用随包导航适配器，也可以由外部工作空间实现适配器；同一设备不要同时运行多个执行同一命令的适配器。只运行通用协调器不会自动控制底盘或 MAVROS。

当前 [TaskExecutionCommand.msg](../EPGeneral_task_control/msg/TaskExecutionCommand.msg) 包含 **SCHEDULE=1、CANCEL=2、STOP=3、PREPARE=4、UNLOAD=5、EMERGENCY_STOP=6**。旧接口参考仅列到 5，新接入必须以当前 `.msg` 为准。

| 消息 | 必须处理的字段 | 说明 |
| --- | --- | --- |
| command 与 feedback | `request_id/task_id/subtask_id/device_id/execution_id`：string；`revision`：uint32 | 反馈与当前请求身份一致，避免串任务 |
| command | `action`：uint8；`xml_path/frame_id/map_id`：string；`scheduled_at`：time | 读取任务 XML，核对地图与坐标系，按 UTC 调度 |
| feedback | `state`：string；`waypoint_index/waypoint_count`：int32；`progress`：float64 | 准备、就绪、运行和终态反馈，进度按当前协议/适配器语义（完成为 1.0） |
| feedback | `position`：geometry_msgs/Point；`error_code/message`：string | 任务参考系内真实位置与失败原因 |

`storage.directory` 中的 XML 绝对路径须对执行器可读。模板准备反馈阈值 2 秒，执行反馈阈值 5 秒，UTC 容差 2 秒；适配器持续反馈周期应小于相应阈值。急停必须反映真实执行结果，普通 STOP 与原生急停闭锁不能等同。

### 4.2 使用随包导航适配器时

| 外部责任 | 默认接口 | 类型/要求 | `task_control.yaml` 配置 |
| --- | --- | --- | --- |
| 提供导航 Action 服务端 | `/move_base` | `move_base_msgs/MoveBaseAction` | `adapter.navigation_action` |
| 发布里程计 | `/fastlio_odom` | `nav_msgs/Odometry`，位姿新鲜 | `adapter.odom_topic`；`pose_timeout_seconds=2.0` |
| 接收停车速度并作用到驱动 | `/cmd_vel` | `geometry_msgs/Twist` | `adapter.zero_velocity_topic` |
| 提供导航启动文件 | `scout_navigation/navigation_teb.launch` | 能加载对应地图并建立导航 Action | `adapter.navigation_launch_package/navigation_launch_file` |
| 提供定位及机体 TF | 任务 `map` 系与里程计参考系之间的有效变换 | 与 Odometry 的 frame 一致 | `ros.map_frame` 与外部 TF 配置 |

`/move_base` 是 Action 名称空间，不是单独一个普通 topic；应提供其 goal/cancel/status/feedback/result 整套接口。停车配置模板为 20 Hz 连续 10 次零 Twist，外部底盘或速度仲裁器必须实际接收并执行该命令。

任务适配器的 `active_map_state_file`、`navigation_map_root`、`navigation_map_yaml` 要与重定位一致。不能仅把旧状态文件写成 localized 就代替当前有效定位。选择 `navigation_task_control.launch` 或兼容的 `scout_task_control.launch` 会同时运行协调器和导航适配器，无需另开一份通用协调器。

## 5. ROS 话题之外的外部协作配置

### 5.1 建图 launch、保存服务与文件工具

由 `map_stream.yaml` 的 `integrations.backend` 决定真正执行的后端；共享模板未写 backend 时走 Go2 兼容路径。不能把所有 integrations 字段都当作每种设备必须提供的运行接口。

| 后端 | 外部应提供 | 具体配置/约定 |
| --- | --- | --- |
| `go2_accumulator` | `go2_tf_manager`、`go2_pose_adapter`、`cloud_frame_adapter`、`go2_map_accumulator` 的被 include launch；`fast_lio/fastlio_mapping` 与 `go2_bringup` 参数文件 | CCS 的 `mapping_prerequisites.launch` 和 `fast_lio_mapping.launch` 仍依赖外部包；修改 `integrations.mapping_prerequisites.*`、`integrations.fast_lio.*` |
| `go2_accumulator` | `/go2_map_accumulator/save`，以及 `go2_map_tools/pcd_to_pgm.launch` | `integrations.map_accumulator.service/setup_file`、`integrations.pgm.*`；保存脚本无请求参数调用服务，外部必须提供兼容调用契约 |
| `scout_finalize` | Scout 的 FAST-LIO、pointcloud_mapper、TF/pose adapter 及 `scout_map_tools/finalize_map.py` | `integrations.scout` 下的 `*_package/*_launch`、`finalize_executable/map_root/filtered_pcd_filename` |
| `managed_finalize` | Wheeltec 或其他匹配生命周期的 FAST-LIO、mapper、TF/pose adapter、finalizer | `integrations.managed` 同类字段，另须配置实际 `fast_lio_node/mapper_node/tf_node/geometry_tf_node/pose_node` |
| `ground_air_service` | 原生建图阶段、保存 launch 和地图文件 | `integrations.ground_air.expected_nodes/save_package/save_launch/map_root/saved_*_filename`；当前为 `car_bringup/save_mapping.launch` |

Scout/Wheeltec finalizer 要兼容 `rosrun <package> <executable> <map_name> --replace-raw`，在配置 map_root 下相应地图目录产出 `public_map.pcd/map.pgm/map.yaml`。Ground-Air 保存 wrapper 执行配置的 save launch，并检查相应成果；`/ground_air/mapping/save` 是算法保存链路配合项，不能仅凭配置名推断其 `.srv` 类型。

**不需要实现的占位接口：** Scout 的 `/unused_scout_map_service`、Wheeltec 的 `/unused_wheeltec_map_service`、两者的 `unused.launch` 是兼容配置结构保留项，相应 finalize 后端不要求为这些名称额外创建服务或 launch。

保存服务的外部 `.srv` 定义未包含在本目录，尤其 Go2 的 save 服务不能从无参调用推断它一定是 Trigger 或 Empty。应在设备用 `rosservice type`、`rossrv show` 核对。

### 5.2 地图、任务和状态文件必须对齐

| 配置位置 | 与谁保持一致 | 外部要求 |
| --- | --- | --- |
| 建图 `artifacts.accumulator_pcd_path/source_pcd_path/source_pgm_path/source_yaml_path` | 对应算法/地图工具真实输出位置；部分后端按会话地图名派生路径 | 产出本次会话的新文件；不能用旧文件冒充保存成功 |
| 建图 `integrations.scout/managed/ground_air.map_root` | 外部保存器/finalizer 的地图根目录 | 目录可写；文件名及子目录规则一致 |
| 建图 `artifacts.workspace_root/archive_root` | CCS 会话及归档目录 | 有足够空间和读写权限；模板空间下限通常 5 GiB，Ground-Air profile 为 1 GiB |
| 重定位 `storage.map_root` | 任务 `adapter.navigation_map_root`、UDP pgm_file 的 `source.map_root` | 读取同一份下载地图 |
| 重定位 `storage.active_map_state_file` | 任务 `adapter.active_map_state_file`、UDP pgm_file 的 `source.state_file` | 使用同一份活动地图状态，不各写一个副本 |
| 重定位 `storage.pcd_filename` | 外部定位器输入 | 按后端为 `public_map.pcd` 或 `cloud_map.pcd` |
| 任务 `storage.directory` | command 的 `xml_path` | 外部执行器可读取持久化 XML |

栅格 `map.yaml` 的 image、resolution、origin 等必须与实际 PGM 及任务坐标系一致。建图预览 frame 与最终地图 frame 可以不同，但需要真实一致的变换和成果转换，不能只改元数据声明。

### 5.3 网络和授时

| 通道 | 端侧/地面站方向 | 配置位置 |
| --- | --- | --- |
| MQTT TCP 1883 | 端侧连接地面站 Broker | `epgeneral_mqtav.yaml:mqtt.ground_station_ip/port` |
| 遥测 UDP 14560 | 端侧 → 地面站 | `udp_telemetry.yaml:network.destination_host/destination_port` |
| 建图 UDP 14561 / 14562 | 地面站 → 端侧控制 / 端侧 → 地面站数据状态 | `map_stream.yaml:network` |
| 建图 HTTP TCP 14600 | 地面站访问端侧地图/预览资源 | `map_stream.yaml:http` |
| 任务 UDP 14563 / 14564 | 地面站 → 端侧控制 / 端侧 → 地面站状态 | `task_control.yaml:network` |
| 重定位 UDP 14565 / 14566 | 地面站 → 端侧控制 / 端侧 → 地面站状态 | `relocalization.yaml:network` |
| SRT UDP 9000 | 端侧 Listener，地面站 Caller 连接 | `video.yaml:srt_bind_address/srt_port/srt_latency_ms` |
| NTP UDP 123 | 端侧向授时服务器同步 | profile 中 `timesyncd-ccs.conf` 与启动脚本授时设置 |

重定位还需能够访问地面站命令提供的地图下载 URL，具体地址端口以实际地面站配置为准。`device.yaml` 的 `device.ip` 是设备自身地址，`0.0.0.0` 仅用于本机监听，不能作为地面站目的地址。

修改地面站地址时逐份更新 MQTT、UDP、建图、重定位、任务及授时配置。特别注意 `epgeneral_udp_telemetry.launch` 的 `destination_host` 默认 `192.168.151.100`，会覆盖 YAML；启动时必须显式传实际值。仅设置 `CCS_GROUND_STATION_IP` 不会重写所有 YAML，Ground-Air 脚本也不提供统一的该变量替换逻辑。

任务按 UTC 时间执行。按照设备部署方式配置 NTP 后，用 `timedatectl timesync-status` 核实服务器和同步状态。一键脚本的授时预检失败会阻止启动新组件。

## 6. Ground-Air 专属外部契约

本节只适用于 `ground_air_agv`，其他设备不需要构建 `ground_air_msgs`。当前 CCS 提供阶段管理/桥接节点，算法工作空间提供原生定位、建图及任务执行能力。

| 提供者与方向 | 接口 | 类型/要求 | 配置方法 |
| --- | --- | --- | --- |
| CCS 阶段管理器提供，建图/重定位控制调用 | `/ground_air/system/set_stage` | `ground_air_msgs/SetSystemStage` | 使用随包 stage manager；不是要求外部重复实现一个同名服务 |
| CCS 阶段管理器发布 | `/ground_air/system/stage`、`/ground_air/system/stage_detail` | `std_msgs/UInt8`、`std_msgs/String`，latched | 阶段 0 基础、1 建图、2 重定位 |
| 外部定位服务，CCS 调用 | `/ground_air/load_map` | `ground_air_msgs/LoadMap` | 当前 initial pose adapter 中固定服务名，重命名需配套 remap/适配 |
| 外部定位服务，CCS 调用 | `/ground_air/relocalize` | `ground_air_msgs/Relocalize` | initialpose 转换为原生重定位请求，使用初始猜测 |
| 外部车辆节点发布 | `/ground_air/vehicle_status` | `ground_air_msgs/VehicleStatus` | `adapter.vehicle_status_topic` |
| 外部任务节点发布 | `/ground_air/mission/status` | `ground_air_msgs/MissionStatus` | `adapter.mission_status_topic` |
| 外部节点维护参数 | `/ground_air/localized` | bool，实时定位有效状态 | `adapter.localization_param`；不是 ROS 话题 |
| 外部服务，CCS 调用 | `/ground_air/prepare_ground` | `std_srvs/Trigger` | `adapter.prepare_ground_service` |
| 外部服务，CCS 调用 | `/ground_air/mission/submit` | `ground_air_msgs/SubmitMission` | `adapter.mission_submit_service` |
| 外部服务，CCS 调用 | `/ground_air/mission/start`、`/ground_air/mission/cancel` | `std_srvs/Trigger` | `adapter.mission_start_service/mission_cancel_service` |
| 外部服务，CCS 调用 | `/ground_air/emergency_stop` | `ground_air_msgs/SetEmergencyStop` | `adapter.emergency_stop_service`；需真正闭锁并回报成功 |

外部工作空间须提供 `car_bringup/task_system.launch`，在 `adapter.task_launch_package/task_launch_file` 指定。当前 profile 限制线速度 0.1 m/s、角速度 0.2 rad/s，航点停留 2 秒，服务超时 10 秒。持久化急停文件由 `adapter.emergency_lock_file` 指定，应保留在 CCS 可写目录。

外部消息和服务完整定义不在本仓库，交付时用 `rosmsg show` / `rossrv show` 检查与源码访问字段一致。不要根据接口名称自行发明服务请求格式。

建图 profile 的 expected_nodes 必须对应原生建图实际节点，包括 `/fast_lio_node`、`/fastlio_odometry_to_px4`、`/ground_filter_node`、`/noground_trans_node`、`/dynamic_mapping`、`/ground_air_map_recorder`、`/ground_air_world_tf_owner`、`/ground_air_start_mapping` 及两个静态 TF 节点。`odom <- camera_init`、`base_link <- body` 的静态变换由一键脚本持有，避免阶段切换时重复广播。阶段请求按 caller/map_id 归属，建图与重定位互斥。

Ground-Air 重定位 profile 使用局部 `ros_package_path_prepend/exclude` 和 `cmake_prefix_path_exclude` 选择 CCS override 的 `car_bringup/relocalization_system.launch`，须按 [Ground-Air 部署资料](GROUND_AIR_AGV_DEPLOYMENT.md) 安装配套 override，不能只复制 YAML 或全局改写 ROS_PACKAGE_PATH。

## 7. 四套设备 profile 的实际话题差异

以下为仓库配置值，不代表已在线探测。完整原件分别见 [Go2](../deploy/go2_edu/config/)、[Scout](../deploy/scout_mini/config/)、[Wheeltec](../deploy/wheeltec_r550p/config/)、[Ground-Air](../deploy/ground_air_agv/config/)。

| 项目 | Go2 EDU | Scout Mini | Wheeltec R550P | Ground-Air AGV |
| --- | --- | --- | --- | --- |
| 状态源 | `/livox/lidar`，CustomMsg 新鲜度 | `/scout_status`，ScoutStatus 新鲜度 | `/odom`，Odometry 新鲜度 | `/mavros/state`，State 字段 |
| 电池源 | 禁用 | `/BMS_status`，ScoutBmsStatus 的 battery_voltage | `/PowerVoltage`，Float32.data | `/mavros/battery`，BatteryState |
| UDP 位姿 | `/lio/odometry` | `/scout/odom` | `/fastlio_odom` | `/mavros/local_position/pose` 和 `/Odometry` |
| UDP IMU | `/livox/imu` | `/livox/imu` | `/livox/imu` | `/mavros/imu/data` |
| 建图点云 | `/lio/cloud_registered_body` | `/cloud_registered_body` | `/cloud_registered_body` | `/cloud_registered` |
| 建图位姿 | `/lio/odometry` | `/fastlio_odom` | `/fastlio_odom` | `/Odometry` |
| 点云 frame / coordinates | body_lio / sensor | body / sensor | body / sensor | camera_init / map |
| 建图后端 | go2_accumulator | scout_finalize | managed_finalize | ground_air_service |
| 重定位地图话题 | 配置禁用 | `/map_2d` | `/map_2d` | `/map` |
| 视频输入 | `/camera/color/image_raw` | `/camera/color/image_raw` | 当前脚本不启动视频 | `/a8_cam/image_raw` |
| 一键脚本任务路径 | 不启动任务 | 导航适配器 | 导航适配器 | ground_air 适配器 |

Scout 的状态 mapping 为 `system_status: fault_code`、`mode: control_mode`；电池只取电压，其他量为 null。需要匹配驱动提供的 `scout_msgs/ScoutBmsStatus`，相关补丁见 `deploy/scout_mini/patches`。

Scout 的 UDP 位姿 `/scout/odom` 与建图/任务 `/fastlio_odom` 不同；Scout/Wheeltec 的 FAST-LIO 可用性监测仍指向 `/Odometry`。这些不是同一键的别名，外部应提供各自接口，或经核实后分别修改相关 source。Go2 未启用的任务配置仍留有 `192.168.151.100`，未来启用前必须补齐设备适配器并改为实际地面站地址。

## 8. 配置示例与实施步骤

### 8.1 把已有里程计接入遥测

假设外部发布 `/robot/local_odom`，类型为 `nav_msgs/Odometry`。在选定 profile 原有 `vision_pose` descriptor 中替换 source，保留原有 name/display_name/type/level：

```yaml
source:
  topic: /robot/local_odom
  message_type: nav_msgs/Odometry
  mapping:
    position: pose.pose.position
    orientation: pose.pose.orientation
```

这只是 source 子段，不能用它覆盖整份 YAML。PoseStamped 的字段少一层 pose，必须同步修改 message_type 与 mapping。若需要把同一里程计用于任务，还要另改 `task_control.yaml:adapter.odom_topic`；若用于建图预览，再改 `map_stream.yaml:ros.stream.pose` 并核对坐标系。

### 8.2 无飞控的底盘状态接入

以下替换 `epgeneral_mqtav.yaml` 中 `ros.state` 与 `ros.battery`，保留 MQTT 等其他配置：

```yaml
state:
  topic: /robot/local_odom
  message_type: nav_msgs/Odometry
  connected_on_message: true
  timeout_seconds: 3.0
  mapping: {connected: null, armed: null, system_status: null, mode: null}
battery:
  enabled: false
```

`state/battery` 必须置于 `ros:` 下。若有真实电压话题，可参照 Wheeltec profile 配置 Float32 的 data 字段。

### 8.3 建图/定位/任务的改动顺序

1. 选与硬件匹配的 profile，核实实际运行目录，备份原配置。
2. 外部工作空间构建驱动、消息、算法和导航；加载 underlay，再构建/加载 CCS overlay。
3. 用 `rostopic type/echo/hz` 核实真实名字、类型、字段、时间戳和 frame；按第 3 节填写各 source。
4. 配置外部算法的雷达/IMU输入、标定外参和 TF；再填写 CCS 的点云坐标模式及 frame。
5. 选择建图 backend，填可读 setup.bash、可调用 package/launch、保存工具和可写输出目录；排除旧会话残留的同名进程。
6. 对齐重定位 stages、初始位姿话题、地图路径和活动状态文件；外部定位器必须实际读取传入地图。
7. 配置导航或 Ground-Air 适配器，确认反馈、停车和急停链路。无任务适配器的设备保持任务不启动。
8. 统一 device ID、本机 IP、地面站地址与授时；重新启动受影响节点，按第 9 节联调。

### 8.4 显式选择配置文件启动

下列命令在 ROS Linux 设备执行，`PROFILE_DIR` 改为真实目录。各命令对应独立终端，按需要选择，不与一键栈重复运行：

```bash
export PROFILE_DIR=/home/robot/ccs_edge_ws/config/my_robot

roslaunch epgeneral_mqtav epgeneral_mqtav.launch \
  device_config_file:="$PROFILE_DIR/device.yaml" \
  config_file:="$PROFILE_DIR/epgeneral_mqtav.yaml"

roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  device_config_file:="$PROFILE_DIR/device.yaml" \
  telemetry_config_file:="$PROFILE_DIR/udp_telemetry.yaml" \
  destination_host:=192.168.50.101 destination_port:=14560

roslaunch epgeneral_map_stream epgeneral_map_stream.launch \
  device_config_file:="$PROFILE_DIR/device.yaml" \
  mapping_config_file:="$PROFILE_DIR/map_stream.yaml"

roslaunch epgeneral_relocalization epgeneral_relocalization.launch \
  device_config_file:="$PROFILE_DIR/device.yaml" \
  config_file:="$PROFILE_DIR/relocalization.yaml"

roslaunch epgeneral_task_control navigation_task_control.launch \
  device_config_file:="$PROFILE_DIR/device.yaml" \
  task_config_file:="$PROFILE_DIR/task_control.yaml"

roslaunch epgeneral_video_srt epgeneral_video_srt.launch \
  device_config_file:="$PROFILE_DIR/device.yaml" \
  video_config_file:="$PROFILE_DIR/video.yaml"
```

使用外部自定义任务适配器时将任务入口换为 `epgeneral_task_control.launch`，另启动自己的适配器；Ground-Air 使用 `epgeneral_ground_air_control/ground_air_task_control.launch`。源码中的 `deployment.enabled/state` 多为元数据，不能作为通用启停开关；实际以启动入口及节点读取逻辑为准。

## 9. 联调验收与常见问题

先检查环境和消息，再启动所选功能，按实际 profile 替换命令中的话题：

```bash
rospack find livox_ros_driver2
rosmsg show epgeneral_task_control/TaskExecutionCommand
rosmsg show epgeneral_task_control/TaskExecutionFeedback
rostopic type /livox/lidar
rostopic hz /livox/lidar
rostopic type /fastlio_odom
rostopic echo -n 1 /fastlio_odom/header
rostopic info /initialpose
rostopic type /map_2d
rosrun tf tf_echo map odom
rostopic info /cmd_vel
rostopic type /move_base/goal
rostopic echo -n 1 /epgeneral_udp_telemetry/diagnostics
timedatectl timesync-status
ss -lntup
```

检查无副作用的服务类型与结构，不用真实任务/急停服务调用代替接口检查：

```bash
# 仅 Go2 保存后端
rosservice type /go2_map_accumulator/save
# 仅 Ground-Air
rosservice type /ground_air/system/set_stage
rossrv show ground_air_msgs/SetSystemStage
rossrv show ground_air_msgs/SubmitMission
rossrv show ground_air_msgs/SetEmergencyStop
rosparam get /ground_air/localized
```

| 验收项 | 应达到的结果 |
| --- | --- |
| 状态/遥测 | 类型和字段可读；断开源后正确体现未知/超时；地面站收到实际数据 |
| 视频 | 相机话题持续有帧，地面站可以建立 SRT 连接并解码 |
| 建图 | prepare 依赖检查通过，start 后有预览；保存生成本次 PCD/PGM/YAML，结束释放所属算法资源 |
| 重定位 | 地图安装到约定位置，有 initialpose 接收者，获得有效 map←odom，并更新同一活动状态文件 |
| 任务 | 准备反馈正常、地图与 TF 一致、UTC 同步；在受控现场验证执行/停止/急停反馈与真实执行器一致 |

| 现象 | 优先检查 |
| --- | --- |
| 消息类加载失败 | 外部消息包是否构建、是否 source、package/Message 是否准确 |
| 有话题却字段取不到 | PoseStamped 与 Odometry 路径区别、mapping 的 null 和实际字段 |
| YAML 改了地址仍发旧地址 | 实际配置副本、UDP launch destination_host 覆盖、旧节点是否重启 |
| 原始雷达正常但无建图预览 | 是否有 PointCloud2/配对里程计、时间差、frame/TF/外参 |
| 重定位等待超时 | external launch 是否退出、地图话题及 initialpose 订阅者、map←odom 是否产生 |
| PGM 状态异常 | 是话题模式还是 pgm_file 模式；状态文件和 map_root 是否一致 |
| 任务收到了但车辆不执行 | 是否有且仅有一个适配器、反馈身份、Action/原生服务、地图/实时定位、UTC |

## 10. 依据与后续维护

主要依据为 [共享 YAML](../EPGeneral_device_config/config/)、[各设备部署配置](../deploy/)、[ROS 任务消息](../EPGeneral_task_control/msg/)、[UDP 节点](../EPGeneral_udp_telemetry/src/epgeneral_udp_telemetry/node.py)、[建图节点](../EPGeneral_map_stream/src/epgeneral_map_stream/node.py)、[重定位桥接](../EPGeneral_relocalization/src/epgeneral_relocalization/ros_bridge.py)、[导航适配器](../EPGeneral_task_control/src/epgeneral_task_control/scout_adapter.py)、[Ground-Air 任务适配器](../EPGeneral_ground_air_control/src/epgeneral_ground_air_control/task_adapter.py) 及相应 launch/scripts。

逐键参数范围及更多启动环境变量见 [设备内接口与配置参考](INTERFACE_REFERENCE.md)，部署流程见 [使用手册](USER_MANUAL.md)。当说明与实现不一致时，以当前配置解析器、`.msg` 和实际使用的启动入口为准；外部工作空间的消息/服务定义、标定结果和运行状态需要在设备上另行核验。
