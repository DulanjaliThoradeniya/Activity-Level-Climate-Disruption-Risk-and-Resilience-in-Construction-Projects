import argparse
import base64
import calendar
import hashlib
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import re
import threading
import time
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from prophet import Prophet
from sklearn.metrics import mean_absolute_error, mean_squared_error


TARGETS = {
    "maximum_temperature": "Maximum Temperature (°C)",
    "mean_temperature": "Mean Temperature (°C)",
    "minimum_temperature": "Minimum Temperature (°C)",
    "rainfall": "Daily Rainfall Total (mm)",
}
HISTORICAL_PAGE = "https://www.weather.gov.sg/climate-historical-daily/"
CSV_URL = "https://www.weather.gov.sg/files/dailydata/DAILYDATA_{station_code}_{year}{month:02d}.csv"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / ".climate-cache"
REPORT_DIR = PROJECT_ROOT / "forecast-results"
API_CACHE_DIR = PROJECT_ROOT / ".forecast-api-cache"
DAILY_CACHE_SECONDS = 24 * 60 * 60
MAX_HISTORY_YEARS = 30
API_CACHE_LOCK = threading.Lock()
SCHEDULER_LOCK = threading.Lock()
MULTI_MODEL_OUTPUT = PROJECT_ROOT / "data" / "climate-multi-model-forecasts.json"
SCHEDULER_STATUS = PROJECT_ROOT / "data" / "climate-backend-status.json"
SINGAPORE_TIMEZONE = timezone(timedelta(hours=8))


def load_model_payload():
    if not MULTI_MODEL_OUTPUT.exists():
        raise OSError("The saved multi-model forecast file is not available yet.")
    return json.loads(MULTI_MODEL_OUTPUT.read_text(encoding="utf-8"))


def load_backend_status():
    if not SCHEDULER_STATUS.exists():
        return {
            "status": "starting",
            "nextRunAt": None,
            "models": {},
        }
    return json.loads(SCHEDULER_STATUS.read_text(encoding="utf-8"))


def output_is_fresh(path, maximum_age_seconds=24 * 60 * 60):
    if not path.exists():
        return False
    return time.time() - path.stat().st_mtime < maximum_age_seconds


def save_backend_status(status):
    SCHEDULER_STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SCHEDULER_STATUS.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SCHEDULER_STATUS)


def rebuild_forecasts():
    with SCHEDULER_LOCK:
        script_dir = PROJECT_ROOT / "Climate Model" / "Trained model"
        history_result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "update_climate_data.py"), "--refresh-recent"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        commands = [
            ("existing_model", script_dir / "build_prophet_forecasts.py"),
            ("xgboost", script_dir / "build_multi_model_forecasts.py"),
            ("remaining_models", script_dir / "build_remaining_climate_models.py"),
        ]
        model_status = {}
        if history_result.returncode != 0:
            model_status["history"] = {
                "status": "error",
                "startedAt": datetime.now(timezone.utc).isoformat(),
                "completedAt": datetime.now(timezone.utc).isoformat(),
                "error": history_result.stderr.strip()[-2000:],
            }
        for model_id, script in commands:
            started_at = datetime.now(timezone.utc).isoformat()
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=3600,
            )
            model_status[model_id] = {
                "status": "ready" if result.returncode == 0 else "error",
                "startedAt": started_at,
                "completedAt": datetime.now(timezone.utc).isoformat(),
                "error": result.stderr.strip()[-2000:] if result.returncode else "",
            }

        next_run = next_singapore_refresh(datetime.now(SINGAPORE_TIMEZONE))
        save_backend_status({
            "status": "ready" if any(item["status"] == "ready" for item in model_status.values()) else "error",
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "nextRunAt": next_run.isoformat(),
            "intervalHours": 24,
            "models": model_status,
        })


def next_singapore_refresh(now_sg):
    next_run = now_sg.replace(hour=7, minute=0, second=0, microsecond=0)
    if now_sg >= next_run:
        next_run += timedelta(days=1)
    return next_run


def forecast_scheduler():
    while True:
        now_sg = datetime.now(SINGAPORE_TIMEZONE)
        next_run = next_singapore_refresh(now_sg)

        needs_build = not MULTI_MODEL_OUTPUT.exists()
        if MULTI_MODEL_OUTPUT.exists():
            saved_sg = datetime.fromtimestamp(MULTI_MODEL_OUTPUT.stat().st_mtime, tz=SINGAPORE_TIMEZONE)
            current_scheduled_run = now_sg.replace(hour=7, minute=0, second=0, microsecond=0)
            if now_sg < current_scheduled_run:
                current_scheduled_run -= timedelta(days=1)
            needs_build = saved_sg < current_scheduled_run

        if needs_build:
            rebuild_forecasts()

        if MULTI_MODEL_OUTPUT.exists():
            saved_time = datetime.fromtimestamp(MULTI_MODEL_OUTPUT.stat().st_mtime, tz=timezone.utc)
            save_backend_status({
                "status": "ready",
                "completedAt": saved_time.isoformat(),
                "nextRunAt": next_run.isoformat(),
                "intervalHours": 24,
                "models": {
                    "existing_model": {"status": "ready"},
                    "xgboost": {"status": "ready"},
                    "remaining_models": {"status": "ready"},
                },
            })
        time.sleep(60)


def api_cache_key(area, target, years, forecast_days):
    value = f"{area}|{target}|{years}|{forecast_days}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def load_daily_api_cache(area, target, years, forecast_days):
    path = API_CACHE_DIR / f"{api_cache_key(area, target, years, forecast_days)}.json"
    if not path.exists() or time.time() - path.stat().st_mtime >= DAILY_CACHE_SECONDS:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("expiresAt", 0) > time.time():
            return payload["response"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return None


def save_daily_api_cache(area, target, years, forecast_days, response):
    API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = API_CACHE_DIR / f"{api_cache_key(area, target, years, forecast_days)}.json"
    payload = {
        "expiresAt": time.time() + DAILY_CACHE_SECONDS,
        "cachedAt": datetime.now(timezone.utc).isoformat(),
        "response": response,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_area_stations():
    source = (PROJECT_ROOT / "weather.js").read_text(encoding="utf-8")
    match = re.search(
        r"window\.neaHistoricalStationByArea\s*=\s*(\{[\s\S]*?\})\s*;",
        source,
    )
    if not match:
        raise RuntimeError("Could not find the NEA area-to-station mapping in weather.js.")
    return json.loads(match.group(1))


def shift_month(month, offset):
    index = month.year * 12 + month.month - 1 + offset
    year, zero_based_month = divmod(index, 12)
    return date(year, zero_based_month + 1, 1)


def decode_nea_csv(content):
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def fetch_month(station_code, month):
    url = CSV_URL.format(
        station_code=station_code,
        year=month.year,
        month=month.month,
    )
    cache_path = CACHE_DIR / f"{station_code}_{month.year}{month.month:02d}.csv"
    latest_published_month = shift_month(date.today().replace(day=1), -1)
    cache_is_fresh = (
        cache_path.exists()
        and time.time() - cache_path.stat().st_mtime < DAILY_CACHE_SECONDS
    )
    if cache_path.exists() and (month != latest_published_month or cache_is_fresh):
        return cache_path.read_text(encoding="utf-8-sig"), url, False

    request = Request(url, headers={"User-Agent": "C-RESIST climate research prototype"})
    try:
        with urlopen(request, timeout=30) as response:
            content = decode_nea_csv(response.read())
    except HTTPError as error:
        if error.code == 404:
            return None, url, True
        raise RuntimeError(f"NEA returned HTTP {error.code} for {url}") from error
    except URLError as error:
        raise RuntimeError(f"Could not retrieve NEA historical data from {url}: {error}") from error

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(content, encoding="utf-8")
    time.sleep(0.1)
    return content, url, False


def download_history(station_code, years):
    cached_history, cache_path = load_csv_history_cache(station_code)
    if cached_history is not None:
        return cached_history, 0, []

    latest_completed_month = shift_month(date.today().replace(day=1), -1)
    requested_years = MAX_HISTORY_YEARS if years is None else years
    months = [shift_month(latest_completed_month, -offset) for offset in range(requested_years * 12)]
    monthly_frames = []
    unavailable_months = 0
    source_urls = []
    consecutive_unavailable = 0

    # Iterate backwards and stop after a full year without files, so all available
    # history is retrieved without issuing needless requests beyond NEA's archive.
    for month in reversed(months):
        content, url, unavailable = fetch_month(station_code, month)
        if unavailable:
            unavailable_months += 1
            consecutive_unavailable += 1
            if consecutive_unavailable >= 12:
                break
            continue
        consecutive_unavailable = 0
        source_urls.append(url)
        frame = pd.read_csv(io.StringIO(content), encoding="utf-8-sig")
        frame.columns = [column.strip() for column in frame.columns]
        frame["Date"] = pd.to_datetime(
            {"year": frame["Year"], "month": frame["Month"], "day": frame["Day"]},
            errors="coerce",
        )
        monthly_frames.append(frame)

    if not monthly_frames:
        raise RuntimeError(
            f"No NEA historical monthly CSV records were available for station {station_code}."
        )

    history = pd.concat(monthly_frames, ignore_index=True)
    history = history.dropna(subset=["Date"]).sort_values("Date")
    history = history.drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
    for column in TARGETS.values():
        if column in history:
            history[column] = pd.to_numeric(history[column], errors="coerce")

    _, cache_path = load_csv_history_cache(station_code)
    save_csv_history_cache(station_code, history, cache_path)
    return history, unavailable_months, source_urls


def load_csv_history_cache(station_code):
    cache_path = CACHE_DIR / f"{station_code}_last_24_hours.csv"
    if not cache_path.exists() or time.time() - cache_path.stat().st_mtime >= DAILY_CACHE_SECONDS:
        return None, cache_path
    try:
        history = pd.read_csv(cache_path)
        history.columns = [column.strip() for column in history.columns]
        history["Date"] = pd.to_datetime(history["Date"], errors="coerce")
        history = history.dropna(subset=["Date"])
        for column in TARGETS.values():
            if column in history:
                history[column] = pd.to_numeric(history[column], errors="coerce")
        if history.empty:
            return None, cache_path
        return history.reset_index(drop=True), cache_path
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return None, cache_path


def save_csv_history_cache(station_code, history, cache_path):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    serializable = history.copy()
    serializable["Date"] = serializable["Date"].dt.strftime("%Y-%m-%d")
    temporary_path = cache_path.with_suffix(".csv.tmp")
    serializable.to_csv(temporary_path, index=False)
    temporary_path.replace(cache_path)


def chart_data_uri(figure):
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
    plt.close(figure)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def make_forecast_chart(data, forecast, test, target_label, area, station):
    figure, axis = plt.subplots(figsize=(13, 5.5))
    axis.plot(data["ds"], data["y"], color="#26352d", linewidth=1.1, label="Observed NEA history")
    axis.plot(forecast["ds"], forecast["yhat"], color="#2677c9", linewidth=1.8, label="Prophet estimate")
    axis.fill_between(
        forecast["ds"],
        forecast["yhat_lower"],
        forecast["yhat_upper"],
        color="#73b5e8",
        alpha=0.23,
        label="95% prediction interval",
    )
    axis.axvspan(
        test["ds"].min(),
        test["ds"].max(),
        color="#e5a53c",
        alpha=0.16,
        label="Held-out evaluation period",
    )
    axis.axvline(data["ds"].max(), color="#738378", linestyle="--", linewidth=1)
    axis.set_title(f"{target_label} forecast for {area}\nNEA station: {station}")
    axis.set_xlabel("Date")
    axis.set_ylabel(target_label)
    axis.grid(True, alpha=0.22)
    axis.legend(loc="best", frameon=False)
    figure.autofmt_xdate()
    return chart_data_uri(figure)


def make_monthly_temperature_chart(history):
    maximum = history.groupby(history["Date"].dt.month)["Maximum Temperature (°C)"].mean()
    minimum = history.groupby(history["Date"].dt.month)["Minimum Temperature (°C)"].mean()
    figure, axis = plt.subplots(figsize=(9, 4.5))
    months = range(1, 13)
    axis.plot(months, maximum.reindex(months), marker="o", color="#d76547", label="Mean daily maximum")
    axis.plot(months, minimum.reindex(months), marker="o", color="#2677c9", label="Mean daily minimum")
    axis.set_xticks(list(months), calendar.month_abbr[1:])
    axis.set_ylabel("Temperature (°C)")
    axis.set_title("Historical temperature by calendar month")
    axis.grid(True, alpha=0.22)
    axis.legend(frameon=False)
    return chart_data_uri(figure)


def make_temperature_distribution_chart(history):
    figure, axis = plt.subplots(figsize=(9, 4.5))
    values = history["Maximum Temperature (°C)"].dropna()
    axis.hist(values, bins=24, color="#4c9770", edgecolor="white")
    axis.axvline(values.mean(), color="#bf633f", linestyle="--", linewidth=1.5, label=f"Mean: {values.mean():.1f} °C")
    axis.set_xlabel("Daily maximum temperature (°C)")
    axis.set_ylabel("Number of recorded days")
    axis.set_title("Distribution of NEA daily maximum temperatures")
    axis.grid(axis="y", alpha=0.22)
    axis.legend(frameon=False)
    return chart_data_uri(figure)


def make_monthly_rainfall_chart(history):
    monthly_rainfall = history.groupby(history["Date"].dt.month)["Daily Rainfall Total (mm)"].mean()
    months = range(1, 13)
    figure, axis = plt.subplots(figsize=(9, 4.5))
    axis.bar(list(months), monthly_rainfall.reindex(months), color="#4c8bb8")
    axis.set_xticks(list(months), calendar.month_abbr[1:])
    axis.set_ylabel("Mean daily rainfall (mm)")
    axis.set_title("Historical daily rainfall by calendar month")
    axis.grid(True, axis="y", alpha=0.22)
    return chart_data_uri(figure)


def make_component_chart(model, forecast, target_label):
    figure = model.plot_components(forecast)
    figure.suptitle(f"Prophet model components - {target_label}", fontsize=13, y=1.02)
    figure.tight_layout()
    return chart_data_uri(figure)


class _RainfallProphet:
    """Add a log1p rainfall transform while preserving Prophet's public methods."""

    def __init__(self, prophet_kwargs):
        self.model = Prophet(**prophet_kwargs)
        self.holidays = self.model.holidays

    def fit(self, frame, *args, **kwargs):
        transformed = frame.copy()
        transformed["y"] = np.log1p(np.maximum(transformed["y"].to_numpy(dtype=float), 0.0))
        self.model.fit(transformed, *args, **kwargs)

        # Calibrate an occurrence threshold and a positive-rain bias from the
        # training data only. This prevents the continuous Prophet output from
        # spreading small positive rainfall values over every dry day.
        fitted = self.model.predict(frame[["ds"]])
        fitted_rain = np.clip(np.expm1(fitted["yhat"]), 0.0, None)
        observed = np.maximum(frame["y"].to_numpy(dtype=float), 0.0)
        observed_rainy = observed[observed > 0]
        dates = pd.to_datetime(frame["ds"])
        self.rain_dates = dates.dt.dayofyear.to_numpy(dtype=int)
        self.rain_observed = observed
        self.rain_positive_values = observed_rainy
        if observed_rainy.size and fitted_rain.size:
            fitted_positive = fitted_rain[fitted_rain > 0]
            if fitted_positive.size:
                target_rain_fraction = float(np.mean(observed > 0))
                target_quantile = float(np.clip(1.0 - target_rain_fraction, 0.05, 0.95))
                self.rain_threshold = float(np.quantile(fitted_positive, target_quantile))
                predicted_rain = fitted_rain >= self.rain_threshold
                calibration = fitted_rain[predicted_rain]
                if calibration.size:
                    scale_candidates = np.linspace(0.50, 1.50, 21)
                    best_score = float("inf")
                    best_scale = 1.0
                    actual_rain = observed_rainy
                    for scale in scale_candidates:
                        aligned = np.clip(calibration, 0.0, None) * scale
                        if aligned.size > actual_rain.size:
                            aligned = np.sort(aligned)[-actual_rain.size:]
                        elif aligned.size < actual_rain.size:
                            aligned = np.pad(aligned, (0, actual_rain.size - aligned.size),
                                             constant_values=np.median(calibration))
                        actual_sorted = np.sort(actual_rain)
                        predicted_sorted = np.sort(aligned)
                        paired_mape = np.mean(np.abs((actual_sorted - predicted_sorted) / np.maximum(actual_sorted, 0.1))) * 100.0
                        paired_mae = mean_absolute_error(actual_sorted, predicted_sorted)
                        score = 0.75 * min(paired_mape, 500.0) + 0.25 * paired_mae
                        if score < best_score:
                            best_score = score
                            best_scale = float(scale)
                    self.rain_scale = best_scale
                else:
                    self.rain_scale = 1.0
            else:
                self.rain_threshold = 0.0
                self.rain_scale = 1.0
        else:
            self.rain_threshold = 0.0
            self.rain_scale = 1.0
        return self

    def predict(self, future, *args, **kwargs):
        forecast = self.model.predict(future, *args, **kwargs)
        for column in ("yhat", "yhat_lower", "yhat_upper"):
            values = np.clip(np.expm1(forecast[column]), 0.0, None)
            forecast[column] = values

        if hasattr(self, "rain_observed"):
            dates = pd.to_datetime(future["ds"]).dt.dayofyear.to_numpy(dtype=int)
            for index, day_of_year in enumerate(dates):
                distance = np.abs(self.rain_dates - day_of_year)
                distance = np.minimum(distance, 366 - distance)
                nearby = distance <= 31
                if not np.any(nearby):
                    continue
                local = self.rain_observed[nearby]
                rainy = local[local > 0]
                wet_fraction = float(np.mean(local > 0))
                prophet_amount = float(forecast.at[index, "yhat"])
                if wet_fraction >= 0.08 and rainy.size >= 5:
                    climatology_amount = float(np.median(rainy))
                    seasonal_departure = prophet_amount - float(np.median(self.rain_positive_values))
                    blended = 0.65 * climatology_amount + 0.35 * prophet_amount
                    if prophet_amount > getattr(self, "rain_threshold", 0.0) or seasonal_departure > 0:
                        forecast.at[index, "yhat"] = max(0.1, blended * getattr(self, "rain_scale", 1.0))
                    else:
                        forecast.at[index, "yhat"] = 0.0
                elif prophet_amount >= getattr(self, "rain_threshold", 0.0):
                    forecast.at[index, "yhat"] = prophet_amount * getattr(self, "rain_scale", 1.0)
                else:
                    forecast.at[index, "yhat"] = 0.0

            lower = np.clip(forecast["yhat_lower"].to_numpy(dtype=float) * getattr(self, "rain_scale", 1.0), 0.0, None)
            upper = np.clip(forecast["yhat_upper"].to_numpy(dtype=float) * getattr(self, "rain_scale", 1.0), 0.0, None)
            forecast["yhat_lower"] = np.minimum(lower, forecast["yhat"])
            forecast["yhat_upper"] = np.maximum(upper, forecast["yhat"])
        return forecast

    def __getattr__(self, name):
        return getattr(self.model, name)


def _make_tuned_prophet(target, changepoint_prior_scale=0.05,
                         seasonality_prior_scale=8.0,
                         holidays_prior_scale=5.0,
                         yearly_fourier=10,
                         weekly_fourier=5,
                         interval_width=0.95):
    rainfall = target == "rainfall"
    kwargs = dict(
        yearly_seasonality=yearly_fourier,
        weekly_seasonality=weekly_fourier,
        daily_seasonality=False,
        changepoint_prior_scale=changepoint_prior_scale,
        seasonality_prior_scale=seasonality_prior_scale,
        holidays_prior_scale=holidays_prior_scale,
        seasonality_mode="additive",
        interval_width=interval_width,
    )
    return _RainfallProphet(kwargs) if rainfall else Prophet(**kwargs)


def new_prophet_model(target=None):
    if target == "rainfall":
        return _make_tuned_prophet(target, changepoint_prior_scale=0.03,
                                   seasonality_prior_scale=10.0,
                                   holidays_prior_scale=5.0,
                                   yearly_fourier=10,
                                   weekly_fourier=3,
                                   interval_width=0.95)
    return _make_tuned_prophet(target, changepoint_prior_scale=0.05,
                               seasonality_prior_scale=8.0,
                               holidays_prior_scale=5.0,
                               yearly_fourier=12,
                               weekly_fourier=5,
                               interval_width=0.95)


def select_prophet_model(data, target):
    """Select from Prophet configurations using only data preceding the final test split."""
    test_days = min(60, max(30, len(data) // 10))
    selection_data = data.iloc[:-test_days].reset_index(drop=True)
    if len(selection_data) < 730:
        return new_prophet_model(target), test_days

    if target == "rainfall":
        # Use one intentionally conservative rainfall configuration across all
        # stations. Hyperparameter differences can otherwise make sparse rainfall
        # signals station-specific and unstable.
        candidates = (
            (0.03, 10.0, 5.0, 10, 3, 0.95),
            (0.02, 12.0, 5.0, 12, 3, 0.95),
        )
    else:
        candidates = (
            (0.02, 8.0, 5.0, 8, 3, 0.95),
            (0.03, 10.0, 5.0, 10, 3, 0.95),
            (0.05, 8.0, 10.0, 12, 5, 0.95),
            (0.08, 12.0, 5.0, 15, 5, 0.95),
        )

    # Two rolling validation folds make the choice more robust than one split.
    validation_sizes = sorted({min(45, max(30, len(selection_data) // 10)),
                               min(60, max(45, len(selection_data) // 6))})
    validation_mae = []
    for cps, seasonality_scale, holidays_scale, yearly_order, weekly_order, interval_width in candidates:
        fold_scores = []
        for validation_days in validation_sizes:
            inner_train = selection_data.iloc[:-validation_days]
            inner_test = selection_data.iloc[-validation_days:]
            if len(inner_train) < 365:
                continue
            try:
                model = _make_tuned_prophet(
                    target,
                    changepoint_prior_scale=cps,
                    seasonality_prior_scale=seasonality_scale,
                    holidays_prior_scale=holidays_scale,
                    yearly_fourier=yearly_order,
                    weekly_fourier=weekly_order,
                    interval_width=interval_width,
                )
                model.fit(inner_train)
                periods = (inner_test["ds"].max() - inner_train["ds"].max()).days
                prediction = model.predict(model.make_future_dataframe(periods=periods))
                evaluation = inner_test.merge(prediction[["ds", "yhat"]], on="ds", how="left")
                evaluation = evaluation.dropna(subset=["yhat"])
                mae = mean_absolute_error(evaluation["y"], evaluation["yhat"])
                if target == "rainfall":
                    non_zero = evaluation["y"].to_numpy(dtype=float) > 0.0
                    if np.any(non_zero):
                        actual = evaluation.loc[non_zero, "y"].to_numpy(dtype=float)
                        predicted = evaluation.loc[non_zero, "yhat"].to_numpy(dtype=float)
                        mape = np.mean(np.abs((actual - predicted) / actual)) * 100.0
                        # MAE is the primary criterion; bounded MAPE prevents selecting
                        # a model that lowers millimetre error by producing extreme percentages.
                        score = mae + 0.05 * min(mape, 500.0)
                    else:
                        score = mae
                else:
                    score = mae
                fold_scores.append(float(score))
            except Exception:
                continue
        if fold_scores:
            validation_mae.append((float(np.mean(fold_scores)),
                                   (cps, seasonality_scale, holidays_scale, yearly_order,
                                    weekly_order, interval_width)))

    best_parameters = None
    if validation_mae:
        best_parameters = min(validation_mae, key=lambda item: item[0])[1]
    if best_parameters is None:
        return new_prophet_model(target), test_days

    return _make_tuned_prophet(
        target,
        changepoint_prior_scale=best_parameters[0],
        seasonality_prior_scale=best_parameters[1],
        holidays_prior_scale=best_parameters[2],
        yearly_fourier=best_parameters[3],
        weekly_fourier=best_parameters[4],
        interval_width=best_parameters[5],
    ), test_days


def make_report_html(area, station, station_code, target_label, data, metrics, test_days, forecast_days, charts, forecast):
    escaped_area = html.escape(area)
    escaped_station = html.escape(station)
    escaped_target = html.escape(target_label)
    history_start = data["ds"].min().strftime("%Y-%m-%d")
    history_end = data["ds"].max().strftime("%Y-%m-%d")
    future_values = forecast.loc[forecast["ds"] > data["ds"].max(), "yhat"]
    forecast_values = future_values.tail(forecast_days)
    maximum_daily = float(forecast_values.max()) if not forecast_values.empty else 0.0
    minimum_daily = float(forecast_values.min()) if not forecast_values.empty else 0.0
    is_rainfall = "rain" in target_label.lower()
    is_maximum_temperature = "maximum temperature" in target_label.lower()
    is_minimum_temperature = "minimum temperature" in target_label.lower()
    is_mean_temperature = "mean temperature" in target_label.lower()
    is_temperature = is_maximum_temperature or is_minimum_temperature or is_mean_temperature
    maximum_label = "Maximum rainfall over 14 forecast days" if is_rainfall else "Maximum forecast temperature" if is_temperature else "Highest estimate"
    minimum_label = "Minimum rainfall over 14 forecast days" if is_rainfall else "Minimum forecast temperature" if is_temperature else "Lowest estimate"
    forecast_unit = "mm" if is_rainfall else "deg C"
    mape_text = "Unavailable" if metrics.get("mape") is None else f"{metrics['mape']:.2f}%"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NEA climate model - {escaped_area}</title>
  <style>
    :root{{font-family:Arial,sans-serif;color:#22342a;background:#f4f7f4}}
    *{{box-sizing:border-box}}
    body{{margin:0;padding:18px;background:#f4f7f4}}
    h1{{font-size:22px;margin:0 0 5px}}
    h2{{font-size:17px;margin:22px 0 7px}}
    p{{line-height:1.55;color:#5b6a60}}
    .meta,.metric-row,.tab-row{{display:flex;gap:10px;flex-wrap:wrap}}
    .meta span,.metric,.tab{{background:#fff;border:1px solid #dce6de;border-radius:8px;padding:9px 12px}}
    .metric strong{{display:block;font-size:18px;color:#285f46;margin-top:3px}}
    .tab-row{{margin:18px 0 12px;background:transparent;border:0;padding:0}}
    .tab{{cursor:pointer;color:#476454;font-weight:700}}
    .tab[aria-selected="true"]{{background:#285f46;color:#fff;border-color:#285f46}}
    section[hidden]{{display:none}}
    figure{{margin:14px 0 20px;background:#fff;border:1px solid #dce6de;border-radius:10px;padding:12px}}
    figure img{{display:block;width:100%;height:auto}}
    figcaption{{color:#607067;font-size:13px;line-height:1.5;padding:8px 3px 0}}
    .note{{padding:12px 14px;border-left:3px solid #d99b39;background:#fff8e9}}
    .source a{{color:#28765d}}
    @media(max-width:600px){{body{{padding:11px}}h1{{font-size:19px}}.metric{{flex:1 1 40%}}}}
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">NEA HISTORICAL OBSERVATIONS · EXPLORATORY MODEL</p>
    <h1>{escaped_area} climate forecast</h1>
    <p>Model station: <strong>{escaped_station} ({station_code})</strong> · Target: {escaped_target} · History: {history_start} to {history_end}</p>
    <div class="metric-row">
      <div class="metric">Forecast horizon<strong>{forecast_days} days</strong></div>
      <div class="metric">Evaluation window<strong>{test_days} days</strong></div>
      <div class="metric">{maximum_label}<strong>{maximum_daily:.2f} {forecast_unit}</strong></div>
      <div class="metric">{minimum_label}<strong>{minimum_daily:.2f} {forecast_unit}</strong></div>
    </div>
  </header>
  <nav class="tab-row" role="tablist" aria-label="Climate report sections">
    <button class="tab" type="button" role="tab" aria-selected="true" aria-controls="forecast-panel" data-panel="forecast-panel">Forecast</button>
    <button class="tab" type="button" role="tab" aria-selected="false" aria-controls="eda-panel" data-panel="eda-panel">Exploratory data analysis</button>
    <button class="tab" type="button" role="tab" aria-selected="false" aria-controls="components-panel" data-panel="components-panel">Model components</button>
  </nav>
  <main>
    <section id="forecast-panel" role="tabpanel">
      <p>The blue line is the Prophet model estimate, the shaded band is its 95% prediction interval, and the orange span marks the held-out dates used for evaluation. Black points/line show NEA historical observations.</p>
      <figure><img src="{charts['forecast']}" alt="Observed NEA climate history and Prophet forecast with prediction interval"><figcaption>Historical observations and a {forecast_days}-day model projection for {escaped_area}.</figcaption></figure>
      <p class="note">This is an exploratory statistical model, not an official NEA forecast, warning, or project-site measurement. The selected project area uses the mapped representative station shown above.</p>
      <h2>Validation error results</h2>
      <div class="metric-row">
        <div class="metric">Test-period MAE<strong>{metrics['mae']:.2f}</strong></div>
        <div class="metric">Test-period RMSE<strong>{metrics['rmse']:.2f}</strong></div>
        <div class="metric">Test-period MAPE<strong>{mape_text}</strong></div>
      </div>
      <p>Validation metrics compare the model with the final {test_days} historical observations that were withheld from training. MAE is mean absolute error and RMSE penalizes larger errors more strongly. These are retrospective test scores, not guarantees of future accuracy.</p>
    </section>
    <section id="eda-panel" role="tabpanel" hidden>
      <h2>Historical temperature seasonality</h2>
      <p>Mean daily maximum and minimum temperatures are averaged by calendar month across the downloaded record. This illustrates recurring seasonal patterns; it does not establish a climate trend by itself.</p>
      <figure><img src="{charts['monthly_temperature']}" alt="Average daily maximum and minimum temperature by month"><figcaption>Monthly averages from the NEA station record.</figcaption></figure>
      <h2>Maximum temperature distribution</h2>
      <p>The histogram shows how often each daily maximum-temperature range occurred. The dashed line is the overall mean; rare values should be checked against the source record before interpretation.</p>
      <figure><img src="{charts['temperature_distribution']}" alt="Distribution of daily maximum temperatures"><figcaption>Recorded daily maximum temperatures, not model predictions.</figcaption></figure>
      <h2>Rainfall seasonality</h2>
      <p>Bars show mean daily rainfall for each calendar month, including days with zero rainfall. Monthly averages help describe the historical record but do not predict a specific rain event.</p>
      <figure><img src="{charts['monthly_rainfall']}" alt="Mean daily rainfall by calendar month"><figcaption>Daily rainfall record aggregated by calendar month.</figcaption></figure>
    </section>
    <section id="components-panel" role="tabpanel" hidden>
      <p>Prophet decomposes the fitted signal into trend and seasonal components. Components describe the fitted model; they are not independent causal explanations.</p>
      <figure><img src="{charts['components']}" alt="Prophet forecast trend and seasonality components"><figcaption>Model components for {escaped_target} at {escaped_station}.</figcaption></figure>
    </section>
  </main>
  <p class="source">Source: <a href="{HISTORICAL_PAGE}" target="_blank" rel="noopener">Meteorological Service Singapore / NEA Historical Daily Records</a>. Generated from NEA station CSV records. MAE and RMSE are evaluated on the final {test_days} historical days held out from model fitting.</p>
  <script>
    document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => {{
      document.querySelectorAll('.tab').forEach(tab => tab.setAttribute('aria-selected', String(tab === button)));
      document.querySelectorAll('[role="tabpanel"]').forEach(panel => panel.hidden = panel.id !== button.dataset.panel);
    }}));
    parent.postMessage({{type: 'nea-climate-report-ready'}}, '*');
  </script>
</body>
</html>"""


def generate_report(area, target, years, forecast_days):
    area_stations = load_area_stations()
    if area not in area_stations:
        raise ValueError(f"Unknown project area: {area}")
    if target not in TARGETS:
        raise ValueError(f"Unknown forecast variable: {target}")
    if years is not None and years < 2:
        raise ValueError("At least two years of NEA records are required for yearly seasonality.")
    if forecast_days < 1:
        raise ValueError("The forecast horizon must be at least one day.")

    station = area_stations[area]
    history, unavailable_months, source_urls = download_history(station["code"], years)
    if history.empty:
        raise RuntimeError(
            f"No NEA historical monthly records were found for {station['name']} ({station['code']})."
        )
    expected_months = (MAX_HISTORY_YEARS if years is None else years) * 12
    if unavailable_months == expected_months:
        raise RuntimeError(f"No NEA historical monthly records were found for station {station['code']}.")

    target_column = TARGETS[target]
    if target_column not in history:
        raise RuntimeError(f"The NEA station CSV does not contain the target column: {target_column}")
    data = history[["Date", target_column]].rename(columns={"Date": "ds", target_column: "y"})
    data = data.dropna().sort_values("ds").drop_duplicates("ds")
    # Use only short gaps: complete calendar dates prevent missing observations
    # from weakening the weekly and yearly seasonal components.
    data = data.set_index("ds").asfreq("D")
    data["y"] = data["y"].interpolate(limit=7, limit_direction="both")
    data = data.dropna().reset_index()
    if len(data) < 425:
        raise RuntimeError(
            f"Only {len(data)} valid observations are available for {station['name']}; "
            "at least 425 are required for yearly seasonality and held-out evaluation."
        )

    test_days = min(60, max(30, len(data) // 10))
    train = data.iloc[:-test_days].copy()
    test = data.iloc[-test_days:].copy()
    if len(train) < 365:
        raise RuntimeError(
            f"Only {len(train)} training observations remain after the test split; "
            "at least 365 are required for yearly seasonality."
        )

    target_label = target_column.replace(" (°C)", " (deg C)")
    model, test_days = select_prophet_model(data, target)
    train = data.iloc[:-test_days].copy()
    test = data.iloc[-test_days:].copy()
    model.fit(train)
    heldout_calendar_days = (data["ds"].max() - train["ds"].max()).days
    future = model.make_future_dataframe(periods=heldout_calendar_days + forecast_days)
    forecast = model.predict(future)
    evaluation = test.merge(
        forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]],
        on="ds",
        how="left",
    )
    y_true = evaluation["y"].to_numpy()
    y_pred = evaluation["yhat"].to_numpy()
    non_zero = y_true != 0
    metrics = {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mape": (
            float(np.mean(np.abs((y_true[non_zero] - y_pred[non_zero]) / y_true[non_zero])) * 100)
            if np.any(non_zero)
            else None
        ),
        "mape_sample_count": int(np.count_nonzero(non_zero)),
    }

    charts = {
        "forecast": make_forecast_chart(data, forecast, test, target_label, area, station["name"]),
        "monthly_temperature": make_monthly_temperature_chart(history),
        "temperature_distribution": make_temperature_distribution_chart(history),
        "monthly_rainfall": make_monthly_rainfall_chart(history),
        "components": make_component_chart(model, forecast, target_label),
    }
    return make_report_html(
        area,
        station["name"],
        station["code"],
        target_label,
        data,
        metrics,
        test_days,
        forecast_days,
        charts,
        forecast,
    ), {
        "station": station["name"],
        "observations": len(data),
        "history_start": data["ds"].min().strftime("%Y-%m-%d"),
        "history_end": data["ds"].max().strftime("%Y-%m-%d"),
        "metrics": metrics,
        "unavailable_months": unavailable_months,
        "source_months": len(source_urls),
    }


class ForecastRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def _add_api_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        if urlsplit(self.path).path not in {"/api/forecast", "/api/models/status", "/api/model-forecast"}:
            self.send_error(404)
            return
        self.send_response(204)
        self._add_api_headers()
        self.end_headers()

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/api/models/status":
            status_payload = load_backend_status()
            self._send_json(status_payload)
            return
        if request_path == "/api/model-forecast":
            query = parse_qs(urlsplit(self.path).query)
            station = query.get("station", [""])[0]
            target = query.get("target", ["maximum_temperature"])[0]
            model = query.get("model", ["xgboost"])[0]
            try:
                payload = load_model_payload()
                result = payload.get("models", {}).get(model, {}).get("stations", {}).get(station, {}).get("targets", {}).get(target)
                if not result:
                    raise ValueError(f"No saved {model} forecast is available for station {station} and target {target}.")
                self._send_json({"model": model, "station": station, "target": target, "result": result})
            except Exception as error:
                self._send_json({"error": str(error)}, status=404 if isinstance(error, ValueError) else 503)
            return
        if request_path != "/api/forecast":
            super().do_GET()
            return

        query = parse_qs(urlsplit(self.path).query)
        area = query.get("location", [""])[0]
        target = query.get("target", ["maximum_temperature"])[0]
        try:
            station = load_area_stations().get(area)
            if not station:
                raise ValueError(f"Unknown forecast location: {area}")
            payload = json.loads((PROJECT_ROOT / "data" / "climate-forecasts.json").read_text(encoding="utf-8"))
            forecast = payload.get("stations", {}).get(station["code"], {}).get("targets", {}).get(target)
            if not forecast:
                raise ValueError(f"No saved forecast is available for {area} and {target}.")
            self._send_json({
                "model": "prophet",
                "station": station["code"],
                "target": target,
                "result": forecast,
            })
        except (OSError, json.JSONDecodeError, ValueError) as error:
            self._send_json({"error": str(error)}, status=404)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._add_api_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(port, open_browser=False):
    server = ThreadingHTTPServer(("127.0.0.1", port), ForecastRequestHandler)
    scheduler = threading.Thread(target=forecast_scheduler, name="c-resist-forecast-scheduler", daemon=True)
    scheduler.start()
    print(f"C-RESIST climate forecast service: http://127.0.0.1:{port}/")
    print("Keep this terminal open while using Climate Forecast in the app. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    server.serve_forever()


def main():
    area_stations = load_area_stations()
    parser = argparse.ArgumentParser(
        description="Generate an exploratory NEA climate forecast report or serve forecasts to the C-RESIST app."
    )
    parser.add_argument("--location", choices=sorted(area_stations))
    parser.add_argument("--years", type=int, default=None, help="Maximum years to retrieve; default retrieves all available NEA history")
    parser.add_argument("--forecast-days", type=int, default=14, help="Future forecast horizon in days (default: 14)")
    parser.add_argument("--target", choices=sorted(TARGETS), default="maximum_temperature")
    parser.add_argument("--serve", action="store_true", help="Run the local forecast service for the web app.")
    parser.add_argument("--open-browser", action="store_true", help="Open the app after starting the local service.")
    parser.add_argument("--port", type=int, default=8765, help="Local forecast service port (default: 8765)")
    args = parser.parse_args()
    if args.serve:
        start_server(args.port, open_browser=args.open_browser)
        return
    if not args.location:
        parser.error("provide --location or use --serve")
    if args.years is not None and args.years < 2:
        parser.error("--years must be at least 2 for the yearly seasonal model.")
    if args.forecast_days < 1:
        parser.error("--forecast-days must be at least 1.")

    report, details = generate_report(args.location, args.target, args.years, args.forecast_days)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{re.sub(r'[^a-z0-9]+', '-', args.location.lower()).strip('-')}.html"
    report_path.write_text(report, encoding="utf-8")

    print(f"Project area: {args.location}")
    print(f"NEA historical station: {details['station']}")
    print(f"Observations: {details['observations']} ({details['history_start']} to {details['history_end']})")
    print(f"Target: {TARGETS[args.target]}")
    print(f"Forecast horizon: {args.forecast_days} days")
    print(f"Test MAE: {details['metrics']['mae']:.3f}")
    print(f"Test RMSE: {details['metrics']['rmse']:.3f}")
    if details["metrics"]["mape"] is not None:
        print(f"Test MAPE: {details['metrics']['mape']:.2f}%")
    else:
        print("Test MAPE: unavailable (no non-zero observed values)")
    print(f"NEA source records downloaded: {details['source_months']} monthly file(s)")
    print(f"Report saved to: {report_path}")


if __name__ == "__main__":
    main()
