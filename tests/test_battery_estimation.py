import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ccs_monitor.battery_estimation import BatteryEstimator, DEFAULT_PROFILE_PAYLOAD


class BatteryEstimatorTests(unittest.TestCase):
    def make_estimator(self, payload=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "battery.json"
        config.write_text(
            json.dumps(payload or DEFAULT_PROFILE_PAYLOAD, ensure_ascii=False),
            encoding="utf-8",
        )
        return BatteryEstimator(config, root / "history"), root, config

    def test_wheeltec_curve_anchors_interpolation_and_clamping(self):
        estimator, _, _ = self.make_estimator()
        expected = (
            (20.0, 0), (20.8, 10), (21.5, 25), (22.3, 45),
            (23.1, 65), (24.0, 82), (24.8, 94), (25.5, 100),
        )
        for voltage, percentage in expected:
            self.assertEqual(estimator.percentage("wheeltec_r550p", voltage), percentage)
        self.assertEqual(estimator.percentage("wheeltec_r550p", 19.0), 0)
        self.assertEqual(estimator.percentage("wheeltec_r550p", 26.0), 100)
        self.assertAlmostEqual(estimator.percentage("wheeltec_r550p", 20.4), 5.0)

    def test_scout_curve_anchors_and_interpolation(self):
        estimator, _, _ = self.make_estimator()
        self.assertEqual(estimator.percentage("scout_mini", 24.5), 0)
        self.assertEqual(estimator.percentage("scout_mini", 29.4), 100)
        self.assertAlmostEqual(estimator.percentage("scout_mini", 25.55), 17.5)

    def test_observe_uses_rolling_median_and_keeps_minute_history(self):
        estimator, root, _ = self.make_estimator()
        stamp = datetime(2026, 8, 25, 10, 1, 2, tzinfo=timezone.utc)
        estimator.observe("UGV_003", "wheeltec_r550p", 20.0, stamp, True)
        estimator.observe("UGV_003", "wheeltec_r550p", 25.5, stamp, True)
        result = estimator.observe("UGV_003", "wheeltec_r550p", 21.5, stamp, True)
        self.assertEqual(result, 25.0)
        records = json.loads(
            (root / "history" / "UGV_003.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["profile"], "wheeltec_r550p")
        self.assertEqual(records[0]["voltage_median"], 21.5)

    def test_disabled_unknown_and_invalid_voltage_do_not_estimate(self):
        estimator, _, _ = self.make_estimator()
        stamp = datetime.now(timezone.utc)
        for profile, voltage in (
            ("disabled", 25.0), ("go2_edu", 25.0),
            ("wheeltec_r550p", None), ("wheeltec_r550p", 0),
            ("wheeltec_r550p", math.nan), ("wheeltec_r550p", "bad"),
        ):
            self.assertIsNone(estimator.observe("device", profile, voltage, stamp, True))

    def test_schema_one_is_migrated_and_preserves_calibrated_scout_curve(self):
        payload = {
            "schema_version": 1,
            "profiles": {"scout_mini": {
                "full_voltage": 30,
                "retention_days": 90,
                "curve": [
                    {"voltage": 24, "percentage": 0},
                    {"voltage": 28, "percentage": 80},
                ],
            }},
        }
        estimator, _, config = self.make_estimator(payload)
        self.assertEqual(estimator.percentage("scout_mini", 26), 40)
        self.assertEqual(estimator.percentage("scout_mini", 30), 100)
        migrated = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], 2)
        self.assertIn("wheeltec_r550p", migrated["profiles"])
        self.assertEqual(migrated["profiles"]["scout_mini"]["retention_days"], 2)
        self.assertEqual(migrated["profiles"]["scout_mini"]["sample_window"], 15)


if __name__ == "__main__":
    unittest.main()
