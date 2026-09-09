# Go2 Robot 2 Deployment

This profile is exclusively for `QRD_002`, `unitree@192.168.50.111`. Keep
`go2_edu` and all other device profiles unchanged. Do not deploy the control
platform or a full CCS repository on the robot.

See [the deployment validation record](VALIDATION.md) for measured results,
backup checksums, known historical state and validation limits. The
[migration and rollback procedure](VALIDATION.md#迁移与回滚用法) gives the exact
one-time command workflow for [migrate_workspace.sh](migrate_workspace.sh).

## Source of Truth and Layout

The native underlay is `/home/unitree/go2_nav_ws`; the CCS overlay is
`/home/unitree/ccs_edge_ws`. The native `README.md`, `STARTUP_GUIDE.md`, and
the launches in `src/go2_bringup`, `go2_core`, `go2_control`,
`go2_mapping`, `go2_localization` and `go2_navigation` were inspected.
Some native prose still names the older nvidia/.100 installation. For this
device, the actual unitree paths and launch parameters take precedence.

The overlay contains eight physical source-package directories: device config,
GO2 integration, map stream, MQTAV, relocalization, task control, UDP telemetry
and video SRT. They reside directly under `src`, not behind package symlinks.
The native underlay remains separate; it is not copied into the overlay.

| Workspace location | Contents |
| --- | --- |
| `src/<package>` | Real CCS source files, generated ROS messages built together |
| `config/go2_robot2` | The YAML files from this deployment profile |
| `start_ccs_edge_dev.sh` | The script in this directory, installed executable |
| `scripts/ccs_sntp_sync.py` | SNTP helper, installed executable |
| `run/managed`, `logs/managed` | Owned process IDs and per-launch logs |
| `run/state/go2_task_safety.json` | Persistent task emergency-stop latch |
| `maps`, `mission`, `logs` | Existing device data; preserve during migration |

Neither `vendor` nor `bin` is part of the final runtime layout. Keep migration
backups outside those directories. A rollback must restore a matched set of
source, generated messages, profile and startup files.

## Startup and Native Parameters

Run from the robot:

```bash
cd /home/unitree/ccs_edge_ws
./start_ccs_edge_dev.sh --check
./start_ccs_edge_dev.sh
```

`--check` (alias `--preflight`) verifies configuration, dependencies, addresses,
clock offset and persistent launch parsing. It does not start/stop nodes or set
the clock. Normal startup is foreground and uses the same managed-process model
as Scout/Wheeltec, but refuses existing conflicting GO2/CCS nodes instead of
taking over an unknown workflow. An existing ROS master can be reused.

The script explicitly loads ROS Noetic, the native underlay and the CCS overlay
in that order. It calls the following functional launches directly:

| Lifecycle | Launch and key parameters |
| --- | --- |
| Persistent LiDAR | `livox_ros_driver2 msg_MID360.launch` |
| Persistent chassis | `go2_control control.launch use_real_sdk:=true network_interface:=go2dds`; bridge starts disabled |
| Persistent RGB | `realsense2_camera rs_camera.launch`; D435i, 640x480 at 30 FPS, depth/IR/IMU/TF disabled |
| Persistent CCS | MQTAV, UDP, video SRT, map stream, relocalization and navigation task launches, each with explicit device/profile YAML |
| Mapping FAST-LIO | Integration `mapping_fast_lio.launch`, shared exclusive lock |
| Mapping prerequisites | `mapping_prerequisites_go2_robot2.launch`: native core with explicit extrinsics, no costmap cloud, accumulator output path passed by map stream |
| Map export | `go2_mapping export_occupancy.launch`: explicit input PCD, output PGM and YAML paths |
| Relocalization | Exclusive guard, then integration `navigation.launch` with `map_name:={map_id}`, `map_root:={map_root}` and explicit native extrinsics |
| Task execution | Attach to the already-localized navigation stack; no duplicate native total launch |

Do not invoke native total `mapping.launch` or `navigation.launch` from the
root script: those include persistent drivers/control and would duplicate their
owners. FAST-LIO still consumes Livox IMU, not the chassis IMU.

Defaults are LAN `192.168.50.111/24`, DDS `go2dds=192.168.123.18/24`,
ground station `192.168.50.101`, and native extrinsics
`/home/unitree/go2_nav_ws/src/go2_core/config/extrinsics.yaml`.
Environment overrides include `CCS_EDGE_WORKSPACE`, `CCS_GO2_NAV_WORKSPACE`,
`CCS_EDGE_PROFILE_CONFIG_DIR`, `CCS_GROUND_STATION_IP`, `CCS_ROS_IP`,
`CCS_GO2_NETWORK_INTERFACE` and `CCS_GO2_USE_REAL_SDK`. Path/network overrides
require matching profile YAML; they do not silently rewrite profile storage.
`CCS_GO2_USE_REAL_SDK=false` selects the native mock bridge for isolated
validation, while sensor launches remain real.

On exit, task command consumption is stopped first, the owned real bridge is
disabled, and owned launches are stopped in reverse order. Only a ROS master
started by this script is stopped. Unknown node owners are never killed.

## Failure Analysis and Compatibility

UDP packets arrived at the platform, but the exact eight-entry GO2 descriptor
set was absent from its accepted contracts. Add that set to development and
release UDP configurations while retaining strict hash validation and all
existing descriptor sets. Heartbeats carry the same contract and were rejected
for the same reason.

The accepted eight-descriptor SHA-256 is
`832977e1229667bc8a49936e707473702756c8203aeb3e77fbfb727131590f02`.

This profile sends IMU telemetry from `/go2/imu`, whose quaternion is valid.
`/livox/imu` remains an algorithm input but does not provide valid orientation
for the platform IMU contract. The descriptor display name `MID360 IMU` is
retained intentionally as a wire-contract compatibility label; changing it
would change the descriptor hash. Other devices' sources and validators remain
unchanged.

The native reset service is `std_srvs/Trigger` (MD5
`937c9679a518e3a18d831e57125ea522`), not `std_srvs/Empty` (MD5
`d41d8cd98f00b204e9800998ecf8427e`). The adapter must check the Trigger success
response before enabling the SDK bridge through `std_srvs/SetBool`.

The mapping preview's `lio_odom` and accumulator artifact's `odom` are distinct
intentional frames. Keep `artifacts.frame: odom`, the native pose/cloud adapters,
and the control-side Go2 artifact-frame contract aligned. Do not globally rename
frames or fabricate a transform for other devices.

The task profile attaches to the selected map, gates on fresh localization,
arms at execution time, and confirms disable on terminal states. Emergency stop
uses the periodic `/go2/diagnostics` bridge `motion_enabled` state for freshness
alongside `/go2/control/enabled`, which is latched and updates only on changes.
The safety latch
persists in `run/state/go2_task_safety.json`; a new task or process restart must
not clear it. The device-only `std_srvs/Trigger` service
`/epgeneral_navigation_task_adapter/reset_emergency_stop` is the explicit
operator reset, allowed only without an active execution and after fresh
confirmation that the bridge is disabled. Reset never enables motion.

## Migration and Clock Service

Use the reviewed migration script for the verified .111 paths only. Inventory
each package link and resolved target before moving anything. Archive the old
source/config/startup/service state and build outputs, retain a checksum, move
the real package directories to `src`, overlay the reviewed CCS changes, then
rebuild the CCS workspace once against the unchanged native underlay. This
one-time path change invalidates old CMake/generated-message references.

Install this profile's `ccs-sntp-sync.service` under
`/etc/systemd/system` and reload systemd after the helper has been placed in
`scripts`. Its explicit `--set` operation belongs to the boot-time service,
not the application startup path. Do not step the system clock during a task.
The normal script only queries the ground-station SNTP server and requires
absolute offset no greater than five seconds.

Remove `vendor` and `bin` only after verifying all source contents, regenerated
package paths and live service/startup references. Preserve user maps, missions,
logs and native workspace. On failure, stop only this deployment, restore the
matched backup, reload the restored service definition and start the restored
entrypoint; do not partially mix old generated messages with new Python code.

## Incremental Acceptance

Run impacted static/profile/launch tests and adapter lifecycle mocks together
before the single CCS build. After migration, run one preflight and one idle
startup. Confirm physical package discovery, no active runtime references to
removed directories, matching ROS service/message MD5s, received UDP/heartbeat,
fresh chassis IMU, and a disabled bridge. Re-run only checks affected by a fix.

Hardware acceptance for this change is read-only: do not enable the bridge,
submit motion goals or repeat full mapping runs. Test reset rejection, stale
states, cancel/enable races, disable failure and emergency-stop acknowledgement
with mocks. Actual motion remains a separate operator-supervised acceptance.

The completed 2026-09-09 rollout is recorded in [VALIDATION.md](VALIDATION.md).
It distinguishes actual device results from mock coverage and explicitly leaves
real motion acceptance and boot-time SNTP service retesting outstanding.
