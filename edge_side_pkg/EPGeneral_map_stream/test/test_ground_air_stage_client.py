import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(PACKAGE))
STAGE_DIR = os.path.join(REPO, "edge_side_pkg", "deploy", "ground_air_agv", "car_bringup_scripts")
if not os.path.isdir(STAGE_DIR):
    STAGE_DIR = "/home/bitcq/catkin_ws/src/car_bringup/scripts"
sys.path.insert(0, STAGE_DIR)
from system_stage_core import normalize_request, build_stage_commands
from system_stage_runtime import StageController

spec = importlib.util.spec_from_file_location(
    "stage_client", os.path.join(PACKAGE, "scripts", "ground_air_stage_client.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


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

    def test_manual_stage_switch_revokes_old_owner(self):
        self.transition()
        self.assertTrue(self.transition(2, "other", owner="").success)
        calls = self.backend.stop.call_count
        self.assertFalse(self.transition(0).success)
        self.assertEqual(self.backend.stop.call_count, calls)

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


class StageClientTests(unittest.TestCase):
    def test_external_tf_check_requires_mode_and_both_nodes(self):
        with self.assertRaisesRegex(RuntimeError, "legacy resident TF"):
            client.require_external_tf(
                lambda key, default: 1, lambda: list(client.STATIC_TF_NODES))
        with self.assertRaisesRegex(RuntimeError, "external TF"):
            client.require_external_tf(lambda key, default: 0, lambda: [])
        external_mode = (
            lambda key, default:
            1 if key == client.EXTERNAL_TF_PARAM else default)
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            client.require_external_tf(
                external_mode, lambda: ["/odom_camera_init_broadcaster"])
        client.require_external_tf(
            external_mode, lambda: list(client.STATIC_TF_NODES))

    def test_stop_and_abort_do_not_require_external_tf(self):
        self.assertTrue(client.action_requires_external_tf("--check"))
        self.assertTrue(client.action_requires_external_tf("--start"))
        self.assertFalse(client.action_requires_external_tf("--stop"))
        self.assertFalse(client.action_requires_external_tf("--abort"))

    def test_validates_success_and_stage(self):
        for success, active in ((False, 1), (True, 0)):
            call = Mock(return_value=SimpleNamespace(success=success, active_stage=active, message="busy"))
            with self.assertRaisesRegex(RuntimeError, "set_stage rejected"):
                client.request_stage(call, 1, "map1", 5)

    def test_request_includes_map_and_timeout(self):
        call = Mock(return_value=SimpleNamespace(success=True, active_stage=0, message="base"))
        client.request_stage(call, 0, "map1", 5)
        call.assert_called_once_with(stage=0, map_id="map1", timeout=5)

    def test_node_readiness_requires_entire_set(self):
        ticks = iter([0, 0.1, 0.9, 1.1])
        with self.assertRaisesRegex(RuntimeError, "did not become ready"):
            client.wait_nodes(lambda: ["/fast_lio_node"], ["/fast_lio_node", "/tf"], 1,
                              clock=lambda: next(ticks), sleep=lambda _: None)
        client.wait_nodes(lambda: ["/fast_lio_node", "/tf"], ["/fast_lio_node", "/tf"], 1)


if __name__ == "__main__":
    unittest.main()
