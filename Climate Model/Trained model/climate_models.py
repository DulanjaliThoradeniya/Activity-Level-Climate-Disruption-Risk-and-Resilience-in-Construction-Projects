from __future__ import annotations

import json
import sys
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from prophet import Prophet
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, TweedieRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
)
from xgboost import XGBClassifier, XGBRegressor


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from climate_history_validation import load_history

HISTORY_PATH = PROJECT_ROOT / "data" / "climate-history.json"
FORECAST_PATH = PROJECT_ROOT / "data" / "climate-forecasts.json"
HORIZON_DAYS = 14
BACKTEST_FOLDS = 5
RAINFALL_EVALUATION_DAYS = 364
RAINFALL_VALIDATION_DAYS = 364
DETAILED_EVALUATION_STATION = "S24"
STANDARD_RAINFALL_EVALUATION_DAYS = 28
STANDARD_TEMPERATURE_BACKTEST_FOLDS = BACKTEST_FOLDS
RAINFALL_FOLD_STRIDE = 14
MINIMUM_OBSERVATIONS = 425
TARGETS = {
    "rainfall": {"index": 1, "label": "Daily Rainfall Total (mm)", "unit": "mm"},
    "mean_temperature": {"index": 2, "label": "Mean Temperature", "unit": "deg C"},
    "maximum_temperature": {"index": 3, "label": "Maximum Temperature", "unit": "deg C"},
    "minimum_temperature": {"index": 4, "label": "Minimum Temperature", "unit": "deg C"},
}
LEAD_WINDOWS = {"days_1_3": (1, 3), "days_4_7": (4, 7), "days_8_14": (8, 14)}
FEATURE_LAGS = (1, 2, 3, 7, 14, 30, 56, 364)
SUMMARY_WINDOWS = (3, 7, 14, 30, 90, 365)
WEATHER_COLUMNS = ("mean_temperature", "maximum_temperature", "minimum_temperature")
XGB_PARAMETERS = {
    "n_estimators": 120,
    "max_depth": 3,
    "learning_rate": 0.05,
    "subsample": 0.85,
    "colsample_bytree": 0.9,
    "min_child_weight": 5,
    "reg_lambda": 3,
    "n_jobs": 1,
    "random_state": 5231,
    "tree_method": "hist",
}
RAIN_THRESHOLDS = tuple(round(float(value), 2) for value in np.arange(0.1, 0.901, 0.05))
CLASSIFIER_CANDIDATES = (
    "xgboost",
    "xgboost_balanced",
    "xgboost_regularized",
    "random_forest",
)
AMOUNT_CANDIDATES = ("xgboost", "random_forest", "tweedie")


def _metric_values(actual: list[float], predicted: list[float]) -> dict[str, float | int]:
    if not actual:
        return {"n": 0, "mae": None, "rmse": None, "mean_bias": None}
    return {
        "n": len(actual),
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mean_bias": float(np.mean(np.asarray(predicted) - np.asarray(actual))),
    }


def _occurrence_values(
    actual: list[float],
    probabilities: list[float],
    threshold: float = 0.5,
) -> dict[str, float | int]:
    observed = np.asarray(actual) > 0
    predicted = np.asarray(probabilities) >= threshold
    true_positive = int(np.sum(observed & predicted))
    false_positive = int(np.sum(~observed & predicted))
    true_negative = int(np.sum(~observed & ~predicted))
    false_negative = int(np.sum(observed & ~predicted))
    return {
        "n": len(actual),
        "rainy_days": int(observed.sum()),
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(observed, predicted)),
        "precision": float(precision_score(observed, predicted, zero_division=0)),
        "recall": float(recall_score(observed, predicted, zero_division=0)),
        "f1": float(f1_score(observed, predicted, zero_division=0)),
        "false_positive_rate": (
            float(false_positive / (false_positive + true_negative))
            if false_positive + true_negative
            else 0.0
        ),
        "brier": float(np.mean((np.asarray(probabilities) - observed.astype(float)) ** 2)),
        "confusion_matrix": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
    }


def _series_maps(records: list[list[Any]]) -> tuple[dict[str, dict[date, float]], list[date]]:
    maps = {name: {} for name in TARGETS}
    dates = []
    for row in records:
        observed_date = date.fromisoformat(row[0])
        dates.append(observed_date)
        for name, target in TARGETS.items():
            value = row[target["index"]]
            if isinstance(value, (int, float)) and np.isfinite(value):
                maps[name][observed_date] = float(value)
    return maps, dates


def _index_series(
    maps: dict[str, dict[date, float]],
) -> dict[str, tuple[list[date], np.ndarray, dict[date, float]]]:
    indexed = {}
    for name, values in maps.items():
        observed_dates = sorted(values)
        indexed[name] = (
            observed_dates,
            np.asarray([values[observed_date] for observed_date in observed_dates], dtype=float),
            values,
        )
    return indexed


def _context_features(
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    origin: date,
    forecast_date: date,
    lead: int,
) -> list[float]:
    annual = (forecast_date.timetuple().tm_yday - 1) / 365.2425 * 2 * np.pi
    weekday = forecast_date.weekday() / 7 * 2 * np.pi
    month = forecast_date.month
    features = [
        float(lead),
        float((forecast_date - date(forecast_date.year, 1, 1)).days),
        float(np.sin(annual)),
        float(np.cos(annual)),
        float(np.sin(annual * 2)),
        float(np.cos(annual * 2)),
        float(np.sin(weekday)),
        float(np.cos(weekday)),
        float(month in (11, 12, 1, 2, 3)),
        float(month in (6, 7, 8, 9)),
        float(month in (4, 5, 10)),
    ]
    for name in ("rainfall", *WEATHER_COLUMNS):
        observed_dates, observed_values, values = indexed_series[name]
        observed_index = bisect_right(observed_dates, origin)
        for lag in FEATURE_LAGS:
            features.append(values.get(origin - timedelta(days=lag), np.nan))
        for window in SUMMARY_WINDOWS:
            start = origin - timedelta(days=window - 1)
            start_index = bisect_left(observed_dates, start)
            sample = observed_values[start_index:observed_index]
            features.extend(
                [
                    float(np.mean(sample)) if len(sample) else np.nan,
                    float(np.sum(sample)) if len(sample) else np.nan,
                    float(np.std(sample)) if len(sample) else np.nan,
                ]
            )
            if name == "rainfall":
                wet = sample[sample > 0]
                features.extend(
                    [
                        float(len(wet) / len(sample)) if len(sample) else np.nan,
                        float(np.mean(wet)) if len(wet) else 0.0,
                    ]
                )
            else:
                features.extend(
                    [
                        float(np.min(sample)) if len(sample) else np.nan,
                        float(np.max(sample)) if len(sample) else np.nan,
                    ]
                )
        if name == "rainfall":
            streaks = {True: 0, False: 0}
            for day_offset in range(1, 366):
                observed = values.get(origin - timedelta(days=day_offset))
                if observed is None:
                    break
                is_wet = observed > 0
                streaks[is_wet] += 1
                streaks[not is_wet] = 0
            features.extend([float(streaks[False]), float(streaks[True])])
        else:
            recent_mean = values.get(origin)
            week_sample = observed_values[
                bisect_left(observed_dates, origin - timedelta(days=6)):observed_index
            ]
            month_sample = observed_values[
                bisect_left(observed_dates, origin - timedelta(days=29)):observed_index
            ]
            features.extend(
                [
                    float(np.mean(week_sample) - np.mean(month_sample))
                    if len(week_sample) and len(month_sample)
                    else np.nan,
                    float(recent_mean) if recent_mean is not None else np.nan,
                ]
            )
    return features


def _feature_names() -> list[str]:
    names = [
        "lead",
        "day_of_year",
        "annual_sin",
        "annual_cos",
        "semiannual_sin",
        "semiannual_cos",
        "weekday_sin",
        "weekday_cos",
        "northeast_monsoon",
        "southwest_monsoon",
        "intermonsoon",
    ]
    for variable in ("rainfall", *WEATHER_COLUMNS):
        names.extend(f"{variable}_lag_{lag}" for lag in FEATURE_LAGS)
        for window in SUMMARY_WINDOWS:
            names.extend(
                [
                    f"{variable}_mean_{window}",
                    f"{variable}_total_{window}",
                    f"{variable}_std_{window}",
                ]
            )
            if variable == "rainfall":
                names.extend(
                    [f"rainfall_wet_frequency_{window}", f"rainfall_wet_mean_{window}"]
                )
            else:
                names.extend([f"{variable}_min_{window}", f"{variable}_max_{window}"])
        if variable == "rainfall":
            names.extend(["consecutive_dry_days", "consecutive_wet_days"])
        else:
            names.extend([f"{variable}_week_minus_month_mean", f"{variable}_origin"])
    return names


def _training_examples(
    target_dates: list[date],
    maps: dict[str, dict[date, float]],
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    cutoff: date,
) -> tuple[np.ndarray, np.ndarray]:
    available_targets = maps["rainfall"]
    feature_rows = []
    labels = []
    origin_dates = [observed_date for observed_date in target_dates if observed_date < cutoff]
    for origin in origin_dates:
        if (origin - target_dates[0]).days % 7:
            continue
        if (origin - target_dates[0]).days < 365:
            continue
        for lead in range(1, HORIZON_DAYS + 1):
            target_date = origin + timedelta(days=lead)
            if target_date > cutoff or target_date not in available_targets:
                continue
            feature_rows.append(_context_features(indexed_series, origin, target_date, lead))
            labels.append(available_targets[target_date])
    if not labels:
        raise ValueError("Insufficient historical rainfall to train the XGBoost model.")
    return np.asarray(feature_rows, dtype=float), np.asarray(labels, dtype=float)


def _fit_rainfall_model(
    target_dates: list[date],
    maps: dict[str, dict[date, float]],
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    cutoff: date,
) -> tuple[XGBClassifier, XGBRegressor]:
    features, rainfall = _training_examples(target_dates, maps, indexed_series, cutoff)
    wet = rainfall > 0
    if wet.sum() < 20 or (~wet).sum() < 20:
        raise ValueError("Insufficient wet/dry examples to fit the two-stage rainfall model.")
    occurrence = XGBClassifier(eval_metric="logloss", **XGB_PARAMETERS)
    amount = XGBRegressor(objective="reg:squarederror", **XGB_PARAMETERS)
    occurrence.fit(features, wet.astype(int))
    amount.fit(features[wet], rainfall[wet])
    return occurrence, amount


def _fit_rainfall_candidates(
    target_dates: list[date],
    maps: dict[str, dict[date, float]],
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    cutoff: date,
    include_alternatives: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    features, rainfall = _training_examples(target_dates, maps, indexed_series, cutoff)
    wet = rainfall > 0
    wet_count = int(wet.sum())
    dry_count = int((~wet).sum())
    if wet_count < 20 or dry_count < 20:
        raise ValueError("Insufficient wet/dry examples to fit the two-stage rainfall models.")
    if not include_alternatives:
        occurrence = XGBClassifier(eval_metric="logloss", **XGB_PARAMETERS)
        amount = XGBRegressor(objective="reg:squarederror", **XGB_PARAMETERS)
        occurrence.fit(features, wet.astype(int))
        amount.fit(features[wet], rainfall[wet])
        return {"xgboost": occurrence}, {"xgboost": amount}
    classifiers: dict[str, Any] = {
        "xgboost": XGBClassifier(eval_metric="logloss", **XGB_PARAMETERS),
        "xgboost_balanced": XGBClassifier(
            eval_metric="logloss",
            scale_pos_weight=dry_count / wet_count,
            **XGB_PARAMETERS,
        ),
        "xgboost_regularized": XGBClassifier(
            eval_metric="logloss",
            n_estimators=180,
            max_depth=2,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=10,
            reg_lambda=8,
            n_jobs=1,
            random_state=5231,
            tree_method="hist",
        ),
        "random_forest": make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestClassifier(
                n_estimators=200,
                min_samples_leaf=5,
                class_weight="balanced_subsample",
                max_features="sqrt",
                n_jobs=1,
                random_state=5231,
            ),
        ),
    }
    regressors: dict[str, Any] = {
        "xgboost": XGBRegressor(objective="reg:squarederror", **XGB_PARAMETERS),
        "random_forest": make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestRegressor(
                n_estimators=200,
                min_samples_leaf=5,
                max_features=0.8,
                n_jobs=1,
                random_state=5231,
            ),
        ),
        "tweedie": make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            TweedieRegressor(power=1.5, link="log", alpha=1.0, max_iter=1000),
        ),
    }
    for classifier in classifiers.values():
        classifier.fit(features, wet.astype(int))
    for regressor in regressors.values():
        regressor.fit(features[wet], rainfall[wet])
    return classifiers, regressors


def _predict_rainfall_candidates(
    classifiers: dict[str, Any],
    regressors: dict[str, Any],
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    origin: date,
    forecast_dates: list[date],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    features = np.asarray(
        [
            _context_features(indexed_series, origin, forecast_date, lead)
            for lead, forecast_date in enumerate(forecast_dates, start=1)
        ],
        dtype=float,
    )
    probabilities = {
        name: classifier.predict_proba(features)[:, 1]
        for name, classifier in classifiers.items()
    }
    amounts = {
        name: np.maximum(0.0, regressor.predict(features))
        for name, regressor in regressors.items()
    }
    return probabilities, amounts


def _fit_probability_calibrator(
    probabilities: list[float],
    actual: list[float],
    method: str,
) -> Any:
    probability_array = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    wet = np.asarray(actual) > 0
    if method == "raw":
        return None
    if len(np.unique(wet)) < 2:
        raise ValueError("Probability calibration requires both wet and dry validation cases.")
    if method == "sigmoid":
        logits = np.log(probability_array / (1 - probability_array)).reshape(-1, 1)
        calibrator = LogisticRegression(C=1.0, random_state=5231)
        calibrator.fit(logits, wet.astype(int))
        return calibrator
    if method == "isotonic":
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(probability_array, wet.astype(float))
        return calibrator
    raise ValueError(f"Unsupported rainfall probability calibration: {method}")


def _apply_probability_calibrator(
    probabilities: list[float] | np.ndarray,
    calibrator: Any,
    method: str,
) -> np.ndarray:
    values = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    if method == "raw":
        return values
    if method == "sigmoid":
        logits = np.log(values / (1 - values)).reshape(-1, 1)
        return calibrator.predict_proba(logits)[:, 1]
    return np.asarray(calibrator.predict(values), dtype=float)


def _predict_rainfall(
    model: tuple[XGBClassifier, XGBRegressor],
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    origin: date,
    forecast_dates: list[date],
) -> list[dict[str, Any]]:
    occurrence_model, amount_model = model
    features = np.asarray(
        [
            _context_features(indexed_series, origin, forecast_date, lead)
            for lead, forecast_date in enumerate(forecast_dates, start=1)
        ],
        dtype=float,
    )
    probability = occurrence_model.predict_proba(features)[:, 1]
    conditional_amount = np.maximum(0.0, amount_model.predict(features))
    return [
        {
            "rain_probability": float(probability[index]),
            "value": float(
                conditional_amount[index] if probability[index] >= 0.5 else 0.0
            ),
        }
        for index in range(len(forecast_dates))
    ]


def _prophet_forecast(
    records: list[list[Any]],
    target_name: str,
    origin: date,
    forecast_dates: list[date],
) -> list[float]:
    target_map = _series_maps(records)[0][target_name]
    train = pd.DataFrame(
        [
            {"ds": pd.Timestamp(observed_date), "y": value}
            for observed_date, value in target_map.items()
            if observed_date <= origin
        ]
    )
    if len(train) < 365:
        raise ValueError(f"Only {len(train)} training observations available for {target_name}.")
    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        interval_width=0.95,
    )
    model.fit(train)
    predicted = model.predict(pd.DataFrame({"ds": pd.to_datetime(forecast_dates)}))
    values = predicted["yhat"].to_numpy(dtype=float)
    if target_name == "rainfall":
        values = np.maximum(0.0, values)
    return values.tolist()


def _rainfall_baselines(
    maps: dict[str, dict[date, float]],
    origin: date,
    forecast_dates: list[date],
) -> dict[str, list[dict[str, float]]]:
    history = maps["rainfall"]
    last_value = history.get(origin)
    recent_dates = sorted(day for day in history if origin - timedelta(days=6) <= day <= origin)
    recent = [history[day] for day in recent_dates]
    persistence_probability = float(np.mean(np.asarray(recent) > 0)) if recent else 0.0
    persistence_amount = float(np.mean(recent)) if recent else 0.0
    seasonal = {}
    for forecast_date in forecast_dates:
        season_values = [
            value
            for observed_date, value in history.items()
            if observed_date <= origin
            and observed_date.month == forecast_date.month
            and observed_date.day == forecast_date.day
            and (origin - observed_date).days <= 5 * 366
        ]
        seasonal[forecast_date] = season_values
    results = {
        "no_rain": [
            {"value": 0.0, "rain_probability": 0.0}
            for _ in forecast_dates
        ],
        "seasonal": [],
        "persistence": [],
    }
    for forecast_date in forecast_dates:
        samples = seasonal[forecast_date]
        results["seasonal"].append(
            {
                "value": float(np.mean(samples)) if samples else 0.0,
                "rain_probability": (
                    float(np.mean(np.asarray(samples) > 0)) if samples else 0.0
                ),
            }
        )
        results["persistence"].append(
            {
                "value": max(0.0, last_value if last_value is not None else persistence_amount),
                "rain_probability": (
                    1.0 if last_value is not None and last_value > 0 else persistence_probability
                ),
            }
        )
    return results


def _score_rows(rows: list[dict[str, Any]], prediction_key: str) -> dict[str, Any]:
    actual = [row["actual"] for row in rows]
    predicted = [row[prediction_key] for row in rows]
    result = _metric_values(actual, predicted)
    if actual:
        heavy = [
            (observed, estimate)
            for observed, estimate in zip(actual, predicted)
            if observed >= 10.0
        ]
        result["heavy_rain"] = {
            **_metric_values(
                [pair[0] for pair in heavy],
                [pair[1] for pair in heavy],
            ),
            "threshold_mm": 10.0,
        }
    if rows and "rain_probability" in rows[0]:
        result["occurrence"] = _occurrence_values(
            actual,
            [row["rain_probability"] for row in rows],
            float(rows[0].get("threshold", 0.5)),
        )
    return result


def _model_description(target_name: str) -> str:
    if target_name == "rainfall":
        return (
            "Rainfall forecasts are selected from chronologically validated two-stage "
            "occurrence/amount models and simple baselines. Predictors use calendar features "
            "and only rainfall and temperature observations available by the forecast origin. "
            "No archived future weather forecast inputs are available in the bundled history."
        )
    return (
        "Facebook Prophet additive time-series model with yearly and weekly seasonality. "
        "This temperature model was retained as the baseline; no alternative temperature model "
        "was deployed."
    )


def _period_origins(latest_date: date, days: int) -> list[date]:
    fold_count = days // RAINFALL_FOLD_STRIDE
    first_origin = latest_date - timedelta(days=days + 1)
    return [
        first_origin + timedelta(days=RAINFALL_FOLD_STRIDE * index)
        for index in range(fold_count)
    ]


def _collect_rainfall_period(
    records: list[list[Any]],
    maps: dict[str, dict[date, float]],
    indexed_series: dict[str, tuple[list[date], np.ndarray, dict[date, float]]],
    target_dates: list[date],
    origins: list[date],
    period_name: str,
    include_alternatives: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actual_map = maps["rainfall"]
    rows: list[dict[str, Any]] = []
    fold_ranges = []
    for origin in origins:
        forecast_dates = [
            origin + timedelta(days=lead) for lead in range(1, HORIZON_DAYS + 1)
        ]
        actual_dates = [day for day in forecast_dates if day in actual_map]
        if len(actual_dates) < 10:
            continue
        classifiers, regressors = _fit_rainfall_candidates(
            target_dates,
            maps,
            indexed_series,
            origin,
            include_alternatives,
        )
        probabilities, amounts = _predict_rainfall_candidates(
            classifiers, regressors, indexed_series, origin, forecast_dates
        )
        prophet_values = _prophet_forecast(
            records, "rainfall", origin, forecast_dates
        )
        baseline_values = _rainfall_baselines(maps, origin, forecast_dates)
        for index, forecast_date in enumerate(forecast_dates):
            if forecast_date not in actual_map:
                continue
            rows.append(
                {
                    "date": forecast_date.isoformat(),
                    "actual": actual_map[forecast_date],
                    "lead": index + 1,
                    "probabilities": {
                        name: float(values[index])
                        for name, values in probabilities.items()
                    },
                    "amounts": {
                        name: float(values[index])
                        for name, values in amounts.items()
                    },
                    "prophet": float(prophet_values[index]),
                    "baselines": {
                        name: {
                            "value": float(values[index]["value"]),
                            "rain_probability": float(
                                values[index]["rain_probability"]
                            ),
                        }
                        for name, values in baseline_values.items()
                    },
                }
            )
        fold_ranges.append(
            {
                "origin": origin.isoformat(),
                "start": forecast_dates[0].isoformat(),
                "end": forecast_dates[-1].isoformat(),
                "n": len(actual_dates),
            }
        )
        print(f"  {period_name} origin {origin.isoformat()} complete")
    return rows, fold_ranges


def _calibrated_period_rows(
    rows: list[dict[str, Any]],
    classifier_name: str,
    method: str,
    calibrator: Any,
) -> list[dict[str, Any]]:
    raw = [row["probabilities"][classifier_name] for row in rows]
    calibrated = _apply_probability_calibrator(raw, calibrator, method)
    return [
        {**row, "rain_probability": float(calibrated[index])}
        for index, row in enumerate(rows)
    ]


def _score_rainfall_predictions(
    rows: list[dict[str, Any]],
    predictions: list[float],
    probabilities: list[float],
    threshold: float,
) -> dict[str, Any]:
    scored = [
        {
            **row,
            "prediction": float(predictions[index]),
            "rain_probability": float(probabilities[index]),
            "threshold": threshold,
        }
        for index, row in enumerate(rows)
    ]
    return _score_rows(scored, "prediction")


def _select_rainfall_pipeline(
    validation_rows: list[dict[str, Any]],
    include_alternatives: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = [row["actual"] for row in validation_rows]
    classifier_candidates = (
        CLASSIFIER_CANDIDATES if include_alternatives else ("xgboost",)
    )
    amount_candidates = AMOUNT_CANDIDATES if include_alternatives else ("xgboost",)
    calibration_methods = (
        ("raw", "sigmoid", "isotonic") if include_alternatives else ("raw",)
    )
    threshold_candidates = RAIN_THRESHOLDS if include_alternatives else (0.5,)
    strategy_candidates = (
        ("thresholded", "probability_weighted")
        if include_alternatives
        else ("thresholded",)
    )
    calibrators: dict[str, dict[str, Any]] = {}
    calibrated_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for classifier_name in classifier_candidates:
        calibrators[classifier_name] = {}
        for method in calibration_methods:
            calibrator = _fit_probability_calibrator(
                [row["probabilities"][classifier_name] for row in validation_rows],
                actual,
                method,
            )
            calibrators[classifier_name][method] = calibrator
            calibrated_rows[(classifier_name, method)] = _calibrated_period_rows(
                validation_rows, classifier_name, method, calibrator
            )

    candidates = []
    for classifier_name in classifier_candidates:
        for amount_name in amount_candidates:
            for method in calibration_methods:
                rows = calibrated_rows[(classifier_name, method)]
                probabilities = [row["rain_probability"] for row in rows]
                conditional_amounts = [row["amounts"][amount_name] for row in rows]
                for strategy in strategy_candidates:
                    if strategy == "probability_weighted":
                        predictions = [
                            probabilities[index] * conditional_amounts[index]
                            for index in range(len(rows))
                        ]
                        threshold = 0.5
                        candidates.append(
                            {
                                "classifier": classifier_name,
                                "amount_model": amount_name,
                                "calibration": method,
                                "strategy": strategy,
                                "threshold": None,
                                "metrics": _score_rainfall_predictions(
                                    rows, predictions, probabilities, threshold
                                ),
                            }
                        )
                        continue
                    for threshold in threshold_candidates:
                        hard = [
                            conditional_amounts[index]
                            if probabilities[index] >= threshold
                            else 0.0
                            for index in range(len(rows))
                        ]
                        candidates.append(
                            {
                                "classifier": classifier_name,
                                "amount_model": amount_name,
                                "calibration": method,
                                "strategy": strategy,
                                "threshold": threshold,
                                "metrics": _score_rainfall_predictions(
                                    rows, hard, probabilities, threshold
                                ),
                            }
                        )

    selected = min(
        candidates,
        key=lambda candidate: (
            candidate["metrics"]["mae"],
            candidate["metrics"]["rmse"],
        ),
    )
    config = {
        key: selected[key]
        for key in ("classifier", "amount_model", "calibration", "strategy", "threshold")
    }
    benchmark_predictions = {
        "xgboost_original": [
            row["amounts"]["xgboost"]
            if row["probabilities"]["xgboost"] >= 0.5
            else 0.0
            for row in validation_rows
        ],
        "no_rain": [0.0 for _ in validation_rows],
        **{
            baseline: [row["baselines"][baseline]["value"] for row in validation_rows]
            for baseline in ("seasonal", "persistence")
        },
        "prophet": [max(0.0, row["prophet"]) for row in validation_rows],
    }
    benchmark_metrics = {
        name: _metric_values(actual, predictions)
        for name, predictions in benchmark_predictions.items()
    }
    validation_predictions = _apply_rainfall_pipeline(
        validation_rows,
        config,
        calibrators[config["classifier"]][config["calibration"]],
    )
    validation_metrics = _score_rainfall_predictions(
        validation_rows,
        validation_predictions,
        [
            row["rain_probability"]
            for row in _calibrated_period_rows(
                validation_rows,
                config["classifier"],
                config["calibration"],
                calibrators[config["classifier"]][config["calibration"]],
            )
        ],
        _occurrence_threshold(config),
    )
    return config, calibrators, {
        "selected": selected,
        "candidates": candidates,
        "benchmarks": benchmark_metrics,
        "benchmark_predictions": benchmark_predictions,
        "metrics": validation_metrics,
        "tuning": {
            "classifier_candidates": list(classifier_candidates),
            "amount_candidates": list(amount_candidates),
            "calibration_candidates": list(calibration_methods),
            "threshold_candidates": list(threshold_candidates),
            "strategy_candidates": list(strategy_candidates),
            "validation_candidate_count": len(candidates),
        },
    }


def _occurrence_threshold(config: dict[str, Any]) -> float:
    return (
        float(config["threshold"])
        if config["strategy"] == "thresholded"
        else 0.5
    )


def _apply_rainfall_pipeline(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    calibrator: Any,
) -> list[float]:
    calibrated = _calibrated_period_rows(
        rows,
        config["classifier"],
        config["calibration"],
        calibrator,
    )
    predictions = []
    for row in calibrated:
        probability = row["rain_probability"]
        amount = row["amounts"][config["amount_model"]]
        if config["strategy"] == "probability_weighted":
            value = probability * amount
        else:
            value = amount if probability >= float(config["threshold"]) else 0.0
        predictions.append(max(0.0, float(value)))
    return predictions


def _attach_predictions(
    rows: list[dict[str, Any]],
    predictions: list[float],
    probabilities: list[float],
    threshold: float,
) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "prediction": float(predictions[index]),
            "rain_probability": float(probabilities[index]),
            "threshold": threshold,
        }
        for index, row in enumerate(rows)
    ]


def _lead_metrics(
    rows: list[dict[str, Any]],
    baselines: bool = False,
) -> dict[str, Any]:
    result = {}
    for name, (first, last) in LEAD_WINDOWS.items():
        selected = [row for row in rows if first <= row["lead"] <= last]
        result[name] = {
            "lead_start": first,
            "lead_end": last,
            **_score_rows(selected, "prediction"),
        }
        if baselines:
            result[name]["baselines"] = {
                baseline: _score_rows(
                    [
                        {
                            **row,
                            "candidate_prediction": row["baselines"][baseline]["value"],
                        }
                        for row in selected
                    ],
                    "candidate_prediction",
                )
                for baseline in ("no_rain", "seasonal", "persistence")
            }
    return result


def _validation_residual_margins(
    validation_rows: list[dict[str, Any]],
    predictions: list[float],
) -> dict[str, float]:
    margins = {}
    for name, (first, last) in LEAD_WINDOWS.items():
        errors = [
            abs(row["actual"] - predictions[index])
            for index, row in enumerate(validation_rows)
            if first <= row["lead"] <= last
        ]
        if errors:
            rank = min(len(errors) - 1, int(np.ceil(0.9 * (len(errors) + 1))) - 1)
            margins[name] = float(np.sort(errors)[rank])
        else:
            margins[name] = 0.0
    return margins


def _select_validation_winner(validation_candidates: dict[str, float]) -> str:
    tie_priority = {
        "no_rain": 0,
        "seasonal": 1,
        "persistence": 2,
        "prophet": 3,
        "xgboost_original": 4,
        "tuned_pipeline": 5,
    }
    winner = min(
        validation_candidates,
        key=lambda candidate: (
            validation_candidates[candidate],
            tie_priority[candidate],
        ),
    )
    return "tuned_pipeline" if winner == "xgboost_original" else winner


def _evaluate_rainfall(
    records: list[list[Any]],
    station_name: str,
    station_code: str,
) -> dict[str, Any]:
    maps, _ = _series_maps(records)
    indexed_series = _index_series(maps)
    target_dates = sorted(maps["rainfall"])
    latest_date = target_dates[-1]
    evaluation_days = (
        RAINFALL_EVALUATION_DAYS
        if station_code == DETAILED_EVALUATION_STATION
        else STANDARD_RAINFALL_EVALUATION_DAYS
    )
    validation_days = (
        RAINFALL_VALIDATION_DAYS
        if station_code == DETAILED_EVALUATION_STATION
        else STANDARD_RAINFALL_EVALUATION_DAYS
    )
    test_origins = _period_origins(latest_date, evaluation_days)
    validation_origins = _period_origins(
        test_origins[0], validation_days
    )
    validation_rows, validation_folds = _collect_rainfall_period(
        records,
        maps,
        indexed_series,
        target_dates,
        validation_origins,
        "validation",
        station_code == DETAILED_EVALUATION_STATION,
    )
    if len(validation_folds) < min(2, len(validation_origins)):
        raise ValueError(f"Too few chronological validation folds for {station_name}.")
    include_alternatives = station_code == DETAILED_EVALUATION_STATION
    config, calibrators, selection = _select_rainfall_pipeline(
        validation_rows, include_alternatives
    )
    selected_validation_predictions = _apply_rainfall_pipeline(
        validation_rows,
        config,
        calibrators[config["classifier"]][config["calibration"]],
    )
    validation_residual_margins = _validation_residual_margins(
        validation_rows, selected_validation_predictions
    )

    test_rows, test_folds = _collect_rainfall_period(
        records,
        maps,
        indexed_series,
        target_dates,
        test_origins,
        "final_test",
        include_alternatives,
    )
    selected_test_predictions = _apply_rainfall_pipeline(
        test_rows,
        config,
        calibrators[config["classifier"]][config["calibration"]],
    )
    selected_test_probabilities = [
        row["rain_probability"]
        for row in _calibrated_period_rows(
            test_rows,
            config["classifier"],
            config["calibration"],
            calibrators[config["classifier"]][config["calibration"]],
        )
    ]
    selected_test_rows = _attach_predictions(
        test_rows,
        selected_test_predictions,
        selected_test_probabilities,
        _occurrence_threshold(config),
    )
    for row in selected_test_rows:
        lead_name = next(
            name
            for name, (first, last) in LEAD_WINDOWS.items()
            if first <= row["lead"] <= last
        )
        margin = validation_residual_margins[lead_name]
        row["lower"] = max(0.0, row["prediction"] - margin)
        row["upper"] = row["prediction"] + margin
        row["interval_level"] = 0.9
    selected_metrics = _score_rows(selected_test_rows, "prediction")
    selected_metrics["occurrence"] = _occurrence_values(
        [row["actual"] for row in selected_test_rows],
        [row["rain_probability"] for row in selected_test_rows],
        _occurrence_threshold(config),
    )
    selected_metrics["interval"] = {
        "nominal_coverage": 0.9,
        "validation_calibration": "90th-percentile rolling-validation absolute error, grouped by lead window",
        "by_lead": {
            name: {
                "n": sum(
                    first <= row["lead"] <= last for row in selected_test_rows
                ),
                "validation_margin_mm": validation_residual_margins[name],
                "empirical_coverage": float(
                    np.mean(
                        [
                            row["lower"] <= row["actual"] <= row["upper"]
                            for row in selected_test_rows
                            if first <= row["lead"] <= last
                        ]
                    )
                ),
            }
            for name, (first, last) in LEAD_WINDOWS.items()
        },
    }
    by_lead = _lead_metrics(selected_test_rows, baselines=True)

    test_candidate_rows: dict[str, list[dict[str, Any]]] = {
        "tuned_pipeline": selected_test_rows,
        "xgboost_original": [],
        "prophet": [],
        "no_rain": [],
        "seasonal": [],
        "persistence": [],
    }
    original_predictions = [
        row["amounts"]["xgboost"]
        if row["probabilities"]["xgboost"] >= 0.5
        else 0.0
        for row in test_rows
    ]
    original_probabilities = [row["probabilities"]["xgboost"] for row in test_rows]
    test_candidate_rows["xgboost_original"] = _attach_predictions(
        test_rows, original_predictions, original_probabilities, 0.5
    )
    for baseline in ("no_rain", "seasonal", "persistence", "prophet"):
        predictions = [
            (
                max(0.0, row["prophet"])
                if baseline == "prophet"
                else row["baselines"][baseline]["value"]
            )
            for row in test_rows
        ]
        probabilities = [
            (
                float(row["prophet"] > 0)
                if baseline == "prophet"
                else row["baselines"][baseline]["rain_probability"]
            )
            for row in test_rows
        ]
        test_candidate_rows[baseline] = _attach_predictions(
            test_rows, predictions, probabilities, 0.5
        )
    candidate_metrics = {
        name: _score_rows(rows, "prediction")
        for name, rows in test_candidate_rows.items()
    }
    selected_validation_mae = selection["selected"]["metrics"]["mae"]
    baseline_validation_maes = selection["benchmarks"]
    validation_candidates = {
        **{
            name: metrics["mae"]
            for name, metrics in baseline_validation_maes.items()
        },
        "tuned_pipeline": selected_validation_mae,
    }
    selected_validation_winner = _select_validation_winner(validation_candidates)
    chosen_model = (
        "tuned_pipeline"
        if selected_validation_winner == "tuned_pipeline"
        else selected_validation_winner
    )
    # Selection is frozen on the validation period; the final test period is report-only.
    if chosen_model != "tuned_pipeline":
        selected_test_rows = test_candidate_rows[chosen_model]
        validation_predictions = selection["benchmark_predictions"][chosen_model]
        validation_residual_margins = _validation_residual_margins(
            validation_rows, validation_predictions
        )
        for row in selected_test_rows:
            lead_name = next(
                name
                for name, (first, last) in LEAD_WINDOWS.items()
                if first <= row["lead"] <= last
            )
            margin = validation_residual_margins[lead_name]
            row["lower"] = max(0.0, row["prediction"] - margin)
            row["upper"] = row["prediction"] + margin
            row["interval_level"] = 0.9
        selected_metrics = _score_rows(selected_test_rows, "prediction")
        selected_metrics["occurrence"] = _occurrence_values(
            [row["actual"] for row in selected_test_rows],
            [row["rain_probability"] for row in selected_test_rows],
        )
        selected_test_predictions = [row["prediction"] for row in selected_test_rows]
        selected_test_probabilities = [
            row["rain_probability"] for row in selected_test_rows
        ]
        selected_metrics["interval"] = {
            "nominal_coverage": 0.9,
            "validation_calibration": "90th-percentile rolling-validation absolute error, grouped by lead window",
            "by_lead": {
                name: {
                    "n": sum(first <= row["lead"] <= last for row in selected_test_rows),
                    "validation_margin_mm": validation_residual_margins[name],
                    "empirical_coverage": float(
                        np.mean(
                            [
                                row["lower"] <= row["actual"] <= row["upper"]
                                for row in selected_test_rows
                                if first <= row["lead"] <= last
                            ]
                        )
                    ),
                }
                for name, (first, last) in LEAD_WINDOWS.items()
            },
        }
        by_lead = _lead_metrics(selected_test_rows, baselines=True)

    selected_name = {
        "tuned_pipeline": (
            f"Two-stage {config['classifier']} + {config['amount_model']} "
            f"({config['calibration']}, {config['strategy']}"
            + (
                f", threshold {config['threshold']:.2f}"
                if config["threshold"] is not None
                else ""
            )
            + ")"
        ),
        "xgboost_original": "Original two-stage XGBoost",
        "prophet": "Prophet",
        "no_rain": "No-rain baseline",
        "seasonal": "Seasonal baseline",
        "persistence": "Persistence baseline",
    }[chosen_model]
    if chosen_model == "tuned_pipeline":
        final_classifiers, final_regressors = _fit_rainfall_candidates(
            target_dates, maps, indexed_series, latest_date, include_alternatives
        )
        forecast_dates = [
            latest_date + timedelta(days=lead)
            for lead in range(1, HORIZON_DAYS + 1)
        ]
        final_probabilities, final_amounts = _predict_rainfall_candidates(
            final_classifiers,
            final_regressors,
            indexed_series,
            latest_date,
            forecast_dates,
        )
        final_calibrated = _apply_probability_calibrator(
            final_probabilities[config["classifier"]],
            calibrators[config["classifier"]][config["calibration"]],
            config["calibration"],
        )
        final_values = []
        for index, probability in enumerate(final_calibrated):
            amount = final_amounts[config["amount_model"]][index]
            value = (
                probability * amount
                if config["strategy"] == "probability_weighted"
                else amount if probability >= float(config["threshold"]) else 0.0
            )
            final_values.append(max(0.0, float(value)))
        final_rain_probabilities = final_calibrated.tolist()
        description = (
            f"Two-stage {config['classifier']} occurrence classifier and "
            f"{config['amount_model']} conditional rainfall model; "
            f"{config['calibration']} probability calibration; "
            f"{config['strategy']} amount forecast. Threshold: "
            f"{config['threshold'] if config['threshold'] is not None else 'not applied'}. "
            "Selected only on a separate chronological validation period."
        )
    else:
        forecast_dates = [
            latest_date + timedelta(days=lead)
            for lead in range(1, HORIZON_DAYS + 1)
        ]
        if chosen_model == "prophet":
            final_values = _prophet_forecast(
                records, "rainfall", latest_date, forecast_dates
            )
            final_rain_probabilities = [
                float(value > 0) for value in final_values
            ]
        else:
            final_baselines = _rainfall_baselines(
                maps, latest_date, forecast_dates
            )
            final_values = [
                value["value"] for value in final_baselines[chosen_model]
            ]
            final_rain_probabilities = [
                value["rain_probability"]
                for value in final_baselines[chosen_model]
            ]
        description = (
            f"{selected_name}, selected on the chronological validation period; "
            "the final test period was not used for model selection."
        )
    forecast = []
    for index, forecast_date in enumerate(forecast_dates):
        lead = index + 1
        lead_name = next(
            name
            for name, (first, last) in LEAD_WINDOWS.items()
            if first <= lead <= last
        )
        margin = validation_residual_margins[lead_name]
        value = max(0.0, float(final_values[index]))
        forecast.append(
            {
                "date": forecast_date.isoformat(),
                "value": value,
                "lower": max(0.0, value - margin),
                "upper": value + margin,
                "rain_probability": float(final_rain_probabilities[index]),
            }
        )
    selection_summary = {
        "selected": chosen_model,
        "selected_name": selected_name,
        "evaluation_profile": (
            "extended_changi"
            if station_code == DETAILED_EVALUATION_STATION
            else "standard_station"
        ),
        "tuned_candidate": config,
        "best_tuned_candidates": [
            {
                **{
                    key: candidate[key]
                    for key in (
                        "classifier",
                        "amount_model",
                        "calibration",
                        "strategy",
                        "threshold",
                    )
                },
                "metrics": candidate["metrics"],
            }
            for candidate in sorted(
                selection["candidates"],
                key=lambda candidate: (
                    candidate["metrics"]["mae"],
                    candidate["metrics"]["rmse"],
                ),
            )[:5]
        ],
        "validation_period": {
            "start": min(row["date"] for row in validation_rows),
            "end": max(row["date"] for row in validation_rows),
            "origins": len(validation_folds),
            "target_pairs": len(validation_rows),
            "unique_dates": len({row["date"] for row in validation_rows}),
        },
        "test_period": {
            "start": min(row["date"] for row in selected_test_rows),
            "end": max(row["date"] for row in selected_test_rows),
            "origins": len(test_folds),
            "target_pairs": len(selected_test_rows),
            "unique_dates": len({row["date"] for row in selected_test_rows}),
        },
        "validation_metrics": (
            selection["metrics"]
            if chosen_model == "tuned_pipeline"
            else selection["benchmarks"][chosen_model]
        ),
        "validation_benchmarks": selection["benchmarks"],
        "selection_rule": (
            "Choose the lowest validation MAE among calibrated two-stage pipelines, "
            "Prophet, no-rain, seasonal, and persistence forecasts. The final chronological "
            "test period is not used to choose a model, calibrator, or threshold."
        ),
    }
    comparison = {
        "selected": chosen_model,
        "selection_rule": selection_summary["selection_rule"],
        "candidates": candidate_metrics,
        "tuning": selection["tuning"],
    }
    return {
        "model": chosen_model,
        "model_name": selected_name,
        "model_description": description,
        "data_end": latest_date.isoformat(),
        "forecast": forecast,
        "backtest": [
            {
                "date": row["date"],
                "actual": row["actual"],
                "predicted": row["prediction"],
                "rain_probability": row["rain_probability"],
                "lead": row["lead"],
            }
            for row in selected_test_rows
        ],
        "validation": {
            **selected_metrics,
            "folds": test_folds,
            "by_lead": by_lead,
            "candidate_by_lead": {
                name: {
                    candidate: _score_rows(
                        [row for row in rows if first <= row["lead"] <= last],
                        "prediction",
                    )
                    for candidate, rows in test_candidate_rows.items()
                }
                for name, (first, last) in LEAD_WINDOWS.items()
            },
            "baselines": {
                name: candidate_metrics[name]
                for name in ("no_rain", "seasonal", "persistence")
            },
            "comparison": comparison,
            "model_selection": selection_summary,
            "mape": None,
        },
    }


def _evaluate_target(
    records: list[list[Any]],
    target_name: str,
    station_name: str,
    station_code: str,
) -> dict[str, Any]:
    if target_name == "rainfall":
        return _evaluate_rainfall(records, station_name, station_code)
    maps, _ = _series_maps(records)
    indexed_series = _index_series(maps)
    target_map = maps[target_name]
    target_dates = sorted(target_map)
    latest_target_date = target_dates[-1]
    backtest_folds = (
        BACKTEST_FOLDS
        if station_code == DETAILED_EVALUATION_STATION
        else STANDARD_TEMPERATURE_BACKTEST_FOLDS
    )
    origin_dates = [
        latest_target_date - timedelta(days=HORIZON_DAYS * offset)
        for offset in reversed(range(1, backtest_folds + 1))
    ]
    backtest_rows = []
    fold_ranges = []
    candidate_forecasts = {"prophet": [], "xgboost": [], "no_rain": [], "seasonal": [], "persistence": []}
    for origin in origin_dates:
        forecast_dates = [origin + timedelta(days=lead) for lead in range(1, HORIZON_DAYS + 1)]
        actual_dates = [day for day in forecast_dates if day in target_map]
        if len(actual_dates) < 10:
            continue
        prophet_values = _prophet_forecast(records, target_name, origin, forecast_dates)
        fold_rows = []
        if target_name == "rainfall":
            rainfall_model = _fit_rainfall_model(
                target_dates, maps, indexed_series, origin
            )
            xgb_values = _predict_rainfall(
                rainfall_model, indexed_series, origin, forecast_dates
            )
            baseline_values = _rainfall_baselines(maps, origin, forecast_dates)
        else:
            xgb_values = []
            baseline_values = {}
        for index, forecast_date in enumerate(forecast_dates):
            if forecast_date not in target_map:
                continue
            actual = target_map[forecast_date]
            row = {
                "date": forecast_date.isoformat(),
                "actual": actual,
                "lead": index + 1,
                "prophet": prophet_values[index],
                "prophet_rain_probability": 1.0 if prophet_values[index] > 0 else 0.0,
            }
            candidate_forecasts["prophet"].append(row)
            if target_name == "rainfall":
                xgb = xgb_values[index]
                row["xgboost"] = xgb["value"]
                row["rain_probability"] = xgb["rain_probability"]
                candidate_forecasts["xgboost"].append(row.copy())
                for baseline_name, baseline in baseline_values.items():
                    candidate_forecasts[baseline_name].append(
                        {
                            **row,
                            "value": baseline[index]["value"],
                            "rain_probability": baseline[index]["rain_probability"],
                        }
                    )
            fold_rows.append(row)
        fold_ranges.append(
            {
                "origin": origin.isoformat(),
                "start": forecast_dates[0].isoformat(),
                "end": forecast_dates[-1].isoformat(),
                "n": len(fold_rows),
            }
        )
        backtest_rows = fold_rows

    if not fold_ranges:
        raise ValueError(f"No valid 14-day backtest folds for {station_name} ({target_name}).")

    candidate_by_lead = {}
    if target_name == "rainfall":
        candidate_rows = {
            "xgboost": [
                {**row, "prediction": row["xgboost"]}
                for row in candidate_forecasts["xgboost"]
            ],
            "prophet": [
                {
                    **row,
                    "prediction": row["prophet"],
                    "rain_probability": row["prophet_rain_probability"],
                }
                for row in candidate_forecasts["prophet"]
            ],
            **{
                name: [
                    {**row, "prediction": row["value"]}
                    for row in candidate_forecasts[name]
                ]
                for name in ("no_rain", "seasonal", "persistence")
            },
        }
        candidate_metrics = {
            name: _score_rows(candidate_rows[name], "prediction")
            for name in ("xgboost", "prophet", "no_rain", "seasonal", "persistence")
        }
        xgb_improves = (
            candidate_metrics["xgboost"]["mae"] < candidate_metrics["prophet"]["mae"]
        )
        selected_model = "xgboost" if xgb_improves else "prophet"
        selected_rows = []
        for row in candidate_forecasts[selected_model]:
            selected_rows.append(
                {
                    **row,
                    "prediction": (
                        row["xgboost"] if selected_model == "xgboost" else row["prophet"]
                    ),
                    "rain_probability": (
                        row["rain_probability"]
                        if selected_model == "xgboost"
                        else row["prophet_rain_probability"]
                    ),
                }
            )
        selected_metrics = _score_rows(selected_rows, "prediction")
        if selected_model == "xgboost":
            selected_metrics["occurrence"] = candidate_metrics["xgboost"]["occurrence"]
        else:
            selected_metrics["occurrence"] = _occurrence_values(
                [row["actual"] for row in selected_rows],
                [row["prophet_rain_probability"] for row in selected_rows],
            )
        by_lead = {}
        for name, (first, last) in LEAD_WINDOWS.items():
            selected_window = [
                row for row in selected_rows if first <= row["lead"] <= last
            ]
            window_metrics = _score_rows(selected_window, "prediction")
            if selected_model == "xgboost":
                window_metrics["occurrence"] = _occurrence_values(
                    [row["actual"] for row in selected_window],
                    [row["rain_probability"] for row in selected_window],
                )
            by_lead[name] = {
                "lead_start": first,
                "lead_end": last,
                **window_metrics,
                "baselines": {
                    baseline: _score_rows(
                        [
                            row for row in candidate_forecasts[baseline]
                            if first <= row["lead"] <= last
                        ],
                        "value",
                    )
                    for baseline in ("no_rain", "seasonal", "persistence")
                },
            }
            candidate_by_lead[name] = {
                "lead_start": first,
                "lead_end": last,
                **{
                    candidate: _score_rows(
                        [
                            row for row in candidate_rows[candidate]
                            if first <= row["lead"] <= last
                        ],
                        "prediction",
                    )
                    for candidate in ("xgboost", "prophet")
                },
            }
        selected_name = "Two-stage XGBoost" if selected_model == "xgboost" else "Prophet"
        validation_baselines = {
            name: candidate_metrics[name]
            for name in ("no_rain", "seasonal", "persistence")
        }
        comparison = {
            "selected": selected_model,
            "selection_rule": (
                "Select two-stage XGBoost when its aggregate rolling MAE is lower than Prophet, "
                "the primary rainfall point-error objective; report RMSE and occurrence trade-offs "
                "alongside MAE."
            ),
            "candidates": candidate_metrics,
        }
    else:
        selected_model = "prophet"
        selected_name = "Prophet"
        selected_rows = [
            {**row, "prediction": row["prophet"]}
            for row in candidate_forecasts["prophet"]
        ]
        selected_metrics = _score_rows(selected_rows, "prediction")
        by_lead = {
            name: {
                "lead_start": first,
                "lead_end": last,
                **_score_rows(
                    [row for row in selected_rows if first <= row["lead"] <= last],
                    "prediction",
                ),
            }
            for name, (first, last) in LEAD_WINDOWS.items()
        }
        validation_baselines = {}
        comparison = {
            "selected": "prophet",
            "selection_rule": "Retain Prophet; temperature alternatives were not evaluated.",
            "candidates": {"prophet": selected_metrics},
        }

    forecast_dates = [latest_target_date + timedelta(days=lead) for lead in range(1, HORIZON_DAYS + 1)]
    if target_name == "rainfall":
        if selected_model == "xgboost":
            final_rainfall_model = _fit_rainfall_model(
                target_dates, maps, indexed_series, latest_target_date
            )
            final_values = _predict_rainfall(
                final_rainfall_model, indexed_series, latest_target_date, forecast_dates
            )
            residuals = [
                abs(row["actual"] - row["xgboost"])
                for row in candidate_forecasts["xgboost"]
            ]
        else:
            prophet_values = _prophet_forecast(
                records, target_name, latest_target_date, forecast_dates
            )
            final_values = [
                {"value": value, "rain_probability": 1.0 if value > 0 else 0.0}
                for value in prophet_values
            ]
            residuals = [
                abs(row["actual"] - row["prophet"])
                for row in candidate_forecasts["prophet"]
            ]
        margin = float(np.quantile(residuals, 0.9)) if residuals else 0.0
        forecast = [
            {
                "date": forecast_dates[index].isoformat(),
                "value": max(0.0, final_values[index]["value"]),
                "lower": max(0.0, final_values[index]["value"] - margin),
                "upper": max(0.0, final_values[index]["value"] + margin),
                "rain_probability": final_values[index]["rain_probability"],
            }
            for index in range(HORIZON_DAYS)
        ]
        model_description = (
            _model_description(target_name)
            if selected_model == "xgboost"
            else "Prophet additive trend and yearly/weekly seasonality; rainfall estimates are clipped at zero."
        )
    else:
        forecast_frame = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            interval_width=0.95,
        )
        training_values = pd.DataFrame(
            [
                {"ds": pd.Timestamp(observed_date), "y": value}
                for observed_date, value in target_map.items()
            ]
        )
        forecast_frame.fit(training_values)
        future = forecast_frame.predict(pd.DataFrame({"ds": pd.to_datetime(forecast_dates)}))
        forecast = [
            {
                "date": forecast_dates[index].isoformat(),
                "value": float(future.iloc[index]["yhat"]),
                "lower": float(future.iloc[index]["yhat_lower"]),
                "upper": float(future.iloc[index]["yhat_upper"]),
            }
            for index in range(HORIZON_DAYS)
        ]
        model_description = _model_description(target_name)

    return {
        "model": selected_model,
        "model_name": selected_name,
        "model_description": model_description,
        "data_end": latest_target_date.isoformat(),
        "forecast": forecast,
        "backtest": [
            {
                "date": row["date"],
                "actual": row["actual"],
                "predicted": (
                    row["xgboost"] if selected_model == "xgboost" else row["prophet"]
                ),
            }
            for row in backtest_rows
        ],
        "validation": {
            **selected_metrics,
            "folds": fold_ranges,
            "by_lead": by_lead,
            "candidate_by_lead": candidate_by_lead,
            "baselines": validation_baselines,
            "comparison": comparison,
            "mape": None,
        },
    }


def _write_rainfall_diagnostics(
    target_result: dict[str, Any],
    output_path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = target_result["backtest"]
    actual = np.asarray([row["actual"] for row in rows], dtype=float)
    predicted = np.asarray([row["predicted"] for row in rows], dtype=float)
    probabilities = np.asarray(
        [row["rain_probability"] for row in rows], dtype=float
    )
    leads = np.asarray([row["lead"] for row in rows], dtype=int)
    residuals = predicted - actual
    threshold = float(target_result["validation"]["occurrence"]["threshold"])
    observed_wet = actual > 0
    predicted_wet = probabilities >= threshold
    confusion = np.asarray(
        [
            [np.sum(~observed_wet & ~predicted_wet), np.sum(~observed_wet & predicted_wet)],
            [np.sum(observed_wet & ~predicted_wet), np.sum(observed_wet & predicted_wet)],
        ]
    )
    calibration_actual = []
    calibration_predicted = []
    bin_edges = np.linspace(0.0, 1.0, 11)
    for index, (lower, upper) in enumerate(zip(bin_edges[:-1], bin_edges[1:])):
        in_bin = (probabilities >= lower) & (
            (probabilities <= upper) if index == 9 else (probabilities < upper)
        )
        if np.any(in_bin):
            calibration_actual.append(float(np.mean(observed_wet[in_bin])))
            calibration_predicted.append(float(np.mean(probabilities[in_bin])))

    figure, axes = plt.subplots(2, 3, figsize=(15, 9))
    axes[0, 0].scatter(actual, predicted, alpha=0.45, s=16)
    limit = max(float(np.max(actual)), float(np.max(predicted)), 1.0)
    axes[0, 0].plot([0, limit], [0, limit], color="black", linestyle="--")
    axes[0, 0].set(xlabel="Actual rainfall (mm)", ylabel="Predicted rainfall (mm)", title="Actual vs predicted")

    axes[0, 1].hist(residuals, bins=30, color="#4388a8", alpha=0.85)
    axes[0, 1].axvline(0, color="black", linestyle="--")
    axes[0, 1].set(xlabel="Prediction − actual (mm)", ylabel="Count", title="Prediction residuals")

    axes[0, 2].scatter(leads, residuals, alpha=0.45, s=16)
    axes[0, 2].axhline(0, color="black", linestyle="--")
    axes[0, 2].set(xlabel="Forecast lead (days)", ylabel="Prediction − actual (mm)", title="Residuals by lead")

    image = axes[1, 0].imshow(confusion, cmap="Blues")
    axes[1, 0].set(
        xticks=[0, 1],
        yticks=[0, 1],
        xticklabels=["Dry", "Wet"],
        yticklabels=["Dry", "Wet"],
        xlabel="Predicted",
        ylabel="Observed",
        title=f"Occurrence confusion matrix (threshold {threshold:.2f})",
    )
    for row_index in range(2):
        for column_index in range(2):
            axes[1, 0].text(
                column_index,
                row_index,
                str(int(confusion[row_index, column_index])),
                ha="center",
                va="center",
            )
    figure.colorbar(image, ax=axes[1, 0], fraction=0.046)

    axes[1, 1].plot([0, 1], [0, 1], color="black", linestyle="--")
    axes[1, 1].plot(calibration_predicted, calibration_actual, marker="o")
    axes[1, 1].set(
        xlabel="Mean forecast probability",
        ylabel="Observed wet-day frequency",
        title="Probability calibration",
        xlim=(0, 1),
        ylim=(0, 1),
    )

    axes[1, 2].hist(actual, bins=30, alpha=0.6, label="Actual", density=True)
    axes[1, 2].hist(predicted, bins=30, alpha=0.6, label="Predicted", density=True)
    axes[1, 2].set(xlabel="Rainfall (mm)", ylabel="Density", title="Rainfall distributions")
    axes[1, 2].legend()
    figure.suptitle(
        f"Changi rainfall diagnostics · untouched test {rows[0]['date']} to {rows[-1]['date']}"
    )
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=140)
    plt.close(figure)


def build_forecast_payload(
    history_path: Path = HISTORY_PATH,
    station_code: str | None = None,
) -> dict[str, Any]:
    payload = load_history(history_path)
    generated_at = datetime.now(timezone.utc).isoformat()
    station_codes = [station_code] if station_code else sorted(payload["stations"])
    stations = {}
    for code in station_codes:
        records = payload["stations"].get(code)
        if not isinstance(records, list):
            raise ValueError(f"Station {code} is missing from {history_path}.")
        targets = {}
        for name, target in TARGETS.items():
            observed_count = sum(
                isinstance(row[target["index"]], (int, float))
                and np.isfinite(row[target["index"]])
                for row in records
            )
            if observed_count < MINIMUM_OBSERVATIONS:
                continue
            target_result = _evaluate_target(
                records,
                name,
                payload["stationNames"].get(code, code),
                code,
            )
            target_result["generated_at"] = generated_at
            if code == "S24" and name == "rainfall":
                diagnostics_path = (
                    PROJECT_ROOT
                    / "data"
                    / "climate-rainfall-diagnostics-S24.png"
                )
                _write_rainfall_diagnostics(target_result, diagnostics_path)
                target_result["diagnostics_url"] = "data/climate-rainfall-diagnostics-S24.png"
            targets[name] = target_result
        stations[code] = {"name": payload["stationNames"].get(code, code), "targets": targets}
        print(f"{code}: generated {len(targets)} target forecast(s)")
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "historyUpdatedThrough": payload["updatedThrough"],
        "backtestHorizonDays": HORIZON_DAYS,
        "backtestFolds": BACKTEST_FOLDS,
        "standardTemperatureBacktestFolds": STANDARD_TEMPERATURE_BACKTEST_FOLDS,
        "stations": stations,
    }


def write_forecast_payload(
    output_path: Path = FORECAST_PATH,
    history_path: Path = HISTORY_PATH,
    station_code: str | None = None,
) -> dict[str, Any]:
    payload = build_forecast_payload(history_path=history_path, station_code=station_code)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    print(f"Wrote {output_path} with {len(payload['stations'])} station(s).")
    return payload
