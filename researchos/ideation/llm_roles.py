"""LLM-backed, role-separated adapters for the T4 evolution controller.

Each adapter accepts and returns typed artifacts only. The shared invoker keeps
provider configuration in the existing `LLMClient`; role prompts never receive
another role's private scoring or planning state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel

from ..pydantic_compat import model_dump, model_validate
from ..runtime.llm_client import LLMClient
from ..runtime.prompts import get_prompt_env
from .evolution_controller import IdeaEvolverPort, IdeaGeneratorPort, IdeaScoringPort
from .models import CandidateDossier, CrossoverCompatibilityDecision, EvolutionPlan, OpportunityQuery, RouteGenerationResult, ScoreReport, T4RunConfig


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_JsonCall = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class T4RoleCallConfig:
    tier: str
    profile: str | None = None
    model_override: str | None = None
    endpoint_override: str | None = None
    max_context_override: int | None = None
    timeout: int = 120
    max_retries_per_model: int = 2
    retry_base_delay: float = 2.0


class LLMJsonRoleInvoker:
    """Call the configured LLM and return a validated JSON object."""

    def __init__(self, client: LLMClient | None = None, config: T4RoleCallConfig | None = None, *, call: _JsonCall | None = None) -> None:
        if client is None and call is None:
            raise ValueError("LLMJsonRoleInvoker requires an LLMClient or a test call")
        self.client = client
        self.config = config or T4RoleCallConfig(tier="standard")
        self._call = call

    async def invoke(self, *, prompt_name: str, system_contract: str, payload: dict[str, Any]) -> dict[str, Any]:
        user = get_prompt_env().get_template(prompt_name).render(payload_json=json.dumps(payload, ensure_ascii=False, indent=2))
        if self._call is not None:
            content = await self._call(system_contract, user)
        else:
            assert self.client is not None
            response = await self.client.chat(
                messages=[{"role": "system", "content": system_contract}, {"role": "user", "content": user}],
                tools=None,
                temperature=0.2,
                tier=self.config.tier,
                profile=self.config.profile,
                model_override=self.config.model_override,
                endpoint_override=self.config.endpoint_override,
                max_context_override=self.config.max_context_override,
                timeout=self.config.timeout,
                max_retries_per_model=self.config.max_retries_per_model,
                retry_base_delay=self.config.retry_base_delay,
            )
            content = str(getattr(response.raw.choices[0].message, "content", "") or "")
        return _parse_json_object(content)


class LLMIdeaGenerator(IdeaGeneratorPort):
    """Opportunity and route Seed generator. It has no scoring APIs."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def plan_opportunities(self, *, evidence_summary: dict[str, Any], run_config: T4RunConfig) -> list[OpportunityQuery]:
        payload = {"prompt_version": "1.0.0", "evidence_summary": evidence_summary, "run_config": model_dump(run_config, mode="json")}
        data = await self.invoker.invoke(
            prompt_name="idea_opportunity_planner.j2",
            system_contract=(
                "You are IdeaGeneratorAgent in opportunity-planning mode. Return only JSON with an `opportunities` array. "
                "Generate 3-6 evidence-linked research opportunities. Do not score, rank, select, or delete ideas. "
                "Do not turn synthesis inference or abstract-only material into an established fact."
            ),
            payload=payload,
        )
        raw = data.get("opportunities") if isinstance(data.get("opportunities"), list) else []
        return [model_validate(OpportunityQuery, item) for item in raw if isinstance(item, dict)]

    async def generate_route(
        self,
        *,
        route: str,
        opportunities: list[OpportunityQuery],
        evidence_bundle: dict[str, Any],
        quota: int,
        repair: bool,
    ) -> list[CandidateDossier] | RouteGenerationResult:
        payload = {
            "prompt_version": "1.0.0",
            "route": route,
            "quota": quota,
            "repair": repair,
            "opportunities": [model_dump(item, mode="json") for item in opportunities],
            "evidence_bundle": evidence_bundle,
        }
        data = await self.invoker.invoke(
            prompt_name="idea_generator.j2",
            system_contract=(
                "You are IdeaGeneratorAgent. Generate candidates only for the assigned Route and return only JSON. "
                "You do not score, rank, select, or delete candidates. Preserve evidence provenance and permissions. "
                "Abstract-only evidence may inspire a candidate or upgrade requirement, never an established mechanism or final claim. "
                "Every CandidateDossier must include its presentation object: title, display_title, basis_summary, practical_implication, "
                "counterfactual, complete gate1_card, basis_sources, idea_origin, constraint_status, and mechanism_family. "
                "If the route cannot be supported, return status=unsupported with a concrete reason instead of fabricating a candidate."
            ),
            payload=payload,
        )
        if str(data.get("status") or "").lower() == "unsupported":
            return model_validate(RouteGenerationResult, {"route": route, **data})
        raw = data.get("candidates") if isinstance(data.get("candidates"), list) else []
        candidates = [model_validate(CandidateDossier, item) for item in raw if isinstance(item, dict)]
        for candidate in candidates:
            _require_gate1_candidate_presentation(candidate)
        return candidates


class LLMIdeaScorer(IdeaScoringPort):
    """Independent scorer. Its payload omits route, lineage, and ranking fields."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def score_population(self, *, candidates: list[CandidateDossier], scoring_batch_id: str, blind: bool) -> list[ScoreReport]:
        payload = {
            "prompt_version": "1.0.0",
            "rubric_version": "1.0.0",
            "scoring_batch_id": scoring_batch_id,
            "blind": blind,
            "candidates": [_blind_candidate(candidate) for candidate in candidates],
        }
        data = await self.invoker.invoke(
            prompt_name="idea_scorer.j2",
            system_contract=(
                "You are IdeaScoringAgent. Return only JSON with a `scores` array. You do not generate, rewrite, rank, "
                "or delete candidates. Route, parent/child identity, creation time, and generator self-assessment are hidden. "
                "Use the fixed five-dimension rubric. Contribution distinctiveness is internal readiness, not proof of external novelty. "
                "Evidence calibration measures honest permission use, not evidence volume. Also provide compatibility_scores and "
                "compatibility_rationales for novelty, feasibility, impact, evaluability, differentiation, cost, and contribution_strength."
            ),
            payload=payload,
        )
        raw = data.get("scores") if isinstance(data.get("scores"), list) else []
        scores = [model_validate(ScoreReport, item) for item in raw if isinstance(item, dict)]
        for score in scores:
            _require_gate1_compatibility_scores(score)
        return scores

    async def review_crossover_pairs(self, *, candidates: list[CandidateDossier], pairs: list[tuple[str, str]]) -> list[CrossoverCompatibilityDecision]:
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        payload = {
            "prompt_version": "1.0.0",
            "pairs": [
                {"pair_id": f"{left}__{right}", "parents": [_blind_candidate(by_id[left]), _blind_candidate(by_id[right])]}
                for left, right in pairs if left in by_id and right in by_id
            ],
        }
        data = await self.invoker.invoke(
            prompt_name="idea_crossover_reviewer.j2",
            system_contract=(
                "You are IdeaScoringAgent in crossover-review mode. Return only JSON with `decisions`. "
                "Do not generate a Child. Approve only when one coherent problem, mechanism chain, and Gene Donor Map exist. "
                "Reject module stacking, assumption conflict, evidence elevation, and unacceptable complexity."
            ),
            payload=payload,
        )
        raw = data.get("decisions") if isinstance(data.get("decisions"), list) else []
        return [model_validate(CrossoverCompatibilityDecision, item) for item in raw if isinstance(item, dict)]


class LLMIdeaEvolver(IdeaEvolverPort):
    """Plan-bound Child generator. It has no score or selection method."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def generate_offspring(self, *, plans: list[EvolutionPlan], parents: list[CandidateDossier]) -> list[CandidateDossier]:
        by_id = {candidate.candidate_id: candidate for candidate in parents}
        payload = {
            "prompt_version": "1.0.0",
            "plans": [model_dump(plan, mode="json") for plan in plans],
            "parents": [_evolver_parent(candidate) for candidate in parents],
        }
        data = await self.invoker.invoke(
            prompt_name="idea_evolver.j2",
            system_contract=(
                "You are IdeaEvolverAgent. Return only JSON with a `children` array. Create one Child only from each explicit Evolution Plan. "
                "Do not select parents, change a plan, score results, or overwrite a Parent. Preserve named genes, respect the Gene Donor Map, "
                "and never elevate abstract-only evidence."
            ),
            payload=payload,
        )
        raw = data.get("children") if isinstance(data.get("children"), list) else []
        children = [model_validate(CandidateDossier, item) for item in raw if isinstance(item, dict)]
        approved_parent_sets = {tuple(plan.parent_ids) for plan in plans}
        for child in children:
            _require_gate1_candidate_presentation(child)
            if tuple(child.lineage.parent_ids) not in approved_parent_sets:
                raise ValueError(f"LLM Evolver returned child {child.candidate_id} without an approved parent set")
            if child.candidate_id in by_id:
                raise ValueError("LLM Evolver attempted to overwrite a parent candidate")
        return children


def _blind_candidate(candidate: CandidateDossier) -> dict[str, Any]:
    """Remove signals that bias independent scoring while retaining candidate identity."""

    return {
        "candidate_id": candidate.candidate_id,
        "genome": model_dump(candidate.genome, mode="json", exclude={"route", "parents", "generation_created"}),
        "contributions": [model_dump(item, mode="json") for item in candidate.contributions],
        "hypotheses": [model_dump(item, mode="json") for item in candidate.hypotheses],
        "evidence_composition": candidate.evidence_composition,
        "warnings": candidate.warnings,
    }


def _evolver_parent(candidate: CandidateDossier) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "genome": model_dump(candidate.genome, mode="json"),
        "contributions": [model_dump(item, mode="json") for item in candidate.contributions],
        "hypotheses": [model_dump(item, mode="json") for item in candidate.hypotheses],
        "lineage": model_dump(candidate.lineage, mode="json"),
    }


_GATE1_COMPATIBILITY_KEYS = (
    "novelty",
    "feasibility",
    "impact",
    "evaluability",
    "differentiation",
    "cost",
    "contribution_strength",
)


def _require_gate1_candidate_presentation(candidate: CandidateDossier) -> None:
    presentation = candidate.presentation
    if presentation is None:
        raise ValueError(f"T4 Generator returned candidate {candidate.candidate_id} without the required Gate1 presentation")
    if not 2 <= len(candidate.hypotheses) <= 3:
        raise ValueError(f"T4 Generator returned candidate {candidate.candidate_id} without 2-3 provisional hypotheses")
    minimum_sources = 2 if presentation.constraint_status in {"mainline", "bridge"} else 1
    if len(presentation.basis_sources) < minimum_sources:
        raise ValueError(
            f"T4 Generator returned candidate {candidate.candidate_id} without enough LLM-authored basis sources"
        )


def _require_gate1_compatibility_scores(score: ScoreReport) -> None:
    missing = [
        key
        for key in _GATE1_COMPATIBILITY_KEYS
        if key not in score.compatibility_scores or key not in score.compatibility_rationales
    ]
    if missing:
        raise ValueError(
            f"T4 Scoring Agent returned score {score.candidate_id} without Gate1 compatibility fields: {', '.join(missing)}"
        )
    invalid = [
        key
        for key in _GATE1_COMPATIBILITY_KEYS
        if not isinstance(score.compatibility_scores[key], int) or not 1 <= score.compatibility_scores[key] <= 5
    ]
    if invalid:
        raise ValueError(
            f"T4 Scoring Agent returned invalid Gate1 compatibility scores for {score.candidate_id}: {', '.join(invalid)}"
        )
    weak = [
        key
        for key in _GATE1_COMPATIBILITY_KEYS
        if len(" ".join(str(score.compatibility_rationales[key]).split())) < 18
    ]
    if weak:
        raise ValueError(
            f"T4 Scoring Agent returned incomplete Gate1 compatibility rationales for {score.candidate_id}: {', '.join(weak)}"
        )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("T4 role response must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("T4 role response must be a JSON object")
    return value
