#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'scout_finalize_map: %s\n' "$*" >&2; exit 1; }

check_tools() {
  local package_name="$1" executable="$2" source_pcd="$3" map_root="$4" package_dir
  command -v rosrun >/dev/null 2>&1 || fail "rosrun is unavailable"
  command -v rospack >/dev/null 2>&1 || fail "rospack is unavailable"
  package_dir=$(rospack find "${package_name}") || fail "ROS package is unavailable: ${package_name}"
  [[ -x "${package_dir}/scripts/${executable}" || -x "${package_dir}/${executable}" ]] \
    || fail "ROS executable is unavailable: ${package_name}/${executable}"
  [[ -d "$(dirname "${source_pcd}")" ]] || fail "FAST-LIO PCD directory is unavailable"
  [[ -d "${map_root}" && -w "${map_root}" ]] || fail "map root is unavailable or not writable"
}

if [[ "${1:-}" == "--check" ]]; then
  [[ "$#" -eq 5 ]] || fail "usage: $0 --check PACKAGE EXECUTABLE SOURCE_PCD MAP_ROOT"
  check_tools "$2" "$3" "$4" "$5"
  exit 0
fi

[[ "$#" -eq 10 ]] || fail "usage: $0 PACKAGE EXECUTABLE MAP_NAME SOURCE_PCD MAP_ROOT TARGET_PCD TARGET_PGM TARGET_YAML LOG TIMEOUT"
PACKAGE_NAME="$1" EXECUTABLE="$2" MAP_NAME="$3" SOURCE_PCD="$4" MAP_ROOT="$5"
TARGET_PCD="$6" TARGET_PGM="$7" TARGET_YAML="$8" LOG_FILE="$9" TIMEOUT="${10}"
[[ "${MAP_NAME}" =~ ^[0-9]{8}_[0-9]{6}$ ]] || fail "map_name must use YYYYMMDD_HHMMSS"
check_tools "${PACKAGE_NAME}" "${EXECUTABLE}" "${SOURCE_PCD}" "${MAP_ROOT}"
[[ -s "${SOURCE_PCD}" ]] || fail "current FAST-LIO PCD is missing or empty"
MAP_DIR="${MAP_ROOT}/${MAP_NAME}"
[[ ! -e "${MAP_DIR}" ]] || fail "map directory already exists: ${MAP_DIR}"
mkdir -p "$(dirname "${LOG_FILE}")" "$(dirname "${TARGET_PCD}")"
timeout --signal=INT --kill-after=5 "${TIMEOUT}" \
  rosrun "${PACKAGE_NAME}" "${EXECUTABLE}" "${MAP_NAME}" >"${LOG_FILE}" 2>&1 \
  || fail "finalize_map failed; see ${LOG_FILE}"
for path in "${MAP_DIR}/public_map.pcd" "${MAP_DIR}/map.pgm" "${MAP_DIR}/map.yaml"; do
  [[ -s "${path}" ]] || fail "finalized artifact is missing or empty: ${path}"
done
cp -- "${MAP_DIR}/public_map.pcd" "${TARGET_PCD}.tmp"
cp -- "${MAP_DIR}/map.pgm" "${TARGET_PGM}.tmp"
cp -- "${MAP_DIR}/map.yaml" "${TARGET_YAML}.tmp"
mv -f -- "${TARGET_PCD}.tmp" "${TARGET_PCD}"
mv -f -- "${TARGET_PGM}.tmp" "${TARGET_PGM}"
mv -f -- "${TARGET_YAML}.tmp" "${TARGET_YAML}"
printf 'Scout map finalized: %s\n' "${MAP_DIR}"
