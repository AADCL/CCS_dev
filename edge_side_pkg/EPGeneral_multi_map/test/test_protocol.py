import os
import unittest
import zlib

import msgpack

from epgeneral_multi_map.config import load_config
from epgeneral_multi_map.protocol import (
    ProtocolError,
    decode_command,
    encode_cloud_chunks,
    encode_envelope,
)


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(PACKAGE, "config", "multi_mapping.yaml")
DEVICE = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "device.yaml"
)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG, DEVICE)
        self.identity = {"map_id": "map-1", "session_id": "session-uav-1"}
        self.start_payload = {
            "request_id": "request-1",
            "return_host": self.config["ground_station_ip"],
            "return_port": self.config["data_port"],
            "cloud_rate_hz": 5.0,
            "voxel_size_m": 0.1,
            "compression": "zlib",
            "point_format": "xyz_f32_le",
            "coordinate_contract": "sensor+map_body+body_sensor",
            "job_id": "job-1",
            "role": "primary",
            "primary_device_id": "UAV_001",
            "participant_device_ids": ["UAV_001", "UGV_002"],
            "start_at_ns": 1_786_982_405_000_000_000,
            "slice_duration_ns": 5_000_000_000,
        }

    def _command(self, message_type, payload):
        return encode_envelope(
            self.config, self.identity, message_type, 0, payload,
            sent_at_ns=1_786_982_404_000_000_000,
        )

    def test_start_requires_joint_fields(self):
        payload = dict(self.start_payload)
        for key in (
            "job_id", "role", "primary_device_id", "participant_device_ids",
            "start_at_ns", "slice_duration_ns",
        ):
            payload.pop(key)
        with self.assertRaisesRegex(ProtocolError, "job_id"):
            decode_command(self._command("start_mapping", payload), self.config)

    def test_valid_joint_start_preserves_compatible_extra_fields(self):
        payload = dict(self.start_payload, operator_note="kept")
        decoded = decode_command(self._command("start_mapping", payload), self.config)
        self.assertEqual(decoded["payload"], payload)

    def test_participants_must_be_unique_and_include_local_and_primary(self):
        payload = dict(self.start_payload)
        payload["participant_device_ids"] = ["UAV_001", "uav_001"]
        with self.assertRaisesRegex(ProtocolError, "unique"):
            decode_command(self._command("start_mapping", payload), self.config)

        payload = dict(self.start_payload)
        payload["participant_device_ids"] = ["UGV_002", "UGV_003"]
        with self.assertRaisesRegex(ProtocolError, "local device"):
            decode_command(self._command("start_mapping", payload), self.config)

        payload = dict(self.start_payload)
        payload["primary_device_id"] = "UGV_003"
        with self.assertRaisesRegex(ProtocolError, "primary_device_id"):
            decode_command(self._command("start_mapping", payload), self.config)

    def test_role_must_match_local_and_primary_device(self):
        payload = dict(self.start_payload, role="secondary")
        with self.assertRaisesRegex(ProtocolError, "role"):
            decode_command(self._command("start_mapping", payload), self.config)

    def test_slice_duration_must_stay_inside_configured_range(self):
        payload = dict(self.start_payload, slice_duration_ns=999_999_999)
        with self.assertRaisesRegex(ProtocolError, "slice_duration_ns"):
            decode_command(self._command("start_mapping", payload), self.config)

    def test_start_requires_absolute_start_time(self):
        payload = dict(self.start_payload)
        payload.pop("start_at_ns")
        with self.assertRaisesRegex(ProtocolError, "start_at_ns"):
            decode_command(self._command("start_mapping", payload), self.config)

    def test_stop_requires_job_and_absolute_stop_time(self):
        payload = {"request_id": "stop-1", "reason": "operator", "stop_at_ns": 100}
        with self.assertRaisesRegex(ProtocolError, "job_id"):
            decode_command(self._command("stop_mapping", payload), self.config)

        payload["job_id"] = "job-1"
        decoded = decode_command(self._command("stop_mapping", payload), self.config)
        self.assertEqual(decoded["payload"]["stop_at_ns"], 100)

        payload.pop("stop_at_ns")
        with self.assertRaisesRegex(ProtocolError, "stop_at_ns"):
            decode_command(self._command("stop_mapping", payload), self.config)

    def test_slice_metadata_survives_bounded_chunk_encoding(self):
        transform = {
            "x": 0.0, "y": 0.0, "z": 0.0,
            "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
        }
        metadata = {
            "frame_id": 3,
            "sample_stamp_ns": 1_786_982_420_100_000_000,
            "point_count": 1000,
            "map_from_body": transform,
            "body_from_sensor": transform,
            "job_id": "job-1",
            "slice_id": 3,
            "slice_start_ns": 1_786_982_420_000_000_000,
            "slice_end_ns": 1_786_982_425_000_000_000,
            "partial": False,
            "error_tail": False,
            "truncated": False,
            "frame_index": 0,
            "slice_frame_count": 1,
            "pose_before_stamp_ns": 1_786_982_420_090_000_000,
            "pose_after_stamp_ns": 1_786_982_420_110_000_000,
            "pose_max_error_ns": 10_000_000,
            "pose_interpolated": True,
        }
        compressed = zlib.compress(os.urandom(20_000))
        datagrams = encode_cloud_chunks(self.config, self.identity, 7, metadata, compressed)
        decoded = [msgpack.unpackb(item, raw=False) for item in datagrams]
        self.assertGreater(len(decoded), 1)
        self.assertTrue(all(len(item) <= self.config["max_datagram_bytes"] for item in datagrams))
        self.assertEqual([item["sequence"] for item in decoded], list(range(7, 7 + len(decoded))))
        self.assertTrue(all(item["payload"]["slice_id"] == 3 for item in decoded))
        rebuilt = b"".join(item["payload"]["data"] for item in decoded)
        self.assertEqual(rebuilt, compressed)
        self.assertEqual(decoded[0]["payload"]["frame_crc32"], zlib.crc32(compressed) & 0xFFFFFFFF)

    def test_wrong_protocol_and_oversize_datagrams_are_rejected(self):
        encoded = self._command("start_mapping", self.start_payload)
        raw = msgpack.unpackb(encoded, raw=False)
        raw["protocol_id"] = "wrong"
        with self.assertRaisesRegex(ProtocolError, "protocol_id"):
            decode_command(msgpack.packb(raw, use_bin_type=True), self.config)
        with self.assertRaisesRegex(ProtocolError, "exceeds"):
            decode_command(b"x" * (self.config["max_datagram_bytes"] + 1), self.config)


if __name__ == "__main__":
    unittest.main()
