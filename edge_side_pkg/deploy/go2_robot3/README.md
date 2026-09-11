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

## Runtime Monitoring

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

## Runtime Layout

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

## Hardware And Services

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
