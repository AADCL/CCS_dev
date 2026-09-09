# QRD_003 Deployment Procedure

Target: `unitree@192.168.50.112`; platform: `192.168.50.101`.
CCS workspace: `/home/unitree/ccs_edge_ws`.
Native workspace: `/home/unitree/go2_nav_ws`.

This is the installation procedure. Record actual backup paths, payload
hashes, deployed revisions, build counts and runtime evidence after execution.
Do not reuse the earlier QRD_002 deployment's measurements.

## Installation

1. Record target identity, interfaces, disk, extrinsics, installed ROS packages,
   active tasks, processes, nodes and camera launch owner. Confirm idle state.
   Back up affected source, generated messages, configs, startup files and
   the existing timesyncd drop-in, with hashes and prior existence recorded.
   Preserve native maps and target emergency-stop state.
2. Install eight physical packages into `src`: `epgeneral_device_config`,
   `epgeneral_mqtav`, `epgeneral_udp_telemetry`, `epgeneral_video_srt`,
   `epgeneral_map_stream`, `epgeneral_relocalization`, `epgeneral_task_control`
   and `epgeneral_go2_integration`. Directory spelling may match the repository;
   preflight validates ROS names and physical locations. Do not transfer
   another device's runtime data or the whole platform as an edge dependency.
3. Install profile `config/*` to workspace `config/go2_robot3/`, `scripts/*.py`
   to workspace `scripts/`, and both `start_ccs_edge_dev.sh` and
   `verify_ros_contract.py` to the workspace root.
   Preserve LF and mark shell/Python entrypoints executable. Include the
   robot3 mapping-prerequisites launch in the map-stream package. Verify
   transferred file hashes before building.
4. Install missing dependencies only, including `python3-msgpack`,
   `python3-paho-mqtt` and GStreamer SRT/x264 when absent. Reuse native binaries:

```bash
source /opt/ros/noetic/setup.bash
source /home/unitree/go2_nav_ws/devel/setup.bash
cd /home/unitree/ccs_edge_ws
catkin_make -j2
```

5. While idle, apply the backed-up timesyncd drop-in and restart existing
   timesyncd. The read-only SNTP query must report absolute offset <=2 s.
   Do not step the clock during a task, add a clock setter or add CCS autostart.
6. Run root `--check`. Reset is deferred when on-demand navigation is stopped;
   run `ROS_MASTER_URI=http://127.0.0.1:11321 ./verify_ros_contract.py` after
   sourcing the CCS overlay to verify actual Trigger/SetBool handshakes and
   refused reset on an owned isolated master. Record its JSON output and
   confirm port 11321 closes afterward. The real enable type is checked as
   SetBool after normal bridge startup. No production control is invoked by
   the probe.
7. Normally stop only the identified old camera workflow, then start the root
   entrypoint. Conflicting native/CCS nodes cause refusal. New sensor data
   and disabled DDS diagnostics are required before task consumption.
8. Register `QRD_003 / 宇树Go2_3` with no initial map binding, preserve strict
   UDP contracts and apply its map-frame convention. Reuse the original
   platform working/data directories; restart normally only if needed.

## Acceptance Evidence

Record source revision, dirty-file and payload hashes, exact manifest and
backup path, build/restart counts, package paths, endpoint types, and preflight
output. Sample >=60 s using the real platform parser/store. Record UTC
boundaries, accepted UDP packets by level, heartbeats, MQTT, battery/IMU/Livox
freshness, decoded SRT frames/FPS and warnings.

The D435i has an observed USB 2.1 connection and an existing `Right MIPI`
warning. Measure RGB/SRT stability at 15 FPS; do not claim the warning is fixed
without evidence. Report simulation separately from production observations.
Real enable, motion targets, complete mapping and complete relocalization are
outside the no-motion acceptance scope.

## Rollback

Stop the new workflow normally by its recorded root PID or Ctrl+C; retain
shutdown diagnostics and logs. Restore the exact backed-up source, generated
message, configuration and startup set together and verify its hashes. On a
fresh installation, keep the failed CCS deployment stopped and retain its
payload/logs for diagnosis; there is no previous CCS runtime to restore.

Restore the previous timesyncd drop-in if one existed; otherwise remove only
the specific drop-in added here. Restart timesyncd while idle. Restart the
recorded previous camera command only after the new camera has stopped.
Restore changed platform configuration from its matching backup and normally
restart its original process if required. Preserve target maps, tasks and
emergency-stop state throughout rollback.
