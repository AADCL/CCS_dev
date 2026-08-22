#!/usr/bin/env bash

set -eo pipefail

WORKSPACE="${CCS_EDGE_WORKSPACE:-/home/nvidia/ccs_edge_ws}"
GROUND_STATION_IP="${CCS_GROUND_STATION_IP:-192.168.50.101}"
NTP_SERVER="${CCS_NTP_SERVER:-${GROUND_STATION_IP}}"
ROS_IP_VALUE="${CCS_ROS_IP:-192.168.50.100}"
GO2_NAV_SETUP="${CCS_GO2_NAV_SETUP:-/home/nvidia/go2_mid360_nav/catkin_ws/devel/setup.bash}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-}"
if [[ -z "${PROFILE_CONFIG_DIR}" ]]; then
  if [[ -r "${SCRIPT_DIR}/config/device.yaml" ]]; then
    PROFILE_CONFIG_DIR="${SCRIPT_DIR}/config"
  else
    PROFILE_CONFIG_DIR="${WORKSPACE}/config/go2_edu"
  fi
fi
STATE_DIR="${CCS_EDGE_STATE_DIR:-${HOME}/.ros/ccs_edge_dev}"
LOG_DIR="${STATE_DIR}/log"
PID_DIR="${STATE_DIR}/run"
START_LOG="${LOG_DIR}/startup.log"
SHUTDOWN_STARTED=false

LAUNCH_NAMES=(livox_driver go2_bridge mqtav udp_telemetry video_srt map_stream)
NODE_NAMES=(/livox_lidar_publisher2 /epqrd_go2_bridge /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_video_srt /epgeneral_map_stream)
EXPECTED_COMMANDS=(livox_ros_driver2 epqrd_go2_bridge epgeneral_mqtav epgeneral_udp_telemetry epgeneral_video_srt epgeneral_map_stream)
NODE_EXECUTABLES=(
  "/home/nvidia/go2_mid360_nav/catkin_ws/devel/lib/livox_ros_driver2/livox_ros_driver2_node"
  "${WORKSPACE}/devel/lib/epqrd_go2_bridge/epqrd_go2_bridge_node"
  "${WORKSPACE}/devel/lib/epgeneral_mqtav/epgeneral_mqtav_node.py"
  "${WORKSPACE}/devel/lib/epgeneral_udp_telemetry/epgeneral_udp_telemetry_node.py"
  "${WORKSPACE}/devel/lib/epgeneral_video_srt/epgeneral_video_srt_node"
  "${WORKSPACE}/devel/lib/epgeneral_map_stream/epgeneral_map_stream_node.py"
)
NODE_PROCESS_PIDS=("" "" "" "" "" "")

mkdir -p "${LOG_DIR}" "${PID_DIR}"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${START_LOG}"
}

report() {
  local level="$1"
  shift
  log "${level}: $*"
  printf '[%s] %s\n' "${level}" "$*"
}

fail() {
  report "ERROR" "$*"
  printf '[ERROR] 详细日志：%s\n' "${START_LOG}" >&2
  exit 1
}

[[ -r /opt/ros/noetic/setup.bash ]] || fail "未找到 ROS Noetic 环境"
[[ -r "${GO2_NAV_SETUP}" ]] || fail "未找到 Go2 MID360 工作空间：${GO2_NAV_SETUP}"
[[ -r "${WORKSPACE}/devel/setup.bash" ]] || fail "工作空间尚未编译：${WORKSPACE}"
for config_name in device.yaml go2.yaml epgeneral_mqtav.yaml udp_telemetry.yaml; do
  [[ -r "${PROFILE_CONFIG_DIR}/${config_name}" ]] \
    || fail "缺少 Go2 profile 配置：${PROFILE_CONFIG_DIR}/${config_name}"
done

source /opt/ros/noetic/setup.bash
source "${GO2_NAV_SETUP}"
source "${WORKSPACE}/devel/setup.bash" --extend

export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"
export CMAKE_PREFIX_PATH="/opt/unitree_robotics:${CMAKE_PREFIX_PATH:-}"

process_is_running() {
  local pid_file="$1"
  local expected="$2"
  local pid

  [[ -r "${pid_file}" ]] || return 1
  read -r pid <"${pid_file}"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  ps -p "${pid}" -o args= 2>/dev/null | grep -Fq -- "${expected}"
}

ros_node_exists() {
  local node_name="$1"
  rosnode list 2>/dev/null | grep -Fxq -- "${node_name}"
}

wait_for_master() {
  local attempt
  for attempt in $(seq 1 15); do
    rosparam list >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

wait_for_node() {
  local node_name="$1"
  local attempt
  for attempt in $(seq 1 15); do
    ros_node_exists "${node_name}" && return 0
    sleep 1
  done
  return 1
}

stop_managed_processes() {
  local index
  local name
  local node_name
  local expected
  local pid_file
  local pid
  local node_pid
  local attempt

  set +e
  report "INFO" "正在停止端侧功能包..."
  for ((index=${#LAUNCH_NAMES[@]} - 1; index >= 0; index--)); do
    name="${LAUNCH_NAMES[index]}"
    node_name="${NODE_NAMES[index]}"
    expected="${EXPECTED_COMMANDS[index]}"
    pid_file="${PID_DIR}/${name}.pid"
    NODE_PROCESS_PIDS[index]="$(pgrep -f "(^| )${NODE_EXECUTABLES[index]}( |$)" | tr '\n' ' ')"
    ros_node_exists "${node_name}" && rosnode kill "${node_name}" >/dev/null 2>&1
    if process_is_running "${pid_file}" "${expected}"; then
      read -r pid <"${pid_file}"
      kill "${pid}" 2>/dev/null
      report "INFO" "已请求停止 ${name}（PID ${pid}）"
    fi
  done

  for attempt in $(seq 1 20); do
    local any_running=false
    for ((index=0; index<${#LAUNCH_NAMES[@]}; index++)); do
      process_is_running "${PID_DIR}/${LAUNCH_NAMES[index]}.pid" "${EXPECTED_COMMANDS[index]}" \
        && any_running=true
    done
    [[ "${any_running}" == "false" ]] && break
    sleep 0.25
  done

  for ((index=0; index<${#NODE_PROCESS_PIDS[@]}; index++)); do
    for node_pid in ${NODE_PROCESS_PIDS[index]}; do
      if [[ "${node_pid}" =~ ^[0-9]+$ ]] && kill -0 "${node_pid}" 2>/dev/null; then
        kill "${node_pid}" 2>/dev/null
        report "WARN" "节点未及时退出，直接停止 ${LAUNCH_NAMES[index]}（PID ${node_pid}）"
      fi
    done
  done
  sleep 1

  for ((index=0; index<${#NODE_PROCESS_PIDS[@]}; index++)); do
    for node_pid in ${NODE_PROCESS_PIDS[index]}; do
      if [[ "${node_pid}" =~ ^[0-9]+$ ]] && kill -0 "${node_pid}" 2>/dev/null; then
        kill -KILL "${node_pid}" 2>/dev/null
        report "WARN" "强制停止残留节点 ${LAUNCH_NAMES[index]}（PID ${node_pid}）"
      fi
    done
  done

  for ((index=0; index<${#LAUNCH_NAMES[@]}; index++)); do
    pid_file="${PID_DIR}/${LAUNCH_NAMES[index]}.pid"
    if process_is_running "${pid_file}" "${EXPECTED_COMMANDS[index]}"; then
      read -r pid <"${pid_file}"
      kill -KILL "${pid}" 2>/dev/null
      report "WARN" "强制停止 ${LAUNCH_NAMES[index]}（PID ${pid}）"
    fi
    rm -f "${pid_file}"
  done

  pid_file="${PID_DIR}/roscore.pid"
  if process_is_running "${pid_file}" "roscore"; then
    read -r pid <"${pid_file}"
    kill "${pid}" 2>/dev/null
    report "INFO" "已停止由脚本启动的 ROS Master（PID ${pid}）"
  fi
  rm -f "${pid_file}"
  report "OK" "全部端侧功能包进程已结束"
}

shutdown_all() {
  local exit_code="${1:-0}"
  if [[ "${SHUTDOWN_STARTED}" == "true" ]]; then
    return
  fi
  SHUTDOWN_STARTED=true
  trap - INT TERM EXIT
  printf '\n'
  stop_managed_processes
  exit "${exit_code}"
}

trap 'shutdown_all 130' INT
trap 'shutdown_all 143' TERM
trap 'shutdown_all $?' EXIT

sync_time() {
  local attempt
  local synchronized
  local server_name

  report "INFO" "正在通过 ${NTP_SERVER} 检查时间同步..."
  command -v timedatectl >/dev/null 2>&1 || fail "未安装 timedatectl"
  command -v systemctl >/dev/null 2>&1 || fail "未安装 systemctl"
  for attempt in $(seq 1 30); do
    synchronized="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
    server_name="$(timedatectl show-timesync -p ServerName --value 2>/dev/null || true)"
    if systemctl is-active --quiet systemd-timesyncd \
      && [[ "${synchronized}" == "yes" ]] \
      && [[ "${server_name}" == "${NTP_SERVER}" ]]; then
      report "OK" "时间已与 ${NTP_SERVER} 同步"
      return 0
    fi
    sleep 1
  done
  fail "30 秒内无法确认与 ${NTP_SERVER} 的时间同步，功能包未启动"
}

start_roscore() {
  local pid_file="${PID_DIR}/roscore.pid"
  local log_file="${LOG_DIR}/roscore.log"

  if rosparam list >/dev/null 2>&1; then
    report "OK" "ROS Master 已运行"
    return 0
  fi

  roscore >"${log_file}" 2>&1 </dev/null &
  printf '%s\n' "$!" >"${pid_file}"
  wait_for_master || fail "ROS Master 未能就绪"
  report "OK" "ROS Master 已启动（PID $(<"${pid_file}")）"
}

start_launch() {
  local name="$1"
  local node_name="$2"
  shift 2
  local pid_file="${PID_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"
  local stale_pid
  local attempt

  if ros_node_exists "${node_name}"; then
    report "OK" "${name} 已运行（${node_name}）"
    return 0
  fi

  if process_is_running "${pid_file}" "$1"; then
    read -r stale_pid <"${pid_file}"
    report "WARN" "清理 ${name} 陈旧进程（PID ${stale_pid}）"
    kill "${stale_pid}" 2>/dev/null || true
    for attempt in $(seq 1 20); do
      kill -0 "${stale_pid}" 2>/dev/null || break
      sleep 0.25
    done
    kill -0 "${stale_pid}" 2>/dev/null \
      && fail "${name} 陈旧进程无法停止（PID ${stale_pid}）"
  fi

  roslaunch "$@" >"${log_file}" 2>&1 </dev/null &
  printf '%s\n' "$!" >"${pid_file}"
  if ! wait_for_node "${node_name}"; then
    kill "$(<"${pid_file}")" 2>/dev/null || true
    fail "${name} 启动失败，日志：${log_file}"
  fi
  report "OK" "${name} 已启动（PID $(<"${pid_file}")，节点 ${node_name}）"
}

monitor_nodes() {
  local index
  while true; do
    sleep 2
    for ((index=0; index<${#NODE_NAMES[@]}; index++)); do
      ros_node_exists "${NODE_NAMES[index]}" \
        || fail "${LAUNCH_NAMES[index]} 节点异常退出：${NODE_NAMES[index]}"
    done
  done
}

report "INFO" "启动 ccs_edge_dev，工作空间 ${WORKSPACE}"
report "INFO" "Go2 profile=${PROFILE_CONFIG_DIR}，地面站=${GROUND_STATION_IP}"
sync_time
start_roscore
start_launch livox_driver /livox_lidar_publisher2 \
  livox_ros_driver2 msg_MID360.launch
start_launch go2_bridge /epqrd_go2_bridge \
  epqrd_go2_bridge epqrd_go2_bridge.launch \
  config_file:="${PROFILE_CONFIG_DIR}/go2.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch mqtav /epgeneral_mqtav \
  epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="${PROFILE_CONFIG_DIR}/epgeneral_mqtav.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch udp_telemetry /epgeneral_udp_telemetry \
  epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  telemetry_config_file:="${PROFILE_CONFIG_DIR}/udp_telemetry.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  destination_host:="${GROUND_STATION_IP}" \
  link_status_topic:="/qrd/QRD_001/link/udp_tx" \
  diagnostics_topic:="/qrd/QRD_001/diagnostics"
start_launch video_srt /epgeneral_video_srt \
  epgeneral_video_srt epgeneral_realsense_d435i_srt.launch \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch map_stream /epgeneral_map_stream \
  epgeneral_map_stream epgeneral_map_stream.launch \
  mapping_config_file:="$(rospack find epgeneral_map_stream)/config/mapping.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
report "OK" "全部功能包启动完成；按 Ctrl+C 停止全部进程"
monitor_nodes
