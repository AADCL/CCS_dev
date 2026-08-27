#!/usr/bin/env bash
set -eo pipefail

WORKSPACE="${CCS_EDGE_WORKSPACE:-/home/nrc19/ccs_edge_ws}"
GROUND_STATION_IP="${CCS_GROUND_STATION_IP:-192.168.50.101}"
NTP_SERVER="${CCS_NTP_SERVER:-${GROUND_STATION_IP}}"
ROS_IP_VALUE="${CCS_ROS_IP:-192.168.50.122}"
LIVOX_SETUP="${CCS_LIVOX_SETUP:-/home/nrc19/livox_fastlio/devel/setup.bash}"
PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-${WORKSPACE}/config/wheeltec_r550p}"
STATE_DIR="${CCS_EDGE_STATE_DIR:-${HOME}/.ros/ccs_edge_dev_wheeltec_r550p}"
LOG_DIR="${STATE_DIR}/log"
PID_DIR="${STATE_DIR}/run"
START_LOG="${LOG_DIR}/startup.log"
SHUTDOWN_STARTED=false
ROSCORE_MANAGED=false

LAUNCH_NAMES=(wheeltec_base mqtav udp_telemetry map_stream relocalization task_control)
NODE_NAMES=(/wheeltec_robot /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_map_stream /epgeneral_relocalization /epgeneral_task_control)
EXPECTED_COMMANDS=(wheeltec_livox_base.launch epgeneral_mqtav epgeneral_udp_telemetry epgeneral_map_stream epgeneral_relocalization navigation_task_control.launch)
MANAGED=(false false false false false false)

mkdir -p "${LOG_DIR}" "${PID_DIR}"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${START_LOG}"; }
report() { local level="$1"; shift; log "${level}: $*"; printf '[%s] %s\n' "${level}" "$*"; }
fail() { report ERROR "$*"; printf '[ERROR] 详细日志：%s\n' "${START_LOG}" >&2; exit 1; }

ros_node_exists() { rosnode list 2>/dev/null | grep -Fxq -- "$1"; }
wait_for_master() { local i; for i in $(seq 1 20); do rosparam list >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
wait_for_node() { local node="$1" i; for i in $(seq 1 30); do ros_node_exists "${node}" && return 0; sleep 1; done; return 1; }
wait_for_topic() { local topic="$1" i; for i in $(seq 1 30); do rostopic type "${topic}" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
wait_for_message() { timeout "${2:-30}" rostopic echo -n 1 "$1" >/dev/null 2>&1; }

publish_zero_velocity() {
  if rosparam list >/dev/null 2>&1; then
    timeout 3 rostopic pub -1 /cmd_vel geometry_msgs/Twist \
      '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' \
      >/dev/null 2>&1 || true
  fi
}

stop_managed_processes() {
  local index pid_file pid attempt
  set +e
  report INFO "正在停止 WheelTech R550P 端侧进程..."
  publish_zero_velocity
  for ((index=${#LAUNCH_NAMES[@]} - 1; index>=0; index--)); do
    [[ "${MANAGED[index]}" == true ]] || continue
    ros_node_exists "${NODE_NAMES[index]}" && rosnode kill "${NODE_NAMES[index]}" >/dev/null 2>&1
    pid_file="${PID_DIR}/${LAUNCH_NAMES[index]}.pid"
    if [[ -r "${pid_file}" ]]; then
      read -r pid <"${pid_file}"
      kill "${pid}" 2>/dev/null || true
      for attempt in $(seq 1 20); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.25; done
      kill -KILL "${pid}" 2>/dev/null || true
      rm -f "${pid_file}"
    fi
  done
  if [[ "${ROSCORE_MANAGED}" == true && -r "${PID_DIR}/roscore.pid" ]]; then
    read -r pid <"${PID_DIR}/roscore.pid"
    kill "${pid}" 2>/dev/null || true
    rm -f "${PID_DIR}/roscore.pid"
  fi
  report OK "WheelTech R550P 端侧进程已清理"
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
[[ -r "${LIVOX_SETUP}" ]] || fail "未找到 WheelTech/Livox 工作空间：${LIVOX_SETUP}"
[[ -r "${WORKSPACE}/devel/setup.bash" ]] || fail "CCS 工作空间尚未编译：${WORKSPACE}"
for name in device.yaml epgeneral_mqtav.yaml udp_telemetry.yaml video.yaml map_stream.yaml relocalization.yaml task_control.yaml; do
  [[ -r "${PROFILE_CONFIG_DIR}/${name}" ]] || fail "缺少 WheelTech profile 配置：${PROFILE_CONFIG_DIR}/${name}"
done
[[ -e /dev/wheeltec_controller ]] || fail "缺少 /dev/wheeltec_controller"

source /opt/ros/noetic/setup.bash
source "${LIVOX_SETUP}"
source "${WORKSPACE}/devel/setup.bash" --extend
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"

report INFO "启动 WheelTech R550P profile，工作空间=${WORKSPACE}，地面站=${GROUND_STATION_IP}"
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

start_launch 0 /wheeltec_robot wheeltec_system_bringup wheeltec_livox_base.launch
wait_for_node /livox_lidar_publisher2 || fail "Livox 驱动节点未就绪"
for topic in /odom /imu /PowerVoltage /livox/lidar /livox/imu; do
  wait_for_topic "${topic}" || fail "未发现 ${topic}"
  wait_for_message "${topic}" || fail "${topic} 30 秒内无数据"
done
start_launch 1 /epgeneral_mqtav epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="${PROFILE_CONFIG_DIR}/epgeneral_mqtav.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 2 /epgeneral_udp_telemetry epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  telemetry_config_file:="${PROFILE_CONFIG_DIR}/udp_telemetry.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  destination_host:="${GROUND_STATION_IP}" link_status_topic:="/ugv/UGV_003/link/udp_tx" diagnostics_topic:="/ugv/UGV_003/diagnostics"
start_launch 3 /epgeneral_map_stream epgeneral_map_stream epgeneral_map_stream.launch \
  mapping_config_file:="${PROFILE_CONFIG_DIR}/map_stream.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 4 /epgeneral_relocalization epgeneral_relocalization epgeneral_relocalization.launch \
  config_file:="${PROFILE_CONFIG_DIR}/relocalization.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
start_launch 5 /epgeneral_task_control epgeneral_task_control navigation_task_control.launch \
  task_config_file:="${PROFILE_CONFIG_DIR}/task_control.yaml" device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"
wait_for_node /epgeneral_navigation_task_adapter || fail "任务导航适配器启动失败"
report OK "全部非视频端侧功能包已启动；按 Ctrl+C 安全停止"

while true; do
  sleep 2
  for index in "${!NODE_NAMES[@]}"; do
    if [[ "${MANAGED[index]}" == true ]] && ! ros_node_exists "${NODE_NAMES[index]}"; then
      fail "${LAUNCH_NAMES[index]} 节点异常退出：${NODE_NAMES[index]}"
    fi
  done
done
