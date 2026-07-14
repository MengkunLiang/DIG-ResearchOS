"""Controller-orchestrated, artifact-first T4 evolution.

The controller has no embedded research-domain knowledge. Semantic work is
injected through role-separated ports; deterministic code owns scheduling,
evidence policy, IDs, fingerprints, artifact persistence, contracts, lineage,
and population survival.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from ..pydantic_compat import model_dump
from .config import T4EvolutionSettings
from .evidence import build_idea_evidence_index
from .models import (
    CandidateDossier,
    CandidateStatus,
    CrossoverCompatibilityDecision,
    EvolutionPhase,
    IdeaFamily,
    OpportunityQuery,
    PopulationSnapshot,
    PortfolioSelection,
    RoundArtifact,
    RouteGenerationResult,
    ScoreReport,
    T4InternalState,
    T4RunConfig,
)
from .population import (
    build_idea_families,
    compile_crossover_plans,
    compile_mutation_plans,
    compute_gene_delta,
    detect_complexity_inflation,
    select_evolution_parents,
    select_portfolio,
    select_survivors,
    validate_idea_contract,
)
from .state import T4ArtifactStore, run_config_fingerprint, t4_input_fingerprint


class IdeaGeneratorPort(Protocol):
    """The Generator creates route-scoped candidates but never scores them."""

    async def plan_opportunities(
        self,
        *,
        evidence_summary: dict[str, Any],
        run_config: T4RunConfig,
    ) -> list[OpportunityQuery]: ...

    async def generate_route(
        self,
        *,
        route: str,
        opportunities: list[OpportunityQuery],
        evidence_bundle: dict[str, Any],
        quota: int,
        repair: bool,
    ) -> list[CandidateDossier] | RouteGenerationResult: ...


class IdeaScoringPort(Protocol):
    """The Scorer only returns scores/diagnosis or pair compatibility."""

    async def score_population(
        self,
        *,
        candidates: list[CandidateDossier],
        scoring_batch_id: str,
        blind: bool,
    ) -> list[ScoreReport]: ...

    async def review_crossover_pairs(
        self,
        *,
        candidates: list[CandidateDossier],
        pairs: list[tuple[str, str]],
    ) -> list[CrossoverCompatibilityDecision]: ...


class IdeaEvolverPort(Protocol):
    """The Evolver creates plan-bounded children but cannot select survivors."""

    async def generate_offspring(
        self,
        *,
        plans: list,
        parents: list[CandidateDossier],
    ) -> list[CandidateDossier]: ...


@dataclass(frozen=True)
class EvolutionRunResult:
    population: PopulationSnapshot
    portfolio: PortfolioSelection
    state: T4InternalState
    route_results: list[RouteGenerationResult]


class IdeaEvolutionController:
    """Run P0 formation and an optional full P0 -> P1 transition."""

    def __init__(
        self,
        *,
        workspace_dir,
        settings: T4EvolutionSettings,
        generator: IdeaGeneratorPort,
        scorer: IdeaScoringPort,
        evolver: IdeaEvolverPort,
    ) -> None:
        self.store = T4ArtifactStore(workspace_dir)
        self.settings = settings
        self.generator = generator
        self.scorer = scorer
        self.evolver = evolver

    async def run(self, run_config: T4RunConfig) -> EvolutionRunResult:
        """Run a fresh or resumable evolution from the configured input state."""

        input_fp = t4_input_fingerprint(self.store.workspace_dir)
        config_fp = run_config_fingerprint(run_config)
        self.store.write_run_config(run_config)
        p0, p0_dossiers, route_results = await self._ensure_p0(run_config, input_fp, config_fp)
        if run_config.rounds == 0:
            scores = await self._score(p0_dossiers, "SB-P0")
            families = self._load_or_build_families(p0_dossiers, generation=0)
            portfolio = select_portfolio(p0, scores, families, maximum=run_config.final_top_k)
            self.store.write_json("ideation/portfolio.json", model_dump(portfolio, mode="json"))
            state = self._set_waiting_state(p0, run_config, display_ids=_portfolio_ids(portfolio), completed_rounds=0)
            return EvolutionRunResult(population=p0, portfolio=portfolio, state=state, route_results=route_results)
        return await self._run_one_evolution_round(
            p0=p0,
            p0_dossiers=p0_dossiers,
            route_results=route_results,
            run_config=run_config,
        )

    async def _ensure_p0(
        self,
        run_config: T4RunConfig,
        input_fp: str,
        config_fp: str,
    ) -> tuple[PopulationSnapshot, list[CandidateDossier], list[RouteGenerationResult]]:
        if self.store.phase_is_complete(
            phase=EvolutionPhase.FORMATION,
            generation=0,
            input_fingerprint=input_fp,
            run_config_fingerprint=config_fp,
        ):
            population = self.store.read_population("P0")
            return population, self._load_dossiers(population.active_candidate_ids), self._load_route_results()

        evidence = build_idea_evidence_index(self.store.workspace_dir, store=self.store)
        opportunities = await self.generator.plan_opportunities(
            evidence_summary=evidence["summary"],
            run_config=run_config,
        )
        self._validate_opportunities(opportunities)
        self.store.write_json(
            "ideation/evidence/opportunities.json",
            {"schema_version": "1.0.0", "semantics": "t4_opportunity_map", "opportunities": [model_dump(item, mode="json") for item in opportunities]},
        )
        route_specs = {item.route: item for item in self.settings.route_quotas}
        requested_routes = [route for route, quota in run_config.route_quotas.items() if quota > 0 and route in route_specs]
        generated = await asyncio.gather(
            *[
                self._generate_route(
                    route=route,
                    quota=min(run_config.route_quotas[route], route_specs[route].maximum),
                    opportunities=opportunities,
                    evidence_summary=evidence["summary"],
                    required=route_specs[route].required,
                )
                for route in requested_routes
            ]
        )
        route_results = [result for result, _candidates in generated]
        dossiers = [candidate for _result, candidates in generated for candidate in candidates]
        self._validate_p0_dossiers(dossiers, route_results, run_config)
        for dossier in dossiers:
            self.store.write_candidate(dossier)
        families = self._load_or_build_families(dossiers, generation=0)
        p0 = PopulationSnapshot(
            population_id="P0",
            generation=0,
            input_fingerprint=input_fp,
            run_config_fingerprint=config_fp,
            active_candidate_ids=[item.candidate_id for item in dossiers],
            family_ids=[item.family_id for item in families],
        )
        self.store.write_population(p0)
        self.store.write_json(
            "ideation/evolution/routes/round_0.json",
            {"schema_version": "1.0.0", "semantics": "t4_route_generation", "routes": [model_dump(item, mode="json") for item in route_results]},
        )
        self.store.initialize_state(config=run_config, population=p0)
        self.store.write_phase_marker(
            phase=EvolutionPhase.FORMATION,
            generation=0,
            input_fingerprint=input_fp,
            run_config_fingerprint=config_fp,
            artifact_paths=[
                "ideation/populations/P0.json",
                "ideation/evidence/evidence_index.jsonl",
                "ideation/evidence/evidence_index_summary.json",
                "ideation/evidence/opportunities.json",
                "ideation/evolution/routes/round_0.json",
            ],
        )
        return p0, dossiers, route_results

    async def _generate_route(
        self,
        *,
        route: str,
        quota: int,
        opportunities: list[OpportunityQuery],
        evidence_summary: dict[str, Any],
        required: bool,
    ) -> tuple[RouteGenerationResult, list[CandidateDossier]]:
        evidence_bundle = {
            "route": route,
            "opportunity_ids": [item.opportunity_id for item in opportunities if route in item.compatible_routes],
            "evidence_summary": evidence_summary,
        }
        output = await self.generator.generate_route(
            route=route,
            opportunities=opportunities,
            evidence_bundle=evidence_bundle,
            quota=quota,
            repair=False,
        )
        result, candidates = self._normalize_route_output(route, output)
        if required and len(candidates) < quota:
            repaired = await self.generator.generate_route(
                route=route,
                opportunities=opportunities,
                evidence_bundle=evidence_bundle,
                quota=quota,
                repair=True,
            )
            result, candidates = self._normalize_route_output(route, repaired, repaired_once=True)
        return result, candidates

    async def _run_one_evolution_round(
        self,
        *,
        p0: PopulationSnapshot,
        p0_dossiers: list[CandidateDossier],
        route_results: list[RouteGenerationResult],
        run_config: T4RunConfig,
    ) -> EvolutionRunResult:
        scores_p0 = await self._score(p0_dossiers, "SB-P0")
        self._write_scores("P0", scores_p0)
        families_p0 = self._load_or_build_families(p0_dossiers, generation=0)
        parent_ids, parent_reasons = select_evolution_parents(
            p0_dossiers, scores_p0, families_p0, maximum=self.settings.offspring.mutation_maximum
        )
        mutation_plans = compile_mutation_plans(
            parent_ids,
            scores_p0,
            round_number=1,
            limit=self.settings.offspring.mutation_maximum,
        )
        crossover_decisions: list[CrossoverCompatibilityDecision] = []
        if run_config.allow_crossover and self.settings.offspring.crossover_maximum:
            pairs = _candidate_pairs(parent_ids)
            if pairs:
                crossover_decisions = await self.scorer.review_crossover_pairs(candidates=_copies(p0_dossiers), pairs=pairs[:3])
        crossover_plans = compile_crossover_plans(
            crossover_decisions,
            round_number=1,
            limit=min(run_config.max_crossover_children, self.settings.offspring.crossover_maximum),
        )
        plans = mutation_plans + crossover_plans
        self.store.write_json(
            "ideation/evolution/plans/round_1.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_evolution_plan_batch",
                "parent_selection": {"ids": parent_ids, "reasons": parent_reasons},
                "plans": [model_dump(item, mode="json") for item in plans],
                "crossover_decisions": [model_dump(item, mode="json") for item in crossover_decisions],
            },
        )
        parent_lookup = {item.candidate_id: item for item in p0_dossiers}
        children = await self.evolver.generate_offspring(plans=plans, parents=_copies(p0_dossiers))
        self._validate_children(children, plans, parent_lookup)
        for child in children:
            self.store.write_candidate(child)
        union = [*p0_dossiers, *children]
        union_scores = await self._score(union, "SB-U1")
        self._write_scores("U1", union_scores)
        contracts = [validate_idea_contract(item) for item in union]
        deltas = []
        complexity = []
        plan_by_parent = {tuple(plan.parent_ids): plan for plan in plans}
        for child in children:
            plan = plan_by_parent.get(tuple(child.lineage.parent_ids))
            if plan is None:
                raise ValueError(f"child {child.candidate_id} lacks a matching evolution plan")
            parents = [parent_lookup[parent_id] for parent_id in plan.parent_ids]
            deltas.append(compute_gene_delta(child, parents, plan))
            complexity.append(
                detect_complexity_inflation(
                    child,
                    parents,
                    ratio_limit=self.settings.complexity_growth_ratio_limit,
                )
            )
        families_p1 = self._load_or_build_families(union, generation=1)
        survivor_ids, archived_ids, survival = select_survivors(
            union,
            union_scores,
            contracts,
            deltas,
            complexity,
            families_p1,
            target_size=run_config.active_population_size,
        )
        p1 = PopulationSnapshot(
            population_id="P1",
            generation=1,
            input_fingerprint=p0.input_fingerprint,
            run_config_fingerprint=p0.run_config_fingerprint,
            active_candidate_ids=survivor_ids,
            family_ids=[item.family_id for item in families_p1],
            elite_candidate_ids=survivor_ids[:1],
            archived_candidate_ids=archived_ids,
            created_from_round=1,
        )
        self.store.write_population(p1)
        self.store.write_round(
            RoundArtifact(
                round=1,
                input_population_id="P0",
                output_population_id="P1",
                input_fingerprint=p0.input_fingerprint,
                run_config_fingerprint=p0.run_config_fingerprint,
                parent_ids=parent_ids,
                offspring_ids=[item.candidate_id for item in children],
                survivor_ids=survivor_ids,
                archived_ids=archived_ids,
                plan_ids=[item.plan_id for item in plans],
                score_batch_ids=["SB-P0", "SB-U1"],
                completion_status="completed",
            )
        )
        self.store.write_json(
            "ideation/evolution/round_1_diagnostics.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_evolution_diagnostics",
                "contracts": [model_dump(item, mode="json") for item in contracts],
                "gene_deltas": [model_dump(item, mode="json") for item in deltas],
                "complexity": [model_dump(item, mode="json") for item in complexity],
                "survival": survival,
            },
        )
        portfolio = select_portfolio(p1, union_scores, families_p1, maximum=run_config.final_top_k)
        self.store.write_json("ideation/portfolio.json", model_dump(portfolio, mode="json"))
        state = self._set_waiting_state(p1, run_config, display_ids=_portfolio_ids(portfolio), completed_rounds=1)
        self.store.write_phase_marker(
            phase=EvolutionPhase.SURVIVAL,
            generation=1,
            input_fingerprint=p1.input_fingerprint,
            run_config_fingerprint=p1.run_config_fingerprint,
            artifact_paths=[
                "ideation/populations/P1.json",
                "ideation/evolution/round_1.json",
                "ideation/evolution/round_1_diagnostics.json",
                "ideation/portfolio.json",
            ],
        )
        return EvolutionRunResult(population=p1, portfolio=portfolio, state=state, route_results=route_results)

    async def _score(self, candidates: list[CandidateDossier], batch_id: str) -> list[ScoreReport]:
        reports = await self.scorer.score_population(candidates=_copies(candidates), scoring_batch_id=batch_id, blind=True)
        expected = {item.candidate_id for item in candidates}
        actual = {item.candidate_id for item in reports}
        if expected != actual:
            raise ValueError(f"independent scoring must cover exactly the population; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
        if any(not report.blind for report in reports):
            raise ValueError("independent population scores must be blind")
        return reports

    def _write_scores(self, population_id: str, reports: list[ScoreReport]) -> None:
        self.store.write_json(
            f"ideation/scoring/{population_id}.json",
            {"schema_version": "1.0.0", "semantics": "t4_independent_score_batch", "scores": [model_dump(item, mode="json") for item in reports]},
        )

    def _load_or_build_families(self, dossiers: list[CandidateDossier], *, generation: int) -> list[IdeaFamily]:
        families = build_idea_families(
            [item.genome for item in dossiers],
            generation=generation,
            similarity_threshold=self.settings.family_similarity_threshold,
        )
        self.store.write_json(
            f"ideation/families/generation_{generation}.json",
            {"schema_version": "1.0.0", "semantics": "t4_idea_families", "families": [model_dump(item, mode="json") for item in families]},
        )
        return families

    def _set_waiting_state(
        self,
        population: PopulationSnapshot,
        run_config: T4RunConfig,
        *,
        display_ids: list[str],
        completed_rounds: int,
    ) -> T4InternalState:
        try:
            state = self.store.read_state()
            if state.current_population_id != population.population_id:
                state = self.store.activate_population(population.population_id, phase=EvolutionPhase.WAITING_HUMAN)
        except ValueError:
            state = self.store.initialize_state(config=run_config, population=population)
        updated = state.model_copy(
            update={
                "phase": EvolutionPhase.WAITING_HUMAN,
                "generation": population.generation,
                "completed_rounds": completed_rounds,
                "current_population_id": population.population_id,
                "display_candidate_ids": display_ids,
                "last_completed_artifact": f"ideation/populations/{population.population_id}.json",
                "generation_history": list(dict.fromkeys([*state.generation_history, population.population_id])),
            }
        )
        self.store.write_state(updated)
        return updated

    def _load_dossiers(self, candidate_ids: list[str]) -> list[CandidateDossier]:
        dossiers: list[CandidateDossier] = []
        for candidate_id in candidate_ids:
            matches = sorted((self.store.path("ideation/candidates")).glob(f"{candidate_id}.v*.json"))
            if not matches:
                raise ValueError(f"missing candidate dossier for {candidate_id}")
            dossiers.append(self.store.read_model(matches[-1].relative_to(self.store.workspace_dir), CandidateDossier))
        return dossiers

    def _load_route_results(self) -> list[RouteGenerationResult]:
        try:
            payload = self.store.read_model("ideation/evolution/routes/round_0.json", _LooseArtifact)
        except ValueError:
            return []
        raw = payload.payload.get("routes") if isinstance(payload.payload.get("routes"), list) else []
        return [RouteGenerationResult.model_validate(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _normalize_route_output(
        route: str,
        output: list[CandidateDossier] | RouteGenerationResult,
        *,
        repaired_once: bool = False,
    ) -> tuple[RouteGenerationResult, list[CandidateDossier]]:
        if isinstance(output, RouteGenerationResult):
            return output.model_copy(update={"repaired_once": output.repaired_once or repaired_once}), []
        candidates = list(output)
        return (
            RouteGenerationResult(
                route=route,
                status="supported" if candidates else "partial",
                candidate_ids=[item.candidate_id for item in candidates],
                repaired_once=repaired_once,
            ),
            candidates,
        )

    def _validate_opportunities(self, opportunities: list[OpportunityQuery]) -> None:
        if not self.settings.opportunity_minimum <= len(opportunities) <= self.settings.opportunity_maximum:
            raise ValueError("Opportunity Map count is outside the configured range")
        ids = [item.opportunity_id for item in opportunities]
        if len(set(ids)) != len(ids):
            raise ValueError("Opportunity Map contains duplicate IDs")

    def _validate_p0_dossiers(
        self,
        dossiers: list[CandidateDossier],
        route_results: list[RouteGenerationResult],
        config: T4RunConfig,
    ) -> None:
        if len(dossiers) > config.max_initial_population:
            raise ValueError("P0 exceeds configured maximum initial population")
        ids = [item.candidate_id for item in dossiers]
        if len(set(ids)) != len(ids):
            raise ValueError("P0 contains duplicate candidate IDs")
        result_by_route = {item.route: item for item in route_results}
        for route in ("evidence_routed_literature", "informed_brainstorm"):
            quota = config.route_quotas.get(route, 0)
            result = result_by_route.get(route)
            if quota and (result is None or result.status != "supported" or len(result.candidate_ids) < quota):
                raise ValueError(f"required route did not produce its configured seed quota: {route}")

    @staticmethod
    def _validate_children(children: list[CandidateDossier], plans: list, parents: dict[str, CandidateDossier]) -> None:
        plan_parent_sets = {tuple(plan.parent_ids) for plan in plans}
        child_ids = [item.candidate_id for item in children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("offspring contain duplicate candidate IDs")
        for child in children:
            if child.candidate_id in parents:
                raise ValueError("offspring must never overwrite a parent candidate")
            if tuple(child.lineage.parent_ids) not in plan_parent_sets:
                raise ValueError(f"offspring {child.candidate_id} does not match an approved evolution plan")


class _LooseArtifact:
    """Small adapter for typed reads of envelope objects with flexible content."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @classmethod
    def model_validate(cls, value: Any) -> "_LooseArtifact":
        if not isinstance(value, dict):
            raise ValueError("artifact must be an object")
        return cls(value)


def _copies(candidates: list[CandidateDossier]) -> list[CandidateDossier]:
    return [item.model_copy(deep=True) for item in candidates]


def _candidate_pairs(parent_ids: list[str]) -> list[tuple[str, str]]:
    return [(parent_ids[left], parent_ids[right]) for left in range(len(parent_ids)) for right in range(left + 1, len(parent_ids))]


def _portfolio_ids(portfolio: PortfolioSelection) -> list[str]:
    return [item for item in [portfolio.lead_id, *portfolio.alternative_ids, *portfolio.high_upside_ids] if item]
