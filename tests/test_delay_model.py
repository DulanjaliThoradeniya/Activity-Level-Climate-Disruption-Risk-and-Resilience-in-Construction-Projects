from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from risk_engine.delay_model import (
    FeatureEncoder,
    RuleBaseline,
    chronological_event_split,
    evaluate_predictions,
    project_event_holdout,
    target_values,
    weather_event_ids,
)
from risk_engine.evaluate_delay_model import evaluate_prediction_file
from risk_engine.feature_builder import DATASET_FIELDS, DEFAULT_HISTORY_PATH
from risk_engine.train_delay_model import train_delay_models, validate_training_data

ROOT = Path(__file__).resolve().parents[1]


def _row(day: date, index: int, project_id: str = "SYNTH-1") -> dict:
    rainfall = 14.0 if day.day % 12 in {1, 2} else 0.0
    planned = 8.0
    loss = min(3.0, max(0.0, (rainfall - 5.0) * 0.04))
    values = {field: None for field in DATASET_FIELDS}
    values.update(
        {
            "project_id": project_id,
            "project_name": "Synthetic test project",
            "wbs_code": f"SP-{index % 6 + 1:02d}",
            "application_activity_id": f"wbs_sp_{index % 6 + 1:02d}",
            "activity_name": f"Activity {index % 6 + 1}",
            "date": day.isoformat(),
            "nea_station_id": "S24",
            "nea_station_name": "Changi Meteorological Station",
            "rainfall_mm": rainfall,
            "mean_temperature_c": 30.0,
            "maximum_temperature_c": 34.0,
            "minimum_temperature_c": 25.0,
            "recent_cumulative_rainfall_mm": 4.0,
            "recent_rainfall_window_days": 7,
            "recent_rainfall_window_end": (day - timedelta(days=1)).isoformat(),
            "activity_exposure": "outdoor",
            "heat_sensitivity": "medium",
            "rain_sensitivity": "high",
            "activity_type": "SP",
            "stage": "Site preparation",
            "kg_hazard_ids": '["HZ-RAIN"]',
            "kg_hazards": '[{"id":"HZ-RAIN","label":"Rain","evidence_status":"CITED"}]',
            "kg_consequences": '[{"id":"C-1","kind":"performance","label":"Delay","source_ids":[]}]',
            "kg_candidate_control_ids": '["CTRL-1"]',
            "kg_candidate_intervention_ids": '[]',
            "kg_evidence_statuses": '["CITED"]',
            "site_drainage_condition": "reported",
            "ground_condition": "unknown",
            "existing_controls": '["drainage"]',
            "existing_controls_status": "PROJECT_REPORTED",
            "planned_productive_hours": planned,
            "actual_productive_hours": planned - loss,
            "weather_attributable_lost_hours": loss,
            "other_disruption_causes": "[]",
            "other_disruption_hours": 0.0,
            "weather_data_source": "NEA Historical Daily Records",
            "outcome_data_source": "synthetic test assumptions",
            "data_source": "synthetic test assumptions",
            "weather_evidence_status": "NEA_HISTORICAL_STATION_RECORD",
            "outcome_evidence_status": "SYNTHETIC_DEMONSTRATION_NOT_EMPIRICAL",
            "evidence_status": "SYNTHETIC_DEMONSTRATION_NOT_EMPIRICAL",
            "is_synthetic": True,
        }
    )
    return values


class DelayModelTests(unittest.TestCase):
    def test_feature_encoder_fits_imputation_and_category_vocabulary_on_training(self) -> None:
        training = [
            {
                "rainfall_mm": 1.0,
                "planned_productive_hours": 8.0,
                "nea_station_id": "S24",
                "stage": "early",
                "kg_hazard_ids": '["HZ-A"]',
                "kg_consequences": "[]",
            },
            {
                "rainfall_mm": None,
                "planned_productive_hours": 8.0,
                "nea_station_id": "S24",
                "stage": "early",
                "kg_hazard_ids": '["HZ-A"]',
                "kg_consequences": "[]",
            },
        ]
        encoder = FeatureEncoder.create("kg_enhanced")
        encoder.fit(training)
        schema = encoder.schema()
        self.assertEqual(schema["numeric_medians_fit_on_training_only"]["rainfall_mm"], 1.0)
        unseen = {
            **training[0],
            "stage": "unseen validation stage",
            "kg_hazard_ids": '["HZ-UNSEEN"]',
        }
        transformed = encoder.transform([unseen])
        self.assertEqual(transformed.shape[1], len(schema["encoded_feature_names"]))
        self.assertNotIn("stage=unseen validation stage", schema["encoded_feature_names"])

    def test_rule_baseline_is_transparent_and_bounded_by_planned_hours(self) -> None:
        prediction = RuleBaseline().predict(
            [
                {
                    "rainfall_mm": 1000,
                    "mean_temperature_c": 50,
                    "planned_productive_hours": 2,
                    "rain_sensitivity": "high",
                    "heat_sensitivity": "high",
                    "activity_exposure": "outdoor",
                }
            ]
        )
        self.assertEqual(float(prediction[0]), 2.0)
        self.assertIn("ILLUSTRATIVE_ASSUMPTIONS", RuleBaseline().assumptions()["evidence_status"])

    def test_target_requires_weather_attributed_hours_and_planned_bounds(self) -> None:
        self.assertEqual(target_values([_row(date(2025, 1, 1), 0)]).shape, (1,))
        invalid = _row(date(2025, 1, 1), 0)
        invalid["weather_attributable_lost_hours"] = 9
        with self.assertRaisesRegex(ValueError, "outside"):
            target_values([invalid])

    def test_event_split_keeps_dates_and_rain_events_out_of_both_partitions(self) -> None:
        start = date(2025, 1, 1)
        rows = [
            _row(start + timedelta(days=index), index)
            for index in range(100)
        ]
        event_ids = weather_event_ids(rows)
        self.assertEqual(
            event_ids["2025-01-01"],
            event_ids["2025-01-02"],
        )
        train, validation, metadata = chronological_event_split(
            rows, validation_fraction=0.2, embargo_days=7
        )
        self.assertLess(
            max(row["date"] for row in train),
            min(row["date"] for row in validation),
        )
        self.assertGreater(
            (
                date.fromisoformat(min(row["date"] for row in validation))
                - date.fromisoformat(max(row["date"] for row in train))
            ).days,
            7,
        )
        train_events = {event_ids[row["date"]] for row in train}
        validation_events = {event_ids[row["date"]] for row in validation}
        self.assertFalse(train_events & validation_events)
        self.assertGreater(metadata["discarded_embargo_rows"], 0)

    def test_event_split_keeps_same_date_across_stations_together(self) -> None:
        day = date(2025, 1, 1)
        rows = [
            {**_row(day, 0), "nea_station_id": "S24", "rainfall_mm": 0},
            {**_row(day, 1), "nea_station_id": "S80", "rainfall_mm": 20},
        ]
        groups = weather_event_ids(rows)
        self.assertEqual(groups[day.isoformat()], "climate:2025-01-01:2025-01-01")

    def test_event_grouping_keeps_a_warm_episode_together(self) -> None:
        start = date(2025, 1, 1)
        rows = [
            {
                **_row(start + timedelta(days=index), index),
                "rainfall_mm": 0.0,
                "mean_temperature_c": 33.0 if index in {10, 11, 12} else 29.0,
            }
            for index in range(20)
        ]
        groups = weather_event_ids(rows)
        self.assertEqual(groups["2025-01-11"], groups["2025-01-13"])
        self.assertNotEqual(groups["2025-01-10"], groups["2025-01-11"])

    def test_project_holdout_is_disabled_when_not_enough_projects(self) -> None:
        rows = [_row(date(2025, 1, 1), index) for index in range(30)]
        train, test, report = project_event_holdout(rows)
        self.assertFalse(report["available"])
        self.assertEqual(train, [])
        self.assertEqual(test, [])

    def test_metrics_include_rainfall_segments_and_stage(self) -> None:
        rows = [
            {"rainfall_mm": 20, "stage": "earthworks"},
            {"rainfall_mm": 0, "stage": "site"},
            {"rainfall_mm": 4, "stage": "site"},
        ]
        report = evaluate_predictions(rows, [1, 0, 2], [2, 0, 1])
        self.assertEqual(report["overall"]["mae"], 2 / 3)
        self.assertEqual(report["heavy_rainfall"]["n"], 1)
        self.assertEqual(report["dry_weather"]["n"], 1)
        self.assertEqual(set(report["by_wbs_stage"]), {"earthworks", "site"})

    def test_synthetic_training_requires_explicit_opt_in(self) -> None:
        rows = [_row(date(2025, 1, 1) + timedelta(days=index), index) for index in range(20)]
        with self.assertRaisesRegex(ValueError, "explicit"):
            validate_training_data(rows, "synthetic", history_path=DEFAULT_HISTORY_PATH)
        quality = validate_training_data(
            rows,
            "synthetic",
            allow_synthetic_demo=True,
            history_path=DEFAULT_HISTORY_PATH,
        )
        self.assertEqual(quality["status"], "VALID")
        self.assertFalse(quality["training_eligible"])

    def test_end_to_end_synthetic_demo_training_and_prediction_report(self) -> None:
        rows = [
            _row(date(2025, 1, 1) + timedelta(days=index), index)
            for index in range(100)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "synthetic.csv"
            import csv

            with dataset.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=DATASET_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            report = train_delay_models(
                dataset,
                dataset_type="synthetic",
                allow_synthetic_demo=True,
                output_dir=root / "models",
                embargo_days=3,
                seed=1,
            )
            self.assertEqual(report["model_profile"], "SYNTHETIC_DEMO")
            self.assertFalse(report["empirically_validated"])
            self.assertFalse(report["real_safety_decision_use_permitted"])
            self.assertEqual(len(report["model_comparison"]), 7)
            self.assertEqual(
                set(report["weather_only_vs_kg_enhanced"]),
                {"statistical_ridge", "xgboost", "lightgbm"},
            )
            output = root / "models" / "synthetic_demo"
            self.assertTrue((output / "evaluation_metadata.json").is_file())
            self.assertTrue((output / "feature_schema.json").is_file())
            evaluation = evaluate_prediction_file(
                output / "chronological_validation_predictions.csv"
            )
            self.assertEqual(len(evaluation["models"]), 7)
            saved = json.loads((output / "evaluation_metadata.json").read_text())
            self.assertFalse(saved["empirically_validated"])
            self.assertEqual(
                saved["training_dataset_quality"]["synthetic_rows"], len(rows)
            )


if __name__ == "__main__":
    unittest.main()
