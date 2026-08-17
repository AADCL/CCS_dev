import io
import os
import tempfile
import unittest

import numpy as np
import yaml

from epgeneral_multi_map_fusion.config import ConfigError, load_config
from epgeneral_multi_map_fusion.fusion import FusionError, run_fusion
from epgeneral_multi_map_fusion.pcd import load_xyz


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(PACKAGE, "config", "fusion.yaml")
IDENTITY = {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}


def _pose(x=0.0, y=0.0, z=0.0):
    value = dict(IDENTITY)
    value.update({"x": x, "y": y, "z": z})
    return value


def _write_ascii_pcd(path, points):
    array = np.asarray(points, dtype=np.float64)
    with io.open(path, "w", encoding="ascii") as stream:
        stream.write(
            "VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
            "WIDTH %d\nHEIGHT 1\nPOINTS %d\nDATA ascii\n" % (len(array), len(array))
        )
        for point in array:
            stream.write("%.9f %.9f %.9f\n" % tuple(point))


class FusionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = self.temporary.name

    def tearDown(self):
        self.temporary.cleanup()

    def _map(self, map_id, points):
        path = os.path.join(self.root, map_id + ".pcd")
        _write_ascii_pcd(path, points)
        return {"map_id": map_id, "pcd_path": path}

    def _run(self, maps, placements):
        output = os.path.join(self.root, "fused.pcd")
        report = os.path.join(self.root, "fusion.json")
        job = {
            "schema_version": 1,
            "reference_map_id": maps[0]["map_id"],
            "maps": maps,
            "placements": placements,
            "output": {"pcd_path": output, "report_path": report},
        }
        job_path = os.path.join(self.root, "job.yaml")
        with io.open(job_path, "w", encoding="utf-8") as stream:
            yaml.safe_dump(job, stream, default_flow_style=False)
        return run_fusion(CONFIG, job_path), output, report

    def test_three_calibrated_maps_follow_a_chain(self):
        points = np.asarray(
            [[0.0, 0.0, 0.0], [0.4, 0.0, 0.0], [0.0, 0.4, 0.0], [0.0, 0.0, 0.4]]
        )
        maps = [self._map("a", points), self._map("b", points), self._map("c", points)]
        placements = [
            {
                "source_map_id": "b",
                "target_map_id": "a",
                "kind": "calibration",
                "T_target_from_source": _pose(x=2.0),
            },
            {
                "source_map_id": "c",
                "target_map_id": "b",
                "kind": "calibration",
                "T_target_from_source": _pose(x=3.0),
            },
        ]
        result, output, report = self._run(maps, placements)

        self.assertTrue(os.path.isfile(output))
        self.assertTrue(os.path.isfile(report))
        self.assertEqual(result["output"]["point_count"], 12)
        self.assertAlmostEqual(result["T_reference_from_map"]["b"][0][3], 2.0)
        self.assertAlmostEqual(result["T_reference_from_map"]["c"][0][3], 5.0)
        self.assertEqual(len(load_xyz(output, 100)), 12)

    def test_registration_refines_a_measured_initial_pose(self):
        target = np.asarray(
            [
                [
                    0.123 + x * 1.3 + y * 0.02,
                    0.234 + y * 0.9 + z * 0.03,
                    0.345 + z * 1.1 + x * 0.01,
                ]
                for x in range(4)
                for y in range(5)
                for z in range(5)
            ],
            dtype=np.float64,
        )
        translation = np.asarray([0.2, -0.1, 0.05])
        maps = [self._map("target", target), self._map("source", target - translation)]
        placements = [
            {
                "source_map_id": "source",
                "target_map_id": "target",
                "kind": "registration",
                "T_target_from_source": IDENTITY,
            }
        ]
        result, output, _ = self._run(maps, placements)

        recovered = np.asarray(result["T_reference_from_map"]["source"])[:3, 3]
        self.assertTrue(np.allclose(recovered, translation, atol=1e-5))
        self.assertGreater(result["placements"][0]["quality"]["source_fitness"], 0.99)
        self.assertEqual(len(load_xyz(output, 1000)), len(target))

    def test_disconnected_placement_graph_is_rejected(self):
        points = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        maps = [self._map(name, points) for name in ("a", "b", "c", "d")]
        placements = [
            {"source_map_id": "b", "target_map_id": "a", "T_target_from_source": IDENTITY},
            {"source_map_id": "c", "target_map_id": "a", "T_target_from_source": IDENTITY},
            {"source_map_id": "c", "target_map_id": "b", "T_target_from_source": IDENTITY},
        ]
        with self.assertRaisesRegex(FusionError, "disconnected"):
            self._run(maps, placements)

    def test_more_than_eight_maps_have_no_fixed_limit(self):
        points = np.asarray([[0, 0, 0], [0, 0.2, 0], [0, 0, 0.2]], dtype=np.float64)
        maps = [self._map("map%d" % index, points) for index in range(9)]
        placements = [
            {
                "source_map_id": item["map_id"],
                "target_map_id": maps[0]["map_id"],
                "T_target_from_source": _pose(x=index * 2.0),
            }
            for index, item in enumerate(maps[1:], 1)
        ]
        result, output, _ = self._run(maps, placements)
        self.assertTrue(os.path.isfile(output))
        self.assertEqual(len(result["T_reference_from_map"]), 9)

    def test_sample_config_and_invalid_fitness(self):
        self.assertNotIn("max_input_maps", load_config(CONFIG))
        with io.open(CONFIG, "r", encoding="utf-8") as stream:
            payload = stream.read().replace("min_source_fitness: 0.20", "min_source_fitness: 2.0")
        path = os.path.join(self.root, "bad.yaml")
        with io.open(path, "w", encoding="utf-8") as stream:
            stream.write(payload)
        with self.assertRaisesRegex(ConfigError, "fitness"):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
