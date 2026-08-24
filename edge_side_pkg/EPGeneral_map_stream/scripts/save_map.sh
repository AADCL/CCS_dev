#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'save_map: %s\n' "$*" >&2
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

validate_service() {
  local service_name="$1"
  [[ "${service_name}" =~ ^/[A-Za-z0-9_/]+$ ]] \
    || fail "service name is invalid: ${service_name}"
}

validate_output() {
  local output_path="$1"
  [[ "${output_path}" == /* ]] || fail "output PCD path must be absolute"
  local output_dir
  output_dir=$(dirname "${output_path}")
  [[ -d "${output_dir}" && -w "${output_dir}" ]] \
    || fail "output PCD directory is not writable: ${output_dir}"
}

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 4 ]] \
    || fail "usage: $0 --check SETUP SERVICE OUTPUT_PCD"
  source_setup "$2"
  command -v rosservice >/dev/null 2>&1 || fail "rosservice is unavailable"
  command -v timeout >/dev/null 2>&1 || fail "timeout is unavailable"
  validate_service "$3"
  validate_output "$4"
  exit 0
fi

[[ "$#" -eq 4 ]] || fail "usage: $0 SETUP SERVICE OUTPUT_PCD TIMEOUT_SECONDS"
SETUP_FILE="$1"
SERVICE_NAME="$2"
OUTPUT_PCD_PATH="$3"
TIMEOUT_SECONDS="$4"

[[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "timeout is invalid"
source_setup "${SETUP_FILE}"
command -v rosservice >/dev/null 2>&1 || fail "rosservice is unavailable"
command -v timeout >/dev/null 2>&1 || fail "timeout is unavailable"
validate_service "${SERVICE_NAME}"
validate_output "${OUTPUT_PCD_PATH}"

printf 'calling map accumulator service=%s output=%s timeout=%ss\n' \
  "${SERVICE_NAME}" "${OUTPUT_PCD_PATH}" "${TIMEOUT_SECONDS}"
set +e
SERVICE_OUTPUT=$(timeout --signal=TERM "${TIMEOUT_SECONDS}" \
  rosservice call "${SERVICE_NAME}" 2>&1)
SERVICE_STATUS=$?
set -e
[[ -z "${SERVICE_OUTPUT}" ]] || printf '%s\n' "${SERVICE_OUTPUT}"
if [[ "${SERVICE_STATUS}" -eq 124 ]]; then
  fail "map accumulator service timed out after ${TIMEOUT_SECONDS}s"
fi
[[ "${SERVICE_STATUS}" -eq 0 ]] \
  || fail "map accumulator service failed with exit code ${SERVICE_STATUS}"
printf 'map accumulator service completed; awaiting freshness validation\n'
