import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from ccs_monitor.map_building_v2 import CloudFragmentDescriptor, load_preview_pcd
except ModuleNotFoundError:
    CloudFragmentDescriptor = None
    load_preview_pcd = None
from epgeneral_map_stream.artifacts import write_binary_pcd


@unittest.skipIf(CloudFragmentDescriptor is None, "ground package is unavailable")
class GroundContractTests(unittest.TestCase):
    def test_ground_station_reads_edge_binary_pcd_fragment(self):
        points = np.random.RandomState(7).normal(size=(1000, 3)).astype("<f4")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fragment.pcd")
            metadata = write_binary_pcd(path, points)
            descriptor = CloudFragmentDescriptor(
                1, "http://127.0.0.1/fragment.pcd?token=x",
                metadata["byte_count"], metadata["sha256"], len(points),
                "lio_odom", 100, 200, datetime.now(timezone.utc),
            )
            loaded = load_preview_pcd(Path(path), descriptor)
        np.testing.assert_allclose(loaded, points, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
