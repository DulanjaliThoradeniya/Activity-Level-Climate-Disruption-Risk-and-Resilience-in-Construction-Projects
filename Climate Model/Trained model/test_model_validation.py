import unittest
from datetime import date, timedelta

from model_validation import (
    HISTORY_END,
    HISTORY_START,
    chronological_split,
    filter_observations,
    preserve_published_validation,
    record_validation_version,
    regression_metrics,
    rolling_origin_predictions,
)


class ModelValidationTests(unittest.TestCase):
    def test_filters_to_requested_history_window_and_ignores_missing_values(self):
        dates, values = filter_observations(
            [
                HISTORY_START - timedelta(days=1),
                HISTORY_START,
                HISTORY_END,
                HISTORY_END + timedelta(days=1),
            ],
            [12.0, 2.0, 3.0, 4.0],
            [HISTORY_END],
        )

        self.assertEqual(dates, [HISTORY_START])
        self.assertEqual(values, [2.0])

    def test_chronological_split_uses_floor_for_training_count(self):
        dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(11)]

        split, metadata = chronological_split(dates)

        self.assertEqual(split, 8)
        self.assertEqual(metadata["training_count"], 8)
        self.assertEqual(metadata["testing_count"], 3)
        self.assertEqual(metadata["training_end_date"], dates[7].isoformat())
        self.assertEqual(metadata["testing_start_date"], dates[8].isoformat())

    def test_rolling_predictions_advance_without_refitting_on_test_values(self):
        dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(100)]
        values = [float(index) for index in range(100)]
        model_inputs = []

        def predictor(history_dates, history_values, future_dates):
            model_inputs.append((list(history_dates), list(history_values), list(future_dates)))
            return [history_values[-1]] * 14

        rows = rolling_origin_predictions(dates, values, 80, predictor)

        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row["date"] for row in rows}), 20)
        self.assertEqual(model_inputs[0][1][-1], values[79])
        self.assertEqual(model_inputs[1][1][-1], values[93])
        self.assertEqual(model_inputs[0][2][0], dates[80])
        self.assertEqual(model_inputs[1][2][0], dates[94])
        self.assertEqual(set(row["lead"] for row in rows), set(range(1, 15)))

    def test_nonfinite_heldout_predictions_fail_instead_of_skipping_test_data(self):
        dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(20)]

        with self.assertRaisesRegex(ValueError, "Non-finite prediction"):
            rolling_origin_predictions(
                dates,
                [1.0] * len(dates),
                16,
                lambda history_dates, history_values, future_dates: [float("nan")] * len(future_dates),
            )

    def test_rainfall_metrics_use_thresholds_and_temperature_keeps_negative_r2(self):
        rainfall = regression_metrics([0.0, 10.0, 30.0], [0.0, 0.0, 40.0], "rainfall")
        temperature = regression_metrics([1.0, 2.0], [10.0, 0.0], "mean_temperature")

        self.assertEqual(rainfall["occurrence"]["threshold_mm"], 1.0)
        self.assertEqual(rainfall["occurrence"]["precision"], 1.0)
        self.assertEqual(rainfall["occurrence"]["recall"], 0.5)
        self.assertEqual(rainfall["heavy_rain"]["mae"], 10.0)
        self.assertIsNone(rainfall["mape"])
        self.assertLess(temperature["r2"], 0.0)

    def test_validation_versions_preserve_metrics_without_copying_forecasts(self):
        payload = {
            "generatedAt": "baseline",
            "models": {
                "xgboost": {
                    "stations": {
                        "S1": {
                            "station_name": "Station 1",
                            "targets": {
                                "rainfall": {
                                    "forecast": [{"date": "2020-01-01", "value": 4.0}],
                                    "validation": {"mae": 2.0},
                                }
                            },
                        }
                    }
                }
            },
        }

        preserve_published_validation(payload)
        payload["models"]["xgboost"]["stations"]["S1"]["targets"]["rainfall"]["validation"]["mae"] = 9.0
        version_id = record_validation_version(payload, payload["models"])

        baseline = payload["validationHistory"]["published-baseline"]
        self.assertEqual(baseline["generatedAt"], "baseline")
        self.assertEqual(
            baseline["models"]["xgboost"]["stations"]["S1"]["targets"]["rainfall"]["validation"]["mae"],
            2.0,
        )
        self.assertNotIn(
            "forecast",
            baseline["models"]["xgboost"]["stations"]["S1"]["targets"]["rainfall"],
        )
        self.assertEqual(payload["activeValidationVersion"], version_id)
        self.assertTrue(version_id.startswith("80-20-"))


if __name__ == "__main__":
    unittest.main()
