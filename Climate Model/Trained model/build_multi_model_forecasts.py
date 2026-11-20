import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from model_validation import (
    FORECAST_HORIZON_DAYS,
    chronological_split,
    filter_observations,
    preserve_published_validation,
    rolling_origin_predictions,
    validation_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from climate_history_validation import load_history

HISTORY_PATH = PROJECT_ROOT / "data" / "climate-history.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "climate-multi-model-forecasts.json"
TARGET_INDEXES = {
    "rainfall": 1,
    "mean_temperature": 2,
    "maximum_temperature": 3,
    "minimum_temperature": 4,
}
FORECAST_DAYS = FORECAST_HORIZON_DAYS
SINGAPORE_TIMEZONE = timezone(timedelta(hours=8))


def features(values, row_index, date):
    day = pd.Timestamp(date)
    recent = values[max(0, row_index - 28):row_index]
    result = [
        values[row_index - 1] if row_index >= 1 else float(np.mean(recent)),
        values[row_index - 2] if row_index >= 2 else float(np.mean(recent)),
        values[row_index - 7] if row_index >= 7 else float(np.mean(recent)),
        values[row_index - 14] if row_index >= 14 else float(np.mean(recent)),
        float(np.mean(recent)) if len(recent) else 0.0,
        math.sin(2 * math.pi * day.dayofyear / 365.25),
        math.cos(2 * math.pi * day.dayofyear / 365.25),
        float(day.month),
    ]
    return result


def model_for(target):
    objective = "count:poisson" if target == "rainfall" else "reg:squarederror"
    return XGBRegressor(
        n_estimators=350,
        max_depth=4,
        learning_rate=0.035,
        subsample=0.85,
        colsample_bytree=0.85,
        objective=objective,
        random_state=2026,
        n_jobs=-1,
    )


def predict_target(records, target, estimated_dates=()):
    target_index = TARGET_INDEXES[target]
    dates, values = filter_observations(
        [row[0] for row in records if isinstance(row, list) and len(row) == 5],
        [row[target_index] for row in records if isinstance(row, list) and len(row) == 5],
        estimated_dates,
    )
    if len(values) < 180:
        return None

    split, split_metadata = chronological_split(dates)
    if split <= 28:
        return None

    def fit_model(training_values, training_dates):
        rows, labels = [], []
        for index in range(28, len(training_values)):
            rows.append(features(training_values, index, training_dates[index]))
            labels.append(training_values[index])
        model = model_for(target)
        model.fit(np.asarray(rows), np.asarray(labels))
        return model

    validation_model = fit_model(values[:split], dates[:split])

    def predict_horizon(model, history_values, future_dates):
        history = list(history_values)
        predictions = []
        for forecast_date in future_dates:
            row = features(history, len(history), forecast_date)
            predicted = float(model.predict(np.asarray([row]))[0])
            if target == "rainfall":
                predicted = max(0.0, predicted)
            predictions.append(predicted)
            history.append(predicted)
        return predictions

    backtest = rolling_origin_predictions(
        dates,
        values,
        split,
        lambda history_dates, history_values, future_dates: predict_horizon(
            validation_model,
            history_values,
            future_dates,
        ),
    )
    if not backtest:
        return None
    validation = validation_summary(backtest, target)
    validation["data_split"] = split_metadata
    residuals = np.asarray(
        [row["actual"] - row["predicted"] for row in backtest],
        dtype=float,
    )

    future_dates = [
        dates[-1] + timedelta(days=offset)
        for offset in range(1, FORECAST_DAYS + 1)
    ]
    forecast = predict_horizon(validation_model, values, future_dates)
    residual_scale = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
    if not np.isfinite(residual_scale):
        residual_scale = 0.0
    interval = max(residual_scale, 1e-6) * 1.96
    forecast_rows = []
    for date, value in zip(future_dates, forecast):
        forecast_rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "value": round(value, 6),
            "lower": round(max(0.0, value - interval) if target == "rainfall" else value - interval, 6),
            "upper": round(value + interval, 6),
        })

    backtest_rows = [
        {
            "date": row["date"],
            "origin": row["origin"],
            "lead": row["lead"],
            "actual": row["actual"],
            "predicted": row["predicted"],
        }
        for row in backtest
    ]
    return {
        "model": "xgboost",
        "model_name": "XGBoost",
        "model_description": "Lag-feature gradient boosted regression tree with calendar features, trained from the shared bundled NEA observations.",
        "data_start": dates[0].isoformat(),
        "data_end": dates[-1].isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast": forecast_rows,
        "backtest": backtest_rows,
        "validation": validation,
    }


def build():
    history = load_history(HISTORY_PATH)

    if OUTPUT_PATH.exists():
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    else:
        payload = {
            "schemaVersion": 1,
            "source": "NEA Historical Daily Records",
            "forecastDays": FORECAST_DAYS,
            "models": {},
        }
    preserve_published_validation(payload)
    stations_output = payload.get("models", {}).get("xgboost", {}).get("stations", {})
    errors = []
    for station_code, station_name in history["stations"].items():
        pass
    # station metadata is not guaranteed to be the same shape across cache versions.
    station_records = history["stations"]
    station_names = history.get("stationNames", history.get("station_names", {}))
    for station_code, records in station_records.items():
        targets = {}
        for target, target_index in TARGET_INDEXES.items():
            estimated_dates = [
                observed_date
                for observed_date, fields in history.get("estimatedValues", {}).get(station_code, {}).items()
                if target in fields
            ]
            _, eligible_values = filter_observations(
                [row[0] for row in records],
                [row[target_index] for row in records],
                estimated_dates,
            )
            if len(eligible_values) < 180:
                continue
            result = predict_target(records, target, estimated_dates)
            if result:
                targets[target] = result
            else:
                message = {
                    "station": station_code,
                    "target": target,
                    "error": "Insufficient valid history for a 14-day chronological holdout.",
                }
                errors.append(message)
                print(
                    f"{station_code} {target} xgboost: {message['error']}",
                    file=sys.stderr,
                )
        if targets:
            station = stations_output.setdefault(
                station_code, {"station_name": station_names.get(station_code, station_code), "targets": {}}
            )
            station["targets"].update(targets)
    if errors:
        raise RuntimeError(f"XGBoost failed for {len(errors)} eligible station targets; previous forecasts were preserved.")
    payload["validationBaselineCaptured"] = bool(
        payload.get("validationHistory", {}).get("published-baseline")
    )
    payload["generatedAt"] = datetime.now(SINGAPORE_TIMEZONE).isoformat()
    payload["forecastDays"] = FORECAST_DAYS
    payload.setdefault("models", {})["xgboost"] = {
        "status": "partial" if errors and stations_output else "ready" if stations_output else "unavailable",
        "stations": stations_output,
        "errors": errors,
    }
    temporary_path = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary_path.replace(OUTPUT_PATH)
    return payload


if __name__ == "__main__":
    build()
