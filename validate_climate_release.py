"""Reject incomplete or stale climate forecast releases."""

from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from climate_history_validation import HISTORY_PATH, TARGETS, load_history
from update_climate_data import RAINFALL_PROXY_STATIONS, parse_station_mapping


ROOT = Path(__file__).resolve().parent
MODELS = ("xgboost", "lightgbm", "lstm", "tft", "neuralprophet_new")


def validate_forecast(result, expected_dates, description, expected_end):
    if not isinstance(result, dict):
        raise ValueError(f"{description}: forecast is missing")
    if result.get("data_end") != expected_end:
        raise ValueError(f"{description}: input cutoff {result.get('data_end')} is not {expected_end}")
    rows = result.get("forecast")
    if not isinstance(rows, list) or [row.get("date") for row in rows] != expected_dates:
        raise ValueError(f"{description}: expected 14 consecutive forecast dates {expected_dates[0]} to {expected_dates[-1]}")
    for row in rows:
        for field in ("value", "lower", "upper"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{description}: invalid {field} on {row['date']}")
            if description.endswith("/rainfall") and value < 0:
                raise ValueError(f"{description}: negative rainfall on {row['date']}")
        if not row["lower"] <= row["value"] <= row["upper"]:
            raise ValueError(f"{description}: invalid interval on {row['date']}")


def validate_release(root=ROOT, today=None, require_current=False):
    rainfall_ends = []
    required = {station["code"] for station in parse_station_mapping().values()}
    required.update(RAINFALL_PROXY_STATIONS)
    history = load_history(root / "data" / "climate-history.json", required, minimum_rainfall=180)
    prophet_path = root / "data" / "climate-forecasts.json"
    multi_path = root / "data" / "climate-multi-model-forecasts.json"
    try:
        prophet = json.loads(prophet_path.read_text(encoding="utf-8"))
        multi = json.loads(multi_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read forecast release: {error}") from error
    today = today or datetime.now(ZoneInfo("Asia/Singapore")).date()
    dates = [(today + timedelta(days=offset)).isoformat() for offset in range(14)]
    if prophet.get("schemaVersion") != 1 or multi.get("schemaVersion") != 1:
        raise ValueError("Forecast release has an unsupported schema version")
    prophet_stations = prophet.get("stations", {})
    models = multi.get("models", {})
    for model in MODELS:
        if models.get(model, {}).get("status") != "ready" or models[model].get("errors"):
            raise ValueError(f"{model}: model is missing, incomplete, or reports errors")
    for station in sorted(required):
        records = history["stations"][station]
        for index, target in enumerate(TARGETS, 1):
            valid = [row for row in records if isinstance(row[index], (int, float)) and not isinstance(row[index], bool)]
            non_estimated = [
                row for row in valid
                if target not in history.get("estimatedValues", {}).get(station, {}).get(row[0], ())
            ]
            if len(valid) >= 425 and station in prophet_stations:
                result = prophet_stations[station].get("targets", {}).get(target)
                validate_forecast(result, dates, f"prophet/{station}/{target}", valid[-1][0])
            elif len(valid) >= 425 and station not in RAINFALL_PROXY_STATIONS:
                raise ValueError(f"prophet/{station}/{target}: station is missing")
            if len(non_estimated) >= 180:
                expected_end = non_estimated[-1][0]
                if target == "rainfall":
                    rainfall_ends.append(expected_end)
                # Each forecast starts the day after that station's latest NEA observation, so a
                # station whose reading for yesterday is incomplete keeps a valid forecast instead
                # of blocking every other station's update (the site labels earlier start dates).
                model_dates = [
                    (date.fromisoformat(expected_end) + timedelta(days=offset)).isoformat()
                    for offset in range(1, 15)
                ]
                for model in MODELS:
                    result = models[model].get("stations", {}).get(station, {}).get("targets", {}).get(target)
                    validate_forecast(result, model_dates, f"{model}/{station}/{target}", expected_end)
    yesterday = (today - timedelta(days=1)).isoformat()
    current = sum(end == yesterday for end in rainfall_ends)
    if require_current and not current:
        raise ValueError(f"No station has NEA rainfall for {yesterday}; the daily update did not add new data.")
    print(f"Validated 14-day release: {dates[0]} through {dates[-1]} "
          f"({current}/{len(rainfall_ends)} rainfall stations use NEA data through {yesterday}).")


if __name__ == "__main__":
    import sys
    validate_release(require_current="--require-current" in sys.argv[1:])
