import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ccs_monitor.battery_estimation import BatteryEstimator


class BatteryEstimatorTests(unittest.TestCase):
    def make_estimator(self, curve):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "battery.json"
        config.write_text(json.dumps({
            "schema_version": 1,
            "profiles": {"scout_mini": {
                "full_voltage": 30, "retention_days": 90, "curve": curve,
            }},
        }), encoding="utf-8")
        return BatteryEstimator(config, root / "history"), root

    def test_unconfigured_curve_records_voltage_without_inventing_percentage(self):
        estimator, root = self.make_estimator([])
        result = estimator.observe(
            "UGV_001", "scout_mini", 25.6,
            datetime(2026, 8, 25, 10, 1, 2, tzinfo=timezone.utc), True,
        )
        self.assertIsNone(result)
        records = json.loads((root / "history" / "UGV_001.json").read_text(encoding="utf-8"))
        self.assertEqual(records[0]["voltage_median"], 25.6)

    def test_curve_interpolates_and_full_voltage_is_clamped(self):
        estimator, _ = self.make_estimator([
            {"voltage": 24, "percentage": 0},
            {"voltage": 28, "percentage": 80},
        ])
        self.assertEqual(estimator.percentage(26), 40)
        self.assertEqual(estimator.percentage(30), 100)

    def test_go2_is_not_estimated(self):
        estimator, _ = self.make_estimator([])
        self.assertIsNone(estimator.observe(
            "QRD_001", "go2_edu", 28,
            datetime.now(timezone.utc), True,
        ))


if __name__ == "__main__":
    unittest.main()
