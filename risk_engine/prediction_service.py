"""Inference service shared by the FastAPI endpoint and static bundle builder."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np

from .activity_mapper import build_activity_mapping
from .delay_model import clip_lost_hours
from .feature_builder import (
    DEFAULT_HISTORY_PATH,
    DEFAULT_STATION_SOURCE_PATH,
    _knowledge_features,
    load_nea_history,
    load_station_mapping,
)
from .graph_loader import DEFAULT_WORKBOOK, load_knowledge_graph
from .train_delay_model import DEFAULT_OUTPUT_DIR

DEFAULT_STATIC_BUNDLE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "activity-disruption-predictions.json"
)

FORECAST_FIELDS = (
    "rainfall_mm",
    "mean_temperature_c",
    "maximum_temperature_c",
    "minimum_temperature_c",
    "recent_cumulative_rainfall_mm",
    "recent_rainfall_window_days",
)


def _number(value: Any, field: str, *, optional: bool = True) -> float | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"{field} is required.")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a finite number.") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number.")
    return result


def _safe_id(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} must be non-empty.")
    return result


def canonical_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only predictor inputs so static bundles can be safely matched."""
    project_id = _safe_id(request.get("project_id"), "project_id")
    station_id = _safe_id(request.get("nea_station_id"), "nea_station_id")
    raw_activities = request.get("activities")
    raw_days = request.get("forecast_days")
    if not isinstance(raw_activities, list) or not raw_activities:
        raise ValueError("activities must be a non-empty array.")
    if not isinstance(raw_days, list) or not raw_days:
        raise ValueError("forecast_days must be a non-empty array.")
    work_hours_per_day = _number(
        request.get("work_hours_per_day", 8.0),
        "work_hours_per_day",
        optional=False,
    )
    if work_hours_per_day is None or not 0 < work_hours_per_day <= 24:
        raise ValueError("work_hours_per_day must be greater than 0 and no more than 24.")

    activities: list[dict[str, Any]] = []
    activity_ids: set[str] = set()
    for raw in raw_activities:
        if not isinstance(raw, Mapping):
            raise ValueError("Each activity must be an object.")
        activity_id = _safe_id(raw.get("id"), "activity.id")
        wbs_code = _safe_id(
            raw.get("wbs_code") or raw.get("catalogueCode"),
            f"{activity_id}.wbs_code",
        ).upper()
        if activity_id in activity_ids:
            raise ValueError(f"Duplicate schedule activity id {activity_id!r}.")
        activity_ids.add(activity_id)
        exposure = str(raw.get("exposure", "not confirmed")).strip().lower()
        heat_sensitivity = str(
            raw.get("heat_sensitivity", raw.get("heatSensitivity", "medium"))
        ).strip().lower()
        rain_sensitivity = str(
            raw.get("rain_sensitivity", raw.get("rainSensitivity", "medium"))
        ).strip().lower()
        if exposure not in {
            "outdoor",
            "partially sheltered",
            "indoor",
            "not confirmed",
        }:
            raise ValueError(f"{activity_id}: invalid activity exposure.")
        if heat_sensitivity not in {"low", "medium", "high"}:
            raise ValueError(f"{activity_id}: invalid heat sensitivity.")
        if rain_sensitivity not in {"low", "medium", "high"}:
            raise ValueError(f"{activity_id}: invalid rainfall sensitivity.")
        existing_controls = raw.get("existing_controls", [])
        if isinstance(existing_controls, str):
            try:
                existing_controls = json.loads(existing_controls)
            except json.JSONDecodeError:
                existing_controls = [existing_controls]
        if not isinstance(existing_controls, list):
            raise ValueError(f"{activity_id}: existing_controls must be an array.")
        planned_hours = _number(
            raw.get("planned_productive_hours", work_hours_per_day),
            f"{activity_id}.planned_productive_hours",
            optional=False,
        )
        if planned_hours is None or not 0 < planned_hours <= 24:
            raise ValueError(
                f"{activity_id}.planned_productive_hours must be greater than 0 and no more than 24."
            )
        raw_stop_dates = raw.get("mandatory_stop_work_dates", [])
        if not isinstance(raw_stop_dates, list):
            raise ValueError(
                f"{activity_id}.mandatory_stop_work_dates must be an array."
            )
        try:
            mandatory_stop_dates = sorted(
                {
                    date.fromisoformat(str(item)).isoformat()
                    for item in raw_stop_dates
                }
            )
        except ValueError as error:
            raise ValueError(
                f"{activity_id}.mandatory_stop_work_dates contains an invalid date."
            ) from error
        activities.append(
            {
                "id": activity_id,
                "wbs_code": wbs_code,
                "planned_productive_hours": planned_hours,
                "exposure": exposure,
                "heat_sensitivity": heat_sensitivity,
                "rain_sensitivity": rain_sensitivity,
                "stage": str(raw.get("stage") or ""),
                "site_drainage_condition": raw.get("site_drainage_condition"),
                "ground_condition": raw.get("ground_condition"),
                "existing_controls": json.dumps(
                    existing_controls, ensure_ascii=True, sort_keys=True
                ),
                "existing_controls_status": str(
                    raw.get("existing_controls_status") or "NOT_PROVIDED"
                ),
                "mandatory_stop_work_dates": mandatory_stop_dates,
            }
        )
    activities.sort(key=lambda activity: (activity["wbs_code"], activity["id"]))

    days: list[dict[str, Any]] = []
    dates: set[str] = set()
    for raw in raw_days:
        if not isinstance(raw, Mapping):
            raise ValueError("Each forecast day must be an object.")
        day = date.fromisoformat(str(raw.get("date", ""))).isoformat()
        if day in dates:
            raise ValueError(f"Duplicate forecast date {day!r}.")
        dates.add(day)
        values = {field: _number(raw.get(field), field) for field in FORECAST_FIELDS}
        for field in ("rainfall_mm", "recent_cumulative_rainfall_mm"):
            if values[field] is not None and values[field] < 0:
                raise ValueError(f"{field} cannot be negative.")
        if values["rainfall_mm"] is None or values["mean_temperature_c"] is None:
            raise ValueError(f"{day}: rainfall and mean temperature are required.")
        days.append(
            {
                "date": day,
                **values,
                "forecast_source": str(raw.get("forecast_source") or "UNKNOWN"),
                "forecast_model_by_target": dict(
                    raw.get("forecast_model_by_target") or {}
                ),
                "forecast_uncertainty": dict(raw.get("forecast_uncertainty") or {}),
            }
        )
    days.sort(key=lambda item: item["date"])
    return {
        "schema_version": 1,
        "project_id": project_id,
        "nea_station_id": station_id,
        "location": str(request.get("location") or ""),
        "work_hours_per_day": work_hours_per_day,
        "activities": activities,
        "forecast_days": days,
        "approved_stop_work_source": str(
            request.get("approved_stop_work_source") or ""
        ),
    }


def request_fingerprint(request: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        canonical_request(request),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class ActivityDisruptionPredictor:
    """Load a chosen downstream delay model and predict date-specific work loss."""

    def __init__(
        self,
        *,
        model_root: str | Path = DEFAULT_OUTPUT_DIR,
        profile: str = "real_research",
        allow_synthetic_demo: bool = False,
        workbook_path: str | Path = DEFAULT_WORKBOOK,
        application_source_path: str | Path | None = None,
        history_path: str | Path = DEFAULT_HISTORY_PATH,
        station_mapping_path: str | Path = DEFAULT_STATION_SOURCE_PATH,
    ) -> None:
        self.model_root = Path(model_root).expanduser().resolve()
        self.profile = profile
        self.allow_synthetic_demo = allow_synthetic_demo
        self.workbook_path = workbook_path
        self.application_source_path = application_source_path
        self.history = load_nea_history(history_path)
        self.station_mapping = load_station_mapping(station_mapping_path)
        self.graph = load_knowledge_graph(workbook_path)
        mapping_args: dict[str, Any] = {"workbook_path": workbook_path}
        if application_source_path is not None:
            mapping_args["application_source_path"] = application_source_path
        self.activity_mapping = build_activity_mapping(**mapping_args).mapping

        profile_dir = self.model_root / profile
        if not profile_dir.is_dir():
            raise FileNotFoundError(f"Trained model profile does not exist: {profile_dir}")
        if profile == "synthetic_demo" and not allow_synthetic_demo:
            raise PermissionError(
                "Synthetic demonstration models require explicit allow_synthetic_demo=True."
            )
        metadata_path = profile_dir / "evaluation_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("model_profile") == "SYNTHETIC_DEMO" and not allow_synthetic_demo:
            raise PermissionError("SYNTHETIC_DEMO is disabled for this predictor.")
        if profile == "real_research" and metadata.get("model_profile") != "REAL_RESEARCH":
            raise ValueError("Real profile directory contains non-real model metadata.")
        if profile == "synthetic_demo" and metadata.get("model_profile") != "SYNTHETIC_DEMO":
            raise ValueError("Synthetic profile directory contains mismatched metadata.")
        candidates = [
            (model_id, result)
            for model_id, result in metadata["model_comparison"].items()
            if model_id.endswith("__kg_enhanced")
            and (profile_dir / f"{model_id}.joblib").is_file()
            and result.get("overall", {}).get("mae") is not None
            and (profile == "synthetic_demo" or result.get("evaluation_profile") == "REAL_RESEARCH")
        ]
        if not candidates:
            raise ValueError("No evaluated KG-enhanced model is available for inference.")
        self.model_id, selected_metrics = min(
            candidates, key=lambda candidate: candidate[1]["overall"]["mae"]
        )
        self.validation_mae = float(selected_metrics["overall"]["mae"])
        artifact_path = profile_dir / f"{self.model_id}.joblib"
        artifact = joblib.load(artifact_path)
        if artifact.get("profile") != metadata["model_profile"]:
            raise ValueError("Model artifact profile does not match evaluation metadata.")
        self.model_profile = str(artifact["profile"])
        self.estimator = artifact["estimator"]
        self.encoder = artifact["encoder"]
        self.interval_half_width = float(
            artifact.get("prediction_interval_half_width_hours") or 0
        )
        if not math.isfinite(self.interval_half_width):
            raise ValueError("Saved model uncertainty is invalid.")

    def _station(self, request: Mapping[str, Any]) -> str:
        canonical = canonical_request(request)
        station_id = canonical["nea_station_id"]
        if station_id not in self.history["stations"]:
            raise ValueError(f"NEA station {station_id!r} is not in bundled history.")
        location = canonical["location"]
        if location:
            mapping = self.station_mapping.get(location)
            if not mapping or mapping["code"] != station_id:
                raise ValueError(
                    f"Project location {location!r} does not match NEA station {station_id!r}."
                )
        return station_id

    def predict(self, request: Mapping[str, Any]) -> dict[str, Any]:
        canonical = canonical_request(request)
        station_id = self._station(canonical)

        feature_rows: list[dict[str, Any]] = []
        index: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for activity in canonical["activities"]:
            code = activity["wbs_code"]
            if code not in self.activity_mapping:
                raise ValueError(f"Unknown canonical WBS activity {code!r}.")
            kg = _knowledge_features(
                code, self.graph, self.activity_mapping
            )
            consequences = json.loads(kg["kg_consequences"])
            for forecast in canonical["forecast_days"]:
                row = {
                    "nea_station_id": station_id,
                    "rainfall_mm": forecast["rainfall_mm"],
                    "mean_temperature_c": forecast["mean_temperature_c"],
                    "maximum_temperature_c": forecast["maximum_temperature_c"],
                    "minimum_temperature_c": forecast["minimum_temperature_c"],
                    "recent_cumulative_rainfall_mm": forecast[
                        "recent_cumulative_rainfall_mm"
                    ],
                    "recent_rainfall_window_days": forecast[
                        "recent_rainfall_window_days"
                    ],
                    "planned_productive_hours": activity[
                        "planned_productive_hours"
                    ],
                    "wbs_code": code,
                    "activity_exposure": activity["exposure"],
                    "heat_sensitivity": activity["heat_sensitivity"],
                    "rain_sensitivity": activity["rain_sensitivity"],
                    "activity_type": kg["activity_type"],
                    "stage": kg["stage"],
                    "site_drainage_condition": activity["site_drainage_condition"],
                    "ground_condition": activity["ground_condition"],
                    "existing_controls_status": activity["existing_controls_status"],
                    "existing_controls": activity["existing_controls"],
                    "kg_hazard_ids": kg["kg_hazard_ids"],
                    "kg_consequences": kg["kg_consequences"],
                    "kg_consequence_kinds": "|".join(
                        sorted({str(item.get("kind")) for item in consequences if item.get("kind")})
                    ),
                    "kg_consequence_labels": "|".join(
                        sorted({str(item.get("label")) for item in consequences if item.get("label")})
                    ),
                    "kg_candidate_control_ids": kg["kg_candidate_control_ids"],
                    "kg_candidate_intervention_ids": kg[
                        "kg_candidate_intervention_ids"
                    ],
                    "kg_evidence_statuses": kg["kg_evidence_statuses"],
                }
                feature_rows.append(row)
                index.append((activity, forecast))
        matrix = self.encoder.transform(feature_rows)
        raw_predictions = np.asarray(self.estimator.predict(matrix), dtype=float)
        planned_rows = feature_rows
        lost_hours = clip_lost_hours(raw_predictions, planned_rows)
        profiles: dict[str, list[dict[str, Any]]] = {
            activity["id"]: [] for activity in canonical["activities"]
        }
        for (activity, forecast), lost in zip(index, lost_hours):
            day = forecast["date"]
            planned_hours = float(activity["planned_productive_hours"])
            lower = max(0.0, min(planned_hours, float(lost) - self.interval_half_width))
            upper = min(planned_hours, float(lost) + self.interval_half_width)
            mandatory_stop = day in activity["mandatory_stop_work_dates"]
            profile = {
                "date": day,
                "productivityFactor": 0.0
                if mandatory_stop
                else max(0.0, min(1.0, 1.0 - float(lost) / planned_hours)),
                "predictedLostHours": float(lost),
                "uncertainty": {
                    "lowerLostHours": lower,
                    "upperLostHours": upper,
                    "method": "held-out validation absolute residual q90",
                },
                "modelStatus": self.model_profile,
                "modelId": self.model_id,
                "forecastSource": forecast["forecast_source"],
                "forecastModelByTarget": forecast["forecast_model_by_target"],
                "mandatoryStopWork": mandatory_stop,
                "wshClassification": "NOT_ASSESSED_BY_PRODUCTIVITY_MODEL",
            }
            profiles[activity["id"]].append(profile)

        return {
            "schema_version": 1,
            "status": "AVAILABLE",
            "project_id": canonical["project_id"],
            "location": canonical["location"],
            "nea_station_id": station_id,
            "model_status": self.model_profile,
            "model_id": self.model_id,
            "evaluation_mae_hours": self.validation_mae,
            "uncertainty_hours": self.interval_half_width,
            "real_safety_decision_use_permitted": False,
            "wsh_risk_classification_included": False,
            "request_fingerprint": request_fingerprint(canonical),
            "bundle_request": canonical,
            "productivity_profiles": profiles,
        }


def find_forecast(request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a versioned prediction bundle against the current project inputs."""
    if not isinstance(request, Mapping):
        raise ValueError("Prediction request must be an object.")
    return canonical_request(request)


__all__ = [
    "ActivityDisruptionPredictor",
    "DEFAULT_STATIC_BUNDLE",
    "FORECAST_FIELDS",
    "canonical_request",
    "find_forecast",
    "request_fingerprint",
]
