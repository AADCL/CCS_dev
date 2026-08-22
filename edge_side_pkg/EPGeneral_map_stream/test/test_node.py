import io
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import msgpack
import numpy as np
import yaml

from epgeneral_map_stream.artifacts import ArtifactError
from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.node import RosMapStreamNode
from epgeneral_map_stream.protocol import encode_envelope

try:
    from .test_paths import device_config_path
except ImportError:
    from test_paths import device_config_path


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = device_config_path(PACKAGE)


class FakeSocket(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, destination):
        self.sent.append((data, destination))

    def close(self):
        pass


class FakeSubscriber(object):
    def __init__(self):
        self.unregistered = False

    def unregister(self):
        self.unregistered = True


class FakeRospy(object):
    def __init__(self, config, events):
        self.config = config
        self.events = events
        self.logs = []

    def get_published_topics(self):
        return [(self.config["input_cloud_topic"], self.config["input_cloud_message_type"]),
                (self.config["input_imu_topic"], self.config["input_imu_message_type"]),
                (self.config["cloud_topic"], self.config["cloud_message_type"]),
                (self.config["pose_topic"], self.config["pose_message_type"])]

    def wait_for_message(self, topic, unused_class, timeout=None):
        self.events.append("probe:%s" % topic)
        stamp = SimpleNamespace(to_nsec=lambda: 123456789)
        header = SimpleNamespace(stamp=stamp, frame_id=self.config["input_imu_frame"])
        vector = SimpleNamespace(x=0.0, y=0.0, z=0.0)
        if topic == self.config["input_imu_topic"]:
            return SimpleNamespace(
                header=header,
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                angular_velocity=vector, linear_acceleration=vector)
        if topic == self.config["pose_topic"]:
            return SimpleNamespace(
                header=SimpleNamespace(stamp=stamp, frame_id=self.config["map_frame"]),
                child_frame_id=self.config["body_frame"],
                pose=SimpleNamespace(pose=SimpleNamespace(
                    position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))))
        frame = (self.config["input_cloud_frame"]
                 if topic == self.config["input_cloud_topic"] else self.config["cloud_frame"])
        return SimpleNamespace(header=SimpleNamespace(stamp=stamp, frame_id=frame), fields=[])

    def Subscriber(self, unused_topic, unused_class, unused_callback, **unused_kwargs):
        self.events.append("subscribe:%s" % unused_topic)
        return FakeSubscriber()

    def loginfo(self, message, *args):
        self.logs.append(("INFO", message % args if args else message))

    def logwarn(self, message, *args):
        self.logs.append(("WARN", message % args if args else message))

    def logwarn_throttle(self, unused_seconds, unused_message):
        pass

    def logerr(self, message, *args):
        self.logs.append(("ERROR", message % args if args else message))


class FakeRunner(object):
    def __init__(self, events):
        self.events = events

    def check(self, commands):
        self.events.append("check_integrations")
        self.assert_command_arrays(commands)

    @staticmethod
    def assert_command_arrays(commands):
        if not all(isinstance(item, list) for item in commands.values()):
            raise RuntimeError("commands must be argument arrays")

    def run(self, arguments, timeout=None):
        name = os.path.basename(arguments[0])
        self.events.append("run:%s" % name)
        if name == "stop_fast_lio.sh":
            content = ("VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
                       "WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n1 0 0\n")
            for path in (arguments[3], arguments[4]):
                with io.open(path, "w", encoding="ascii") as stream:
                    stream.write(content)
            fresh_ns = time.time_ns() + 1_000_000_000
            os.utime(arguments[3], ns=(fresh_ns, fresh_ns))
        elif name == "generate_pgm.sh":
            with io.open(arguments[5], "wb") as stream:
                stream.write(b"P5\n2 2\n255\n" + bytes((0, 254, 205, 254)))
            with io.open(arguments[6], "w", encoding="utf-8") as stream:
                yaml.safe_dump({
                    "image": "map.pgm", "resolution": 0.1,
                    "origin": [0.0, 0.0, 0.0], "negate": 0,
                    "occupied_thresh": 0.65, "free_thresh": 0.196,
                }, stream)
        return "ok"


class FakeArtifactServer(object):
    port = 14600

    def __init__(self):
        self.unregistered = []

    def register(self, unused_path, unused_ttl, **unused_kwargs):
        return "token", "2030-01-01T00:00:00+00:00"

    def unregister(self, token, delete=False):
        self.unregistered.append((token, delete))

    def cleanup(self):
        pass

    def close(self):
        pass


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = dict(load_config(MAPPING, DEVICE))
        self.config.update(
            workspace_root=self.temp.name, min_free_bytes=1,
            generated_pcd_path=os.path.join(self.temp.name, "generated.pcd"),
            source_pcd_path=os.path.join(self.temp.name, "source.pcd"),
            artifact_poll_seconds=0.001, artifact_stable_polls=2,
            artifact_generation_timeout_seconds=1.0,
        )
        with io.open(self.config["source_pcd_path"], "w", encoding="ascii") as stream:
            stream.write("old source PCD")
        self.clock_value = [10.0]
        self.events = []
        self.runner = FakeRunner(self.events)
        self.node = RosMapStreamNode(
            FakeRospy(self.config, self.events), self.config,
            clock=lambda: self.clock_value[0], message_resolver=lambda unused: object,
            command_runner=self.runner, artifact_server=FakeArtifactServer())
        self.node.socket = FakeSocket()
        self.identity = {"map_id": "map-1", "session_id": "a" * 32}

    def command(self, message_type, payload, sequence=0):
        return encode_envelope(self.config, self.identity, message_type, sequence, payload)

    def messages(self):
        return [msgpack.unpackb(item[0], raw=False) for item in self.node.socket.sent]

    def prepare(self):
        self.node.handle_datagram(self.command("prepare_mapping", {
            "request_id": "prepare-1", "return_host": self.config["ground_station_ip"],
            "return_port": self.config["data_port"],
            "required_inputs": ["pointcloud", "imu", "artifact_storage", "map_generation"],
        }), self.config["ground_station_ip"])

    def test_ros_messages_are_mirrored_to_dedicated_log(self):
        path = os.path.join(self.temp.name, "logs", "map_stream.log")
        self.node.log_path = path
        self.node._log_info("mapping test session=%s", "abc123")
        with io.open(path, "r", encoding="utf-8") as stream:
            content = stream.read()
        self.assertIn("INFO  mapping test session=abc123", content)
        self.assertIn(("INFO", "mapping test session=abc123"), self.node.rospy.logs)

    def start(self):
        self.node.handle_datagram(self.command("start_mapping", {
            "request_id": "start-1",
            "coordinate_contract": "sensor+map_body+body_sensor",
        }), self.config["ground_station_ip"])

    def test_prepare_success_and_duplicate_are_idempotent(self):
        self.prepare()
        self.assertEqual(self.node.state, "ready")
        result = self.messages()[-1]
        self.assertEqual(result["message_type"], "prepare_result")
        self.assertTrue(result["payload"]["accepted"])
        first_count = len(self.node.socket.sent)
        self.prepare()
        self.assertEqual(len(self.node.socket.sent), first_count + 1)
        self.assertEqual(self.node.state, "ready")

    def test_prepare_failure_returns_all_checks_and_allows_retry(self):
        self.node.rospy.get_published_topics = lambda: []
        self.prepare()
        payload = self.messages()[-1]["payload"]
        self.assertFalse(payload["accepted"])
        self.assertEqual(len(payload["checks"]), 4)
        self.assertEqual(self.node.state, "standby")

    def test_start_requires_prepare_and_input_timeout_cleans_session(self):
        self.start()
        self.assertFalse(self.messages()[-1]["payload"]["accepted"])
        self.prepare()
        self.node.handle_datagram(self.command("start_mapping", {
            "request_id": "start-2",
            "coordinate_contract": "sensor+map_body+body_sensor",
        }), self.config["ground_station_ip"])
        self.assertEqual(self.node.state, "mapping")
        self.clock_value[0] += self.config["input_timeout_seconds"] + 0.1
        self.node._watchdog()
        self.assertEqual(self.node.state, "standby")
        self.assertEqual(self.messages()[-1]["payload"]["error_code"], "SENSOR_UNAVAILABLE")

    def test_start_runs_fast_lio_and_probes_outputs_before_subscribing(self):
        self.prepare()
        self.start()
        start = self.events.index("run:start_fast_lio.sh")
        cloud_probe = self.events.index("probe:%s" % self.config["cloud_topic"])
        pose_probe = self.events.index("probe:%s" % self.config["pose_topic"])
        subscribe = min(index for index, event in enumerate(self.events)
                        if event.startswith("subscribe:"))
        self.assertLess(start, cloud_probe)
        self.assertLess(cloud_probe, pose_probe)
        self.assertLess(pose_probe, subscribe)
        messages = self.messages()
        starting = next(index for index, item in enumerate(messages)
                        if item["message_type"] == "session_status"
                        and item["payload"]["state"] == "starting")
        ack = next(index for index, item in enumerate(messages)
                   if item["message_type"] == "command_ack"
                   and item["payload"]["command"] == "start_mapping")
        self.assertLess(starting, ack)

    def test_start_output_failure_stops_fast_lio_and_rejects_command(self):
        self.prepare()
        original = self.node.rospy.wait_for_message

        def fail_fast_output(topic, message_class, timeout=None):
            if topic == self.config["cloud_topic"]:
                raise RuntimeError("FAST_LIO cloud timeout")
            return original(topic, message_class, timeout)

        self.node.rospy.wait_for_message = fail_fast_output
        self.start()
        self.assertEqual(self.node.state, "standby")
        self.assertIn("run:start_fast_lio.sh", self.events)
        self.assertIn("run:abort_fast_lio.sh", self.events)
        ack = self.messages()[-1]
        self.assertEqual(ack["message_type"], "command_ack")
        self.assertFalse(ack["payload"]["accepted"])

    def test_prepare_accepts_imu_without_integrity_validation(self):
        original = self.node.rospy.wait_for_message

        def invalid_imu(topic, message_class, timeout=None):
            message = original(topic, message_class, timeout)
            if topic == self.config["input_imu_topic"]:
                message.angular_velocity.x = float("nan")
            return message

        self.node.rospy.wait_for_message = invalid_imu
        self.prepare()
        payload = self.messages()[-1]["payload"]
        self.assertTrue(payload["accepted"])
        imu = next(item for item in payload["checks"] if item["name"] == "imu")
        self.assertTrue(imu["available"])

    def test_sampling_window_is_queued_for_pcd_worker(self):
        self.prepare()
        self.start()
        session = self.node.session
        stamp = SimpleNamespace(to_nsec=lambda: 123456789)
        pose = SimpleNamespace(
            header=SimpleNamespace(stamp=stamp, frame_id=self.config["map_frame"]),
            child_frame_id=self.config["body_frame"],
            pose=SimpleNamespace(pose=SimpleNamespace(
                position=SimpleNamespace(x=1.0, y=2.0, z=3.0),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))))
        cloud = SimpleNamespace(
            header=SimpleNamespace(stamp=stamp, frame_id=self.config["cloud_frame"]), fields=[])
        self.node._pose_callback(session.token, pose)
        with patch("epgeneral_map_stream.node.extract_pointcloud2",
                   return_value=np.asarray([[1, 0, 0], [2, 0, 0]], dtype=np.float32)):
            self.node._cloud_callback(session.token, cloud)
            self.clock_value[0] += self.config["sample_window_seconds"] + 0.01
            self.node._cloud_callback(session.token, cloud)
        queued_session, queued_token, scans = self.node.preview_queue.get_nowait()
        self.assertIs(queued_session, session)
        self.assertEqual(queued_token, session.token)
        self.assertEqual(len(scans), 2)

    def test_cloud_waits_for_matching_pose_when_callback_arrives_first(self):
        self.prepare()
        self.start()
        session = self.node.session
        stamp = SimpleNamespace(to_nsec=lambda: 123456789)
        pose = SimpleNamespace(
            header=SimpleNamespace(stamp=stamp, frame_id=self.config["map_frame"]),
            child_frame_id=self.config["body_frame"],
            pose=SimpleNamespace(pose=SimpleNamespace(
                position=SimpleNamespace(x=1.0, y=2.0, z=3.0),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))))
        cloud = SimpleNamespace(
            header=SimpleNamespace(stamp=stamp, frame_id=self.config["cloud_frame"]), fields=[])
        with patch("epgeneral_map_stream.node.extract_pointcloud2",
                   return_value=np.asarray([[1, 0, 0], [2, 0, 0]], dtype=np.float32)):
            self.node._cloud_callback(session.token, cloud)
            self.assertEqual(len(session.pending_clouds), 1)
            self.assertEqual(session.window_points, 0)
            self.node._pose_callback(session.token, pose)
        self.assertEqual(session.pending_clouds, [])
        self.assertEqual(session.window_points, 2)

    def test_stop_ack_precedes_generating_and_ready(self):
        self.prepare()
        self.start()
        self.node.handle_datagram(self.command("stop_mapping", {
            "request_id": "stop-1", "reason": "done",
        }), self.config["ground_station_ip"])
        self.node.generation_thread.join(timeout=2.0)
        messages = self.messages()
        stop_index = next(index for index, item in enumerate(messages)
                          if item["message_type"] == "command_ack"
                          and item["payload"]["command"] == "stop_mapping")
        generating_index = next(index for index, item in enumerate(messages)
                                if item["message_type"] == "artifact_status"
                                and item["payload"]["state"] == "generating")
        ready = next(item for item in messages if item["message_type"] == "artifact_status"
                     and item["payload"]["state"] == "ready")
        self.assertLess(stop_index, generating_index)
        self.assertIn("token=token", ready["payload"]["url"])
        self.assertLess(self.events.index("run:stop_fast_lio.sh"),
                        self.events.index("run:generate_pgm.sh"))
        self.assertEqual(self.node.state, "standby")

    def test_abort_stops_fast_lio_without_generating_artifacts(self):
        self.prepare()
        self.start()
        old_subscribers = list(self.node.session.subscribers)
        self.node.handle_datagram(self.command("abort_mapping", {
            "request_id": "abort-1", "reason": "operator requested abort",
        }), self.config["ground_station_ip"])
        message = self.messages()[-1]
        self.assertEqual(message["message_type"], "command_ack")
        self.assertEqual(message["payload"]["command"], "abort_mapping")
        self.assertTrue(message["payload"]["accepted"])
        self.assertIn("run:abort_fast_lio.sh", self.events)
        self.assertNotIn("run:stop_fast_lio.sh", self.events)
        self.assertNotIn("run:generate_pgm.sh", self.events)
        self.assertTrue(all(item.unregistered for item in old_subscribers))
        self.assertEqual(self.node.state, "standby")

    def test_preview_fragment_ack_removes_cache_entry(self):
        self.prepare()
        self.start()
        session = self.node.session
        session.fragment_cache[7] = {"token": "fragment-token"}
        self.node.handle_datagram(self.command("cloud_fragment_ack", {
            "request_id": "ack-1", "fragment_id": 7,
        }), self.config["ground_station_ip"])
        self.assertNotIn(7, session.fragment_cache)

    def test_fragment_cleanup_unregisters_all_cached_files(self):
        self.prepare()
        self.start()
        session = self.node.session
        session.fragment_cache[1] = {"token": "one"}
        session.fragment_cache[2] = {"token": "two"}
        self.node._cleanup_fragments(session)
        self.assertEqual(session.fragment_cache, {})
        self.assertIn(("one", True), self.node.artifact_server.unregistered)
        self.assertIn(("two", True), self.node.artifact_server.unregistered)

    def test_close_stops_active_fast_lio(self):
        self.prepare()
        self.start()
        self.node.close()
        self.assertIn("run:abort_fast_lio.sh", self.events)
        self.assertEqual(self.node.state, "standby")

    def test_restart_active_mapping_aborts_without_artifact_and_reprepares(self):
        self.prepare()
        self.start()
        old_subscribers = list(self.node.session.subscribers)
        self.node.handle_datagram(self.command("prepare_mapping", {
            "request_id": "prepare-restart",
            "return_host": self.config["ground_station_ip"],
            "return_port": self.config["data_port"],
            "required_inputs": ["pointcloud", "imu", "artifact_storage", "map_generation"],
            "restart_active": True,
        }, sequence=3), self.config["ground_station_ip"])
        payload = self.messages()[-1]["payload"]
        self.assertTrue(payload["accepted"])
        self.assertTrue(payload["restarted"])
        self.assertEqual(payload["previous_state"], "mapping")
        self.assertEqual(payload["active_session_id"], self.identity["session_id"])
        self.assertIn("run:abort_fast_lio.sh", self.events)
        self.assertTrue(all(item.unregistered for item in old_subscribers))
        self.assertEqual(self.node.state, "ready")
        self.assertFalse(os.path.exists(self.node.session.paths.archive_path))

    def test_restart_rejects_different_active_session(self):
        self.prepare()
        self.start()
        other_identity = dict(self.identity, session_id="b" * 32)
        raw = encode_envelope(self.config, other_identity, "prepare_mapping", 3, {
            "request_id": "prepare-other",
            "return_host": self.config["ground_station_ip"],
            "return_port": self.config["data_port"],
            "required_inputs": ["pointcloud"], "restart_active": True,
        })
        self.node.handle_datagram(raw, self.config["ground_station_ip"])
        payload = self.messages()[-1]["payload"]
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error_code"], "BUSY")
        self.assertEqual(self.node.state, "mapping")

    def test_control_operations_are_written_to_ros_log(self):
        self.prepare()
        self.start()
        text = "\n".join(item[1] for item in self.node.rospy.logs)
        self.assertIn("mapping RX type=prepare_mapping", text)
        self.assertIn("mapping integration completed name=start_fast_lio", text)
        self.assertIn("mapping TX type=command_ack", text)

    def test_pgm_failure_reports_error_without_ready_artifact(self):
        self.prepare()
        self.start()
        original = self.runner.run

        def fail_pgm(arguments, timeout=None):
            if os.path.basename(arguments[0]) == "generate_pgm.sh":
                raise ArtifactError("PGM generation failed")
            return original(arguments, timeout)

        self.runner.run = fail_pgm
        self.node.handle_datagram(self.command("stop_mapping", {
            "request_id": "stop-failed", "reason": "done",
        }), self.config["ground_station_ip"])
        self.node.generation_thread.join(timeout=2.0)
        states = [item["payload"]["state"] for item in self.messages()
                  if item["message_type"] == "artifact_status"]
        self.assertEqual(states[-1], "error")
        self.assertNotIn("ready", states)
        self.assertEqual(self.node.state, "standby")
