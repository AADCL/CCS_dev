#!/usr/bin/env bash
set -euo pipefail
fail() { printf 'ground_air_mapping_stack: %s\n' "$*" >&2; exit 1; }
[[ "$#" -ge 2 ]] || fail "usage: $0 MODE SETUP [ARGS...]"
MODE="$1"; SETUP_FILE="$2"; shift 2
[[ -r "${SETUP_FILE}" ]] || fail "setup file is not readable: ${SETUP_FILE}"
set +u
# shellcheck disable=SC1090
source "${SETUP_FILE}"
set -u
CLIENT="$(dirname "$(readlink -f "$0")")/ground_air_stage_client.py"
case "${MODE}" in
  --check)
    [[ "$#" -ge 2 ]] || fail "usage: $0 --check SETUP PACKAGE LAUNCH [ARGS...]"
    roslaunch --files "$@" >/dev/null 2>&1 || fail "mapping launch is unavailable"
    exec python3 "${CLIENT}" --check
    ;;
  --start)
    [[ "$#" -eq 5 ]] || fail "usage: $0 --start SETUP SESSION LOG TIMEOUT MAP_ID NODES"
    SESSION="$1"; LOG_FILE="$2"; TIMEOUT="$3"; MAP_ID="$4"; NODES="$5"
    mkdir -p "$(dirname "${LOG_FILE}")"
    python3 "${CLIENT}" --start "${SESSION}" "${MAP_ID}" "${TIMEOUT}" "${NODES}" 2>&1 | tee -a "${LOG_FILE}"
    ;;
  --stop|--abort)
    [[ "$#" -eq 3 ]] || fail "usage: $0 MODE SETUP SESSION MAP_ID TIMEOUT"
    exec python3 "${CLIENT}" "${MODE}" "$1" "$2" "$3"
    ;;
  *) fail "unknown mode" ;;
esac
