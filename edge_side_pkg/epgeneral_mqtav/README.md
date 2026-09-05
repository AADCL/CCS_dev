# epgeneral_mqtav

配套 CCS 0.23.1：[完整使用手册](../documents/USER_MANUAL.md) · [设备内接口与参数](../documents/INTERFACE_REFERENCE.md)。包级 launch 默认读取共享配置包；一键脚本显式读取工作空间 `config/<profile>`，修改后需重启。

<!-- epgeneral_mqtav_VERSION: 0.4.1 -->

当前版本：`v0.4.1`

`epgeneral_mqtav` 是运行在端侧计算机上的 ROS1 功能包。它订阅 ROS 健康状态并通过 MQTT 向地面站上报在线状态、设备链路、电量、模式和任务状态。为兼容既有地面站协议，MQTT topic 根和客户端 ID 前缀继续使用 `mqtav`。

## 功能与协议

- 设备 ID 构成 MQTT 客户端 ID：`mqtav-<device_id>`；设备 IP 随每条消息上报。
- 节点以 QoS 1 每秒发布一条 `heartbeat` 和一条 `status`。MQTT 自动以 1 至 60 秒指数退避重连，断线期间不缓存旧遥测。
- 每次节点启动生成新的 `session_id`，同一启动周期的 presence、heartbeat 和 status 共用该值；地面站据此安全重置 sequence 窗口。
- 连接后发布 retained `online` presence；MQTT Last Will 为 retained `offline` presence。正常退出也会主动发布 `offline`。
- 状态来自 `/mavros/state` 的 `connected`、`armed`、`system_status`、`mode`，以及 `/mavros/battery` 的 `percentage`、`voltage`、`current`。电量百分比统一为 0 到 100 的数值。
- `ros.state.mapping` 可将上述四个健康字段映射到任意 ROS 消息字段；值为 `null` 时上报不可用。未配置 mapping 时继续使用 MAVROS 同名字段。
- `ros.state.connected_on_message` 可用消息新鲜度表示自定义底盘在线状态，配合 `timeout_seconds` 超时置为离线。
- `ros.battery.mapping` 支持从任意 ROS 消息字段读取 `percentage`、`voltage`、`current`；值为 `null` 时该项上报不可用。
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
  "session_id": "a4d3...",
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

编辑共享的 `epgeneral_device_config/config/epgeneral_mqtav.yaml` 和 `device.yaml` 后再启动节点。以下字段均为必填；`ros.mission` 和 `ros.battery` 可设置 `enabled: false`：

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
  node_name: "epgeneral_mqtav"
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

## Ubuntu 20.04 / ROS Noetic + Python 3 部署

确保 MAVROS 已能连接飞控，然后在机载计算机执行：

```bash
sudo apt update
sudo apt install python3-paho-mqtt python3-yaml python3-catkin-pkg python3-rospkg
source /opt/ros/noetic/setup.bash
python3 --version  # 使用 ROS Noetic 的系统 Python 3
mkdir -p ~/catkin_ws/src
cp -a /path/to/CCS_dev/edge_side_pkg/EPGeneral_device_config ~/catkin_ws/src/
cp -a /path/to/CCS_dev/edge_side_pkg/epgeneral_mqtav ~/catkin_ws/src/
cd ~/catkin_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="$(rospack find epgeneral_device_config)/config/epgeneral_mqtav.yaml" \
  device_config_file:="$(rospack find epgeneral_device_config)/config/device.yaml"
```

仅使用默认 MAVROS 配置的设备需要另外安装对应 ROS 发行版的 `mavros_msgs`；Go2 等通用字段映射不依赖 MAVROS。

可通过启动参数把日志写入指定目录：

```bash
roslaunch epgeneral_mqtav epgeneral_mqtav.launch log_dir:=/var/log/epgeneral_mqtav
```

默认日志为 `~/.ros/log/epgeneral_mqtav/epgeneral_mqtav.log`，单个文件达到 10 MiB 后轮转，最多保留 5 份历史日志。每条日志均同步刷盘并输出到 `roslaunch` 控制台；启动、配置加载、订阅、连接、断联、重连失败、数据发送、每次心跳、关闭和未捕获异常均有记录。

### Python 解释器与历史兼容

ROS Melodic 默认会使用 Python 2.7。`epgeneral_mqtav` v0.3.0 已兼容 Python 3.6.9，但必须让整个 catkin 工作空间使用 Python 3 构建。若启动日志出现 `devel/lib/python2.7`，说明仍在使用旧缓存。清理旧构建产物后重新编译：

```bash
cd ~/catkin_ws
source /opt/ros/noetic/setup.bash
python3 --version  # 使用系统 Python 3
mv build "build.backup-$(date +%Y%m%d-%H%M%S)"
mv devel "devel.backup-$(date +%Y%m%d-%H%M%S)"
catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
python3 -c "import epgeneral_mqtav; print(epgeneral_mqtav.get_version())"
roslaunch epgeneral_mqtav epgeneral_mqtav.launch
```

重建后，`devel/lib/python3/dist-packages/epgeneral_mqtav` 应存在，启动脚本的第一行应为 `#!/usr/bin/python3`。同一工作空间中的 Python ROS 节点也应与 Python 3 配套；不能混用 `devel/lib/python2.7` 和本包。

## 验证

使用 `catkin build` 而非 `catkin_make` 时，请在工作空间根目录执行：

```bash
source /opt/ros/noetic/setup.bash
catkin clean -y
catkin config --cmake-args -DPYTHON_EXECUTABLE=/usr/bin/python3
catkin build epgeneral_mqtav
source devel/setup.bash
```

在包目录中运行：

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
PYTHONPATH=src python3 scripts/check_version.py
```

在 catkin 工作空间中可进一步运行 `catkin_make run_tests_mqtav`。生产部署前，请使用本地 Mosquitto 或地面站 Broker 验证三个主题的 QoS 1 消息、异常断电后的 Last Will，以及 MAVROS 两个默认话题。
