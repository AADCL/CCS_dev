# 端侧设备交互接口总册

文档版本：`v0.21.1`，更新日期：2026-08-26。

## v0.19.2 地面站初始位姿选择器修复

- 本次只修复地面站十字标线叠层和初始位姿平面反投影，`ccs-relocalization-v1` 消息、端口、字段和状态转换均未变化。
- Scout 使用 `epgeneral_relocalization` v0.2.2；重启后旧 `localized` 状态自动失效，必须重新建立实时定位。

## v0.19.1 重复重定位一致性

- `ccs-relocalization-v1` 的消息类型和 schema 不变；`start_stack.payload.replace_existing` 为可选布尔值，缺省 false。
- 活动地图状态文件 schema 2 保存 `map_id/status`，仅 localized 状态增加 `map_frame/odom_frame/localized_at/map_from_odom`；schema 1 仍可读取。
- Scout 替换流程先清除旧 TF、取消旧监测并停止旧进程，再重新计算。成功结果先在端侧原子提交，随后由地面站覆盖同地图绑定。
- Go2 同步维护 schema 2 并清除历史 TF，但 `start_stack` 和 `initial_pose` 继续返回 `UNSUPPORTED_BACKEND`。

## v0.19.0 设备详情遥测语义

- Scout `vision_pose=/scout/odom`（frame `odom`），`fastlio2=/Odometry`（实测 frame `camera_init`），`imu=/livox/imu`；Map 位姿由持久化 `map <- odom` 在地面站组合。
- Go2 `global_pose` 与 `fastlio2` 均来自 `/qrd/QRD_001/odometry`，IMU 来自 `/qrd/QRD_001/imu`。
- `pgm_mapping` 携带 `map_id`，端侧检查 `<map_root>/<map_id>/map.pgm` 普通文件；地面站仅接受与设备 `active_map_id` 一致的状态。
- UDP envelope 仍为 schema 1 / `ccs-udp-telemetry-v1`；地面站只额外接受配置中明确列出的 descriptor hash。
- Scout 30 V 定义为满电，曲线未标定时百分比为 `null`；Go2 原生百分比不被估算覆盖。

指控平台 v0.19.2 配套 `epgeneral_udp_telemetry` v0.3.0、`epgeneral_relocalization` v0.2.1 和 `epgeneral_map_stream` v0.10.0。MQTT schema 1.0、SRT 与 UDP 遥测 wire schema 保持兼容。

本文件是地面站与端侧软件之间的接口基线。以后每次代码更新都必须核对并同步本文件。所有接口默认运行于可信局域网，不提供认证、加密、可靠重传或拥塞控制。

## 接口总览与兼容性

| 通道 | 方向 | 地址/端口 | 协议版本 | 当前端侧实现 |
| --- | --- | --- | --- | --- |
| MQTT 摘要状态 | 端侧 -> 地面站 | TCP 1883，`mqtav/...` | JSON schema `1.0` | epgeneral_mqtav v0.3.1 |
| UDP 高频遥测 | 端侧 -> 地面站 | UDP 14560 | `ccs-udp-telemetry-v1` | epgeneral_udp_telemetry v0.3.0 |
| SRT 视频 | 地面站 Caller -> 端侧 Listener | UDP 9000 | baseline H.264/MPEG-TS/SRT | epgeneral_video_srt v0.1.0 |
| UDP 实时建图控制 | 地面站 -> 端侧 | UDP 14561 | `ccs-map-stream-v1` | 保留后端 |
| UDP 实时建图数据 | 端侧 -> 地面站 | UDP 14562 | `ccs-map-stream-v1` | 保留后端 |
| UDP 遥控建图 v2 | 双向 | UDP 14561/14562 + 端侧 TCP 14600 | `ccs-map-stream-v2` | epgeneral_map_stream v0.9.1 |
| UDP 任务控制 | 地面站 -> 端侧 | UDP 14563 | `ccs-task-control-v2` | epgeneral_task_control v0.3.1 |
| UDP 任务状态 | 端侧 -> 地面站 | UDP 14564 | `ccs-task-control-v2` | epgeneral_task_control v0.3.1 |

端侧身份由 `edge_side_pkg/EPGeneral_device_config/config/device.yaml` 提供，`device.id` 和 `device.ip` 必须与地面站 `config/devices.json` 完全一致。MQTT、遥测、建图和任务协议中的 `device_id` 均使用该 ID。

地面站允许编辑设备 ID，并级联本地当前地图和未归档任务引用，但不会修改端侧配置或历史执行记录。修改后必须同步更新端侧 `device.yaml` 并重启相关节点，否则旧 ID 的 MQTT/UDP 数据会被视为未登记设备。设备页面显示的“运行模式”仍来自 MQTT `health.flight_mode`；`armed` 和 `system_status` 字段继续接收并保持 wire contract，仅不再显示在设备完整信息区。

## MQTT presence、heartbeat、status

### Go2 EDU ROS 状态桥接

`epqrd_go2_bridge` v0.2.0 从 Unitree SDK2 `rt/lowstate` 和 `rt/sportmodestate` 读取只读状态。除原有 `/qrd/QRD_001/battery`、`imu`、`odometry`、`robot_mode`、`link/sdk`、`heartbeat` 和 `diagnostics` 外，新增 `low_state/*` 与 `sport_mode/*` 十二个强类型语义话题，完整覆盖两个 SDK 状态的全部字段。字段、类型、数组顺序及订阅示例见包内 `docs/ROS_TOPIC_INTERFACES.md`。`epgeneral_mqtav` 将 SDK 链路新鲜度映射为既有 `fcu_connected` 字段，不增加 MQTT schema 字段；`epgeneral_udp_telemetry` 从 prefixed Odometry 和 Imu 继续生成 `ccs-udp-telemetry-v1` 数据。

`/qrd/QRD_001/link/udp_tx` 仅表示本机 UDP socket 最近一次发送成功。端到端 UDP 在线状态仍由地面站的 heartbeat 接收超时判断。任务协调包在 `/qrd/QRD_001/task_status` 发布 latched `std_msgs/String`，供 MQTT mission 状态订阅。

Broker 由地面站监听 `0.0.0.0:1883`，QoS 1，无认证/TLS。设备 ID 为 `UAV_001` 时主题如下：

| 消息 | 主题 | retained | 建议频率 |
| --- | --- | --- | --- |
| presence | `mqtav/UAV_001/presence` | 是 | 连接/断开时 |
| heartbeat | `mqtav/UAV_001/heartbeat` | 否 | 1 Hz |
| status | `mqtav/UAV_001/status` | 否 | 1 Hz |

公共 JSON 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 固定 `1.0` |
| `message_type` | string | `presence`、`heartbeat` 或 `status`，必须与主题一致 |
| `timestamp` | string | 带时区 ISO 8601 |
| `session_id` | string | 可选；端侧进程每次启动生成新 UUID，presence/heartbeat/status 共用 |
| `sequence` | integer | heartbeat/status 非负递增序列；presence 可省略 |
| `device.id` | string | 必须与主题 ID 和地面站配置一致 |
| `device.ip` | string | 必须与静态设备 IP 一致，不一致只记录告警，不自动修改 |

presence 额外包含 `status: online|offline`。连接成功发布 retained online；Last Will 和正常退出发布 retained offline。heartbeat 不增加业务 payload。

status 示例：

```json
{
  "schema_version": "1.0",
  "message_type": "status",
  "timestamp": "2026-08-05T10:20:30.000Z",
  "session_id": "a4d3f6f50b2d4ec5a85ca91d75e2c033",
  "sequence": 42,
  "device": {"id": "UAV_001", "ip": "192.168.151.250"},
  "health": {
    "fcu_connected": true,
    "armed": false,
    "system_status": 3,
    "flight_mode": "AUTO.MISSION",
    "battery": {"percentage": 76.5, "voltage": 15.8, "current": 4.2},
    "mission_status": "active"
  }
}
```

地面站收到合法 heartbeat 立即设 MQTT 在线；2 秒未收到进入 warning，5 秒进入 offline/error。sequence 按设备和消息类型校验；`session_id` 改变时清空上一进程的序列窗口。旧端侧未提供 session 时，新的 online presence 作为计数器重置边界。QoS 1 同 sequence 重复投递会静默忽略，小于当前值的帧才视为乱序。`fcu_connected=true` 映射健康，false 映射需关注。任务别名：running/active/executing -> 执行中，idle/standby -> 待机，paused -> 暂停，done/succeeded/completed -> 完成，其余未知。

## UDP 14560 高频遥测

端侧向地面站 `config/udp_telemetry.json` 的地址发送 MessagePack。最大数据报 16 KiB。所有包使用以下信封：

Go2 EDU 必须使用 `deploy/go2_edu/config/device.yaml` 与 `udp_telemetry.yaml` 启动节点，并通过
`ground_station_ip` 或 `CCS_GROUND_STATION_IP` 指向当前地面站。若 launch 回退到功能包通用配置，
报文会携带错误设备 ID，且订阅不到 `/qrd/<device_id>/...` 位姿和 IMU 话题；仅有 UDP `sendto`
成功并不能证明平台能够显示数据。

```python
{
  "schema_version": 1,
  "protocol_id": "ccs-udp-telemetry-v1",
  "descriptor_hash": "<规范化 descriptors 的 SHA-256>",
  "device_id": "UAV_001",
  "session_id": "<端侧进程启动会话>",
  "message_type": "heartbeat",  # 或 telemetry
  "sequence": 123,
  "sent_at_ns": 1785900000000000000,
  "level": None,                 # telemetry 为 1/2/3
  "payload": {}
}
```

heartbeat 为 1 Hz，`level=None` 且 payload 为空。合法心跳使 UDP 链路在线；2 秒 warning，5 秒 offline。telemetry 的 payload 键必须存在于两端一致的 descriptor，等级固定为 1/2/3，对应 20/5/1 Hz。地面站把报文 ID 规范化为设备配置中的 ID；发现新 session 后退役旧 session，旧 session 的延迟数据不得切回当前高频快照。

### Descriptor 与 payload

| name | type | level | payload |
| --- | --- | --- | --- |
| `global_pose` | pose | 1 | `valid,x,y,z,roll,pitch,yaw,sample_age_seconds` |
| `vision_pose` | pose | 1 | 同 pose |
| `imu` | imu | 1 | `valid,roll,pitch,yaw,angular_velocity_x/y/z,linear_acceleration_x/y/z,sample_age_seconds` |
| `livox_pointcloud` | pointcloud_status | 2 | `valid,status,estimated_hz,sample_age_seconds` |
| `livox_driver` | availability | 3 | `valid,status,sample_age_seconds` |
| `fastlio2` | availability | 3 | 同 availability |
| `pgm_mapping` | availability | 3 | 同 availability |
| `octomap_mapping` | availability | 3 | 同 availability |
| `occupancy_grid_mapping` | availability | 3 | 同 availability |
| `mapping_mode` | text_status | 3 | availability 字段加 `value`，最长 128 字符 |

`status` 只允许 `available`、`unavailable`、`unknown`。所有浮点数必须有限。点云内容禁止放入 14560，只发送接收元数据。配置对应关系：地面站 `config/udp_telemetry.json` 定义公共名称/类型/等级；端侧 `epgeneral_udp_telemetry/config/telemetry.yaml` 额外定义 ROS topic、message type、字段路径和超时。公共 descriptor 不一致将导致哈希拒收。

端侧在 Pose/IMU 进入平滑窗口前拒绝非有限数值、非数值字段和无效四元数。单个 descriptor 失败时只发送该项 `valid=false`，同一 Level 1 报文的其他有效项继续发送；地面站仍对最终报文执行严格有限数值校验。

`/epgeneral_udp_telemetry/diagnostics` 保留 `epgeneral_udp_telemetry/udp_tx` 状态，增加各等级的发送数、失败数、字节数和下一序列；每个 descriptor 另发布 `epgeneral_udp_telemetry/source/<name>`，包含 topic、message type、level、`received_count`、`accepted_count`、`rejected_count`、`last_sample_age_seconds` 和 `last_rejection_reason`。`sendto` 成功只证明本机 socket 调用成功，端到端在线仍以地面站 heartbeat 为准。

联调时依次检查：

```bash
rostopic hz /mavros/local_position/pose
rostopic echo /epgeneral_udp_telemetry/diagnostics
sudo tcpdump -ni any udp port 14560
```

若 ROS 话题有数据但详情页仍为 `--`，先确认对应 source 的 `accepted_count` 是否增长、`last_rejection_reason` 是否报告非法数值或映射错误，再检查地面站日志中的 descriptor hash、非有限数值、未知设备、旧 session 和乱序累计告警。

## SRT 视频

端侧 `epgeneral_video_srt` 直接订阅 YAML 指定的 `sensor_msgs/Image` 或 `sensor_msgs/CompressedImage`，缩放后编码为 baseline H.264 并封装 MPEG-TS。`mpegtsmux alignment=7` 生成 1316 字节对齐输出，`srtsink` 固定为 Listener，默认监听 `0.0.0.0:9000/UDP`，延迟 120 ms。

地面站从设备 profile 读取 IP、`srt_port` 和 `srt_latency_ms`，作为 Caller 构造：

```text
srt://<设备IP>:<端口>?mode=caller&transtype=live&latency=<延迟毫秒×1000>
```

IPv6 地址使用方括号。地面站先执行 `ffmpeg -hide_banner -protocols` 并要求 Input 列表含 `srt`，再通过 `QProcess` 解复用 MPEG-TS、解码 H.264 并输出 640×480 RGBA rawvideo。FFmpeg 意外退出每隔 1 秒重试，最多 3 次；关闭开关、切换设备/页面或退出立即终止进程并取消重试。视频无音频、录像、认证或加密，视频故障不改变 MQTT/UDP 状态。

端侧和地面站的延迟配置均以毫秒保存；SRT URL 的 `latency` 查询参数使用微秒，因此地面站乘以 1000。端侧应开放 UDP 9000，并通过 `gst-inspect-1.0 srtsink` 检查插件。系统 FFmpeg 必须由用户安装且带 libsrt。

## UDP 14561/14562 单机遥控建图 v2（指控平台 v0.18.3）

v2 使用独立 `schema_version=2` 和 `protocol_id=ccs-map-stream-v2`，不与 v1 自动回退。端侧 `epgeneral_map_stream v0.9.1` 协调 Livox、FAST_LIO、map accumulator、坐标转换链和 PGM 生成器；最终 PCD、PGM 和 YAML 均由端侧成果 ZIP 提供。

v2 保留 v1 信封中的 `map_id/device_id/session_id/message_type/sequence/sent_at_ns/payload`。v0.18.3 使用 `cloud_fragment_ready` 和 `cloud_fragment_ack`：UDP 只承载控制、状态与轻量描述符，PCD 内容通过 TCP 14600 下载。端侧未收到 ACK 时最多重发描述符 3 次，未确认文件和后台队列均有硬上限。

`prepare_mapping` 下发 `request_id`、`return_host`、`return_port` 及 `required_inputs=[pointcloud,imu,artifact_storage,map_generation]`。重新协商额外携带 `restart_active=true`。`pointcloud` 检查原始 `/livox/lidar` 的类型、新鲜度、frame 和字段；`imu` 只检查 `/livox/imu` 类型并等待一条新数据，不校验消息字段完整性。端侧必须返回：

```python
{
  "request_id": "...", "accepted": True,
  "checks": [{"name": "pointcloud", "available": True, "reason": ""}],
  "sample_window_seconds": 1.0,
  "frame_id": "odom",
  "capability_version": "0.9.1",
  "preview_transport": "pcd_fragment_http",
  "fragment_interval_seconds": 1.0,
  "restarted": False, "previous_state": "", "active_session_id": "",
  "error_code": "", "reason": ""
}
```

`accepted` 必须等于所有 checks 的逻辑与。`start_mapping` 带 `coordinate_contract=sensor+map_body+body_sensor`、`preview_transport=pcd_fragment_http` 和 1 秒周期。每个 `cloud_fragment_ready` 包含 `fragment_id/url/byte_count/sha256/point_count/frame_id/source_frame_id/display_from_source/started_at_ns/ended_at_ns/expires_at`。其中 `frame_id=odom`、`source_frame_id=lio_odom`，`display_from_source` 为端侧实际用于点变换的 `{x,y,z,qx,qy,qz,qw}`。平台限制 URL 主机为设备 IP，校验坐标契约、字节数、SHA-256 和二进制 XYZ PCD 后以 `odom` 增量显示，再以 request ID 和 fragment ID 确认。

实时预览坐标生命周期为 `lio_odom --(odom <- lio_odom TF)--> odom`。端侧以点云窗口最后一帧时间戳查询 TF，将窗口统一到 `lio_odom` 后再实际变换全部点坐标，禁止只修改 PCD 的 frame 标签。FAST_LIO 最终成果 ZIP 的 manifest 仍声明 `frame_id=lio_odom`；平台完整校验并提交后，才将成果本地基准定义为 `map`。

FAST_LIO 点云和里程计按 header 时间戳在 50 ms 窗口内匹配。点云回调先到时端侧最多缓存 3 帧等待对应位姿，不得直接匹配约 100 ms 前的上一帧位姿；位姿时间已越过窗口或缓存溢出时才丢帧，并记录原因和时间差。

指控端 prepare 命令截止时间为 10 秒，start 为 45 秒，活动会话清理及重新协商为 45 秒。UDP 重试次数耗尽只停止发送，不提前结束等待；截止时间内到达且 request/session 匹配的 ACK 仍有效。端侧对同一 request ID 返回缓存结果，保证重试幂等。

完整点云连续 10 秒中断时，指控端进入警告但保留会话；只有连续 30 秒无完整帧且端侧心跳也中断超过 5 秒才判定失败。`abort_mapping` 可在 ready/starting/mapping/error 阶段强制结束当前 session，端侧注销订阅、清空点云/位姿/补包缓存并执行 `abort_fast_lio.sh`，ACK 后回到 standby，不生成 PCD/PGM/YAML。stopping/generating/serving 阶段拒绝强制结束以保护成果事务。

当 `restart_active=true` 且 session 一致时，端侧在 ready/starting/mapping/error 状态报告旧 session 和状态，注销 ROS 订阅、清空缓存、使用 `abort_fast_lio.sh` 停止进程组并删除 PID，删除未提交临时成果后重新执行准备检查。该路径不调用正常 stop，因而不生成 PCD/PGM。stopping/generating/serving 属于成果事务阶段，端侧必须拒绝强制重启并返回当前状态及不可中断原因；不同 session 始终返回 `BUSY`。

`stop_mapping` ACK 只表示开始生成成果。端侧以 `artifact_status.state=generating|ready|error` 报告进度；ready payload 必须包含：

```python
{
  "state": "ready",
  "url": "http://<设备IP>:<端口>/mapping/result.zip?token=<短期令牌>",
  "byte_count": 123456,
  "sha256": "<64 hex>",
  "expires_at": "2026-08-20T10:10:00+00:00"
}
```

指控平台只允许 URL 主机等于设备 IP 的明文 HTTP，禁止重定向，并使用 Range 续传。端侧默认在 TCP 14600 提供固定路径 `/mapping/result.zip?token=<短期令牌>`，令牌有效期默认 15 分钟。ZIP 必须且只能包含 `manifest.json` 及清单声明的一个 PCD、一个 PGM、一个 ROS YAML。清单 schema 1 包含 `map_id/device_id/session_id/frame_id/generated_at`，以及 `files.pcd/pgm/yaml` 的 `path/byte_count/sha256`。路径穿越、符号链接、重复或未声明文件、异常压缩比和任何校验不匹配均会拒绝整个成果。

端侧配置 schema 6 增加 `integrations.map_accumulator` 和 `artifacts.accumulator_pcd_path`。建图启动链先启动 `go2_map_accumulator/map_accumulator.launch` 并等待 `/go2_map_accumulator` 节点；停止建图时再调用 `/go2_map_accumulator/save`，确认 `/home/nvidia/go2_mid360_nav/maps/current/public_map.pcd` 非空、晚于 session 启动且指纹已变化，再停止 FAST_LIO/转换进程并继续 PGM/YAML/ZIP。服务失败、超时或旧文件校验失败统一 abort，不发布成果。

端侧必须将命令接收、request/session ID、状态转换、FAST_LIO 启停、源 PCD 基线与最终指纹、PCD 分片发布/确认/背压、子进程输出和错误同时写入 ROS 日志与 `~/.ros/ccs_edge_dev/log/map_stream.log`。指控端每个 session 保留最近 200 条 TX/RX/LOCAL 日志。

## UDP 14561/14562 实时建图 v1（保留后端）

此协议独立于 UDP 14560。地面站绑定 14562，并使用该 socket 向所选设备 IP 的 14561 发送控制指令。端侧必须把上行数据发回 `start_mapping.return_host:return_port`，并保持 source IP 与 `devices.json` 一致。

### 公共 MessagePack 信封

```python
{
  "schema_version": 1,
  "protocol_id": "ccs-map-stream-v1",
  "map_id": "<稳定地图 UUID>",
  "device_id": "UAV_001",
  "session_id": "<地面站生成的 32 位十六进制 ID>",
  "message_type": "start_mapping",
  "sequence": 0,
  "sent_at_ns": 1785900000000000000,
  "payload": {}
}
```

字符串标识不能为空且最长 128 字符，sequence 和 sent_at_ns 为非负整数。单个编码后数据报不得超过 1400 字节。端侧对同一 `request_id` 的重试必须幂等：重复 start 不得创建第二会话，重复 stop 不得重复释放资源。

### 下行 start_mapping

```python
{
  "request_id": "<UUID>",
  "return_host": "192.168.151.100",
  "return_port": 14562,
  "cloud_rate_hz": 5.0,
  "voxel_size_m": 0.10,
  "compression": "zlib",
  "point_format": "xyz_f32_le",
  "coordinate_contract": "sensor+map_body+body_sensor",
  "job_id": "<可选：地面站联合建图任务 ID>",
  "role": "primary",
  "primary_device_id": "UAV_001"
}
```

地面站每 500 ms 重发，最多 5 次。端侧不得协商高于请求值的点云频率、点数或包长；可以在 ACK 中用 `actual_parameters` 报告更低速率/更大体素。端侧开始 ROS 订阅、同步和发送后返回 accepted ACK。

`job_id`、`role` 和 `primary_device_id` 为 v0.11.0 的可选兼容字段。`role` 只允许 `primary|secondary`。同一联合任务中每台设备仍使用独立 `session_id`、sequence、frame ID 和 ACK 重试；端侧不得把不同设备的数据合并到一个会话。旧端侧可以忽略这些字段。

### 多设备会话与外参

- 地面站必须等待参与设备全部确认 start，才将逻辑任务标记为建图中。
- 每台设备继续上传自身局部地图坐标中的 `map <- body` 和 `body <- sensor`；端侧不负责跨设备外参融合。
- 用户录入的外参方向固定为 `primary_map <- secondary_map`，XYZ 单位为米，roll/pitch/yaw 单位为度。
- 地面站先复原各设备局部点云，再使用外参转换到主坐标系。实时预览使用内置体素融合，停止后由选定地面站插件生成正式 PCD。
- 从设备超过 5 秒无完整帧后进入降级态，由用户决定剔除或中止。主设备失效时不得自动切换主坐标系。
- 端侧后续若实现联合任务显示，只需读取可选字段；不得更改 `cloud_chunk`、zlib、CRC32、XYZ float32 或同步位姿契约。

### 下行 stop_mapping

```python
{"request_id": "<UUID>", "reason": "用户结束建图"}
```

端侧停止新增帧、完成或丢弃当前不完整帧、释放订阅/缓存并返回 ACK，随后可发送 `session_status: stopped`。地面站在 ACK 后保存；ACK 超时后也会保存已经完整接收的数据。

### 上行 command_ack

```python
{
  "request_id": "<原请求 ID>",
  "command": "start_mapping",
  "accepted": true,
  "reason": "",
  "error_code": null,
  "actual_parameters": {"cloud_rate_hz": 5.0, "voxel_size_m": 0.10}
}
```

`command` 只能是 start_mapping/stop_mapping，accepted 必须为布尔值。拒绝时必须给 reason，建议错误码：`BUSY`、`INVALID_CONFIG`、`MAP_ID_MISMATCH`、`DEVICE_ID_MISMATCH`、`SENSOR_UNAVAILABLE`、`POSE_UNAVAILABLE`、`UNSUPPORTED_FORMAT`、`INTERNAL_ERROR`。

### 上行 session_heartbeat 与 session_status

heartbeat 以 1 Hz 发送：

```python
{"state": "mapping"}
```

status 在状态变化时发送：

```python
{"state": "error", "reason": "point cloud topic timed out", "error_code": "SENSOR_UNAVAILABLE"}
```

state 只允许 `starting`、`mapping`、`stopping`、`stopped`、`error`。端侧主动错误会中断会话并保留地面站已有临时结果。

### 上行 cloud_chunk

```python
{
  "frame_id": 81,
  "chunk_count": 12,
  "chunk_index": 3,
  "frame_crc32": 305419896,
  "sample_stamp_ns": 1785900000123456789,
  "point_count": 24000,
  "map_from_body": {"x": 1.0, "y": 2.0, "z": 0.5, "qx": 0.0, "qy": 0.0, "qz": 0.1, "qw": 0.995},
  "body_from_sensor": {"x": 0.2, "y": 0.0, "z": 0.3, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0},
  "data": b"<本分片二进制>"
}
```

生成顺序：

1. 将一帧传感器坐标点云编码为连续 little-endian float32 `[x0,y0,z0,x1,y1,z1,...]`，长度必须等于 `point_count * 12`。
2. 对完整原始字节串执行一次 zlib 压缩。
3. 对**完整压缩字节串**计算 CRC32：`zlib.crc32(compressed) & 0xffffffff`。
4. 按 MessagePack 信封开销切分压缩串，保证每个最终 UDP 数据报不超过 1400 字节；所有分片携带完全相同的帧元数据。
5. 每个分片使用唯一 sequence。允许分片乱序到达；重复 sequence 或重复 chunk_index 会被忽略。

`map_from_body` 表示同步采样时刻的 `map <- body`，`body_from_sensor` 表示 `body <- sensor`。四元数顺序为 x/y/z/w，范数不得接近零。地面站执行：

```text
p_map = T_map_body * T_body_sensor * p_sensor
```

端侧负责在同一 ROS 时钟下同步点云和位姿；地面站不接收独立位姿流、不插值。首版不发送 intensity、RGB、ring 或 timestamp-per-point。

### 限制、超时与会话恢复

- 默认最大帧点数 200000，最大解压 2400000 字节，最大累计体素 5000000，最大预览点 300000；以地面站 `config/map_building.json` 为准。
- 同一 frame 的分片元数据不一致、缺片超过 1 秒、CRC/zlib/尺寸失败、非有限坐标、错误来源 IP、错误 map/device/session 或重复/非法序列均拒绝。
- 2 秒无完整帧显示链路警告；5 秒中断并保存检查点。所有超时使用地面站单调时钟，不信任端侧时间。
- 端侧重启必须停止旧 session；只有收到新的 start_mapping 才开始新 session。禁止跨 session 复用 frame/sequence 状态。
- 地面站每 5 秒原子更新 `.mapping/<session_id>/session.json`、`partial.pcd`、`trajectory.csv`。正式提交成功后删除会话目录。

### PGM 产物下载扩展（v0.13.0）

PGM 下载与实时建图共享 UDP 14561/14562，但两者互斥。公共信封保持 schema 1；`map_id` 是地面站目标地图 ID，`device_id` 是来源设备 ID，`session_id` 由每次下载生成。端侧产物由 payload 中独立的 `source_map_id` 标识。

下行 `request_pgm_artifact` payload：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | string | 幂等请求 ID |
| `source_map_id` | string | 端侧完整 ROS PGM 产物 ID |
| `return_host` / `return_port` | string / int | 地面站可达地址与 UDP 14562 |
| `compression` | string | 固定 `zlib` |

端侧必须先返回 `command_ack`，其中 `command=request_pgm_artifact`。拒绝时 `reason` 使用 `ARTIFACT_NOT_FOUND`、`UNSUPPORTED_COMMAND`、`BUSY` 或可读错误；旧版本无 ACK 时地面站按 500 ms 最多重试 5 次。

上行 `pgm_manifest` payload：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source_map_id`, `frame_id` | string | 产物与来源坐标系 |
| `pgm_format` | string | `P2` 或 `P5` |
| `width`, `height` | int | PGM 像素尺寸 |
| `resolution` | float | 米/像素 |
| `origin` | float[3] | ROS `[x, y, yaw]`，yaw 为弧度 |
| `negate` | bool/int | ROS map_server 语义 |
| `occupied_thresh`, `free_thresh` | float | 满足 `0 <= free < occupied <= 1` |
| `generated_at_ns` | int | 端侧产物时间，Unix ns |
| `uncompressed_size`, `compressed_size` | int | PGM 原始/压缩字节数 |
| `chunk_count` | int | 分片总数，最大 100000 |
| `crc32` | uint32 | 完整 zlib 压缩流 CRC32 |
| `sha256` | string | 解压后完整 PGM 字节 SHA-256 |

上行 `pgm_chunk` payload 包含 `source_map_id`、`chunk_index`、`chunk_count` 和二进制 `data`。分片可乱序；同一 index 重复分片必须内容一致，地面站只采用首个。单个数据报仍不得超过 `config/map_building.json` 的约 1400 字节上限。

缺片 10 秒后，地面站发送 `request_pgm_chunks`，payload 包含新的 `request_id`、`source_map_id` 和升序去重的 `missing_chunks`。最多补传 5 轮。端侧可使用 `pgm_transfer_status` 报告 `state=complete|error` 及可选 `reason`，但完整性最终以 CRC、SHA、zlib、P2/P5 和 manifest/YAML 字段校验为准。

单个未压缩 PGM 上限 64 MiB。地面站严格校验来源 IP、map/device/session/source_map ID、尺寸、分片数、解压尺寸和哈希。下载检查点保存在目标地图 `.pgm_fusion/<job_id>/<device_id>/`；包含 manifest、分片文件和 `chunk-state.json`，重启后只请求缺失分片。

坐标外参由用户在地面站输入，方向固定为 `目标 PCD frame <- 来源 PGM frame`，只包含 X/Y 米制平移和 yaw 角度。端侧不负责融合或坐标转换。输出采用逆向最近邻采样和 `occupied > free > unknown`，超出目标 PCD XY 边界的内容必须经用户确认后裁剪。

端侧后续实现检查清单：

- 从 `source_map_id` 安全定位同一时刻的完整 PGM 与 ROS YAML 元数据，不允许目录穿越。
- 在内存或稳定快照中读取完整 P2/P5 文件，zlib 压缩后计算压缩流 CRC32 和原始文件 SHA-256。
- 保持 session 内 manifest 和分片不可变，支持任意缺片索引补发和重复请求幂等。
- 执行 64 MiB、100000 分片和数据报尺寸限制；不存在、忙碌或传感器错误时明确 ACK 拒绝。
- 不修改现有 start/stop/cloud_chunk 路径；PGM 传输期间拒绝实时建图请求，实时建图期间拒绝 PGM 请求。

## 配置对应关系

| 地面站 | 端侧 | 必须一致/可达内容 |
| --- | --- | --- |
| `config/devices.json` | `epgeneral_device_config/config/device.yaml` | device ID、IP |
| `config/mqtt.json` | `epgeneral_mqtav/config/config.yaml` | Broker IP/端口、topic root、QoS、频率 |
| `config/udp_telemetry.json` | `epgeneral_udp_telemetry/config/telemetry.yaml` | protocol ID、目标 14560、descriptor 名称/类型/等级/哈希 |
| `config/devices.json`、`config/srt_video.json` | `EPGeneral_video_srt/config/video.yaml` | 设备 IP、UDP 9000、延迟、H.264/MPEG-TS/SRT |
| `config/map_building.json` | `epgeneral_map_stream/config/mapping.yaml` | protocol ID、14561/14562、包长、压缩、格式、速率、体素和资源上限 |
| `config/devices.json` | `epgeneral_device_config/config/device.yaml` | device ID、IP；本地 `device_types.json` 不参与端侧协议 |

## 端侧建图实现检查清单

- 使用共享设备 ID/IP，监听 UDP 14561，仅接受目标 device/map/session 正确的 MessagePack。
- 按 request ID 幂等处理 start/stop，并及时返回 command_ack 和 1 Hz session heartbeat。
- 订阅点云、机体位姿与静态传感器外参；在 ROS 时间域同步后组成同一帧。
- 输出传感器坐标 XYZ `<f4`，整帧 zlib、整帧 CRC32，再按不超过 1400 字节分片。
- 保证每分片 sequence 唯一、frame ID 单调；错误时发送 session_status，而不是继续发送损坏数据。
- 将上行目标设为 start_mapping 指定的 return_host/return_port，不写死地面站地址。
- stop 后停止发送，释放 ROS subscriber、位姿缓存和会话资源；控制 socket 保持监听以接受下一次 start，进程退出时再关闭。
- 在 localhost/局域网测试乱序、重复、缺片、CRC 错误、点云/位姿超时、重复命令和干净退出。

当前结论：v1 作为历史后端保留；v0.18.3 地面站与端侧 `epgeneral_map_stream` v0.9.1 保持 `ccs-map-stream-v2` 兼容，并增加 accumulator 随建图链启动、停止前主动保存与新鲜度校验。真机部署结果见对应版本部署记录。

## UDP 地图任务控制接口（ccs-task-control-v2）

v0.21.1 继续使用 `ccs-task-control-v2`、MessagePack schema 2 和 UDP 14563/14564。v1 端侧不参与 v2 运行链路；平台仅迁移并读取旧任务数据。Scout v0.3.1 任务适配器除要求任务地图、平台全局激活地图和端侧 localized 地图一致外，还必须在启动导航前确认实时 `/fastlio_odom` 和 `map<-odom` TF 存在。执行失败时通过 `LOCALIZATION_UNAVAILABLE`、`MAP_FRAME_MISMATCH` 或 `NAVIGATION_STARTUP_TIMEOUT` 返回原因。

Scout 执行时启动 `roslaunch scout_navigation navigation_teb.launch map_name:=<map_id>`，通过 `/fastlio_odom` 和 `map<-odom` TF 获取 map 坐标，将 `map` frame、Z=0 的目标经 actionlib 发送到 `/move_base/goal`。首点航向由当前位置指向首点，后续航向由前一点指向当前点。终止/急停先取消 move_base 目标，再向 `/cmd_vel` 连续发布零速度并停止导航进程；本版本不动态设置 TEB 巡航速度。

v2 在保留 1400 字节数据报、800 字节分片、zlib、CRC32、request ID 幂等和来源 IP 校验的基础上，新增 `negotiate_task`、`read_task`、`terminate_task`、`emergency_stop`、`delete_task` 及反向任务读取分片消息。端侧任务状态为 `no_task`、`task_exists`、`receiving`、`received`、`ready`、`running`、`completed`、`failed`、`emergency_stop`。

常规终止等待端侧 ACK/终态；急停必须先停止适配器并发布零速度，再删除端侧任务内容，依次上报 `emergency_stop` 和 `no_task`。

### 兼容性与网络

- 指控平台版本：v0.17.0；端侧任务协调包：`epgeneral_task_control v0.1.0`。任务协议 schema 保持 1。
- 地面站绑定 `0.0.0.0:14564/UDP` 接收上行，并从同一 socket 发往设备 `14563/UDP`。
- 可信内网明文 MessagePack，schema 1；默认单包不超过 1400 字节，命令每 500 ms 重试、最多 5 次。
- 任务数据是 zlib 压缩的 UTF-8 JSON，整包 CRC32；默认分片 payload 800 字节、最多 500 航点、压缩后最多 1 MiB。
- 设备应使用 NTP 保持 UTC 时钟同步。共同执行的 `scheduled_at` 默认在当前时间后 5 秒；所有设备必须在启动前完成 ACK。

所有消息信封如下，`payload` 随消息类型变化：

```python
{
  "schema_version": 1,
  "protocol_id": "ccs-task-control-v2",
  "task_id": "<稳定任务 ID>",
  "subtask_id": "<设备子任务 ID>",
  "device_id": "UAV_001",
  "execution_id": "<执行 ID；仅下发时可为空>",
  "message_type": "task_prepare",
  "request_id": "<幂等 UUID>",
  "sequence": 42,
  "sent_at_ns": 1786500000000000000,
  "payload": {}
}
```

端侧必须核对 protocol/task/subtask/device/execution、来源地址、序列和有限数值；同一 request ID 的重复命令返回原 ACK，不重复产生副作用。

### 下行任务传输

`task_prepare` 宣告一次子任务传输：

```python
{
  "revision": 3,
  "chunk_count": 12,
  "compressed_bytes": 8240,
  "raw_bytes": 31240,
  "crc32": 305419896,
  "compression": "zlib",
  "encoding": "json-utf8"
}
```

端侧接受后返回 command_ack。随后接收 `task_chunk`：

```python
{
  "revision": 3,
  "chunk_count": 12,
  "chunk_index": 0,
  "crc32": 305419896,
  "data": b"<二进制分片>"
}
```

`task_commit` 要求重组、CRC、zlib、JSON schema、设备、修订、航点数量和有限坐标校验，并原子持久化：

```python
{"revision": 3, "chunk_count": 12, "crc32": 305419896}
```

若缺片，commit 的 ACK 可返回 `accepted: true` 和 `missing_chunks: [2, 7]`，地面站补发后再次 commit。完整任务 JSON：

```python
{
  "schema_version": 1,
  "task_id": "...", "task_name": "园区巡检", "map_id": "...", "frame_id": "map",
  "subtask_id": "...", "device_id": "UAV_001", "revision": 3,
  "cruise_speed_mps": 1.5, "start_delay_seconds": 2.0,
  "waypoints": [
    {"index": 0, "waypoint_id": "...", "x": 1.0, "y": 2.0, "z": 1.5}
  ]
}
```

坐标采用任务所选地图 `frame_id` 的本地 ENU，单位米。端侧不得把 MQTT mission 状态当作本协议 ACK。

### 下行执行与停止

`execute_task`：

```python
{"revision": 3, "scheduled_at": "2026-08-12T08:00:05+00:00"}
```

端侧验证本地已提交同一修订，ACK 后进入 ready/running。`terminate_task` 用于常规终止，`emergency_stop` 用于强制停止并清除端侧任务，两者均须幂等；急停还必须发布零速度指令。

### 上行 ACK、心跳、状态和进度

所有下行命令使用 `command_ack`：

```python
{
  "accepted": true,
  "command": "task_commit",
  "reason": "",
  "error_code": None,
  "missing_chunks": []
}
```

建议拒绝错误码：`BUSY`、`UNKNOWN_TASK`、`REVISION_MISMATCH`、`MISSING_CHUNKS`、`CRC_ERROR`、`INVALID_WAYPOINT`、`MAP_FRAME_MISMATCH`、`CLOCK_UNSYNCED`、`EXECUTION_CONFLICT`、`INTERNAL_ERROR`。

执行期间以 1 Hz 发送 `task_heartbeat`，payload 至少包含 `state`。状态变化时发送：

```python
{"state": "running", "message": "已开始轨迹执行"}
```

`task_status.state` 允许 `scheduled`、`running`、`completed`、`stopped`、`failed`。航点进度：

```python
{
  "state": "running",
  "waypoint_index": 4,
  "waypoint_count": 12,
  "progress": 0.36,
  "position": {"x": 3.1, "y": 7.2, "z": 1.5},
  "error_code": None,
  "message": "前往航点 5"
}
```

UDP 14560 全局位姿仍是地面站地图实时标记来源；14564 进度是任务执行真值。两条链路不能互相覆盖状态。

### 端侧任务包实现清单

- 监听 UDP 14563，严格解析信封、限制资源并按 request ID 幂等 ACK。
- 按 task/subtask/revision 缓存分片，校验 CRC32 和 zlib 后原子保存轨迹。
- 校验地图 frame、2–500 个有限 XYZ 航点、速度、启动延迟和设备 ID。
- 使用 NTP 校时，按 `scheduled_at` 启动；同一设备拒绝重叠执行。
- 以 1 Hz 发送 heartbeat，并在状态变化和航点推进时发送状态/进度至来源地面站 14564。
- 正确处理 cancel/stop、进程重启、重复命令、缺片、修订不匹配和执行故障。
- `epgeneral_task_control` 将成功提交轨迹原子保存为 XML，并通过 `TaskExecutionCommand`/`TaskExecutionFeedback` 与设备专属控制节点交互；它不直接解锁飞控或调用 MAVROS。
- 默认 ROS 话题为 `/epgeneral_task_control/execution_command` 和 `/epgeneral_task_control/execution_feedback`。command 携带 action、全部 ID、revision、XML 路径、frame 和 UTC 时间；feedback 必须回传相同 ID/revision/request ID、状态、进度和位置。
- XML 根节点为 `trajectory`，保存 task/subtask/device/revision/CRC；metadata 保存任务名、map/frame、速度和延迟；waypoints 按连续 index 保存 ID 与 XYZ。
- 当前仓库已包含协议与协调包并完成自动测试；设备专属运动控制适配节点和真实 ROS Melodic 联调不在该包范围内。

## 设备重定位协议 v1

`ccs-relocalization-v1` 使用 MessagePack schema 1。地面站向端侧 UDP 14565 发送 `negotiate`、`map_offer`、`start_stack`、`initial_pose`；端侧向地面站 UDP 14566 发送 `negotiation_status`、`download_status`、`stack_status`、`relocalization_result`、`session_heartbeat`、`command_error`。

公共信封字段为 `map_id/device_id/session_id/request_id/message_type/sequence/sent_at_ns/payload`。双方必须限制 1400 字节，按 request ID 幂等，拒绝错误设备、来源 IP、会话、乱序状态和非有限数值。

地图缺失时端侧返回 `map_required`。地面站 TCP 14601 的 `/relocalization/map.zip?token=...` 支持 HEAD、GET 和单段 Range，禁止重定向；ZIP 只允许 `manifest.json/public_map.pcd/map.pgm/map.yaml`。端侧校验声明大小、SHA-256、PCD/PGM/YAML 内容后原子安装至 profile 的 `ccs_download/<map_id>`。

`initial_pose` payload 使用 map 系米和弧度：

```json
{"frame_id":"map","x":1.0,"y":2.0,"yaw":0.5,"covariance":{"x":0.25,"y":0.25,"yaw":0.0685389}}
```

成功结果返回 `map_frame`、`odom_frame` 及 `map_from_odom` 的 `x/y/z/qx/qy/qz/qw`。Scout 默认要求连续 10 个 10 Hz TF 样本的平移跨度不超过 0.10 m、yaw 跨度不超过 2°；30 秒未稳定返回失败。Go2 v0.19.0 profile 返回 `UNSUPPORTED_BACKEND`。

## 配置对应关系补充

| 地面站 | 端侧 | 对应内容 |
| --- | --- | --- |
| `config/task_system.json` | `epgeneral_task_control/config/task_control.yaml` | `ccs-task-control-v2`、14563/14564、包长、分片、任务限制与 UTC 调度 |
| `config/relocalization.json` | `epgeneral_relocalization/config/relocalization.yaml` | `ccs-relocalization-v1`、14565/14566、TCP 14601、profile、地图根目录与 TF 稳定阈值 |
