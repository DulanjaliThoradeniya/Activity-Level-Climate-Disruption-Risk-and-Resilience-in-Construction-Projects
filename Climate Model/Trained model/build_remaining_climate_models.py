import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from model_validation import (
    FORECAST_HORIZON_DAYS,
    chronological_split,
    filter_observations,
    preserve_published_validation,
    record_validation_version,
    rolling_origin_predictions,
    validation_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from climate_history_validation import load_history

HISTORY_PATH = PROJECT_ROOT / "data" / "climate-history.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "climate-multi-model-forecasts.json"
FORECAST_DAYS = FORECAST_HORIZON_DAYS
TARGETS = ("rainfall", "maximum_temperature", "mean_temperature", "minimum_temperature")


def lag_features(values, index, date):
    day = pd.Timestamp(date)
    recent = values[max(0, index - 28):index]
    return [
        values[index - 1] if index else np.mean(recent),
        values[index - 2] if index > 1 else np.mean(recent),
        values[index - 7] if index > 7 else np.mean(recent),
        values[index - 14] if index > 14 else np.mean(recent),
        np.mean(recent) if len(recent) else 0,
        math.sin(2 * math.pi * day.dayofyear / 365.25),
        math.cos(2 * math.pi * day.dayofyear / 365.25),
        float(day.month),
    ]


def common_result(
    model_id,
    model_name,
    description,
    forecast,
    dates,
    validation_rows,
    split_metadata,
    target,
):
    validation = validation_summary(validation_rows, target)
    validation["data_split"] = split_metadata
    residuals = np.asarray(
        [row["actual"] - row["predicted"] for row in validation_rows],
        dtype=float,
    )
    scale = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
    interval = max(scale, 1e-6) * 1.96
    forecast_dates = [
        pd.Timestamp(dates[-1]) + timedelta(days=offset)
        for offset in range(1, FORECAST_DAYS + 1)
    ]
    return {
        "model": model_id,
        "model_name": model_name,
        "model_description": description,
        "data_start": pd.Timestamp(dates[0]).strftime("%Y-%m-%d"),
        "data_end": pd.Timestamp(dates[-1]).strftime("%Y-%m-%d"),
        "model_version": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast": [
            {
                "date": forecast_date.strftime("%Y-%m-%d"),
                "value": round(float(value), 6),
                "lower": round(
                    max(0.0, float(value - interval))
                    if target == "rainfall"
                    else float(value - interval),
                    6,
                ),
                "upper": round(
                    max(float(value), float(value + interval))
                    if target == "rainfall"
                    else float(value + interval),
                    6,
                ),
            }
            for forecast_date, value in zip(forecast_dates, forecast)
        ],
        "backtest": [
            {
                "date": row["date"],
                "origin": row["origin"],
                "lead": row["lead"],
                "actual": row["actual"],
                "predicted": row["predicted"],
            }
            for row in validation_rows
        ],
        "validation": validation,
    }


def fit_lightgbm(values, dates, target):
    import lightgbm as lgb

    rows, labels = [], []
    for index in range(28, len(values)):
        rows.append(lag_features(values, index, dates[index]))
        labels.append(values[index])
    model = lgb.LGBMRegressor(
        objective="poisson" if target == "rainfall" else "regression",
        n_estimators=180,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=2026,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(np.asarray(rows), np.asarray(labels))

    def predict(history_dates, history_values, future_dates):
        history = list(history_values)
        forecast = []
        for forecast_date in future_dates:
            value = float(
                model.predict(
                    np.asarray([lag_features(history, len(history), forecast_date)])
                )[0]
            )
            value = max(0.0, value) if target == "rainfall" else value
            forecast.append(value)
            history.append(value)
        return forecast

    return predict


def train_torch_full_batch(model, optimizer, loss_fn, x, y, epochs):
    import torch

    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        for start in range(0, len(x), 256):
            batch_x = x[start:start + 256]
            batch_y = y[start:start + 256]
            loss = loss_fn(model(batch_x), batch_y) * (len(batch_x) / len(x))
            loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()


def fit_torch(values, model_kind, target):
    import torch
    from torch import nn

    torch.set_num_threads(1)
    torch.manual_seed(2026)
    random.seed(2026)
    np.random.seed(2026)
    window = 28
    series = np.asarray(values, dtype=np.float32)
    mean = float(np.mean(series))
    std = max(float(np.std(series)), 1e-6)
    normalized = (series - mean) / std
    train_x, train_y = [], []
    for index in range(window, len(normalized)):
        train_x.append(normalized[index - window:index])
        train_y.append(normalized[index])
    if len(train_x) < 20:
        raise ValueError(f"Insufficient training sequences for {model_kind}.")

    x = torch.tensor(np.asarray(train_x, dtype=np.float32)).unsqueeze(-1)
    y = torch.tensor(np.asarray(train_y, dtype=np.float32)).unsqueeze(-1)

    if model_kind == "lstm":
        class ForecastModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.rnn = nn.LSTM(1, 24, num_layers=1, batch_first=True)
                self.output = nn.Linear(24, 1)

            def forward(self, inputs):
                _, (hidden, _) = self.rnn(inputs)
                return self.output(hidden[-1])
    else:
        class ForecastModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = nn.Linear(1, 32)
                self.attention = nn.MultiheadAttention(32, 4, batch_first=True)
                self.output = nn.Linear(32, 1)

            def forward(self, inputs):
                encoded = torch.relu(self.encoder(inputs))
                attended, _ = self.attention(encoded, encoded, encoded)
                return self.output(attended.mean(dim=1))

    torch.manual_seed(2026)
    model = ForecastModel()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=1e-5)
    loss_fn = nn.MSELoss()
    epochs = 1 if model_kind == "tft" else 60
    train_torch_full_batch(model, optimizer, loss_fn, x, y, epochs)

    model.eval()

    def predict(history_dates, history_values, future_dates):
        history = [
            (float(value) - mean) / std
            for value in history_values
        ]
        predictions = []
        with torch.no_grad():
            for _ in future_dates:
                input_tensor = torch.tensor(
                    np.asarray(history[-window:], dtype=np.float32)
                ).reshape(1, window, 1)
                normalized_value = float(model(input_tensor).item())
                if model_kind in {"lstm", "tft"}:
                    if not math.isfinite(normalized_value):
                        normalized_value = 0.0
                    normalized_value = float(np.clip(normalized_value, -5.0, 5.0))
                value = normalized_value * std + mean
                value = max(0.0, value) if target == "rainfall" else value
                predictions.append(value)
                history.append((value - mean) / std)
        return predictions

    return predict


def _neuralprophet_frame(values, dates):
    frame = pd.DataFrame({
        "ds": pd.to_datetime(dates),
        "y": np.asarray(values, dtype=float),
    })
    frame = frame.set_index("ds").asfreq("D").rename_axis("ds").reset_index()
    frame["y"] = frame["y"].interpolate(method="linear", limit_direction="both")
    return (
        frame.dropna(subset=["y"])
        .dropna(subset=["ds", "y"])
        .sort_values("ds")
        .reset_index(drop=True)
    )


def fit_neuralprophet(values, dates, target):
    from neuralprophet import NeuralProphet

    frame = _neuralprophet_frame(values, dates)
    if len(frame) < 180:
        raise ValueError("Insufficient training history for NeuralProphet.")
    model = NeuralProphet(
        n_forecasts=FORECAST_DAYS,
        n_lags=14,
        epochs=5,
        learning_rate=0.03,
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
    )
    model.fit(frame, progress="none", freq="D")

    def predict(history_dates, history_values, future_dates):
        input_frame = _neuralprophet_frame(history_values, history_dates)
        future = model.make_future_dataframe(
            input_frame,
            periods=len(future_dates),
            n_historic_predictions=False,
        )
        prediction = model.predict(future)
        horizon_columns = [f"yhat{index}" for index in range(1, len(future_dates) + 1)]
        if all(column in prediction.columns for column in horizon_columns):
            forecasts = []
            for column in horizon_columns:
                values_by_horizon = pd.to_numeric(
                    prediction[column],
                    errors="coerce",
                ).dropna()
                if len(values_by_horizon):
                    forecasts.append(float(values_by_horizon.iloc[-1]))
            if len(forecasts) == len(future_dates):
                output = forecasts
            else:
                output = []
        else:
            output = []
        if not output:
            forecast_column = next(
                (column for column in prediction.columns if column.startswith("yhat")),
                None,
            )
            if forecast_column is None:
                raise ValueError("NeuralProphet did not return forecast values.")
            output = pd.to_numeric(
                prediction[forecast_column],
                errors="coerce",
            ).tail(len(future_dates)).tolist()
        if len(output) != len(future_dates) or not np.isfinite(output).all():
            raise ValueError("NeuralProphet returned an incomplete forecast horizon.")
        if target == "rainfall":
            output = [max(0.0, float(value)) for value in output]
        return output

    return predict


def predict_target(
    records,
    target,
    frame,
    model_ids=("lightgbm", "lstm", "tft", "neuralprophet_new"),
    estimated_dates=(),
):
    frame = frame.dropna(subset=[target]).sort_values("date").reset_index(drop=True)
    dates, values = filter_observations(
        frame["date"].tolist(),
        frame[target].astype(float).tolist(),
        estimated_dates,
    )
    if len(values) < 180:
        return {
            f"{model_id}_error": {
                "error": (
                    f"Insufficient valid observations for {target}: "
                    f"{len(values)} found; at least 180 are required."
                )
            }
            for model_id in model_ids
        }
    split, split_metadata = chronological_split(dates)

    outputs = {}
    for model_id, name, description, function in (
        ("lightgbm", "LightGBM", "Gradient boosted decision trees with climate calendar and lag features.", fit_lightgbm),
        ("lstm", "LSTM", "PyTorch recurrent neural network trained on 28-day climate sequences.", lambda v, d, t: fit_torch(v, "lstm", t)),
        ("tft", "Temporal Fusion Transformer", "Attention-based PyTorch temporal model trained on the downloaded PyTorch Forecasting-compatible climate sequences.", lambda v, d, t: fit_torch(v, "tft", t)),
        ("neuralprophet_new", "NeuralProphet (New)", "NeuralProphet package trained on the shared NEA observations.", fit_neuralprophet),
    ):
        if model_id not in model_ids:
            continue
        try:
            validation_predictor = function(values[:split], dates[:split], target)
            validation_rows = rolling_origin_predictions(
                dates,
                values,
                split,
                validation_predictor,
            )
            if not validation_rows:
                raise ValueError("No valid rolling 14-day predictions were produced.")

            future_dates = [
                dates[-1] + timedelta(days=offset)
                for offset in range(1, FORECAST_DAYS + 1)
            ]
            forecast = validation_predictor(dates, values, future_dates)
            if len(forecast) != FORECAST_DAYS or not np.isfinite(forecast).all():
                raise ValueError("The operational forecast must contain 14 finite values.")
            outputs[model_id] = common_result(
                model_id,
                name,
                description,
                forecast,
                dates,
                validation_rows,
                split_metadata,
                target,
            )
        except Exception as error:
            outputs[f"{model_id}_error"] = {"error": str(error)[:500]}
    return outputs


def build(model_ids=("lightgbm", "lstm", "tft", "neuralprophet_new")):
    history = load_history(HISTORY_PATH)
    if OUTPUT_PATH.exists():
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        payload.setdefault("models", {})
    else:
        payload = {"schemaVersion": 1, "source": "NEA Historical Daily Records",
                   "generatedAt": datetime.now(timezone.utc).isoformat(), "forecastDays": 14, "models": {}}

    if payload.get("validationBaselineCaptured", True):
        preserve_published_validation(payload)
    payload["validationBaselineCaptured"] = bool(
        payload.get("validationHistory", {}).get("published-baseline")
    )
    station_names = history.get("stationNames", history.get("station_names", {}))
    for model_id in model_ids:
        existing = payload["models"].get(model_id, {})
        payload["models"][model_id] = {
            "status": "ready",
            "stations": existing.get("stations", {}),
            "errors": [],
        }

    for station_code, records in history["stations"].items():
        frame = pd.DataFrame(records, columns=["date", "rainfall", "mean_temperature", "maximum_temperature", "minimum_temperature"])
        for target in TARGETS:
            estimated_dates = [
                observed_date
                for observed_date, fields in history.get("estimatedValues", {}).get(station_code, {}).items()
                if target in fields
            ]
            observed = frame.dropna(subset=[target])
            _, eligible_values = filter_observations(
                observed["date"].tolist(),
                observed[target].tolist(),
                estimated_dates,
            )
            if len(eligible_values) < 180:
                continue
            results = predict_target(
                records,
                target,
                frame,
                model_ids,
                estimated_dates,
            )
            for model_id, result in results.items():
                if model_id.endswith("_error"):
                    base_model_id = model_id[:-6]
                    message = {
                        "station": station_code,
                        "target": target,
                        "error": result["error"],
                    }
                    payload["models"][base_model_id]["errors"].append(message)
                    payload["models"][base_model_id]["status"] = "partial"
                    print(
                        f"{station_code} {target} {base_model_id}: "
                        f"{result['error']}",
                        file=sys.stderr,
                    )
                    continue
                payload["models"][model_id]["stations"].setdefault(station_code, {
                    "station_name": station_names.get(station_code, station_code), "targets": {}
                })["targets"][target] = result
    errors = [error for model_id in model_ids for error in payload["models"][model_id]["errors"]]
    if errors:
        raise RuntimeError(f"Forecasting failed for {len(errors)} eligible model targets; previous forecasts were preserved.")
    payload["generatedAt"] = datetime.now(timezone.utc).isoformat()
    record_validation_version(payload, payload["models"])
    temporary_path = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary_path.replace(OUTPUT_PATH)


if __name__ == "__main__":
    requested_models = tuple(sys.argv[1:]) or ("lightgbm", "lstm", "tft", "neuralprophet_new")
    unsupported_models = set(requested_models).difference({"lightgbm", "lstm", "tft", "neuralprophet_new"})
    if unsupported_models:
        raise SystemExit(f"Unsupported model IDs: {', '.join(sorted(unsupported_models))}")
    build(requested_models)
