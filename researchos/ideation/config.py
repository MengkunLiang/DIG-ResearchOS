"""Load versioned, system-maintained defaults for the T4 evolution controller."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

from ..runtime.system_config import system_config_path


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RouteQuota(_ConfigModel):
    """Exploration budget for one LLM perspective, not a Candidate obligation."""
    route: str = Field(min_length=1)
    minimum: int = Field(ge=0, le=20)
    maximum: int = Field(ge=0, le=20)
    required: bool = False
    may_be_unsupported: bool = False

    @model_validator(mode="after")
    def quota_order(self) -> "RouteQuota":
        if self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return self


class PopulationDefaults(_ConfigModel):
    # This is a scheduling ceiling, not a minimum viable scientific
    # population. A constrained workspace may safely continue with one
    # evidence-bounded Candidate and record the diversity limitation.
    max_initial_population: int = Field(default=14, ge=1, le=30)
    active_population_target: int = Field(default=7, ge=1, le=20)
    active_population_minimum: int = Field(default=6, ge=1, le=20)
    active_population_maximum: int = Field(default=8, ge=1, le=20)
    portfolio_minimum: int = Field(default=1, ge=1, le=3)
    portfolio_maximum: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def population_bounds(self) -> "PopulationDefaults":
        if not self.active_population_minimum <= self.active_population_target <= self.active_population_maximum:
            raise ValueError("active population target must be inside configured bounds")
        # P0 is only the initial generation budget.  A later active
        # population can legitimately be larger after offspring are admitted;
        # conversely it may be smaller when routes are unsupported.  These
        # values guide scheduling and must not make a safe recovery config
        # unparsable.
        return self


class OffspringDefaults(_ConfigModel):
    mutation_minimum: int = Field(default=2, ge=0, le=12)
    mutation_maximum: int = Field(default=4, ge=0, le=12)
    crossover_minimum: int = Field(default=0, ge=0, le=4)
    crossover_maximum: int = Field(default=2, ge=0, le=4)
    max_total: int = Field(default=5, ge=0, le=12)

    @model_validator(mode="after")
    def child_bounds(self) -> "OffspringDefaults":
        if self.mutation_maximum < self.mutation_minimum or self.crossover_maximum < self.crossover_minimum:
            raise ValueError("offspring maximum must not be below minimum")
        if self.mutation_maximum + self.crossover_maximum > self.max_total:
            raise ValueError("maximum offspring exceeds max_total")
        return self


class T4EvolutionSettings(_ConfigModel):
    schema_version: str = "1.0.0"
    route_quotas: list[RouteQuota]
    population: PopulationDefaults = Field(default_factory=PopulationDefaults)
    offspring: OffspringDefaults = Field(default_factory=OffspringDefaults)
    opportunity_minimum: int = Field(default=3, ge=1, le=10)
    opportunity_maximum: int = Field(default=6, ge=1, le=12)
    maximum_rounds: int = Field(default=3, ge=1, le=5)
    # Token similarity only recalls possible sibling Families; it cannot
    # decide that two causal programmes are duplicates.
    family_similarity_threshold: float = Field(default=0.45, ge=0, le=1)
    complexity_growth_ratio_limit: float = Field(default=1.8, ge=1, le=5)
    one_repair_attempt_per_route: bool = True
    # Crossover reviewers return a compact but nested schema. Keep several
    # repair attempts available for semantic field-shape mistakes; this is
    # separate from provider/network retries and never auto-approves a Child.
    crossover_structured_repair_attempts: int = Field(default=3, ge=1, le=8)
    route_max_concurrency: int = Field(default=2, ge=1, le=4)
    # Score reports are evidence-dense. Small sequential batches keep a long
    # population from truncating its later candidates at the provider boundary.
    scoring_batch_size: int = Field(default=3, ge=1, le=6)
    bridge_policy_default: str = "allow_abstract_with_upgrade"
    scoring_rubric_path: str = "config/system_config/idea_scoring_rubric.yaml"
    evidence_permissions_path: str = "config/system_config/idea_evidence_permissions.yaml"
    evolution_operators_path: str = "config/system_config/idea_evolution_operators.yaml"

    @model_validator(mode="after")
    def expected_routes_present(self) -> "T4EvolutionSettings":
        routes = {item.route for item in self.route_quotas}
        required = {"evidence_routed_literature", "informed_brainstorm"}
        missing = required - routes
        if missing:
            raise ValueError("required routes missing: " + ", ".join(sorted(missing)))
        if self.opportunity_maximum < self.opportunity_minimum:
            raise ValueError("opportunity_maximum must not be below opportunity_minimum")
        return self


def load_t4_evolution_settings(path: Path | None = None) -> T4EvolutionSettings:
    """Load strict system defaults without accepting project-specific content."""

    config_path = path or system_config_path("t4_evolution.yaml")
    raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError(f"T4 evolution config must be a mapping: {config_path}")
    return T4EvolutionSettings.model_validate(raw)
