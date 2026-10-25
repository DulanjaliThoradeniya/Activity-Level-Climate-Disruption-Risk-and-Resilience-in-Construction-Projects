import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
        and row[0][:7] in month_keys
    }
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
            by_date[observed.isoformat()] = [
                observed.isoformat(),
                numeric_value(row, TARGET_COLUMNS["rainfall"]),
                numeric_value(row, TARGET_COLUMNS["mean_temperature"]),
                numeric_value(row, TARGET_COLUMNS["maximum_temperature"]),
                numeric_value(row, TARGET_COLUMNS["minimum_temperature"]),
            ]

    records = [by_date[key] for key in sorted(by_date)]
    if len(records) < MINIMUM_RECORDS:
        raise RuntimeError(
            f"Station {station} has only {len(records)} valid daily records; "
            f"at least {MINIMUM_RECORDS} are needed."
        )
    return records


def main():
    parser = argparse.ArgumentParser(description="Bundle recent NEA station records for the static climate forecast.")
    parser.add_argument(
        "--refresh-recent",
        action="store_true",
        help="Re-download the two latest completed months instead of using cached CSVs.",
    )
    args = parser.parse_args()

    stations = parse_station_mapping()
    station_names = {}
    for station in stations.values():
        station_names.setdefault(station["code"], station["name"])

    current_month = date.today().replace(day=1)
    previous_month_index = current_month.year * 12 + current_month.month - 2
    year, zero_based_month = divmod(previous_month_index, 12)
    month = zero_based_month + 1
    months = month_range(date(year, month, 1), 60)
    previous_data = {}
    if OUTPUT_PATH.exists():
        previous_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("stations", {})
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
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )
    temporary_path.replace(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)} with {len(results)} stations.")


if __name__ == "__main__":
    main()
