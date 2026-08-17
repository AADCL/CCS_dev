import socket
import threading
import time
import unittest

import msgpack

from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.node import RosMapStreamNode
from epgeneral_map_stream.protocol import encode_envelope

import os


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = os.path.join(os.path.dirname(PACKAGE), "epgeneral_device_config", "config", "device.yaml")


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class FakeTimer(object):
    def shutdown(self):
        pass


class FakeSubscriber(object):
    def unregister(self):
        pass


class FakeRospy(object):
    def __init__(self, config):
        self.config = config

    def Duration(self, seconds):
        return seconds

    def Timer(self, unused_duration, unused_callback):
        return FakeTimer()

    def on_shutdown(self, unused_callback):
        pass

    def get_published_topics(self):
        return [(self.config["cloud_topic"], self.config["cloud_message_type"]),
                (self.config["pose_topic"], self.config["pose_message_type"])]

    def Subscriber(self, unused_topic, unused_type, unused_callback, **unused_kwargs):
        return FakeSubscriber()

    def loginfo(self, unused_message, *unused_args):
        pass

    def logwarn(self, unused_message, *unused_args):
        pass

    def logerr(self, unused_message, *unused_args):
        pass


class UdpIntegrationTests(unittest.TestCase):
    def test_real_udp_start_and_stop(self):
        config = dict(load_config(MAPPING, DEVICE))
        config.update(bind_host="127.0.0.1", ground_station_ip="127.0.0.1", control_port=free_port())
        ground = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ground.bind(("127.0.0.1", 0))
        ground.settimeout(2.0)
        config["data_port"] = ground.getsockname()[1]
        node = RosMapStreamNode(FakeRospy(config), config, message_resolver=lambda unused: object)
        node.start()
        identity = {"map_id": "map-udp", "session_id": "d" * 32}
        start = encode_envelope(config, identity, "start_mapping", 0, {
            "request_id": "udp-start", "return_host": "127.0.0.1", "return_port": config["data_port"],
            "cloud_rate_hz": 5.0, "voxel_size_m": 0.1, "compression": "zlib",
            "point_format": "xyz_f32_le", "coordinate_contract": "sensor+map_body+body_sensor",
        })
        ground.sendto(start, ("127.0.0.1", config["control_port"]))
        received = []
        while len(received) < 2:
            received.append(msgpack.unpackb(ground.recvfrom(4096)[0], raw=False))
        self.assertTrue(any(item["message_type"] == "command_ack" and item["payload"]["accepted"] for item in received))

        stop = encode_envelope(config, identity, "stop_mapping", 1, {"request_id": "udp-stop", "reason": "done"})
        ground.sendto(stop, ("127.0.0.1", config["control_port"]))
        received = []
        while len(received) < 2:
            received.append(msgpack.unpackb(ground.recvfrom(4096)[0], raw=False))
        self.assertTrue(any(item["message_type"] == "command_ack" for item in received))
        self.assertTrue(any(item["message_type"] == "session_status" and item["payload"]["state"] == "stopped" for item in received))
        deadline = time.time() + 1.0
        while time.time() < deadline and node.state != "standby":
            time.sleep(0.01)
        self.assertEqual(node.state, "standby")
        node.close()
        ground.close()
