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
