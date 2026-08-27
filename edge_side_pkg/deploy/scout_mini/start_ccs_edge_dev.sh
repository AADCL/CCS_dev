#!/usr/bin/env bash
set -eo pipefail

WORKSPACE="${CCS_EDGE_WORKSPACE:-/home/nvidia/ccs_edge_ws}"
GROUND_STATION_IP="${CCS_GROUND_STATION_IP:-192.168.50.101}"
NTP_SERVER="${CCS_NTP_SERVER:-${GROUND_STATION_IP}}"
ROS_IP_VALUE="${CCS_ROS_IP:-192.168.50.120}"
LIVOX_SETUP="${CCS_LIVOX_SETUP:-/home/nvidia/livox_fastlio/devel/setup.bash}"
REALSENSE_SETUP="${CCS_REALSENSE_SETUP:-/home/nvidia/realsense_ws/devel/setup.bash}"
NAVIGATION_SETUP="${CCS_NAVIGATION_SETUP:-/home/nvidia/github_upload/AADCL_UAV_UGV/Scout_mini/devel/setup.bash}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-${WORKSPACE}/config/scout_mini}"
STATE_DIR="${CCS_EDGE_STATE_DIR:-${HOME}/.ros/ccs_edge_dev_scout_mini}"
LOG_DIR="${STATE_DIR}/log"
PID_DIR="${STATE_DIR}/run"
START_LOG="${LOG_DIR}/startup.log"
SHUTDOWN_STARTED=false

LAUNCH_NAMES=(scout_livox d435i mqtav udp_telemetry video_srt map_stream relocalization task_control)
NODE_NAMES=(/scout_base_node /camera/realsense2_camera /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_video_srt /epgeneral_map_stream /epgeneral_relocalization /epgeneral_task_control)
EXPECTED_COMMANDS=(scout_livox_base.launch D435I.launch epgeneral_mqtav epgeneral_udp_telemetry epgeneral_video_srt epgeneral_map_stream epgeneral_relocalization scout_task_control.launch)
MANAGED=(false false false false false false false false)

mkdir -p "${LOG_DIR}" "${PID_DIR}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${START_LOG}"; }
report() { local level="$1"; shift; log "${level}: $*"; printf '[%s] %s\n' "${level}" "$*"; }
fail() { report ERROR "$*"; printf '[ERROR] 详细日志：%s\n' "${START_LOG}" >&2; exit 1; }

process_is_running() {
  local pid_file="$1" expected="$2" pid
  [[ -r "${pid_file}" ]] || return 1
  read -r pid <"${pid_file}"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  ps -p "${pid}" -o args= 2>/dev/null | grep -Fq -- "${expected}"
}

ros_node_exists() { rosnode list 2>/dev/null | grep -Fxq -- "$1"; }

ros_executable_exists() {
  local package="$1" executable="$2" prefix
  local old_ifs="${IFS}"
  IFS=':'
  for prefix in ${CMAKE_PREFIX_PATH:-}; do
    [[ -x "${prefix}/lib/${package}/${executable}" ]] && { IFS="${old_ifs}"; return 0; }
  done
  IFS="${old_ifs}"
  return 1
}

wait_for_master() {
  local attempt
  for attempt in $(seq 1 20); do rosparam list >/dev/null 2>&1 && return 0 || sleep 1; done
  return 1
}

wait_for_node() {
  local node="$1" attempt
  for attempt in $(seq 1 30); do ros_node_exists "${node}" && return 0 || sleep 1; done
  return 1
}

wait_for_topic() {
  local topic="$1" attempt
  for attempt in $(seq 1 30); do rostopic type "${topic}" >/dev/null 2>&1 && return 0 || sleep 1; done
  return 1
}

wait_for_message() {
  local topic="$1" timeout_seconds="${2:-30}"
  timeout "${timeout_seconds}" rostopic echo -n 1 "${topic}" >/dev/null 2>&1
}

stop_managed_processes() {
  local index pid_file pid attempt
  set +e
  report INFO "正在停止 Scout Mini 端侧进程..."
  for ((index=${#LAUNCH_NAMES[@]} - 1; index>=0; index--)); do
    [[ "${MANAGED[index]}" == true ]] || continue
    ros_node_exists "${NODE_NAMES[index]}" && rosnode kill "${NODE_NAMES[index]}" >/dev/null 2>&1
    pid_file="${PID_DIR}/${LAUNCH_NAMES[index]}.pid"
    if [[ -r "${pid_file}" ]]; then
      read -r pid <"${pid_file}"
      kill "${pid}" 2>/dev/null
      for attempt in $(seq 1 20); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.25; done
      kill -KILL "${pid}" 2>/dev/null || true
      rm -f "${pid_file}"
    fi
  done
  if [[ "${ROSCORE_MANAGED:-false}" == true && -r "${PID_DIR}/roscore.pid" ]]; then
    read -r pid <"${PID_DIR}/roscore.pid"
    kill "${pid}" 2>/dev/null || true
    rm -f "${PID_DIR}/roscore.pid"
  fi
  report OK "Scout Mini 端侧进程已清理"
}

shutdown_all() {
  local exit_code="${1:-0}"
  [[ "${SHUTDOWN_STARTED}" == true ]] && return
  SHUTDOWN_STARTED=true
  trap - INT TERM EXIT
  stop_managed_processes
  exit "${exit_code}"
}
trap 'shutdown_all 130' INT
trap 'shutdown_all 143' TERM
trap 'shutdown_all $?' EXIT

[[ -r /opt/ros/noetic/setup.bash ]] || fail "未找到 ROS Noetic 环境"
[[ -r "${REALSENSE_SETUP}" ]] || fail "未找到 RealSense 工作空间：${REALSENSE_SETUP}"
[[ -r "${LIVOX_SETUP}" ]] || fail "未找到 livox_fastlio 工作空间：${LIVOX_SETUP}"
[[ -r "${NAVIGATION_SETUP}" ]] || fail "未找到 Scout navigation 工作空间：${NAVIGATION_SETUP}"
[[ -r "${WORKSPACE}/devel/setup.bash" ]] || fail "工作空间尚未编译：${WORKSPACE}"
for config_name in device.yaml epgeneral_mqtav.yaml udp_telemetry.yaml video.yaml map_stream.yaml relocalization.yaml task_control.yaml; do
  [[ -r "${PROFILE_CONFIG_DIR}/${config_name}" ]] || fail "缺少 Scout profile 配置：${PROFILE_CONFIG_DIR}/${config_name}"
done
ip link show can0 2>/dev/null | grep -Eq 'state UP|<[^>]*UP' || fail "can0 未处于 UP 状态"

source /opt/ros/noetic/setup.bash
source "${REALSENSE_SETUP}"
source "${NAVIGATION_SETUP}" --extend
source "${LIVOX_SETUP}" --extend
source "${WORKSPACE}/devel/setup.bash" --extend
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"
ros_executable_exists scout_base scout_base_node || fail "当前 ROS overlay 中缺少已编译的 scout_base_node"
ros_executable_exists livox_ros_driver2 livox_ros_driver2_node || fail "当前 ROS overlay 中缺少已编译的 livox_ros_driver2_node"

report INFO "启动 Scout Mini profile，工作空间=${WORKSPACE}，地面站=${GROUND_STATION_IP}"
for attempt in $(seq 1 30); do
  synchronized="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  server_name="$(timedatectl show-timesync -p ServerName --value 2>/dev/null || true)"
  [[ "${synchronized}" == yes && "${server_name}" == "${NTP_SERVER}" ]] && break
  [[ "${attempt}" == 30 ]] && fail "无法确认与 ${NTP_SERVER} 的时间同步"
  sleep 1
done
report OK "时间已与 ${NTP_SERVER} 同步"

if ! rosparam list >/dev/null 2>&1; then
  roscore >"${LOG_DIR}/roscore.log" 2>&1 </dev/null &
  printf '%s\n' "$!" >"${PID_DIR}/roscore.pid"
  ROSCORE_MANAGED=true
  wait_for_master || fail "ROS Master 未能就绪"
else
  ROSCORE_MANAGED=false
fi

start_launch() {
  local index="$1" node="$2"; shift 2
  local name="${LAUNCH_NAMES[index]}" pid_file="${PID_DIR}/${LAUNCH_NAMES[index]}.pid" log_file="${LOG_DIR}/${LAUNCH_NAMES[index]}.log"
  if ros_node_exists "${node}"; then report OK "${name} 已运行（${node}）"; return 0; fi
  roslaunch "$@" >"${log_file}" 2>&1 </dev/null &
  printf '%s\n' "$!" >"${pid_file}"
  MANAGED[index]=true
  wait_for_node "${node}" || fail "${name} 启动失败，日志：${log_file}"
  report OK "${name} 已启动（节点 ${node}）"
}

start_launch 0 /scout_base_node scout_system_bringup scout_livox_base.launch
start_launch 1 /camera/realsense2_camera scout_system_bringup D435I.launch
wait_for_topic /scout_status || fail "未发现 /scout_status"
wait_for_topic /BMS_status || fail "未发现 /BMS_status"
wait_for_topic /camera/color/image_raw || fail "未发现 /camera/color/image_raw"
wait_for_message /scout_status || fail "/scout_status 30 秒内无数据"
wait_for_message /BMS_status || fail "/BMS_status 30 秒内无数据，请检查 Scout SDK BMS 状态"
wait_for_message /livox/lidar || fail "/livox/lidar 30 秒内无数据，请检查 Mid-360"
wait_for_message /livox/imu || fail "/livox/imu 30 秒内无数据，请检查 Mid-360 IMU"
wait_for_message /camera/color/image_raw || fail "/camera/color/image_raw 30 秒内无数据"
start_launch 2 /epgeneral_mqtav epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="${PROFILE_CONFIG_DIR}/epgeneral_mqtav.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 3 /epgeneral_udp_telemetry epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  telemetry_config_file:="${PROFILE_CONFIG_DIR}/udp_telemetry.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  destination_host:="${GROUND_STATION_IP}" link_status_topic:="/ugv/UGV_001/link/udp_tx" diagnostics_topic:="/ugv/UGV_001/diagnostics"
start_launch 4 /epgeneral_video_srt epgeneral_video_srt epgeneral_video_srt.launch \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" video_config_file:="${PROFILE_CONFIG_DIR}/video.yaml"
start_launch 5 /epgeneral_map_stream epgeneral_map_stream epgeneral_map_stream.launch \
  mapping_config_file:="${PROFILE_CONFIG_DIR}/map_stream.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 6 /epgeneral_relocalization epgeneral_relocalization epgeneral_relocalization.launch \
  config_file:="${PROFILE_CONFIG_DIR}/relocalization.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 7 /epgeneral_task_control epgeneral_task_control scout_task_control.launch \
  task_config_file:="${PROFILE_CONFIG_DIR}/task_control.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
wait_for_node /scout_navigation_task_adapter || fail "Scout 任务导航适配器启动失败"
report OK "Scout Mini 常驻栈和任务控制功能包已启动；按 Ctrl+C 停止"
while true; do
  sleep 2
  for index in "${!NODE_NAMES[@]}"; do
    if [[ "${MANAGED[index]}" == true ]] && ! ros_node_exists "${NODE_NAMES[index]}"; then
      fail "${LAUNCH_NAMES[index]} 节点异常退出：${NODE_NAMES[index]}"
    fi
  done
done
