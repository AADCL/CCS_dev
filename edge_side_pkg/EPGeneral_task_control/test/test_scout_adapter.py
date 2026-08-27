import json
import math
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from epgeneral_task_control.scout_adapter import (
    ScoutAdapterError, ScoutNavigationAdapter, execution_error_code, load_localized_map_state,
    move_base_failure, navigation_ready_deadline, validate_trajectory,
    validate_waypoints_on_navigation_map, waypoint_yaws,
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
        self.assertEqual(execution_error_code("navigation process exited"), "NAVIGATION_PROCESS_EXITED")

    def test_move_base_plan_failure_preserves_action_detail(self):
        error = move_base_failure(4, "Failed to find a valid plan.", 2)
        self.assertEqual(execution_error_code(error), "NAVIGATION_PLAN_FAILED")
        self.assertIn("action state 4", str(error))
        self.assertIn("Failed to find a valid plan", str(error))

    def test_navigation_map_rejects_unknown_and_outside_waypoints(self):
        payload = self.payload()
        payload["waypoints"] = [
            {"index": 0, "x": 0.5, "y": 0.5, "z": 0.0},
            {"index": 1, "x": 1.5, "y": 0.5, "z": 0.0},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "map.pgm"), "wb") as stream:
                stream.write(b"P5\n3 2\n255\n" + bytes([254, 205, 0, 254, 254, 254]))
            yaml_path = os.path.join(directory, "map.yaml")
            with open(yaml_path, "w") as stream:
                stream.write("image: map.pgm\nresolution: 1.0\norigin: [0.0, 0.0, 0.0]\n")
                stream.write("negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n")
            validate_waypoints_on_navigation_map(payload, yaml_path)
            payload["waypoints"][1]["y"] = 1.5
            with self.assertRaisesRegex(ScoutAdapterError, "not in known free space") as captured:
                validate_waypoints_on_navigation_map(payload, yaml_path)
            self.assertEqual(execution_error_code(captured.exception), "WAYPOINT_NOT_TRAVERSABLE")
            payload["waypoints"][1]["x"] = 4.0
            with self.assertRaisesRegex(ScoutAdapterError, "outside"):
                validate_waypoints_on_navigation_map(payload, yaml_path)

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

    def test_tf_lookup_error_becomes_localization_error(self):
        adapter = ScoutNavigationAdapter(None, {
            "pose_timeout_seconds": 2.0, "map_frame": "map",
        }, object, object)
        adapter.latest_odom = type("Odom", (), {
            "header": type("Header", (), {"frame_id": "odom"})(),
            "pose": type("PoseWithCovariance", (), {"pose": object()})(),
        })()
        adapter.latest_odom_at = time.monotonic()
        adapter.tf_buffer = type("Buffer", (), {
            "lookup_transform": lambda *_args: (_ for _ in ()).throw(RuntimeError("map frame missing")),
        })()
        adapter.rospy = type("Rospy", (), {
            "Time": lambda *_args: 0, "Duration": lambda *_args: 2.0,
        })()
        with self.assertRaisesRegex(ScoutAdapterError, "TF is unavailable") as captured:
            adapter._map_pose()
        self.assertEqual(execution_error_code(captured.exception), "LOCALIZATION_UNAVAILABLE")

    def test_not_localized_error_is_reported_as_localization_unavailable(self):
        error = ScoutAdapterError("Scout is not localized on a usable map")
        self.assertEqual(execution_error_code(error), "LOCALIZATION_UNAVAILABLE")

    def test_tf_listener_is_created_and_retained(self):
        buffer = object()
        listener = object()
        tf2_ros = type("Tf2Ros", (), {
            "Buffer": staticmethod(lambda **unused_kwargs: buffer),
            "TransformListener": staticmethod(lambda value: listener if value is buffer else None),
        })()
        converter = object()
        rospy = type("Rospy", (), {"Duration": staticmethod(lambda value: value)})()
        adapter = ScoutNavigationAdapter(rospy, {}, object, object)
        adapter._initialize_tf(tf2_ros, converter)
        self.assertIs(adapter.tf_buffer, buffer)
        self.assertIs(adapter.tf_listener, listener)
        self.assertIs(adapter.tf_converter, converter)

    def test_trajectory_store_round_trips_complete_payload(self):
        payload = self.payload()
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryStore(directory)
            saved = store.commit(payload, 123)
            loaded = store.load_payload("task-1", "sub-1")
            self.assertEqual(loaded["xml_path"], saved["xml_path"])
            self.assertEqual(loaded["waypoints"][2]["y"], 3.0)

    def test_execution_finish_and_stop_keep_navigation_process(self):
        class Process(object):
            def poll(self): return None

        class Client(object):
            def __init__(self): self.cancelled = False
            def cancel_all_goals(self): self.cancelled = True

        adapter = ScoutNavigationAdapter(None, {}, object, object)
        process = Process()
        client = Client()
        command = type("Command", (), {})()
        adapter.navigation_process = process
        adapter.client = client
        adapter.execution = {"command": command, "payload": self.payload(), "waypoint_index": 0}
        adapter._finish(command, "completed", 2, 1.0, "done")
        self.assertIs(adapter.navigation_process, process)
        self.assertIs(adapter.client, client)
        adapter.execution = {"command": command, "payload": self.payload(), "waypoint_index": 0}
        adapter._stop(command)
        self.assertTrue(client.cancelled)
        self.assertIs(adapter.navigation_process, process)

    def test_prepare_starts_navigation_once_and_schedule_reuses_it(self):
        class Process(object):
            pid = 123
            def poll(self): return None

        class Client(object):
            def __init__(self): self.cancelled = False
            def wait_for_server(self, unused_duration): return True
            def cancel_all_goals(self): self.cancelled = True

        class Publisher(object):
            def __init__(self): self.messages = []
            def publish(self, value): self.messages.append(value)

        class Feedback(object):
            def __init__(self):
                self.position = type("Point", (), {"x": 0.0, "y": 0.0, "z": 0.0})()

        class Rospy(object):
            @staticmethod
            def Duration(value): return value
            @staticmethod
            def loginfo(*unused_args): pass

        class Stamp(object):
            def __init__(self, value): self.value = value
            def to_sec(self): return self.value

        class CommandClass(object):
            SCHEDULE, CANCEL, STOP, PREPARE, UNLOAD = 1, 2, 3, 4, 5

        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryStore(directory)
            store.commit(self.payload(), 123)
            starts = []
            client = Client()
            config = {
                "storage_directory": directory, "device_id": "UGV_001", "map_frame": "map",
                "active_map_state_file": "state.json", "navigation_map_root": directory,
                "navigation_map_yaml": "map.yaml", "navigation_launch_package": "scout_navigation",
                "navigation_launch_file": "navigation_teb.launch", "odom_topic": "/fastlio_odom",
                "zero_velocity_topic": "/cmd_vel", "navigation_startup_timeout_seconds": 1.0,
                "waypoint_timeout_seconds": 10.0, "pose_timeout_seconds": 2.0,
                "zero_velocity_count": 10, "zero_velocity_hz": 20.0,
            }
            adapter = ScoutNavigationAdapter(
                Rospy(), config, CommandClass, Feedback,
                process_factory=lambda *args, **kwargs: starts.append((args, kwargs)) or Process(),
                action_client_factory=lambda: client,
            )
            adapter.feedback_pub = Publisher()
            adapter._map_pose = lambda: (0.0, 0.0, 0.0)
            command = type("Command", (), {
                "action": CommandClass.PREPARE, "request_id": "prepare", "task_id": "task-1",
                "subtask_id": "sub-1", "device_id": "UGV_001", "execution_id": "",
                "revision": 3, "map_id": "map-1", "scheduled_at": Stamp(0.0),
            })()
            with patch("epgeneral_task_control.scout_adapter.load_localized_map_state",
                       return_value={"map_id": "map-1"}), \
                    patch("epgeneral_task_control.scout_adapter.validate_waypoints_on_navigation_map"), \
                    patch("os.path.isfile", return_value=True), \
                    patch("epgeneral_task_control.scout_adapter.os.setsid", create=True):
                adapter._prepare(command)
                adapter.prepare_worker.join(timeout=2.0)
                self.assertEqual(adapter.feedback_pub.messages[-1].state, "ready")
                adapter._prepare(command)
            self.assertEqual(len(starts), 1)

            command.action = CommandClass.SCHEDULE
            command.execution_id = "exec-1"
            command.scheduled_at = Stamp(time.time() + 30.0)
            adapter._schedule(command)
            self.assertEqual(len(starts), 1)
            adapter._stop(command)
            self.assertTrue(client.cancelled)
            self.assertIsNotNone(adapter.navigation_process)


if __name__ == "__main__":
    unittest.main()
