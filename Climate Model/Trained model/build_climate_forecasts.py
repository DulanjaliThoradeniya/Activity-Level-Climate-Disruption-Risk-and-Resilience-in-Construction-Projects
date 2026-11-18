import argparse
from pathlib import Path

from climate_models import FORECAST_PATH, HISTORY_PATH, write_forecast_payload


def main():
    parser = argparse.ArgumentParser(
        description="Build static model forecasts and rolling validation from bundled NEA history."
    )
    parser.add_argument(
        "--station",
        help="Build only one station (for example S24) instead of all mapped stations.",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=HISTORY_PATH,
        help="Path to the bundled climate-history.json file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=FORECAST_PATH,
        help="Where to write the static climate-forecasts.json file.",
    )
    args = parser.parse_args()
    write_forecast_payload(
        output_path=args.output,
        history_path=args.history,
        station_code=args.station,
    )


if __name__ == "__main__":
    main()
