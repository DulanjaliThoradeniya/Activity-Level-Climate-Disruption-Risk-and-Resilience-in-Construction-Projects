"""Tests for workbook loading, WBS mapping, and evidence-preserving paths."""

from pathlib import Path
import unittest

import networkx as nx

from risk_engine.activity_mapper import (
    build_activity_mapping,
    get_activity_knowledge,
)
from risk_engine.graph_loader import (
    DEFAULT_WORKBOOK,
    find_table,
    load_knowledge_graph,
    read_workbook,
)
from risk_engine.graph_validator import validate_graph

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "refine.js"


class GraphLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workbook = read_workbook(DEFAULT_WORKBOOK)
        cls.graph = load_knowledge_graph(DEFAULT_WORKBOOK)
        cls.mapping_report = build_activity_mapping(DEFAULT_WORKBOOK, APP_SOURCE)

    def test_loads_all_workbook_worksheets_and_graph_types(self) -> None:
        self.assertGreaterEqual(len(self.workbook.worksheets), 30)
        self.assertIsInstance(self.graph, nx.MultiDiGraph)
        self.assertGreater(self.graph.number_of_nodes(), 0)
        self.assertGreater(self.graph.number_of_edges(), 0)

    def test_actual_column_signatures_are_discovered(self) -> None:
        self.assertIsNotNone(find_table(self.workbook, "triples"))
        self.assertIsNotNone(find_table(self.workbook, "activity_hazard_links"))
        self.assertIsNotNone(find_table(self.workbook, "code_compliance"))
        self.assertIsNotNone(find_table(self.workbook, "adverse_weather_controls"))
        self.assertIsNotNone(find_table(self.workbook, "operational_gates"))

    def test_every_canonical_wbs_code_maps_to_the_existing_app_id(self) -> None:
        activities = find_table(self.workbook, "activities")
        self.assertIsNotNone(activities)
        expected_codes = {
            row["WBS code"] for row in activities.rows
        }
        self.assertEqual(len(expected_codes), 69)
        self.assertEqual(set(self.mapping_report.entries), expected_codes)
        self.assertEqual(len(self.mapping_report.mapping), 69)
        self.assertEqual(self.mapping_report.issues, [])

        for code in sorted(expected_codes):
            with self.subTest(wbs_code=code):
                entry = self.mapping_report.entries[code]
                self.assertEqual(
                    entry.browser_id,
                    f"wbs_{code.lower().replace('-', '_')}",
                )
                self.assertEqual(entry.browser_id, entry.workbook_browser_id)
                self.assertTrue(self.graph.nodes[code]["defined"])
                self.assertEqual(self.graph.nodes[code]["browser_id"], entry.browser_id)

    def test_preserves_original_relationship_ids_and_evidence_status(self) -> None:
        triples = find_table(self.workbook, "triples")
        self.assertIsNotNone(triples)
        self.assertEqual(self.graph.graph["triple_count"], len(triples.rows))
        first = triples.rows[0]
        source_id = first["Subject ID"]
        target_id = first["Object ID"]
        triple_id = first["Triple ID"]
        self.assertTrue(self.graph.has_edge(source_id, target_id, key=triple_id))
        edge = self.graph.edges[source_id, target_id, triple_id]
        self.assertEqual(edge["predicate"], first["Predicate"])
        self.assertEqual(edge["source_references"], first["Source IDs"])
        self.assertEqual(edge["evidence_status"], first["Validation status"])
        self.assertFalse(edge["is_approved_wsh_assessment"])

    def test_representative_activity_hazard_control_intervention_path(self) -> None:
        knowledge = get_activity_knowledge("SP-01", graph=self.graph)
        hazard_ids = {item["id"] for item in knowledge["hazards"]}
        control_ids = {item["id"] for item in knowledge["controls"]}
        intervention_ids = {item["id"] for item in knowledge["interventions"]}
        consequence_ids = {item["id"] for item in knowledge["consequences"]}

        self.assertIn("H08", hazard_ids)
        self.assertIn("C-01", control_ids)
        self.assertTrue(any(item.startswith("RI-") for item in intervention_ids))
        self.assertTrue(consequence_ids or knowledge["consequences"])
        self.assertTrue(knowledge["evidence"])
        self.assertTrue(knowledge["activity_hazard_link_ids"])

    def test_adverse_weather_candidates_remain_explicitly_unverified(self) -> None:
        candidates = [
            data
            for _, _, data in self.graph.edges(data=True)
            if data.get("relationship_type") == "candidate_wbs_applicability"
        ]
        self.assertTrue(candidates)
        self.assertTrue(all(item["candidate"] for item in candidates))
        self.assertTrue(
            all(item["evidence_status"] == "CANDIDATE_VERIFY" for item in candidates)
        )

    def test_wsh_governance_worksheets_are_loaded_as_distinct_node_types(self) -> None:
        self.assertEqual(self.graph.nodes["CP-01"]["node_type"], "wsh_code_requirement")
        self.assertEqual(
            self.graph.nodes["AW-01"]["node_type"], "adverse_weather_control"
        )
        self.assertEqual(self.graph.nodes["G-01"]["node_type"], "wsh_operational_gate")

    def test_knowledge_graph_and_templates_are_not_approved_assessments(self) -> None:
        self.assertEqual(self.graph.graph["assessment_record_count"], 0)
        self.assertEqual(self.graph.graph["approved_assessment_record_count"], 0)
        templates = [
            attributes
            for _, attributes in self.graph.nodes(data=True)
            if attributes.get("node_type") == "wsh_assessment_template"
        ]
        self.assertTrue(templates)
        self.assertTrue(
            all(not item["is_approved_wsh_assessment"] for item in templates)
        )
        knowledge = get_activity_knowledge("SP-01", graph=self.graph)
        self.assertEqual(knowledge["approved_wsh_assessments"], [])

    def test_validation_reports_graph_counts_and_missing_links(self) -> None:
        report = validate_graph(self.graph, self.mapping_report)
        self.assertGreater(report.summary["knowledge_triples"], 0)
        self.assertEqual(report.summary["canonical_activities"], 69)
        self.assertEqual(report.summary["mapped_activities"], 69)
        self.assertIsInstance(report.missing_links, list)
        self.assertIsInstance(report.data_quality_issues, list)
        self.assertEqual(report.missing_activity_mappings, [])
        self.assertTrue(report.data_quality_issues)

    def test_validator_detects_missing_nodes_duplicate_ids_and_bad_relations(self) -> None:
        graph = nx.MultiDiGraph()
        graph.graph.update(
            {
                "load_diagnostics": {
                    "duplicate_ids": [{"id": "A", "issue": "duplicate_id"}],
                    "invalid_relationships": [],
                    "missing_schemas": [],
                    "warnings": [],
                },
                "ontology_properties": {
                    "mayExperience": {
                        "domain": "ConstructionActivity",
                        "range": "ClimateHazard",
                    }
                },
                "activity_mappings": {},
            }
        )
        graph.add_node("A", node_type="construction_activity", defined=True)
        graph.add_node("B", node_type="evidence_source", defined=True)
        graph.add_node("MISSING", node_type="unresolved_reference", defined=False)
        graph.add_edge(
            "A",
            "B",
            relationship_id="BAD-1",
            relationship_type="mayExperience",
            predicate="mayExperience",
            relationship_origin="Knowledge_Triples",
            object_type="ENTITY",
        )
        graph.add_edge(
            "A",
            "MISSING",
            relationship_id="BAD-2",
            relationship_type="mayExperience",
            predicate="mayExperience",
            relationship_origin="Knowledge_Triples",
            object_type="ENTITY",
        )

        report = validate_graph(graph, {"A": "app_activity"})

        self.assertFalse(report.valid)
        self.assertEqual(len(report.duplicate_ids), 1)
        self.assertTrue(any(item["endpoint_id"] == "MISSING" for item in report.missing_links))
        self.assertTrue(
            any(item["issue"] == "range_type_mismatch" for item in report.invalid_relationships)
        )

    def test_unknown_activity_is_not_silently_substituted(self) -> None:
        with self.assertRaises(KeyError):
            get_activity_knowledge("NOT-A-WBS-CODE", graph=self.graph)


if __name__ == "__main__":
    unittest.main()
