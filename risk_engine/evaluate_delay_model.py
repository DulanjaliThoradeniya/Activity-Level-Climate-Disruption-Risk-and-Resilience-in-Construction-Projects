"""Summarize saved chronological validation predictions for delay models."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .delay_model import (
    HEAVY_RAINFALL_THRESHOLD_MM,
    evaluate_predictions,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICTIONS = (
    ROOT
    / "models"
    / "delay_model"
    / "synthetic_demo"
    / "chronological_validation_predictions.csv"
)


def evaluate_prediction_file(
    predictions_path: str | Path,
    *,
    heavy_rainfall_threshold_mm: float = HEAVY_RAINFALL_THRESHOLD_MM,
) -> dict[str, Any]:
    path = Path(predictions_path).expanduser().resolve()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    if not records:
        raise ValueError(f"No predictions found in {path}.")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault(row["model_id"], []).append(row)
    report: dict[str, Any] = {
        "source": str(path),
        "evaluation_type": "saved_validation_predictions",
        "models": {},
    }
    for model_id, rows in sorted(grouped.items()):
        actual = [float(row["actual_weather_lost_hours"]) for row in rows]
        predicted = [float(row["predicted_weather_lost_hours"]) for row in rows]
        report["models"][model_id] = {
            "model_profile": rows[0].get("model_profile"),
            **evaluate_predictions(
                rows,
                actual,
                predicted,
                heavy_rainfall_threshold_mm=heavy_rainfall_threshold_mm,
            ),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recompute activity disruption validation metrics from saved predictions."
    )
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--heavy-rainfall-threshold-mm",
        type=float,
        default=HEAVY_RAINFALL_THRESHOLD_MM,
    )
    args = parser.parse_args()
    report = evaluate_prediction_file(
        args.predictions,
        heavy_rainfall_threshold_mm=args.heavy_rainfall_threshold_mm,
    )
    output = (
        args.output.expanduser().resolve()
        if args.output
        else args.predictions.expanduser().resolve().with_name(
            "recomputed_evaluation.json"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Evaluated {len(report['models'])} model predictions: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
