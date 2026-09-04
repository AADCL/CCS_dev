#!/usr/bin/env bash
# Run from an extracted, reviewed incremental bundle on the AGV.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WS=/home/bitcq/ccs_edge_ws
CAR=/home/bitcq/catkin_ws/src/car_bringup
USER_UNIT=/home/bitcq/.config/systemd/user/ccs-edge-dev.service
source /opt/ros/noetic/setup.bash
source /home/bitcq/catkin_ws/devel/setup.bash --extend
source "${WS}/devel/setup.bash" --extend
[[ "$(id -un)" == bitcq ]] || { echo "run as bitcq" >&2; exit 1; }
if rosnode list 2>/dev/null | grep -Eq '^/(fast_lio_node|ground_air_map_recorder|ground_air_map_manager|ground_air_global_relocalizer)$'; then
  echo "active mapping/relocalization: deployment refused" >&2; exit 1
fi
printf '%s  %s\n' \
  fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326 "${CAR}/launch/manual_mapping.launch" \
  62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52 "${CAR}/launch/save_mapping.launch" | sha256sum -c -
verify_known_version() {
  local path="$1" actual expected
  shift
  [[ -e "${path}" ]] || {
    echo "missing deployment target: ${path}" >&2
    exit 1
  }
  actual="$(sha256sum "${path}" | awk '{print $1}')"
  for expected in "$@"; do
    [[ "${actual}" == "${expected}" ]] && return 0
  done
  echo "unknown local version: ${path} (${actual})" >&2
  exit 1
}
verify_known_version "${WS}/start_ccs_edge_dev.sh" \
  84f41b241e7a9bb60ef8f5e665b36425e8438adcc33375c841a5e3c9474eec95 \
  f608cf5edf99c68442af23e94a18342c6b4768b97c28bfe0e8cbfbb3201f7601 \
  9a1de3eb2cfa43afce77c43613009d6da299b706841c261d86fcf0bbf6de1c8c \
  7ab979eac7eb7a7a6c59c81492b47c3dc9750aa142943d9f76ccf501862fd74d \
  e763d39a5b026d66d9c2ba63754603842cdb8c40f85aa164aa6d2ed37d1a3631
verify_known_version "${USER_UNIT}" \
  78f938e51fb56254c8f4fb774d732e3d936c5c2abf672852034227fb7a782272 \
  25ef74acf3d7a4c0e558b54984f665e49b09d04c0d703493f33d73b9a4435b9f
verify_known_version "${WS}/config/ground_air_agv/map_stream.yaml" \
  9f407bea824e0b4f07a06b859a6f7c8504a94b4f8348d4c95cbd67157cf3a6b5 \
  1c849d10636c17cee5eefc5d2fb6163e158f2415d18e89f73ecadb665d4975f2 \
  2d8f3a2e154e293030847c96ba87c6cb34975e73d09d128793fa5503a1bd7e77
verify_known_version "${CAR}/scripts/ground_air_stage_manager_node.py" \
  c4982816e6e925a9400dd3e5ef8c87d67dde5f7ef63cc77a77377d5251bc3c3d \
  f04e11e3d2314f5f273a0435dd6f1a6211ba66133791318ed25379c0372ca976 \
  c68a28f3ccc5e7e0cdddc42217727c77c3d2d0871e25b38ad964869882f36f14 \
  b325e6824d80c9da75e7839cbc63aca1f746d05f03493a13db600e029e9bd7de
verify_known_version "${CAR}/scripts/system_stage_runtime.py" \
  54be3b247b476de552aa6419bd88582973a53530a5bf370883a03049ea3535ce \
  9cb93b358b01dc08db13d1b059657f57ea2e6d92adae6eef149acaf9544e2594 \
  ed5c3908ddc71af9402e7672aa3f50827cc06df819311828ef338c6cf602b873
verify_known_version "${CAR}/scripts/system_stage_core.py" \
  7739f3a558e5ac53d705cea9d008ce233c89f3c9b8f791e797ea1c3040e56d2f \
  b835947d675fa55cdd005f24843fdf0604909d8ae38b020cc40b0084cff4a815 \
  2ef294c534e1e29f8b7a94411b3a98fa2f62cc48dcf0f13fd78374c64357b397 \
  755120930dcaabcde2a2e5e502463eefa5ecef8566ada8df739993de02024b95
verify_known_version "${CAR}/launch/manual_mapping_control.launch" \
  627eae854fd0c5ef4b177bc4728bde9e613db34b22df9e32d456318ca188970b \
  c307e964689ccbb0d62539a328244d489a2704e52afd5453c3a83526f211ca4d \
  0a00597b087b2941b0f708957e3301ce35033a0354ebb43d7efb89d5a4afcc64
verify_known_version "${CAR}/launch/mapping_coordinate_transforms.launch" \
  0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553 \
  1d90403b5a34931ddc54cd84d2aa9c4f8744145ed02d8c2c315b6aa9676344e5
if [[ -e "${CAR}/launch/relocalization_control.launch" ]]; then
  verify_known_version "${CAR}/launch/relocalization_control.launch" \
    4574f03bcd88c6f26638a6323c6b413e490c3b65461a6947529119506dfe09f6
fi
verify_known_version "${WS}/src/EPGeneral_map_stream/scripts/ground_air_mapping_stack.sh" \
  fb1dca36aeabdfaff96467717bd64a6fbc69ca6226f8004569aed9ccac003eac
verify_known_version "${WS}/src/EPGeneral_map_stream/scripts/ground_air_stage_client.py" \
  5959aa686f793c528e088ee68f662dd3a4faa71c44a3ef47752d21bc9188b9dd \
  ba88a5de26ff31c9f05849071764029e71455ca052817c9e4897b8b854bc9f57
BACKUP="/home/bitcq/.deployment_backups/$(date -u +%Y%m%dT%H%M%SZ)_stage_manager_ccs"
mkdir -p "${BACKUP}/files"
rosnode list >"${BACKUP}/nodes.before" 2>/dev/null || true
systemctl --user is-enabled ccs-edge-dev.service >"${BACKUP}/service.enabled.before" 2>&1 || true
systemctl --user is-active ccs-edge-dev.service >"${BACKUP}/service.active.before" 2>&1 || true
sha256sum "${CAR}/launch/manual_mapping.launch" "${CAR}/launch/save_mapping.launch" >"${BACKUP}/original-launch.sha256"
declare -a SOURCES=() TARGETS=() MODES=()
add() { SOURCES+=("$1"); TARGETS+=("$2"); MODES+=("$3"); }
add edge_side_pkg/deploy/ground_air_agv/start_ccs_edge_dev.sh "${WS}/start_ccs_edge_dev.sh" 755
add edge_side_pkg/deploy/ground_air_agv/ccs-edge-dev.service "${USER_UNIT}" 644
add edge_side_pkg/deploy/ground_air_agv/config/map_stream.yaml "${WS}/config/ground_air_agv/map_stream.yaml" 644
for name in manual_mapping_control.launch mapping_coordinate_transforms.launch relocalization_control.launch; do
  add "edge_side_pkg/deploy/ground_air_agv/launch/${name}" "${CAR}/launch/${name}" 644
done
for name in ground_air_stage_manager_node.py system_stage_runtime.py system_stage_core.py; do
  add "edge_side_pkg/deploy/ground_air_agv/car_bringup_scripts/${name}" "${CAR}/scripts/${name}" 755
done
for name in test_system_stage_core.py test_ground_air_stage_manager.py test_stage_manager_contract.py; do
  add "edge_side_pkg/deploy/ground_air_agv/car_bringup_tests/${name}" "${CAR}/tests/${name}" 644
done
for name in CMakeLists.txt src/epgeneral_map_stream/config.py test/test_ground_air_backend.py test/test_ground_air_stage_client.py; do
  add "edge_side_pkg/EPGeneral_map_stream/${name}" "${WS}/src/EPGeneral_map_stream/${name}" 644
done
for name in ground_air_mapping_stack.sh ground_air_stage_client.py; do
  add "edge_side_pkg/EPGeneral_map_stream/scripts/${name}" "${WS}/src/EPGeneral_map_stream/scripts/${name}" 755
done
for name in GROUND_AIR_AGV_DEPLOYMENT.md GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md; do
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
  cmp -s "${ROOT}/${SOURCES[index]}" "${TARGETS[index]}" || {
    echo "installed file mismatch: ${TARGETS[index]}" >&2
    exit 1
  }
  sha256sum "${TARGETS[index]}" >>"${BACKUP}/after.sha256"
done
bash -n "${WS}/start_ccs_edge_dev.sh"
bash -n "${WS}/src/EPGeneral_map_stream/scripts/ground_air_mapping_stack.sh"
python3 -c 'import sys, xml.etree.ElementTree as ET; [ET.parse(path) for path in sys.argv[1:]]' \
  "${CAR}/launch/manual_mapping_control.launch" \
  "${CAR}/launch/mapping_coordinate_transforms.launch" \
  "${CAR}/launch/relocalization_control.launch"
for control_launch in manual_mapping_control.launch relocalization_control.launch; do
  expanded_nodes="$(roslaunch --nodes "${CAR}/launch/${control_launch}" map_id:=deployment_preflight)"
  if grep -Eq '^/(odom_camera_init_broadcaster|base_link_body_broadcaster)$' <<<"${expanded_nodes}"; then
    echo "on-demand launch duplicates startup TF: ${control_launch}" >&2
    exit 1
  fi
done
python3 -m py_compile "${CAR}/scripts/ground_air_stage_manager_node.py" "${CAR}/scripts/system_stage_runtime.py" "${CAR}/scripts/system_stage_core.py" "${WS}/src/EPGeneral_map_stream/scripts/ground_air_stage_client.py"
systemd-analyze --user verify "${USER_UNIT}"
cd "${WS}/src/EPGeneral_map_stream/test"
PYTHONPATH="${WS}/src/EPGeneral_map_stream/src:${PYTHONPATH:-}" python3 -m unittest \
  test_artifacts test_ground_air_backend test_ground_air_stage_client test_ground_contract test_node test_processing test_protocol test_scripts test_udp_integration test_version_and_entrypoint
cd "${WS}"
catkin_make --pkg epgeneral_map_stream -DCMAKE_BUILD_TYPE=Release -j1
cd "${CAR}/tests"
python3 -m unittest test_system_stage_core test_ground_air_stage_manager test_stage_manager_contract
sha256sum -c "${BACKUP}/original-launch.sha256"
systemctl --user daemon-reload
systemctl --user enable ccs-edge-dev.service
systemctl --user restart ccs-edge-dev.service

READY=false
for attempt in $(seq 1 90); do
  nodes="$(rosnode list 2>/dev/null || true)"
  required_ready=true
  for node in /mavros /livox_lidar_publisher2 /epgeneral_mqtav /epgeneral_udp_telemetry /epgeneral_map_stream /ground_air_stage_manager /odom_camera_init_broadcaster /base_link_body_broadcaster; do
    if ! grep -qx "${node}" <<<"${nodes}"; then
      required_ready=false
      break
    fi
  done
  guard="$(rosparam get /ground_air_stage_manager/ccs_session_guard_version 2>/dev/null || true)"
  external_tf="$(rosparam get /ground_air_stage_manager/external_tf_required 2>/dev/null || true)"
  stage_service="$(rosservice type /ground_air/system/set_stage 2>/dev/null || true)"
  restart_count="$(systemctl --user show ccs-edge-dev.service -p NRestarts --value 2>/dev/null || true)"
  if ${required_ready} &&
     systemctl --user is-active --quiet ccs-edge-dev.service &&
     [[ "${guard}" == 1 && "${external_tf}" == 1 && "${stage_service}" == ground_air_msgs/SetSystemStage && "${restart_count}" == 0 ]] &&
     ! rosparam get /ground_air_stage_manager/resident_tf_version >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 2
done
if [[ "${READY}" != true ]]; then
  echo "ccs-edge-dev did not become ready after deployment" >&2
  systemctl --user status ccs-edge-dev.service --no-pager >&2 || true
  journalctl --user -u ccs-edge-dev.service -n 80 --no-pager >&2 || true
  tail -n 100 /home/bitcq/.ros/ccs_edge_dev_ground_air_agv/log/startup.log >&2 || true
  exit 1
fi
echo "Deployment ready; run static mapping acceptance. BACKUP=${BACKUP}"
