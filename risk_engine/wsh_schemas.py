"""Typed, JSON-compatible schemas for auditable WSH risk assessments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal, Mapping

RiskLevel = Literal["Low", "Medium", "High"]
AssessmentStatus = Literal[
    "APPROVED",
    "ASSESSED_PENDING_APPROVAL",
    "REQUIRES_RA_REVIEW",
    "UNRESOLVED_HAZARD",
]
ControlHierarchy = Literal[
    "ELIMINATION",
    "SUBSTITUTION",
    "ENGINEERING",
    "ADMINISTRATIVE",
    "PPE",
    "UNCLASSIFIED",
]
CONTROL_HIERARCHY_ORDER: tuple[str, ...] = (
    "ELIMINATION",
    "SUBSTITUTION",
    "ENGINEERING",
    "ADMINISTRATIVE",
    "PPE",
    "UNCLASSIFIED",
)


@dataclass(frozen=True)
class RiskMatrix:
    """Organization-specific classification for each severity/likelihood pair."""

    version: str
    classifications: Mapping[tuple[int, int], RiskLevel]
    organization_approved: bool = False
    approved_by: str | None = None
    approval_date: str | None = None
    source: str = "ORGANIZATION_CONFIGURED"
    is_example_template: bool = False
    template_note: str | None = None

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Risk matrix version is required.")
        if not isinstance(self.organization_approved, bool):
            raise ValueError("organization_approved must be a boolean.")
        expected = {
            (severity, likelihood)
            for severity in range(1, 6)
            for likelihood in range(1, 6)
        }
        actual = set(self.classifications)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"Risk matrix must define all 25 severity/likelihood pairs; "
                f"missing={missing}, extra={extra}."
            )
        for pair, level in self.classifications.items():
            if level not in {"Low", "Medium", "High"}:
                raise ValueError(
                    f"Risk classification for {pair} must be Low, Medium, or High."
                )
        if self.organization_approved:
            if not self.approved_by or not self.approved_by.strip():
                raise ValueError("An approved risk matrix requires approved_by.")
            if not _is_iso_date(self.approval_date):
                raise ValueError(
                    "An approved risk matrix requires a valid ISO approval_date."
                )

    def candidate_classification(
        self, severity: int, likelihood: int
    ) -> RiskLevel:
        _validate_rating(severity, "severity")
        _validate_rating(likelihood, "likelihood")
        return self.classifications[(severity, likelihood)]

    def classify(self, severity: int, likelihood: int) -> RiskLevel | None:
        """Return an operative class only after organization approval."""
        if not self.organization_approved:
            return None
        return self.candidate_classification(severity, likelihood)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "classifications": {
                f"{severity}-{likelihood}": level
                for (severity, likelihood), level in sorted(
                    self.classifications.items()
                )
            },
            "organization_approved": self.organization_approved,
            "approved_by": self.approved_by,
            "approval_date": self.approval_date,
            "source": self.source,
            "is_example_template": self.is_example_template,
            "template_note": self.template_note,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RiskMatrix:
        raw_classifications = value.get("classifications", {})
        if not isinstance(raw_classifications, Mapping):
            raise ValueError("Risk matrix classifications must be a mapping.")
        organization_approved = value.get("organization_approved", False)
        if not isinstance(organization_approved, bool):
            raise ValueError("organization_approved must be a boolean.")
        classifications: dict[tuple[int, int], RiskLevel] = {}
        for key, level in raw_classifications.items():
            if isinstance(key, tuple) and len(key) == 2:
                pair = (int(key[0]), int(key[1]))
            elif isinstance(key, str):
                pieces = key.replace("/", "-").split("-")
                if len(pieces) != 2:
                    raise ValueError(
                        f"Invalid risk-matrix cell {key!r}; use 'severity-likelihood'."
                    )
                pair = (int(pieces[0]), int(pieces[1]))
            else:
                raise ValueError(f"Invalid risk-matrix cell key: {key!r}.")
            classifications[pair] = level
        return cls(
            version=str(value.get("version", "")),
            classifications=classifications,
            organization_approved=organization_approved,
            approved_by=value.get("approved_by"),
            approval_date=value.get("approval_date"),
            source=str(value.get("source", "ORGANIZATION_CONFIGURED")),
            is_example_template=bool(value.get("is_example_template", False)),
            template_note=value.get("template_note"),
        )


@dataclass(frozen=True)
class RatingInput:
    severity: int
    likelihood: int
    severity_rationale: str
    likelihood_rationale: str
    evidence: tuple[str, ...] = ()
    control_effect_evidence: str | None = None


@dataclass(frozen=True)
class ControlInput:
    control_id: str
    hierarchy: ControlHierarchy
    implementation_status: str
    approval_status: str
    evidence: str | None = None
    verified_by: str | None = None
    verification_date: str | None = None


@dataclass(frozen=True)
class AssessmentInput:
    initial: RatingInput | None
    residual: RatingInput | None
    site_conditions: Mapping[str, Any] | str | None
    existing_controls_considered: bool
    existing_control_ids: tuple[str, ...] = ()
    controls: tuple[ControlInput, ...] = ()
    assessor: str | None = None
    approval_status: str = "PENDING"
    approved_by: str | None = None
    approval_date: str | None = None
    evidence: tuple[str, ...] = ()
    review_reason: str | None = None


@dataclass(frozen=True)
class RiskStage:
    severity: int | None
    likelihood: int | None
    rpn: int | None
    risk_level: RiskLevel | None
    candidate_classification: RiskLevel | None
    status: str
    severity_rationale: str | None
    likelihood_rationale: str | None
    classification_basis: str
    matrix_version: str
    evidence: tuple[str, ...] = ()
    control_effect_evidence: str | None = None


@dataclass(frozen=True)
class HazardAssessment:
    hazard_id: str
    hazard_name: str
    unresolved: bool
    unresolved_reason: str | None
    applicable: bool
    activity_hazard_link_ids: tuple[str, ...]
    potential_consequences: tuple[str, ...]
    candidate_controls: tuple[Mapping[str, Any], ...]
    candidate_interventions: tuple[Mapping[str, Any], ...]
    initial: RiskStage
    residual: RiskStage
    assessment_status: AssessmentStatus
    approval_status: str
    assessor: str | None
    approved_by: str | None
    approval_date: str | None
    matrix: Mapping[str, Any]
    site_conditions: Mapping[str, Any] | str | None
    existing_controls_considered: bool
    existing_control_ids: tuple[str, ...]
    implemented_controls: tuple[Mapping[str, Any], ...]
    highest_approved_risk_level: RiskLevel | None
    authorization_status: str
    action_required: str
    review_reason: str | None


@dataclass(frozen=True)
class ActivityAssessment:
    schema_version: str
    assessment_type: str
    wbs_code: str
    application_activity_id: str | None
    activity_name: str
    assessment_status: str
    activity_risk_level: RiskLevel | None
    activity_risk_hazard_id: str | None
    authorization_status: str
    matrix: Mapping[str, Any]
    rating_scales: Mapping[str, Any]
    hazard_assessments: tuple[HazardAssessment, ...] = field(default_factory=tuple)
    unresolved_hazards: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return _json_compatible(asdict(self))


def _validate_rating(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value not in range(1, 6):
        raise ValueError(f"{name} must be an integer from 1 to 5.")


def _is_iso_date(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _json_compatible(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_compatible(item) for item in value]
    return value


WSH_ASSESSMENT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "C-RESIST WSH Activity Assessment",
    "type": "object",
    "required": [
        "schema_version",
        "assessment_type",
        "wbs_code",
        "assessment_status",
        "activity_risk_level",
        "authorization_status",
        "rating_scales",
        "hazard_assessments",
        "unresolved_hazards",
    ],
    "properties": {
        "schema_version": {"const": "1.0"},
        "assessment_type": {"const": "WSH_ACTIVITY_RISK_ASSESSMENT"},
        "wbs_code": {"type": "string"},
        "application_activity_id": {"type": ["string", "null"]},
        "activity_name": {"type": "string"},
        "assessment_status": {
            "enum": [
                "APPROVED",
                "ASSESSED_PENDING_APPROVAL",
                "REQUIRES_RA_REVIEW",
                "UNRESOLVED_HAZARD",
            ]
        },
        "activity_risk_level": {
            "enum": ["Low", "Medium", "High", None],
            "description": (
                "Highest applicable approved hazard-level risk; null if no "
                "hazard has an approved rating."
            ),
        },
        "activity_risk_hazard_id": {"type": ["string", "null"]},
        "authorization_status": {
            "enum": [
                "AUTHORIZED_TO_PROCEED",
                "NOT_AUTHORIZED_HIGH_RISK",
                "NOT_AUTHORIZED_PENDING_CONTROLS_OR_APPROVAL",
                "NOT_AUTHORIZED_REQUIRES_RA_REVIEW",
                "NO_APPLICABLE_RESOLVED_HAZARDS",
            ]
        },
        "matrix": {"type": "object"},
        "rating_scales": {"type": "object"},
        "hazard_assessments": {
            "type": "array",
            "items": {"type": "object"},
            "description": (
                "Each hazard includes separate initial and residual 1–5 severity "
                "and injury/ill-health likelihood ratings, RPN, evidence, approval "
                "metadata, and classification basis."
            ),
        },
        "unresolved_hazards": {
            "type": "array",
            "items": {"type": "string"},
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
