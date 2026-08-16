# epgeneral_map_stream

<!-- epgeneral_map_stream_VERSION: 0.1.0 -->

版本：`v0.1.0`。

`epgeneral_map_stream` 是 ROS Melodic/Python 3.6.9 端侧实时建图数据包。节点在 standby 状态监听地面站 UDP 14561 指令；建图期间同步 PointCloud2 与位姿，以 `ccs-map-stream-v1` 向指令指定且经配置校验的 UDP 14562 上传分片点云、同步位姿和静态外参。

## 数据流

1. start 前不订阅点云和位姿，仅保留控制 socket。
2. start 校验来源 IP、设备/地图/会话、返回地址、点格式和 ROS 话题后创建订阅，返回 ACK。
3. 点云剔除非有限值和距离范围外点，按协商体素降采样，和 50 ms 内最近位姿组成一帧。
4. XYZ 编码为 little-endian float32，整帧 zlib、整帧 CRC32，再分片到每个最终 MessagePack 数据报不超过 1400 字节。
5. stop 使旧回调失效，释放订阅和缓存，返回 ACK/status 后回到 standby。

点云保持 sensor 坐标系。每帧携带 `map_from_body` 与配置中的 `body_from_sensor`，不发布 ROS TF，不在端侧累计地图。

## 安装

```bash
sudo apt update
sudo apt install python3-yaml python3-msgpack python3-numpy python3-catkin-pkg python3-rospkg \
  ros-melodic-sensor-msgs ros-melodic-nav-msgs
cd ~/c3po_ctrl_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

编辑 `config/mapping.yaml`，保证地面站 IP、PointCloud2/位姿话题、frame 名称和 `body_from_sensor` 标定正确，再启动：

```bash
roslaunch epgeneral_map_stream epgeneral_map_stream.launch
```

可覆盖 `mapping_config_file` 与 `device_config_file` launch 参数。设备 ID/IP 始终来自 `epgeneral_device_config/config/device.yaml`。

## 验证与排障

```bash
rostopic type /livox/lidar
rostopic type /Odometry
rostopic hz /livox/lidar
rostopic hz /Odometry
sudo tcpdump -ni any 'udp port 14561 or udp port 14562'
PYTHONPATH=src python3 -m unittest discover -s test -v
python3 scripts/check_version.py
```

输入话题必须已经发布且类型与 YAML 一致。点云 `header.frame_id` 必须等于 sensor frame；位姿 `header.frame_id` 和 `child_frame_id` 必须等于 map/body frame。控制指令来源 IP、`return_host` 和 YAML 地面站 IP 必须一致，`return_port` 必须等于配置的数据端口。

包仅适用于可信局域网，不提供认证、加密、重传或拥塞控制。首版不支持 Livox CustomMsg、intensity、RGB、ring 或逐点时间戳。
