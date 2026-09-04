import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from epgeneral_ground_air_control.initial_pose_adapter import InitialPoseAdapter  # noqa: E402
from epgeneral_ground_air_control.stage_bridge import RelocalizationStageBridge  # noqa: E402
from epgeneral_ground_air_control.system_stage_core import (  # noqa: E402
    BASE, MAPPING, RELOCALIZATION, build_stage_commands, normalize_request,
)
from epgeneral_ground_air_control.system_stage_runtime import StageController  # noqa: E402


class Response:
    def __init__(self, success=True, message="ok", active_stage=RELOCALIZATION,
                 fitness=0.8, rmse=0.1):
        self.success = success
        self.message = message
        self.active_stage = active_stage
        self.fitness = fitness
        self.rmse = rmse


class Backend:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.conflicts = []

    def find_conflicts(self, _managed):
        return self.conflicts

    def start(self, command):
        child = object()
        self.started.append((command, child))
        return child

    def wait_primary(self, _stage, _child, _timeout):
        return True

    def wait_stage_transforms(self, _stage, _child, _timeout):
        return True

    def stop(self, child):
        self.stopped.append(child)


class StageControllerTests(unittest.TestCase):
    def test_relocalization_uses_workspace_control_package(self):
        request = normalize_request(RELOCALIZATION, "test60", 60)
        command = build_stage_commands(request)[0]
        self.assertEqual(command[:3], (
            "roslaunch", "epgeneral_ground_air_control",
            "relocalization_control.launch",
        ))
        self.assertIn("map_id:=test60", command)

    def test_same_owner_duplicate_is_idempotent_and_other_owner_is_rejected(self):
        backend = Backend()
        controller = StageController(backend)
        request = normalize_request(RELOCALIZATION, "test60", 60)
        commands = build_stage_commands(request)
        first = controller.transition(request, commands, "/ccs_relocalization_stage_a")
        duplicate = controller.transition(
            request, commands, "/ccs_relocalization_stage_a"
        )
        conflict = controller.transition(
            request, commands, "/ccs_relocalization_stage_b"
        )
        self.assertTrue(first.success)
        self.assertTrue(duplicate.success)
        self.assertFalse(conflict.success)
        self.assertEqual(len(backend.started), 1)

    def test_unidentified_caller_cannot_stop_ccs_owned_stage(self):
        backend = Backend()
        controller = StageController(backend)
        request = normalize_request(RELOCALIZATION, "test60", 60)
        controller.transition(
            request, build_stage_commands(request),
            "/ccs_relocalization_stage_a",
        )
        stop = normalize_request(BASE, "test60", 15)
        result = controller.transition(stop, build_stage_commands(stop), "")
        self.assertFalse(result.success)
        self.assertEqual(result.active_stage, RELOCALIZATION)
        self.assertEqual(backend.stopped, [])

    def test_mapping_owner_cannot_switch_to_relocalization(self):
        backend = Backend()
        controller = StageController(backend)
        mapping = normalize_request(MAPPING, "test60", 60)
        controller.transition(
            mapping, build_stage_commands(mapping), "/ccs_mapping_stage_a"
        )
        relocation = normalize_request(RELOCALIZATION, "test60", 60)
        result = controller.transition(
            relocation, build_stage_commands(relocation), "/ccs_mapping_stage_a"
        )
        self.assertFalse(result.success)
        self.assertEqual(result.active_stage, MAPPING)


class AdapterTests(unittest.TestCase):
    def test_loads_map_and_relocalizes_with_initial_guess(self):
        calls = []
        adapter = InitialPoseAdapter(
            "test60",
            lambda map_id, uri: calls.append(("load", map_id, uri)) or Response(),
            lambda use_guess, pose, timeout: (
                calls.append(("relocalize", use_guess, pose, timeout))
                or Response()
            ),
            60,
            lambda _message: None,
        )
        pose = object()
        adapter.load()
        adapter.handle_initial_pose(pose)
        self.assertEqual(calls[0], ("load", "test60", ""))
        self.assertEqual(calls[1], ("relocalize", True, pose, 60.0))

    def test_bridge_releases_only_once(self):
        calls = []

        def service(stage, map_id, timeout):
            calls.append((stage, map_id, timeout))
            return Response(active_stage=stage)

        bridge = RelocalizationStageBridge(
            service, "test60", 60, lambda _message: None
        )
        bridge.acquire()
        bridge.release()
        bridge.release()
        self.assertEqual([item[0] for item in calls], [RELOCALIZATION, BASE])


if __name__ == "__main__":
    unittest.main()
