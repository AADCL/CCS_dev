# Ground-Air AGV 重定位部署说明

## 运行契约

`AGV_001` 使用 `ground_air_agv` profile。指控依次协商地图、下发地图、启动栈并提交 `map` 坐标系初始位姿。端侧协调器实际创建的顶层命令为：

```bash
roslaunch car_bringup relocalization_system.launch map_id:=<map_id>
```

该命令仅在子进程内将 `/home/bitcq/ccs_edge_ws/overrides` 前置到 `ROS_PACKAGE_PATH`，并从该子进程的包搜索路径与 `CMAKE_PREFIX_PATH` 排除车辆 underlay 根，避免 ROS1 因发现两个同名 launch 而拒绝启动。Python 与动态库路径保持不变；驻留 manager 仍使用完整 underlay，并从中只读加载 FAST-LIO、管线和定位依赖。覆盖 launch 请求 stage 2，驻留的 `epgeneral_ground_air_control` 管理器唯一启动 `relocalization_control.launch`。

精简栈只包含 FAST-LIO、既有里程计管线、Ground-Air localization 和初始位姿适配器。Livox、MAVROS、MQTT、相机、车辆基础节点及 `odom -> camera_init`、`body -> base_link` 静态 TF 均复用手动启动一键栈后的常驻实例。适配器调用 `/ground_air/load_map`，收到 `/initialpose` 后以 `use_initial_guess=true` 调用 `/ground_air/relocalize`；栈就绪检查使用 Ground-Air 实际发布的 `/map`，不使用其他设备 profile 的 `/map_2d`。

地图传输协议和 ZIP 清单继续使用 `public_map.pcd`。端侧完成大小、SHA-256 和 PCD 内容校验后，在原子安装到 `ccs_edge_ws/maps/download/<map_id>` 前将其改名为 `cloud_map.pcd`。这是 Ground-Air 定位器要求的处理后地图名，目录中仍只有一个 PCD；Scout/Wheeltec profile 不受影响。

## TF 上报

Ground-Air 不使用 Scout/Wheeltec 的稳定窗口。端侧每 1 秒查询最新动态 `map <- odom`：

- 首个有限、四元数有效、时间未过期的样本立即返回成功；仅首样本受 30 秒超时约束。
- 同一会话继续每秒发送 `relocalization_result(state=succeeded)`。
- 有新时间戳时更新校准值；设备静止、TF 时间戳未更新或单周期查询不到时，沿用并重发最后一个有效值，不把静止误判为失败。
- 首个结果立即持久化，后续每 30 秒及正常退出时刷新；平台地图显示始终使用最新内存样本。

协议继续使用 `ccs-relocalization-v1`、端侧 UDP 14565、平台 UDP 14566 和地图 HTTP 14601，不新增消息类型或字段。

## 部署边界

`AGV_001` 上电自启动已禁用，服务保持 `disabled`；需要运行时手动执行 `systemctl --user start ccs-edge-dev.service`。增量部署和回滚记录并保持 enabled/active 状态，不修改现有 unit、linger 或上电启动设置。

增量包必须解压到 `/home/bitcq/ccs_edge_ws/.deploy/<批次>`，并执行 `deploy_relocalization_update.sh`。脚本对源码根目录以及每个目标、备份、临时和验收目录执行 `realpath` 包含性校验，并将 `TMPDIR`、`ROS_HOME` 与 `ROS_LOG_DIR` 显式重定向到工作区内。允许只读 source `/opt/ros/noetic` 和 `/home/bitcq/catkin_ws`，允许重启既有 `ccs-edge-dev.service`；禁止在 `ccs_edge_ws` 之外新增、覆盖、备份或生成临时文件。

部署前必须无活动 FAST-LIO、建图或重定位阶段。脚本记录原始 `manual_mapping.launch`、`save_mapping.launch`、`relocalization_system.launch` 和 `mapping_coordinate_transforms.launch` 校验值，部署后复核一致；构建本次实际涉及的包。

manager 从 `/home/bitcq/ccs_edge_ws/src/EPGeneral_ground_air_control/scripts/ground_air_stage_manager_node.py` 运行并发布 guard `2`，仍接受原建图 `/ccs_mapping_stage_<session>` caller。增量包必须同时包含 v0.13.2 建图客户端、关联版本元数据和回归测试，并执行版本检查；不能只升级 manager。此前客户端严格要求 guard `1`，即使重定位验收通过，建图仍会在准备阶段失败。此次客户端明确支持整数 guard `1`、`2`，服务签名与 caller/map_id 归属规则不变。

## 增量验收

先运行 `rosrun epgeneral_map_stream ground_air_stage_client.py --check`，确认实际客户端接受当前 guard `2`、外部 TF 就绪、阶段仍为 BASE。仅检查节点列表或 manager 参数不足以确认建图兼容；部署前后 `is-enabled` 必须保持 `disabled`。

使用完整地图 `test60`，先确认基础节点、UDP 14565、阶段服务和两条常驻静态 TF 正常。协调器完成地图协商/下发后启动栈，提交 `(x=0, y=0, yaw=0)`。定位器接受初始位姿后至少观察 10 秒，要求：

- 只有一套 FAST-LIO、Ground-Air localization 和动态 `map -> odom` 发布者。
- 首次成功不等待稳定样本；若定位器持续发布，结果间隔约 1 秒且数值有限。设备静止而不更新 TF 时，允许跳过新数据检查，但平台应继续收到端侧按 1 Hz 重发的最后有效值。
- 平台状态保持成功，实时绑定跟随最新样本，不产生非法状态转换告警。
- 重复启动不增加进程；中止后阶段回到 BASE，常驻节点与静态 TF 继续运行。

若零位姿被 fitness/RMSE 质量门禁拒绝，不降低阈值；保存服务响应、点云、TF 和日志后，在地图上人工选点重试。验收禁止发送运动、航点、解锁或飞行模式指令。

## 回滚

备份位于 `/home/bitcq/ccs_edge_ws/.deployment_backups/<批次>`。按 `manifest.tsv` 处理本批次新增文件，并从 `files/home/bitcq/ccs_edge_ws/...` 恢复既有文件，包含同批次建图客户端和版本元数据；在工作区重新增量构建，并按部署前 active/inactive 状态恢复服务。上电自启动保持 `disabled`，不启用服务。回滚前后复核四个 underlay launch 的校验值和实际建图 `--check`；若恢复了仅接受 guard `1` 的旧客户端，其对 guard `2` 的预检失败属于已知回退，不能改参数掩盖。平台侧同时恢复该批次实际修改的 `AGV_001` profile 和 `config/relocalization.json`。
