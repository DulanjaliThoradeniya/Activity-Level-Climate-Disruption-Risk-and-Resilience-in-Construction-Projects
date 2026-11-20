import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from climate_history_validation import load_history


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".climate-cache"
OUTPUT_PATH = ROOT / "data" / "climate-history.json"
CSV_URL = "https://www.weather.gov.sg/files/dailydata/DAILYDATA_{station}_{year}{month:02d}.csv"
TARGET_COLUMNS = {
    "rainfall": "Daily Rainfall Total (mm)",
    "mean_temperature": "Mean Temperature",
    "maximum_temperature": "Maximum Temperature",
    "minimum_temperature": "Minimum Temperature",
}
MINIMUM_RECORDS = 365
DEFAULT_START_YEAR = 2010
SINGAPORE_TIMEZONE = timezone(timedelta(hours=8))
NEA_RAINFALL_API = "https://api-open.data.gov.sg/v2/real-time/api/rainfall?date={observed_date}"
NEA_TEMPERATURE_API = "https://api-open.data.gov.sg/v2/real-time/api/air-temperature?date={observed_date}"
RAINFALL_PROXY_STATIONS = {"S94": "Pasir Ris Street 51"}


def parse_station_mapping():
    source = (ROOT / "weather.js").read_text(encoding="utf-8")
    match = re.search(
        r"window\.neaHistoricalStationByArea\s*=\s*(\{[\s\S]*?\})\s*;",
        source,
    )
    if not match:
        raise RuntimeError("Could not find the NEA station mapping in weather.js.")
    return json.loads(match.group(1))


def month_range(end_month, count):
    index = end_month.year * 12 + end_month.month - 1
    months = []
    for offset in reversed(range(count)):
        year, month = divmod(index - offset, 12)
        months.append((year, month + 1))
    return months


def read_csv_text(path):
    content = path.read_bytes()
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def refresh_recent_observations(payload):
    yesterday = (datetime.now(SINGAPORE_TIMEZONE) - timedelta(days=1)).date()
    station_data = payload["stations"]
    latest_dates = [
        date.fromisoformat(row[0])
        for rows in station_data.values()
        for row in rows
        if isinstance(row, list) and len(row) == 5 and any(value is not None for value in row[1:])
    ]
    latest_date = max(latest_dates) if latest_dates else yesterday - timedelta(days=31)
    observed_date = latest_date
    while observed_date <= yesterday:
        refresh_recent_observation_day(payload, observed_date)
        observed_date += timedelta(days=1)


def fetch_daily_readings(url, observed_date):
    readings = []
    seen_tokens = set()
    token = None
    while True:
        page_url = url.format(observed_date=observed_date.isoformat())
        if token:
            page_url += "&" + urlencode({"paginationToken": token})
        request = Request(page_url, headers={"User-Agent": "C-RESIST climate research prototype"})
        for attempt in range(4):
            try:
                with urlopen(request, timeout=45) as response:
                    page = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if error.code != 429 or attempt == 3:
                    raise
                retry_after = error.headers.get("Retry-After", "")
                delay = int(retry_after) if retry_after.isdigit() else 2 ** (attempt + 2)
                print(f"NEA rate limit reached; retrying in {delay} seconds.")
                time.sleep(delay)
        if page.get("code") != 0 or not isinstance(page.get("data", {}).get("readings"), list):
            raise RuntimeError(f"Invalid NEA response for {observed_date}.")
        data = page["data"]
        readings.extend(data["readings"])
        token = data.get("paginationToken")
        if not token:
            return readings
        if token in seen_tokens:
            raise RuntimeError(f"Repeated NEA pagination token for {observed_date}.")
        seen_tokens.add(token)


def complete_station_readings(readings, observed_date, station_ids, interval_minutes):
    values = {}
    for reading in readings:
        timestamp = datetime.fromisoformat(reading["timestamp"]).astimezone(SINGAPORE_TIMEZONE)
        if timestamp.date() != observed_date:
            raise RuntimeError(f"NEA returned an unexpected observation date: {timestamp.isoformat()}")
        slot = timestamp.hour * 60 + timestamp.minute
        if slot % interval_minutes:
            raise RuntimeError(f"Unexpected NEA observation interval: {timestamp.isoformat()}")
        for station in reading.get("data", []):
            station_id, value = station.get("stationId"), station.get("value")
            if station_id in station_ids and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                by_slot = values.setdefault(station_id, {})
                if slot in by_slot:
                    raise RuntimeError(f"Duplicate NEA reading for {station_id} at {timestamp.isoformat()}.")
                by_slot[slot] = float(value)
    expected = 1440 // interval_minutes
    return {station: list(slots.values()) for station, slots in values.items() if len(slots) == expected}


def refresh_recent_observation_day(payload, observed_date):
    date_text = observed_date.isoformat()
    stations = payload["stations"]
    rainfall = complete_station_readings(fetch_daily_readings(NEA_RAINFALL_API, observed_date), observed_date, stations, 5)
    temperatures = complete_station_readings(fetch_daily_readings(NEA_TEMPERATURE_API, observed_date), observed_date, stations, 1)
    for station_id in stations:
        by_date = {row[0]: row for row in stations[station_id] if isinstance(row, list) and len(row) == 5}
        existing = by_date.get(date_text, [date_text, None, None, None, None]).copy()
        if station_id in rainfall and existing[1] is None:
            existing[1] = round(sum(rainfall[station_id]), 6)
        if station_id in temperatures:
            observed = temperatures[station_id]
            for index, value in ((2, sum(observed) / len(observed)), (3, max(observed)), (4, min(observed))):
                if existing[index] is None:
                    existing[index] = round(value, 2)
        if any(value is not None for value in existing[1:]):
            by_date[date_text] = existing
            stations[station_id] = [by_date[key] for key in sorted(by_date)]
    print(f"{date_text}: complete NEA rainfall for {len(rainfall)} stations, temperature for {len(temperatures)} stations")


def fetch_month(station, year, month):
    url = CSV_URL.format(station=station, year=year, month=month)
    request = Request(url, headers={"User-Agent": "C-RESIST climate research prototype"})
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read()
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f"NEA returned HTTP {error.code} for {url}") from error
    except URLError as error:
        raise RuntimeError(f"Could not retrieve NEA data from {url}: {error}") from error

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{station}_{year}{month:02d}.csv").write_bytes(content)
    time.sleep(0.05)
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def numeric_value(row, target):
    for column, value in row.items():
        if column.strip().startswith(target):
            try:
                return float(value.strip())
            except (AttributeError, ValueError):
                return None
    return None


def load_station(station, months, prior_records, refresh_months):
    month_keys = {f"{year:04d}-{month:02d}" for year, month in months}
    by_date = {
        row[0]: row
        for row in prior_records
        if isinstance(row, list)
        and len(row) == 5
        and isinstance(row[0], str)
    }
    earliest_requested = f"{months[0][0]:04d}-{months[0][1]:02d}-01"
    for year, month in months:
        month_key = f"{year:04d}-{month:02d}"
        cache_path = CACHE_DIR / f"{station}_{year}{month:02d}.csv"
        if month_key in refresh_months:
            content = fetch_month(station, year, month)
        elif cache_path.exists():
            content = read_csv_text(cache_path)
        elif any(observed_date.startswith(f"{month_key}-") for observed_date in by_date):
            continue
        else:
            content = fetch_month(station, year, month)
        if content is None:
            continue
        for row in csv.DictReader(io.StringIO(content)):
            try:
                observed = date(int(row["Year"]), int(row["Month"]), int(row["Day"]))
            except (KeyError, TypeError, ValueError):
                continue
            day = observed.isoformat()
            updated = [
                day,
                numeric_value(row, TARGET_COLUMNS["rainfall"]),
                numeric_value(row, TARGET_COLUMNS["mean_temperature"]),
                numeric_value(row, TARGET_COLUMNS["maximum_temperature"]),
                numeric_value(row, TARGET_COLUMNS["minimum_temperature"]),
            ]
            previous = by_date.get(day)
            if previous:
                updated = [day] + [
                    value if value is not None else previous[index]
                    for index, value in enumerate(updated[1:], 1)
                ]
            by_date[day] = updated

    records = [by_date[key] for key in sorted(by_date)]
    if len(records) < MINIMUM_RECORDS:
        raise RuntimeError(
            f"Station {station} has only {len(records)} valid daily records; "
            f"at least {MINIMUM_RECORDS} are needed."
        )
    return records


def preserve_estimated_provenance(previous, payload):
    previous_estimates = previous.get("estimatedValues", {})
    previous_stations = previous.get("stations", {})
    indexes = {"rainfall": 1, "mean_temperature": 2, "maximum_temperature": 3, "minimum_temperature": 4}
    retained = {}
    for station, days in previous_estimates.items():
        old_rows = {row[0]: row for row in previous_stations.get(station, []) if isinstance(row, list) and len(row) == 5}
        new_rows = {row[0]: row for row in payload["stations"].get(station, []) if isinstance(row, list) and len(row) == 5}
        for day, fields in days.items():
            old, new = old_rows.get(day), new_rows.get(day)
            if old is None or new is None:
                continue
            kept = [field for field in fields if field in indexes and old[indexes[field]] is not None and old[indexes[field]] == new[indexes[field]]]
            if kept:
                retained.setdefault(station, {})[day] = kept
    if retained:
        if not previous.get("estimatedValuesSource"):
            raise RuntimeError("Estimated input values have no recorded source.")
        payload["estimatedValues"] = retained
        payload["estimatedValuesSource"] = previous["estimatedValuesSource"]


def preserve_nearby_provenance(previous, payload):
    retained = {}
    for station, days in previous.get("nearbyStationValues", {}).items():
        old_rows = {row[0]: row for row in previous.get("stations", {}).get(station, [])}
        new_rows = {row[0]: row for row in payload["stations"].get(station, [])}
        for day, source in days.items():
            old, new = old_rows.get(day), new_rows.get(day)
            if old is not None and new is not None and old[1] is not None and old[1] == new[1]:
                retained.setdefault(station, {})[day] = source
    if retained:
        if not previous.get("nearbyStationValuesSource"):
            raise RuntimeError("Nearby-station input values have no recorded source.")
        payload["nearbyStationValues"] = retained
        payload["nearbyStationValuesSource"] = previous["nearbyStationValuesSource"]


def main():
    parser = argparse.ArgumentParser(description="Bundle recent NEA station records for the static climate forecast.")
    parser.add_argument(
        "--refresh-recent",
        action="store_true",
        help="Re-download the two latest completed months instead of using cached CSVs.",
    )
    parser.add_argument(
        "--history-years",
        type=int,
        default=None,
        help="Optional: download only this many years backwards instead of starting at --start-year.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=DEFAULT_START_YEAR,
        help=f"First year of NEA history to keep (default: {DEFAULT_START_YEAR}).",
    )
    args = parser.parse_args()
    if args.history_years is not None and args.history_years < 1:
        parser.error("--history-years must be at least 1")

    stations = parse_station_mapping()
    station_names = {}
    for station in stations.values():
        station_names.setdefault(station["code"], station["name"])
    station_names.update(RAINFALL_PROXY_STATIONS)

    current_month = date.today().replace(day=1)
    previous_month_index = current_month.year * 12 + current_month.month - 2
    year, zero_based_month = divmod(previous_month_index, 12)
    month = zero_based_month + 1
    month_count = (
        args.history_years * 12
        if args.history_years is not None
        else (year - args.start_year) * 12 + month
    )
    months = month_range(date(year, month, 1), month_count)
    first_kept_date = f"{months[0][0]:04d}-{months[0][1]:02d}-01"
    previous_payload = {}
    if OUTPUT_PATH.exists():
        previous_payload = load_history(OUTPUT_PATH)
    previous_data = {
        station: [row for row in rows if isinstance(row, list) and row and row[0] >= first_kept_date]
        for station, rows in previous_payload.get("stations", {}).items()
    }
    refresh_months = {
        f"{month_year:04d}-{month_number:02d}"
        for month_year, month_number in months[-2:]
    } if args.refresh_recent else set()

    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                load_station,
                station,
                months,
                previous_data.get(station, []),
                refresh_months,
            ): station
            for station in station_names
        }
        for future in as_completed(futures):
            station = futures[future]
            results[station] = future.result()
            print(f"{station}: {len(results[station])} daily records")

    payload = {
        "schemaVersion": 1,
        "source": "NEA Historical Daily Records",
        "sourceUrl": "https://www.weather.gov.sg/climate-historical-daily/",
        "updatedThrough": f"{year:04d}-{month:02d}",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stationNames": station_names,
        "stations": results,
    }
    if args.refresh_recent:
        refresh_recent_observations(payload)
    preserve_estimated_provenance(previous_payload, payload)
    preserve_nearby_provenance(previous_payload, payload)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )
    load_history(temporary_path, station_names, minimum_rainfall=180)
    temporary_path.replace(OUTPUT_PATH)

    requested_cache_files = {
        f"{station}_{year_number}{month_number:02d}.csv"
        for station in station_names
        for year_number, month_number in months
    }
    for cache_path in CACHE_DIR.glob("*.csv"):
        if cache_path.name not in requested_cache_files:
            cache_path.unlink(missing_ok=True)
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)} with {len(results)} stations.")


if __name__ == "__main__":
    main()
