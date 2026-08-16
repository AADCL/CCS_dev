import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EDGE_CONFIG = ROOT / "edge_side_pkg" / "edge_device_config" / "config" / "device.yaml"


class EdgeDeviceAlignmentTests(unittest.TestCase):
    def test_shared_device_matches_ground_station_profile(self):
        edge = yaml.safe_load(EDGE_CONFIG.read_text(encoding="utf-8"))
        ground = json.loads((ROOT / "config" / "devices.json").read_text(encoding="utf-8"))
        self.assertEqual(edge["schema_version"], 1)
        identity = edge["device"]
        matching = [item for item in ground["devices"] if item["device_id"] == identity["id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["ip_address"], identity["ip"])


if __name__ == "__main__":
    unittest.main()
