# C-RESIST AI
Editable HTML/CSS/JavaScript prototype for climate-shock risk and resilience assessment in construction.

## Run
Open the published website in a browser. The climate forecast runs in browser JavaScript from a bundled NEA history file; VS Code, Python, and a separate forecast service are not required. `.github/workflows/deploy-pages.yml` publishes the static app to GitHub Pages on each push to `main`, and the history updater also deploys its refreshed data. To enable this the first time, select **Settings → Pages → Build and deployment → GitHub Actions** in the repository. If testing a local copy, serve the project files over HTTP(S); browsers generally block fetching the bundled JSON when `index.html` is opened directly as a `file://` URL.

## Climate forecast report
The project-area selector contains the 47 areas currently returned by NEA's 2-hour weather forecast feed. The Climate Forecast popup defaults to rainfall. Select **Climate Forecast** to generate the report in the browser using the historical observations bundled in `data/climate-history.json`. It includes the latest year of observations, a 14-day estimate and interval, EDA charts, and MAE, RMSE, and MAPE from a held-out validation window. No forecast API or Python package is needed in normal use.

The bundled data contains up to 60 completed months from NEA's [Historical Daily Records](https://www.weather.gov.sg/climate-historical-daily/) for the 31 representative stations used by the area mapping. `.github/workflows/update-climate-history.yml` refreshes the bundle monthly and can also be started manually from the repository's Actions tab. The updater reuses the existing bundled history and refreshes the latest two completed months rather than downloading the full multi-year dataset each time. To update the data manually, run `python update_climate_data.py`; Python is only needed for this maintenance task, not for app users.

The browser uses a transparent additive regression with a linear trend, annual seasonal terms, and a weekly term. It is not Facebook Prophet or an AI model. The validation metrics come from a held-out period of up to 60 recent observations; MAPE excludes zero-rainfall days. Available variables depend on the station's historical record, so the popup disables variables without enough observations. This is exploratory station-based evidence, not an official NEA forecast or a site-specific warning. Rainfall is intermittent, so short, intense storms may not be represented by the smoothed 14-day estimate. The displayed residual interval is approximate and is not a calibrated guarantee.

## Edit
- Structure/content: `index.html`
- Design: `style.css`
- Simulation/interactions/animations: `script.js`

## Current demonstrator
Project setup -> activity selection and schedule -> illustrative rainfall/flood scenario -> impacted activities and resilience actions.

The left panel groups project information, NEA weather, building storeys, and seven stages (SP, EW, FD, SS, MEP, FN, TC). Select stage activities and enter durations, site exposure, and rainfall sensitivity in the stage dialog. New activities default to outdoor exposure and medium rainfall sensitivity; users should confirm or change these illustrative assumptions using site layouts and method statements. The center Gantt compares planned and climate-adjusted dates. The right panel lists impacted activities and actions under five resilience stages. Project target completion is calculated from start date, planned working-day duration, and selected weekdays; calculated completion is based on the activity schedule and selected scenario.

The calendar scheduler supports any number of predecessor and successor links; each link independently records its relationship (`FS` finish-to-start, `SS` start-to-start, `FF` finish-to-finish, or `SF` start-to-finish) and working-day lag/lead (positive values delay, negative values lead). Links use inclusive working-date finishes and the selected project calendar. CSV imports remain compatible with the original `id,name,stage,durationDays,predecessors,exposure,heatSensitivity,rainSensitivity` columns; exports and the template also include a `dependencies` JSON column with `predecessorId`, `type`, and `lag`. Existing `predecessors` IDs are treated as finish-to-start links with zero lag when no dependency details are present. Use ISO dates (`YYYY-MM-DD`), weekday numbers Monday=1 through Sunday=7, and comma-separated non-working dates. Dates are local calendar labels, not timestamps.

The scenario calculation is conditional on the entered event occurring. Its productivity factor (0–1) is an illustrative user assumption applied to eligible exposed activities on working days in the scenario window. The optional recovery allowance is added once after event-affected activity work completes. Working hours per day are recorded as calendar context; duration inputs and the scheduler use whole productive workdays. This prototype does not infer intensity or stoppage hours from forecast text. The weather feed is a regional near-term outlook and is separate from schedule stress testing. Safety review remains a separate WSH screening estimate.

## Quick walkthrough
1. Open `index.html` directly or through VS Code Live Server and create a project. Set its name, NEA area, start date, planned working-day duration, and working weekdays.
2. Open a construction stage, select applicable activities, enter durations, and confirm each activity's exposure and rainfall-sensitivity assumptions.
3. Set the rainfall/flood event duration and select **Run future scenario**. Review the planned versus climate-adjusted Gantt, impacted activities in the 14-day assessment window, calculated completion, and five-stage response actions.
4. Select **Climate Forecast** to open the browser-generated report from bundled NEA historical data, including EDA charts and validation metrics. This forecast is regional evidence and does not automatically create site-specific schedule impacts.

The default intervention productivity equals the baseline scenario productivity, so it claims no improvement until the user changes the assumption. A comparison with positive delay avoided is only a model output, not evidence that a real control will achieve that effect.

## Tests
With Node.js installed, run `node schedule-engine.test.cjs` and `node climate-forecast.test.cjs`. The climate forecast was also exercised in a browser against Changi and Pasir Ris station data; browser rendering, keyboard use, responsive layout, weather-feed freshness, and file picker interaction still need a manual check.

## Audit and development roadmap
- Existing activities/dependencies live in `script.js` network definitions and the detailed `workBreakdowns` in `refine.js`. The previous whole-project Gantt dates were generated as a sequential display estimate in `refine.js`, not calculated through a calendar scheduler.
- `refine.js` calculates a separate illustrative future-scenario disruption screen and WSH severity × likelihood RPN. The WSH score does not drive schedule dates. Official regional outlook is displayed without converting text into activity delay or worker likelihood. Legacy arbitrary resilience proxy percentages were removed from the results panel.
- `weather.js` retrieves NEA's 24-hour feed and maps an area to a broad region. That regional forecast does not provide site-specific conditions. Missing data are displayed as unavailable.
- Project setup calculates the target completion date inclusively from the project start date, planned working-day duration, and selected weekdays. Public holidays are not excluded.
- The former reset handler was bound from both weather and refinement scripts. It is now a single assessment reset handler in `refine.js`; weather refresh is exposed from `weather.js` for that handler.
- `climate-forecast.js` fits and validates the browser-side additive regression and builds the chart/EDA report; `update_climate_data.py` bundles NEA monthly CSVs as static JSON for all mapped stations. The previous Python/Prophet service remains as a separate research prototype and is no longer required by the website popup.
- The NEA 24-hour feed remains a broad regional outlook. The historical model uses a representative NEA station mapped to the project area; neither should be interpreted as a site-specific warning. The Prophet output is exploratory and should be compared with simple baselines and validated before supporting a forecast claim. Keep a structured activity→hazard→impact→availability/productivity→schedule model with evidence status and applicability notes before considering graph storage. Add sustainability comparisons only with entered energy, equipment, water, cost, or emissions data. Resource-constrained scheduling, crashing, and fast-tracking remain future work with safety constraints.

Local JSON persistence uses schema version 1 and stores only the project schedule and scenario assumptions in this browser. JSON/CSV export is available in the schedule panel.
