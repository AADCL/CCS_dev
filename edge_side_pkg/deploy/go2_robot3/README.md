# GO2 Robot 3: QRD_003

This profile targets `unitree@192.168.50.112`, displayed by CCS as
`宇树Go2_3`, with ground station `192.168.50.101`. It uses ROS Noetic on
Ubuntu 20.04 ARM64, native underlay `/home/unitree/go2_nav_ws`, and CCS overlay
`/home/unitree/ccs_edge_ws`.

## Daily Operation

Run on the GO2 device:

```bash
cd /home/unitree/ccs_edge_ws
./start_ccs_edge_dev.sh --check
./start_ccs_edge_dev.sh
```

The check reads configuration, confirms physical ROS packages and generated
interfaces, expands persistent and on-demand launches, resolves executables,
checks network interfaces and MID360 reachability, and queries station time
with a two-second limit. It does not launch or stop nodes or adjust the clock.
An absent navigation reset service is reported as deferred until its owning
stack is running; its generated Trigger contract is checked.

Use Ctrl+C for foreground shutdown. For a managed background process:

```bash
kill -TERM "$(cat /home/unitree/ccs_edge_ws/run/managed/startup.pid)"
```

Shutdown stops task consumption, sends zero velocity, requests SetBool(false),
and requires new diagnostics confirming a disabled bridge and current DDS
state. It stops owned launch processes in reverse order and retains a reused
ROS master. An unconfirmed disable produces an error that must be inspected
before further operation.

## Runtime Layout

| Location under `/home/unitree/ccs_edge_ws` | Purpose |
| --- | --- |
| `src/<package>` | Eight physical CCS ROS package directories |
| `config/go2_robot3/` | Device-specific YAML and timesyncd example |
| `scripts/ccs_go2_preflight.py` | Read-only package, launch and profile checks |
| `scripts/ccs_ros_readiness.py` | Fresh sensor and disabled-state observations |
| `scripts/ccs_sntp_sync.py` | Read-only SNTP query, no clock-setting mode |
| `start_ccs_edge_dev.sh` | Manual startup and owned-process supervisor |
| `verify_ros_contract.py` | Isolated ROS service handshake and reset-rejection probe |
| `logs/managed/` | Per-launch logs and owned ROS master log |
| `run/managed/` | Startup lock and owned process IDs |
| `run/go2_stack.lock` | Mapping/navigation exclusivity lock |
| `run/state/` | Relocalization and persistent emergency-stop state |
| `maps/download/` | Maps delivered through CCS |
| `maps/sessions/`, `maps/archive/` | Mapping sessions and completed artifacts |
| `mission/` | This device's task storage |

Runtime does not depend on historical `vendor` or `bin` directories. Do not
copy another robot's task, map binding, PID or emergency-stop state. Retain
existing target state during subsequent updates.

## Hardware And Services

Nine persistent launches provide Livox, the real GO2 SDK bridge, D435i,
MQTT, UDP telemetry, SRT video, map control, relocalization control and task
control. The bridge constructor starts disabled. Startup confirms current
disabled diagnostics before task consumption. Native total bringup is unused.

| Interface | Configuration |
| --- | --- |
| LAN | `eth0`, `192.168.50.112/24` |
| DDS | `go2dds`, `192.168.123.18/24` |
| MID360 | `eth1`, host `192.168.1.50/24`, sensor `192.168.1.119` |
| D435i | Serial `339222070647`, RGB `640x480`, `15 FPS` |
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
tasks never remove the persisted latch.

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
