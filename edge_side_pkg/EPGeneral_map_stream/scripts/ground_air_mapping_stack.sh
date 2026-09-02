#!/usr/bin/env bash

set -euo pipefail

fail() { printf 'ground_air_mapping_stack: %s\n' "$*" >&2; exit 1; }

source_setup() {
  local setup_file="$1"
  [[ -r "${setup_file}" ]] || fail "setup file is not readable: ${setup_file}"
  set +u
  # shellcheck disable=SC1090
  source "${setup_file}"
  set -u
}

check_launch() {
  local setup_file="$1" package_name="$2" launch_file="$3"
  shift 3
  source_setup "${setup_file}"
  command -v roslaunch >/dev/null 2>&1 || fail "roslaunch is unavailable"
  command -v rospack >/dev/null 2>&1 || fail "rospack is unavailable"
  rospack find "${package_name}" >/dev/null 2>&1 \
    || fail "ROS package is unavailable: ${package_name}"
  roslaunch --files "${package_name}" "${launch_file}" "$@" >/dev/null 2>&1 \
    || fail "launch file or arguments are unavailable: ${package_name}/${launch_file}"
}

terminate_child() {
  if [[ -n "${LAUNCH_PID:-}" ]]; then
    kill -INT "${LAUNCH_PID}" 2>/dev/null || true
    wait "${LAUNCH_PID}" 2>/dev/null || true
  fi
}

supervise() {
  [[ "$#" -ge 8 ]] || fail "internal supervisor arguments are incomplete"
  local setup_file="$1" pid_file="$2" log_file="$3" timeout_seconds="$4"
  local package_name="$5" launch_file="$6" expected_csv="$7"
  shift 7

  LAUNCH_PID=""
  trap 'terminate_child; rm -f -- "${pid_file}.ready" "${pid_file}.stopping"' EXIT
  trap 'exit 0' INT TERM HUP
  source_setup "${setup_file}"

  printf 'stage=ground_air_mapping action=start launch=%s/%s\n' \
    "${package_name}" "${launch_file}"
  roslaunch "${package_name}" "${launch_file}" "$@" &
  LAUNCH_PID=$!

  local deadline nodes ready node
  IFS=',' read -r -a EXPECTED_NODES <<<"${expected_csv}"
  deadline=$(awk -v now="$(date +%s)" -v timeout="${timeout_seconds}" \
    'BEGIN { print now + timeout }')
  while true; do
    kill -0 "${LAUNCH_PID}" 2>/dev/null \
      || fail "mapping launch exited during startup; see ${log_file}"
    nodes=$(rosnode list 2>/dev/null || true)
    ready=true
    for node in "${EXPECTED_NODES[@]}"; do
      if ! grep -Fxq -- "${node}" <<<"${nodes}"; then
        ready=false
        break
      fi
    done
    [[ "${ready}" == true ]] && break
    if awk -v now="$(date +%s)" -v limit="${deadline}" \
        'BEGIN { exit !(now >= limit) }'; then
      fail "mapping nodes did not become ready within ${timeout_seconds}s"
    fi
    sleep 0.2
  done

  printf 'ready\n' >"${pid_file}.ready.tmp.$$"
  mv -f "${pid_file}.ready.tmp.$$" "${pid_file}.ready"
  printf 'stage=ground_air_mapping action=ready nodes=%s\n' "${expected_csv}"
  wait "${LAUNCH_PID}"
}

stop_group() {
  local pid_file="$1" timeout_seconds="$2" mode="$3" pid pgid deadline
  if [[ ! -r "${pid_file}" ]]; then
    printf 'ground-air mapping stack is not running; PID file is absent\n'
    return 0
  fi
  read -r pid <"${pid_file}" || true
  [[ "${pid:-}" =~ ^[0-9]+$ ]] || fail "mapping PID file is invalid"
  if kill -0 "${pid}" 2>/dev/null; then
    pgid=$(ps -o pgid= -p "${pid}" | tr -d '[:space:]')
    [[ "${pgid}" == "${pid}" ]] \
      || fail "mapping PID is not a managed process-group leader"
    : >"${pid_file}.stopping"
    kill -INT -- "-${pid}" 2>/dev/null || kill -INT "${pid}" 2>/dev/null || true
    deadline=$(awk -v now="$(date +%s)" -v timeout="${timeout_seconds}" \
      'BEGIN { print now + timeout }')
    while kill -0 "${pid}" 2>/dev/null; do
      if awk -v now="$(date +%s)" -v limit="${deadline}" \
          'BEGIN { exit !(now >= limit) }'; then
        kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
        break
      fi
      sleep 0.2
    done
  fi
  rm -f -- "${pid_file}" "${pid_file}.ready" "${pid_file}.stopping"
  printf 'ground-air mapping stack %s\n' "${mode}"
}

case "${1:-}" in
  --check)
    [[ "$#" -ge 4 ]] || fail "usage: $0 --check SETUP PACKAGE LAUNCH [ARGS...]"
    shift
    check_launch "$@"
    ;;
  --supervise)
    shift
    supervise "$@"
    ;;
  --start)
    [[ "$#" -ge 9 ]] \
      || fail "usage: $0 --start SETUP PID LOG TIMEOUT PACKAGE LAUNCH EXPECTED_CSV [ARGS...]"
    shift
    SETUP_FILE="$1"; PID_FILE="$2"; LOG_FILE="$3"; TIMEOUT_SECONDS="$4"
    PACKAGE_NAME="$5"; LAUNCH_FILE="$6"; EXPECTED_CSV="$7"
    shift 7
    [[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "timeout is invalid"
    [[ -n "${EXPECTED_CSV}" ]] || fail "expected node list is empty"
    check_launch "${SETUP_FILE}" "${PACKAGE_NAME}" "${LAUNCH_FILE}" "$@"
    mkdir -p "$(dirname "${PID_FILE}")" "$(dirname "${LOG_FILE}")"
    if [[ -r "${PID_FILE}" ]]; then
      read -r OLD_PID <"${PID_FILE}" || true
      if [[ "${OLD_PID:-}" =~ ^[0-9]+$ ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
        fail "mapping stack is already running with PID ${OLD_PID}"
      fi
      rm -f -- "${PID_FILE}"
    fi
    rm -f -- "${PID_FILE}.ready" "${PID_FILE}.stopping"
    setsid "$0" --supervise "${SETUP_FILE}" "${PID_FILE}" "${LOG_FILE}" \
      "${TIMEOUT_SECONDS}" "${PACKAGE_NAME}" "${LAUNCH_FILE}" \
      "${EXPECTED_CSV}" "$@" >>"${LOG_FILE}" 2>&1 &
    SUPERVISOR_PID=$!
    printf '%s\n' "${SUPERVISOR_PID}" >"${PID_FILE}.tmp.$$"
    mv -f "${PID_FILE}.tmp.$$" "${PID_FILE}"
    START_DEADLINE=$(awk -v now="$(date +%s)" -v timeout="${TIMEOUT_SECONDS}" \
      'BEGIN { print now + timeout + 5 }')
    while [[ ! -r "${PID_FILE}.ready" ]]; do
      if ! kill -0 "${SUPERVISOR_PID}" 2>/dev/null; then
        rm -f -- "${PID_FILE}"
        fail "mapping stack exited during startup; see ${LOG_FILE}"
      fi
      if awk -v now="$(date +%s)" -v limit="${START_DEADLINE}" \
          'BEGIN { exit !(now >= limit) }'; then
        kill -TERM -- "-${SUPERVISOR_PID}" 2>/dev/null || true
        rm -f -- "${PID_FILE}" "${PID_FILE}.ready"
        fail "mapping stack startup timed out; see ${LOG_FILE}"
      fi
      sleep 0.2
    done
    printf 'ground-air mapping stack started with PID %s\n' "${SUPERVISOR_PID}"
    ;;
  --stop)
    [[ "$#" -eq 3 ]] || fail "usage: $0 --stop PID TIMEOUT"
    stop_group "$2" "$3" "stopped after successful save"
    ;;
  --abort)
    [[ "$#" -eq 3 ]] || fail "usage: $0 --abort PID TIMEOUT"
    stop_group "$2" "$3" "aborted without artifacts"
    ;;
  *)
    fail "unknown mode"
    ;;
esac
