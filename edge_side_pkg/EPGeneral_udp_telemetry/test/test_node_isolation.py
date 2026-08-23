import os
import sys
import unittest


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PACKAGE, "src"))

from epgeneral_udp_telemetry.node import RosUdpTelemetryNode


class _FaultySampler(object):
    last_rejection_reason = ""

    def snapshot(self, now):
        raise ValueError("broken source")

    def reject(self, reason, received=True):
        self.last_rejection_reason = str(reason)


class _Rospy(object):
    def logwarn_throttle(self, interval, message):
        pass

    def loginfo_throttle(self, interval, message):
        pass

    def loginfo(self, message, *args):
        pass

    def logerr_throttle(self, interval, message):
        pass


class _Socket(object):
    def __init__(self, error=None):
        self.error = error
        self.datagrams = []

    def sendto(self, datagram, destination):
        if self.error is not None:
            raise self.error
        self.datagrams.append((datagram, destination))


class NodeIsolationTests(unittest.TestCase):
    def test_snapshot_failure_only_invalidates_its_descriptor(self):
        descriptors = [
            {"name": "global_pose", "display_name": "Global", "type": "pose", "level": 1, "source": {"topic": "/pose"}},
            {"name": "vision_pose", "display_name": "Vision", "type": "pose", "level": 1, "source": {"topic": "/vision"}},
        ]
        config = {
            "device_id": "TEST", "destination_host": "127.0.0.1", "destination_port": 14560,
            "descriptor_hash": "0" * 64, "protocol_id": "ccs-udp-telemetry-v1",
            "max_datagram_bytes": 16384, "descriptors": descriptors,
        }
        node = RosUdpTelemetryNode(_Rospy(), config)
        try:
            node.samplers["global_pose"].add({
                "x": 1.0, "y": 2.0, "z": 3.0,
                "quaternion": (0.0, 0.0, 0.0, 1.0),
            }, 1.0)
            node.samplers["vision_pose"] = _FaultySampler()
            sent = []
            node._send = lambda message_type, sequence, level, payload: sent.append(payload)
            node._send_level(1)
        finally:
            node.socket.close()
        self.assertTrue(sent[0]["global_pose"]["valid"])
        self.assertEqual(sent[0]["global_pose"]["x"], 1.0)
        self.assertEqual(sent[0]["vision_pose"], {"valid": False, "sample_age_seconds": None})

    def test_level_send_statistics_count_success_bytes_and_failures(self):
        descriptor = {
            "name": "global_pose", "display_name": "Global", "type": "pose", "level": 1,
            "source": {"topic": "/pose"},
        }
        config = {
            "device_id": "TEST", "destination_host": "127.0.0.1", "destination_port": 14560,
            "descriptor_hash": "0" * 64, "protocol_id": "ccs-udp-telemetry-v1",
            "max_datagram_bytes": 16384, "descriptors": [descriptor],
        }
        node = RosUdpTelemetryNode(_Rospy(), config)
        original_socket = node.socket
        try:
            original_socket.close()
            node.socket = _Socket()
            node._send("telemetry", 0, 1, {"global_pose": {"valid": False}})
            self.assertEqual(node.level_stats[1]["sent_count"], 1)
            self.assertGreater(node.level_stats[1]["byte_count"], 0)
            node.socket = _Socket(OSError("send failed"))
            node._send("telemetry", 1, 1, {"global_pose": {"valid": False}})
            self.assertEqual(node.level_stats[1]["failure_count"], 1)
        finally:
            if hasattr(node.socket, "close"):
                node.socket.close()


if __name__ == "__main__":
    unittest.main()
