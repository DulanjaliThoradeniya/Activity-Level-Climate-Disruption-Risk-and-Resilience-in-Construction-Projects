from __future__ import annotations

import csv
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from risk_engine.data_validator import validate_dataset
from risk_engine.feature_builder import (
    DATASET_FIELDS,
    build_activity_day_dataset,
    load_activity_observations_csv,
    temporal_train_validation_split,
    write_dataset_csv,
)
from risk_engine.synthetic_generator import (
    SyntheticAssumptions,
    build_synthetic_dataset,
)

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "climate-history.json"
WEATHER_JS = ROOT / "weather.js"
WORKBOOK = ROOT / "knowledge_graph" / "Knowledge Graph Project.xlsx"
REFINE_JS = ROOT / "refine.js"


class FeatureBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from risk_engine.graph_loader import load_knowledge_graph

        cls.graph = load_knowledge_graph(WORKBOOK)
        cls.project = {
            "project_id": "PROJECT-1",
            "name": "Test project",
            "location": "Changi",
            "site_conditions": {
                "drainage_condition": "partially documented",
                "ground_condition": "clay (reported)",
            },
            "activities": [
                {
                    "wbs_code": "SP-01",
                    "exposure": "outdoor",
                    "heatSensitivity": "high",
                    "rainSensitivity": "medium",
                    "existing_controls": ["covered storage"],
                }
            ],
        }

    def build_row(self, observed_date: str = "2024-01-10") -> dict:
        rows = build_activity_day_dataset(
            [self.project],
            [
                {
                    "project_id": "PROJECT-1",
                    "wbs_code": "SP-01",
                    "date": observed_date,
                    "planned_productive_hours": 8,
                    "actual_productive_hours": 6,
                    "weather_attributable_lost_hours": 1,
                    "other_disruption_causes": ["equipment breakdown"],
                    "other_disruption_hours": 1,
                    "data_source": "site daily report",
                    "evidence_status": "SOURCE_UNVERIFIED",
                }
            ],
            history_path=HISTORY,
            station_mapping_path=WEATHER_JS,
            graph=self.graph,
            workbook_path=WORKBOOK,
            application_source_path=REFINE_JS,
        )
        return rows[0]

    def test_builds_activity_day_features_from_specific_nea_station(self) -> None:
        row = self.build_row()
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
        self.assertEqual(row["nea_station_id"], "S24")
        nea_row = next(
            value
            for value in history["stations"]["S24"]
            if value[0] == "2024-01-10"
        )
        self.assertEqual(row["rainfall_mm"], nea_row[1])
        self.assertEqual(row["mean_temperature_c"], nea_row[2])
        self.assertEqual(row["wbs_code"], "SP-01")
        self.assertEqual(row["application_activity_id"], "wbs_sp_01")
        self.assertEqual(row["activity_exposure"], "outdoor")
        self.assertEqual(row["heat_sensitivity"], "high")
        self.assertEqual(row["site_drainage_condition"], "partially documented")
        self.assertEqual(json.loads(row["existing_controls"]), ["covered storage"])
        self.assertFalse(row["is_synthetic"])

    def test_knowledge_graph_features_preserve_hazard_and_candidate_controls(self) -> None:
        row = self.build_row()
        self.assertGreater(len(json.loads(row["kg_hazard_ids"])), 0)
        self.assertIsInstance(json.loads(row["kg_candidate_control_ids"]), list)
        self.assertEqual(row["existing_controls_status"], "PROJECT_REPORTED")

    def test_rainfall_window_is_prior_seven_days_and_excludes_current_day(self) -> None:
        row = self.build_row("2024-01-10")
        self.assertEqual(row["recent_rainfall_window_days"], 7)
        self.assertEqual(row["recent_rainfall_window_end"], "2024-01-09")
        self.assertLess(row["recent_rainfall_window_end"], row["date"])
        self.assertIsNotNone(row["recent_cumulative_rainfall_mm"])

    def test_builder_never_fills_an_unavailable_rainfall_window(self) -> None:
        project = {**self.project, "nea_station_id": "S111"}
        rows = build_activity_day_dataset(
            [project],
            [
                {
                    "project_id": "PROJECT-1",
                    "wbs_code": "SP-01",
                    "date": "2010-09-10",
                    "actual_productive_hours": 7,
                }
            ],
            history_path=HISTORY,
            station_mapping_path=WEATHER_JS,
            graph=self.graph,
            workbook_path=WORKBOOK,
            application_source_path=REFINE_JS,
        )
        self.assertIsNone(rows[0]["recent_cumulative_rainfall_mm"])

    def test_duplicate_activity_days_and_unknown_stations_are_rejected(self) -> None:
        observation = {
            "project_id": "PROJECT-1",
            "wbs_code": "SP-01",
            "date": "2024-01-10",
            "actual_productive_hours": 7,
        }
        with self.assertRaisesRegex(ValueError, "Duplicate activity-day"):
            build_activity_day_dataset(
                [self.project],
                [observation, observation],
                graph=self.graph,
                workbook_path=WORKBOOK,
                application_source_path=REFINE_JS,
            )
        with self.assertRaisesRegex(ValueError, "absent from the bundled"):
            build_activity_day_dataset(
                [{**self.project, "nea_station_id": "S999"}],
                [observation],
                graph=self.graph,
                workbook_path=WORKBOOK,
                application_source_path=REFINE_JS,
            )

    def test_csv_writer_and_observation_reader_preserve_missing_values(self) -> None:
        observation = {
            "project_id": "PROJECT-1",
            "wbs_code": "SP-01",
            "date": "2024-01-10",
            "actual_productive_hours": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "observations.csv"
            with input_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=observation)
                writer.writeheader()
                writer.writerow(observation)
            loaded = load_activity_observations_csv(input_path)
            self.assertIsNone(loaded[0]["actual_productive_hours"])

            output_path = Path(directory) / "features.csv"
            write_dataset_csv([self.build_row()], output_path)
            with output_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(tuple(reader.fieldnames or ()), DATASET_FIELDS)
                row = next(reader)
                self.assertEqual(row["nea_station_id"], "S24")

    def test_temporal_split_keeps_all_validation_dates_after_training(self) -> None:
        rows = [
            {"date": (date(2024, 1, 1) + timedelta(days=index)).isoformat()}
            for index in range(10)
        ]
        training, validation = temporal_train_validation_split(rows, 0.2)
        self.assertLess(
            max(row["date"] for row in training),
            min(row["date"] for row in validation),
        )
        with self.assertRaisesRegex(ValueError, "two distinct dates"):
            temporal_train_validation_split([{"date": "2024-01-01"}])

    def test_validator_detects_future_rolling_features_and_synthetic_mislabels(self) -> None:
        row = self.build_row()
        row["recent_rainfall_window_end"] = row["date"]
        row["is_synthetic"] = True
        report = validate_dataset(
            [row],
            "real",
            known_wbs_codes={"SP-01"},
            known_stations={"S24"},
        )
        issues = {item["issue"] for item in report["errors"]}
        self.assertIn("recent_rainfall_uses_current_or_future_date", issues)
        self.assertIn("real_dataset_contains_synthetic_row", issues)

    def test_unverified_real_outcomes_are_not_training_eligible(self) -> None:
        row = self.build_row()
        report = validate_dataset(
            [row],
            "real",
            known_wbs_codes={"SP-01"},
            known_stations={"S24"},
        )
        self.assertFalse(report["training_eligible"])
        self.assertIn(
            "outcome_evidence_requires_review",
            {item["issue"] for item in report["warnings"]},
        )

    def test_synthetic_dataset_covers_all_69_and_is_not_training_eligible(self) -> None:
        rows = build_synthetic_dataset(
            SyntheticAssumptions(activity_duration_workdays=1),
            history_path=HISTORY,
            station_mapping_path=WEATHER_JS,
            workbook_path=WORKBOOK,
        )
        self.assertEqual(len(rows), 69)
        self.assertEqual(len({row["wbs_code"] for row in rows}), 69)
        self.assertTrue(all(row["is_synthetic"] for row in rows))
        self.assertTrue(
            all(
                row["evidence_status"] == "SYNTHETIC_DEMONSTRATION_NOT_EMPIRICAL"
                for row in rows
            )
        )
        report = validate_dataset(
            rows,
            "synthetic",
            known_wbs_codes={row["wbs_code"] for row in rows},
            known_stations={row["nea_station_id"] for row in rows},
        )
        self.assertEqual(report["status"], "VALID")
        self.assertFalse(report["training_eligible"])

    def test_validator_reports_empty_real_dataset_as_not_yet_collected(self) -> None:
        report = validate_dataset([], "real")
        self.assertEqual(report["status"], "NO_DATA")
        self.assertFalse(report["training_eligible"])
        self.assertIn(
            "no_activity_day_observations",
            {item["issue"] for item in report["warnings"]},
        )


if __name__ == "__main__":
    unittest.main()
