# epgeneral_task_control

<!-- epgeneral_task_control_VERSION: 0.4.4 -->

版本：`v0.4.4`。运行配置统一由 `epgeneral_device_config/config/task_control.yaml` 提供。Scout Mini 通过 `/fastlio_odom`、实时 `map<-odom` TF 和 `/move_base/goal` 执行有序地图航点；任务目录默认位于 `~/ccs_edge_ws/mission/`。导航适配器持有 `tf2_ros.TransformListener`，持续接收 `/tf` 和 `/tf_static`。

任务准备阶段会使用导航 `map.yaml` 和 PGM 检查全部航点。地图外、障碍区或未知区航点返回 `WAYPOINT_NOT_TRAVERSABLE`，不会进入 `ready`。运行期 `move_base` 规划失败返回 `NAVIGATION_PLAN_FAILED` 并保留 action 状态文本。

节点监听 UDP 14563，向地面站 UDP 14564 发送 ACK、1 Hz 心跳、任务状态和航点进度。任务文件完整提交后，Scout 适配器启动并保持 `scout_navigation navigation_teb.launch map_name:=<map_id>`；执行和常规停止只发送或取消目标，删除、急停和节点关闭才停止导航进程。

## 状态与数据

- 接收状态：`no_task -> receiving -> received（导航准备中） -> ready`，错误进入 `failed` 并自动重试准备，急停依次进入 `emergency_stop -> no_task`。
- 执行状态：`scheduling -> scheduled -> running -> completed/stopped/failed`。
- 轨迹网络内容保持 zlib JSON；v2 清单和子任务 JSON 写入 `~/ccs_edge_ws/mission/<task_id>/`，执行兼容 XML 写入同一 mission 根目录下的 `<ID哈希>/trajectory.xml`。
- XML 保存任务、子任务、设备、修订、CRC、地图/frame、速度、延迟和有序 XYZ 航点。一次原子替换失败不会破坏旧修订。
- 同一设备只允许一个 execution；进程重启不会恢复运动，会向 ROS 适配器发布 CANCEL 或 STOP。
- Scout 默认要求每次准备在 25 秒内连接 `/move_base`，单航点超时 300 秒；准备期间每秒反馈状态，失败后按配置周期重试。
- 执行前必须同时存在有效的实时 `/fastlio_odom` 和 `map<-odom` TF；仅有历史 `relocalization.json` 不允许启动导航。TF 查询或位姿转换异常统一反馈 `LOCALIZATION_UNAVAILABLE`，不会从 ROS callback 泄漏异常。

ROS command 包含 `PREPARE/SCHEDULE/CANCEL/STOP/UNLOAD`、request/task/subtask/device/execution ID、revision、XML 路径、map/frame 和 UTC 启动时间。feedback 必须回传相同 ID/revision/request ID；准备阶段返回 preparing/ready/failed，执行阶段返回 scheduled/running/终态、航点和位置。

`ros.status_topic` 额外发布 latched `std_msgs/String`，内容为当前接收或执行状态。Go2 profile 将其设置为 `/qrd/QRD_001/task_status`，供 MQTT 健康状态订阅。

## 安装与启动

```bash
sudo apt update
sudo apt install python3-yaml python3-msgpack python3-catkin-pkg python3-rospkg \
  ros-melodic-geometry-msgs
cd ~/c3po_ctrl_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch epgeneral_task_control epgeneral_task_control.launch
```

启动前修改 `epgeneral_device_config/config/task_control.yaml` 的地面站 IP，并保证 `epgeneral_device_config/config/device.yaml` 与地面站设备 ID/IP 一致。设备和地面站都应使用 NTP；放行端侧 UDP 14563 和地面站 UDP 14564。

```bash
rostopic info /epgeneral_task_control/execution_command
rostopic echo /epgeneral_task_control/execution_feedback
sudo tcpdump -ni any 'udp port 14563 or udp port 14564'
PYTHONPATH=src python3 -m unittest discover -s test -v
python3 scripts/check_version.py
```

协议运行于可信局域网，不提供认证、加密、可靠流传输或拥塞控制。轨迹坐标为地图 frame 下的局部 ENU 米制 XYZ；跨地图 TF、经纬度、避障和动力学控制由设备专属执行节点负责。
