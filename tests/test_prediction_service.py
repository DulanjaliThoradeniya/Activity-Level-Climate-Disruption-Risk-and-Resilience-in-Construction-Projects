from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from risk_engine.prediction_service import (
    ActivityDisruptionPredictor,
    canonical_request,
    request_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def _request() -> dict:
    return {
        "project_id": "test-project",
        "location": "Changi",
        "nea_station_id": "S24",
        "work_hours_per_day": 6,
        "activities": [
            {
                "id": "activity-1",
                "wbs_code": "1.1.1",
                "planned_productive_hours": 6,
                "exposure": "outdoor",
                "heat_sensitivity": "medium",
                "rain_sensitivity": "high",
                "mandatory_stop_work_dates": ["2026-11-03"],
            }
        ],
        "forecast_days": [
            {
                "date": "2026-11-02",
                "rainfall_mm": 4.0,
                "mean_temperature_c": 29.0,
                "maximum_temperature_c": 32.0,
                "minimum_temperature_c": 26.0,
                "recent_cumulative_rainfall_mm": 18.0,
                "recent_rainfall_window_days": 7,
                "forecast_source": "NEA station forecast",
                "forecast_model_by_target": {"rainfall": "xgboost"},
                "forecast_uncertainty": {"rainfall": {"lower": 0.0, "upper": 12.0}},
            },
            {
                "date": "2026-11-03",
                "rainfall_mm": 8.0,
                "mean_temperature_c": 30.0,
            },
        ],
    }


class _Encoder:
    def transform(self, rows):
        return rows


class _Estimator:
    def predict(self, rows):
        return np.full(len(rows), 1.5)


class PredictionServiceTests(unittest.TestCase):
    def test_canonical_request_preserves_station_and_working_hours(self) -> None:
        canonical = canonical_request(_request())
        self.assertEqual(canonical["nea_station_id"], "S24")
        self.assertEqual(canonical["work_hours_per_day"], 6)
        self.assertEqual(
            canonical["activities"][0]["planned_productive_hours"], 6
        )
        self.assertEqual(canonical["forecast_days"][0]["recent_cumulative_rainfall_mm"], 18)
        self.assertEqual(
            canonical["activities"][0]["mandatory_stop_work_dates"],
            ["2026-11-03"],
        )

    def test_fingerprint_ignores_input_order_but_tracks_changes(self) -> None:
        request = _request()
        reordered = {**request, "forecast_days": list(reversed(request["forecast_days"]))}
        self.assertEqual(request_fingerprint(request), request_fingerprint(reordered))
        changed = {**request, "nea_station_id": "S119"}
        self.assertNotEqual(request_fingerprint(request), request_fingerprint(changed))

    def test_request_rejects_duplicate_activity_ids_and_invalid_hours(self) -> None:
        request = _request()
        request["activities"].append(dict(request["activities"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            canonical_request(request)
        request = _request()
        request["work_hours_per_day"] = 0
        with self.assertRaisesRegex(ValueError, "work_hours_per_day"):
            canonical_request(request)

    def test_prediction_output_keeps_wsh_separate_and_stop_work_hard(self) -> None:
        predictor = ActivityDisruptionPredictor.__new__(ActivityDisruptionPredictor)
        predictor.history = {"stations": {"S24": {}}}
        predictor.station_mapping = {"Changi": {"code": "S24"}}
        predictor.activity_mapping = {"1.1.1": "application-activity-1"}
        predictor.graph = object()
        predictor.encoder = _Encoder()
        predictor.estimator = _Estimator()
        predictor.model_profile = "SYNTHETIC_DEMO"
        predictor.model_id = "test__kg_enhanced"
        predictor.validation_mae = 0.5
        predictor.interval_half_width = 1.0
        graph_features = {
            "activity_type": "test",
            "stage": "Test stage",
            "kg_consequences": "[]",
            "kg_hazard_ids": "[]",
            "kg_candidate_control_ids": "[]",
            "kg_candidate_intervention_ids": "[]",
            "kg_evidence_statuses": "[]",
        }
        with patch(
            "risk_engine.prediction_service._knowledge_features",
            return_value=graph_features,
        ):
            result = predictor.predict(_request())

        self.assertEqual(result["model_status"], "SYNTHETIC_DEMO")
        self.assertFalse(result["real_safety_decision_use_permitted"])
        self.assertFalse(result["wsh_risk_classification_included"])
        self.assertEqual(result["productivity_profiles"]["activity-1"][0]["productivityFactor"], 0.75)
        stop_day = result["productivity_profiles"]["activity-1"][1]
        self.assertTrue(stop_day["mandatoryStopWork"])
        self.assertEqual(stop_day["productivityFactor"], 0)
        self.assertEqual(
            stop_day["wshClassification"], "NOT_ASSESSED_BY_PRODUCTIVITY_MODEL"
        )

    def test_synthetic_profile_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary) / "synthetic_demo"
            profile_dir.mkdir()
            with self.assertRaises(PermissionError):
                ActivityDisruptionPredictor(
                    model_root=temporary,
                    profile="synthetic_demo",
                    allow_synthetic_demo=False,
                )

    def test_inference_skips_rule_baseline_without_a_joblib_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary) / "synthetic_demo"
            profile_dir.mkdir()
            (profile_dir / "evaluation_metadata.json").write_text(
                '{"model_profile":"SYNTHETIC_DEMO","model_comparison":'
                '{"rule_baseline__kg_enhanced":{"overall":{"mae":0.1}}}}',
                encoding="utf-8",
            )
            with (
                patch("risk_engine.prediction_service.load_nea_history", return_value={}),
                patch("risk_engine.prediction_service.load_station_mapping", return_value={}),
                patch("risk_engine.prediction_service.load_knowledge_graph"),
                patch(
                    "risk_engine.prediction_service.build_activity_mapping",
                    return_value=type("Mapping", (), {"mapping": {}})(),
                ),
                self.assertRaisesRegex(ValueError, "No evaluated KG-enhanced model"),
            ):
                ActivityDisruptionPredictor(
                    model_root=temporary,
                    profile="synthetic_demo",
                    allow_synthetic_demo=True,
                )


if __name__ == "__main__":
    unittest.main()
