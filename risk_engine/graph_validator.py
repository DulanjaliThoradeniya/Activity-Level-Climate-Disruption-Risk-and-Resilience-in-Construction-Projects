"""Validation and command-line reporting for the C-RESIST knowledge graph."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping

import networkx as nx

from .activity_mapper import ActivityMappingReport, build_activity_mapping
from .graph_loader import DEFAULT_WORKBOOK, load_knowledge_graph

_ONTOLOGY_TYPE_MAP = {
    "ConstructionActivity": "construction_activity",
    "ClimateHazard": "climate_hazard",
    "ExposureCondition": "exposure_condition",
    "ImpactMechanism": "impact_mechanism",
    "SafetyConsequence": "safety_consequence",
    "PerformanceConsequence": "performance_consequence",
    "RainfallVariable": "rainfall_variable",
    "TriggerRule": "trigger_rule",
    "ControlMeasure": "control_measure",
    "ResilienceIntervention": "resilience_intervention",
    "EvidenceSource": "evidence_source",
    "RiskAssessment": "wsh_assessment_template",
    "ConstructionStage": "construction_stage",
    "RainfallEvent": "rainfall_event",
    "ResilienceStage": "resilience_stage",
    "HierarchyLevel": "hierarchy_level",
    "ActivityHazardLink": "activity_hazard_link",
    "WeatherObservation": "weather_observation",
    "WeatherForecast": "weather_forecast",
}


@dataclass
class ValidationReport:
    valid: bool
    summary: dict[str, Any] = field(default_factory=dict)
    missing_links: list[dict[str, Any]] = field(default_factory=list)
    invalid_relationships: list[dict[str, Any]] = field(default_factory=list)
    duplicate_ids: list[dict[str, Any]] = field(default_factory=list)
    missing_activity_mappings: list[str] = field(default_factory=list)
    data_quality_issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _domain_types(value: str) -> set[str]:
    types = set()
    for item in re.split(r"[\n,;|]+", value):
        item = item.strip()
        if not item or item.lower() == "any":
            continue
        if item.lower().startswith("literal"):
            types.add("literal")
        else:
            types.add(item)
    return types


def _edge_problem(
    graph: nx.MultiDiGraph,
    source_id: str,
    target_id: str,
    edge: Mapping[str, Any],
    properties: Mapping[str, Mapping[str, str]],
) -> dict[str, Any] | None:
    if edge.get("relationship_origin") != "Knowledge_Triples":
        return None

    predicate = str(edge.get("predicate", "")).strip()
    relationship_id = edge.get("relationship_id")
    if not predicate:
        return {
            "relationship_id": relationship_id,
            "issue": "missing_relationship_type",
        }
    property_schema = properties.get(predicate)
    if property_schema is None:
        return {
            "relationship_id": relationship_id,
            "predicate": predicate,
            "issue": "relationship_not_defined_in_ontology",
        }

    source = graph.nodes[source_id]
    target = graph.nodes[target_id]
    if not source.get("defined") or not target.get("defined"):
        return None

    domain = _domain_types(str(property_schema.get("domain", "")))
    relation_range = _domain_types(str(property_schema.get("range", "")))
    source_type = source.get("node_type")
    target_type = target.get("node_type")
    allowed_source_types = {
        _ONTOLOGY_TYPE_MAP.get(item, item) for item in domain
    }
    allowed_target_types = {
        _ONTOLOGY_TYPE_MAP.get(item, item) for item in relation_range
    }
    if domain and source_type not in allowed_source_types:
        return {
            "relationship_id": relationship_id,
            "predicate": predicate,
            "subject_id": source_id,
            "subject_type": source_type,
            "allowed_subject_types": sorted(allowed_source_types),
            "issue": "domain_type_mismatch",
        }
    if relation_range and target_type not in allowed_target_types:
        return {
            "relationship_id": relationship_id,
            "predicate": predicate,
            "object_id": target_id,
            "object_type": target_type,
            "allowed_object_types": sorted(allowed_target_types),
            "issue": "range_type_mismatch",
        }
    declared_object_type = str(edge.get("object_type", "")).upper()
    if declared_object_type == "LITERAL" and target_type != "literal":
        return {
            "relationship_id": relationship_id,
            "predicate": predicate,
            "object_id": target_id,
            "issue": "literal_target_is_not_typed_literal",
        }
    if declared_object_type == "ENTITY" and target_type == "literal":
        return {
            "relationship_id": relationship_id,
            "predicate": predicate,
            "object_id": target_id,
            "issue": "entity_target_is_typed_literal",
        }
    return None


def validate_graph(
    graph: nx.MultiDiGraph,
    activity_mapping: ActivityMappingReport | Mapping[str, str] | None = None,
) -> ValidationReport:
    """Validate graph endpoints, ontology predicates, IDs, and WBS mappings."""
    diagnostics = graph.graph.get("load_diagnostics", {})
    missing_links: list[dict[str, Any]] = []
    invalid_relationships = list(diagnostics.get("invalid_relationships", ()))
    duplicate_ids = list(diagnostics.get("duplicate_ids", ()))

    for source_id, target_id, edge_key, edge in graph.edges(
        keys=True, data=True
    ):
        for endpoint_id, endpoint_role in (
            (source_id, "subject"),
            (target_id, "object"),
        ):
            node = graph.nodes[endpoint_id]
            if not node.get("defined") and node.get("node_type") != "literal":
                missing_links.append(
                    {
                        "relationship_id": edge.get("relationship_id", edge_key),
                        "relationship_type": edge.get("relationship_type"),
                        "endpoint_role": endpoint_role,
                        "endpoint_id": endpoint_id,
                        "source_sheet": edge.get("source_sheet"),
                    }
                )
        problem = _edge_problem(
            graph,
            source_id,
            target_id,
            edge,
            graph.graph.get("ontology_properties", {}),
        )
        if problem is not None:
            invalid_relationships.append(problem)

    mapping_issues: list[dict[str, Any]] = []
    if isinstance(activity_mapping, ActivityMappingReport):
        mapping_entries = activity_mapping.entries
        mapping_issues = list(activity_mapping.issues)
    elif activity_mapping is not None:
        mapping_entries = activity_mapping
    else:
        mapping_entries = graph.graph.get("activity_mappings", {})

    activity_nodes = {
        node_id
        for node_id, attributes in graph.nodes(data=True)
        if attributes.get("defined")
        and attributes.get("node_type") == "construction_activity"
    }
    missing_activity_mappings = sorted(
        node_id
        for node_id in activity_nodes
        if node_id not in mapping_entries
        or (
            isinstance(mapping_entries[node_id], str)
            and not mapping_entries[node_id]
        )
        or (
            not isinstance(mapping_entries[node_id], str)
            and not getattr(mapping_entries[node_id], "browser_id", None)
        )
    )
    missing_activity_mappings.extend(
        item
        for item in diagnostics.get("missing_activity_mappings", ())
        if item not in missing_activity_mappings
    )

    evidence_ids = {
        node_id
        for node_id, attributes in graph.nodes(data=True)
        if attributes.get("node_type") == "evidence_source"
        and attributes.get("defined")
    }
    missing_evidence_references: list[dict[str, str]] = []
    for node_id, attributes in graph.nodes(data=True):
        for source_id in attributes.get("source_ids", ()):
            if source_id not in evidence_ids:
                missing_evidence_references.append(
                    {"record_id": node_id, "source_id": source_id}
                )
    for source_id, target_id, _, edge in graph.edges(data=True, keys=True):
        for evidence_id in edge.get("source_ids", ()):
            if evidence_id not in evidence_ids:
                missing_evidence_references.append(
                    {
                        "record_id": str(edge.get("relationship_id", source_id)),
                        "source_id": evidence_id,
                    }
                )
    missing_evidence_references = list(
        {
            (item["record_id"], item["source_id"]): item
            for item in missing_evidence_references
        }.values()
    )

    node_types = Counter(
        str(attributes.get("node_type", "untyped"))
        for _, attributes in graph.nodes(data=True)
    )
    relation_types = Counter(
        str(attributes.get("relationship_type", "untyped"))
        for _, _, attributes in graph.edges(data=True)
    )
    evidence_statuses = Counter(
        str(attributes.get("evidence_status"))
        for _, attributes in graph.nodes(data=True)
        if attributes.get("evidence_status")
    )
    evidence_statuses.update(
        str(attributes.get("evidence_status"))
        for _, _, attributes in graph.edges(data=True)
        if attributes.get("evidence_status")
    )

    data_quality_issues = [
        *diagnostics.get("missing_schemas", ()),
        *diagnostics.get("warnings", ()),
        *mapping_issues,
    ]
    if missing_evidence_references:
        data_quality_issues.append(
            {
                "issue": "unresolved_source_references",
                "count": len(missing_evidence_references),
                "examples": missing_evidence_references[:10],
            }
        )
    if graph.graph.get("assessment_record_count", 0) == 0:
        data_quality_issues.append(
            {
                "issue": "no_project_specific_wsh_assessment_records",
                "note": (
                    "Knowledge-graph links and workbook assessment templates are "
                    "not approved WSH assessments."
                ),
            }
        )
    if missing_links:
        data_quality_issues.append(
            {
                "issue": "unresolved_relationship_endpoints",
                "count": len(missing_links),
                "examples": missing_links[:10],
            }
        )
    if duplicate_ids:
        data_quality_issues.append(
            {"issue": "duplicate_ids", "count": len(duplicate_ids)}
        )
    if invalid_relationships:
        data_quality_issues.append(
            {
                "issue": "invalid_relationships",
                "count": len(invalid_relationships),
                "examples": invalid_relationships[:10],
            }
        )
    if missing_activity_mappings:
        data_quality_issues.append(
            {
                "issue": "missing_activity_mappings",
                "count": len(missing_activity_mappings),
                "codes": missing_activity_mappings,
            }
        )

    summary = {
        "source_workbook": graph.graph.get("source_workbook"),
        "worksheets_inspected": len(graph.graph.get("worksheet_schemas", {})),
        "nodes": graph.number_of_nodes(),
        "defined_nodes": sum(
            1 for _, attributes in graph.nodes(data=True) if attributes.get("defined")
        ),
        "unresolved_nodes": sum(
            1 for _, attributes in graph.nodes(data=True) if not attributes.get("defined")
        ),
        "relationships": graph.number_of_edges(),
        "knowledge_triples": graph.graph.get("triple_count", 0),
        "nodes_by_type": dict(sorted(node_types.items())),
        "relationships_by_type": dict(sorted(relation_types.items())),
        "evidence_statuses": dict(sorted(evidence_statuses.items())),
        "mapped_activities": len(activity_nodes) - len(missing_activity_mappings),
        "canonical_activities": len(activity_nodes),
        "assessment_templates": node_types.get("wsh_assessment_template", 0),
        "assessment_records": graph.graph.get("assessment_record_count", 0),
        "approved_assessment_records": graph.graph.get(
            "approved_assessment_record_count", 0
        ),
        "missing_source_references": len(missing_evidence_references),
    }
    valid = not any(
        (
            diagnostics.get("missing_schemas"),
            missing_links,
            invalid_relationships,
            duplicate_ids,
            missing_activity_mappings,
        )
    )
    return ValidationReport(
        valid=valid,
        summary=summary,
        missing_links=missing_links,
        invalid_relationships=invalid_relationships,
        duplicate_ids=duplicate_ids,
        missing_activity_mappings=missing_activity_mappings,
        data_quality_issues=data_quality_issues,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the C-RESIST knowledge graph workbook."
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help="Path to the knowledge graph XLSX workbook.",
    )
    parser.add_argument(
        "--app-source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "refine.js",
        help="Existing application catalogue source used to verify WBS IDs.",
    )
    args = parser.parse_args(argv)

    graph = load_knowledge_graph(args.workbook)
    mappings = build_activity_mapping(args.workbook, args.app_source)
    report = validate_graph(graph, mappings)
    summary = report.summary
    print(f"Knowledge graph validation: {'PASS' if report.valid else 'ISSUES FOUND'}")
    print(f"Worksheets inspected: {summary['worksheets_inspected']}")
    print(
        f"Nodes: {summary['nodes']} ({summary['defined_nodes']} defined, "
        f"{summary['unresolved_nodes']} unresolved); relationships: "
        f"{summary['relationships']} ({summary['knowledge_triples']} workbook triples)"
    )
    print(
        f"Canonical WBS activities mapped: {summary['mapped_activities']}/"
        f"{summary['canonical_activities']}"
    )
    unresolved_ids = sorted({item["endpoint_id"] for item in report.missing_links})
    print(
        f"Unresolved link endpoints: {len(report.missing_links)} across "
        f"{len(unresolved_ids)} missing node IDs; duplicate IDs: "
        f"{len(report.duplicate_ids)}; invalid relationships: "
        f"{len(report.invalid_relationships)}; missing activity mappings: "
        f"{len(report.missing_activity_mappings)}"
    )
    print(
        f"Project-specific WSH assessment records: {summary['assessment_records']} "
        f"({summary['approved_assessment_records']} explicitly approved); "
        f"assessment templates: {summary['assessment_templates']}"
    )
    if unresolved_ids:
        print(f"Missing node definitions: {', '.join(unresolved_ids)}")
    for issue in report.data_quality_issues[:8]:
        if issue.get("issue") in {
            "unresolved_relationship_endpoints",
            "duplicate_ids",
            "invalid_relationships",
            "missing_activity_mappings",
        }:
            continue
        print(f"- {issue.get('issue', 'data_quality_issue')}: {issue}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
