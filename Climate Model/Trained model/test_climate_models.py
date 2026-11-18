import unittest
from datetime import date, timedelta

import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from climate_models import (
    HORIZON_DAYS,
    RAIN_THRESHOLDS,
    RAINFALL_EVALUATION_DAYS,
    _context_features,
    _feature_names,
    _index_series,
    _occurrence_values,
    _period_origins,
    _rainfall_baselines,
    _select_validation_winner,
    _series_maps,
)


class ForecastLeakageTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        start = date(2023, 1, 1)
        for offset in range(500):
            observed = start + timedelta(days=offset)
            self.records.append(
                [
                    observed.isoformat(),
                    float(offset % 3 == 0) * (offset % 20),
                    28.0 + offset % 4,
                    31.0 + offset % 5,
                    25.0 + offset % 3,
                ]
            )
        self.maps, _ = _series_maps(self.records)
        self.origin = start + timedelta(days=450)
        self.forecast_date = self.origin + timedelta(days=5)

    def test_forecast_features_ignore_observations_after_origin(self):
        initial = _context_features(
            _index_series(self.maps),
            self.origin,
            self.forecast_date,
            5,
        )
        changed_future = {
            name: {
                observed_date: (
                    value if observed_date <= self.origin else value + 10000
                )
                for observed_date, value in values.items()
            }
            for name, values in self.maps.items()
        }
        after_change = _context_features(
            _index_series(changed_future),
            self.origin,
            self.forecast_date,
            5,
        )
        np.testing.assert_allclose(initial, after_change, equal_nan=True)

    def test_rainfall_baselines_ignore_future_observations(self):
        forecast_dates = [
            self.origin + timedelta(days=lead)
            for lead in range(1, 15)
        ]
        baseline_before = _rainfall_baselines(self.maps, self.origin, forecast_dates)
        changed_future = {
            name: {
                observed_date: (
                    value if observed_date <= self.origin else value + 10000
                )
                for observed_date, value in values.items()
            }
            for name, values in self.maps.items()
        }
        baseline_after = _rainfall_baselines(changed_future, self.origin, forecast_dates)
        self.assertEqual(baseline_before, baseline_after)
        self.assertTrue(all(point["value"] >= 0 for point in baseline_before["no_rain"]))

    def test_feature_names_match_generated_features(self):
        features = _context_features(
            _index_series(self.maps),
            self.origin,
            self.forecast_date,
            5,
        )
        self.assertEqual(len(_feature_names()), len(features))

    def test_final_test_origins_are_non_overlapping_14_day_folds(self):
        origins = _period_origins(self.origin, RAINFALL_EVALUATION_DAYS)
        self.assertEqual(len(origins), RAINFALL_EVALUATION_DAYS // HORIZON_DAYS)
        self.assertTrue(
            all(
                (later - earlier).days == HORIZON_DAYS
                for earlier, later in zip(origins, origins[1:])
            )
        )

    def test_threshold_search_and_occurrence_confusion_metrics(self):
        self.assertEqual(len(RAIN_THRESHOLDS), 17)
        self.assertEqual((RAIN_THRESHOLDS[0], RAIN_THRESHOLDS[-1]), (0.1, 0.9))
        metrics = _occurrence_values([0.0, 2.0, 0.0, 3.0], [0.2, 0.7, 0.8, 0.4], 0.6)
        self.assertEqual(metrics["threshold"], 0.6)
        self.assertEqual(metrics["confusion_matrix"]["false_positive"], 1)
        self.assertEqual(metrics["confusion_matrix"]["false_negative"], 1)

    def test_validation_ties_prefer_simpler_baseline(self):
        candidates = {
            "no_rain": 4.0,
            "seasonal": 4.2,
            "prophet": 4.5,
            "xgboost_original": 4.0,
            "tuned_pipeline": 4.0,
        }
        self.assertEqual(_select_validation_winner(candidates), "no_rain")
        candidates["tuned_pipeline"] = 3.9
        self.assertEqual(_select_validation_winner(candidates), "tuned_pipeline")


if __name__ == "__main__":
    unittest.main()
