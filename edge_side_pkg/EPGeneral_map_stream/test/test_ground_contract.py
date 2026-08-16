import os
import unittest
import zlib

import numpy as np

from ccs_monitor.map_building import CloudFrameAssembler, MapBuildingProtocol
from ccs_monitor.map_building_config import load_map_building_config
from epgeneral_map_stream.config import load_config
from epgeneral_map_stream.protocol import encode_cloud_chunks


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = os.path.join(os.path.dirname(PACKAGE), "epgeneral_device_config", "config", "device.yaml")


class GroundContractTests(unittest.TestCase):
    def test_ground_station_reassembles_edge_cloud(self):
        edge_config = load_config(MAPPING, DEVICE)
        ground_config = load_map_building_config()
        protocol = MapBuildingProtocol(ground_config)
        assembler = CloudFrameAssembler(ground_config)
        identity = {"map_id": "map-contract", "session_id": "c" * 32}
        transform = {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
        points = np.random.RandomState(7).normal(size=(1000, 3)).astype("<f4")
        compressed = zlib.compress(points.tobytes())
        datagrams = encode_cloud_chunks(edge_config, identity, 0, {
            "frame_id": 1,
            "sample_stamp_ns": 123,
            "point_count": len(points),
            "map_from_body": transform,
            "body_from_sensor": transform,
        }, compressed)
        completed = None
        for datagram in reversed(datagrams):
            completed = assembler.push(protocol.decode(datagram)) or completed
        self.assertIsNotNone(completed)
        np.testing.assert_allclose(completed[0], points, rtol=0, atol=0)
        self.assertEqual(completed[1][0], 123)
