import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import refresh_station_forecasts as refresh
from refresh_station_forecasts import valid_result


class StationForecastValidationTests(unittest.TestCase):
    def test_requires_exact_dates_and_finite_values(self):
        dates = [f"2026-11-{day:02d}" for day in range(11, 25)]
        result = {
            "data_end": "2026-11-10",
            "forecast": [{"date": day, "value": 1.0} for day in dates],
        }
        self.assertTrue(valid_result(result, "2026-11-10", dates))
        self.assertFalse(valid_result(result, "2026-11-09", dates))
        result["forecast"][3]["value"] = float("nan")
        self.assertFalse(valid_result(result, "2026-11-10", dates))
        result["forecast"][3]["value"] = 1.0
        result["forecast"][3]["date"] = dates[2]
        self.assertFalse(valid_result(result, "2026-11-10", dates))

    def test_rainfall_refresh_does_not_require_temperature_input(self):
        dates = [f"2026-11-{day:02d}" for day in range(11, 25)]
        result = {"data_end": "2026-11-10", "forecast": [{"date": day, "value": 1.0} for day in dates]}
        xgb = SimpleNamespace(predict_target=lambda records, target: dict(result))
        other = SimpleNamespace(predict_target=lambda records, target, frame, models: {model: dict(result) for model in models})
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history.json"
            output = Path(directory) / "forecasts.json"
            history.write_text(json.dumps({"stations": {"S94": [["2026-11-10", 2.0, None, None, None]]}}))
            output.write_text(json.dumps({"models": {model: {"stations": {}} for model in refresh.MODELS}}))
            with patch.object(refresh, "HISTORY", history), patch.object(refresh, "OUTPUT", output), \
                 patch.object(refresh, "load_module", side_effect=[xgb, other]), \
                 patch("sys.argv", ["refresh_station_forecasts.py", "--through", "2026-11-10", "--station", "S94"]):
                with self.assertRaises(SystemExit):
                    refresh.main()
            saved = json.loads(output.read_text(encoding="utf-8"))
            for model in refresh.MODELS:
                self.assertEqual(saved["models"][model]["stations"]["S94"]["targets"]["rainfall"]["data_end"], "2026-11-10")


if __name__ == "__main__":
    unittest.main()
