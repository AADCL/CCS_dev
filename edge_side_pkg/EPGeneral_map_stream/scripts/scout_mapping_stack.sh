#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'scout_mapping_stack: %s\n' "$*" >&2; exit 1; }

check_launch() {
  local package_name="$1" launch_file="$2"
  command -v roslaunch >/dev/null 2>&1 || fail "roslaunch is unavailable"
  command -v rosnode >/dev/null 2>&1 || fail "rosnode is unavailable"
  roslaunch --files "${package_name}" "${launch_file}" >/dev/null 2>&1 \
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
  (
    trap - INT TERM
    exec roslaunch "${package_name}" "${launch_file}"
  ) >>"${log_file}" 2>&1 &
  REPLY=$!
}

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 7 ]] || fail "usage: $0 --check FAST_PACKAGE FAST_LAUNCH TF_PACKAGE TF_LAUNCH POSE_PACKAGE POSE_LAUNCH"
  check_launch "$2" "$3"
  check_launch "$4" "$5"
  check_launch "$6" "$7"
  exit 0
fi

if [[ "${1:-}" == "--supervise" ]]; then
  [[ "$#" -eq 11 ]] || fail "invalid supervisor arguments"
  PID_FILE="$2" LOG_FILE="$3" START_TIMEOUT="$4" STOP_TIMEOUT="$5"
  FAST_PACKAGE="$6" FAST_LAUNCH="$7" TF_PACKAGE="$8" TF_LAUNCH="$9"
  POSE_PACKAGE="${10}" POSE_LAUNCH="${11}"
  FAST_PID="" TF_PID="" POSE_PID="" STOPPING=false

  cleanup() {
    [[ "${STOPPING}" == true ]] && return
    STOPPING=true
    stop_child "${POSE_PID}" "${STOP_TIMEOUT}"
    stop_child "${TF_PID}" "${STOP_TIMEOUT}"
    stop_child "${FAST_PID}" "${STOP_TIMEOUT}"
    rm -f -- "${PID_FILE}.ready"
  }
  trap 'cleanup; exit 0' INT TERM
  trap 'cleanup' EXIT

  start_roslaunch "${FAST_PACKAGE}" "${FAST_LAUNCH}" "${LOG_FILE}"
  FAST_PID="${REPLY}"
  wait_node /laserMapping "${START_TIMEOUT}" || fail "FAST-LIO did not become ready"
  printf 'stage=fast_lio action=ready pid=%s\n' "${FAST_PID}" >>"${LOG_FILE}"

  start_roslaunch "${TF_PACKAGE}" "${TF_LAUNCH}" "${LOG_FILE}"
  TF_PID="${REPLY}"
  wait_node /scout_tf_manager "${START_TIMEOUT}" || fail "TF manager did not become ready"
  wait_node /scout_geometry_tf_publisher "${START_TIMEOUT}" || fail "geometry TF publisher did not become ready"
  printf 'stage=tf_manager action=ready pid=%s\n' "${TF_PID}" >>"${LOG_FILE}"

  start_roslaunch "${POSE_PACKAGE}" "${POSE_LAUNCH}" "${LOG_FILE}"
  POSE_PID="${REPLY}"
  wait_node /scout_pose_adapter "${START_TIMEOUT}" || fail "pose adapter did not become ready"
  printf 'stage=pose_adapter action=ready pid=%s\n' "${POSE_PID}" >>"${LOG_FILE}"
  : >"${PID_FILE}.ready"

  while true; do
    for child_pid in "${FAST_PID}" "${TF_PID}" "${POSE_PID}"; do
      kill -0 "${child_pid}" 2>/dev/null || fail "managed mapping process exited unexpectedly"
    done
    sleep 0.5
  done
fi

if [[ "${1:-}" == "--start" ]]; then
  [[ "$#" -eq 11 ]] || fail "usage: $0 --start PID LOG START_TIMEOUT STOP_TIMEOUT FAST_PACKAGE FAST_LAUNCH TF_PACKAGE TF_LAUNCH POSE_PACKAGE POSE_LAUNCH"
  PID_FILE="$2" LOG_FILE="$3" START_TIMEOUT="$4"
  [[ "${START_TIMEOUT}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "startup timeout is invalid"
  if [[ -r "${PID_FILE}" ]]; then
    read -r old_pid <"${PID_FILE}" || true
    kill -0 "${old_pid:-0}" 2>/dev/null && fail "mapping stack is already running"
    rm -f -- "${PID_FILE}" "${PID_FILE}.ready"
  fi
  mkdir -p "$(dirname "${PID_FILE}")" "$(dirname "${LOG_FILE}")"
  : >"${LOG_FILE}"
  (
    trap - INT TERM
    exec setsid "$0" --supervise "${@:2}"
  ) >>"${LOG_FILE}" 2>&1 &
  supervisor_pid=$!
  printf '%s\n' "${supervisor_pid}" >"${PID_FILE}"
  deadline=$(awk -v now="$(date +%s)" -v timeout="${START_TIMEOUT}" 'BEGIN {print now + (timeout * 3) + 5}')
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
    kill -INT "${supervisor_pid}"
    deadline=$(awk -v now="$(date +%s)" -v timeout="${TIMEOUT}" 'BEGIN {print now + (timeout * 3) + 5}')
    while kill -0 "${supervisor_pid}" 2>/dev/null; do
      if awk -v now="$(date +%s)" -v limit="${deadline}" 'BEGIN {exit !(now >= limit)}'; then
        kill -TERM "${supervisor_pid}" 2>/dev/null || true
        break
      fi
      sleep 0.2
    done
  fi
  rm -f -- "${PID_FILE}" "${PID_FILE}.ready"
  printf 'Scout mapping stack stopped\n'
  exit 0
fi

fail "unknown mode"
