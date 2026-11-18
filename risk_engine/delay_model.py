"""Leakage-aware activity climate-disruption model utilities."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
import random
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from sklearn.feature_extraction import DictVectorizer

TARGET_FIELD = "weather_attributable_lost_hours"
HEAVY_RAINFALL_THRESHOLD_MM = 10.0
DEFAULT_RANDOM_SEED = 5231

WEATHER_NUMERIC_FEATURES = (
    "rainfall_mm",
    "mean_temperature_c",
    "maximum_temperature_c",
    "minimum_temperature_c",
    "recent_cumulative_rainfall_mm",
    "recent_rainfall_window_days",
    "planned_productive_hours",
)
WEATHER_CATEGORICAL_FEATURES = ("nea_station_id",)
KNOWLEDGE_NUMERIC_FEATURES: tuple[str, ...] = ()
KNOWLEDGE_CATEGORICAL_FEATURES = (
    "wbs_code",
    "activity_exposure",
    "heat_sensitivity",
    "rain_sensitivity",
    "activity_type",
    "stage",
    "site_drainage_condition",
    "ground_condition",
    "existing_controls_status",
    "existing_controls",
    "kg_hazard_ids",
    "kg_consequence_kinds",
    "kg_consequence_labels",
    "kg_candidate_control_ids",
    "kg_candidate_intervention_ids",
    "kg_evidence_statuses",
)

SENSITIVITY_MULTIPLIERS = {"low": 0.5, "medium": 1.0, "high": 1.5}
EXPOSURE_MULTIPLIERS = {
    "outdoor": 1.0,
    "partially sheltered": 0.5,
    "indoor": 0.0,
    "not confirmed": 1.0,
}


@dataclass
class FeatureEncoder:
    """Train-only numeric imputation and category encoding."""

    feature_set: str
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...]
    medians: dict[str, float]
    vectorizer: DictVectorizer

    @classmethod
    def create(cls, feature_set: str) -> "FeatureEncoder":
        if feature_set not in {"weather_only", "kg_enhanced"}:
            raise ValueError(f"Unknown feature set {feature_set!r}.")
        numeric = WEATHER_NUMERIC_FEATURES
        categorical = WEATHER_CATEGORICAL_FEATURES
        if feature_set == "kg_enhanced":
            categorical += KNOWLEDGE_CATEGORICAL_FEATURES
        return cls(
            feature_set=feature_set,
            numeric_features=numeric,
            categorical_features=categorical,
            medians={},
            vectorizer=DictVectorizer(sparse=False, sort=True),
        )

    def _raw_value(self, row: Mapping[str, Any], field: str) -> Any:
        if field == "kg_consequence_kinds":
            items = _json_list(row.get("kg_consequences"))
            return _join_categories(
                item.get("kind") for item in items if isinstance(item, Mapping)
            )
        if field == "kg_consequence_labels":
            items = _json_list(row.get("kg_consequences"))
            return _join_categories(
                item.get("label") for item in items if isinstance(item, Mapping)
            )
        if field in {
            "existing_controls",
            "kg_hazard_ids",
            "kg_candidate_control_ids",
            "kg_candidate_intervention_ids",
            "kg_evidence_statuses",
        }:
            return _join_categories(_json_list(row.get(field)))
        return row.get(field)

    def _dictionaries(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        dictionaries: list[dict[str, Any]] = []
        for row in rows:
            values: dict[str, Any] = {}
            for field in self.numeric_features:
                raw = row.get(field)
                number = _number(raw)
                values[field] = (
                    self.medians.get(field, 0.0) if number is None else number
                )
            for field in self.categorical_features:
                raw = self._raw_value(row, field)
                values[field] = (
                    str(raw).strip() if raw is not None and str(raw).strip() else "__MISSING__"
                )
            dictionaries.append(values)
        return dictionaries

    def fit(self, rows: Sequence[Mapping[str, Any]]) -> "FeatureEncoder":
        if not rows:
            raise ValueError("Cannot fit feature encoder on an empty training set.")
        for field in self.numeric_features:
            observed = sorted(
                number
                for row in rows
                if (number := _number(row.get(field))) is not None
            )
            if not observed:
                self.medians[field] = 0.0
            else:
                middle = len(observed) // 2
                self.medians[field] = (
                    observed[middle]
                    if len(observed) % 2
                    else (observed[middle - 1] + observed[middle]) / 2
                )
        self.vectorizer.fit(self._dictionaries(rows))
        return self

    def transform(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        if not self.medians:
            raise ValueError("Feature encoder must be fitted before transform.")
        matrix = self.vectorizer.transform(self._dictionaries(rows))
        return np.asarray(matrix, dtype=float)

    def fit_transform(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        return self.fit(rows).transform(rows)

    def schema(self) -> dict[str, Any]:
        if not self.medians:
            raise ValueError("Feature schema is available only after fitting.")
        return {
            "feature_set": self.feature_set,
            "numeric_input_fields": list(self.numeric_features),
            "categorical_input_fields": list(self.categorical_features),
            "numeric_medians_fit_on_training_only": dict(self.medians),
            "encoded_feature_names": list(self.vectorizer.get_feature_names_out()),
            "target": TARGET_FIELD,
            "target_unit": "productive hours lost per activity working day",
            "target_is_weather_attributable": True,
            "date_and_project_id_used_as_predictors": False,
        }


@dataclass(frozen=True)
class RuleBaseline:
    """Transparent demonstration baseline; coefficients are explicit assumptions."""

    rain_threshold_mm: float = 5.0
    rain_lost_hours_per_excess_mm: float = 0.04
    heat_threshold_mean_temperature_c: float = 32.0
    heat_lost_hours_per_excess_degree: float = 0.2
    sensitivity_multipliers: Mapping[str, float] | None = None
    exposure_multipliers: Mapping[str, float] | None = None

    def predict(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        sensitivity = self.sensitivity_multipliers or SENSITIVITY_MULTIPLIERS
        exposure = self.exposure_multipliers or EXPOSURE_MULTIPLIERS
        predictions: list[float] = []
        for row in rows:
            planned = _number(row.get("planned_productive_hours")) or 0.0
            rainfall = _number(row.get("rainfall_mm"))
            temperature = _number(row.get("mean_temperature_c"))
            if rainfall is None or temperature is None:
                predictions.append(float("nan"))
                continue
            rain_factor = sensitivity.get(
                str(row.get("rain_sensitivity", "medium")).lower(), 1.0
            )
            heat_factor = sensitivity.get(
                str(row.get("heat_sensitivity", "medium")).lower(), 1.0
            )
            exposure_factor = exposure.get(
                str(row.get("activity_exposure", "not confirmed")).lower(), 1.0
            )
            rain_loss = (
                max(0.0, rainfall - self.rain_threshold_mm)
                * self.rain_lost_hours_per_excess_mm
                * rain_factor
            )
            heat_loss = (
                max(0.0, temperature - self.heat_threshold_mean_temperature_c)
                * self.heat_lost_hours_per_excess_degree
                * heat_factor
            )
            predictions.append(
                min(planned, max(0.0, exposure_factor * (rain_loss + heat_loss)))
            )
        return np.asarray(predictions, dtype=float)

    def assumptions(self) -> dict[str, Any]:
        return {
            "model_type": "transparent_rule_based_baseline",
            "rain_threshold_mm": self.rain_threshold_mm,
            "rain_lost_hours_per_excess_mm": self.rain_lost_hours_per_excess_mm,
            "heat_threshold_mean_temperature_c": self.heat_threshold_mean_temperature_c,
            "heat_lost_hours_per_excess_degree": self.heat_lost_hours_per_excess_degree,
            "sensitivity_multipliers": dict(
                self.sensitivity_multipliers or SENSITIVITY_MULTIPLIERS
            ),
            "exposure_multipliers": dict(
                self.exposure_multipliers or EXPOSURE_MULTIPLIERS
            ),
            "evidence_status": "ILLUSTRATIVE_ASSUMPTIONS_NOT_VALIDATED_EFFECTS",
        }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    return parsed if isinstance(parsed, list) else [parsed]


def _join_categories(values: Iterable[Any]) -> str:
    unique = sorted(
        {
            str(value).strip().replace("|", "/")
            for value in values
            if value is not None and str(value).strip()
        }
    )
    return "|".join(unique) if unique else "__NONE__"


def target_values(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    targets: list[float] = []
    for index, row in enumerate(rows):
        value = _number(row.get(TARGET_FIELD))
        planned = _number(row.get("planned_productive_hours"))
        if value is None or planned is None:
            raise ValueError(
                f"Training row {index} requires numeric {TARGET_FIELD} and "
                "planned_productive_hours."
            )
        if value < 0 or value > planned:
            raise ValueError(
                f"Training row {index} has weather-attributable lost hours outside "
                "0..planned productive hours."
            )
        targets.append(value)
    return np.asarray(targets, dtype=float)


def weather_event_ids(
    rows: Sequence[Mapping[str, Any]],
    *,
    maximum_dry_gap_days: int = 1,
) -> dict[str, str]:
    """Assign same-date rows together and cluster station-observed wet episodes.

    Event grouping is for splitting only: a date is wet if any input station
    reports positive daily rainfall, preserving station-specific feature values.
    Rainy dates and consecutive warm episodes (mean temperature >= 32 C or daily
    maximum >= 35 C), separated by at most the configured inactive-day gap,
    share a climate event. Thresholds define split groups only, not safety limits.
    """
    if maximum_dry_gap_days < 0:
        raise ValueError("maximum_dry_gap_days cannot be negative.")
    weather_by_date: dict[str, list[tuple[float | None, ...]]] = defaultdict(list)
    for row in rows:
        day = str(row.get("date", ""))
        try:
            day = date.fromisoformat(day).isoformat()
        except ValueError as error:
            raise ValueError(f"Invalid event-grouping date {day!r}.") from error
        rain = _number(row.get("rainfall_mm"))
        mean = _number(row.get("mean_temperature_c"))
        maximum = _number(row.get("maximum_temperature_c"))
        weather_by_date[day].append((rain, mean, maximum))
    event_dates = sorted(
        day
        for day, observations in weather_by_date.items()
        if any(
            (rain is not None and rain > 0)
            or (mean is not None and mean >= 32.0)
            or (maximum is not None and maximum >= 35.0)
            for rain, mean, maximum in observations
        )
    )
    event_ranges: list[tuple[date, date]] = []
    for day_string in event_dates:
        current = date.fromisoformat(day_string)
        if (
            event_ranges
            and (current - event_ranges[-1][1]).days <= maximum_dry_gap_days + 1
        ):
            start, _ = event_ranges[-1]
            event_ranges[-1] = (start, current)
        else:
            event_ranges.append((current, current))

    result: dict[str, str] = {}
    for day_string in weather_by_date:
        current = date.fromisoformat(day_string)
        matching = next(
            (
                (start, end)
                for start, end in event_ranges
                if start <= current <= end
                and (end - start).days > 0
            ),
            None,
        )
        if matching is not None:
            result[day_string] = (
                f"climate:{matching[0].isoformat()}:{matching[1].isoformat()}"
            )
        elif any(
            start == current and end == current for start, end in event_ranges
        ):
            result[day_string] = f"climate:{day_string}:{day_string}"
        else:
            result[day_string] = f"non_event:{day_string}"
    return result


def chronological_event_split(
    rows: Sequence[Mapping[str, Any]],
    validation_fraction: float = 0.2,
    embargo_days: int = 7,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    """Chronologically hold out whole weather-event groups and purge an embargo."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1.")
    if embargo_days < 0:
        raise ValueError("embargo_days cannot be negative.")
    ids_by_date = weather_event_ids(rows)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[ids_by_date[str(row["date"])]].append(row)
    groups = sorted(
        grouped.items(),
        key=lambda item: min(str(row["date"]) for row in item[1]),
    )
    if len(groups) < 2:
        raise ValueError("At least two independent weather-event groups are required.")
    validation_group_index = max(
        1, min(len(groups) - 1, int(len(groups) * (1 - validation_fraction)))
    )
    initial_train_groups = groups[:validation_group_index]
    validation_groups = groups[validation_group_index:]
    validation_start = min(
        date.fromisoformat(str(row["date"]))
        for _, group_rows in validation_groups
        for row in group_rows
    )
    cutoff = validation_start - timedelta(days=embargo_days)
    training = [
        row
        for _, group_rows in initial_train_groups
        for row in group_rows
        if date.fromisoformat(str(row["date"])) < cutoff
    ]
    validation = [
        row for _, group_rows in validation_groups for row in group_rows
    ]
    if not training or not validation:
        raise ValueError(
            "Chronological event split is empty after applying the temporal embargo."
        )
    train_events = {ids_by_date[str(row["date"])] for row in training}
    validation_events = {ids_by_date[str(row["date"])] for row in validation}
    if train_events & validation_events:
        raise ValueError("A weather event would be shared across the temporal split.")
    training_end = max(date.fromisoformat(str(row["date"])) for row in training)
    validation_start_actual = min(
        date.fromisoformat(str(row["date"])) for row in validation
    )
    if (validation_start_actual - training_end).days <= embargo_days:
        raise ValueError("The chronological split did not preserve the embargo.")
    return (
        training,
        validation,
        {
            "method": "chronological_weather_event_grouped_with_embargo",
            "validation_fraction_requested": validation_fraction,
            "embargo_days": embargo_days,
            "training_rows": len(training),
            "validation_rows": len(validation),
            "training_start": min(str(row["date"]) for row in training),
            "training_end": max(str(row["date"]) for row in training),
            "validation_start": min(str(row["date"]) for row in validation),
            "validation_end": max(str(row["date"]) for row in validation),
            "training_event_groups": len(train_events),
            "validation_event_groups": len(validation_events),
            "discarded_embargo_rows": sum(
                1
                for _, group_rows in initial_train_groups
                for row in group_rows
                if date.fromisoformat(str(row["date"])) >= cutoff
            ),
        },
    )


def project_event_holdout(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int = DEFAULT_RANDOM_SEED,
    minimum_rows_per_partition: int = 20,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    """Hold out projects and purge shared event-days from training."""
    projects = sorted({str(row.get("project_id", "")) for row in rows})
    if len(projects) < 3:
        return [], [], {
            "available": False,
            "reason": "At least three projects are required for grouped holdout.",
            "project_count": len(projects),
        }
    random.Random(seed).shuffle(projects)
    heldout_count = max(1, round(len(projects) * 0.2))
    heldout_projects = set(projects[:heldout_count])
    event_ids = weather_event_ids(rows)
    heldout = [row for row in rows if str(row["project_id"]) in heldout_projects]
    heldout_events = {event_ids[str(row["date"])] for row in heldout}
    training = [
        row
        for row in rows
        if str(row["project_id"]) not in heldout_projects
        and event_ids[str(row["date"])] not in heldout_events
    ]
    if (
        len(training) < minimum_rows_per_partition
        or len(heldout) < minimum_rows_per_partition
    ):
        return [], [], {
            "available": False,
            "reason": "Insufficient observations after project/event leakage purging.",
            "project_count": len(projects),
            "heldout_project_ids": sorted(heldout_projects),
            "training_rows_after_event_purge": len(training),
            "heldout_rows": len(heldout),
        }
    if {str(row["project_id"]) for row in training} & heldout_projects:
        raise ValueError("A held-out project leaked into grouped training data.")
    if {event_ids[str(row["date"])] for row in training} & heldout_events:
        raise ValueError("A held-out weather event leaked into grouped training data.")
    return training, heldout, {
        "available": True,
        "method": "project_grouped_holdout_with_weather_event_purge",
        "seed": seed,
        "project_count": len(projects),
        "heldout_project_ids": sorted(heldout_projects),
        "training_rows": len(training),
        "validation_rows": len(heldout),
        "purged_training_rows_sharing_heldout_weather_events": sum(
            1
            for row in rows
            if str(row["project_id"]) not in heldout_projects
            and event_ids[str(row["date"])] in heldout_events
        ),
    }


def regression_metrics(
    actual: Sequence[float],
    predicted: Sequence[float],
) -> dict[str, float | int | None]:
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    valid = np.isfinite(actual_array) & np.isfinite(predicted_array)
    actual_array, predicted_array = actual_array[valid], predicted_array[valid]
    if not len(actual_array):
        return {"n": 0, "mae": None, "rmse": None, "prediction_bias": None}
    residual = predicted_array - actual_array
    return {
        "n": int(len(actual_array)),
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(np.square(residual)))),
        "prediction_bias": float(np.mean(residual)),
    }


def evaluate_predictions(
    rows: Sequence[Mapping[str, Any]],
    actual: Sequence[float],
    predicted: Sequence[float],
    *,
    heavy_rainfall_threshold_mm: float = HEAVY_RAINFALL_THRESHOLD_MM,
) -> dict[str, Any]:
    overall = regression_metrics(actual, predicted)
    rainfall = np.asarray(
        [_number(row.get("rainfall_mm")) for row in rows], dtype=object
    )
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)

    def segment(mask: np.ndarray) -> dict[str, Any]:
        indexes = np.flatnonzero(mask)
        return regression_metrics(actual_array[indexes], predicted_array[indexes])

    heavy_mask = np.asarray(
        [
            value is not None and float(value) >= heavy_rainfall_threshold_mm
            for value in rainfall
        ],
        dtype=bool,
    )
    dry_mask = np.asarray(
        [value is not None and float(value) == 0 for value in rainfall],
        dtype=bool,
    )
    stage_values: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        stage_values[str(row.get("stage") or "UNKNOWN")].append(index)
    return {
        "overall": overall,
        "heavy_rainfall_threshold_mm": heavy_rainfall_threshold_mm,
        "heavy_rainfall": segment(heavy_mask),
        "dry_weather": segment(dry_mask),
        "by_wbs_stage": {
            stage: regression_metrics(
                actual_array[indexes], predicted_array[indexes]
            )
            for stage, indexes in sorted(stage_values.items())
        },
    }


def available_model_specs(seed: int = DEFAULT_RANDOM_SEED) -> dict[str, Any]:
    """Construct sklearn and tree regressors lazily for declared dependencies."""
    from lightgbm import LGBMRegressor
    from sklearn.linear_model import Ridge
    from xgboost import XGBRegressor

    return {
        "statistical_ridge": lambda: Ridge(alpha=1.0),
        "xgboost": lambda: XGBRegressor(
            objective="reg:squarederror",
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            subsample=1.0,
            colsample_bytree=1.0,
            reg_lambda=1.0,
            random_state=seed,
            n_jobs=1,
            verbosity=0,
        ),
        "lightgbm": lambda: LGBMRegressor(
            objective="regression",
            n_estimators=100,
            max_depth=3,
            num_leaves=7,
            learning_rate=0.05,
            subsample=1.0,
            colsample_bytree=1.0,
            reg_lambda=1.0,
            random_state=seed,
            n_jobs=1,
            verbosity=-1,
        ),
    }


def fit_predict_model(
    model_name: str,
    training_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
    *,
    feature_set: str,
    seed: int = DEFAULT_RANDOM_SEED,
) -> tuple[Any, FeatureEncoder, np.ndarray]:
    if model_name == "rule_baseline":
        return RuleBaseline(), FeatureEncoder.create(feature_set), RuleBaseline().predict(
            prediction_rows
        )
    specs = available_model_specs(seed)
    if model_name not in specs:
        raise ValueError(f"Unsupported model {model_name!r}.")
    encoder = FeatureEncoder.create(feature_set)
    training_x = encoder.fit_transform(training_rows)
    prediction_x = encoder.transform(prediction_rows)
    estimator = specs[model_name]()
    estimator.fit(training_x, target_values(training_rows))
    return estimator, encoder, np.asarray(estimator.predict(prediction_x), dtype=float)


def clip_lost_hours(
    predictions: Sequence[float],
    rows: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    planned = np.asarray(
        [_number(row.get("planned_productive_hours")) or 0.0 for row in rows],
        dtype=float,
    )
    values = np.asarray(predictions, dtype=float)
    return np.clip(values, 0.0, planned)


__all__ = [
    "DEFAULT_RANDOM_SEED",
    "FeatureEncoder",
    "HEAVY_RAINFALL_THRESHOLD_MM",
    "KNOWLEDGE_CATEGORICAL_FEATURES",
    "RuleBaseline",
    "TARGET_FIELD",
    "WEATHER_CATEGORICAL_FEATURES",
    "WEATHER_NUMERIC_FEATURES",
    "chronological_event_split",
    "clip_lost_hours",
    "evaluate_predictions",
    "fit_predict_model",
    "project_event_holdout",
    "regression_metrics",
    "target_values",
    "weather_event_ids",
]
