#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'start_fast_lio: %s\n' "$*" >&2
  exit 1
}

source_setup() {
  local setup_file="$1"
  [[ -r "${setup_file}" ]] || fail "setup file is not readable: ${setup_file}"
  # shellcheck disable=SC1090
  set +u
  source "${setup_file}"
  set -u
}

validate_extrinsics() {
  local extrinsics_file="$1"
  [[ -r "${extrinsics_file}" ]] \
    || fail "extrinsics file is not readable: ${extrinsics_file}"
  command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable"
  python3 - "${extrinsics_file}" <<'PY' \
    || fail "extrinsics YAML is invalid or incomplete: ${extrinsics_file}"
import math
import sys

import yaml

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    data = yaml.safe_load(stream)
if not isinstance(data, dict):
    raise ValueError("root must be a mapping")

required_frames = (
    "odom", "robot_init", "base_footprint", "base_link", "lidar_link", "camera_link")
frames = data.get("frames")
if not isinstance(frames, dict) or any(not frames.get(key) for key in required_frames):
    raise ValueError("frames are incomplete")
lio_frames = data.get("lio_frames")
if not isinstance(lio_frames, dict) or any(not lio_frames.get(key) for key in ("world", "body")):
    raise ValueError("lio_frames are incomplete")

for parent, child in (
        ("mid360_mount", "base_link_to_lidar_link"),
        ("d435i_mount", "base_link_to_camera_link")):
    section = data.get(parent)
    transform = section.get(child) if isinstance(section, dict) else None
    if not isinstance(transform, dict):
        raise ValueError("%s.%s is missing" % (parent, child))
    for key in ("x", "y", "z", "roll_deg", "pitch_deg", "yaw_deg"):
        value = transform.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s.%s.%s is not numeric" % (parent, child, key))
        if not math.isfinite(float(value)):
            raise ValueError("%s.%s.%s is not finite" % (parent, child, key))
PY
}

check_launch() {
  local package_name="$1"
  local launch_file="$2"
  shift 2
  command -v rospack >/dev/null 2>&1 || fail "rospack is unavailable"
  command -v roslaunch >/dev/null 2>&1 || fail "roslaunch is unavailable"
  rospack find "${package_name}" >/dev/null 2>&1 \
    || fail "ROS package is unavailable: ${package_name}"
  roslaunch --files "${package_name}" "${launch_file}" "$@" >/dev/null 2>&1 \
    || fail "launch file or arguments are unavailable: ${package_name}/${launch_file}"
}

terminate_children() {
  local pid
  for pid in "${CHILD_PIDS[@]:-}"; do
    kill -INT "${pid}" 2>/dev/null || true
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    wait "${pid}" 2>/dev/null || true
  done
}

request_shutdown() {
  SHUTTING_DOWN=true
  exit 0
}

child_exit_is_expected() {
  [[ "${SHUTTING_DOWN}" == true || -e "${pid_file}.stopping" ]]
}

supervise() {
  [[ "$#" -ge 10 ]] || fail "internal supervisor arguments are incomplete"
  local prerequisite_setup="$1"
  local extrinsics_file="$2"
  local prerequisite_timeout="$3"
  local fast_setup="$4"
  local prerequisite_package="$5"
  local prerequisite_launch="$6"
  local fast_package="$7"
  local fast_launch="$8"
  local pid_file="$9"
  local log_file="${10}"
  shift 10

  CHILD_PIDS=()
  SHUTTING_DOWN=false
  trap 'terminate_children; rm -f -- "${pid_file}.ready" "${pid_file}.stopping"' EXIT
  trap 'request_shutdown' INT TERM HUP

  source_setup "${prerequisite_setup}"
  source_setup "${fast_setup}"

  printf 'stage=fast_lio action=start launch=%s/%s\n' "${fast_package}" "${fast_launch}"
  roslaunch "${fast_package}" "${fast_launch}" "$@" &
  local fast_pid=$!
  CHILD_PIDS+=("${fast_pid}")

  local deadline
  deadline=$(awk -v now="$(date +%s)" -v timeout="${prerequisite_timeout}" \
    'BEGIN { print now + timeout }')
  while true; do
    kill -0 "${fast_pid}" 2>/dev/null \
      || fail "FAST_LIO exited during startup; see ${log_file}"
    if rosnode list 2>/dev/null | grep -Fxq /laserMapping; then
      break
    fi
    if awk -v now="$(date +%s)" -v limit="${deadline}" \
        'BEGIN { exit !(now >= limit) }'; then
      fail "FAST_LIO did not become ready within ${prerequisite_timeout}s"
    fi
    sleep 0.2
  done
  printf 'stage=fast_lio action=ready node=/laserMapping\n'

  printf 'stage=prerequisites action=start launch=%s/%s extrinsics=%s\n' \
    "${prerequisite_package}" "${prerequisite_launch}" "${extrinsics_file}"
  roslaunch "${prerequisite_package}" "${prerequisite_launch}" \
    "extrinsics_file:=${extrinsics_file}" &
  local prerequisite_pid=$!
  CHILD_PIDS+=("${prerequisite_pid}")

  deadline=$(awk -v now="$(date +%s)" -v timeout="${prerequisite_timeout}" \
    'BEGIN { print now + timeout }')
  local expected_nodes=(
    /go2_tf_manager /go2_pose_adapter /cloud_to_base /cloud_world_to_odom
    /go2_map_accumulator)
  while true; do
    kill -0 "${prerequisite_pid}" 2>/dev/null \
      || fail "coordinate prerequisite launch exited during startup; see ${log_file}"
    local nodes
    nodes=$(rosnode list 2>/dev/null || true)
    local ready=true
    local node
    for node in "${expected_nodes[@]}"; do
      if ! grep -Fxq "${node}" <<<"${nodes}"; then
        ready=false
        break
      fi
    done
    if [[ "${ready}" == true ]]; then
      break
    fi
    if awk -v now="$(date +%s)" -v limit="${deadline}" \
        'BEGIN { exit !(now >= limit) }'; then
      fail "coordinate prerequisites did not become ready within ${prerequisite_timeout}s"
    fi
    sleep 0.2
  done
  printf 'stage=prerequisites action=ready nodes=%s\n' "${expected_nodes[*]}"

  local ready_tmp="${pid_file}.ready.tmp.$$"
  printf 'ready\n' >"${ready_tmp}"
  mv -f "${ready_tmp}" "${pid_file}.ready"
  printf 'stage=mapping_stack action=ready supervisor_pid=%s\n' "$$"

  while true; do
    if ! kill -0 "${prerequisite_pid}" 2>/dev/null; then
      child_exit_is_expected && exit 0
      fail "coordinate prerequisite launch exited unexpectedly"
    fi
    if ! kill -0 "${fast_pid}" 2>/dev/null; then
      child_exit_is_expected && exit 0
      fail "FAST_LIO exited unexpectedly"
    fi
    sleep 0.5
  done
}

if [[ "${1:-}" == "--supervise" ]]; then
  shift
  supervise "$@"
  exit 0
fi

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 8 ]] || fail \
    "usage: $0 --check PREREQUISITE_SETUP EXTRINSICS FAST_SETUP PREREQUISITE_PACKAGE PREREQUISITE_LAUNCH FAST_PACKAGE FAST_LAUNCH"
  validate_extrinsics "$3"
  source_setup "$2"
  source_setup "$4"
  check_launch "$5" "$6" "extrinsics_file:=$3"
  check_launch "$7" "$8"
  exit 0
fi

[[ "$#" -ge 10 ]] || fail \
  "usage: $0 PREREQUISITE_SETUP EXTRINSICS PREREQUISITE_TIMEOUT FAST_SETUP PREREQUISITE_PACKAGE PREREQUISITE_LAUNCH FAST_PACKAGE FAST_LAUNCH PID_FILE LOG_FILE [ROSLAUNCH_ARGS...]"
PREREQUISITE_SETUP="$1"
EXTRINSICS_FILE="$2"
PREREQUISITE_TIMEOUT="$3"
FAST_SETUP="$4"
PREREQUISITE_PACKAGE="$5"
PREREQUISITE_LAUNCH="$6"
FAST_PACKAGE="$7"
FAST_LAUNCH="$8"
PID_FILE="$9"
LOG_FILE="${10}"
shift 10

[[ "${PREREQUISITE_TIMEOUT}" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || fail "prerequisite timeout is invalid"
validate_extrinsics "${EXTRINSICS_FILE}"
source_setup "${PREREQUISITE_SETUP}"
source_setup "${FAST_SETUP}"
check_launch "${PREREQUISITE_PACKAGE}" "${PREREQUISITE_LAUNCH}" \
  "extrinsics_file:=${EXTRINSICS_FILE}"
check_launch "${FAST_PACKAGE}" "${FAST_LAUNCH}" "$@"
[[ -n "${PID_FILE}" && -n "${LOG_FILE}" ]] || fail "PID and log paths must not be empty"
mkdir -p "$(dirname "${PID_FILE}")" "$(dirname "${LOG_FILE}")"

if [[ -r "${PID_FILE}" ]]; then
  read -r OLD_PID <"${PID_FILE}" || true
  if [[ "${OLD_PID:-}" =~ ^[0-9]+$ ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    fail "mapping stack is already running with PID ${OLD_PID}"
  fi
  rm -f "${PID_FILE}"
fi

rm -f -- "${PID_FILE}.ready" "${PID_FILE}.stopping"

setsid "$0" --supervise \
  "${PREREQUISITE_SETUP}" "${EXTRINSICS_FILE}" "${PREREQUISITE_TIMEOUT}" \
  "${FAST_SETUP}" "${PREREQUISITE_PACKAGE}" "${PREREQUISITE_LAUNCH}" \
  "${FAST_PACKAGE}" "${FAST_LAUNCH}" "${PID_FILE}" "${LOG_FILE}" \
  "$@" >>"${LOG_FILE}" 2>&1 &
SUPERVISOR_PID=$!
TEMP_PID="${PID_FILE}.tmp.$$"
printf '%s\n' "${SUPERVISOR_PID}" >"${TEMP_PID}"
mv -f "${TEMP_PID}" "${PID_FILE}"

START_DEADLINE=$(awk -v now="$(date +%s)" -v timeout="${PREREQUISITE_TIMEOUT}" \
  'BEGIN { print now + (timeout * 2) + 5 }')
while [[ ! -r "${PID_FILE}.ready" ]]; do
  if ! kill -0 "${SUPERVISOR_PID}" 2>/dev/null; then
    rm -f "${PID_FILE}"
    fail "mapping stack exited during startup; see ${LOG_FILE}"
  fi
  if awk -v now="$(date +%s)" -v limit="${START_DEADLINE}" \
      'BEGIN { exit !(now >= limit) }'; then
    kill -TERM -- "-${SUPERVISOR_PID}" 2>/dev/null || true
    rm -f "${PID_FILE}" "${PID_FILE}.ready"
    fail "mapping stack startup timed out; see ${LOG_FILE}"
  fi
  sleep 0.2
done
printf 'mapping stack started with PID %s\n' "${SUPERVISOR_PID}"
