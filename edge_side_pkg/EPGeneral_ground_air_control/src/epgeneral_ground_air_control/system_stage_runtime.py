#!/usr/bin/env python3
"""Stage lifecycle logic with an injected ROS/process backend."""

from dataclasses import dataclass

from .system_stage_core import BASE


@dataclass(frozen=True)
class TransitionResult:
    success: bool
    message: str
    active_stage: int


class StageController:
    """Own exactly the child processes started for one active stage."""

    def __init__(self, backend):
        self.backend = backend
        self.active_stage = BASE
        self.active_map_id = ""
        self.active_owner = ""
        self.children = []

    def _stop_children(self):
        for child in reversed(self.children):
            self.backend.stop(child)
        self.children = []
        self.active_stage = BASE
        self.active_map_id = ""
        self.active_owner = ""

    def transition(self, request, commands, owner=""):
        if self.active_stage != BASE and (owner or self.active_owner):
            owns_active = (
                request.map_id == self.active_map_id
                and owner == self.active_owner
            )
            if not owns_active or request.stage not in (BASE, self.active_stage):
                return TransitionResult(
                    False, "CCS session does not own the active stage", self.active_stage
                )
        if (
            request.stage == self.active_stage
            and (request.stage == BASE or request.map_id == self.active_map_id)
        ):
            return TransitionResult(
                True, "requested stage is already active", self.active_stage
            )
        if request.stage == BASE:
            self._stop_children()
            return TransitionResult(True, "base stage active", BASE)

        conflicts = self.backend.find_conflicts(self.active_stage != BASE)
        if conflicts:
            return TransitionResult(
                False,
                "unmanaged stage nodes are active: {}".format(
                    ", ".join(sorted(conflicts))
                ),
                self.active_stage,
            )

        self._stop_children()
        try:
            primary = self.backend.start(commands[0])
            self.children.append(primary)
            if not self.backend.wait_primary(
                request.stage, primary, request.timeout
            ):
                raise RuntimeError("primary stage nodes did not become ready")
            if not self.backend.wait_stage_transforms(
                request.stage, primary, request.timeout
            ):
                raise RuntimeError("required coordinate transforms did not become ready")
        except Exception as error:
            self._stop_children()
            return TransitionResult(
                False, "stage startup failed: {}".format(error), BASE
            )

        self.active_stage = request.stage
        self.active_map_id = request.map_id
        self.active_owner = owner
        return TransitionResult(
            True,
            "stage active; relocalization quality is reported separately"
            if request.stage == 2
            else "stage active",
            self.active_stage,
        )

    def shutdown(self):
        self._stop_children()
