# 端侧设备交互接口总册

文档版本：`v0.13.1`，更新日期：2026-08-18。

地面站 v0.13.1 新增的已保存地图 PCD/PGM 同步融合仅使用本地文件，不产生端侧消息。v0.13.0 的 PGM 产物下载继续复用 UDP 14561/14562，不改变公共信封 schema。MQTT、RTSP、UDP 14560 和任务 UDP 14563/14564 均保持不变。
端侧 `epgeneral_map_stream` v0.1.0 尚未实现 PGM 文件服务；本版本不修改任何真实端侧包代码或版本。旧端侧可继续执行实时建图，但 PGM 下载会超时并显示“不支持”。

本文件是地面站与端侧软件之间的接口基线。以后每次代码更新都必须核对并同步本文件。所有接口默认运行于可信局域网，不提供认证、加密、可靠重传或拥塞控制。

## 接口总览与兼容性

| 通道 | 方向 | 地址/端口 | 协议版本 | 当前端侧实现 |
| --- | --- | --- | --- | --- |
| MQTT 摘要状态 | 端侧 -> 地面站 | TCP 1883，`mqtav/...` | JSON schema `1.0` | mqtav v0.3.0 |
| UDP 高频遥测 | 端侧 -> 地面站 | UDP 14560 | `ccs-udp-telemetry-v1` | epgeneral_udp_telemetry v0.2.1 |
| RTSP 视频 | 地面站拉取端侧 | TCP 8554 `/usb_cam` | H.264/RTP/RTSP | epgeneral_usb_cam_rtsp v0.1.0 |
| UDP 实时建图控制 | 地面站 -> 端侧 | UDP 14561 | `ccs-map-stream-v1` | epgeneral_map_stream v0.1.0 |
| UDP 实时建图数据 | 端侧 -> 地面站 | UDP 14562 | `ccs-map-stream-v1` | epgeneral_map_stream v0.1.0 |
| UDP 任务控制 | 地面站 -> 端侧 | UDP 14563 | `ccs-task-control-v1` | epgeneral_task_control v0.1.0 |
| UDP 任务状态 | 端侧 -> 地面站 | UDP 14564 | `ccs-task-control-v1` | epgeneral_task_control v0.1.0 |

端侧身份由 `edge_side_pkg/EPGeneral_device_config/config/device.yaml` 提供，`device.id` 和 `device.ip` 必须与地面站 `config/devices.json` 完全一致。MQTT、遥测、建图和任务协议中的 `device_id` 均使用该 ID。

## MQTT presence、heartbeat、status

### Go2 EDU ROS 状态桥接

`epqrd_go2_bridge` v0.1.0 从 Unitree SDK2 `rt/lowstate` 和 `rt/sportmodestate` 读取只读状态。默认发布 `/qrd/QRD_001/battery`、`imu`、`odometry`、`robot_mode`、`link/sdk`、`heartbeat` 和 `diagnostics`。`epgeneral_mqtav` 将 SDK 链路新鲜度映射为既有 `fcu_connected` 字段，不增加 MQTT schema 字段；`epgeneral_udp_telemetry` 从 prefixed Odometry 和 Imu 继续生成 `ccs-udp-telemetry-v1` 数据。

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

地面站收到合法 heartbeat 立即设 MQTT 在线；2 秒未收到进入 warning，5 秒进入 offline/error。`fcu_connected=true` 映射健康，false 映射需关注。任务别名：running/active/executing -> 执行中，idle/standby -> 待机，paused -> 暂停，done/succeeded/completed -> 完成，其余未知。

## UDP 14560 高频遥测

端侧向地面站 `config/udp_telemetry.json` 的地址发送 MessagePack。最大数据报 16 KiB。所有包使用以下信封：

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

heartbeat 为 1 Hz，`level=None` 且 payload 为空。合法心跳使 UDP 链路在线；2 秒 warning，5 秒 offline。telemetry 的 payload 键必须存在于两端一致的 descriptor，等级固定为 1/2/3，对应 20/5/1 Hz。

### Descriptor 与 payload

| name | type | level | payload |
| --- | --- | --- | --- |
| `global_pose` | pose | 1 | `valid,x,y,z,roll,pitch,yaw,sample_stamp_ns,data_age_seconds` |
| `vision_pose` | pose | 1 | 同 pose |
| `imu` | imu | 1 | `valid,roll,pitch,yaw,angular_velocity_x/y/z,linear_acceleration_x/y/z,sample_stamp_ns,data_age_seconds` |
| `livox_pointcloud` | pointcloud_status | 2 | `valid,status,last_received_at_ns,data_age_seconds,estimated_rate_hz` |
| `livox_driver` | availability | 3 | `valid,status,last_received_at_ns,data_age_seconds` |
| `fastlio2` | availability | 3 | 同 availability |
| `pgm_mapping` | availability | 3 | 同 availability |
| `octomap_mapping` | availability | 3 | 同 availability |
| `occupancy_grid_mapping` | availability | 3 | 同 availability |
| `mapping_mode` | text_status | 3 | availability 字段加 `value`，最长 128 字符 |

`status` 只允许 `available`、`unavailable`、`unknown`。所有浮点数必须有限。点云内容禁止放入 14560，只发送接收元数据。配置对应关系：地面站 `config/udp_telemetry.json` 定义公共名称/类型/等级；端侧 `epgeneral_udp_telemetry/config/telemetry.yaml` 额外定义 ROS topic、message type、字段路径和超时。公共 descriptor 不一致将导致哈希拒收。

## RTSP 视频

端侧提供 `rtsp://<device.ip>:8554/usb_cam`；IPv6 URL 使用方括号。视频编码为 H.264，经 RTP/RTSP 传输，无音频、录制、认证或 TLS。地面站只在详情页开关打开时拉流，关闭、切换设备/页面或退出时立即释放播放器。拉流失败不会改变 MQTT/UDP 状态，也不会自动重连。

## UDP 14561/14562 实时建图

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
| `config/mqtt.json` | `mqtav/config/config.yaml` | Broker IP/端口、topic root、QoS、频率 |
| `config/udp_telemetry.json` | `epgeneral_udp_telemetry/config/telemetry.yaml` | protocol ID、目标 14560、descriptor 名称/类型/等级/哈希 |
| 固定 RTSP 推导 | `epgeneral_usb_cam_rtsp/config/video.yaml` | 端侧 8554、`/usb_cam`、H.264 |
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

当前结论：地面站 v0.8.0 与端侧 `epgeneral_map_stream` v0.1.0 已实现上述协议。自动测试覆盖协议、处理、会话与 localhost UDP 契约；ROS Melodic 真机上的雷达、里程计和局域网联调仍需在部署设备执行。

## UDP 地图任务控制接口（ccs-task-control-v1）

### 兼容性与网络

- 地面站版本：v0.13.1；端侧任务协调包：`epgeneral_task_control v0.1.0`。任务协议 schema 保持 1。
- 地面站绑定 `0.0.0.0:14564/UDP` 接收上行，并从同一 socket 发往设备 `14563/UDP`。
- 可信内网明文 MessagePack，schema 1；默认单包不超过 1400 字节，命令每 500 ms 重试、最多 5 次。
- 任务数据是 zlib 压缩的 UTF-8 JSON，整包 CRC32；默认分片 payload 800 字节、最多 500 航点、压缩后最多 1 MiB。
- 设备应使用 NTP 保持 UTC 时钟同步。共同执行的 `scheduled_at` 默认在当前时间后 5 秒；所有设备必须在启动前完成 ACK。

所有消息信封如下，`payload` 随消息类型变化：

```python
{
  "schema_version": 1,
  "protocol_id": "ccs-task-control-v1",
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

端侧验证本地已提交同一修订，ACK 后进入 scheduled，在 UTC 时间到达时执行。`cancel_execution` 用于尚在 preparing/scheduled 的会话，`stop_task` 用于 running 会话，两者 payload 均为 `{"reason": "用户终止任务"}`。停止应幂等并尽快进入安全状态。

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

## 配置对应关系补充

| 地面站 | 端侧 | 对应内容 |
| --- | --- | --- |
| `config/task_system.json` | `epgeneral_task_control/config/task_control.yaml` | `ccs-task-control-v1`、14563/14564、包长、分片、任务限制与 UTC 调度 |
