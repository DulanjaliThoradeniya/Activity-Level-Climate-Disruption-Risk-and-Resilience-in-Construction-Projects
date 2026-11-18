# Activity-day observations

`real_activity_observations.csv` is intentionally header-only until sourced,
project-specific daily records are provided. It is the built feature dataset;
do not populate it with simulated values. Each row represents one project,
canonical WBS activity, and working day. Keep the original NEA station ID; do
not replace it with a Singapore-wide weather average.

`activity_outcome_template.csv` documents the input daily outcome columns
accepted by `load_activity_observations_csv`. Add genuine sourced records there
or in an equivalent file, then pass them with the project configuration to
`build_activity_day_dataset` and write the resulting feature rows to
`real_activity_observations.csv`.

The project configuration passed to `risk_engine.feature_builder` should use
the current schedule export's activity fields (`catalogueCode`, `exposure`,
`heatSensitivity`, `rainSensitivity`) or their snake-case equivalents, plus
`project_id`, `location` or `nea_station_id`, and optional site conditions.
Dates and actual outcome hours belong in the daily observation CSV. Graph
controls are knowledge-graph candidates and are not treated as implemented
project controls.

Build a feature dataset from genuine activity-day observations with
`build_activity_day_dataset(projects, observations)`, then write it with
`write_dataset_csv`. The canonical WBS code, NEA station, weather, recent
rainfall, graph features, project settings, outcomes, and provenance are
validated without imputing missing values.

`synthetic_demonstration.csv` is generated separately by:

```powershell
.\.venv\Scripts\python.exe -B -m risk_engine.synthetic_generator
```

It uses station-level historical NEA observations for a clearly synthetic
demonstration project. Its productivity, sequential schedule, durations, and
weather-attributed lost hours are assumption-derived, not empirical or
validated. Assumptions are written to `synthetic_assumptions.json`.

Validate datasets and write the quality report with:

```powershell
.\.venv\Scripts\python.exe -B -m risk_engine.data_validator
```

The temporal split helper puts validation dates strictly after training dates.
The rolling rainfall feature uses only the seven calendar days before the row
date. Same-day weather fields are retrospective NEA observations; prospective
use requires weather available as of the prediction time, not future observed
weather.

## Activity climate-disruption model training

The downstream productivity model is separate from the existing climate
forecasting models. It must not be used for WSH risk classification or real
safety decisions. The target is weather-attributable productive hours lost;
rows without that outcome are not trainable. Real training additionally
requires verified outcome sources.

Train real observations after they pass quality checks:

```powershell
.\.venv\Scripts\python.exe -B -m risk_engine.train_delay_model --dataset real
```

Synthetic demonstration training requires explicit opt-in:

```powershell
.\.venv\Scripts\python.exe -B -m risk_engine.train_delay_model --dataset synthetic --allow-synthetic-demo
```

Synthetic results are labelled `SYNTHETIC_DEMO`, stored separately under
`models/delay_model/synthetic_demo/`, and are not empirical validation. The
training comparison includes a transparent rule baseline, Ridge statistical
baseline, XGBoost, and LightGBM; statistical/tree models are each evaluated
with weather-only and knowledge-graph-enhanced features. Validation is
chronological, grouped by same-date climate event with an embargo. Project
grouped holdout is reported only when enough distinct projects and observations
remain after shared-event purging.

To recompute metrics from stored validation predictions:

```powershell
.\.venv\Scripts\python.exe -B -m risk_engine.evaluate_delay_model
```

Model binaries, feature schemas/preprocessing, validation predictions, and
evaluation metadata are written below `models/delay_model/`. Rainfall
evaluation segments use a configurable 10 mm default; this is only an
evaluation segment, not a WSH or stop-work threshold.
