from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from math import sqrt
from statistics import median
from typing import Any, Callable, Optional, Sequence


HISTORY_START = date(2006, 1, 1)
SINGAPORE_TIMEZONE = timezone(timedelta(hours=8))
HISTORY_END = datetime.now(SINGAPORE_TIMEZONE).date() - timedelta(days=1)
TRAINING_FRACTION = 0.8
FORECAST_HORIZON_DAYS = 14
WET_DAY_THRESHOLD_MM = 1.0
HEAVY_RAIN_THRESHOLD_MM = 20.0


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def filter_observations(
    dates: Sequence[Any],
    values: Sequence[Any],
    excluded_dates: Sequence[Any] = (),
) -> tuple[list[date], list[float]]:
    if len(dates) != len(values):
        raise ValueError("Observation dates and values must have the same length.")

    excluded = {_as_date(value) for value in excluded_dates}
    observations = []
    for raw_date, raw_value in zip(dates, values):
        observed_date = _as_date(raw_date)
        if (
            not HISTORY_START <= observed_date <= HISTORY_END
            or observed_date in excluded
        ):
            continue
        if raw_value is None:
            continue
        value = float(raw_value)
        if value != value or value in (float("inf"), float("-inf")):
            continue
        observations.append((observed_date, value))

    observations.sort(key=lambda row: row[0])
    if len({observed_date for observed_date, _ in observations}) != len(observations):
        raise ValueError("Duplicate valid observations found for a date.")
    return [row[0] for row in observations], [row[1] for row in observations]


def chronological_split(
    dates: Sequence[date],
    training_fraction: float = TRAINING_FRACTION,
) -> tuple[int, dict[str, Any]]:
    if not 0.0 < training_fraction < 1.0:
        raise ValueError("The training fraction must be between zero and one.")
    if not dates:
        raise ValueError("At least one valid observation is required.")
    if list(dates) != sorted(dates):
        raise ValueError("Observations must be sorted chronologically.")

    total = len(dates)
    split = int(total * training_fraction)
    if split < 1 or split >= total:
        raise ValueError("The data does not contain enough observations for an 80/20 split.")

    training_count = split
    testing_count = total - split
    return split, {
        "training_fraction": training_fraction,
        "testing_fraction": 1.0 - training_fraction,
        "total_valid_observations": total,
        "training_count": training_count,
        "testing_count": testing_count,
        "training_percent": training_count * 100.0 / total,
        "testing_percent": testing_count * 100.0 / total,
        "training_start_date": dates[0].isoformat(),
        "training_end_date": dates[split - 1].isoformat(),
        "testing_start_date": dates[split].isoformat(),
        "testing_end_date": dates[-1].isoformat(),
    }


def rolling_origin_predictions(
    dates: Sequence[date],
    values: Sequence[float],
    split: int,
    predictor: Callable[[Sequence[date], Sequence[float], Sequence[date]], Sequence[float]],
    horizon: int = FORECAST_HORIZON_DAYS,
) -> list[dict[str, Any]]:
    if len(dates) != len(values):
        raise ValueError("Observation dates and values must have the same length.")
    if not 1 <= split < len(dates):
        raise ValueError("The split must leave at least one observation in both sets.")
    if horizon < 1:
        raise ValueError("Forecast horizon must be positive.")
    if list(dates) != sorted(dates):
        raise ValueError("Observations must be sorted chronologically.")

    observed_by_date = dict(zip(dates, values))
    last_test_date = dates[-1]
    origin = dates[split - 1]
    predictions = []

    while origin < last_test_date:
        forecast_dates = [
            origin + timedelta(days=lead)
            for lead in range(1, horizon + 1)
        ]
        history_end = max(
            index for index, observed_date in enumerate(dates)
            if observed_date <= origin
        )
        history_dates = dates[:history_end + 1]
        history_values = values[:history_end + 1]
        forecast_values = list(predictor(history_dates, history_values, forecast_dates))
        if len(forecast_values) != horizon:
            raise ValueError(
                f"Predictor returned {len(forecast_values)} values; expected {horizon}."
            )

        for lead, (forecast_date, prediction) in enumerate(
            zip(forecast_dates, forecast_values),
            start=1,
        ):
            actual = observed_by_date.get(forecast_date)
            if forecast_date > dates[split - 1] and actual is not None:
                value = float(prediction)
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(f"Non-finite prediction for {forecast_date.isoformat()}.")
                predictions.append({
                    "date": forecast_date.isoformat(),
                    "origin": origin.isoformat(),
                    "lead": lead,
                    "actual": float(actual),
                    "predicted": value,
                })
        origin += timedelta(days=horizon)

    return predictions


def regression_metrics(
    actual: Sequence[float],
    predicted: Sequence[float],
    target: str,
) -> dict[str, Any]:
    if len(actual) != len(predicted):
        raise ValueError("Actual and predicted values must have the same length.")
    if not actual:
        return {"n": 0, "mae": None, "rmse": None, "r2": None}

    errors = [float(prediction) - float(observed) for observed, prediction in zip(actual, predicted)]
    absolute_errors = [abs(error) for error in errors]
    mae = sum(absolute_errors) / len(absolute_errors)
    rmse = sqrt(sum(error * error for error in errors) / len(errors))
    mean_actual = sum(actual) / len(actual)
    total_variance = sum((float(observed) - mean_actual) ** 2 for observed in actual)
    r2 = (
        1.0 - sum(error * error for error in errors) / total_variance
        if total_variance > 0.0
        else None
    )
    actual_sum = sum(abs(float(observed)) for observed in actual)
    result = {
        "n": len(actual),
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "mean_bias": sum(errors) / len(errors),
        "median_absolute_error": median(absolute_errors),
        "wape": sum(absolute_errors) / actual_sum if actual_sum else None,
    }

    if target == "rainfall":
        def occurrence(threshold: float) -> dict[str, Any]:
            observed_events = [float(value) >= threshold for value in actual]
            predicted_events = [float(value) >= threshold for value in predicted]
            tp = sum(observed and predicted for observed, predicted in zip(observed_events, predicted_events))
            fp = sum(not observed and predicted for observed, predicted in zip(observed_events, predicted_events))
            tn = sum(not observed and not predicted for observed, predicted in zip(observed_events, predicted_events))
            fn = sum(observed and not predicted for observed, predicted in zip(observed_events, predicted_events))
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            return {
                "threshold_mm": threshold,
                "precision": precision,
                "recall": recall,
                "f1": 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0,
                "false_alarm_rate": fp / (fp + tn) if fp + tn else 0.0,
                "true_positive": tp,
                "false_positive": fp,
                "true_negative": tn,
                "false_negative": fn,
            }

        wet_days = [index for index, value in enumerate(actual) if value >= WET_DAY_THRESHOLD_MM]
        heavy_days = [index for index, value in enumerate(actual) if value >= HEAVY_RAIN_THRESHOLD_MM]
        result["occurrence"] = occurrence(WET_DAY_THRESHOLD_MM)
        result["heavy_rain_occurrence"] = occurrence(HEAVY_RAIN_THRESHOLD_MM)
        result["wet_day_mae"] = (
            sum(abs(float(predicted[index]) - float(actual[index])) for index in wet_days) / len(wet_days)
            if wet_days else None
        )
        result["heavy_rain_mae"] = (
            sum(abs(float(predicted[index]) - float(actual[index])) for index in heavy_days) / len(heavy_days)
            if heavy_days else None
        )
        result["wet_day_count"] = len(wet_days)
        result["heavy_rain_count"] = len(heavy_days)
        result["rain_occurrence_f1"] = result["occurrence"]["f1"]
        result["heavy_rain"] = {
            "n": len(heavy_days),
            "mae": result["heavy_rain_mae"],
            "precision": result["heavy_rain_occurrence"]["precision"],
            "recall": result["heavy_rain_occurrence"]["recall"],
            "false_alarm_rate": result["heavy_rain_occurrence"]["false_alarm_rate"],
        }
        result["mape"] = None
    else:
        nonzero = [index for index, value in enumerate(actual) if value != 0]
        result["mape"] = (
            sum(absolute_errors[index] / abs(float(actual[index])) for index in nonzero)
            / len(nonzero) * 100.0
            if nonzero else None
        )
        result["mape_sample_count"] = len(nonzero)

    return result


def validation_summary(rows: Sequence[dict[str, Any]], target: str) -> dict[str, Any]:
    actual = [row["actual"] for row in rows]
    predicted = [row["predicted"] for row in rows]
    lead_windows = {"days_1_3": (1, 3), "days_4_7": (4, 7), "days_8_14": (8, 14)}
    by_lead = {
        name: regression_metrics(
            [row["actual"] for row in rows if first <= row["lead"] <= last],
            [row["predicted"] for row in rows if first <= row["lead"] <= last],
            target,
        )
        for name, (first, last) in lead_windows.items()
    }
    by_horizon = {}
    for lead in range(1, FORECAST_HORIZON_DAYS + 1):
        horizon_rows = [row for row in rows if row["lead"] == lead]
        by_horizon[str(lead)] = regression_metrics(
            [row["actual"] for row in horizon_rows],
            [row["predicted"] for row in horizon_rows],
            target,
        )
    summary = regression_metrics(actual, predicted, target)
    summary["by_lead"] = by_lead
    summary["by_horizon"] = by_horizon
    summary["forecast_origins"] = len({row["origin"] for row in rows})
    summary["validation_protocol"] = (
        "Fixed model fit on the first 80% of valid observations; non-overlapping "
        "14-day forecasts advance through the held-out period. Actual test observations "
        "are used only as lag inputs after their dates have passed; model weights are "
        "not refit on test data."
    )
    return summary


def validation_snapshot(models: dict[str, Any]) -> dict[str, Any]:
    snapshot = {}
    for model_id, model in models.items():
        station_snapshot = {}
        for station_id, station in model.get("stations", {}).items():
            target_snapshot = {}
            for target, result in station.get("targets", {}).items():
                validation = result.get("validation")
                if validation is None:
                    continue
                target_snapshot[target] = {
                    "model_name": result.get("model_name"),
                    "data_start": result.get("data_start"),
                    "data_end": result.get("data_end"),
                    "validation": deepcopy(validation),
                }
            if target_snapshot:
                station_snapshot[station_id] = {
                    "station_name": station.get("station_name", station_id),
                    "targets": target_snapshot,
                }
        snapshot[model_id] = {
            "status": model.get("status"),
            "errors": deepcopy(model.get("errors", [])),
            "stations": station_snapshot,
        }
    return snapshot


def preserve_published_validation(
    payload: dict[str, Any],
    models: Optional[dict[str, Any]] = None,
) -> None:
    history = payload.setdefault("validationHistory", {})
    baseline = validation_snapshot(models if models is not None else payload.get("models", {}))
    if baseline:
        history.setdefault(
            "published-baseline",
            {"generatedAt": payload.get("generatedAt"), "models": baseline},
        )


def record_validation_version(
    payload: dict[str, Any],
    models: dict[str, Any],
) -> str:
    version_id = validation_version_id()
    payload.setdefault("validationHistory", {})[version_id] = {
        "generatedAt": payload.get("generatedAt"),
        "models": validation_snapshot(models),
    }
    payload["activeValidationVersion"] = version_id
    return version_id


def validation_version_id() -> str:
    return datetime.now(SINGAPORE_TIMEZONE).strftime("80-20-%Y%m%dT%H%M%S%f%z")
