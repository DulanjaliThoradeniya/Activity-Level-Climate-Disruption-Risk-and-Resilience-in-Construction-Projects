"""Build a static, candidate-only WBS intervention knowledge bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .activity_mapper import build_activity_mapping, get_activity_knowledge
from .graph_loader import DEFAULT_WORKBOOK, load_knowledge_graph
from .graph_validator import validate_graph
from .wsh_assessment import assess_activity, load_code_example_matrix

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "activity-intervention-knowledge.json"


def _record(graph: Any, node_id: str) -> dict[str, Any]:
    return dict(graph.nodes[node_id].get("record", {}))


def _source_records(graph: Any, source_ids: list[str]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source_id in dict.fromkeys(source_ids):
        if not graph.has_node(source_id):
            continue
        record = _record(graph, source_id)
        sources.append(
            {
                "source_id": source_id,
                "reference": record.get("APA 7 reference"),
                "document_reference": record.get("Document reference"),
                "sections": record.get("Sections / pages used"),
                "verification_status": record.get("Verification status"),
                "caution": record.get("Relevance and caution"),
            }
        )
    return sources


def build_intervention_bundle(
    workbook_path: str | Path = DEFAULT_WORKBOOK,
) -> dict[str, Any]:
    """Create static graph facts for the existing 69 canonical WBS activities."""
    graph = load_knowledge_graph(workbook_path)
    mapping = build_activity_mapping(workbook_path)
    if mapping.issues or len(mapping.mapping) != 69:
        raise ValueError(
            "The application WBS mapping must be complete before building the bundle; "
            f"mapped={len(mapping.mapping)}, issues={mapping.issues!r}."
        )
    matrix = load_code_example_matrix(workbook_path)
    quality = validate_graph(graph, mapping)
    activities: dict[str, dict[str, Any]] = {}

    for wbs_code, application_id in sorted(mapping.mapping.items()):
        knowledge = get_activity_knowledge(wbs_code, graph=graph)
        assessment = assess_activity(graph, wbs_code)
        hazards_by_id = {
            hazard["hazard_id"]: hazard
            for hazard in assessment["hazard_assessments"]
        }
        hazards: list[dict[str, Any]] = []
        interventions: dict[str, dict[str, Any]] = {}
        controls: dict[str, dict[str, Any]] = {}
        for hazard_id, hazard_assessment in sorted(hazards_by_id.items()):
            hazard_node = graph.nodes.get(hazard_id, {})
            hazard_record = _record(graph, hazard_id)
            hazards.append(
                {
                    "hazard_id": hazard_id,
                    "name": hazard_node.get("label", hazard_id),
                    "definition": hazard_record.get("Definition"),
                    "evidence_status": hazard_node.get("evidence_status"),
                    "source_ids": list(hazard_node.get("source_ids", ())),
                }
            )
            for candidate in hazard_assessment["candidate_interventions"]:
                intervention_id = candidate["intervention_id"]
                node = graph.nodes[intervention_id]
                record = _record(graph, intervention_id)
                item = interventions.setdefault(
                    intervention_id,
                    {
                        "intervention_id": intervention_id,
                        "name": node.get("label", intervention_id),
                        "stage": record.get("Stage"),
                        "hierarchy": candidate.get("hierarchy"),
                        "mechanism_of_action": record.get("Mechanism of action"),
                        "implementation_requirements": record.get(
                            "Implementation requirements"
                        ),
                        "expected_safety_benefit": record.get(
                            "Expected safety benefit (qualitative)"
                        ),
                        "potential_performance_benefit": record.get(
                            "Potential performance benefit"
                        ),
                        "residual_risk_considerations": record.get(
                            "Residual risk considerations"
                        ),
                        "effectiveness": record.get("Effectiveness"),
                        "primary_source_location": record.get(
                            "Primary source location"
                        ),
                        "evidence_category": record.get("Evidence category"),
                        "evidence_status": node.get("evidence_status"),
                        "source_ids": list(node.get("source_ids", ())),
                        "linked_control_ids": [
                            value.strip()
                            for value in str(
                                record.get("Linked control IDs", "")
                            ).replace(",", ";").split(";")
                            if value.strip()
                        ],
                        "candidate_only": True,
                        "changes_wsh_risk_automatically": False,
                        "hazard_ids": [],
                    },
                )
                if hazard_id not in item["hazard_ids"]:
                    item["hazard_ids"].append(hazard_id)
            for candidate in hazard_assessment["candidate_controls"]:
                control_id = candidate["control_id"]
                control_node = graph.nodes.get(control_id, {})
                control_record = _record(graph, control_id)
                item = controls.setdefault(
                    control_id,
                    {
                        "control_id": control_id,
                        "name": control_node.get("label", control_id),
                        "hierarchy": candidate.get("hierarchy"),
                        "description": control_record.get("Description"),
                        "source_location": control_record.get("Source location"),
                        "source_ids": list(control_node.get("source_ids", ())),
                        "evidence_status": control_node.get("evidence_status"),
                        "candidate_only": True,
                        "hazard_ids": [],
                    },
                )
                if hazard_id not in item["hazard_ids"]:
                    item["hazard_ids"].append(hazard_id)

        evidence = [
            {
                "source_id": source["id"],
                "reference": source.get("record", {}).get("APA 7 reference"),
                "document_reference": source.get("record", {}).get(
                    "Document reference"
                ),
                "sections": source.get("record", {}).get("Sections / pages used"),
                "verification_status": source.get("record", {}).get(
                    "Verification status"
                ),
                "caution": source.get("record", {}).get("Relevance and caution"),
            }
            for source in knowledge["evidence"]
        ]
        activities[wbs_code] = {
            "wbs_code": wbs_code,
            "application_activity_id": application_id,
            "name": knowledge["activity"].get("label"),
            "stage": knowledge["activity"].get("record", {}).get("Stage"),
            "scope": knowledge["activity"].get("record", {}).get("Scope note"),
            "hazards": hazards,
            "consequences": knowledge["consequences"],
            "controls": sorted(controls.values(), key=lambda item: item["control_id"]),
            "interventions": sorted(
                interventions.values(), key=lambda item: item["intervention_id"]
            ),
            "evidence": evidence,
            "wsh_assessment": {
                "status": assessment["assessment_status"],
                "authorization_status": assessment["authorization_status"],
                "risk_matrix_version": matrix.version,
                "organization_approved_matrix": matrix.organization_approved,
                "severity": None,
                "likelihood": None,
                "rpn": None,
                "assessment_records_available": bool(
                    knowledge["wsh_assessment_records"]
                    or knowledge["approved_wsh_assessments"]
                ),
            },
        }

    return {
        "schema_version": 1,
        "bundle_type": "STATIC_CANDIDATE_KNOWLEDGE_ONLY",
        "source_workbook": Path(workbook_path).name,
        "activities": activities,
        "graph_quality": {
            **quality.summary,
            "unresolved_link_count": len(quality.missing_links),
            "unresolved_node_ids": sorted(
                node_id
                for node_id, attributes in graph.nodes(data=True)
                if not attributes.get("defined")
            ),
            "data_quality_issues": quality.data_quality_issues,
        },
        "safety_notice": (
            "Graph relationships and intervention records are candidates, not approved "
            "site-specific WSH assessments. The workbook contains no organization-approved "
            "risk matrix or project-approved activity assessments. Severity, likelihood, "
            "residual ratings, implementation approvals, and numeric intervention "
            "effectiveness are not inferred."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a static candidate-only graph knowledge JSON for the C-RESIST browser UI."
        )
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    bundle = build_intervention_bundle(args.workbook)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote candidate-only knowledge for {len(bundle['activities'])} WBS activities "
        f"to {output}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
