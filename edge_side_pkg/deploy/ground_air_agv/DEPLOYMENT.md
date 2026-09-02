# Ground-Air AGV CCS Edge Deployment

This profile targets `AGV_001` at `192.168.50.130` on Ubuntu 20.04, ROS Noetic,
and ARM64. The CCS overlay is `/home/bitcq/ccs_edge_ws`; `/opt/ros/noetic` and
`/home/bitcq/catkin_ws` remain read-only underlays.

The one-click startup runs MAVROS, the existing Livox MID-360 driver, MQTT
status reporting, and UDP telemetry.
FAST-LIO, ground filtering, mapping, relocalization, navigation, control, tasks,
MQTT, UDP telemetry, and video are not part of this bringup. Their packages and
profile files are staged only for later integration.

Run:

```bash
cd /home/bitcq/ccs_edge_ws
./start_ccs_edge_dev.sh
```

Logs are written to `~/.ros/ccs_edge_dev_ground_air_agv/log`. The script never
arms the FCU, changes flight mode, or publishes velocity, pose, or waypoint
commands.
