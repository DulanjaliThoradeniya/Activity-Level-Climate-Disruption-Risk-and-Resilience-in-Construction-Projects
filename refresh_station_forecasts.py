"""Refresh saved station results by calling the existing forecasting implementations."""

import argparse
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "Climate Model" / "Trained model"
HISTORY = ROOT / "data" / "climate-history.json"
OUTPUT = ROOT / "data" / "climate-multi-model-forecasts.json"
MODELS = ("xgboost", "lightgbm", "lstm", "tft", "neuralprophet_new")
TARGETS = ("rainfall", "mean_temperature", "maximum_temperature", "minimum_temperature")
COLUMNS = ("date", *TARGETS)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_result(result, input_end, expected_dates):
    if not isinstance(result, dict) or result.get("data_end") != input_end:
        return False
    points = result.get("forecast")
    if not isinstance(points, list) or [point.get("date") for point in points] != expected_dates:
        return False
    return all(isinstance(point.get("value"), (float, int)) and math.isfinite(point["value"]) for point in points)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", required=True, help="Last completed input day (YYYY-MM-DD)")
    parser.add_argument("--station", action="append", help="Refresh only the specified station; may be repeated")
    parser.add_argument("--target", action="append", choices=TARGETS, help="Refresh only the specified variable; may be repeated")
    args = parser.parse_args()
    input_end = date.fromisoformat(args.through)
    dates = [(input_end + timedelta(days=offset)).isoformat() for offset in range(1, 15)]
    history = json.loads(HISTORY.read_text(encoding="utf-8"))
    unknown_stations = set(args.station or ()) - history["stations"].keys()
    if unknown_stations:
        parser.error(f"Unknown station(s): {', '.join(sorted(unknown_stations))}")
    bundle = json.loads(OUTPUT.read_text(encoding="utf-8"))
    xgb = load_module("existing_xgboost_builder", MODEL_DIR / "build_multi_model_forecasts.py")
    remaining = load_module("existing_other_builders", MODEL_DIR / "build_remaining_climate_models.py")
    failures = []

    for station, records in history["stations"].items():
        if args.station and station not in args.station:
            continue
        existing = {
            (model, target): bundle["models"].get(model, {}).get("stations", {}).get(station, {}).get("targets", {}).get(target)
            for model in MODELS for target in (args.target or TARGETS)
        }
        missing = [(model, target) for (model, target), result in existing.items()
                   if not valid_result(result, args.through, dates)]
        if not missing:
            print(f"{station}: already current", flush=True)
            continue
        daily = {row[0]: row for row in records if isinstance(row, list) and len(row) == 5}
        last = daily.get(args.through)
        records = [row for row in records if row[0] <= args.through]
        frame = pd.DataFrame(records, columns=COLUMNS)
        updates = {}
        for target in args.target or TARGETS:
            needed = [model for model in MODELS if (model, target) in missing]
            if not needed:
                continue
            value = last[COLUMNS.index(target)] if last else None
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                failures.append(f"{station} {target}: input is unavailable through {args.through}")
                print(failures[-1], flush=True)
                continue
            results = {}
            if "xgboost" in needed:
                try:
                    results["xgboost"] = xgb.predict_target(records, target)
                except Exception as error:
                    failures.append(f"{station} {target} xgboost: {error}")
            other = tuple(model for model in needed if model != "xgboost")
            if other:
                results.update(remaining.predict_target(records, target, frame, other))
            for model in needed:
                result = results.get(model)
                if not valid_result(result, args.through, dates):
                    failures.append(f"{station} {target} {model}: {results.get(model + '_error', 'invalid or missing 14-day forecast')}")
                    continue
                provenance = []
                if any(target in fields for fields in history.get("estimatedValues", {}).get(station, {}).values()):
                    provenance.append("Some inputs are Open-Meteo gridded historical forecasts, not NEA measurements; see estimatedValues in data/climate-history.json.")
                if provenance:
                    result["input_provenance"] = " ".join(provenance)
                else:
                    result.pop("input_provenance", None)
                updates[(model, target)] = result
            print(f"{station} {target}: validated {sum((model, target) in updates for model in needed)}/{len(needed)} results", flush=True)
        for (model, target), result in updates.items():
            bundle["models"][model]["stations"].setdefault(station, {
                "station_name": history.get("stationNames", {}).get(station, station), "targets": {}
            })["targets"][target] = result
        if updates:
            bundle["generatedAt"] = datetime.now(timezone.utc).isoformat()
            bundle["inputProvenance"] = "See each result's input_provenance and estimatedValues in data/climate-history.json. Stations may have different forecast dates."
            temporary = OUTPUT.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            temporary.replace(OUTPUT)
    if failures:
        print("\nForecasts not refreshed:\n" + "\n".join(failures), flush=True)
        raise SystemExit(1)
    print("All station results are current.", flush=True)


if __name__ == "__main__":
    main()
