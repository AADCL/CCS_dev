# EPGeneral_multi_map 0.1.0

`EPGeneral_multi_map` 是 ROS1 Noetic 机器人端联合建图采集包。它在每台机器人上独立运行，接收地面站统一下发的联合任务和绝对开始/停止时间，将 PointCloud2 与通用配置的位姿消息按时间配准，形成相同绝对时间窗的切片，并通过 UDP 上传。地图融合仍由指控平台完成。

本包不能在同一台计算机上启动多个机器人实例来宣称联合建图；localhost 自动测试只验证单节点协议链路。首版不保存 PCD、切片文件或恢复检查点，不新增预处理/滤波算法，也不提供纯 TF 查询模式。

## 环境与安装

- Ubuntu 20.04、ROS1 Noetic。
- 系统 Python 3.8 或更高版本；Catkin 必须使用同一个 Python 解释器。
- `rospy`、`roslib`、`sensor_msgs`、`nav_msgs`、PyYAML、MessagePack、NumPy。
- 同一工作空间中的 `epgeneral_device_config`，其 `config/device.yaml` 提供本机设备 ID/IP。

```bash
sudo apt update
sudo apt install python3-yaml python3-msgpack python3-numpy python3-catkin-pkg \
  python3-rospkg ros-noetic-roslib ros-noetic-sensor-msgs ros-noetic-nav-msgs
mkdir -p ~/catkin_ws/src
cp -r /path/to/CCS_dev/edge_side_pkg/EPGeneral_multi_map ~/catkin_ws/src/
cp -r /path/to/CCS_dev/edge_side_pkg/EPGeneral_device_config ~/catkin_ws/src/
cd ~/catkin_ws
source /opt/ros/noetic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

若现场 Python 高于 3.8，仍需确认 ROS Python 包、PyYAML、MessagePack 和 NumPy均安装到 Catkin 实际使用的解释器中。

## 配置

默认配置为 `config/multi_mapping.yaml`，设备身份复用 `epgeneral_device_config/config/device.yaml`。

| 分组 | 关键字段 | 说明 |
| --- | --- | --- |
| `network` | `bind_host/control_port` | 控制监听地址，默认 UDP 14561 |
| `network` | `ground_station_ip/data_port` | 唯一可信地面站和上行端口，默认 UDP 14562 |
| `ros.cloud` | `topic/message_type` | 点云话题；类型固定为 `sensor_msgs/PointCloud2` |
| `ros.pose` | `topic/message_type` | 位姿话题及通用 ROS 消息类型 |
| `ros.pose` | `position_path/orientation_path` | 位姿消息的点分属性路径 |
| `ros.frames` | `map/body/sensor` | 地图、机体、传感器 frame 契约 |
| `body_from_sensor` | XYZ + XYZW | 静态 `body <- sensor` 外参 |
| `sync` | tolerance/buffer/clock/rollback | 位姿插值和时间有效性边界 |
| `slicing` | default/min/max/late | 地面站可选切片时长及迟到宽限 |
| `preprocess` | range/voxel/rate | 完全复用旧包距离、体素、频率和限点语义 |
| `timeouts` | input/clock/start | 输入断流、命令时钟和统一启动门限 |
| `limits` | frame/slice/datagram | 点数、解压字节、切片资源和 1400 字节上限 |

控制端口和数据端口必须不同；同机上还必须避免与 `epgeneral_map_stream` 等包重复占用 UDP 14561。正常部署应只启动所选建图包之一。

现场位姿类型未知时，只修改 `message_type`、`position_path` 和 `orientation_path`。消息必须带 `header.stamp`、`header.frame_id`；若含 `child_frame_id`，它必须与配置的 body frame 一致。

## 启动

```bash
roslaunch epgeneral_multi_map epgeneral_multi_map.launch
```

覆盖配置文件：

```bash
roslaunch epgeneral_multi_map epgeneral_multi_map.launch \
  mapping_config_file:=/absolute/path/multi_mapping.yaml \
  device_config_file:=/absolute/path/device.yaml
```

状态机为 `standby → armed → mapping → stopping → stopped/error → standby`。合法 start 必须至少包含相同的 `job_id`、参与设备集合、`start_at_ns` 和 `slice_duration_ns`，且至少两台不同设备中包含本机。每台设备保留独立 `session_id`。stop 必须包含相同 `job_id` 和提前下发的 `stop_at_ns`；非完整最后一片也会上传并标记 `partial`，错误结束则额外标记 `error_tail`。

## 传输与资源

- 每个点处理后为 XYZ little-endian float32，即 12 字节；例如 200,000 点的未压缩上限约 2.29 MiB。
- 默认每片最多 50 帧、5,000,000 点和 128 MiB 原始输入引用；任一上限触发时只截断当前片。
- zlib 数据按实际 MessagePack 封包动态切分，每个 UDP 数据报不超过 1400 字节。
- UDP 为尽力传输：首版没有 ACK、缺片请求或选择性重传。
- 上传后立即清空 SliceBatch 中的原始 PointCloud2 引用，不在端侧落盘。

主要错误码：`COLLABORATION_REQUIRED`、`PARTICIPANT_SET_INVALID`、`CLOCK_UNSYNCED`、`START_LEAD_TOO_SHORT`、`START_TIME_MISSED`、`SESSION_MISMATCH`、`STOP_TIME_MISSED`、`INPUT_TIMEOUT`、`EMPTY_SLICE`、`SLICE_TRUNCATED`、`SENSOR_UNAVAILABLE`、`POSE_UNAVAILABLE` 和 `INTERNAL_ERROR`。

## 诊断

```bash
rostopic type /livox/lidar
rostopic hz /livox/lidar
rostopic type /Odometry
rostopic hz /Odometry
rostopic echo -n 1 /Odometry/header
ss -lunp | grep -E '14561|14562'
sudo tcpdump -ni any 'udp port 14561 or udp port 14562'
chronyc tracking
```

若 start 被拒绝，依次核对设备 ID/IP、地面站源 IP、两个 ROS 话题及类型、frame、NTP/chrony、开始提前量和端口占用。若出现 `INPUT_TIMEOUT`，点云和位姿必须分别检查；其中一路活跃不会掩盖另一路断流。

## 测试与实机验收

纯 Python：

```bash
cd ~/catkin_ws/src/EPGeneral_multi_map
PYTHONPATH=src python3 -m unittest discover -s test -v
PYTHONPATH=src python3 scripts/check_version.py
```

实机验收至少需要两台不同机器人和一台地面站：同步 UTC；两机使用相同 `job_id`、参与集合、开始时间和切片时长；确认不同 `session_id`；检查相同时间戳计算出相同 `slice_id`/窗口；提前 stop；确认最后不完整尾片上传后两端回到 standby；最后在地面站既有融合流程检查结果。当前 Windows 开发环境已完成纯 Python 和 localhost 单节点测试，但尚未完成 Ubuntu 20.04/Noetic Catkin、真实 ROS 话题或双机器人实机验收。

当前地面站 v0.13.1 尚未下发本包严格要求的全部联合时间字段，接入前必须完成 [地面站兼容扩展说明](../../docs/地面站兼容扩展说明.md) 中的协议补字段；无需重做其已有多设备 session、融合算法和地图保存能力。
