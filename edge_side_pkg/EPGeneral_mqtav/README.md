# mqtav

<!-- mqtav_VERSION: 0.3.0 -->

当前版本：`v0.3.0`

mqtav 是运行在无人机机载计算机上的 ROS1 功能包。它订阅 MAVROS 健康状态并通过 MQTT 向地面站上报在线状态、飞控链路、电量、飞行模式和任务状态。

## 功能与协议

- 设备 ID 构成 MQTT 客户端 ID：`mqtav-<device_id>`；设备 IP 随每条消息上报。
- 节点以 QoS 1 每秒发布一条 `heartbeat` 和一条 `status`。MQTT 自动以 1 至 60 秒指数退避重连，断线期间不缓存旧遥测。
- 连接后发布 retained `online` presence；MQTT Last Will 为 retained `offline` presence。正常退出也会主动发布 `offline`。
- 状态来自 `/mavros/state` 的 `connected`、`armed`、`system_status`、`mode`，以及 `/mavros/battery` 的 `percentage`、`voltage`、`current`。电量百分比统一为 0 到 100 的数值。
- 默认未启用任务状态。启用后可从任意 ROS 消息的安全点分字段路径提取任务字段；未收到、读取失败或未配置时为 `unknown`。

主题由配置展开，例如设备 `UAV_001` 的默认主题为：

| 用途 | MQTT 主题 | 保留 |
| --- | --- | --- |
| 在线状态 | `mqtav/UAV_001/presence` | 是 |
| 心跳 | `mqtav/UAV_001/heartbeat` | 否 |
| 健康状态 | `mqtav/UAV_001/status` | 否 |

心跳和健康状态使用相同 JSON 信封：

```json
{
  "schema_version": "1.0",
  "message_type": "status",
  "timestamp": "2026-07-31T09:30:00.000Z",
  "sequence": 42,
  "device": {"id": "UAV_001", "ip": "192.168.151.250"},
  "health": {
    "fcu_connected": true,
    "armed": false,
    "system_status": 3,
    "flight_mode": "AUTO.MISSION",
    "battery": {"percentage": 76.5, "voltage": 15.8, "current": 4.2},
    "mission_status": "unknown"
  }
}
```

presence 消息含有相同的 `schema_version`、`timestamp` 和 `device` 字段，并使用 `message_type: "presence"` 与 `status: "online"` 或 `"offline"`。

## 配置

编辑 `config/config.yaml` 和共享的 `epgeneral_device_config/config/device.yaml` 后再启动节点。以下字段均为必填，除 `ros.mission` 可保持 `enabled: false` 外：

```yaml
mqtt:
  ground_station_ip: "192.168.20.10"
  port: 1883
  client_id_prefix: "mqtav-"
  qos: 1
  keepalive_seconds: 10
  heartbeat_hz: 1
  telemetry_hz: 1
  topics:
    presence: "mqtav/{device_id}/presence"
    heartbeat: "mqtav/{device_id}/heartbeat"
    status: "mqtav/{device_id}/status"

ros:
  node_name: "mqtav"
  state:
    topic: "/mavros/state"
    message_type: "mavros_msgs/State"
  battery:
    topic: "/mavros/battery"
    message_type: "sensor_msgs/BatteryState"
  mission:
    enabled: false
    topic: "/mission/status"
    message_type: "std_msgs/String"
    field_path: "data"
```

设备身份文件内容为：

```yaml
schema_version: 1
device:
  id: "UAV_001"
  ip: "192.168.151.250"
```

`ground_station_ip` 必须是地面站 MQTT Broker 的可达 IP。当前版本仅支持内网明文 MQTT，不提供账号密码、TLS、下行控制或飞行控制指令。主题模板只允许 `{device_id}` 占位符，且不得含 MQTT 通配符。

## Ubuntu 18.04 / ROS Melodic + Python 3.6.9 部署

确保 MAVROS 已能连接飞控，然后在机载计算机执行：

```bash
sudo apt update
sudo apt install python3-paho-mqtt python3-yaml python3-catkin-pkg python3-rospkg ros-melodic-mavros ros-melodic-mavros-extras
source /opt/ros/melodic/setup.bash
python3 --version  # 应为 Python 3.6.9
mkdir -p ~/catkin_ws/src
cp -r /path/to/CCS_dev/edge_side_pkg ~/catkin_ws/src/edge_side_pkg
cd ~/catkin_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch mqtav mqtav.launch \
  config_file:="$(rospack find mqtav)/config/config.yaml" \
  device_config_file:="$(rospack find epgeneral_device_config)/config/device.yaml"
```

可通过启动参数把日志写入指定目录：

```bash
roslaunch mqtav mqtav.launch log_dir:=/var/log/mqtav
```

默认日志为 `~/.ros/log/mqtav/mqtav.log`，单个文件达到 10 MiB 后轮转，最多保留 5 份历史日志。每条日志均同步刷盘并输出到 `roslaunch` 控制台；启动、配置加载、订阅、连接、断联、重连失败、数据发送、每次心跳、关闭和未捕获异常均有记录。

### Melodic Python 解释器报错

ROS Melodic 默认会使用 Python 2.7。mqtav v0.3.0 已兼容 Python 3.6.9，但必须让整个 catkin 工作空间使用 Python 3 构建。若启动日志出现 `devel/lib/python2.7`，说明仍在使用旧缓存。清理旧构建产物后重新编译：

```bash
cd ~/catkin_ws
source /opt/ros/melodic/setup.bash
python3 --version  # 预期 Python 3.6.9
rm -rf build devel
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
python3 -c "import mqtav; print(mqtav.get_version())"
roslaunch mqtav mqtav.launch
```

重建后，`devel/lib/python3/dist-packages/mqtav` 应存在，启动脚本的第一行应为 `#!/usr/bin/python3`。同一工作空间中的 Python ROS 节点也应与 Python 3 配套；不能混用 `devel/lib/python2.7` 和本包。

## 验证

使用 `catkin build` 而非 `catkin_make` 时，请在工作空间根目录执行：

```bash
source /opt/ros/melodic/setup.bash
catkin clean -y
catkin config --cmake-args -DPYTHON_EXECUTABLE=/usr/bin/python3
catkin build mqtav
source devel/setup.bash
```

在包目录中运行：

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
PYTHONPATH=src python3 scripts/check_version.py
```

在 catkin 工作空间中可进一步运行 `catkin_make run_tests_mqtav`。生产部署前，请使用本地 Mosquitto 或地面站 Broker 验证三个主题的 QoS 1 消息、异常断电后的 Last Will，以及 MAVROS 两个默认话题。
