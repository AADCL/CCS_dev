import io
import os
import socket
import tempfile
import time
import unittest

import msgpack
import yaml

from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.node import RosMapStreamNode
from epgeneral_map_stream.protocol import encode_envelope


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


class Runner(object):
    def check(self):
        pass

    def run(self, command, values):
        if "stop" in command[0]:
            with io.open(values["pcd_path"], "w", encoding="ascii") as stream:
                stream.write("FIELDS x y z\nPOINTS 1\nDATA ascii\n0 0 0\n")
            with io.open(values["pgm_path"], "wb") as stream:
                stream.write(b"P5\n1 1\n255\n\x00")
            with io.open(values["yaml_path"], "w", encoding="utf-8") as stream:
                yaml.safe_dump({
                    "image": "map.pgm", "resolution": 0.1,
                    "origin": [0, 0, 0], "negate": 0,
                    "occupied_thresh": 0.65, "free_thresh": 0.196,
                }, stream)


class UdpIntegrationTests(unittest.TestCase):
    def test_real_udp_v2_prepare_start_stop_and_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(load_config(MAPPING, DEVICE))
            config.update(
                bind_host="127.0.0.1", ground_station_ip="127.0.0.1",
                device_ip="127.0.0.1", control_port=free_port(), http_bind_host="127.0.0.1",
                http_port=0, workspace_root=directory, min_free_bytes=1,
                artifact_poll_seconds=0.001, artifact_stable_polls=2,
                artifact_generation_timeout_seconds=1.0,
            )
            ground = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.addCleanup(ground.close)
            ground.bind(("127.0.0.1", 0))
            ground.settimeout(2.0)
            config["data_port"] = ground.getsockname()[1]
            node = RosMapStreamNode(
                FakeRospy(config), config, message_resolver=lambda unused: object,
                command_runner=Runner())
            node.start()
            self.addCleanup(node.close)
            identity = {"map_id": "map-udp", "session_id": "d" * 32}

            prepare = encode_envelope(config, identity, "prepare_mapping", 0, {
                "request_id": "udp-prepare", "return_host": "127.0.0.1",
                "return_port": config["data_port"],
                "required_inputs": ["pointcloud", "pose", "artifact_storage", "map_generation"],
            })
            ground.sendto(prepare, ("127.0.0.1", config["control_port"]))
            response = msgpack.unpackb(ground.recvfrom(4096)[0], raw=False)
            self.assertEqual(response["message_type"], "prepare_result")
            self.assertTrue(response["payload"]["accepted"])

            start = encode_envelope(config, identity, "start_mapping", 0, {
                "request_id": "udp-start",
                "coordinate_contract": "sensor+map_body+body_sensor",
            })
            ground.sendto(start, ("127.0.0.1", config["control_port"]))
            received = [msgpack.unpackb(ground.recvfrom(4096)[0], raw=False) for unused in range(2)]
            self.assertTrue(any(item["message_type"] == "command_ack" for item in received))

            stop = encode_envelope(config, identity, "stop_mapping", 0, {
                "request_id": "udp-stop", "reason": "done"})
            ground.sendto(stop, ("127.0.0.1", config["control_port"]))
            states = []
            deadline = time.time() + 2.0
            while time.time() < deadline and "ready" not in states:
                item = msgpack.unpackb(ground.recvfrom(4096)[0], raw=False)
                if item["message_type"] == "artifact_status":
                    states.append(item["payload"]["state"])
            self.assertEqual(states, ["generating", "ready"])
            self.assertEqual(node.state, "standby")
