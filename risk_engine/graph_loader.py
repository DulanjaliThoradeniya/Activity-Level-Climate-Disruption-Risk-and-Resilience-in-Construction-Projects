"""Load the C-RESIST workbook into a provenance-preserving NetworkX graph."""

from __future__ import annotations

from dataclasses import dataclass
import math
import posixpath
import re
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile

import networkx as nx

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DEFAULT_WORKBOOK = (
    Path(__file__).resolve().parents[1]
    / "knowledge_graph"
    / "Knowledge Graph Project.xlsx"
)

SOURCE_ID_PATTERN = re.compile(r"\bSRC-\d{2}\b")
WBS_CODE_PATTERN = re.compile(r"\b[A-Z]{2}-\d{2}\b")


class WorkbookError(ValueError):
    """Raised when an XLSX workbook cannot be read as an Open XML workbook."""


@dataclass(frozen=True)
class WorksheetData:
    name: str
    header_row: int | None
    headers: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_numbers: tuple[int, ...]
    raw_rows: tuple[tuple[int, dict[str, Any]], ...]


@dataclass(frozen=True)
class WorkbookData:
    path: Path
    worksheets: tuple[WorksheetData, ...]

    @property
    def worksheet_schemas(self) -> dict[str, dict[str, Any]]:
        return {
            sheet.name: {
                "header_row": sheet.header_row,
                "headers": list(sheet.headers),
                "row_count": len(sheet.rows),
            }
            for sheet in self.worksheets
        }


@dataclass(frozen=True)
class WorkbookTable:
    key: str
    sheet_name: str
    header_row: int
    headers: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_numbers: tuple[int, ...]


# These signatures are based on the workbook's inspected column headers.
# Sheet names are intentionally not used to locate the source tables.
TABLE_SIGNATURES: dict[str, tuple[str, ...]] = {
    "activities": ("WBS code", "Activity name", "Stage", "Scope note"),
    "hazards": ("Hazard ID", "Hazard", "Definition"),
    "exposures": ("Exposure ID", "Exposure condition", "Hazards enabled"),
    "mechanisms": ("Mechanism ID", "Mechanism", "Domain"),
    "consequences": ("ID", "Type", "Consequence"),
    "variables": ("Variable ID", "Variable", "Unit"),
    "threshold_rules": ("Rule ID", "Rule class", "Triggered hazards"),
    "controls": ("Control ID", "Control", "Target hazards"),
    "interventions": ("Intervention ID", "Intervention", "Target hazards"),
    "evidence": ("Source ID", "APA 7 reference", "Verification status"),
    "triples": (
        "Triple ID",
        "Subject ID",
        "Predicate",
        "Object ID",
        "Object type",
        "Source IDs",
        "Evidence category",
        "Validation status",
    ),
    "activity_hazard_links": (
        "Link ID",
        "WBS code",
        "Hazard ID",
        "Exposure ID",
        "Applicable control IDs",
        "Applicable intervention IDs",
        "Source IDs",
        "Validation status",
    ),
    "activity_map": ("WBS_code", "Browser_ID", "Match_status"),
    "assessment_templates": (
        "RA ID",
        "WBS code",
        "Hazard ID",
        "Approval status",
    ),
    "assessment_records": (
        "project_id",
        "assessment_date",
        "wbs_code",
        "hazard_id",
        "severity_1_5",
        "likelihood_1_5",
        "approval_status",
    ),
    "code_compliance": (
        "Requirement ID",
        "2021 Code section",
        "Implementation action",
        "Status",
    ),
    "adverse_weather_controls": (
        "Guidance ID",
        "Candidate WBS applicability (verify)",
        "2025 guideline section",
        "Caution",
    ),
    "operational_gates": (
        "Gate ID",
        "Engine response",
        "Can daily rainfall alone evaluate?",
        "Source",
    ),
}

_COLUMN_RE = re.compile(r"([A-Z]+)(\d+)$")


def _column_index(letters: str) -> int:
    result = 0
    for character in letters:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(part.text or "" for part in cell.iter(f"{{{MAIN_NS}}}t"))

    value = cell.find(f"{{{MAIN_NS}}}v")
    if value is None or value.text is None:
        return None
    raw = value.text

    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (IndexError, ValueError) as error:
            raise WorkbookError(f"Invalid shared-string index {raw!r}.") from error
    if cell_type == "b":
        return raw == "1"
    if cell_type in {"str", "e"}:
        return raw

    try:
        number = float(raw)
    except ValueError:
        return raw
    if not math.isfinite(number):
        return raw
    return int(number) if number.is_integer() else number


def _worksheet_path(target: str) -> str:
    target = target.replace("\\", "/")
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join("xl", target))


def _row_values(
    row: ET.Element, shared_strings: list[str]
) -> tuple[int, dict[int, Any]]:
    row_number = int(row.attrib.get("r", "0"))
    values: dict[int, Any] = {}
    for cell in row.findall(f"{{{MAIN_NS}}}c"):
        match = _COLUMN_RE.fullmatch(cell.attrib.get("r", ""))
        if not match:
            continue
        value = _cell_value(cell, shared_strings)
        if value is not None and value != "":
            values[_column_index(match.group(1))] = value
    return row_number, values


def _materialize_worksheet(
    name: str,
    xml_root: ET.Element,
    shared_strings: list[str],
) -> WorksheetData:
    raw_rows = tuple(
        _row_values(row, shared_strings)
        for row in xml_root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row")
    )
    header_position: int | None = None
    best_score = -1.0
    for position, (_, values) in enumerate(raw_rows[:12]):
        if len(values) < 2:
            continue
        text_cells = sum(isinstance(value, str) for value in values.values())
        numeric_cells = len(values) - text_cells
        score = len(values) + text_cells * 0.1 - numeric_cells * 0.5
        if score > best_score:
            header_position, best_score = position, score

    if header_position is None:
        return WorksheetData(
            name=name,
            header_row=None,
            headers=(),
            rows=(),
            row_numbers=(),
            raw_rows=tuple(
                (number, {str(index): value for index, value in values.items()})
                for number, values in raw_rows
            ),
        )

    header_number, header_values = raw_rows[header_position]
    headers_by_column: dict[int, str] = {}
    used_headers: set[str] = set()
    for column, value in header_values.items():
        header = str(value).strip()
        if not header:
            continue
        unique_header = header
        suffix = 2
        while unique_header in used_headers:
            unique_header = f"{header}#{suffix}"
            suffix += 1
        used_headers.add(unique_header)
        headers_by_column[column] = unique_header

    data_rows: list[dict[str, Any]] = []
    data_row_numbers: list[int] = []
    for row_number, values in raw_rows[header_position + 1 :]:
        record = {
            headers_by_column[column]: value
            for column, value in values.items()
            if column in headers_by_column
        }
        if record:
            data_rows.append(record)
            data_row_numbers.append(row_number)

    return WorksheetData(
        name=name,
        header_row=header_number,
        headers=tuple(headers_by_column.values()),
        rows=tuple(data_rows),
        row_numbers=tuple(data_row_numbers),
        raw_rows=tuple(
            (number, {str(index): value for index, value in values.items()})
            for number, values in raw_rows
        ),
    )


def read_workbook(path: str | Path = DEFAULT_WORKBOOK) -> WorkbookData:
    """Read worksheet names, headers, and cell values without changing the XLSX."""
    workbook_path = Path(path).expanduser().resolve()
    if not workbook_path.is_file():
        raise WorkbookError(f"Knowledge-graph workbook not found: {workbook_path}")

    try:
        with zipfile.ZipFile(workbook_path) as archive:
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            relationship_targets = {
                relationship.attrib["Id"]: relationship.attrib["Target"]
                for relationship in relationships_root.findall(
                    f"{{{PKG_REL_NS}}}Relationship"
                )
            }
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(part.text or "" for part in item.iter(f"{{{MAIN_NS}}}t"))
                    for item in shared_root.findall(f"{{{MAIN_NS}}}si")
                ]

            worksheets: list[WorksheetData] = []
            for sheet in workbook_root.findall(
                f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet"
            ):
                relationship_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
                target = relationship_targets.get(relationship_id or "")
                if target is None:
                    raise WorkbookError(
                        f"Worksheet {sheet.attrib.get('name')!r} has no workbook relationship."
                    )
                worksheet_file = _worksheet_path(target)
                try:
                    sheet_root = ET.fromstring(archive.read(worksheet_file))
                except KeyError as error:
                    raise WorkbookError(
                        f"Worksheet XML is missing: {worksheet_file}"
                    ) from error
                worksheets.append(
                    _materialize_worksheet(
                        sheet.attrib["name"], sheet_root, shared_strings
                    )
                )
    except (OSError, ET.ParseError, zipfile.BadZipFile) as error:
        raise WorkbookError(f"Unable to read XLSX workbook {workbook_path}: {error}") from error

    return WorkbookData(workbook_path, tuple(worksheets))


def find_table(workbook: WorkbookData, key: str) -> WorkbookTable | None:
    """Locate a source table by its inspected column signature, not its sheet name."""
    required = TABLE_SIGNATURES[key]
    candidates: list[WorkbookTable] = []
    for sheet in workbook.worksheets:
        for position, (_, values) in enumerate(sheet.raw_rows[:20]):
            headers = {
                str(value).strip(): column
                for column, value in values.items()
                if isinstance(value, str) and value.strip()
            }
            if not set(required).issubset(headers):
                continue
            header_row = sheet.raw_rows[position][0]
            ordered_headers = tuple(
                headers_for_column
                for _, headers_for_column in sorted(
                    (column, str(value).strip())
                    for column, value in values.items()
                    if isinstance(value, str) and value.strip()
                )
            )
            rows: list[dict[str, Any]] = []
            row_numbers: list[int] = []
            for row_number, row_values in sheet.raw_rows[position + 1 :]:
                record = {
                    str(values[column]).strip(): value
                    for column, value in row_values.items()
                    if column in values
                    and isinstance(values[column], str)
                    and values[column].strip()
                }
                if record:
                    rows.append(record)
                    row_numbers.append(row_number)
            candidates.append(
                WorkbookTable(
                    key,
                    sheet.name,
                    header_row,
                    ordered_headers,
                    tuple(rows),
                    tuple(row_numbers),
                )
            )

    if not candidates:
        return None
    if len(candidates) > 1:
        sheets = ", ".join(candidate.sheet_name for candidate in candidates)
        raise WorkbookError(
            f"Ambiguous workbook schema for {key!r}; matching worksheets: {sheets}."
        )
    return candidates[0]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _record_references(record: dict[str, Any]) -> tuple[str, ...]:
    references: list[str] = []
    for key in ("Source IDs", "Source ID", "Source"):
        value = _text(record.get(key))
        if value:
            references.extend(SOURCE_ID_PATTERN.findall(value))
    return tuple(dict.fromkeys(references))


def _evidence_category(record: dict[str, Any]) -> str | None:
    for key in ("Evidence category", "Evidence basis", "Verification status"):
        value = _text(record.get(key))
        if value:
            return value
    return None


def _validation_status(record: dict[str, Any]) -> str | None:
    for key in ("Validation status", "Status"):
        value = _text(record.get(key))
        if value:
            return value
    return None


def _add_defined_node(
    graph: nx.MultiDiGraph,
    node_id: str,
    node_type: str,
    record: dict[str, Any],
    sheet_name: str,
    row_number: int,
    id_field: str,
    label_fields: Iterable[str],
    duplicate_ids: list[dict[str, Any]],
) -> None:
    label = next((_text(record.get(field)) for field in label_fields if _text(record.get(field))), node_id)
    attributes = {
        "id": node_id,
        "node_type": node_type,
        "label": label,
        "defined": True,
        "source_sheet": sheet_name,
        "source_row": row_number,
        "id_field": id_field,
        "record": dict(record),
        "source_ids": _record_references(record),
        "evidence_category": _evidence_category(record),
        "evidence_status": _validation_status(record),
    }

    if graph.has_node(node_id):
        existing = graph.nodes[node_id]
        if existing.get("defined"):
            duplicate_ids.append(
                {
                    "id": node_id,
                    "sheet": sheet_name,
                    "row": row_number,
                    "existing_sheet": existing.get("source_sheet"),
                    "issue": (
                        "duplicate_id"
                        if existing.get("node_type") == node_type
                        else "cross_type_id_collision"
                    ),
                }
            )
            definitions = list(existing.get("duplicate_definitions", ()))
            definitions.append(attributes)
            existing["duplicate_definitions"] = definitions
            return
        graph.nodes[node_id].update(attributes)
        graph.nodes[node_id]["reference_only"] = False
        return
    graph.add_node(node_id, **attributes)


def _ensure_reference_node(
    graph: nx.MultiDiGraph,
    node_id: str,
    object_type: str,
    source: str,
) -> None:
    if graph.has_node(node_id):
        return
    if object_type.upper() == "LITERAL":
        graph.add_node(
            node_id,
            id=node_id,
            label=node_id,
            node_type="literal",
            defined=True,
            literal_value=node_id,
            source_sheet=source,
            source_ids=(),
        )
        return
    graph.add_node(
        node_id,
        id=node_id,
        label=node_id,
        node_type="unresolved_reference",
        defined=False,
        reference_only=True,
        referenced_by=[],
        source_sheet=None,
        source_ids=(),
    )
    graph.nodes[node_id]["referenced_by"].append(source)


def _add_edge(
    graph: nx.MultiDiGraph,
    subject_id: str,
    object_id: str,
    relationship_id: str,
    attributes: dict[str, Any],
    object_type: str = "ENTITY",
    source: str = "Knowledge_Triples",
) -> None:
    subject = _text(subject_id)
    target = _text(object_id)
    if not subject or not target:
        graph.graph["load_diagnostics"]["invalid_relationships"].append(
            {
                "relationship_id": relationship_id,
                "source": source,
                "issue": "missing_subject_or_object",
                "subject_id": subject,
                "object_id": target,
            }
        )
        return
    _ensure_reference_node(graph, subject, "ENTITY", source)
    _ensure_reference_node(graph, target, object_type, source)

    key = relationship_id or f"{source}:{subject}:{target}"
    edge_key: str = key
    duplicate_index = 2
    while graph.has_edge(subject, target, edge_key):
        edge_key = f"{key}#{duplicate_index}"
        duplicate_index += 1
    if edge_key != key:
        graph.graph["load_diagnostics"]["duplicate_ids"].append(
            {
                "id": relationship_id,
                "sheet": source,
                "issue": "duplicate_relationship_id",
            }
        )
    graph.add_edge(subject, target, key=edge_key, **attributes)
    for endpoint in (subject, target):
        node = graph.nodes[endpoint]
        if not node.get("defined"):
            node.setdefault("referenced_by", []).append(
                f"{source}:{relationship_id}"
            )


def _ontology_properties(
    workbook: WorkbookData,
) -> tuple[dict[str, dict[str, str]], str | None]:
    for sheet in workbook.worksheets:
        for position, (_, values) in enumerate(sheet.raw_rows[:40]):
            header = {
                str(value).strip(): column
                for column, value in values.items()
                if isinstance(value, str) and value.strip()
            }
            required = {"Property", "Domain", "Range"}
            if not required.issubset(header):
                continue
            properties: dict[str, dict[str, str]] = {}
            for _, row in sheet.raw_rows[position + 1 :]:
                name = _text(row.get(header["Property"]))
                if not name:
                    if properties:
                        break
                    continue
                properties[name] = {
                    "domain": _text(row.get(header["Domain"])),
                    "range": _text(row.get(header["Range"])),
                }
            return properties, sheet.name
    return {}, None


_ENTITY_SPECS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("activities", "WBS code", "construction_activity", ("Activity name",)),
    ("hazards", "Hazard ID", "climate_hazard", ("Hazard",)),
    ("exposures", "Exposure ID", "exposure_condition", ("Exposure condition",)),
    ("mechanisms", "Mechanism ID", "impact_mechanism", ("Mechanism",)),
    ("consequences", "ID", "consequence", ("Consequence",)),
    ("variables", "Variable ID", "rainfall_variable", ("Variable",)),
    ("threshold_rules", "Rule ID", "trigger_rule", ("Rule class", "Rule ID")),
    ("controls", "Control ID", "control_measure", ("Control",)),
    ("interventions", "Intervention ID", "resilience_intervention", ("Intervention",)),
    ("evidence", "Source ID", "evidence_source", ("APA 7 reference", "Type")),
    (
        "assessment_templates",
        "RA ID",
        "wsh_assessment_template",
        ("RA ID", "Hazard"),
    ),
    (
        "code_compliance",
        "Requirement ID",
        "wsh_code_requirement",
        ("Requirement",),
    ),
    (
        "adverse_weather_controls",
        "Guidance ID",
        "adverse_weather_control",
        ("Control / rule",),
    ),
    (
        "operational_gates",
        "Gate ID",
        "wsh_operational_gate",
        ("Condition",),
    ),
)


def load_knowledge_graph(path: str | Path = DEFAULT_WORKBOOK) -> nx.MultiDiGraph:
    """Load workbook entities and explicit relationships into a typed MultiDiGraph.

    Knowledge triples and activity-hazard link rows remain graph relationships;
    the separate empty/site-approved-assessment worksheets are not treated as
    evidence that a WSH risk assessment has been approved.
    """
    workbook = read_workbook(path)
    graph = nx.MultiDiGraph()
    diagnostics: dict[str, Any] = {
        "missing_schemas": [],
        "duplicate_ids": [],
        "invalid_relationships": [],
        "missing_tables": [],
        "warnings": [],
    }
    graph.graph.update(
        {
            "source_workbook": str(workbook.path),
            "worksheet_schemas": workbook.worksheet_schemas,
            "knowledge_graph_relationships_only": True,
            "load_diagnostics": diagnostics,
            "activity_mappings": {},
            "ontology_properties": {},
            "assessment_record_count": 0,
            "approved_assessment_record_count": 0,
            "project_specific_wsh_assessment_records": [],
            "approved_wsh_assessment_records": [],
        }
    )

    tables: dict[str, WorkbookTable] = {}
    for key, signature in TABLE_SIGNATURES.items():
        try:
            table = find_table(workbook, key)
        except WorkbookError as error:
            diagnostics["missing_schemas"].append(
                {"table": key, "required_columns": list(signature), "issue": str(error)}
            )
            continue
        if table is None:
            diagnostics["missing_schemas"].append(
                {"table": key, "required_columns": list(signature)}
            )
            continue
        tables[key] = table
        if not table.rows and key not in {"assessment_records"}:
            diagnostics["warnings"].append(
                {"table": key, "issue": "table_has_no_data_rows"}
            )

    # Keep unmodeled, site-specific assessment/trial worksheets separate from
    # the knowledge graph; their headers and row counts remain in the report.
    for worksheet in workbook.worksheets:
        graph.graph["worksheet_schemas"][worksheet.name]["row_count"] = len(
            worksheet.rows
        )

    for key, id_field, node_type, label_fields in _ENTITY_SPECS:
        table = tables.get(key)
        if table is None:
            continue
        seen_ids: set[str] = set()
        for row_number, record in zip(table.row_numbers, table.rows):
            node_id = _text(record.get(id_field))
            if not node_id:
                diagnostics["invalid_relationships"].append(
                    {
                        "source": table.sheet_name,
                        "row": row_number,
                        "issue": f"missing_id:{id_field}",
                    }
                )
                continue
            if node_id in seen_ids:
                diagnostics["duplicate_ids"].append(
                    {
                        "id": node_id,
                        "sheet": table.sheet_name,
                        "row": row_number,
                        "issue": "duplicate_id_in_table",
                    }
                )
            seen_ids.add(node_id)
            resolved_type = node_type
            if key == "consequences":
                consequence_type = _text(record.get("Type")).lower()
                if node_id.startswith("SC-") or consequence_type == "safety":
                    resolved_type = "safety_consequence"
                elif node_id.startswith("PC-") or "performance" in consequence_type:
                    resolved_type = "performance_consequence"
                else:
                    resolved_type = "consequence"
            _add_defined_node(
                graph,
                node_id,
                resolved_type,
                record,
                table.sheet_name,
                row_number,
                id_field,
                label_fields,
                diagnostics["duplicate_ids"],
            )
            if key == "assessment_templates":
                graph.nodes[node_id]["assessment_kind"] = "workbook_template"
                graph.nodes[node_id]["is_approved_wsh_assessment"] = False
                graph.nodes[node_id]["approval_status"] = _text(
                    record.get("Approval status")
                ) or None

    activity_map_table = tables.get("activity_map")
    if activity_map_table is not None:
        seen_codes: set[str] = set()
        mapping: dict[str, dict[str, Any]] = {}
        for row_number, record in zip(
            activity_map_table.row_numbers, activity_map_table.rows
        ):
            code = _text(record.get("WBS_code")).upper()
            browser_id = _text(record.get("Browser_ID"))
            if not code or not browser_id:
                diagnostics["invalid_relationships"].append(
                    {
                        "source": activity_map_table.sheet_name,
                        "row": row_number,
                        "issue": "missing_activity_mapping_code_or_browser_id",
                    }
                )
                continue
            if code in seen_codes:
                diagnostics["duplicate_ids"].append(
                    {
                        "id": code,
                        "sheet": activity_map_table.sheet_name,
                        "row": row_number,
                        "issue": "duplicate_activity_mapping",
                    }
                )
            seen_codes.add(code)
            mapping[code] = {
                "browser_id": browser_id,
                "match_status": _text(record.get("Match_status")),
                "note": _text(record.get("Note")),
            }
            if graph.has_node(code):
                graph.nodes[code]["browser_id"] = browser_id
                graph.nodes[code]["mapping_status"] = mapping[code]["match_status"]
                graph.nodes[code]["mapping_note"] = mapping[code]["note"]
        graph.graph["activity_mappings"] = mapping
        activity_table = tables.get("activities")
        known_codes = {
            _text(row.get("WBS code")).upper()
            for row in activity_table.rows
            if activity_table is not None
        } if activity_table is not None else set()
        diagnostics["missing_activity_mappings"] = sorted(known_codes - set(mapping))
        diagnostics["mapping_codes_without_activity"] = sorted(set(mapping) - known_codes)

    ontology, ontology_sheet = _ontology_properties(workbook)
    graph.graph["ontology_properties"] = ontology
    graph.graph["ontology_sheet"] = ontology_sheet
    if not ontology:
        diagnostics["missing_schemas"].append(
            {"table": "ontology_relationships", "required_columns": ["Property", "Domain", "Range"]}
        )

    triples = tables.get("triples")
    if triples is not None:
        seen_triple_ids: set[str] = set()
        for row_number, record in zip(triples.row_numbers, triples.rows):
            triple_id = _text(record.get("Triple ID"))
            subject = _text(record.get("Subject ID"))
            predicate = _text(record.get("Predicate"))
            object_id = _text(record.get("Object ID"))
            if triple_id in seen_triple_ids:
                diagnostics["duplicate_ids"].append(
                    {
                        "id": triple_id,
                        "sheet": triples.sheet_name,
                        "row": row_number,
                        "issue": "duplicate_triple_id",
                    }
                )
            seen_triple_ids.add(triple_id)
            graph.graph.setdefault("triple_count", 0)
            graph.graph["triple_count"] += 1
            object_type = _text(record.get("Object type")) or "ENTITY"
            _add_edge(
                graph,
                subject,
                object_id,
                triple_id,
                {
                    "relationship_id": triple_id,
                    "triple_id": triple_id,
                    "relationship_type": predicate,
                    "predicate": predicate,
                    "source_ids": _record_references(record),
                    "source_references": _text(record.get("Source IDs")),
                    "evidence_description": _text(
                        record.get("Evidence description")
                    ),
                    "evidence_category": _evidence_category(record),
                    "evidence_status": _validation_status(record),
                    "object_type": object_type,
                    "relationship_origin": "Knowledge_Triples",
                    "source_sheet": triples.sheet_name,
                    "source_row": row_number,
                    "is_approved_wsh_assessment": False,
                },
                object_type,
                triples.sheet_name,
            )

    activity_hazard_links = tables.get("activity_hazard_links")
    if activity_hazard_links is not None:
        for row_number, record in zip(
            activity_hazard_links.row_numbers, activity_hazard_links.rows
        ):
            link_id = _text(record.get("Link ID"))
            if not link_id:
                diagnostics["invalid_relationships"].append(
                    {
                        "source": activity_hazard_links.sheet_name,
                        "row": row_number,
                        "issue": "missing_activity_hazard_link_id",
                    }
                )
                continue
            _add_defined_node(
                graph,
                link_id,
                "activity_hazard_link",
                record,
                activity_hazard_links.sheet_name,
                row_number,
                "Link ID",
                ("Activity name", "Hazard"),
                diagnostics["duplicate_ids"],
            )
            graph.nodes[link_id]["evidence_status"] = _validation_status(record)
            graph.nodes[link_id]["evidence_category"] = _evidence_category(record)
            graph.nodes[link_id]["is_approved_wsh_assessment"] = False
            activity_id = _text(record.get("WBS code")).upper()
            hazard_id = _text(record.get("Hazard ID"))
            linked_fields: list[tuple[str, str, str]] = [
                ("links_activity", activity_id, "ENTITY"),
                ("links_hazard", hazard_id, "ENTITY"),
                (
                    "declares_exposure_condition",
                    _text(record.get("Exposure ID")),
                    "ENTITY",
                ),
            ]
            for field, relationship in (
                ("Mechanism IDs", "identifies_mechanism"),
                ("Applicable control IDs", "candidate_control"),
                ("Applicable intervention IDs", "candidate_intervention"),
            ):
                for target in re.findall(
                    r"\b(?:IM-\d{2}|C-\d{2}|RI-[A-Z]\d{2})\b",
                    _text(record.get(field)),
                ):
                    linked_fields.append((relationship, target, "ENTITY"))

            for relationship, target_id, object_type in linked_fields:
                if not target_id:
                    continue
                _add_edge(
                    graph,
                    link_id,
                    target_id,
                    f"{link_id}:{relationship}:{target_id}",
                    {
                        "relationship_id": link_id,
                        "relationship_type": relationship,
                        "predicate": relationship,
                        "source_ids": _record_references(record),
                        "source_references": _text(record.get("Source IDs")),
                        "evidence_category": _evidence_category(record),
                        "evidence_status": _validation_status(record),
                        "relationship_origin": "Activity_Hazard_Links",
                        "source_sheet": activity_hazard_links.sheet_name,
                        "source_row": row_number,
                        "candidate": relationship
                        in {"candidate_control", "candidate_intervention"},
                        "is_approved_wsh_assessment": False,
                    },
                    object_type,
                    activity_hazard_links.sheet_name,
                )

    adverse_table = tables.get("adverse_weather_controls")
    if adverse_table is not None:
        for row_number, record in zip(adverse_table.row_numbers, adverse_table.rows):
            guidance_id = _text(record.get("Guidance ID"))
            applicability = _text(
                record.get("Candidate WBS applicability (verify)")
            )
            for code in WBS_CODE_PATTERN.findall(applicability):
                _add_edge(
                    graph,
                    guidance_id,
                    code,
                    f"{guidance_id}:candidate:{code}",
                    {
                        "relationship_id": guidance_id,
                        "relationship_type": "candidate_wbs_applicability",
                        "predicate": "candidate_wbs_applicability",
                        "applicability_basis": applicability,
                        "evidence_status": "CANDIDATE_VERIFY",
                        "caution": _text(record.get("Caution")),
                        "relationship_origin": adverse_table.sheet_name,
                        "source_sheet": adverse_table.sheet_name,
                        "source_row": row_number,
                        "candidate": True,
                        "is_approved_wsh_assessment": False,
                    },
                    source=adverse_table.sheet_name,
                )

    assessment_records = tables.get("assessment_records")
    if assessment_records is not None:
        approved_count = 0
        approved_records: list[dict[str, Any]] = []
        for record in assessment_records.rows:
            status = _text(record.get("approval_status")).upper()
            if status in {"APPROVED", "APPROVED_BY_MANAGER", "APPROVED_FINAL"}:
                approved_count += 1
                approved_records.append(dict(record))
        graph.graph["assessment_record_count"] = len(assessment_records.rows)
        graph.graph["approved_assessment_record_count"] = approved_count
        graph.graph["project_specific_wsh_assessment_records"] = [
            dict(record) for record in assessment_records.rows
        ]
        graph.graph["approved_wsh_assessment_records"] = approved_records

    graph.graph["load_diagnostics"].setdefault("missing_activity_mappings", [])
    graph.graph["load_diagnostics"].setdefault(
        "mapping_codes_without_activity", []
    )
    graph.graph["node_count_defined"] = sum(
        1 for _, attributes in graph.nodes(data=True) if attributes.get("defined")
    )
    return graph
