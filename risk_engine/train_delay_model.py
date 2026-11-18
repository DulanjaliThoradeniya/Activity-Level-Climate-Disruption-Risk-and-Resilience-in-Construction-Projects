"""Train downstream activity-level delay/disruption regressors."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import sklearn
import xgboost
import lightgbm

from .activity_mapper import build_activity_mapping
from .data_validator import validate_dataset
from .delay_model import (
    DEFAULT_RANDOM_SEED,
    HEAVY_RAINFALL_THRESHOLD_MM,
    FeatureEncoder,
    RuleBaseline,
    TARGET_FIELD,
    chronological_event_split,
    clip_lost_hours,
    evaluate_predictions,
    fit_predict_model,
    project_event_holdout,
    target_values,
)
from .feature_builder import DEFAULT_HISTORY_PATH, load_nea_history
from .data_validator import DEFAULT_REAL_PATH, DEFAULT_SYNTHETIC_PATH

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "models" / "delay_model"
MODEL_VARIANTS = (
    ("statistical_ridge", "weather_only"),
    ("statistical_ridge", "kg_enhanced"),
    ("xgboost", "weather_only"),
    ("xgboost", "kg_enhanced"),
    ("lightgbm", "weather_only"),
    ("lightgbm", "kg_enhanced"),
)


def _load_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except OSError as error:
        raise ValueError(f"Cannot read training dataset {source}: {error}") from error


def _real_flag(row: Mapping[str, Any]) -> bool:
    return str(row.get("is_synthetic", "")).strip().lower() in {"true", "1", "yes"}


def validate_training_data(
    rows: Sequence[Mapping[str, Any]],
    dataset_type: str,
    *,
    allow_synthetic_demo: bool = False,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    mapping = build_activity_mapping()
    history = load_nea_history(history_path)
    quality = validate_dataset(
        rows,
        dataset_type,
        known_wbs_codes=set(mapping.mapping),
        known_stations=set(history["stations"]),
    )
    if quality["errors"]:
        raise ValueError(
            f"{dataset_type} dataset failed Phase 3 validation: "
            f"{len(quality['errors'])} error(s)."
        )
    if dataset_type == "real":
        if any(_real_flag(row) for row in rows):
            raise ValueError("Real training data contains synthetic-labelled rows.")
        if not quality["training_eligible"]:
            raise ValueError(
                "Real training requires verified actual productive hours and sources."
            )
    else:
        if not allow_synthetic_demo:
            raise ValueError(
                "Synthetic training requires explicit --allow-synthetic-demo consent."
            )
        if not rows or not all(_real_flag(row) for row in rows):
            raise ValueError("Synthetic demo training requires every row labelled synthetic.")
        if not all(
            "SYNTHETIC" in str(row.get("evidence_status", "")).upper()
            for row in rows
        ):
            raise ValueError("Synthetic demo rows must retain synthetic evidence labels.")

    if not rows:
        raise ValueError("Training dataset contains no rows.")
    targets = target_values(rows)
    if not np.isfinite(targets).all():
        raise ValueError("Training target contains non-finite values.")
    if len({str(row.get("date")) for row in rows}) < 10:
        raise ValueError("At least ten distinct dates are required for evaluation.")
    return quality


def _model_id(model_name: str, feature_set: str) -> str:
    return f"{model_name}__{feature_set}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _prediction_rows(
    rows: Sequence[Mapping[str, Any]],
    actual: Sequence[float],
    predictions: Sequence[float],
    model_id: str,
    profile: str,
    interval_half_width: float | None,
) -> list[dict[str, Any]]:
    predictions = clip_lost_hours(predictions, rows)
    output: list[dict[str, Any]] = []
    for row, observed, predicted in zip(rows, actual, predictions):
        planned = float(row["planned_productive_hours"])
        width = interval_half_width or 0.0
        output.append(
            {
                "model_id": model_id,
                "model_profile": profile,
                "project_id": row["project_id"],
                "wbs_code": row["wbs_code"],
                "date": row["date"],
                "nea_station_id": row["nea_station_id"],
                "stage": row.get("stage"),
                "rainfall_mm": row.get("rainfall_mm"),
                "actual_weather_lost_hours": float(observed),
                "predicted_weather_lost_hours": float(predicted),
                "prediction_lower_90": max(0.0, float(predicted) - width),
                "prediction_upper_90": min(planned, float(predicted) + width),
                "is_synthetic": profile == "SYNTHETIC_DEMO",
            }
        )
    return output


def _evaluate_model(
    model_name: str,
    feature_set: str,
    training_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    *,
    profile: str,
    seed: int,
    heavy_rainfall_threshold_mm: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any, FeatureEncoder | None]:
    if model_name == "rule_baseline":
        fitted: Any = RuleBaseline()
        encoder = None
        raw_predictions = fitted.predict(test_rows)
    else:
        fitted, encoder, raw_predictions = fit_predict_model(
            model_name,
            training_rows,
            test_rows,
            feature_set=feature_set,
            seed=seed,
        )
    predictions = clip_lost_hours(raw_predictions, test_rows)
    actual = target_values(test_rows)
    abs_errors = np.abs(predictions - actual)
    uncertainty = (
        float(np.quantile(abs_errors, 0.9, method="higher"))
        if len(abs_errors)
        else None
    )
    metrics = evaluate_predictions(
        test_rows,
        actual,
        predictions,
        heavy_rainfall_threshold_mm=heavy_rainfall_threshold_mm,
    )
    metrics["prediction_interval"] = {
        "method": "validation_absolute_residual_90th_percentile",
        "half_width_hours": uncertainty,
        "calibrated_on_same_validation_fold": True,
    }
    predictions_csv = _prediction_rows(
        test_rows,
        actual,
        predictions,
        _model_id(model_name, feature_set),
        profile,
        uncertainty,
    )
    return metrics, predictions_csv, fitted, encoder


def _write_predictions(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "model_id",
        "model_profile",
        "project_id",
        "wbs_code",
        "date",
        "nea_station_id",
        "stage",
        "rainfall_mm",
        "actual_weather_lost_hours",
        "predicted_weather_lost_hours",
        "prediction_lower_90",
        "prediction_upper_90",
        "is_synthetic",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def train_delay_models(
    dataset_path: str | Path,
    *,
    dataset_type: str,
    allow_synthetic_demo: bool = False,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    validation_fraction: float = 0.2,
    embargo_days: int = 7,
    seed: int = DEFAULT_RANDOM_SEED,
    heavy_rainfall_threshold_mm: float = HEAVY_RAINFALL_THRESHOLD_MM,
) -> dict[str, Any]:
    rows = _load_rows(dataset_path)
    quality = validate_training_data(
        rows,
        dataset_type,
        allow_synthetic_demo=allow_synthetic_demo,
    )
    training_rows, validation_rows, split_metadata = chronological_event_split(
        rows,
        validation_fraction=validation_fraction,
        embargo_days=embargo_days,
    )
    profile = "SYNTHETIC_DEMO" if dataset_type == "synthetic" else "REAL_RESEARCH"
    output = Path(output_dir).expanduser().resolve() / profile.lower()
    output.mkdir(parents=True, exist_ok=True)
    evaluation: dict[str, Any] = {}
    all_predictions: list[dict[str, Any]] = []
    model_artifacts: dict[str, Any] = {}
    feature_schemas: dict[str, Any] = {}
    interval_widths: dict[str, float] = {}

    for model_name, feature_set in (("rule_baseline", "kg_enhanced"),) + MODEL_VARIANTS:
        model_id = _model_id(model_name, feature_set)
        metrics, predictions, fitted, encoder = _evaluate_model(
            model_name,
            feature_set,
            training_rows,
            validation_rows,
            profile=profile,
            seed=seed,
            heavy_rainfall_threshold_mm=heavy_rainfall_threshold_mm,
        )
        metrics["feature_set"] = feature_set
        metrics["model_type"] = (
            "transparent_rule_based"
            if model_name == "rule_baseline"
            else ("statistical" if model_name == "statistical_ridge" else model_name)
        )
        metrics["evaluation_profile"] = profile
        evaluation[model_id] = metrics
        all_predictions.extend(predictions)
        width = metrics["prediction_interval"]["half_width_hours"]
        if width is not None:
            interval_widths[model_id] = width
        if model_name == "rule_baseline":
            _write_json(output / f"{model_id}.json", fitted.assumptions())
        else:
            feature_schemas[model_id] = encoder.schema()
            artifact = {
                "profile": profile,
                "model_id": model_id,
                "model_name": model_name,
                "feature_set": feature_set,
                "estimator": fitted,
                "encoder": encoder,
                "prediction_interval_half_width_hours": width,
                "training_rows": len(training_rows),
                "training_date_start": split_metadata["training_start"],
                "training_date_end": split_metadata["training_end"],
                "training_dataset": str(Path(dataset_path).resolve()),
                "random_seed": seed,
            }
            model_path = output / f"{model_id}.joblib"
            joblib.dump(artifact, model_path)
            model_artifacts[model_id] = str(model_path)
            _write_json(
                output / f"{model_id}.feature_schema.json",
                feature_schemas[model_id],
            )

    grouped_train, grouped_test, grouped_split = project_event_holdout(
        rows, seed=seed
    )
    grouped_evaluation: dict[str, Any] = {}
    if grouped_split["available"]:
        for model_name, feature_set in (("rule_baseline", "kg_enhanced"),) + MODEL_VARIANTS:
            metrics, _, _, _ = _evaluate_model(
                model_name,
                feature_set,
                grouped_train,
                grouped_test,
                profile=profile,
                seed=seed,
                heavy_rainfall_threshold_mm=heavy_rainfall_threshold_mm,
            )
            grouped_evaluation[_model_id(model_name, feature_set)] = metrics

    _write_predictions(output / "chronological_validation_predictions.csv", all_predictions)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_profile": profile,
        "empirically_validated": dataset_type == "real",
        "real_safety_decision_use_permitted": False,
        "training_started": True,
        "training_data_type": dataset_type,
        "training_dataset_quality": {
            "status": quality["status"],
            "row_count": quality["row_count"],
            "synthetic_rows": quality["synthetic_rows"],
            "errors": quality["errors"],
            "warnings": quality["warnings"],
        },
        "target": {
            "field": TARGET_FIELD,
            "unit": "weather-attributable productive hours lost per activity working day",
        },
        "prediction_timing": (
            "Retrospective same-day observed weather; not a forward weather forecast "
            "or a real-time safety decision."
        ),
        "leakage_controls": {
            "date_and_project_id_are_not_predictors": True,
            "train_only_imputation_and_category_vocabulary": True,
            "weather_event_grouped_validation": True,
            "same_date_station_observations_stay_in_one_weather_group": True,
            "temporal_embargo_days": embargo_days,
            "weather_event_definition": (
                "Rows from the same date are grouped. Positive-rainfall dates and "
                "warm episodes (mean temperature >= 32 C or daily maximum >= 35 C), "
                "allowing at most one intervening inactive day, are grouped into "
                "climate events. Those thresholds define split groups only, not "
                "safety limits. Event ids are used only for split isolation."
            ),
            "project_grouped_holdout": grouped_split,
        },
        "chronological_split": split_metadata,
        "heavy_rainfall_segment_threshold_mm": heavy_rainfall_threshold_mm,
        "model_comparison": evaluation,
        "project_grouped_holdout_metrics": grouped_evaluation,
        "weather_only_vs_kg_enhanced": {
            model_name: {
                "weather_only_mae": evaluation[
                    _model_id(model_name, "weather_only")
                ]["overall"]["mae"],
                "kg_enhanced_mae": evaluation[
                    _model_id(model_name, "kg_enhanced")
                ]["overall"]["mae"],
                "kg_minus_weather_mae": (
                    evaluation[_model_id(model_name, "kg_enhanced")]["overall"]["mae"]
                    - evaluation[_model_id(model_name, "weather_only")]["overall"]["mae"]
                ),
                "interpretation": "Descriptive held-out comparison only; not causal evidence.",
            }
            for model_name in ("statistical_ridge", "xgboost", "lightgbm")
        },
        "rule_baseline_assumptions": RuleBaseline().assumptions(),
        "training_parameters": {
            "validation_fraction": validation_fraction,
            "embargo_days": embargo_days,
            "random_seed": seed,
            "model_variants": list(MODEL_VARIANTS),
        },
        "environment": {
            "python": platform.python_version(),
            "scikit_learn": sklearn.__version__,
            "xgboost": xgboost.__version__,
            "lightgbm": lightgbm.__version__,
        },
        "model_artifacts": model_artifacts,
        "synthetic_demo_interval_widths_hours": (
            interval_widths if dataset_type == "synthetic" else {}
        ),
        "uncertainty_caveat": (
            "Residual intervals use the held-out validation fold and are not "
            "independently calibrated. Synthetic intervals are demonstration-only."
        ),
    }
    _write_json(output / "evaluation_metadata.json", report)
    _write_json(
        output / "feature_schema.json",
        feature_schemas,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train separate activity climate-disruption regression models."
    )
    parser.add_argument("--dataset", choices=("real", "synthetic"), default="real")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--allow-synthetic-demo", action="store_true")
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--embargo-days", type=int, default=7)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument(
        "--heavy-rainfall-threshold-mm",
        type=float,
        default=HEAVY_RAINFALL_THRESHOLD_MM,
        help="Configurable evaluation-segment threshold; not a WSH or stop-work limit.",
    )
    args = parser.parse_args()
    dataset_path = args.data or (
        DEFAULT_SYNTHETIC_PATH if args.dataset == "synthetic" else DEFAULT_REAL_PATH
    )
    report = train_delay_models(
        dataset_path,
        dataset_type=args.dataset,
        allow_synthetic_demo=args.allow_synthetic_demo,
        output_dir=args.output_dir,
        validation_fraction=args.validation_fraction,
        embargo_days=args.embargo_days,
        seed=args.seed,
        heavy_rainfall_threshold_mm=args.heavy_rainfall_threshold_mm,
    )
    print(
        f"Trained {report['model_profile']} activity disruption models. "
        f"Evaluation metadata: {Path(args.output_dir).resolve() / report['model_profile'].lower() / 'evaluation_metadata.json'}"
    )
    if report["model_profile"] == "SYNTHETIC_DEMO":
        print(
            "Synthetic scores are separate demonstration results, not empirical "
            "validation. These models are prohibited for real safety decisions."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
