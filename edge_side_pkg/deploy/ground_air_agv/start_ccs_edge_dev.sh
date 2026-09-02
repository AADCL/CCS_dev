#!/usr/bin/env bash
set -eo pipefail

WORKSPACE="${CCS_EDGE_WORKSPACE:-/home/bitcq/ccs_edge_ws}"
UNDERLAY_SETUP="${CCS_DEVICE_UNDERLAY_SETUP:-/home/bitcq/catkin_ws/devel/setup.bash}"
ROS_IP_VALUE="${CCS_ROS_IP:-192.168.50.130}"
NTP_SERVER="${CCS_NTP_SERVER:-192.168.50.101}"
FCU_DEVICE="${CCS_FCU_DEVICE:-/dev/serial/by-id/usb-CUAV_PX4_CUAV_Nora_0-if00}"
FCU_BAUD="${CCS_FCU_BAUD:-57600}"
PROFILE_CONFIG_DIR="${CCS_EDGE_PROFILE_CONFIG_DIR:-${WORKSPACE}/config/ground_air_agv}"
LAUNCH_DIR="${CCS_EDGE_LAUNCH_DIR:-${WORKSPACE}/launch}"
PID_DIR="${WORKSPACE}/run"
LOG_DIR="${CCS_EDGE_LOG_DIR:-${HOME}/.ros/ccs_edge_dev_ground_air_agv/log}"
START_LOG="${LOG_DIR}/startup.log"
SHUTDOWN_STARTED=false
ROSCORE_MANAGED=false

LAUNCH_NAMES=(mavros livox mqtav udp_telemetry map_stream a8_camera video_srt mapping_tf)
NODE_NAMES=(/mavros /livox_lidar_publisher2 /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_map_stream /a8_mini_camera /epgeneral_video_srt /odom_camera_init_broadcaster)
OPTIONAL=(false false false false false true true false)
MANAGED=(false false false false false false false false)

mkdir -p "${PID_DIR}" "${LOG_DIR}"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${START_LOG}"; }
report() { local level="$1"; shift; log "${level}: $*"; printf '[%s] %s\n' "${level}" "$*"; }
fail() { report ERROR "$*"; printf '[ERROR] 详细日志：%s\n' "${START_LOG}" >&2; exit 1; }

ros_node_exists() { rosnode list 2>/dev/null | grep -Fxq -- "$1"; }
wait_for_master() { local i; for i in $(seq 1 20); do rosparam list >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
wait_for_node() { local node="$1" i; for i in $(seq 1 30); do ros_node_exists "${node}" && return 0; sleep 1; done; return 1; }
wait_for_topic() { local topic="$1" i; for i in $(seq 1 30); do rostopic type "${topic}" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
wait_for_message() { timeout "${2:-30}" rostopic echo -n 1 "$1" >/dev/null 2>&1; }

stop_launch_process() {
  local index="$1" pid_file pid attempt
  pid_file="${PID_DIR}/${LAUNCH_NAMES[index]}.pid"
  if [[ -r "${pid_file}" ]]; then
    read -r pid <"${pid_file}" || true
    if [[ -n "${pid:-}" ]]; then
      kill "${pid}" 2>/dev/null || true
      for attempt in $(seq 1 20); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.25; done
      kill -KILL "${pid}" 2>/dev/null || true
    fi
    rm -f "${pid_file}"
  fi
  MANAGED[index]=false
}

stop_managed_processes() {
  local index pid
  set +e
  report INFO "正在停止 AGV 端侧进程..."
  for ((index=${#LAUNCH_NAMES[@]} - 1; index>=0; index--)); do
    [[ "${MANAGED[index]}" == true ]] || continue
    stop_launch_process "${index}"
  done
  if [[ "${ROSCORE_MANAGED}" == true && -r "${PID_DIR}/roscore.pid" ]]; then
    read -r pid <"${PID_DIR}/roscore.pid"
    kill "${pid}" 2>/dev/null || true
    rm -f "${PID_DIR}/roscore.pid"
  fi
  report OK "AGV 端侧进程已清理"
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
[[ -r "${UNDERLAY_SETUP}" ]] || fail "未找到设备 underlay：${UNDERLAY_SETUP}"
[[ -r "${WORKSPACE}/devel/setup.bash" ]] || fail "CCS 工作空间尚未编译：${WORKSPACE}"
[[ -e "${FCU_DEVICE}" ]] || fail "未找到 PX4 串口：${FCU_DEVICE}"
for name in device.yaml epgeneral_mqtav.yaml udp_telemetry.yaml video.yaml map_stream.yaml relocalization.yaml task_control.yaml; do
  [[ -r "${PROFILE_CONFIG_DIR}/${name}" ]] || fail "缺少 ground_air_agv profile 配置：${PROFILE_CONFIG_DIR}/${name}"
done
for name in mavros_base.launch livox_mid360_base.launch; do
  [[ -r "${LAUNCH_DIR}/${name}" ]] || fail "缺少基础 launch：${LAUNCH_DIR}/${name}"
done

source /opt/ros/noetic/setup.bash
source "${UNDERLAY_SETUP}" --extend
source "${WORKSPACE}/devel/setup.bash" --extend
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export ROS_IP="${ROS_IP_VALUE}"

livox_path="$(rospack find livox_ros_driver2 2>/dev/null || true)"
[[ -n "${livox_path}" ]] || fail "当前 ROS overlay 中找不到 livox_ros_driver2"
a8_path="$(rospack find a8_mini_camera 2>/dev/null || true)"
[[ -n "${a8_path}" ]] || fail "当前 ROS overlay 中找不到 a8_mini_camera"
car_bringup_path="$(rospack find car_bringup 2>/dev/null || true)"
[[ -r "${car_bringup_path}/launch/mapping_coordinate_transforms.launch" ]] || fail "缺少开机常驻坐标转换 launch"
report INFO "启动 AGV profile：MAVROS、Livox、MQTT、UDP 遥测、A8 Mini 与 SRT；Livox 包=${livox_path}；A8 包=${a8_path}"

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

start_optional_launch() {
  local index="$1" node="$2"; shift 2
  local name="${LAUNCH_NAMES[index]}" pid_file="${PID_DIR}/${LAUNCH_NAMES[index]}.pid" log_file="${LOG_DIR}/${LAUNCH_NAMES[index]}.log"
  if ros_node_exists "${node}"; then report OK "${name} 已运行（${node}）"; return 0; fi
  roslaunch "$@" >"${log_file}" 2>&1 </dev/null &
  printf '%s\n' "$!" >"${pid_file}"
  MANAGED[index]=true
  if ! wait_for_node "${node}"; then
    report WARN "${name} 启动失败，基础服务继续运行；日志：${log_file}"
    stop_launch_process "${index}"
    return 1
  fi
  report OK "${name} 已启动（可降级节点 ${node}）"
}

start_launch 7 /odom_camera_init_broadcaster car_bringup mapping_coordinate_transforms.launch
wait_for_node /base_link_body_broadcaster || fail "开机常驻坐标转换未完整就绪：/base_link_body_broadcaster"

start_launch 0 /mavros "${LAUNCH_DIR}/mavros_base.launch" fcu_url:="${FCU_DEVICE}:${FCU_BAUD}"
for topic in /mavros/state /mavros/imu/data /mavros/battery; do
  wait_for_topic "${topic}" || fail "未发现 ${topic}"
  wait_for_message "${topic}" || fail "${topic} 30 秒内无数据"
done

start_launch 1 /livox_lidar_publisher2 "${LAUNCH_DIR}/livox_mid360_base.launch" msg_frame_id:=base_link
for topic in /livox/lidar /livox/imu; do
  wait_for_topic "${topic}" || fail "未发现 ${topic}"
  wait_for_message "${topic}" || fail "${topic} 30 秒内无数据"
done

start_launch 2 /epgeneral_mqtav epgeneral_mqtav epgeneral_mqtav.launch \
  config_file:="${PROFILE_CONFIG_DIR}/epgeneral_mqtav.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"

start_launch 3 /epgeneral_udp_telemetry epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  telemetry_config_file:="${PROFILE_CONFIG_DIR}/udp_telemetry.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  destination_host:="192.168.50.101" \
  link_status_topic:="/agv/AGV_001/link/udp_tx" \
  diagnostics_topic:="/agv/AGV_001/diagnostics"

start_launch 4 /epgeneral_map_stream epgeneral_map_stream epgeneral_map_stream.launch \
  mapping_config_file:="${PROFILE_CONFIG_DIR}/map_stream.yaml" \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml"

camera_started=false
if start_optional_launch 5 /a8_mini_camera a8_mini_camera a8_mini_camera.launch \
  camera_ip:=192.168.144.25 image_topic:=/a8_cam/image_raw; then
  camera_started=true
fi
if [[ "${camera_started}" == true ]] && wait_for_topic /a8_cam/image_raw && wait_for_message /a8_cam/image_raw 30; then
  report OK "A8 Mini 图像已就绪（/a8_cam/image_raw）"
else
  report WARN "A8 Mini 30 秒内无图像，视频链降级等待恢复；基础服务继续运行"
fi

start_optional_launch 6 /epgeneral_video_srt epgeneral_video_srt epgeneral_video_srt.launch \
  device_config_file:="${PROFILE_CONFIG_DIR}/device.yaml" \
  video_config_file:="${PROFILE_CONFIG_DIR}/video.yaml" || true

report OK "静态 TF、MAVROS、Livox、MQTT、UDP 遥测与建图响应已启动；A8/SRT 按可用性运行；其他端侧功能保持禁用；按 Ctrl+C 停止"
while true; do
  sleep 2
  for index in "${!NODE_NAMES[@]}"; do
    if [[ "${MANAGED[index]}" == true ]] && ! ros_node_exists "${NODE_NAMES[index]}"; then
      if [[ "${OPTIONAL[index]}" == true ]]; then
        report WARN "${LAUNCH_NAMES[index]} 可降级节点异常退出，基础服务继续运行：${NODE_NAMES[index]}"
        stop_launch_process "${index}"
      else
        fail "${LAUNCH_NAMES[index]} 节点异常退出：${NODE_NAMES[index]}"
      fi
    fi
  done
done
