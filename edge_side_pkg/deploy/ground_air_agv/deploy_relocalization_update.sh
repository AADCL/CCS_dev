#!/usr/bin/env bash
# Run from an extracted incremental bundle below /home/bitcq/ccs_edge_ws/.deploy.
set -euo pipefail

WS=/home/bitcq/ccs_edge_ws
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WS_REAL="$(realpath -m "${WS}")"
ROOT_REAL="$(realpath -m "${ROOT}")"
UNDERLAY=/home/bitcq/catkin_ws
PROFILE_DIR="${WS}/config/ground_air_agv"
DEPLOY_TAG="$(date -u +%Y%m%dT%H%M%SZ)_agv_relocalization"
BACKUP="${WS}/.deployment_backups/${DEPLOY_TAG}"
EVIDENCE="${WS}/artifacts/relocalization_acceptance/${DEPLOY_TAG}"
TEMP_ROOT="${WS}/.tmp/${DEPLOY_TAG}"
ROS_HOME="${WS}/run/ros_home"
ROS_LOG_DIR="${WS}/log/ground_air_agv/ros"

inside_workspace() {
  local resolved
  resolved="$(realpath -m "$1")"
  [[ "${resolved}" == "${WS_REAL}" || "${resolved}" == "${WS_REAL}/"* ]]
}

require_workspace_path() {
  inside_workspace "$1" || {
    echo "write target escapes ccs_edge_ws: $1" >&2
    exit 1
  }
}

[[ "$(id -un)" == bitcq ]] || { echo "run as bitcq" >&2; exit 1; }
inside_workspace "${ROOT_REAL}" || { echo "bundle must be extracted below ${WS}/.deploy" >&2; exit 1; }
for path in "${BACKUP}" "${EVIDENCE}" "${TEMP_ROOT}" "${ROS_HOME}" "${ROS_LOG_DIR}"; do
  require_workspace_path "${path}"
done
mkdir -p "${BACKUP}/files" "${EVIDENCE}" "${TEMP_ROOT}" "${ROS_HOME}" "${ROS_LOG_DIR}"
export TMPDIR="${TEMP_ROOT}" TMP="${TEMP_ROOT}" TEMP="${TEMP_ROOT}" ROS_HOME ROS_LOG_DIR

source /opt/ros/noetic/setup.bash
source "${UNDERLAY}/devel/setup.bash" --extend
source "${WS}/devel/setup.bash" --extend

if rosnode list 2>/dev/null | grep -Eq '^/(fast_lio_node|ground_air_map_recorder|ground_air_map_manager|ground_air_global_relocalizer)$|^/ccs_relocalization_stage_'; then
  echo "active mapping/relocalization session: deployment refused" >&2
  exit 1
fi

READONLY_LAUNCHES=("${UNDERLAY}/src/car_bringup/launch/manual_mapping.launch" "${UNDERLAY}/src/car_bringup/launch/save_mapping.launch" "${UNDERLAY}/src/car_bringup/launch/relocalization_system.launch" "${UNDERLAY}/src/car_bringup/launch/mapping_coordinate_transforms.launch")
sha256sum "${READONLY_LAUNCHES[@]}" >"${BACKUP}/underlay-launches.before.sha256"
rosnode list >"${EVIDENCE}/nodes.before" 2>/dev/null || true
rosservice list >"${EVIDENCE}/services.before" 2>/dev/null || true
rosrun tf tf_echo odom camera_init >"${EVIDENCE}/tf-odom-camera_init.before" 2>&1 &
TF_PID=$!
sleep 2
kill "${TF_PID}" 2>/dev/null || true
wait "${TF_PID}" 2>/dev/null || true
systemctl --user status ccs-edge-dev.service --no-pager >"${EVIDENCE}/service.before" 2>&1 || true

declare -a SOURCES=() TARGETS=() MODES=()
add() {
  require_workspace_path "$2"
  SOURCES+=("$1")
  TARGETS+=("$2")
  MODES+=("$3")
}

add edge_side_pkg/deploy/ground_air_agv/start_ccs_edge_dev.sh "${WS}/start_ccs_edge_dev.sh" 755
add edge_side_pkg/deploy/ground_air_agv/config/relocalization.yaml "${PROFILE_DIR}/relocalization.yaml" 644
add edge_side_pkg/deploy/ground_air_agv/overrides/car_bringup/package.xml "${WS}/overrides/car_bringup/package.xml" 644
add edge_side_pkg/deploy/ground_air_agv/overrides/car_bringup/launch/relocalization_system.launch "${WS}/overrides/car_bringup/launch/relocalization_system.launch" 644

for package in EPGeneral_relocalization EPGeneral_ground_air_control; do
  while IFS= read -r source; do
    relative="${source#edge_side_pkg/${package}/}"
    mode=644
    [[ "${relative}" == scripts/*.py ]] && mode=755
    add "${source}" "${WS}/src/${package}/${relative}" "${mode}"
  done < <(cd "${ROOT}" && find "edge_side_pkg/${package}" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' | sort)
done

for document in GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT_LOG.md; do
  add "edge_side_pkg/documents/${document}" "${WS}/documents/${document}" 644
done

for index in "${!TARGETS[@]}"; do
  source_path="${ROOT}/${SOURCES[index]}"
  target="${TARGETS[index]}"
  [[ -r "${source_path}" ]] || { echo "missing bundle source: ${SOURCES[index]}" >&2; exit 1; }
  if [[ -e "${target}" ]]; then
    archive_path="${BACKUP}/files${target}"
    require_workspace_path "${archive_path}"
    mkdir -p "$(dirname "${archive_path}")"
    cp -a "${target}" "${archive_path}"
    sha256sum "${target}" >>"${BACKUP}/before.sha256"
    printf 'existing\t%s\n' "${target}" >>"${BACKUP}/manifest.tsv"
  else
    printf 'new\t%s\n' "${target}" >>"${BACKUP}/manifest.tsv"
  fi
done

for index in "${!TARGETS[@]}"; do
  target="${TARGETS[index]}"
  require_workspace_path "${target}"
  mkdir -p "$(dirname "${target}")"
  install -m "${MODES[index]}" "${ROOT}/${SOURCES[index]}" "${target}"
  cmp -s "${ROOT}/${SOURCES[index]}" "${target}" || { echo "installed file mismatch: ${target}" >&2; exit 1; }
  sha256sum "${target}" >>"${BACKUP}/after.sha256"
done

bash -n "${WS}/start_ccs_edge_dev.sh"
python3 -m py_compile "${WS}/src/EPGeneral_relocalization/src/epgeneral_relocalization/"*.py "${WS}/src/EPGeneral_relocalization/scripts/"*.py "${WS}/src/EPGeneral_ground_air_control/src/epgeneral_ground_air_control/"*.py "${WS}/src/EPGeneral_ground_air_control/scripts/"*.py
python3 -c 'import sys, xml.etree.ElementTree as ET; [ET.parse(path) for path in sys.argv[1:]]' "${WS}/src/EPGeneral_relocalization/launch/epgeneral_relocalization.launch" "${WS}/src/EPGeneral_ground_air_control/launch/relocalization_control.launch" "${WS}/overrides/car_bringup/launch/relocalization_system.launch"

cd "${WS}/src/EPGeneral_relocalization/test"
CCS_GROUND_AIR_PROFILE_CONFIG="${PROFILE_DIR}" PYTHONPATH="${WS}/src/EPGeneral_relocalization/src" python3 -m unittest test_core
cd "${WS}/src/EPGeneral_ground_air_control/test"
PYTHONPATH="${WS}/src/EPGeneral_ground_air_control/src" python3 -m unittest test_control test_launch_contract

cd "${WS}"
catkin_make --force-cmake --pkg epgeneral_relocalization epgeneral_ground_air_control -DCMAKE_BUILD_TYPE=Release -j1
source "${WS}/devel/setup.bash" --extend

[[ "$(rospack find epgeneral_ground_air_control)" == "${WS}/src/EPGeneral_ground_air_control" ]]
[[ "$(rospack find epgeneral_relocalization)" == "${WS}/src/EPGeneral_relocalization" ]]
scoped_ros_package_path="${WS}/overrides:${WS}/src:/opt/ros/noetic/share"
scoped_cmake_prefix_path="${WS}/devel:/opt/ros/noetic"
override_path="$(ROS_PACKAGE_PATH="${scoped_ros_package_path}" CMAKE_PREFIX_PATH="${scoped_cmake_prefix_path}" rospack find car_bringup)"
[[ "${override_path}" == "${WS}/overrides/car_bringup" ]] || { echo "scoped car_bringup override did not resolve" >&2; exit 1; }
override_nodes="$(ROS_PACKAGE_PATH="${scoped_ros_package_path}" CMAKE_PREFIX_PATH="${scoped_cmake_prefix_path}" roslaunch --nodes car_bringup relocalization_system.launch map_id:=deployment_preflight)"
grep -Eq '^/ccs_relocalization_stage_' <<<"${override_nodes}"
control_nodes="$(roslaunch --nodes epgeneral_ground_air_control relocalization_control.launch map_id:=deployment_preflight)"
if grep -Eq '^/(mavros|livox_lidar_publisher2|odom_camera_init_broadcaster|base_link_body_broadcaster)$' <<<"${control_nodes}"; then
  echo "relocalization control launch duplicates resident nodes" >&2
  exit 1
fi

sha256sum -c "${BACKUP}/underlay-launches.before.sha256"
systemctl --user restart ccs-edge-dev.service

READY=false
for attempt in $(seq 1 120); do
  nodes="$(rosnode list 2>/dev/null || true)"
  required_ready=true
  for node in /mavros /livox_lidar_publisher2 /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_map_stream /ground_air_stage_manager /epgeneral_relocalization /odom_camera_init_broadcaster /base_link_body_broadcaster; do
    grep -qx "${node}" <<<"${nodes}" || required_ready=false
  done
  guard="$(rosparam get /ground_air_stage_manager/ccs_session_guard_version 2>/dev/null || true)"
  stage_service="$(rosservice type /ground_air/system/set_stage 2>/dev/null || true)"
  if ${required_ready} && systemctl --user is-active --quiet ccs-edge-dev.service && [[ "${guard}" == 2 ]] && [[ "${stage_service}" == ground_air_msgs/SetSystemStage ]] && ss -lun | grep -Eq '[:.]14565[[:space:]]'; then
    READY=true
    break
  fi
  sleep 2
done
if [[ "${READY}" != true ]]; then
  systemctl --user status ccs-edge-dev.service --no-pager >&2 || true
  tail -n 120 "${WS}/log/ground_air_agv/startup.log" >&2 || true
  exit 1
fi

main_pid="$(systemctl --user show ccs-edge-dev.service -p MainPID --value)"
restart_count="$(systemctl --user show ccs-edge-dev.service -p NRestarts --value)"
sleep 5
[[ "$(systemctl --user show ccs-edge-dev.service -p MainPID --value)" == "${main_pid}" ]]
[[ "$(systemctl --user show ccs-edge-dev.service -p NRestarts --value)" == "${restart_count}" ]]

rosnode list >"${EVIDENCE}/nodes.after"
rosservice list >"${EVIDENCE}/services.after"
systemctl --user status ccs-edge-dev.service --no-pager >"${EVIDENCE}/service.after" 2>&1
sha256sum -c "${BACKUP}/underlay-launches.before.sha256" >"${EVIDENCE}/underlay-launches.after"
printf 'BACKUP=%s\nEVIDENCE=%s\n' "${BACKUP}" "${EVIDENCE}"
