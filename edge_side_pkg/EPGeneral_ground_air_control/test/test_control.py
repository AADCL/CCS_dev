import os
import sys
import unittest
from unittest.mock import Mock


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


class StageOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.backend.find_conflicts.return_value = []
        self.backend.wait_primary.return_value = True
        self.backend.wait_stage_transforms.return_value = True
        self.controller = StageController(self.backend)

    def transition(self, stage=1, map_id="map1", owner="/ccs_mapping_stage_a"):
        request = normalize_request(stage, map_id, 5)
        return self.controller.transition(request, build_stage_commands(request), owner=owner)

    def test_mapping_only_starts_primary_and_duplicate_is_idempotent(self):
        self.assertTrue(self.transition().success)
        commands = self.backend.start.call_args_list
        self.assertIn("manual_mapping_control.launch", commands[0].args[0])
        self.assertTrue(self.transition().success)
        self.assertEqual(self.backend.start.call_count, 1)

    def test_stop_owned_session_returns_base(self):
        self.transition()
        self.backend.find_conflicts.reset_mock()
        self.backend.find_conflicts.return_value = [
            "coordinate transform pair is not ready"]
        self.assertTrue(self.transition(0).success)
        self.backend.find_conflicts.assert_not_called()
        self.assertEqual(self.backend.stop.call_count, 1)
        self.assertEqual(self.controller.children, [])
        self.assertEqual(self.controller.active_owner, "")
        self.assertTrue(self.transition(0).success)

    def test_does_not_adopt_same_map_started_manually(self):
        self.transition(owner="")
        self.assertFalse(self.transition().success)
        self.assertFalse(self.transition(0).success)
        self.backend.stop.assert_not_called()

    def test_wrong_owner_or_map_cannot_stop_or_replace(self):
        self.transition()
        for stage in (0, 1):
            self.assertFalse(self.transition(stage, owner="/ccs_mapping_stage_b").success)
            self.assertFalse(self.transition(stage, map_id="other").success)
        self.backend.stop.assert_not_called()

    def test_ccs_does_not_interrupt_relocalization(self):
        self.transition(2, owner="")
        self.assertFalse(self.transition().success)
        self.assertFalse(self.transition(0).success)
        self.backend.stop.assert_not_called()

    def test_manual_stage_switch_cannot_revoke_ccs_owner(self):
        self.transition()
        for stage in (0, 1, 2):
            self.assertFalse(self.transition(stage, owner="").success)
        self.backend.stop.assert_not_called()
        self.assertEqual(self.controller.active_owner, "/ccs_mapping_stage_a")
        self.assertTrue(self.transition(0).success)

    def test_mapping_and_relocalization_cannot_take_each_others_session(self):
        for active_stage, active_owner, other_stage, other_owner in (
                (1, "/ccs_mapping_stage_a", 2, "/ccs_relocalization_stage_b"),
                (2, "/ccs_relocalization_stage_b", 1, "/ccs_mapping_stage_a")):
            with self.subTest(active_stage=active_stage):
                self.backend.reset_mock()
                self.controller = StageController(self.backend)
                self.assertTrue(self.transition(active_stage, owner=active_owner).success)
                self.assertFalse(self.transition(other_stage, owner=other_owner).success)
                self.assertFalse(self.transition(0, owner=other_owner).success)
                self.assertEqual(self.controller.active_stage, active_stage)
                self.assertEqual(self.controller.active_owner, active_owner)
                self.backend.stop.assert_not_called()
                self.assertEqual(self.backend.start.call_count, 1)

    def test_failed_start_stops_only_its_children(self):
        self.backend.wait_stage_transforms.return_value = False
        self.assertFalse(self.transition().success)
        self.assertEqual(self.backend.stop.call_count, 1)
        self.assertEqual(self.controller.active_stage, 0)
        self.assertTrue(self.transition(0).success)

    def test_stop_failure_is_not_reported_as_base(self):
        self.transition()
        self.backend.stop.side_effect = RuntimeError("still running")
        with self.assertRaisesRegex(RuntimeError, "still running"):
            self.transition(0)
        self.assertEqual(self.controller.active_stage, 1)
        self.assertEqual(len(self.controller.children), 1)

    def test_unmanaged_nodes_rejected_without_stopping_anything(self):
        self.backend.find_conflicts.return_value = ["/fast_lio_node"]
        self.assertFalse(self.transition().success)
        self.backend.stop.assert_not_called()
        self.backend.start.assert_not_called()


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
