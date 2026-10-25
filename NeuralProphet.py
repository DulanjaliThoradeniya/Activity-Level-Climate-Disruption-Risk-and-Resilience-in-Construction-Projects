import argparse
import base64
import calendar
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import re
import time
from datetime import date
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
CACHE_DIR = Path(__file__).resolve().parent / ".climate-cache"
REPORT_DIR = Path(__file__).resolve().parent / "forecast-results"


def load_area_stations():
    source = Path(__file__).resolve().with_name("weather.js").read_text(encoding="utf-8")
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
    if cache_path.exists():
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
    latest_completed_month = shift_month(date.today().replace(day=1), -1)
    months = [shift_month(latest_completed_month, -offset) for offset in range(years * 12)]
    monthly_frames = []
    unavailable_months = 0
    source_urls = []

    for month in reversed(months):
        content, url, unavailable = fetch_month(station_code, month)
        if unavailable:
            unavailable_months += 1
            continue
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

    return history, unavailable_months, source_urls


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


def make_report_html(area, station, station_code, target_label, data, metrics, test_days, forecast_days, charts):
    escaped_area = html.escape(area)
    escaped_station = html.escape(station)
    escaped_target = html.escape(target_label)
    history_start = data["ds"].min().strftime("%Y-%m-%d")
    history_end = data["ds"].max().strftime("%Y-%m-%d")
    mape_text = f"{metrics['mape']:.2f}%" if metrics["mape"] is not None else "N/A (no non-zero observations)"
    mape_sample = (
        f" ({metrics['mape_sample_count']} of {test_days} test days)"
        if metrics["mape"] is not None
        else ""
    )

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
      <div class="metric">Test-period MAE<strong>{metrics['mae']:.2f}</strong></div>
      <div class="metric">Test-period RMSE<strong>{metrics['rmse']:.2f}</strong></div>
      <div class="metric">Test-period MAPE<strong>{mape_text}</strong></div>
      <div class="metric">Forecast horizon<strong>{forecast_days} days</strong></div>
      <div class="metric">Evaluation window<strong>{test_days} days</strong></div>
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
      <p>Validation metrics compare the model with the final {test_days} historical observations that were withheld from training. MAE is mean absolute error, RMSE penalizes larger errors more strongly, and MAPE is mean absolute percentage error calculated on non-zero observations only{mape_sample}. These are retrospective test scores, not guarantees of future accuracy.</p>
      <p class="note">This is an exploratory statistical model, not an official NEA forecast, warning, or project-site measurement. The selected project area uses the mapped representative station shown above.</p>
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
  <p class="source">Source: <a href="{HISTORICAL_PAGE}" target="_blank" rel="noopener">Meteorological Service Singapore / NEA Historical Daily Records</a>. Generated from NEA station CSV records. MAE, RMSE, and non-zero-observation MAPE are evaluated on the final {test_days} historical days held out from model fitting.</p>
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
    if years < 2:
        raise ValueError("At least two years of NEA records are required for yearly seasonality.")
    if forecast_days < 1:
        raise ValueError("The forecast horizon must be at least one day.")

    station = area_stations[area]
    history, unavailable_months, source_urls = download_history(station["code"], years)
    if not source_urls:
        raise RuntimeError(
            f"No NEA historical monthly records were found for {station['name']} ({station['code']})."
        )
    if unavailable_months == years * 12:
        raise RuntimeError(f"No NEA historical monthly records were found for station {station['code']}.")

    target_column = TARGETS[target]
    if target_column not in history:
        raise RuntimeError(f"The NEA station CSV does not contain the target column: {target_column}")
    data = history[["Date", target_column]].rename(columns={"Date": "ds", target_column: "y"})
    data = data.dropna().reset_index(drop=True)
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
    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        interval_width=0.95,
    )
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
        super().__init__(*args, directory=str(Path(__file__).resolve().parent), **kwargs)

    def _add_api_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        if urlsplit(self.path).path != "/api/forecast":
            self.send_error(404)
            return
        self.send_response(204)
        self._add_api_headers()
        self.end_headers()

    def do_GET(self):
        if urlsplit(self.path).path != "/api/forecast":
            super().do_GET()
            return

        query = parse_qs(urlsplit(self.path).query)
        area = query.get("location", [""])[0]
        target = query.get("target", ["maximum_temperature"])[0]
        try:
            report, details = generate_report(area, target, years=5, forecast_days=14)
            body = json.dumps({"reportHtml": report, "details": details}).encode("utf-8")
            self.send_response(200)
        except ValueError as error:
            body = json.dumps({"error": str(error)}).encode("utf-8")
            self.send_response(400)
        except (RuntimeError, HTTPError, URLError) as error:
            body = json.dumps({"error": str(error)}).encode("utf-8")
            self.send_response(502)

        self._add_api_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(port, open_browser=False):
    server = ThreadingHTTPServer(("127.0.0.1", port), ForecastRequestHandler)
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
    parser.add_argument("--years", type=int, default=5, help="Number of recent years to retrieve (default: 5)")
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
    if args.years < 2:
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
