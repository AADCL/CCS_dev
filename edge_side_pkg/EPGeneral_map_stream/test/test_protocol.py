import os
import unittest
import zlib

import msgpack

from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.protocol import ProtocolError, decode_command, encode_cloud_chunks, encode_envelope

try:
    from .test_paths import device_config_path
except ImportError:
    from test_paths import device_config_path


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "map_stream.yaml")
DEVICE = device_config_path(PACKAGE)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(MAPPING, DEVICE)
        self.identity = {"map_id": "map-1", "session_id": "a" * 32}
        self.prepare_payload = {
            "request_id": "request-1", "return_host": self.config["ground_station_ip"],
            "return_port": self.config["data_port"],
            "required_inputs": ["pointcloud", "pose", "artifact_storage", "map_generation"],
        }

    def test_prepare_command_round_trip(self):
        encoded = encode_envelope(self.config, self.identity, "prepare_mapping", 0, self.prepare_payload)
        decoded = decode_command(encoded, self.config)
        self.assertEqual(decoded["payload"], self.prepare_payload)
        self.assertEqual(decoded["schema_version"], 2)

    def test_prepare_command_accepts_restart_active(self):
        payload = dict(self.prepare_payload, restart_active=True)
        encoded = encode_envelope(
            self.config, self.identity, "prepare_mapping", 1, payload)
        decoded = decode_command(encoded, self.config)
        self.assertTrue(decoded["payload"]["restart_active"])

    def test_joint_mapping_fields_are_optional_but_atomic(self):
        payload = dict(
            self.prepare_payload, job_id="job-1", role="primary",
            primary_device_id=self.config["device_id"],
        )
        decoded = decode_command(
            encode_envelope(self.config, self.identity, "prepare_mapping", 1, payload),
            self.config,
        )
        self.assertEqual(decoded["payload"]["job_id"], "job-1")
        del payload["role"]
        with self.assertRaisesRegex(ProtocolError, "provided together"):
            decode_command(
                encode_envelope(self.config, self.identity, "prepare_mapping", 2, payload),
                self.config,
            )

    def test_wrong_protocol_is_rejected(self):
        encoded = encode_envelope(self.config, self.identity, "prepare_mapping", 0, self.prepare_payload)
        raw = msgpack.unpackb(encoded, raw=False)
        raw["protocol_id"] = "wrong"
        with self.assertRaisesRegex(ProtocolError, "protocol_id"):
            decode_command(msgpack.packb(raw, use_bin_type=True), self.config)

    def test_cloud_chunks_fit_and_reassemble(self):
        compressed = zlib.compress(os.urandom(20000))
        transform = {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
        metadata = {
            "frame_id": 3,
            "sample_stamp_ns": 10,
            "point_count": 100,
            "map_from_body": transform,
            "body_from_sensor": transform,
        }
        datagrams = encode_cloud_chunks(self.config, self.identity, 7, metadata, compressed)
        self.assertGreater(len(datagrams), 1)
        self.assertTrue(all(len(item) <= 1400 for item in datagrams))
        decoded = [msgpack.unpackb(item, raw=False) for item in datagrams]
        self.assertEqual([item["sequence"] for item in decoded], list(range(7, 7 + len(decoded))))
        rebuilt = b"".join(item["payload"]["data"] for item in decoded)
        self.assertEqual(rebuilt, compressed)
        self.assertEqual(decoded[0]["payload"]["frame_crc32"], zlib.crc32(compressed) & 0xFFFFFFFF)
