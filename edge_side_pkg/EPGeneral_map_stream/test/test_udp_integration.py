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

try:
    from .test_paths import device_config_path
except ImportError:
    from test_paths import device_config_path


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "map_stream.yaml")
DEVICE = device_config_path(PACKAGE)


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
        return [(self.config["input_cloud_topic"], self.config["input_cloud_message_type"]),
                (self.config["input_imu_topic"], self.config["input_imu_message_type"]),
                (self.config["cloud_topic"], self.config["cloud_message_type"]),
                (self.config["pose_topic"], self.config["pose_message_type"])]

    def wait_for_message(self, topic, unused_class, timeout=None):
        stamp = type("Stamp", (), {"to_nsec": lambda self: 123456789})()
        vector = type("Vector", (), {"x": 0.0, "y": 0.0, "z": 0.0})()
        if topic == self.config["input_imu_topic"]:
            orientation = type("Quaternion", (), {"x": 0.0, "y": 0.0,
                                                    "z": 0.0, "w": 1.0})()
            return type("Imu", (), {
                "header": type("Header", (), {"stamp": stamp,
                                                "frame_id": self.config["input_imu_frame"]})(),
                "orientation": orientation, "angular_velocity": vector,
                "linear_acceleration": vector})()
        if topic == self.config["pose_topic"]:
            position = type("Position", (), {"x": 0.0, "y": 0.0, "z": 0.0})()
            orientation = type("Quaternion", (), {"x": 0.0, "y": 0.0,
                                                    "z": 0.0, "w": 1.0})()
            pose = type("Pose", (), {"position": position, "orientation": orientation})()
            return type("Odometry", (), {
                "header": type("Header", (), {"stamp": stamp,
                                                "frame_id": self.config["map_frame"]})(),
                "child_frame_id": self.config["body_frame"],
                "pose": type("PoseWithCovariance", (), {"pose": pose})()})()
        frame = (self.config["input_cloud_frame"]
                 if topic == self.config["input_cloud_topic"] else self.config["cloud_frame"])
        return type("Cloud", (), {
            "header": type("Header", (), {"stamp": stamp, "frame_id": frame})(),
            "fields": []})()

    def Subscriber(self, unused_topic, unused_type, unused_callback, **unused_kwargs):
        return FakeSubscriber()

    def loginfo(self, unused_message, *unused_args):
        pass

    def logwarn(self, unused_message, *unused_args):
        pass

    def logerr(self, unused_message, *unused_args):
        pass


class Runner(object):
    def check(self, unused_commands):
        pass

    def run(self, arguments, timeout=None):
        name = os.path.basename(arguments[0])
        if name == "save_map.sh":
            with io.open(arguments[3], "w", encoding="ascii") as stream:
                stream.write("FIELDS x y z\nPOINTS 1\nDATA ascii\n1 0 0\n")
            fresh_ns = time.time_ns() + 1_000_000_000
            os.utime(arguments[3], ns=(fresh_ns, fresh_ns))
        elif name == "stop_fast_lio.sh":
            with io.open(arguments[3], "r", encoding="ascii") as source:
                content = source.read()
            with io.open(arguments[4], "w", encoding="ascii") as stream:
                stream.write(content)
        elif name == "generate_pgm.sh":
            with io.open(arguments[5], "wb") as stream:
                stream.write(b"P5\n1 1\n255\n\x00")
            with io.open(arguments[6], "w", encoding="utf-8") as stream:
                yaml.safe_dump({
                    "image": "map.pgm", "resolution": 0.1,
                    "origin": [0, 0, 0], "negate": 0,
                    "occupied_thresh": 0.65, "free_thresh": 0.196,
                }, stream)


class TransformLookup(object):
    def lookup(self, unused_target, unused_source, unused_stamp_ns):
        return {"x": 0.0, "y": 0.0, "z": 0.0,
                "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}


class UdpIntegrationTests(unittest.TestCase):
    def test_real_udp_v2_prepare_start_stop_and_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(load_config(MAPPING, DEVICE))
            config.update(
                bind_host="127.0.0.1", ground_station_ip="127.0.0.1",
                device_ip="127.0.0.1", control_port=free_port(), http_bind_host="127.0.0.1",
                http_port=0, workspace_root=directory, min_free_bytes=1,
                accumulator_pcd_path=os.path.join(directory, "accumulator.pcd"),
                source_pcd_path=os.path.join(directory, "source.pcd"),
                artifact_poll_seconds=0.001, artifact_stable_polls=2,
                artifact_generation_timeout_seconds=1.0,
            )
            with io.open(config["source_pcd_path"], "w", encoding="ascii") as stream:
                stream.write("old source PCD")
            ground = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.addCleanup(ground.close)
            ground.bind(("127.0.0.1", 0))
            ground.settimeout(2.0)
            config["data_port"] = ground.getsockname()[1]
            node = RosMapStreamNode(
                FakeRospy(config), config, message_resolver=lambda unused: object,
                command_runner=Runner(), transform_lookup=TransformLookup())
            node.start()
            self.addCleanup(node.close)
            identity = {"map_id": "map-udp", "session_id": "d" * 32}

            prepare = encode_envelope(config, identity, "prepare_mapping", 0, {
                "request_id": "udp-prepare", "return_host": "127.0.0.1",
                "return_port": config["data_port"],
                "required_inputs": ["pointcloud", "imu", "artifact_storage", "map_generation"],
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
            self.assertGreaterEqual(states.count("generating"), 1)
            self.assertEqual(states[-1], "ready")
            self.assertEqual(node.state, "standby")
