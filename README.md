# C-RESIST AI

C-RESIST AI is a browser-based construction climate-risk demonstrator for climate-shock assessment in construction projects.

## Main Features

- Project setup with calendar, working days, location, and target completion date.
- Construction activity selection and predecessor/successor scheduling.
- Planned Gantt chart with WSH assessment status.
- NEA 24-hour regional weather information.
- 14-day Prophet climate forecasts for rainfall and temperature.
- Climate-disruption scenario, delay, WSH, and resilience assessment screens.
- Local browser persistence, JSON/CSV import and export, and printable project information.

## System Architecture

| Area | Main files | Responsibility |
|---|---|---|
| Application shell | `index.html`, `script.js`, `setup.js`, `splash.js` | Page structure, project setup, activity selection, and application state. |
| Schedule and Gantt | `schedule-engine.js`, `schedule.js`, `schedule.css` | Calendar-aware dependency scheduling, CSV/JSON import and export, scenario adjustments, and timeline rendering. |
| Weather and climate UI | `weather.js`, `rainfall-station.js`, `climate-forecast.js`, `assessment-screen.js` | NEA weather display, forecast selection, station fallback checks, and illustrative activity screens. |
| Scenario and risk UI | `refine.js`, `assessment-screen.js`, `refine.css` | Climate-disruption assumptions, activity exposure, candidate hazards and controls, and resilience summaries. |
| Climate forecast models | `Climate Model/Trained model/` | Historical-data preparation, Prophet and comparison-model training, validation, and forecast generation. |
| Forecast and station data | `data/climate-history.json`, `data/climate-forecasts.json`, `data/climate-multi-model-forecasts.json` | Versioned historical observations and precomputed forecast results used by the static website. |
| Offline browser snapshot | `build_climate_offline_bundle.py`, `data/climate-offline.js` | Packages the published climate JSON files for use when the website is opened from disk. |
| Activity prediction and graph | `risk_engine/`, `knowledge_graph/`, `models/delay_model/` | Optional activity-day feature generation, synthetic-demo delay prediction, graph-based candidate controls, and WSH assessment helpers. |
| Data refresh | `update_climate_data.py`, `refresh_station_forecasts.py` | Refreshes station history and selected saved station forecasts. |
| Automated updates and hosting | `.github/workflows/` | GitHub Actions workflows for GitHub Pages deployment and climate-data/forecast updates. |

### Code and data flow

1. `index.html` defines the browser interface and loads the JavaScript and CSS modules. `setup.js` initializes project details, `script.js` connects the activity catalogue and project state, and `splash.js` manages startup presentation.
2. `schedule-engine.js` calculates dates from activity durations, dependency types, working weekdays, holidays, and scenario inputs. `schedule.js` connects those calculations to the Gantt chart, schedule editor, and CSV/JSON import and export.
3. `weather.js` retrieves the NEA regional outlook. `climate-forecast.js` reads the precomputed climate JSON and draws the forecast and model-comparison views; it does not train models in the browser. `rainfall-station.js` verifies forecast coverage and applies only the documented S29-to-S94 fallback. `assessment-screen.js` chooses available rainfall results and presents clearly labelled illustrative activity hazards and controls.
4. `refine.js` handles the climate-disruption scenario and related project summaries. The displayed scenario assumptions, forecast comparisons, and WSH examples are separate from an approved, site-specific risk assessment.
5. Climate model scripts under `Climate Model/Trained model/` consume `data/climate-history.json`. The Prophet and multi-model builders write versioned forecast JSON under `data/`; `build_climate_offline_bundle.py` combines those inputs into `data/climate-offline.js` for local-file browsing.
6. The optional `risk_engine/` Python package maps project activities to knowledge-graph records, builds activity/day features, validates and evaluates delay models, and creates candidate intervention or WSH reports. Its prediction service is optional; the static website does not require a running Python API.

The `data/activity_observations/` files distinguish observation templates and synthetic demonstrations from real project outcome data. `knowledge_graph/` contains the source workbooks used for knowledge-graph and provisional WSH examples. Treat any synthetic or provisional outputs as demonstrations, not empirical findings or work authorization.

## Running the Application

### Browser application

Open `index.html` directly, or serve the project locally:

```powershell
py -m http.server 8000
```

Then open `http://localhost:8000`.

When opening the file directly, the climate popup reads `data/climate-offline.js` from this folder rather than the published website. After changing any climate history or forecast JSON, rebuild that local snapshot and use **Refresh forecast**:

```powershell
py build_climate_offline_bundle.py
```

The snapshot contains the saved forecasts; rebuilding it does not train models or obtain newer observations. Hosted pages continue to load the JSON files normally.

### Generate a Prophet forecast locally

Install the dependencies once:

```powershell
py -m pip install -r requirements.txt
```

Generate one forecast:

```powershell
py "Climate Model/Trained model/prophet_forecast.py" --location "Changi" --target rainfall --years 5 --forecast-days 14
```

Generate the published static forecast bundle:

```powershell
py "Climate Model/Trained model/build_prophet_forecasts.py"
```

## Climate Forecast Data and Caching

- NEA observations are bundled in `data/climate-history.json`.
- Published Prophet output is stored in `data/climate-forecasts.json`.
- Historical station CSV downloads start in 2010 by default (`--start-year`, default 2010) and run up to the last completed month; the latest days are added from the NEA real-time APIs, with monthly files cached in `.climate-cache/`.
- `--refresh-recent` also retrieves paginated NEA rainfall and temperature observations from data.gov.sg. It uses only complete station-days, preserves existing daily values, and stops on retrieval errors or persistent rate limits; partial days are not treated as complete observations. It preserves the provenance of any retained estimates.
- Where station observations are unavailable, the history bundle may include gridded estimates from the Open-Meteo Historical Forecast API. These are past forecasts, not observations; `estimatedValues` identifies affected stations, dates, and variables, while `estimatedValuesSource` records provenance. Check those fields before treating a value as observed data.
- Saved forecasts are refreshed incrementally and can have different dates and variable coverage. Updating input history alone does not regenerate saved results. Run `py refresh_station_forecasts.py --help` for the supported date, station, and target options. A missing temperature input does not prevent rainfall from being refreshed.
- The multi-model bundle records validation and input-period metadata by station, model, and target. Forecast periods and availability can differ; check the saved metadata and do not assume every station or variable is current. Temperature results may be stale or unavailable.
- When S29 has no current rainfall forecast, the climate report and assessment use S94 (Pasir Ris Street 51) only if its saved rainfall forecast covers the complete current 14-day window. The proxy is labelled, and no temperature data are substituted. The nearest comparison uses the coordinates for S29 in [NEA rainfall metadata on 3 August 2025](https://api.data.gov.sg/v1/environment/rainfall?date=2025-08-03) (1.387, 103.935) and S94 in [NEA metadata on 10 November 2026](https://api.data.gov.sg/v1/environment/rainfall?date=2026-11-10) (1.3679, 103.9489); S94 was the nearest in the latter station list, approximately 2.63 km away. Locations and availability may change, and the historical S29 archive calls the station Pasir Ris (West). S25 is not substituted because its historical station coordinates have not been independently verified.
- Generated forecast output can be cached locally for 24 hours in `.forecast-api-cache/`.
- Detailed local HTML forecasts and charts are written to `forecast-results/`.
- Runtime caches and generated reports are excluded from source control.

## Folder map

| Area | Files / folders |
| --- | --- |
| Project input, Gantt chart, scheduling | `index.html`, `setup.js`, `script.js`, `schedule.js`, `schedule-engine.js`, `splash.js`, `*.css` |
| Live weather and climate forecast display | `weather.js`, `climate-forecast.js`, `rainfall-station.js`, `data/climate-*.json` |
| Risk assessment (WSH, hazards, controls) | `refine.js`, `assessment-screen.js`, `risk_engine/`, `knowledge_graph/`, `models/delay_model/` |
| Climate data collection and forecasting | `update_climate_data.py`, `refresh_station_forecasts.py`, `build_climate_offline_bundle.py`, `Climate Model/Trained model/` |
| Tests | `tests/`, `*.test.cjs`, `test_climate_offline_bundle.py` |
| Automation | `.github/workflows/`, `start-climate-resist.bat` |
| Project export examples | `reports/Project Export Examples/` |

Source files stay in the project root because `index.html`, the scripts and the GitHub workflows reference them by relative path.

The browser reads the published JSON forecast data. It does not download NEA historical records or retrain Prophet models whenever the interface opens.

The climate forecast dialog retrieves the latest NEA station rainfall interval and air temperature separately from the daily training data. Today's readings are provisional and are not inserted into the daily model inputs. The 14-day forecast begins after the latest completed input day; a complete current horizon requires refreshed inputs and regenerated model results each day. The chart draws one forecast line without a bridge from a later observation, and excludes observations after a saved forecast starts; stale results remain labelled as such.

The rainfall model-comparison view highlights the station-specific validation rows used by the activity rain-hazard screen and identifies the model and MAE for each forecast date or consecutive date range. Selection uses the same lowest-available-MAE rule as the screen, including any labelled station proxy; MAE is a retrospective station-level metric, not a daily accuracy score. Temperature comparisons do not supply this rain screen.

## Forecasting Model

`Climate Model/Trained model/prophet_forecast.py` prepares daily observations, fills only short data gaps, splits training and final held-out periods, fits Prophet, and evaluates rainfall and temperature forecasts. Rainfall remains in millimetres and uses additional handling for zero-heavy, intermittent observations.

Model candidates are selected only from earlier training observations. MAE, RMSE, and non-zero-observation MAPE are reported as retrospective held-out scores, not guarantees of future accuracy.

## Schedule and Risk Behaviour

Activities support finish-to-start, start-to-start, finish-to-finish, and start-to-finish relationships with working-day lag or lead values. Schedule calculations use the selected weekdays and project calendar; public holidays are not inferred automatically.

The scenario, productivity, delay, and WSH values are explicitly entered assumptions. The application is a decision-support prototype, not an official safety, structural engineering, or weather-warning service.

The bundled knowledge contains no approved project WSH assessments. Select an activity bar to see an illustrative rain-related hazard screen and candidate controls. It uses the lowest available station-specific rainfall validation MAE independently for each date from the existing five-model outputs, restricted to the activity dates in the next 14 Singapore calendar days. Validation MAE is retrospective, not a guarantee of best future performance. Missing or stale forecasts remain unavailable; dry forecasts do not mean the site is hazard-free. The candidate hazard filter uses a provisional 20 mm daily-total threshold; daily totals cannot establish rain intensity or rule out excluded hazards. Severity, likelihood and RPN are fixed synthetic examples, not weather-calibrated predictions, and site risk remains unassessed. A competent RA team and supervisor must verify conditions and approve work.

The lower Gantt bars use the mean of the available illustrative per-hazard RPN examples for the screened activity (green 1–4, amber above 4–12, red above 12); grey indicates no illustrative RPN or incomplete forecast/score coverage. These display bands are not approved WSH risk classifications. Missing forecast days and unscored hazards are not treated as safe. Delay predictions are not displayed on the Gantt or in the activity assessment; the underlying scheduling and export calculations remain available. The workbook's exposure classes and typical predecessors are reference assumptions that require project confirmation; it contains no project-specific activity-day outcomes or approved hazard ratings.

The activity popup groups hazard rows by forecast date and uses the same colour as the Gantt for its illustrative RPN card. The Gantt marks today's Singapore date with a vertical line and shades the 14-day screening window, regardless of forecast availability. It also shows the selected station results' input end dates and forecast periods; different models can have different periods.

The activity popup also displays per-hazard hypothetical examples from the `Provisional_WSH_Scores` sheet in `knowledge_graph/Knowledge_Graph_Project_with_Provisional_WSH_Scores.xlsx`. These values are synthetic desk-based assumptions, not survey findings, approved ratings, or safety decisions; the example matrix is not employer-approved. They do not color the Gantt or authorize work. Rebuild the static examples after a source update with `.\.venv\Scripts\python.exe -m risk_engine.build_provisional_wsh_bundle`; the command reads the workbook without changing it and checks scores, provenance, and hazard coverage.

The popup's experimental forecast context shows the selected activity's exposure and sensitivities alongside available forecast dates overlapping its planned calendar-date range. It uses the existing lowest-validation-MAE model selection separately for each variable and date, and reports missing forecasts rather than inferring a rating. It refreshes when the open assessment is recalculated, the location changes, the window regains focus, and every 24 hours while the dialog remains open; it also reloads on reopening. It reads the published five-model bundle but does not refresh or train climate models. The workbook examples are not adjusted for weather or site inputs, and no WSH prediction model has been trained from them.

## Local Persistence

The browser stores the project schedule and scenario assumptions locally using versioned JSON. Schedule CSV and JSON import/export are available in the schedule panel.

## Activity Productivity Predictions

The schedule can optionally use activity/day productivity profiles from the separate `risk_engine` model. It resolves the project's existing NEA station mapping, chooses the lowest-validation-MAE forecast for each available station, target, and date from the five-model forecast bundle, and passes those values to the downstream productivity model. The climate forecast page and its five-model comparison are not changed. WSH risk assessment remains separate; productivity predictions do not assess or authorize work. Explicit mandatory stop-work dates remain hard constraints.

The browser first attempts the loopback FastAPI service, then a project-specific `data/activity-disruption-predictions.json` bundle whose embedded inputs must exactly match the current project, station, activities, and forecast dates. If neither source is available or current forecast dates do not overlap the saved bundle, the interface reports why and retains the existing illustrative scenario workflow. The current model artifacts are synthetic demonstration data and must not be interpreted as empirical validation or used for real safety decisions.

Install the optional API dependencies and run the explicitly demo-only local service:

```powershell
.\.venv\Scripts\python.exe -m pip install -r risk_engine\api-requirements.txt
.\.venv\Scripts\python.exe -m risk_engine.prediction_api --profile synthetic_demo --allow-synthetic-demo
```

For static hosting, prepare a project-specific request JSON using the schema accepted by `risk_engine.prediction_service.canonical_request`, then generate the matching bundle:

```powershell
.\.venv\Scripts\python.exe -m risk_engine.build_static_prediction_bundle --request private-inputs\project-prediction-request.json --profile synthetic_demo --allow-synthetic-demo
```

GitHub Pages serves the resulting JSON; it does not execute Python. Do not publish a project-specific request or bundle containing information that should remain private.

## WSH Intervention Recommendations

`risk_engine.intervention_engine.recommend_activity_interventions` returns a JSON-compatible activity report with graph-linked hazards, candidate controls and interventions, source documents/sections, WSH assessment and stop/restart gate status, site applicability review, implementation resources/owner/time, and schedule comparison fields. `recommend_for_activities` produces separate reports for multiple WBS codes. Graph applicability remains a candidate until the work method and site are reviewed; an intervention never grants work authorization or changes residual WSH risk.

The knowledge graph does not assign numeric intervention effectiveness or observed productivity gains. Numeric productivity profiles are evaluated only when an explicit scenario assumption or quantified evidence is supplied; assumptions are labeled as non-empirical. Site owners, required resources, implementation/recovery workdays, approvals, and applicability are not inferred when missing.

An optional `schedule_runner` callback lets the engine compare baseline and intervention scenarios through the existing `schedule-engine.js` `calculate` function. It uses the same activities, project start, weekday/holiday calendar, and dependency logic. The callback must map the Python calendar's `holidays` array to the JavaScript engine's `Set`. Optional `implementationDelays` and `recoveryDaysByActivity` scenario maps account for assigned intervention and recovery workdays without changing legacy scenarios.

## Tests

Run all browser-logic tests when Node.js is available:

```powershell
node --test assessment-screen.test.cjs climate-forecast.test.cjs rainfall-station.test.cjs schedule-assessment.test.cjs schedule-engine.test.cjs schedule-gantt.test.cjs weather-live.test.cjs
```

Run the Python unit tests:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -B -m unittest discover -s "Climate Model\Trained model" -p "test_*.py" -v
.\.venv\Scripts\python.exe -B test_climate_offline_bundle.py
```

Compile the main Python modules:

```powershell
py -m py_compile "Climate Model/Trained model/prophet_forecast.py" "Climate Model/Trained model/build_prophet_forecasts.py" "Climate Model/Trained model/climate_models.py" update_climate_data.py
```

## User workflow
Project setup -> activity selection and planned schedule -> WSH assessment status, graph-linked hazards and candidate controls.

The left panel groups project information, NEA weather, building storeys, and the 69 activities in the numbered WBS catalogue: 1. SP - Site preparation, 2. EW - Earthwork, 3. FD - Foundation, 4. SS - Superstructure, 5. TW - Temporary works (cross-cutting), 6. BE - Building envelope, 7. MEP - MEP installation, 8. FN - Finishing, and 9. XW - External works. Activities receive sequential stage.subcategory numbers (for example, 1.1 SP-01); the WBS activity codes are retained. The MEP stage is labelled MEP while its source activity codes keep the PDF's ME prefix. Select stage activities and enter durations, site exposure, and rainfall sensitivity in the stage dialog. New activities keep the existing defaults: duration unset, outdoor exposure, and medium rainfall sensitivity. Users should confirm or change these illustrative assumptions using site layouts and method statements; dependencies remain editable separately. The center Gantt shows planned dates and unassessed WSH status. The right panel explains that site-specific assessments are required and lists general WSH controls. Project target completion is calculated from start date, planned working-day duration, and selected weekdays; calculated completion shows the planned activity schedule.

The calendar scheduler supports any number of predecessor and successor links; each link independently records its relationship (`FS` finish-to-start, `SS` start-to-start, `FF` finish-to-finish, or `SF` start-to-finish) and working-day lag/lead (positive values delay, negative values lead). Links use inclusive working-date finishes and the selected project calendar. CSV imports remain compatible with the original `id,name,stage,durationDays,predecessors,exposure,heatSensitivity,rainSensitivity` columns; exports and the template also include a `dependencies` JSON column with `predecessorId`, `type`, and `lag`. Existing `predecessors` IDs are treated as finish-to-start links with zero lag when no dependency details are present. Use ISO dates (`YYYY-MM-DD`), weekday numbers Monday=1 through Sunday=7, and comma-separated non-working dates. Dates are local calendar labels, not timestamps.

When an activity-specific productivity prediction is unavailable, the scenario calculation remains conditional on the entered event occurring. Its productivity factor (0–1) is an illustrative user assumption applied to eligible exposed activities on working days in the scenario window. The optional recovery allowance is added after scenario-affected activity work completes. Activity predictions, when available, are applied by working date; the scheduler propagates direct disruption and inherited dependency shifts without summing them as separate project delays. Working hours per day convert predicted lost hours into productive work fractions while schedule durations remain in working days. The weather outlook, activity productivity/delay, and WSH assessment are distinct outputs.

## Quick Walkthrough
1. Open the published website, or run `start-climate-resist.bat` for a local copy, and create a project. Set its name, NEA area, start date, planned working-day duration, and working weekdays.
2. Open a construction stage, select applicable activities, enter durations, and confirm each activity's exposure and rainfall-sensitivity assumptions.
3. Use **Reset activities** at the top-right of the Gantt heading to clear the selected activity schedule and start again; project dates, calendar, and scenario settings are kept.
4. Review the planned Gantt and WSH control measures; grey bars indicate that WSH risk is unassessed.
5. Select an activity bar in the Gantt chart to review its WSH-assessment status, planned dates, graph-linked hazards, and candidate interventions.
6. Select **Climate Forecast** to open the 14-day climate forecast with held-out validation scores and prediction intervals. For saved five-model results containing split metadata, the chart highlights the chronological training and test periods and shows the 14-day forecast on a separate expanded date scale. Rainfall results use 80% of verified observations since 2006 for training and 20% for rolling test forecasts; temperature results may use older validation periods. These retrospective errors do not guarantee future accuracy. This regional model output does not automatically create site-specific schedule impacts.

The default intervention productivity equals the baseline scenario productivity, so it claims no improvement until the user changes the assumption. A comparison with positive delay avoided is only a model output, not evidence that a real control will achieve that effect.

## Implementation notes and limitations

- Activities and dependencies are defined in the application modules; project dates are calculated with the selected working calendar. Public holidays are excluded only when entered by the user.
- The NEA 24-hour outlook is regional, and the historical model uses representative monitoring stations. Neither provides site-specific warnings.
- Climate forecasts, activity productivity/delay estimates, and WSH assessments are distinct outputs. Validation metrics describe retrospective performance and do not guarantee future accuracy.
- The activity screen's hazard threshold and provisional severity/likelihood/RPN examples are illustrative. Missing or stale data are reported as unavailable; they must not be interpreted as safe conditions.
- Delay-model artifacts currently include a synthetic demonstration profile. Do not treat its predictions as empirically validated project outcomes. Have a competent project risk-assessment team verify site conditions and approve controls before work.
- The application is a research demonstrator, not an official weather-warning, engineering, or safety-authorization system.

Local JSON persistence uses schema version 1 and stores only the project schedule and scenario assumptions in this browser. JSON/CSV export is available in the schedule panel.
