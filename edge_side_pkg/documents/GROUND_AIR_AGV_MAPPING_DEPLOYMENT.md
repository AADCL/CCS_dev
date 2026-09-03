# Ground-Air AGV 建图部署说明

## 适用范围与指令映射

适用于 AGV_001（192.168.50.130），复用 Scout 的 epgeneral_map_stream 协议、会话状态、产物校验、下载和日志格式。原始 manual_mapping.launch 和 save_mapping.launch 保持只读，不修改、不覆盖。

当前启动顺序：ROS Master → MAVROS → Livox → MQTT → UDP 遥测 → 建图响应 → A8 Mini（可降级）→ SRT（可降级）→ stage manager。最后一项命令为：

```bash
rosrun car_bringup ground_air_stage_manager_node.py
```

manager 作为自启动最后一项启动后，立即独立持有 `mapping_coordinate_transforms.launch`，并在开放阶段服务前验证两个静态 TF 节点及变换均就绪。manager 开机阶段仍为 BASE，但 `odom → camera_init` 与 `body → base_link` 常驻；不要再由建图会话或其他自启动项重复启动该 launch。

- 指控开始：客户端调用 /ground_air/system/set_stage，stage=1、map_id=会话地图名。manager 只启动 manual_mapping_control.launch，等待 FAST-LIO/记录器/世界 TF 所有者，并基于已常驻的静态 TF 检查完整建图 TF 链。响应端再检查全部预期节点、输入和输出。
- 指控结束：仍先运行 roslaunch car_bringup save_mapping.launch；校验新鲜非空的 PCD/PGM/YAML/metadata 后调用同一服务 stage=0。manager 只优雅停止建图专用进程，常驻 TF 保持不变，确认退出后才完成产物响应。
- 保存失败：不调用 stage=0，恢复 MAPPING、保留 FAST-LIO 和 TF，允许再次结束。
- 取消/启动失败清理：使用同一会话身份和 map_id 请求 BASE，不按 ROS 节点名批量杀进程。

## 常驻与按需边界

常驻复用 MAVROS、Livox、MQTT、UDP 遥测、建图响应、stage manager、两个静态 TF，以及可降级的 A8/SRT。FAST-LIO、地图记录器、动态栅格和其他建图专用进程按需运行。

manual_mapping_control.launch 只引用现有 start_mapping.launch 与配置资源，start_mavros=false，启动 FAST-LIO、数据转换、地面滤除、动态栅格、地图记录器等。mapping_coordinate_transforms.launch 只包含四个 frame 参数和两个静态变换：odom → camera_init，body → base_link。两份新增 launch 位于 ~/catkin_ws/src/car_bringup/launch/，此次不修改它们。

完整建图 TF 链为 map → odom → camera_init → body → base_link。BASE 阶段只承诺两条常驻静态边 `odom → camera_init` 与 `body → base_link`；`map → odom` 和 `camera_init → body` 仍由建图/定位专用节点提供。

## 状态与会话归属

指控状态 IDLE / STARTING / MAPPING / SAVING 保持兼容。重复开始不重复启动；保存中重复结束返回当前状态，不产生第二个保存线程。ROS manager 的阶段码仍为 BASE=0、MAPPING=1、RELOCALIZATION=2。

短生命周期客户端使用 /ccs_mapping_stage_<32位会话ID> 作为 ROS caller ID。manager 在同一把阶段锁内验证此 caller ID 与 map_id：指控不能接管或终止人工/其他会话启动的阶段。人工调用原有服务的切换行为不变，人工切换到其他阶段会撤销原指控归属。caller ID 仅用于误操作防护，不是安全认证。

guard 能力参数为 /ground_air_stage_manager/ccs_session_guard_version=1；缺少此能力时指控准备失败，禁止无保护调用旧 manager。不修改 SetSystemStage.srv 或指控报文。

## 增量部署与回滚

部署包使用 deploy/ground_air_agv/car_bringup_scripts 保存目标设备 manager、runtime 与 stage core 的完整补丁版本；三者均先校验已知版本、备份后再覆盖。

1. 确认车辆静止且没有活动建图/重定位，记录 ROS 节点与原始 launch SHA-256。
2. 运行包内 deploy_stage_manager_update.sh：校验基线、备份受影响文件、只安装列出的响应脚本/manager/启动脚本/文档，运行语法与目标单测，只增量编译 epgeneral_map_stream。
3. 重启现有 systemd 用户服务 ccs-edge-dev.service。不重启整车，不更改 enabled/linger 或其他自启动配置。该统一服务重启会短暂中断它持有的遥测和传感器进程。
4. 核对最后启动的是 manager、set_stage 可用、BASE 无建图节点但两个静态 TF 已存在；执行静态开始—结束闭环并核对静态 TF 节点 PID 在建图前后不变。
5. 备份目录和 before/after.sha256 由部署脚本输出；原始 manual_mapping.launch 和 save_mapping.launch 的校验值必须前后一致。

回滚时先确认无活动会话，停止 ccs-edge-dev.service；按备份 manifest 恢复逐个受影响文件，并仅删除 manifest 明确标记为原先不存在的新增文件。重新增量编译 epgeneral_map_stream、删除 ROS 参数 /ground_air_stage_manager/ccs_session_guard_version（旧版本不支持），再启动该用户服务。原始 launch 不参与回滚覆盖。

## 产物、日志和静态验收

地图：/home/bitcq/catkin_ws/maps/<YYYYMMDD_HHMMSS>/，包含 cloud_map.pcd、map.pgm、map.yaml、metadata.json。map_id 与 manager 使用的默认地图根目录必须和 AGV profile 一致。配置中的控制 launch 参数用于准备检查，实际阶段命令由 system_stage_core.py 构造。

manager 日志：~/.ros/ccs_edge_dev_ground_air_agv/log/stage_manager.log；启动顺序：同目录 startup.log；指控日志：~/.ros/ccs_edge_dev/log/map_stream.log；会话调用日志：会话目录 ground_air_mapping.log。

仅静态验证准备、开始、重复开始、点云预览、TF 单一发布者、保存、产物哈希、BASE 复位和无残留进程。不发送运动、解锁、飞控模式或航点指令，不评价移动建图精度。保存失败保活和会话隔离在本地单测验证，不在端侧制造磁盘/权限故障。验收证据见部署日志及 artifacts/agv_incremental_test/。
