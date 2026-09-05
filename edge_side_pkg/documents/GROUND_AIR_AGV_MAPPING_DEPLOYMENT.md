# Ground-Air AGV 建图部署说明

## 适用范围

适用于 `AGV_001`（`192.168.50.130`），复用 Scout 的指令应答、会话状态、产物校验、下载和日志组织方式。原始 `manual_mapping.launch` 与 `save_mapping.launch` 保持只读，部署前后必须校验 SHA-256，不修改、不覆盖。

`epgeneral_map_stream` v0.13.2 修复 guard 版本兼容，指控协议、UDP/TCP 端口与现有帧契约不变。当前部署使用工作区内 guard `2` manager；建图客户端兼容整数 guard `1`、`2`，缺失、类型错误和未知版本继续拒绝并报告实际值与支持范围。此次兼容修复不需要修改指控业务代码或帧配置。

## 手动启动顺序与所有权

`AGV_001` 已取消上电自启动，`ccs-edge-dev.service` 保持 `disabled`。需要运行时执行 `systemctl --user start ccs-edge-dev.service`，也可在服务未运行时手动执行 `/home/bitcq/ccs_edge_ws/start_ccs_edge_dev.sh`。两种入口不能同时启动；禁用上电自启动不停止当前服务。

统一启动顺序为：ROS Master -> MAVROS -> Livox -> MQTT -> UDP 遥测 -> map-stream -> A8 Mini（可降级）-> SRT（可降级）-> stage manager -> 重定位协调器 -> 静态 TF launch。最后一项必须使用：

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

`start_ccs_edge_dev.sh` 直接持有该 roslaunch 进程组，并分别等待 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster`。启动时若任一同名 TF 节点已由外部进程占用，脚本拒绝重复接管；运行期间任一节点退出，supervisor 失败退出并由 systemd 重启整套端侧服务。

`ground_air_stage_manager_node.py` 以 `rosrun epgeneral_ground_air_control ground_air_stage_manager_node.py` 从 `ccs_edge_ws` 常驻，只提供 `/ground_air/system/set_stage`、建图/重定位互斥 owner 及所属进程组管理，不创建、复用或停止静态 TF。manager 发布 `ccs_session_guard_version=2` 与 `external_tf_required=1`，并清除旧的 `resident_tf_version` 标识。

`ccs-edge-dev.service` 使用 `KillMode=mixed`：停止服务时先通知主脚本，由脚本先停止 stage manager 及其 FAST-LIO/阶段子进程，再停止静态 TF，超时后才由 systemd 清理剩余进程。

## 实时预览坐标系契约

FAST-LIO 发布的 `/cloud_registered` 保持 `camera_init` 源坐标。Ground-Air profile 使用 `ros.frames.map=camera_init`、`ros.frames.preview=odom`；MapStream 以点云窗口时间戳查询 `odom <- camera_init`，并将点坐标实际变换到 `odom` 后再生成 PCD 分片，不能只修改 frame 标签。

端侧与指控端的契约必须同时满足：

| 阶段/产物 | 坐标系 |
| --- | --- |
| `prepare_result.frame_id` | `odom` |
| `cloud_fragment_ready.frame_id` | `odom` |
| `cloud_fragment_ready.source_frame_id` | `camera_init` |
| 成果 manifest `frame_id` | `map` |

指控端 `config/map_building.json` 及发布默认镜像中的 `device_frames.AGV_001` 对应配置为 `remote_mapping=odom`、`preview_source=camera_init`、`remote_artifact=map`。先前端侧准备响应返回 `camera_init`，而指控准备阶段要求全局 `odom`，因此协商通过后立即触发 `FRAME_MISMATCH` 并自动取消；只把端侧预览标签改为 `odom` 又会使首个分片与原 AGV profile 不一致。本次采用端侧真实转换与指控设备配置同步，保持准备和分片阶段语义一致。

## 建图指令流程

- 准备建图：短期客户端执行 `ground_air_stage_client.py --check`，验证服务、guard 和两条外部静态 TF，不调用阶段切换服务。guard `2` 与原建图 caller 协议兼容，不应将 manager 参数改回 `1`。
- 开始建图：响应端调用 `/ground_air/system/set_stage`，`stage=1`。manager 启动 `roslaunch car_bringup manual_mapping_control.launch`，等待 FAST-LIO、地图记录器、建图态 `map -> odom` 和完整坐标链就绪；两条静态 TF 直接复用一键栈常驻实例。
- 精简入口：`manual_mapping_control.launch` 直接组合 FAST-LIO、里程计适配、滤地、动态栅格、地图记录器、建图态 world-TF owner 和 `/ground_air/mapping/start` 调用，不再间接引用 `start_mapping.launch`、`mapping_system.launch` 或静态 TF launch。
- 重复开始：同一会话与 `map_id` 幂等返回，不创建第二套 FAST-LIO、记录器或 TF。
- 结束建图：先运行 `roslaunch car_bringup save_mapping.launch`，验证本次新生成且非空、配对的 PCD/PGM/YAML/metadata；成功后以同一会话请求 `stage=0`，由 manager 优雅停止本次建图进程组。
- 保存失败：返回失败并保持 MAPPING，FAST-LIO 与建图进程继续运行，允许再次下发结束指令。
- 取消或启动失败：以同一会话身份和 `map_id` 请求 BASE；即使外部 TF 已故障，也不得阻止所属会话停止建图。

完整建图 TF 链为 `map -> odom -> camera_init -> body -> base_link`。`map -> odom` 由建图进程组内的 `ground_air_world_tf_owner` 以 mapping 模式发布，`camera_init -> body` 由 FAST-LIO 发布，另两条静态边由一键脚本持有的 launch 唯一发布。BASE 阶段只承诺两条常驻静态边。

stage 2 由 `epgeneral_ground_air_control/relocalization_control.launch` 直接启动 FAST-LIO、定位层和初始位姿适配器并复用常驻静态 TF。顶层命令通过仅包含 `relocalization_system.launch` 的工作区覆盖保持 `roslaunch car_bringup relocalization_system.launch` 不变；underlay 原文件不修改。完整契约见 `GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md`。

原始 `roslaunch car_bringup manual_mapping.launch` 保持逐字节不变，作为历史和整套回滚参考；它依赖旧 `mapping_system.launch` 的启动关系，在当前“静态 TF 独立常驻”架构下不保证可独立完成建图，也不得在统一服务运行时执行。当前人工诊断应停止 `ccs-edge-dev.service` 后显式启动所需基础层与 `manual_mapping_control.launch`；只有完整回滚关联 launch 后，才按旧说明使用原命令。

## 增量部署

guard 兼容修复采用已审核文件清单，通过 SSH/SFTP 逐文件备份并原子替换客户端、版本元数据、相关测试和文档。所有新增、备份、临时与证据文件均位于 `/home/bitcq/ccs_edge_ws` 内，保留原有权限；不更新 manager、车辆 underlay、unit 或启动脚本。

1. 确认阶段为 BASE 且无活动建图/重定位会话，记录服务的 enabled/active 状态、主进程 PID、manager 与静态 TF PID，以及本次目标文件和原始 launch 的校验值。
2. 对照已审核差异准备文件，在工作区内建立时间戳备份和清单；先校验传输后的临时文件，再原子替换目标。发布元数据与 Python 包版本保持 v0.13.2 一致。
3. 按 ROS Noetic、车辆 `catkin_ws/devel/setup.bash --extend`、CCS `ccs_edge_ws/devel/setup.bash --extend` 的顺序加载环境，运行版本一致性、Python/Bash 检查和目标测试。
4. 执行实际 `ground_air_stage_client.py --check`，确认 guard `2` 被接受、两条外部 TF 就绪、阶段仍为 BASE，再进行静态建图闭环。
5. 复核服务仍为 `disabled`，原进程、静态 TF 与 underlay 校验值保持一致。短期客户端每次命令都会重新加载，因此本次修复无需重启服务；常驻 map-stream 进程已加载的版本/能力标记可能保留旧值，待下次手动重启后加载磁盘上的 v0.13.2。

后续重定位增量包使用 `deploy_relocalization_update.sh`，必须同步建图客户端及相应元数据/测试，并在重启后的就绪门禁中运行实际建图 `--check`。旧 `deploy_stage_manager_update.sh` 属于 guard `1`、underlay manager 部署链，不能用于当前 guard `2` 设备，也不能用于本次修复。

将真实备份路径、文件清单、部署前后校验、服务状态及测试结果写入部署日志。本次不改两端帧配置；以后若修改 Ground-Air `map_stream.yaml` 的帧契约，仍需与指控运行配置及发布默认镜像 `config/map_building.json` 成对更新或回滚。

## 静态验收

端侧只做无运动增量测试，不发送解锁、模式切换、速度、位置或航点指令。

```bash
systemctl --user is-enabled ccs-edge-dev.service
systemctl --user is-active ccs-edge-dev.service
systemctl --user show ccs-edge-dev.service -p MainPID -p NRestarts -p KillMode
rostopic echo -n 1 /ground_air/system/stage
rosparam get /ground_air_stage_manager/ccs_session_guard_version
rosparam get /ground_air_stage_manager/external_tf_required
rosrun epgeneral_map_stream ground_air_stage_client.py --check
rosnode list | grep -E 'ground_air_stage_manager|odom_camera_init|base_link_body|fast_lio'
rosrun tf tf_echo odom camera_init
rosrun tf tf_echo body base_link
```

`is-enabled` 预期输出 `disabled`，其非零返回值不代表服务故障。`--check` 应成功且不切换阶段。验收至少覆盖：BASE 无建图节点、两个静态 TF 唯一发布；`prepare_result.frame_id=odom`，首个分片为 `frame_id=odom/source_frame_id=camera_init` 且被指控接受；开始、重复开始、保存、BASE 复位；建图期间存在且仅存在一个建图态 world-TF owner；建图前后两个静态 TF PID 不变；结束后无 FAST-LIO、建图 roslaunch 或孤儿进程；成果 manifest 为 `map`；原始 launch 校验值不变。保存失败保活只用本地单测验证，不在端侧主动制造磁盘或权限故障。

## 产物与日志

- 地图：`/home/bitcq/catkin_ws/maps/<YYYYMMDD_HHMMSS>/`，包含 `cloud_map.pcd`、`map.pgm`、`map.yaml`、`metadata.json`。
- 启动顺序：`/home/bitcq/ccs_edge_ws/log/ground_air_agv/startup.log`。
- stage manager：同目录 `stage_manager.log`。
- 静态 TF roslaunch：同目录 `mapping_tf.log`，PID 为 `/home/bitcq/ccs_edge_ws/run/mapping_tf.pid`。
- 指控响应：`/home/bitcq/ccs_edge_ws/log/ground_air_agv/map_stream.log`；ROS 节点日志位于同目录 `ros/latest/epgeneral_map_stream-1.log`（编号以现场为准），会话日志位于会话目录 `ground_air_mapping.log`。
- ROS home：`/home/bitcq/ccs_edge_ws/run/ros_home`；部署备份与验收证据分别位于工作区内 `.deployment_backups/`、`artifacts/`。

## 回滚

确认没有活动建图/重定位后，按本次备份清单逐文件原子恢复客户端、元数据、测试和文档，并保留权限。此次短期客户端修复不需要重启、构建或修改 unit；服务保持部署前的 active/inactive 状态和已禁用的上电自启动状态。复核目标文件与原始 launch 的校验值，并运行实际预检。仅回滚旧客户端会重新出现 guard `2` 被拒绝的问题，应将其作为已知回退结果记录，不能通过改 manager 参数绕过。

若回滚的是未来包含常驻包或帧配置的其他批次，按该批次清单恢复、增量构建并按原 active/inactive 状态重载所需进程；仍保持上电自启动禁用。帧配置变更必须同时恢复端侧 YAML、指控运行 JSON 及发布默认镜像，随后确认准备和预览分片契约一致。
