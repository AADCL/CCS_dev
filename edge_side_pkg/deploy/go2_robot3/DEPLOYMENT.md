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
   timesyncd. Startup uses a read-only SNTP availability check on UDP 123;
   clock offset and server synchronization status do not gate startup.
   Do not step the clock during a task, add a clock setter or add CCS autostart.
6. Run root `--check`. Reset is deferred when on-demand navigation is stopped;
   run `ROS_MASTER_URI=http://127.0.0.1:11321 ./verify_ros_contract.py` after
   sourcing the CCS overlay to verify actual Trigger/SetBool handshakes and
   refused reset on an owned isolated master. Record its JSON output and
   confirm port 11321 closes afterward. The real enable type is checked as
   SetBool after normal bridge startup. No production control is invoked by
   the probe.
7. Normally stop only the identified old camera workflow, then start the root
   entrypoint. The D435i defaults to automatic single-device selection; set
   `CCS_D435_SERIAL` only when multiple RealSense devices are present and pass
   the serial without a leading underscore. Conflicting native/CCS nodes cause
   refusal. The root script requires the camera node and two fresh
   `/camera/color/image_raw` frames before reporting camera ready or starting
   SRT. Core sensor data and disabled DDS diagnostics are then required before
   task consumption. On camera timeout, inspect
   `/home/unitree/.ros/ccs_edge_ws/latest/camera.log`; the
   existing owned-process cleanup stops only this workflow.
8. Register `QRD_003 / 宇树Go2_3` with no initial map binding, preserve strict
   UDP contracts and apply its map-frame convention. Reuse the original
   platform working/data directories; restart normally only if needed.

## Acceptance Evidence

Record source revision, dirty-file and payload hashes, exact manifest and
backup path, build/restart counts, package paths, endpoint types, and preflight
output. Sample >=60 s using the real platform parser/store. Record UTC
boundaries, accepted UDP packets by level, heartbeats, MQTT, battery/IMU/Livox
freshness, decoded SRT frames/FPS and warnings.

For the 2026-09-10 camera/startup-script increment, back up and hash only the
root startup script, readiness helper, documentation and deployment manifest.
No catkin rebuild is required when those are the only changed runtime files.
Run the GO2_3 profile tests, the two affected documentation tests and
`bash -n`; do not substitute a full repository test run.

On the target, compare ROS nodes and managed PIDs before and after `--check` to
prove that it did not start or stop processes. Run the root entrypoint once
with the bridge disabled. Record that camera readiness follows two fresh RGB
frames, image size is `640x480`, a 30-frame rate window is `13-17 Hz`, and the
SRT node remains alive. End with Ctrl+C or TERM and record task-consumer-first
shutdown, successful disable confirmation and owned-process cleanup. This
increment does not repeat the 60-second UDP/MQTT sample, platform SRT decode,
mapping, relocalization, navigation or motion tests.

The D435i has an observed USB 2.1 connection and an existing `Right MIPI`
warning. Measure RGB/SRT stability at 15 FPS; do not claim the warning is fixed
without evidence. Report simulation separately from production observations.
Real enable, motion targets, complete mapping and complete relocalization are
outside the no-motion acceptance scope.

## Mapping-script line-ending increment

When map-stream shell files originate from a Windows checkout, validate every
`src/EPGeneral_map_stream/scripts/*.sh` file before installation: the CR byte
count must be zero, each file must pass `bash -n`, and each installed hash must
match staging. Keep the repository `.gitattributes` rule that forces these
scripts to LF.

The mapping negotiation preflight runs the FAST-LIO, accumulator-save and PGM
generator `--check` commands without starting mapping. PGM preflight verifies
the setup, ROS package and launch file, but must allow the future export PCD to
be absent before the first mapping session. Normal PGM generation requires the session PCD to be
non-empty, creates the
export directories when absent, and atomically publishes that PCD before
running the PGM generator. The prior export PCD is optional on the first map.
A script-only
repair does not require a catkin rebuild or CCS restart because map-stream
executes the source-package scripts by path for each request.

## Runtime-monitor increment

Back up the root startup script, deployment documents, manifest and original
managed/ROS logs before updating the watchdog. Run the focused GO2_3 watchdog
and profile tests plus `bash -n`; the shell tests use fake ROS queries and
verify transient recovery, persistent failure, partial output, missing nodes
and owned-process exits. On the target, use an owned isolated ROS master for
a bounded real-bridge check with control disabled throughout. Verify DDS
freshness, node responsiveness and cleanup, then run root `--check` and
confirm production nodes/PIDs are unchanged. No catkin rebuild is required.

## Rollback

Stop the new workflow normally by its recorded root PID or Ctrl+C; retain
shutdown diagnostics and logs. Restore the exact backed-up source, generated
message, configuration and startup set together and verify its hashes. On a
fresh installation, keep the failed CCS deployment stopped and retain its
payload/logs for diagnosis; there is no previous CCS runtime to restore.

Restore the previous timesyncd drop-in if one existed; otherwise remove only
the specific drop-in added here. Restart timesyncd while idle. Restart the
recorded previous camera command only after the new camera has stopped.
For a script-only incremental rollback, restore the matching startup script,
readiness helper, documentation and manifest backup as one set and verify
their hashes; no package rebuild or platform restart is required. Restore
changed platform configuration from its matching backup and normally
restart its original process if required. Preserve target maps, tasks and
emergency-stop state throughout rollback.

## Emergency-latch and per-start log increment

Back up the task-control coordinator, map-stream node/config/artifact helpers
and launch file, root startup script, GO2_3 documentation, deployment manifest,
current emergency marker and the ROS logs that explain its origin. Record
pre-deployment modes and SHA-256 values. No message definitions, C++ source or
catkin metadata change, so do not rebuild the workspace.

The coordinator checks `adapter.emergency_stop_state_file` during negotiation,
task prepare, commit and execute. A marker is fail-closed even when damaged.
It returns `EMERGENCY_STOP_LATCHED` before a task can be reported delivered and
does not retry that terminal preparation error. Other preparation failures,
including temporary localization loss, retain the five-second retry. After a
successful manual reset, the platform must deliver the task again with a new
request ID; the previous idempotency result remains cached by design.

Normal startup requires `setsid` and creates
`/home/unitree/.ros/ccs_edge_ws/<UTC-start-time.nanoseconds>_<pid>/`. It exports
that start's `ros/` as `ROS_LOG_DIR`, points MQTT, relocalization and map-stream
to start-local subdirectories and updates `latest`. Map-stream keeps PIDs and
artifacts in their existing session directory but writes FAST-LIO and PGM logs
to `mapping/sessions/<session_id>/`. `--check` must not create a start directory
or change `latest`.

Before switching, confirm there is no active execution, mapping or artifact
generation. Stop the old root workflow by its recorded PID and confirm the
bridge remains disabled. Install only the reviewed files with LF and original
permissions. Run task-control, control-safety, map config/artifact/node,
GO2_3 profile/watchdog, documentation and Bash syntax checks. The Linux
watchdog test must show that SIGINT/TERM reaches the supervisor while detached
children remain alive until ordered cleanup.

After the new workflow is ready, observe fresh disabled diagnostics, confirm
there is no execution or control transition, resolve the private Trigger
service and call `reset_emergency_stop` exactly once. Require a successful
response, absence of the marker and a still-disabled bridge. Do not delete the
marker directly. Observe at least three former retry periods, perform one
ordered TERM shutdown and restart, and verify the master exits last without
creating a new latch. Leave the restarted services running and wait for a new
task delivery; do not enable control or send a motion target.

If the first ordered-shutdown trial creates a marker whose reason is a SetBool
`returned no response` after the adapter has already entered `rospy` shutdown,
retain that marker and the complete start log as evidence. The shutdown-only
adapter path may reuse a fresh disabled diagnostic for the internal
`shutdown-unload`; it must still attempt SetBool and latch on stale or enabled
state. Back up the adapter and control-safety files before installing this
follow-up correction, rerun the focused safety tests, clear only the
validation-induced marker through the reset service under the same disabled
conditions, and repeat the ordered-shutdown trial.
