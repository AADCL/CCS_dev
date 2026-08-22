# epgeneral_map_stream

<!-- epgeneral_map_stream_VERSION: 0.6.0 -->

版本：`v0.6.0`。

`epgeneral_map_stream` 是 ROS Noetic/Python 3 端侧遥控建图包，使用独立的
`ccs-map-stream-v2`。节点监听平台 UDP 14561，向协商得到的平台 UDP 14562
发送准备结果、ACK、状态和 PCD 分片描述符；实时预览 PCD 与最终成果均通过
TCP 14600 提供带令牌的 HTTP 下载。最终 PCD、PGM 与 ROS `map.yaml` 打包后，
通过带短期令牌且支持 HTTP Range 的 TCP 14600 服务提供下载。

v2 不自动回退 v1。实时点云仅用于平台预览，最终地图以成果 ZIP 为准。

## 流程

1. `prepare_mapping` 检查原始 `/livox/lidar`、`/livox/imu` 的类型、新鲜数据、
   LiDAR frame、成果目录空间以及 FAST_LIO/PGM 集成，逐项返回结果；IMU 只要求有新数据。
   使用 `restart_active=true` 重新协商时，ready/starting/mapping/error 会话会通过
   `abort_fast_lio.sh` 丢弃实时数据后重新准备；成果生成和服务阶段不可中断。
2. `start_mapping` 协商 `preview_transport=pcd_fragment_http` 和默认 1 秒分片周期，立即发送
   `session_status=starting`，再执行 `start_fast_lio.sh`。输出就绪后，有界后台队列把同步点云
   转换到 `lio_odom`，原子写入不可变二进制 XYZ PCD，并用 `cloud_fragment_ready` 发布
   URL、大小和 SHA-256。平台校验并显示后发送 `cloud_fragment_ack`，端侧删除临时文件。
   两路 FAST_LIO 输出按 header 时间戳匹配；点云短暂先到时最多缓存 3 帧等待位姿，
   只有位姿时间越过 50 ms 同步窗口或队列溢出才丢帧。
3. `stop_mapping` 立即停止订阅并返回 ACK；后台通过 `stop_fast_lio.sh` 停止
   FAST_LIO，将本 session 新生成的 `artifacts.generated_pcd_path` 快照到会话目录，
   再由 `generate_pgm.sh` 归档既有公开地图、原子更新 `artifacts.source_pcd_path`
   并生成 PGM/YAML。固定输出 PCD 必须在本 session 启动后发生指纹变化，否则作为旧成果拒绝；
   PGM 失败时恢复原公开 PCD、PGM 和 YAML。
4. 节点发送 `artifact_status=ready`。ZIP 在令牌过期前可重复完整下载或 Range
   续传，同时控制服务已经可以接受下一次准备协商。

## 配置 FAST_LIO 与 PGM 生成器

必须按设备修改 `config/mapping.yaml` 中的话题、frame、静态外参、工作目录和
`integrations.fast_lio`、`integrations.pgm`。prepare 的 `map_generation`
会检查 setup、ROS 包、launch 和 FAST_LIO 固定输出目录，失败时不会进入 start。

Python 节点仅以参数数组调用仓库提供的四个 Bash 包装器，不执行 shell 字符串：

```text
scripts/start_fast_lio.sh
scripts/stop_fast_lio.sh
scripts/abort_fast_lio.sh
scripts/generate_pgm.sh
```

包装器负责 source 指定工作空间、调用 roslaunch、管理 FAST_LIO 进程组/PID 和
限制运行时间。launch 参数允许的模板变量为：

```text
{map_id} {device_id} {session_id} {session_dir}
{pcd_path} {pgm_path} {yaml_path}
```

FAST_LIO start 必须在启动超时内产生输出。Go2 MID360 profile 使用本包的
`fast_lio_mapping.launch` 加载 `go2_bringup` 参数并启用退出保存；其 setup 必须是
同时 overlay 导航工作区的 edge workspace。固定输出写入 `artifacts.generated_pcd_path`，
验证为当前 session 后才发布到 `artifacts.source_pcd_path`。PGM launch 以前台退出码表示完成。
会话文件为：

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
rostopic type /livox/imu
rostopic type /lio/cloud_registered_body
rostopic type /lio/odometry
rostopic hz /livox/lidar
rostopic hz /livox/imu
rostopic hz /lio/cloud_registered_body
rostopic hz /lio/odometry
ss -lntup | grep -E '14561|14600'
PYTHONPATH=src python3 -m unittest discover -s test -v
python3 scripts/check_version.py
```

包面向可信局域网，不提供 TLS。HTTP 令牌是短期随机访问能力，不能替代网络
隔离。目标设备必须使用 NTP/chrony 与平台同步 UTC。
