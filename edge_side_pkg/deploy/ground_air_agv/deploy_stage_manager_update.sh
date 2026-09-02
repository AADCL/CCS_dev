#!/usr/bin/env bash
# Run from an extracted, reviewed incremental bundle on the AGV.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WS=/home/bitcq/ccs_edge_ws
CAR=/home/bitcq/catkin_ws/src/car_bringup
source "${WS}/devel/setup.bash"
[[ "$(id -un)" == bitcq ]] || { echo "run as bitcq" >&2; exit 1; }
if rosnode list | grep -Eq '^/(fast_lio_node|ground_air_map_recorder|ground_air_map_manager|ground_air_global_relocalizer)$'; then
  echo "active mapping/relocalization: deployment refused" >&2; exit 1
fi
printf '%s  %s\n' \
  fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326 "${CAR}/launch/manual_mapping.launch" \
  62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52 "${CAR}/launch/save_mapping.launch" | sha256sum -c -
verify_known_version() {
  local path="$1" before="$2" current="$3" actual
  actual="$(sha256sum "${path}" | awk '{print $1}')"
  [[ "${actual}" == "${before}" || "${actual}" == "${current}" ]] || {
    echo "unknown local version: ${path} (${actual})" >&2
    exit 1
  }
}
verify_known_version "${CAR}/scripts/ground_air_stage_manager_node.py" \
  c4982816e6e925a9400dd3e5ef8c87d67dde5f7ef63cc77a77377d5251bc3c3d \
  f04e11e3d2314f5f273a0435dd6f1a6211ba66133791318ed25379c0372ca976
verify_known_version "${CAR}/scripts/system_stage_runtime.py" \
  54be3b247b476de552aa6419bd88582973a53530a5bf370883a03049ea3535ce \
  9cb93b358b01dc08db13d1b059657f57ea2e6d92adae6eef149acaf9544e2594
verify_known_version "${CAR}/scripts/system_stage_core.py" \
  7739f3a558e5ac53d705cea9d008ce233c89f3c9b8f791e797ea1c3040e56d2f \
  b835947d675fa55cdd005f24843fdf0604909d8ae38b020cc40b0084cff4a815
BACKUP="/home/bitcq/.deployment_backups/$(date -u +%Y%m%dT%H%M%SZ)_stage_manager_ccs"
mkdir -p "${BACKUP}/files"
rosnode list >"${BACKUP}/nodes.before"
sha256sum "${CAR}/launch/manual_mapping.launch" "${CAR}/launch/save_mapping.launch" >"${BACKUP}/original-launch.sha256"
declare -a SOURCES=() TARGETS=() MODES=()
add() { SOURCES+=("$1"); TARGETS+=("$2"); MODES+=("$3"); }
add edge_side_pkg/deploy/ground_air_agv/start_ccs_edge_dev.sh "${WS}/start_ccs_edge_dev.sh" 755
for name in ground_air_stage_manager_node.py system_stage_runtime.py system_stage_core.py; do
  add "edge_side_pkg/deploy/ground_air_agv/car_bringup_scripts/${name}" "${CAR}/scripts/${name}" 755
done
for name in test_system_stage_core.py test_ground_air_stage_manager.py; do
  add "edge_side_pkg/deploy/ground_air_agv/car_bringup_tests/${name}" "${CAR}/tests/${name}" 644
done
for name in CMakeLists.txt src/epgeneral_map_stream/config.py test/test_ground_air_backend.py test/test_ground_air_stage_client.py; do
  add "edge_side_pkg/EPGeneral_map_stream/${name}" "${WS}/src/EPGeneral_map_stream/${name}" 644
done
for name in ground_air_mapping_stack.sh ground_air_stage_client.py; do
  add "edge_side_pkg/EPGeneral_map_stream/scripts/${name}" "${WS}/src/EPGeneral_map_stream/scripts/${name}" 755
done
for name in GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md; do
  add "edge_side_pkg/documents/${name}" "${WS}/documents/${name}" 644
done
for index in "${!TARGETS[@]}"; do
  target="${TARGETS[index]}"
  [[ -r "${ROOT}/${SOURCES[index]}" ]] || { echo "missing bundle source" >&2; exit 1; }
  if [[ -e "${target}" ]]; then
    cp -a --parents "${target}" "${BACKUP}/files/"
    sha256sum "${target}" >>"${BACKUP}/before.sha256"
    printf 'existing\t%s\n' "${target}" >>"${BACKUP}/manifest.tsv"
  else
    printf 'new\t%s\n' "${target}" >>"${BACKUP}/manifest.tsv"
  fi
done
echo "BACKUP=${BACKUP}"
for index in "${!TARGETS[@]}"; do
  install -m "${MODES[index]}" "${ROOT}/${SOURCES[index]}" "${TARGETS[index]}"
  sha256sum "${TARGETS[index]}" >>"${BACKUP}/after.sha256"
done
bash -n "${WS}/start_ccs_edge_dev.sh"
bash -n "${WS}/src/EPGeneral_map_stream/scripts/ground_air_mapping_stack.sh"
python3 -m py_compile "${CAR}/scripts/ground_air_stage_manager_node.py" "${CAR}/scripts/system_stage_runtime.py" "${CAR}/scripts/system_stage_core.py" "${WS}/src/EPGeneral_map_stream/scripts/ground_air_stage_client.py"
cd "${WS}/src/EPGeneral_map_stream/test"
PYTHONPATH="${WS}/src/EPGeneral_map_stream/src:${PYTHONPATH:-}" python3 -m unittest \
  test_artifacts test_ground_air_backend test_ground_air_stage_client test_ground_contract test_node test_processing test_protocol test_scripts test_udp_integration test_version_and_entrypoint
cd "${WS}"
catkin_make --pkg epgeneral_map_stream -DCMAKE_BUILD_TYPE=Release -j1
cd "${CAR}/tests"
python3 -m unittest test_system_stage_core test_ground_air_stage_manager test_stage_manager_contract
sha256sum -c "${BACKUP}/original-launch.sha256"
systemctl --user restart ccs-edge-dev.service
echo "Deployment installed; inspect startup completion and run static acceptance. BACKUP=${BACKUP}"
