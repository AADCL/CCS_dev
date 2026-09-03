# Ground-Air AGV CCS Edge Deployment

This profile targets `AGV_001` at `192.168.50.130` on Ubuntu 20.04, ROS Noetic, and ARM64. The CCS overlay is `/home/bitcq/ccs_edge_ws`; vehicle packages remain in `/home/bitcq/catkin_ws`.

`start_ccs_edge_dev.sh` starts MAVROS, Livox MID-360, MQTT, UDP telemetry, map-stream, optional A8/SRT, and the Ground-Air stage manager. Its final startup item is exactly:

```bash
roslaunch car_bringup mapping_coordinate_transforms.launch
```

The transform launch owns only the resident `odom -> camera_init` and `body -> base_link` static publishers. FAST-LIO remains on demand. `manual_mapping_control.launch` directly starts the mapping stack and its mapping-mode `map -> odom` owner without including the legacy mapping or static-transform launch. `relocalization_control.launch` does the same for localization. The stage manager owns these on-demand process groups but never starts or stops the resident transform launch.

The user service uses `KillMode=mixed`, allowing the supervisor to stop the stage manager and any stage children before stopping the static transforms. Logs are under `~/.ros/ccs_edge_dev_ground_air_agv/log`; the transform log is `mapping_tf.log` and its PID file is `/home/bitcq/ccs_edge_ws/run/mapping_tf.pid`.

Deploy with the reviewed incremental bundle's `deploy_stage_manager_update.sh`. It verifies known target hashes, backs up every affected file and prior service state, runs targeted tests, incrementally builds `epgeneral_map_stream`, reloads the user unit, enables/restarts `ccs-edge-dev.service`, and waits up to 180 seconds for the required ROS nodes and capability parameters. It does not reboot the vehicle.

Acceptance is static only: verify the service and both resident transform edges, run one start/duplicate-start/save cycle, confirm resident transform PIDs remain stable, and confirm FAST-LIO/mapping processes exit while base services stay active. Never arm the FCU or publish movement, pose, mode, or waypoint commands during this check.

The original `manual_mapping.launch` remains byte-for-byte unchanged as a historical/full-rollback reference. Its legacy nested dependencies no longer form the supported standalone mapping path under this TF architecture. Use `manual_mapping_control.launch` for current diagnostics, and never run the legacy entry while `ccs-edge-dev.service` is active.

See `edge_side_pkg/documents/GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md` for command mapping, artifacts, rollback, and evidence requirements.
