import json
import math
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

import yaml


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from epgeneral_relocalization.artifacts import (  # noqa: E402
    ArtifactError, install_archive, validate_map_directory,
)
from epgeneral_relocalization.protocol import Protocol, ProtocolError  # noqa: E402
from epgeneral_relocalization.node import RelocalizationNode  # noqa: E402
from epgeneral_relocalization.config import ConfigError, load_config  # noqa: E402
from epgeneral_relocalization.ros_bridge import (  # noqa: E402
    RosBridge, angle_span, rostopic_has_subscriber, scoped_search_path,
)


def write_archive(path, map_id="map-1", malicious=False):
    pcd = (b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
           b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n0 0 0\n")
    pgm = b"P5\n1 1\n255\n\x00"
    map_yaml = (b"image: map.pgm\nresolution: 1.0\norigin: [0, 0, 0]\n"
                b"negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n")
    import hashlib
    values = {"public_map.pcd": pcd, "map.pgm": pgm, "map.yaml": map_yaml}
    files = {
        role: {"path": name, "byte_count": len(values[name]),
               "sha256": hashlib.sha256(values[name]).hexdigest()}
        for role, name in (("pcd", "public_map.pcd"), ("pgm", "map.pgm"), ("yaml", "map.yaml"))
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "schema_version": 1, "map_id": map_id, "files": files}))
        for name, data in values.items():
            archive.writestr(name, data)
        if malicious:
            archive.writestr("../escape", b"bad")


class ProtocolTests(unittest.TestCase):
    def test_round_trip_and_nan_rejection(self):
        protocol = Protocol("ccs-relocalization-v1", 1400)
        message = {
            "map_id": "map-1", "device_id": "UGV_001", "session_id": "s",
            "request_id": "r", "message_type": "initial_pose", "sequence": 1,
            "sent_at_ns": 2, "payload": {"x": 1.0},
        }
        self.assertEqual(protocol.decode(protocol.encode(message)), message)
        message["payload"]["x"] = math.nan
        with self.assertRaises(ProtocolError):
            protocol.encode(message)


class ArtifactTests(unittest.TestCase):
    def test_install_is_atomic_and_rejects_extra_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "map.zip")
            root = os.path.join(directory, "maps")
            write_archive(archive)
            target = install_archive(archive, root, "map-1", 1024 * 1024)
            self.assertTrue(validate_map_directory(target))
            write_archive(archive, malicious=True)
            with self.assertRaises(ArtifactError):
                install_archive(archive, root, "map-1", 1024 * 1024)
            self.assertTrue(validate_map_directory(target))

    def test_ground_air_install_renames_processed_pcd_after_wire_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "map.zip")
            root = os.path.join(directory, "maps")
            write_archive(archive)
            target = install_archive(
                archive, root, "map-1", 1024 * 1024, "cloud_map.pcd"
            )
            self.assertTrue(validate_map_directory(
                target, pcd_filename="cloud_map.pcd"
            ))
            self.assertTrue(os.path.isfile(os.path.join(target, "cloud_map.pcd")))
            self.assertFalse(os.path.exists(os.path.join(target, "public_map.pcd")))

    def test_map_id_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "map.zip")
            write_archive(archive, "../bad")
            with self.assertRaises(ArtifactError):
                install_archive(archive, os.path.join(directory, "maps"), "../bad", 1024 * 1024)


class TfMathTests(unittest.TestCase):
    def test_scoped_search_path_hides_only_the_selected_underlay(self):
        value = scoped_search_path(
            os.pathsep.join(("/edge/src", "/vehicle/src", "/opt/ros/share")),
            "/edge/overrides",
            ["/vehicle/src"],
        )
        self.assertEqual(value.split(os.pathsep), [
            os.path.abspath("/edge/overrides"),
            os.path.abspath("/edge/src"),
            os.path.abspath("/opt/ros/share"),
        ])

    def test_angle_span_handles_wraparound(self):
        span = angle_span([math.radians(179), math.radians(-179)])
        self.assertAlmostEqual(math.degrees(span), 2.0, places=6)

    def test_initial_pose_readiness_requires_a_real_subscriber(self):
        self.assertTrue(rostopic_has_subscriber(
            "Type: geometry_msgs/PoseWithCovarianceStamped\n\n"
            "Publishers:\n * /epgeneral_relocalization\n\n"
            "Subscribers:\n * /global_localization\n"
        ))
        self.assertFalse(rostopic_has_subscriber(
            "Type: geometry_msgs/PoseWithCovarianceStamped\n\n"
            "Publishers:\n * /epgeneral_relocalization\n\nSubscribers: None\n"
        ))

    def test_localization_health_gate_requires_a_fresh_true_sample(self):
        bridge = object.__new__(RosBridge)
        bridge.config = {"localization_health_timeout_seconds": 1.0}
        bridge._health_lock = threading.Lock()
        bridge._health_required = True
        bridge._health_ok = False
        bridge._health_updated_at = 0.0

        with mock.patch(
                "epgeneral_relocalization.ros_bridge.time.monotonic",
                return_value=10.0):
            bridge._health_callback(SimpleNamespace(data=True))
        with mock.patch(
                "epgeneral_relocalization.ros_bridge.time.monotonic",
                return_value=10.5):
            self.assertTrue(bridge._localization_health_ready())
        with mock.patch(
                "epgeneral_relocalization.ros_bridge.time.monotonic",
                return_value=11.1):
            self.assertFalse(bridge._localization_health_ready())
        bridge._reset_health_gate()
        self.assertFalse(bridge._localization_health_ready())

    def test_continuous_monitor_ignores_provisional_tf_until_health_is_true(self):
        class Stamp:
            def to_sec(self):
                return 100.0

        class FakeRospy:
            Time = type("Time", (), {
                "__init__": lambda self, _value: None,
                "now": staticmethod(lambda: Stamp()),
            })
            Duration = lambda self, value: value

            @staticmethod
            def is_shutdown():
                return False

        class Buffer:
            calls = 0

            def lookup_transform(self, *_unused):
                self.calls += 1
                return SimpleNamespace(
                    header=SimpleNamespace(stamp=Stamp()),
                    transform=SimpleNamespace(
                        translation=SimpleNamespace(x=0.0, y=0.0, z=0.0),
                        rotation=SimpleNamespace(
                            x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                )

        bridge = object.__new__(RosBridge)
        bridge.config = {
            "map_frame": "map", "odom_frame": "odom",
            "tf_timeout_seconds": 0.1, "tf_report_interval_seconds": 0.01,
        }
        bridge.rospy = FakeRospy()
        bridge.buffer = Buffer()
        bridge.logger = SimpleNamespace(warning=lambda *_unused: None)
        bridge._monitor_lock = threading.Lock()
        bridge._monitor_generation = 1
        health = iter((False, False, True))
        bridge._localization_health_ready = lambda: next(health, True)
        results = []

        def callback(success, transform, reason):
            results.append((success, transform, reason))
            bridge.cancel_monitor()

        bridge._monitor_continuous(callback, 1)
        self.assertEqual(bridge.buffer.calls, 1)
        self.assertTrue(results[0][0])

    def test_continuous_monitor_reports_changing_tf_without_stability_wait(self):
        class Stamp:
            def __init__(self, value):
                self.value = value

            def to_sec(self):
                return self.value

        class TimeValue(Stamp):
            @staticmethod
            def now():
                return Stamp(100.0)

        class FakeRospy:
            Time = TimeValue
            Duration = lambda self, value: value

            @staticmethod
            def is_shutdown():
                return False

        class Buffer:
            count = 0

            def lookup_transform(self, *_unused):
                self.count += 1
                return SimpleNamespace(
                    header=SimpleNamespace(stamp=Stamp(99.9 + self.count * 0.01)),
                    transform=SimpleNamespace(
                        translation=SimpleNamespace(
                            x=float(self.count), y=0.0, z=0.0
                        ),
                        rotation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                )

        class Logger:
            def warning(self, *_unused):
                pass

        bridge = object.__new__(RosBridge)
        bridge.config = {
            "map_frame": "map", "odom_frame": "odom",
            "tf_timeout_seconds": 0.1, "tf_report_interval_seconds": 0.01,
        }
        bridge.rospy = FakeRospy()
        bridge.buffer = Buffer()
        bridge.logger = Logger()
        bridge._monitor_lock = threading.Lock()
        bridge._monitor_generation = 1
        samples = []

        def callback(success, transform, reason):
            self.assertTrue(success, reason)
            samples.append(transform)
            if len(samples) == 3:
                bridge.cancel_monitor()

        bridge._monitor_continuous(callback, 1)
        self.assertEqual([item[0] for item in samples], [1.0, 2.0, 3.0])

    def test_continuous_monitor_fails_after_tf_timeout(self):
        class FakeTime:
            def __init__(self, _value):
                pass

            @staticmethod
            def now():
                return SimpleNamespace(to_sec=lambda: 100.0)

        class FakeRospy:
            Time = FakeTime
            Duration = lambda self, value: value

            @staticmethod
            def is_shutdown():
                return False

        class Logger:
            def warning(self, *_unused):
                pass

        bridge = object.__new__(RosBridge)
        bridge.config = {
            "map_frame": "map", "odom_frame": "odom",
            "tf_timeout_seconds": 0.03, "tf_report_interval_seconds": 0.01,
        }
        bridge.rospy = FakeRospy()
        bridge.buffer = SimpleNamespace(
            lookup_transform=lambda *_args: (_ for _ in ()).throw(RuntimeError("missing"))
        )
        bridge.logger = Logger()
        bridge._monitor_lock = threading.Lock()
        bridge._monitor_generation = 1
        results = []
        bridge._monitor_continuous(
            lambda success, transform, reason: results.append(
                (success, transform, reason)
            ),
            1,
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertIn("unavailable", results[0][2])

    def test_continuous_monitor_reuses_last_sample_when_tf_stops_updating(self):
        class Stamp:
            def __init__(self, value):
                self.value = value

            def to_sec(self):
                return self.value

        class TimeValue(Stamp):
            @staticmethod
            def now():
                return Stamp(100.0)

        class FakeRospy:
            Time = TimeValue
            Duration = lambda self, value: value

            @staticmethod
            def is_shutdown():
                return False

        class Buffer:
            count = 0

            def lookup_transform(self, *_unused):
                self.count += 1
                if self.count > 1:
                    raise RuntimeError("publisher idle")
                return SimpleNamespace(
                    header=SimpleNamespace(stamp=Stamp(100.0)),
                    transform=SimpleNamespace(
                        translation=SimpleNamespace(x=4.0, y=5.0, z=0.0),
                        rotation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                )

        class Logger:
            def warning(self, *_unused):
                pass

        bridge = object.__new__(RosBridge)
        bridge.config = {
            "map_frame": "map", "odom_frame": "odom",
            "tf_timeout_seconds": 0.03, "tf_report_interval_seconds": 0.01,
        }
        bridge.rospy = FakeRospy()
        bridge.buffer = Buffer()
        bridge.logger = Logger()
        bridge._monitor_lock = threading.Lock()
        bridge._monitor_generation = 1
        samples = []

        def callback(success, transform, reason):
            self.assertTrue(success, reason)
            samples.append(transform)
            if len(samples) == 3:
                bridge.cancel_monitor()

        bridge._monitor_continuous(callback, 1)
        self.assertEqual(samples, [
            (4.0, 5.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            (4.0, 5.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            (4.0, 5.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ])

    def test_ground_air_profile_enables_one_second_continuous_reporting(self):
        profile = os.environ.get(
            "CCS_GROUND_AIR_PROFILE_CONFIG",
            os.path.join(ROOT, "..", "deploy", "ground_air_agv", "config"),
        )
        config = load_config(
            os.path.join(profile, "relocalization.yaml"),
            os.path.join(profile, "device.yaml"),
        )
        self.assertEqual(config["backend"], "ground_air_agv")
        self.assertEqual(config["pcd_filename"], "cloud_map.pcd")
        self.assertTrue(config["tf_continuous_reporting"])
        self.assertEqual(config["tf_report_interval_seconds"], 1.0)

    def test_go2_template_declares_health_gate_but_remains_disabled(self):
        profile = os.path.join(ROOT, "..", "deploy", "go2_edu", "config")
        config = load_config(
            os.path.join(profile, "relocalization.yaml"),
            os.path.join(profile, "device.yaml"),
        )
        self.assertEqual(config["backend"], "go2_edu")
        self.assertFalse(config["enabled"])
        self.assertEqual(
            config["localization_health_topic"], "/localization/ok")
        self.assertEqual(config["localization_health_timeout_seconds"], 2.0)

    def test_enabled_go2_requires_at_least_one_navigation_stage(self):
        profile = os.path.join(ROOT, "..", "deploy", "go2_edu", "config")
        with open(os.path.join(profile, "relocalization.yaml"), "r") as stream:
            payload = yaml.safe_load(stream)
        payload["enabled"] = True
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "relocalization.yaml")
            with open(config_path, "w") as stream:
                yaml.safe_dump(payload, stream)
            with self.assertRaisesRegex(ConfigError, "stages are empty"):
                load_config(
                    config_path, os.path.join(profile, "device.yaml"))


class NodeIdempotencyTests(unittest.TestCase):
    def test_active_map_state_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            node = object.__new__(RelocalizationNode)
            node.config = {"active_map_state_file": os.path.join(directory, "state", "active.json")}
            node._write_active_map("map-1")
            with open(node.config["active_map_state_file"], "r") as stream:
                state = json.load(stream)
            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(state["map_id"], "map-1")
            self.assertNotIn("map_from_odom", state)

    def test_localized_state_round_trips_and_nonlocalized_state_clears_tf(self):
        with tempfile.TemporaryDirectory() as directory:
            node = object.__new__(RelocalizationNode)
            node.config = {
                "active_map_state_file": os.path.join(directory, "active.json"),
                "map_frame": "map", "odom_frame": "odom",
            }
            transform = dict(zip(
                ("x", "y", "z", "qx", "qy", "qz", "qw"),
                (1.0, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            ))
            node._write_active_state("map-1", "localized", transform)
            self.assertEqual(node._read_active_state()["map_from_odom"], transform)
            node._write_active_state("map-1", "starting")
            self.assertNotIn("map_from_odom", node._read_active_state())

    def test_schema_one_state_is_read_for_startup_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "active.json")
            with open(path, "w") as stream:
                json.dump({"schema_version": 1, "map_id": "map-1"}, stream)
            node = object.__new__(RelocalizationNode)
            node.config = {"active_map_state_file": path}
            self.assertEqual(node._read_active_state()["status"], "standby")

    def test_localized_state_is_invalidated_after_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            node = object.__new__(RelocalizationNode)
            node.config = {
                "active_map_state_file": os.path.join(directory, "active.json"),
                "map_frame": "map", "odom_frame": "odom",
            }
            transform = dict(zip(
                ("x", "y", "z", "qx", "qy", "qz", "qw"),
                (1.0, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            ))
            node._write_active_state("map-1", "localized", transform)
            node.persisted_state = node._read_active_state()
            node._invalidate_persisted_localization()
            state = node._read_active_state()
            self.assertEqual(state["status"], "standby")
            self.assertNotIn("map_from_odom", state)

    def test_initial_pose_caches_relocalizing_progress_before_tf_result(self):
        class FakeRos(object):
            def publish_and_monitor(self, *args):
                self.args = args

        node = object.__new__(RelocalizationNode)
        node.config = {"enabled": True, "backend": "scout_mini"}
        node.state = "awaiting_pose"
        node.ros = FakeRos()
        node.operation_generation = 3
        node.response_cache = {}
        node._send = lambda *unused: None
        node._write_active_state = lambda *unused, **kwargs: None
        message = {
            "map_id": "map-1", "device_id": "UGV_001", "session_id": "session",
            "request_id": "request", "payload": {"x": 1.0, "y": 2.0, "yaw": 0.5},
        }
        node._initial_pose(message)
        self.assertEqual(node.state, "relocalizing")
        self.assertEqual(
            node.response_cache["request"],
            ("relocalization_result", {"state": "relocalizing"}),
        )

    def test_ground_air_initial_pose_starts_tf_monitor(self):
        class FakeRos(object):
            def publish_and_monitor(self, *args):
                self.args = args

        node = object.__new__(RelocalizationNode)
        node.config = {"enabled": True, "backend": "ground_air_agv"}
        node.state = "awaiting_pose"
        node.ros = FakeRos()
        node.operation_generation = 4
        node.response_cache = {}
        node._send = lambda *unused: None
        node._write_active_state = lambda *unused, **kwargs: None
        message = {
            "map_id": "test60", "device_id": "AGV_001", "session_id": "session",
            "request_id": "request", "payload": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        }
        node._initial_pose(message)
        self.assertEqual(node.state, "relocalizing")
        self.assertEqual(node.ros.args[:3], (0.0, 0.0, 0.0))

    def test_enabled_go2_initial_pose_starts_tf_monitor(self):
        class FakeRos(object):
            def publish_and_monitor(self, *args):
                self.args = args

        node = object.__new__(RelocalizationNode)
        node.config = {"enabled": True, "backend": "go2_edu"}
        node.state = "awaiting_pose"
        node.ros = FakeRos()
        node.operation_generation = 5
        node.response_cache = {}
        node._send = lambda *unused: None
        node._write_active_state = lambda *unused, **kwargs: None
        message = {
            "map_id": "map-1", "device_id": "QRD_002",
            "session_id": "session", "request_id": "request",
            "payload": {"x": 1.0, "y": 2.0, "yaw": 0.5},
        }
        node._initial_pose(message)
        self.assertEqual(node.state, "relocalizing")
        self.assertEqual(node.ros.args[:3], (1.0, 2.0, 0.5))

    def test_enabled_go2_rejects_initial_pose_until_stack_is_restarted(self):
        node = object.__new__(RelocalizationNode)
        node.config = {"enabled": True, "backend": "go2_edu"}
        node.state = "localized"
        node.ros = SimpleNamespace(
            publish_and_monitor=lambda *_unused: self.fail(
                "stale Go2 stack must not accept a new pose"))
        replies = []
        node._reply = lambda *args: replies.append(args)
        node._write_active_state = lambda *unused, **kwargs: None
        node._initial_pose({
            "map_id": "map-1", "device_id": "QRD_002",
            "session_id": "session", "request_id": "request",
            "payload": {"x": 1.0, "y": 2.0, "yaw": 0.5},
        })
        self.assertEqual(
            replies[-1][2]["reason"], "STACK_NOT_READY")

    def test_enabled_go2_negotiates_map_required(self):
        with tempfile.TemporaryDirectory() as directory:
            node = object.__new__(RelocalizationNode)
            node.config = {
                "enabled": True, "backend": "go2_edu",
                "map_root": directory, "pcd_filename": "public_map.pcd",
            }
            node.lock = threading.RLock()
            node.identity = None
            node.operation_generation = 0
            node.ros = None
            node.stack = SimpleNamespace(stop=lambda: None)
            writes, replies = [], []
            node._write_active_state = lambda *args: writes.append(args)
            node._reply = lambda *args: replies.append(args)
            message = {
                "map_id": "map-1", "device_id": "QRD_002",
                "session_id": "session", "request_id": "request",
                "payload": {},
            }
            node._negotiate(message)
            self.assertEqual(node.state, "map_required")
            self.assertEqual(writes[-1], ("map-1", "map_required"))
            self.assertEqual(replies[-1][1:], (
                "negotiation_status", {"state": "map_required"}))

    def test_repeat_start_reuses_running_stack_and_waits_for_new_pose(self):
        class FakeStack(object):
            def is_running(self):
                return True

            def start(self, *_unused):
                self.fail("running stack must not be restarted")

        class FakeRos(object):
            cancelled = False

            def cancel_monitor(self):
                self.cancelled = True

        node = object.__new__(RelocalizationNode)
        node.config = {"enabled": True, "backend": "ground_air_agv"}
        node.state = "localized"
        node.stack = FakeStack()
        node.ros = FakeRos()
        writes, replies = [], []
        node._write_active_state = lambda *args: writes.append(args)
        node._reply = lambda *args: replies.append(args)
        message = {"map_id": "test60", "payload": {"replace_existing": True}}
        node._start_stack(message)
        self.assertEqual(node.state, "awaiting_pose")
        self.assertTrue(node.ros.cancelled)
        self.assertEqual(writes, [("test60", "awaiting_pose")])
        self.assertEqual(replies[-1][1:], (
            "stack_status", {"state": "awaiting_pose"}
        ))

    def test_disabled_go2_still_rejects_stack_and_clears_persisted_transform(self):
        node = object.__new__(RelocalizationNode)
        node.config = {"enabled": False, "backend": "go2_edu"}
        writes, replies = [], []
        node._write_active_state = lambda *args: writes.append(args)
        node._reply = lambda *args: replies.append(args)
        message = {"map_id": "map-1", "payload": {"replace_existing": True}}
        node._start_stack(message)
        self.assertEqual(writes[-1], ("map-1", "unsupported"))
        self.assertEqual(replies[-1][2]["reason"], "UNSUPPORTED_BACKEND")

    def test_go2_replacement_restarts_stack_before_waiting_for_pose(self):
        class FakeStack(object):
            def __init__(self):
                self.started = []

            def is_running(self):
                return True

            def start(self, *args):
                self.started.append(args)

        class FakeRos(object):
            def cancel_monitor(self):
                self.cancelled = True

        class ImmediateThread(object):
            def __init__(self, target, args, name):
                self.target, self.args, self.name = target, args, name
                self.daemon = False

            def start(self):
                self.target(*self.args)

        node = object.__new__(RelocalizationNode)
        node.config = {
            "enabled": True, "backend": "go2_edu",
            "pcd_filename": "public_map.pcd",
        }
        node.state = "localized"
        node.map_dir = "/maps/map-1"
        node.stack = FakeStack()
        node.ros = FakeRos()
        node.operation_generation = 2
        writes, replies = [], []
        node._write_active_state = lambda *args: writes.append(args)
        node._reply = lambda *args: replies.append(args)
        message = {"map_id": "map-1", "payload": {"replace_existing": True}}
        with mock.patch(
                "epgeneral_relocalization.node.validate_map_directory"):
            with mock.patch(
                    "epgeneral_relocalization.node.threading.Thread",
                    ImmediateThread):
                node._start_stack(message)
        self.assertEqual(node.stack.started, [("map-1", "/maps/map-1")])
        self.assertEqual(node.state, "awaiting_pose")
        self.assertEqual(replies[-1][1:], (
            "stack_status", {"state": "awaiting_pose"}))

    def test_stale_tf_result_is_ignored(self):
        class Logger(object):
            def info(self, *unused):
                pass

        node = object.__new__(RelocalizationNode)
        node.operation_generation = 2
        node.logger = Logger()
        node._reply = lambda *unused: self.fail("stale result replied")
        node._tf_result({}, 1, True, (0, 0, 0, 0, 0, 0, 1), "")

    def test_ros_monitor_generation_cancels_previous_attempt(self):
        bridge = object.__new__(RosBridge)
        bridge._monitor_lock = __import__("threading").Lock()
        bridge._monitor_generation = 0
        first = bridge._next_monitor_generation()
        bridge.cancel_monitor()
        self.assertFalse(bridge._monitor_is_current(first))

    def test_continuous_tf_persists_first_sample_and_reports_every_sample(self):
        node = object.__new__(RelocalizationNode)
        node.operation_generation = 1
        node.state = "relocalizing"
        node.config = {
            "map_frame": "map", "odom_frame": "odom",
            "tf_continuous_reporting": True,
            "tf_persist_interval_seconds": 30.0,
        }
        node._latest_tf = None
        node._latest_tf_map_id = ""
        node._last_tf_persisted_at = 0.0
        node._tf_dirty = False
        writes, replies = [], []
        node._write_active_state = lambda *args: writes.append(args)
        node._reply = lambda *args: replies.append(args)
        message = {"map_id": "test60"}
        node._tf_result(message, 1, True, (1, 0, 0, 0, 0, 0, 1), "")
        node._tf_result(message, 1, True, (2, 0, 0, 0, 0, 0, 1), "")
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[-1][2]["map_from_odom"]["x"], 2)


if __name__ == "__main__":
    unittest.main()
