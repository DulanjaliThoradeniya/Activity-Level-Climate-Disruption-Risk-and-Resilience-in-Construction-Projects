"""Transparent, evidence-gated WSH risk assessment for climate-related hazards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import networkx as nx

from .graph_loader import DEFAULT_WORKBOOK, read_workbook
from .wsh_schemas import (
    CONTROL_HIERARCHY_ORDER,
    ActivityAssessment,
    AssessmentInput,
    ControlInput,
    HazardAssessment,
    RatingInput,
    RiskLevel,
    RiskMatrix,
    RiskStage,
    WSH_ASSESSMENT_JSON_SCHEMA,
)

DEFAULT_WSH_CODE = (
    "Singapore Code of Practice on WSH Risk Management, Third Revision 2021"
)
_RISK_LEVEL_ORDER = {"Low": 1, "Medium": 2, "High": 3}
_APPROVED_STATUSES = {"APPROVED", "APPROVED_BY_MANAGER", "APPROVED_FINAL"}
_IMPLEMENTED_STATUSES = {"IMPLEMENTED", "VERIFIED", "COMPLETED", "IN_PLACE"}


def load_code_example_matrix(
    workbook_path: str | Path = DEFAULT_WORKBOOK,
) -> RiskMatrix:
    """Read the workbook's Table 2 example matrix as an unapproved template."""
    workbook = read_workbook(workbook_path)
    matches: list[tuple[str, dict[str, Any]]] = []
    required = {
        "Severity S",
        "Likelihood L",
        "Reference descriptive class (SRC-01 Table 2 EXAMPLE)",
    }
    for sheet in workbook.worksheets:
        for position, (_, values) in enumerate(sheet.raw_rows[:20]):
            headers = {
                str(value).strip(): column
                for column, value in values.items()
                if isinstance(value, str) and value.strip()
            }
            if not required.issubset(headers):
                continue
            for matrix_row_number, cells in sheet.raw_rows[position + 1 :]:
                severity_value = cells.get(headers["Severity S"])
                likelihood_value = cells.get(headers["Likelihood L"])
                level = cells.get(
                    headers["Reference descriptive class (SRC-01 Table 2 EXAMPLE)"]
                )
                if (
                    severity_value in range(1, 6)
                    and likelihood_value in range(1, 6)
                    and level in {"Low", "Medium", "High"}
                ):
                    matches.append(
                        (
                            sheet.name,
                            {
                                "severity": severity_value,
                                "likelihood": likelihood_value,
                                "level": level,
                                "row": matrix_row_number,
                            },
                        )
                    )
            if matches:
                break

    classifications: dict[tuple[int, int], RiskLevel] = {
        (record["severity"], record["likelihood"]): record["level"]
        for _, record in matches
    }
    if len(classifications) != 25:
        raise ValueError(
            "Workbook does not contain all 25 Code example risk-matrix cells; "
            f"found {len(classifications)}."
        )
    return RiskMatrix(
        version="SRC-01-2021-TABLE-2-EXAMPLE",
        classifications=classifications,
        organization_approved=False,
        source="WORKBOOK_WSH_Risk_Matrix_REFERENCE_DESCRIPTIVE_CLASS",
        is_example_template=True,
        template_note=(
            "Illustrative Code of Practice Table 2 descriptive classes only. "
            "The Code does not prescribe universal Low/Medium/High cutoffs. "
            "Replace with a complete employer-approved organization matrix."
        ),
    )


@lru_cache(maxsize=4)
def _load_wsh_rating_scales_cached(workbook_path: str) -> dict[str, Any]:
    workbook = read_workbook(workbook_path)
    scales: dict[str, dict[str, Any]] = {}
    for scale_name, label_header, description_prefix in (
        ("severity", "Severity", "Description (SRC-01 Table 4"),
        ("likelihood", "Likelihood", "Description (SRC-01 Table 5"),
    ):
        records: dict[str, dict[str, Any]] = {}
        for sheet in workbook.worksheets:
            for position, (_, values) in enumerate(sheet.raw_rows[:20]):
                headers = {
                    str(value).strip(): column
                    for column, value in values.items()
                    if isinstance(value, str) and value.strip()
                }
                description_header = next(
                    (
                        header
                        for header in headers
                        if header.startswith(description_prefix)
                    ),
                    None,
                )
                if (
                    "Level" not in headers
                    or label_header not in headers
                    or description_header is None
                ):
                    continue
                for _, row in sheet.raw_rows[position + 1 :]:
                    level = row.get(headers["Level"])
                    label = row.get(headers[label_header])
                    description = row.get(headers[description_header])
                    if (
                        isinstance(level, int)
                        and level in range(1, 6)
                        and _nonempty(label)
                        and _nonempty(description)
                    ):
                        records[str(level)] = {
                            "label": str(label),
                            "description": str(description),
                            "source": row.get(headers.get("Source", -1)),
                        }
                if records:
                    scales[scale_name] = records
                    break
        if set(records) != {str(level) for level in range(1, 6)}:
            raise ValueError(
                f"Workbook does not contain all five {scale_name} rating definitions."
            )

    return {
        "severity": scales["severity"],
        "likelihood": scales["likelihood"],
        "severity_basis": (
            "Credible injury/ill-health with existing or required controls in place; "
            "do not derive severity from rainfall amount. WSH Code 6.4.2.2-6.4.3."
        ),
        "likelihood_basis": (
            "Likelihood that the hazard causes injury or ill-health, not rainfall "
            "probability. Consider exposure, site characteristics, existing controls, "
            "procedures and relevant incident/health factors. WSH Code 6.4.4."
        ),
        "source": DEFAULT_WSH_CODE,
    }


def load_wsh_rating_scales(
    workbook_path: str | Path = DEFAULT_WORKBOOK,
) -> dict[str, Any]:
    """Load the actual 1–5 severity and injury-likelihood definitions."""
    return _load_wsh_rating_scales_cached(
        str(Path(workbook_path).expanduser().resolve())
    )


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping) or isinstance(value, Sequence):
        return bool(value)
    return True


def _as_tuple_of_text(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),)


def _rating_from_mapping(
    value: Any,
    stage: str,
    legacy: Mapping[str, Any],
) -> tuple[RatingInput | None, list[str]]:
    if value is None:
        return None, [f"{stage}_rating_missing"]
    if not isinstance(value, Mapping):
        return None, [f"{stage}_rating_must_be_an_object"]

    errors: list[str] = []
    severity = value.get("severity")
    likelihood = value.get("likelihood")
    for name, score in (("severity", severity), ("likelihood", likelihood)):
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
            or score not in range(1, 6)
        ):
            errors.append(f"{stage}_{name}_must_be_an_integer_from_1_to_5")

    severity_rationale = str(value.get("severity_rationale", "")).strip()
    likelihood_rationale = str(value.get("likelihood_rationale", "")).strip()
    if not severity_rationale:
        errors.append(f"{stage}_severity_rationale_missing")
    if not likelihood_rationale:
        errors.append(f"{stage}_likelihood_rationale_missing")

    evidence = _as_tuple_of_text(value.get("evidence", legacy.get("evidence")))
    if not evidence:
        errors.append(f"{stage}_evidence_missing")
    control_effect_evidence = value.get(
        "control_effect_evidence", legacy.get("control_effect_evidence")
    )
    if stage == "residual" and not _nonempty(control_effect_evidence):
        errors.append("residual_control_effect_evidence_missing")

    if errors:
        return None, errors
    return (
        RatingInput(
            severity=severity,
            likelihood=likelihood,
            severity_rationale=severity_rationale,
            likelihood_rationale=likelihood_rationale,
            evidence=evidence,
            control_effect_evidence=(
                str(control_effect_evidence).strip()
                if _nonempty(control_effect_evidence)
                else None
            ),
        ),
        [],
    )


def _normalize_control(
    raw: Mapping[str, Any], control_id: str | None = None
) -> ControlInput | None:
    identity = str(raw.get("control_id", control_id or "")).strip()
    if not identity:
        return None
    hierarchy_raw = str(raw.get("hierarchy", "UNCLASSIFIED")).strip().upper()
    hierarchy_aliases = {
        "ELIMINATION": "ELIMINATION",
        "SUBSTITUTION": "SUBSTITUTION",
        "ENGINEERING": "ENGINEERING",
        "ADMINISTRATIVE": "ADMINISTRATIVE",
        "ADMINISTRATIVE / STOP-WORK": "ADMINISTRATIVE",
        "PPE": "PPE",
    }
    hierarchy = hierarchy_aliases.get(hierarchy_raw, "UNCLASSIFIED")
    return ControlInput(
        control_id=identity,
        hierarchy=hierarchy,  # type: ignore[arg-type]
        implementation_status=str(
            raw.get("implementation_status", raw.get("status", "NOT_IMPLEMENTED"))
        )
        .strip()
        .upper(),
        approval_status=str(raw.get("approval_status", "PENDING")).strip().upper(),
        evidence=(
            str(raw.get("evidence")).strip() if _nonempty(raw.get("evidence")) else None
        ),
        verified_by=(
            str(raw.get("verified_by")).strip()
            if _nonempty(raw.get("verified_by"))
            else None
        ),
        verification_date=(
            str(raw.get("verification_date")).strip()
            if _nonempty(raw.get("verification_date"))
            else None
        ),
    )


def _normalize_assessment(
    raw: Mapping[str, Any] | None,
    defaults: Mapping[str, Any] | None = None,
) -> tuple[AssessmentInput, dict[str, list[str]]]:
    values = dict(defaults or {})
    if raw is not None:
        values.update(raw)
    initial, initial_errors = _rating_from_mapping(
        values.get("initial"), "initial", values
    )
    residual, residual_errors = _rating_from_mapping(
        values.get("residual"), "residual", values
    )

    existing_ids = _as_tuple_of_text(
        values.get("existing_control_ids", values.get("existing_controls"))
    )
    existing_confirmed = values.get("existing_controls_considered") is True
    general_errors: list[str] = []
    if not _nonempty(values.get("site_conditions")):
        general_errors.append("site_conditions_missing")
    if not _nonempty(values.get("assessor")):
        general_errors.append("assessor_missing")
    if not existing_confirmed:
        general_errors.append("existing_controls_consideration_not_confirmed")

    controls: list[ControlInput] = []
    for item in values.get("controls", ()) or ():
        if isinstance(item, Mapping):
            control = _normalize_control(item)
            if control is not None:
                controls.append(control)

    approval_status = str(values.get("approval_status", "PENDING")).strip().upper()
    approved_by = values.get("approved_by")
    approval_date = values.get("approval_date")
    if approval_status in _APPROVED_STATUSES:
        if not _nonempty(approved_by):
            general_errors.append("approved_by_missing")
        if not _valid_iso_date(approval_date):
            general_errors.append("approval_date_missing_or_invalid")

    normalized = AssessmentInput(
        initial=initial,
        residual=residual,
        site_conditions=values.get("site_conditions"),
        existing_controls_considered=existing_confirmed,
        existing_control_ids=existing_ids,
        controls=tuple(controls),
        assessor=(
            str(values.get("assessor")).strip()
            if _nonempty(values.get("assessor"))
            else None
        ),
        approval_status=approval_status,
        approved_by=(
            str(approved_by).strip() if _nonempty(approved_by) else None
        ),
        approval_date=(
            str(approval_date).strip()
            if _nonempty(approval_date)
            else None
        ),
        evidence=_as_tuple_of_text(values.get("evidence")),
        review_reason=(
            str(values.get("review_reason")).strip()
            if _nonempty(values.get("review_reason"))
            else None
        ),
    )
    return normalized, {
        "initial": initial_errors,
        "residual": residual_errors,
        "general": general_errors,
    }


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _node_record(graph: nx.MultiDiGraph, node_id: str) -> dict[str, Any]:
    return dict(graph.nodes[node_id].get("record", {}))


def _relationship_type(edge: Mapping[str, Any]) -> str:
    return str(edge.get("relationship_type", edge.get("predicate", "")))


def _activity_hazard_paths(
    graph: nx.MultiDiGraph, wbs_code: str
) -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {}
    for _, hazard_id, _, edge in graph.out_edges(wbs_code, keys=True, data=True):
        if _relationship_type(edge) in {"mayExperience", "links_hazard"}:
            paths.setdefault(hazard_id, [])
    for link_id, _, _, edge in graph.in_edges(wbs_code, keys=True, data=True):
        if _relationship_type(edge) != "links_activity":
            continue
        if graph.nodes[link_id].get("node_type") != "activity_hazard_link":
            continue
        for _, hazard_id, _, hazard_edge in graph.out_edges(
            link_id, keys=True, data=True
        ):
            if _relationship_type(hazard_edge) == "links_hazard":
                paths.setdefault(hazard_id, []).append(link_id)
    return paths


def _record_references(*records: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for record in records:
        for field in ("Source IDs", "Source ID", "source_ids"):
            raw = record.get(field)
            if isinstance(raw, (tuple, list)):
                candidates = [str(item) for item in raw]
            else:
                candidates = re.findall(r"\bSRC-\d{2}\b", str(raw or ""))
            for candidate in candidates:
                if candidate not in result:
                    result.append(candidate)
    return tuple(result)


def _candidate_control_records(
    graph: nx.MultiDiGraph, link_ids: Sequence[str], hazard_id: str
) -> list[dict[str, Any]]:
    controls: dict[str, dict[str, Any]] = {}
    for link_id in link_ids:
        for _, target_id, _, edge in graph.out_edges(
            link_id, keys=True, data=True
        ):
            if _relationship_type(edge) != "candidate_control":
                continue
            if graph.nodes[target_id].get("node_type") != "control_measure":
                continue
            record = _node_record(graph, target_id)
            hierarchy = str(
                record.get("Hierarchy of control (SRC-01 Fig.3 / App.C)", "")
            ).strip()
            controls[target_id] = {
                "control_id": target_id,
                "name": graph.nodes[target_id].get("label", target_id),
                "hierarchy": _canonical_hierarchy(hierarchy),
                "hierarchy_source": hierarchy or None,
                "source_ids": list(
                    _record_references(
                        record,
                        {"source_ids": graph.nodes[target_id].get("source_ids", ())},
                    )
                ),
                "evidence_category": graph.nodes[target_id].get(
                    "evidence_category"
                ),
                "evidence_status": graph.nodes[target_id].get("evidence_status"),
                "candidate_only": True,
                "implemented": False,
            }
    for control_id, _, _, edge in graph.in_edges(
        hazard_id, keys=True, data=True
    ):
        if _relationship_type(edge) != "mitigates":
            continue
        if graph.nodes[control_id].get("node_type") != "control_measure":
            continue
        record = _node_record(graph, control_id)
        hierarchy = str(
            record.get("Hierarchy of control (SRC-01 Fig.3 / App.C)", "")
        ).strip()
        controls.setdefault(
            control_id,
            {
                "control_id": control_id,
                "name": graph.nodes[control_id].get("label", control_id),
                "hierarchy": _canonical_hierarchy(hierarchy),
                "hierarchy_source": hierarchy or None,
                "source_ids": list(
                    _record_references(
                        record,
                        {"source_ids": graph.nodes[control_id].get("source_ids", ())},
                    )
                ),
                "evidence_category": graph.nodes[control_id].get(
                    "evidence_category"
                ),
                "evidence_status": graph.nodes[control_id].get("evidence_status"),
                "candidate_only": True,
                "implemented": False,
            },
        )
    return sorted(
        controls.values(),
        key=lambda item: (
            CONTROL_HIERARCHY_ORDER.index(item["hierarchy"]),
            item["control_id"],
        ),
    )


def _candidate_intervention_records(
    graph: nx.MultiDiGraph, link_ids: Sequence[str], hazard_id: str
) -> list[dict[str, Any]]:
    intervention_ids: set[str] = set()
    for link_id in link_ids:
        for _, target_id, _, edge in graph.out_edges(
            link_id, keys=True, data=True
        ):
            if _relationship_type(edge) == "candidate_intervention":
                if graph.nodes[target_id].get("node_type") == "resilience_intervention":
                    intervention_ids.add(target_id)
    for intervention_id, _, _, edge in graph.in_edges(
        hazard_id, keys=True, data=True
    ):
        if _relationship_type(edge) == "mitigates":
            if graph.nodes[intervention_id].get("node_type") == "resilience_intervention":
                intervention_ids.add(intervention_id)
    results: list[dict[str, Any]] = []
    for intervention_id in sorted(intervention_ids):
        record = _node_record(graph, intervention_id)
        results.append(
            {
                "intervention_id": intervention_id,
                "name": graph.nodes[intervention_id].get("label", intervention_id),
                "hierarchy": _canonical_hierarchy(
                    str(record.get("WSH hierarchy category", ""))
                ),
                "hierarchy_source": record.get("WSH hierarchy category"),
                "evidence_status": graph.nodes[intervention_id].get(
                    "evidence_status"
                ),
                "effectiveness": record.get("Effectiveness"),
                "candidate_only": True,
                "changes_risk_rating_automatically": False,
            }
        )
    return sorted(
        results,
        key=lambda item: (
            CONTROL_HIERARCHY_ORDER.index(item["hierarchy"]),
            item["intervention_id"],
        ),
    )


def _canonical_hierarchy(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {
        "ELIMINATION",
        "SUBSTITUTION",
        "ENGINEERING",
        "ADMINISTRATIVE",
        "PPE",
    }:
        return normalized
    if "ENGINEERING" in normalized:
        return "ENGINEERING"
    if "SUBSTITUTION" in normalized:
        return "SUBSTITUTION"
    if "ELIMINATION" in normalized:
        return "ELIMINATION"
    if "PPE" in normalized:
        return "PPE"
    if "ADMIN" in normalized or "STOP-WORK" in normalized:
        return "ADMINISTRATIVE"
    return "UNCLASSIFIED"


def _rating_stage(
    rating: RatingInput | None,
    errors: Sequence[str],
    matrix: RiskMatrix,
) -> RiskStage:
    if rating is None:
        return RiskStage(
            severity=None,
            likelihood=None,
            rpn=None,
            risk_level=None,
            candidate_classification=None,
            status="REQUIRES_RA_REVIEW",
            severity_rationale=None,
            likelihood_rationale=None,
            evidence=(),
            control_effect_evidence=None,
            classification_basis=(
                "ORGANIZATION_APPROVED_MATRIX"
                if matrix.organization_approved
                else "UNAPPROVED_MATRIX_TEMPLATE"
            ),
            matrix_version=matrix.version,
        )
    severity, likelihood = rating.severity, rating.likelihood
    level = matrix.classify(severity, likelihood)
    candidate = matrix.candidate_classification(severity, likelihood)
    if errors:
        status = "REQUIRES_RA_REVIEW"
    elif not matrix.organization_approved:
        status = "REQUIRES_RA_REVIEW"
    else:
        status = "ASSESSED"
    return RiskStage(
        severity=severity,
        likelihood=likelihood,
        rpn=severity * likelihood,
        risk_level=level,
        candidate_classification=candidate,
        status=status,
        severity_rationale=rating.severity_rationale,
        likelihood_rationale=rating.likelihood_rationale,
        evidence=rating.evidence,
        control_effect_evidence=rating.control_effect_evidence,
        classification_basis=(
            "ORGANIZATION_APPROVED_MATRIX"
            if matrix.organization_approved
            else "COP_TABLE_2_EXAMPLE_NOT_APPROVED"
        ),
        matrix_version=matrix.version,
    )


def _control_completion(
    controls: Sequence[ControlInput],
) -> tuple[bool, list[dict[str, Any]]]:
    if not controls:
        return False, []
    records: list[dict[str, Any]] = []
    complete = True
    for control in controls:
        implemented = control.implementation_status in _IMPLEMENTED_STATUSES
        approved = control.approval_status in _APPROVED_STATUSES
        verified = (
            control.implementation_status in {"VERIFIED", "COMPLETED"}
            and bool(control.verified_by)
            and _valid_iso_date(control.verification_date)
            and bool(control.evidence)
        )
        records.append(
            {
                "control_id": control.control_id,
                "hierarchy": control.hierarchy,
                "implementation_status": control.implementation_status,
                "approval_status": control.approval_status,
                "evidence": control.evidence,
                "verified_by": control.verified_by,
                "verification_date": control.verification_date,
                "implemented": implemented,
                "approved": approved,
                "verified": verified,
            }
        )
        complete = complete and implemented and approved and verified
    return complete, records


def _authorized_approval(
    assessment: AssessmentInput,
    errors: Sequence[str],
    matrix: RiskMatrix,
    level: RiskLevel | None,
) -> bool:
    return (
        not errors
        and matrix.organization_approved
        and assessment.approval_status in _APPROVED_STATUSES
        and bool(assessment.assessor)
        and bool(assessment.approved_by)
        and _valid_iso_date(assessment.approval_date)
        and level is not None
    )


def _build_hazard_assessment(
    graph: nx.MultiDiGraph,
    hazard_id: str,
    link_ids: Sequence[str],
    raw_assessment: Mapping[str, Any] | None,
    defaults: Mapping[str, Any] | None,
    matrix: RiskMatrix,
) -> HazardAssessment:
    hazard_exists = (
        graph.has_node(hazard_id)
        and graph.nodes[hazard_id].get("defined")
        and graph.nodes[hazard_id].get("node_type") == "climate_hazard"
    )
    hazard_record = _node_record(graph, hazard_id) if hazard_exists else {}
    hazard_name = (
        str(hazard_record.get("Hazard") or graph.nodes[hazard_id].get("label") or hazard_id)
        if graph.has_node(hazard_id)
        else hazard_id
    )

    assessment, errors = _normalize_assessment(raw_assessment, defaults)
    general_errors = list(errors["general"])
    if assessment.initial is not None and not assessment.existing_controls_considered:
        general_errors.append("existing_controls_consideration_not_confirmed")
    initial_errors = [*errors["initial"], *general_errors]
    residual_errors = [
        *errors["residual"],
        *general_errors,
    ]

    initial = _rating_stage(assessment.initial, initial_errors, matrix)
    residual = _rating_stage(assessment.residual, residual_errors, matrix)

    controls_complete, implemented_controls = _control_completion(
        assessment.controls
    )
    unresolved = not hazard_exists
    approval_valid = assessment.approval_status in _APPROVED_STATUSES
    initial_approved = (
        _authorized_approval(
            assessment, initial_errors, matrix, initial.risk_level
        )
        and initial.status == "ASSESSED"
    )
    residual_approved = (
        _authorized_approval(
            assessment, residual_errors, matrix, residual.risk_level
        )
        and residual.status == "ASSESSED"
    )

    if unresolved:
        status = "UNRESOLVED_HAZARD"
        authorization = "NOT_AUTHORIZED_REQUIRES_RA_REVIEW"
        action_required = "Resolve the missing hazard node and conduct RA-team review."
        highest_approved = None
    elif initial.risk_level == "High" or residual.risk_level == "High":
        if residual_approved and residual.risk_level != "High" and controls_complete:
            status = "APPROVED"
            authorization = "AUTHORIZED_TO_PROCEED"
            action_required = (
                "Maintain verified approved controls and continue monitoring site conditions."
            )
            highest_approved = residual.risk_level
        else:
            status = (
                "REQUIRES_RA_REVIEW"
                if not initial_approved or not residual_approved or not controls_complete
                else "APPROVED"
            )
            authorization = "NOT_AUTHORIZED_HIGH_RISK"
            action_required = (
                "Stop or do not commence this work. Reduce approved residual risk "
                "below High; implement, verify and approve controls before work starts."
            )
            highest_approved = (
                residual.risk_level
                if residual_approved
                else initial.risk_level if initial_approved else None
            )
    elif not initial_approved or not residual_approved:
        status = (
            "ASSESSED_PENDING_APPROVAL"
            if (
                assessment.initial is not None
                and not initial_errors
                and matrix.organization_approved
                and not approval_valid
            )
            else "REQUIRES_RA_REVIEW"
        )
        authorization = "NOT_AUTHORIZED_REQUIRES_RA_REVIEW"
        action_required = (
            "Complete missing site evidence, initial/residual ratings and required "
            "approval before representing this work as authorized."
        )
        authorization = (
            "NOT_AUTHORIZED_PENDING_CONTROLS_OR_APPROVAL"
            if (
                initial.status == "ASSESSED"
                and residual.status == "ASSESSED"
                and matrix.organization_approved
                and not controls_complete
            )
            else "NOT_AUTHORIZED_REQUIRES_RA_REVIEW"
        )
        highest_approved = (
            initial.risk_level if initial_approved else None
        )
    elif not controls_complete:
        status = "REQUIRES_RA_REVIEW"
        authorization = "NOT_AUTHORIZED_PENDING_CONTROLS_OR_APPROVAL"
        action_required = (
            "Implement and verify all controls relied upon by the residual rating; "
            "record implementation evidence and approval."
        )
        highest_approved = residual.risk_level
    else:
        status = "APPROVED"
        authorization = "AUTHORIZED_TO_PROCEED"
        action_required = (
            "Maintain verified approved controls and review the assessment when "
            "controls or site conditions change."
        )
        highest_approved = residual.risk_level

    if not matrix.organization_approved:
        status = "REQUIRES_RA_REVIEW"
        authorization = "NOT_AUTHORIZED_REQUIRES_RA_REVIEW"
        action_required = (
            "Obtain employer-concurred organization risk-matrix approval and "
            "complete competent-person/RA-team review."
        )
        highest_approved = None

    consequences: list[str] = []
    for link_id in link_ids:
        record = _node_record(graph, link_id)
        for field in ("Potential injury / ill-health", "Potential operational consequence"):
            value = str(record.get(field, "")).strip()
            if value and value not in consequences:
                consequences.append(value)
    for _, consequence_id, _, edge in graph.out_edges(
        hazard_id, keys=True, data=True
    ):
        if edge.get("predicate") not in {
            "mayResultInSafetyConsequence",
            "mayResultInPerformanceConsequence",
        }:
            continue
        if not graph.nodes[consequence_id].get("defined"):
            continue
        record = _node_record(graph, consequence_id)
        text = str(record.get("Consequence", "")).strip()
        if text and text not in consequences:
            consequences.append(text)

    candidate_controls = _candidate_control_records(graph, link_ids, hazard_id) if hazard_exists else []
    candidate_interventions = (
        _candidate_intervention_records(graph, link_ids, hazard_id)
        if hazard_exists
        else []
    )
    matrix_data = matrix.to_dict()
    matrix_data["code_reference"] = DEFAULT_WSH_CODE

    if unresolved:
        review_reason = "Hazard node referenced by activity is unresolved in the workbook."
    elif assessment.review_reason:
        review_reason = assessment.review_reason
    else:
        missing = [*initial_errors]
        if not residual_approved:
            missing.extend(residual_errors)
        review_reason = "; ".join(dict.fromkeys(missing)) or None

    return HazardAssessment(
        hazard_id=hazard_id,
        hazard_name=hazard_name,
        unresolved=unresolved,
        unresolved_reason=(
            "Hazard node is referenced by an activity/link but has no defined hazard record."
            if unresolved
            else None
        ),
        applicable=True,
        activity_hazard_link_ids=tuple(link_ids),
        potential_consequences=tuple(consequences),
        candidate_controls=tuple(candidate_controls),
        candidate_interventions=tuple(candidate_interventions),
        initial=initial,
        residual=residual,
        assessment_status=status,  # type: ignore[arg-type]
        approval_status=assessment.approval_status,
        assessor=assessment.assessor,
        approved_by=assessment.approved_by,
        approval_date=assessment.approval_date,
        matrix=matrix_data,
        site_conditions=assessment.site_conditions,
        existing_controls_considered=assessment.existing_controls_considered,
        existing_control_ids=assessment.existing_control_ids,
        implemented_controls=tuple(implemented_controls),
        highest_approved_risk_level=highest_approved,
        authorization_status=authorization,
        action_required=action_required,
        review_reason=review_reason,
    )


def _risk_level_for_summary(
    assessment: HazardAssessment,
) -> RiskLevel | None:
    if assessment.assessment_status != "APPROVED":
        return None
    return assessment.residual.risk_level or assessment.initial.risk_level


def assess_activity(
    graph: nx.MultiDiGraph,
    wbs_code: str,
    hazard_assessments: Mapping[str, Mapping[str, Any]] | None = None,
    matrix: RiskMatrix | Mapping[str, Any] | None = None,
    *,
    site_conditions: Mapping[str, Any] | str | None = None,
    assessor: str | None = None,
    approval_status: str = "PENDING",
    approved_by: str | None = None,
    approval_date: str | None = None,
    existing_controls_considered: bool = False,
    existing_control_ids: Sequence[str] = (),
    evidence: Sequence[str] = (),
) -> dict[str, Any]:
    """Assess each graph-supported activity/hazard pair without inferring ratings.

    Severity and likelihood are assessor-supplied WSH judgments. Rainfall
    probability, forecast confidence, exposure categories, and graph evidence
    are not converted into likelihood or severity scores.
    """
    code = str(wbs_code).strip().upper()
    if not graph.has_node(code):
        raise KeyError(f"Unknown WBS activity code: {wbs_code!r}")
    activity_node = graph.nodes[code]
    if activity_node.get("node_type") != "construction_activity":
        raise ValueError(f"{code!r} is not a construction activity node.")

    if matrix is None:
        matrix_object = load_code_example_matrix()
    elif isinstance(matrix, RiskMatrix):
        matrix_object = matrix
    else:
        matrix_object = RiskMatrix.from_dict(matrix)

    paths = _activity_hazard_paths(graph, code)
    if not paths:
        return ActivityAssessment(
            schema_version="1.0",
            assessment_type="WSH_ACTIVITY_RISK_ASSESSMENT",
            wbs_code=code,
            application_activity_id=activity_node.get("browser_id"),
            activity_name=str(activity_node.get("label", code)),
            assessment_status="REQUIRES_RA_REVIEW",
            activity_risk_level=None,
            activity_risk_hazard_id=None,
            authorization_status="NO_APPLICABLE_RESOLVED_HAZARDS",
            matrix=matrix_object.to_dict(),
            rating_scales=load_wsh_rating_scales(),
            hazard_assessments=(),
            unresolved_hazards=(),
            warnings=(
                "No graph-supported activity/hazard paths were found; verify the activity scope.",
            ),
        ).to_dict()

    defaults = {
        "site_conditions": site_conditions,
        "assessor": assessor,
        "approval_status": approval_status,
        "approved_by": approved_by,
        "approval_date": approval_date,
        "existing_controls_considered": existing_controls_considered,
        "existing_control_ids": existing_control_ids,
        "evidence": evidence,
    }
    supplied = hazard_assessments or {}
    assessments = tuple(
        _build_hazard_assessment(
            graph,
            hazard_id,
            paths[hazard_id],
            supplied.get(hazard_id),
            defaults,
            matrix_object,
        )
        for hazard_id in sorted(paths)
    )
    unresolved_hazards = tuple(
        assessment.hazard_id for assessment in assessments if assessment.unresolved
    )
    approved_levels = [
        (assessment.hazard_id, _risk_level_for_summary(assessment))
        for assessment in assessments
        if _risk_level_for_summary(assessment) is not None
    ]
    summary_hazard_id: str | None = None
    summary_level: RiskLevel | None = None
    if approved_levels:
        summary_hazard_id, summary_level = max(
            approved_levels,
            key=lambda item: (_RISK_LEVEL_ORDER[item[1]], item[0]),
        )

    if unresolved_hazards:
        overall_status = "UNRESOLVED_HAZARD"
    elif all(
        item.assessment_status == "APPROVED" for item in assessments
    ):
        overall_status = "APPROVED"
    elif any(
        item.assessment_status == "ASSESSED_PENDING_APPROVAL"
        for item in assessments
    ):
        overall_status = "ASSESSED_PENDING_APPROVAL"
    else:
        overall_status = "REQUIRES_RA_REVIEW"

    if any(item.authorization_status == "NOT_AUTHORIZED_HIGH_RISK" for item in assessments):
        authorization = "NOT_AUTHORIZED_HIGH_RISK"
    elif all(
        item.authorization_status == "AUTHORIZED_TO_PROCEED"
        for item in assessments
    ):
        authorization = "AUTHORIZED_TO_PROCEED"
    elif not assessments:
        authorization = "NO_APPLICABLE_RESOLVED_HAZARDS"
    elif unresolved_hazards or not matrix_object.organization_approved:
        authorization = "NOT_AUTHORIZED_REQUIRES_RA_REVIEW"
    else:
        authorization = "NOT_AUTHORIZED_PENDING_CONTROLS_OR_APPROVAL"

    warnings: list[str] = []
    unrelated_assessment_ids = sorted(set(supplied) - set(paths))
    if unrelated_assessment_ids:
        warnings.append(
            "Assessment inputs were provided for hazards not linked to this activity "
            f"and were not applied: {', '.join(unrelated_assessment_ids)}."
        )
    if not matrix_object.organization_approved:
        warnings.append(
            "No employer-concurred organization risk matrix is configured. "
            "Any classification is an unapproved candidate only."
        )
    if unresolved_hazards:
        warnings.append(
            "One or more activity hazards have unresolved graph definitions and require review."
        )
    warnings.append(
        "Severity and likelihood must be assessed for injury/ill-health using site evidence; "
        "rainfall probability is not WSH likelihood."
    )

    output = ActivityAssessment(
        schema_version="1.0",
        assessment_type="WSH_ACTIVITY_RISK_ASSESSMENT",
        wbs_code=code,
        application_activity_id=activity_node.get("browser_id"),
        activity_name=str(activity_node.get("label", code)),
        assessment_status=overall_status,
        activity_risk_level=summary_level,
        activity_risk_hazard_id=summary_hazard_id,
        authorization_status=authorization,
        matrix=matrix_object.to_dict(),
        rating_scales=load_wsh_rating_scales(),
        hazard_assessments=assessments,
        unresolved_hazards=unresolved_hazards,
        warnings=tuple(warnings),
    )
    return output.to_dict()


def reassess_activity(
    graph: nx.MultiDiGraph,
    wbs_code: str,
    hazard_assessments: Mapping[str, Mapping[str, Any]] | None = None,
    matrix: RiskMatrix | Mapping[str, Any] | None = None,
    **current_context: Any,
) -> dict[str, Any]:
    """Re-evaluate against caller-supplied updated controls/site conditions."""
    return assess_activity(
        graph,
        wbs_code,
        hazard_assessments,
        matrix,
        **current_context,
    )


__all__ = [
    "DEFAULT_WSH_CODE",
    "WSH_ASSESSMENT_JSON_SCHEMA",
    "RiskMatrix",
    "assess_activity",
    "load_code_example_matrix",
    "load_wsh_rating_scales",
    "reassess_activity",
]
