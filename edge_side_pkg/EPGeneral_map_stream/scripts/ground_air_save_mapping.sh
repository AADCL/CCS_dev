#!/usr/bin/env bash

set -euo pipefail

fail() { printf 'ground_air_save_mapping: %s\n' "$*" >&2; exit 1; }

source_setup() {
  local setup_file="$1"
  [[ -r "${setup_file}" ]] || fail "setup file is not readable: ${setup_file}"
  set +u
  # shellcheck disable=SC1090
  source "${setup_file}"
  set -u
}

check_integration() {
  local setup_file="$1" package_name="$2" launch_file="$3" map_root="$4"
  source_setup "${setup_file}"
  command -v roslaunch >/dev/null 2>&1 || fail "roslaunch is unavailable"
  command -v rospack >/dev/null 2>&1 || fail "rospack is unavailable"
  command -v timeout >/dev/null 2>&1 || fail "timeout is unavailable"
  rospack find "${package_name}" >/dev/null 2>&1 \
    || fail "ROS package is unavailable: ${package_name}"
  roslaunch --files "${package_name}" "${launch_file}" >/dev/null 2>&1 \
    || fail "save launch is unavailable: ${package_name}/${launch_file}"
  [[ -d "${map_root}" && -w "${map_root}" ]] \
    || fail "map root is unavailable or not writable: ${map_root}"
}

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 5 ]] || fail "usage: $0 --check SETUP PACKAGE LAUNCH MAP_ROOT"
  check_integration "$2" "$3" "$4" "$5"
  exit 0
fi

[[ "$#" -eq 14 ]] || fail \
  "usage: $0 SETUP PACKAGE LAUNCH MAP_ROOT MAP_NAME PCD_NAME PGM_NAME YAML_NAME METADATA_NAME TARGET_PCD TARGET_PGM TARGET_YAML LOG TIMEOUT"
SETUP_FILE="$1"; PACKAGE_NAME="$2"; LAUNCH_FILE="$3"; MAP_ROOT="$4"
MAP_NAME="$5"; PCD_NAME="$6"; PGM_NAME="$7"; YAML_NAME="$8"; METADATA_NAME="$9"
TARGET_PCD="${10}"; TARGET_PGM="${11}"; TARGET_YAML="${12}"
LOG_FILE="${13}"; TIMEOUT_SECONDS="${14}"

[[ "${MAP_NAME}" =~ ^[0-9]{8}_[0-9]{6}$ ]] \
  || fail "map_name must use YYYYMMDD_HHMMSS"
[[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "timeout is invalid"
for name in "${PCD_NAME}" "${PGM_NAME}" "${YAML_NAME}" "${METADATA_NAME}"; do
  [[ -n "${name}" && "${name}" != */* && "${name}" != . && "${name}" != .. ]] \
    || fail "artifact name must be a file name: ${name}"
done

check_integration "${SETUP_FILE}" "${PACKAGE_NAME}" "${LAUNCH_FILE}" "${MAP_ROOT}"
MAP_DIR="${MAP_ROOT}/${MAP_NAME}"
[[ ! -e "${MAP_DIR}" ]] || fail "map destination already exists: ${MAP_DIR}"
mkdir -p "$(dirname "${TARGET_PCD}")" "$(dirname "${TARGET_PGM}")" \
  "$(dirname "${TARGET_YAML}")" "$(dirname "${LOG_FILE}")"

printf 'launching save command: roslaunch %s %s\n' \
  "${PACKAGE_NAME}" "${LAUNCH_FILE}" >>"${LOG_FILE}"
set +e
timeout --signal=INT --kill-after=5 "${TIMEOUT_SECONDS}" \
  roslaunch "${PACKAGE_NAME}" "${LAUNCH_FILE}" >>"${LOG_FILE}" 2>&1
STATUS=$?
set -e
[[ "${STATUS}" -eq 0 ]] \
  || fail "save launch exited with status ${STATUS}; see ${LOG_FILE}"

SOURCE_PCD="${MAP_DIR}/${PCD_NAME}"
SOURCE_PGM="${MAP_DIR}/${PGM_NAME}"
SOURCE_YAML="${MAP_DIR}/${YAML_NAME}"
SOURCE_METADATA="${MAP_DIR}/${METADATA_NAME}"
for path in "${SOURCE_PCD}" "${SOURCE_PGM}" "${SOURCE_YAML}" "${SOURCE_METADATA}"; do
  [[ -s "${path}" ]] || fail "saved artifact is missing or empty: ${path}"
done

python3 - "${SOURCE_METADATA}" "${MAP_NAME}" <<'PY' \
  || fail "saved metadata does not match map_name: ${MAP_NAME}"
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    metadata = json.load(stream)
if metadata.get("map_id") != sys.argv[2]:
    raise ValueError("map_id mismatch")
if metadata.get("point_cloud") != "cloud_map.pcd":
    raise ValueError("point_cloud mismatch")
if metadata.get("occupancy_map") != "map.yaml":
    raise ValueError("occupancy_map mismatch")
if int(metadata.get("point_count", 0)) <= 0:
    raise ValueError("point_count is not positive")
PY

TEMP_PCD="${TARGET_PCD}.tmp.$$"
TEMP_PGM="${TARGET_PGM}.tmp.$$"
TEMP_YAML="${TARGET_YAML}.tmp.$$"
trap 'rm -f -- "${TEMP_PCD}" "${TEMP_PGM}" "${TEMP_YAML}"' EXIT
cp -- "${SOURCE_PCD}" "${TEMP_PCD}"
cp -- "${SOURCE_PGM}" "${TEMP_PGM}"
cp -- "${SOURCE_YAML}" "${TEMP_YAML}"
mv -f -- "${TEMP_PCD}" "${TARGET_PCD}"
mv -f -- "${TEMP_PGM}" "${TARGET_PGM}"
mv -f -- "${TEMP_YAML}" "${TARGET_YAML}"
trap - EXIT
printf 'ground-air map saved and verified: %s\n' "${MAP_DIR}"
