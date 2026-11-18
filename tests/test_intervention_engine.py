from __future__ import annotations

import unittest

from risk_engine.graph_loader import load_knowledge_graph
from risk_engine.intervention_engine import (
    _calendar_workday_delta,
    recommend_activity_interventions,
    recommend_for_activities,
)


class InterventionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = load_knowledge_graph()
        cls.code = "BE-01"
        cls.base = recommend_activity_interventions(
            cls.code, graph=cls.graph
        )
        cls.first = cls.base["recommendations"][0]

    def _plan(self, *, status: str = "APPROVED") -> dict:
        return {
            "required_resources": ["site-assigned resources"],
            "responsible_personnel": "site-assigned responsible person",
            "implementation_time_workdays": 0,
            "recovery_workdays": 0,
            "approval_status": status,
        }

    def _review(self) -> dict:
        return {
            "status": "CONFIRMED_APPLICABLE",
            "reviewer": "test assessor",
            "review_date": "2026-11-10",
            "rationale": "Test-only applicability fixture.",
        }

    def _approved_assessment_fixture(self) -> dict:
        assessment = dict(self.base["wsh_assessment"])
        assessment["assessment_status"] = "APPROVED"
        assessment["authorization_status"] = "AUTHORIZED_TO_PROCEED"
        assessment["matrix"] = {"organization_approved": True, "version": "test"}
        assessment["hazard_assessments"] = [
            dict(item) for item in assessment["hazard_assessments"]
        ]
        return assessment

    def test_graph_paths_retain_hazard_controls_and_source_sections(self) -> None:
        self.assertTrue(self.base["recommendations"])
        self.assertTrue(self.base["hazards"])
        self.assertTrue(
            all(hazard["hazard_id"] for hazard in self.base["hazards"])
        )
        recommendation = self.first
        self.assertTrue(recommendation["hazard_id"])
        self.assertTrue(recommendation["intervention_id"])
        self.assertTrue(recommendation["required_implementation_actions"])
        self.assertIn(
            recommendation["hierarchy_of_controls_category"],
            {"ELIMINATION", "SUBSTITUTION", "ENGINEERING", "ADMINISTRATIVE", "PPE", "UNCLASSIFIED"},
        )
        self.assertTrue(recommendation["source_ids"])
        self.assertTrue(recommendation["sources"])
        self.assertTrue(
            all(source["source_id"] for source in recommendation["sources"])
        )
        self.assertIsNotNone(recommendation["source_section"])
        self.assertTrue(recommendation["candidate_only"])
        self.assertFalse(recommendation["work_authorization_granted"])

    def test_unassessed_activity_is_not_authorized_and_requires_ra_review(self) -> None:
        self.assertFalse(self.base["continuation_authorized"])
        self.assertEqual(
            self.base["wsh_assessment"]["assessment_status"], "REQUIRES_RA_REVIEW"
        )
        self.assertIn(
            "G-01",
            {gate["gate_id"] for gate in self.base["stop_work_and_restart_gates"]},
        )
        self.assertTrue(
            all(
                item["safety_acceptability"] == "DO_NOT_PROCEED_PENDING_WSH_GATES"
                for item in self.base["recommendations"]
            )
        )

    def test_high_risk_and_adverse_weather_restart_gates_block_continuation(self) -> None:
        assessment = self._approved_assessment_fixture()
        assessment["activity_risk_level"] = None
        assessment["authorization_status"] = "NOT_AUTHORIZED_HIGH_RISK"
        assessment["hazard_assessments"][0]["residual"]["risk_level"] = "High"
        assessment["hazard_assessments"][0][
            "authorization_status"
        ] = "NOT_AUTHORIZED_HIGH_RISK"
        conditions = {
            "adverse_weather_occurred": True,
            "safe_restart_verified": False,
        }
        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            wsh_assessment=assessment,
            site_conditions=conditions,
        )
        gates = {gate["gate_id"] for gate in report["stop_work_and_restart_gates"]}
        self.assertIn("G-03", gates)
        self.assertIn("G-08", gates)
        self.assertFalse(report["continuation_authorized"])
        self.assertTrue(
            all(
                not item["work_authorization_granted"]
                for item in report["recommendations"]
            )
        )

    def test_site_specific_stop_gate_conditions_are_not_inferred(self) -> None:
        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            site_conditions={"below_ground_work": True},
        )
        below_ground_gate = next(
            gate
            for gate in report["stop_work_and_restart_gates"]
            if gate["gate_id"] == "G-04"
        )
        self.assertFalse(below_ground_gate["triggered"])
        self.assertEqual(below_ground_gate["condition_status"], "NOT_ASSESSED")
        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            site_conditions={
                "below_ground_work": True,
                "unsafe_flooding": True,
            },
        )
        below_ground_gate = next(
            gate
            for gate in report["stop_work_and_restart_gates"]
            if gate["gate_id"] == "G-04"
        )
        self.assertTrue(below_ground_gate["triggered"])

    def test_candidate_weather_controls_are_not_marked_approved(self) -> None:
        self.assertTrue(self.base["weather_control_candidates"])
        self.assertTrue(
            all(
                control["applicability_status"]
                == "CANDIDATE_REQUIRES_SITE_VERIFICATION"
                for control in self.base["weather_control_candidates"]
            )
        )

    def test_missing_applicability_and_implementation_inputs_remain_unknown(self) -> None:
        recommendation = self.first
        self.assertEqual(
            recommendation["applicability"]["status"],
            "REQUIRES_WORK_METHOD_AND_SITE_REVIEW",
        )
        self.assertIsNone(
            recommendation["implementation_plan"]["required_resources"]
        )
        self.assertIsNone(
            recommendation["implementation_plan"]["responsible_personnel"]
        )
        self.assertIsNone(
            recommendation["implementation_plan"]["implementation_time_workdays"]
        )
        self.assertFalse(recommendation["feasible"])

    def test_unapproved_or_incomplete_implementation_plan_is_not_feasible(self) -> None:
        intervention_id = self.first["intervention_id"]
        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            applicability_reviews={intervention_id: self._review()},
            implementation_plans={
                intervention_id: self._plan(status="PENDING")
            },
        )
        item = next(
            entry
            for entry in report["recommendations"]
            if entry["intervention_id"] == intervention_id
        )
        self.assertFalse(item["feasible"])
        self.assertEqual(item["approval_status"], "PENDING")
        self.assertTrue(item["implementation_plan_issues"])

    def test_candidate_recommendation_never_reduces_residual_risk(self) -> None:
        assessment = self._approved_assessment_fixture()
        before = assessment["hazard_assessments"][0]["residual"]
        report = recommend_activity_interventions(
            self.code, graph=self.graph, wsh_assessment=assessment
        )
        self.assertTrue(report["wsh_assessment_authorizes_proceed"])
        self.assertFalse(report["continuation_authorized"])
        self.assertEqual(
            assessment["hazard_assessments"][0]["residual"], before
        )
        self.assertTrue(
            all(
                not item["residual_risk_changed_by_recommendation"]
                and item["residual_wsh_assessment_status"]
                == "REASSESSMENT_REQUIRED"
                for item in report["recommendations"]
            )
        )

    def test_qualitative_graph_benefit_never_becomes_numeric_effect(self) -> None:
        for item in self.base["recommendations"]:
            self.assertIsNone(item["estimated_productivity_impact"])
            self.assertIsNone(item["potential_project_delay_avoided"])
            self.assertIn("No numerical", item["uncertainty"])

    def test_explicit_scenario_assumption_runs_through_schedule_callback(self) -> None:
        intervention_id = self.first["intervention_id"]
        calls = []

        def runner(activities, project_start, calendar, scenario):
            calls.append((activities, project_start, calendar, scenario))
            if scenario.get("implementationDelays"):
                return {
                    "adjustedFinish": "2026-11-12",
                    "base": {
                        "application-activity-1": {
                            "finish": "2026-11-10",
                        }
                    },
                    "adj": {
                        "application-activity-1": {
                            "finish": "2026-11-12",
                            "direct": True,
                            "inherited": False,
                            "directLostProductiveHours": 1.0,
                            "uncertaintyHours": 0.5,
                        }
                    },
                }
            return {
                "adjustedFinish": "2026-11-16",
                "base": {
                    "application-activity-1": {
                        "finish": "2026-11-10",
                    }
                },
                "adj": {
                    "application-activity-1": {
                        "finish": "2026-11-16",
                        "direct": True,
                        "inherited": False,
                    }
                },
            }

        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            wsh_assessment=self._approved_assessment_fixture(),
            applicability_reviews={intervention_id: self._review()},
            implementation_plans={intervention_id: self._plan()},
            productivity_assumptions={
                intervention_id: {
                    "source_type": "EXPLICIT_SCENARIO_ASSUMPTION",
                    "rationale": "Test-only profile assumption, not measured evidence.",
                    "adjusted_profile": [
                        {"date": "2026-11-10", "productivityFactor": 0.9}
                    ],
                }
            },
            schedule_activity_id="application-activity-1",
            schedule_input={
                "activities": [
                    {"id": "application-activity-1", "durationDays": 2}
                ],
                "project_start": "2026-11-09",
                "calendar": {
                    "weekdays": [1, 2, 3, 4, 5],
                    "holidays": ["2026-11-11"],
                },
                "scenario": {
                    "activityProfiles": {
                        "application-activity-1": [
                            {
                                "date": "2026-11-10",
                                "productivityFactor": 0.5,
                            }
                        ]
                    }
                },
            },
            schedule_runner=runner,
        )
        item = next(
            entry
            for entry in report["recommendations"]
            if entry["intervention_id"] == intervention_id
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][3]["activityProfiles"]["application-activity-1"][0][
                "productivityFactor"
            ],
            0.5,
        )
        self.assertEqual(
            calls[1][3]["activityProfiles"]["application-activity-1"][0][
                "productivityFactor"
            ],
            0.9,
        )
        self.assertEqual(
            calls[1][3]["implementationDelays"]["application-activity-1"], 0
        )
        self.assertEqual(
            calls[1][3]["recoveryDaysByActivity"]["application-activity-1"], 0
        )
        self.assertEqual(item["schedule_evaluation"]["status"], "EVALUATED")
        self.assertEqual(
            item["potential_project_delay_avoided"],
            2,
        )
        self.assertEqual(
            item["schedule_evaluation"]["revised_activity_delay_workdays"], 1
        )
        self.assertEqual(
            item["schedule_evaluation"]["activity_finish_change_workdays"], -2
        )
        self.assertEqual(
            item["estimated_productivity_impact"]["status"],
            "SCENARIO_ASSUMPTION_NOT_EMPIRICAL",
        )
        self.assertTrue(item["feasible"])
        self.assertFalse(item["work_authorization_granted"])

    def test_invalid_productivity_assumption_is_not_used(self) -> None:
        intervention_id = self.first["intervention_id"]
        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            productivity_assumptions={
                intervention_id: {
                    "source_type": "UNLABELLED",
                    "rationale": "",
                    "adjusted_profile": [
                        {"date": "2026-11-10", "productivityFactor": 1.2}
                    ],
                }
            },
        )
        item = next(
            entry
            for entry in report["recommendations"]
            if entry["intervention_id"] == intervention_id
        )
        self.assertIsNone(item["estimated_productivity_impact"])
        self.assertTrue(item["productivity_assumption_issues"])

    def test_delay_avoided_uses_project_workdays_not_calendar_days(self) -> None:
        calendar = {
            "weekdays": [1, 2, 3, 4, 5],
            "holidays": ["2026-11-11"],
        }
        self.assertEqual(
            _calendar_workday_delta("2026-11-10", "2026-11-12", calendar), 1
        )
        self.assertEqual(
            _calendar_workday_delta("2026-11-16", "2026-11-12", calendar), -2
        )

    def test_feasible_options_rank_before_unreviewed_candidates(self) -> None:
        intervention_id = self.first["intervention_id"]
        report = recommend_activity_interventions(
            self.code,
            graph=self.graph,
            applicability_reviews={intervention_id: self._review()},
            implementation_plans={intervention_id: self._plan()},
        )
        self.assertTrue(report["recommendations"][0]["feasible"])
        self.assertEqual(
            report["recommendations"][0]["intervention_id"], intervention_id
        )

    def test_multiple_activities_remain_separate(self) -> None:
        report = recommend_for_activities(
            ["BE-01", "EW-01"], graph=self.graph
        )
        self.assertEqual(
            [entry["wbs_code"] for entry in report["activities"]],
            ["BE-01", "EW-01"],
        )
        self.assertTrue(report["wsh_risk_and_schedule_disruption_are_separate"])


if __name__ == "__main__":
    unittest.main()
