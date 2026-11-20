"""Validate the bundled station history before forecasting or publication."""

import argparse
from datetime import date
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "data" / "climate-history.json"
TARGETS = ("rainfall", "mean_temperature", "maximum_temperature", "minimum_temperature")


def load_history(path, required_stations=(), minimum_rainfall=0):
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{path}: cannot read UTF-8 history: {error}") from error
    if not content.strip():
        raise ValueError(f"{path}: history is empty")
    if any(line.startswith(("<<<<<<< ", "=======", ">>>>>>> ")) for line in content.splitlines()):
        raise ValueError(f"{path}: unresolved Git merge conflict markers")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}") from error
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError(f"{path}: expected a schemaVersion 1 history object")
    stations = payload.get("stations")
    names = payload.get("stationNames")
    if not isinstance(stations, dict) or not stations or not isinstance(names, dict):
        raise ValueError(f"{path}: missing stations or stationNames")
    missing = set(required_stations) - stations.keys()
    if missing:
        raise ValueError(f"{path}: missing required stations: {', '.join(sorted(missing))}")
    target_counts = [0, 0, 0, 0]
    for station, rows in stations.items():
        if not isinstance(station, str) or not re.fullmatch(r"S\d+", station):
            raise ValueError(f"{path}: invalid station identifier {station!r}")
        if not isinstance(names.get(station), str) or not names[station].strip():
            raise ValueError(f"{path}: missing station name for {station}")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{path}: station {station} has no historical records")
        previous = ""
        rainfall_count = 0
        valid_count = 0
        for row in rows:
            if not isinstance(row, list) or len(row) != 5:
                raise ValueError(f"{path}: station {station} requires date and four weather fields")
            day = row[0]
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise ValueError("not an ISO date")
            except ValueError as error:
                raise ValueError(f"{path}: station {station} has invalid date {day!r}") from error
            if day <= previous:
                raise ValueError(f"{path}: station {station} has duplicate or unordered date {day}")
            previous = day
            for index, value in enumerate(row[1:]):
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{path}: station {station} {day} has invalid {TARGETS[index]}")
                if index == 0 and value < 0:
                    raise ValueError(f"{path}: station {station} {day} has negative rainfall")
                target_counts[index] += 1
                valid_count += 1
                if index == 0:
                    rainfall_count += 1
        if not valid_count or rainfall_count < minimum_rainfall:
            raise ValueError(f"{path}: station {station} has insufficient valid rainfall history")
    if minimum_rainfall and any(count == 0 for count in target_counts):
        raise ValueError(f"{path}: one or more weather variables have no observations")
    for metadata, source in (
        ("estimatedValues", "estimatedValuesSource"),
        ("nearbyStationValues", "nearbyStationValuesSource"),
    ):
        entries = payload.get(metadata, {})
        if not isinstance(entries, dict) or (entries and not isinstance(payload.get(source), dict)):
            raise ValueError(f"{path}: invalid or missing provenance for {metadata}")
        for station, days in entries.items():
            if station not in stations or not isinstance(days, dict):
                raise ValueError(f"{path}: invalid {metadata} station {station!r}")
            by_date = {row[0]: row for row in stations[station]}
            for day, detail in days.items():
                row = by_date.get(day)
                if row is None:
                    raise ValueError(f"{path}: {metadata} refers to missing {station} {day}")
                if metadata == "estimatedValues":
                    if not isinstance(detail, list) or not detail or any(
                        field not in TARGETS or row[TARGETS.index(field) + 1] is None
                        for field in detail
                    ):
                        raise ValueError(f"{path}: invalid estimated values for {station} {day}")
                elif not isinstance(detail, dict) or not isinstance(detail.get("station"), str) or row[1] is None:
                    raise ValueError(f"{path}: invalid nearby station source for {station} {day}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=HISTORY_PATH)
    args = parser.parse_args()
    from update_climate_data import RAINFALL_PROXY_STATIONS, parse_station_mapping

    required = {station["code"] for station in parse_station_mapping().values()}
    required.update(RAINFALL_PROXY_STATIONS)
    history = load_history(args.path, required, minimum_rainfall=180)
    print(f"Validated {args.path}: {len(history['stations'])} stations.")


if __name__ == "__main__":
    main()
