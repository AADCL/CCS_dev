# QRD_003 Validation Record

Validation date: 2026-09-09. The target was `unitree@192.168.50.112`,
device `QRD_003` (宇树Go2_3), with the command platform at
`192.168.50.101`. This was a no-motion deployment: control remained disabled,
and no movement goal, complete mapping run, relocalization run, or navigation
task was executed.

## Result summary

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

## Startup-script incremental update (2026-09-10)

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

## Mapping negotiation line-ending repair (2026-09-10)

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

### Runtime first-map export repair (2026-09-10 15:58)

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

## Source, payload and backup

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

## Build and static validation

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

## Target preflight and contracts

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

## Live sensor sample

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

## UDP capture

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

## Platform reload and MQTT capture

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

## Camera and one-click startup

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

## Safe shutdown and remaining acceptance

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

## Startup time-service availability update (2026-09-10)

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

## Runtime watchdog correction (2026-09-10)

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

## Emergency-stop recovery and per-start logs (2026-09-10)

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

## Local pre-PR review follow-up (2026-09-11)

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
