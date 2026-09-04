# 空地 AGV 端侧部署说明

## 设备与边界

- 设备：`AGV_001`，端侧 IP `192.168.50.130`
- 地面站：`192.168.50.101`
- 系统：Jetson ARM64、Ubuntu 20.04、ROS Noetic
- CCS 工作空间：`/home/bitcq/ccs_edge_ws`
- 车辆工作空间：`/home/bitcq/catkin_ws`

部署过程不修改原始 `car_bringup/manual_mapping.launch` 或 `save_mapping.launch`，不使用明文凭据，不发送任何运动、解锁、模式或航点指令。

## 一键启动组件

`start_ccs_edge_dev.sh` 依次启动 MAVROS、Livox MID-360、MQTT、UDP 遥测、map-stream、A8 Mini、SRT、stage manager，最后执行：

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

该 launch 只常驻 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster`，统一发布 `odom -> camera_init` 和 `body -> base_link`。FAST-LIO 不在开机阶段启动，收到建图或重定位阶段指令后才由 stage manager 启动。

stage manager 的启动命令仍为：

```bash
rosrun car_bringup ground_air_stage_manager_node.py
```

它只管理建图/重定位阶段和会话归属，不持有静态 TF。A8/SRT 可降级；其余必需节点或任一静态 TF 节点退出会触发 supervisor 失败处理。

## 服务管理

用户服务文件为 `~/.config/systemd/user/ccs-edge-dev.service`。部署脚本启用该服务；不需要整车重启。

```bash
systemctl --user daemon-reload
systemctl --user enable --now ccs-edge-dev.service
systemctl --user status ccs-edge-dev.service --no-pager
```

`KillMode=mixed` 允许主脚本按“stage manager/阶段进程 -> 静态 TF -> 其他服务”的顺序清理。统一服务重启会短暂中断 MAVROS、Livox、MQTT、UDP、map-stream 和视频链。

## 建图与重定位响应

指控开始建图时，map-stream 先验证 stage service、会话归属保护、外部 TF 模式和两个 TF 节点，再请求 `stage=1`。manager 通过 `manual_mapping_control.launch` 启动 FAST-LIO、滤地、动态栅格、地图记录器和建图态 `map -> odom`；重复开始保持幂等。该入口不引用旧 `mapping_system.launch`，不会重复启动静态 TF。

结束建图仍运行 `roslaunch car_bringup save_mapping.launch`。地图文件验证成功后请求 BASE 并停止建图专用进程；保存失败时保持建图运行。TF 故障不能阻止所属会话执行停止或取消。

实时预览使用端侧真实坐标转换：`/cloud_registered` 的 `camera_init` 点云通过常驻 `odom <- camera_init` TF 转换到 `odom`。端侧返回 `prepare_result.frame_id=odom`，预览分片返回 `frame_id=odom/source_frame_id=camera_init`，最终成果保持 `frame_id=map`。指控端只同步 `AGV_001` 的 `remote_mapping=odom`、`preview_source=camera_init`、`remote_artifact=map` 配置，不修改建图协调器代码。

重定位 stage 使用 `relocalization_control.launch` 启动 FAST-LIO 与定位层，同样复用两条常驻静态 TF。原始建图与重定位 launch 均保留为历史/整套回滚参考，但旧入口依赖已变化，当前架构不保证其可独立完成阶段启动；统一服务运行时不得人工执行会间接包含 `mapping_coordinate_transforms.launch` 的旧入口。

完整细节见 [GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md](GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md)。

## 构建与日志

```bash
cd /home/bitcq/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source /home/bitcq/catkin_ws/devel/setup.bash --extend
catkin_make --pkg epgeneral_map_stream -DCMAKE_BUILD_TYPE=Release -j1
```

增量部署脚本在检查 launch 和构建前严格按 ROS Noetic、车辆 `catkin_ws`、CCS `ccs_edge_ws` 的顺序加载 overlay，后两项使用 `--extend`。只加载 CCS overlay 会使 `fast_lio_open3d` 等车辆包不可见，必须在服务重启前中止部署并修正环境。

日志位于 `~/.ros/ccs_edge_dev_ground_air_agv/log/`；其中 `startup.log` 记录启动顺序，`stage_manager.log` 记录阶段切换，`mapping_tf.log` 记录静态 TF launch。PID 位于 `/home/bitcq/ccs_edge_ws/run/`。

Ground-Air `map_stream.yaml` 部署到 `/home/bitcq/ccs_edge_ws/config/ground_air_agv/map_stream.yaml`。变更实时预览帧时必须同时更新指控运行配置和发布默认镜像中的 `config/map_building.json`，然后分别重启 `ccs-edge-dev.service` 与 CCS；回滚也必须恢复同一批次的两端配置。

## 静态验收

```bash
systemctl --user is-enabled ccs-edge-dev.service
systemctl --user is-active ccs-edge-dev.service
rostopic echo -n 1 /ground_air/system/stage
rosnode list
rostopic echo -n 1 /livox/lidar
rostopic echo -n 1 /livox/imu
rosrun tf tf_echo odom camera_init
rosrun tf tf_echo body base_link
```

确认 TF 只有一套发布者，启动日志中 TF launch 最后出现，建图开始/重复开始/结束闭环后静态 TF PID 不变，FAST-LIO、world-TF owner 和建图节点退出而基础服务继续运行。端侧仅评价流程、接口和产物，不评价车辆移动后的建图精度。
