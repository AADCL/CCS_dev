# Ground-Air AGV CCS Edge Deployment

首次从源码或端侧 ZIP 安装，请先完成[使用手册的 Ground-Air 首次安装补充](../../documents/USER_MANUAL.md#ground-air-首次安装补充)，包括 launch/override、源脚本执行权限、用户服务与日志目录；本页后续增量更新步骤以已有部署为前提。

CCS 0.23.1 当前入口：[使用手册](../../documents/USER_MANUAL.md) · [接口与配置](../../documents/INTERFACE_REFERENCE.md)。本页保留设备专项步骤；运行配置以脚本传入的工作空间 config/profile 为准，不能只修改包内默认 YAML。

This profile targets `AGV_001` at `192.168.50.130` on Ubuntu 20.04, ROS Noetic, and ARM64. The CCS overlay is `/home/bitcq/ccs_edge_ws`; vehicle packages remain in `/home/bitcq/catkin_ws`.

`start_ccs_edge_dev.sh` starts MAVROS, Livox MID-360, MQTT, UDP telemetry, map-stream, optional A8/SRT, the workspace-owned Ground-Air stage manager, and `epgeneral_relocalization`. Its final startup item is exactly:

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

The transform launch owns only the resident `odom -> camera_init` and `body -> base_link` static publishers. FAST-LIO remains on demand. `manual_mapping_control.launch` directly starts the mapping stack and its mapping-mode `map -> odom` owner without including the legacy mapping or static-transform launch. `relocalization_control.launch` does the same for localization. The stage manager owns these on-demand process groups but never starts or stops the resident transform launch.

The Ground-Air map-stream profile keeps `ros.frames.map=camera_init` and sets `ros.frames.preview=odom`. MapStream therefore looks up `odom <- camera_init` at the point-cloud timestamp and transforms the actual point coordinates before publishing a fragment; changing only the frame label is invalid. The wire contract is `prepare_result.frame_id=odom`, fragment `frame_id=odom`, fragment `source_frame_id=camera_init`, and artifact manifest `frame_id=map`.

Boot autostart is disabled for `AGV_001`; keep `ccs-edge-dev.service` in the `disabled` state. Start the existing service manually with `systemctl --user start ccs-edge-dev.service` when needed. Disabling autostart does not stop an already running stack, and neither deployment nor rollback may re-enable it.

The user service uses `KillMode=mixed`, allowing the supervisor to stop the coordinator and stage manager before stopping the static transforms. New application and ROS logs are under `/home/bitcq/ccs_edge_ws/log/ground_air_agv`, ROS home is `/home/bitcq/ccs_edge_ws/run/ros_home`, and the transform PID file is `/home/bitcq/ccs_edge_ws/run/mapping_tf.pid`.

The current stage manager runs from `/home/bitcq/ccs_edge_ws/src/EPGeneral_ground_air_control/scripts/ground_air_stage_manager_node.py` and advertises guard `2`. The v0.13.2 mapping client explicitly accepts integer guard versions `1` and `2`; missing, malformed or unknown versions remain errors with the actual and supported values. The original mapping caller identity and service fields remain unchanged.

For the client compatibility hotfix, use the reviewed file manifest to back up and atomically replace the short-lived client, version metadata, related tests and documentation through SSH/SFTP. Keep all writes, temporary files, backups and evidence below `/home/bitcq/ccs_edge_ws`, preserve permissions, verify hashes and record service state and PIDs before and after. The client reloads on each command, so this hotfix needs no service restart or unit change; the resident map-stream process may retain its previously loaded version marker until the next manual restart. Do not use the legacy `deploy_stage_manager_update.sh`: it targets the old guard `1` manager in the vehicle underlay.

Before ROS package discovery, the deployment script sources `/opt/ros/noetic/setup.bash`, then `/home/bitcq/catkin_ws/devel/setup.bash --extend`, then `/home/bitcq/ccs_edge_ws/devel/setup.bash --extend`. Keep this order: sourcing only the CCS overlay hides vehicle packages such as `fast_lio_open3d` from launch validation.

Update both the ground-station runtime `config/map_building.json` and its release-default mirror so `device_frames.AGV_001` uses `remote_mapping=odom`, `preview_source=camera_init`, and `remote_artifact=map`; restart CCS to reload that configuration. This is a configuration-only ground-station change. The endpoint YAML and ground-station JSON form one compatibility unit and must be deployed or rolled back together.

Acceptance is static only: verify autostart remains `disabled`, then run `rosrun epgeneral_map_stream ground_air_stage_client.py --check` against the actual guard `2` manager and confirm it leaves the stage at BASE; verify the service and both resident transform edges; confirm prepare reports `odom` and the first accepted fragment reports `odom` from `camera_init`; run one start/duplicate-start/save cycle; confirm the artifact reports `map`, resident transform PIDs remain stable, and FAST-LIO/mapping processes exit while base services stay active. Never arm the FCU or publish movement, pose, mode, or waypoint commands during this check.

The original `manual_mapping.launch` remains byte-for-byte unchanged as a historical/full-rollback reference. Its legacy nested dependencies no longer form the supported standalone mapping path under this TF architecture. Use `manual_mapping_control.launch` for current diagnostics, and never run the legacy entry while `ccs-edge-dev.service` is active.

See `edge_side_pkg/documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md` for command mapping, artifacts, rollback, and evidence requirements.

Relocalization updates use `deploy_relocalization_update.sh`. The bundle must also synchronize the compatible mapping client, associated version metadata and regression tests, and run the actual mapping `--check` after startup readiness. Checking only that the manager publishes guard `2` missed this regression. Every deployment, backup, temporary, configuration, package and evidence write is containment-checked below `/home/bitcq/ccs_edge_ws`; the vehicle underlay and existing user service definition are read-only. Preserve the pre-deployment service enablement state and require `disabled` for this device. See `edge_side_pkg/documents/GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md`.
