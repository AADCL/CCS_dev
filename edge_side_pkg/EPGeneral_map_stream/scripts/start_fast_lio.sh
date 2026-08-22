#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'start_fast_lio: %s\n' "$*" >&2
  exit 1
}

check_launch() {
  local setup_file="$1"
  local package_name="$2"
  local launch_file="$3"
  [[ -r "${setup_file}" ]] || fail "setup file is not readable: ${setup_file}"
  # shellcheck disable=SC1090
  set +u
  source "${setup_file}"
  set -u
  command -v rospack >/dev/null 2>&1 || fail "rospack is unavailable after sourcing ${setup_file}"
  command -v roslaunch >/dev/null 2>&1 || fail "roslaunch is unavailable after sourcing ${setup_file}"
  rospack find "${package_name}" >/dev/null 2>&1 || fail "ROS package is unavailable: ${package_name}"
  roslaunch --files "${package_name}" "${launch_file}" >/dev/null 2>&1 \
    || fail "launch file is unavailable: ${package_name}/${launch_file}"
}

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 5 ]] || fail "usage: $0 --check SETUP PACKAGE LAUNCH GENERATED_PCD"
  check_launch "$2" "$3" "$4"
  [[ -d "$(dirname "$5")" && -w "$(dirname "$5")" ]] \
    || fail "generated PCD directory is not writable: $(dirname "$5")"
  exit 0
fi

[[ "$#" -ge 6 ]] || fail "usage: $0 SETUP PACKAGE LAUNCH PID_FILE LOG_FILE GENERATED_PCD [ROSLAUNCH_ARGS...]"
SETUP_FILE="$1"
PACKAGE_NAME="$2"
LAUNCH_FILE="$3"
PID_FILE="$4"
LOG_FILE="$5"
GENERATED_PCD_PATH="$6"
shift 6

check_launch "${SETUP_FILE}" "${PACKAGE_NAME}" "${LAUNCH_FILE}"
[[ -n "${PID_FILE}" && -n "${LOG_FILE}" ]] || fail "PID and log paths must not be empty"
mkdir -p "$(dirname "${PID_FILE}")" "$(dirname "${LOG_FILE}")"

if [[ -r "${PID_FILE}" ]]; then
  read -r OLD_PID <"${PID_FILE}" || true
  if [[ "${OLD_PID:-}" =~ ^[0-9]+$ ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    fail "FAST_LIO is already running with PID ${OLD_PID}"
  fi
  rm -f "${PID_FILE}"
fi

mkdir -p "$(dirname "${GENERATED_PCD_PATH}")"
rm -f -- "${GENERATED_PCD_PATH}"

setsid roslaunch "${PACKAGE_NAME}" "${LAUNCH_FILE}" "$@" >>"${LOG_FILE}" 2>&1 &
FAST_LIO_PID=$!
TEMP_PID="${PID_FILE}.tmp.$$"
printf '%s\n' "${FAST_LIO_PID}" >"${TEMP_PID}"
mv -f "${TEMP_PID}" "${PID_FILE}"
sleep 1
if ! kill -0 "${FAST_LIO_PID}" 2>/dev/null; then
  rm -f "${PID_FILE}"
  fail "FAST_LIO exited during startup; see ${LOG_FILE}"
fi
printf 'FAST_LIO started with PID %s\n' "${FAST_LIO_PID}"
