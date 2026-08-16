import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import msgpack

from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.node import RosMapStreamNode
from epgeneral_map_stream.protocol import encode_envelope


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = os.path.join(os.path.dirname(PACKAGE), "epgeneral_device_config", "config", "device.yaml")


class FakeSubscriber(object):
    def __init__(self, topic, callback):
        self.topic = topic
        self.callback = callback
        self.unregistered = False

    def unregister(self):
        self.unregistered = True


class FakeSocket(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, destination):
        self.sent.append((data, destination))


class FakeRospy(object):
    def __init__(self, config):
        self.config = config
        self.subscribers = []
        self.logs = []

    def get_published_topics(self):
        return [
            (self.config["cloud_topic"], self.config["cloud_message_type"]),
            (self.config["pose_topic"], self.config["pose_message_type"]),
        ]

    def Subscriber(self, topic, unused_class, callback, **unused_kwargs):
        subscriber = FakeSubscriber(topic, callback)
        self.subscribers.append(subscriber)
        return subscriber

    def loginfo(self, message, *args):
        self.logs.append(("info", message % args if args else message))

    def logwarn(self, message, *args):
        self.logs.append(("warning", message % args if args else message))

    def logerr(self, message, *args):
        self.logs.append(("error", message % args if args else message))


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(MAPPING, DEVICE)
        self.clock_value = [10.0]
        self.rospy = FakeRospy(self.config)
        self.node = RosMapStreamNode(
            self.rospy, self.config, clock=lambda: self.clock_value[0], message_resolver=lambda unused: object
        )
        self.node.socket = FakeSocket()
        self.identity = {"map_id": "map-1", "session_id": "b" * 32}
        self.start_payload = {
            "request_id": "start-request",
            "return_host": self.config["ground_station_ip"],
            "return_port": self.config["data_port"],
            "cloud_rate_hz": 10.0,
            "voxel_size_m": 0.01,
            "compression": "zlib",
            "point_format": "xyz_f32_le",
            "coordinate_contract": "sensor+map_body+body_sensor",
        }

    def command(self, message_type, payload, sequence=0):
        return encode_envelope(self.config, self.identity, message_type, sequence, payload)

    def messages(self):
        return [msgpack.unpackb(item[0], raw=False) for item in self.node.socket.sent]

    def test_start_duplicate_and_stop_return_to_standby(self):
        start = self.command("start_mapping", self.start_payload)
        self.node.handle_datagram(start, self.config["ground_station_ip"])
        self.assertEqual(self.node.state, "mapping")
        self.assertEqual(len(self.rospy.subscribers), 2)
        ack = self.messages()[0]
        self.assertTrue(ack["payload"]["accepted"])
        self.assertEqual(ack["payload"]["actual_parameters"], {"cloud_rate_hz": 5.0, "voxel_size_m": 0.05})

        self.node.handle_datagram(start, self.config["ground_station_ip"])
        self.assertEqual(len(self.rospy.subscribers), 2)
        stop_payload = {"request_id": "stop-request", "reason": "done"}
        self.node.handle_datagram(self.command("stop_mapping", stop_payload, 1), self.config["ground_station_ip"])
        self.assertEqual(self.node.state, "standby")
        self.assertTrue(all(item.unregistered for item in self.rospy.subscribers))
        self.assertEqual(self.messages()[-1]["payload"]["state"], "stopped")

    def test_unexpected_source_ip_is_ignored(self):
        self.node.handle_datagram(self.command("start_mapping", self.start_payload), "192.0.2.1")
        self.assertEqual(self.node.state, "standby")
        self.assertEqual(self.node.socket.sent, [])

    def test_input_timeout_sends_error_and_cleans_session(self):
        self.node.handle_datagram(self.command("start_mapping", self.start_payload), self.config["ground_station_ip"])
        self.clock_value[0] += self.config["input_timeout_seconds"] + 0.1
        self.node._watchdog()
        self.assertEqual(self.node.state, "standby")
        self.assertEqual(self.messages()[-1]["message_type"], "session_status")
        self.assertEqual(self.messages()[-1]["payload"]["error_code"], "SENSOR_UNAVAILABLE")

    def test_synchronized_cloud_callback_sends_chunk_frame(self):
        self.node.handle_datagram(self.command("start_mapping", self.start_payload), self.config["ground_station_ip"])
        session = self.node.session
        stamp = SimpleNamespace(to_nsec=lambda: 123456789)
        pose = SimpleNamespace(
            header=SimpleNamespace(stamp=stamp, frame_id=self.config["map_frame"]),
            child_frame_id=self.config["body_frame"],
            pose=SimpleNamespace(pose=SimpleNamespace(
                position=SimpleNamespace(x=1.0, y=2.0, z=3.0),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            )),
        )
        cloud = SimpleNamespace(header=SimpleNamespace(stamp=stamp, frame_id=self.config["sensor_frame"]))
        self.node._pose_callback(session.token, pose)
        with patch("epgeneral_map_stream.node.extract_pointcloud2", return_value=np.asarray([[1, 0, 0], [2, 0, 0]], dtype=np.float32)):
            self.node._cloud_callback(session.token, cloud)
        cloud_messages = [item for item in self.messages() if item["message_type"] == "cloud_chunk"]
        self.assertTrue(cloud_messages)
        self.assertEqual(cloud_messages[0]["payload"]["sample_stamp_ns"], 123456789)
        self.assertEqual(cloud_messages[0]["payload"]["map_from_body"]["x"], 1.0)
