# ros_task_control

<!-- ROS_TASK_CONTROL_VERSION: 0.1.0 -->

版本：`v0.1.0`。面向 Ubuntu 18.04、ROS Melodic 和 Python 3.6.9，实现 `ccs-task-control-v1` 的端侧任务接收、XML 原子持久化、UTC 调度协调及状态反馈。

节点监听 UDP 14563，向地面站 UDP 14564 发送 ACK、1 Hz 心跳、任务状态和航点进度。任务包不会解锁飞控或直接调用 MAVROS；设备控制节点通过 `/ros_task_control/execution_command` 和 `/ros_task_control/execution_feedback` 接入。

## 状态与数据

- 接收状态：`standby -> receiving -> ready`，错误清理后回到已有任务的 ready 或 standby。
- 执行状态：`scheduling -> scheduled -> running -> completed/stopped/failed`。
- 轨迹网络内容保持 zlib JSON；提交后写入 `~/.ros/ros_task_control/tasks/<ID哈希>/trajectory.xml`。
- XML 保存任务、子任务、设备、修订、CRC、地图/frame、速度、延迟和有序 XYZ 航点。一次原子替换失败不会破坏旧修订。
- 同一设备只允许一个 execution；进程重启不会恢复运动，会向 ROS 适配器发布 CANCEL 或 STOP。

ROS command 包含 action、request/task/subtask/device/execution ID、revision、XML 路径、frame 和 UTC 启动时间。feedback 必须回传相同 ID/revision/request ID，以及 scheduled/running/终态、航点和位置。

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
roslaunch ros_task_control ros_task_control.launch
```

启动前修改 `config/task_control.yaml` 的地面站 IP，并保证 `edge_device_config/config/device.yaml` 与地面站设备 ID/IP 一致。设备和地面站都应使用 NTP；放行端侧 UDP 14563 和地面站 UDP 14564。

```bash
rostopic info /ros_task_control/execution_command
rostopic echo /ros_task_control/execution_feedback
sudo tcpdump -ni any 'udp port 14563 or udp port 14564'
PYTHONPATH=src python3 -m unittest discover -s test -v
python3 scripts/check_version.py
```

协议运行于可信局域网，不提供认证、加密、可靠流传输或拥塞控制。轨迹坐标为地图 frame 下的局部 ENU 米制 XYZ；跨地图 TF、经纬度、避障和动力学控制由设备专属执行节点负责。
