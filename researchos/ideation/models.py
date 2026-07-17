"""Pydantic domain models for the internal T4 evolutionary workflow.

The models contain no research-domain defaults.  Research text and source
references are supplied by the active workspace and LLM role outputs; this
module only enforces stable structure, evidence boundaries, identifiers, and
population integrity.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.0.0"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_PLACEHOLDER_TEXT_RE = re.compile(
    r"^(?:\.{2,}|…+|unknown|n/?a|none|tbd|todo|not\s+assessed|待(?:补充|确定|核验|验证|评估|研究)|未提供|未标注)$",
    flags=re.IGNORECASE,
)


def normalize_crossover_decision(value: Any) -> Any:
    """Normalize explicit compatibility verdicts at every T4 persistence boundary.

    Only ``approved`` may authorize a Child. ``parallel`` is nevertheless a
    first-class, scientifically meaningful no-child conclusion: two ideas can
    be worth retaining independently even when their components should not be
    forced into one mechanism. Providers and older plan artifacts use several
    unambiguous aliases for that conclusion. Unknown text remains invalid
    instead of being guessed into an approval or a rejection.
    """

    if not isinstance(value, str):
        return value
    compact = " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())
    aliases = {
        "approve": "approved",
        "approved": "approved",
        "compatible": "approved",
        "reject": "rejected",
        "rejected": "rejected",
        "incompatible": "rejected",
        "parallel": "parallel",
        "keep parallel": "parallel",
        "parallel keep": "parallel",
        "keep separate": "parallel",
        "remain separate": "parallel",
        "并行": "parallel",
        "并行保留": "parallel",
        "保持并行": "parallel",
        "uncertain": "uncertain",
        "needs clarification": "uncertain",
        "needs review": "uncertain",
        "defer": "uncertain",
        "待澄清": "uncertain",
        "待确认": "uncertain",
    }
    return aliases.get(compact, value)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReadingLevel(str, Enum):
    FULL_TEXT = "full_text"
    PARTIAL_TEXT = "partial_text"
    ABSTRACT_ONLY = "abstract_only"
    METADATA_ONLY = "metadata_only"
    SYNTHESIS_INFERENCE = "synthesis_inference"
    BRAINSTORM = "brainstorm"


class EvidencePermission(str, Enum):
    RECALL = "recall"
    PROBLEM_ANCHOR = "problem_anchor"
    MECHANISM_SUPPORT = "mechanism_support"
    SUPPORT = "support"
    INSPIRATION = "inspiration"
    CONDITIONAL_FINAL_CLAIM = "conditional_final_claim"
    FINAL_CLAIM = "final_claim"
    RESOURCE_LEAD = "resource_lead"


class EvidenceRole(str, Enum):
    ANCHOR = "anchor"
    SUPPORT = "support"
    INSPIRATION = "inspiration"
    CONJECTURE = "conjecture"


class EvidenceStatus(str, Enum):
    DIRECT_SUPPORT = "direct_support"
    LIMITED_SUPPORT = "limited_support"
    ABSTRACT_HINT = "abstract_hint"
    LLM_INFERENCE = "llm_inference"
    CONJECTURE = "conjecture"


class DomainRole(str, Enum):
    CORE = "core"
    BRIDGE = "bridge"
    ADJACENT = "adjacent"


class CandidateMaturity(str, Enum):
    SEED = "seed"
    EVOLVED = "evolved"
    SELECTED = "selected"
    ARCHIVED = "archived"
    LEGACY_PARTIAL = "legacy_partial"


class CandidateStatus(str, Enum):
    ACTIVE = "active"
    ELITE = "elite"
    PORTFOLIO = "portfolio"
    ARCHIVED = "archived"
    UNSUPPORTED = "unsupported"
    SELECTED = "selected"


class EvolutionOperator(str, Enum):
    DEEPEN_MECHANISM = "deepen_mechanism"
    REFRAME_PROBLEM = "reframe_problem"
    CHALLENGE_ASSUMPTION = "challenge_assumption"
    TIGHTEN_CONTRIBUTION = "tighten_contribution"
    REPAIR_VALIDATION = "repair_validation"
    NARROW_BOUNDARY = "narrow_boundary"
    EVIDENCE_UPGRADE = "evidence_upgrade"
    MUTATION = "mutation"
    CROSSOVER = "crossover"


class EvolutionPhase(str, Enum):
    PRE_RUN = "pre_run"
    EVIDENCE_ROUTING = "evidence_routing"
    OPPORTUNITY_MAP = "opportunity_map"
    FORMATION = "formation"
    GENOME_FAMILY = "genome_family"
    SCORING = "scoring"
    EVOLUTION_PLANNING = "evolution_planning"
    OFFSPRING = "offspring"
    SURVIVAL = "survival"
    WAITING_HUMAN = "waiting_human"
    SELECTED_COMPILATION = "selected_compilation"
    COMPLETE = "complete"


def _validate_identifier(value: str) -> str:
    cleaned = value.strip()
    if not _IDENTIFIER_RE.fullmatch(cleaned):
        raise ValueError("must be a stable identifier beginning with a letter")
    return cleaned


def _validate_workspace_relative_path(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    if cleaned.startswith("/") or ".." in cleaned.split("/"):
        raise ValueError("source_path must be workspace-relative")
    return cleaned


def _require_meaningful_text(value: object, field: str) -> str:
    """Require content without encoding an English-centric prose-length rule."""

    text = " ".join(str(value or "").split())
    if not text or _PLACEHOLDER_TEXT_RE.fullmatch(text):
        raise ValueError(f"{field} must contain a non-placeholder value")
    return text


class SourceRef(_StrictModel):
    source_path: str = Field(min_length=1)
    locator: str = ""
    citation_key: str = ""
    paper_id: str = ""
    note: str = ""

    @field_validator("source_path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        return _validate_workspace_relative_path(value)


class EvidenceAtom(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    atom_id: str
    paper_id: str = ""
    source_path: str = Field(min_length=1)
    section_key: str = Field(min_length=1)
    section_title: str = ""
    content: str = Field(min_length=1)
    domain_role: DomainRole = DomainRole.CORE
    reading_level: ReadingLevel
    evidence_status: EvidenceStatus
    allowed_uses: set[EvidencePermission] = Field(default_factory=set)
    forbidden_uses: set[EvidencePermission] = Field(default_factory=set)
    citation_key: str = ""
    bridge_ids: list[str] = Field(default_factory=list)
    requires_original_section_check: bool = True
    content_fingerprint: str = ""

    _id = field_validator("atom_id")(_validate_identifier)
    _path = field_validator("source_path")(_validate_workspace_relative_path)

    @model_validator(mode="after")
    def check_permission_boundary(self) -> "EvidenceAtom":
        if self.allowed_uses & self.forbidden_uses:
            raise ValueError("allowed_uses and forbidden_uses must not overlap")
        forbidden_by_level: dict[ReadingLevel, set[EvidencePermission]] = {
            ReadingLevel.ABSTRACT_ONLY: {
                EvidencePermission.MECHANISM_SUPPORT,
                EvidencePermission.CONDITIONAL_FINAL_CLAIM,
                EvidencePermission.FINAL_CLAIM,
            },
            ReadingLevel.METADATA_ONLY: {
                EvidencePermission.PROBLEM_ANCHOR,
                EvidencePermission.MECHANISM_SUPPORT,
                EvidencePermission.SUPPORT,
                EvidencePermission.CONDITIONAL_FINAL_CLAIM,
                EvidencePermission.FINAL_CLAIM,
            },
            ReadingLevel.SYNTHESIS_INFERENCE: {
                EvidencePermission.MECHANISM_SUPPORT,
                EvidencePermission.CONDITIONAL_FINAL_CLAIM,
                EvidencePermission.FINAL_CLAIM,
            },
            ReadingLevel.BRAINSTORM: {
                EvidencePermission.PROBLEM_ANCHOR,
                EvidencePermission.MECHANISM_SUPPORT,
                EvidencePermission.SUPPORT,
                EvidencePermission.CONDITIONAL_FINAL_CLAIM,
                EvidencePermission.FINAL_CLAIM,
            },
        }
        prohibited = forbidden_by_level.get(self.reading_level, set()) & self.allowed_uses
        if prohibited:
            raise ValueError(
                "reading level cannot grant: " + ", ".join(sorted(item.value for item in prohibited))
            )
        return self


class GeneProvenance(_StrictModel):
    source_routes: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    reading_levels: list[ReadingLevel] = Field(default_factory=list)
    evidence_role: EvidenceRole = EvidenceRole.CONJECTURE
    confidence: Literal["high", "medium", "low"] = "low"
    upgrade_required: bool = False

    @model_validator(mode="after")
    def preserve_permission_calibration(self) -> "GeneProvenance":
        if ReadingLevel.ABSTRACT_ONLY in self.reading_levels and self.evidence_role in {
            EvidenceRole.ANCHOR,
            EvidenceRole.SUPPORT,
        }:
            raise ValueError("abstract-only provenance can be inspiration or conjecture, not anchor/support")
        if self.evidence_role in {EvidenceRole.ANCHOR, EvidenceRole.SUPPORT} and not self.source_refs:
            raise ValueError("anchor/support provenance requires at least one source_ref")
        return self


class IdeaGene(_StrictModel):
    value: Any
    provenance: GeneProvenance = Field(default_factory=GeneProvenance)

    @field_validator("value")
    @classmethod
    def no_empty_gene(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("gene value must not be empty")
        return value


class OpportunityQuery(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    opportunity_id: str
    type: Literal[
        "cross_paper_tension",
        "hidden_assumption",
        "mechanism_gap",
        "failure_boundary",
        "evaluation_blind_spot",
        "design_rationale_conflict",
        "unexplained_phenomenon",
        "disconnected_mechanism",
        "user_seed_challenge",
        "survey_challenge",
        "bridge_transfer_opportunity",
    ]
    one_line_summary: str = Field(min_length=1)
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    evidence_atom_ids: list[str] = Field(default_factory=list)
    compatible_routes: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    # Opportunity planning is allowed to use the model's scholarly knowledge
    # and analogical reasoning.  These fields make that epistemic status
    # visible; they do not turn a conjecture into evidence or a verified gap.
    knowledge_origin: Literal[
        "workspace_evidence",
        "llm_parametric_knowledge",
        "cross_domain_analogy",
        "mixed",
    ] = "workspace_evidence"
    verification_required: bool = False
    conceptual_leap: str = ""
    competing_explanations: list[str] = Field(default_factory=list)
    priority_components: dict[str, float] = Field(default_factory=dict)
    priority_score: float | None = Field(default=None, ge=0, le=5)

    _id = field_validator("opportunity_id")(_validate_identifier)

    @model_validator(mode="after")
    def opportunity_text_is_meaningful(self) -> "OpportunityQuery":
        for field in ("one_line_summary", "question", "why_it_matters"):
            _require_meaningful_text(getattr(self, field), field)
        return self


class EvidenceBundleItem(_StrictModel):
    atom_id: str
    use_as: EvidenceRole
    upgrade_required: bool = False

    _id = field_validator("atom_id")(_validate_identifier)


class EvidenceBundle(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    bundle_id: str
    opportunity_ids: list[str] = Field(min_length=1)
    route: str = Field(min_length=1)
    anchors: list[EvidenceBundleItem] = Field(default_factory=list)
    expansions: list[EvidenceBundleItem] = Field(default_factory=list)
    bridges: list[EvidenceBundleItem] = Field(default_factory=list)
    user_constraints: list[str] = Field(default_factory=list)
    known_boundaries: list[str] = Field(default_factory=list)
    reading_upgrade_hints: list[str] = Field(default_factory=list)
    context_budget: dict[str, int] = Field(default_factory=dict)
    status: Literal["ready", "warning", "unsupported"] = "ready"
    unsupported_reason: str = ""

    _id = field_validator("bundle_id")(_validate_identifier)

    @model_validator(mode="after")
    def unsupported_has_reason(self) -> "EvidenceBundle":
        if self.status == "unsupported" and not self.unsupported_reason.strip():
            raise ValueError("unsupported bundle requires unsupported_reason")
        return self


class ProvisionalHypothesis(_StrictModel):
    hypothesis_id: str
    statement: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    observable_prediction: str = Field(min_length=1)
    discriminating_test: str = Field(min_length=1)
    # This is a forward-looking implication of the provisional hypothesis,
    # not a claim that it has already been established.  Keeping it here lets
    # a Child retain useful scientific interpretation without confusing it
    # with a Contribution-level result.
    what_changes_if_true: str = ""
    evidence_status: str = "proposed_not_verified"

    _id = field_validator("hypothesis_id")(_validate_identifier)

    @model_validator(mode="after")
    def hypothesis_text_is_meaningful(self) -> "ProvisionalHypothesis":
        for field in ("statement", "mechanism", "observable_prediction", "discriminating_test"):
            _require_meaningful_text(getattr(self, field), field)
        return self


class Contribution(_StrictModel):
    contribution_id: str
    statement: str = Field(min_length=1)
    contribution_type: Literal["invention", "improvement", "exaptation", "measurement", "mechanism", "theory", "design"]
    what_changes_if_true: str = Field(min_length=1)

    _id = field_validator("contribution_id")(_validate_identifier)

    @model_validator(mode="after")
    def contribution_text_is_meaningful(self) -> "Contribution":
        _require_meaningful_text(self.statement, "statement")
        _require_meaningful_text(self.what_changes_if_true, "what_changes_if_true")
        return self


class CandidatePresentation(_StrictModel):
    """LLM-authored scientific prose required by the legacy Gate1 projection."""

    title: str = Field(min_length=1)
    display_title: str = Field(min_length=1)
    basis_summary: str = Field(min_length=1)
    practical_implication: str = Field(min_length=1)
    counterfactual: str = Field(min_length=1)
    # ``gate1_card`` is a legacy compatibility view.  It is not the current
    # researcher decision surface: FinalIdeaCardTranslation is.  Keeping this
    # partial prevents an omitted old ``selection_advice`` string from
    # rejecting an otherwise coherent Candidate before the Final Card LLM gets
    # a chance to write its candidate-specific recommendation.
    gate1_card: dict[str, str] = Field(default_factory=dict)
    basis_sources: list[dict[str, str]] = Field(min_length=1)
    innovation: dict[str, str]
    minimum_validation: dict[str, object]
    idea_origin: str = Field(min_length=1)
    constraint_status: Literal["mainline", "supplement", "bridge", "not_supported_by_current_evidence"]
    mechanism_family: str = Field(min_length=1)
    cross_domain_sources: list[str] = Field(default_factory=list)
    cross_domain_relation: str = ""

    @model_validator(mode="after")
    def presentation_is_complete(self) -> "CandidatePresentation":
        for field in (
            "title",
            "display_title",
            "basis_summary",
            "practical_implication",
            "counterfactual",
            "idea_origin",
            "mechanism_family",
        ):
            _require_meaningful_text(getattr(self, field), field)
        # ``selection_advice`` is deliberately not a Candidate invariant.  It
        # is selection prose and is now owned by the Final Card Compiler's
        # required LLM-authored ``recommendation`` field.  Existing artifacts
        # may retain it for compatibility, but an absent value must schedule
        # card enrichment rather than invalidate the Candidate.
        required_card_fields = {"role_summary", "evidence_interpretation", "risk_summary", "user_edit_hint"}
        if not required_card_fields.issubset(self.gate1_card):
            raise ValueError("gate1_card is missing required user-facing fields")
        if not {"summary", "type", "novelty_delta", "non_incremental_reason"}.issubset(self.innovation):
            raise ValueError("innovation is missing required Gate1 fields")
        for source in self.basis_sources:
            if not {"ref", "claim", "implication"}.issubset(source):
                raise ValueError("each basis_source needs ref, claim, and implication")
            for field in ("ref", "claim", "implication"):
                _require_meaningful_text(source[field], f"basis_source.{field}")
        for field in required_card_fields:
            _require_meaningful_text(self.gate1_card[field], f"gate1_card.{field}")
        if "selection_advice" in self.gate1_card and self.gate1_card["selection_advice"].strip():
            _require_meaningful_text(self.gate1_card["selection_advice"], "gate1_card.selection_advice")
        for field in ("summary", "type", "novelty_delta", "non_incremental_reason"):
            _require_meaningful_text(self.innovation[field], f"innovation.{field}")
        if self.cross_domain_sources and not self.cross_domain_relation.strip():
            raise ValueError("cross_domain_sources require cross_domain_relation")
        return self


class BridgeCoverageEntry(_StrictModel):
    """LLM-authored Bridge visibility or escape-hatch reasoning for one bridge."""

    bridge_id: str
    candidate_ids: list[str] = Field(default_factory=list)
    visible_to_gate: bool
    decision_summary: str = Field(min_length=1)
    escape_status: Literal["not_needed_selected", "deferred", "rejected", "merged", "no_candidate_available"]
    escape_reason: str = Field(min_length=1)
    falsification_or_kill_criteria: str = Field(min_length=1)
    can_revisit_if: str = Field(min_length=1)

    _id = field_validator("bridge_id")(_validate_identifier)

    @field_validator("candidate_ids")
    @classmethod
    def unique_candidate_ids(cls, value: list[str]) -> list[str]:
        normalized = [_validate_identifier(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("bridge coverage candidate_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def bridge_explanations_are_meaningful(self) -> "BridgeCoverageEntry":
        for field in (
            "decision_summary",
            "escape_reason",
            "falsification_or_kill_criteria",
            "can_revisit_if",
        ):
            _require_meaningful_text(getattr(self, field), field)
        return self


class IdeaSeed(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    version: int = Field(default=1, ge=1)
    maturity: CandidateMaturity = CandidateMaturity.SEED
    route: str = Field(min_length=1)
    one_line_thesis: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    opportunity: str = Field(min_length=1)
    candidate_mechanism: str = Field(min_length=1)
    contribution_sketch: list[str] = Field(min_length=1)
    provisional_predictions: list[str] = Field(min_length=1)
    main_uncertainty: str = Field(min_length=1)
    evidence_refs: list[SourceRef] = Field(default_factory=list)
    evidence_role: EvidenceRole = EvidenceRole.CONJECTURE
    unsupported_reason: str = ""

    _id = field_validator("candidate_id")(_validate_identifier)


class RouteGenerationResult(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    route: str = Field(min_length=1)
    status: Literal["supported", "unsupported", "partial"]
    candidate_ids: list[str] = Field(default_factory=list)
    unsupported_reason: str = ""
    repaired_once: bool = False
    bridge_reviews: list[BridgeCoverageEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def route_result_integrity(self) -> "RouteGenerationResult":
        if self.status == "unsupported" and not self.unsupported_reason.strip():
            raise ValueError("unsupported route requires unsupported_reason")
        if self.status == "supported" and not self.candidate_ids:
            raise ValueError("supported route requires candidate_ids")
        return self


class IdeaGenome(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    version: int = Field(default=1, ge=1)
    generation_created: int = Field(default=0, ge=0)
    maturity: CandidateMaturity = CandidateMaturity.EVOLVED
    route: str = Field(min_length=1)
    parents: list[str] = Field(default_factory=list)
    problem: IdeaGene
    opportunity: IdeaGene
    challenged_assumption: IdeaGene
    core_thesis: IdeaGene
    mechanism: IdeaGene
    design_or_artifact: IdeaGene
    contribution_package: IdeaGene
    hypothesis_bundle: IdeaGene
    validation_logic: IdeaGene
    boundary_conditions: IdeaGene
    risks: IdeaGene
    migration_quality: Literal["native", "legacy_partial"] = "native"

    _id = field_validator("candidate_id")(_validate_identifier)

    @field_validator("parents")
    @classmethod
    def unique_parents(cls, value: list[str]) -> list[str]:
        normalized = [_validate_identifier(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("parents must be unique")
        return normalized

    @model_validator(mode="after")
    def parent_cannot_be_self(self) -> "IdeaGenome":
        if self.candidate_id in self.parents:
            raise ValueError("candidate cannot be its own parent")
        return self


class IdeaFamily(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    family_id: str
    generation: int = Field(ge=0)
    family_title: str = Field(min_length=1)
    shared_problem: str = Field(min_length=1)
    member_ids: list[str] = Field(min_length=1)
    champion_id: str | None = None
    sibling_family_ids: list[str] = Field(default_factory=list)
    gene_donors: dict[str, str] = Field(default_factory=dict)

    _id = field_validator("family_id")(_validate_identifier)

    @field_validator("member_ids", "sibling_family_ids")
    @classmethod
    def unique_identifiers(cls, value: list[str]) -> list[str]:
        normalized = [_validate_identifier(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("identifiers must be unique")
        return normalized

    @model_validator(mode="after")
    def family_consistency(self) -> "IdeaFamily":
        if self.champion_id is not None and self.champion_id not in self.member_ids:
            raise ValueError("champion_id must be a family member")
        if self.family_id in self.sibling_family_ids:
            raise ValueError("family cannot be its own sibling")
        return self


CORE_SCORE_DIMENSIONS = (
    "research_value",
    "mechanism_integrity",
    "contribution_distinctiveness",
)


class QualitativeDiagnostic(str, Enum):
    """A non-ranking diagnostic level used by the new T4 score contract.

    ``ScoreReport`` deliberately keeps this as a qualitative label rather
    than a fourth numerical dimension.  The small arithmetic shims are only a
    backwards-compatibility bridge for old population artifacts and callers
    that have not yet been migrated; new selection code should use the label
    as a non-blocking diagnostic, never as a formal score.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    NOT_ASSESSED = "not_assessed"

    @property
    def legacy_rank(self) -> float:
        return {
            QualitativeDiagnostic.LOW: 2.0,
            QualitativeDiagnostic.MEDIUM: 3.0,
            QualitativeDiagnostic.HIGH: 4.0,
            QualitativeDiagnostic.NOT_ASSESSED: 3.0,
        }[self]

    def __float__(self) -> float:
        return self.legacy_rank

    def __neg__(self) -> float:
        return -self.legacy_rank

    def __mul__(self, other: object) -> float:
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return self.legacy_rank * float(other)
        return NotImplemented

    def __rmul__(self, other: object) -> float:
        return self.__mul__(other)


def _qualitative_diagnostic(value: object) -> QualitativeDiagnostic:
    """Tolerantly map legacy numeric or textual diagnostics to a label.

    This is intentionally a migration aid, not a replacement for an LLM
    assessment.  Unknown values degrade to ``not_assessed`` rather than
    rejecting an otherwise usable score report.
    """

    if isinstance(value, QualitativeDiagnostic):
        return value
    if isinstance(value, str):
        cleaned = " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())
        aliases = {
            "low": QualitativeDiagnostic.LOW,
            "weak": QualitativeDiagnostic.LOW,
            "limited": QualitativeDiagnostic.LOW,
            "low uncertainty": QualitativeDiagnostic.LOW,
            "low confidence": QualitativeDiagnostic.LOW,
            "低": QualitativeDiagnostic.LOW,
            "低不确定性": QualitativeDiagnostic.LOW,
            "medium": QualitativeDiagnostic.MEDIUM,
            "moderate": QualitativeDiagnostic.MEDIUM,
            "mixed": QualitativeDiagnostic.MEDIUM,
            "medium uncertainty": QualitativeDiagnostic.MEDIUM,
            "中": QualitativeDiagnostic.MEDIUM,
            "中等不确定性": QualitativeDiagnostic.MEDIUM,
            "high": QualitativeDiagnostic.HIGH,
            "strong": QualitativeDiagnostic.HIGH,
            "very high": QualitativeDiagnostic.HIGH,
            "high uncertainty": QualitativeDiagnostic.HIGH,
            "高": QualitativeDiagnostic.HIGH,
            "高不确定性": QualitativeDiagnostic.HIGH,
            "not assessed": QualitativeDiagnostic.NOT_ASSESSED,
            "unknown": QualitativeDiagnostic.NOT_ASSESSED,
            "n a": QualitativeDiagnostic.NOT_ASSESSED,
            "未评估": QualitativeDiagnostic.NOT_ASSESSED,
            "未评定": QualitativeDiagnostic.NOT_ASSESSED,
        }
        if cleaned in aliases:
            return aliases[cleaned]
        try:
            value = float(cleaned)
        except ValueError:
            return QualitativeDiagnostic.NOT_ASSESSED
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric <= 2:
            return QualitativeDiagnostic.LOW
        if numeric < 3.5:
            return QualitativeDiagnostic.MEDIUM
        return QualitativeDiagnostic.HIGH
    return QualitativeDiagnostic.NOT_ASSESSED


def _legacy_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _score_explanation_present(value: object) -> bool:
    text = " ".join(str(value or "").split())
    return bool(text and not _PLACEHOLDER_TEXT_RE.fullmatch(text))


class ScoreDimensions(_StrictModel):
    """The three and only three formal numerical T4 score dimensions."""

    research_value: float = Field(ge=1, le=5)
    mechanism_integrity: float = Field(ge=1, le=5)
    contribution_distinctiveness: float = Field(ge=1, le=5)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_five_dimension_payloads(cls, value: object) -> object:
        """Ignore legacy diagnostic dimensions when this model is read alone.

        ``ScoreReport`` preserves those values under ``diagnostics`` before
        constructing this model.  This narrower fallback is for historical
        callers that directly deserialize ``ScoreDimensions``.
        """

        if not isinstance(value, dict):
            return value
        return {key: value[key] for key in CORE_SCORE_DIMENSIONS if key in value}


class ScoreDiagnostics(_StrictModel):
    """Non-blocking observations retained beside, never inside, core scores."""

    evidence_calibration: str = ""
    validation_feasibility: str = ""
    legacy_numeric_values: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def tolerate_diagnostic_shape_drift(cls, value: object) -> object:
        if not isinstance(value, dict):
            return {}
        normalized = dict(value)
        for field in ("evidence_calibration", "validation_feasibility"):
            raw = normalized.get(field)
            normalized[field] = "" if raw in (None, "") else str(raw)
        legacy = normalized.get("legacy_numeric_values")
        if isinstance(legacy, dict):
            normalized["legacy_numeric_values"] = {
                str(key): numeric
                for key, item in legacy.items()
                if str(key).strip() and (numeric := _legacy_number(item)) is not None
            }
        else:
            normalized["legacy_numeric_values"] = {}
        warnings = normalized.get("warnings")
        if isinstance(warnings, str):
            warnings = [warnings]
        normalized["warnings"] = [str(item) for item in warnings if str(item).strip()] if isinstance(warnings, list) else []
        return normalized


def _derive_unweighted_overall(scores: ScoreDimensions) -> float:
    """Return a neutral derived summary for profile-free artifact reads."""

    return round(
        (scores.research_value + scores.mechanism_integrity + scores.contribution_distinctiveness) / 3,
        4,
    )


class ProfileFitAssessment(_StrictModel):
    """Independent fit assessment for the selected publication orientation.

    This is intentionally separate from ``ScoreDimensions``.  It can change
    when a researcher changes the intended contribution narrative, while the
    core scientific score and Evidence Permission remain the same.
    """

    profile_type: Literal["utd_is", "ccf_cs", "management_is", "technical_cs", "hybrid", "custom"] = "hybrid"
    overall_fit: QualitativeDiagnostic = QualitativeDiagnostic.NOT_ASSESSED
    dimensions: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    cautions: list[str] = Field(default_factory=list)
    # Historic numerical Profile Fit is preserved solely for artifact
    # inspection.  New scoring calls must use qualitative fit diagnostics.
    legacy_numeric_values: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_numeric_profile_fit(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        raw_profile_type = " ".join(str(normalized.get("profile_type") or "hybrid").strip().casefold().replace("_", " ").split())
        profile_aliases = {
            "utd": "utd_is",
            "utd is": "utd_is",
            "informs": "utd_is",
            "ccf": "ccf_cs",
            "ccf a": "ccf_cs",
            "technical": "technical_cs",
            "technical cs": "technical_cs",
            "management": "management_is",
            "management is": "management_is",
        }
        normalized_profile_type = profile_aliases.get(raw_profile_type, raw_profile_type.replace(" ", "_"))
        normalized["profile_type"] = (
            normalized_profile_type
            if normalized_profile_type in {"utd_is", "ccf_cs", "management_is", "technical_cs", "hybrid", "custom"}
            else "custom"
        )
        legacy = dict(normalized.get("legacy_numeric_values") or {})
        numeric = _legacy_number(normalized.get("overall_fit"))
        if numeric is not None:
            legacy.setdefault("overall_fit", numeric)
        normalized["overall_fit"] = _qualitative_diagnostic(normalized.get("overall_fit")).value
        raw_dimensions = normalized.get("dimensions")
        if isinstance(raw_dimensions, dict):
            dimensions: dict[str, str] = {}
            for key, item in raw_dimensions.items():
                name = str(key).strip()
                if not name:
                    continue
                numeric_item = _legacy_number(item)
                if numeric_item is not None:
                    legacy.setdefault(f"dimension:{name}", numeric_item)
                    dimensions[name] = _qualitative_diagnostic(numeric_item).value
                else:
                    text = " ".join(str(item or "").split())
                    if text:
                        dimensions[name] = text
            normalized["dimensions"] = dimensions
        else:
            normalized["dimensions"] = {}
        normalized["rationale"] = "" if normalized.get("rationale") in (None, "") else str(normalized["rationale"])
        cautions = normalized.get("cautions")
        if isinstance(cautions, str):
            cautions = [cautions]
        normalized["cautions"] = [str(item) for item in cautions if str(item).strip()] if isinstance(cautions, list) else []
        normalized["legacy_numeric_values"] = legacy
        return normalized


class TargetProfile(_StrictModel):
    """User-confirmed publication orientation for one T4 run.

    The profile governs task emphasis and presentation, never source truth,
    Evidence Permission, citations, or immutable candidate lineage.
    """

    profile_type: Literal["utd_is", "ccf_cs", "management_is", "technical_cs", "hybrid", "custom"] = "hybrid"
    target_venues: list[str] = Field(default_factory=list)
    primary_orientation: Literal["theory_and_phenomenon", "technical_and_computational", "balanced"] = "balanced"
    priority_dimensions: list[str] = Field(default_factory=list)
    secondary_dimensions: list[str] = Field(default_factory=list)
    storytelling_emphasis: list[str] = Field(default_factory=list)
    scoring_profile: str = "hybrid"
    portfolio_profile_weight: float = Field(default=0.2, ge=0, le=0.5)
    user_instruction: str = ""
    inferred_from: list[str] = Field(default_factory=list)
    confirmed_by_user: bool = False
    confidence: Literal["high", "medium", "low"] = "low"

    @field_validator("target_venues", "priority_dimensions", "secondary_dimensions", "storytelling_emphasis", "inferred_from")
    @classmethod
    def unique_nonempty_strings(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = str(item).strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                result.append(cleaned)
                seen.add(key)
        return result


class ScoreReport(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    scoring_batch_id: str
    rubric_version: str = "2.0.0"
    blind: bool = True
    scores: ScoreDimensions
    # This historical field name is retained for existing artifacts and CLI
    # views.  It is always derived from the three formal dimensions, never
    # accepted as an independently assessed readiness gate.
    overall_readiness: float = Field(default=0.0, ge=1, le=5)
    score_uncertainty: QualitativeDiagnostic = QualitativeDiagnostic.NOT_ASSESSED
    rationales: dict[str, str] = Field(default_factory=dict)
    dominant_strength: str = ""
    dominant_bottleneck: str = ""
    preserve_genes: list[str] = Field(default_factory=list)
    modify_genes: list[str] = Field(default_factory=list)
    recommended_operators: list[EvolutionOperator] = Field(default_factory=list)
    # Retained only so historical population artifacts can be read. New
    # scorers express upside through the qualitative field below.
    high_upside: bool = False
    # All fields below are qualitative diagnostics.  They guide a human or an
    # evolution operator; they cannot make an otherwise valid Candidate fail.
    scientific_upside: QualitativeDiagnostic = QualitativeDiagnostic.NOT_ASSESSED
    scientific_upside_rationale: str = ""
    evolution_potential: QualitativeDiagnostic = QualitativeDiagnostic.NOT_ASSESSED
    recommended_crossover_role: str = ""
    wildcard_recommended: bool = False
    wildcard_rationale: str = ""
    uncertain: bool = False
    diagnostics: ScoreDiagnostics = Field(default_factory=ScoreDiagnostics)
    rationale_missing: list[str] = Field(default_factory=list)
    diagnostic_warnings: list[str] = Field(default_factory=list)
    # Deprecated Gate1 display values remain readable in historical artifacts,
    # but new prompts never require or generate them.
    compatibility_scores: dict[str, int] = Field(default_factory=dict)
    compatibility_rationales: dict[str, str] = Field(default_factory=dict)
    profile_fit: ProfileFitAssessment = Field(default_factory=ProfileFitAssessment)

    _id = field_validator("candidate_id", "scoring_batch_id")(_validate_identifier)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_score_contract(cls, value: object) -> object:
        """Move old non-core score fields into explicit diagnostics.

        Older persisted reports used a five-dimension ``scores`` object.  The
        two retired numeric values are retained without reinterpretation under
        ``diagnostics.legacy_numeric_values``.  This makes old artifacts
        readable while ensuring no new ScoreReport ever *requires* them.
        """

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        # ``overall_readiness`` is always recomputed below. Ignoring an old,
        # malformed, or provider-supplied value prevents it from becoming an
        # accidental fourth score gate.
        normalized.pop("overall_readiness", None)
        rationales = normalized.get("rationales")
        if isinstance(rationales, dict):
            normalized["rationales"] = {
                str(key): str(item)
                for key, item in rationales.items()
                if str(key).strip() and item is not None
            }
        else:
            normalized["rationales"] = {}
        for field in ("dominant_strength", "dominant_bottleneck", "scientific_upside_rationale", "recommended_crossover_role", "wildcard_rationale"):
            raw = normalized.get(field)
            normalized[field] = "" if raw in (None, "") else str(raw)
        for field in ("preserve_genes", "modify_genes", "rationale_missing", "diagnostic_warnings"):
            raw = normalized.get(field)
            if isinstance(raw, str):
                raw = [raw]
            normalized[field] = [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []
        operators = normalized.get("recommended_operators")
        if isinstance(operators, str):
            operators = [operators]
        valid_operators = {item.value for item in EvolutionOperator}
        normalized["recommended_operators"] = [
            raw
            for item in operators
            if (raw := item.value if isinstance(item, EvolutionOperator) else str(item)) in valid_operators
        ] if isinstance(operators, list) else []
        if not isinstance(normalized.get("compatibility_scores"), dict):
            normalized["compatibility_scores"] = {}
        if not isinstance(normalized.get("compatibility_rationales"), dict):
            normalized["compatibility_rationales"] = {}
        if not isinstance(normalized.get("profile_fit"), dict):
            normalized.pop("profile_fit", None)
        if not isinstance(normalized.get("diagnostics"), dict):
            normalized["diagnostics"] = {}
        diagnostics = dict(normalized.get("diagnostics") or {})
        legacy = dict(diagnostics.get("legacy_numeric_values") or {})

        def move_legacy_diagnostic(name: str, raw: object) -> None:
            if raw in (None, "", [], {}):
                return
            numeric = _legacy_number(raw)
            if numeric is not None:
                legacy.setdefault(name, numeric)
                return
            if name == "evidence_calibration" and not diagnostics.get("evidence_calibration"):
                diagnostics["evidence_calibration"] = str(raw)
            if name in {"validation_tractability", "validation_feasibility"} and not diagnostics.get("validation_feasibility"):
                diagnostics["validation_feasibility"] = str(raw)

        raw_scores = normalized.get("scores")
        if isinstance(raw_scores, dict):
            scores = dict(raw_scores)
            for legacy_key in ("evidence_calibration", "validation_tractability", "validation_feasibility"):
                move_legacy_diagnostic(legacy_key, scores.pop(legacy_key, None))
            normalized["scores"] = scores
        for legacy_key in ("evidence_calibration", "validation_tractability", "validation_feasibility"):
            move_legacy_diagnostic(legacy_key, normalized.pop(legacy_key, None))
        for legacy_key in ("score_uncertainty", "scientific_upside", "evolution_potential"):
            numeric = _legacy_number(normalized.get(legacy_key))
            if numeric is not None:
                legacy.setdefault(legacy_key, numeric)
        diagnostics["legacy_numeric_values"] = legacy
        normalized["diagnostics"] = diagnostics
        return normalized

    @field_validator("score_uncertainty", "scientific_upside", "evolution_potential", mode="before")
    @classmethod
    def qualitative_diagnostics_only(cls, value: object) -> QualitativeDiagnostic:
        return _qualitative_diagnostic(value)

    @model_validator(mode="after")
    def score_integrity(self) -> "ScoreReport":
        # The summary is deterministic so an LLM cannot promote an Idea by
        # supplying an unrelated readiness value. Orientation-specific ranking
        # weights are applied by the score parser when a target profile exists.
        object.__setattr__(self, "overall_readiness", _derive_unweighted_overall(self.scores))
        missing = [key for key in CORE_SCORE_DIMENSIONS if not _score_explanation_present(self.rationales.get(key))]
        warnings = list(dict.fromkeys([*self.diagnostic_warnings, *(f"rationale_missing:{key}" for key in missing)]))
        object.__setattr__(self, "rationale_missing", list(dict.fromkeys([*self.rationale_missing, *missing])))
        object.__setattr__(self, "diagnostic_warnings", warnings)
        if set(self.preserve_genes) & set(self.modify_genes):
            raise ValueError("preserve_genes and modify_genes must not overlap")
        return self


class GeneDonorMap(_StrictModel):
    donors: dict[str, str] = Field(min_length=1)
    synthesized_genes: list[str] = Field(default_factory=list)

    @field_validator("donors")
    @classmethod
    def valid_donor_ids(cls, value: dict[str, str]) -> dict[str, str]:
        if not all(key.strip() and _IDENTIFIER_RE.fullmatch(item.strip()) for key, item in value.items()):
            raise ValueError("gene donor map requires non-empty gene names and stable candidate IDs")
        return {key.strip(): item.strip() for key, item in value.items()}


class EvolutionPlan(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str
    plan_fingerprint: str = Field(min_length=16)
    round: int = Field(ge=1)
    child_type: Literal["mutation", "crossover"]
    parent_ids: list[str] = Field(min_length=1, max_length=2)
    operator: EvolutionOperator
    preserve_genes: list[str] = Field(default_factory=list)
    modify_genes: list[str] = Field(default_factory=list)
    gene_donor_map: GeneDonorMap | None = None
    constraints: list[str] = Field(default_factory=list)
    expected_improvements: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)

    _id = field_validator("plan_id")(_validate_identifier)

    @model_validator(mode="after")
    def plan_integrity(self) -> "EvolutionPlan":
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("parent_ids must be unique")
        if self.child_type == "mutation" and len(self.parent_ids) != 1:
            raise ValueError("mutation requires exactly one parent")
        if self.child_type == "crossover":
            if len(self.parent_ids) != 2:
                raise ValueError("crossover requires exactly two parents")
            if self.gene_donor_map is None:
                raise ValueError("crossover requires a gene_donor_map")
        if set(self.preserve_genes) & set(self.modify_genes):
            raise ValueError("preserve_genes and modify_genes must not overlap")
        return self


class EvolutionPlanDeferral(_StrictModel):
    """A bounded, auditable decision not to force a speculative Child.

    An Evolution Plan is an invitation to explore, not a command to manufacture
    a cosmetic rewrite.  The Evolver may use this object only when it cannot
    create a substantive, plan-consistent Child without inventing support or
    obscuring an unresolved incompatibility.  It is deliberately not a
    Candidate and therefore cannot affect ranking, selection, or evidence.
    """

    plan_id: str
    status: Literal["no_improvement", "incompatible", "deferred"]
    rationale: str = Field(min_length=1)
    revisit_condition: str = Field(min_length=1)

    _id = field_validator("plan_id")(_validate_identifier)

    @model_validator(mode="after")
    def deferral_is_actionable(self) -> "EvolutionPlanDeferral":
        _require_meaningful_text(self.rationale, "rationale")
        _require_meaningful_text(self.revisit_condition, "revisit_condition")
        return self


class CandidateLineage(_StrictModel):
    candidate_id: str
    parent_ids: list[str] = Field(default_factory=list)
    route: str = Field(min_length=1)
    created_by: Literal["generator", "evolver", "human_composition", "legacy_migration"]
    evolution_plan_id: str = ""
    gene_delta_path: str = ""
    complexity_inflation: Literal["low", "medium", "high", "unknown"] = "unknown"

    _id = field_validator("candidate_id")(_validate_identifier)


class CreativeContext(_StrictModel):
    """LLM-authored scientific exploration preserved alongside the typed genome.

    These fields intentionally describe proposals rather than certified facts.
    They prevent a useful conceptual leap, competing explanation, or research
    programme from being compressed away merely because a first-pass Seed is
    not yet a final paper plan.
    """

    conceptual_leap: str = ""
    competing_explanations: list[str] = Field(default_factory=list)
    surprising_prediction: str = ""
    research_program_potential: str = ""
    knowledge_origin: Literal[
        "workspace_evidence",
        "llm_parametric_knowledge",
        "cross_domain_analogy",
        "mixed",
    ] = "mixed"
    evidence_status: Literal["supported", "conjectural", "mixed"] = "conjectural"
    verification_required: bool = True
    reading_or_validation_upgrades: list[str] = Field(default_factory=list)
    extended_validation_design: str = ""


class CandidateDossier(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    version: int = Field(ge=1)
    status: CandidateStatus
    maturity: CandidateMaturity
    genome: IdeaGenome
    contributions: list[Contribution] = Field(default_factory=list)
    hypotheses: list[ProvisionalHypothesis] = Field(default_factory=list)
    evidence_composition: dict[str, int] = Field(default_factory=dict)
    score_report_path: str = ""
    lineage: CandidateLineage
    artifact_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    creative_context: CreativeContext = Field(default_factory=CreativeContext)
    presentation: CandidatePresentation | None = None

    _id = field_validator("candidate_id")(_validate_identifier)

    @model_validator(mode="after")
    def dossier_integrity(self) -> "CandidateDossier":
        if self.candidate_id != self.genome.candidate_id or self.candidate_id != self.lineage.candidate_id:
            raise ValueError("dossier, genome, and lineage candidate_id must match")
        if len({item.contribution_id for item in self.contributions}) != len(self.contributions):
            raise ValueError("contribution IDs must be unique")
        if len({item.hypothesis_id for item in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("hypothesis IDs must be unique")
        # Mature native Candidates are decision-ready only when their scientific
        # package is neither under-specified nor mechanically over-expanded.
        # Legacy migrations remain readable as explicitly partial records.
        if self.maturity == CandidateMaturity.EVOLVED:
            if not 2 <= len(self.contributions) <= 4:
                raise ValueError("an evolved candidate requires 2-4 contributions")
            if not 2 <= len(self.hypotheses) <= 4:
                raise ValueError("an evolved candidate requires 2-4 provisional hypotheses")
        return self


class ImpactImplication(_StrictModel):
    implication_type: Literal["scientific", "engineering", "managerial", "business", "deployment"]
    statement: str = Field(min_length=1)
    evidence_status: EvidenceStatus
    conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def implication_text_is_complete(self) -> "ImpactImplication":
        _require_meaningful_text(self.statement, "implications.statement")
        for index, condition in enumerate(self.conditions):
            _require_meaningful_text(condition, f"implications.conditions[{index}]")
        return self


class FinalIdeaCardTranslation(_StrictModel):
    """A non-mutating, LLM-authored presentation of a portfolio Candidate.

    The card is the researcher's decision surface at Gate1.  Its explanatory
    fields therefore cannot be silently omitted and reconstructed by a
    renderer from a score, title, or another Candidate field.  Lists remain
    optional where an Idea genuinely has no applicable stakeholder,
    implication, condition, or dependency; the LLM must still explain that
    absence in the relevant narrative fields.
    """

    candidate_id: str
    profile_type: Literal["utd_is", "ccf_cs", "management_is", "technical_cs", "hybrid", "custom"]
    core_thesis: str = Field(min_length=1)
    contribution_ids: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    plain_language_summary: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    affected_stakeholders_or_processes: list[str] = Field(default_factory=list)
    representative_scenario: str = Field(min_length=1)
    real_world_significance: str = Field(min_length=1)
    current_failure: str = Field(min_length=1)
    scientific_technical_core: str = Field(min_length=1)
    implications: list[ImpactImplication] = Field(default_factory=list)
    conditions_for_impact: list[str] = Field(default_factory=list)
    claims_not_to_make: list[str] = Field(min_length=1)
    risks_and_boundaries: list[str] = Field(min_length=1)
    evidence_status_summary: str = Field(min_length=1)
    # These are LLM-authored presentation semantics for comparing a Portfolio.
    # They never alter the Candidate Genome, scores, sources, or lineage.  They
    # are required because an empty value would otherwise make the renderer
    # fall back to deterministic or unrelated Candidate text.
    short_title: str = Field(min_length=1)
    contribution_type_label: str = Field(min_length=1)
    innovation_type: str = Field(min_length=1)
    innovation_delta: str = Field(min_length=1)
    non_routine_explanation: str = Field(min_length=1)
    relationship_to_portfolio: str = Field(min_length=1)
    dependency_candidate_ids: list[str] = Field(default_factory=list)
    composition_guidance: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    bottleneck_explanation: str = Field(min_length=1)

    _id = field_validator("candidate_id")(_validate_identifier)

    @model_validator(mode="after")
    def card_explanations_are_complete(self) -> "FinalIdeaCardTranslation":
        """Reject missing or placeholder LLM prose before a Gate1 projection.

        This is deliberately a completeness boundary, not a scientific-quality
        rubric.  It does not decide whether an Idea is good, true, or ready for
        publication.  It only ensures that the LLM has supplied the explanations
        a researcher needs to compare the visible Portfolio without the renderer
        manufacturing substitute prose.
        """

        for field in (
            "plain_language_summary",
            "why_it_matters",
            "representative_scenario",
            "real_world_significance",
            "current_failure",
            "scientific_technical_core",
            "evidence_status_summary",
            "short_title",
            "contribution_type_label",
            "innovation_type",
            "innovation_delta",
            "non_routine_explanation",
            "relationship_to_portfolio",
            "composition_guidance",
            "recommendation",
            "bottleneck_explanation",
        ):
            _require_meaningful_text(getattr(self, field), field)
        for field in (
            "affected_stakeholders_or_processes",
            "conditions_for_impact",
            "claims_not_to_make",
            "risks_and_boundaries",
        ):
            for index, value in enumerate(getattr(self, field)):
                _require_meaningful_text(value, f"{field}[{index}]")
        if len(set(self.dependency_candidate_ids)) != len(self.dependency_candidate_ids):
            raise ValueError("dependency_candidate_ids must not contain duplicates")
        for candidate_id in self.dependency_candidate_ids:
            _validate_identifier(candidate_id)
        return self


class PopulationSnapshot(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    population_id: str
    generation: int = Field(ge=0)
    input_fingerprint: str = Field(min_length=16)
    run_config_fingerprint: str = Field(min_length=16)
    active_candidate_ids: list[str] = Field(default_factory=list)
    family_ids: list[str] = Field(default_factory=list)
    elite_candidate_ids: list[str] = Field(default_factory=list)
    archived_candidate_ids: list[str] = Field(default_factory=list)
    created_from_round: int | None = Field(default=None, ge=0)

    _id = field_validator("population_id")(_validate_identifier)

    @model_validator(mode="after")
    def population_integrity(self) -> "PopulationSnapshot":
        for name in ("active_candidate_ids", "family_ids", "elite_candidate_ids", "archived_candidate_ids"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        if set(self.active_candidate_ids) & set(self.archived_candidate_ids):
            raise ValueError("active and archived candidates must not overlap")
        if not set(self.elite_candidate_ids).issubset(self.active_candidate_ids):
            raise ValueError("elite candidates must be active")
        return self


class RoundArtifact(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    round: int = Field(ge=0)
    input_population_id: str
    output_population_id: str
    input_fingerprint: str = Field(min_length=16)
    run_config_fingerprint: str = Field(min_length=16)
    parent_ids: list[str] = Field(default_factory=list)
    offspring_ids: list[str] = Field(default_factory=list)
    survivor_ids: list[str] = Field(default_factory=list)
    archived_ids: list[str] = Field(default_factory=list)
    plan_ids: list[str] = Field(default_factory=list)
    score_batch_ids: list[str] = Field(default_factory=list)
    completion_status: Literal["completed", "partial", "failed"]

    _population_ids = field_validator("input_population_id", "output_population_id")(_validate_identifier)


class GeneDelta(_StrictModel):
    child_id: str
    parent_ids: list[str] = Field(min_length=1)
    classification: Literal["substantive", "clarification_only", "cosmetic", "regressive"]
    changed_genes: list[str] = Field(default_factory=list)
    preserved_genes: list[str] = Field(default_factory=list)
    violated_preserve_constraints: list[str] = Field(default_factory=list)
    word_count_growth_ratio: float = Field(ge=0)

    _id = field_validator("child_id")(_validate_identifier)


class ComplexityReport(_StrictModel):
    candidate_id: str
    complexity_growth: Literal["low", "medium", "high"]
    new_components: list[str] = Field(default_factory=list)
    new_data_requirements: list[str] = Field(default_factory=list)
    new_experiment_stages: list[str] = Field(default_factory=list)
    expected_gain: str = ""
    # Complexity is an evolution diagnosis, not an automatic scientific
    # rejection. A bold Candidate may deserve to survive with a narrowing or
    # validation target even when it expands the Parent's surface area.
    decision_hint: Literal["acceptable", "review_complexity", "reject_inflation"] = "acceptable"

    _id = field_validator("candidate_id")(_validate_identifier)


class IdeaContractResult(_StrictModel):
    candidate_id: str
    status: Literal["pass", "fail", "pass_with_warning"]
    contracts: dict[str, Literal["pass", "fail", "warning"]]
    hard_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    _id = field_validator("candidate_id")(_validate_identifier)


_CROSSOVER_EXPLANATION_KEYS = (
    "explanation",
    "rationale",
    "reason",
    "summary",
    "detail",
    "description",
    "assessment",
    "value",
)
_CROSSOVER_AUXILIARY_FIELDS = (
    "explanation",
    "reviewer_explanation",
    "reviewer_notes",
    "analysis",
    "notes",
    "rationale",
    "reason",
    "summary",
    "detail",
    "description",
    "assessment",
    "value",
)


def _normalize_crossover_review_payload(value: Any) -> Any:
    """Preserve explicit reviewer content across known response-shape variants.

    The compatibility record needs prose for three scientific judgements. Some
    providers emit that prose in a small wrapper such as
    ``{"compatible": true, "explanation": "..."}`` and include a top-level
    explanation for the human reviewer. Those are presentation-shape variants,
    not new scientific claims. Retain only text already returned by the model,
    drop the unmodeled auxiliary commentary, and leave unknown fields or opaque
    nested objects to the strict schema validator.

    This lives beside the durable model rather than in one LLM adapter so that
    an old plan loaded during resume receives exactly the same normalization as
    a newly received provider response.
    """

    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    decision = normalize_crossover_decision(normalized.get("decision"))
    normalized["decision"] = decision
    for field in (
        "problem_compatibility",
        "bottleneck_complementarity",
        "mechanism_coherence",
    ):
        nested = normalized.get(field)
        if not isinstance(nested, dict):
            continue
        for key in _CROSSOVER_EXPLANATION_KEYS:
            text = nested.get(key)
            if isinstance(text, str) and " ".join(text.split()):
                normalized[field] = " ".join(text.split())
                break

    # A former no-child compatibility projection stored one shared rationale
    # instead of repeating it in the three explanatory fields that the native
    # record now requires.  These verdicts cannot create an offspring, so
    # reusing the exact preserved rationale is a lossless presentation/schema
    # migration rather than a scientific inference.  An approved crossover
    # remains strict: it must state each of the three judgments explicitly
    # before it can authorize a new Candidate.
    if decision in {"parallel", "rejected", "uncertain"}:
        shared_explanation = ""
        for key in _CROSSOVER_EXPLANATION_KEYS:
            candidate = normalized.get(key)
            if isinstance(candidate, str) and " ".join(candidate.split()):
                shared_explanation = " ".join(candidate.split())
                break
        if not shared_explanation:
            conflicts = normalized.get("conflicts")
            if isinstance(conflicts, list):
                for candidate in conflicts:
                    text = " ".join(str(candidate or "").split())
                    if text:
                        shared_explanation = text
                        break
        if shared_explanation:
            for field in (
                "problem_compatibility",
                "bottleneck_complementarity",
                "mechanism_coherence",
            ):
                if not isinstance(normalized.get(field), str) or not " ".join(str(normalized.get(field) or "").split()):
                    normalized[field] = shared_explanation

    complexity = normalized.get("complexity_risk")
    if isinstance(complexity, dict):
        for key in ("level", "risk", "complexity", "value", "assessment"):
            label = complexity.get(key)
            if isinstance(label, str) and label.strip():
                normalized["complexity_risk"] = label
                break

    # ``complexity_risk`` is a compact scheduling label, while providers
    # occasionally place their full reviewer explanation in that slot.  The
    # explanation is still useful evidence for a researcher, but it must not
    # make a completed Evolution plan unreadable after a restart.  Preserve
    # the exact model-authored wording in ``conflicts`` and choose the
    # conservative label ``high``.  This repair can never make a crossover
    # easier to approve or remove a stated risk.
    complexity = normalized.get("complexity_risk")
    if isinstance(complexity, str):
        compact = " ".join(complexity.strip().casefold().split())
        known_labels = {
            "low",
            "medium",
            "moderate",
            "high",
            "低",
            "中",
            "中等",
            "高",
            "低风险",
            "中风险",
            "高风险",
            "低复杂度",
            "中等复杂度",
            "高复杂度",
        }
        if compact.startswith(("low", "低")):
            normalized["complexity_risk"] = "low"
        elif compact.startswith(("medium", "moderate", "中")):
            normalized["complexity_risk"] = "medium"
        elif compact.startswith(("high", "高")):
            normalized["complexity_risk"] = "high"
        elif compact and compact not in known_labels:
            note = " ".join(complexity.split())
            conflicts = normalized.get("conflicts")
            if isinstance(conflicts, list):
                normalized_conflicts = [str(item) for item in conflicts if str(item).strip()]
            else:
                normalized_conflicts = []
            preserved_note = f"Reviewer complexity note: {note}"
            if preserved_note not in normalized_conflicts:
                normalized_conflicts.append(preserved_note)
            normalized["conflicts"] = normalized_conflicts
            normalized["complexity_risk"] = "high"

    for field in _CROSSOVER_AUXILIARY_FIELDS:
        normalized.pop(field, None)
    return normalized


class CrossoverCompatibilityDecision(_StrictModel):
    pair_id: str
    parent_ids: list[str] = Field(min_length=2, max_length=2)
    # A compatibility review is allowed to preserve an explicit ``parallel``
    # conclusion. It is no more eligible to create a Child than ``rejected``
    # or ``uncertain``; only ``approved`` is consumed by
    # ``compile_crossover_plans``. Keeping the distinct verdict avoids
    # misreporting a useful portfolio-diversity conclusion as a generic reject.
    decision: Literal["approved", "rejected", "uncertain", "parallel"]
    problem_compatibility: str = Field(min_length=1)
    bottleneck_complementarity: str = Field(min_length=1)
    mechanism_coherence: str = Field(min_length=1)
    conflicts: list[str] = Field(default_factory=list)
    proposed_gene_donor_map: GeneDonorMap | None = None
    complexity_risk: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="before")
    @classmethod
    def _discard_no_child_donor_map(cls, value: Any) -> Any:
        """Keep a donor map only when a decision can actually create a Child.

        Providers commonly express a useful parallel/rejected verdict with
        ``proposed_gene_donor_map: {donors: {}}``.  That is not an incomplete
        crossover plan; it means precisely that no genes should be transferred.
        The map has no semantic use for a no-child verdict, so remove it before
        validating ``GeneDonorMap``.  An approved verdict deliberately retains
        the strict non-empty requirement below because it authorizes a real
        descendant.
        """

        if not isinstance(value, dict):
            return value
        normalized = _normalize_crossover_review_payload(value)
        decision = normalize_crossover_decision(normalized.get("decision"))
        if decision == "approved":
            return normalized
        donor_map = normalized.get("proposed_gene_donor_map")
        if donor_map is None:
            return normalized
        normalized.pop("proposed_gene_donor_map", None)
        return normalized

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, value: Any) -> Any:
        return normalize_crossover_decision(value)

    @field_validator("conflicts", mode="before")
    @classmethod
    def _normalize_conflicts(cls, value: Any) -> Any:
        """Accept a single reviewer conflict as the one-item list it denotes.

        A compatibility review frequently has one concise incompatibility
        statement.  Requiring the model to wrap that statement in an array is
        a surface-format concern, not a scientific integrity boundary.  Keep
        every supplied statement visible, but reject non-textual structures so
        a malformed donor map or hidden object cannot be mistaken for a
        conflict explanation.
        """

        if value is None:
            return []
        if isinstance(value, str):
            text = " ".join(value.split())
            return [text] if text else []
        if isinstance(value, (tuple, set)):
            return [" ".join(str(item).split()) for item in value if " ".join(str(item).split())]
        return value


class HumanCompositionCompatibility(_StrictModel):
    """LLM-authored semantic assessment for a component-level human request."""

    composition_id: str
    source_candidate_ids: list[str] = Field(min_length=2)
    source_components: list[str] = Field(min_length=2)
    problem_compatibility: Literal["high", "medium", "low"]
    assumption_conflict: Literal["none", "resolvable", "hard_conflict"]
    mechanism_compatibility: Literal["high", "medium", "low"]
    joint_testability: Literal["high", "medium", "low"]
    contribution_coherence: Literal["high", "medium", "low"]
    evidence_compatibility: Literal["high", "medium", "low"]
    complexity_risk: Literal["low", "medium", "high"]
    composition_type: Literal["complementary", "hierarchical", "parallel", "conflicting"]
    recommended_action: Literal["compose", "keep_parallel", "request_user_choice", "reject_auto_merge"]
    explanation_for_user: str = Field(min_length=1)
    required_repairs: list[str] = Field(default_factory=list)
    gene_donor_map: GeneDonorMap | None = None

    _id = field_validator("composition_id")(_validate_identifier)

    @model_validator(mode="after")
    def composition_integrity(self) -> "HumanCompositionCompatibility":
        _require_meaningful_text(self.explanation_for_user, "explanation_for_user")
        if len(set(self.source_candidate_ids)) != len(self.source_candidate_ids):
            raise ValueError("human composition source_candidate_ids must be unique")
        if self.recommended_action == "compose" and self.gene_donor_map is None:
            raise ValueError("composable human composition requires a Gene Donor Map")
        if self.assumption_conflict == "hard_conflict" and self.recommended_action == "compose":
            raise ValueError("hard assumption conflicts cannot be auto-composed")
        return self


class PortfolioSelection(_StrictModel):
    population_id: str
    lead_id: str | None = None
    alternative_ids: list[str] = Field(default_factory=list)
    high_upside_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)

    _id = field_validator("population_id")(_validate_identifier)


class T4RunConfig(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    mode: Literal["quick", "standard", "deep", "auto"] = "standard"
    rounds: int = Field(default=2, ge=0, le=3)
    allow_crossover: bool = True
    final_top_k: int = Field(default=3, ge=1, le=3)
    # A small recovered Population is valid. Route and diversity targets
    # influence exploration, but must not prevent one usable Candidate from
    # reaching a human review with its limitations visible.
    max_initial_population: int = Field(default=14, ge=1, le=30)
    active_population_size: int = Field(default=7, ge=1, le=20)
    max_offspring_per_round: int = Field(default=5, ge=0, le=12)
    max_crossover_children: int = Field(default=2, ge=0, le=4)
    bridge_policy: Literal["allow_abstract_with_upgrade", "full_text_only", "exclude_bridge"] = "allow_abstract_with_upgrade"
    ui_verbosity: Literal["concise", "normal", "debug"] = "normal"
    route_quotas: dict[str, int] = Field(default_factory=dict)
    target_profile: TargetProfile = Field(default_factory=TargetProfile)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    raw_user_input: str = ""

    @model_validator(mode="after")
    def mode_round_alignment(self) -> "T4RunConfig":
        expected = {"quick": {0}, "standard": {1, 2}, "deep": {2, 3}}
        if self.mode in expected and self.rounds not in expected[self.mode]:
            allowed = ", ".join(str(value) for value in sorted(expected[self.mode]))
            raise ValueError(f"{self.mode} mode requires rounds in {{{allowed}}}")
        # Existing workspaces persisted Standard=1 / Deep=2 before the default
        # was expanded. They remain resumable with their explicitly confirmed
        # exploration budget; only newly created configs take the P0->P2/P3
        # defaults above.
        if self.max_crossover_children > self.max_offspring_per_round:
            raise ValueError("max_crossover_children cannot exceed max_offspring_per_round")
        # ``max_initial_population`` bounds P0 generation work.  The active
        # population is a survival *target* for later generations and may be
        # larger after mutation/crossover.  Treating this relationship as a
        # validity invariant made perfectly safe small smoke runs (P0=1,
        # target=2, one mutation child) unreadable on resume, which in turn
        # sent a confirmed native T4 run down the pre-run/legacy path.
        # Availability is handled by survival selection: it keeps the usable
        # candidates it has and records an underfilled-population warning.
        if not self.allow_crossover and self.max_crossover_children:
            raise ValueError("max_crossover_children must be zero when crossover is disabled")
        return self


class T4InternalState(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    semantics: Literal["t4_internal_state"] = "t4_internal_state"
    phase: EvolutionPhase
    generation: int = Field(default=0, ge=0)
    configured_rounds: int = Field(default=2, ge=0)
    completed_rounds: int = Field(default=0, ge=0)
    current_population_id: str = ""
    display_candidate_ids: list[str] = Field(default_factory=list)
    pending_directive_id: str | None = None
    input_fingerprint: str = Field(min_length=16)
    input_fingerprints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    run_config_fingerprint: str = Field(min_length=16)
    last_completed_artifact: str = ""
    generation_history: list[str] = Field(default_factory=list)
    archived_population_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def state_integrity(self) -> "T4InternalState":
        if self.completed_rounds > self.configured_rounds and self.configured_rounds >= 0:
            raise ValueError("completed_rounds cannot exceed configured_rounds")
        if self.current_population_id and self.current_population_id not in self.generation_history:
            raise ValueError("current_population_id must be present in generation_history")
        return self


class IdeaDirective(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    directive_id: str
    action: Literal[
        "select_candidate",
        "select_multiple",
        "keep_parallel",
        "compose_from_components",
        "continue_evolution",
        "focus_candidate",
        "merge_candidates",
        "refine_candidate",
        "show_more",
        "show_archive",
        "inspect_score",
        "inspect_evidence",
        "inspect_lineage",
        "inspect_hypotheses",
        "inspect_contributions",
        "inspect_genome",
        "inspect_files",
        "compare_candidates",
        "regenerate_route",
        "change_target_profile",
        "rollback",
        "pause",
        "cancel",
    ]
    target_candidate_ids: list[str] = Field(default_factory=list)
    target_family_ids: list[str] = Field(default_factory=list)
    component_refs: list[str] = Field(default_factory=list)
    preserve_genes: list[str] = Field(default_factory=list)
    donor_genes: dict[str, str] = Field(default_factory=dict)
    requested_route: str = ""
    requested_rounds: int | None = Field(default=None, ge=0, le=3)
    constraints: list[str] = Field(default_factory=list)
    raw_user_input: str = Field(min_length=1)
    confirmation_required: bool = False

    _id = field_validator("directive_id")(_validate_identifier)


class IdeaGateViewModel(_StrictModel):
    """Renderer input only; it contains no Rich markup or hidden model trace."""

    generation: int = Field(ge=0)
    population_id: str
    input_fingerprint: str = Field(min_length=16)
    summary: str = Field(min_length=1)
    candidate_ids: list[str] = Field(default_factory=list)
    recommendation: dict[str, str] = Field(default_factory=dict)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    action_guidance: list[dict[str, str]] = Field(default_factory=list)
    artifact_paths: list[dict[str, str]] = Field(default_factory=list)

    _id = field_validator("population_id")(_validate_identifier)
