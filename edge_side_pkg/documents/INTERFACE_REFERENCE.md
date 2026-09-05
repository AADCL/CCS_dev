# 端侧设备内接口与配置参考

适用产品：CCS 0.23.1；更新日期：2026-09-05。包版本见[端侧 README](../README.md)，操作步骤见[使用手册](USER_MANUAL.md)。

本册面向设备集成开发者，描述 CCS 包如何调用设备其他工作空间的功能。MQTT、UDP、SRT 消息格式以[地面站通信协议](../../docs/EDGE_DEVICE_INTERFACES.md)为准。配置 schema、网络 schema 和软件版本是三个独立概念。

## 1. 工作空间与进程边界

### 1.1 ROS 环境

所有交互节点使用同一个 `ROS_MASTER_URI`，`ROS_IP` 为其他节点可达的本机地址。新终端先 source Noetic，再加载设备 underlay，最后加载 CCS overlay。已有 overlay 用 `--extend`，并以 `rospack find` 检查实际解析路径；复制源码不会自动提供外部依赖。

| profile | 外部工作空间及集成要求 |
| --- | --- |
| Go2 | `/home/nvidia/go2_mid360_nav/catkin_ws` 提供 Livox、LIO、地图 accumulator 和 PGM 工具；CCS 在 `/home/nvidia/ccs_edge_ws` |
| Scout | 启动脚本依次 source Noetic、RealSense、Scout navigation、livox_fastlio、CCS；依赖 Scout 状态/BMS、Livox、FAST-LIO、TF/pose/cloud adapter 和 move_base |
| Wheeltec | `/home/nrc19/livox_fastlio` 提供底盘、Livox、FAST-LIO、地图工具和导航；CCS 在 `/home/nrc19/ccs_edge_ws` |
| Ground-Air | `/home/bitcq/catkin_ws` 提供算法与 `ground_air_msgs`；`/home/bitcq/ccs_edge_ws` 提供 CCS、阶段控制及局部 override |

算法工作空间不是本仓库的部署目标。已有 profile 中地图输出到算法工作空间的 `maps` 是显式文件接口，不能据此覆盖算法源码。Ground-Air 的重定位子进程局部 prepend `ccs_edge_ws/overrides` 并排除原同名包搜索路径，避免 `car_bringup` 重复资源；不要全局替换 `ROS_PACKAGE_PATH`。

### 1.2 功能交互矩阵

| CCS 包 | 从外部接收 | 向外部输出或调用 | 就绪与故障语义 |
| --- | --- | --- | --- |
| device_config | 无 | 七份 YAML 资源 | 不是节点，不需要 rosrun |
| mqtav | 可配置 ROS 状态、电池、任务摘要 | MQTT presence/heartbeat/status | 未知值保留未知；状态消息新鲜度可代替 MAVROS connected |
| udp_telemetry | Pose、Odometry、IMU、String、任意话题新鲜度及 PGM 文件 | UDP 14560、Bool 链路状态、DiagnosticArray | 单个坏来源隔离；本机 sendto 成功不表示地面站已收到 |
| video_srt | Image 或 CompressedImage | H.264 baseline/MPEG-TS/SRT Listener | 外部相机驱动需独立运行；缺帧及插件异常写日志 |
| map_stream | Livox/IMU 输入探测、PointCloud2、Odometry、TF | 外部建图 launch、保存服务/finalizer、UDP 控制状态及 HTTP 成果 | prepare 校验输入和依赖，start 才进入建图；会话结束释放自身资源 |
| relocalization | 地面站地图归档、定位 TF | 外部定位 launch、initialpose | 栈就绪后发初始位姿；状态文件与实时定位一致 |
| task_control | UDP 任务、执行反馈、适配器的 Odometry/TF | 自定义 command、任务状态、move_base action、停车 Twist | 协调器不直接控制 MAVROS；运动由设备适配器执行 |
| ground_air_control | stage 请求、initialpose | SetSystemStage、LoadMap、Relocalize、阶段状态 | 建图与重定位互斥，按会话归属释放阶段 |

## 2. ROS 消息、服务与 TF

### 2.1 状态、遥测与视频

MQTT 的 `ros.state/battery` 使用 `package/Message` 动态加载消息类，`mapping` 使用点分字段路径。Scout 来源为 `/scout_status`、`/BMS_status`；Wheeltec 为 `/odom`、`/PowerVoltage`；Go2 用 `/livox/lidar` 新鲜度且禁用电池源；Ground-Air 使用 `/mavros/state`、`/mavros/battery`。没有可确认的数据时不能填造电池或飞控状态。

UDP descriptor 的 pose/imu/text_status 加载具体消息；availability/pointcloud_status 使用 AnyMsg 监测到达时间。`pgm_file` 从状态文件取得 map_id，再检查地图根目录内的 `map.pgm`，不是订阅示例 topic。默认诊断输出为 `/epgeneral_udp_telemetry/diagnostics`（diagnostic_msgs/DiagnosticArray），链路状态为 `/epgeneral_udp_telemetry/link/udp_tx`（std_msgs/Bool，latched），可按 launch 覆盖。

视频输入类型仅为 `sensor_msgs/Image` 或 `sensor_msgs/CompressedImage`，输出无 ROS 消息。运行依赖 appsrc、videoconvert、x264enc、h264parse、mpegtsmux、srtsink。地面站作为 Caller 连接端侧 UDP 9000；YAML 延迟单位为 ms，FFmpeg SRT URL 的 latency 单位为微秒。

### 2.2 建图与坐标契约

输入探测与预览不是同一话题：`ros.inputs` 检查雷达/IMU，`ros.stream` 提供预览点云和配对位姿。配置的消息类型、字段路径及 frame 必须匹配实际消息。点坐标需要按 TF/外参真正变换，不能只改 frame 字符串。

| 后端 | 外部契约 |
| --- | --- |
| go2_accumulator | prerequisites/FAST-LIO launch；`/go2_map_accumulator/save`；PCD 转 PGM 工具 |
| scout_finalize | Scout FAST-LIO、pointcloud_mapper、TF/pose adapter；`finalize_map.py` 产出 PCD/PGM/YAML |
| managed_finalize | 与 Scout 生命周期相同，由 `integrations.managed` 指定 Wheeltec 的包、launch 和节点名 |
| ground_air_service | 阶段服务管理原生建图；`/ground_air/mapping/save` 及 save launch 提供地图成果 |

Ground-Air 输入 `/cloud_registered` 在 camera_init，预览需转换为 odom，最终 manifest 声明 map。静态 `odom <- camera_init` 和 `base_link <- body` 由一键脚本持有，stage manager 不重复启动。客户端接受整数 guard 1/2；manager 当前发布 2。缺失、类型错误、未知 guard 均拒绝；stop/abort 仍需匹配 caller/map_id，但不要求 TF 继续存在。

### 2.3 重定位与文件协作

定位栈应订阅 `/initialpose`（geometry_msgs/PoseWithCovarianceStamped），发布配置指定的地图话题和 `map <- odom` TF。先等 map 及 initialpose 订阅者就绪，再发送初始位姿。Scout/Wheeltec 按连续样本稳定窗口判断；Ground-Air 首个有效 TF 即成功，此后每秒回报缓存或新 TF，最多每 30 秒持久化。后续静止或短暂查询失败不会把缓存误判成首样本超时。

地图安装根目录、重定位 `active_map_state_file`、任务适配器读取的同名状态路径、UDP `pgm_file` 的 `state_file/map_root` 必须对齐。状态 schema 2 包含 status/map_id；localized 另有 map_frame、odom_frame、localized_at、map_from_odom（x/y/z/qx/qy/qz/qw）。重启后旧 localized 不作为实时定位；任务需要有效状态及变换。地图文件名按后端为 public_map.pcd 或 cloud_map.pcd，栅格由 map.pgm 与 map.yaml 配对。

### 2.4 任务适配器接口

消息真源为 `EPGeneral_task_control/msg/TaskExecutionCommand.msg` 和 `TaskExecutionFeedback.msg`。command 默认话题 `/epgeneral_task_control/execution_command`，feedback 默认 `/epgeneral_task_control/execution_feedback`，摘要为 `/epgeneral_task_control/task_status`（std_msgs/String）。

命令常量为 SCHEDULE=1、CANCEL=2、STOP=3、PREPARE=4、UNLOAD=5。适配器必须保持 request/task/subtask/device/execution/revision 身份对应，读取协调器持久化 XML，按 UTC scheduled_at 执行并回报真实状态及进度。完整字段及类型以随包 .msg 为准，可用 `rosmsg show epgeneral_task_control/TaskExecutionCommand` 和对应 Feedback 检查构建结果。

导航适配器调用 `move_base_msgs/MoveBaseAction`，读取 nav_msgs/Odometry 和 TF，向配置的 zero_velocity_topic 发布 geometry_msgs/Twist 停车。commit 后准备并常驻导航；常规 stop 复用导航进程，删除/卸载按状态清理。PGM 可通行性、TF、反馈超时和 UTC 校验不应被自定义适配器跳过。

以下为当前 .msg 的全部业务字段；同名身份字段在 command 和 feedback 中均为 string，revision 均为 uint32。

| 消息 | 字段 | ROS 类型 | 定义 |
| --- | --- | --- | --- |
| 两者 | request_id、task_id、subtask_id、device_id、execution_id | string | 请求、任务、子任务、设备和执行身份，反馈必须对应当前请求 |
| 两者 | revision | uint32 | 任务修订号 |
| command | action | uint8 | 前述五个命令常量 |
| command | xml_path | string | 协调器持久化的任务 XML 绝对路径，执行器需可读 |
| command | frame_id、map_id | string | 航点坐标系与地图身份 |
| command | scheduled_at | time | ROS time 表示的 UTC 计划开始时间，不是本地字符串 |
| feedback | state | string | preparing/ready/failed、scheduled/running 及执行终态，按请求阶段回报 |
| feedback | waypoint_index、waypoint_count | int32 | 当前航点索引及总数 |
| feedback | progress | float64 | 执行进度值，与地面站协议定义一致 |
| feedback | position | geometry_msgs/Point | x/y/z，任务参考系内位置，m |
| feedback | error_code、message | string | 结构化失败原因及诊断说明 |

### 2.5 Ground-Air 专属服务

| 接口 | 类型与已核实字段 |
| --- | --- |
| `/ground_air/system/set_stage` | ground_air_msgs/SetSystemStage；请求 stage/map_id/timeout，响应 success/message/active_stage |
| `/ground_air/system/stage` | std_msgs/UInt8，latched；0=基础、1=建图、2=重定位 |
| `/ground_air/system/stage_detail` | std_msgs/String，latched，阶段诊断详情 |
| `/ground_air/load_map` | ground_air_msgs/LoadMap，由初始位姿适配器加载地图 |
| `/ground_air/relocalize` | ground_air_msgs/Relocalize，适配 initialpose，use_initial_guess=true |

外部 .srv 不在本仓库，设备升级后先执行 `rossrv show ground_air_msgs/SetSystemStage`、`rossrv show ground_air_msgs/LoadMap`、`rossrv show ground_air_msgs/Relocalize` 核验，不能把本表当作完整外部消息定义。建图 caller 为 `/ccs_mapping_stage_<session>`，阶段由 caller/map_id 共同归属；保持幂等与互斥。旧 `deploy_stage_manager_update.sh` 和 `car_bringup_scripts` 是 legacy underlay 路径，当前新部署使用 CCS 控制包。

## 3. 配置入口、覆盖与修改

| 节点 | 路径选择与覆盖规则 |
| --- | --- |
| mqtav | launch 的 config_file/device_config_file/log_dir 转为 CLI；业务值直接读 YAML |
| relocalization | launch 的 config_file/device_config_file 转为 CLI；阶段环境只对子进程生效 |
| map_stream | 私有 mapping_config_file/device_config_file 选择 YAML |
| task_control | 私有 task_config_file/device_config_file；适配器读取同一配置 |
| udp_telemetry | 私有路径读取 YAML，再由 destination_host/destination_port/link_status_topic/diagnostics_topic 覆盖 |
| video_srt | device YAML 加载到全局 /edge_device；video YAML 加载到节点私有参数，C++ 启动读取 |
| ground_air_control | 无独立 YAML；launch 参数控制 map_id/maps_root/service_wait_timeout/relocalize_timeout |

所有节点启动读取配置，没有热重载。除明确列出的覆盖项外，`rosparam set` 不会改变直接读取 YAML 的运行节点。

设备脚本入口修改 `<CCS工作空间>/config/<profile>/*.yaml`；单包默认入口修改 `$(rospack find epgeneral_device_config)/config/*.yaml`，或显式传入文件。profile 原件在仓库 `deploy/<profile>/config`，发布 ZIP 和设备实际运行配置是不同层次。

修改顺序：备份实际运行配置；停止受影响会话/节点；修改 YAML；用包解析器校验；确认 ID、地址、端口和跨包地图路径一致；重启；检查 ROS 数据及地面站接收。示例见手册。地面站地址变化需同步 MQTT/network 配置及授时服务器；仅设 CCS_GROUND_STATION_IP 不会重写所有 YAML，Ground-Air 脚本还存在固定地址与诊断话题，需要逐项同步。

### 3.1 参数表约定

后续表格按文件划分，键均为 YAML 完整路径；`[]` 表示列表元素。值栏“必填；示例”表示文件必须提供该值，并非加载器缺省。路径示例依 profile 而异；默认配置不能直接投入设备。每张表的参数修改位置就是该文件的实际运行副本，生效方式均为重启对应节点；身份变更需重启全部通信节点。

`deployment.state`（字符串）、`deployment.enabled`（布尔）是可选说明元数据，缺省无元数据，加载器不据此启停。视频顶层 `enabled` 同样未被 C++ 读取。不要假定所有未知键都会被严格拒绝。

## 4. device.yaml

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `schema_version` | int；必填 1 | 共享身份配置 schema |
| `device.id` | string；必填 | 唯一非空设备 ID，与地面站登记、主题、任务及会话一致 |
| `device.ip` | string；必填 | 设备自身 IP，不能填地面站地址；整套建图/任务使用 IPv4。MQTT 单包解析器也支持 IPv6，不代表整套支持 IPv6 |

## 5. epgeneral_mqtav.yaml

本文件没有配置 schema 字段。以下频率为 Hz，时间为秒。状态/电池 mapping 值是消息字段路径，不是常量；null 表示不提供该项。

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `mqtt.ground_station_ip` | string；必填 | Broker 的 IPv4/IPv6 地址，不能填 DNS 名 |
| `mqtt.port` | int；必填，示例 1883 | 1..65535，Broker TCP 端口 |
| `mqtt.client_id_prefix` | string；必填，示例 mqtav- | 非空，与 device.id 组成客户端 ID |
| `mqtt.qos` | int；必填，示例 1 | 0 或 1 |
| `mqtt.keepalive_seconds` | int；必填，示例 10 | 1..3600 |
| `mqtt.heartbeat_hz` | number；必填，示例 1 | 大于 0 且不超过 100 |
| `mqtt.telemetry_hz` | number；必填，示例 1 | 状态发送频率，大于 0 且不超过 100 |
| `mqtt.topics.presence` | string；必填 | 示例 mqtav/{device_id}/presence |
| `mqtt.topics.heartbeat` | string；必填 | 示例 mqtav/{device_id}/heartbeat |
| `mqtt.topics.status` | string；必填 | 示例 mqtav/{device_id}/status；三主题仅支持 device_id 模板，禁用 +/# 通配符 |
| `ros.node_name` | string；必填 | 非空 ROS 节点名 |
| `ros.state.topic` | string；必填 | 以 / 开头的状态输入话题 |
| `ros.state.message_type` | string；必填 | package/Message，必须在已 source 工作空间中可加载 |
| `ros.state.connected_on_message` | bool；默认 false | true 时以消息新鲜度判断 connected |
| `ros.state.timeout_seconds` | number；默认 3.0 | connected_on_message=true 时生效，0.1..3600 秒 |
| `ros.state.mapping.connected` | string/null；默认 connected | connected 字段路径；新鲜度模式可置 null |
| `ros.state.mapping.armed` | string/null；默认 armed | 解锁状态来源 |
| `ros.state.mapping.system_status` | string/null；默认 system_status | 系统状态来源 |
| `ros.state.mapping.mode` | string/null；默认 mode | 模式来源 |
| `ros.battery.enabled` | bool；默认 true | false 时不订阅电池，不伪造电量 |
| `ros.battery.topic` | string；启用时必填 | 绝对话题 |
| `ros.battery.message_type` | string；启用时必填 | package/Message |
| `ros.battery.mapping.percentage` | string/null；默认 percentage | 原始电池百分比字段，沿用消息语义 |
| `ros.battery.mapping.voltage` | string/null；默认 voltage | 电压字段，V |
| `ros.battery.mapping.current` | string/null；默认 current | 电流字段，A |
| `ros.mission.enabled` | bool；默认 false | 是否采集可选任务摘要，不启动任务控制器 |
| `ros.mission.topic` | string；启用时必填 | 绝对话题 |
| `ros.mission.message_type` | string；启用时必填 | package/Message |
| `ros.mission.field_path` | string；启用时必填 | 非空任务文本路径，String 通常为 data |

## 6. udp_telemetry.yaml

descriptor 的 name/display_name/type/level 共同决定 SHA-256 descriptor_hash；源 topic/mapping 不参与该 hash。修改 descriptor 身份或显示定义时必须同步地面站接受的描述符，不得只改端侧。

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `schema_version` | int；必填 1 | 配置 schema |
| `protocol_id` | string；应填 ccs-udp-telemetry-v1 | 解析缺省为空，不能省略后期待地面站正常接收 |
| `network.destination_host` | string；应填地面站 IP | 解析缺省为空；launch 可覆盖 |
| `network.destination_port` | int；必填，示例 14560 | 1..65535；launch 可覆盖 |
| `network.max_datagram_bytes` | int；默认 16384 | 512..65507，最终数据报上限 |
| `descriptors[].name` | string；必填 | 非空且列表内唯一 |
| `descriptors[].display_name` | string；必填 | 地面站显示名，参与 hash |
| `descriptors[].type` | enum；必填 | pose/imu/pointcloud_status/availability/text_status |
| `descriptors[].level` | int；必填 | 1=20 Hz，2=5 Hz，3=1 Hz；不是任意频率设置 |
| `descriptors[].source.kind` | string；可选 | pgm_file 选择文件检测；未配置时使用话题源 |
| `descriptors[].source.topic` | string；话题源必填 | ROS 输入名；pgm_file 不订阅此键 |
| `descriptors[].source.message_type` | string；按类型必填 | pose/imu/text_status 必填具体消息；新鲜度检测用 AnyMsg |
| `descriptors[].source.timeout_seconds` | number；点云默认 1.0，availability/text 默认 3.0 | 秒，新鲜度阈值应为正值；pose/imu 复用最近值并报告 sample_age，不通过此键停止输出 |
| `descriptors[].source.mapping.position` | string；默认 pose.position | pose 的位置路径，PoseStamped 为 pose.position，Odometry 为 pose.pose.position |
| `descriptors[].source.mapping.orientation` | string；pose 默认 pose.orientation，imu 默认 orientation | 四元数路径 |
| `descriptors[].source.mapping.angular_velocity` | string；默认 angular_velocity | IMU 角速度路径 |
| `descriptors[].source.mapping.linear_acceleration` | string；默认 linear_acceleration | IMU 加速度路径 |
| `descriptors[].source.mapping.value` | string；默认 data | text_status 文本路径 |
| `descriptors[].source.state_file` | string；pgm_file 必填 | 重定位活动地图状态 JSON |
| `descriptors[].source.map_root` | string；pgm_file 必填 | 检查 map_id/map.pgm 的目录，要求 type=availability |

平滑窗口按等级频率聚合，非有限值和零范数四元数被隔离。排查时关注 accepted_count、last_rejection_reason、sample_age；不要用 sendto 状态代替端到端心跳验收。

## 7. video.yaml

以下默认值由 C++ 读取；修改后需重启视频节点，不能只重启地面站播放器。

| 键 | 类型 / 默认 | 定义与约束 |
| --- | --- | --- |
| `image_topic` | string；/camera/image_raw | 输入相机话题 |
| `image_message_type` | string；sensor_msgs/Image | 仅支持 Image 或 CompressedImage |
| `output_width` | int；640 | 输出像素宽度，正数；优先于 image_width |
| `output_height` | int；480 | 输出像素高度，正数；优先于 image_height |
| `image_width` | int；640 | 兼容别名，仅 output_width 缺失时读取 |
| `image_height` | int；480 | 兼容别名，仅 output_height 缺失时读取 |
| `framerate` | int；30 | 输出帧率 Hz，1..120 |
| `srt_bind_address` | string；0.0.0.0 | 本机 Listener 绑定地址 |
| `srt_port` | int；9000 | UDP 端口，1..65535 |
| `srt_latency_ms` | int；120 | SRT 延迟，20..8000 毫秒 |
| `bitrate_kbps` | int；2000 | 编码码率，正 kbps |
| `frame_timeout_seconds` | number；5.0 | 缺帧告警阈值，正秒数 |
| `enabled` | bool；可选元数据 | 当前 C++ 不读取，不能据此禁用视频 |
| `camera_model` | string；可选元数据 | profile 相机说明，当前 C++ 不读取 |
| `deployment.state` | string；可选元数据 | 部署状态说明 |
| `deployment.enabled` | bool；可选元数据 | 不控制启停，实际由脚本/launch 决定 |

## 8. map_stream.yaml

配置 schema=6，协议为 ccs-map-stream-v2。除明确写“默认/可选”的项外，下表均必须提供；数值示例来自公共模板，设备 profile 可能不同。backend 不会消除基础 integrations 配置结构，保留 profile 中的兼容占位字段，勿自行删除。

### 8.1 通信、输入与处理

| 键 | 类型 / 值 | 定义与约束 |
| --- | --- | --- |
| `schema_version` | int；6 | 配置 schema |
| `protocol_id` | string；ccs-map-stream-v2 | 与地面站一致 |
| `network.bind_host` | string；0.0.0.0 | 本地 IP，可为 unspecified |
| `network.control_port` | int；14561 | 1..65535，控制接收 |
| `network.ground_station_ip` | string；按设备 | 有效且非 unspecified 的 IP |
| `network.data_port` | int；14562 | 1..65535，状态上行；prepare 可协商 return_host/return_port |
| `network.max_datagram_bytes` | int；1400 | 512..1400 |
| `http.bind_host` | string；0.0.0.0 | HTTP 本地绑定 |
| `http.port` | int；14600 | 1..65535，预览/成果下载 |
| `http.token_ttl_seconds` | number；900 | 正秒数，下载令牌有效期 |
| `ros.inputs.lidar.topic` | string；/livox/lidar | 原始雷达预检话题 |
| `ros.inputs.lidar.message_type` | string；按 profile | 雷达具体 ROS 消息类 |
| `ros.inputs.lidar.frame` | string；按 profile | 原始雷达 frame |
| `ros.inputs.imu.topic` | string；/livox/imu | IMU 预检话题 |
| `ros.inputs.imu.message_type` | string；sensor_msgs/Imu | 固定预期类型 |
| `ros.inputs.imu.frame` | string；按 profile | IMU frame 配置 |
| `ros.stream.cloud.topic` | string；按 profile | 实际预览点云话题 |
| `ros.stream.cloud.message_type` | string；sensor_msgs/PointCloud2 | 固定预期类型 |
| `ros.stream.cloud.frame` | string；按 profile | 点云源 frame |
| `ros.stream.cloud.coordinates` | enum；map 或 sensor | 点的真实坐标语义 |
| `ros.stream.pose.topic` | string；按 profile | 点云配对位姿 |
| `ros.stream.pose.message_type` | string；nav_msgs/Odometry | 固定预期类型 |
| `ros.stream.pose.position_path` | string；pose.pose.position | 位置信息字段路径 |
| `ros.stream.pose.orientation_path` | string；pose.pose.orientation | 四元数字段路径 |
| `ros.frames.map` | string；按 profile | 建图位姿参考帧 |
| `ros.frames.preview` | string；odom | 上传预览参考帧；Go2 必须与 map 不同 |
| `ros.frames.body` | string；按 profile | 设备本体帧 |
| `ros.frames.sensor` | string；按 profile | 传感器帧 |
| `ros.body_from_sensor.x`、`ros.body_from_sensor.y`、`ros.body_from_sensor.z` | number；标定值 | sensor 到 body 平移，m，有限值 |
| `ros.body_from_sensor.qx`、`ros.body_from_sensor.qy`、`ros.body_from_sensor.qz`、`ros.body_from_sensor.qw` | number；标定值 | 有限四元数，范数至少 1e-6，读取时归一化 |
| `sync.tolerance_seconds` | number；0.05 | 点云/位姿时间容忍，0 < 值 <= 1 秒 |
| `sync.pose_buffer_size` | int；100 | 2..10000 个样本 |
| `sync.preview_transform_timeout_seconds` | number；0.20 | 0 < 值 <= 5 秒 |
| `preprocess.sample_window_seconds` | number；1.0 | 正秒数，点云聚合窗口 |
| `preprocess.preview_transport` | string；pcd_fragment_http | 当前预览使用 HTTP PCD 分片描述符 |
| `preprocess.min_range_m` | number；0.30 | >=0，且小于 max_range_m |
| `preprocess.max_range_m` | number；100 | 正米数 |
| `preprocess.voxel_size_m` | number；0.05 | 正米数，体素下采样 |

### 8.2 生命周期与资源

| 键 | 类型 / 值 | 定义与约束 |
| --- | --- | --- |
| `timeouts.prepare_probe_timeout_seconds` | number；1.5 | 正秒数，输入预检 |
| `timeouts.integration_check_timeout_seconds` | number；8 | 正秒数，外部命令检查 |
| `timeouts.ready_timeout_seconds` | number；60 | 正秒数，准备状态有效窗口 |
| `timeouts.input_timeout_seconds` | number；3 | 正秒数，数据源超时 |
| `timeouts.command_cache_seconds` | number；60 | 正秒数，幂等命令缓存 |
| `timeouts.artifact_poll_seconds` | number；0.5 | 正秒数，成果稳定性轮询 |
| `timeouts.artifact_stable_polls` | int；2 | 2..100，连续稳定次数 |
| `limits.max_frame_points` | int；200000 | 正数，单帧点上限 |
| `limits.max_window_points` | int；1000000 | 不小于 max_frame_points |
| `limits.max_decompressed_bytes` | int；2400000 | 至少 max_frame_points × 12 |
| `limits.max_artifact_bytes` | int；4294967296 | 1024..17179869184 字节 |
| `limits.min_free_bytes` | int；5368709120 | 至少 1024 字节；Ground-Air 示例为 1073741824 |
| `limits.command_output_bytes` | int；16384 | 256..1048576，外部命令输出截取上限 |
| `limits.max_preview_fragment_bytes` | int；8388608 | 1024..1073741824，单预览文件大小 |
| `limits.max_pending_preview_fragments` | int；4 | 1..64，待处理队列上限 |
| `limits.max_unacked_preview_fragments` | int；16 | 1..256，未确认预览上限 |
| `artifacts.workspace_root` | string；按 profile | 可写会话工作目录，展开 ~ |
| `artifacts.accumulator_pcd_path` | string；按 profile | Go2 accumulator 原始输出路径 |
| `artifacts.source_pcd_path` | string；按 profile | 外部生成工具原始 PCD |
| `artifacts.source_pgm_path` | string；按 profile | 外部生成工具原始 PGM |
| `artifacts.source_yaml_path` | string；按 profile | 外部生成工具原始 YAML |
| `artifacts.archive_root` | string；按 profile | 外部成果归档根目录 |
| `artifacts.pcd_path` | string；{session_dir}/map.pcd | 会话内 PCD 模板 |
| `artifacts.pgm_path` | string；{session_dir}/map.pgm | 会话内 PGM 模板 |
| `artifacts.yaml_path` | string；{session_dir}/map.yaml | 会话内 YAML 模板 |
| `artifacts.frame` | string；默认 ros.frames.map | 最终成果 manifest frame，profile 常为 map |
| `deployment.state`、`deployment.enabled` | string / bool；可选 | 说明元数据，无启停作用 |

### 8.3 外部程序配置

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `integrations.backend` | enum；默认 go2_accumulator | go2_accumulator/scout_finalize/managed_finalize/ground_air_service |
| `integrations.mapping_prerequisites.setup_file` | string；必填 | prerequisites underlay setup.bash |
| `integrations.mapping_prerequisites.launch_file` | string；必填 | prerequisites launch 文件名 |
| `integrations.mapping_prerequisites.extrinsics_file` | string；必填 | 已标定外参文件 |
| `integrations.mapping_prerequisites.startup_timeout_seconds` | number；必填，示例 15 | 正秒数 |
| `integrations.fast_lio.setup_file` | string；必填 | 建图启动环境 |
| `integrations.fast_lio.package` | string；必填 | 外部建图 ROS 包 |
| `integrations.fast_lio.launch_file` | string；必填 | 外部建图 launch |
| `integrations.fast_lio.launch_args` | string[]；必填，可空 | 每元素一个 launch 参数模板，不是一条拼接 shell |
| `integrations.fast_lio.startup_timeout_seconds` | number；必填，示例 30 | 正秒数 |
| `integrations.fast_lio.stop_timeout_seconds` | number；必填，示例 30 | 正秒数，受管进程停止等待 |
| `integrations.fast_lio.pid_path` | string；必填 | 含 {session_dir} 的会话 PID 路径 |
| `integrations.fast_lio.log_path` | string；必填 | 含 {session_dir} 的会话日志路径 |
| `integrations.map_accumulator.setup_file` | string；必填 | 保存接口环境 |
| `integrations.map_accumulator.service` | string；必填 | 绝对 ROS 服务名；非 Go2 可为 profile 兼容占位值 |
| `integrations.map_accumulator.save_timeout_seconds` | number；必填，示例 60 | 0 < 值 <= 600 秒 |
| `integrations.pgm.setup_file` | string；必填 | 地图转换环境 |
| `integrations.pgm.package` | string；必填 | PGM 工具包 |
| `integrations.pgm.launch_file` | string；必填 | PGM launch，按后端保留占位值 |
| `integrations.pgm.launch_args` | string[]；必填，可空 | PGM launch 模板参数 |
| `integrations.pgm.generation_timeout_seconds` | number；必填，示例 300 | 正秒数；节点成果生成等待还包含 30 秒余量 |
| `integrations.pgm.log_path` | string；必填 | 含 {session_dir} 的转换日志 |

建图模板允许 map_id、device_id、session_id、session_dir、pcd_path、pgm_path、yaml_path、map_name；不允许未知模板字段。map_name 使用 YYYYMMDD_HHMMSS。PID、日志和会话成果必须留在 session_dir 内，不得通过 .. 越界。更改 backend 后既要调用 load_config，也要检查 build_integration_commands；专有参数部分在构造命令时才校验。

### 8.4 Scout 与 managed 后端专有项

下表每行列出两个完整键。scout_finalize 使用 integrations.scout，managed_finalize 使用 integrations.managed；另一个分组不参与该后端。除节点名默认值外，所选后端各项均需提供非空字符串，具体包名见对应 profile。

| Scout 键 / managed 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `integrations.scout.fast_lio_package` / `integrations.managed.fast_lio_package` | string；必填 | FAST-LIO 包 |
| `integrations.scout.fast_lio_launch` / `integrations.managed.fast_lio_launch` | string；必填 | FAST-LIO launch |
| `integrations.scout.mapper_package` / `integrations.managed.mapper_package` | string；必填 | 点云累积包 |
| `integrations.scout.mapper_launch` / `integrations.managed.mapper_launch` | string；必填 | 累积器 launch |
| `integrations.scout.tf_package` / `integrations.managed.tf_package` | string；必填 | TF 管理包 |
| `integrations.scout.tf_launch` / `integrations.managed.tf_launch` | string；必填 | TF launch |
| `integrations.scout.pose_package` / `integrations.managed.pose_package` | string；必填 | 位姿适配包 |
| `integrations.scout.pose_launch` / `integrations.managed.pose_launch` | string；必填 | 位姿适配 launch |
| `integrations.scout.finalize_package` / `integrations.managed.finalize_package` | string；必填 | 最终地图转换包 |
| `integrations.scout.finalize_executable` / `integrations.managed.finalize_executable` | string；必填 | rosrun 可执行文件，示例 finalize_map.py |
| `integrations.scout.filtered_pcd_filename` / `integrations.managed.filtered_pcd_filename` | string；必填 | 单一文件名，禁用 .、.. 和目录分隔符 |
| `integrations.scout.map_root` / `integrations.managed.map_root` | string；必填 | 地图工具可写根目录，按 map_name 建子目录 |
| `integrations.managed.fast_lio_node` | string；默认 /laserMapping | 生命周期检查的绝对 ROS 节点名 |
| `integrations.managed.mapper_node` | string；默认 /scout_pointcloud_mapper | Wheeltec 必须覆盖为其节点名 |
| `integrations.managed.tf_node` | string；默认 /scout_tf_manager | 同上 |
| `integrations.managed.geometry_tf_node` | string；默认 /scout_geometry_tf_publisher | 同上 |
| `integrations.managed.pose_node` | string；默认 /scout_pose_adapter | 同上 |

加载器也接受 integrations.scout 下同名的五个 *_node 可选键，但仅 managed_finalize 的命令使用可配置节点参数；Scout 脚本继续使用其既有节点约定，不应依赖这些键改名。

### 8.5 Ground-Air 后端专有项

仅 ground_air_service 使用以下分组。

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `integrations.ground_air.expected_nodes` | string[]；必填 | 非空绝对 ROS 节点名列表，含外部静态 TF owner |
| `integrations.ground_air.save_package` | string；必填 | 保存 launch 所属包，示例 car_bringup |
| `integrations.ground_air.save_launch` | string；必填 | 示例 save_mapping.launch |
| `integrations.ground_air.map_root` | string；必填 | 算法侧地图输出根目录 |
| `integrations.ground_air.saved_pcd_filename` | string；默认 cloud_map.pcd | 单一 PCD 文件名 |
| `integrations.ground_air.saved_pgm_filename` | string；默认 map.pgm | 单一 PGM 文件名 |
| `integrations.ground_air.saved_yaml_filename` | string；默认 map.yaml | 单一地图 YAML 文件名 |
| `integrations.ground_air.metadata_filename` | string；默认 metadata.json | 保存元数据文件名；上述文件名禁用目录分隔符、. 和 .. |

## 9. relocalization.yaml

network/storage/ros/tf_stability 结构必填。stages 仅接受程序可调用的包与 launch，不会自动安装外部算法。

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `schema_version` | int；必填 1 | 配置 schema |
| `protocol_id` | string；必填 ccs-relocalization-v1 | 与地面站一致 |
| `enabled` | bool；必填 | 本包实际读取的开关；Go2 为 false |
| `backend` | enum；必填 | scout_mini/wheeltec_r550p/ground_air_agv/go2_edu |
| `network.bind_host` | string；必填，示例 0.0.0.0 | 本机 UDP 绑定地址 |
| `network.control_port` | int；必填，示例 14565 | 1..65535 |
| `network.ground_station_ip` | string；必填 | 地面站控制来源与状态目标 |
| `network.status_port` | int；必填，示例 14566 | 1..65535 |
| `network.max_datagram_bytes` | int；必填，示例 1400 | 512..65507 |
| `storage.map_root` | string；必填 | 下载地图可写根目录，需与任务及遥测一致 |
| `storage.pcd_filename` | string；默认 public_map.pcd | 仅 public_map.pcd/cloud_map.pcd |
| `storage.active_map_state_file` | string；默认 ~/.ros/ccs_edge_dev/state/relocalization.json | 活动地图状态；Ground-Air 指向 CCS run 目录 |
| `storage.max_artifact_bytes` | int；必填，示例 4294967296 | 正字节数，下载归档上限 |
| `storage.download_timeout_seconds` | number；必填，示例 300 | 正秒数 |
| `ros.map_frame` | string；必填，示例 map | 定位地图坐标系 |
| `ros.odom_frame` | string；必填，示例 odom | 本地里程计坐标系 |
| `ros.initial_pose_topic` | string；必填，示例 /initialpose | 初始位姿输出 |
| `ros.map_topic` | string；必填 | 就绪判定地图话题，Scout 通常 /map_2d、AGV /map |
| `ros.startup_timeout_seconds` | number；必填，示例 60 | 正秒数，栈启动等待 |
| `ros.stages` | mapping[]；必填 | 按顺序启动；非 Go2 后端必须非空 |
| `ros.stages[].name` | string；必填 | 阶段标识 |
| `ros.stages[].package` | string；必填 | ROS 包名 |
| `ros.stages[].launch` | string；必填 | launch 文件名 |
| `ros.stages[].args` | string[]；默认 [] | 每元素一个 launch 参数，可用本节模板 |
| `ros.stages[].ros_package_path_prepend` | string；可选，无缺省覆盖 | 子进程 ROS_PACKAGE_PATH 前置目录，可用模板 |
| `ros.stages[].ros_package_path_exclude` | string[]；默认 [] | 从子进程环境排除指定绝对路径 |
| `ros.stages[].cmake_prefix_path_exclude` | string[]；默认 [] | 排除子进程 CMAKE_PREFIX_PATH 指定绝对路径 |
| `tf_stability.timeout_seconds` | number；必填，示例 30 | 正秒数；连续回报模式仅首样本受此超时约束 |
| `tf_stability.sample_hz` | number；必填，示例 10 | 正 Hz，稳定采样频率 |
| `tf_stability.sample_count` | int；必填，示例 10 | >=2，稳定窗口样本数 |
| `tf_stability.translation_tolerance_m` | number；必填，示例 0.10 | 正米数 |
| `tf_stability.yaw_tolerance_deg` | number；必填，示例 2 | 正角度数 |
| `tf_reporting.continuous` | bool；默认 false | Ground-Air profile=true，首样本后持续发送 |
| `tf_reporting.interval_seconds` | number；默认 1/sample_hz | 正秒数，Ground-Air=1 |
| `tf_reporting.persist_interval_seconds` | number；默认 30 | 正秒数，后续样本落盘间隔 |
| `deployment.state` | string；可选元数据 | 不改变功能开关 |

重定位阶段模板为 map_id、map_dir、map_root、map_pcd、map_yaml，与建图模板不同。路径排除是规范化后的绝对路径精确匹配，不是递归排除整个目录树。保留 Ground-Air profile 中 prepend/exclude 的配套配置，单独删除会重新暴露同名 underlay 包。

## 10. task_control.yaml

adapter 整段可省略，此时仅运行通用协调器；提供非空 adapter 时表内字段全部必填。其数值不设置隐式默认，示例来自公共配置。网络 bind 可用 0.0.0.0，其余身份/地面站 IP 使用有效 IPv4。

| 键 | 类型 / 默认或要求 | 定义与约束 |
| --- | --- | --- |
| `schema_version` | int；必填 2 | 配置 schema |
| `protocol_id` | string；必填 ccs-task-control-v2 | 当前协议 |
| `network.bind_host` | string；必填 | 本地 IPv4 绑定 |
| `network.control_port` | int；必填，示例 14563 | 1..65535 |
| `network.ground_station_ip` | string；必填 | 地面站 IPv4 |
| `network.status_port` | int；必填，示例 14564 | 1..65535 |
| `network.max_datagram_bytes` | int；必填，示例 1400 | 512..1400 |
| `storage.directory` | string；必填，示例 ~/ccs_edge_ws/mission | XML 和任务持久化目录，展开 ~ |
| `ros.command_topic` | string；必填 | command 发布话题 |
| `ros.feedback_topic` | string；必填 | feedback 订阅话题 |
| `ros.status_topic` | string；必填 | String 摘要状态发布话题 |
| `ros.map_frame` | string；必填，示例 map | 任务目标坐标系 |
| `timeouts.ack_cache_seconds` | number；必填，示例 60 | >=1 秒，ACK 幂等缓存 |
| `timeouts.transfer_seconds` | number；必填，示例 10 | >=0.1 秒，任务传输超时 |
| `timeouts.adapter_feedback_seconds` | number；必填，示例 2 | >=0.1 秒，适配器反馈阈值 |
| `timeouts.execution_feedback_seconds` | number；必填，示例 5 | >=0.1 秒，执行反馈阈值 |
| `timeouts.preparation_retry_seconds` | number；必填，示例 5 | >=0.5 秒，准备重试 |
| `timeouts.utc_tolerance_seconds` | number；必填，示例 2 | >=0.01 秒，UTC 误差容忍 |
| `limits.max_waypoints` | int；必填，示例 500 | 2..500 |
| `limits.max_compressed_bytes` | int；必填，示例 1048576 | >=1024 字节 |
| `limits.max_raw_bytes` | int；必填，示例 8388608 | >=1024 且不小于 max_compressed_bytes |
| `limits.max_chunks` | int；必填，示例 2048 | 1..4096 |
| `adapter.active_map_state_file` | string；条件必填 | 与重定位活动地图状态一致 |
| `adapter.navigation_launch_package` | string；条件必填 | 导航 ROS 包 |
| `adapter.navigation_launch_file` | string；条件必填 | 导航 launch |
| `adapter.navigation_map_root` | string；条件必填 | 与重定位下载根目录一致 |
| `adapter.navigation_map_yaml` | string；条件必填，示例 map.yaml | 地图栅格描述文件名 |
| `adapter.navigation_action` | string；条件必填，示例 /move_base | MoveBaseAction 服务端命名空间 |
| `adapter.odom_topic` | string；条件必填 | nav_msgs/Odometry 输入 |
| `adapter.zero_velocity_topic` | string；条件必填，示例 /cmd_vel | geometry_msgs/Twist 停车话题 |
| `adapter.navigation_startup_timeout_seconds` | number；条件必填，示例 25 | 正秒数 |
| `adapter.waypoint_timeout_seconds` | number；条件必填，示例 300 | 正秒数，单航点执行 |
| `adapter.pose_timeout_seconds` | number；条件必填，示例 2 | 正秒数，位姿新鲜度 |
| `adapter.zero_velocity_hz` | number；条件必填，示例 20 | 正 Hz，停车消息频率 |
| `adapter.zero_velocity_count` | int；条件必填，示例 10 | >=1，停车消息次数 |
| `deployment.state`、`deployment.enabled` | string / bool；可选 | 说明元数据，不启动任务节点 |

使用 YAML 的 true/false 和真正的数字，不要写字符串 "false" 或 "2.0"。各包校验强度不同，不能依赖类型强制转换来修正配置。

## 11. launch 参数与脚本环境变量

### 11.1 单包 launch

文件路径参数均为 string，建议使用绝对路径。默认共享目录为 `$(find epgeneral_device_config)/config`。参数在启动时求值，重启才生效。

| launch / 参数 | 默认或要求 | 作用 |
| --- | --- | --- |
| 所有业务主 launch：device_config_file | 共享目录/device.yaml | 唯一身份配置 |
| epgeneral_mqtav.launch：config_file | 共享目录/epgeneral_mqtav.yaml | MQTT 配置 |
| epgeneral_mqtav.launch：log_dir | HOME/.ros/log/epgeneral_mqtav | 耐久日志目录 |
| epgeneral_udp_telemetry.launch：telemetry_config_file | 共享目录/udp_telemetry.yaml | 遥测配置 |
| epgeneral_udp_telemetry.launch：destination_host | 192.168.151.100 | 总会覆盖 YAML 的同项；现场必须显式传值 |
| epgeneral_udp_telemetry.launch：destination_port | 14560 | 同上，端口覆盖 |
| epgeneral_udp_telemetry.launch：link_status_topic | /epgeneral_udp_telemetry/link/udp_tx | Bool 发布名 |
| epgeneral_udp_telemetry.launch：diagnostics_topic | /epgeneral_udp_telemetry/diagnostics | DiagnosticArray 发布名 |
| epgeneral_video_srt.launch / epgeneral_realsense_d435i_srt.launch：video_config_file | 共享目录/video.yaml | 视频配置；后者不会自动安装相机驱动 |
| epgeneral_map_stream.launch：mapping_config_file | 共享目录/map_stream.yaml | 建图配置 |
| mapping_prerequisites.launch：extrinsics_file | Go2 calibration/go2_edu_02/extrinsics.yaml 绝对路径 | Go2 prerequisites 的外参文件，不通用于其他 profile |
| epgeneral_relocalization.launch：config_file | 共享目录/relocalization.yaml | 重定位配置 |
| epgeneral_relocalization.launch：log_dir | 空字符串 | 空时由节点选默认日志目录 |
| relocalization_map_server.launch：map_yaml | 必填 | map_server 读取的栅格描述文件 |
| epgeneral_task_control.launch / scout_task_control.launch / navigation_task_control.launch：task_config_file | 共享目录/task_control.yaml | 后两者同时包含导航适配器，只选择一个入口 |
| ground_air_control 的 relocalization_control.launch：map_id | 必填 | 当前地图 ID |
| 同上：maps_root | /home/bitcq/ccs_edge_ws/maps/download | 下载地图根目录 |
| 同上：service_wait_timeout / relocalize_timeout | 90.0 / 60.0 | 正秒数，外部服务等待/重定位请求超时 |

设备 bringup 的 profile_dir 默认相对 launch 位置，不同安装布局下应显式传绝对路径。Go2 的 enable_task_control 默认 false，仅该 launch 生效，不会启用一键脚本中的任务。Wheeltec 的 enable_video 默认 false。三种 bringup 的 ground_station_ip 默认 192.168.50.101，主要传给 UDP，不会改写 MQTT 等 YAML。

Ground-Air 设备适配 launch 还提供：manual_mapping_control/relocalization_control 的 map_id（必填）、maps_root（默认 /home/bitcq/catkin_ws/maps）、service_wait_timeout（90 秒）及重定位的 relocalize_timeout（60 秒）；override 的 relocalization_system 使用 map_id 和两个超时。mapping_coordinate_transforms 的 odom_frame/camera_init_frame/body_frame/base_frame 默认 odom/camera_init/body/base_link。mavros_base 的 fcu_url 默认串口 by-id 路径加 :57600，gcs_url 默认空；livox_mid360_base 的 msg_frame_id 默认 base_link。

### 11.2 一键脚本环境

下表为启动前 export 的字符串变量；时间、波特率仍需满足其使用程序的要求。不设置时采用表内缺省。这些变量不改变运行 YAML 内容。

| 环境变量 | 适用 profile / 默认 | 含义 |
| --- | --- | --- |
| `CCS_EDGE_WORKSPACE` | 全部；见 README 工作空间表 | CCS 工作空间根目录 |
| `CCS_EDGE_PROFILE_CONFIG_DIR` | 全部；工作空间/config/profile | 运行 YAML 目录；Go2 未设时先尝试脚本旁 config/device.yaml |
| `CCS_ROS_IP` | Go2 .100、Scout .120、Wheeltec .122、AGV .130；前缀 192.168.50 | ROS 本机地址 |
| `CCS_GROUND_STATION_IP` | Go2/Scout/Wheeltec；192.168.50.101 | 授时默认目标及 UDP 覆盖；Ground-Air 脚本不提供此变量 |
| `CCS_NTP_SERVER` | 前三者默认地面站变量；AGV 默认 192.168.50.101 | 预检要求的授时服务器 |
| `CCS_GO2_NAV_SETUP` | Go2；/home/nvidia/go2_mid360_nav/catkin_ws/devel/setup.bash | 算法 underlay |
| `CCS_LIVOX_SETUP` | Scout /home/nvidia/livox_fastlio/devel/setup.bash；Wheeltec /home/nrc19/livox_fastlio/devel/setup.bash | 雷达与算法环境 |
| `CCS_REALSENSE_SETUP` | Scout；/home/nvidia/realsense_ws/devel/setup.bash | 相机环境 |
| `CCS_NAVIGATION_SETUP` | Scout；/home/nvidia/github_upload/AADCL_UAV_UGV/Scout_mini/devel/setup.bash | 导航环境 |
| `CCS_DEVICE_UNDERLAY_SETUP` | AGV；/home/bitcq/catkin_ws/devel/setup.bash | Ground-Air 算法环境 |
| `CCS_EDGE_STATE_DIR` | Go2 ~/.ros/ccs_edge_dev；Scout/Wheeltec 加 _scout_mini / _wheeltec_r550p | 脚本 PID/log 根目录，不自动改变 YAML 内状态文件 |
| `CCS_FCU_DEVICE` | AGV；/dev/serial/by-id/usb-CUAV_PX4_CUAV_Nora_0-if00 | 飞控串口设备 |
| `CCS_FCU_BAUD` | AGV；57600 | 飞控串口波特率 |
| `CCS_EDGE_LAUNCH_DIR` | AGV；工作空间/launch | 已安装的设备适配 launch |
| `CCS_EDGE_LOG_DIR` | AGV；工作空间/log/ground_air_agv | 组件日志目录 |
| `CCS_ROS_HOME` | AGV；工作空间/run/ros_home | ROS 主目录，实际以脚本 PID_DIR 为前缀 |
| `CCS_ROS_LOG_DIR` | AGV；组件日志目录/ros | ROS 日志目录 |

### 11.3 timesyncd-ccs.conf

四套配置均包含 [Time]：`NTP=192.168.50.101` 为主授时地址，`FallbackNTP=` 清空回退列表，`RootDistanceMaxSec=5` 为最大根距离秒数，`PollIntervalMinSec=16`、`PollIntervalMaxSec=64` 为轮询间隔秒数。安装到 /etc/systemd/timesyncd.conf.d/ccs.conf 后重启 systemd-timesyncd，并用 timedatectl timesync-status 核实真实 ServerAddress 与同步状态。端口为 UDP 123。

不要同时引入相互争用的授时服务；已有 chrony 的设备应按现场管理方式配置等价授时并核对一键脚本的预检要求。授时失败会阻止脚本启动新 ROS 组件。

## 12. 联调检查清单

~~~bash
rospack find epgeneral_device_config
rospack find epgeneral_task_control
rostopic type /initialpose
rosmsg show epgeneral_task_control/TaskExecutionCommand
rosmsg show epgeneral_task_control/TaskExecutionFeedback
rosservice type /ground_air/system/set_stage
rossrv show ground_air_msgs/SetSystemStage
rosrun tf tf_echo map odom
ss -lntup
~~~

按设备能力选用上述命令；非 Ground-Air 不要求存在阶段服务。首次接入外部包先核实实际消息类型、字段、frame、到达频率和 launch 参数，再更新 profile。通信通过不等于算法或执行器已验收；设备运动前按使用手册确认任务适配与现场操作条件。
