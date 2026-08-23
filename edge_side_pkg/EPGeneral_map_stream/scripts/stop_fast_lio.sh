#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'stop_fast_lio: %s\n' "$*" >&2
  exit 1
}

[[ "$#" -eq 5 ]] || fail "usage: $0 SETUP PID_FILE SOURCE_PCD TARGET_PCD TIMEOUT_SECONDS"
SETUP_FILE="$1"
PID_FILE="$2"
SOURCE_PCD_PATH="$3"
TARGET_PCD_PATH="$4"
TIMEOUT_SECONDS="$5"

[[ -r "${SETUP_FILE}" ]] || fail "setup file is not readable: ${SETUP_FILE}"
# shellcheck disable=SC1090
set +u
source "${SETUP_FILE}"
set -u
[[ -r "${PID_FILE}" ]] || fail "FAST_LIO PID file is missing: ${PID_FILE}"
read -r FAST_LIO_PID <"${PID_FILE}" || true
[[ "${FAST_LIO_PID:-}" =~ ^[0-9]+$ ]] || fail "FAST_LIO PID file is invalid"
[[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "timeout is invalid"

if kill -0 "${FAST_LIO_PID}" 2>/dev/null; then
  FAST_LIO_PGID=$(ps -o pgid= -p "${FAST_LIO_PID}" | tr -d '[:space:]')
  [[ "${FAST_LIO_PGID}" == "${FAST_LIO_PID}" ]] \
    || fail "PID is not a managed FAST_LIO process-group leader"
  : >"${PID_FILE}.stopping"
  kill -INT -- "-${FAST_LIO_PID}" 2>/dev/null || kill -INT "${FAST_LIO_PID}" 2>/dev/null || true
fi

DEADLINE=$(awk -v now="$(date +%s)" -v timeout="${TIMEOUT_SECONDS}" 'BEGIN { print now + timeout }')
while kill -0 "${FAST_LIO_PID}" 2>/dev/null; do
  if awk -v now="$(date +%s)" -v deadline="${DEADLINE}" 'BEGIN { exit !(now >= deadline) }'; then
    kill -TERM -- "-${FAST_LIO_PID}" 2>/dev/null || kill -TERM "${FAST_LIO_PID}" 2>/dev/null || true
    break
  fi
  sleep 0.2
done

TERM_DEADLINE=$(( $(date +%s) + 5 ))
while kill -0 "${FAST_LIO_PID}" 2>/dev/null; do
  (( $(date +%s) < TERM_DEADLINE )) || fail "FAST_LIO did not stop after SIGINT and SIGTERM"
  sleep 0.2
done
rm -f "${PID_FILE}" "${PID_FILE}.ready" "${PID_FILE}.stopping"

[[ -s "${SOURCE_PCD_PATH}" ]] || fail "source PCD is missing or empty: ${SOURCE_PCD_PATH}"
mkdir -p "$(dirname "${TARGET_PCD_PATH}")"
TEMP_PCD="${TARGET_PCD_PATH}.tmp.$$"
trap 'rm -f "${TEMP_PCD}"' EXIT
cp -- "${SOURCE_PCD_PATH}" "${TEMP_PCD}"
mv -f -- "${TEMP_PCD}" "${TARGET_PCD_PATH}"
trap - EXIT
printf 'FAST_LIO stopped and source PCD was snapshotted: %s\n' "${TARGET_PCD_PATH}"
