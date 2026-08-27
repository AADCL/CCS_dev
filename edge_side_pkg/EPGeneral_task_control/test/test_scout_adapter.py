import json
import math
import os
import tempfile
import unittest

from epgeneral_task_control.scout_adapter import (
    ScoutAdapterError, execution_error_code, load_localized_map_state,
    navigation_ready_deadline, validate_trajectory, waypoint_yaws,
)
from epgeneral_task_control.storage import TrajectoryStore


class ScoutAdapterCoreTests(unittest.TestCase):
    def payload(self):
        return {
            "schema_version": 2, "task_id": "task-1", "subtask_id": "sub-1",
            "device_id": "UGV_001", "revision": 3, "task_name": "巡检",
            "map_id": "map-1", "frame_id": "map", "cruise_speed_mps": 1.0,
            "start_delay_seconds": 0.0,
            "waypoints": [
                {"index": 0, "waypoint_id": "a", "x": 1.0, "y": 1.0, "z": 4.0},
                {"index": 1, "waypoint_id": "b", "x": 2.0, "y": 1.0, "z": 2.0},
                {"index": 2, "waypoint_id": "c", "x": 2.0, "y": 3.0, "z": 8.0},
            ],
        }

    def test_waypoint_yaw_uses_current_pose_then_previous_point(self):
        yaws = waypoint_yaws(self.payload()["waypoints"], 0.0, 0.0)
        self.assertAlmostEqual(yaws[0], math.pi / 4.0)
        self.assertAlmostEqual(yaws[1], 0.0)
        self.assertAlmostEqual(yaws[2], math.pi / 2.0)

    def test_navigation_ready_deadline_never_passes_group_start(self):
        self.assertEqual(navigation_ready_deadline(100.0, 130.0, 25.0), 125.0)
        self.assertEqual(navigation_ready_deadline(100.0, 110.0, 25.0), 110.0)

    def test_execution_errors_have_actionable_codes(self):
        self.assertEqual(
            execution_error_code("/fastlio_odom has not published a pose"),
            "LOCALIZATION_UNAVAILABLE",
        )
        self.assertEqual(
            execution_error_code("move_base action server did not become ready"),
            "NAVIGATION_STARTUP_TIMEOUT",
        )
        self.assertEqual(
            execution_error_code("localized map does not match task map"),
            "MAP_FRAME_MISMATCH",
        )

    def test_trajectory_is_validated_against_execution_identity(self):
        payload = validate_trajectory(self.payload(), "task-1", "sub-1", "UGV_001", "map")
        self.assertEqual(len(payload["waypoints"]), 3)
        with self.assertRaises(ScoutAdapterError):
            validate_trajectory(payload, "task-1", "sub-1", "UGV_001", "odom")
        payload["waypoints"][1]["index"] = 4
        with self.assertRaises(ScoutAdapterError):
            validate_trajectory(payload, "task-1", "sub-1", "UGV_001", "map")

    def test_localized_map_state_requires_map_from_odom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "relocalization.json")
            value = {"schema_version": 2, "map_id": "map-1", "status": "localized",
                     "map_from_odom": {key: 0.0 for key in ("x", "y", "z", "qx", "qy", "qz")}}
            value["map_from_odom"]["qw"] = 1.0
            with open(path, "w") as stream:
                json.dump(value, stream)
            self.assertEqual(load_localized_map_state(path)["map_id"], "map-1")
            value["status"] = "map_ready"
            with open(path, "w") as stream:
                json.dump(value, stream)
            with self.assertRaises(ScoutAdapterError):
                load_localized_map_state(path)

    def test_trajectory_store_round_trips_complete_payload(self):
        payload = self.payload()
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryStore(directory)
            saved = store.commit(payload, 123)
            loaded = store.load_payload("task-1", "sub-1")
            self.assertEqual(loaded["xml_path"], saved["xml_path"])
            self.assertEqual(loaded["waypoints"][2]["y"], 3.0)


if __name__ == "__main__":
    unittest.main()
