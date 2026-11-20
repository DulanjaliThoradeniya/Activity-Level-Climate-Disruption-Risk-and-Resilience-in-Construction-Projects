import json
import tempfile
import unittest
from pathlib import Path

from climate_history_validation import load_history
from validate_climate_release import validate_forecast


class ClimateHistoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "history.json"
        self.history = {
            "schemaVersion": 1,
            "stationNames": {"S1": "Station"},
            "stations": {"S1": [["2026-11-18", 1.0, 27.0, 30.0, 25.0]]},
        }

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_rejects_empty_conflict_and_invalid_utf8_with_path(self):
        for content, message in (
            (b"", "empty"),
            (b"<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> branch\n", "merge conflict"),
            (b"\xff", "UTF-8"),
        ):
            self.path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, message) as caught:
                load_history(self.path)
            self.assertIn(str(self.path), str(caught.exception))

    def test_rejects_missing_stations_duplicate_dates_and_invalid_values(self):
        self.write(self.history)
        with self.assertRaisesRegex(ValueError, "missing required stations"):
            load_history(self.path, {"S1", "S2"})
        self.history["stations"]["S1"].append(self.history["stations"]["S1"][0][:])
        self.write(self.history)
        with self.assertRaisesRegex(ValueError, "duplicate or unordered"):
            load_history(self.path)
        self.history["stations"]["S1"].pop()
        self.history["stations"]["S1"][0][1] = -1
        self.write(self.history)
        with self.assertRaisesRegex(ValueError, "negative rainfall"):
            load_history(self.path)

    def test_requires_full_forecast_window_and_finite_values(self):
        dates = [f"2026-11-{day:02d}" for day in range(1, 15)]
        result = {
            "data_end": "2026-10-31",
            "forecast": [{"date": day, "value": 1.0, "lower": 0.0, "upper": 2.0} for day in dates],
        }
        validate_forecast(result, dates, "xgboost/S1/rainfall", "2026-10-31")
        result["forecast"].pop()
        with self.assertRaisesRegex(ValueError, "14 consecutive"):
            validate_forecast(result, dates, "xgboost/S1/rainfall", "2026-10-31")


if __name__ == "__main__":
    unittest.main()
