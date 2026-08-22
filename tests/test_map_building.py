import importlib.util
import tempfile
import unittest
import zlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


DEPS_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("msgpack", "numpy"))

if DEPS_AVAILABLE:
    import numpy as np

    from ccs_monitor.map_building import (
        CloudFrameAssembler,
        MapBuildingEnvelope,
        MapBuildingProtocol,
        MapBuildingProtocolError,
        VoxelMapAccumulator,
        transform_sensor_points,
        write_binary_pcd,
    )
    from ccs_monitor.map_building_config import load_map_building_config


@unittest.skipUnless(DEPS_AVAILABLE, "msgpack/numpy are not installed")
class MapBuildingCoreTests(unittest.TestCase):
    def setUp(self):
        self.config = load_map_building_config()
        self.protocol = MapBuildingProtocol(self.config)
        self.identity = {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}

    def envelope(self, payload, sequence=1):
        return MapBuildingEnvelope(
            "map-1", "UAV-001", "session-1", "cloud_chunk", sequence, 1, payload
        )

    def chunks(self, points, chunk_size=15):
        raw = np.asarray(points, dtype="<f4").tobytes()
        compressed = zlib.compress(raw)
        pieces = [compressed[index:index + chunk_size] for index in range(0, len(compressed), chunk_size)]
        crc = zlib.crc32(compressed) & 0xFFFFFFFF
        return [self.envelope({
            "frame_id": 7,
            "chunk_count": len(pieces),
            "chunk_index": index,
            "frame_crc32": crc,
            "sample_stamp_ns": 123,
            "point_count": len(points),
            "map_from_body": self.identity,
            "body_from_sensor": self.identity,
            "data": piece,
        }, index + 1) for index, piece in enumerate(pieces)]

    def test_start_command_round_trip(self):
        envelope = MapBuildingEnvelope(
            "map-1", "UAV-001", "session-1", "start_mapping", 0, 1,
            {
                "request_id": "request-1", "return_host": "192.168.1.2", "return_port": 14562,
                "cloud_rate_hz": 5.0, "voxel_size_m": 0.1, "compression": "zlib",
                "point_format": "xyz_f32_le", "coordinate_contract": "sensor+map_body+body_sensor",
            },
        )
        self.assertEqual(self.protocol.decode(self.protocol.encode(envelope)), envelope)

    def test_out_of_order_chunks_reassemble_and_duplicate_is_ignored(self):
        assembler = CloudFrameAssembler(self.config)
        chunks = self.chunks([(1, 2, 3), (4, 5, 6)], chunk_size=5)
        self.assertIsNone(assembler.push(chunks[-1]))
        self.assertIsNone(assembler.push(chunks[-1]))
        result = None
        for chunk in chunks[:-1]:
            result = assembler.push(chunk)
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result[0], [(1, 2, 3), (4, 5, 6)])

    def test_crc_failure_rejects_entire_frame(self):
        assembler = CloudFrameAssembler(self.config)
        chunk = self.chunks([(1, 2, 3)], chunk_size=100)[0]
        broken = self.envelope({**chunk.payload, "frame_crc32": 1})
        with self.assertRaises(MapBuildingProtocolError):
            assembler.push(broken)

    def test_missing_chunks_are_requested_and_closed_frames_ignore_late_duplicates(self):
        now = [10.0]
        config = replace(
            self.config, retransmit_delay_seconds=0.25, retransmit_max_attempts=2,
            frame_timeout_seconds=2.0,
        )
        assembler = CloudFrameAssembler(config, clock=lambda: now[0])
        chunks = self.chunks([(1, 2, 3), (4, 5, 6)], chunk_size=5)
        assembler.push(chunks[0])
        now[0] += 0.3
        requests = assembler.retransmit_requests()
        self.assertEqual(requests[0][0], 7)
        self.assertEqual(requests[0][2], 1)
        self.assertNotIn(0, requests[0][1])
        result = None
        for chunk in chunks[1:]:
            result = assembler.push(chunk)
        self.assertIsNotNone(result)
        self.assertIsNone(assembler.push(chunks[0]))

    def test_pose_and_extrinsic_are_composed(self):
        body = {**self.identity, "x": 10.0}
        sensor = {**self.identity, "y": 2.0}
        transformed = transform_sensor_points(np.asarray([[1.0, 0.0, 0.0]]), body, sensor)
        np.testing.assert_allclose(transformed, [[11.0, 2.0, 0.0]])

    def test_voxel_running_centroid_and_deterministic_preview(self):
        accumulator = VoxelMapAccumulator(1.0, 10, 1)
        accumulator.add(np.asarray([[0.1, 0.1, 0.1], [0.3, 0.3, 0.3], [1.2, 0, 0]]))
        np.testing.assert_allclose(accumulator.points()[0], [0.2, 0.2, 0.2])
        np.testing.assert_array_equal(accumulator.preview(), accumulator.preview())
        self.assertEqual(len(accumulator.points()), 2)

    def test_binary_pcd_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_binary_pcd(Path(directory) / "map.pcd", np.asarray([[1, 2, 3]], dtype=np.float32))
            data = path.read_bytes()
            self.assertIn(b"DATA binary\n", data)
            self.assertTrue(data.endswith(np.asarray([[1, 2, 3]], dtype="<f4").tobytes()))


if __name__ == "__main__":
    unittest.main()
