# epgeneral_map_stream

<!-- epgeneral_map_stream_VERSION: 0.2.0 -->

版本：`v0.2.0`。

`epgeneral_map_stream` 是 ROS Noetic/Python 3 端侧遥控建图包，使用独立的
`ccs-map-stream-v2`。节点监听平台 UDP 14561，向协商得到的平台 UDP 14562
发送准备结果、ACK、状态和实时点云；最终 PCD、PGM 与 ROS `map.yaml` 打包后，
通过带短期令牌且支持 HTTP Range 的 TCP 14600 服务提供下载。

v2 不自动回退 v1。实时点云仅用于平台预览，最终地图以成果 ZIP 为准。

## 流程

1. `prepare_mapping` 检查配置话题的类型和新鲜数据、frame、成果目录空间以及
   外部 start/stop 适配器，逐项返回 `prepare_result`。
2. `start_mapping` 创建 ROS 订阅并执行 start 适配器。每个采样窗口把同步点云
   重投影到最后一帧传感器坐标系，经距离过滤和体素降采样后使用
   XYZ float32 little-endian、zlib、CRC32 和 1400 字节 MessagePack 分片上传。
3. `stop_mapping` 立即停止订阅并返回 ACK，后台执行 stop 适配器，等待 PCD、
   PGM、YAML 文件稳定，验证并生成带 manifest 和 SHA-256 的 ZIP。
4. 节点发送 `artifact_status=ready`。ZIP 在令牌过期前可重复完整下载或 Range
   续传，同时控制服务已经可以接受下一次准备协商。

## 配置外部建图程序

必须先修改 `config/mapping.yaml` 中的设备话题、frame、静态外参、工作目录和
命令适配器。仓库中的 `/usr/local/bin/ccs-mapping-start` 与
`ccs-mapping-stop` 是安全占位名称，不提供真实 SLAM 实现；未安装时 prepare 的
`map_generation` 检查会明确失败。

命令以参数数组直接执行，绝不经 shell。允许的模板变量为：

```text
{map_id} {device_id} {session_id} {session_dir}
{pcd_path} {pgm_path} {yaml_path}
```

start/stop 命令必须快速返回。stop 命令只负责触发生成，随后由节点等待以下
会话文件：

```text
<workspace_root>/<session_id>/map.pcd
<workspace_root>/<session_id>/map.pgm
<workspace_root>/<session_id>/map.yaml
```

YAML 的 `image` 必须引用 `map.pgm`，并包含有效的 `resolution`、三元素
`origin`、`occupied_thresh` 和 `free_thresh`。

## 安装与运行

```bash
sudo apt update
sudo apt install python3-yaml python3-msgpack python3-numpy python3-catkin-pkg \
  python3-rospkg ros-noetic-sensor-msgs ros-noetic-nav-msgs
sudo install -d -o "$USER" -g "$USER" /var/lib/ccs/map_stream
cd ~/catkin_ws
source /opt/ros/noetic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch epgeneral_map_stream epgeneral_map_stream.launch
```

设备 ID/IP 始终来自 `epgeneral_device_config/config/device.yaml`。防火墙需允许
平台访问端侧 UDP 14561 和 TCP 14600，并允许端侧发往平台 UDP 14562。

## 验证

```bash
rostopic type /livox/lidar
rostopic type /Odometry
rostopic hz /livox/lidar
rostopic hz /Odometry
ss -lntup | grep -E '14561|14600'
PYTHONPATH=src python3 -m unittest discover -s test -v
python3 scripts/check_version.py
```

包面向可信局域网，不提供 TLS。HTTP 令牌是短期随机访问能力，不能替代网络
隔离。目标设备必须使用 NTP/chrony 与平台同步 UTC。
