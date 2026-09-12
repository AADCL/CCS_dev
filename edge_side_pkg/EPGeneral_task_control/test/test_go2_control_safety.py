import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import yaml

from epgeneral_task_control.config import ConfigError, load_config
from epgeneral_task_control.control_safety import ControlSafetyError, NavigationControlSafety
from epgeneral_task_control.scout_adapter import ScoutNavigationAdapter


def response(success=True, message="ok"):
    return types.SimpleNamespace(success=success, message=message)


def configuration(directory):
    return {
        "navigation_management": "attach", "auto_arm_on_schedule": True,
        "auto_disarm_on_terminal": True, "localization_ok_topic": "/localization/ok",
        "control_enabled_topic": "/go2/control/enabled",
        "navigation_reset_service": "/go2_navigation_supervisor/reset",
        "control_enable_service": "/go2_sdk_bridge_real/enable",
        "emergency_stop_state_file": os.path.join(directory, "emergency.json"),
        "control_service_timeout_seconds": 0.15, "control_state_timeout_seconds": 0.08,
        "pose_timeout_seconds": 2.0, "map_frame": "map",
    }


class ControlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = configuration(self.directory.name)
        self.stop = threading.Event()
        self.rospy = Mock()
        self.control = NavigationControlSafety(self.rospy, self.config, self.stop)
        self.control.localization_callback(types.SimpleNamespace(data=True))
        self.control.control_callback(types.SimpleNamespace(data=False))
        self.calls = []
        self.control.reset_client = Mock(return_value=response())

        def enable(value):
            self.calls.append(value)
            self.control.control_callback(types.SimpleNamespace(data=value))
            return response()

        self.control.enable_client = Mock(side_effect=enable)

    def test_start_uses_trigger_reset_and_set_bool_enable(self):
        trigger, set_bool = object(), object()
        modules = {"std_msgs.msg": types.SimpleNamespace(Bool=object()),
                   "std_srvs.srv": types.SimpleNamespace(Trigger=trigger, SetBool=set_bool)}
        with patch.dict(sys.modules, modules):
            self.control.start(Mock())
        self.assertEqual(self.rospy.ServiceProxy.call_args_list[0].args,
                         (self.config["navigation_reset_service"], trigger))
        self.assertEqual(self.rospy.ServiceProxy.call_args_list[1].args,
                         (self.config["control_enable_service"], set_bool))
        self.assertEqual(self.rospy.Service.call_args.args[0], "~reset_emergency_stop")

    def test_successful_arm_and_disarm_require_confirmed_state(self):
        self.control.arm()
        self.control.assert_running()
        self.control.disarm()
        self.assertEqual(self.calls, [True, False])
        self.assertFalse(self.control.latched)

    def test_reset_rejection_never_enables(self):
        self.control.reset_client.return_value = response(False, "localization unavailable")
        with self.assertRaisesRegex(ControlSafetyError, "navigation reset refused"):
            self.control.arm()
        self.assertEqual(self.calls, [])

    def test_service_unavailable_never_enables(self):
        self.rospy.wait_for_service.side_effect = RuntimeError("unavailable")
        with self.assertRaisesRegex(ControlSafetyError, "unavailable"):
            self.control.arm()
        self.assertEqual(self.calls, [])

    def test_stale_localization_prevents_reset(self):
        self.control.localization = (True, time.monotonic() - 5.0)
        with self.assertRaisesRegex(ControlSafetyError, "stale"):
            self.control.arm()
        self.control.reset_client.assert_not_called()

    def test_stale_disabled_state_prevents_reset(self):
        self.control.control = (False, time.monotonic() - 5.0)
        with self.assertRaisesRegex(ControlSafetyError, "fresh disabled"):
            self.control.arm()
        self.control.reset_client.assert_not_called()

    def test_enable_rejection_is_compensated(self):
        original = self.control.enable_client.side_effect
        self.control.enable_client.side_effect = lambda value: response(False, "locked") if value else original(value)
        with self.assertRaisesRegex(ControlSafetyError, "control enable refused"):
            self.control.arm()
        self.assertEqual(self.control.enable_client.call_args_list[0].args, (True,))
        self.assertEqual(self.control.enable_client.call_args_list[1].args, (False,))

    def test_cancel_during_reset_prevents_enable(self):
        self.control.reset_client.side_effect = lambda: self.stop.set() or response()
        with self.assertRaisesRegex(ControlSafetyError, "cancelled"):
            self.control.arm()
        self.assertEqual(self.calls, [])

    def test_cancel_during_enable_compensates_with_disable(self):
        original = self.control.enable_client.side_effect

        def enable(value):
            result = original(value)
            if value:
                self.stop.set()
            return result

        self.control.enable_client.side_effect = enable
        with self.assertRaisesRegex(ControlSafetyError, "cancelled"):
            self.control.arm()
        self.assertEqual(self.calls, [True, False])

    def test_enable_needs_state_after_rpc_issue(self):
        original = self.control.enable_client.side_effect
        self.control.enable_client.side_effect = lambda value: response() if value else original(value)
        with self.assertRaisesRegex(ControlSafetyError, "confirmation timed out"):
            self.control.arm()
        self.assertEqual(self.control.enable_client.call_count, 2)

    def test_disable_rejection_latches_instead_of_succeeding(self):
        self.control.enable_client.side_effect = lambda unused: response(False, "rejected")
        with self.assertRaisesRegex(ControlSafetyError, "disable refused"):
            self.control.disarm()
        self.assertTrue(os.path.isfile(self.control.path))
        self.assertTrue(self.control.latched)

    def test_existing_false_before_disable_is_not_confirmation(self):
        self.control.enable_client.side_effect = lambda unused: response()
        with self.assertRaisesRegex(ControlSafetyError, "confirmation timed out"):
            self.control.disarm()
        self.assertTrue(self.control.latched)

    def test_shutdown_accepts_an_existing_fresh_disabled_confirmation(self):
        self.control.disarm(allow_confirmed_disabled=True)
        self.control.enable_client.assert_not_called()
        self.assertFalse(self.control.latched)

    def test_shutdown_does_not_accept_a_stale_disabled_confirmation(self):
        self.control.control = (False, time.monotonic() - 5.0)
        self.control.enable_client.side_effect = lambda unused: response()
        with self.assertRaisesRegex(ControlSafetyError, "confirmation timed out"):
            self.control.disarm(allow_confirmed_disabled=True)
        self.assertTrue(self.control.latched)

    def test_shutdown_cannot_skip_pending_rpc_even_with_fresh_false(self):
        self.control.rpc_lock.acquire()
        try:
            with self.assertRaisesRegex(ControlSafetyError, "previous control service call"):
                self.control.disarm(allow_confirmed_disabled=True)
            self.assertTrue(self.control.latched)
            self.control.enable_client.assert_not_called()
            self.assertFalse(self.control.transition_lock.locked())
        finally:
            self.control.rpc_lock.release()

    def test_shutdown_waits_for_pending_rpc_then_confirms_disable(self):
        self.control.rpc_lock.acquire()
        released = threading.Event()
        def finish_pending():
            time.sleep(0.03)
            self.control.rpc_lock.release()
            released.set()
        worker = threading.Thread(target=finish_pending)
        worker.start()
        try:
            self.control.disarm(allow_confirmed_disabled=True)
            self.assertTrue(released.is_set())
            self.control.enable_client.assert_called_once_with(False)
            self.assertFalse(self.control.latched)
        finally:
            worker.join(1)

    def test_late_enable_timeout_stays_locked_and_is_compensated(self):
        released = threading.Event()
        entered = threading.Event()
        original = self.control.enable_client.side_effect

        def enable(value):
            if value:
                entered.set()
                released.wait(2.0)
            return original(value)

        self.control.enable_client.side_effect = enable
        errors = []

        def arm():
            try:
                self.control.arm()
            except ControlSafetyError as exc:
                errors.append(exc)

        worker = threading.Thread(target=arm)
        worker.start()
        self.assertTrue(entered.wait(1.0))
        worker.join(1.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertTrue(self.control.latched)
        with self.assertRaisesRegex(ControlSafetyError, "still pending"):
            self.control.clear_latch()
        released.set()
        self.assertTrue(self.control.rpc_lock.acquire(timeout=1.0))
        self.control.rpc_lock.release()
        self.assertEqual(self.calls, [True, False])
        self.assertTrue(self.control.latched)

    def test_emergency_lock_survives_restart_and_only_fresh_disabled_clears_it(self):
        self.control.latch("test emergency")
        restarted = NavigationControlSafety(self.rospy, self.config, threading.Event())
        with self.assertRaisesRegex(ControlSafetyError, "manual reset"):
            restarted.assert_unlatched()
        with self.assertRaisesRegex(ControlSafetyError, "fresh disabled"):
            restarted.clear_latch()
        restarted.control_callback(types.SimpleNamespace(data=True))
        with self.assertRaises(ControlSafetyError):
            restarted.clear_latch()
        restarted.control_callback(types.SimpleNamespace(data=False))
        restarted.clear_latch()
        self.assertFalse(os.path.exists(restarted.path))
        self.assertFalse(restarted.latched)
        self.control.enable_client.assert_not_called()

    def test_corrupt_lock_stays_fail_closed(self):
        with open(self.control.path, "w") as stream:
            stream.write("corrupt")
        restarted = NavigationControlSafety(self.rospy, self.config, threading.Event())
        self.assertTrue(restarted.latched)

    def test_running_requires_live_localization_and_control(self):
        self.control.arm()
        self.control.localization_callback(types.SimpleNamespace(data=False))
        with self.assertRaisesRegex(ControlSafetyError, "localization"):
            self.control.assert_running()
        self.control.localization_callback(types.SimpleNamespace(data=True))
        self.control.control = (True, time.monotonic() - 5.0)
        with self.assertRaisesRegex(ControlSafetyError, "stale"):
            self.control.assert_running()

    def _diagnostic(self, value, age=0.0):
        self.rospy.get_time.return_value = 100.0
        return types.SimpleNamespace(header=types.SimpleNamespace(stamp=types.SimpleNamespace(
            to_sec=lambda: 100.0 - age)), status=[types.SimpleNamespace(name="GO2 SDK bridge",
            values=[types.SimpleNamespace(key="motion_enabled", value=value)])])

    def test_periodic_diagnostics_renew_latched_state_without_native_changes(self):
        self.control.control = (False, time.monotonic() - 5.0)
        self.control.diagnostics_callback(self._diagnostic("false"))
        self.assertTrue(self.control._control_matches(False))
        self.control.arm()
        self.control.control = (True, time.monotonic() - 5.0)
        self.control.diagnostics_callback(self._diagnostic("true"))
        self.control.assert_running()

    def test_stale_or_conflicting_diagnostics_cannot_confirm_state(self):
        self.control.control = (False, time.monotonic() - 5.0)
        self.control.diagnostics_callback(self._diagnostic("false", age=10.0))
        self.assertFalse(self.control._control_matches(False))
        self.control.diagnostics_callback(self._diagnostic("true"))
        self.assertFalse(self.control._control_matches(True))
        self.assertFalse(self.control._control_matches(False))

    def test_pre_rpc_diagnostic_delivered_after_rpc_does_not_confirm_disable(self):
        self.control.control = (False, time.monotonic() - 5.0)
        self.control.enable_client.side_effect = lambda unused: (
            self.control.diagnostics_callback(self._diagnostic("false", age=0.04)) or response())
        with self.assertRaisesRegex(ControlSafetyError, "confirmation timed out"):
            self.control.disarm()


class Go2AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.adapter = ScoutNavigationAdapter(Mock(), configuration(self.directory.name),
                                               types.SimpleNamespace(EMERGENCY_STOP=6), object)
        self.adapter._feedback = Mock()
        self.adapter._publish_zero = Mock()
        self.adapter._map_pose = Mock(return_value=(0.0, 0.0, 0.0))
        self.adapter.client = Mock()
        self.safety = self.adapter.control_safety
        self.safety.control_callback(types.SimpleNamespace(data=False))
        self.safety.localization_callback(types.SimpleNamespace(data=True))
        self.safety.reset_client = Mock(return_value=response())

        def enable(value):
            self.safety.control_callback(types.SimpleNamespace(data=value))
            return response()

        self.safety.enable_client = Mock(side_effect=enable)
        self.command = types.SimpleNamespace(action=6)

    def test_attach_never_launches_or_owns_native_navigation(self):
        self.adapter.process_factory = Mock()
        self.assertIsNone(self.adapter._start_navigation("map-1"))
        self.assertFalse(self.adapter._process_exited())
        self.adapter._stop_navigation()
        self.adapter.process_factory.assert_not_called()

    def test_legacy_default_does_not_create_control_safety(self):
        adapter = ScoutNavigationAdapter(Mock(), {}, object, object)
        self.assertIsNone(adapter.control_safety)
        self.assertTrue(adapter._process_exited())

    def test_emergency_stop_cancels_and_confirms_disable_then_reports(self):
        client = self.adapter.client
        self.adapter._emergency_stop(self.command)
        client.cancel_all_goals.assert_called_once()
        self.adapter._publish_zero.assert_called_once()
        self.safety.enable_client.assert_called_once_with(False)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "emergency_stopped")
        self.assertTrue(self.safety.latched)
        self.assertIsNone(self.adapter.prepared)

    def test_emergency_stop_disable_failure_reports_failed(self):
        self.safety.enable_client.side_effect = lambda unused: response(False, "not disabled")
        self.adapter._emergency_stop(self.command)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")
        self.assertIn("not disabled", self.adapter._feedback.call_args.args[4])
        self.assertTrue(self.safety.latched)

    def test_emergency_persistence_failure_still_attempts_disarm(self):
        with patch.object(self.safety, "latch", side_effect=ControlSafetyError("disk unavailable")):
            self.adapter._emergency_stop(self.command)
        self.safety.enable_client.assert_called_once_with(False)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")

    def test_cancel_error_does_not_skip_disable(self):
        self.adapter.client.cancel_all_goals.side_effect = RuntimeError("action unavailable")
        self.adapter._stop(self.command)
        self.safety.enable_client.assert_called_once_with(False)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")

    def test_completed_is_not_reported_when_disarm_fails(self):
        self.adapter.execution = self._execution()
        self.safety.enable_client.side_effect = lambda unused: response(False, "cannot disable")
        self.adapter._finish(self.command, "completed", 1, 1.0, "done")
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")
        self.assertIsNone(self.adapter.execution)

    def test_localization_watchdog_disarms_active_execution(self):
        self.adapter.control_armed = True
        self.adapter.execution = self._execution()
        self.safety.localization_callback(types.SimpleNamespace(data=False))
        self.adapter.watchdog()
        self.safety.enable_client.assert_called_once_with(False)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")

    def test_shutdown_cancel_error_still_disarms(self):
        self.adapter.client.cancel_all_goals.side_effect = RuntimeError("lost action")
        self.adapter.close()
        self.safety.enable_client.assert_called_once_with(False)

    def test_ros_shutdown_with_fresh_disabled_state_does_not_reissue_rpc(self):
        self.adapter.rospy.is_shutdown.return_value = True
        self.adapter.close()
        self.safety.enable_client.assert_not_called()
        self.assertFalse(self.safety.latched)

    def test_on_shutdown_hook_accepts_fresh_disabled_state_before_shutdown_flag(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = True
        self.adapter.close()
        self.safety.enable_client.assert_not_called()
        self.assertFalse(self.safety.latched)

    def test_close_outside_ros_shutdown_still_confirms_disable(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = False
        self.adapter.close()
        self.safety.enable_client.assert_called_once_with(False)

    def test_shutdown_unload_with_fresh_disabled_state_does_not_reissue_rpc(self):
        self.adapter.rospy.is_shutdown.return_value = True
        self.command.request_id = "shutdown-unload"
        self.adapter._unload(self.command)
        self.safety.enable_client.assert_not_called()
        self.assertFalse(self.safety.latched)

    def test_shutdown_unload_accepts_disabled_state_before_shutdown_flag(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = True
        self.command.request_id = "shutdown-unload"
        self.adapter._unload(self.command)
        self.safety.enable_client.assert_not_called()
        self.assertFalse(self.safety.latched)

    def test_coordinator_shutdown_before_adapter_signal_closes_once(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = False
        self.command.request_id = "shutdown-unload"
        self.adapter._unload(self.command)
        first = self.adapter.close_result
        self.adapter.close()
        self.assertEqual(self.adapter.close_result, first)
        self.safety.enable_client.assert_not_called()
        self.adapter._publish_zero.assert_called_once()
        self.assertFalse(self.safety.latched)
        self.assertTrue(self.adapter.closed)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "unloaded")

    def test_coordinator_restart_can_prepare_again_and_restore_watchdog(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = False
        execution = self._execution()
        command, payload = execution["command"], execution["payload"]
        client = self.adapter.client
        client.wait_for_server.return_value = True
        self.adapter._make_action_client = Mock(return_value=client)
        previous_timer = Mock()
        self.adapter.monitor_timer = previous_timer
        self.command.request_id = "shutdown-unload"
        self.adapter._unload(self.command)
        previous_timer.shutdown.assert_called_once()
        with patch("epgeneral_task_control.scout_adapter.TrajectoryStore") as store, \
                patch("epgeneral_task_control.scout_adapter.load_localized_map_state", return_value={"map_id": "map-1"}), \
                patch("epgeneral_task_control.scout_adapter.validate_waypoints_on_navigation_map"), \
                patch("epgeneral_task_control.scout_adapter.os.path.isfile", return_value=True):
            store.return_value.load_payload.return_value = payload
            self.adapter._prepare(command)
            self.assertIsNotNone(self.adapter.prepare_worker)
            self.adapter.prepare_worker.join(1.0)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "ready")
        self.assertFalse(self.adapter.closing)
        self.assertFalse(self.adapter.closed)
        self.assertFalse(self.adapter.stop_event.is_set())
        self.assertIs(self.adapter.client, client)
        self.assertIs(self.adapter.monitor_timer, self.adapter.rospy.Timer.return_value)
        self.adapter.rospy.Timer.assert_called_once()
        self.assertIsNone(self.adapter.close_result)
        self.safety.enable_client.assert_not_called()
        self.adapter.close()
        self.safety.enable_client.assert_called_once_with(False)

    def test_adapter_shutdown_after_coordinator_unload_blocks_new_preparation(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = False
        self.command.request_id = "shutdown-unload"
        self.adapter._unload(self.command)
        self.adapter.rospy.core.is_shutdown_requested.return_value = True
        self.adapter.close()
        self.adapter._prepare(self.command)
        self.assertEqual(self.adapter._feedback.call_args.args[5], "BUSY")
        self.assertTrue(self.adapter.close_permanent)
        self.assertTrue(self.adapter.closing)
        self.adapter._publish_zero.assert_called_once()
        self.safety.enable_client.assert_not_called()

    def test_close_then_internal_unload_preserves_failure_without_retry(self):
        self.adapter.rospy.is_shutdown.return_value = False
        self.adapter.rospy.core.is_shutdown_requested.return_value = False
        self.safety.enable_client.side_effect = lambda _: response(False, "cannot stop")
        result = self.adapter.close()
        self.command.request_id = "shutdown-unload"
        self.safety.control_callback(types.SimpleNamespace(data=False))
        self.adapter._unload(self.command)
        self.assertEqual(self.adapter.close(), result)
        self.assertEqual(result[0], "failed")
        self.assertTrue(self.safety.latched)
        self.safety.enable_client.assert_called_once_with(False)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")

    def test_closing_rejects_new_work_and_does_not_restart_watchdog_stop(self):
        self.adapter.close()
        calls = self.safety.enable_client.call_count
        self.adapter._prepare(self.command)
        self.assertEqual(self.adapter._feedback.call_args.args[5], "BUSY")
        self.adapter._schedule(self.command)
        self.assertEqual(self.adapter._feedback.call_args.args[5], "BUSY")
        with patch.dict(sys.modules, {"std_srvs.srv": types.SimpleNamespace(TriggerResponse=response)}):
            self.assertFalse(self.adapter._reset_emergency_stop(None).success)
        self.adapter.watchdog()
        self.adapter._finish(self.command, "stopped", -1, 0, "late", force=True)
        self.assertEqual(self.safety.enable_client.call_count, calls)
        self.assertTrue(self.adapter.stop_event.is_set())

    def test_unload_outside_shutdown_still_requires_rpc(self):
        self.command.request_id = "ordinary-unload"
        self.safety.enable_client.side_effect = lambda _: response(False, "refused")
        self.adapter._unload(self.command)
        self.safety.enable_client.assert_called_once_with(False)
        self.assertTrue(self.safety.latched)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")

    def test_concurrent_closes_are_serialized(self):
        entered, release = threading.Event(), threading.Event()
        original = self.safety.enable_client.side_effect
        def pending(value):
            entered.set()
            release.wait(1)
            return original(value)
        self.safety.enable_client.side_effect = pending
        results = []
        first = threading.Thread(target=lambda: results.append(self.adapter.close()))
        second = threading.Thread(target=lambda: results.append(self.adapter.close()))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        release.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.safety.enable_client.assert_called_once_with(False)
        self.adapter._publish_zero.assert_called_once()

    def test_manual_reset_requires_no_active_execution_and_does_not_enable(self):
        self.safety.latch("test")
        modules = {"std_srvs.srv": types.SimpleNamespace(TriggerResponse=response)}
        with patch.dict(sys.modules, modules):
            self.adapter.execution = {"command": self.command}
            self.assertFalse(self.adapter._reset_emergency_stop(None).success)
            self.adapter.execution = None
            self.assertTrue(self.adapter._reset_emergency_stop(None).success)
        self.safety.enable_client.assert_not_called()
        self.assertFalse(self.safety.latched)

    def test_new_prepare_does_not_clear_emergency(self):
        self.safety.latch("test")
        self.adapter._prepare(self.command)
        self.assertTrue(self.safety.latched)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")
        self.assertEqual(self.adapter._feedback.call_args.args[5], "EMERGENCY_STOP_LATCHED")

    def _execution(self):
        payload = {"task_id": "t", "subtask_id": "s", "device_id": "QRD_002", "revision": 1,
                   "map_id": "map-1", "frame_id": "map", "start_delay_seconds": 0.0,
                   "waypoints": [{"index": 0, "x": 1.0, "y": 1.0, "z": 0.0},
                                 {"index": 1, "x": 2.0, "y": 1.0, "z": 0.0}]}
        command = types.SimpleNamespace(task_id="t", subtask_id="s", device_id="QRD_002",
                                        revision=1, request_id="request", map_id="map-1",
                                        scheduled_at=types.SimpleNamespace(to_sec=lambda: time.time() + 0.01))
        self.adapter.config.update(storage_directory=self.directory.name, device_id="QRD_002",
                                   active_map_state_file="state.json", navigation_map_root=self.directory.name,
                                   navigation_map_yaml="map.yaml", navigation_startup_timeout_seconds=1.0,
                                   waypoint_timeout_seconds=1.0, control_state_timeout_seconds=1.0)
        self.adapter.latest_odom_at = time.monotonic()
        execution = {"command": command, "payload": payload, "scheduled_at": time.time(), "waypoint_index": -1}
        return execution

    def test_prepare_schedule_and_completion_use_mock_goals_and_disable(self):
        execution = self._execution()
        command, payload = execution["command"], execution["payload"]
        client = self.adapter.client
        client.wait_for_server.return_value = True
        client.wait_for_result.return_value = True
        client.get_state.return_value = 3
        self.adapter._make_action_client = Mock(return_value=client)

        def goal():
            return types.SimpleNamespace(target_pose=types.SimpleNamespace(
                header=types.SimpleNamespace(), pose=types.SimpleNamespace(
                    position=types.SimpleNamespace(), orientation=types.SimpleNamespace())))

        modules = {"move_base_msgs.msg": types.SimpleNamespace(MoveBaseGoal=goal)}
        with patch.dict(sys.modules, modules), \
                patch("epgeneral_task_control.scout_adapter.TrajectoryStore") as store, \
                patch("epgeneral_task_control.scout_adapter.load_localized_map_state", return_value={"map_id": "map-1"}), \
                patch("epgeneral_task_control.scout_adapter.validate_waypoints_on_navigation_map"), \
                patch("epgeneral_task_control.scout_adapter.os.path.isfile", return_value=True):
            store.return_value.load_payload.return_value = payload
            self.adapter._prepare(command)
            self.adapter.prepare_worker.join(1.0)
            self.assertEqual(self.adapter._feedback.call_args.args[1], "ready")
            self.assertIsNone(self.adapter.navigation_process)
            self.adapter._schedule(command)
            self.adapter.worker.join(2.0)
        self.assertFalse(self.adapter.worker.is_alive())
        self.assertEqual(client.send_goal.call_count, 2)
        self.assertEqual([call.args for call in self.safety.enable_client.call_args_list], [(True,), (False,)])
        self.assertEqual(self.adapter._feedback.call_args.args[1], "completed")
        self.assertIsNone(self.adapter.execution)

    def test_cancel_during_scheduled_wait_never_arms_or_sends_goal(self):
        execution = self._execution()
        execution["scheduled_at"] = time.time() + 30.0
        self.adapter.execution = execution
        client = self.adapter.client
        worker = threading.Thread(target=self.adapter._run, args=(execution,))
        self.adapter.worker = worker
        worker.start()
        self.adapter._stop(execution["command"])
        worker.join(1.0)
        self.assertFalse(worker.is_alive())
        self.safety.reset_client.assert_not_called()
        self.safety.enable_client.assert_called_once_with(False)
        client.send_goal.assert_not_called()

    def test_reset_failure_reports_failed_without_sending_goal(self):
        execution = self._execution()
        self.adapter.execution = execution
        client = self.adapter.client
        self.safety.reset_client.return_value = response(False, "reset not ready")
        self.adapter._run(execution)
        client.send_goal.assert_not_called()
        self.safety.enable_client.assert_called_once_with(False)
        self.assertEqual(self.adapter._feedback.call_args.args[1], "failed")
        self.assertIn("reset not ready", self.adapter._feedback.call_args.args[4])

    def test_emergency_during_enable_cannot_send_goal(self):
        execution = self._execution()
        self.adapter.execution = execution
        client = self.adapter.client
        enable_entered, enable_released = threading.Event(), threading.Event()
        original = self.safety.enable_client.side_effect

        def enable(value):
            if value:
                enable_entered.set()
                enable_released.wait(1.0)
            return original(value)

        self.safety.enable_client.side_effect = enable
        self.safety.config["control_service_timeout_seconds"] = 1.0
        worker = threading.Thread(target=self.adapter._run, args=(execution,))
        self.adapter.worker = worker
        worker.start()
        self.assertTrue(enable_entered.wait(1.0))
        emergency = threading.Thread(target=self.adapter._emergency_stop, args=(execution["command"],))
        emergency.start()
        self.assertTrue(self.adapter.stop_event.wait(1.0))
        enable_released.set()
        worker.join(2.0)
        emergency.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertFalse(emergency.is_alive())
        client.send_goal.assert_not_called()
        self.assertEqual(self.safety.enable_client.call_args.args, (False,))
        self.assertEqual(self.adapter._feedback.call_args.args[1], "emergency_stopped")
        self.assertTrue(self.safety.latched)

    def test_old_watchdog_failure_does_not_terminate_replacement_execution(self):
        old = self._execution()
        self.adapter.execution = old
        self.adapter.control_armed = True
        self.safety.localization_callback(types.SimpleNamespace(data=False))
        captured, released = threading.Event(), threading.Event()
        original = self.adapter._finish

        def delayed(*args, **kwargs):
            if threading.current_thread().name == "old-watchdog":
                captured.set()
                released.wait(1.0)
            return original(*args, **kwargs)

        self.adapter._finish = delayed
        worker = threading.Thread(target=self.adapter.watchdog, name="old-watchdog")
        worker.start()
        self.assertTrue(captured.wait(1.0))
        original(old["command"], "completed", 1, 1.0, "done", expected_execution=old)
        replacement = self._execution()
        self.adapter.execution = replacement
        self.adapter.stop_event.clear()
        self.safety.enable_client.reset_mock()
        released.set()
        worker.join(1.0)
        self.assertFalse(worker.is_alive())
        self.assertIs(self.adapter.execution, replacement)
        self.assertFalse(self.adapter.stop_event.is_set())
        self.safety.enable_client.assert_not_called()

    def test_slow_control_transitions_keep_coordinator_alive(self):
        from test_node import NodeTests, pack
        harness = NodeTests()
        harness.setUp()
        self.addCleanup(harness.tearDown)
        harness.config["execution_feedback_seconds"] = 0.09
        crc32, count = harness.deliver()
        harness.node.handle_datagram(pack(harness.config, "task_commit", "commit", {
            "revision": 1, "chunk_count": count, "crc32": crc32}), harness.config["ground_station_ip"])
        harness.prepare_ready()
        harness.node.handle_datagram(pack(harness.config, "execute_task", "execute", {
            "revision": 1, "scheduled_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 5)) + "+00:00",
        }, execution_id="exec-1"), harness.config["ground_station_ip"])
        execution = self._execution()
        command = types.SimpleNamespace(request_id="execute", task_id="task-1", subtask_id="sub-1",
                                        device_id=harness.config["device_id"], execution_id="exec-1", revision=1)
        execution["command"] = command
        self.adapter.execution = execution
        self.adapter.config.update(execution_feedback_seconds=0.09, control_service_timeout_seconds=0.5)

        def forward(command, state, index, progress, message, error_code="", **unused):
            feedback = types.SimpleNamespace(**vars(command), state=state, waypoint_index=index,
                                             waypoint_count=2, progress=progress, message=message,
                                             error_code=error_code, position=types.SimpleNamespace(x=0, y=0, z=0))
            harness.node.feedback_callback(feedback)

        self.adapter._feedback.side_effect = forward
        self.safety.reset_client.side_effect = lambda: time.sleep(0.24) or response()
        errors = []

        def arm():
            try:
                with self.adapter._transition_feedback(execution, "arming"):
                    self.safety.arm()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=arm)
        worker.start()
        while worker.is_alive():
            harness.node.watchdog()
            worker.join(0.02)
        self.assertEqual(errors, [])
        self.assertIsNotNone(harness.node.execution)
        self.assertEqual(harness.node.execution.state, "scheduled")
        execution["feedback_state"] = "running"
        forward(command, "running", 0, 0.5, "moving")
        original = self.safety.enable_client.side_effect
        self.safety.enable_client.side_effect = lambda value: time.sleep(0.24) or original(value)
        worker = threading.Thread(target=self.adapter._finish, args=(command, "completed", 1, 1.0, "done"))
        worker.start()
        while worker.is_alive():
            harness.node.watchdog()
            worker.join(0.02)
        self.assertIsNone(harness.node.execution)
        self.assertFalse(any(item["payload"].get("state") == "failed" for item in harness.messages()))

    def test_configuration_rejects_unsafe_or_incomplete_control_options(self):
        package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source = os.path.join(os.path.dirname(package), "EPGeneral_device_config", "config", "task_control.yaml")
        device = os.path.join(package, "test", "fixtures", "device.yaml")
        with open(source, encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        adapter = dict(self.adapter.config, active_map_state_file="state.json", navigation_map_root="maps",
                       navigation_map_yaml="map.yaml", navigation_launch_package="go2_bringup",
                       navigation_launch_file="navigation.launch", navigation_action="/move_base",
                       odom_topic="/odom_nav", zero_velocity_topic="/cmd_vel_nav",
                       navigation_startup_timeout_seconds=30, waypoint_timeout_seconds=60,
                       zero_velocity_hz=20, zero_velocity_count=10)
        path = os.path.join(self.directory.name, "task.yaml")
        for delta in ({"auto_arm_on_schedule": "true"}, {"auto_disarm_on_terminal": False},
                      {"emergency_stop_state_file": ""}, {"navigation_management": "other"},
                      {"control_state_timeout_seconds": 0}, {"control_service_timeout_seconds": True}, {}):
            config["adapter"] = dict(adapter, **delta)
            with open(path, "w", encoding="utf-8") as stream:
                yaml.safe_dump(config, stream)
            if delta:
                with self.assertRaises(ConfigError):
                    load_config(path, device)
            else:
                self.assertTrue(load_config(path, device)["adapter"]["auto_arm_on_schedule"])


if __name__ == "__main__":
    unittest.main()
