"""Evidence-gated, safety-first intervention recommendations."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
from typing import Any, Callable, Mapping, Sequence

import networkx as nx

from risk_engine.activity_mapper import get_activity_knowledge
from risk_engine.graph_loader import DEFAULT_WORKBOOK, load_knowledge_graph
from risk_engine.wsh_assessment import assess_activity

ScheduleRunner = Callable[
    [Sequence[Mapping[str, Any]], str, Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any],
]

_HIERARCHY_ORDER = {
    "ELIMINATION": 0,
    "SUBSTITUTION": 1,
    "ENGINEERING": 2,
    "ADMINISTRATIVE": 3,
    "PPE": 4,
    "UNCLASSIFIED": 5,
}
_STOP_GATE_IDS = {"G-03", "G-04", "G-05", "G-06", "G-07", "G-08"}
_SOURCE_ID_PATTERN = re.compile(r"\bSRC-\d{2}\b")


def _record(graph: nx.MultiDiGraph, node_id: str) -> dict[str, Any]:
    return dict(graph.nodes[node_id].get("record", {}))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _split_values(value: Any) -> list[str]:
    return [item.strip() for item in re.split(r"[;,]", _text(value)) if item.strip()]


def _source_ids(value: Any) -> list[str]:
    return list(dict.fromkeys(_SOURCE_ID_PATTERN.findall(_text(value))))


def _source_details(
    graph: nx.MultiDiGraph, source_ids: Sequence[str], section: str | None = None
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source_id in dict.fromkeys(source_ids):
        if not graph.has_node(source_id) or graph.nodes[source_id].get("node_type") != "evidence_source":
            sources.append(
                {
                    "source_id": source_id,
                    "reference": None,
                    "section": section,
                    "verification_status": "SOURCE_RECORD_MISSING",
                }
            )
            continue
        record = _record(graph, source_id)
        sources.append(
            {
                "source_id": source_id,
                "reference": record.get("APA 7 reference"),
                "document_reference": record.get("Document reference"),
                "section": section or record.get("Sections / pages used"),
                "verification_status": record.get("Verification status"),
                "caution": record.get("Relevance and caution"),
            }
        )
    return sources


def _hierarchy(value: Any) -> str:
    normalized = _text(value).upper()
    return normalized if normalized in _HIERARCHY_ORDER else "UNCLASSIFIED"


def _site_confirmed(review: Mapping[str, Any] | None) -> bool:
    if not review or review.get("status") != "CONFIRMED_APPLICABLE":
        return False
    return all(
        _text(review.get(field))
        for field in ("reviewer", "review_date", "rationale")
    )


def _implementation_plan(
    plan: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    if plan is None:
        return {
            "required_resources": None,
            "responsible_personnel": None,
            "implementation_time_workdays": None,
            "recovery_workdays": None,
            "approval_status": "NOT_PROVIDED",
        }, [
            "Required resources, responsible personnel, and implementation time need site assignment."
        ]
    errors: list[str] = []
    resources = plan.get("required_resources")
    if not isinstance(resources, list) or not resources or any(
        not _text(item) for item in resources
    ):
        errors.append("Required resources need explicit site assignment.")
        resources = None
    personnel = _text(plan.get("responsible_personnel")) or None
    if personnel is None:
        errors.append("Responsible personnel need explicit site assignment.")
    implementation_days = plan.get("implementation_time_workdays")
    if (
        isinstance(implementation_days, bool)
        or not isinstance(implementation_days, int)
        or implementation_days < 0
    ):
        errors.append("Implementation time must be supplied as whole project workdays.")
        implementation_days = None
    recovery_days = plan.get("recovery_workdays")
    if (
        isinstance(recovery_days, bool)
        or not isinstance(recovery_days, int)
        or recovery_days < 0
    ):
        errors.append("Recovery time must be explicitly supplied as whole project workdays.")
        recovery_days = None
    return {
        "required_resources": resources,
        "responsible_personnel": personnel,
        "implementation_time_workdays": implementation_days,
        "recovery_workdays": recovery_days,
        "approval_status": _text(plan.get("approval_status")) or "NOT_PROVIDED",
    }, errors


def _validate_productivity_assumption(
    assumption: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if assumption is None:
        return None, []
    errors: list[str] = []
    source_type = assumption.get("source_type")
    if source_type not in {"EXPLICIT_SCENARIO_ASSUMPTION", "QUANTIFIED_EVIDENCE"}:
        errors.append(
            "Productivity effect requires an explicit scenario assumption or quantified evidence."
        )
    rationale = _text(assumption.get("rationale"))
    if not rationale:
        errors.append("Productivity assumption rationale is required.")
    if source_type == "QUANTIFIED_EVIDENCE" and not _split_values(
        assumption.get("evidence_ids")
    ):
        errors.append("Quantified productivity evidence requires source IDs.")
    entries = assumption.get("adjusted_profile")
    if not isinstance(entries, list) or not entries:
        errors.append("A productivity assumption requires an adjusted daily profile.")
        entries = []
    normalized_entries: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            errors.append("Each adjusted profile entry must be an object.")
            continue
        day = _text(entry.get("date"))
        try:
            date.fromisoformat(day)
        except ValueError:
            errors.append(f"Invalid productivity profile date: {day!r}.")
            continue
        if day in seen_dates:
            errors.append(f"Duplicate productivity profile date: {day!r}.")
            continue
        seen_dates.add(day)
        factor = entry.get("productivityFactor")
        if (
            isinstance(factor, bool)
            or not isinstance(factor, (int, float))
            or not 0 <= factor <= 1
        ):
            errors.append(f"Invalid productivity factor for {day!r}; expected 0 to 1.")
            continue
        normalized_entries.append(dict(entry))
    if errors:
        return None, errors
    return {
        "source_type": source_type,
        "rationale": rationale,
        "evidence_ids": _split_values(assumption.get("evidence_ids")),
        "adjusted_profile": normalized_entries,
        "status": (
            "SCENARIO_ASSUMPTION_NOT_EMPIRICAL"
            if source_type == "EXPLICIT_SCENARIO_ASSUMPTION"
            else "QUANTIFIED_EVIDENCE_REQUIRES_REVIEW"
        ),
    }, []


def _site_gates(
    graph: nx.MultiDiGraph,
    assessment: Mapping[str, Any],
    site_conditions: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    conditions = site_conditions or {}
    matrix_approved = bool(
        assessment.get("matrix", {}).get("organization_approved")
    )
    status = assessment.get("assessment_status")
    risk_level = assessment.get("activity_risk_level")
    high_hazard_present = any(
        item.get("authorization_status") == "NOT_AUTHORIZED_HIGH_RISK"
        or item.get("residual", {}).get("risk_level") == "High"
        for item in assessment.get("hazard_assessments", ())
    )
    gate_status: dict[str, tuple[bool, str]] = {
        "G-01": (status != "APPROVED", "ASSESSED"),
        "G-02": (not matrix_approved, "ASSESSED"),
        "G-03": (
            risk_level == "High" or high_hazard_present,
            "ASSESSED" if status == "APPROVED" and matrix_approved else "NOT_ASSESSED",
        ),
    }
    below_ground_assessed = (
        "below_ground_work" in conditions and "unsafe_flooding" in conditions
    )
    gate_status["G-04"] = (
        conditions.get("below_ground_work") is True
        and conditions.get("unsafe_flooding") is True,
        "ASSESSED" if below_ground_assessed else "NOT_ASSESSED",
    )
    wind = conditions.get("wind_speed_kmh")
    wind_assessed = conditions.get("outdoor_work") is False or (
        conditions.get("outdoor_work") is True
        and isinstance(wind, (int, float))
        and not isinstance(wind, bool)
    )
    gate_status["G-05"] = (
        conditions.get("outdoor_work") is True
        and isinstance(wind, (int, float))
        and not isinstance(wind, bool)
        and wind > 60,
        "ASSESSED" if wind_assessed else "NOT_ASSESSED",
    )
    gate_status["G-06"] = (
        conditions.get("mewp_or_gondola_limit_exceeded") is True,
        "ASSESSED"
        if "mewp_or_gondola_limit_exceeded" in conditions
        else "NOT_ASSESSED",
    )
    gate_status["G-07"] = (
        conditions.get("lightning_unsafe_for_outdoor_work") is True,
        "ASSESSED"
        if "lightning_unsafe_for_outdoor_work" in conditions
        else "NOT_ASSESSED",
    )
    restart_assessed = (
        "adverse_weather_occurred" in conditions
        and (
            conditions.get("adverse_weather_occurred") is False
            or "safe_restart_verified" in conditions
        )
    )
    gate_status["G-08"] = (
        conditions.get("adverse_weather_occurred") is True
        and conditions.get("safe_restart_verified") is not True,
        "ASSESSED" if restart_assessed else "NOT_ASSESSED",
    )
    heat_assessed = conditions.get("heat_stress_present") is False or (
        conditions.get("heat_stress_present") is True
        and "wbgt_assessed" in conditions
    )
    gate_status["G-09"] = (
        conditions.get("heat_stress_present") is True
        and not conditions.get("wbgt_assessed"),
        "ASSESSED" if heat_assessed else "NOT_ASSESSED",
    )
    gate_status["G-10"] = (False, "APPLY_TO_EACH_PROPOSED_ACTION")
    gates: list[dict[str, Any]] = []
    for gate_id, (triggered, condition_status) in sorted(gate_status.items()):
        if not graph.has_node(gate_id):
            continue
        record = _record(graph, gate_id)
        source_ids = _source_ids(record.get("Source"))
        gates.append(
            {
                "gate_id": gate_id,
                "condition": record.get("Condition"),
                "engine_response": record.get("Engine response"),
                "approval_or_input": record.get("Approval / input"),
                "source_ids": source_ids,
                "sources": _source_details(graph, source_ids),
                "triggered": triggered,
                "condition_status": condition_status,
            }
        )
    return gates


def _weather_controls_for_activity(
    graph: nx.MultiDiGraph, wbs_code: str
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for node_id, attributes in graph.nodes(data=True):
        if attributes.get("node_type") != "adverse_weather_control":
            continue
        record = _record(graph, node_id)
        applicability = _text(
            record.get("Candidate WBS applicability (verify)")
        )
        codes = re.findall(r"\b[A-Z]{2}-\d{2}\b", applicability.upper())
        if codes and wbs_code not in codes:
            continue
        if not codes and "all applicable work" not in applicability.lower():
            continue
        source_text = "; ".join(
            _text(value)
            for key, value in record.items()
            if "source" in key.lower() and _text(value)
        )
        source_ids = _source_ids(source_text)
        results.append(
            {
                "guidance_id": node_id,
                "name": record.get("Control / rule"),
                "candidate_only": True,
                "applicability_status": "CANDIDATE_REQUIRES_SITE_VERIFICATION",
                "trigger_or_condition": record.get("Trigger / condition"),
                "recommended_action": record.get("Recommended action"),
                "hierarchy": _hierarchy(record.get("Hierarchy (interpretive)")),
                "guideline_section": record.get("2025 guideline section"),
                "caution": record.get("Caution"),
                "source_ids": source_ids,
                "sources": _source_details(
                    graph, source_ids, _text(record.get("2025 guideline section")) or None
                ),
            }
        )
    return sorted(results, key=lambda item: item["guidance_id"])


def _calendar_workday_delta(
    start: str, end: str, calendar: Mapping[str, Any]
) -> int:
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date == end_date:
        return 0
    weekdays = set(calendar.get("weekdays", (1, 2, 3, 4, 5)))
    holidays = set(calendar.get("holidays", ()))
    direction = 1 if end_date > start_date else -1
    count = 0
    current = start_date
    while current != end_date:
        current = date.fromordinal(current.toordinal() + direction)
        if current.isoweekday() in weekdays and current.isoformat() not in holidays:
            count += direction
    return count


def _schedule_comparison(
    *,
    schedule_runner: ScheduleRunner | None,
    schedule_input: Mapping[str, Any] | None,
    schedule_activity_id: str | None,
    implementation_plan: Mapping[str, Any],
    assumption: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "NOT_EVALUATED",
        "baseline_completion_date": None,
        "revised_completion_date": None,
        "completion_change_workdays": None,
        "potential_project_delay_avoided_workdays": None,
    }
    if schedule_runner is None or schedule_input is None:
        result["status"] = "SCHEDULE_RUNNER_OR_INPUT_NOT_PROVIDED"
        return result
    if not schedule_activity_id:
        result["status"] = "SCHEDULE_ACTIVITY_MAPPING_REQUIRED"
        return result
    implementation_days = implementation_plan.get("implementation_time_workdays")
    recovery_days = implementation_plan.get("recovery_workdays")
    if implementation_days is None or recovery_days is None:
        result["status"] = "IMPLEMENTATION_OR_RECOVERY_TIME_REQUIRED"
        return result
    activities = deepcopy(schedule_input.get("activities", []))
    start = _text(schedule_input.get("project_start"))
    calendar = deepcopy(schedule_input.get("calendar", {}))
    baseline_scenario = deepcopy(schedule_input.get("scenario", {}))
    if not activities or not start or not isinstance(calendar, Mapping):
        result["status"] = "INVALID_SCHEDULE_INPUT"
        return result
    baseline_result = schedule_runner(
        activities, start, calendar, baseline_scenario
    )
    candidate_scenario = deepcopy(baseline_scenario)
    if assumption is not None:
        profiles = candidate_scenario.get("activityProfiles", {})
        if not isinstance(profiles, Mapping):
            raise ValueError("Schedule activityProfiles must be an object.")
        profiles = dict(profiles)
        profiles[schedule_activity_id] = deepcopy(assumption["adjusted_profile"])
        candidate_scenario["activityProfiles"] = profiles
    for field, value in (
        ("implementationDelays", implementation_days),
        ("recoveryDaysByActivity", recovery_days),
    ):
        schedule_values = candidate_scenario.get(field, {})
        if not isinstance(schedule_values, Mapping):
            raise ValueError(f"Schedule {field} must be an object.")
        updated_values = dict(schedule_values)
        updated_values[schedule_activity_id] = value
        candidate_scenario[field] = updated_values
    revised_result = schedule_runner(
        activities, start, calendar, candidate_scenario
    )
    baseline_finish = _text(baseline_result.get("adjustedFinish"))
    revised_finish = _text(revised_result.get("adjustedFinish"))
    if not baseline_finish or not revised_finish:
        raise ValueError(
            "Schedule runner must return an adjustedFinish YYYY-MM-DD date."
        )
    change = _calendar_workday_delta(baseline_finish, revised_finish, calendar)
    baseline_activity = (
        baseline_result.get("adj", {}).get(schedule_activity_id, {})
        if isinstance(baseline_result.get("adj"), Mapping)
        else {}
    )
    revised_activity = (
        revised_result.get("adj", {}).get(schedule_activity_id, {})
        if isinstance(revised_result.get("adj"), Mapping)
        else {}
    )
    baseline_activity_finish = _text(baseline_activity.get("finish")) or None
    revised_activity_finish = _text(revised_activity.get("finish")) or None
    activity_change: int | None = None
    if baseline_activity_finish and revised_activity_finish:
        activity_change = _calendar_workday_delta(
            baseline_activity_finish, revised_activity_finish, calendar
        )
    revised_base_activity = (
        revised_result.get("base", {}).get(schedule_activity_id, {})
        if isinstance(revised_result.get("base"), Mapping)
        else {}
    )
    revised_base_finish = _text(revised_base_activity.get("finish")) or None
    revised_activity_delay: int | None = None
    if revised_base_finish and revised_activity_finish:
        revised_activity_delay = _calendar_workday_delta(
            revised_base_finish, revised_activity_finish, calendar
        )
    result.update(
        {
            "status": "EVALUATED",
            "baseline_completion_date": baseline_finish,
            "revised_completion_date": revised_finish,
            "completion_change_workdays": change,
            "potential_project_delay_avoided_workdays": -change,
            "baseline_activity_finish_date": baseline_activity_finish,
            "revised_activity_finish_date": revised_activity_finish,
            "revised_activity_delay_workdays": revised_activity_delay,
            "activity_finish_change_workdays": activity_change,
            "activity_direct_disruption": revised_activity.get("direct"),
            "activity_inherited_delay": revised_activity.get("inherited"),
            "activity_direct_lost_productive_hours": revised_activity.get(
                "directLostProductiveHours"
            ),
            "activity_uncertainty_hours": revised_activity.get("uncertaintyHours"),
            "scenario_assumption_status": (
                assumption.get("status") if assumption is not None else None
            ),
        }
    )
    return result


def recommend_activity_interventions(
    wbs_code: str,
    *,
    graph: nx.MultiDiGraph | None = None,
    workbook_path: str = str(DEFAULT_WORKBOOK),
    wsh_assessment: Mapping[str, Any] | None = None,
    site_conditions: Mapping[str, Any] | None = None,
    applicability_reviews: Mapping[str, Mapping[str, Any]] | None = None,
    implementation_plans: Mapping[str, Mapping[str, Any]] | None = None,
    productivity_assumptions: Mapping[str, Mapping[str, Any]] | None = None,
    schedule_activity_id: str | None = None,
    schedule_input: Mapping[str, Any] | None = None,
    schedule_runner: ScheduleRunner | None = None,
) -> dict[str, Any]:
    """Build auditable candidate recommendations for one canonical activity.

    The callback receives the existing schedule engine's activities, start,
    calendar, and scenario values. No productivity benefit is inferred from
    qualitative graph statements.
    """
    graph = graph if graph is not None else load_knowledge_graph(workbook_path)
    code = _text(wbs_code).upper()
    knowledge = get_activity_knowledge(code, graph=graph)
    assessment = dict(wsh_assessment or assess_activity(graph, code))
    if assessment.get("wbs_code") != code:
        raise ValueError("WSH assessment WBS code does not match the requested activity.")
    hazards_by_id = {
        str(item["hazard_id"]): item
        for item in assessment.get("hazard_assessments", ())
    }
    high_hazard_present = any(
        item.get("authorization_status") == "NOT_AUTHORIZED_HIGH_RISK"
        or item.get("residual", {}).get("risk_level") == "High"
        for item in hazards_by_id.values()
    )
    gates = _site_gates(graph, assessment, site_conditions)
    triggered_stop_gates = {
        gate["gate_id"]
        for gate in gates
        if gate["triggered"] and gate["gate_id"] in _STOP_GATE_IDS
    }
    assessment_authorizes_proceed = (
        assessment.get("assessment_status") == "APPROVED"
        and assessment.get("authorization_status") == "AUTHORIZED_TO_PROCEED"
        and not high_hazard_present
        and not triggered_stop_gates
    )
    knowledge_by_id = {
        record["id"]: record
        for collection in ("hazards", "controls", "interventions")
        for record in knowledge[collection]
    }
    reviews = applicability_reviews or {}
    plans = implementation_plans or {}
    assumptions = productivity_assumptions or {}
    recommendations: list[dict[str, Any]] = []
    schedule_cache: dict[str, dict[str, Any]] = {}

    for hazard_id, hazard_assessment in hazards_by_id.items():
        hazard_knowledge = knowledge_by_id.get(hazard_id, {})
        hazard_name = hazard_knowledge.get("label", hazard_assessment.get("hazard_name"))
        for candidate in hazard_assessment.get("candidate_interventions", ()):
            intervention_id = str(candidate["intervention_id"])
            node = graph.nodes.get(intervention_id, {})
            record = dict(node.get("record", {}))
            hierarchy = _hierarchy(
                record.get("WSH hierarchy category", candidate.get("hierarchy"))
            )
            review = reviews.get(intervention_id)
            confirmed = _site_confirmed(review)
            plan, plan_issues = _implementation_plan(plans.get(intervention_id))
            assumption, assumption_issues = _validate_productivity_assumption(
                assumptions.get(intervention_id)
            )
            if assumption and assumption["source_type"] == "QUANTIFIED_EVIDENCE":
                missing_sources = [
                    source_id
                    for source_id in assumption["evidence_ids"]
                    if not graph.has_node(source_id)
                    or graph.nodes[source_id].get("node_type") != "evidence_source"
                ]
                if missing_sources:
                    assumption = None
                    assumption_issues.append(
                        "Quantified productivity evidence references unknown graph sources: "
                        + ", ".join(missing_sources)
                    )
            source_ids = list(
                dict.fromkeys(
                    [
                        *_source_ids(record.get("Source IDs")),
                        *_split_values(candidate.get("source_ids")),
                    ]
                )
            )
            if assumption and assumption["source_type"] == "QUANTIFIED_EVIDENCE":
                source_ids.extend(
                    source_id
                    for source_id in assumption["evidence_ids"]
                    if source_id not in source_ids
                )
            linked_control_ids = _split_values(record.get("Linked control IDs"))
            linked_controls: list[dict[str, Any]] = []
            for control_id in linked_control_ids:
                control = knowledge_by_id.get(control_id)
                if not control and graph.has_node(control_id):
                    control = {
                        "id": control_id,
                        "label": graph.nodes[control_id].get("label", control_id),
                    }
                if control:
                    control_record = _record(graph, control_id)
                    linked_controls.append(
                        {
                            "control_id": control_id,
                            "name": control.get("label"),
                            "hierarchy": _hierarchy(
                                control_record.get(
                                    "Hierarchy of control (SRC-01 Fig.3 / App.C)"
                                )
                            ),
                            "candidate_only": True,
                            "source_ids": _source_ids(
                                control_record.get("Source IDs")
                            ),
                        }
                    )

            if intervention_id not in schedule_cache:
                schedule_cache[intervention_id] = _schedule_comparison(
                    schedule_runner=schedule_runner,
                    schedule_input=schedule_input,
                    schedule_activity_id=schedule_activity_id,
                    implementation_plan=plan,
                    assumption=assumption,
                )
            schedule = schedule_cache[intervention_id]
            if plan["approval_status"] != "APPROVED":
                plan_issues.append(
                    "Implementation plan requires explicit approval before it is feasible."
                )
            feasible = confirmed and not plan_issues and not assumption_issues
            if not assessment_authorizes_proceed:
                safety_status = "DO_NOT_PROCEED_PENDING_WSH_GATES"
            elif not confirmed:
                safety_status = "SITE_APPLICABILITY_REVIEW_REQUIRED"
            elif not feasible:
                safety_status = "IMPLEMENTATION_PLAN_INCOMPLETE"
            else:
                safety_status = "REVIEWED_APPLICABLE_NOT_WORK_AUTHORIZATION"
            section = _text(record.get("Primary source location")) or None
            reason = _text(record.get("Mechanism of action")) or _text(
                record.get("Expected safety benefit (qualitative)")
            )
            recommendation = {
                "wbs_code": code,
                "wbs_name": knowledge["activity"].get("label"),
                "application_activity_id": knowledge.get("application_id"),
                "hazard_id": hazard_id,
                "climate_hazard": hazard_name,
                "wsh_risk_classification": hazard_assessment.get(
                    "residual", {}
                ).get("risk_level"),
                "wsh_assessment_status": hazard_assessment.get(
                    "assessment_status", assessment.get("assessment_status")
                ),
                "wsh_authorization_status": assessment.get(
                    "authorization_status"
                ),
                "intervention_id": intervention_id,
                "recommended_intervention": record.get(
                    "Intervention", candidate.get("name")
                ),
                "reason_for_recommendation": reason or None,
                "intervention_stage": record.get("Stage"),
                "hierarchy_of_controls_category": hierarchy,
                "candidate_only": True,
                "safety_control_or_productivity_improvement": (
                    "SAFETY_CONTROL_AND_RESILIENCE_INTERVENTION"
                    if _text(record.get("Expected safety benefit (qualitative)"))
                    else "OPTIONAL_PRODUCTIVITY_IMPROVEMENT"
                ),
                "applicability": {
                    "status": (
                        "CONFIRMED_APPLICABLE"
                        if confirmed
                        else "REQUIRES_WORK_METHOD_AND_SITE_REVIEW"
                    ),
                    "review": dict(review) if review else None,
                },
                "linked_safety_controls": linked_controls,
                "required_implementation_actions": _split_values(
                    record.get("Implementation requirements")
                ),
                "implementation_plan": plan,
                "implementation_plan_issues": plan_issues,
                "estimated_productivity_impact": (
                    {
                        "source_type": assumption["source_type"],
                        "status": assumption["status"],
                        "rationale": assumption["rationale"],
                        "daily_profile": assumption["adjusted_profile"],
                    }
                    if assumption
                    else None
                ),
                "productivity_assumption_issues": assumption_issues,
                "schedule_evaluation": schedule,
                "potential_project_delay_avoided": schedule[
                    "potential_project_delay_avoided_workdays"
                ],
                "potential_project_delay_avoided_unit": "PROJECT_WORKDAYS",
                "safety_acceptability": safety_status,
                "feasible": feasible,
                "work_authorization_granted": False,
                "residual_wsh_assessment_status": "REASSESSMENT_REQUIRED",
                "residual_wsh_risk_level": (
                    hazard_assessment.get("residual", {}).get("risk_level")
                ),
                "residual_risk_changed_by_recommendation": False,
                "source_ids": source_ids,
                "source_section": section,
                "sources": _source_details(graph, source_ids, section),
                "evidence_status": node.get("evidence_status"),
                "evidence_category": record.get("Evidence category"),
                "potential_performance_benefit": record.get(
                    "Potential performance benefit"
                ),
                "expected_safety_benefit": record.get(
                    "Expected safety benefit (qualitative)"
                ),
                "residual_risk_considerations": record.get(
                    "Residual risk considerations"
                ),
                "mechanism_of_action": record.get("Mechanism of action"),
                "effectiveness": record.get("Effectiveness"),
                "uncertainty": (
                    "No numerical intervention effectiveness is established by the graph."
                    if not assumption
                    else (
                        "Scenario assumption; not empirical validation."
                        if assumption["source_type"]
                        == "EXPLICIT_SCENARIO_ASSUMPTION"
                        else "Quantified source requires review for applicability."
                    )
                ),
                "approval_status": plan["approval_status"],
            }
            recommendations.append(recommendation)

    safety_rank = {
        "REVIEWED_APPLICABLE_NOT_WORK_AUTHORIZATION": 0,
        "IMPLEMENTATION_PLAN_INCOMPLETE": 1,
        "SITE_APPLICABILITY_REVIEW_REQUIRED": 2,
        "DO_NOT_PROCEED_PENDING_WSH_GATES": 3,
    }
    recommendations.sort(
        key=lambda item: (
            safety_rank[item["safety_acceptability"]],
            not item["feasible"],
            -int(item["potential_project_delay_avoided"] or 0),
            _HIERARCHY_ORDER[item["hierarchy_of_controls_category"]],
            item["intervention_id"],
        )
    )
    return {
        "schema_version": "1.0",
        "report_type": "WSH_CLIMATE_INTERVENTION_RECOMMENDATIONS",
        "wbs_code": code,
        "wbs_name": knowledge["activity"].get("label"),
        "application_activity_id": knowledge.get("application_id"),
        "wsh_assessment": assessment,
        "hazards": [
            {
                "hazard_id": hazard_id,
                "climate_hazard": knowledge_by_id.get(hazard_id, {}).get(
                    "label", hazard_assessment.get("hazard_name")
                ),
                "unresolved": hazard_assessment.get("unresolved", False),
                "assessment_status": hazard_assessment.get("assessment_status"),
                "initial_risk": hazard_assessment.get("initial"),
                "residual_risk": hazard_assessment.get("residual"),
                "candidate_controls": hazard_assessment.get(
                    "candidate_controls", ()
                ),
                "candidate_interventions": hazard_assessment.get(
                    "candidate_interventions", ()
                ),
            }
            for hazard_id, hazard_assessment in hazards_by_id.items()
        ],
        "stop_work_and_restart_gates": gates,
        "primary_wsh_safety_references": _source_details(
            graph,
            ("SRC-01", "SRC-02"),
        ),
        "wsh_assessment_authorizes_proceed": assessment_authorizes_proceed,
        "continuation_authorized": False,
        "weather_control_candidates": _weather_controls_for_activity(graph, code),
        "recommendations": recommendations,
        "ranking_basis": [
            "Feasibility and site-verified safety applicability",
            "WSH authorization and mandatory stop-work gates",
            "Potential project delay avoided in project-calendar workdays",
            "Hierarchy of controls",
        ],
        "wsh_risk_and_schedule_disruption_are_separate": True,
        "warnings": [
            "Knowledge-graph records are candidates, not approved site-specific WSH assessments.",
            "Recommendations do not authorize work or lower residual WSH risk.",
            "Qualitative intervention benefits are not converted into numeric productivity effects.",
        ],
    }


def recommend_for_activities(
    wbs_codes: Sequence[str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Return one report per canonical activity, preserving activity boundaries."""
    reports = [
        recommend_activity_interventions(code, **kwargs)
        for code in dict.fromkeys(_text(item).upper() for item in wbs_codes)
    ]
    return {
        "schema_version": "1.0",
        "report_type": "WSH_CLIMATE_INTERVENTION_RECOMMENDATIONS",
        "activities": reports,
        "recommendations": [
            recommendation
            for report in reports
            for recommendation in report["recommendations"]
        ],
        "wsh_risk_and_schedule_disruption_are_separate": True,
    }


__all__ = ["recommend_activity_interventions", "recommend_for_activities"]
