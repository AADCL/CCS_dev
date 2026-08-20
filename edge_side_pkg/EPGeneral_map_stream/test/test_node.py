import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import msgpack
import numpy as np
import yaml

from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.node import RosMapStreamNode
from epgeneral_map_stream.protocol import encode_envelope


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = os.path.join(os.path.dirname(PACKAGE), "epgeneral_device_config", "config", "device.yaml")


class FakeSocket(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, destination):
        self.sent.append((data, destination))


class FakeSubscriber(object):
    def __init__(self):
        self.unregistered = False

    def unregister(self):
        self.unregistered = True


class FakeRospy(object):
    def __init__(self, config):
        self.config = config

    def get_published_topics(self):
        return [(self.config["cloud_topic"], self.config["cloud_message_type"]),
                (self.config["pose_topic"], self.config["pose_message_type"])]

    def Subscriber(self, unused_topic, unused_class, unused_callback, **unused_kwargs):
        return FakeSubscriber()

    def loginfo(self, unused_message, *unused_args):
        pass

    def logwarn(self, unused_message, *unused_args):
        pass

    def logwarn_throttle(self, unused_seconds, unused_message):
        pass

    def logerr(self, unused_message, *unused_args):
        pass


class FakeRunner(object):
    def check(self):
        pass

    def run(self, command, values):
        if "stop" in command[0]:
            with io.open(values["pcd_path"], "w", encoding="ascii") as stream:
                stream.write("VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n")
                stream.write("WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n0 0 0\n")
            with io.open(values["pgm_path"], "wb") as stream:
                stream.write(b"P5\n2 2\n255\n" + bytes((0, 254, 205, 254)))
            with io.open(values["yaml_path"], "w", encoding="utf-8") as stream:
                yaml.safe_dump({
                    "image": "map.pgm", "resolution": 0.1,
                    "origin": [0.0, 0.0, 0.0], "negate": 0,
                    "occupied_thresh": 0.65, "free_thresh": 0.196,
                }, stream)
        return "ok"


class FakeArtifactServer(object):
    port = 14600

    def register(self, unused_path, unused_ttl):
        return "token", "2030-01-01T00:00:00+00:00"

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
            artifact_poll_seconds=0.001, artifact_stable_polls=2,
            artifact_generation_timeout_seconds=1.0,
        )
        self.clock_value = [10.0]
        self.node = RosMapStreamNode(
            FakeRospy(self.config), self.config,
            clock=lambda: self.clock_value[0], message_resolver=lambda unused: object,
            command_runner=FakeRunner(), artifact_server=FakeArtifactServer())
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
            "required_inputs": ["pointcloud", "pose", "artifact_storage", "map_generation"],
        }), self.config["ground_station_ip"])

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

    def test_sampling_window_sends_v2_cloud_chunks(self):
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
            header=SimpleNamespace(stamp=stamp, frame_id=self.config["sensor_frame"]), fields=[])
        self.node._pose_callback(session.token, pose)
        with patch("epgeneral_map_stream.node.extract_pointcloud2",
                   return_value=np.asarray([[1, 0, 0], [2, 0, 0]], dtype=np.float32)):
            self.node._cloud_callback(session.token, cloud)
            self.clock_value[0] += self.config["sample_window_seconds"] + 0.01
            self.node._cloud_callback(session.token, cloud)
        chunks = [item for item in self.messages() if item["message_type"] == "cloud_chunk"]
        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["schema_version"], 2)
        self.assertEqual(chunks[0]["payload"]["map_from_body"]["x"], 1.0)

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
        self.assertEqual(self.node.state, "standby")
