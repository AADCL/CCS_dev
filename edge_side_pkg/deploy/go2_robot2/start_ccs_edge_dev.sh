#!/usr/bin/env bash
set -eo pipefail

WORKSPACE="${CCS_EDGE_WORKSPACE:-/home/unitree/ccs_edge_ws}"
NAV_WORKSPACE="${CCS_GO2_NAV_WORKSPACE:-/home/unitree/go2_nav_ws}"
PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-${WORKSPACE}/config/go2_robot2}"
GROUND_STATION_IP="${CCS_GROUND_STATION_IP:-192.168.50.101}"
NTP_SERVER="${CCS_NTP_SERVER:-${GROUND_STATION_IP}}"
ROS_IP_VALUE="${CCS_ROS_IP:-192.168.50.111}"
NETWORK_INTERFACE="${CCS_GO2_NETWORK_INTERFACE:-go2dds}"
USE_REAL_SDK="${CCS_GO2_USE_REAL_SDK:-true}"
STATE_DIR="${CCS_EDGE_STATE_DIR:-${WORKSPACE}/run/managed}"
LOG_DIR="${CCS_EDGE_LOG_DIR:-${WORKSPACE}/logs/managed}"
CHECK_ONLY=false
SHUTDOWN_STARTED=false
ROSCORE_MANAGED=false
ROSCORE_PID=""
LAUNCH_NAMES=(livox control camera mqtav udp_telemetry video map_stream relocalization task_control)
NODE_NAMES=(/livox_lidar_publisher2 /go2_sdk_bridge_real /camera/realsense2_camera /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_video_srt /epgeneral_map_stream /epgeneral_relocalization /epgeneral_task_control)
MANAGED=(false false false false false false false false false)
PIDS=("" "" "" "" "" "" "" "" "")

case "${1:-}" in
  "") ;;
  --check|--preflight) CHECK_ONLY=true ;;
  *) printf 'Usage: %s [--check|--preflight]\n' "$0" >&2; exit 2 ;;
esac
[[ "$#" -le 1 ]] || { printf 'Unexpected arguments\n' >&2; exit 2; }
case "${USE_REAL_SDK}" in
  true) ;;
  false) NODE_NAMES[1]=/go2_sdk_bridge_mock ;;
  *) printf 'CCS_GO2_USE_REAL_SDK must be true or false\n' >&2; exit 2 ;;
esac

report() { printf '[%s] %s\n' "$1" "$2"; }
fail() { report ERROR "$*" >&2; exit 1; }
ros_node_exists() { rosnode list 2>/dev/null | grep -Fxq -- "$1"; }
wait_for_master() {
  local attempt
  for attempt in $(seq 1 20); do
    rosparam list >/dev/null 2>&1 && return 0
    [[ -z "${ROSCORE_PID}" ]] || kill -0 "${ROSCORE_PID}" 2>/dev/null || return 1
    sleep 1
  done
  return 1
}
wait_for_node() {
  local node="$1" pid="$2" attempt
  for attempt in $(seq 1 30); do
    kill -0 "${pid}" 2>/dev/null || return 1
    ros_node_exists "${node}" && return 0
    sleep 1
  done
  return 1
}
wait_for_message() { timeout "${2:-20}" rostopic echo -n 1 "$1" >/dev/null 2>&1; }

stop_process() {
  local pid="$1" attempt
  kill -0 "${pid}" 2>/dev/null || return 0
  # roslaunch propagates SIGINT to its children, including nodelets.
  kill -INT "${pid}" 2>/dev/null || true
  for attempt in $(seq 1 80); do
    kill -0 "${pid}" 2>/dev/null || { wait "${pid}" 2>/dev/null || true; return 0; }
    sleep 0.25
  done
  kill -TERM "${pid}" 2>/dev/null || true
  for attempt in $(seq 1 20); do
    kill -0 "${pid}" 2>/dev/null || { wait "${pid}" 2>/dev/null || true; return 0; }
    sleep 0.25
  done
  report ERROR "Owned process ${pid} did not exit; inspect it before restarting."
  return 1
}

shutdown_all() {
  local exit_code="${1:-0}" index response
  [[ "${SHUTDOWN_STARTED}" == true ]] && return
  SHUTDOWN_STARTED=true
  trap - INT TERM EXIT
  set +e
  # Stop the command consumer first so it cannot arm while teardown disables.
  if [[ "${MANAGED[8]}" == true ]]; then
    if stop_process "${PIDS[8]}"; then
      MANAGED[8]=false
      rm -f -- "${STATE_DIR}/task_control.pid"
    else
      exit_code=1
    fi
  fi
  if [[ "${MANAGED[1]}" == true && "${USE_REAL_SDK}" == true ]]; then
    timeout 3 rostopic pub -1 /cmd_vel_nav geometry_msgs/Twist \
      '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' >/dev/null 2>&1
    response="$(timeout 8 rosservice call /go2_sdk_bridge_real/enable 'data: false' 2>&1)"
    if ! grep -Eq '^success: (True|true)$' <<<"${response}"; then
      report ERROR "GO2 disable was not confirmed: ${response}"
      exit_code=1
    fi
  fi
  for ((index=${#LAUNCH_NAMES[@]} - 1; index>=0; index--)); do
    [[ "${MANAGED[index]}" == true ]] || continue
    if stop_process "${PIDS[index]}"; then
      rm -f -- "${STATE_DIR}/${LAUNCH_NAMES[index]}.pid"
    else
      exit_code=1
    fi
  done
  if [[ "${ROSCORE_MANAGED}" == true ]]; then
    if stop_process "${ROSCORE_PID}"; then
      rm -f -- "${STATE_DIR}/roscore.pid"
    else
      exit_code=1
    fi
  fi
  exit "${exit_code}"
}

[[ -r /opt/ros/noetic/setup.bash ]] || fail "ROS Noetic is unavailable."
[[ -r "${NAV_WORKSPACE}/devel/setup.bash" ]] || fail "Native GO2 underlay is not built."
[[ -r "${WORKSPACE}/devel/setup.bash" ]] || fail "CCS overlay is not built."
[[ -r "${NAV_WORKSPACE}/src/go2_core/config/extrinsics.yaml" ]] || fail "GO2 extrinsics are missing."
[[ -r "${WORKSPACE}/scripts/ccs_sntp_sync.py" ]] || fail "Workspace SNTP helper is missing."
for name in device.yaml epgeneral_mqtav.yaml udp_telemetry.yaml video.yaml map_stream.yaml relocalization.yaml task_control.yaml; do
  [[ -r "${PROFILE_CONFIG_DIR}/${name}" ]] || fail "Missing profile: ${name}"
done
source /opt/ros/noetic/setup.bash
source "${NAV_WORKSPACE}/devel/setup.bash"
source "${WORKSPACE}/devel/setup.bash" --extend
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"
python3 - "${PROFILE_CONFIG_DIR}" "${ROS_IP_VALUE}" "${GROUND_STATION_IP}" <<'PY'
import pathlib
import sys
import yaml
profile = pathlib.Path(sys.argv[1])
device = yaml.safe_load((profile / "device.yaml").read_text())["device"]
if device["id"] != "QRD_002" or device["ip"] != sys.argv[2]:
    raise SystemExit("GO2 profile identity does not match the startup environment")
for name in ("map_stream", "relocalization", "task_control"):
    config = yaml.safe_load((profile / (name + ".yaml")).read_text())
    if config["network"]["ground_station_ip"] != sys.argv[3]:
        raise SystemExit(name + " ground station does not match the startup environment")
mqtt = yaml.safe_load((profile / "epgeneral_mqtav.yaml").read_text())
udp = yaml.safe_load((profile / "udp_telemetry.yaml").read_text())
if mqtt["mqtt"]["ground_station_ip"] != sys.argv[3] or udp["network"]["destination_host"] != sys.argv[3]:
    raise SystemExit("MQTT/UDP ground station does not match the startup environment")
PY
ip -o -4 addr show | awk '{print $4}' | grep -Fxq "${ROS_IP_VALUE}/24" || fail "GO2 LAN address is missing."
if [[ "${USE_REAL_SDK}" == true ]]; then
  ip -o -4 addr show dev "${NETWORK_INTERFACE}" | awk '{print $4}' | grep -Fxq '192.168.123.18/24' || fail "GO2 DDS address is missing."
fi
python3 "${WORKSPACE}/scripts/ccs_sntp_sync.py" --server "${NTP_SERVER}" --retries 2 --max-offset 5 || fail "Ground-station time check failed; synchronize before starting."
python3 -c 'import yaml, msgpack, paho.mqtt.client' || fail "CCS Python dependencies are missing."
gst-inspect-1.0 srtsink >/dev/null 2>&1 || fail "GStreamer SRT plugin is missing."
gst-inspect-1.0 x264enc >/dev/null 2>&1 || fail "GStreamer x264 plugin is missing."

start_launch() {
  local index="$1"; shift
  local name="${LAUNCH_NAMES[index]}" node="${NODE_NAMES[index]}" log_file="${LOG_DIR}/${LAUNCH_NAMES[index]}.log"
  if [[ "${CHECK_ONLY}" == true ]]; then
    roslaunch --files "$@" >/dev/null
    return
  fi
  ros_node_exists "${node}" && fail "Refusing to replace existing node ${node}; stop its owner first."
  roslaunch "$@" >"${log_file}" 2>&1 </dev/null &
  PIDS[index]="$!"
  MANAGED[index]=true
  printf '%s\n' "${PIDS[index]}" >"${STATE_DIR}/${name}.pid"
  wait_for_node "${node}" "${PIDS[index]}" || fail "${name} failed; inspect ${log_file}"
  report OK "${name} is ready."
}

if [[ "${CHECK_ONLY}" != true ]]; then
  mkdir -p "${LOG_DIR}" "${STATE_DIR}"
  exec 9>"${STATE_DIR}/startup.lock"
  flock -n 9 || fail "The GO2 workspace startup script is already running."
  trap 'shutdown_all 130' INT
  trap 'shutdown_all 143' TERM
  trap 'shutdown_all $?' EXIT
  if rosparam list >/dev/null 2>&1; then
    existing_nodes="$(rosnode list)"
    for node in "${NODE_NAMES[@]}" /go2_sdk_bridge_mock /go2_velocity_shaper /epgeneral_navigation_task_adapter \
      /laserMapping /go2_map_builder /go2_map_accumulator /go2_pose_adapter /go2_tf_manager \
      /go2_ndt_localizer /go2_localization_guard /move_base /ccs_go2_mapping_guard /ccs_go2_navigation_guard; do
      grep -Fxq -- "${node}" <<<"${existing_nodes}" && fail "Existing GO2/CCS node ${node}; stop its owning workflow first."
    done
  else
    roscore >"${LOG_DIR}/roscore.log" 2>&1 </dev/null &
    ROSCORE_PID="$!"
    printf '%s\n' "${ROSCORE_PID}" >"${STATE_DIR}/roscore.pid"
    ROSCORE_MANAGED=true
    wait_for_master || fail "ROS master failed to start."
  fi
fi

start_launch 0 livox_ros_driver2 msg_MID360.launch
start_launch 1 go2_control control.launch use_real_sdk:="${USE_REAL_SDK}" network_interface:="${NETWORK_INTERFACE}"
start_launch 2 realsense2_camera rs_camera.launch device_type:=d435i enable_color:=true \
  color_width:=640 color_height:=480 color_fps:=30 enable_depth:=false enable_infra:=false \
  enable_infra1:=false enable_infra2:=false enable_gyro:=false enable_accel:=false publish_tf:=false
start_launch 3 epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="${PROFILE_CONFIG_DIR}/epgeneral_mqtav.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" log_dir:="${WORKSPACE}/logs/mqtav"
start_launch 4 epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  telemetry_config_file:="${PROFILE_CONFIG_DIR}/udp_telemetry.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  destination_host:="${GROUND_STATION_IP}" destination_port:=14560 \
  link_status_topic:=/qrd/QRD_002/link/udp_tx diagnostics_topic:=/qrd/QRD_002/diagnostics
start_launch 5 epgeneral_video_srt epgeneral_realsense_d435i_srt.launch \
  video_config_file:="${PROFILE_CONFIG_DIR}/video.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 6 epgeneral_map_stream epgeneral_map_stream.launch \
  mapping_config_file:="${PROFILE_CONFIG_DIR}/map_stream.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 7 epgeneral_relocalization epgeneral_relocalization.launch \
  config_file:="${PROFILE_CONFIG_DIR}/relocalization.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" log_dir:="${WORKSPACE}/logs/relocalization"
start_launch 8 epgeneral_task_control navigation_task_control.launch \
  task_config_file:="${PROFILE_CONFIG_DIR}/task_control.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"

if [[ "${CHECK_ONLY}" == true ]]; then
  report OK "GO2 configuration, time and persistent launch files passed preflight; no nodes were started."
  exit 0
fi
wait_for_node /epgeneral_navigation_task_adapter "${PIDS[8]}" || fail "Navigation task adapter is unavailable."
wait_for_node /go2_velocity_shaper "${PIDS[1]}" || fail "GO2 velocity shaper is unavailable."
for topic in /livox/lidar /livox/imu /camera/color/image_raw; do
  wait_for_message "${topic}" || fail "No fresh input on ${topic}."
done
if [[ "${USE_REAL_SDK}" == true ]]; then
  for topic in /go2/imu /go2/diagnostics /go2/control/enabled; do
    wait_for_message "${topic}" || fail "No fresh GO2 state on ${topic}."
  done
fi
report OK "GO2 CCS services are running. Mapping/localization remain task-managed. Ctrl+C stops only this workflow."
while true; do
  sleep 2
  for index in "${!NODE_NAMES[@]}"; do
    if [[ "${MANAGED[index]}" == true ]]; then
      kill -0 "${PIDS[index]}" 2>/dev/null || fail "${LAUNCH_NAMES[index]} roslaunch exited."
      ros_node_exists "${NODE_NAMES[index]}" || fail "${NODE_NAMES[index]} exited."
    fi
  done
  ros_node_exists /epgeneral_navigation_task_adapter || fail "Navigation task adapter exited."
  ros_node_exists /go2_velocity_shaper || fail "GO2 velocity shaper exited."
done
