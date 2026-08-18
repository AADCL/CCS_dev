# EPGeneral_multi_map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended when the project owner explicitly authorizes subagents) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立 ROS1 Noetic 端侧包 `epgeneral_multi_map`，使多台物理机器人按同一绝对时间基准采集、配准、切片并上传本机点云与位姿，供指控平台后续融合。

**Architecture:** 新包复用现有 `epgeneral_map_stream` 的自有协议、点云预处理和节点生命周期思路，但不在运行时导入旧包。纯 Python 核心负责协议、位姿插值、固定时间窗口、有界切片和状态机，ROS 适配层只负责订阅、时钟、定时器和 UDP I/O。

**Tech Stack:** Ubuntu 20.04、ROS1 Noetic、catkin、系统 Python 3.8、rospy、roslib、sensor_msgs、NumPy、PyYAML、MessagePack、zlib、UDP。

## Global Constraints

- 目标代码基线改为项目负责人指定的 `CCS_dev-main` 源码快照，地面站版本为 `0.13.1`；该目录没有 Git 元数据，不能从本地证明其 branch、HEAD 或 remote。
- 新目录必须命名为 `edge_side_pkg/EPGeneral_multi_map`，ROS package 必须命名为 `epgeneral_multi_map`；`multi` 是正式拼写。
- 本次不得修改 `edge_side_pkg/EPGeneral_map_stream`、`ccs_monitor` 或 `config/map_building.json`。
- 新旧建图包不得在同一机器人上同时运行；二者继续互斥使用 UDP 14561。
- 保持 `ccs-map-stream-v1`、`schema_version=1`、UDP 14561/14562、MessagePack 信封、zlib、CRC32、1400 字节上限和 `xyz_f32_le`。
- 新包只接受完整联合任务指令，不得把旧开始指令降级为单机器人建图。
- 实际跨机器人配准、优化、融合、地图保存和 UI 均留在指控平台。
- 点云固定为 `sensor_msgs/PointCloud2`；位姿使用 YAML 配置的 `package/Message` 与字段路径，不新增纯 TF 模式。
- 不新增滤波、去动态目标、运动补偿、GPU、Open3D、PCL Python、ROS2、端侧落盘或 UDP 重传。
- 正式部署使用系统 Python 3.8；纯 Python 核心必须兼容 Python 3.8 及以上。
- 当前工作目录没有 Git 元数据；每阶段只记录测试检查点，不提交。Commit 与 Push 必须推迟到 SOP Step 11。

---

## 1. 功能目标

本功能解决当前端侧只能逐帧即时上传、不能让多台机器人按统一时间边界组织数据的问题。`CCS_dev-main` 已用共享 `job_id` 表达一次多设备建图任务，因此不再新增语义重复的 `collaboration_id`。每台机器人收到同一 `job_id`、参与设备集合、`start_at_ns`、切片时长和 `stop_at_ns` 后：

1. 提前检查并订阅本机点云和位姿；
2. 在统一开始时刻进入采集；
3. 为每帧点云匹配或插值得到唯一位姿；
4. 按确定性绝对时间窗口组织帧；
5. 在完整窗口、停止尾片或错误尾片封闭时逐帧上传；
6. 正常或异常结束后释放资源并回到 `standby`。

## 2. 现有系统与限制

现有 `EPGeneral_map_stream` 已实现：

- UDP 14561 开始/停止控制；
- UDP 14562 ACK、心跳、状态和点云分片；
- PointCloud2 XYZ 提取；
- 通用位姿消息字段路径；
- 最近位姿匹配；
- 距离过滤、体素降采样、频率和单帧点数限制；
- zlib、CRC32、MessagePack 和 1400 字节分片；
- 单活动 session、请求幂等、输入超时和待机复位。

旧端侧包的当前限制：

- 开始和停止均按指令到达时刻立即执行；
- 位姿只选最近样本，不进行前后插值；
- 每帧立即上传，没有绝对时间切片；
- 不识别地面站现有 `job_id`、`role`、`primary_device_id`，也没有参与设备集合和统一时间字段；
- 没有完整片、停止尾片、错误尾片和切片级资源限制；

`CCS_dev-main` v0.13.1 已新增的地面站能力：

- 地图页面和 `MapBuildingService.start_job` 已支持多设备任务；
- 一个共享 `job_id` 下为每台设备创建独立 `session_id`，开始 payload 已包含 `job_id`、`role` 和 `primary_device_id`；
- 已具备多设备 ACK barrier、掉线降级、检查点、融合算法执行和地图结果保存；
- 尚未发送 `participant_device_ids`、`start_at_ns`、`slice_duration_ns` 和 `stop_at_ns`，也尚未按 `slice_id` 汇总帧。

## 3. 现有接口

| 接口名称 | 所在文件 | 生产者 | 消费者 | 数据格式 | 是否修改 |
| --- | --- | --- | --- | --- | --- |
| 共享设备身份 | `edge_side_pkg/EPGeneral_device_config/config/device.yaml` | 部署人员 | 全部端侧包 | YAML schema 1，device.id/device.ip | 否 |
| 旧端侧建图配置 | `edge_side_pkg/EPGeneral_map_stream/config/mapping.yaml` | 部署人员 | epgeneral_map_stream | YAML | 否 |
| 地面站建图配置 | `config/map_building.json` | 部署人员 | MapBuildingService | JSON schema 1 | 否 |
| 建图控制信封 | `ccs_monitor/map_building_services.py` | 地面站 | 端侧 UDP 14561 | MessagePack ccs-map-stream-v1 | 本次不改生产者 |
| 多设备任务标识 | `ccs_monitor/map_building_services.py` | `_ActiveJob` | 每设备 start payload | 共享 job_id + 独立 session_id | 复用 job_id |
| 地面站多地图融合 | `ccs_monitor/map_fusion.py`、`ccs_monitor/epgeneral_multi_map_fusion.py` | MapBuildingService | MapRepository | 多设备 PCD + 外参 + ICP/体素结果 | 否 |
| 建图上行信封 | `edge_side_pkg/EPGeneral_map_stream/src/epgeneral_map_stream/protocol.py` | 旧端侧包 | 地面站 UDP 14562 | MessagePack ccs-map-stream-v1 | 否 |
| cloud_chunk 重组 | `ccs_monitor/map_building.py` | 端侧数据 | CloudFrameAssembler | zlib + CRC32 + xyz_f32_le | 否 |
| 点云预处理 | `edge_side_pkg/EPGeneral_map_stream/src/epgeneral_map_stream/processing.py` | 旧端侧节点 | 旧上传器 | NumPy Nx3 float32 | 否 |
| ROS 点云输入 | 现场配置话题 | 雷达驱动 | 新旧端侧包 | sensor_msgs/PointCloud2 | 复用 |
| ROS 位姿输入 | 现场配置话题 | 定位/里程计节点 | 新旧端侧包 | 可配置 ROS 消息和字段路径 | 复用 |
| 地图持久化 | `ccs_monitor/map_repository.py` | 地面站 | 地图页面 | map.json、PCD、trajectory.csv | 否 |

## 4. 新增接口

### 4.1 纯 Python 类型

在 `src/epgeneral_multi_map/models.py` 新增：

```python
@dataclass(frozen=True)
class PoseSample:
    stamp_ns: int
    translation: np.ndarray
    rotation_xyzw: np.ndarray

@dataclass(frozen=True)
class PoseMatch:
    transform: np.ndarray
    before_stamp_ns: int
    after_stamp_ns: int
    max_error_ns: int
    interpolated: bool

@dataclass
class SynchronizedFrame:
    stamp_ns: int
    raw_message: object
    raw_point_count: int
    raw_bytes: int
    map_from_body: np.ndarray
    pose_match: PoseMatch

@dataclass
class SliceBatch:
    slice_id: int
    start_ns: int
    end_ns: int
    frames: list
    partial: bool = False
    error_tail: bool = False
    truncated: bool = False
    dropped_invalid: int = 0
    dropped_sync: int = 0
    dropped_late: int = 0
    dropped_resource: int = 0

@dataclass
class SessionStats:
    dropped_invalid: int = 0
    dropped_sync: int = 0
    dropped_late: int = 0
    dropped_resource: int = 0
    max_uploaded_stamp_ns: int = 0

@dataclass
class MappingSession:
    identity: dict
    job_id: str
    participant_device_ids: tuple
    start_at_ns: int
    slice_duration_ns: int
    destination: tuple
    token: str
    pose_buffer: object
    collector: object
    stats: SessionStats
    stop_at_ns: Optional[int] = None
    mapping_started: bool = False
    last_cloud_monotonic: Optional[float] = None
    last_pose_monotonic: Optional[float] = None

    @classmethod
    def from_command(cls, command: dict, config: dict) -> "MappingSession": ...
```

`MappingSession` 只存在于一次活动任务期间；`token` 用于使旧 subscriber callback 在复位后自动失效。`last_cloud_monotonic` 和 `last_pose_monotonic` 分别跟踪两路输入活性，不用 ROS 消息时间戳计算本机输入超时。

### 4.2 位姿同步接口

在 `src/epgeneral_multi_map/time_sync.py` 新增：

```python
class PoseBuffer:
    def __init__(self, maximum: int) -> None: ...
    def add(self, sample: PoseSample) -> bool: ...
    def match(self, stamp_ns: int, tolerance_ns: int) -> Optional[PoseMatch]: ...
    def stamps(self) -> Tuple[int, ...]: ...
    def clear(self) -> None: ...

def interpolate_pose(before: PoseSample, after: PoseSample, stamp_ns: int) -> PoseMatch: ...
def slerp_xyzw(left: np.ndarray, right: np.ndarray, ratio: float) -> np.ndarray: ...
```

匹配规则：

- 精确时间戳直接使用该样本；
- 前后样本都在容差内时优先插值；
- 无法形成双边插值时，允许使用容差内最近单样本；
- 不允许超出容差外推；
- 四元数先归一化，再通过点积符号选择最短弧 SLERP。

### 4.3 ROS 消息适配与现有处理接口

在 `src/epgeneral_multi_map/processing.py` 新增：

```python
def stamp_to_ns(stamp: object) -> int: ...
def transform_from_pose(message: object, position_path: str,
                        orientation_path: str) -> np.ndarray: ...
def synchronized_frame(message: object, stamp_ns: int,
                       pose_match: PoseMatch) -> SynchronizedFrame: ...
def extract_pointcloud2(message: object, reader=None) -> np.ndarray: ...
def preprocess_points(points: np.ndarray, min_range_m: float,
                      max_range_m: float, voxel_size_m: float,
                      max_points: int) -> np.ndarray: ...
```

`synchronized_frame` 的 `raw_bytes` 精确定义为 `len(message.data)`，用于约束切片内保留的 PointCloud2 原始 payload；Python 对象和帧列表开销另由 `max_slice_frames` 限制。XYZ 提取和已有距离、体素、频率、点数处理语义必须与旧包一致。

### 4.4 切片接口

在 `src/epgeneral_multi_map/slicing.py` 新增：

```python
class SliceCollector:
    def __init__(self, start_at_ns: int, duration_ns: int,
                 late_arrival_ns: int, limits: Mapping[str, int]) -> None: ...
    def slice_id_for(self, stamp_ns: int) -> int: ...
    def window_for(self, slice_id: int) -> Tuple[int, int]: ...
    def add(self, frame: SynchronizedFrame) -> str: ...
    def seal_ready(self, wall_time_ns: int) -> List[SliceBatch]: ...
    def seal_tail(self, stop_at_ns: int, error_tail: bool) -> Optional[SliceBatch]: ...
    def clear(self) -> None: ...

class PassThroughSliceProcessor:
    def process(self, batch: SliceBatch) -> SliceBatch:
        return batch
```

窗口规则：

- `slice_id = (stamp_ns - start_at_ns) // duration_ns`；
- `slice_start_ns = start_at_ns + slice_id * duration_ns`；
- 完整窗口为 `[slice_start_ns, slice_end_ns)`；
- 完整窗口在 `slice_end_ns + late_arrival_ns` 后封闭；
- 最多同时保留“正在宽限的上一片”和“当前片”；
- 停止尾片在 `stop_at_ns` 立即封闭，不额外等待迟到宽限；
- `stop_at_ns` 正好位于窗口边界时不制造空尾片。

### 4.5 协议 payload 扩展

不新增 message type。`start_mapping` 在既有字段之外必须包含：

```python
{
    "job_id": "ground-station-job-id",
    "role": "primary",
    "primary_device_id": "UAV_001",
    "participant_device_ids": ["UAV_001", "UGV_001"],
    "start_at_ns": 1786982405000000000,
    "slice_duration_ns": 5000000000
}
```

`stop_mapping` 在既有字段之外必须包含：

```python
{
    "job_id": "ground-station-job-id",
    "stop_at_ns": 1786982462500000000
}
```

每帧 `cloud_chunk` 元数据新增：

```python
{
    "job_id": "ground-station-job-id",
    "slice_id": 3,
    "slice_start_ns": 1786982420000000000,
    "slice_end_ns": 1786982425000000000,
    "partial": False,
    "error_tail": False,
    "truncated": False,
    "frame_index": 0,
    "slice_frame_count": 23,
    "pose_before_stamp_ns": 1786982420100000000,
    "pose_after_stamp_ns": 1786982420120000000,
    "pose_max_error_ns": 10000000,
    "pose_interpolated": True
}
```

切片结束继续使用 `session_status`：

```python
{
    "state": "mapping",
    "event": "slice_complete",
    "slice_id": 3,
    "slice_start_ns": 1786982420000000000,
    "slice_end_ns": 1786982425000000000,
    "partial": False,
    "error_tail": False,
    "truncated": False,
    "valid_frames": 23,
    "uploaded_frames": 23,
    "dropped_invalid": 0,
    "dropped_sync": 1,
    "dropped_late": 0,
    "dropped_resource": 0
}
```

空片发送相同 `session_status`，但 `event="slice_empty"`、`uploaded_frames=0`、`error_code="EMPTY_SLICE"`，不得发送空 `cloud_chunk`。

### 4.5 ROS callback 和节点接口

在 `src/epgeneral_multi_map/node.py` 新增：

```python
class RosMultiMapNode:
    def start(self) -> None: ...
    def handle_datagram(self, datagram: bytes, peer_ip: str) -> None: ...
    def _handle_start(self, command: dict, peer_ip: str) -> None: ...
    def _handle_stop(self, command: dict) -> None: ...
    def _pose_callback(self, token: str, message: object) -> None: ...
    def _cloud_callback(self, token: str, message: object) -> None: ...
    def _watchdog(self, unused_event: object = None) -> None: ...
    def close(self) -> None: ...
```

不新增 ROS 发布 topic、Qt signal、数据库 model 或端侧文件格式。

## 5. 开源参考

| 项目 | URL | License | 借鉴内容 | 不直接使用 |
| --- | --- | --- | --- | --- |
| ROS message_filters | https://github.com/ros/ros_comm/tree/noetic-devel/utilities/message_filters | BSD | 带时间戳输入、有界缓存、before/after 查询接口 | 不用 ApproximateTimeSynchronizer 代替插值，不新增运行依赖 |
| ROS tf2 TimeCache | https://github.com/ros/geometry2/tree/noetic-devel | BSD | 排序缓存、拒绝外推、平移线性插值、四元数 SLERP | 不接入 TF Buffer，不新增纯 TF 模式，不复制 C++ 实现 |
| Apache Beam | https://github.com/apache/beam | Apache-2.0 | event time、fixed window、allowed lateness、窗口过期 | 不安装 Beam，不引入 runner 或分布式依赖 |

实现以 CCS_dev 现有自有代码为主体。若实施中必须直接复制第三方源码，必须暂停并重新评估许可证；本路线默认不直接复制。

## 6. 技术方法

```text
UDP start_mapping + YAML + device.yaml
→ 联合字段、来源 IP、设备集合、协议参数和时钟校验
→ 提前解析 ROS 消息类型并订阅，进入 armed
→ start_at_ns 到达后接受 PointCloud2
→ 通用位姿缓存按时间排序
→ 每帧点云查找前后位姿并插值
→ 按事件时间归入绝对固定窗口
→ 窗口结束 + 0.2 秒宽限后封片
→ 透传切片处理器
→ 现有距离/体素/频率/点数预处理
→ zlib + CRC32 + cloud_chunk 分片上传
→ session_status 上报切片统计
→ 发送后释放原始 PointCloud2
→ stop_at_ns 封闭尾片或输入超时封闭错误尾片
→ 清理 subscriber、缓存和 session，回到 standby
```

端侧不保存数据。地面站持久化和融合不在本次代码范围。

## 7. 文件级计划

### 7.1 新增

```text
edge_side_pkg/EPGeneral_multi_map/CMakeLists.txt
edge_side_pkg/EPGeneral_multi_map/package.xml
edge_side_pkg/EPGeneral_multi_map/setup.py
edge_side_pkg/EPGeneral_multi_map/config/multi_mapping.yaml
edge_side_pkg/EPGeneral_multi_map/launch/epgeneral_multi_map.launch
edge_side_pkg/EPGeneral_multi_map/scripts/check_version.py
edge_side_pkg/EPGeneral_multi_map/scripts/epgeneral_multi_map_node.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/__init__.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/config.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/models.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/protocol.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/time_sync.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/slicing.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/processing.py
edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/node.py
edge_side_pkg/EPGeneral_multi_map/test/__init__.py
edge_side_pkg/EPGeneral_multi_map/test/test_config.py
edge_side_pkg/EPGeneral_multi_map/test/test_protocol.py
edge_side_pkg/EPGeneral_multi_map/test/test_time_sync.py
edge_side_pkg/EPGeneral_multi_map/test/test_slicing.py
edge_side_pkg/EPGeneral_multi_map/test/test_processing.py
edge_side_pkg/EPGeneral_multi_map/test/test_node.py
edge_side_pkg/EPGeneral_multi_map/test/test_udp_integration.py
edge_side_pkg/EPGeneral_multi_map/test/test_ground_contract.py
edge_side_pkg/EPGeneral_multi_map/test/test_version_and_entrypoint.py
edge_side_pkg/EPGeneral_multi_map/README.md
edge_side_pkg/EPGeneral_multi_map/CHANGELOG.md
docs/地面站兼容扩展说明.md
```

### 7.2 实现完成后同步

```text
README.md
CHANGELOG.md
edge_side_pkg/README.md
docs/EDGE_DEVICE_INTERFACES.md
docs/DEVELOPMENT_NOTES.md
docs/EDGE_PACKAGE_DEVELOPMENT_PLAN.md
需求分析.md
```

### 7.3 明确不修改

```text
ccs_monitor/**
config/map_building.json
edge_side_pkg/EPGeneral_map_stream/**
edge_side_pkg/EPGeneral_device_config/config/device.yaml
tests/test_map_building.py
tests/test_map_building_service.py
```

## 8. 配置计划

新包使用 ROS package 自有 YAML：

```yaml
schema_version: 1
protocol_id: "ccs-map-stream-v1"

network:
  bind_host: "0.0.0.0"
  control_port: 14561
  ground_station_ip: "192.168.151.100"
  data_port: 14562
  max_datagram_bytes: 1400

ros:
  cloud:
    topic: "/livox/lidar"
    message_type: "sensor_msgs/PointCloud2"
  pose:
    topic: "/Odometry"
    message_type: "nav_msgs/Odometry"
    position_path: "pose.pose.position"
    orientation_path: "pose.pose.orientation"
  frames:
    map: "map"
    body: "body"
    sensor: "livox_frame"
  body_from_sensor:
    x: 0.0
    y: 0.0
    z: 0.0
    qx: 0.0
    qy: 0.0
    qz: 0.0
    qw: 1.0

sync:
  tolerance_seconds: 0.05
  pose_buffer_size: 400
  max_message_clock_offset_seconds: 2.0
  timestamp_rollback_tolerance_seconds: 0.001

slicing:
  default_duration_seconds: 5.0
  min_duration_seconds: 1.0
  max_duration_seconds: 60.0
  late_arrival_seconds: 0.2

preprocess:
  min_range_m: 0.30
  max_range_m: 100.0
  min_voxel_size_m: 0.05
  max_cloud_rate_hz: 5.0

timeouts:
  input_timeout_seconds: 3.0
  command_cache_seconds: 60.0
  clock_skew_tolerance_seconds: 2.0
  start_late_tolerance_seconds: 0.1
  minimum_start_lead_seconds: 0.5

limits:
  max_participant_devices: 32
  max_frame_points: 200000
  max_decompressed_bytes: 2400000
  max_slice_frames: 50
  max_slice_points: 5000000
  max_slice_bytes: 134217728
```

开始指令可在 1～60 秒内覆盖切片时长。资源限制只截断当前切片；`max_slice_bytes` 精确统计当前切片保留的 PointCloud2 `message.data` 字节总数，不依赖 Python 进程 RSS，其他对象开销由帧数上限间接约束。

新 start 指令在接收时必须满足 `start_at_ns >= now_ns + minimum_start_lead_seconds`，否则以 `START_LEAD_TOO_SHORT` 拒绝且不订阅。`start_late_tolerance_seconds` 只处理任务已经 armed 后因调度器/看门狗唤醒抖动而略晚进入 mapping 的情况，不能用于接受已经迟到的 start 指令；超过该容差则以 `START_TIME_MISSED` 终止并复位。

## 9. 兼容性策略

- 旧数据能否读取：能。现有地面站继续读取旧包数据；新上行扩展字段保持附加字段形式。
- 旧配置能否继续使用：能。旧包 `mapping.yaml` 不变；新包有独立 YAML。
- 旧设备能否继续接入：能。旧设备继续运行 `epgeneral_map_stream`。
- 单设备旧流程是否保持：保持，且本次不修改其代码、配置和 UI。
- 新包是否接受旧指令：否，缺少联合字段时返回拒绝 ACK。
- 是否需要 schema migration：否。协议和文件 schema 均保持版本 1。
- 当前地面站能否直接启动新包：还不能。v0.13.1 已有联合任务和融合，不需重做多 session；后续只需按 `docs/地面站兼容扩展说明.md` 在现有 job 指令上补充参与集合、统一开始/切片/停止字段，并增加切片汇总。
- 上行兼容：当前地面站解析器允许附加 payload 字段，`test_ground_contract.py` 必须证明带切片字段的单帧仍可按现有 XYZ 契约重组。

## 10. 错误码和状态约定

复用现有错误码，并新增以下语义字符串：

```text
COLLABORATION_REQUIRED
PARTICIPANT_SET_INVALID
CLOCK_UNSYNCED
START_TIME_MISSED
START_LEAD_TOO_SHORT
SESSION_MISMATCH
STOP_TIME_MISSED
INPUT_TIMEOUT
EMPTY_SLICE
SLICE_TRUNCATED
```

对外 `session_status.state` 仍只使用 `starting`、`mapping`、`stopping`、`stopped` 和 `error`。更细事件通过 `payload.event` 表达，不新增 message type。

## 11. 测试计划

### 11.1 Step 6 修改前基线

在创建新包前运行：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q ccs_monitor run.py tests
$env:PYTHONPATH = "edge_side_pkg/EPGeneral_map_stream/src"
python -m unittest discover -s edge_side_pkg/EPGeneral_map_stream/test -v
Remove-Item Env:PYTHONPATH
```

预期：现有地面站和旧端侧包测试全部通过；若失败，先记录为基线问题，不得把既有失败归因于新功能。

### 11.2 新包纯 Python

```powershell
$env:PYTHONPATH = "edge_side_pkg/EPGeneral_multi_map/src"
python -m unittest discover -s edge_side_pkg/EPGeneral_multi_map/test -v
Remove-Item Env:PYTHONPATH
```

测试覆盖配置、协议、插值、切片、资源限制、状态机、UDP、上行地面站契约和版本入口。

### 11.3 Python 3.8

```powershell
$env:PYTHONPATH = "edge_side_pkg/EPGeneral_multi_map/src"
& "C:\Users\BM\3D Objects\anaconda\python.exe" -m unittest discover -s edge_side_pkg/EPGeneral_multi_map/test -v
Remove-Item Env:PYTHONPATH
```

目标机仍需使用 `/usr/bin/python3` 复验，不能只以 Windows Anaconda 代替。

### 11.4 ROS Noetic

```bash
source /opt/ros/noetic/setup.bash
cd ~/catkin_ws
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
catkin_make run_tests_epgeneral_multi_map
source devel/setup.bash
roslaunch epgeneral_multi_map epgeneral_multi_map.launch
```

预期：catkin 构建成功，节点使用系统 Python 3.8 启动，绑定 14561，进入 standby。

### 11.5 回归和不可修改项

完成新包后重新运行 11.1 全部命令，并把 `EPGeneral_map_stream` 与基线提交进行归一化文本比较。预期旧包无内容变化，地面站测试结果不退化。

## 12. 分阶段 TDD 实施任务

### Task 1: 包骨架与严格配置

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/CMakeLists.txt`
- Create: `edge_side_pkg/EPGeneral_multi_map/package.xml`
- Create: `edge_side_pkg/EPGeneral_multi_map/setup.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/config/multi_mapping.yaml`
- Create: `edge_side_pkg/EPGeneral_multi_map/launch/epgeneral_multi_map.launch`
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/__init__.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/config.py`
- Test: `edge_side_pkg/EPGeneral_multi_map/test/test_config.py`

**Interfaces:**
- Consumes: `device.yaml` 的 `device.id` 和 `device.ip`。
- Produces: `load_config(mapping_path, device_path) -> dict`，供所有后续任务使用。

- [x] **Step 1: 写入失败配置测试**

```python
class ConfigTests(unittest.TestCase):
    def test_sample_config_targets_noetic_and_shared_identity(self):
        config = load_config(CONFIG, DEVICE)
        self.assertEqual(config["device_id"], "UAV_001")
        self.assertEqual(config["control_port"], 14561)
        self.assertEqual(config["default_slice_duration_ns"], 5_000_000_000)
        self.assertEqual(config["late_arrival_ns"], 200_000_000)
        self.assertEqual(config["max_slice_frames"], 50)

    def test_invalid_slice_duration_range_is_rejected(self):
        payload = valid_mapping_payload()
        payload["slicing"]["min_duration_seconds"] = 10.0
        payload["slicing"]["max_duration_seconds"] = 1.0
        with self.assertRaises(ConfigError):
            load_payloads(payload, valid_device_payload())
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_config -v`  
Expected: FAIL，原因是 `epgeneral_multi_map.config` 尚不存在。

- [x] **Step 3: 实现配置解析和包元数据**

```python
def seconds_to_ns(value, name):
    number = _finite_number(value, name)
    if number < 0:
        raise ConfigError("%s must be non-negative" % name)
    return int(round(number * 1_000_000_000))

def load_config(mapping_path, device_path):
    mapping = _read_yaml(mapping_path)
    device = _read_yaml(device_path)["device"]
    config = _validate_mapping(mapping)
    config["device_id"] = _text(device, "id", "device.id")
    config["device_ip"] = _ip(device["ip"], "device.ip")
    return config
```

`package.xml` 只声明 catkin、rospy、roslib、sensor_msgs、nav_msgs、`epgeneral_device_config`、python3-yaml、python3-msgpack、python3-numpy 和 python3-nose，不加入 Beam、Open3D、PCL 或 GPU 依赖。`CMakeLists.txt` 使用 `find_package(PythonInterp 3.8 REQUIRED)`。

- [x] **Step 4: 运行配置测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_config -v`  
Expected: PASS，包含合法样例、端口、外参、时间范围、资源上限和 PointCloud2 类型约束。

- [x] **Step 5: 记录阶段检查点，不提交**

记录新增文件列表和测试输出；由于 SOP Step 11 门禁及当前无 Git 元数据，不执行 commit。

### Task 2: 协议兼容扩展

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/protocol.py`
- Test: `edge_side_pkg/EPGeneral_multi_map/test/test_protocol.py`

**Interfaces:**
- Consumes: Task 1 的 config dict。
- Produces: `decode_command`、`encode_envelope`、`encode_cloud_chunks`。

- [x] **Step 1: 写入联合指令和分片失败测试**

```python
def test_start_requires_joint_fields(self):
    payload = old_start_payload()
    raw = encode_raw("start_mapping", payload)
    with self.assertRaisesRegex(ProtocolError, "job_id"):
        decode_command(raw, self.config)

def test_participants_must_be_unique_and_include_local_device(self):
    payload = joint_start_payload()
    payload["participant_device_ids"] = ["UAV_001", "uav_001"]
    with self.assertRaisesRegex(ProtocolError, "participant"):
        decode_command(encode_raw("start_mapping", payload), self.config)

def test_slice_metadata_survives_chunk_encoding(self):
    datagrams = encode_cloud_chunks(
        self.config, identity(), 7, slice_frame_metadata(), zlib.compress(b"\0" * 1200))
    decoded = [msgpack.unpackb(item, raw=False) for item in datagrams]
    self.assertTrue(all(item["payload"]["slice_id"] == 3 for item in decoded))
    self.assertTrue(all(len(item) <= self.config["max_datagram_bytes"] for item in datagrams))
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_protocol -v`  
Expected: FAIL，原因是协议模块尚不存在。

- [x] **Step 3: 实现严格 payload 校验**

```python
def _validate_joint_start(command, config):
    payload = command["payload"]
    job_id = _identifier(payload.get("job_id"), "job_id")
    role = payload.get("role")
    if role not in {"primary", "secondary"}:
        raise ProtocolError("role is invalid")
    primary_device_id = _identifier(payload.get("primary_device_id"), "primary_device_id")
    participants = payload.get("participant_device_ids")
    if not isinstance(participants, list):
        raise ProtocolError("participant_device_ids must be a list")
    normalized = [_identifier(item, "participant_device_ids").casefold() for item in participants]
    if len(normalized) < 2 or len(normalized) > config["max_participant_devices"]:
        raise ProtocolError("participant_device_ids count is invalid")
    if len(set(normalized)) != len(normalized):
        raise ProtocolError("participant_device_ids must be unique")
    if config["device_id"].casefold() not in normalized:
        raise ProtocolError("participant_device_ids must include local device")
    start_at_ns = _integer(payload.get("start_at_ns"), "start_at_ns", 1)
    duration_ns = _integer(payload.get("slice_duration_ns"), "slice_duration_ns", 1)
    if not config["min_slice_duration_ns"] <= duration_ns <= config["max_slice_duration_ns"]:
        raise ProtocolError("slice_duration_ns is outside configured bounds")
    return job_id, role, primary_device_id, start_at_ns, duration_ns
```

编码函数必须通过试编码计算每片最大 data 长度，保证最终 MessagePack 数据报不超过 1400 字节，且每个分片 sequence 唯一。

- [x] **Step 4: 运行协议测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_protocol -v`  
Expected: PASS，覆盖旧指令拒绝、参与设备集合、stop 字段、附加字段、CRC 和包长。

- [x] **Step 5: 记录阶段检查点，不提交**

保存协议测试输出；不修改地面站协议文件。

### Task 3: 位姿模型、排序缓存和插值

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/models.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/time_sync.py`
- Test: `edge_side_pkg/EPGeneral_multi_map/test/test_time_sync.py`

**Interfaces:**
- Consumes: `PoseSample`。
- Produces: `PoseBuffer.match(...) -> Optional[PoseMatch]`。

- [x] **Step 1: 写入插值失败测试**

```python
def test_bracketing_samples_interpolate_translation_and_shortest_rotation(self):
    buffer = PoseBuffer(10)
    buffer.add(sample(0, (0, 0, 0), (0, 0, 0, 1)))
    buffer.add(sample(2_000_000_000, (2, 0, 0), (0, 0, 1, 0)))
    match = buffer.match(1_000_000_000, 1_100_000_000)
    np.testing.assert_allclose(match.transform[:3, 3], [1, 0, 0], atol=1e-6)
    self.assertTrue(match.interpolated)
    self.assertEqual(match.before_stamp_ns, 0)
    self.assertEqual(match.after_stamp_ns, 2_000_000_000)

def test_out_of_order_pose_is_sorted_and_outside_tolerance_is_rejected(self):
    buffer = PoseBuffer(3)
    buffer.add(sample(30, (3, 0, 0)))
    buffer.add(sample(10, (1, 0, 0)))
    buffer.add(sample(20, (2, 0, 0)))
    self.assertIsNone(buffer.match(100, 5))
    self.assertEqual(buffer.stamps(), (10, 20, 30))
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_time_sync -v`  
Expected: FAIL，原因是 PoseBuffer 尚不存在。

- [x] **Step 3: 实现有序缓存和 SLERP**

```python
def slerp_xyzw(left, right, ratio):
    q0 = _normalized(left)
    q1 = _normalized(right)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    if dot > 0.9995:
        return _normalized(q0 + ratio * (q1 - q0))
    angle = math.acos(max(-1.0, min(1.0, dot)))
    scale0 = math.sin((1.0 - ratio) * angle) / math.sin(angle)
    scale1 = math.sin(ratio * angle) / math.sin(angle)
    return _normalized(scale0 * q0 + scale1 * q1)
```

`PoseBuffer.add` 使用 `bisect` 按 stamp 插入、同时间戳拒绝并返回 `False`、超过容量删除最旧样本，并以 `threading.RLock` 保护。`match` 禁止容差外外推。

- [x] **Step 4: 运行插值测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_time_sync -v`  
Expected: PASS，覆盖精确、双边插值、最近回退、乱序、重复、零四元数和清理。

- [x] **Step 5: 记录阶段检查点，不提交**

保存数值误差测试结果；不导入 tf2 Buffer。

### Task 4: 固定窗口、迟到容忍和资源限制

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/slicing.py`
- Test: `edge_side_pkg/EPGeneral_multi_map/test/test_slicing.py`

**Interfaces:**
- Consumes: `SynchronizedFrame`。
- Produces: `SliceBatch` 和 `PassThroughSliceProcessor`。

- [x] **Step 1: 写入窗口失败测试**

```python
def test_absolute_windows_are_deterministic(self):
    collector = make_collector(start=10_000, duration=5_000, late=200)
    self.assertEqual(collector.slice_id_for(10_000), 0)
    self.assertEqual(collector.slice_id_for(14_999), 0)
    self.assertEqual(collector.slice_id_for(15_000), 1)
    self.assertEqual(collector.window_for(1), (15_000, 20_000))

def test_window_waits_for_lateness_then_drops_expired_frame(self):
    collector = make_collector(start=10_000, duration=5_000, late=200)
    collector.add(frame(14_900))
    self.assertEqual(collector.seal_ready(15_199), [])
    sealed = collector.seal_ready(15_200)
    self.assertEqual(sealed[0].slice_id, 0)
    self.assertEqual(collector.add(frame(14_950)), "late")

def test_resource_overflow_truncates_only_current_window(self):
    collector = make_collector(max_frames=1)
    self.assertEqual(collector.add(frame(10_100)), "accepted")
    self.assertEqual(collector.add(frame(10_200)), "truncated")
    self.assertEqual(collector.add(frame(15_100)), "accepted")
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_slicing -v`  
Expected: FAIL，原因是 SliceCollector 尚不存在。

- [x] **Step 3: 实现窗口和封片**

```python
def slice_id_for(self, stamp_ns):
    if stamp_ns < self.start_at_ns:
        raise SliceError("stamp precedes start_at_ns")
    return (stamp_ns - self.start_at_ns) // self.duration_ns

def window_for(self, slice_id):
    start_ns = self.start_at_ns + slice_id * self.duration_ns
    return start_ns, start_ns + self.duration_ns

def _would_overflow(self, state, frame):
    return (
        len(state.frames) + 1 > self.limits["max_slice_frames"] or
        state.raw_points + frame.raw_point_count > self.limits["max_slice_points"] or
        state.raw_bytes + frame.raw_bytes > self.limits["max_slice_bytes"]
    )
```

封片按 slice_id 升序返回；空窗口返回零帧 `SliceBatch` 供状态层报告；`seal_tail` 删除 stamp 不小于 stop_at_ns 的未发送帧。

- [x] **Step 4: 运行切片测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_slicing -v`  
Expected: PASS，覆盖边界、迟到、两个并存窗口、空片、尾片、精确边界和四类统计。

- [x] **Step 5: 记录阶段检查点，不提交**

记录最大窗口数量和资源截断测试输出。

### Task 5: 现有预处理语义和切片处理器

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/processing.py`
- Test: `edge_side_pkg/EPGeneral_multi_map/test/test_processing.py`

**Interfaces:**
- Consumes: raw PointCloud2、`SliceBatch`。
- Produces: NumPy `Nx3 float32` 和透传批次。

- [x] **Step 1: 写入预处理回归测试**

```python
def test_existing_range_voxel_and_limit_semantics_are_preserved(self):
    points = np.asarray([
        [0.1, 0, 0], [1.01, 0, 0], [1.02, 0, 0],
        [2.0, 0, 0], [np.nan, 0, 0]
    ], dtype=np.float32)
    result = preprocess_points(points, 0.3, 100.0, 0.1, 2)
    np.testing.assert_allclose(result, [[1.01, 0, 0], [2.0, 0, 0]])

def test_pass_through_processor_preserves_batch_identity(self):
    batch = SliceBatch(0, 0, 5, [])
    self.assertIs(PassThroughSliceProcessor().process(batch), batch)
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_processing -v`  
Expected: FAIL，原因是 processing 模块尚不存在。

- [x] **Step 3: 移植并隔离现有处理**

```python
def preprocess_points(points, min_range_m, max_range_m, voxel_size_m, max_points):
    array = np.asarray(points, dtype=np.float32).reshape((-1, 3))
    finite = np.isfinite(array).all(axis=1)
    distance = np.linalg.norm(array, axis=1)
    filtered = array[finite & (distance >= min_range_m) & (distance <= max_range_m)]
    keys = np.floor(filtered / voxel_size_m).astype(np.int64)
    _, first = np.unique(keys, axis=0, return_index=True)
    reduced = filtered[np.sort(first)]
    return reduced[:max_points].astype(np.float32, copy=False)
```

`extract_pointcloud2` 继续只输出 XYZ；原始消息由 `SynchronizedFrame` 保留到切片发送完成。

- [x] **Step 4: 运行处理测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_processing -v`  
Expected: PASS，并与旧包相同输入的输出一致。

- [x] **Step 5: 记录阶段检查点，不提交**

保存新旧预处理对照结果。

### Task 6: 节点启动、联合校验和 armed 状态

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/node.py`
- Test: `edge_side_pkg/EPGeneral_multi_map/test/test_node.py`

**Interfaces:**
- Consumes: Tasks 1～5 的接口。
- Produces: `RosMultiMapNode` 的启动、start ACK、订阅和 armed/mapping 转换。

- [x] **Step 1: 写入开始状态机失败测试**

```python
def test_valid_joint_start_arms_then_starts_at_shared_time(self):
    node, clock = make_node(wall_ns=1_000_000_000)
    node.handle_datagram(joint_start(start_at_ns=2_000_000_000), GROUND_IP)
    self.assertEqual(node.state, "armed")
    self.assertEqual(node.subscription_count, 2)
    clock.wall_ns = 1_999_999_999
    node._watchdog()
    self.assertFalse(node.session.mapping_started)
    clock.wall_ns = 2_000_000_000
    node._watchdog()
    self.assertTrue(node.session.mapping_started)

def test_old_start_is_rejected_without_subscribing(self):
    node, _ = make_node()
    node.handle_datagram(old_start(), GROUND_IP)
    self.assertEqual(node.state, "standby")
    self.assertEqual(last_ack(node)["error_code"], "COLLABORATION_REQUIRED")
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_node.NodeTests.test_valid_joint_start_arms_then_starts_at_shared_time -v`  
Expected: FAIL，原因是 RosMultiMapNode 尚不存在。

- [x] **Step 3: 实现联合开始路径**

```python
def _handle_start(self, command, peer_ip):
    if self.session is not None:
        return self._handle_busy_or_duplicate(command)
    validated = self._validate_start_time_and_identity(command, peer_ip)
    cloud_class, pose_class = self._resolve_input_types()
    self._verify_published_topics(cloud_class, pose_class)
    session = MappingSession.from_command(validated, self.config)
    self._subscribe_session(session, cloud_class, pose_class)
    self.session = session
    self.state = "armed"
    self._ack_start(session)
```

`_validate_start_time_and_identity` 检查来源 IP、sent_at 时钟偏差、严格最短提前量、map/device/session/collaboration 和参与设备集合；已经迟到的 start 一律拒绝。相同 request_id 幂等返回缓存 ACK。armed 后的 watchdog 单独使用 `start_late_tolerance_seconds` 判断唤醒抖动，超过容差则发送 `START_TIME_MISSED` 并复位。

- [x] **Step 4: 运行开始状态机测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_node -v`  
Expected: PASS 已完成的 start、duplicate、busy、错误来源、话题缺失和统一开始测试。

- [x] **Step 5: 记录阶段检查点，不提交**

保存 ACK 和状态转换测试输出。

### Task 7: ROS 回调、切片封闭和上传

**Files:**
- Modify: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/node.py`
- Modify: `edge_side_pkg/EPGeneral_multi_map/test/test_node.py`

**Interfaces:**
- Consumes: `PoseBuffer`、`SliceCollector`、`preprocess_points` 和 `encode_cloud_chunks`。
- Produces: 带切片元数据的 cloud_chunk 和 slice status。

- [x] **Step 1: 写入采集与上传失败测试**

```python
def test_cloud_is_interpolated_binned_and_uploaded_after_lateness(self):
    node, clock = mapping_node(start_at_ns=10_000, duration_ns=5_000, late_ns=200)
    node._pose_callback(node.token, pose_message(10_000, x=0.0))
    node._pose_callback(node.token, pose_message(12_000, x=2.0))
    node._cloud_callback(node.token, cloud_message(11_000, [[1, 0, 0]]))
    clock.wall_ns = 15_199
    node._watchdog()
    self.assertEqual(cloud_messages(node), [])
    clock.wall_ns = 15_200
    node._watchdog()
    frames = cloud_messages(node)
    self.assertEqual(frames[0]["payload"]["slice_id"], 0)
    self.assertTrue(frames[0]["payload"]["pose_interpolated"])
    self.assertEqual(slice_status(node)["event"], "slice_complete")
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_node.NodeTests.test_cloud_is_interpolated_binned_and_uploaded_after_lateness -v`  
Expected: FAIL，因为 callback 尚未连接切片上传。

- [x] **Step 3: 实现回调和批次发送**

```python
def _cloud_callback(self, token, message):
    with self.lock:
        session = self._active_session(token, accept_states=("mapping", "stopping"))
        session.last_cloud_monotonic = self.clock.monotonic()
        stamp_ns = stamp_to_ns(message.header.stamp)
        if not self._valid_message_stamp(session, stamp_ns):
            session.stats.dropped_invalid += 1
            return
        pose_match = session.pose_buffer.match(stamp_ns, self.config["sync_tolerance_ns"])
        if pose_match is None:
            session.stats.dropped_sync += 1
            return
        frame = synchronized_frame(message, stamp_ns, pose_match)
        session.collector.add(frame)

def _upload_batch(self, session, batch):
    processed = self.slice_processor.process(batch)
    prepared = [self._prepare_frame(frame) for frame in processed.frames]
    for frame_index, prepared_frame in enumerate(prepared):
        self._send_prepared_frame(session, processed, frame_index, len(prepared), prepared_frame)
    self._send_slice_status(session, processed, len(prepared))
    processed.frames[:] = []
```

位姿 callback 在成功解析消息后更新 `last_pose_monotonic` 并写入 `PoseBuffer`。上传频率限制使用点云时间戳而不是 callback 到达时间；超过频率的帧按现有语义丢弃并统计。

- [x] **Step 4: 运行采集上传测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_node -v`  
Expected: PASS，覆盖插值、完整片、空片、迟到丢弃、资源截断、原始消息释放和 sequence 唯一。

- [x] **Step 5: 记录阶段检查点，不提交**

记录每片消息数、包长和内存释放断言。

### Task 8: 统一停止、错误尾片和复位

**Files:**
- Modify: `edge_side_pkg/EPGeneral_multi_map/src/epgeneral_multi_map/node.py`
- Modify: `edge_side_pkg/EPGeneral_multi_map/test/test_node.py`

**Interfaces:**
- Consumes: 活动 MappingSession。
- Produces: partial/error tail、stop_time_missed 状态和 standby 复位。

- [x] **Step 1: 写入停止与超时失败测试**

```python
def test_scheduled_stop_keeps_only_pre_stop_frames_and_resets(self):
    node, clock = mapping_node()
    add_synchronized_cloud(node, stamp_ns=14_000)
    add_synchronized_cloud(node, stamp_ns=16_000)
    node.handle_datagram(stop_command(stop_at_ns=15_000), GROUND_IP)
    clock.wall_ns = 15_000
    node._watchdog()
    self.assertEqual(uploaded_sample_stamps(node), [14_000])
    self.assertTrue(last_slice_metadata(node)["partial"])
    self.assertEqual(node.state, "standby")
    self.assertEqual(node.subscription_count, 0)

def test_input_timeout_uploads_valid_error_tail_then_resets(self):
    node, clock = mapping_node()
    add_synchronized_cloud(node, stamp_ns=11_000)
    clock.monotonic_value += node.config["input_timeout_seconds"] + 0.01
    node._watchdog()
    self.assertTrue(last_slice_metadata(node)["error_tail"])
    self.assertEqual(last_status(node)["state"], "error")
    self.assertEqual(node.state, "standby")

def test_pose_only_activity_cannot_hide_cloud_timeout(self):
    node, clock = mapping_node()
    publish_pose(node)
    clock.monotonic_value += node.config["input_timeout_seconds"] + 0.01
    publish_pose(node)
    node._watchdog()
    self.assertEqual(last_status(node)["error_code"], "INPUT_TIMEOUT")
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_node.NodeTests.test_scheduled_stop_keeps_only_pre_stop_frames_and_resets -v`  
Expected: FAIL，因为停止调度和尾片尚未实现。

- [x] **Step 3: 实现所有终止路径共用清理**

```python
def _finish_session(self, session, stop_at_ns, error_code="", reason=""):
    batch = session.collector.seal_tail(stop_at_ns, error_tail=bool(error_code))
    if batch is not None and batch.frames:
        self._upload_batch(session, batch)
    elif batch is not None:
        self._send_slice_status(session, batch, 0, error_code="EMPTY_SLICE")
    final_state = "error" if error_code else "stopped"
    self._send_session_status(session, final_state, reason, error_code)
    self._unregister(session)
    session.pose_buffer.clear()
    session.collector.clear()
    self.session = None
    self.state = "standby"
```

mapping 开始后，点云和位姿两路输入分别计时；任一路超过 `input_timeout_seconds` 都触发 `INPUT_TIMEOUT`，不能用另一条活跃输入掩盖。迟到 stop 必须先上报 `STOP_TIME_MISSED`、`stop_at_ns` 和 `max_uploaded_stamp_ns`；已发数据不撤回，未来地面站二次截断。

- [x] **Step 4: 运行终止路径测试**

Run: `PYTHONPATH=src python3 -m unittest test.test_node -v`  
Expected: PASS，覆盖正常停止、重复停止、错 session、迟到停止、输入超时、时钟回退、节点 close 和复位。

- [x] **Step 5: 记录阶段检查点，不提交**

保存所有终止路径的订阅数、缓存长度和最终状态断言。

### Task 9: 入口、版本、localhost UDP 和地面站上行契约

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/scripts/epgeneral_multi_map_node.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/scripts/check_version.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/test/test_udp_integration.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/test/test_ground_contract.py`
- Create: `edge_side_pkg/EPGeneral_multi_map/test/test_version_and_entrypoint.py`

**Interfaces:**
- Consumes: 完整 RosMultiMapNode。
- Produces: roslaunch 入口、`__version__="0.1.0"` 和真实 UDP 契约证据。

- [x] **Step 1: 写入入口和 UDP 失败测试**

```python
def test_real_udp_start_slice_stop(self):
    node = start_localhost_node(ephemeral_control_port())
    send_joint_start(node.control_address, start_at_ns=time.time_ns() + 500_000_000)
    publish_fake_pose_and_cloud(node)
    wait_until(lambda: received_event("slice_complete"), timeout=3.0)
    send_stop(node.control_address, stop_at_ns=time.time_ns() + 100_000_000)
    wait_until(lambda: node.state == "standby", timeout=3.0)

def test_python_files_parse_as_python38(self):
    for path in package_python_files():
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 8))
```

- [x] **Step 2: 验证测试先失败**

Run: `PYTHONPATH=src python3 -m unittest test.test_udp_integration test.test_version_and_entrypoint -v`  
Expected: FAIL，因为脚本和集成夹具尚不存在。

- [x] **Step 3: 实现入口和版本一致性检查**

```python
def run():
    import rospkg
    import rospy
    rospy.init_node("epgeneral_multi_map")
    package_path = rospkg.RosPack().get_path("epgeneral_multi_map")
    device_path = rospkg.RosPack().get_path("epgeneral_device_config")
    mapping_file = rospy.get_param(
        "~mapping_config_file", package_path + "/config/multi_mapping.yaml")
    device_file = rospy.get_param(
        "~device_config_file", device_path + "/config/device.yaml")
    node = RosMultiMapNode(rospy, load_config(mapping_file, device_file))
    rospy.on_shutdown(node.close)
    node.start()
    rospy.spin()
```

`test_ground_contract.py` 使用当前 `ccs_monitor.map_building.MapBuildingProtocol` 和 `CloudFrameAssembler` 验证带附加切片字段的单帧仍能重组；不修改地面站代码。

- [x] **Step 4: 运行完整新包测试**

Run: `PYTHONPATH=src python3 -m unittest discover -s test -v`  
Expected: 全部 PASS，localhost 测试只启动一个真实节点，不宣称同机多机器人建图。

- [x] **Step 5: 运行版本检查**

Run: `PYTHONPATH=src python3 scripts/check_version.py`  
Expected: 输出 `epgeneral_multi_map version 0.1.0 is consistent`。

### Task 10: 文档、兼容扩展说明和完整回归

**Files:**
- Create: `edge_side_pkg/EPGeneral_multi_map/README.md`
- Create: `edge_side_pkg/EPGeneral_multi_map/CHANGELOG.md`
- Create: `docs/地面站兼容扩展说明.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `edge_side_pkg/README.md`
- Modify: `docs/EDGE_DEVICE_INTERFACES.md`
- Modify: `docs/DEVELOPMENT_NOTES.md`
- Modify: `docs/EDGE_PACKAGE_DEVELOPMENT_PLAN.md`
- Modify: `需求分析.md`

**Interfaces:**
- Consumes: 已通过测试的新包事实。
- Produces: 部署、协议、地面站后续改造和验收文档。

- [x] **Step 1: 编写地面站兼容扩展说明**

文档必须明确列出：

```text
1. v0.13.1 已有多设备选择、_ActiveJob、多 session、融合算法和地图保存，本次不得重复设计或修改这些能力。
2. start_mapping 保留既有 job_id、role、primary_device_id，并增加 participant_device_ids、start_at_ns、slice_duration_ns。
3. 所有设备必须收到同一个 job_id、参与集合、start_at_ns 和 slice_duration_ns；每设备 session_id 继续独立。
4. stop_mapping 增加 job_id、stop_at_ns，并要求提前下发。
5. CloudFrameAssembler 保存并校验 slice_id、窗口边界、partial、truncated 和位姿依据。
6. 地面站在现有 _ActiveJob 内按 device_id/session_id/slice_id 汇总，等齐或超时后进入既有融合流程。
7. 地面站按 stop_at_ns 二次过滤迟到停止前已上传的越界帧。
8. 首版仍为 UDP 尽力传输；未来 ACK、缺片请求和选择性重传单独设计。
9. v0.13.1 已有 UI 与融合算法属于环境基线，不属于本端侧包的修改交付。
```

- [x] **Step 2: 编写包 README 和 CHANGELOG**

README 必须包含 Noetic/Python 3.8 安装、配置字段、端口互斥、roslaunch、rostopic/ss/tcpdump 诊断、状态机、错误码、资源估算和实机验收步骤。CHANGELOG 固定首版 `0.1.0`。

- [x] **Step 3: 同步公共接口文档**

只在测试事实成立后更新项目文档；明确“代码/自动化测试完成”与“ROS/双机器人实机未验证”，不得写成已完成实机融合。

- [x] **Step 4: 执行完整回归**

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q ccs_monitor run.py tests
$env:PYTHONPATH = "edge_side_pkg/EPGeneral_map_stream/src"
python -m unittest discover -s edge_side_pkg/EPGeneral_map_stream/test -v
$env:PYTHONPATH = "edge_side_pkg/EPGeneral_multi_map/src"
python -m unittest discover -s edge_side_pkg/EPGeneral_multi_map/test -v
Remove-Item Env:PYTHONPATH
```

Expected: 原地面站、旧端侧包和新端侧包测试均通过。

- [x] **Step 5: 核对不可修改项和交付物**

确认 `ccs_monitor`、`config/map_building.json` 和 `EPGeneral_map_stream` 未改变；确认不存在任何未完成占位标记；确认没有生成端侧 PCD、缓存文件或新端口。

## 13. 验收指标

1. 输入包含两个以上不重复参与设备且包含本机 ID 的合法 start 指令，节点必须 ACK accepted 并进入 armed。
2. 输入单设备、重复设备、缺少本机或缺少 collaboration 字段的 start 指令，节点必须拒绝且保持 standby。
3. 在 `start_at_ns - 1 ns` 调用 watchdog，系统不得上传建图帧；在 `start_at_ns` 后必须进入 mapping。
4. 对相同 `start_at_ns`、`slice_duration_ns` 和帧时间戳，两台机器人必须计算相同 slice_id 和窗口边界。
5. 输入点云时刻两侧的合法位姿，输出平移必须按时间比例线性插值，旋转必须为归一化最短弧 SLERP。
6. 点云无法在同步容差内获得位姿时，系统必须只增加 dropped_sync，不上传该帧。
7. 完整窗口结束后 0.2 秒内到达的合法迟到帧必须归入原片；超过宽限的帧必须标记 late 并丢弃。
8. 窗口没有有效帧时，系统必须发送 slice_empty 状态且不得发送 cloud_chunk。
9. 当前片超过帧数、点数或字节上限时，系统必须标记 truncated 并拒绝该片后续帧；下一片仍可正常接收。
10. 提前收到 stop 指令后，系统必须在 stop_at_ns 只上传时间戳小于 stop_at_ns 的 partial 尾片，并回到 standby。
11. 迟到 stop 指令必须上报 STOP_TIME_MISSED、stop_at_ns 和 max_uploaded_stamp_ns，并丢弃所有尚未上传的越界帧。
12. 点云或位姿输入超过 3 秒无数据时，系统必须尽力上传已有错误尾片、发送 error 状态、释放订阅和缓存并回到 standby。
13. 相同 request_id 的重复开始/停止不得创建第二 session、重复订阅或重复释放资源。
14. 每个编码后的 UDP 数据报不得超过 1400 字节；完整帧 CRC32、解压字节数和点数必须一致。
15. 发送完成后 SliceBatch 的 raw PointCloud2 引用必须释放；机器人端不得创建 PCD、切片文件或恢复检查点。
16. 现有距离过滤、体素降采样、频率和点数限制在相同输入下必须保持旧包语义。
17. `EPGeneral_map_stream` 文件内容和既有测试结果不得因本功能改变。
18. 纯 Python 测试必须在 Python 3.8 和当前开发 Python 通过；ROS 构建必须在 Ubuntu 20.04/Noetic/System Python 3.8 验证。
19. localhost UDP 测试必须覆盖 start、armed、mapping、切片上传、stop 和 standby，但不得标记为双机器人实机验收。
20. `docs/地面站兼容扩展说明.md` 必须以 v0.13.1 现有 `_ActiveJob`/多 session/融合为基线，完整列出协议补字段、切片汇总和停止二次截断改动，不得要求重做既有能力。

## 14. 完成定义与阶段门禁

- Step 5：项目负责人明确回复“可以按照该路线开始开发”后完成。
- Step 6：先执行修改前基线测试并记录结果。
- Step 7：必须按 Task 1～10 顺序 TDD 实施；适用时使用 SOP 指定的 ponytail 技能。
- Step 8：执行完整测试、旧功能回归和不可修改项检查。
- Step 9：同步配置、接口、README 和 CHANGELOG。
- Step 10：项目负责人依据第 13 节验收。
- Step 11：只有项目负责人批准后，才在具备 Git 元数据的正确仓库执行 commit 和 push。

未经 Step 5 审批，不得创建 `EPGeneral_multi_map` 或执行 Step 6。
