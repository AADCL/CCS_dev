import os
import sys
import unittest
import zlib

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ccs_monitor.map_building import CloudFrameAssembler, MapBuildingProtocol
from ccs_monitor.map_building_config import load_map_building_config
from epgeneral_multi_map.config import load_config
from epgeneral_multi_map.protocol import encode_cloud_chunks


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE = os.path.join(os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "device.yaml")


class GroundContractTests(unittest.TestCase):
    def test_current_ground_reassembles_frame_with_slice_extensions(self):
        edge = load_config(os.path.join(PACKAGE, "config", "multi_mapping.yaml"), DEVICE)
        ground_config = load_map_building_config()
        protocol = MapBuildingProtocol(ground_config)
        assembler = CloudFrameAssembler(ground_config)
        points = np.asarray([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]], dtype="<f4")
        transform = {"x": 0.0, "y": 0.0, "z": 0.0,
                     "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
        metadata = {
            "frame_id": 1, "sample_stamp_ns": 123, "point_count": len(points),
            "map_from_body": transform, "body_from_sensor": transform,
            "job_id": "job-1", "slice_id": 0, "slice_start_ns": 100,
            "slice_end_ns": 200, "frame_index": 0, "frame_count": 1,
            "partial": False, "error_tail": False, "truncated": False,
            "pose_before_stamp_ns": 120, "pose_after_stamp_ns": 126,
            "pose_max_error_ns": 3, "pose_interpolated": True,
        }
        result = None
        for datagram in encode_cloud_chunks(
                edge, {"map_id": "map-1", "session_id": "session-1"}, 0,
                metadata, zlib.compress(points.tobytes())):
            result = assembler.push(protocol.decode(datagram)) or result
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result[0], points)


if __name__ == "__main__":
    unittest.main()
