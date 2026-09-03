# Ground-Air AGV 建图部署说明

## 适用范围

适用于 `AGV_001`（`192.168.50.130`），复用 Scout 的指令应答、会话状态、产物校验、下载和日志组织方式。原始 `manual_mapping.launch` 与 `save_mapping.launch` 保持只读，部署前后必须校验 SHA-256，不修改、不覆盖。

指控协议、UDP/TCP 端口和 `epgeneral_map_stream` 包版本不因本次 TF 启动调整而变化。

## 自启动顺序与所有权

统一启动顺序为：ROS Master -> MAVROS -> Livox -> MQTT -> UDP 遥测 -> map-stream -> A8 Mini（可降级）-> SRT（可降级）-> stage manager -> 静态 TF launch。最后一项必须使用：

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

`start_ccs_edge_dev.sh` 直接持有该 roslaunch 进程组，并分别等待 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster`。启动时若任一同名 TF 节点已由外部进程占用，脚本拒绝重复接管；运行期间任一节点退出，supervisor 失败退出并由 systemd 重启整套端侧服务。

`ground_air_stage_manager_node.py` 仍以 `rosrun car_bringup ground_air_stage_manager_node.py` 常驻，但只提供 `/ground_air/system/set_stage` 及建图/重定位进程组管理，不再创建、复用或停止静态 TF。manager 发布 `ccs_session_guard_version=1` 与 `external_tf_required=1`，并清除旧的 `resident_tf_version` 标识。

`ccs-edge-dev.service` 使用 `KillMode=mixed`：停止服务时先通知主脚本，由脚本先停止 stage manager 及其 FAST-LIO/阶段子进程，再停止静态 TF，超时后才由 systemd 清理剩余进程。

## 建图指令流程

- 开始建图：响应端调用 `/ground_air/system/set_stage`，`stage=1`。manager 启动 `roslaunch car_bringup manual_mapping_control.launch`，等待 FAST-LIO、地图记录器、建图态 `map -> odom` 和完整坐标链就绪；两条静态 TF 直接复用自启动实例。
- 精简入口：`manual_mapping_control.launch` 直接组合 FAST-LIO、里程计适配、滤地、动态栅格、地图记录器、建图态 world-TF owner 和 `/ground_air/mapping/start` 调用，不再间接引用 `start_mapping.launch`、`mapping_system.launch` 或静态 TF launch。
- 重复开始：同一会话与 `map_id` 幂等返回，不创建第二套 FAST-LIO、记录器或 TF。
- 结束建图：先运行 `roslaunch car_bringup save_mapping.launch`，验证本次新生成且非空、配对的 PCD/PGM/YAML/metadata；成功后以同一会话请求 `stage=0`，由 manager 优雅停止本次建图进程组。
- 保存失败：返回失败并保持 MAPPING，FAST-LIO 与建图进程继续运行，允许再次下发结束指令。
- 取消或启动失败：以同一会话身份和 `map_id` 请求 BASE；即使外部 TF 已故障，也不得阻止所属会话停止建图。

完整建图 TF 链为 `map -> odom -> camera_init -> body -> base_link`。`map -> odom` 由建图进程组内的 `ground_air_world_tf_owner` 以 mapping 模式发布，`camera_init -> body` 由 FAST-LIO 发布，另两条静态边由开机 launch 唯一发布。BASE 阶段只承诺两条常驻静态边。

为避免相同回归，stage 2 使用新增 `relocalization_control.launch`，直接启动 FAST-LIO 与定位层并复用常驻静态 TF；原始 `start_relocalization.launch` 与 `relocalization_system.launch` 不修改。

原始 `roslaunch car_bringup manual_mapping.launch` 保持逐字节不变，作为历史和整套回滚参考；它依赖旧 `mapping_system.launch` 的启动关系，在当前“静态 TF 独立常驻”架构下不保证可独立完成建图，也不得在统一服务运行时执行。当前人工诊断应停止 `ccs-edge-dev.service` 后显式启动所需基础层与 `manual_mapping_control.launch`；只有完整回滚关联 launch 后，才按旧说明使用原命令。

## 增量部署

使用 `deploy/ground_air_agv/deploy_stage_manager_update.sh`。脚本执行以下门禁：

1. 拒绝在 FAST-LIO、建图记录器或重定位节点活动时部署。
2. 校验原始建图/保存 launch 及全部已知目标版本。
3. 将启动脚本、systemd unit、三个控制 launch、manager/runtime/core、响应客户端、测试和说明文档备份到时间戳目录。
4. 只增量编译 `epgeneral_map_stream`，运行 Bash/Python/XML/systemd 检查和目标单测。
5. 执行 `systemctl --user daemon-reload`，启用并重启 `ccs-edge-dev.service`，最长等待 180 秒，直至基础必需节点、manager、两条静态 TF 和能力参数均就绪；不重启整车。

部署脚本输出的 `BACKUP=...` 必须写入部署日志。`before.sha256`、`after.sha256`、`manifest.tsv`、`service.enabled.before`、`service.active.before` 和 `original-launch.sha256` 用于审计及逐文件回滚。

## 静态验收

端侧只做无运动增量测试，不发送解锁、模式切换、速度、位置或航点指令。

```bash
systemctl --user is-enabled ccs-edge-dev.service
systemctl --user is-active ccs-edge-dev.service
systemctl --user show ccs-edge-dev.service -p MainPID -p NRestarts -p KillMode
rostopic echo -n 1 /ground_air/system/stage
rosparam get /ground_air_stage_manager/external_tf_required
rosnode list | grep -E 'ground_air_stage_manager|odom_camera_init|base_link_body|fast_lio'
rosrun tf tf_echo odom camera_init
rosrun tf tf_echo body base_link
```

验收至少覆盖：BASE 无建图节点、两个静态 TF 唯一发布；开始、重复开始、保存、BASE 复位；建图期间存在且仅存在一个建图态 world-TF owner；建图前后两个静态 TF PID 不变；结束后无 FAST-LIO、建图 roslaunch 或孤儿进程；原始 launch 校验值不变。保存失败保活只用本地单测验证，不在端侧主动制造磁盘或权限故障。

## 产物与日志

- 地图：`/home/bitcq/catkin_ws/maps/<YYYYMMDD_HHMMSS>/`，包含 `cloud_map.pcd`、`map.pgm`、`map.yaml`、`metadata.json`。
- 启动顺序：`~/.ros/ccs_edge_dev_ground_air_agv/log/startup.log`。
- stage manager：同目录 `stage_manager.log`。
- 静态 TF roslaunch：同目录 `mapping_tf.log`，PID 为 `/home/bitcq/ccs_edge_ws/run/mapping_tf.pid`。
- 指控响应：`~/.ros/ccs_edge_dev/log/map_stream.log`；会话日志位于会话目录 `ground_air_mapping.log`。

## 回滚

确认没有活动建图后停止用户服务，按备份 `manifest.tsv` 恢复所有 `existing` 文件，并只删除标记为 `new` 的文件；随后增量编译 `epgeneral_map_stream` 并执行 `systemctl --user daemon-reload`。根据 `service.enabled.before` 恢复 enable/disable 状态，根据 `service.active.before` 恢复 active/inactive 状态，不固定重新启动服务。回滚后再次校验原始 `manual_mapping.launch` 与 `save_mapping.launch`。
