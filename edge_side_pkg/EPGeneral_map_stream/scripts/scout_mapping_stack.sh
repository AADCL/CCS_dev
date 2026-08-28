#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'scout_mapping_stack: %s\n' "$*" >&2; exit 1; }

check_launch() {
  local package_name="$1" launch_file="$2"
  shift 2
  command -v roslaunch >/dev/null 2>&1 || fail "roslaunch is unavailable"
  command -v rosnode >/dev/null 2>&1 || fail "rosnode is unavailable"
  roslaunch --files "${package_name}" "${launch_file}" "$@" >/dev/null 2>&1 \
    || fail "launch is unavailable: ${package_name}/${launch_file}"
}

wait_node() {
  local node_name="$1" timeout_seconds="$2" deadline
  deadline=$(awk -v now="$(date +%s)" -v timeout="${timeout_seconds}" 'BEGIN {print now + timeout}')
  while ! rosnode list 2>/dev/null | grep -Fxq -- "${node_name}"; do
    awk -v now="$(date +%s)" -v limit="${deadline}" 'BEGIN {exit !(now >= limit)}' \
      && return 1
    sleep 0.2
  done
}

child_alive() {
  local child_pid="${1:-}" process_state
  [[ "${child_pid}" =~ ^[0-9]+$ ]] || return 1
  process_state=$(ps -o stat= -p "${child_pid}" 2>/dev/null) || return 1
  [[ "${process_state}" != Z* ]]
}

stop_child() {
  local child_pid="${1:-}" timeout_seconds="$2" deadline
  [[ "${child_pid}" =~ ^[0-9]+$ ]] || return 0
  child_alive "${child_pid}" || { wait "${child_pid}" 2>/dev/null || true; return 0; }
  kill -INT "${child_pid}" 2>/dev/null || true
  deadline=$(awk -v now="$(date +%s)" -v timeout="${timeout_seconds}" 'BEGIN {print now + timeout}')
  while child_alive "${child_pid}"; do
    if awk -v now="$(date +%s)" -v limit="${deadline}" 'BEGIN {exit !(now >= limit)}'; then
      kill -TERM "${child_pid}" 2>/dev/null || true
      break
    fi
    sleep 0.2
  done
  wait "${child_pid}" 2>/dev/null || true
}

start_roslaunch() {
  local package_name="$1" launch_file="$2" log_file="$3"
  shift 3
  (
    trap - INT TERM
    exec roslaunch "${package_name}" "${launch_file}" "$@"
  ) >>"${log_file}" 2>&1 &
  REPLY=$!
}

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 9 || "$#" -eq 14 ]] || fail "invalid check arguments"
  check_launch "$2" "$3" rviz:=false
  check_launch "$4" "$5" map_name:=19700101_000000
  check_launch "$6" "$7"
  check_launch "$8" "$9"
  exit 0
fi

if [[ "${1:-}" == "--supervise" ]]; then
  [[ "$#" -eq 14 || "$#" -eq 19 ]] || fail "invalid supervisor arguments"
  PID_FILE="$2" LOG_FILE="$3" START_TIMEOUT="$4" STOP_TIMEOUT="$5"
  MAP_NAME="$6" FAST_PACKAGE="$7" FAST_LAUNCH="$8"
  MAPPER_PACKAGE="$9" MAPPER_LAUNCH="${10}"
  TF_PACKAGE="${11}" TF_LAUNCH="${12}"
  POSE_PACKAGE="${13}" POSE_LAUNCH="${14}"
  FAST_NODE="${15:-/laserMapping}"
  MAPPER_NODE="${16:-/scout_pointcloud_mapper}"
  TF_NODE="${17:-/scout_tf_manager}"
  GEOMETRY_TF_NODE="${18:-/scout_geometry_tf_publisher}"
  POSE_NODE="${19:-/scout_pose_adapter}"
  FAST_PID="" MAPPER_PID="" TF_PID="" POSE_PID="" STOPPING=false

  cleanup() {
    [[ "${STOPPING}" == true ]] && return
    STOPPING=true
    if [[ -e "${PID_FILE}.stop" ]]; then
      stop_child "${MAPPER_PID}" "${STOP_TIMEOUT}"
      stop_child "${FAST_PID}" "${STOP_TIMEOUT}"
      stop_child "${POSE_PID}" "${STOP_TIMEOUT}"
      stop_child "${TF_PID}" "${STOP_TIMEOUT}"
    else
      stop_child "${POSE_PID}" "${STOP_TIMEOUT}"
      stop_child "${TF_PID}" "${STOP_TIMEOUT}"
      stop_child "${MAPPER_PID}" "${STOP_TIMEOUT}"
      stop_child "${FAST_PID}" "${STOP_TIMEOUT}"
    fi
    rm -f -- "${PID_FILE}.ready" "${PID_FILE}.stop"
  }
  trap 'cleanup; exit 0' INT TERM
  trap 'cleanup' EXIT

  start_roslaunch "${FAST_PACKAGE}" "${FAST_LAUNCH}" "${LOG_FILE}" rviz:=false
  FAST_PID="${REPLY}"
  wait_node "${FAST_NODE}" "${START_TIMEOUT}" || fail "FAST-LIO did not become ready"
  printf 'stage=fast_lio action=ready pid=%s\n' "${FAST_PID}" >>"${LOG_FILE}"

  start_roslaunch "${MAPPER_PACKAGE}" "${MAPPER_LAUNCH}" "${LOG_FILE}" \
    "map_name:=${MAP_NAME}"
  MAPPER_PID="${REPLY}"
  wait_node "${MAPPER_NODE}" "${START_TIMEOUT}" \
    || fail "pointcloud mapper did not become ready"
  printf 'stage=pointcloud_mapper action=ready pid=%s map_name=%s\n' \
    "${MAPPER_PID}" "${MAP_NAME}" >>"${LOG_FILE}"

  start_roslaunch "${TF_PACKAGE}" "${TF_LAUNCH}" "${LOG_FILE}"
  TF_PID="${REPLY}"
  wait_node "${TF_NODE}" "${START_TIMEOUT}" || fail "TF manager did not become ready"
  wait_node "${GEOMETRY_TF_NODE}" "${START_TIMEOUT}" || fail "geometry TF publisher did not become ready"
  printf 'stage=tf_manager action=ready pid=%s\n' "${TF_PID}" >>"${LOG_FILE}"

  start_roslaunch "${POSE_PACKAGE}" "${POSE_LAUNCH}" "${LOG_FILE}"
  POSE_PID="${REPLY}"
  wait_node "${POSE_NODE}" "${START_TIMEOUT}" || fail "pose adapter did not become ready"
  printf 'stage=pose_adapter action=ready pid=%s\n' "${POSE_PID}" >>"${LOG_FILE}"
  : >"${PID_FILE}.ready"

  while true; do
    for child_pid in "${FAST_PID}" "${MAPPER_PID}" "${TF_PID}" "${POSE_PID}"; do
      child_alive "${child_pid}" || fail "managed mapping process exited unexpectedly"
    done
    sleep 0.5
  done
fi

if [[ "${1:-}" == "--start" ]]; then
  [[ "$#" -eq 14 || "$#" -eq 19 ]] || fail "invalid start arguments"
  PID_FILE="$2" LOG_FILE="$3" START_TIMEOUT="$4" MAP_NAME="$6"
  [[ "${START_TIMEOUT}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "startup timeout is invalid"
  [[ "${MAP_NAME}" =~ ^[0-9]{8}_[0-9]{6}$ ]] || fail "map_name must use YYYYMMDD_HHMMSS"
  if [[ -r "${PID_FILE}" ]]; then
    read -r old_pid <"${PID_FILE}" || true
    kill -0 "${old_pid:-0}" 2>/dev/null && fail "mapping stack is already running"
    rm -f -- "${PID_FILE}" "${PID_FILE}.ready" "${PID_FILE}.stop"
  fi
  mkdir -p "$(dirname "${PID_FILE}")" "$(dirname "${LOG_FILE}")"
  : >"${LOG_FILE}"
  (
    trap - INT TERM
    exec setsid "$0" --supervise "${@:2}"
  ) >>"${LOG_FILE}" 2>&1 &
  supervisor_pid=$!
  printf '%s\n' "${supervisor_pid}" >"${PID_FILE}"
  deadline=$(awk -v now="$(date +%s)" -v timeout="${START_TIMEOUT}" 'BEGIN {print now + (timeout * 4) + 5}')
  while [[ ! -r "${PID_FILE}.ready" ]]; do
    if ! kill -0 "${supervisor_pid}" 2>/dev/null; then
      rm -f -- "${PID_FILE}"
      fail "mapping stack exited during startup; see ${LOG_FILE}"
    fi
    if awk -v now="$(date +%s)" -v limit="${deadline}" 'BEGIN {exit !(now >= limit)}'; then
      kill -TERM "${supervisor_pid}" 2>/dev/null || true
      rm -f -- "${PID_FILE}" "${PID_FILE}.ready"
      fail "mapping stack startup timed out; see ${LOG_FILE}"
    fi
    sleep 0.2
  done
  printf 'Scout mapping stack started with PID %s\n' "${supervisor_pid}"
  exit 0
fi

if [[ "${1:-}" == "--stop" || "${1:-}" == "--abort" ]]; then
  [[ "$#" -eq 3 ]] || fail "usage: $0 --stop|--abort PID_FILE TIMEOUT"
  PID_FILE="$2" TIMEOUT="$3"
  [[ -r "${PID_FILE}" ]] || fail "mapping PID file is missing: ${PID_FILE}"
  read -r supervisor_pid <"${PID_FILE}"
  [[ "${supervisor_pid}" =~ ^[0-9]+$ ]] || fail "mapping PID is invalid"
  if kill -0 "${supervisor_pid}" 2>/dev/null; then
    if [[ "$1" == "--stop" ]]; then
      : >"${PID_FILE}.stop"
    else
      rm -f -- "${PID_FILE}.stop"
    fi
    kill -INT "${supervisor_pid}"
    deadline=$(awk -v now="$(date +%s)" -v timeout="${TIMEOUT}" 'BEGIN {print now + (timeout * 4) + 5}')
    while kill -0 "${supervisor_pid}" 2>/dev/null; do
      if awk -v now="$(date +%s)" -v limit="${deadline}" 'BEGIN {exit !(now >= limit)}'; then
        kill -TERM "${supervisor_pid}" 2>/dev/null || true
        break
      fi
      sleep 0.2
    done
  fi
  rm -f -- "${PID_FILE}" "${PID_FILE}.ready" "${PID_FILE}.stop"
  printf 'Scout mapping stack stopped\n'
  exit 0
fi

fail "unknown mode"
