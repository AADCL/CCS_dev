import os
import socket
import tempfile
import unittest

import msgpack

from epgeneral_task_control.config import load_config
from epgeneral_task_control.node import RosTaskControlNode
from epgeneral_task_control.storage import TrajectoryStore

from test_node import FakeCommand, FakeRospy, pack


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_CONFIG = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "task_control.yaml")
DEVICE_CONFIG = os.path.join(PACKAGE, "test", "fixtures", "device.yaml")


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class UdpIntegrationTests(unittest.TestCase):
    def test_real_udp_prepare_ack_matches_ground_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(load_config(TASK_CONFIG, DEVICE_CONFIG))
            config.update(bind_host="127.0.0.1", ground_station_ip="127.0.0.1",
                          control_port=free_port(), status_port=free_port(), storage_directory=directory)
            ground = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ground.bind(("127.0.0.1", config["status_port"]))
            ground.settimeout(2.0)
            node = RosTaskControlNode(FakeRospy(), config, FakeCommand, object,
                                      store=TrajectoryStore(directory))
            node.start()
            payload = {"revision": 1, "chunk_count": 1, "compressed_bytes": 100, "raw_bytes": 200,
                       "crc32": 123, "compression": "zlib", "encoding": "json-utf8"}
            ground.sendto(pack(config, "task_prepare", "udp-prepare", payload),
                          ("127.0.0.1", config["control_port"]))
            raw, address = ground.recvfrom(4096)
            message = msgpack.unpackb(raw, raw=False)
            self.assertEqual(address[0], "127.0.0.1")
            self.assertEqual(message["protocol_id"], "ccs-task-control-v2")
            self.assertEqual(message["message_type"], "command_ack")
            self.assertTrue(message["payload"]["accepted"])
            node.close()
            ground.close()


if __name__ == "__main__":
    unittest.main()
