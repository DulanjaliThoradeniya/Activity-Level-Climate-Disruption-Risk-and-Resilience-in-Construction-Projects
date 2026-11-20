import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

from prophet_forecast import TARGETS, load_area_stations, new_prophet_model


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from climate_history_validation import load_history

HISTORY_PATH = ROOT / "data" / "climate-history.json"
OUTPUT_PATH = ROOT / "data" / "climate-forecasts.json"
TARGET_COLUMNS = {
    "rainfall": 1,
    "mean_temperature": 2,
    "maximum_temperature": 3,
    "minimum_temperature": 4,
}
MINIMUM_OBSERVATIONS = 425
FORECAST_DAYS = 14
HISTORICAL_PAGE = "https://www.weather.gov.sg/climate-historical-daily/"


def _metric_values(actual, predicted):
    non_zero = actual != 0
    return {
        "n": int(len(actual)),
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mape": (
            float(np.mean(np.abs((actual[non_zero] - predicted[non_zero]) / actual[non_zero])) * 100)
            if np.any(non_zero)
            else None
        ),
        "mape_sample_count": int(np.count_nonzero(non_zero)),
    }


def _predict(model, periods):
    future = model.make_future_dataframe(periods=periods)
    return model.predict(future)[["ds", "yhat", "yhat_lower", "yhat_upper"]]


def _forecast_target(records, target_name, generated_at, forecast_start):
    target_index = TARGET_COLUMNS[target_name]
    observations = [
        {"ds": pd.Timestamp(row[0]), "y": float(row[target_index])}
        for row in records
        if isinstance(row, list)
        and len(row) > target_index
        and isinstance(row[0], str)
        and isinstance(row[target_index], (int, float))
        and math.isfinite(row[target_index])
    ]
    if not observations:
        return None
    data = pd.DataFrame(observations).sort_values("ds").drop_duplicates("ds").reset_index(drop=True)
    if len(data) < MINIMUM_OBSERVATIONS:
        return None

    test_days = min(60, max(30, len(data) // 10))
    training = data.iloc[:-test_days].copy()
    test = data.iloc[-test_days:].copy()
    if len(training) < 365:
        return None

    validation_model = new_prophet_model()
    validation_model.fit(training)
    heldout_days = (test["ds"].max() - training["ds"].max()).days
    validation_forecast = _predict(validation_model, heldout_days)
    evaluation = test.merge(validation_forecast, on="ds", how="left")
    actual = evaluation["y"].to_numpy()
    predicted = evaluation["yhat"].to_numpy()
    validation = _metric_values(actual, predicted)
    validation["start"] = test["ds"].min().strftime("%Y-%m-%d")
    validation["end"] = test["ds"].max().strftime("%Y-%m-%d")

    latest_date = data["ds"].max().date()
    forecast_days = (forecast_start - latest_date).days + FORECAST_DAYS
    if forecast_days <= 0:
        forecast_days = FORECAST_DAYS
    model = new_prophet_model()
    model.fit(data)
    future_forecast = _predict(model, forecast_days)
    future_forecast = future_forecast.loc[
        future_forecast["ds"] >= pd.Timestamp(forecast_start)
    ].head(FORECAST_DAYS)
    rainfall = target_name == "rainfall"
    forecast = [
        {
            "date": row.ds.strftime("%Y-%m-%d"),
            "value": max(0.0, float(row.yhat)) if rainfall else float(row.yhat),
            "lower": max(0.0, float(row.yhat_lower)) if rainfall else float(row.yhat_lower),
            "upper": max(0.0, float(row.yhat_upper)) if rainfall else float(row.yhat_upper),
        }
        for row in future_forecast.itertuples(index=False)
    ]
    if len(forecast) != FORECAST_DAYS:
        raise RuntimeError(
            f"Prophet produced {len(forecast)} future days for {target_name}; "
            f"expected {FORECAST_DAYS}."
        )

    return {
        "model": "prophet",
        "model_name": "Prophet",
        "model_description": (
            "Facebook Prophet additive time-series forecast with yearly and weekly "
            "seasonality, trained on the latest bundled NEA daily observations."
        ),
        "data_end": latest_date.isoformat(),
        "generated_at": generated_at,
        "forecast": forecast,
        "backtest": [
            {
                "date": row.ds.strftime("%Y-%m-%d"),
                "actual": float(row.y),
                "predicted": max(0.0, float(row.yhat)) if rainfall else float(row.yhat),
            }
            for row in evaluation.itertuples(index=False)
        ],
        "validation": validation,
        "source_url": HISTORICAL_PAGE,
    }


def build_payload(history_path=HISTORY_PATH, output_path=OUTPUT_PATH, station_code=None, target_name=None):
    if target_name is not None and target_name not in TARGETS:
        raise ValueError(f"Unknown forecast variable: {target_name}")
    history = load_history(history_path)

    station_names = {}
    for station in load_area_stations().values():
        station_names.setdefault(station["code"], station["name"])
    if station_code is not None and station_code not in station_names:
        raise ValueError(f"Unknown NEA station code: {station_code}")

    generated_at = datetime.now(timezone.utc).isoformat()
    forecast_start = datetime.now(ZoneInfo("Asia/Singapore")).date()
    stations = {}
    station_codes = [station_code] if station_code else sorted(station_names)
    targets = [target_name] if target_name else list(TARGETS)
    for code in station_codes:
        records = history["stations"].get(code)
        if not isinstance(records, list):
            continue
        target_results = {}
        for name in targets:
            result = _forecast_target(records, name, generated_at, forecast_start)
            if result is not None:
                target_results[name] = result
        if target_results:
            stations[code] = {
                "station_name": station_names[code],
                "targets": target_results,
            }
            print(f"{code}: generated {len(target_results)} Prophet forecasts")

    payload = {
        "schemaVersion": 1,
        "source": "NEA Historical Daily Records",
        "sourceUrl": HISTORICAL_PAGE,
        "generatedAt": generated_at,
        "forecastStart": forecast_start.isoformat(),
        "forecastDays": FORECAST_DAYS,
        "model": "Prophet",
        "stations": stations,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    print(f"Wrote {output_path} with {len(stations)} stations.")


def main():
    parser = argparse.ArgumentParser(
        description="Build daily Prophet forecasts from the bundled NEA historical records."
    )
    parser.add_argument("--station", help="Build one station only, for example S24.")
    parser.add_argument("--target", choices=sorted(TARGETS), help="Build one climate variable only.")
    parser.add_argument("--history", type=Path, default=HISTORY_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    build_payload(args.history, args.output, args.station, args.target)


if __name__ == "__main__":
    main()
