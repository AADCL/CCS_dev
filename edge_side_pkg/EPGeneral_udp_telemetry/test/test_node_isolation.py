import os
import sys
import threading
import unittest

import msgpack


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


class _BlockingSocket(_Socket):
    def __init__(self):
        super(_BlockingSocket, self).__init__()
        self.first_send_entered = threading.Event()
        self.release_first_send = threading.Event()

    def sendto(self, datagram, destination):
        if not self.datagrams:
            self.first_send_entered.set()
            if not self.release_first_send.wait(2.0):
                raise RuntimeError("timed out waiting to release first send")
        super(_BlockingSocket, self).sendto(datagram, destination)


class _ObservedLock(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._attempt_lock = threading.Lock()
        self._attempts = 0
        self.second_attempted = threading.Event()

    def __enter__(self):
        with self._attempt_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempted.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()


class NodeIsolationTests(unittest.TestCase):
    @staticmethod
    def _config(descriptors):
        return {
            "device_id": "TEST", "destination_host": "127.0.0.1", "destination_port": 14560,
            "descriptor_hash": "0" * 64, "protocol_id": "ccs-udp-telemetry-v1",
            "max_datagram_bytes": 16384, "descriptors": descriptors,
        }

    def test_snapshot_failure_only_invalidates_its_descriptor(self):
        descriptors = [
            {"name": "global_pose", "display_name": "Global", "type": "pose", "level": 1, "source": {"topic": "/pose"}},
            {"name": "vision_pose", "display_name": "Vision", "type": "pose", "level": 1, "source": {"topic": "/vision"}},
        ]
        config = self._config(descriptors)
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
        config = self._config([descriptor])
        node = RosUdpTelemetryNode(_Rospy(), config)
        original_socket = node.socket
        try:
            original_socket.close()
            node.socket = _Socket()
            node._send("telemetry", 1, 1, {"global_pose": {"valid": False}})
            self.assertEqual(node.level_stats[1]["sent_count"], 1)
            self.assertGreater(node.level_stats[1]["byte_count"], 0)
            node.socket = _Socket(OSError("send failed"))
            node._send("telemetry", 1, 1, {"global_pose": {"valid": False}})
            self.assertEqual(node.level_stats[1]["failure_count"], 1)
            self.assertEqual(node.sequences[1], 2)
        finally:
            if hasattr(node.socket, "close"):
                node.socket.close()

    def test_concurrent_level_callbacks_allocate_sequences_in_send_order(self):
        descriptor = {
            "name": "global_pose", "display_name": "Global", "type": "pose", "level": 1,
            "source": {"topic": "/pose"},
        }
        node = RosUdpTelemetryNode(_Rospy(), self._config([descriptor]))
        original_socket = node.socket
        blocking_socket = _BlockingSocket()
        node.send_lock = _ObservedLock()
        errors = []

        def send_level():
            try:
                node._send_level(1)
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=send_level)
        second = threading.Thread(target=send_level)
        try:
            original_socket.close()
            node.socket = blocking_socket
            first.start()
            self.assertTrue(blocking_socket.first_send_entered.wait(1.0))
            second.start()
            self.assertTrue(node.send_lock.second_attempted.wait(1.0))
            blocking_socket.release_first_send.set()
            first.join(2.0)
            second.join(2.0)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            sequences = [msgpack.unpackb(item[0], raw=False)["sequence"]
                         for item in blocking_socket.datagrams]
            self.assertEqual(sequences, [0, 1])
            self.assertEqual(node.sequences[1], 2)
        finally:
            blocking_socket.release_first_send.set()
            first.join(2.0)
            second.join(2.0)
            if hasattr(node.socket, "close"):
                node.socket.close()


if __name__ == "__main__":
    unittest.main()
