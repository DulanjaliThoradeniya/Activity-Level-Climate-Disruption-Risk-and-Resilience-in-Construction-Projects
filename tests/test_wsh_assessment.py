"""Tests for transparent WSH assessment, matrix gating, and authorization."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from risk_engine.graph_loader import load_knowledge_graph
from risk_engine.wsh_assessment import (
    WSH_ASSESSMENT_JSON_SCHEMA,
    assess_activity,
    load_code_example_matrix,
    load_wsh_rating_scales,
    reassess_activity,
)
from risk_engine.wsh_schemas import RiskMatrix

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "knowledge_graph" / "Knowledge Graph Project.xlsx"


def test_matrix() -> RiskMatrix:
    """A synthetic 25-cell fixture to test lookup, not a proposed WSH matrix."""
    levels = ("Low", "Medium", "High")
    classifications = {
        (severity, likelihood): levels[(severity + likelihood) % len(levels)]
        for severity in range(1, 6)
        for likelihood in range(1, 6)
    }
    classifications[(5, 5)] = "High"
    classifications[(4, 4)] = "High"
    classifications[(2, 2)] = "Low"
    return RiskMatrix(
        version="TEST-FIXTURE-NOT-FOR-WORKPLACE-USE",
        classifications=classifications,
        organization_approved=True,
        approved_by="Test fixture only",
        approval_date="2026-01-01",
        source="UNIT_TEST_ONLY",
    )


def assessment_record(
    severity: int = 2,
    likelihood: int = 2,
    residual_severity: int | None = None,
    residual_likelihood: int | None = None,
    **overrides: object,
) -> dict[str, object]:
    residual_severity = (
        severity if residual_severity is None else residual_severity
    )
    residual_likelihood = (
        likelihood if residual_likelihood is None else residual_likelihood
    )
    value: dict[str, object] = {
        "site_conditions": {
            "work_area": "Documented test work area",
            "weather_and_surface": "Observed conditions recorded by assessor",
        },
        "assessor": "Competent assessor (test fixture)",
        "approval_status": "APPROVED",
        "approved_by": "Manager (test fixture)",
        "approval_date": "2026-02-01",
        "existing_controls_considered": True,
        "existing_control_ids": ["C-01"],
        "evidence": ["TEST-EVIDENCE-INITIAL"],
        "controls": [
            {
                "control_id": "C-01",
                "hierarchy": "ENGINEERING",
                "implementation_status": "VERIFIED",
                "approval_status": "APPROVED",
                "evidence": "TEST-IMPLEMENTATION-EVIDENCE",
                "verified_by": "Verifier (test fixture)",
                "verification_date": "2026-02-01",
            }
        ],
        "initial": {
            "severity": severity,
            "likelihood": likelihood,
            "severity_rationale": "Test-only, assessor-provided severity rationale.",
            "likelihood_rationale": (
                "Test-only, injury/ill-health likelihood rationale; "
                "not a rainfall probability."
            ),
            "evidence": ["TEST-EVIDENCE-INITIAL"],
        },
        "residual": {
            "severity": residual_severity,
            "likelihood": residual_likelihood,
            "severity_rationale": "Test-only residual severity rationale.",
            "likelihood_rationale": "Test-only residual likelihood rationale.",
            "evidence": ["TEST-EVIDENCE-RESIDUAL"],
            "control_effect_evidence": "Test-only evidence for manually entered residual rating.",
        },
    }
    value.update(overrides)
    return value


class WSHAssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = load_knowledge_graph(WORKBOOK)
        cls.matrix = test_matrix()

    def test_code_example_matrix_is_loaded_as_unapproved_template(self) -> None:
        matrix = load_code_example_matrix(WORKBOOK)
        self.assertEqual(len(matrix.classifications), 25)
        self.assertFalse(matrix.organization_approved)
        self.assertTrue(matrix.is_example_template)
        self.assertTrue(matrix.version.startswith("SRC-01-2021"))
        self.assertIsNone(matrix.classify(5, 5))
        self.assertEqual(matrix.candidate_classification(5, 5), "High")

    def test_scales_use_workbook_definitions_and_injury_likelihood(self) -> None:
        scales = load_wsh_rating_scales(WORKBOOK)
        self.assertEqual(set(scales["severity"]), {"1", "2", "3", "4", "5"})
        self.assertEqual(set(scales["likelihood"]), {"1", "2", "3", "4", "5"})
        self.assertEqual(scales["severity"]["5"]["label"], "Catastrophic")
        self.assertEqual(scales["likelihood"]["1"]["label"], "Rare")
        self.assertIn("not rainfall probability", scales["likelihood_basis"])

    def test_all_25_matrix_cells_calculate_rpn_and_configured_class(self) -> None:
        for severity in range(1, 6):
            for likelihood in range(1, 6):
                with self.subTest(severity=severity, likelihood=likelihood):
                    output = assess_activity(
                        self.graph,
                        "SP-01",
                        {"H08": assessment_record(severity, likelihood)},
                        self.matrix,
                    )
                    hazard = next(
                        item
                        for item in output["hazard_assessments"]
                        if item["hazard_id"] == "H08"
                    )
                    expected = self.matrix.classifications[
                        (severity, likelihood)
                    ]
                    self.assertEqual(hazard["initial"]["rpn"], severity * likelihood)
                    self.assertEqual(hazard["initial"]["risk_level"], expected)
                    self.assertEqual(
                        hazard["residual"]["risk_level"], expected
                    )
                    self.assertEqual(
                        hazard["initial"]["classification_basis"],
                        "ORGANIZATION_APPROVED_MATRIX",
                    )

    def test_missing_ratings_or_assessment_evidence_requires_ra_review(self) -> None:
        output = assess_activity(
            self.graph,
            "SP-01",
            {"H08": {"initial": {"severity": 3}}},
            self.matrix,
        )
        hazard = next(
            item
            for item in output["hazard_assessments"]
            if item["hazard_id"] == "H08"
        )
        self.assertEqual(hazard["assessment_status"], "REQUIRES_RA_REVIEW")
        self.assertEqual(hazard["authorization_status"], "NOT_AUTHORIZED_REQUIRES_RA_REVIEW")
        self.assertIsNone(hazard["initial"]["rpn"])
        self.assertIn("initial_likelihood_must_be_an_integer_from_1_to_5", hazard["review_reason"])

    def test_unapproved_code_example_never_becomes_operational_classification(self) -> None:
        output = assess_activity(
            self.graph,
            "SP-01",
            {"H08": assessment_record(5, 5)},
            load_code_example_matrix(WORKBOOK),
        )
        hazard = next(
            item
            for item in output["hazard_assessments"]
            if item["hazard_id"] == "H08"
        )
        self.assertIsNone(hazard["initial"]["risk_level"])
        self.assertEqual(hazard["initial"]["candidate_classification"], "High")
        self.assertEqual(
            hazard["initial"]["classification_basis"],
            "COP_TABLE_2_EXAMPLE_NOT_APPROVED",
        )
        self.assertEqual(hazard["assessment_status"], "REQUIRES_RA_REVIEW")
        self.assertEqual(output["activity_risk_level"], None)
        self.assertNotEqual(output["authorization_status"], "AUTHORIZED_TO_PROCEED")

    def test_initial_and_residual_risk_are_separate_and_not_auto_reduced(self) -> None:
        item = assessment_record(
            4,
            4,
            residual_severity=4,
            residual_likelihood=4,
            controls=[
                {
                    "control_id": "C-01",
                    "hierarchy": "ENGINEERING",
                    "implementation_status": "RECOMMENDED",
                    "approval_status": "PENDING",
                }
            ],
        )
        output = assess_activity(
            self.graph, "SP-01", {"H08": item}, self.matrix
        )
        hazard = next(
            item for item in output["hazard_assessments"] if item["hazard_id"] == "H08"
        )
        self.assertEqual(hazard["initial"]["rpn"], 16)
        self.assertEqual(hazard["residual"]["rpn"], 16)
        self.assertEqual(hazard["initial"]["severity"], 4)
        self.assertEqual(hazard["residual"]["severity"], 4)
        self.assertEqual(hazard["implemented_controls"][0]["implemented"], False)
        self.assertEqual(
            hazard["authorization_status"],
            "NOT_AUTHORIZED_HIGH_RISK",
        )

    def test_multiple_hazards_are_assessed_and_highest_approved_is_summary(self) -> None:
        output = assess_activity(
            self.graph,
            "SP-01",
            {
                "H08": assessment_record(1, 1),
                "H09": assessment_record(2, 2),
                "H10": assessment_record(5, 5),
            },
            self.matrix,
        )
        hazard_ids = {item["hazard_id"] for item in output["hazard_assessments"]}
        self.assertTrue({"H08", "H09", "H10"}.issubset(hazard_ids))
        self.assertEqual(output["activity_risk_hazard_id"], "H10")
        self.assertEqual(output["activity_risk_level"], "High")
        self.assertEqual(output["authorization_status"], "NOT_AUTHORIZED_HIGH_RISK")

    def test_unresolved_hazards_are_retained_and_flagged(self) -> None:
        graph = self.graph.copy()
        graph.add_node(
            "H-UNKNOWN",
            id="H-UNKNOWN",
            label="H-UNKNOWN",
            node_type="unresolved_reference",
            defined=False,
        )
        graph.add_edge(
            "SP-01",
            "H-UNKNOWN",
            key="TEST-UNRESOLVED-HAZARD",
            relationship_id="TEST-UNRESOLVED-HAZARD",
            relationship_type="mayExperience",
            predicate="mayExperience",
            relationship_origin="Knowledge_Triples",
        )
        output = assess_activity(graph, "SP-01", {}, self.matrix)
        self.assertIn("H-UNKNOWN", output["unresolved_hazards"])
        unresolved = next(
            item
            for item in output["hazard_assessments"]
            if item["hazard_id"] == "H-UNKNOWN"
        )
        self.assertTrue(unresolved["unresolved"])
        self.assertEqual(
            unresolved["assessment_status"], "UNRESOLVED_HAZARD"
        )
        self.assertNotEqual(output["authorization_status"], "AUTHORIZED_TO_PROCEED")

    def test_high_risk_is_not_authorized_without_reduction_and_approval(self) -> None:
        output = assess_activity(
            self.graph,
            "SP-01",
            {
                "H08": assessment_record(
                    5, 5, residual_severity=5, residual_likelihood=5
                )
            },
            self.matrix,
        )
        hazard = next(
            item for item in output["hazard_assessments"] if item["hazard_id"] == "H08"
        )
        self.assertEqual(hazard["initial"]["risk_level"], "High")
        self.assertEqual(hazard["residual"]["risk_level"], "High")
        self.assertEqual(
            hazard["authorization_status"], "NOT_AUTHORIZED_HIGH_RISK"
        )
        self.assertIn("Stop or do not commence", hazard["action_required"])

    def test_verified_approved_controls_and_residual_rating_allow_reduced_risk(self) -> None:
        output = assess_activity(
            self.graph,
            "SP-01",
            {
                "H08": assessment_record(
                    5, 5, residual_severity=2, residual_likelihood=2
                )
            },
            self.matrix,
        )
        hazard = next(
            item for item in output["hazard_assessments"] if item["hazard_id"] == "H08"
        )
        self.assertEqual(hazard["initial"]["risk_level"], "High")
        self.assertEqual(hazard["residual"]["risk_level"], "Low")
        self.assertEqual(hazard["assessment_status"], "APPROVED")
        self.assertEqual(
            hazard["authorization_status"], "AUTHORIZED_TO_PROCEED"
        )

    def test_recommended_intervention_does_not_change_residual_rating(self) -> None:
        item = assessment_record(
            5,
            5,
            residual_severity=5,
            residual_likelihood=5,
        )
        before = assess_activity(
            self.graph, "SP-01", {"H08": item}, self.matrix
        )
        item["recommended_intervention_ids"] = ["RI-P01"]
        after = assess_activity(
            self.graph, "SP-01", {"H08": item}, self.matrix
        )
        self.assertEqual(
            before["hazard_assessments"][0]["residual"]["rpn"],
            after["hazard_assessments"][0]["residual"]["rpn"],
        )
        self.assertEqual(
            after["hazard_assessments"][0]["authorization_status"],
            "NOT_AUTHORIZED_HIGH_RISK",
        )

    def test_controls_are_ranked_by_the_hierarchy_of_controls(self) -> None:
        output = assess_activity(self.graph, "SP-01", {}, self.matrix)
        hazard = next(
            item for item in output["hazard_assessments"] if item["hazard_id"] == "H08"
        )
        ranks = {
            "ELIMINATION": 0,
            "SUBSTITUTION": 1,
            "ENGINEERING": 2,
            "ADMINISTRATIVE": 3,
            "PPE": 4,
            "UNCLASSIFIED": 5,
        }
        values = [ranks[item["hierarchy"]] for item in hazard["candidate_controls"]]
        self.assertEqual(values, sorted(values))
        self.assertTrue(
            all(item["candidate_only"] for item in hazard["candidate_controls"])
        )

    def test_control_approval_implementation_and_assessment_approval_are_required(self) -> None:
        item = assessment_record(2, 2)
        item["approval_status"] = "PENDING"
        item["approval_date"] = None
        item["approved_by"] = None
        item["controls"] = [
            {
                "control_id": "C-01",
                "hierarchy": "ENGINEERING",
                "implementation_status": "IMPLEMENTED",
                "approval_status": "PENDING",
                "evidence": "Some evidence, but no verification",
            }
        ]
        output = assess_activity(
            self.graph, "SP-01", {"H08": item}, self.matrix
        )
        hazard = next(
            item for item in output["hazard_assessments"] if item["hazard_id"] == "H08"
        )
        self.assertEqual(
            hazard["assessment_status"], "ASSESSED_PENDING_APPROVAL"
        )
        self.assertEqual(
            hazard["authorization_status"],
            "NOT_AUTHORIZED_PENDING_CONTROLS_OR_APPROVAL",
        )
        self.assertFalse(hazard["implemented_controls"][0]["approved"])
        self.assertFalse(hazard["implemented_controls"][0]["verified"])

    def test_reassessment_uses_updated_site_conditions_and_ratings(self) -> None:
        first = assessment_record(2, 2)
        second = assessment_record(
            4,
            4,
            residual_severity=4,
            residual_likelihood=4,
            site_conditions={"work_area": "Changed and newly exposed condition"},
            review_reason="Site conditions changed; reassessment recorded.",
        )
        before = assess_activity(
            self.graph, "SP-01", {"H08": first}, self.matrix
        )
        after = reassess_activity(
            self.graph, "SP-01", {"H08": second}, self.matrix
        )
        before_hazard = next(
            item
            for item in before["hazard_assessments"]
            if item["hazard_id"] == "H08"
        )
        after_hazard = next(
            item
            for item in after["hazard_assessments"]
            if item["hazard_id"] == "H08"
        )
        self.assertNotEqual(
            before_hazard["initial"]["rpn"], after_hazard["initial"]["rpn"]
        )
        self.assertEqual(after_hazard["site_conditions"], second["site_conditions"])
        self.assertEqual(
            after_hazard["review_reason"],
            "Site conditions changed; reassessment recorded.",
        )

    def test_invalid_ratings_and_incomplete_matrices_are_rejected(self) -> None:
        invalid_output = assess_activity(
            self.graph,
            "SP-01",
            {"H08": assessment_record(severity=0)},
            self.matrix,
        )
        invalid_hazard = next(
            item
            for item in invalid_output["hazard_assessments"]
            if item["hazard_id"] == "H08"
        )
        self.assertEqual(
            invalid_hazard["assessment_status"], "REQUIRES_RA_REVIEW"
        )
        self.assertIsNone(invalid_hazard["initial"]["rpn"])
        with self.assertRaises(ValueError):
            RiskMatrix(
                version="INCOMPLETE",
                classifications={(1, 1): "Low"},  # type: ignore[dict-item]
            )

    def test_result_is_json_compatible_and_has_dialog_schema_fields(self) -> None:
        output = assess_activity(
            self.graph,
            "SP-01",
            {"H08": assessment_record()},
            self.matrix,
        )
        encoded = json.dumps(output)
        decoded = json.loads(encoded)
        for field in WSH_ASSESSMENT_JSON_SCHEMA["required"]:
            self.assertIn(field, decoded)
        self.assertEqual(decoded["assessment_type"], "WSH_ACTIVITY_RISK_ASSESSMENT")
        self.assertEqual(set(decoded["rating_scales"]["severity"]), {"1", "2", "3", "4", "5"})
        hazard = next(
            item
            for item in decoded["hazard_assessments"]
            if item["hazard_id"] == "H08"
        )
        self.assertIn("assessor", hazard)
        self.assertIn("approval_date", hazard)
        self.assertIn("matrix", hazard)


if __name__ == "__main__":
    unittest.main()
