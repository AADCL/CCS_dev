# Ground-Air AGV 建图部署说明

## 适用范围

本说明适用于 `AGV_001` 的指控建图响应。部署复用 Scout 的 `epgeneral_map_stream` 协议、状态机、进程组管理、产物下载和日志格式，不修改既有指控协议。

人工诊断入口 `roslaunch car_bringup manual_mapping.launch` 和保存入口 `roslaunch car_bringup save_mapping.launch` 均保持原样。指控开始建图使用新增入口：

```bash
roslaunch car_bringup manual_mapping_control.launch map_id:=<YYYYMMDD_HHMMSS>
```

## 常驻与按需边界

常驻复用项为 MAVROS、Livox、MQTT、UDP 遥测、A8 Mini、SRT、`epgeneral_map_stream`，以及 `odom -> camera_init`、`body -> base_link` 两条静态 TF。统一启动脚本在 ROS Master 就绪后、其他业务节点启动前执行 `mapping_coordinate_transforms.launch`，并确认两个 TF 节点均已就绪。开始建图时 `manual_mapping_control.launch` 只启动 FAST-LIO、里程计转换、地面滤除、动态栅格、地图记录器、`map -> odom` 所有者和启动操作节点；建图会话不再启动或停止静态 TF。保存成功后只终止本次建图进程组，常驻 TF 保持运行。

新增 `manual_mapping_control.launch` 只引用已有 `start_mapping.launch` 和配置资源；`mapping_coordinate_transforms.launch` 只包含四个 frame 参数和两个 `tf2_ros/static_transform_publisher` 节点，由 `~/ccs_edge_ws/start_ccs_edge_dev.sh` 常驻持有。`ccs-edge-dev.service` 作为 systemd 用户服务调用该脚本，并通过用户 linger 保证无登录会话时也随系统启动。两个 launch 均部署到 `~/catkin_ws/src/car_bringup/launch/`。原始 `manual_mapping.launch` 不复制、不覆盖；部署前后必须核对其 SHA-256 一致。

## 状态与响应

- `IDLE`：无建图会话，允许准备和开始。
- `STARTING`：启动建图进程组并等待 FAST-LIO、输入和 TF；此时拒绝新的开始。
- `MAPPING`：持续发送心跳与点云预览；相同 request 重试返回缓存应答，不启动第二套节点。
- `SAVING`：执行 `save_mapping.launch`，校验新鲜且非空的 `cloud_map.pcd`、`map.pgm`、`map.yaml`、`metadata.json`。
- 保存成功：先验证产物，再优雅停止本会话进程组并返回下载地址和 SHA-256。
- 保存失败：返回失败并恢复 `MAPPING`，FAST-LIO 保持运行，常驻 TF 不受建图状态影响，允许再次结束；强制取消仅清理响应程序持有的建图进程组。

## 部署步骤

1. 备份 `~/ccs_edge_ws/src/EPGeneral_map_stream`、AGV profile、根启动脚本和受影响文档，记录校验值。
2. 部署 `EPGeneral_map_stream` v0.13.0、`config/ground_air_agv`、`start_ccs_edge_dev.sh`、`ccs-edge-dev.service`、`manual_mapping_control.launch` 和 `mapping_coordinate_transforms.launch`。
3. 恢复 Shell 脚本 0755 权限，运行版本一致性、Python 编译、Bash 语法和端侧增量单测。
4. 运行 `roslaunch --nodes/--files` 检查建图入口、坐标转换入口与保存入口；仅执行受影响 catkin 工作区的 Release 增量构建。
5. 执行 `systemctl --user enable --now ccs-edge-dev.service` 并启用用户 linger；先确认服务为 `enabled/active`、两个静态 TF 节点与 TF 链就绪，再确认其他常驻节点、UDP 14561 和 HTTP 14600 在线。端侧仅做增量服务重启，不重启整车。

地图保存在 `/home/bitcq/catkin_ws/maps/<YYYYMMDD_HHMMSS>/`，响应日志位于 `~/.ros/ccs_edge_dev/log/map_stream.log`，supervisor 日志位于 `~/.ros/ccs_edge_dev_ground_air_agv/log/`。文档和日志不得记录明文凭据。

## 增量验收

验收不发送运动、解锁、模式切换或航点指令，只检查静态流程：准备条件、一次启动、重复开始幂等、点云预览、结束保存、产物哈希、进程退出、常驻节点存活，以及第二次开始—结束复位。静止设备不评价地图精度。

若 `/map` 不发布，先检查 `map -> odom -> camera_init -> body -> base_link` TF 链和 `/withoutGround_world`；不要通过降低保存校验绕过 PGM/YAML 条件。

## 回滚

停止当前建图会话后，恢复备份的 `EPGeneral_map_stream`、AGV profile、`start_ccs_edge_dev.sh` 和 `manual_mapping_control.launch` 前版本；若回滚到不含常驻 TF 的版本，再删除 `mapping_coordinate_transforms.launch`。重新执行增量构建并启动统一入口，再次核对原始 `manual_mapping.launch` 与 `save_mapping.launch` 校验值，确认常驻节点恢复且无建图节点残留。
