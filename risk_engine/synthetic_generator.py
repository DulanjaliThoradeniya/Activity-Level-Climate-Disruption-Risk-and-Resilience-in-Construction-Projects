"""Generate labelled demonstration data, never empirical productivity evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

from .activity_mapper import build_activity_mapping
from .feature_builder import (
    DEFAULT_HISTORY_PATH,
    DEFAULT_STATION_SOURCE_PATH,
    build_activity_day_dataset,
    load_nea_history,
    load_station_mapping,
    write_dataset_csv,
)
from .graph_loader import DEFAULT_WORKBOOK, load_knowledge_graph

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = ROOT / "data" / "activity_observations" / "synthetic_demonstration.csv"
DEFAULT_ASSUMPTIONS_PATH = (
    ROOT / "data" / "activity_observations" / "synthetic_assumptions.json"
)


@dataclass(frozen=True)
class SyntheticAssumptions:
    project_id: str = "DEMO-SYNTHETIC-001"
    project_name: str = "Synthetic demonstration project (not a real project)"
    location: str = "Changi"
    start_date: str = "2025-01-01"
    working_weekdays: tuple[int, ...] = (1, 2, 3, 4, 5)
    holidays: tuple[str, ...] = ()
    activity_duration_workdays: int = 5
    planned_productive_hours_per_day: float = 8.0
    default_exposure: str = "outdoor"
    default_heat_sensitivity: str = "low"
    default_rain_sensitivity: str = "medium"
    rainfall_threshold_mm: float = 5.0
    rain_lost_hours_per_excess_mm: float = 0.04
    heat_threshold_mean_temperature_c: float = 32.0
    heat_lost_hours_per_excess_degree: float = 0.2
    sensitivity_multipliers: dict[str, float] = field(
        default_factory=lambda: {"low": 0.5, "medium": 1.0, "high": 1.5}
    )
    exposure_multipliers: dict[str, float] = field(
        default_factory=lambda: {
            "outdoor": 1.0,
            "partially sheltered": 0.5,
            "indoor": 0.0,
        }
    )
    other_disruption_hours_per_day: float = 0.0
    other_disruption_assumption: str = (
        "No non-weather disruption is modelled; assumed 0 hours for demonstration."
    )
    schedule_assumption: str = (
        "All 69 canonical WBS activities are scheduled sequentially in catalogue "
        "order for the configured number of weekdays; no dependencies, holidays "
        "other than the explicit list, or parallel work are inferred."
    )
    productivity_assumption: str = (
        "Synthetic lost hours are deterministic functions of same-day observed "
        "station rainfall and mean temperature, activity sensitivity, and exposure. "
        "They are not historical measurements, causal estimates, or validated effects."
    )


def _next_workday(day: date, weekdays: set[int], holidays: set[str]) -> date:
    current = day
    while current.isoweekday() not in weekdays or current.isoformat() in holidays:
        current += timedelta(days=1)
    return current


def build_synthetic_dataset(
    assumptions: SyntheticAssumptions = SyntheticAssumptions(),
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    station_mapping_path: str | Path = DEFAULT_STATION_SOURCE_PATH,
    workbook_path: str | Path = DEFAULT_WORKBOOK,
) -> list[dict[str, Any]]:
    """Create one synthetic project with all canonical WBS activities."""
    if assumptions.activity_duration_workdays < 1:
        raise ValueError("activity_duration_workdays must be at least one.")
    if assumptions.planned_productive_hours_per_day <= 0:
        raise ValueError("planned productive hours must be greater than zero.")
    if not assumptions.working_weekdays or any(
        day not in range(1, 8) for day in assumptions.working_weekdays
    ):
        raise ValueError("working_weekdays must contain ISO weekday numbers 1 to 7.")
    start = date.fromisoformat(assumptions.start_date)
    mapping = load_station_mapping(station_mapping_path)
    history = load_nea_history(history_path)
    station = mapping.get(assumptions.location)
    if station is None:
        raise ValueError(f"Unknown project location {assumptions.location!r}.")
    station_id = station["code"]
    if station_id not in history["stations"]:
        raise ValueError(
            f"NEA station {station_id!r} for {assumptions.location} is unavailable."
        )

    activity_mapping = build_activity_mapping(workbook_path=workbook_path)
    codes = list(activity_mapping.entries)
    if len(codes) != 69 or activity_mapping.missing_codes:
        raise ValueError(
            "Synthetic generation requires all 69 canonical activity mappings; "
            f"found {len(codes)} mapped and {len(activity_mapping.missing_codes)} missing."
        )
    project_activities = [
        {
            "wbs_code": code,
            "exposure": assumptions.default_exposure,
            "heatSensitivity": assumptions.default_heat_sensitivity,
            "rainSensitivity": assumptions.default_rain_sensitivity,
            "existing_controls": [],
            "existing_controls_status": "NOT_PROVIDED",
        }
        for code in codes
    ]
    project = {
        "project_id": assumptions.project_id,
        "project_name": assumptions.project_name,
        "location": assumptions.location,
        "nea_station_id": station_id,
        "site_conditions": {
            "drainage_condition": None,
            "ground_condition": None,
        },
        "activities": project_activities,
    }

    weekdays = set(assumptions.working_weekdays)
    holidays = set(assumptions.holidays)
    date_cursor = _next_workday(start, weekdays, holidays)
    observations: list[dict[str, Any]] = []
    weather_by_date = history["stations"][station_id]
    for activity in project_activities:
        wbs_code = activity["wbs_code"]
        for _ in range(assumptions.activity_duration_workdays):
            current_date = _next_workday(date_cursor, weekdays, holidays)
            day_string = current_date.isoformat()
            weather = weather_by_date.get(day_string, (None, None, None, None))
            rainfall, mean_temperature = weather[:2]
            exposure_multiplier = assumptions.exposure_multipliers.get(
                activity["exposure"]
            )
            if (
                rainfall is None
                or mean_temperature is None
                or exposure_multiplier is None
            ):
                weather_loss: float | None = None
                actual_hours: float | None = None
            else:
                rain_factor = assumptions.sensitivity_multipliers[
                    activity["rainSensitivity"]
                ]
                heat_factor = assumptions.sensitivity_multipliers[
                    activity["heatSensitivity"]
                ]
                rain_loss = (
                    max(0.0, rainfall - assumptions.rainfall_threshold_mm)
                    * assumptions.rain_lost_hours_per_excess_mm
                    * rain_factor
                )
                heat_loss = (
                    max(
                        0.0,
                        mean_temperature
                        - assumptions.heat_threshold_mean_temperature_c,
                    )
                    * assumptions.heat_lost_hours_per_excess_degree
                    * heat_factor
                )
                weather_loss = min(
                    assumptions.planned_productive_hours_per_day,
                    exposure_multiplier * (rain_loss + heat_loss),
                )
                actual_hours = max(
                    0.0,
                    assumptions.planned_productive_hours_per_day
                    - weather_loss
                    - assumptions.other_disruption_hours_per_day,
                )

            observations.append(
                {
                    "project_id": assumptions.project_id,
                    "wbs_code": wbs_code,
                    "date": day_string,
                    "nea_station_id": station_id,
                    "planned_productive_hours": (
                        assumptions.planned_productive_hours_per_day
                    ),
                    "actual_productive_hours": actual_hours,
                    "weather_attributable_lost_hours": weather_loss,
                    "other_disruption_causes": [
                        assumptions.other_disruption_assumption
                    ],
                    "other_disruption_hours": (
                        assumptions.other_disruption_hours_per_day
                    ),
                    "data_source": (
                        "Synthetic assumptions applied to NEA station-level "
                        "historical weather"
                    ),
                    "evidence_status": "SYNTHETIC_DEMONSTRATION_NOT_EMPIRICAL",
                    "is_synthetic": True,
                }
            )
            date_cursor = current_date + timedelta(days=1)

    graph = load_knowledge_graph(workbook_path)
    return build_activity_day_dataset(
        [project],
        observations,
        history_path=history_path,
        station_mapping_path=station_mapping_path,
        graph=graph,
        workbook_path=workbook_path,
    )


def write_synthetic_dataset(
    assumptions: SyntheticAssumptions = SyntheticAssumptions(),
    *,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    assumptions_path: str | Path = DEFAULT_ASSUMPTIONS_PATH,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    station_mapping_path: str | Path = DEFAULT_STATION_SOURCE_PATH,
    workbook_path: str | Path = DEFAULT_WORKBOOK,
) -> int:
    rows = build_synthetic_dataset(
        assumptions,
        history_path=history_path,
        station_mapping_path=station_mapping_path,
        workbook_path=workbook_path,
    )
    write_dataset_csv(rows, output_path)
    assumptions_target = Path(assumptions_path).expanduser().resolve()
    assumptions_target.parent.mkdir(parents=True, exist_ok=True)
    assumptions_target.write_text(
        json.dumps(
            {
                "dataset_type": "synthetic_demonstration",
                "empirical_observations": False,
                "training_eligible": False,
                "assumptions": asdict(assumptions),
            },
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate explicitly synthetic activity-day demonstration data."
    )
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--location", default="Changi")
    parser.add_argument("--activity-days", type=int, default=5)
    parser.add_argument("--work-hours", type=float, default=8.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--assumptions", type=Path, default=DEFAULT_ASSUMPTIONS_PATH)
    args = parser.parse_args()
    assumptions = SyntheticAssumptions(
        start_date=args.start_date,
        location=args.location,
        activity_duration_workdays=args.activity_days,
        planned_productive_hours_per_day=args.work_hours,
    )
    count = write_synthetic_dataset(
        assumptions,
        output_path=args.output,
        assumptions_path=args.assumptions,
    )
    print(
        f"Wrote {count} SYNTHETIC demonstration activity-days to {args.output}. "
        "These outcomes are not empirical observations and are not training-ready."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
