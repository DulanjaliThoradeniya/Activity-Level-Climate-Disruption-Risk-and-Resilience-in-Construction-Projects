"""Verify provisional examples remain synthetic, complete and arithmetically correct."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from risk_engine.build_provisional_wsh_bundle import DEFAULT_KNOWLEDGE, DEFAULT_WORKBOOK, build_bundle
from risk_engine.graph_loader import read_workbook


class ProvisionalBundleTests(unittest.TestCase):
    def test_examples_cover_activities_and_preserve_scores(self):
        bundle = build_bundle(DEFAULT_WORKBOOK, DEFAULT_KNOWLEDGE)
        self.assertEqual(bundle["bundle_type"], "SYNTHETIC_PROVISIONAL_WSH_EXAMPLES")
        self.assertEqual(len(bundle["activities"]), 69)
        rows = [row for activity in bundle["activities"].values() for row in activity]
        self.assertEqual(len(rows), 349)
        self.assertTrue(all(row["rpn"] == row["severity"] * row["likelihood"] for row in rows))
        self.assertEqual(next(row["rpn"] for row in bundle["activities"]["SP-01"] if row["hazard_id"] == "H08"), 12)

    def test_rejects_mismatched_or_approved_rows(self):
        workbook = read_workbook(DEFAULT_WORKBOOK)
        sheet = next(sheet for sheet in workbook.worksheets if sheet.name == "Provisional_WSH_Scores")
        for field, value in (("RPN (=S*L)", 999), ("Approval status", "APPROVED")):
            with self.subTest(field=field):
                bad_sheet = replace(sheet, rows=({**sheet.rows[0], field: value}, *sheet.rows[1:]))
                bad_workbook = replace(workbook, worksheets=tuple(bad_sheet if item is sheet else item for item in workbook.worksheets))
                with patch("risk_engine.build_provisional_wsh_bundle.read_workbook", return_value=bad_workbook):
                    with self.assertRaises(ValueError):
                        build_bundle(DEFAULT_WORKBOOK, DEFAULT_KNOWLEDGE)


if __name__ == "__main__":
    unittest.main()
