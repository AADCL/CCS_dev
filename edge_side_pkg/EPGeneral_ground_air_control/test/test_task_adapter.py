import os
import tempfile
import unittest

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
