#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'abort_fast_lio: %s\n' "$*" >&2
  exit 1
}

[[ "$#" -eq 2 ]] || fail "usage: $0 PID_FILE TIMEOUT_SECONDS"
PID_FILE="$1"
TIMEOUT_SECONDS="$2"
[[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "timeout is invalid"

if [[ ! -r "${PID_FILE}" ]]; then
  printf 'FAST_LIO is not running; PID file is absent\n'
  exit 0
fi

read -r FAST_LIO_PID <"${PID_FILE}" || true
[[ "${FAST_LIO_PID:-}" =~ ^[0-9]+$ ]] || fail "FAST_LIO PID file is invalid"
if kill -0 "${FAST_LIO_PID}" 2>/dev/null; then
  FAST_LIO_PGID=$(ps -o pgid= -p "${FAST_LIO_PID}" | tr -d '[:space:]')
  [[ "${FAST_LIO_PGID}" == "${FAST_LIO_PID}" ]] \
    || fail "PID is not a managed FAST_LIO process-group leader"
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
  if (( $(date +%s) >= TERM_DEADLINE )); then
    kill -KILL -- "-${FAST_LIO_PID}" 2>/dev/null || kill -KILL "${FAST_LIO_PID}" 2>/dev/null || true
    sleep 0.2
    break
  fi
  sleep 0.2
done
kill -0 "${FAST_LIO_PID}" 2>/dev/null && fail "FAST_LIO did not stop"
rm -f "${PID_FILE}"
printf 'FAST_LIO aborted without generating mapping artifacts\n'
