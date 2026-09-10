import os
import tempfile
import unittest
import time
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from epgeneral_ground_air_control.task_adapter import (
    GroundAirAdapterError,
    GroundAirTaskAdapter,
    feedback_error_code,
    mission_progress,
    validate_ground_trajectory,
)


def payload(speed=0.1, task_type="ground"):
    return {
        "schema_version": 2, "task_id": "task-1", "subtask_id": "sub-1",
        "device_id": "AGV_001", "revision": 2, "task_name": "巡检",
        "task_type": task_type, "map_id": "map-1", "frame_id": "map",
        "cruise_speed_mps": speed, "start_delay_seconds": 0.0,
        "waypoints": [
            {"index": 0, "waypoint_id": "a", "x": 0.0, "y": 0.0, "z": 0.0},
            {"index": 1, "waypoint_id": "b", "x": 1.0, "y": 0.0, "z": 0.0},
        ],
    }


class GroundAirAdapterCoreTests(unittest.TestCase):
    def test_wait_for_next_pose_does_not_hold_callback_lock(self):
        adapter = self.make_adapter()
        pose = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(to_sec=lambda: 100.0)))
        timer = threading.Timer(0.03, lambda: adapter._local_pose_callback(pose))
        timer.start()
        try:
            adapter._wait_fresh_pose(timeout=0.3)
        finally:
            timer.join()
        adapter.local_pose = None
        with self.assertRaises(GroundAirAdapterError):
            adapter._wait_fresh_pose(timeout=0.03)
        adapter.stop_event.set()
        with self.assertRaises(GroundAirAdapterError):
            adapter._wait_fresh_pose(timeout=0.3)

    def make_adapter(self):
        ros = SimpleNamespace(Time=SimpleNamespace(now=lambda: SimpleNamespace(to_sec=lambda: 100.0)))
        adapter = GroundAirTaskAdapter(ros, {
            "prepare_ground_service": "/ground_air/prepare_ground",
            "pose_timeout_seconds": 2.0,
        }, object, object)
        adapter._feedback = Mock()
        return adapter

    def test_local_pose_requires_arrival_and_source_timestamp_freshness(self):
        adapter = self.make_adapter()
        with self.assertRaises(GroundAirAdapterError):
            adapter._require_fresh_pose()
        pose = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(to_sec=lambda: 99.5)))
        adapter._local_pose_callback(pose)
        adapter._require_fresh_pose()
        adapter.local_pose_received_at = time.monotonic() - 3
        with self.assertRaises(GroundAirAdapterError):
            adapter._require_fresh_pose()
        for stamp in (0.0, 97.0, 101.0, float("nan")):
            pose.header.stamp.to_sec = lambda stamp=stamp: stamp
            adapter._local_pose_callback(pose)
            with self.assertRaises(GroundAirAdapterError):
                adapter._require_fresh_pose()

    def test_cancelled_pose_wait_preserves_stop_and_subsequent_execution(self):
        for start_next in (False, True):
            with self.subTest(start_next=start_next):
                adapter = self.make_adapter()
                adapter.execution = {
                    "command": object(), "payload": payload(),
                    "scheduled_at": time.time() - 1.0,
                }
                stop_command = object()
                next_execution = {"command": object()} if start_next else None

                def cancelled_wait():
                    adapter._stop(stop_command)
                    if start_next:
                        adapter.execution = next_execution
                        adapter.stop_event.clear()
                    raise GroundAirAdapterError("task preparation was cancelled", "VEHICLE_NOT_READY")

                adapter._wait_fresh_pose = Mock(side_effect=cancelled_wait)
                adapter._run_scheduled()

                adapter._feedback.assert_called_once_with(
                    stop_command, "stopped", -1, 0.0, "ground mission stopped")
                self.assertIs(adapter.execution, next_execution)

    def test_native_rejections_are_preserved_without_retry_or_mode_change(self):
        adapter = self.make_adapter()
        for message, code in (
                ("ground navigation requires ground configuration", "GROUND_CONFIGURATION_REQUIRED"),
                ("local pose is stale", "LOCALIZATION_UNAVAILABLE")):
            service = Mock(return_value=SimpleNamespace(success=False, message=message))
            adapter._proxy = Mock(return_value=service)
            with self.assertRaises(GroundAirAdapterError) as error:
                adapter._call_trigger("/ground_air/prepare_ground")
            self.assertEqual(error.exception.error_code, code)
            self.assertIn(message, str(error.exception))
            service.assert_called_once_with()

    def test_terminal_feedback_resets_previous_waypoint_clock(self):
        adapter = self.make_adapter()
        for state in (4, 5, 6):
            adapter.execution = {"mission_id": "second", "command": object(), "waypoint_index": 1}
            adapter.waypoint_started_at = 1.0
            adapter._mission_status_callback(SimpleNamespace(
                mission_id="first", state=state, current_index=1, total_goals=2, detail="old"))
            self.assertIsNotNone(adapter.execution)
            adapter._mission_status_callback(SimpleNamespace(
                mission_id="second", state=state, current_index=1, total_goals=2, detail="done"))
            self.assertIsNone(adapter.execution)
            self.assertEqual(adapter.waypoint_started_at, 0.0)

    def test_native_fault_is_rejected_before_prepare_service(self):
        adapter = self.make_adapter()
        adapter._require_fresh_pose = Mock()
        adapter._lock_is_persisted = lambda: False
        adapter.vehicle_status_received_at = time.monotonic()
        adapter.vehicle_status = SimpleNamespace(emergency_stop=False, localized=True,
            connected=True, armed=True, flight_mode="OFFBOARD", mode=1, detail="ready")
        adapter._require_vehicle_ready()
        for mode in (0, 2, 3, 4, 5, 6):
            adapter.vehicle_status.mode = mode
            with self.assertRaises(GroundAirAdapterError) as error:
                adapter._require_vehicle_ready()
            self.assertEqual(error.exception.error_code, "GROUND_CONFIGURATION_REQUIRED")

    def test_accepts_ground_speed_at_limit(self):
        result = validate_ground_trajectory(
            payload(), "task-1", "sub-1", "AGV_001", "map", 0.1)
        self.assertEqual(result["task_type"], "ground")

    def test_rejects_air_task_and_overspeed(self):
        with self.assertRaisesRegex(GroundAirAdapterError, "ground tasks only") as error:
            validate_ground_trajectory(
                payload(task_type="air"), "task-1", "sub-1", "AGV_001", "map", 0.1)
        self.assertEqual(error.exception.error_code, "UNSUPPORTED_TASK_TYPE")
        with self.assertRaisesRegex(GroundAirAdapterError, "exceeds AGV limit") as error:
            validate_ground_trajectory(
                payload(speed=0.11), "task-1", "sub-1", "AGV_001", "map", 0.1)
        self.assertEqual(error.exception.error_code, "SPEED_LIMIT_EXCEEDED")

    def test_progress_and_error_mapping_are_stable(self):
        self.assertEqual(mission_progress("running", 1, 4), 0.25)
        self.assertEqual(mission_progress("completed", 1, 4), 1.0)
        self.assertEqual(
            feedback_error_code(RuntimeError("live map<-odom TF is unavailable")),
            "LOCALIZATION_UNAVAILABLE")

    def test_emergency_lock_is_atomic_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = GroundAirTaskAdapter(
                None, {"emergency_lock_file": os.path.join(directory, "estop.json")},
                object, object)
            adapter._persist_lock(True)
            self.assertTrue(adapter._lock_is_persisted())
            adapter._persist_lock(False)
            self.assertFalse(adapter._lock_is_persisted())


if __name__ == "__main__":
    unittest.main()
