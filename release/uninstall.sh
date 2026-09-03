#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [ "${1:-}" != "--yes" ]; then
    read -r -p "Uninstall CCS from $root? config/ and data/ will be kept. [y/N] " answer
    case "$answer" in y|Y) ;; *) exit 0;; esac
fi
"$root/ccs-maintenance" --uninstall --prefix "$root"
