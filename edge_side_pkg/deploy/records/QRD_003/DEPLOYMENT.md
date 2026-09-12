# QRD_003 部署与验收记录

合并日期：2026-09-11；操作基线：CCS 0.23.1。此文件是该设备唯一的部署记录入口，后续按日期追加。

## 当前入口

- 设备：QRD_003；profile：`go2_robot3`；端侧：`unitree@192.168.50.112`。
- [配置与脚本](../../go2_robot3/)保持原位置；[从零部署](../../../documents/DEPLOYMENT_GUIDE.md)、[接口填写](../../../documents/CONFIG_TOPIC_REFERENCE.md)、[使用手册](../../../documents/USER_MANUAL.md)、[设备索引](../../README.md)。
- 原生 Go2：相机自动选择/RGB freshness、SNTP 可用性检查、独立进程会话、有序停用、持久急停和按启动时间日志。2026-09-09 相机未连接/跳过记录保留，9 月 10 日有后续实测。2026-09-12 已部署适配器退出急停修复（含 shutdown_requested）、完成服务复位与重启，联合下发通过，最终 disabled、定位 standby；见[最新记录](#joint-shutdown-20260912)。其他存储/根脚本改动不能视为随本轮部署。

- 2026-09-12 后续：用户已确认本轮落地测试完成，见[现场确认](#field-confirmation-20260912)。此前 disabled/standby 是工具验收时快照，不作为当前实时状态；后续未部署修订的状态保持。

## 历史材料与来源校验

以下合并原文件，保留日期、版本、哈希、失败证据、跳过项和原操作示例。历史章节的“当前/最终运行”、旧日志、相机筛选、时差门控、旧服务/保存路径及退出方式仅适用于当时；新部署执行上方当前指南。未记载实测不能补写通过，旧请求不构成新任务指令。

| 原文件（相对 edge_side_pkg） | 原始字节 SHA-256 | 合并章节 |
| --- | --- | --- |
| `deploy/go2_robot3/README.md` | `a350bc1f8dee22a1eba176dc175c1188d39eb6c6614f57278d72913f36e68bdf` | [材料 1](#source-1) |
| `deploy/go2_robot3/DEPLOYMENT.md` | `a59611c15aba8b2852b4fdd70b68b075e3481344622f0bb08a191c935ec0202d` | [材料 2](#source-2) |
| `deploy/go2_robot3/VALIDATION.md` | `13c16f8fbbe94c821310443a5953aac9e46a0d6d0d008835549fb51db0a1019d` | [材料 3](#source-3) |

<a id="source-1"></a>

## 历史材料 1：deploy/go2_robot3/README.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s1-go2-robot-3-qrd_003"></a>

### GO2 Robot 3: QRD_003

This profile targets `unitree@192.168.50.112`, displayed by CCS as
`宇树Go2_3`, with ground station `192.168.50.101`. It uses ROS Noetic on
Ubuntu 20.04 ARM64, native underlay `/home/unitree/go2_nav_ws`, and CCS overlay
`/home/unitree/ccs_edge_ws`.

<a id="s1-daily-operation"></a>

#### Daily Operation

Run on the GO2 device:

```bash
cd /home/unitree/ccs_edge_ws
./start_ccs_edge_dev.sh --check
./start_ccs_edge_dev.sh
```

The check reads configuration, confirms physical ROS packages and generated
interfaces, expands persistent and on-demand launches, resolves executables,
checks network interfaces and MID360 reachability, and confirms that the
ground-station SNTP service (UDP 123) responds. Clock offset and the server's
synchronization status do not block startup. It does not launch or stop nodes
or adjust the clock; systemd-timesyncd continues to handle synchronization.
On success it prints one preflight summary. Normal startup prints one
`[OK] <name> is ready.` line per component and one final summary; helper
details remain hidden unless a check fails.

The D435i uses the RealSense driver's single-device automatic selection by
default. Set `CCS_D435_SERIAL` only when more than one RealSense is connected:

```bash
CCS_D435_SERIAL=339222070647 ./start_ccs_edge_dev.sh
```

The value is passed to `serial_no` exactly as provided. Do not add the legacy
leading underscore. Startup waits for two fresh, increasing
`/camera/color/image_raw` frames before reporting `camera is ready` or starting
SRT. A 30-second timeout stops this workflow and points to
`/home/unitree/.ros/ccs_edge_ws/latest/camera.log`.

Each normal start creates
`/home/unitree/.ros/ccs_edge_ws/<UTC-start-time.nanoseconds>_<pid>/` and updates
the `latest` symlink. Set `CCS_EDGE_LOG_ROOT` only when the complete runtime log
tree must use another location. Component output, ROS logs, MQTT,
relocalization and mapping-session logs stay below that one start directory.
`--check` does not create a directory or update `latest`.

Use Ctrl+C for foreground shutdown. For a managed background process:

```bash
kill -TERM "$(cat /home/unitree/ccs_edge_ws/run/managed/startup.pid)"
```

Shutdown stops task consumption, sends zero velocity, requests SetBool(false),
and requires new diagnostics confirming a disabled bridge and current DDS
state. Owned roscore and roslaunch processes run in separate sessions so a
terminal Ctrl+C reaches the supervisor first. It stops owned launch processes
in reverse order, stops an owned ROS master last, and retains a reused master.
During ROS teardown, an internal `shutdown-unload` may reuse an existing
disabled confirmation only when the diagnostic is still fresh. Ordinary task
stops and stale or enabled shutdown states continue to require a new SetBool
response and confirmation; failures remain persistently latched.
An unconfirmed disable produces an error that must be inspected before further
operation.

<a id="s1-runtime-monitoring"></a>

#### Runtime Monitoring

The real GO2 SDK bridge starts disabled on `go2dds`; launching it provides
DDS telemetry and the gated control service. The native launch disables
automatic motion-mode switching. Runtime supervision queries the ROS node
list once per cycle for all required nodes. A transient query failure or
missing node is retried up to three times, with a five-second query timeout
and a one-second retry interval. Diagnostics and recovery events go to
`/home/unitree/.ros/ccs_edge_ws/latest/runtime_monitor.log`. A confirmed owned-process exit stops the
workflow immediately; repeated ROS query failure or node absence still
uses the existing safe cleanup. Query failure is no longer reported as a
confirmed bridge process exit.

<a id="s1-runtime-layout"></a>

#### Runtime Layout

| Location under `/home/unitree/ccs_edge_ws` | Purpose |
| --- | --- |
| `src/<package>` | Eight physical CCS ROS package directories |
| `config/go2_robot3/` | Device-specific YAML and timesyncd example |
| `scripts/ccs_go2_preflight.py` | Read-only package, launch and profile checks |
| `scripts/ccs_ros_readiness.py` | Separate fresh core-sensor, camera and disabled-state observations |
| `scripts/ccs_sntp_sync.py` | Read-only SNTP availability check during startup, no clock-setting mode |
| `start_ccs_edge_dev.sh` | Manual startup and owned-process supervisor |
| `verify_ros_contract.py` | Isolated ROS service handshake and reset-rejection probe |
| `run/managed/` | Startup lock and owned process IDs |
| `run/go2_stack.lock` | Mapping/navigation exclusivity lock |
| `run/state/` | Relocalization and persistent emergency-stop state |
| `maps/download/` | Maps delivered through CCS |
| `maps/sessions/`, `maps/archive/` | Mapping sessions and completed artifacts |
| `mission/` | This device's task storage |

The log tree is outside the workspace at `/home/unitree/.ros/ccs_edge_ws/`.
Every start directory contains `startup.log`, per-launch output, `ros/`,
`mqtav/`, `relocalization/` and `mapping/`; mapping session logs are retained
under `mapping/sessions/<session_id>/` while map artifacts remain under
`maps/sessions/`.

Runtime does not depend on historical `vendor` or `bin` directories. Do not
copy another robot's task, map binding, PID or emergency-stop state. Retain
existing target state during subsequent updates.

<a id="s1-hardware-and-services"></a>

#### Hardware And Services

Nine persistent launches provide Livox, the real GO2 SDK bridge, D435i,
MQTT, UDP telemetry, SRT video, map control, relocalization control and task
control. The bridge constructor starts disabled. Startup confirms D435i RGB
before SRT, then confirms core inputs and current disabled diagnostics before
task consumption. Native total bringup is unused.

| Interface | Configuration |
| --- | --- |
| LAN | `eth0`, `192.168.50.112/24` |
| DDS | `go2dds`, `192.168.123.18/24` |
| MID360 | `eth1`, host `192.168.1.50/24`, sensor `192.168.1.119` |
| D435i | Automatic single-device selection; observed serial `339222070647`; optional `CCS_D435_SERIAL`; RGB `640x480`, `15 FPS` |
| Disabled camera streams | Depth, infrared, gyro, accel, camera TF |
| SRT | UDP `9000`, latency `120 ms`, H.264 `2500 kbps` |
| MQTT connection | Periodic `/go2/state/low_state`, timeout `3 s` |
| MQTT armed | Latched `/go2/control/enabled` |
| Telemetry | `/go2/imu`, `/odom_nav`, `/go2/battery_state` |
| Algorithm IMU | `/livox/imu` |
| Reset / enable | `/go2_navigation_supervisor/reset`: Trigger; `/go2_sdk_bridge_real/enable`: SetBool |

FAST-LIO, map accumulation/export, localization and navigation run on demand.
Relocalization owns navigation; task control attaches and gates execution on
fresh localization. Mapping uses native extrinsics, preview `lio_odom` and
artifact `odom`; mapping and navigation share one exclusivity lock.

Install `config/timesyncd-ccs.conf` as
`/etc/systemd/timesyncd.conf.d/ccs-ground-station.conf` after backing up the previous target
file. Existing timesyncd owns synchronization. No new clock-setting service
or CCS boot autostart is installed.

Emergency-stop recovery is explicit. After confirming no active task and a
fresh disabled bridge, an operator may call the task adapter's private
`reset_emergency_stop` Trigger service, resolving its name through
`rosservice list` first. Clearing does not enable motion. Restart and new
tasks never remove the persisted latch. While the marker exists, negotiation
reports `EMERGENCY_STOP_LATCHED`, task prepare/commit/execute are rejected
before delivery can be reported successful, and the coordinator does not
repeat the known terminal preparation failure every five seconds. A new task
delivery is required after manual reset.

After loading the same three ROS environments as startup, the deployment
operator can verify actual Trigger/SetBool wire handshakes on an isolated
master. Install `verify_ros_contract.py` at the workspace root, preserve LF
and mark it executable:

```bash
ROS_MASTER_URI=http://127.0.0.1:11321 ./verify_ros_contract.py
```

The probe refuses other masters and occupied port 11321, owns its test master,
and creates only `/ccs_probe` services/topics. It verifies mock arm/disarm and
reset refusal through the actual task safety class, records ROS type/MD5 and
call counts, and cleans its master. It never contacts production controls.

`DEPLOYMENT.md` describes installation and rollback. Measured evidence belongs
in `VALIDATION.md`; procedures alone do not prove runtime acceptance.

<a id="source-2"></a>

## 历史材料 2：deploy/go2_robot3/DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s2-qrd_003-deployment-procedure"></a>

### QRD_003 Deployment Procedure

Target: `unitree@192.168.50.112`; platform: `192.168.50.101`.
CCS workspace: `/home/unitree/ccs_edge_ws`.
Native workspace: `/home/unitree/go2_nav_ws`.

This is the installation procedure. Record actual backup paths, payload
hashes, deployed revisions, build counts and runtime evidence after execution.
Do not reuse the earlier QRD_002 deployment's measurements.

<a id="s2-installation"></a>

#### Installation

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

<a id="s2-acceptance-evidence"></a>

#### Acceptance Evidence

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

<a id="s2-mapping-script-line-ending-increment"></a>

#### Mapping-script line-ending increment

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

<a id="s2-runtime-monitor-increment"></a>

#### Runtime-monitor increment

Back up the root startup script, deployment documents, manifest and original
managed/ROS logs before updating the watchdog. Run the focused GO2_3 watchdog
and profile tests plus `bash -n`; the shell tests use fake ROS queries and
verify transient recovery, persistent failure, partial output, missing nodes
and owned-process exits. On the target, use an owned isolated ROS master for
a bounded real-bridge check with control disabled throughout. Verify DDS
freshness, node responsiveness and cleanup, then run root `--check` and
confirm production nodes/PIDs are unchanged. No catkin rebuild is required.

<a id="s2-rollback"></a>

#### Rollback

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

<a id="s2-emergency-latch-and-per-start-log-increment"></a>

#### Emergency-latch and per-start log increment

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

<a id="source-3"></a>

## 历史材料 3：deploy/go2_robot3/VALIDATION.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s3-qrd_003-validation-record"></a>

### QRD_003 Validation Record

Validation date: 2026-09-09. The target was `unitree@192.168.50.112`,
device `QRD_003` (宇树Go2_3), with the command platform at
`192.168.50.101`. This was a no-motion deployment: control remained disabled,
and no movement goal, complete mapping run, relocalization run, or navigation
task was executed.

<a id="s3-result-summary"></a>

#### Result summary

| Area | Result | Evidence |
| --- | --- | --- |
| Source deployment and build | PASS | Eight physical CCS packages installed under `/home/unitree/ccs_edge_ws/src`; one actual `catkin_make` invocation completed successfully |
| Root `--check` preflight | PASS | Package paths, service MD5s, persistent/on-demand launches and SNTP offset checked without starting nodes |
| Isolated ROS service contract | PASS | Trigger/SetBool types and MD5s, reset refusal, arm/disarm simulation and zero production calls verified on an owned ROS master |
| GO2 stack ownership guard | PASS | 5 of 5 target tests passed |
| Live sensors and disabled control | PASS | Livox lidar/IMU, GO2 IMU, low state and battery were fresh; diagnostics reported `telemetry OK, motion disabled` |
| 65 s UDP capture | PARTIAL | All four streams and the formal descriptor set were parsed, but six late frames produced production-store `out_of_order` warnings; strict no-warning acceptance did not pass |
| 65 s MQTT capture | PASS | Live presence, heartbeat and status were received with no parser warning, disconnect, stale message or sequence gap; every status reported `armed=false` |
| D435i RGB and SRT decode | SKIPPED | The user approved skipping this phase because the currently disconnected hardware link is temporary and the camera was previously confirmed usable |
| Real mapping/relocalization/navigation | NOT RUN | Outside the no-motion acceptance scope |

<a id="s3-startup-script-incremental-update-2026-09-10"></a>

#### Startup-script incremental update (2026-09-10)

This follow-up preserves the 2026-09-09 record above and validates only the
D435i and one-click startup changes. It did not repeat the full build,
UDP/MQTT capture, platform SRT decode, mapping, relocalization, navigation or
motion tests.

The current target log showed that `realsense2_camera 2.3.2` enumerated
`Intel RealSense D435I / 339222070647`, while the previous root script
requested literal serial `_339222070647` and lowercase device name
`d435i`. The updated script uses automatic single-device selection by
default, accepts an optional exact `CCS_D435_SERIAL`, and requires two fresh,
increasing RGB frames before reporting camera ready or starting SRT.

| Incremental check | Result | Measured evidence |
| --- | --- | --- |
| Local focused tests | PASS | GO2_3 profile `18/18`; affected documentation `2/2`; Git Bash syntax check passed |
| Target `--check` | PASS | Printed only the final GO2 preflight line; ROS processes, nodes and managed PID files were unchanged |
| D435i discovery/profile | PASS | Serial `339222070647`; USB `2.1`; RGB stream `640x480 RGB8 @ 15 FPS`; RealSense node reported up |
| Camera readiness ordering | PASS | `camera is ready` appeared only after fresh RGB; MQTT, UDP and SRT ready lines followed it |
| RGB rate | PASS | Repeated 30-frame windows measured `14.526-15.428 Hz`, within the `13-17 Hz` acceptance band |
| SRT process | PASS | `/epgeneral_video_srt` answered ROS XML-RPC ping in `1.98 ms`; platform decode was not rerun |
| Disabled control | PASS | `/go2/control/enabled` was `False`; no enable call or movement goal was issued |
| TERM shutdown | PASS | Task control and its adapter disappeared before the real bridge; the bridge logged disabled, then all owned launches and the owned master exited |
| Final idle state | PASS | No ROS master, roslaunch process, ROS node or managed PID remained; only the existing empty `startup.lock` remained |

The first bounded startup after introducing deferred camera readiness exposed
a shell control-flow defect: a false `[[ ... ]] && report` expression became
the launch function's return status under `set -e`. The root process exited
before the camera freshness check and its existing trap cleaned every owned
process. The condition was replaced with an explicit `if` plus `return 0`,
covered by the focused test, redeployed and then validated by the successful
run recorded above.

The incremental backup is
`/home/unitree/ccs_edge_ws/backups/go2-robot3-startup-20260910T035841Z`.
Its predeployment hash record is `predeploy.sha256`; the staging and evidence
directory is
`/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-startup-20260910T035841Z`.
Selected runtime hashes changed as follows:

| File | Before SHA-256 | After SHA-256 |
| --- | --- | --- |
| `start_ccs_edge_dev.sh` | `fde6a6a027d39e8b126ec7d77a4eff1af1099fe3f95912b04727453259122f43` | `d10732953e40bcf8328f619f340178ec58744ed05e76e9298448c06a377d0ab6` |
| `scripts/ccs_ros_readiness.py` | `7d68f2f8d1d1e786cb37e746ee739b3712f2a9b36a70f1971e3ee34efa016232` | `ef6338e34b45627b9cb7db5fff9cde01482965aa8a1fa7e6a425522969e3482b` |
| `docs/go2_robot3/README.md` | `4d339e1c7abd90a9e22769ba6d8b15df267add6f04cd449577df5e1594bb06db` | `5972be171881d8772753e2bbf5cb2bc30de3663669c1d57c071558f176d6152b` |
| `docs/go2_robot3/DEPLOYMENT.md` | `d5b3ec878003026bea43b70182be525db597064dd064c9586558975029b1c64c` | `f82e842c3fc0020254d096eefd52a4cfadeaaff67005c721a294329058be2c5a` |

The USB 2.1 limitation, transient control-transfer warnings, startup HWM
readiness warnings and previously recorded `Right MIPI` warning remain open
hardware/driver observations. This successful RGB sample does not mark them
fixed.

<a id="s3-mapping-negotiation-line-ending-repair-2026-09-10"></a>

#### Mapping negotiation line-ending repair (2026-09-10)

At `14:24:48.124`, QRD_003 rejected `prepare` with
`mapping command returned 127: /usr/bin/env: 'bash\r': No such file or
directory`. Target inspection confirmed that all nine
`EPGeneral_map_stream/scripts/*.sh` files contained CR bytes; the package
resolved to `/home/unitree/ccs_edge_ws/src/EPGeneral_map_stream`. No mapping
process was active during replacement.

The repair backup is
`/home/unitree/ccs_edge_ws/backups/go2-robot3-map-lf-20260910T071528Z`.
Staging and evidence are under
`/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-map-lf-20260910T071528Z`.
The source checkout now enforces LF through `.gitattributes`, and the package
test rejects CR bytes in any map-stream shell script.

| Script | CR bytes before | Before SHA-256 | After SHA-256 |
| --- | ---: | --- | --- |
| `abort_fast_lio.sh` | 50 | `c22c1063e10fee8012b6bdc4af3a823c5524d1eefd7b584f7a57607311505efd` | `5433cad4e247030ff720f5bb37442f26a68d47f91246007bc9c1a247433af4b0` |
| `generate_pgm.sh` | 105 | `e3dd1995cff6e14015406836e265f5654ce46e7c6e3eceb09c3ef6fec1531711` | `5bae01b1de8a0ac6beb77bf5c6366da5a1690076c9c65d1c13e9151b19eaefaa` |
| `ground_air_mapping_stack.sh` | 29 | `982978fe2bd37406f7fd40ff1b1f9d8f8c637b4c629978c982e6893955b591cb` | `fb1dca36aeabdfaff96467717bd64a6fbc69ca6226f8004569aed9ccac003eac` |
| `ground_air_save_mapping.sh` | 103 | `c4a5fbfbc17dd555250485030fdc88ca1e4d6114a12000ad57ac5b824de6d364` | `9f6c327f012bd4e112c62987b66a71341dfb2affbb735f4b1647a682a1c48eb4` |
| `save_map.sh` | 71 | `78d21825c74e87457810c509b1be3699c02d4c7712f3742417f13653692e3be2` | `f194d272a9611281027afd3173fdf5206b7ceae38a55a1d3929e94e76e222cc2` |
| `scout_finalize_map.sh` | 49 | `79689bed68a644f514e4a4e136d575f79599438dfea9a9959b70c2a529b8f462` | `dbdd0ac55f6c533caa9246271cc304337e841cca4456e05bb5d3b967414efce0` |
| `scout_mapping_stack.sh` | 195 | `072e06d7d64c1c5bfa14e499dd7c1df3d60aadefe4a3f9edb20daa5a40cf186a` | `c9ecbee35d2fcc81982a5f8a28abef0932fd11cf3f8aae68a5df5c782c07b556` |
| `start_fast_lio.sh` | 267 | `1721372a503a1e1025813520eb62d230c98dfc88e2f803ee89b49fca6fb2da34` | `6a923a220d52820060baf9e949bd486192d65c103062c54d64979af7cf3e4b73` |
| `stop_fast_lio.sh` | 58 | `de5a40906406872fcd7d2078edf182633138c33299799d0a50c0405f6ad2caf4` | `229f1440009d1eec2ac04d93002fc3bc217d8fff15c0acf5b92191d778fb8150` |

The first post-install check showed an independent first-map precondition bug:
`generate_pgm.sh --check` required
`run/map/export/public_map.pcd`, although that output is created only after
mapping stops. Its preflight now checks only the setup, ROS package and launch
file. The normal generation path still rejects a missing or empty session PCD; the
prior export PCD is optional and is replaced atomically from the session PCD.

All nine target scripts had `CR=0`, executable permissions and matching hashes,
and passed `bash -n`. The exact `check_fast_lio`, `check_save_map` and
`check_pgm` commands then passed without starting mapping. Two target regression
tests passed: LF enforcement and PGM preflight with a missing future map. No
catkin build, CCS restart, map creation, localization, navigation or motion
command was performed.

<a id="s3-runtime-first-map-export-repair-2026-09-10-1558"></a>

##### Runtime first-map export repair (2026-09-10 15:58)

The first completed mapping session then reached generation but returned
`generate_pgm: source PCD is missing or empty` for
`run/map/export/public_map.pcd`. Session
`1357dc694edcf0e32342dff16afee370` already contained a valid
`1,736,638` byte `map.pcd` with SHA-256
`5102459a0ce99d5ace521e8af23c9fa84f000f917ccd724ef552627183b6bd4b`.
The failure was caused by the runtime script checking the prior public export
before copying the current session PCD there.

The runtime check was removed while the non-empty session PCD check remains.
The script now creates all export parent directories before atomically
publishing the session PCD. An isolated first-export test passed with no export
directory present. The target's real `go2_mapping/export_occupancy.launch`
then converted a backed-up copy of the failed session PCD in staging and
produced a `41,055` byte PGM and `123` byte YAML.

After installation, three target regression tests passed and the failed session
was recovered without rerunning mapping. Complete artifacts now exist both in
`maps/sessions/1357dc694edcf0e32342dff16afee370` and
`run/map/export`; the exported PCD matches the original session PCD exactly.
No lidar, FAST-LIO, localization, navigation or motion process was started.

The matching backup is
`/home/unitree/ccs_edge_ws/backups/go2-robot3-pgm-first-map-20260910T080353Z`
and staging/evidence is
`/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-pgm-first-map-20260910T080353Z`.
`generate_pgm.sh` changed from
`5bae01b1de8a0ac6beb77bf5c6366da5a1690076c9c65d1c13e9151b19eaefaa`
to `3451cddceacfbddf049adfdff61b16075a034cffbe45eff92ced233b976350c2`.

<a id="s3-source-payload-and-backup"></a>

#### Source, payload and backup

- Repository HEAD during deployment: `ab4e3d66693bf0d09a855243d1db3850418d315d`.
  The latest fetched `origin/main` used for comparison was
  `d968c66cba49d11c90ced93b8bdd54d5263cba5a`; the GO2 fix reference was
  `59feb0d41c77dec151cf42ddfdd38c88fea0662d`. The worktree was intentionally
  dirty and no commit, push or PR was made.
- Transferred payload:
  `.diagnostics/go2_robot3/go2_robot3_payload.tar.gz`, SHA-256
  `1cb46254ce6475dadb623d6231b75a093a8c4366707039c0fd45aff40effa236`.
  The transferred manifest verified 173 entries and the archive contained 174
  files including the manifest itself. All eight package paths resolved to
  real directories under the target workspace rather than historical
  `vendor/bin` content.
- Target staging directory:
  `/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-20260909T095800Z`.
- Target backup directory:
  `/home/unitree/ccs_edge_ws/backups/go2-robot3-20260909T082747Z`.
  `native-config-before.tar.gz` has SHA-256
  `5b30f8db71b89d33e7cf60b52e485a821b6d4f218105feabda5c449b98438977`;
  `timesyncd-conf-d-before.tar.gz` has SHA-256
  `5ff756b23734d91513ec0b22fa2f95d456c207afbeb310baebdbd2bc9897aedb`.
  The native map at `/home/unitree/go2_nav_ws/maps/lab_202609081925` was
  retained.
- The post-deployment UDP ordering patch was backed up under
  `backups/go2-robot3-20260909T082747Z/postdeploy-patches`. The deployed node
  and regression test hashes are respectively
  `9170249917dec6dabd97f892a56c34e595bcfd4203a5e396c59b5e046bf0c43f` and
  `320fb23b3637ad169c2706664005198dc3d30fcfcb1de7e5f0db1e7764e4b1b8`.
  Their pre-patch copies have hashes `2e87d924dc726f9a4d0117a1048e694b7e08d541bc1abd5f5781f3c7ed197bc4`
  and `8e6f2ba99c8c88a84cd6463b8861de1e829a008396a9b7cce731a383c9718a49`.

<a id="s3-build-and-static-validation"></a>

#### Build and static validation

The first build wrapper used `set -u` before sourcing the Noetic environment.
`/opt/ros/noetic/etc/catkin/profile.d/1.ros_distro.sh` therefore stopped with
`ROS_DISTRO: unbound variable`. This happened before `catkin_make` was invoked
and is recorded as a pre-build shell failure, not as a failed catkin build.

After loading Noetic, `/home/unitree/go2_nav_ws/devel` and the CCS workspace in
the required order, the only actual build invocation was:

```bash
catkin_make -j2 -l2 -DCMAKE_BUILD_TYPE=Release
```

It succeeded and traversed these eight packages:

1. `epgeneral_device_config`
2. `epgeneral_go2_integration`
3. `epgeneral_mqtav`
4. `epgeneral_udp_telemetry`
5. `epgeneral_video_srt`
6. `epgeneral_map_stream`
7. `epgeneral_relocalization`
8. `epgeneral_task_control`

Local focused tests passed: robot3 profile 13, task control 68,
relocalization 30, map stream 85 (five Windows-native skips), MQTT 36,
platform/relocalization/device/version 45, and release/profile 25. These are
separate targeted runs and are not summed because their coverage overlaps.
After the UDP sequence allocation patch, all 17 UDP package tests also passed
on the target, including the deterministic concurrent-send regression.

<a id="s3-target-preflight-and-contracts"></a>

#### Target preflight and contracts

`./start_ccs_edge_dev.sh --check` passed without starting nodes. It resolved
all eight package directories, five on-demand launch entries, and all nine
persistent launch entries. The final measured SNTP offset to
`192.168.50.101` was `0.011 s`, within the configured `2 s` tolerance.

The service checks resolved:

- reset: `std_srvs/Trigger`, MD5
  `937c9679a518e3a18d831e57125ea522`
- enable/disable: `std_srvs/SetBool`, MD5
  `09fb03525b03e7ea1fd3992bafd87e16`

The isolated probe used `ROS_MASTER_URI=http://127.0.0.1:11321` and returned
`PASS`. It made two reset calls, one simulated enable call and one simulated
disable call, confirmed the refused-reset path and final disabled state, made
zero calls to production services, and cleaned its owned master. The target
GO2 ownership/conflict guard suite passed all five tests.

<a id="s3-live-sensor-sample"></a>

#### Live sensor sample

The diagnostic chain used the real GO2 SDK bridge but kept it disabled. A
representative sample reported battery voltage `29.4377 V`, current
`-1.861 A`, charge `55%`, and `data: False` on
`/go2/control/enabled`. Approximate sustained topic rates were:

| Topic | Measured rate |
| --- | ---: |
| `/livox/lidar` | `10.0 Hz` |
| `/livox/imu` | `200 Hz` |
| `/go2/imu` | `47 Hz` |
| `/go2/state/low_state` | `47-48 Hz` |
| `/go2/battery_state` | `4.73 Hz` |

The GO2 diagnostic message was `telemetry OK, motion disabled`. The real MQTT
status later reported the connection healthy and control still disabled.

<a id="s3-udp-capture"></a>

#### UDP capture

The final capture ran from `2026-09-09T10:48:49.068750Z` to
`2026-09-09T10:49:54.074507Z` for `65.0 s` and received 1753 datagrams. It
observed the formal descriptor hash
`832977e1229667bc8a49936e707473702756c8203aeb3e77fbfb727131590f02`.
That hash was present in the platform allowlist and was accepted by the
production store. The platform's primary/default descriptor hash remained
`bfd44cfe0797e6736af617ae90795d2c57f5ef9e8c75a9dca523a54f10d23fa1`;
the difference is an allowed descriptor-set selection, not a parser mismatch.

| Stream | Received | Production-store accepted | Accepted rate | Late frames |
| --- | ---: | ---: | ---: | ---: |
| Level 1 | 1298 | 1293 | `19.892 Hz` | 5 |
| Level 2 | 325 | 324 | `4.985 Hz` | 1 |
| Heartbeat | 65 | 65 | `1.000 Hz` | 0 |
| Level 3 | 65 | 65 | `1.000 Hz` | 0 |

The parser reported no warning and the store raised no exception. The
production store reported six `out_of_order` warnings, rejected those six late
frames, and protected the current device snapshot from sequence regression.
The gap detector initially observed seven Level 1 and one Level 2 gap
positions. Five Level 1 frames and the Level 2 frame arrived later; across each
stream's final sequence interval, only two Level 1 sequence values were never
observed. Consequently the capture's strict result is `passed: false` with
failure `Target UDP parser/store reported warnings or exceptions`; this record
does not claim a no-warning UDP pass.

The route to the platform used the host WLAN link measured at `144.4 Mbps`;
the host NIC counters reported no interface errors during the check. This does
not erase the six application-level late-frame observations above.

The final snapshot still contained fresh usable IMU and point-cloud data, an
available chassis and Livox driver, and an estimated point-cloud rate of about
`10.0 Hz`. FAST-LIO, mapping and relocalization statuses remained unknown while
their on-demand stacks were idle; PGM output was unavailable because no map
task was run.

<a id="s3-platform-reload-and-mqtt-capture"></a>

#### Platform reload and MQTT capture

An early capture artifact retained the stale QRD_003 platform profile
(`relocalization_profile=go2_edu` and no final status-card set). It was excluded
from final acceptance. The on-disk record was corrected to `go2_native` with
the intended status cards, and the running platform was reloaded rather than
assuming the device registry would update in place. The original platform
process (PID 14152) was closed normally and released MQTT/UDP listeners. An
intermediate start (PID 59092 at `2026-09-09T10:32:00.1795707Z`) exposed that
the stale in-memory registry had persisted the old profile during the earlier
shutdown; it was closed normally after the disk record was repaired. The
second and final start used the same repository and working directory at
`2026-09-09T10:52:28.7781207Z`; PID 60852 was responsive with window title
`多异构智能体指挥与控制系统 · v0.23.1`, TCP `1883`, and UDP
`14560/14562/14564/14566`. Final capture artifacts show QRD_003 using
`go2_native`, the intended status cards, and no map binding.

The MQTT capture ran from `2026-09-09T10:52:53.503853Z` to
`2026-09-09T10:53:58.598008Z` for `65.094 s` and returned `PASS`:

- one live, non-retained `online` presence message;
- 34 live heartbeat messages and 34 live status messages, about `0.522 Hz`
  each;
- one broker connection, zero disconnects, zero offline-presence events, zero
  stale messages and zero parser warnings;
- one session with 68 combined heartbeat/status sequence values, zero
  duplicates, zero out-of-order values and zero missing values;
- all 34 status samples reported `fcu_connected=true` and `armed=false`.
  The last sample reported approximately `29.066 V`, `-1.844 A` and `49%`.

This live sample verifies the optional `/go2/state/low_state` connection source
and the separate `/go2/control/enabled` armed source under normal traffic. The
three-second connection-timeout behavior is covered by the focused MQTT tests;
the live run did not intentionally interrupt chassis telemetry.

<a id="s3-camera-and-one-click-startup"></a>

#### Camera and one-click startup

The root entrypoint was exercised once in normal mode. It launched its owned
ROS master, Livox and disabled GO2 bridge, then stopped safely when
`/camera/color/image_raw` did not become fresh within 30 seconds. It confirmed
disable and removed its owned processes; it did not leave a partial persistent
stack running.

At that time the D435i was absent from `lsusb` and `/dev/video*`, and the kernel
had recorded USB descriptor error `-71` after a disconnect. The user confirmed
that this is a temporary hardware-link condition, that the D435i is otherwise
usable, and explicitly approved skipping RGB/SRT validation for this
deployment. Earlier inspection had confirmed serial `339222070647`, RGB
`640x480` at approximately `15 Hz`, and a USB 2.1 connection. No SRT decoder
count or sustained RGB/SRT stability result is claimed here. The existing
`Right MIPI` warning and USB 2.1 limitation remain recorded and are not marked
as fixed.

<a id="s3-safe-shutdown-and-remaining-acceptance"></a>

#### Safe shutdown and remaining acceptance

Before stopping the temporary telemetry diagnostic chain, the real
`std_srvs/SetBool` disable call returned `success: True` with `StopMove sent`.
Fresh `/go2/diagnostics` then confirmed disabled and
`/go2/control/enabled` remained `False`. Owned processes were stopped in reverse
order: UDP, MQTT, GO2 control, Livox and the owned ROS master. No product ROS
process or listener on ports `11311` or `9000` remained. Shutdown completed at
`2026-09-09T11:00:55Z`.

No control enable, movement command, full mapping, relocalization, map export,
map binding or navigation task was performed. Those operations, plus a future
RGB/SRT decode sample after the camera hardware link is restored, remain
separate real-task acceptance items; the user waived the camera item for this
deployment session.

<a id="s3-startup-time-service-availability-update-2026-09-10"></a>

#### Startup time-service availability update (2026-09-10)

At the user's request, GO2_3 startup now invokes the SNTP helper with
`--availability-only` instead of `--max-offset 2`. The check requires a
response from the configured station's UDP 123 service matching the current
request. It does not calculate or compare clock differences or reject the
server's unsynchronized status. A timeout, malformed reply or unrelated
response still fails the availability check. Existing systemd-timesyncd
continues clock synchronization.

Focused GO2_3 tests passed 20/20, including positive/negative one-day offsets,
an unsynchronized server, timeout and invalid responses. Two affected
documentation tests and Bash syntax validation passed. On the target, the
helper returned `server=192.168.50.101 available=true`; root `--check`
exited 0 with its existing single success line. ROS node and ROS process PID
snapshots before/after were identical. No rebuild or service restart was needed.

Backup:
`/home/unitree/ccs_edge_ws/backups/go2-robot3-time-availability-20260910T131156Z`.
Staging/evidence:
`/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-time-availability-20260910T131156Z`.
See `predeploy.sha256` in the backup and `postdeploy.sha256` in staging.
Startup SHA-256:
`c15ad4a0d6a0bcbd5b45fb043f89b50654e48f7c7b8379e49619f10ae5c7ddb6`.
SNTP helper SHA-256:
`e7bc6d3e1cf06a441e1963c74b63cb07807a92adfa4d4e1642fcb444b045df3a`.
Earlier time-offset measurements above remain historical deployment evidence.

<a id="s3-runtime-watchdog-correction-2026-09-10"></a>

#### Runtime watchdog correction (2026-09-10)

The reported `[ERROR] /go2_sdk_bridge_real exited.` was inconsistent with the
bridge process log in ROS run `b9d30ba8-ad19-11f1-8d7e-3c6d66616e6f`.
The native real bridge used `network_interface:=go2dds`, started disabled,
and had automatic motion-mode switching disabled. Its process remained under
roslaunch supervision until workflow cleanup sent SIGINT at 21:18:55; it
returned 0 and only then unregistered its topics/services. Task consumption
had already stopped. The inspected logs contained no bridge crash or
out-of-memory kill. The old watcher discarded query stderr and classified
every failed five-second `rosnode list` call as a node exit, so the exact
query failure cannot be reconstructed.

The root startup script now collects one complete node snapshot per cycle,
checking its exit status before matching names. Runtime query failures or
missing required nodes receive up to three attempts, separated by one second.
Each attempt rechecks the owned launch processes and owned master; confirmed
process loss still immediately invokes the existing safe shutdown. Diagnostic
and recovery events are retained in `logs/managed/runtime_monitor.log`.
This also removes the early-closing grep pipeline from startup node checks.

Local focused tests passed 29/29, including nine actual Bash watchdog cases.
Two affected documentation tests passed. The nine isolated watchdog cases
also passed on the target, covering transient recovery, persistent timeout,
failed partial output, genuine node absence, owned-launch/master exit and
large node lists.

An owned isolated master on port 11321 ran the native real bridge for
60.324 seconds. It received 2,854 low-state messages and answered all 60
node/PID probes. Control stayed disabled, motion commands stayed zero,
automatic mode switching stayed false and low/sport DDS ages remained fresh.
The test used only disable calls, then cleaned its owned launches/master.
No full mapping, localization, navigation or motion task was replayed.

Backup:
`/home/unitree/ccs_edge_ws/backups/go2-robot3-watchdog-20260910T132457Z`.
Evidence/staging:
`/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-watchdog-20260910T132457Z`.
The backup includes original managed/ROS logs and `predeploy.sha256`;
staging contains isolated test results and `postdeploy.sha256`.
Startup SHA-256 changed from
`c15ad4a0d6a0bcbd5b45fb043f89b50654e48f7c7b8379e49619f10ae5c7ddb6`
to `d56bcfcaddf4d030357c85d0558b5018833d61a881a3644657a1d8852c1239f2`.

<a id="s3-emergency-stop-recovery-and-per-start-logs-2026-09-10"></a>

#### Emergency-stop recovery and per-start logs (2026-09-10)

The persistent marker was created after the ROS master entered shutdown. Its
preserved reason was `navigation unloaded; stop: control service
/go2_sdk_bridge_real/enable failed: rospy shutdown`; the previously observed
16:00 marker likewise originated from a disable RPC timeout. The coordinator
then retried the terminal `EMERGENCY_STOP_LATCHED` preparation every five
seconds even though the task could not become ready.

The coordinator now checks the shared marker during negotiation, prepare,
commit and execute, fails closed on a damaged marker, removes an in-progress
transfer when latching appears, and marks this preparation result terminal.
Temporary localization failures remain retryable. Manual reset does not replay
the old task; a new delivery and request ID are required. The root supervisor
uses a separate session for every owned roscore and roslaunch process.

Every normal start now writes below
`/home/unitree/.ros/ccs_edge_ws/<UTC-time.nanoseconds>_<pid>/` and updates
`latest`. `ROS_LOG_DIR`, component output, MQTT, relocalization and map-stream
logs all resolved below the active start directory. Map artifacts and safety,
task and lock state remained in the workspace. The read-only `--check` emitted
its single documented success line; process snapshots and the log-root listing
were byte-identical before and after it.

Focused target tests passed as follows: task coordinator 17/17, control safety
41/41, map configuration 11/11, map artifacts 7 passed with the one unavailable
ground-package case skipped, map node 24/24, and Linux watchdog/process-group
tests 10/10. The latter sent SIGINT and TERM to an isolated supervisor and
confirmed that its detached child survived until ordered cleanup. Bash syntax,
Python compilation, LF checks, the affected local profile/document tests and
the complete 173-entry target manifest also passed. No catkin rebuild or full
test suite was run.

The first runtime log was
`/home/unitree/.ros/ccs_edge_ws/20260910T145717.709443495Z_52931`. With no task,
mapping or control transition active, diagnostics reported `telemetry OK,
motion disabled`, `motion_command_active=false`, and zero linear/angular
commands. The reset service returned `success: True` with `emergency stop
cleared; control remains disabled`; the marker disappeared and remained absent
across 16 seconds, more than three former retry periods.

That first TERM trial exposed a narrower ROS teardown race: the coordinator and
adapter received shutdown together, and the internal `shutdown-unload` issued a
second service RPC after the adapter's `rospy` client was already closing. The
resulting `returned no response` marker and logs were preserved. The follow-up
fix permits only shutdown paths with an existing fresh disabled diagnostic to
skip that duplicate RPC. Ordinary stops and stale or enabled shutdown states
still require a new service response and retain fail-closed behavior. Four new
unit tests cover both the allowed and rejected cases.

The repeat trial used
`/home/unitree/.ros/ccs_edge_ws/20260910T151926.175848951Z_76571`. The
validation-induced marker was cleared through the service after the same
disabled checks. TERM then stopped task control first, Livox later and the
owned master last. All owned processes exited, no shutdown RPC error was logged
and no emergency marker was created.

The final running instance has root PID `85485`; `latest` resolves to
`/home/unitree/.ros/ccs_edge_ws/20260910T152259.085802727Z_85485`. All nine
startup components reached ready and 13 ROS nodes were present. Final checks
showed `/go2/control/enabled=False`, fresh `motion_enabled=false`, no active
motion command, zero velocity commands, task state `failed` awaiting a new
delivery, and no emergency marker.

Backup:
`/home/unitree/ccs_edge_ws/backups/go2-robot3-emergency-log-20260910T143736Z`.
Staging and evidence:
`/home/unitree/ccs_edge_ws/run/deploy-go2-robot3-emergency-log-20260910T143736Z`.
The later shutdown-race originals are under `late-race-fix/`; the first failed
trial marker is `evidence/validation-induced-marker.json`. Pre-deployment and
post-deployment SHA-256 sets are `predeploy.sha256` and `postdeploy.sha256`.
Key deployed hashes are `38a2dcec59ac110ab47e2ccfb309789e2265d3bf4a1a3f5b5915957c7b59ee03`
for the startup script,
`12770eb26c844a7efd726e30f0aced5e63a3bf8264796807fc1bb000682c46cc`
for the coordinator, `4f4b3147291de764d418abff4102b0a8b1daefa21e781446b5f1969372de29f4`
for control safety, and
`798e1167e17773c374d8ee14b1be9ef735f00cecffdd786efe46bc906638f6df`
for the adapter.

No control enable, motion goal, mapping, artifact generation, relocalization or
navigation execution was performed during this increment.

<a id="s3-local-pre-pr-review-follow-up-2026-09-11"></a>

#### Local pre-PR review follow-up (2026-09-11)

The shutdown adapter now also recognizes `rospy.core.is_shutdown_requested()`.
ROS client shutdown callbacks run before `rospy.is_shutdown()` becomes true, so
fresh disabled confirmation must be accepted during that earlier callback phase
as well. Ordinary close calls still require a disable service confirmation.

Map-stream readiness now returns `ARTIFACT_STORAGE_UNAVAILABLE` and releases the
session when creating an artifact or session-log directory raises `OSError`.
A regression test occupies the log root with a regular file, confirms failure
and session release, then removes the obstruction and prepares successfully.

The startup supervisor now acquires its exclusive lock before creating the
per-start log directory and updating `latest`. A rejected duplicate start can
no longer redirect the active instance's documented log paths.

These review follow-ups were checked locally and were not deployed to a robot
during PR preparation. The target paths, process IDs and deployed hashes above
record the earlier September 10 validation, not this subsequent review revision.

## 后续记录填写格式

追加日期/范围、源码版本与差异、文件清单及前后哈希、备份/证据路径、命令与实测、告警/未测项、启停/回滚。回滚不覆盖更新的急停状态，文档整理不表示重新部署。


<a id="joint-shutdown-20260912"></a>
## 2026-09-12 联合下发拒绝与退出急停修复（已部署）

### 根因与变更边界

部署前仍使用旧适配器，缺少 is_shutdown_requested() 处理。2026-09-12 10:04:01.809526 的 SIGINT 退出重新锁存 navigation unloaded; stop: control service /go2_sdk_bridge_real/enable failed: service returned no response。 10:03 的联合 task_prepare 只读取并拒绝已有急停标记，没有调用底盘控制服务，因此不能归因于同时下发造成停用超时。另经模拟复现：协调器的内部 shutdown-unload 可先于适配器关闭信号到达，造成重复停用；旧 disabled 快捷确认未检查尚未完成的 RPC。

本轮用户已明确授权受控重启、每台一次服务复位和仅下发验收。安装前确认任务 failed、无建图/地图生成、底盘 disabled 且运动指令为零，正常停止已识别 CCS 根入口。两台安装同版 control_safety.py 和 scout_adapter.py：内部 UNLOAD 与 close 共用幂等关闭，先阻止新准备/调度/复位并停止监控，再串行取消目标、零速、停用和清理自有导航；重复入口保留首次结果，失败不能被覆盖。关闭期间拒绝 PREPARE/SCHEDULE/复位；快捷 disabled 同时排除控制过渡与 RPC，真实停用失败仍锁存，普通 STOP/UNLOAD 仍严格确认。

未改变 ROS 契约、YAML、任务协议、运动策略、相机、授时或日志策略。Go2_3 本次同步了适配器关闭修复，不代表其他尚未部署的根脚本/存储改动已发布。未提交、推送、创建 PR 或重建 catkin。

### 备份与源码身份

- 源码基线 HEAD：`c0243faeb08e8e98fe765a9d65166915bda282d2` 加本轮未提交工作树修改；以以下目标字节哈希为准。
- 部署前代码、标记原文、配置、任务、日志、进程证据及 before.json：`/home/unitree/ccs_edge_ws/backups/joint-task-shutdown-20260912T022612Z`。
- 本轮阶段及验收证据：`/home/unitree/ccs_edge_ws/run/joint-task-shutdown-20260912T022612Z`；其中 safety-before-install.json 保留旧入口退出后的标记。部署前标记 SHA-256：`aadaa086d0af37a803784119ad224c2296ba8d1682dff9e13272a812235b0c5f`。备份中的安全文件仅作证据，不用于回滚。
- 目标实际导入路径：`/home/unitree/ccs_edge_ws/src/EPGeneral_task_control/src/epgeneral_task_control/`，实体源码目录。运行模块 LF、0644，根脚本权限保留。

| 文件 | 部署前 SHA-256 | 部署后 SHA-256（两台一致） |
| --- | --- | --- |
| control_safety.py | `4f4b3147291de764d418abff4102b0a8b1daefa21e781446b5f1969372de29f4` | `37a099a8662e001d7a0b360c7661697b8412336739ae0ab327fd939a88a692bc` |
| scout_adapter.py | `798e1167e17773c374d8ee14b1be9ef735f00cecffdd786efe46bc906638f6df` | `fb10efa86ae745f33c06bbb78e2fa05488f4ff1eded1e628e93b301d75393b51` |

### 增量验收实测

- 本地任务安全 51、适配器 13、接收端 17 项通过；两个 Go2 profile 共 33 项通过。覆盖 pending RPC、晚到 enable 补偿、过期状态、拒绝/超时锁存、内部卸载先到、ROS 关闭先到、重复/并发 close、首次失败保留、关闭期间 STOP/watchdog/旧回调与新工作门控。既有锁存拒绝 prepare/commit、传输中锁存、损坏标记、人工复位约束、复位后下发及定位失败可重试继续通过。
- 文档/发行内容10项通过；两台 profile 的 bash -n、Python语法、LF/无 BOM、运行模块 SHA-256 与 git diff --check 通过。仅运行受影响检查。
- 每台目标 ARM Linux：81 项任务测试、10 项真实进程 watchdog/信号测试通过；独立 master 127.0.0.1:11331 运行真实任务双节点 launch，覆盖内部 unload 后 SIGINT、无 unload 的 SIGTERM 两例，均无新标记，关闭停用 RPC=0、生产服务调用=0，测试进程和 master 全部清理。初次测试包缺少 fixture/目录布局，补齐后重新执行通过；不属于生产运行失败。
- 安装后 --check 返回0；运行中再次只读检查，前后 13 个生产节点及 PID 完全相同，latest 不变。原精简提示保持。正常入口均到达最终 services are running；相机新鲜帧及底盘 disabled 检查通过。
- 每台仅调用一次 `/epgeneral_navigation_task_adapter/reset_emergency_stop`（std_srvs/Trigger），返回 `success: True`、`emergency stop cleared; control remains disabled`；未直接删除文件。随后18条连续诊断覆盖约17秒（至少三个5秒周期），新鲜 disabled、运动指令为零，标记解除。
- 本设备根入口以 SIGTERM 正常退出，26 个记录的自有进程全部退出，自有 master 退出，未生成新锁存；重新启动后仍无锁存。终止任务优先、底盘停用、逆序清理、master 最后退出的脚本/进程回归和实机检查通过。
- 联合验收使用原 task `6b482cb3119944be9a166e661052b473`，本设备子任务 `2caa9832fe9049df92fc44461fb4e4e1`，revision=5，4个原航点，CRC32=3976024108；未修改地图或路径。两台在约0.6秒内完成全部 prepare/chunk/commit 发送，端侧 prepare/commit 均 accepted=True，并记录 `trajectory XML committed revision=5 waypoints=4`。请求 ID：prepare `joint-final-e3a249e4a18a42f0b82efbf960d4bb84`，commit `joint-final-414c17b0536246059218747ca537e0fe`。
- 首轮人为分步读日志超过10秒传输期限，仅证实幂等 commit 接受；未将其当作完整传输通过。随后以上新请求完成真实 XML 落盘，最终证据为 joint-delivery-complete-result.json。再次18条诊断覆盖约17秒，仍 disabled、零指令、无急停标记。
- 最终导航准备反馈是 `LOCALIZATION_UNAVAILABLE` / `Scout is not localized on a usable map`，定位文件为 standby，任务状态 failed。此为重启后的定位门控，保留可恢复错误的5秒准备重试；不等于仍被急停拒绝。下发成功不代表 navigation ready。本轮没有 execute、SCHEDULE、运动目标、整轮定位/建图或联合运动验收，也未做全量测试。

### 最终运行、日志与回滚

- 最终保留 CCS 根入口 PID `77500` 运行（仅记录当时身份，后续操作必须重新核对 PID/命令行/归属），底盘 disabled。正式执行前须先完成定位，再按正常操作重新下发/执行；不得为消除 failed 绕过定位或安全门控。
- 当前组件日志：`/home/unitree/.ros/ccs_edge_ws/20260912T023641.077962837Z_77500/`；本轮启动输出：`/home/unitree/ccs_edge_ws/run/joint-task-shutdown-20260912T022612Z/start-final.log`，前次退出日志归档 first-start-logs。两台原日志布局差异保留。
- 启动：`cd /home/unitree/ccs_edge_ws && ./start_ccs_edge_dev.sh`；只读检查加 `--check`。停止：前台 Ctrl+C，或核对 `run/managed/startup.pid` 对应根脚本后 `kill -TERM <核实的PID>`；不要直接杀适配器/bridge/master。
- 端侧本轮文档/清单：`docs/go2_robot3/INCREMENT_20260912_JOINT_SHUTDOWN.md`、同名 `.json`；历史记录保留。文件清单记录文档与运行代码哈希，deployment_source.json 追加本轮条目。
- 回滚：确认空闲后正常停止该 CCS 根入口，先核对当前两模块仍匹配本表部署后哈希，再仅恢复备份中对应两模块及匹配的文档，校验部署前哈希和 LF/权限，--check 后启动。保留最新任务、定位、安全状态和所有诊断；不恢复旧急停文件、不清锁、不整目录覆盖工作空间。旧代码含本次退出竞态，回滚后的急停必须重新诊断，不能自动复位。


<a id="field-confirmation-20260912"></a>
## 2026-09-12 后续现场确认与经验归档

用户在本次会话明确反馈：“落地测试已完成”，并要求归档本轮修改与经验。记录为两台退出急停与联合下发修复的后续现场确认；未另提供测试次数、运动范围、轨迹、速度或逐项原始日志，因此不补写这些指标，也不将其标成工具重新执行的验收。

此前“联合传输已落盘、工具验收未发送 execute/SCHEDULE、disabled/standby”的结果与时间边界原样保留，不再当成现场后续测试后的实时状态。历史 INCREMENT_20260912_JOINT_SHUTDOWN.json 及其 SHA-256 不改写；后续卸载后恢复准备的源码修订仍只有本地测试记录，不能由此次总体反馈推断已部署。

复用规则已归档到 [GO2 部署经验](../../../documents/GO2_DEPLOYMENT_LESSONS.md)，并同步更新从零部署指南、接口参考、使用手册、任务包 README 与部署请求模板：先按 recorded_at/reason 查锁存，统一幂等收尾并排除未完成 RPC，区分卸载复用和永久关闭，在传输时限内连续完成联合下发并检查本轮 XML 落盘，按证据来源记录现场确认。

本次是本地文档整理，没有重新连接、启停或改变设备运行参数。端侧历史文档及清单保持；下一次交付按部署指南的离线目录层级同步本次文档，再生成新的文档清单，不覆盖旧验收证据。
