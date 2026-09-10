#!/usr/bin/env bash
set -eo pipefail

WORKSPACE="${CCS_EDGE_WORKSPACE:-/home/unitree/ccs_edge_ws}"
NAV_WORKSPACE="${CCS_GO2_NAV_WORKSPACE:-/home/unitree/go2_nav_ws}"
PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-${WORKSPACE}/config/go2_robot3}"
GROUND_STATION_IP="${CCS_GROUND_STATION_IP:-192.168.50.101}"
NTP_SERVER="${CCS_NTP_SERVER:-${GROUND_STATION_IP}}"
ROS_IP_VALUE="${CCS_ROS_IP:-192.168.50.112}"
NETWORK_INTERFACE="${CCS_GO2_NETWORK_INTERFACE:-go2dds}"
CAMERA_SERIAL="339222070647"
STATE_DIR="${WORKSPACE}/run/managed"
LOG_DIR="${WORKSPACE}/logs/managed"
PREFLIGHT="${WORKSPACE}/scripts/ccs_go2_preflight.py"
READINESS="${WORKSPACE}/scripts/ccs_ros_readiness.py"
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

report() { printf '[%s] %s\n' "$1" "$2"; }
fail() { report ERROR "$*" >&2; exit 1; }
ros_node_exists() { timeout 5 rosnode list 2>/dev/null | grep -Fxq -- "$1"; }
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
  # Stop the command consumer before publishing zero velocity and disabling.
  if [[ "${MANAGED[8]}" == true ]]; then
    if stop_process "${PIDS[8]}"; then
      MANAGED[8]=false
      rm -f -- "${STATE_DIR}/task_control.pid"
    else
      exit_code=1
    fi
  fi
  if [[ "${MANAGED[1]}" == true ]]; then
    timeout 4 rostopic pub -1 /cmd_vel_nav geometry_msgs/Twist \
      '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' >/dev/null 2>&1
    response="$(timeout 8 rosservice call /go2_sdk_bridge_real/enable 'data: false' 2>&1)"
    if ! grep -Eq '^success: (True|true)$' <<<"${response}"; then
      report ERROR "GO2 disable service was not successful: ${response}"
      exit_code=1
    fi
    if ! python3 "${READINESS}" disabled --timeout 10 --max-age 3; then
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
  exit "${exit_code}"
}

for setup in /opt/ros/noetic/setup.bash "${NAV_WORKSPACE}/devel/setup.bash" "${WORKSPACE}/devel/setup.bash"; do
  [[ -r "${setup}" ]] || fail "ROS workspace is not built: ${setup}"
done
for helper in "${PREFLIGHT}" "${READINESS}" "${WORKSPACE}/scripts/ccs_sntp_sync.py"; do
  [[ -r "${helper}" ]] || fail "Workspace helper is missing: ${helper}"
done
source /opt/ros/noetic/setup.bash
source "${NAV_WORKSPACE}/devel/setup.bash"
source "${WORKSPACE}/devel/setup.bash" --extend
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"
export PYTHONDONTWRITEBYTECODE=1
python3 "${PREFLIGHT}" --profile "${PROFILE_CONFIG_DIR}" --workspace "${WORKSPACE}" \
  --nav-workspace "${NAV_WORKSPACE}" --device-ip "${ROS_IP_VALUE}" --station-ip "${GROUND_STATION_IP}" || fail "Profile preflight failed."
ip -o -4 addr show dev eth0 | awk '{print $4}' | grep -Fxq "${ROS_IP_VALUE}/24" || fail "GO2 eth0 LAN address is missing."
ip -o -4 addr show dev "${NETWORK_INTERFACE}" | awk '{print $4}' | grep -Fxq '192.168.123.18/24' || fail "GO2 DDS address is missing."
ip -o -4 addr show dev eth1 | awk '{print $4}' | grep -Fxq '192.168.1.50/24' || fail "Livox eth1 address is missing."
ping -I eth1 -c 1 -W 2 192.168.1.119 >/dev/null || fail "MID360 is unreachable on eth1."
python3 "${WORKSPACE}/scripts/ccs_sntp_sync.py" --server "${NTP_SERVER}" --retries 2 --max-offset 2 || fail "Ground-station time differs by over 2 seconds or is unavailable; check systemd-timesyncd."
gst-inspect-1.0 srtsink >/dev/null 2>&1 || fail "GStreamer SRT plugin is missing."
gst-inspect-1.0 x264enc >/dev/null 2>&1 || fail "GStreamer x264 plugin is missing."

launch() {
  local index="$1"; shift
  local name="${LAUNCH_NAMES[index]}" node="${NODE_NAMES[index]}" log_file="${LOG_DIR}/${LAUNCH_NAMES[index]}.log"
  python3 "${PREFLIGHT}" --launch "$@" || fail "Invalid ${name} launch or missing executable."
  [[ "${CHECK_ONLY}" == true ]] && return 0
  ros_node_exists "${node}" && fail "Refusing to replace existing node ${node}; stop its owner first."
  roslaunch "$@" >"${log_file}" 2>&1 </dev/null &
  PIDS[index]="$!"
  MANAGED[index]=true
  printf '%s\n' "${PIDS[index]}" >"${STATE_DIR}/${name}.pid"
  wait_for_node "${node}" "${PIDS[index]}" || fail "${name} failed; inspect ${log_file}"
  report OK "${name} node registered."
}
check_on_demand() {
  local extrinsics="${NAV_WORKSPACE}/src/go2_core/config/extrinsics.yaml"
  python3 "${PREFLIGHT}" --launch epgeneral_map_stream mapping_prerequisites_go2_robot3.launch \
    extrinsics_file:="${extrinsics}" output_path:="${WORKSPACE}/run/map/current/public_map.pcd"
  python3 "${PREFLIGHT}" --launch epgeneral_go2_integration mapping_fast_lio.launch \
    lock_file:="${WORKSPACE}/run/go2_stack.lock"
  python3 "${PREFLIGHT}" --launch go2_mapping export_occupancy.launch \
    map_root:="${WORKSPACE}/run/map/export" map_name:=current \
    input_pcd:="${WORKSPACE}/run/map/export/public_map.pcd" \
    output_pgm:="${WORKSPACE}/run/map/export/map.pgm" output_yaml:="${WORKSPACE}/run/map/export/map.yaml"
  python3 "${PREFLIGHT}" --launch epgeneral_go2_integration navigation_guard.launch \
    lock_file:="${WORKSPACE}/run/go2_stack.lock"
  python3 "${PREFLIGHT}" --launch epgeneral_go2_integration navigation.launch \
    map_name:=__preflight__ map_root:="${WORKSPACE}/maps/download" extrinsics_file:="${extrinsics}"
}
check_service_type() {
  local service="$1" expected="$2" actual
  actual="$(timeout 5 rosservice type "${service}" 2>/dev/null)" || return 1
  [[ "${actual}" == "${expected}" ]] || fail "${service} has type ${actual}, expected ${expected}."
  report OK "${service}: ${actual}"
}
check_on_demand
if master_exists; then
  for pair in /go2_sdk_bridge_real/enable:std_srvs/SetBool /go2_navigation_supervisor/reset:std_srvs/Trigger; do
    check_service_type "${pair%:*}" "${pair#*:}" || report INFO "${pair%:*} is not running; wire check deferred until its owning launch runs."
  done
fi

if [[ "${CHECK_ONLY}" != true ]]; then
  mkdir -p "${LOG_DIR}" "${STATE_DIR}"
  exec 9>"${STATE_DIR}/startup.lock"
  flock -n 9 || fail "The GO2 workspace startup script is already running."
  printf '%s\n' "$$" >"${STATE_DIR}/startup.pid"
  trap 'shutdown_all 130' INT
  trap 'shutdown_all 143' TERM
  trap 'shutdown_all $?' EXIT
  if master_exists; then
    existing_nodes="$(timeout 5 rosnode list)"
    for node in "${NODE_NAMES[@]}" /camera/realsense2_camera_manager /go2_sdk_bridge_mock /go2_velocity_shaper \
      /epgeneral_navigation_task_adapter /laserMapping /go2_map_builder /go2_map_accumulator \
      /go2_pose_adapter /go2_tf_manager /go2_ndt_localizer /go2_localization_guard /move_base \
      /ccs_go2_mapping_guard /ccs_go2_navigation_guard; do
      grep -Fxq -- "${node}" <<<"${existing_nodes}" && fail "Existing GO2/CCS node ${node}; stop its owning workflow first."
    done
    report INFO "Reusing the existing ROS master; this script will not stop it."
  else
    roscore >"${LOG_DIR}/roscore.log" 2>&1 </dev/null &
    ROSCORE_PID="$!"
    ROSCORE_MANAGED=true
    printf '%s\n' "${ROSCORE_PID}" >"${STATE_DIR}/roscore.pid"
    wait_for_master || fail "ROS master failed to start."
  fi
fi

launch 0 livox_ros_driver2 msg_MID360.launch
launch 1 go2_control control.launch use_real_sdk:=true network_interface:="${NETWORK_INTERFACE}"
if [[ "${CHECK_ONLY}" != true ]]; then
  wait_for_node /go2_velocity_shaper "${PIDS[1]}" || fail "GO2 velocity shaper is unavailable."
  check_service_type /go2_sdk_bridge_real/enable std_srvs/SetBool || fail "GO2 enable service is unavailable."
  python3 "${READINESS}" disabled --timeout 20 --max-age 3 || fail "Chassis did not start disabled with fresh DDS state."
fi
launch 2 realsense2_camera rs_camera.launch device_type:=d435i serial_no:="_${CAMERA_SERIAL}" \
  enable_color:=true color_width:=640 color_height:=480 color_fps:=15 enable_depth:=false \
  enable_infra:=false enable_infra1:=false enable_infra2:=false enable_gyro:=false enable_accel:=false publish_tf:=false
if [[ "${CHECK_ONLY}" != true ]]; then
  python3 "${READINESS}" inputs --timeout 30 --max-age 3 || fail "Sensor inputs did not become fresh."
fi
launch 3 epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="${PROFILE_CONFIG_DIR}/epgeneral_mqtav.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" log_dir:="${WORKSPACE}/logs/mqtav"
launch 4 epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  telemetry_config_file:="${PROFILE_CONFIG_DIR}/udp_telemetry.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  destination_host:="${GROUND_STATION_IP}" destination_port:=14560 \
  link_status_topic:=/qrd/QRD_003/link/udp_tx diagnostics_topic:=/qrd/QRD_003/diagnostics
launch 5 epgeneral_video_srt epgeneral_realsense_d435i_srt.launch \
  video_config_file:="${PROFILE_CONFIG_DIR}/video.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
launch 6 epgeneral_map_stream epgeneral_map_stream.launch \
  mapping_config_file:="${PROFILE_CONFIG_DIR}/map_stream.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
launch 7 epgeneral_relocalization epgeneral_relocalization.launch \
  config_file:="${PROFILE_CONFIG_DIR}/relocalization.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" log_dir:="${WORKSPACE}/logs/relocalization"
if [[ "${CHECK_ONLY}" != true ]]; then
  python3 "${READINESS}" disabled --timeout 10 --max-age 3 || fail "Fresh disabled state was lost before task startup."
fi
launch 8 epgeneral_task_control navigation_task_control.launch \
  task_config_file:="${PROFILE_CONFIG_DIR}/task_control.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"

if [[ "${CHECK_ONLY}" == true ]]; then
  report OK "Configuration, time, persistent and on-demand launch checks passed; no nodes were started."
  exit 0
fi
wait_for_node /epgeneral_navigation_task_adapter "${PIDS[8]}" || fail "Navigation task adapter is unavailable."
report OK "QRD_003 services are running. Mapping and navigation remain task-managed. Ctrl+C stops this workflow."
while true; do
  sleep 2
  for index in "${!NODE_NAMES[@]}"; do
    owned_process_alive "${PIDS[index]}" || fail "${LAUNCH_NAMES[index]} roslaunch exited."
    ros_node_exists "${NODE_NAMES[index]}" || fail "${NODE_NAMES[index]} exited."
  done
  ros_node_exists /epgeneral_navigation_task_adapter || fail "Navigation task adapter exited."
  ros_node_exists /go2_velocity_shaper || fail "GO2 velocity shaper exited."
done
