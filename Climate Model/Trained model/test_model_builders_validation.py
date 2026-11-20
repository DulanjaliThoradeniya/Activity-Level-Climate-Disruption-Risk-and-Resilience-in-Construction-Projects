import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import build_multi_model_forecasts as xgboost_builder
import build_remaining_climate_models as remaining_builder


class ModelBuilderValidationTests(unittest.TestCase):
    def setUp(self):
        last_day = date(2026, 11, 14)
        first_day = last_day - timedelta(days=199)
        self.records = [
            [(first_day + timedelta(days=index)).isoformat(), float(index % 7), None, None, None]
            for index in range(200)
        ]

    def check_result(self, result):
        split = result["validation"]["data_split"]
        self.assertEqual((split["training_count"], split["testing_count"]), (160, 40))
        self.assertEqual(result["validation"]["n"], 40)
        self.assertEqual(len(result["backtest"]), 40)
        self.assertEqual(result["forecast"][0]["date"], "2026-11-15")
        self.assertEqual(result["forecast"][-1]["date"], "2026-11-28")

    def test_xgboost_fits_only_the_training_portion(self):
        training_sizes = []

        class FakeModel:
            def fit(self, inputs, labels):
                training_sizes.append(len(labels))

            def predict(self, inputs):
                return np.full(len(inputs), 1.0)

        with patch.object(xgboost_builder, "model_for", return_value=FakeModel()):
            result = xgboost_builder.predict_target(self.records, "rainfall")

        self.assertEqual(training_sizes, [160 - 28])
        self.check_result(result)

    def test_remaining_models_do_not_refit_on_test_observations(self):
        training_sizes = []

        def fake_fit(values, dates, target):
            training_sizes.append(len(values))
            return lambda history_dates, history_values, future_dates: [1.0] * len(future_dates)

        frame = pd.DataFrame(
            self.records,
            columns=["date", "rainfall", "mean_temperature", "maximum_temperature", "minimum_temperature"],
        )
        with patch.object(remaining_builder, "fit_lightgbm", fake_fit):
            results = remaining_builder.predict_target(self.records, "rainfall", frame, ("lightgbm",))

        self.assertEqual(training_sizes, [160])
        self.check_result(results["lightgbm"])

    def test_neuralprophet_input_keeps_all_requested_history(self):
        first_day = date(2006, 1, 1)
        dates = [first_day + timedelta(days=index) for index in range(1000)]

        frame = remaining_builder._neuralprophet_frame([1.0] * len(dates), dates)

        self.assertEqual(len(frame), 1000)
        self.assertEqual(frame["ds"].iloc[0].date(), first_day)

    @unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch is not installed")
    def test_memory_bounded_training_matches_full_batch_update(self):
        import torch
        from torch import nn

        torch.set_num_threads(1)
        torch.manual_seed(2026)
        original = nn.Sequential(nn.Flatten(), nn.Linear(28, 8), nn.ReLU(), nn.Linear(8, 1))
        batched = copy.deepcopy(original)
        x = torch.randn(320, 28, 1)
        y = torch.randn(320, 1)
        loss_fn = nn.MSELoss()
        original_optimizer = torch.optim.Adam(original.parameters(), lr=0.005, weight_decay=1e-5)
        batched_optimizer = torch.optim.Adam(batched.parameters(), lr=0.005, weight_decay=1e-5)
        original_optimizer.zero_grad()
        loss_fn(original(x), y).backward()
        nn.utils.clip_grad_norm_(original.parameters(), 1.0)
        original_optimizer.step()

        remaining_builder.train_torch_full_batch(batched, batched_optimizer, loss_fn, x, y, 1)

        for expected, actual in zip(original.parameters(), batched.parameters()):
            torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-6)

    def test_failed_targets_keep_their_previous_saved_results(self):
        previous_result = {"forecast": [{"date": "2026-11-11", "value": 2.0}], "validation": {"mae": 3.0}}
        previous_model = {"stations": {"S1": {"targets": {"rainfall": previous_result}}}}
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.json"
            output_path = Path(directory) / "output.json"
            history_path.write_text(json.dumps({
                "schemaVersion": 1, "stationNames": {"S1": "Test station"},
                "stations": {"S1": self.records},
            }), encoding="utf-8")
            output_path.write_text(
                json.dumps({"models": {"xgboost": previous_model, "lightgbm": previous_model}}),
                encoding="utf-8",
            )
            with (
                patch.object(xgboost_builder, "HISTORY_PATH", history_path),
                patch.object(xgboost_builder, "OUTPUT_PATH", output_path),
                patch.object(xgboost_builder, "predict_target", return_value=None),
            ):
                with self.assertRaisesRegex(RuntimeError, "previous forecasts were preserved"):
                    xgboost_builder.build()
            with (
                patch.object(remaining_builder, "HISTORY_PATH", history_path),
                patch.object(remaining_builder, "OUTPUT_PATH", output_path),
                patch.object(remaining_builder, "predict_target", return_value={"lightgbm_error": {"error": "failed"}}),
            ):
                with self.assertRaisesRegex(RuntimeError, "previous forecasts were preserved"):
                    remaining_builder.build(("lightgbm",))
            result = json.loads(output_path.read_text(encoding="utf-8"))

        for model in ("xgboost", "lightgbm"):
            self.assertEqual(result["models"][model]["stations"]["S1"]["targets"]["rainfall"], previous_result)
            self.assertNotIn("status", result["models"][model])


if __name__ == "__main__":
    unittest.main()
