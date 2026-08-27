import json
import os
import tempfile
import unittest
import zlib
from xml.etree import ElementTree

import msgpack

from epgeneral_task_control.config import ConfigError, load_config
from epgeneral_task_control.protocol import ProtocolError, decode
from epgeneral_task_control.storage import StorageError, TrajectoryStore, decode_trajectory


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_CONFIG = os.path.join(PACKAGE, "config", "task_control.yaml")
DEVICE_CONFIG = os.path.join(PACKAGE, "test", "fixtures", "device.yaml")


def trajectory(device_id="UAV_001", task_name="园区 & 巡检"):
    return {
        "schema_version": 2, "task_id": "task/../1", "task_name": task_name,
        "map_id": "map-1", "frame_id": "map", "subtask_id": "sub/../../1",
        "device_id": device_id, "revision": 3, "cruise_speed_mps": 1.5,
        "start_delay_seconds": 2.0, "waypoints": [
            {"index": 0, "waypoint_id": "a", "x": 1.0, "y": 2.0, "z": 1.5},
            {"index": 1, "waypoint_id": "b", "x": 2.0, "y": 3.0, "z": 1.5},
        ],
    }


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(TASK_CONFIG, DEVICE_CONFIG)

    def test_sample_config_and_invalid_limit(self):
        self.assertEqual(self.config["control_port"], 14563)
        self.assertEqual(self.config["device_id"], "UAV_001")
        with tempfile.TemporaryDirectory() as directory:
            bad = os.path.join(directory, "bad.yaml")
            with open(TASK_CONFIG, "r", encoding="utf-8") as stream:
                content = stream.read().replace("max_datagram_bytes: 1400", "max_datagram_bytes: 1401")
            with open(bad, "w", encoding="utf-8") as stream:
                stream.write(content)
            with self.assertRaises(ConfigError):
                load_config(bad, DEVICE_CONFIG)

    def test_protocol_rejects_wrong_protocol(self):
        raw = {"schema_version": 2, "protocol_id": "wrong", "task_id": "t", "subtask_id": "s",
               "device_id": "UAV_001", "execution_id": "", "message_type": "task_prepare",
               "request_id": "r", "sequence": 0, "sent_at_ns": 0, "payload": {}}
        with self.assertRaises(ProtocolError):
            decode(msgpack.packb(raw, use_bin_type=True), self.config)

    def test_xml_round_trip_escaping_and_hashed_path(self):
        payload = trajectory()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(raw)
        crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
        normalized = decode_trajectory(compressed, crc32, self.config, {
            "task_id": payload["task_id"], "subtask_id": payload["subtask_id"],
            "device_id": payload["device_id"], "revision": payload["revision"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = TrajectoryStore(directory)
            saved = store.commit(normalized, crc32)
            self.assertTrue(saved["xml_path"].startswith(os.path.abspath(directory)))
            self.assertNotIn("..", os.path.relpath(saved["xml_path"], directory))
            root = ElementTree.parse(saved["xml_path"]).getroot()
            self.assertEqual(root.get("schema_version"), "2")
            self.assertEqual(root.find("metadata").get("task_name"), "园区 & 巡检")
            loaded = store.load(payload["task_id"], payload["subtask_id"])
            self.assertEqual(loaded["revision"], 3)
            self.assertEqual(loaded["crc32"], crc32)
            store.delete(payload["task_id"], payload["subtask_id"])
            self.assertIsNone(store.load(payload["task_id"], payload["subtask_id"]))

    def test_invalid_waypoint_and_crc_are_rejected(self):
        payload = trajectory()
        payload["waypoints"][1]["index"] = 4
        compressed = zlib.compress(json.dumps(payload).encode("utf-8"))
        identity = {"task_id": payload["task_id"], "subtask_id": payload["subtask_id"],
                    "device_id": payload["device_id"], "revision": 3}
        with self.assertRaises(StorageError):
            decode_trajectory(compressed, zlib.crc32(compressed) & 0xFFFFFFFF, self.config, identity)
        with self.assertRaises(StorageError):
            decode_trajectory(compressed, 0, self.config, identity)


if __name__ == "__main__":
    unittest.main()
