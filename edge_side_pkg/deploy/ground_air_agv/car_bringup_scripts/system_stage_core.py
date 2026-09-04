#!/usr/bin/env python3
"""Pure request validation and command construction for system stages."""

from dataclasses import dataclass
import re


BASE = 0
MAPPING = 1
RELOCALIZATION = 2
DEFAULT_TIMEOUT = 90.0

_VALID_STAGES = (BASE, MAPPING, RELOCALIZATION)
_MAP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_COORDINATE_NODES = {
    "/odom_camera_init_broadcaster",
    "/base_link_body_broadcaster",
}
_EXCLUSIVE_STAGE_NODES = {
    "/fast_lio_node",
    "/ground_air_map_recorder",
    "/ground_air_map_manager",
    "/ground_air_global_relocalizer",
    "/ground_air_world_tf_owner",
}


class StageError(RuntimeError):
    """Raised when a stage request cannot be safely executed."""


@dataclass(frozen=True)
class StageRequest:
    stage: int
    map_id: str
    timeout: float


@dataclass(frozen=True)
class TopologyState:
    conflicts: tuple
    coordinate_transforms_ready: bool


def analyze_active_nodes(active_nodes):
    active = {
        name if str(name).startswith("/") else "/{}".format(name)
        for name in active_nodes
    }
    coordinate_count = len(active.intersection(_COORDINATE_NODES))
    conflicts = sorted(active.intersection(_EXCLUSIVE_STAGE_NODES))
    if coordinate_count == 1:
        conflicts.append("incomplete coordinate transform pair")
    return TopologyState(tuple(conflicts), coordinate_count == 2)


def normalize_request(stage, map_id="", timeout=0.0):
    stage = int(stage)
    if stage not in _VALID_STAGES:
        raise StageError("unsupported system stage: {}".format(stage))

    selected_map = str(map_id).strip()
    if stage in (MAPPING, RELOCALIZATION) and not _MAP_ID_PATTERN.fullmatch(
        selected_map
    ):
        raise StageError(
            "map_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
        )

    selected_timeout = float(timeout)
    if selected_timeout <= 0.0:
        selected_timeout = DEFAULT_TIMEOUT
    return StageRequest(stage, selected_map, selected_timeout)


def build_stage_commands(request):
    if request.stage == BASE:
        return ()

    if request.stage == MAPPING:
        primary = (
            "roslaunch",
            "car_bringup",
            "manual_mapping_control.launch",
            "map_id:={}".format(request.map_id),
        )
    else:
        timeout = str(float(request.timeout))
        primary = (
            "roslaunch",
            "car_bringup",
            "relocalization_control.launch",
            "map_id:={}".format(request.map_id),
            "service_wait_timeout:={}".format(timeout),
            "relocalize_timeout:={}".format(timeout),
        )
    return (primary,)
