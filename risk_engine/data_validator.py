"""Validate activity-day datasets and report missingness, provenance, and leakage."""

from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .activity_mapper import build_activity_mapping
from .feature_builder import (
    DATASET_FIELDS,
    DEFAULT_HISTORY_PATH,
    DEFAULT_STATION_SOURCE_PATH,
    load_nea_history,
    load_station_mapping,
)

ROOT = Path(__file__).resolve().parents[1]
OBSERVATION_DIR = ROOT / "data" / "activity_observations"
DEFAULT_REAL_PATH = OBSERVATION_DIR / "real_activity_observations.csv"
DEFAULT_SYNTHETIC_PATH = OBSERVATION_DIR / "synthetic_demonstration.csv"
DEFAULT_REPORT_PATH = OBSERVATION_DIR / "quality_report.json"
_NUMERIC_FIELDS = (
    "rainfall_mm",
    "mean_temperature_c",
    "maximum_temperature_c",
    "minimum_temperature_c",
    "recent_cumulative_rainfall_mm",
    "planned_productive_hours",
    "actual_productive_hours",
    "weather_attributable_lost_hours",
    "other_disruption_hours",
)


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_dataset(
    rows: Iterable[Mapping[str, Any]],
    dataset_type: str,
    *,
    known_wbs_codes: set[str] | None = None,
    known_stations: set[str] | None = None,
) -> dict[str, Any]:
    """Return a quality report without coercing, imputing, or relabelling data."""
    if dataset_type not in {"real", "synthetic"}:
        raise ValueError("dataset_type must be 'real' or 'synthetic'.")
    records = list(rows)
    known_wbs_codes = known_wbs_codes or set()
    known_stations = known_stations or set()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    null_counts = {field: 0 for field in DATASET_FIELDS}
    seen: set[tuple[str, str, str]] = set()
    dates: list[str] = []
    synthetic_row_count = 0

    for index, row in enumerate(records, start=2):
        missing_fields = [field for field in DATASET_FIELDS if field not in row]
        if missing_fields:
            errors.append(
                {
                    "issue": "missing_columns",
                    "row": index,
                    "fields": missing_fields,
                }
            )
            continue
        for field in DATASET_FIELDS:
            if row.get(field) is None or row.get(field) == "":
                null_counts[field] += 1
        project_id = str(row.get("project_id") or "").strip()
        wbs_code = str(row.get("wbs_code") or "").strip().upper()
        observed_date = str(row.get("date") or "").strip()
        station_id = str(row.get("nea_station_id") or "").strip()
        try:
            parsed_date = date.fromisoformat(observed_date)
            observed_date = parsed_date.isoformat()
        except ValueError:
            errors.append({"issue": "invalid_date", "row": index, "value": observed_date})
            continue
        dates.append(observed_date)
        key = (project_id, wbs_code, observed_date)
        if key in seen:
            errors.append({"issue": "duplicate_project_activity_day", "row": index})
        seen.add(key)
        if not project_id:
            errors.append({"issue": "missing_project_id", "row": index})
        if known_wbs_codes and wbs_code not in known_wbs_codes:
            errors.append(
                {"issue": "unknown_wbs_code", "row": index, "wbs_code": wbs_code}
            )
        if known_stations and station_id not in known_stations:
            errors.append(
                {"issue": "unknown_nea_station", "row": index, "station_id": station_id}
            )
        if row.get("recent_rainfall_window_end") is not None:
            window_end = str(row["recent_rainfall_window_end"])
            if window_end >= observed_date:
                errors.append(
                    {
                        "issue": "recent_rainfall_uses_current_or_future_date",
                        "row": index,
                        "window_end": window_end,
                        "date": observed_date,
                    }
                )
        synthetic = row.get("is_synthetic")
        is_synthetic = (
            synthetic is True
            or str(synthetic).strip().lower() in {"true", "1", "yes"}
        )
        synthetic_row_count += int(is_synthetic)
        evidence = str(row.get("evidence_status") or "").upper()
        if dataset_type == "synthetic":
            if not is_synthetic:
                errors.append(
                    {"issue": "synthetic_dataset_contains_non_synthetic_row", "row": index}
                )
            if "SYNTHETIC" not in evidence:
                errors.append(
                    {"issue": "synthetic_row_not_labelled", "row": index}
                )
        elif is_synthetic or "SYNTHETIC" in evidence:
            errors.append(
                {"issue": "real_dataset_contains_synthetic_row", "row": index}
            )

        planned = _as_number(row.get("planned_productive_hours"))
        actual = _as_number(row.get("actual_productive_hours"))
        weather_loss = _as_number(row.get("weather_attributable_lost_hours"))
        other_loss = _as_number(row.get("other_disruption_hours"))
        for field in _NUMERIC_FIELDS:
            value = row.get(field)
            if value not in (None, "") and _as_number(value) is None:
                errors.append(
                    {"issue": "non_numeric_value", "row": index, "field": field}
                )
            number = _as_number(value)
            if number is not None and field not in {
                "mean_temperature_c",
                "maximum_temperature_c",
                "minimum_temperature_c",
            } and number < 0:
                errors.append(
                    {"issue": "negative_value", "row": index, "field": field}
                )
        if planned is not None:
            if actual is not None and actual > planned:
                errors.append(
                    {"issue": "actual_hours_exceed_planned", "row": index}
                )
            if weather_loss is not None and weather_loss > planned:
                errors.append(
                    {"issue": "weather_lost_hours_exceed_planned", "row": index}
                )
            if other_loss is not None and other_loss > planned:
                errors.append(
                    {"issue": "other_lost_hours_exceed_planned", "row": index}
                )
        if dataset_type == "real" and actual is None:
            warnings.append(
                {"issue": "missing_actual_productive_hours", "row": index}
            )
        if dataset_type == "real" and not row.get("outcome_data_source"):
            warnings.append(
                {"issue": "missing_outcome_source", "row": index}
            )
        if dataset_type == "real" and str(
            row.get("outcome_evidence_status") or ""
        ).upper() in {"", "SOURCE_UNVERIFIED", "UNVERIFIED", "UNKNOWN"}:
            warnings.append(
                {"issue": "outcome_evidence_requires_review", "row": index}
            )
        if row.get("rainfall_mm") in (None, ""):
            warnings.append({"issue": "missing_station_rainfall", "row": index})
        if row.get("mean_temperature_c") in (None, ""):
            warnings.append({"issue": "missing_station_temperature", "row": index})

    if not records:
        warnings.append(
            {
                "issue": "no_activity_day_observations",
                "message": (
                    "No project-specific daily productivity records were supplied; "
                    "the empty real dataset is a schema placeholder only."
                ),
            }
        )
    verified_outcomes = bool(records) and all(
        bool(row.get("outcome_data_source"))
        and str(row.get("outcome_evidence_status") or "").upper()
        in {"VERIFIED", "AUDITED", "PRIMARY_SOURCE_VERIFIED"}
        and _as_number(row.get("actual_productive_hours")) is not None
        for row in records
    )
    status = (
        "NO_DATA"
        if not records and dataset_type == "real"
        else ("VALID" if not errors else "INVALID")
    )
    return {
        "dataset_type": dataset_type,
        "status": status,
        "row_count": len(records),
        "date_range": {
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        },
        "project_count": len(
            {str(row.get("project_id")) for row in records if row.get("project_id")}
        ),
        "activity_count": len(
            {str(row.get("wbs_code")) for row in records if row.get("wbs_code")}
        ),
        "station_ids": sorted(
            {str(row.get("nea_station_id")) for row in records if row.get("nea_station_id")}
        ),
        "missing_values": null_counts,
        "synthetic_rows": synthetic_row_count,
        "empirical_observations": dataset_type == "real" and verified_outcomes,
        "training_eligible": (
            dataset_type == "real"
            and bool(records)
            and not errors
            and not any(
                _as_number(row.get("actual_productive_hours")) is None
                for row in records
            )
            and verified_outcomes
        ),
        "errors": errors,
        "warnings": warnings,
    }


def _read_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_quality_report(
    real_path: str | Path = DEFAULT_REAL_PATH,
    synthetic_path: str | Path = DEFAULT_SYNTHETIC_PATH,
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    station_mapping_path: str | Path = DEFAULT_STATION_SOURCE_PATH,
) -> dict[str, Any]:
    mapping = build_activity_mapping()
    history = load_nea_history(history_path)
    stations = set(history["stations"])
    station_mapping = load_station_mapping(station_mapping_path)
    real_file = Path(real_path).expanduser().resolve()
    synthetic_file = Path(synthetic_path).expanduser().resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_from": {
            "nea_history": str(Path(history_path).resolve()),
            "activity_catalogue": "existing application WBS catalogue",
            "knowledge_graph": "knowledge_graph/Knowledge Graph Project.xlsx",
        },
        "canonical_activity_mapping_count": len(mapping.mapping),
        "station_mapping_count": len(station_mapping),
        "datasets": {},
        "training_started": False,
    }
    for label, path, dataset_type in (
        ("real", real_file, "real"),
        ("synthetic", synthetic_file, "synthetic"),
    ):
        if not path.is_file():
            rows: list[dict[str, Any]] = []
            dataset_report = {
                "dataset_type": dataset_type,
                "status": "MISSING_FILE",
                "row_count": 0,
                "errors": [{"issue": "dataset_file_missing", "path": str(path)}],
                "warnings": [],
                "training_eligible": False,
            }
        else:
            rows = _read_dataset(path)
            dataset_report = validate_dataset(
                rows,
                dataset_type,
                known_wbs_codes=set(mapping.mapping),
                known_stations=stations,
            )
            dataset_report["path"] = str(path)
        report["datasets"][label] = dataset_report
    report["training_eligible"] = bool(
        report["datasets"]["real"].get("training_eligible")
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate activity-day datasets and print their quality report."
    )
    parser.add_argument("--real", type=Path, default=DEFAULT_REAL_PATH)
    parser.add_argument("--synthetic", type=Path, default=DEFAULT_SYNTHETIC_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    report = build_quality_report(args.real, args.synthetic)
    target = args.report.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    for name, result in report["datasets"].items():
        print(
            f"{name}: {result['status']} - {result['row_count']} rows, "
            f"{len(result.get('errors', []))} errors, "
            f"{len(result.get('warnings', []))} warnings, "
            f"training eligible={result['training_eligible']}"
        )
    print(f"Quality report: {target}")
    print("Model training was not started.")
    return 1 if any(
        result["status"] in {"INVALID", "MISSING_FILE"}
        for result in report["datasets"].values()
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_quality_report", "validate_dataset"]
