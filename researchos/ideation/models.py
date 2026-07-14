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
    priority_components: dict[str, float] = Field(default_factory=dict)
    priority_score: float | None = Field(default=None, ge=0, le=5)

    _id = field_validator("opportunity_id")(_validate_identifier)


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
    evidence_status: str = "proposed_not_verified"

    _id = field_validator("hypothesis_id")(_validate_identifier)


class Contribution(_StrictModel):
    contribution_id: str
    statement: str = Field(min_length=1)
    contribution_type: Literal["invention", "improvement", "exaptation", "measurement", "mechanism", "theory", "design"]
    what_changes_if_true: str = Field(min_length=1)

    _id = field_validator("contribution_id")(_validate_identifier)


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


class ScoreDimensions(_StrictModel):
    research_value: float = Field(ge=1, le=5)
    mechanism_integrity: float = Field(ge=1, le=5)
    contribution_distinctiveness: float = Field(ge=1, le=5)
    evidence_calibration: float = Field(ge=1, le=5)
    validation_tractability: float = Field(ge=1, le=5)


class ScoreReport(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    scoring_batch_id: str
    rubric_version: str = SCHEMA_VERSION
    blind: bool = True
    scores: ScoreDimensions
    overall_readiness: float = Field(ge=1, le=5)
    score_uncertainty: float = Field(ge=0, le=2)
    rationales: dict[str, str] = Field(default_factory=dict)
    dominant_strength: str = Field(min_length=1)
    dominant_bottleneck: str = Field(min_length=1)
    preserve_genes: list[str] = Field(default_factory=list)
    modify_genes: list[str] = Field(default_factory=list)
    recommended_operators: list[EvolutionOperator] = Field(default_factory=list)
    high_upside: bool = False
    uncertain: bool = False

    _id = field_validator("candidate_id", "scoring_batch_id")(_validate_identifier)

    @model_validator(mode="after")
    def score_integrity(self) -> "ScoreReport":
        required = {
            "research_value",
            "mechanism_integrity",
            "contribution_distinctiveness",
            "evidence_calibration",
            "validation_tractability",
        }
        missing = required - set(self.rationales)
        if missing:
            raise ValueError("score rationales missing: " + ", ".join(sorted(missing)))
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


class CandidateLineage(_StrictModel):
    candidate_id: str
    parent_ids: list[str] = Field(default_factory=list)
    route: str = Field(min_length=1)
    created_by: Literal["generator", "evolver", "human_composition", "legacy_migration"]
    evolution_plan_id: str = ""
    gene_delta_path: str = ""
    complexity_inflation: Literal["low", "medium", "high", "unknown"] = "unknown"

    _id = field_validator("candidate_id")(_validate_identifier)


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

    _id = field_validator("candidate_id")(_validate_identifier)

    @model_validator(mode="after")
    def dossier_integrity(self) -> "CandidateDossier":
        if self.candidate_id != self.genome.candidate_id or self.candidate_id != self.lineage.candidate_id:
            raise ValueError("dossier, genome, and lineage candidate_id must match")
        if len({item.contribution_id for item in self.contributions}) != len(self.contributions):
            raise ValueError("contribution IDs must be unique")
        if len({item.hypothesis_id for item in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("hypothesis IDs must be unique")
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


class T4RunConfig(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    mode: Literal["quick", "standard", "deep", "auto"] = "standard"
    rounds: int = Field(default=1, ge=0, le=3)
    allow_crossover: bool = True
    final_top_k: int = Field(default=3, ge=1, le=3)
    max_initial_population: int = Field(default=14, ge=6, le=30)
    active_population_size: int = Field(default=7, ge=1, le=20)
    max_offspring_per_round: int = Field(default=5, ge=0, le=12)
    max_crossover_children: int = Field(default=2, ge=0, le=4)
    bridge_policy: Literal["allow_abstract_with_upgrade", "full_text_only", "exclude_bridge"] = "allow_abstract_with_upgrade"
    ui_verbosity: Literal["concise", "normal", "debug"] = "normal"
    route_quotas: dict[str, int] = Field(default_factory=dict)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    raw_user_input: str = ""

    @model_validator(mode="after")
    def mode_round_alignment(self) -> "T4RunConfig":
        expected = {"quick": 0, "standard": 1, "deep": 2}
        if self.mode in expected and self.rounds != expected[self.mode]:
            raise ValueError(f"{self.mode} mode requires rounds={expected[self.mode]}")
        if self.max_crossover_children > self.max_offspring_per_round:
            raise ValueError("max_crossover_children cannot exceed max_offspring_per_round")
        if self.active_population_size > self.max_initial_population:
            raise ValueError("active_population_size cannot exceed max_initial_population")
        if not self.allow_crossover and self.max_crossover_children:
            raise ValueError("max_crossover_children must be zero when crossover is disabled")
        return self


class T4InternalState(_StrictModel):
    schema_version: str = SCHEMA_VERSION
    semantics: Literal["t4_internal_state"] = "t4_internal_state"
    phase: EvolutionPhase
    generation: int = Field(default=0, ge=0)
    configured_rounds: int = Field(default=1, ge=0)
    completed_rounds: int = Field(default=0, ge=0)
    current_population_id: str = ""
    display_candidate_ids: list[str] = Field(default_factory=list)
    pending_directive_id: str | None = None
    input_fingerprint: str = Field(min_length=16)
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
        "regenerate_route",
        "rollback",
        "pause",
        "cancel",
    ]
    target_candidate_ids: list[str] = Field(default_factory=list)
    target_family_ids: list[str] = Field(default_factory=list)
    component_refs: list[str] = Field(default_factory=list)
    preserve_genes: list[str] = Field(default_factory=list)
    donor_genes: dict[str, str] = Field(default_factory=dict)
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
