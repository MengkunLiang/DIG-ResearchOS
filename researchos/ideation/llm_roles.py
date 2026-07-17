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
from .evolution_controller import IdeaEnricherPort, IdeaEvolverPort, IdeaGeneratorPort, IdeaScoringPort, RouteGenerationPayload
from .errors import T4RoleResponseFormatError
from .final_card_diagnostics import (
    FinalCardCompilationFailure,
    FinalCardFailureDiagnostic,
    classify_final_card_exception,
)
from .models import (
    BridgeCoverageEntry,
    CandidateDossier,
    CandidateMaturity,
    CandidatePresentation,
    CandidateStatus,
    CrossoverCompatibilityDecision,
    EvidenceRole,
    EvolutionPlan,
    EvolutionPlanDeferral,
    FinalIdeaCardTranslation,
    GeneProvenance,
    HumanCompositionCompatibility,
    IdeaGene,
    IdeaGenome,
    OpportunityQuery,
    ReadingLevel,
    RouteGenerationResult,
    SourceRef,
    ScoreReport,
    T4RunConfig,
    TargetProfile,
    normalize_crossover_decision,
)
from .prompt_composer import compose_t4_role_prompt
from .target_profile import derive_overall_score


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_JsonCall = Callable[[str, str], Awaitable[str]]
_ROLE_ARRAY_FIELDS = {
    "idea_opportunity_planner.j2": "opportunities",
    "idea_opportunity_semantic_repair.j2": "opportunities",
    "idea_generator.j2": "seeds",
    "idea_route_semantic_repair.j2": "seeds",
    "idea_candidate_enricher.j2": None,
    "idea_interaction_reviewer.j2": "reviews",
    "idea_scorer.j2": "scores",
    "idea_score_semantic_repair.j2": "scores",
    "idea_score_rationale_repair.j2": "repairs",
    "idea_evolver.j2": "children",
    "idea_offspring_semantic_repair.j2": "children",
    "idea_crossover_reviewer.j2": "decisions",
    "idea_final_card_compiler.j2": "cards",
    "idea_final_card_semantic_repair.j2": "cards",
}


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
    target_profile: TargetProfile | None = None


class LLMJsonRoleInvoker:
    """Call the configured LLM and return a validated JSON object."""

    def __init__(self, client: LLMClient | None = None, config: T4RoleCallConfig | None = None, *, call: _JsonCall | None = None) -> None:
        if client is None and call is None:
            raise ValueError("LLMJsonRoleInvoker requires an LLMClient or a test call")
        self.client = client
        self.config = config or T4RoleCallConfig(tier="standard")
        self._call = call

    async def invoke(self, *, prompt_name: str, system_contract: str, payload: dict[str, Any]) -> dict[str, Any]:
        rendered_task = get_prompt_env().get_template(prompt_name).render(payload_json=json.dumps(payload, ensure_ascii=False, indent=2))
        system_contract, user = compose_t4_role_prompt(
            prompt_name=prompt_name,
            role_contract=system_contract,
            rendered_task=rendered_task,
            payload=payload,
            target_profile=self.config.target_profile,
        )
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
        return _parse_json_object(
            content,
            array_field=_ROLE_ARRAY_FIELDS.get(prompt_name),
        )


class LLMIdeaGenerator(IdeaGeneratorPort):
    """Opportunity and route Seed generator. It has no scoring APIs."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def plan_opportunities(self, *, evidence_summary: dict[str, Any], run_config: T4RunConfig) -> list[OpportunityQuery]:
        payload = {
            "prompt_version": "1.2.0",
            "evidence_summary": evidence_summary,
            "run_config": model_dump(run_config, mode="json", exclude={"target_profile"}),
        }
        data = await self.invoker.invoke(
            prompt_name="idea_opportunity_planner.j2",
            system_contract=(
                "You are IdeaGeneratorAgent in opportunity-planning mode. Return only JSON with an `opportunities` array. "
                "Use workspace material to ground and calibrate opportunities, while using general scholarly knowledge, counterfactual reasoning, "
                "and structural cross-domain analogy to discover non-obvious research opportunities. Do not score, rank, select, or delete ideas. "
                "Mark parametric-knowledge or analogy-driven opportunities as verification_required and never turn synthesis inference or abstract-only "
                "material into an established fact. "
                "Write researcher-facing opportunity prose in clear Chinese, retaining established academic terms in English where they are more precise."
            ),
            payload=payload,
        )
        try:
            return _parse_opportunity_response(data)
        except (TypeError, ValueError) as exc:
            # Opportunity planning is a semantic task. A response such as
            # `research_questions` rather than `opportunities`, or one that
            # nests the usable material differently, should receive one
            # evidence-bounded normalization attempt before it is discarded.
            normalized = await self._repair_opportunity_semantics(
                evidence_summary=evidence_summary,
                run_config=run_config,
                attempted_response=data,
                validation_error=exc,
            )
            return _parse_opportunity_response(normalized)

    async def _repair_opportunity_semantics(
        self,
        *,
        evidence_summary: dict[str, Any],
        run_config: T4RunConfig,
        attempted_response: dict[str, Any],
        validation_error: Exception,
    ) -> dict[str, Any]:
        """Normalize an Opportunity Map without turning absence into evidence.

        The planner may reformulate an evidence-linked question, but it cannot
        silently turn an unavailable source, a retrieval gap, or an
        abstract-only hint into a factual research finding. The controller
        still owns count, identifier, and downstream route validation.
        """

        return await self.invoker.invoke(
            prompt_name="idea_opportunity_semantic_repair.j2",
            system_contract=(
                "You are T4 SemanticRepairAgent for an Opportunity Map. Normalize one parseable but invalid planner response into "
                "one JSON object with an `opportunities` array. Do not generate Candidates, scores, rankings, selection decisions, "
                "papers, citations, datasets, metrics, results, or external novelty claims. Preserve any supplied evidence_atom_ids and "
                "uncertainty. You may map equivalent field names, reorganize misplaced fields, and write a concise evidence-calibrated "
                "question or explanation only from the attempted response and Evidence Summary. Do not state that an unobserved area is a "
                "factual gap. Return exactly one JSON object and no Markdown."
            ),
            payload={
                "evidence_summary": evidence_summary,
                "run_config": model_dump(run_config, mode="json", exclude={"target_profile"}),
                "attempted_response": attempted_response,
                "validator_error": str(validation_error)[:1600],
            },
        )

    async def generate_route(
        self,
        *,
        route: str,
        opportunities: list[OpportunityQuery],
        evidence_bundle: dict[str, Any],
        quota: int,
        repair: bool,
        recovery: bool = False,
    ) -> list[CandidateDossier] | RouteGenerationResult | RouteGenerationPayload:
        payload = {
            "prompt_version": "1.2.0",
            "route": route,
            "quota": quota,
            "repair": repair,
            "recovery": recovery,
            "opportunities": [model_dump(item, mode="json") for item in opportunities],
            "evidence_bundle": evidence_bundle,
        }
        repair_instruction = (
            " This is one repair attempt after the previous response failed the structured-output contract. "
            "Return exactly one complete replacement JSON object, with no Markdown fence, preamble, explanation, or second object; correct every typed field. "
            "Preserve a supplied BridgeCoverageEntry when it is recoverable, but do not invent an optional Bridge review merely to satisfy the envelope."
            if repair
            else ""
        )
        recovery_instruction = (
            " This is a creative re-divergence pass for an underfilled Route. The requested count is an exploration budget, not a requirement to manufacture filler. "
            "Return only genuinely distinct minimal Idea Seeds; you may return fewer and explain an unsupported route when no further non-redundant idea is defensible. "
            "Use the workspace Opportunity and Evidence Bundle as research context, plus general scholarly knowledge, counterfactual reasoning, and structural analogy to propose a falsifiable mechanism, design, and validation path. "
            "Every non-workspace detail must be phrased as a proposal, use conjecture or inspiration provenance, set upgrade_required=true, and appear in risks or validation needs. "
            "Never invent an external paper, source reference, dataset, metric, result, novelty conclusion, or supported mechanism. Do not reuse a reserved candidate ID."
            if recovery
            else ""
        )
        data = await self.invoker.invoke(
            prompt_name="idea_generator.j2",
            system_contract=(
                "You are IdeaGeneratorAgent. Generate candidates only for the assigned Route and return only JSON. "
                "You do not score, rank, select, or delete candidates. Preserve evidence provenance and permissions. "
                "Workspace material grounds and calibrates the proposal; it does not exhaust the idea space. You may use general scholarly knowledge, "
                "counterfactual imagination, and cross-domain structural analogy in every Route. Mark non-workspace insights as conjectural, set an "
                "upgrade requirement, and never present them as verified evidence, an existing paper, available dataset, measured result, or external novelty fact. "
                "Abstract-only evidence may inspire a candidate or upgrade requirement, never an established mechanism or final claim. "
                "Prefer the minimal IdeaSeed contract for initial formation: problem, thesis, candidate mechanism, one contribution, one falsifiable prediction, one main risk, and route origin. "
                "Also preserve concise scientific exploration where it adds value: conceptual leap, competing explanation, surprising prediction, and research-program potential. "
                "Detailed CandidatePresentation, additional hypotheses, full evidence mapping, implications, and experiment details are enrichment work for scoring, mutation, reading upgrades, or the final card stage. "
                "For cross_domain_bridge, a Bridge review is optional initial-route enrichment. Prefer the strongest concise Seed; "
                "if a review is omitted, the controller records it as unreviewed for a later targeted pass rather than treating the Route as failed. "
                "The user-provided bridge-domain names, motivations, and queries are valid creative context for structural analogy: they may "
                "seed a conjectural Cross-domain Idea even when the workspace has not yet supplied a bridge-specific reading note. Mark the transfer "
                "as conjectural and verification-required; do not invent a paper or claim the named domain establishes the mechanism. "
                "Never return bridge_reviews alone: an optional review cannot substitute for an IdeaSeed. "
                "When `cross_domain_sources` is non-empty, set `cross_domain_relation` to exactly one of mechanism_bridge, method_transfer, "
                "evaluation_or_metric_bridge, baseline_or_dataset_relevance, or adjacent_application; keep any richer explanation in the "
                "candidate's readable presentation fields. "
                "If the normal Route cannot be supported, return status=unsupported with a concrete reason instead of fabricating a candidate. "
                "When a legacy CandidatePresentation is included, write its available fields in clear Chinese, but it is optional enrichment and "
                "must never substitute for Final Card LLM explanations or block a usable Seed. Retain established academic terms such as Evidence Permission, "
                "Idea Family, Mutation Child, and Crossover Child when they improve precision."
                + repair_instruction
                + recovery_instruction
            ),
            payload=payload,
        )
        try:
            return self._parse_route_response(route=route, data=data)
        except (TypeError, ValueError) as exc:
            # A model can express the right research content with an alias,
            # misplaced field, or incomplete envelope. Give an LLM one bounded
            # chance to normalize that content before deterministic safety
            # checks decide whether the Route can be retained.
            normalized = await self._repair_route_semantics(
                route=route,
                quota=quota,
                evidence_bundle=evidence_bundle,
                attempted_response=data,
                validation_error=exc,
            )
            result = self._parse_route_response(route=route, data=normalized)
            if isinstance(result, RouteGenerationPayload):
                return RouteGenerationPayload(
                    result=result.result.model_copy(update={"repaired_once": True}),
                    candidates=result.candidates,
                )
            return result.model_copy(update={"repaired_once": True})

    def _parse_route_response(
        self,
        *,
        route: str,
        data: dict[str, Any],
    ) -> RouteGenerationResult | RouteGenerationPayload:
        if str(data.get("status") or "").lower() == "unsupported":
            return model_validate(RouteGenerationResult, {"route": route, **data})
        raw = data.get("candidates") if isinstance(data.get("candidates"), list) else data.get("seeds")
        if not isinstance(raw, list):
            raw = []
        candidates = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _normalize_candidate_dossier_payload(item)
            if not isinstance(normalized.get("genome"), dict):
                normalized = _coerce_minimal_seed_candidate(normalized, route=route, ordinal=index)
            candidate = model_validate(CandidateDossier, normalized)
            candidates.append(_admit_initial_candidate(candidate))
        route_status = _normalize_route_status(
            data.get("status"),
            candidate_count=len(candidates),
            unsupported_reason=str(data.get("unsupported_reason") or ""),
        )
        route_result = model_validate(
            RouteGenerationResult,
            {
                "route": route,
                "status": route_status,
                "candidate_ids": [item.candidate_id for item in candidates],
                "unsupported_reason": str(data.get("unsupported_reason") or ""),
                "bridge_reviews": _normalize_optional_bridge_reviews(data.get("bridge_reviews")),
            },
        )
        return RouteGenerationPayload(result=route_result, candidates=candidates)

    async def _repair_route_semantics(
        self,
        *,
        route: str,
        quota: int,
        evidence_bundle: dict[str, Any],
        attempted_response: dict[str, Any],
        validation_error: Exception,
    ) -> dict[str, Any]:
        """Ask a dedicated role to normalize one parseable Route response.

        The repair sees no scorer, plan, or Portfolio state. It may reorganize
        existing candidate content and fill a presentation field only from the
        supplied Candidate/Evidence material. It cannot manufacture a source,
        strengthen an Evidence Permission, or make a selection decision. A
        provenance mismatch is repaired by downgrading the affected Gene to a
        clearly proposed conjecture, never by discarding an otherwise usable
        Candidate or inventing a citation.
        """

        return await self.invoker.invoke(
            prompt_name="idea_route_semantic_repair.j2",
            system_contract=(
                "You are T4 SemanticRepairAgent. Normalize one parseable but invalid IdeaGenerator Route response into one valid JSON object. "
                "Do not score, rank, select, merge, archive, or delete Candidates. Preserve every source reference, reading level, "
                "Evidence Permission, Candidate ID, and stated uncertainty when present. You may map semantically equivalent field names "
                "or reorganize misplaced information. Bridge-review field aliases such as active/all/covered may be canonicalized, but an optional "
                "Bridge review must never make an otherwise valid Seed fail. When a presentation sentence is missing, write only a concise proposed explanation "
                "grounded in the supplied Candidate and Evidence Bundle. If a Gene uses anchor/support without a valid source_ref, downgrade that Gene "
                "to conjecture or inspiration and set upgrade_required=true rather than inventing a citation. Never invent a source, a result, a metric, "
                "an external novelty claim, or a stronger evidence status. If a safe Candidate cannot be reconstructed, return an explicit unsupported Route with a concrete reason. "
                "For cross_domain_bridge, return at least one minimal Seed whenever a distinct structural transfer can be proposed from the bridge "
                "domain names, motivations, workspace material, or parametric knowledge; retain conjectural status and an explicit verification need. "
                "Return exactly one JSON object and no Markdown."
            ),
            payload={
                "route": route,
                "quota": quota,
                "evidence_bundle": evidence_bundle,
                "attempted_response": attempted_response,
                "validator_error": str(validation_error)[:1600],
            },
        )


class LLMCandidateEnricher(IdeaEnricherPort):
    """Expand an admitted IdeaSeed without taking over scientific selection.

    The Generator is intentionally allowed to be concise and exploratory. This
    role revisits only a promising canonical Seed and returns a complete
    Candidate proposal when that is useful. It neither scores nor filters the
    Candidate, and a malformed answer is handled as local enrichment failure
    by the controller rather than as a Route or Population failure.
    """

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def enrich_candidate(
        self,
        *,
        candidate: CandidateDossier,
        run_config: T4RunConfig,
        evidence_summary: dict[str, Any],
        repair: bool = False,
    ) -> CandidateDossier:
        payload = {
            "prompt_version": "1.0.0",
            "candidate": model_dump(candidate, mode="json"),
            "target_profile": model_dump(run_config.target_profile, mode="json"),
            "evidence_summary": evidence_summary,
            "repair": repair,
        }
        repair_instruction = ""
        if repair:
            repair_instruction = (
                " This is one bounded structural repair after the prior enrichment could not be parsed. "
                "Return one complete replacement Candidate object with the supplied Candidate ID, route, Parent lineage, "
                "problem reframing, and Core Thesis unchanged. Preserve uncertainty instead of inventing a source or result."
            )
        data = await self.invoker.invoke(
            prompt_name="idea_candidate_enricher.j2",
            system_contract=(
                "You are CandidateEnricherAgent. Return only JSON with one `candidate` object. "
                "You enrich an existing IdeaSeed; you do not score, rank, select, merge, archive, reject, or replace it. "
                "Keep the Candidate ID, route, Parent IDs, problem reframing, Core Thesis, and any existing conceptual leap exactly unchanged. "
                "Develop the proposal's mechanism chain, 2-4 non-duplicative hypotheses, contribution package, competing explanations, "
                "validation logic, boundaries, risks, kill criteria, and researcher-readable explanations where the Seed supports them. "
                "Use the LLM's scholarly knowledge and cross-domain reasoning to improve conceptual depth, but mark ideas that are not grounded "
                "in supplied workspace material as conjectural and verification-required. Never invent papers, citations, datasets, metrics, "
                "empirical results, costs, or external-novelty conclusions. Do not elevate an Evidence Permission or add a source path not already "
                "present in the Candidate. A partial enrichment is acceptable: retain Seed maturity and state unresolved upgrades rather than fabricating detail. "
                "Write researcher-facing prose in clear Chinese."
                + repair_instruction
            ),
            payload=payload,
        )
        raw = data.get("candidate")
        if not isinstance(raw, dict):
            raw = data.get("enriched_candidate")
        if not isinstance(raw, dict):
            raise ValueError("Candidate Enricher must return one candidate object")
        normalized = _normalize_candidate_dossier_payload(raw)
        return model_validate(CandidateDossier, normalized)


class LLMIdeaScorer(IdeaScoringPort):
    """Independent scorer. Its payload omits route, lineage, and ranking fields."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def score_population(self, *, candidates: list[CandidateDossier], scoring_batch_id: str, blind: bool) -> list[ScoreReport]:
        return await self._score_population(
            candidates=candidates,
            scoring_batch_id=scoring_batch_id,
            blind=blind,
        )

    async def repair_population_scores(
        self,
        *,
        candidates: list[CandidateDossier],
        scoring_batch_id: str,
        blind: bool,
        failure_reason: str,
        prior_reports: list[ScoreReport] | None = None,
    ) -> list[ScoreReport]:
        """Repair score prose locally before considering a new score batch.

        Candidate-specific rationale is valuable, but it is not a hard
        population-integrity condition.  A failed rationale repair therefore
        returns the original numerical assessment with a diagnostic warning;
        it never turns a usable Candidate into an unscored global failure.
        """

        repair_targets = _score_rationale_repair_targets(
            reports=prior_reports or [],
            candidates=candidates,
        )
        if repair_targets:
            try:
                return await self._repair_score_rationales(
                    candidates=candidates,
                    prior_reports=prior_reports or [],
                    scoring_batch_id=scoring_batch_id,
                    repair_targets=repair_targets,
                    failure_reason=failure_reason,
                )
            except Exception as exc:
                return _mark_score_reports_degraded(
                    prior_reports or [],
                    f"rationale_repair_unavailable:{type(exc).__name__}",
                )
        if _reports_have_reusable_core_contract(prior_reports or [], candidates=candidates, blind=blind):
            return _mark_score_reports_degraded(
                prior_reports or [],
                "non_core_score_diagnostic_not_repaired",
            )
        return await self._score_population(
            candidates=candidates,
            scoring_batch_id=scoring_batch_id,
            blind=blind,
            repair_reason=failure_reason,
        )

    async def _score_population(
        self,
        *,
        candidates: list[CandidateDossier],
        scoring_batch_id: str,
        blind: bool,
        repair_reason: str = "",
    ) -> list[ScoreReport]:
        payload = {
            "prompt_version": "2.0.0",
            "rubric_version": "2.0.0",
            "scoring_batch_id": scoring_batch_id,
            "blind": blind,
            "candidates": [_blind_candidate(candidate) for candidate in candidates],
        }
        repair_instruction = ""
        if repair_reason:
            payload["repair_reason"] = str(repair_reason)[:1600]
            repair_instruction = (
                " This is one structured-output repair. Return a complete replacement scores array covering every supplied "
                "Candidate exactly once. The prior output failed this contract: "
                f"{str(repair_reason)[:900]}. Correct only the structural defect. Return the three core numeric dimensions; "
                "do not add evidence, validation, readiness, profile, or legacy comparison score gates. Do not return a patch, "
                "a subset, Markdown, or commentary."
            )
        data = await self.invoker.invoke(
            prompt_name="idea_scorer.j2",
            system_contract=(
                "You are IdeaScoringAgent. Return only JSON with a `scores` array. You do not generate, rewrite, rank, "
                "or delete candidates. Route, parent/child identity, creation time, and generator self-assessment are hidden. "
                "Use exactly three formal numeric dimensions: research_value, mechanism_integrity, and contribution_distinctiveness. "
                "Each of those three values must use the closed 1.0-5.0 scale, where 1.0 is lowest and 5.0 is highest; never use a 0-10, "
                "percentage, rank, or other scale. "
                "Contribution distinctiveness is internal-population distinctiveness, not proof of external novelty. Do not score evidence "
                "calibration, validation feasibility, current readiness, Profile Fit, maturity, or venue fit. The runtime derives the overall "
                "ranking summary from the three core dimensions. Evidence, validation, Profile Fit, scientific upside, evolution potential, "
                "and uncertainty are optional qualitative diagnostics only. A high-upside Wildcard remains a human-comparison signal, never a "
                "certification of evidence or selection readiness. Give each supplied core rationale a candidate-specific explanation when possible. "
                "Write all researcher-facing rationales, strengths, bottlenecks, and diagnostic explanations in clear Chinese; retain standard academic "
                "terms in English where they are more precise."
                + repair_instruction
            ),
            payload=payload,
        )
        try:
            reports = _parse_score_response(
                data,
                target_profile=self.invoker.config.target_profile,
                scoring_batch_id=scoring_batch_id,
                blind=blind,
            )
        except (TypeError, ValueError) as exc:
            if not _score_semantic_repair_is_lossless(exc):
                # An out-of-range numerical value is not an alias or nesting
                # difference.  Scaling or clamping it would manufacture a
                # different assessment, so let the controller request one
                # full, independent replacement on the explicit 1.0-5.0
                # contract instead of spending a semantic-repair call.
                raise
            # The independent scorer sometimes uses semantically correct
            # aliases or nests a report under a presentation key. Before a
            # complete re-score, let an isolated repair role canonicalize the
            # returned assessment. It is forbidden from changing a numeric
            # score, Candidate content, or the scoring population.
            normalized = await self._repair_score_semantics(
                candidates=candidates,
                scoring_batch_id=scoring_batch_id,
                blind=blind,
                attempted_response=data,
                validation_error=exc,
            )
            reports = _parse_score_response(
                normalized,
                target_profile=self.invoker.config.target_profile,
                scoring_batch_id=scoring_batch_id,
                blind=blind,
            )
        repair_targets = _score_rationale_repair_targets(reports=reports, candidates=candidates)
        if not repair_targets:
            return reports
        try:
            return await self._repair_score_rationales(
                candidates=candidates,
                prior_reports=reports,
                scoring_batch_id=scoring_batch_id,
                repair_targets=repair_targets,
                failure_reason="core score rationale missing or placeholder",
            )
        except Exception as exc:
            return _mark_score_reports_degraded(reports, f"rationale_repair_unavailable:{type(exc).__name__}")

    async def _repair_score_semantics(
        self,
        *,
        candidates: list[CandidateDossier],
        scoring_batch_id: str,
        blind: bool,
        attempted_response: dict[str, Any],
        validation_error: Exception,
    ) -> dict[str, Any]:
        """Canonicalize a parseable score response without re-evaluating it.

        This is deliberately narrower than a re-score. It can recover aliases
        and misplaced diagnostic fields. The three core numeric dimensions
        must come from the attempted response under their original or an
        unambiguous alias. A missing core dimension remains invalid and falls
        through to the controller's normal independent re-score path. Missing
        rationale, evidence, validation, or profile diagnostics do not.
        """

        return await self.invoker.invoke(
            prompt_name="idea_score_semantic_repair.j2",
            system_contract=(
                "You are T4 SemanticRepairAgent for independent scores. Normalize one parseable but invalid score response into "
                "one JSON object with a `scores` array. Do not score, re-score, rank, select, archive, generate, or rewrite Candidates. "
                "Preserve each original core numeric score, Candidate ID, and blind status whenever present; only map semantically equivalent "
                "field names or nesting. The only formal numeric dimensions are research_value, mechanism_integrity, and "
                "contribution_distinctiveness. Treat old evidence/validation values as legacy diagnostics, never as missing core scores. "
                "The formal scale is 1.0-5.0. An out-of-range number is not losslessly repairable: do not clamp, divide, or translate it; "
                "leave it visible so the independent scorer can issue a fresh assessment. "
                "Do not invent a score, source, metric, result, or stronger evidence status. If an essential core numeric assessment is absent "
                "rather than merely misnamed, return the original shape as far as possible; deterministic validation will request an independent "
                "re-score. Return exactly one JSON object and no Markdown."
            ),
            payload={
                "scoring_batch_id": scoring_batch_id,
                "blind": blind,
                "target_profile": model_dump(self.invoker.config.target_profile, mode="json") if self.invoker.config.target_profile else None,
                "candidates": [_score_repair_candidate(candidate) for candidate in candidates],
                "attempted_response": attempted_response,
                "validator_error": str(validation_error)[:1600],
            },
        )

    async def _repair_score_rationales(
        self,
        *,
        candidates: list[CandidateDossier],
        prior_reports: list[ScoreReport],
        scoring_batch_id: str,
        repair_targets: dict[str, dict[str, object]],
        failure_reason: str,
    ) -> list[ScoreReport]:
        """Repair only missing core-score rationale without rewriting a score.

        This remains an independent Scoring Agent call: it receives no route,
        lineage, or ranking signal and may change only named LLM-authored
        explanations.  It is intentionally unable to repair a Candidate by
        inventing a score, an evidence claim, or a validation result.
        """

        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        payload = {
            "prompt_version": "2.0.0",
            "scoring_batch_id": scoring_batch_id,
            "repair_reason": str(failure_reason)[:1600],
            "repairs_required": [
                {
                    "candidate_id": candidate_id,
                    "core_rationale_keys": target["core_rationale_keys"],
                    "candidate": _score_repair_candidate(candidate_by_id[candidate_id]),
                }
                for candidate_id, target in repair_targets.items()
            ],
        }
        data = await self.invoker.invoke(
            prompt_name="idea_score_rationale_repair.j2",
            system_contract=(
                "You are IdeaScoringAgent in a bounded score-repair mode. Return only one JSON object with a `repairs` array. "
                "Do not score, rank, generate, rewrite, select, or delete Candidates. For each requested Candidate, write only the "
                "named core-score rationales. Each rationale must be a candidate-specific Chinese explanation grounded in supplied Candidate "
                "content. Do not write evidence audits, external novelty claims, validation guarantees, generic praise, placeholder wording, "
                "Markdown, or any field that was not requested."
            ),
            payload=payload,
        )
        raw_repairs = data.get("repairs") if isinstance(data.get("repairs"), list) else []
        by_id: dict[str, dict[str, object]] = {}
        for item in raw_repairs:
            if not isinstance(item, dict):
                raise ValueError("T4 Scoring Agent returned a non-object rationale repair")
            candidate_id = str(item.get("candidate_id") or "")
            if candidate_id in by_id:
                raise ValueError(f"T4 Scoring Agent returned duplicate rationale repair for {candidate_id}")
            by_id[candidate_id] = item
        expected_ids = set(repair_targets)
        if set(by_id) != expected_ids:
            raise ValueError(
                "T4 Scoring Agent rationale repair must cover exactly the requested Candidates; "
                f"missing={sorted(expected_ids - set(by_id))}, extra={sorted(set(by_id) - expected_ids)}"
            )
        repaired: list[ScoreReport] = []
        for report in prior_reports:
            target = repair_targets.get(report.candidate_id)
            if target is None:
                repaired.append(report)
                continue
            patch = by_id[report.candidate_id]
            expected_keys = list(target["core_rationale_keys"])
            raw_rationales = patch.get("rationales")
            if not expected_keys and raw_rationales is None:
                raw_rationales = {}
            if not isinstance(raw_rationales, dict) or set(raw_rationales) != set(expected_keys):
                raise ValueError(f"T4 Scoring Agent returned incomplete rationale repair for {report.candidate_id}")
            rationale_updates = {key: _validated_repaired_rationale(raw_rationales[key], report.candidate_id, key) for key in expected_keys}
            repaired.append(
                report.model_copy(
                    update={
                        "rationales": {**report.rationales, **rationale_updates},
                        "rationale_missing": [key for key in report.rationale_missing if key not in rationale_updates],
                        "diagnostic_warnings": [
                            warning
                            for warning in report.diagnostic_warnings
                            if warning not in {f"rationale_missing:{key}" for key in rationale_updates}
                        ],
                    }
                )
            )
        return repaired

    async def review_interaction_pairs(
        self,
        *,
        candidates: list[CandidateDossier],
        shortlist: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Explain a bounded graph shortlist without scoring or merging it.

        The controller owns the deterministic node/edge identities. This role
        can only attach a semantic interpretation to those identities. Bad or
        partial reviewer rows are deliberately returned as an empty/partial
        list so the controller can retain the graph's deterministic fallback.
        """

        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        allowed = {
            (str(item.get("source_id") or ""), str(item.get("target_id") or ""), str(item.get("relation_hint") or ""))
            for item in shortlist
            if isinstance(item, dict)
        }
        pairs: list[dict[str, Any]] = []
        for item in shortlist:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "")
            target_id = str(item.get("target_id") or "")
            if source_id not in by_id or target_id not in by_id:
                continue
            pairs.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "relation_hint": str(item.get("relation_hint") or "parallel"),
                    "deterministic_similarity": item.get("deterministic_similarity") or {},
                    "source_candidate": _interaction_candidate(by_id[source_id]),
                    "target_candidate": _interaction_candidate(by_id[target_id]),
                }
            )
        if not pairs:
            return []
        data = await self.invoker.invoke(
            prompt_name="idea_interaction_reviewer.j2",
            system_contract=(
                "You are InteractionReviewerAgent. Return only JSON with a `reviews` array. "
                "Interpret only the supplied deterministic Candidate-pair shortlist. Do not score, rank, select, delete, merge, "
                "or rewrite any Candidate. For each review, explain shared core, meaningful difference, peer challenge, transferable "
                "element, differentiation need, and crossover potential/risk. A `parallel` relationship is fully valid. Do not treat lexical "
                "similarity as evidence or claim that a crossover must occur. Preserve uncertainty and write researcher-facing explanations in Chinese."
            ),
            payload={"prompt_version": "1.0.0", "pairs": pairs},
        )
        raw = data.get("reviews") if isinstance(data.get("reviews"), list) else []
        reviews: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "").strip()
            target_id = str(item.get("target_id") or "").strip()
            relation_hint = str(item.get("relation_hint") or item.get("requested_relation") or "").strip()
            if (source_id, target_id, relation_hint) not in allowed:
                continue
            normalized = _normalize_interaction_review(item)
            if normalized is not None:
                reviews.append(normalized)
        return reviews

    async def review_crossover_pairs(self, *, candidates: list[CandidateDossier], pairs: list[tuple[str, str]]) -> list[CrossoverCompatibilityDecision]:
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        pair_by_id = {f"{left}__{right}": {left, right} for left, right in pairs if left in by_id and right in by_id}
        payload = {
            "prompt_version": "1.1.0",
            "pairs": [
                {"pair_id": f"{left}__{right}", "parents": [_blind_candidate(by_id[left]), _blind_candidate(by_id[right])]}
                for left, right in pairs if left in by_id and right in by_id
            ],
        }
        data = await self._invoke_crossover_review(payload=payload, repair_reason="")
        try:
            return _parse_crossover_decisions(data, pair_by_id)
        except ValueError as exc:
            # Compatibility review is scorer-only. One repair can correct a
            # field-direction/schema mistake but never authorizes a Child.
            payload["repair_reason"] = str(exc)[:1600]
            data = await self._invoke_crossover_review(payload=payload, repair_reason=str(exc))
            return _parse_crossover_decisions(data, pair_by_id)

    async def _invoke_crossover_review(self, *, payload: dict[str, Any], repair_reason: str) -> dict[str, Any]:
        repair_instruction = ""
        if repair_reason:
            repair_instruction = (
                " This is one structured-output repair. Return a complete replacement decisions array. The previous output failed because: "
                f"{repair_reason[:900]}. For every approved decision, `proposed_gene_donor_map.donors` must map canonical genome gene names "
                "such as `problem`, `mechanism`, or `validation_logic` to one of that pair's exact parent IDs. Do not reverse this mapping, "
                "use prose as a donor ID, or create a Child."
            )
        return await self.invoker.invoke(
            prompt_name="idea_crossover_reviewer.j2",
            system_contract=(
                "You are IdeaScoringAgent in crossover-review mode. Return only JSON with `decisions`. "
                "Do not generate a Child. Approve only when one coherent problem, mechanism chain, and Gene Donor Map exist. "
                "Use `parallel` when both Parents should remain independent; it is a valid no-child conclusion. "
                "Reject module stacking, assumption conflict, evidence elevation, and unacceptable complexity. For an approved decision, "
                "`proposed_gene_donor_map.donors` is strictly `{genome_gene_name: parent_candidate_id}`; each donor value must be one of "
                "that decision's two parent IDs. For every non-approved decision, omit `proposed_gene_donor_map` rather than returning `{}`."
                + repair_instruction
            ),
            payload=payload,
        )

    async def review_human_composition(
        self,
        *,
        composition_id: str,
        candidates: list[CandidateDossier],
        component_refs: list[str],
        preserve_genes: list[str],
        donor_genes: dict[str, str],
        constraints: list[str],
    ) -> HumanCompositionCompatibility:
        """Assess a partial cross-Candidate request before any Child is created."""

        payload = {
            "prompt_version": "1.0.0",
            "composition_id": composition_id,
            "source_components": component_refs,
            "preserve_genes": preserve_genes,
            "requested_donor_genes": donor_genes,
            "constraints": constraints,
            "candidates": [_evolver_parent(candidate) for candidate in candidates],
        }
        data = await self.invoker.invoke(
            prompt_name="idea_composition_reviewer.j2",
            system_contract=(
                "You are IdeaScoringAgent in Human Composition compatibility-review mode. Return only one JSON object. "
                "Do not generate a Candidate, score a Candidate, rewrite a hypothesis, or choose for the researcher. "
                "Assess full Candidate context, not lexical similarity. A compose recommendation requires one coherent Core Thesis, "
                "an explicit Gene Donor Map, compatible evidence permissions, and no hard assumption conflict. "
                "For parallel or conflicting directions, recommend keep_parallel or reject_auto_merge."
            ),
            payload=payload,
        )
        return model_validate(HumanCompositionCompatibility, data)


class LLMIdeaEvolver(IdeaEvolverPort):
    """Plan-bound Child generator. It has no score or selection method."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def generate_offspring(
        self,
        *,
        plans: list[EvolutionPlan],
        parents: list[CandidateDossier],
    ) -> list[CandidateDossier] | EvolutionPlanDeferral:
        return await self._generate_offspring(plans=plans, parents=parents)

    async def repair_offspring(
        self,
        *,
        plans: list[EvolutionPlan],
        parents: list[CandidateDossier],
        failure_reason: str,
    ) -> list[CandidateDossier] | EvolutionPlanDeferral:
        return await self._generate_offspring(
            plans=plans,
            parents=parents,
            repair_reason=failure_reason,
        )

    async def _generate_offspring(
        self,
        *,
        plans: list[EvolutionPlan],
        parents: list[CandidateDossier],
        repair_reason: str = "",
    ) -> list[CandidateDossier] | EvolutionPlanDeferral:
        by_id = {candidate.candidate_id: candidate for candidate in parents}
        payload = {
            "prompt_version": "1.2.0",
            "plans": [model_dump(plan, mode="json") for plan in plans],
            "required_children": [
                {"plan_id": plan.plan_id, "candidate_id": _expected_child_id(plan)}
                for plan in plans
            ],
            "parents": [_evolver_parent(candidate) for candidate in parents],
        }
        repair_instruction = ""
        if repair_reason:
            payload["repair_reason"] = str(repair_reason)[:1600]
            repair_instruction = (
                " This is one structured-output repair. Return either one complete Child for the supplied Evolution Plan, or a documented "
                "single-plan `deferred_plans` outcome when a substantive Child is not defensible. The prior output failed because: "
                f"{str(repair_reason)[:900]}. Do not return a subset, a patch, Markdown, or commentary."
            )
        data = await self.invoker.invoke(
            prompt_name="idea_evolver.j2",
            system_contract=(
                "You are IdeaEvolverAgent. Return only JSON with `children` and `deferred_plans` arrays. Normally create one Child only from each explicit Evolution Plan. "
                "Do not select parents, change a plan, score results, or overwrite a Parent. Preserve named genes, respect the Gene Donor Map, "
                "and never elevate abstract-only evidence. Every `required_children` entry gives the exact new Candidate ID for its Plan; "
                "use that ID consistently as the top-level, genome, and lineage ID. Never reuse a Parent ID. When exactly one supplied Plan cannot yield "
                "a substantive, evidence-calibrated Child, return no Child and exactly one documented deferral for that Plan; do not make a cosmetic rewrite."
                + repair_instruction
            ),
            payload=payload,
        )
        try:
            return _parse_offspring_response(data=data, plans=plans, parents=parents)
        except (TypeError, ValueError) as exc:
            # The controller owns Plan identity and one-to-one Child coverage.
            # A bounded SemanticRepairAgent may normalize aliases or restore a
            # controller-owned Child ID only when the Plan/Parent relation is
            # already unambiguous. It cannot choose a Plan or approve a new
            # lineage.
            normalized = await self._repair_offspring_semantics(
                plans=plans,
                parents=parents,
                attempted_response=data,
                validation_error=exc,
            )
            return _parse_offspring_response(data=normalized, plans=plans, parents=parents)

    async def _repair_offspring_semantics(
        self,
        *,
        plans: list[EvolutionPlan],
        parents: list[CandidateDossier],
        attempted_response: dict[str, Any],
        validation_error: Exception,
    ) -> dict[str, Any]:
        return await self.invoker.invoke(
            prompt_name="idea_offspring_semantic_repair.j2",
            system_contract=(
                "You are T4 SemanticRepairAgent for plan-bound Evolution Children. Normalize one parseable but invalid response into "
                "one JSON object with `children` and `deferred_plans` arrays. Do not select Parents, add or remove Plans, approve a crossover, change parent "
                "sets, alter Evidence Permission, invent a source, or create a new research claim. Map equivalent fields and nesting only. "
                "You may restore a controller-owned Child ID from `required_children` only when exactly one supplied Plan matches the Child's "
                "unchanged parent set and evolution_plan_id. Preserve all source references and uncertainty. When the Child genome, plan, and lineage are "
                "already intact, an absent CandidatePresentation is optional enrichment and must not reject the Child. You may retain or improve supplied "
                "presentation wording only from the Child genes, Parent context, and existing Evidence Permission; do not change a Gene or add support. If a Child cannot be safely tied "
                "to one Plan, leave it unresolved for deterministic validation instead of guessing. A documented deferral may be retained only for the one supplied "
                "Plan and only when the attempted response already makes the inability to improve or incompatibility explicit. Return exactly one JSON object and no Markdown."
            ),
            payload={
                "plans": [model_dump(plan, mode="json") for plan in plans],
                "required_children": [
                    {"plan_id": plan.plan_id, "candidate_id": _expected_child_id(plan), "parent_ids": plan.parent_ids}
                    for plan in plans
                ],
                "parents": [_evolver_parent(candidate) for candidate in parents],
                "attempted_response": attempted_response,
                "validator_error": str(validation_error)[:1600],
            },
        )

    async def generate_human_composition(
        self,
        *,
        composition_id: str,
        target_candidate_id: str,
        compatibility: HumanCompositionCompatibility,
        parents: list[CandidateDossier],
    ) -> CandidateDossier:
        """Create one confirmed Human-composed Candidate from its Gene Donor Map."""

        payload = {
            "prompt_version": "1.0.0",
            "composition_id": composition_id,
            "target_candidate_id": target_candidate_id,
            "compatibility": model_dump(compatibility, mode="json"),
            "parents": [_evolver_parent(candidate) for candidate in parents],
        }
        data = await self.invoker.invoke(
            prompt_name="idea_human_composer.j2",
            system_contract=(
                "You are IdeaEvolverAgent in Human Composition mode. Return only JSON with a `candidate` object. "
                "Create exactly one new Candidate from the confirmed Compatibility Report and Gene Donor Map. "
                "Do not alter source Candidates, invent source evidence, elevate abstract-only evidence, score the new Candidate, "
                "or concatenate source hypotheses. The output must have one coherent Core Thesis, a complete Idea Genome, 2-4 Contributions, "
                "2-4 provisional hypotheses, CandidateLineage.created_by=human_composition, and a falsifiable validation path. "
                "CandidatePresentation is optional enrichment; the separate Final Card Compiler owns required human-facing explanations."
            ),
            payload=payload,
        )
        raw_candidate = data.get("candidate")
        if not isinstance(raw_candidate, dict):
            raise ValueError("Human-composition Evolver must return one candidate object")
        candidate = model_validate(CandidateDossier, _normalize_candidate_dossier_payload(raw_candidate))
        _require_evolved_candidate_scientific_core(candidate)
        source_ids = {item.candidate_id for item in parents}
        if candidate.candidate_id != target_candidate_id:
            raise ValueError("Human-composition Evolver returned a non-deterministic Candidate ID")
        if set(candidate.lineage.parent_ids) != source_ids or set(candidate.genome.parents) != source_ids:
            raise ValueError("Human-composition Candidate does not preserve complete source lineage")
        if candidate.lineage.created_by != "human_composition":
            raise ValueError("Human-composition Candidate must declare human_composition lineage")
        return candidate


class LLMFinalIdeaCardCompiler:
    """Compile portfolio-only Impact Translation without changing Candidates."""

    def __init__(self, invoker: LLMJsonRoleInvoker) -> None:
        self.invoker = invoker

    async def compile(
        self,
        *,
        candidates: list[CandidateDossier],
        target_profile: TargetProfile,
    ) -> list[FinalIdeaCardTranslation]:
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if not candidates:
            diagnostic = classify_final_card_exception(
                ValueError("Final Card Compiler received no supplied Portfolio Candidate"),
                stage="source_preflight",
                candidate_ids=candidate_ids,
            )
            raise FinalCardCompilationFailure(diagnostic)
        if len(candidate_ids) != len(set(candidate_ids)):
            diagnostic = classify_final_card_exception(
                ValueError("Final Card Compiler received duplicate Portfolio Candidate IDs"),
                stage="source_preflight",
                candidate_ids=candidate_ids,
            )
            raise FinalCardCompilationFailure(diagnostic)
        payload = {
            "prompt_version": "1.2.0",
            "candidates": [model_dump(candidate, mode="json") for candidate in candidates],
        }
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        try:
            data = await self.invoker.invoke(
                prompt_name="idea_final_card_compiler.j2",
                system_contract=(
                    "You are the Final Idea Card Compiler. Return only JSON with a `cards` array. You translate only the "
                    "selected Portfolio Candidates into readable impact cards; you do not generate, score, select, or modify a Candidate. "
                    "Preserve scientific content exactly and label implications by their actual Evidence Status. Write the card in clear Chinese for the researcher, "
                    "retaining standard academic terms in English where they are more precise. Treat innovation wording as a proposal-relative delta, not an "
                    "externally verified novelty conclusion: without direct verified novelty evidence, do not use priority claims such as 'first', '首次', "
                    "'no existing method', or equivalents."
                ),
                payload=payload,
            )
        except T4RoleResponseFormatError as exc:
            # Even a prose, fenced, or otherwise unparseable reply can carry
            # usable researcher-facing intent.  Give the dedicated repair LLM
            # the bounded response excerpt plus the canonical Candidate package
            # before treating the card as unavailable.
            diagnostic = classify_final_card_exception(
                exc,
                stage="initial_response_parse",
                candidate_ids=candidate_ids,
            )
            return await self._repair_and_validate(
                candidates=candidates,
                target_profile=target_profile,
                by_id=by_id,
                attempted_response={"raw_response_excerpt": exc.response_excerpt},
                validation_error=exc,
                initial_failure=diagnostic,
            )
        except Exception as exc:
            diagnostic = classify_final_card_exception(
                exc,
                stage="initial_generation",
                candidate_ids=candidate_ids,
            )
            raise FinalCardCompilationFailure(diagnostic) from exc

        try:
            return self._parse_and_validate(
                data=data,
                by_id=by_id,
                target_profile=target_profile,
                candidates=candidates,
            )
        except (TypeError, ValueError) as exc:
            # A Final Card is a presentation translation, so omitted prose,
            # aliases, shallow nesting, incomplete coverage, or an immutable
            # echo mismatch receive an LLM repair pass.  The repair receives
            # the canonical Candidate package and must produce its own
            # explanation; deterministic code never fills a display field.
            diagnostic = classify_final_card_exception(
                exc,
                stage="initial_schema_validation",
                candidate_ids=candidate_ids,
            )
            return await self._repair_and_validate(
                candidates=candidates,
                target_profile=target_profile,
                by_id=by_id,
                attempted_response=data,
                validation_error=exc,
                initial_failure=diagnostic,
            )

    def _parse_and_validate(
        self,
        *,
        data: dict[str, Any],
        by_id: dict[str, CandidateDossier],
        target_profile: TargetProfile,
        candidates: list[CandidateDossier],
    ) -> list[FinalIdeaCardTranslation]:
        cards = _parse_final_card_response(data=data, by_id=by_id, target_profile=target_profile)
        expected = {candidate.candidate_id for candidate in candidates}
        actual = {card.candidate_id for card in cards}
        if actual != expected:
            raise ValueError(
                "Final Idea Card Compiler must cover exactly the Portfolio; "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        for card in cards:
            candidate = by_id[card.candidate_id]
            if card.profile_type != target_profile.profile_type:
                raise ValueError(f"Final Idea Card profile mismatch for {card.candidate_id}")
            if card.core_thesis != str(candidate.genome.core_thesis.value):
                raise ValueError(f"Final Idea Card changed the core thesis for {card.candidate_id}")
            if card.contribution_ids != [item.contribution_id for item in candidate.contributions]:
                raise ValueError(f"Final Idea Card changed contribution membership for {card.candidate_id}")
            if card.hypothesis_ids != [item.hypothesis_id for item in candidate.hypotheses]:
                raise ValueError(f"Final Idea Card changed hypothesis membership for {card.candidate_id}")
        return cards

    async def _repair_and_validate(
        self,
        *,
        candidates: list[CandidateDossier],
        target_profile: TargetProfile,
        by_id: dict[str, CandidateDossier],
        attempted_response: dict[str, Any],
        validation_error: Exception,
        initial_failure: FinalCardFailureDiagnostic,
    ) -> list[FinalIdeaCardTranslation]:
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        try:
            repaired = await self._repair_final_card_semantics(
                candidates=candidates,
                target_profile=target_profile,
                attempted_response=attempted_response,
                validation_error=validation_error,
            )
        except Exception as exc:
            diagnostic = classify_final_card_exception(
                exc,
                stage="semantic_repair_generation",
                candidate_ids=candidate_ids,
                prior_failure=initial_failure,
            )
            raise FinalCardCompilationFailure(diagnostic) from exc
        try:
            return self._parse_and_validate(
                data=repaired,
                by_id=by_id,
                target_profile=target_profile,
                candidates=candidates,
            )
        except (TypeError, ValueError) as exc:
            diagnostic = classify_final_card_exception(
                exc,
                stage="semantic_repair_validation",
                candidate_ids=candidate_ids,
                prior_failure=initial_failure,
            )
            raise FinalCardCompilationFailure(diagnostic) from exc

    async def _repair_final_card_semantics(
        self,
        *,
        candidates: list[CandidateDossier],
        target_profile: TargetProfile,
        attempted_response: dict[str, Any],
        validation_error: Exception,
    ) -> dict[str, Any]:
        return await self.invoker.invoke(
            prompt_name="idea_final_card_semantic_repair.j2",
            system_contract=(
                "You are the T4 Final Idea Card SemanticRepairAgent. Return only JSON with a `cards` array. Normalize an attempted "
                "researcher-facing card into FinalIdeaCardTranslation fields. Preserve all Candidate identity, thesis, contribution, "
                "hypothesis, evidence-status, and risk boundaries. You may write candidate-specific researcher-facing explanations by "
                "synthesizing the supplied Candidate genome, contributions, hypotheses, risks, evidence permissions, and Portfolio context. "
                "This is a presentation translation, not authority to add a mechanism, contribution, hypothesis, source, citation, dataset, "
                "metric, experiment result, stakeholder effect, business value, evidence upgrade, or novelty claim. If a required card cannot "
                "be reconstructed without such an addition, leave it incomplete for the deterministic validator rather than fabricating it. Rewrite any "
                "unsupported priority novelty wording as a conditional Candidate-relative innovation explanation."
            ),
            payload={
                "candidates": [model_dump(candidate, mode="json") for candidate in candidates],
                "target_profile": model_dump(target_profile, mode="json"),
                "attempted_response": attempted_response,
                "validator_error": str(validation_error)[:1600],
            },
        )


def _blind_candidate(candidate: CandidateDossier) -> dict[str, Any]:
    """Remove signals that bias independent scoring while retaining candidate identity."""

    return {
        "candidate_id": candidate.candidate_id,
        "genome": model_dump(candidate.genome, mode="json", exclude={"route", "parents", "generation_created"}),
        "contributions": [model_dump(item, mode="json") for item in candidate.contributions],
        "hypotheses": [model_dump(item, mode="json") for item in candidate.hypotheses],
        "evidence_composition": candidate.evidence_composition,
        "creative_context": model_dump(candidate.creative_context, mode="json"),
        "warnings": candidate.warnings,
    }


def _interaction_candidate(candidate: CandidateDossier) -> dict[str, Any]:
    """Expose candidate content for pair interpretation, not for selection."""

    return {
        "candidate_id": candidate.candidate_id,
        "problem": candidate.genome.problem.value,
        "core_thesis": candidate.genome.core_thesis.value,
        "mechanism": candidate.genome.mechanism.value,
        "contribution_package": candidate.genome.contribution_package.value,
        "hypotheses": [item.statement for item in candidate.hypotheses],
        "validation_logic": candidate.genome.validation_logic.value,
        "boundary_conditions": candidate.genome.boundary_conditions.value,
        "creative_context": model_dump(candidate.creative_context, mode="json"),
    }


def _normalize_interaction_review(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize only explicit Interaction Reviewer aliases.

    This intentionally accepts a partial explanation. The controller's graph
    merge will retain deterministic structure and annotate the missing prose;
    it must never retry a full T4 round merely because one pair's wording is
    absent or uses a localized enum label.
    """

    source_id = str(item.get("source_id") or "").strip()
    target_id = str(item.get("target_id") or "").strip()
    relation_hint = str(item.get("relation_hint") or item.get("requested_relation") or "").strip()
    if not source_id or not target_id or not relation_hint:
        return None
    relation_raw = str(item.get("relation_type") or item.get("relationship") or "parallel").strip().casefold()
    relation_key = " ".join(relation_raw.replace("_", " ").replace("-", " ").split())
    relation_aliases = {
        "competitor": "competitor",
        "competition": "competitor",
        "overlap": "competitor",
        "竞争": "competitor",
        "complement": "complement",
        "complementary": "complement",
        "互补": "complement",
        "distant transfer": "distant_transfer",
        "wildcard": "distant_transfer",
        "远距迁移": "distant_transfer",
        "parallel": "parallel",
        "keep parallel": "parallel",
        "并行": "parallel",
        "并行保留": "parallel",
    }
    relation_type = relation_aliases.get(relation_key)
    if relation_type is None:
        return None
    result: dict[str, Any] = {
        "source_id": source_id,
        "target_id": target_id,
        "relation_hint": relation_hint,
        "relation_type": relation_type,
    }
    for field in (
        "shared_core",
        "key_difference",
        "peer_challenge",
        "transferable_element",
        "differentiation_need",
        "crossover_risk",
        "rationale",
    ):
        value = " ".join(str(item.get(field) or "").split())
        if value:
            result[field] = value
    potential_raw = str(item.get("crossover_potential") or "").strip().casefold()
    potential_key = " ".join(potential_raw.replace("_", " ").replace("-", " ").split())
    potential_aliases = {
        "high": "high",
        "medium": "medium",
        "moderate": "medium",
        "low": "low",
        "none": "none",
        "高": "high",
        "中": "medium",
        "中等": "medium",
        "低": "low",
        "无": "none",
    }
    if potential_key in potential_aliases:
        result["crossover_potential"] = potential_aliases[potential_key]
    return result


def _evolver_parent(candidate: CandidateDossier) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "genome": model_dump(candidate.genome, mode="json"),
        "contributions": [model_dump(item, mode="json") for item in candidate.contributions],
        "hypotheses": [model_dump(item, mode="json") for item in candidate.hypotheses],
        "lineage": model_dump(candidate.lineage, mode="json"),
        "creative_context": model_dump(candidate.creative_context, mode="json"),
    }


_IDEA_GENOME_GENES = frozenset(
    {
        "problem",
        "opportunity",
        "challenged_assumption",
        "core_thesis",
        "mechanism",
        "design_or_artifact",
        "contribution_package",
        "hypothesis_bundle",
        "validation_logic",
        "boundary_conditions",
        "risks",
    }
)


def _expected_child_id(plan: EvolutionPlan) -> str:
    """Derive a stable, controller-owned identity for one Evolution Plan."""

    return f"EVO-{plan.plan_id}-001"


def _normalize_final_card_immutable_fields(
    payload: dict[str, Any],
    *,
    by_id: dict[str, CandidateDossier],
    target_profile: TargetProfile,
) -> dict[str, Any]:
    """Keep display translation from mutating the selected Candidate package.

    Idea Cards are LLM-authored explanations, but their identity, Core Thesis,
    Contribution membership, Hypothesis membership, and requested Profile are
    canonical values already governed by the evolved Candidate. Providers often
    paraphrase these exact echoes despite the prompt. Rebinding the immutable
    fields lets the LLM's readable explanation remain useful without accepting
    a silent scientific mutation.
    """

    candidate = by_id.get(str(payload.get("candidate_id") or ""))
    if candidate is None:
        return payload
    normalized = _normalize_final_card_presentation_fields(payload)
    normalized.update(
        {
            "candidate_id": candidate.candidate_id,
            "profile_type": target_profile.profile_type,
            "core_thesis": str(candidate.genome.core_thesis.value),
            "contribution_ids": [item.contribution_id for item in candidate.contributions],
            "hypothesis_ids": [item.hypothesis_id for item in candidate.hypotheses],
        }
    )
    return normalized


_FINAL_CARD_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "plain_language_summary": ("summary", "overview", "description", "plain_summary", "card_summary"),
    "why_it_matters": ("importance", "significance", "why", "impact_statement"),
    "affected_stakeholders_or_processes": ("stakeholders", "affected_stakeholders", "affected_processes", "target_audience"),
    "representative_scenario": ("scenario", "use_case", "example", "representative_example"),
    "real_world_significance": (
        "real_world_impact",
        "real_world_meaning",
        "practical_significance",
        "practical_impact",
        "现实意义",
    ),
    "current_failure": ("failure", "failure_mode", "current_problem", "pain_point"),
    "scientific_technical_core": ("scientific_core", "technical_core", "core", "mechanism_summary"),
    "implications": ("impact_implications", "practical_implications", "applicable_implications"),
    "conditions_for_impact": ("impact_conditions", "conditions", "dependencies"),
    "claims_not_to_make": ("claims_to_avoid", "non_claims", "caveats", "do_not_claim"),
    "risks_and_boundaries": ("risks", "boundaries", "limitations", "risks_boundaries"),
    "evidence_status_summary": ("evidence_status", "evidence_summary", "evidence_calibration"),
    "short_title": ("title_short", "display_title", "short_name"),
    "contribution_type_label": ("contribution_type", "primary_contribution_type"),
    "innovation_type": ("innovation_kind", "innovation_category"),
    "innovation_delta": ("innovation_change", "innovation_difference"),
    "non_routine_explanation": ("why_non_routine", "non_incremental_explanation"),
    "relationship_to_portfolio": ("portfolio_relationship", "relationship_summary", "candidate_difference"),
    "dependency_candidate_ids": ("dependencies", "depends_on_candidate_ids"),
    "composition_guidance": ("combination_guidance", "merge_guidance"),
    "recommendation": ("selection_recommendation", "recommended_action"),
    "bottleneck_explanation": ("dominant_bottleneck", "bottleneck"),
}
_FINAL_CARD_LIST_FIELDS = frozenset(
    {
        "affected_stakeholders_or_processes",
        "implications",
        "conditions_for_impact",
        "claims_not_to_make",
        "risks_and_boundaries",
        "dependency_candidate_ids",
    }
)
_FINAL_CARD_ALLOWED_FIELDS = frozenset(
    {
        "candidate_id",
        "profile_type",
        "core_thesis",
        "contribution_ids",
        "hypothesis_ids",
        "plain_language_summary",
        "why_it_matters",
        "affected_stakeholders_or_processes",
        "representative_scenario",
        "real_world_significance",
        "current_failure",
        "scientific_technical_core",
        "implications",
        "conditions_for_impact",
        "claims_not_to_make",
        "risks_and_boundaries",
        "evidence_status_summary",
        "short_title",
        "contribution_type_label",
        "innovation_type",
        "innovation_delta",
        "non_routine_explanation",
        "relationship_to_portfolio",
        "dependency_candidate_ids",
        "composition_guidance",
        "recommendation",
        "bottleneck_explanation",
    }
)
_CROSSOVER_DECISION_ALLOWED_FIELDS = frozenset(
    {
        "pair_id",
        "parent_ids",
        "decision",
        "problem_compatibility",
        "bottleneck_complementarity",
        "mechanism_coherence",
        "conflicts",
        "proposed_gene_donor_map",
        "complexity_risk",
    }
)


def _normalize_final_card_presentation_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Map harmless Final Card display aliases without altering research content.

    These fields are a view over a Candidate.  Mapping ``summary`` to
    ``plain_language_summary`` or wrapping a one-line risk as a list is a
    lossless display repair; it neither certifies evidence nor changes the
    Candidate's scientific package. Unknown UI keys are intentionally dropped
    after known aliases have been recovered so they do not reject a usable
    card under the strict model.
    """

    normalized = dict(payload)
    for canonical, aliases in _FINAL_CARD_FIELD_ALIASES.items():
        if normalized.get(canonical) not in (None, "", [], {}):
            continue
        for alias in aliases:
            value = normalized.get(alias)
            if value not in (None, "", [], {}):
                normalized[canonical] = value
                break
    for field in _FINAL_CARD_LIST_FIELDS:
        value = normalized.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = [value.strip()]
        elif value is None:
            continue
        elif not isinstance(value, list):
            normalized[field] = [value]
    return {key: value for key, value in normalized.items() if key in _FINAL_CARD_ALLOWED_FIELDS}


def _parse_final_card_response(
    *,
    data: dict[str, Any],
    by_id: dict[str, CandidateDossier],
    target_profile: TargetProfile,
) -> list[FinalIdeaCardTranslation]:
    raw = data.get("cards") if isinstance(data.get("cards"), list) else []
    if not raw:
        raise ValueError("Final Idea Card Compiler must return a non-empty cards array")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("Final Idea Card Compiler returned a non-object card")
    normalized_cards = [
        _normalize_final_card_immutable_fields(item, by_id=by_id, target_profile=target_profile)
        for item in raw
    ]
    return [model_validate(FinalIdeaCardTranslation, item) for item in normalized_cards]

_GENE_LABEL_ALIASES = (
    ("challenged assumption", "challenged_assumption"),
    ("core thesis", "core_thesis"),
    ("design or artifact", "design_or_artifact"),
    ("contribution package", "contribution_package"),
    ("hypothesis bundle", "hypothesis_bundle"),
    ("validation logic", "validation_logic"),
    ("validation approach", "validation_logic"),
    ("boundary conditions", "boundary_conditions"),
    ("boundary condition", "boundary_conditions"),
)


def _parse_crossover_decisions(
    data: dict[str, Any],
    pair_by_id: dict[str, set[str]],
) -> list[CrossoverCompatibilityDecision]:
    raw = data.get("decisions") if isinstance(data.get("decisions"), list) else []
    decisions: list[CrossoverCompatibilityDecision] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("T4 Crossover Compatibility Check returned a non-object decision")
        pair_id = str(item.get("pair_id") or "")
        expected_parents = pair_by_id.get(pair_id)
        if expected_parents is None:
            raise ValueError(f"T4 Crossover Compatibility Check returned an unknown pair: {pair_id}")
        if pair_id in seen:
            raise ValueError(f"T4 Crossover Compatibility Check returned duplicate pair: {pair_id}")
        normalized = _normalize_crossover_complexity(
            _normalize_crossover_explanations(
                _normalize_crossover_donor_map(item, expected_parents)
            )
        )
        decision = model_validate(CrossoverCompatibilityDecision, normalized)
        if set(decision.parent_ids) != expected_parents or len(decision.parent_ids) != 2:
            raise ValueError(f"T4 Crossover Compatibility Check changed the parent set for {pair_id}")
        donor_map = decision.proposed_gene_donor_map
        if decision.decision == "approved" and donor_map is None:
            raise ValueError(f"approved Crossover decision {pair_id} lacks a Gene Donor Map")
        if donor_map is not None:
            if not set(donor_map.donors).issubset(_IDEA_GENOME_GENES):
                raise ValueError(f"T4 Crossover Compatibility Check used unsupported Gene Donor Map keys for {pair_id}")
            if not set(donor_map.donors.values()).issubset(expected_parents):
                raise ValueError(f"T4 Crossover Compatibility Check used a donor outside the reviewed pair for {pair_id}")
        decisions.append(decision)
        seen.add(pair_id)
    return decisions


def _normalize_crossover_complexity(payload: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a qualitative complexity label without changing its rationale.

    Providers often put a useful Chinese/English sentence such as ``高：需要
    额外对照`` in the small enum slot.  The compatibility decision already
    carries a separate explanation, conflicts, and donor map; mapping this
    unambiguous qualitative prefix is a display/enum repair, not an inference
    about scientific feasibility. Ambiguous phrases remain invalid and are not
    guessed into a favourable risk level.
    """

    normalized = dict(payload)
    # ``parallel`` is a scientifically useful no-crossover verdict, not a
    # malformed response. It is preserved as a durable compatibility outcome;
    # only ``approved`` can reach the Child-plan compiler. Canonicalization
    # here prevents a normal reviewer conclusion from aborting an Evolution
    # round while retaining its distinct portfolio semantics.
    normalized["decision"] = normalize_crossover_decision(normalized.get("decision"))
    if normalized.get("complexity_risk") in (None, "", [], {}):
        for alias in ("complexity", "complexity_assessment", "complexity_level", "risk_level"):
            value = normalized.get(alias)
            if value not in (None, "", [], {}):
                normalized["complexity_risk"] = value
                break
    value = normalized.get("complexity_risk")
    if isinstance(value, dict):
        for key in ("level", "risk", "complexity", "value", "assessment"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                normalized["complexity_risk"] = candidate
                value = candidate
                break
    if isinstance(value, str):
        compact = " ".join(value.strip().casefold().split())
        if compact in {"low", "低", "低风险", "低复杂度"} or compact.startswith(("low", "低")):
            normalized["complexity_risk"] = "low"
        elif compact in {"medium", "moderate", "中", "中等", "中风险", "中等复杂度"} or compact.startswith(("medium", "moderate", "中")):
            normalized["complexity_risk"] = "medium"
        elif compact in {"high", "高", "高风险", "高复杂度"} or compact.startswith(("high", "高")):
            normalized["complexity_risk"] = "high"
        elif any(
            token in compact
            for token in (
                "additional",
                "adds ",
                "substantial",
                "significant",
                "complex",
                "extra control",
                "额外",
                "增加",
                "复杂",
            )
        ):
            # This conservative branch is intentionally one-way: an unqualified
            # sentence about additional design burden can be shown as high risk,
            # never reinterpreted as a favourable low/medium assessment.
            normalized["complexity_risk"] = "high"
    # Extra reviewer commentary can be useful to a human but is not part of
    # CrossoverCompatibilityDecision's durable contract. It cannot approve a
    # Child, so dropping it after the canonical fields are recovered avoids a
    # harmless UI explanation rejecting an otherwise usable decision.
    return {key: value for key, value in normalized.items() if key in _CROSSOVER_DECISION_ALLOWED_FIELDS}


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


def _normalize_crossover_explanations(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten explicit provider explanation wrappers without inventing a view.

    The Crossover schema deliberately records prose for the problem,
    bottleneck, and mechanism review rather than a bare favourable label. Some
    providers return the same review as an object, for example
    ``{"compatibility": "high", "explanation": "..."}``. That is a
    response-shape variation, not authority for runtime code to supply a new
    scientific explanation. Preserve an existing text value only; leave an
    opaque object invalid so the bounded LLM repair path can correct it.
    """

    normalized = dict(payload)
    for field in (
        "problem_compatibility",
        "bottleneck_complementarity",
        "mechanism_coherence",
    ):
        value = normalized.get(field)
        if not isinstance(value, dict):
            continue
        text = _first_crossover_explanation(value)
        if text:
            normalized[field] = text
    return normalized


def _first_crossover_explanation(value: dict[str, Any]) -> str:
    for key in _CROSSOVER_EXPLANATION_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, str):
            text = " ".join(candidate.split())
            if text:
                return text
    return ""


def _normalize_crossover_donor_map(item: dict[str, Any], parent_ids: set[str]) -> dict[str, Any]:
    """Reverse a provider's donor->gene map only when every entry is explicit."""

    donor_map = item.get("proposed_gene_donor_map")
    if not isinstance(donor_map, dict) or not isinstance(donor_map.get("donors"), dict):
        return item
    donors = donor_map["donors"]
    if all(str(gene) in _IDEA_GENOME_GENES and str(donor) in parent_ids for gene, donor in donors.items()):
        return item
    if not donors or not all(str(donor_id) in parent_ids for donor_id in donors):
        return item
    reversed_donors: dict[str, str] = {}
    for donor_id, raw_genes in donors.items():
        genes = _canonical_gene_names(raw_genes)
        if not genes:
            return item
        for gene in genes:
            prior = reversed_donors.get(gene)
            if prior is not None and prior != donor_id:
                return item
            reversed_donors[gene] = str(donor_id)
    normalized = dict(item)
    normalized_map = dict(donor_map)
    normalized_map["donors"] = reversed_donors
    normalized["proposed_gene_donor_map"] = normalized_map
    return normalized


def _canonical_gene_names(raw: object) -> list[str]:
    values = raw if isinstance(raw, list) else [raw]
    detected: list[str] = []
    for value in values:
        text = re.sub(r"[_-]+", " ", str(value or "").casefold())
        matches: list[str] = []
        for label, gene in _GENE_LABEL_ALIASES:
            if label in text:
                matches.append(gene)
        for gene in _IDEA_GENOME_GENES:
            if re.search(rf"(?<![a-z0-9]){re.escape(gene.replace('_', ' '))}(?![a-z0-9])", text):
                matches.append(gene)
        if not matches:
            return []
        for gene in matches:
            if gene not in detected:
                detected.append(gene)
    return detected


def _score_rationale_repair_targets(
    *,
    reports: list[ScoreReport],
    candidates: list[CandidateDossier],
) -> dict[str, dict[str, object]]:
    """Return only missing core-rationale repairs for a structurally valid batch."""

    expected_ids = {candidate.candidate_id for candidate in candidates}
    actual_ids = {report.candidate_id for report in reports}
    if expected_ids != actual_ids or len(reports) != len(actual_ids):
        return {}
    targets: dict[str, dict[str, object]] = {}
    for report in reports:
        if not report.blind:
            return {}
        weak_keys = list(report.rationale_missing)
        if weak_keys:
            targets[report.candidate_id] = {
                "core_rationale_keys": weak_keys,
            }
    return targets


def _reports_have_reusable_core_contract(
    reports: list[ScoreReport],
    *,
    candidates: list[CandidateDossier],
    blind: bool,
) -> bool:
    """Whether an existing batch can survive a non-core diagnostic failure."""

    expected_ids = {candidate.candidate_id for candidate in candidates}
    actual_ids = {report.candidate_id for report in reports}
    return bool(reports) and expected_ids == actual_ids and len(reports) == len(actual_ids) and all(report.blind == blind for report in reports)


def _mark_score_reports_degraded(reports: list[ScoreReport], warning: str) -> list[ScoreReport]:
    """Preserve valid core scores while making a local repair failure visible."""

    return [
        report.model_copy(
            update={"diagnostic_warnings": list(dict.fromkeys([*report.diagnostic_warnings, warning]))}
        )
        for report in reports
    ]


def _score_repair_candidate(candidate: CandidateDossier) -> dict[str, object]:
    """Expose only the evidence-bound fields needed to explain a score repair."""

    return {
        "candidate_id": candidate.candidate_id,
        "core_thesis": candidate.genome.core_thesis.value,
        "mechanism": candidate.genome.mechanism.value,
        "validation_logic": candidate.genome.validation_logic.value,
        "boundary_conditions": candidate.genome.boundary_conditions.value,
        "contributions": [item.statement for item in candidate.contributions],
        "hypotheses": [item.statement for item in candidate.hypotheses],
        "evidence_composition": candidate.evidence_composition,
        "creative_context": model_dump(candidate.creative_context, mode="json"),
        "warnings": candidate.warnings,
    }


def _rationale_length(value: object) -> int:
    return len(" ".join(str(value or "").split()))


_EXPLANATION_PLACEHOLDER_RE = re.compile(
    r"^(?:\.{2,}|…+|unknown|n/?a|none|tbd|todo|待(?:补充|确定|核验)|未提供|未标注)$",
    flags=re.IGNORECASE,
)


def _meaningful_explanation(value: object) -> bool:
    text = " ".join(str(value or "").split())
    return bool(text) and not bool(_EXPLANATION_PLACEHOLDER_RE.fullmatch(text))


def _validated_repaired_rationale(value: object, candidate_id: str, field: str) -> str:
    text = str(value or "").strip()
    if not _meaningful_explanation(text):
        raise ValueError(f"T4 Scoring Agent returned an incomplete rationale repair for {candidate_id}: {field}")
    return text


def _require_evolved_candidate_scientific_core(candidate: CandidateDossier) -> None:
    """Protect the scientific Child contract without requiring legacy display prose.

    CandidatePresentation is an enrichable compatibility payload.  The native
    Final Card Compiler is the only place where complete researcher-facing
    explanation is mandatory, so an absent legacy ``selection_advice`` or a
    wholly omitted presentation cannot discard a valid Child or Seed.
    """

    if not 2 <= len(candidate.hypotheses) <= 4:
        raise ValueError(f"T4 Evolver returned candidate {candidate.candidate_id} without 2-4 provisional hypotheses")
    if not 2 <= len(candidate.contributions) <= 4:
        raise ValueError(f"T4 Evolver returned candidate {candidate.candidate_id} without 2-4 contributions")


def _admit_initial_candidate(candidate: CandidateDossier) -> CandidateDossier:
    """Keep a promising but under-enriched first-pass Candidate as a Seed.

    Initial formation is allowed to be imaginative and incomplete.  A response
    that already has a stable genome but lacks legacy display prose should not
    make its whole Route fail. CandidatePresentation is optional enrichment;
    the Candidate remains at its scientifically earned maturity and receives
    Final Card enrichment only if it reaches the Portfolio. This adapter does
    not add research prose, sources, scores, or positive evidence.

    More than four hypotheses still goes through semantic repair because the
    controller cannot safely decide which LLM-authored hypotheses to discard.
    """

    if candidate.maturity == CandidateMaturity.SEED:
        return candidate
    _require_evolved_candidate_scientific_core(candidate)
    if candidate.presentation is not None and candidate.presentation.basis_sources:
        return candidate
    warning = (
        "presentation_enrichment_required: legacy CandidatePresentation is absent or partial; "
        "retain the scientific Candidate and request Final Card LLM explanation only if it enters the Portfolio."
    )
    return candidate.model_copy(update={"warnings": [*candidate.warnings, warning]})


def _coerce_minimal_seed_candidate(
    payload: dict[str, Any],
    *,
    route: str,
    ordinal: int,
) -> dict[str, Any]:
    """Project a minimal IdeaSeed into a traceable, explicitly degraded dossier.

    This reuses only Seed-provided text. It adds no citation, evidence upgrade,
    score, experiment result, or scientific claim. Missing material remains
    visible as seed maturity and a later enrichment requirement.
    """

    thesis = _seed_text(payload, "one_line_thesis", "core_thesis", "thesis")
    problem = _seed_text(payload, "problem", "target_problem")
    mechanism = _seed_text(payload, "candidate_mechanism", "mechanism")
    prediction = _seed_text(payload, "provisional_prediction", "prediction", "draft_hypothesis")
    risk = _seed_text(payload, "main_uncertainty", "main_risk", "risk")
    if not all((thesis, problem, mechanism, prediction, risk)):
        raise ValueError(
            "minimal IdeaSeed requires problem, thesis, candidate mechanism, one prediction, and one main risk"
        )
    route_value = str(payload.get("route") or route).strip() or route
    candidate_id = str(payload.get("candidate_id") or payload.get("id") or "").strip()
    if not candidate_id:
        compact_route = re.sub(r"[^A-Za-z0-9]+", "-", route_value).strip("-")[:42] or "route"
        candidate_id = f"S-{compact_route}-{ordinal}"
    contribution = _seed_list_or_text(
        payload.get("contribution_sketch") or payload.get("contribution") or thesis
    )
    validation = _seed_text(
        payload,
        "discriminating_test",
        "validation_direction",
        "validation_logic",
        "validation",
    ) or "Validation design is deferred; verification is required before selection."
    opportunity = _seed_text(payload, "opportunity", "why_now") or problem
    boundary = _seed_text(payload, "boundary_conditions", "boundary") or risk
    assumption = _seed_text(payload, "challenged_assumption") or risk
    design = _seed_text(payload, "design_or_artifact", "design") or validation
    evidence_refs = _seed_source_refs(payload.get("evidence_refs") or payload.get("source_refs"))
    knowledge_origin = _seed_knowledge_origin(payload.get("knowledge_origin"), has_workspace_refs=bool(evidence_refs))
    creative_context = {
        "conceptual_leap": _seed_text(payload, "creative_leap", "conceptual_leap", "why_non_obvious"),
        "competing_explanations": _seed_list_or_text(payload.get("competing_explanations") or payload.get("alternative_explanation"))
        if payload.get("competing_explanations") or payload.get("alternative_explanation")
        else [],
        "surprising_prediction": _seed_text(payload, "surprising_prediction", "counterintuitive_prediction"),
        "research_program_potential": _seed_text(payload, "research_program_potential", "research_program", "future_program"),
        "knowledge_origin": knowledge_origin,
        "evidence_status": "mixed" if evidence_refs else "conjectural",
        "verification_required": True,
        "reading_or_validation_upgrades": _seed_list_or_text(
            payload.get("reading_or_validation_upgrades") or payload.get("upgrade_needs")
        )
        if payload.get("reading_or_validation_upgrades") or payload.get("upgrade_needs")
        else [],
    }
    provenance = GeneProvenance(
        source_routes=[route_value],
        source_refs=evidence_refs,
        reading_levels=_seed_reading_levels(payload.get("reading_levels"), evidence_refs),
        evidence_role=EvidenceRole.CONJECTURE,
        confidence="low",
        upgrade_required=True,
    )
    gene = lambda value: IdeaGene(value=value, provenance=provenance)
    warning = (
        "seed_maturity: minimal IdeaSeed admitted; enrich presentation, validation detail, and evidence mapping before final selection."
    )
    if not evidence_refs:
        warning += " knowledge_origin=llm_parametric_knowledge; verification_required=true."
    return {
        "candidate_id": candidate_id,
        "version": int(payload.get("version") or 1),
        "status": CandidateStatus.ACTIVE.value,
        "maturity": CandidateMaturity.SEED.value,
        "genome": {
            "candidate_id": candidate_id,
            "version": int(payload.get("version") or 1),
            "generation_created": 0,
            "maturity": CandidateMaturity.SEED.value,
            "route": route_value,
            "parents": [],
            "problem": model_dump(gene(problem), mode="json"),
            "opportunity": model_dump(gene(opportunity), mode="json"),
            "challenged_assumption": model_dump(gene(assumption), mode="json"),
            "core_thesis": model_dump(gene(thesis), mode="json"),
            "mechanism": model_dump(gene(mechanism), mode="json"),
            "design_or_artifact": model_dump(gene(design), mode="json"),
            "contribution_package": model_dump(gene("; ".join(contribution)), mode="json"),
            "hypothesis_bundle": model_dump(gene(prediction), mode="json"),
            "validation_logic": model_dump(gene(validation), mode="json"),
            "boundary_conditions": model_dump(gene(boundary), mode="json"),
            "risks": model_dump(gene(risk), mode="json"),
        },
        "contributions": [
            {
                "contribution_id": f"{candidate_id}-C1",
                "statement": contribution[0],
                "contribution_type": str(payload.get("contribution_type") or "mechanism"),
                "what_changes_if_true": str(payload.get("what_changes_if_true") or contribution[0]),
            }
        ],
        "hypotheses": [
            {
                "hypothesis_id": f"{candidate_id}-H1",
                "statement": str(payload.get("draft_hypothesis") or prediction),
                "mechanism": mechanism,
                "observable_prediction": prediction,
                "discriminating_test": validation,
                "evidence_status": "proposed_not_verified",
            }
        ],
        "lineage": {
            "candidate_id": candidate_id,
            "parent_ids": [],
            "route": route_value,
            "created_by": "generator",
        },
        "warnings": [warning],
        "creative_context": creative_context,
    }


def _seed_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _seed_list_or_text(value: object) -> list[str]:
    if isinstance(value, list):
        result = [str(item).strip() for item in value if str(item).strip()]
        if result:
            return result
    text = str(value or "").strip()
    return [text] if text else []


def _seed_source_refs(value: object) -> list[SourceRef]:
    if not isinstance(value, list):
        return []
    refs: list[SourceRef] = []
    for item in value:
        if not isinstance(item, dict) or not str(item.get("source_path") or "").strip():
            continue
        refs.append(
            SourceRef(
                source_path=str(item["source_path"]),
                locator=str(item.get("locator") or ""),
                citation_key=str(item.get("citation_key") or ""),
                paper_id=str(item.get("paper_id") or ""),
                note=str(item.get("note") or ""),
            )
        )
    return refs


def _seed_knowledge_origin(value: object, *, has_workspace_refs: bool) -> str:
    """Canonicalize an epistemic label without promoting an idea to evidence."""

    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "workspace": "workspace_evidence",
        "evidence": "workspace_evidence",
        "llm": "llm_parametric_knowledge",
        "parametric": "llm_parametric_knowledge",
        "analogy": "cross_domain_analogy",
        "cross_domain": "cross_domain_analogy",
        "cross_domain_bridge": "cross_domain_analogy",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"workspace_evidence", "llm_parametric_knowledge", "cross_domain_analogy", "mixed"}:
        return normalized
    return "mixed" if has_workspace_refs else "llm_parametric_knowledge"


def _seed_reading_levels(value: object, refs: list[SourceRef]) -> list[ReadingLevel]:
    if isinstance(value, list):
        levels: list[ReadingLevel] = []
        for item in value:
            try:
                levels.append(ReadingLevel(str(item)))
            except ValueError:
                continue
        if levels:
            return list(dict.fromkeys(levels))
    return [] if refs else [ReadingLevel.BRAINSTORM]


def _parse_json_object(content: str, *, array_field: str | None = None) -> dict[str, Any]:
    """Tolerantly recover a role envelope before retaining strict diagnostics.

    This only repairs presentation differences (such as a Markdown fence,
    YAML envelope, known field alias, or trailing comma).  It never fills
    scientific content; typed role parsers still enforce evidence, lineage and
    semantic contracts after this boundary.
    """

    from .response_recovery import recover_t4_mapping

    recovered = recover_t4_mapping(content, array_field=array_field)
    if recovered.usable_content and recovered.payload is not None:
        return recovered.payload
    raise T4RoleResponseFormatError(
        "Model response did not contain a safely parseable JSON object or YAML mapping.",
        content=content,
    )


def _parse_opportunity_response(data: dict[str, Any]) -> list[OpportunityQuery]:
    """Recover common planner aliases before validating an Opportunity Map.

    Opportunity planning has no authority to certify evidence.  It is therefore
    safe to normalize an unambiguous field name such as ``category`` into
    ``type`` or ``summary`` into ``one_line_summary``.  This function never
    supplies research content: an absent question, reason, or type remains a
    typed validation error and follows the bounded semantic-repair path.
    """

    raw = next(
        (
            data.get(key)
            for key in ("opportunities", "opportunity_map", "research_questions", "questions")
            if isinstance(data.get(key), list)
        ),
        None,
    )
    if not isinstance(raw, list):
        raise ValueError("T4 Opportunity Planner must return an opportunities array")
    if not raw:
        raise ValueError("T4 Opportunity Planner returned an empty Opportunity Map")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("T4 Opportunity Planner returned a non-object opportunity")
    return [
        model_validate(OpportunityQuery, _normalize_opportunity_payload(item, ordinal=index))
        for index, item in enumerate(raw, start=1)
    ]


_OPPORTUNITY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "opportunity_id": ("id", "opportunityId", "question_id"),
    "type": ("opportunity_type", "opportunityType", "category", "kind"),
    "one_line_summary": ("summary", "opportunity_summary", "description", "title"),
    "question": ("research_question", "researchQuestion", "question_text"),
    "why_it_matters": ("importance", "rationale", "why", "significance"),
    "evidence_atom_ids": ("evidence_atoms", "evidence_ids", "supporting_atom_ids"),
    "compatible_routes": ("routes", "suggested_routes", "route_options"),
    "confidence": ("uncertainty", "certainty"),
    "knowledge_origin": ("origin", "idea_origin", "knowledge_source"),
    "verification_required": ("needs_verification", "requires_verification"),
    "conceptual_leap": ("creative_leap", "why_non_obvious"),
    "competing_explanations": ("alternative_explanations", "competing_hypotheses"),
}

_OPPORTUNITY_TYPES = frozenset(
    {
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
    }
)


def _normalize_opportunity_payload(payload: dict[str, Any], *, ordinal: int) -> dict[str, Any]:
    """Deterministically project aliases into the small Opportunity contract.

    An Opportunity ID is a controller-scoped planning handle rather than an
    external scientific identifier.  A missing one can therefore receive a
    stable ordinal ID. No missing source ID or evidence level is inferred
    here. A missing display summary may only reuse an already-supplied question
    verbatim, which is a lossless presentation projection rather than a new
    scientific assertion.
    """

    normalized = dict(payload)
    for canonical, aliases in _OPPORTUNITY_FIELD_ALIASES.items():
        if canonical in normalized and normalized[canonical] not in (None, "", [], {}):
            continue
        for alias in aliases:
            value = normalized.get(alias)
            if value not in (None, "", [], {}):
                normalized[canonical] = value
                break
    if not str(normalized.get("one_line_summary") or "").strip():
        question = str(normalized.get("question") or "").strip()
        if question:
            # The summary is an Opportunity display label. Copy the exact
            # supplied question rather than inventing a separate claim or
            # spending a bounded semantic-repair call on presentation only.
            normalized["one_line_summary"] = question
    if not str(normalized.get("opportunity_id") or "").strip():
        normalized["opportunity_id"] = f"O-PLANNER-{ordinal:02d}"

    raw_type = str(normalized.get("type") or "").strip().casefold()
    compact_type = re.sub(r"[^a-z0-9]+", "_", raw_type).strip("_")
    if compact_type in _OPPORTUNITY_TYPES:
        normalized["type"] = compact_type

    raw_confidence = str(normalized.get("confidence") or "").strip().casefold()
    if raw_confidence:
        if any(token in raw_confidence for token in ("low", "uncertain", "unknown", "弱", "低")):
            normalized["confidence"] = "low"
        elif any(token in raw_confidence for token in ("high", "strong", "高")):
            normalized["confidence"] = "high"
        elif any(token in raw_confidence for token in ("medium", "moderate", "中")):
            normalized["confidence"] = "medium"

    raw_origin = str(normalized.get("knowledge_origin") or "").strip().casefold().replace("-", "_").replace(" ", "_")
    origin_aliases = {
        "workspace": "workspace_evidence",
        "evidence": "workspace_evidence",
        "llm": "llm_parametric_knowledge",
        "parametric": "llm_parametric_knowledge",
        "analogy": "cross_domain_analogy",
        "cross_domain": "cross_domain_analogy",
        "cross_domain_bridge": "cross_domain_analogy",
    }
    if raw_origin:
        normalized["knowledge_origin"] = origin_aliases.get(raw_origin, raw_origin)

    for key in ("compatible_routes", "evidence_atom_ids", "competing_explanations"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = [value.strip()]

    # The planner model deliberately has ``extra=forbid``.  Extra display or
    # UI fields are not research evidence, so retaining them only to cause a
    # whole Opportunity Map failure is counterproductive.  Canonical fields
    # are still validated below and no missing semantic field is invented.
    allowed = {
        "schema_version",
        "opportunity_id",
        "type",
        "one_line_summary",
        "question",
        "why_it_matters",
        "evidence_atom_ids",
        "compatible_routes",
        "confidence",
        "knowledge_origin",
        "verification_required",
        "conceptual_leap",
        "competing_explanations",
        "priority_components",
        "priority_score",
    }
    return {key: value for key, value in normalized.items() if key in allowed}


def _score_semantic_repair_is_lossless(error: Exception) -> bool:
    """Return whether a malformed score can be normalized without reassessing it.

    ``ScoreDimensions`` owns a fixed rubric.  A value outside its range is not
    equivalent to a field alias or a nested envelope: converting it would
    invent a new assessment.  Keep that distinction at the LLM-role boundary
    so the controller uses its bounded replacement-score path for scale drift.
    Other parse failures may still be lossless shape differences and retain
    the existing semantic-repair attempt.
    """

    raw_errors = getattr(error, "errors", None)
    if not callable(raw_errors):
        return True
    try:
        issues = raw_errors()
    except Exception:
        return True
    if not isinstance(issues, list):
        return True
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        location = issue.get("loc")
        is_core_score = isinstance(location, tuple) and len(location) >= 2 and location[0] == "scores"
        if is_core_score and str(issue.get("type") or "") in {
            "less_than_equal",
            "greater_than_equal",
            "finite_number",
        }:
            return False
    return True


def _parse_score_response(
    data: dict[str, Any],
    *,
    target_profile: TargetProfile | None = None,
    scoring_batch_id: str | None = None,
    blind: bool | None = None,
) -> list[ScoreReport]:
    """Validate a score envelope while keeping diagnostics non-blocking.

    A valid independent score requires only an identifier, controller-owned
    batch/blind envelope, and the three formal numeric core dimensions.
    Rationale, evidence/validation observations, Profile Fit, upside, and
    legacy Gate1 comparison fields remain visible diagnostics. They cannot
    invalidate a Candidate or trigger an all-or-nothing score batch retry.
    """

    raw = data.get("scores")
    if not isinstance(raw, list):
        raise ValueError("T4 Scoring Agent must return a scores array")
    if not raw:
        raise ValueError("T4 Scoring Agent returned an empty score batch")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("T4 Scoring Agent returned a non-object score")
    reports: list[ScoreReport] = []
    for item in raw:
        report = model_validate(
            ScoreReport,
            _normalize_score_report_payload(
                item,
                target_profile=target_profile,
                scoring_batch_id=scoring_batch_id,
                blind=blind,
            ),
        )
        # ``overall_readiness`` is a compatibility field whose value is always
        # derived from exactly the three formal dimensions.  No provider value
        # for readiness, evidence, validation, or profile fit can override it.
        reports.append(report.model_copy(update={"overall_readiness": derive_overall_score(report.scores, target_profile)}))
    return reports


_SCORE_REPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "candidate_id": ("id", "candidateId", "idea_id"),
    "scoring_batch_id": ("batch_id", "batchId", "scoringBatchId"),
    "overall_readiness": ("readiness", "overall_score", "overallScore"),
    "score_uncertainty": ("uncertainty", "score_confidence_uncertainty"),
    "dominant_strength": ("strength", "main_strength"),
    "dominant_bottleneck": ("bottleneck", "main_bottleneck", "main_risk"),
    "compatibility_scores": ("legacy_scores", "gate1_scores"),
    "compatibility_rationales": ("legacy_rationales", "gate1_rationales"),
    "profile_fit": ("profile", "profile_assessment"),
    "scientific_upside": ("idea_potential", "research_upside", "upside"),
    "scientific_upside_rationale": ("upside_rationale", "idea_potential_rationale"),
    "evolution_potential": ("evolution_readiness", "mutation_potential", "growth_potential"),
    "recommended_crossover_role": ("crossover_role", "crossover_recommendation"),
    "wildcard_recommended": ("preserve_as_wildcard", "high_upside_wildcard"),
    "wildcard_rationale": ("wildcard_reason", "preservation_rationale"),
}
_SCORE_DIMENSION_KEYS = {
    "research_value",
    "mechanism_integrity",
    "contribution_distinctiveness",
}
_SCORE_DIMENSION_ORDER = (
    "research_value",
    "mechanism_integrity",
    "contribution_distinctiveness",
)
_SCORE_DIMENSION_CONTAINER_ALIASES = (
    "core_scores",
    "score_dimensions",
    "dimension_scores",
)
_SCORE_NESTED_REPORT_FIELDS = {
    "overall_readiness",
    "score_uncertainty",
    "rationales",
    "dominant_strength",
    "dominant_bottleneck",
    "preserve_genes",
    "modify_genes",
    "recommended_operators",
    "high_upside",
    "uncertain",
    "compatibility_scores",
    "compatibility_rationales",
    "profile_fit",
    "scientific_upside",
    "scientific_upside_rationale",
    "evolution_potential",
    "recommended_crossover_role",
    "wildcard_recommended",
    "wildcard_rationale",
    "diagnostics",
    "rationale_missing",
    "diagnostic_warnings",
}
_SCORE_REPORT_ALLOWED_FIELDS = {
    "schema_version",
    "candidate_id",
    "scoring_batch_id",
    "rubric_version",
    "blind",
    "scores",
    "overall_readiness",
    "score_uncertainty",
    "rationales",
    "dominant_strength",
    "dominant_bottleneck",
    "preserve_genes",
    "modify_genes",
    "recommended_operators",
    "high_upside",
    "uncertain",
    "compatibility_scores",
    "compatibility_rationales",
    "profile_fit",
    "scientific_upside",
    "scientific_upside_rationale",
    "evolution_potential",
    "recommended_crossover_role",
    "wildcard_recommended",
    "wildcard_rationale",
    "diagnostics",
    "rationale_missing",
    "diagnostic_warnings",
}


def _normalize_score_report_payload(
    payload: dict[str, Any],
    *,
    target_profile: TargetProfile | None,
    scoring_batch_id: str | None,
    blind: bool | None,
) -> dict[str, Any]:
    """Apply envelope-only repair without creating score content.

    Older scorers may nest report fields inside ``scores`` or include retired
    evidence and validation numbers. The former are moved to report level; the
    latter are retained as legacy diagnostics rather than becoming required
    fourth and fifth dimensions.
    """

    normalized = dict(payload)
    for canonical, aliases in _SCORE_REPORT_ALIASES.items():
        if normalized.get(canonical) not in (None, "", [], {}):
            continue
        for alias in aliases:
            value = normalized.get(alias)
            if value not in (None, "", [], {}):
                normalized[canonical] = value
                break

    raw_dimensions = normalized.get("scores")
    if not isinstance(raw_dimensions, dict):
        for alias in _SCORE_DIMENSION_CONTAINER_ALIASES:
            candidate = normalized.get(alias)
            if isinstance(candidate, dict):
                raw_dimensions = dict(candidate)
                normalized["scores"] = raw_dimensions
                break
    if isinstance(raw_dimensions, (list, tuple)) and len(raw_dimensions) == len(_SCORE_DIMENSION_ORDER):
        # The scorer prompt lists the three core dimensions in this exact
        # order. A fixed-length scalar sequence therefore retains every
        # assessment without ranking, scaling, or inventing a field. Values
        # still pass through ScoreDimensions, which rejects non-numeric or
        # out-of-range entries rather than silently changing them.
        raw_dimensions = {
            dimension: value
            for dimension, value in zip(_SCORE_DIMENSION_ORDER, raw_dimensions)
        }
        normalized["scores"] = raw_dimensions
        diagnostics = dict(normalized.get("diagnostics") or {})
        warnings = list(diagnostics.get("warnings") or [])
        if "score_dimension_sequence_normalized" not in warnings:
            warnings.append("score_dimension_sequence_normalized")
        diagnostics["warnings"] = warnings
        normalized["diagnostics"] = diagnostics
    if not isinstance(raw_dimensions, dict) and all(
        key in normalized and normalized.get(key) not in (None, "")
        for key in _SCORE_DIMENSION_ORDER
    ):
        raw_dimensions = {key: normalized[key] for key in _SCORE_DIMENSION_ORDER}
        normalized["scores"] = raw_dimensions
    if isinstance(raw_dimensions, dict):
        dimensions = dict(raw_dimensions)
        for field in _SCORE_NESTED_REPORT_FIELDS:
            if normalized.get(field) in (None, "", [], {}) and dimensions.get(field) not in (None, "", [], {}):
                normalized[field] = dimensions[field]
            dimensions.pop(field, None)
        diagnostics = dict(normalized.get("diagnostics") or {})
        legacy_values = dict(diagnostics.get("legacy_numeric_values") or {})
        for legacy_key in ("evidence_calibration", "validation_tractability", "validation_feasibility"):
            value = dimensions.pop(legacy_key, None)
            _preserve_legacy_score_diagnostic(diagnostics, legacy_values, legacy_key, value)
        diagnostics["legacy_numeric_values"] = legacy_values
        normalized["diagnostics"] = diagnostics
        # Do not carry report-level explanations or UI values into the strict
        # three-dimension object. Missing core dimensions remain a typed
        # validation error and trigger the bounded semantic repair path.
        normalized["scores"] = {key: value for key, value in dimensions.items() if key in _SCORE_DIMENSION_KEYS}

    diagnostics = dict(normalized.get("diagnostics") or {})
    # Some providers group all qualitative observations under ``diagnostics``.
    # These names already have typed report-level homes, so moving them is a
    # lossless envelope repair and prevents StrictModel extra-field rejection.
    for field in ("score_uncertainty", "scientific_upside", "evolution_potential"):
        if normalized.get(field) in (None, "") and diagnostics.get(field) not in (None, ""):
            normalized[field] = diagnostics.pop(field)
    legacy_values = dict(diagnostics.get("legacy_numeric_values") or {})
    for legacy_key in ("evidence_calibration", "validation_tractability", "validation_feasibility"):
        _preserve_legacy_score_diagnostic(diagnostics, legacy_values, legacy_key, normalized.pop(legacy_key, None))
    diagnostics["legacy_numeric_values"] = legacy_values
    normalized["diagnostics"] = diagnostics

    if scoring_batch_id:
        normalized["scoring_batch_id"] = scoring_batch_id
    if blind is not None:
        normalized["blind"] = bool(blind)

    profile = normalized.get("profile_fit")
    if isinstance(profile, dict):
        profile = dict(profile)
        if target_profile is not None and not str(profile.get("profile_type") or "").strip():
            profile["profile_type"] = target_profile.profile_type
        normalized["profile_fit"] = profile

    # Unknown presentation/UI fields cannot improve an assessment and are
    # safely ignored. The three core scientific dimensions still validate.
    return {key: value for key, value in normalized.items() if key in _SCORE_REPORT_ALLOWED_FIELDS}


def _preserve_legacy_score_diagnostic(
    diagnostics: dict[str, object],
    legacy_values: dict[str, object],
    name: str,
    value: object,
) -> None:
    """Retain an old evidence/validation value without treating it as a score."""

    if value in (None, "", [], {}):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        legacy_values.setdefault(name, float(value))
        return
    if isinstance(value, str):
        try:
            legacy_values.setdefault(name, float(value.strip()))
            return
        except ValueError:
            pass
    if name == "evidence_calibration" and not diagnostics.get("evidence_calibration"):
        diagnostics["evidence_calibration"] = str(value)
    elif name in {"validation_tractability", "validation_feasibility"} and not diagnostics.get("validation_feasibility"):
        diagnostics["validation_feasibility"] = str(value)


def _parse_offspring_response(
    *,
    data: dict[str, Any],
    plans: list[EvolutionPlan],
    parents: list[CandidateDossier],
) -> list[CandidateDossier] | EvolutionPlanDeferral:
    """Validate a Child, or a bounded no-Child decision, against Plans.

    Controller execution is currently plan-scoped.  A standalone, typed
    deferral is intentionally allowed only for that one plan so an LLM cannot
    silently drop part of a multi-plan request.
    """

    raw = data.get("children")
    if not isinstance(raw, list):
        raise ValueError("LLM Evolver must return a children array")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("LLM Evolver returned a non-object Child")
    children = [model_validate(CandidateDossier, _normalize_candidate_dossier_payload(item)) for item in raw]
    raw_deferrals = data.get("deferred_plans", [])
    if not isinstance(raw_deferrals, list) or any(not isinstance(item, dict) for item in raw_deferrals):
        raise ValueError("LLM Evolver deferred_plans must be an array of objects")
    deferrals = [model_validate(EvolutionPlanDeferral, item) for item in raw_deferrals]
    if deferrals:
        if children or len(plans) != 1 or len(deferrals) != 1:
            raise ValueError("LLM Evolver may return a deferral only instead of one explicitly supplied Plan")
        deferral = deferrals[0]
        plan = plans[0]
        if deferral.plan_id != plan.plan_id:
            raise ValueError("LLM Evolver deferral does not reference the approved Evolution Plan")
        if deferral.status == "incompatible" and plan.child_type != "crossover":
            raise ValueError("Only a Crossover Plan may be marked incompatible")
        return deferral
    by_id = {candidate.candidate_id: candidate for candidate in parents}
    expected_by_parent_set = {tuple(plan.parent_ids): _expected_child_id(plan) for plan in plans}
    if len(children) != len(plans):
        raise ValueError(
            f"LLM Evolver returned incomplete Plan coverage; expected={len(plans)} Children, got={len(children)}"
        )
    seen_parent_sets: set[tuple[str, ...]] = set()
    for child in children:
        parent_set = tuple(child.lineage.parent_ids)
        expected_id = expected_by_parent_set.get(parent_set)
        if expected_id is None:
            raise ValueError(f"LLM Evolver returned child {child.candidate_id} without an approved parent set")
        if parent_set in seen_parent_sets:
            raise ValueError(f"LLM Evolver returned multiple Children for one Evolution Plan: {parent_set}")
        if child.candidate_id in by_id:
            raise ValueError("LLM Evolver attempted to overwrite a parent candidate")
        if child.candidate_id != expected_id:
            raise ValueError(
                f"LLM Evolver returned child ID {child.candidate_id} for {parent_set}; expected new Child ID {expected_id}"
            )
        _require_evolved_candidate_scientific_core(child)
        seen_parent_sets.add(parent_set)
    return children


_SOURCE_REF_FIELDS = frozenset({"source_path", "locator", "citation_key", "paper_id", "note"})
_IDEA_GENE_FIELDS = frozenset({"value", "provenance"})
_GENOME_GENE_NAMES = (
    "problem",
    "opportunity",
    "challenged_assumption",
    "core_thesis",
    "mechanism",
    "design_or_artifact",
    "contribution_package",
    "hypothesis_bundle",
    "validation_logic",
    "boundary_conditions",
    "risks",
)


def _normalize_candidate_dossier_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize repeated EvidenceAtom display fields before strict validation.

    The model receives both the Candidate and Evidence schemas.  It can copy
    display-only fields such as ``reading_level`` into a nested ``SourceRef``
    even though those fields belong to the enclosing ``GeneProvenance``.  A
    SourceRef is solely a stable citation pointer, so stripping its duplicate
    metadata does not upgrade a Claim or weaken Evidence Permission checks.
    """

    normalized = dict(payload)
    artifacts = normalized.pop("artifacts", None)
    if "artifact_paths" not in normalized and isinstance(artifacts, list):
        normalized["artifact_paths"] = [str(item) for item in artifacts if isinstance(item, str)]

    # A partially emitted legacy presentation is not scientific source data.
    # Retaining it verbatim would make Pydantic reject a coherent genome before
    # the Final Card Compiler can provide the current LLM-authored human view.
    # Drop only an incomplete compatibility payload and leave all canonical
    # Candidate content untouched. A durable warning makes the later LLM
    # enrichment need observable without manufacturing replacement prose.
    presentation = normalized.get("presentation")
    if isinstance(presentation, dict):
        # CandidatePresentation is a legacy display enrichment, not part of
        # the scientific Candidate contract.  Checking only a small set of
        # top-level keys here was not enough: a model could emit all of those
        # keys while omitting a nested legacy `gate1_card` field (or use a
        # placeholder) and Pydantic would then reject the entire otherwise
        # coherent Seed/Child before the dedicated Final Card LLM had a chance
        # to write the researcher-facing explanation.  Validate this isolated
        # compatibility payload exactly as it will be read; if it is partial,
        # retain the canonical genome and write a durable enrichment need.
        # This never manufactures scientific or decision prose.
        try:
            model_validate(CandidatePresentation, presentation)
        except (TypeError, ValueError):
            normalized.pop("presentation", None)
            existing_warnings = normalized.get("warnings")
            warnings = [str(item) for item in existing_warnings] if isinstance(existing_warnings, list) else []
            warnings.append(
                "presentation_enrichment_required: partial legacy CandidatePresentation was retained as an LLM Final Card enrichment need."
            )
            normalized["warnings"] = list(dict.fromkeys(warnings))

    genome = normalized.get("genome")
    if not isinstance(genome, dict):
        return normalized
    normalized_genome = dict(genome)
    for gene_name in _GENOME_GENE_NAMES:
        gene = normalized_genome.get(gene_name)
        if not isinstance(gene, dict):
            continue
        normalized_gene = {key: value for key, value in gene.items() if key in _IDEA_GENE_FIELDS}
        provenance = normalized_gene.get("provenance")
        if not isinstance(provenance, dict):
            normalized_genome[gene_name] = normalized_gene
            continue
        normalized_provenance = dict(provenance)
        refs = normalized_provenance.get("source_refs")
        if isinstance(refs, list):
            normalized_provenance["source_refs"] = [
                {key: value for key, value in ref.items() if key in _SOURCE_REF_FIELDS}
                if isinstance(ref, dict)
                else ref
                for ref in refs
            ]
        normalized_gene["provenance"] = normalized_provenance
        normalized_genome[gene_name] = normalized_gene
    normalized["genome"] = normalized_genome
    return normalized


def _normalize_route_status(raw_status: Any, *, candidate_count: int, unsupported_reason: str) -> str:
    """Keep Route status separate from a Candidate lifecycle status.

    ``active`` is a valid Candidate status, not a RouteGenerationResult status.
    A response containing valid Candidates therefore represents a supported
    Route.  This avoids discarding a usable Candidate solely because a provider
    reused the lifecycle vocabulary at the wrapper level.
    """

    status = str(raw_status or "").strip().lower()
    if status in {"supported", "unsupported", "partial"}:
        return status
    if candidate_count:
        return "supported"
    if unsupported_reason.strip():
        return "unsupported"
    return "partial"


def _normalize_optional_bridge_reviews(value: object) -> list[BridgeCoverageEntry]:
    """Recover common optional Bridge-review surface forms without losing Seeds.

    Cross-domain review prose is enrichment, whereas the Candidate is the
    scientific object that enters the Population. Some providers naturally use
    values such as ``all``/``active``/``covered_by_seeds`` when describing a
    review's display state. These are unambiguous presentation aliases, not
    additional evidence. Canonicalize them where possible and omit only an
    unrecoverable optional review; never let it invalidate otherwise usable
    Cross-domain Ideas.
    """

    if not isinstance(value, list):
        return []
    reviews: list[BridgeCoverageEntry] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("decision_summary", item.get("summary") or item.get("rationale") or "")
        item.setdefault("escape_reason", item.get("reason") or item.get("decision_summary") or "")
        item.setdefault(
            "falsification_or_kill_criteria",
            item.get("kill_criteria") or item.get("falsification") or "",
        )
        item.setdefault("can_revisit_if", item.get("revisit_if") or item.get("revisit_condition") or "")
        item["visible_to_gate"] = _normalize_bridge_visibility(item.get("visible_to_gate"))
        raw_status = str(item.get("escape_status") or item.get("coverage_status") or item.get("status") or "").strip()
        status_aliases = {
            "active": "not_needed_selected",
            "all": "not_needed_selected",
            "covered": "not_needed_selected",
            "covered_by_seed": "not_needed_selected",
            "covered_by_seeds": "not_needed_selected",
            "not_needed": "not_needed_selected",
            "not-needed": "not_needed_selected",
        }
        item["escape_status"] = status_aliases.get(raw_status.casefold(), raw_status)
        candidate_ids = item.get("candidate_ids")
        if isinstance(candidate_ids, str):
            item["candidate_ids"] = [] if candidate_ids.strip().casefold() in {"all", "active", "none"} else [candidate_ids]
        elif isinstance(candidate_ids, list):
            item["candidate_ids"] = [
                candidate_id
                for candidate_id in candidate_ids
                if str(candidate_id).strip().casefold() not in {"all", "active", "none"}
            ]
        item = {
            key: item[key]
            for key in (
                "bridge_id",
                "candidate_ids",
                "visible_to_gate",
                "decision_summary",
                "escape_status",
                "escape_reason",
                "falsification_or_kill_criteria",
                "can_revisit_if",
            )
            if key in item
        }
        try:
            reviews.append(model_validate(BridgeCoverageEntry, item))
        except (TypeError, ValueError):
            # No arbitrary scientific choice can repair an incomplete optional
            # review. Its absence is later made visible by Gate1 projection.
            continue
    return reviews


def _normalize_bridge_visibility(value: object) -> object:
    """Map only unambiguous Bridge visibility aliases to a bool."""

    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"all", "active", "visible", "yes", "true", "show"}:
            return True
        if normalized in {"none", "hidden", "no", "false", "hide"}:
            return False
    return value
