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
CAMERA_SERIAL="${CCS_D435_SERIAL:-}"
READINESS="${WORKSPACE}/scripts/ccs_ros_readiness.py"
STARTUP_LOG=""
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

runtime_record() {
  [[ -n "${STARTUP_LOG}" ]] || return 0
  printf '%s %s\n' "$(date -Is)" "$*" >>"${STARTUP_LOG}"
}
report() {
  local level="$1" message="$2"
  printf '[%s] %s\n' "${level}" "${message}"
  runtime_record "[${level}] ${message}"
}
fail() { report ERROR "$*" >&2; exit 1; }
run_quiet() {
  local output status
  if output="$("$@" 2>&1)"; then
    return 0
  else
    status=$?
    [[ -z "${output}" ]] || runtime_record "${output}"
    [[ -z "${output}" ]] || printf '%s\n' "${output}" >&2
    return "${status}"
  fi
}
ros_node_exists() {
  local nodes
  nodes="$(timeout 5 rosnode list 2>/dev/null)" || return 1
  grep -Fxq -- "$1" <<<"${nodes}"
}
check_runtime_nodes() {
  local attempt index nodes status reason node
  local monitor_log="${LOG_DIR}/runtime_monitor.log"
  local query_error="${STATE_DIR}/runtime_query.err"
  local required_nodes=("${NODE_NAMES[@]}" /epgeneral_navigation_task_adapter /go2_velocity_shaper)
  for attempt in 1 2 3; do
    for index in "${!NODE_NAMES[@]}"; do
      if ! owned_process_alive "${PIDS[index]}"; then
        fail "${LAUNCH_NAMES[index]} roslaunch exited or ownership changed; inspect ${LOG_DIR}/${LAUNCH_NAMES[index]}.log."
      fi
    done
    if [[ "${ROSCORE_MANAGED}" == true ]] && ! owned_process_alive "${ROSCORE_PID}"; then
      fail "Owned ROS master exited; inspect ${LOG_DIR}/roscore.log."
    fi
    if nodes="$(timeout 5 rosnode list 2>"${query_error}")"; then
      reason=""
      for node in "${required_nodes[@]}"; do
        if ! grep -Fxq -- "${node}" <<<"${nodes}"; then
          reason="Required ROS node ${node} is absent from the master"
          break
        fi
      done
      if [[ -z "${reason}" ]]; then
        if [[ "${attempt}" -gt 1 ]]; then
          printf '%s ROS node check recovered on attempt %s.\n' "$(date -Is)" "${attempt}" >>"${monitor_log}"
        fi
        rm -f -- "${query_error}"
        return 0
      fi
    else
      status=$?
      reason="ROS node query failed (exit ${status})"
    fi
    {
      printf '%s attempt=%s/3 %s\n' "$(date -Is)" "${attempt}" "${reason}"
      cat "${query_error}"
    } >>"${monitor_log}"
    if [[ "${attempt}" -lt 3 ]]; then
      sleep 1
    fi
  done
  [[ ! -s "${query_error}" ]] || cat "${query_error}" >&2
  fail "${reason} after 3 checks; inspect ${monitor_log} and ${LOG_DIR}/control.log."
}
master_exists() { timeout 5 rosparam list >/dev/null 2>&1; }
owned_process_alive() {
  kill -0 "$1" 2>/dev/null && [[ "$(ps -o ppid= -p "$1" | tr -d ' ')" == "$$" ]]
}
wait_for_master() {
  local attempt
  for attempt in $(seq 1 20); do
    master_exists && return 0
    owned_process_alive "${ROSCORE_PID}" || return 1
    sleep 1
  done
  return 1
}
wait_for_node() {
  local node="$1" pid="$2" attempt
  for attempt in $(seq 1 30); do
    owned_process_alive "${pid}" || return 1
    ros_node_exists "${node}" && return 0
    sleep 1
  done
  return 1
}
stop_process() {
  local pid="$1" attempt
  kill -0 "${pid}" 2>/dev/null || { wait "${pid}" 2>/dev/null || true; return 0; }
  owned_process_alive "${pid}" || { report ERROR "PID ${pid} is no longer owned by this startup."; return 1; }
  # roslaunch propagates SIGINT to its owned nodes and nodelet manager.
  kill -INT "${pid}" 2>/dev/null || true
  for attempt in $(seq 1 80); do
    kill -0 "${pid}" 2>/dev/null || { wait "${pid}" 2>/dev/null || true; return 0; }
    sleep 0.25
  done
  owned_process_alive "${pid}" || return 1
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
  runtime_record "shutdown started exit_code=${exit_code}"
  # Stop the command consumer before publishing zero velocity and disabling.
  if [[ "${MANAGED[8]}" == true ]]; then
    if stop_process "${PIDS[8]}"; then
      MANAGED[8]=false
      rm -f -- "${STATE_DIR}/task_control.pid"
    else
      exit_code=1
    fi
  fi
  if [[ "${MANAGED[1]}" == true && "${USE_REAL_SDK}" == true ]]; then
    timeout 4 rostopic pub -1 /cmd_vel_nav geometry_msgs/Twist \
      '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' >/dev/null 2>&1
    response="$(timeout 8 rosservice call /go2_sdk_bridge_real/enable 'data: false' 2>&1)"
    if ! grep -Eq '^success: (True|true)$' <<<"${response}"; then
      report ERROR "GO2 disable service was not successful: ${response}"
      exit_code=1
    fi
    if ! run_quiet python3 "${READINESS}" disabled --timeout 10 --max-age 3; then
      report ERROR "Fresh GO2 disabled diagnostics were not confirmed; inspect the chassis."
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
  rm -f -- "${STATE_DIR}/startup.pid"
  runtime_record "shutdown completed exit_code=${exit_code}"
  exit "${exit_code}"
}

[[ -r /opt/ros/noetic/setup.bash ]] || fail "ROS Noetic is unavailable."
[[ -r "${NAV_WORKSPACE}/devel/setup.bash" ]] || fail "Native GO2 underlay is not built."
[[ -r "${WORKSPACE}/devel/setup.bash" ]] || fail "CCS overlay is not built."
[[ -r "${NAV_WORKSPACE}/src/go2_core/config/extrinsics.yaml" ]] || fail "GO2 extrinsics are missing."
[[ -r "${WORKSPACE}/scripts/ccs_sntp_sync.py" ]] || fail "Workspace SNTP helper is missing."
[[ -r "${READINESS}" ]] || fail "Workspace readiness helper is missing: ${READINESS}."
for name in device.yaml epgeneral_mqtav.yaml udp_telemetry.yaml video.yaml map_stream.yaml relocalization.yaml task_control.yaml; do
  [[ -r "${PROFILE_CONFIG_DIR}/${name}" ]] || fail "Missing profile: ${name}"
done
source /opt/ros/noetic/setup.bash
source "${NAV_WORKSPACE}/devel/setup.bash"
source "${WORKSPACE}/devel/setup.bash" --extend
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"
export PYTHONDONTWRITEBYTECODE=1
run_quiet python3 - "${PROFILE_CONFIG_DIR}" "${ROS_IP_VALUE}" "${GROUND_STATION_IP}" <<'PY'
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
run_quiet python3 "${WORKSPACE}/scripts/ccs_sntp_sync.py" --server "${NTP_SERVER}" --retries 2 --max-offset 5 || fail "Ground-station time check failed; synchronize before starting."
run_quiet python3 -c 'import yaml, msgpack, paho.mqtt.client' || fail "CCS Python dependencies are missing."
gst-inspect-1.0 srtsink >/dev/null 2>&1 || fail "GStreamer SRT plugin is missing."
gst-inspect-1.0 x264enc >/dev/null 2>&1 || fail "GStreamer x264 plugin is missing."

start_launch() {
  local index="$1"; shift
  local announce_ready=true
  if [[ "${1:-}" == "--defer-ready" ]]; then
    announce_ready=false
    shift
  fi
  local name="${LAUNCH_NAMES[index]}" node="${NODE_NAMES[index]}" log_file="${LOG_DIR}/${LAUNCH_NAMES[index]}.log"
  if [[ "${CHECK_ONLY}" == true ]]; then
    run_quiet roslaunch --files "$@"
    return
  fi
  ros_node_exists "${node}" && fail "Refusing to replace existing node ${node}; stop its owner first."
  setsid roslaunch "$@" >"${log_file}" 2>&1 </dev/null &
  PIDS[index]="$!"
  MANAGED[index]=true
  printf '%s\n' "${PIDS[index]}" >"${STATE_DIR}/${name}.pid"
  wait_for_node "${node}" "${PIDS[index]}" || fail "${name} failed; inspect ${log_file}"
  if [[ "${announce_ready}" == true ]]; then
    report OK "${name} is ready."
  fi
  return 0
}

command -v setsid >/dev/null 2>&1 || fail "setsid is required for controlled shutdown."

if [[ "${CHECK_ONLY}" != true ]]; then
  mkdir -p "${STATE_DIR}"
  exec 9>"${STATE_DIR}/startup.lock"
  flock -n 9 || fail "The GO2 workspace startup script is already running."
  mkdir -p "${LOG_DIR}"
  STARTUP_LOG="${LOG_DIR}/startup.log"
  runtime_record "startup pid=$$ log_dir=${LOG_DIR}"
  printf '%s\n' "$$" >"${STATE_DIR}/startup.pid"
  trap 'shutdown_all 130' INT
  trap 'shutdown_all 143' TERM
  trap 'shutdown_all $?' EXIT
  if master_exists; then
    existing_nodes="$(timeout 5 rosnode list)"
    for node in "${NODE_NAMES[@]}" /camera/realsense2_camera_manager /go2_sdk_bridge_mock /go2_velocity_shaper /epgeneral_navigation_task_adapter \
      /laserMapping /go2_map_builder /go2_map_accumulator /go2_pose_adapter /go2_tf_manager \
      /go2_ndt_localizer /go2_localization_guard /move_base /ccs_go2_mapping_guard /ccs_go2_navigation_guard; do
      grep -Fxq -- "${node}" <<<"${existing_nodes}" && fail "Existing GO2/CCS node ${node}; stop its owning workflow first."
    done
  else
    setsid roscore >"${LOG_DIR}/roscore.log" 2>&1 </dev/null &
    ROSCORE_PID="$!"
    printf '%s\n' "${ROSCORE_PID}" >"${STATE_DIR}/roscore.pid"
    ROSCORE_MANAGED=true
    wait_for_master || fail "ROS master failed to start."
  fi
fi

start_launch 0 livox_ros_driver2 msg_MID360.launch
start_launch 1 go2_control control.launch use_real_sdk:="${USE_REAL_SDK}" network_interface:="${NETWORK_INTERFACE}"
if [[ "${CHECK_ONLY}" != true && "${USE_REAL_SDK}" == true ]]; then
  wait_for_node /go2_velocity_shaper "${PIDS[1]}" || fail "GO2 velocity shaper is unavailable."
  run_quiet python3 "${READINESS}" disabled --timeout 20 --max-age 3 || fail "Chassis did not start disabled with fresh DDS state; inspect ${LOG_DIR}/control.log."
fi
camera_args=(
  realsense2_camera rs_camera.launch enable_color:=true
  color_width:=640 color_height:=480 color_fps:=30 enable_depth:=false enable_infra:=false
  enable_infra1:=false enable_infra2:=false enable_gyro:=false enable_accel:=false publish_tf:=false
)
if [[ -n "${CAMERA_SERIAL}" ]]; then
  camera_args+=("serial_no:=${CAMERA_SERIAL}")
fi
start_launch 2 --defer-ready "${camera_args[@]}"
if [[ "${CHECK_ONLY}" != true ]]; then
  run_quiet python3 "${READINESS}" camera --timeout 30 --max-age 3 || \
    fail "D435i RGB did not produce fresh frames within 30 seconds; inspect ${LOG_DIR}/camera.log."
  report OK "camera is ready."
  if [[ "${USE_REAL_SDK}" == true ]]; then
    run_quiet python3 "${READINESS}" inputs --timeout 30 --max-age 3 || fail "Core inputs are not fresh; inspect ${LOG_DIR}/livox.log and ${LOG_DIR}/control.log."
  else
    for topic in /livox/lidar /livox/imu; do
      run_quiet timeout 20 rostopic echo -n 1 "${topic}" || fail "No input on ${topic}; inspect ${LOG_DIR}/livox.log."
    done
  fi
fi
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
if [[ "${CHECK_ONLY}" != true && "${USE_REAL_SDK}" == true ]]; then
  run_quiet python3 "${READINESS}" disabled --timeout 10 --max-age 3 || fail "Fresh disabled state was lost before task startup; inspect ${LOG_DIR}/control.log."
fi
start_launch 8 epgeneral_task_control navigation_task_control.launch \
  task_config_file:="${PROFILE_CONFIG_DIR}/task_control.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"

if [[ "${CHECK_ONLY}" == true ]]; then
  report OK "GO2 configuration, time and persistent launch files passed preflight; no nodes were started."
  exit 0
fi
wait_for_node /epgeneral_navigation_task_adapter "${PIDS[8]}" || fail "Navigation task adapter is unavailable."
wait_for_node /go2_velocity_shaper "${PIDS[1]}" || fail "GO2 velocity shaper is unavailable."
report OK "GO2 CCS services are running. Mapping/localization remain task-managed. Ctrl+C stops only this workflow."
while true; do
  sleep 2
  check_runtime_nodes
done
