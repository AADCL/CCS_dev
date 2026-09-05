import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    "stage_client", os.path.join(PACKAGE, "scripts", "ground_air_stage_client.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class StageClientMainTests(unittest.TestCase):
    OWNER = "0123456789abcdef0123456789abcdef"
    MAP_ID = "guard_v2_regression"
    MAPPING_NODES = (
        "/fast_lio_node", "/ground_air_map_recorder", "/ground_air_world_tf_owner")

    def setUp(self):
        self.params = {client.GUARD_PARAM: 2, client.EXTERNAL_TF_PARAM: 1}
        self.proxy = Mock(side_effect=lambda **kwargs: SimpleNamespace(
            success=True, active_stage=kwargs["stage"], message="ok"))
        self.rospy = SimpleNamespace(
            init_node=Mock(),
            wait_for_service=Mock(),
            get_param=Mock(side_effect=lambda key, default: self.params.get(key, default)),
            ServiceProxy=Mock(return_value=self.proxy),
        )
        self.rosnode = SimpleNamespace(get_node_names=Mock(return_value=(
            list(client.STATIC_TF_NODES) + list(self.MAPPING_NODES))))
        self.stage_service = object()
        services = SimpleNamespace(SetSystemStage=self.stage_service)
        modules = {
            "rospy": self.rospy,
            "rosnode": self.rosnode,
            "ground_air_msgs": SimpleNamespace(srv=services),
            "ground_air_msgs.srv": services,
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        stdout = patch("builtins.print")
        stdout.start()
        self.addCleanup(stdout.stop)

    def args(self, mode):
        return [mode, self.OWNER, self.MAP_ID, "7.5", ",".join(self.MAPPING_NODES)]

    def assert_session_request(self, target):
        self.rospy.init_node.assert_called_once_with(
            "ccs_mapping_stage_" + self.OWNER, disable_signals=True)
        self.rospy.wait_for_service.assert_called_once_with(client.SERVICE, timeout=4.0)
        self.rospy.ServiceProxy.assert_called_once_with(client.SERVICE, self.stage_service)
        self.proxy.assert_called_once_with(stage=target, map_id=self.MAP_ID, timeout=7.5)

    def test_supported_versions_pass_preflight_without_stage_request(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.params[client.GUARD_PARAM] = version
                self.rospy.init_node.reset_mock()
                client.main(["--check"])
                self.rospy.init_node.assert_called_once_with(
                    "ccs_mapping_stage_preflight", disable_signals=True)
                self.rospy.ServiceProxy.assert_not_called()
                self.proxy.assert_not_called()

    def test_missing_guard_is_rejected_with_actual_and_supported_versions(self):
        del self.params[client.GUARD_PARAM]
        with self.assertRaisesRegex(RuntimeError, r"actual=None; supported=\(1, 2\)"):
            client.main(["--check"])
        self.rospy.ServiceProxy.assert_not_called()
        self.rosnode.get_node_names.assert_not_called()

    def test_unknown_or_noninteger_guard_is_rejected_for_every_action(self):
        for version in (0, 3, -1, "1", "2", True, False, 1.0, 2.0):
            for mode in ("--check", "--start", "--stop", "--abort"):
                with self.subTest(version=version, mode=mode):
                    self.params[client.GUARD_PARAM] = version
                    with self.assertRaises(RuntimeError) as raised:
                        client.main(self.args(mode))
                    self.assertIn("actual={!r}".format(version), str(raised.exception))
                    self.assertIn("supported=(1, 2)", str(raised.exception))
        self.rospy.ServiceProxy.assert_not_called()
        self.rosnode.get_node_names.assert_not_called()

    def test_v2_start_preserves_owner_map_and_timeout(self):
        client.main(self.args("--start"))
        self.assert_session_request(1)
        self.assertGreaterEqual(self.rosnode.get_node_names.call_count, 2)

    def test_v2_stop_preserves_session_without_external_tf(self):
        self.params = {client.GUARD_PARAM: 2}
        self.rosnode.get_node_names.side_effect = AssertionError("TF lookup on stop")
        client.main(self.args("--stop"))
        self.assert_session_request(0)
        self.rospy.get_param.assert_called_once_with(client.GUARD_PARAM, None)
        self.rosnode.get_node_names.assert_not_called()

    def test_v2_abort_preserves_session_without_external_tf(self):
        self.params = {client.GUARD_PARAM: 2}
        self.rosnode.get_node_names.side_effect = AssertionError("TF lookup on abort")
        client.main(self.args("--abort"))
        self.assert_session_request(0)
        self.rospy.get_param.assert_called_once_with(client.GUARD_PARAM, None)
        self.rosnode.get_node_names.assert_not_called()

    def test_v2_preflight_and_start_still_require_external_tf(self):
        self.rosnode.get_node_names.return_value = []
        for mode in ("--check", "--start"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(RuntimeError, "transforms are not ready"):
                    client.main(self.args(mode))
        self.rospy.ServiceProxy.assert_not_called()


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
