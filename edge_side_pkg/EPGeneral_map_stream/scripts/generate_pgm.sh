#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'generate_pgm: %s\n' "$*" >&2
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
  [[ "$#" -eq 5 ]] || fail "usage: $0 --check SETUP PACKAGE LAUNCH SOURCE_PCD"
  check_launch "$2" "$3" "$4"
  [[ -s "$5" ]] || fail "source PCD is missing or empty: $5"
  exit 0
fi

[[ "$#" -ge 12 ]] || fail "usage: $0 SETUP PACKAGE LAUNCH PCD PGM YAML LOG TIMEOUT SOURCE_PCD SOURCE_PGM SOURCE_YAML ARCHIVE_ROOT [ROSLAUNCH_ARGS...]"
SETUP_FILE="$1"
PACKAGE_NAME="$2"
LAUNCH_FILE="$3"
PCD_PATH="$4"
PGM_PATH="$5"
YAML_PATH="$6"
LOG_FILE="$7"
TIMEOUT_SECONDS="$8"
SOURCE_PCD_PATH="$9"
SOURCE_PGM_PATH="${10}"
SOURCE_YAML_PATH="${11}"
ARCHIVE_ROOT="${12}"
shift 12

check_launch "${SETUP_FILE}" "${PACKAGE_NAME}" "${LAUNCH_FILE}"
[[ -s "${PCD_PATH}" ]] || fail "input PCD is missing or empty: ${PCD_PATH}"
[[ -s "${SOURCE_PCD_PATH}" ]] || fail "source PCD is missing or empty: ${SOURCE_PCD_PATH}"
[[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "timeout is invalid"
mkdir -p "$(dirname "${PGM_PATH}")" "$(dirname "${YAML_PATH}")" "$(dirname "${LOG_FILE}")"
rm -f "${PGM_PATH}" "${YAML_PATH}"

SESSION_NAME="$(basename "$(dirname "${PCD_PATH}")")"
ARCHIVE_DIR="${ARCHIVE_ROOT}/$(date -u '+%Y%m%dT%H%M%SZ')-${SESSION_NAME}"
mkdir -p "${ARCHIVE_DIR}"
for source_path in "${SOURCE_PCD_PATH}" "${SOURCE_PGM_PATH}" "${SOURCE_YAML_PATH}"; do
  if [[ -e "${source_path}" ]]; then
    cp -a -- "${source_path}" "${ARCHIVE_DIR}/"
  fi
done

restore_previous_outputs() {
  local archived
  for source_path in "${SOURCE_PCD_PATH}" "${SOURCE_PGM_PATH}" "${SOURCE_YAML_PATH}"; do
    archived="${ARCHIVE_DIR}/$(basename "${source_path}")"
    if [[ -e "${archived}" ]]; then
      cp -a -- "${archived}" "${source_path}"
    else
      rm -f -- "${source_path}"
    fi
  done
}

TEMP_SOURCE_PCD="${SOURCE_PCD_PATH}.tmp.$$"
trap 'rm -f "${TEMP_SOURCE_PCD}"' EXIT
cp -- "${PCD_PATH}" "${TEMP_SOURCE_PCD}"
mv -f -- "${TEMP_SOURCE_PCD}" "${SOURCE_PCD_PATH}"
trap - EXIT
rm -f -- "${SOURCE_PGM_PATH}" "${SOURCE_YAML_PATH}"

set +e
timeout --signal=INT --kill-after=5 "${TIMEOUT_SECONDS}" \
  roslaunch "${PACKAGE_NAME}" "${LAUNCH_FILE}" "$@" >>"${LOG_FILE}" 2>&1
STATUS=$?
set -e
if [[ "${STATUS}" -ne 0 ]]; then
  restore_previous_outputs
  fail "PGM generator exited with status ${STATUS}; see ${LOG_FILE}"
fi
if [[ ! -s "${SOURCE_PGM_PATH}" || ! -s "${SOURCE_YAML_PATH}" ]]; then
  restore_previous_outputs
  fail "PGM generator did not produce the configured PGM and YAML"
fi

TEMP_PGM="${PGM_PATH}.tmp.$$"
TEMP_YAML="${YAML_PATH}.tmp.$$"
trap 'rm -f "${TEMP_PGM}" "${TEMP_YAML}"' EXIT
cp -- "${SOURCE_PGM_PATH}" "${TEMP_PGM}"
cp -- "${SOURCE_YAML_PATH}" "${TEMP_YAML}"
mv -f -- "${TEMP_PGM}" "${PGM_PATH}"
mv -f -- "${TEMP_YAML}" "${YAML_PATH}"
trap - EXIT
printf 'PGM and YAML are ready: %s %s\n' "${PGM_PATH}" "${YAML_PATH}"
