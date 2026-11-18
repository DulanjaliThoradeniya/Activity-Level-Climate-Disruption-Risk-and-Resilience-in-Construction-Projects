"""Publish explicitly synthetic workbook examples without changing the workbook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .graph_loader import read_workbook
from .wsh_assessment import load_code_example_matrix

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "knowledge_graph" / "Knowledge_Graph_Project_with_Provisional_WSH_Scores.xlsx"
DEFAULT_OUTPUT = ROOT / "data" / "provisional-wsh-examples.json"
DEFAULT_KNOWLEDGE = ROOT / "data" / "activity-intervention-knowledge.json"


def build_bundle(workbook_path: Path, knowledge_path: Path) -> dict[str, Any]:
    workbook = read_workbook(workbook_path)
    sheet = next((item for item in workbook.worksheets if item.name == "Provisional_WSH_Scores"), None)
    if sheet is None:
        raise ValueError("Workbook is missing the Provisional_WSH_Scores sheet.")
    matrix = load_code_example_matrix(workbook_path)
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    if knowledge.get("schema_version") != 1 or knowledge.get("bundle_type") != "STATIC_CANDIDATE_KNOWLEDGE_ONLY":
        raise ValueError("Unsupported activity knowledge bundle.")
    activities = knowledge["activities"]
    expected = {(code, hazard["hazard_id"]) for code, item in activities.items() for hazard in item["hazards"]}
    examples: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for row in sheet.rows:
        code, hazard = row.get("WBS code"), row.get("Hazard ID")
        key = (code, hazard)
        if key in seen or key not in expected:
            raise ValueError(f"Duplicate or unknown activity/hazard pair: {key!r}")
        seen.add(key)
        severity, likelihood = row.get("Severity S (provisional)"), row.get("Likelihood L (provisional)")
        if type(severity) is not int or type(likelihood) is not int or severity not in range(1, 6) or likelihood not in range(1, 6):
            raise ValueError(f"Invalid provisional ratings for {key!r}.")
        classification = matrix.candidate_classification(severity, likelihood)
        if row.get("RPN (=S*L)") != severity * likelihood or row.get("Example risk class (NOT APPROVED)") != classification:
            raise ValueError(f"Provisional RPN or example matrix classification does not match for {key!r}.")
        if row.get("Approval status") != "NOT_APPROVED" or row.get("Rating origin") != "SYNTHETIC_DESK_BASED_ASSUMPTION" or row.get("Suitable for supervised real-world training?") != "NO":
            raise ValueError(f"Unexpected provisional provenance for {key!r}; do not publish as a synthetic example.")
        examples.setdefault(code, []).append({
            "hazard_id": hazard,
            "severity": severity,
            "likelihood": likelihood,
            "rpn": severity * likelihood,
            "example_class": classification,
            "severity_rationale": row.get("Severity rationale"),
            "likelihood_rationale": row.get("Likelihood rationale"),
            "assumed_conditions": row.get("Assumed conditions (NOT OBSERVED)"),
        })
    if seen != expected:
        raise ValueError(f"Provisional examples do not cover the graph hazard pairs: {sorted(expected - seen)!r}")
    return {
        "schema_version": 1,
        "bundle_type": "SYNTHETIC_PROVISIONAL_WSH_EXAMPLES",
        "source_workbook": workbook_path.name,
        "source_sheet": sheet.name,
        "matrix": matrix.version,
        "activities": examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    bundle = build_bundle(args.workbook, args.knowledge)
    args.output.write_text(json.dumps(bundle, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote synthetic, unapproved examples for {len(bundle['activities'])} activities to {args.output}.")


if __name__ == "__main__":
    main()
