"""Build auditable activity-day features from NEA and project observations."""

from __future__ import annotations

import csv
from datetime import date, timedelta
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import networkx as nx

from .activity_mapper import build_activity_mapping, get_activity_knowledge
from .graph_loader import DEFAULT_WORKBOOK, load_knowledge_graph

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY_PATH = ROOT / "data" / "climate-history.json"
DEFAULT_STATION_SOURCE_PATH = ROOT / "weather.js"

DATASET_FIELDS = (
    "project_id",
    "project_name",
    "wbs_code",
    "application_activity_id",
    "activity_name",
    "date",
    "nea_station_id",
    "nea_station_name",
    "rainfall_mm",
    "mean_temperature_c",
    "maximum_temperature_c",
    "minimum_temperature_c",
    "recent_cumulative_rainfall_mm",
    "recent_rainfall_window_days",
    "recent_rainfall_window_end",
    "activity_exposure",
    "heat_sensitivity",
    "rain_sensitivity",
    "activity_type",
    "stage",
    "kg_hazard_ids",
    "kg_hazards",
    "kg_consequences",
    "kg_candidate_control_ids",
    "kg_candidate_intervention_ids",
    "kg_evidence_statuses",
    "site_drainage_condition",
    "ground_condition",
    "existing_controls",
    "existing_controls_status",
    "planned_productive_hours",
    "actual_productive_hours",
    "weather_attributable_lost_hours",
    "other_disruption_causes",
    "other_disruption_hours",
    "weather_data_source",
    "outcome_data_source",
    "data_source",
    "weather_evidence_status",
    "outcome_evidence_status",
    "evidence_status",
    "is_synthetic",
)

_STATION_MAPPING_PATTERN = re.compile(
    r"window\.neaHistoricalStationByArea\s*=\s*(\{[\s\S]*?\})\s*;"
)
_SENSITIVITIES = {"low", "medium", "high"}
_EXPOSURES = {"outdoor", "partially sheltered", "indoor", "not confirmed"}


def load_nea_history(
    history_path: str | Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    """Read the bundled station-indexed NEA history without spatial aggregation."""
    path = Path(history_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read NEA history at {path}: {error}") from error
    if payload.get("schemaVersion") != 1 or not isinstance(
        payload.get("stations"), dict
    ):
        raise ValueError("NEA history must use schemaVersion 1 with station data.")

    observations: dict[str, dict[str, tuple[float | None, ...]]] = {}
    for station_id, records in payload["stations"].items():
        if not isinstance(records, list):
            raise ValueError(f"NEA station {station_id!r} must contain a list.")
        daily: dict[str, tuple[float | None, ...]] = {}
        for record in records:
            if not isinstance(record, list) or len(record) != 5:
                raise ValueError(
                    f"NEA station {station_id!r} contains a malformed daily row."
                )
            try:
                observed_date = date.fromisoformat(str(record[0])).isoformat()
            except ValueError as error:
                raise ValueError(
                    f"NEA station {station_id!r} has an invalid date {record[0]!r}."
                ) from error
            if observed_date in daily:
                raise ValueError(
                    f"NEA station {station_id!r} duplicates {observed_date}."
                )
            values: list[float | None] = []
            for value in record[1:]:
                if value is None:
                    values.append(None)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(float(value))
                else:
                    raise ValueError(
                        f"NEA station {station_id!r} has non-numeric weather data."
                    )
            daily[observed_date] = tuple(values)
        observations[str(station_id)] = daily
    return {
        "source": str(payload.get("source", "Unknown NEA source")),
        "source_url": str(payload.get("sourceUrl", "")),
        "station_names": dict(payload.get("stationNames", {})),
        "stations": observations,
    }


def load_station_mapping(
    source_path: str | Path = DEFAULT_STATION_SOURCE_PATH,
) -> dict[str, dict[str, str]]:
    """Read the application's existing area-to-NEA-station mapping."""
    path = Path(source_path).expanduser().resolve()
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Unable to read station mapping at {path}: {error}") from error
    match = _STATION_MAPPING_PATTERN.search(source)
    if match is None:
        raise ValueError(f"Could not find the NEA station mapping in {path}.")
    try:
        mapping = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise ValueError(f"The NEA station mapping in {path} is invalid JSON.") from error
    return {
        str(area): {"code": str(value["code"]), "name": str(value["name"])}
        for area, value in mapping.items()
    }


def resolve_station_id(
    project: Mapping[str, Any],
    station_mapping: Mapping[str, Mapping[str, str]],
) -> str:
    """Resolve a project to its specific station; never substitute a national value."""
    explicit = project.get("nea_station_id") or project.get("station_id")
    if explicit:
        return str(explicit).strip()
    location = str(project.get("location", "")).strip()
    station = station_mapping.get(location)
    if not station:
        raise ValueError(
            f"Project {project.get('project_id', '<unknown>')!r} has no NEA station "
            f"and location {location!r} is not in the application's station mapping."
        )
    return str(station["code"])


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _activity_settings(project: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    activities = project.get("activities", ())
    if not isinstance(activities, Sequence) or isinstance(activities, (str, bytes)):
        raise ValueError("Project activities must be a sequence of activity records.")
    settings: dict[str, Mapping[str, Any]] = {}
    for activity in activities:
        if not isinstance(activity, Mapping):
            raise ValueError("Each project activity must be an object.")
        wbs_code = str(
            activity.get("wbs_code")
            or activity.get("catalogueCode")
            or activity.get("catalogue_code")
            or ""
        ).strip().upper()
        if not wbs_code:
            raise ValueError("Each project activity requires a canonical WBS code.")
        if wbs_code in settings:
            raise ValueError(f"Project repeats activity settings for {wbs_code}.")
        exposure = str(activity.get("exposure", "not confirmed")).strip().lower()
        heat_sensitivity = str(
            activity.get("heatSensitivity", activity.get("heat_sensitivity", ""))
        ).strip().lower()
        rain_sensitivity = str(
            activity.get("rainSensitivity", activity.get("rain_sensitivity", ""))
        ).strip().lower()
        if exposure not in _EXPOSURES:
            raise ValueError(f"{wbs_code}: unsupported exposure {exposure!r}.")
        if heat_sensitivity not in _SENSITIVITIES:
            raise ValueError(f"{wbs_code}: heat sensitivity is missing or invalid.")
        if rain_sensitivity not in _SENSITIVITIES:
            raise ValueError(f"{wbs_code}: rain sensitivity is missing or invalid.")
        settings[wbs_code] = activity
    return settings


def _weather_features(
    station_id: str,
    observed_date: str,
    history: Mapping[str, Any],
) -> dict[str, Any]:
    stations = history["stations"]
    if station_id not in stations:
        raise ValueError(f"NEA history has no station {station_id!r}.")
    station = stations[station_id]
    current = station.get(observed_date, (None, None, None, None))
    if len(current) != 4:
        raise ValueError(f"Weather row for {station_id} on {observed_date} is invalid.")

    day = date.fromisoformat(observed_date)
    prior_values: list[float] = []
    complete_window = True
    for offset in range(1, 8):
        prior_day = (day - timedelta(days=offset)).isoformat()
        prior = station.get(prior_day)
        rainfall = prior[0] if prior is not None else None
        if rainfall is None:
            complete_window = False
        else:
            prior_values.append(rainfall)

    return {
        "rainfall_mm": current[0],
        "mean_temperature_c": current[1],
        "maximum_temperature_c": current[2],
        "minimum_temperature_c": current[3],
        "recent_cumulative_rainfall_mm": (
            sum(prior_values) if complete_window and len(prior_values) == 7 else None
        ),
        "recent_rainfall_window_days": 7,
        "recent_rainfall_window_end": (day - timedelta(days=1)).isoformat(),
    }


def _knowledge_features(
    wbs_code: str,
    graph: nx.MultiDiGraph,
    activity_mapping: Mapping[str, str],
) -> dict[str, Any]:
    knowledge = get_activity_knowledge(wbs_code, graph=graph)
    return {
        "application_activity_id": activity_mapping[wbs_code],
        "activity_name": knowledge["activity"]["label"],
        "activity_type": wbs_code.split("-", maxsplit=1)[0],
        "stage": knowledge["activity"]["record"].get("Stage"),
        "kg_hazard_ids": _json_value([item["id"] for item in knowledge["hazards"]]),
        "kg_hazards": _json_value(
            [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "evidence_status": item["evidence_status"],
                    "source_ids": item["source_ids"],
                }
                for item in knowledge["hazards"]
            ]
        ),
        "kg_consequences": _json_value(
            [
                {
                    "id": item["id"],
                    "kind": item.get("kind", item.get("node_type")),
                    "label": item["label"],
                    "evidence_status": item.get("evidence_status"),
                    "source_ids": item.get("source_ids", []),
                }
                for item in knowledge["consequences"]
            ]
        ),
        "kg_candidate_control_ids": _json_value(
            [item["id"] for item in knowledge["controls"]]
        ),
        "kg_candidate_intervention_ids": _json_value(
            [item["id"] for item in knowledge["interventions"]]
        ),
        "kg_evidence_statuses": _json_value(
            sorted(
                {
                    str(item["evidence_status"])
                    for item in (
                        knowledge["hazards"]
                        + knowledge["consequences"]
                        + knowledge["exposure_conditions"]
                        + knowledge["controls"]
                        + knowledge["interventions"]
                    )
                    if item.get("evidence_status")
                }
            )
        ),
    }


def build_activity_day_dataset(
    projects: Sequence[Mapping[str, Any]],
    activity_observations: Iterable[Mapping[str, Any]],
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    station_mapping_path: str | Path = DEFAULT_STATION_SOURCE_PATH,
    graph: nx.MultiDiGraph | None = None,
    workbook_path: str | Path = DEFAULT_WORKBOOK,
    application_source_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Join supplied activity-day observations to station weather and KG features.

    This function never invents project dates or outcome values. In particular,
    workbook knowledge-graph controls are kept separate from project-reported
    existing controls and are not represented as implemented controls.
    """
    history = load_nea_history(history_path)
    station_mapping = load_station_mapping(station_mapping_path)
    graph = graph if graph is not None else load_knowledge_graph(workbook_path)
    mapping_args: dict[str, Any] = {"workbook_path": workbook_path}
    if application_source_path is not None:
        mapping_args["application_source_path"] = application_source_path
    mapping_report = build_activity_mapping(**mapping_args)
    activity_mapping = mapping_report.mapping

    projects_by_id: dict[str, Mapping[str, Any]] = {}
    settings_by_project: dict[str, dict[str, Mapping[str, Any]]] = {}
    stations_by_project: dict[str, str] = {}
    for project in projects:
        project_id = str(project.get("project_id", "")).strip()
        if not project_id:
            raise ValueError("Each project requires a non-empty project_id.")
        if project_id in projects_by_id:
            raise ValueError(f"Duplicate project_id {project_id!r}.")
        projects_by_id[project_id] = project
        settings_by_project[project_id] = _activity_settings(project)
        station_id = resolve_station_id(project, station_mapping)
        if station_id not in history["stations"]:
            raise ValueError(
                f"Project {project_id!r} maps to station {station_id!r}, "
                "which is absent from the bundled NEA history."
            )
        stations_by_project[project_id] = station_id

    rows: list[dict[str, Any]] = []
    seen_observations: set[tuple[str, str, str]] = set()
    for observation in activity_observations:
        project_id = str(observation.get("project_id", "")).strip()
        project = projects_by_id.get(project_id)
        if project is None:
            raise ValueError(
                f"Activity observation references unknown project {project_id!r}."
            )
        wbs_code = str(observation.get("wbs_code", "")).strip().upper()
        if wbs_code not in activity_mapping:
            raise ValueError(f"Unknown canonical WBS code {wbs_code!r}.")
        activity = settings_by_project[project_id].get(wbs_code)
        if activity is None:
            raise ValueError(
                f"{project_id}: no project activity settings for {wbs_code}."
            )
        observed_date = str(observation.get("date", "")).strip()
        try:
            observed_date = date.fromisoformat(observed_date).isoformat()
        except ValueError as error:
            raise ValueError(
                f"{project_id}/{wbs_code}: invalid observation date {observed_date!r}."
            ) from error
        key = (project_id, wbs_code, observed_date)
        if key in seen_observations:
            raise ValueError(
                f"Duplicate activity-day observation {project_id}/{wbs_code}/{observed_date}."
            )
        seen_observations.add(key)

        station_id = str(
            observation.get("nea_station_id") or stations_by_project[project_id]
        ).strip()
        if station_id not in history["stations"]:
            raise ValueError(f"Observation references unknown NEA station {station_id!r}.")
        settings = project.get("site_conditions", {})
        if not isinstance(settings, Mapping):
            raise ValueError(f"{project_id}: site_conditions must be an object.")
        existing_controls = activity.get("existing_controls")
        existing_controls_status = (
            activity.get("existing_controls_status")
            or ("PROJECT_REPORTED" if existing_controls is not None else "NOT_PROVIDED")
        )
        kg = _knowledge_features(wbs_code, graph, activity_mapping)
        weather = _weather_features(station_id, observed_date, history)

        row = {field: None for field in DATASET_FIELDS}
        row.update(
            {
                "project_id": project_id,
                "project_name": project.get("project_name") or project.get("name"),
                "wbs_code": wbs_code,
                "date": observed_date,
                "nea_station_id": station_id,
                "nea_station_name": history["station_names"].get(station_id),
                "activity_exposure": activity["exposure"],
                "heat_sensitivity": activity.get(
                    "heatSensitivity", activity.get("heat_sensitivity")
                ),
                "rain_sensitivity": activity.get(
                    "rainSensitivity", activity.get("rain_sensitivity")
                ),
                "site_drainage_condition": settings.get("drainage_condition"),
                "ground_condition": settings.get("ground_condition"),
                "existing_controls": _json_value(existing_controls or []),
                "existing_controls_status": existing_controls_status,
                "planned_productive_hours": observation.get(
                    "planned_productive_hours"
                ),
                "actual_productive_hours": observation.get("actual_productive_hours"),
                "weather_attributable_lost_hours": observation.get(
                    "weather_attributable_lost_hours"
                ),
                "other_disruption_causes": _json_value(
                    _json_load(observation.get("other_disruption_causes"), [])
                ),
                "other_disruption_hours": observation.get("other_disruption_hours"),
                "weather_data_source": history["source"],
                "outcome_data_source": observation.get("data_source"),
                "data_source": observation.get("data_source")
                or "user_supplied_activity_observations",
                "weather_evidence_status": "NEA_HISTORICAL_STATION_RECORD",
                "outcome_evidence_status": observation.get(
                    "evidence_status", "SOURCE_UNVERIFIED"
                ),
                "evidence_status": observation.get(
                    "evidence_status", "SOURCE_UNVERIFIED"
                ),
                "is_synthetic": bool(
                    observation.get("is_synthetic")
                    or "SYNTHETIC"
                    in str(observation.get("evidence_status", "")).upper()
                ),
                **kg,
                **weather,
            }
        )
        rows.append(row)
    return sorted(rows, key=lambda row: (row["date"], row["project_id"], row["wbs_code"]))


def load_activity_observations_csv(path: str | Path) -> list[dict[str, Any]]:
    """Read the documented daily actual-outcome CSV without filling missing values."""
    source = Path(path).expanduser().resolve()
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"project_id", "wbs_code", "date"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError(
                    "Activity observations CSV requires project_id, wbs_code, and date."
                )
            rows: list[dict[str, Any]] = []
            numeric_fields = (
                "planned_productive_hours",
                "actual_productive_hours",
                "weather_attributable_lost_hours",
                "other_disruption_hours",
            )
            for row in reader:
                parsed: dict[str, Any] = dict(row)
                for field in numeric_fields:
                    value = row.get(field)
                    parsed[field] = (
                        None if value is None or not value.strip() else float(value)
                    )
                rows.append(parsed)
            return rows
    except OSError as error:
        raise ValueError(f"Unable to read activity observations at {source}: {error}") from error


def write_dataset_csv(
    rows: Iterable[Mapping[str, Any]],
    path: str | Path,
) -> None:
    """Write rows using a stable, JSON-compatible CSV representation."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=DATASET_FIELDS, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        _json_value(row[field])
                        if isinstance(row.get(field), (dict, list, tuple))
                        else row.get(field)
                    )
                    for field in DATASET_FIELDS
                }
            )


def temporal_train_validation_split(
    rows: Sequence[Mapping[str, Any]],
    validation_fraction: float = 0.2,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split by distinct dates so validation never precedes training data."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be greater than 0 and less than 1.")
    dates = sorted({str(row["date"]) for row in rows})
    if len(dates) < 2:
        raise ValueError("At least two distinct dates are needed for a temporal split.")
    validation_start_index = max(
        1, min(len(dates) - 1, int(len(dates) * (1 - validation_fraction)))
    )
    validation_start = dates[validation_start_index]
    training = [row for row in rows if str(row["date"]) < validation_start]
    validation = [row for row in rows if str(row["date"]) >= validation_start]
    if not training or not validation:
        raise ValueError("Temporal split produced an empty training or validation set.")
    return training, validation


__all__ = [
    "DATASET_FIELDS",
    "DEFAULT_HISTORY_PATH",
    "DEFAULT_STATION_SOURCE_PATH",
    "build_activity_day_dataset",
    "load_activity_observations_csv",
    "load_nea_history",
    "load_station_mapping",
    "resolve_station_id",
    "temporal_train_validation_split",
    "write_dataset_csv",
]
