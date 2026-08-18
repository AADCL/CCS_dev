import os
import socket
import time
import unittest

import msgpack

from epgeneral_multi_map.config import load_config
from epgeneral_multi_map.node import RosMultiMapNode

try:
    from .test_node import FakeRospy, cloud_message, pose_message, start_datagram, stop_datagram
except ImportError:
    from test_node import FakeRospy, cloud_message, pose_message, start_datagram, stop_datagram


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE = os.path.join(os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "device.yaml")


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class UdpIntegrationTests(unittest.TestCase):
    def test_real_udp_start_slice_stop(self):
        config = load_config(os.path.join(PACKAGE, "config", "multi_mapping.yaml"), DEVICE)
        config.update({
            "bind_host": "127.0.0.1", "ground_station_ip": "127.0.0.1",
            "control_port": free_udp_port(), "data_port": free_udp_port(),
            "minimum_start_lead_ns": 50_000_000, "start_late_tolerance_ns": 100_000_000,
            "min_slice_duration_ns": 100_000_000, "late_arrival_ns": 20_000_000,
        })
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", config["data_port"]))
        listener.settimeout(0.05)
        node = RosMultiMapNode(
            FakeRospy(config), config, message_resolver=lambda unused: object,
            point_reader=lambda message, **unused: iter(message.points),
        )
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        received = []
        try:
            node.start()
            start_at = time.time_ns() + 100_000_000
            command = start_datagram(node, start_at_ns=start_at)
            raw = msgpack.unpackb(command, raw=False)
            raw["payload"]["return_host"] = "127.0.0.1"
            raw["payload"]["return_port"] = config["data_port"]
            raw["payload"]["slice_duration_ns"] = 200_000_000
            raw["sent_at_ns"] = time.time_ns()
            sender.sendto(msgpack.packb(raw, use_bin_type=True), node.control_address)
            self.assertTrue(wait_until(lambda: node.state == "mapping"))
            sample_ns = time.time_ns()
            node._pose_callback(node.session.token, pose_message(sample_ns - 20_000_000))
            node._pose_callback(node.session.token, pose_message(sample_ns + 20_000_000))
            node._cloud_callback(node.session.token, cloud_message(sample_ns, [[1.0, 0.0, 0.0]]))

            def got_slice():
                try:
                    data, unused_peer = listener.recvfrom(2048)
                    received.append(msgpack.unpackb(data, raw=False))
                except socket.timeout:
                    pass
                return any(item["message_type"] == "session_status"
                           and item["payload"].get("event") == "slice_complete"
                           for item in received)

            self.assertTrue(wait_until(got_slice))
            stop_at = time.time_ns() + 100_000_000
            stop = stop_datagram(node, stop_at)
            stop_raw = msgpack.unpackb(stop, raw=False)
            stop_raw["sent_at_ns"] = time.time_ns()
            sender.sendto(msgpack.packb(stop_raw, use_bin_type=True), node.control_address)
            self.assertTrue(wait_until(lambda: node.state == "standby"))
        finally:
            node.close()
            sender.close()
            listener.close()


if __name__ == "__main__":
    unittest.main()
