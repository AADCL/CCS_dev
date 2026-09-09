#!/usr/bin/env bash
# One-time, offline migration. This script never stops nodes or enables motion.
set -euo pipefail

WS=/home/unitree/ccs_edge_ws
PACKAGES=(EPGeneral_device_config EPGeneral_go2_integration EPGeneral_map_stream
  epgeneral_mqtav EPGeneral_relocalization EPGeneral_task_control
  EPGeneral_udp_telemetry EPGeneral_video_srt)
MODE="${1:---check}"
STAGE="${2:-}"

fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
[[ "$(id -un)" == unitree ]] || fail "Run as unitree on Go2 robot 2."
[[ "$(realpath -- "$WS")" == "$WS" && ! -L "$WS" ]] || fail "Unexpected workspace."
[[ -d "$WS/src" && ! -L "$WS/src" ]] || fail "Unexpected source directory."
for directory in config bin vendor scripts docs build devel backups run; do
  [[ ! -e "$WS/$directory" && ! -L "$WS/$directory" ]] && continue
  [[ ! -L "$WS/$directory" && "$(realpath -- "$WS/$directory")" == "$WS/$directory" ]] ||
    fail "Unexpected managed directory: $directory"
done
case "$MODE" in
  --check|--apply|--rollback|--finalize) ;;
  *) fail "Usage: $0 --check|--apply STAGE | --rollback|--finalize BACKUP" ;;
esac
ip -o -4 addr show | awk '{print $4}' | grep -Fxq '192.168.50.111/24' ||
  fail "This is not the Go2 robot 2 LAN address."
if [[ "$MODE" != --rollback ]]; then
  python3 - "$WS/config/go2_robot2/device.yaml" <<'PY'
import sys
import yaml
with open(sys.argv[1]) as stream:
    device = yaml.safe_load(stream)["device"]
if device["id"] != "QRD_002" or device["ip"] != "192.168.50.111":
    raise SystemExit("Unexpected device identity")
PY
fi

check_active_paths() {
  if grep -R -l -E '/ccs_edge_ws/(vendor|bin)/' \
      "$WS/src" "$WS/devel" "$WS/config/go2_robot2" "$WS/scripts" \
      "$WS/start_ccs_edge_dev.sh" /etc/systemd/system/ccs-sntp-sync.service; then
    fail "An active vendor/bin path reference remains."
  fi
}

require_idle() {
  if pgrep -af '[/]opt/ros/noetic/bin/roslaunch|[/]opt/ros/noetic/bin/rosmaster' >/dev/null; then
    fail "Stop the owned CCS/Go2 launch chain before migration; no process is killed automatically."
  fi
  [[ ! -f "$WS/mission/active_execution.json" ]] || fail "Active task state exists."
}

validate_backup() {
  [[ "$STAGE" == "$WS/backups/go2-physical-"* ]] || fail "Backup outside allowed directory."
  [[ "$(realpath -- "$STAGE")" == "$STAGE" && ! -L "$STAGE" ]] || fail "Unexpected backup path."
  (cd "$STAGE" && sha256sum -c before.sha256)
}

if [[ "$MODE" == --rollback ]]; then
  require_idle
  validate_backup
  sudo -n true
  FAILED="$STAGE/failed-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -m 750 "$FAILED"
  for item in src build devel config bin vendor scripts docs start_ccs_edge_dev.sh; do
    [[ ! -e "$WS/$item" && ! -L "$WS/$item" ]] || mv -- "$WS/$item" "$FAILED/"
  done
  tar -xpf "$STAGE/before.tar.gz" -C "$WS"
  sudo -n install -m 644 "$STAGE/ccs-sntp-sync.service" /etc/systemd/system/ccs-sntp-sync.service
  sudo -n systemctl daemon-reload
  printf 'Restored deployment from %s; maps, mission, logs and run were retained.\n' "$STAGE"
  exit 0
fi

if [[ "$MODE" == --finalize ]]; then
  validate_backup
  [[ -f "$STAGE/build.ok" ]] || fail "No successful build record."
  (cd "$WS" && sha256sum -c "$STAGE/payload.sha256" >/dev/null)
  check_active_paths
  for package in "${PACKAGES[@]}"; do
    [[ -d "$WS/src/$package" && ! -L "$WS/src/$package" ]] || fail "Package is not physical: $package"
  done
  for obsolete in "$WS/vendor" "$WS/bin"; do
    [[ ! -e "$obsolete" ]] && continue
    [[ ! -L "$obsolete" && "$(realpath -- "$obsolete")" == "$obsolete" ]] || fail "Unexpected obsolete path."
    case "$obsolete" in
      /home/unitree/ccs_edge_ws/vendor|/home/unitree/ccs_edge_ws/bin) rm -r -- "$obsolete" ;;
      *) fail "Refusing deletion." ;;
    esac
  done
  printf 'Removed active vendor/bin directories; recovery archive: %s/before.tar.gz\n' "$STAGE"
  exit 0
fi

[[ "$STAGE" == "$WS/run/deploy-go2-"* ]] || fail "Stage outside allowed directory."
[[ "$(realpath -- "$STAGE")" == "$STAGE" && ! -L "$STAGE" ]] || fail "Unexpected stage path."
[[ -f "$STAGE/payload.sha256" ]] || fail "Missing payload checksum manifest."
if grep -Ev '^[0-9a-f]{64}  (src/|config/go2_robot2/|scripts/|docs/go2_robot2/|start_ccs_edge_dev.sh$)' "$STAGE/payload.sha256"; then
  fail "Unexpected payload paths."
fi
if grep -E '(^|/)\.\.(/|$)' "$STAGE/payload.sha256"; then fail "Payload traversal."; fi
(cd "$STAGE" && sha256sum -c payload.sha256 >/dev/null)
[[ -z "$(find "$STAGE/src" "$STAGE/config" "$STAGE/scripts" "$STAGE/docs" "$STAGE/start_ccs_edge_dev.sh" -type l -print -quit)" ]] ||
  fail "Payload contains symlinks."
[[ -z "$(comm -3 \
  <(cd "$STAGE" && { find src config scripts docs -type f -print; printf '%s\n' start_ccs_edge_dev.sh; } | LC_ALL=C sort) \
  <(cut -c67- "$STAGE/payload.sha256" | LC_ALL=C sort))" ]] ||
  fail "Payload files do not exactly match the checksum manifest."
for package in "${PACKAGES[@]}"; do
  [[ -f "$STAGE/src/$package/package.xml" ]] || fail "Missing package $package."
  [[ -L "$WS/src/$package" ]] || fail "Expected legacy package symlink: $package."
  EXPECTED="$WS/vendor/CCS_dev/edge_side_pkg/$package"
  [[ "$(realpath -- "$WS/src/$package")" == "$EXPECTED" && ! -L "$EXPECTED" ]] ||
    fail "Unexpected package target: $package."
done
command -v rsync >/dev/null || fail "rsync is required."
[[ "$MODE" == --apply ]] || { printf 'Payload and all eight legacy package targets verified.\n'; exit 0; }

require_idle
sudo -n true
BACKUP="$WS/backups/go2-physical-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 750 "$BACKUP"
ITEMS=(src config bin vendor start_ccs_edge_dev.sh)
for item in build devel scripts docs; do [[ ! -e "$WS/$item" ]] || ITEMS+=("$item"); done
tar -czpf "$BACKUP/before.tar.gz" -C "$WS" "${ITEMS[@]}"
cp -- /etc/systemd/system/ccs-sntp-sync.service "$BACKUP/ccs-sntp-sync.service"
cp -- "$STAGE/payload.sha256" "$BACKUP/payload.sha256"
(cd "$BACKUP" && sha256sum before.tar.gz ccs-sntp-sync.service payload.sha256 > before.sha256 && sha256sum -c before.sha256)
printf 'Backup: %s\n' "$BACKUP"
trap 'printf "Migration failed. Keep services stopped; rollback with: bash %s --rollback %s\n" "$0" "$BACKUP" >&2' ERR

for package in "${PACKAGES[@]}"; do
  unlink -- "$WS/src/$package"
  mv -- "$WS/vendor/CCS_dev/edge_side_pkg/$package" "$WS/src/$package"
  rsync -a --delete -- "$STAGE/src/$package/" "$WS/src/$package/"
done
[[ ! -L "$WS/src/CMakeLists.txt" ]] || unlink -- "$WS/src/CMakeLists.txt"
cp -- /opt/ros/noetic/share/catkin/cmake/toplevel.cmake "$WS/src/CMakeLists.txt"
mkdir -p "$WS/scripts" "$WS/docs/go2_robot2"
rsync -a --delete -- "$STAGE/config/go2_robot2/" "$WS/config/go2_robot2/"
rsync -a -- "$STAGE/scripts/" "$WS/scripts/"
rsync -a -- "$STAGE/docs/go2_robot2/" "$WS/docs/go2_robot2/"
install -m 755 "$STAGE/start_ccs_edge_dev.sh" "$WS/start_ccs_edge_dev.sh"
cp -- "$STAGE/payload.sha256" "$WS/config/checksums.sha256"
sudo -n install -m 644 "$WS/docs/go2_robot2/ccs-sntp-sync.service" /etc/systemd/system/ccs-sntp-sync.service
sudo -n systemctl daemon-reload
for generated in build devel; do
  [[ ! -d "$WS/$generated" ]] || mv -- "$WS/$generated" "$BACKUP/original-$generated"
done
set +u
source /opt/ros/noetic/setup.bash
source /home/unitree/go2_nav_ws/devel/setup.bash
set -u
cd "$WS"
catkin_make -j2 -l2 -DCMAKE_BUILD_TYPE=Release
set +u
source "$WS/devel/setup.bash" --extend
set -u
for package in "${PACKAGES[@]}"; do
  ROS_NAME="$(sed -n 's/.*<name>\(.*\)<\/name>.*/\1/p' "$WS/src/$package/package.xml")"
  [[ "$(rospack find "$ROS_NAME")" == "$WS/src/$package" ]] || fail "Wrong ROS source path: $ROS_NAME"
done
[[ -z "$(find "$WS/src" -type l -print -quit)" ]] || fail "A source symlink remains."
check_active_paths
touch "$BACKUP/build.ok"
printf 'Migration and build succeeded. BACKUP=%s\n' "$BACKUP"
printf 'Run startup --check, perform acceptance, then --finalize this backup.\n'
