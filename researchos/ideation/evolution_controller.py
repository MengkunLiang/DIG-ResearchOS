"""Controller-orchestrated, artifact-first T4 evolution.

The controller has no embedded research-domain knowledge. Semantic work is
injected through role-separated ports; deterministic code owns scheduling,
evidence policy, IDs, fingerprints, artifact persistence, contracts, lineage,
and population survival.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Any, Awaitable, Callable, Protocol

from ..pydantic_compat import model_dump
from .config import T4EvolutionSettings
from .evidence import build_idea_evidence_index
from .models import (
    CandidateDossier,
    CandidateStatus,
    CrossoverCompatibilityDecision,
    EvolutionOperator,
    EvolutionPlan,
    EvolutionPhase,
    GeneDonorMap,
    HumanCompositionCompatibility,
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
from .state import T4ArtifactStore, run_config_fingerprint, stable_fingerprint, t4_input_fingerprint


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
    ) -> list[CandidateDossier] | RouteGenerationResult | "RouteGenerationPayload": ...


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


EvolutionProgressCallback = Callable[[EvolutionPhase, str, dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class EvolutionRunResult:
    population: PopulationSnapshot
    portfolio: PortfolioSelection
    state: T4InternalState
    route_results: list[RouteGenerationResult]
    active_dossiers: list[CandidateDossier]
    active_scores: list[ScoreReport]


@dataclass(frozen=True)
class RouteGenerationPayload:
    """One route's typed candidates plus its durable routing diagnostics."""

    result: RouteGenerationResult
    candidates: list[CandidateDossier]


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
        progress_callback: EvolutionProgressCallback | None = None,
    ) -> None:
        self.store = T4ArtifactStore(workspace_dir)
        self.settings = settings
        self.generator = generator
        self.scorer = scorer
        self.evolver = evolver
        self.progress_callback = progress_callback

    async def run(self, run_config: T4RunConfig) -> EvolutionRunResult:
        """Run a fresh or resumable evolution from the configured input state."""

        input_fp = t4_input_fingerprint(self.store.workspace_dir)
        config_fp = run_config_fingerprint(run_config)
        self.store.write_run_config(run_config)
        p0, p0_dossiers, route_results = await self._ensure_p0(run_config, input_fp, config_fp)
        if run_config.rounds == 0:
            await self._report(EvolutionPhase.SCORING, "started", {"population_id": p0.population_id, "candidate_count": len(p0_dossiers)})
            scores = await self._score(p0_dossiers, "SB-P0", run_config=run_config)
            self._write_scores("P0", scores)
            families = self._load_or_build_families(p0_dossiers, generation=0)
            portfolio = select_portfolio(
                p0,
                scores,
                families,
                maximum=run_config.final_top_k,
                profile_weight=run_config.target_profile.portfolio_profile_weight,
            )
            self.store.write_json("ideation/portfolio.json", model_dump(portfolio, mode="json"))
            state = self._set_waiting_state(p0, run_config, display_ids=_portfolio_ids(portfolio), completed_rounds=0)
            active_scores = _select_scores(scores, p0.active_candidate_ids)
            await self._report(
                EvolutionPhase.SURVIVAL,
                "completed",
                {"population_id": p0.population_id, "active_count": len(p0_dossiers), "portfolio_count": len(_portfolio_ids(portfolio))},
            )
            return EvolutionRunResult(
                population=p0,
                portfolio=portfolio,
                state=state,
                route_results=route_results,
                active_dossiers=p0_dossiers,
                active_scores=active_scores,
            )
        population = p0
        dossiers = p0_dossiers
        result: EvolutionRunResult | None = None
        for round_number in range(1, run_config.rounds + 1):
            result = await self._run_evolution_round(
                population=population,
                dossiers=dossiers,
                route_results=route_results,
                run_config=run_config,
                round_number=round_number,
            )
            population = result.population
            dossiers = result.active_dossiers
        if result is None:  # Defensive: rounds=0 returned above.
            raise ValueError("T4 evolution requires a positive round count")
        return result

    async def continue_from_active_population(self, run_config: T4RunConfig) -> EvolutionRunResult:
        """Execute exactly one additional, fully audited Generation.

        A researcher can ask for another Generation after the initial Standard
        run.  ``rounds`` is intentionally not changed in ``t4_run_config``:
        it is part of the fingerprint of the original run.  The extra round is
        instead recorded in the durable state and a new ``P<n>`` snapshot.
        This lets a resumed workspace reuse P0/P1 instead of invalidating it
        merely because the researcher asked for more exploration.
        """

        state = self.store.read_state()
        valid, error = self.store.validate_state_inputs(state)
        if not valid:
            raise ValueError(error or "active T4 population is no longer current")
        population = self.store.read_population(state.current_population_id)
        dossiers = self._load_dossiers(population.active_candidate_ids)
        if not dossiers:
            raise ValueError("the active T4 population has no Candidates to evolve")
        return await self._run_evolution_round(
            population=population,
            dossiers=dossiers,
            route_results=self._load_route_results(),
            run_config=run_config,
            round_number=self._next_available_generation(),
        )

    async def focus_active_candidate(
        self,
        run_config: T4RunConfig,
        *,
        candidate_id: str,
    ) -> EvolutionRunResult:
        """Evolve one active Candidate without silently changing the others.

        Focus Evolution is a real P(r)->P(r+1) operation, not a prompt label.
        It restricts parent selection to the requested Candidate and produces a
        plan-bounded Mutation Child.  The full union is still independently
        rescored and subject to contracts and family-level survival.
        """

        state = self.store.read_state()
        valid, error = self.store.validate_state_inputs(state)
        if not valid:
            raise ValueError(error or "active T4 population is no longer current")
        population = self.store.read_population(state.current_population_id)
        if candidate_id not in population.active_candidate_ids:
            raise ValueError(f"Focus Evolution requires an active Candidate: {candidate_id}")
        dossiers = self._load_dossiers(population.active_candidate_ids)
        return await self._run_evolution_round(
            population=population,
            dossiers=dossiers,
            route_results=self._load_route_results(),
            run_config=run_config,
            round_number=self._next_available_generation(),
            requested_parent_ids=[candidate_id],
            allow_crossover=False,
            operation_label="focus_evolution",
        )

    async def create_crossover_from_active_candidates(
        self,
        run_config: T4RunConfig,
        *,
        parent_ids: list[str],
    ) -> EvolutionRunResult:
        """Create a compatibility-gated Crossover Child from two active Parents.

        The scorer first reviews the specified pair.  Only an approved
        LLM-authored Gene Donor Map can reach the Evolver; rejected or
        uncertain pairs write their review to the evolution plan artifact and
        stop safely rather than producing a concatenated Candidate.
        """

        if len(parent_ids) != 2 or parent_ids[0] == parent_ids[1]:
            raise ValueError("Crossover requires exactly two different active Candidates")
        state = self.store.read_state()
        valid, error = self.store.validate_state_inputs(state)
        if not valid:
            raise ValueError(error or "active T4 population is no longer current")
        population = self.store.read_population(state.current_population_id)
        if not set(parent_ids).issubset(population.active_candidate_ids):
            raise ValueError("Crossover Parents must both be active Candidates")
        dossiers = self._load_dossiers(population.active_candidate_ids)
        return await self._run_evolution_round(
            population=population,
            dossiers=dossiers,
            route_results=self._load_route_results(),
            run_config=run_config,
            round_number=self._next_available_generation(),
            requested_parent_ids=list(parent_ids),
            mutation_limit=0,
            requested_crossover_pairs=[(parent_ids[0], parent_ids[1])],
            allow_crossover=True,
            operation_label="human_requested_crossover",
        )

    async def integrate_human_composed_candidate(
        self,
        run_config: T4RunConfig,
        *,
        composition: HumanCompositionCompatibility,
        child: CandidateDossier,
    ) -> EvolutionRunResult:
        """Independently score and integrate a confirmed Human-composed Child.

        The Evolver authors the child, but this controller owns its lineage,
        union scoring, contract validation, complexity check, survival, and the
        resulting Population snapshot.  It is therefore never an implicit text
        merge or an automatic winner.
        """

        state = self.store.read_state()
        valid, error = self.store.validate_state_inputs(state)
        if not valid:
            raise ValueError(error or "active T4 population is no longer current")
        population = self.store.read_population(state.current_population_id)
        source_ids = list(composition.source_candidate_ids)
        if not set(source_ids).issubset(population.active_candidate_ids):
            raise ValueError("Human composition sources must be active Candidates")
        if child.candidate_id in population.active_candidate_ids:
            raise ValueError("Human composition must create a new Candidate ID")
        if set(child.lineage.parent_ids) != set(source_ids):
            raise ValueError("Human-composed Candidate lineage does not match the Composition Plan")
        if composition.gene_donor_map is None:
            raise ValueError("Human composition requires a confirmed Gene Donor Map")
        round_number = self._next_available_generation()
        plan = EvolutionPlan(
            plan_id=f"{composition.composition_id}-PLAN",
            plan_fingerprint=stable_fingerprint(
                {
                    "composition_id": composition.composition_id,
                    "parents": source_ids,
                    "components": composition.source_components,
                    "donors": composition.gene_donor_map.donors,
                }
            ),
            round=round_number,
            child_type="crossover",
            parent_ids=source_ids,
            operator=EvolutionOperator.CROSSOVER,
            gene_donor_map=composition.gene_donor_map,
            preserve_genes=[],
            modify_genes=list(composition.gene_donor_map.synthesized_genes),
            constraints=list(composition.required_repairs),
            expected_improvements=[composition.explanation_for_user],
            failure_conditions=["A failed Idea Contract, Evidence Permission violation, or inflated complexity rejects the composed Child."],
        )
        return await self._run_evolution_round(
            population=population,
            dossiers=self._load_dossiers(population.active_candidate_ids),
            route_results=self._load_route_results(),
            run_config=run_config,
            round_number=round_number,
            requested_parent_ids=source_ids,
            mutation_limit=0,
            allow_crossover=False,
            operation_label="human_composition",
            provided_plans=[plan],
            provided_children=[child],
        )

    async def regenerate_route_from_active_population(
        self,
        run_config: T4RunConfig,
        *,
        route: str,
    ) -> EvolutionRunResult:
        """Run one requested P0 Route again and integrate its new Seeds safely."""

        route_specs = {item.route: item for item in self.settings.route_quotas}
        spec = route_specs.get(route)
        if spec is None:
            raise ValueError(f"unknown T4 generation Route: {route}")
        state = self.store.read_state()
        valid, error = self.store.validate_state_inputs(state)
        if not valid:
            raise ValueError(error or "active T4 population is no longer current")
        population = self.store.read_population(state.current_population_id)
        evidence = build_idea_evidence_index(self.store.workspace_dir, store=self.store)
        opportunities = await self.generator.plan_opportunities(
            evidence_summary=evidence["summary"],
            run_config=run_config,
        )
        self._validate_opportunities(opportunities)
        quota = min(max(1, run_config.route_quotas.get(route, spec.minimum)), spec.maximum)
        result, seeds = await self._generate_route(
            route=route,
            quota=quota,
            opportunities=opportunities,
            evidence_summary=evidence["summary"],
            required=False,
        )
        round_number = self._next_available_generation()
        self.store.write_json(
            f"ideation/evolution/routes/regeneration_{round_number}_{route}.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_route_regeneration",
                "input_population_id": population.population_id,
                "route": route,
                "result": model_dump(result, mode="json"),
                "candidate_ids": [item.candidate_id for item in seeds],
            },
        )
        if not seeds:
            raise ValueError(f"Route '{route}' did not produce a supported Candidate; its explanation was saved for Gate1 review")
        existing_ids = set(population.active_candidate_ids)
        duplicate_ids = [item.candidate_id for item in seeds if item.candidate_id in existing_ids]
        if duplicate_ids:
            raise ValueError("Regenerated Route returned Candidate IDs already in the active Population: " + ", ".join(duplicate_ids))
        prior_routes = [item for item in self._load_route_results() if item.route != route]
        return await self._run_evolution_round(
            population=population,
            dossiers=self._load_dossiers(population.active_candidate_ids),
            route_results=[*prior_routes, result],
            run_config=run_config,
            round_number=round_number,
            requested_parent_ids=[],
            mutation_limit=0,
            allow_crossover=False,
            operation_label="route_regeneration",
            provided_plans=[],
            provided_children=seeds,
            allow_seed_children=True,
        )

    async def reprofile_active_population(self, run_config: T4RunConfig) -> EvolutionRunResult:
        """Reassess Profile Fit without rewriting evidence or Core Scientific Score.

        A publication-orientation change is a new decision context, not a new
        scientific input.  The old Population remains immutable.  This method
        creates a snapshot with the same Candidates and fresh independent
        Profile Fit assessments, while copying the original five-dimension
        Core Scientific Score verbatim.
        """

        state = self.store.read_state()
        if state.input_fingerprint != t4_input_fingerprint(self.store.workspace_dir):
            raise ValueError("T4 Profile revision cannot use a stale Population")
        population = self.store.read_population(state.current_population_id)
        if population.input_fingerprint != state.input_fingerprint:
            raise ValueError("T4 Profile revision Population has inconsistent input fingerprints")
        dossiers = self._load_dossiers(population.active_candidate_ids)
        old_scores = self._load_active_scores(population)
        revision_generation = self._next_available_generation()
        await self._report(
            EvolutionPhase.SCORING,
            "started",
            {"round_number": revision_generation, "population_id": population.population_id, "candidate_count": len(dossiers)},
        )
        refreshed = await self._score(dossiers, f"SB-PROFILE-{revision_generation}", run_config=run_config)
        refreshed_by_id = {item.candidate_id: item for item in refreshed}
        preserved_core_scores = [
            old.model_copy(
                update={
                    "scoring_batch_id": f"SB-PROFILE-{revision_generation}",
                    "profile_fit": refreshed_by_id[old.candidate_id].profile_fit,
                }
            )
            for old in old_scores
        ]
        config_fp = run_config_fingerprint(run_config)
        output_population = PopulationSnapshot(
            population_id=f"P{revision_generation}",
            generation=revision_generation,
            input_fingerprint=population.input_fingerprint,
            run_config_fingerprint=config_fp,
            active_candidate_ids=list(population.active_candidate_ids),
            family_ids=[],
            elite_candidate_ids=list(population.elite_candidate_ids),
            archived_candidate_ids=[],
            created_from_round=None,
        )
        families = self._load_or_build_families(dossiers, generation=revision_generation)
        output_population = output_population.model_copy(update={"family_ids": [item.family_id for item in families]})
        self.store.write_population(output_population)
        self._write_scores(output_population.population_id, preserved_core_scores)
        portfolio = select_portfolio(
            output_population,
            preserved_core_scores,
            families,
            maximum=run_config.final_top_k,
            profile_weight=run_config.target_profile.portfolio_profile_weight,
        )
        self.store.write_json("ideation/portfolio.json", model_dump(portfolio, mode="json"))
        self.store.write_json(
            f"ideation/evolution/profile_revisions/{revision_generation}.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_profile_revision",
                "source_population_id": population.population_id,
                "output_population_id": output_population.population_id,
                "input_fingerprint": output_population.input_fingerprint,
                "previous_run_config_fingerprint": population.run_config_fingerprint,
                "run_config_fingerprint": config_fp,
                "core_scientific_score_policy": "preserved_from_source_population",
                "profile_fit_policy": "independently_reassessed",
            },
        )
        updated_state = state.model_copy(
            update={
                "phase": EvolutionPhase.WAITING_HUMAN,
                "generation": revision_generation,
                "current_population_id": output_population.population_id,
                "display_candidate_ids": _portfolio_ids(portfolio),
                "run_config_fingerprint": config_fp,
                "last_completed_artifact": f"ideation/populations/{output_population.population_id}.json",
                "generation_history": list(dict.fromkeys([*state.generation_history, output_population.population_id])),
                "archived_population_ids": list(dict.fromkeys([*state.archived_population_ids, population.population_id])),
            }
        )
        self.store.write_state(updated_state)
        await self._report(
            EvolutionPhase.SCORING,
            "completed",
            {"round_number": revision_generation, "population_id": output_population.population_id, "candidate_count": len(preserved_core_scores)},
        )
        await self._report(
            EvolutionPhase.SURVIVAL,
            "completed",
            {
                "round_number": revision_generation,
                "population_id": output_population.population_id,
                "input_count": len(dossiers),
                "offspring_count": 0,
                "active_count": len(dossiers),
                "archived_count": 0,
                "portfolio_count": len(_portfolio_ids(portfolio)),
            },
        )
        return EvolutionRunResult(
            population=output_population,
            portfolio=portfolio,
            state=updated_state,
            route_results=self._load_route_results(),
            active_dossiers=dossiers,
            active_scores=preserved_core_scores,
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

        await self._report(EvolutionPhase.EVIDENCE_ROUTING, "started", {})
        evidence = build_idea_evidence_index(self.store.workspace_dir, store=self.store)
        await self._report(EvolutionPhase.EVIDENCE_ROUTING, "completed", evidence["summary"])
        await self._report(EvolutionPhase.OPPORTUNITY_MAP, "started", {"evidence_atoms": len(evidence["atoms"])})
        opportunities = await self.generator.plan_opportunities(
            evidence_summary=evidence["summary"],
            run_config=run_config,
        )
        self._validate_opportunities(opportunities)
        self.store.write_json(
            "ideation/evidence/opportunities.json",
            {"schema_version": "1.0.0", "semantics": "t4_opportunity_map", "opportunities": [model_dump(item, mode="json") for item in opportunities]},
        )
        await self._report(
            EvolutionPhase.OPPORTUNITY_MAP,
            "completed",
            {"opportunity_count": len(opportunities), "types": [item.type for item in opportunities]},
        )
        route_specs = {item.route: item for item in self.settings.route_quotas}
        requested_routes = [route for route, quota in run_config.route_quotas.items() if quota > 0 and route in route_specs]
        await self._report(
            EvolutionPhase.FORMATION,
            "started",
            {"routes": requested_routes, "target_seed_count": sum(run_config.route_quotas[route] for route in requested_routes)},
        )
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
        await self._report(
            EvolutionPhase.GENOME_FAMILY,
            "completed",
            {"population_id": p0.population_id, "candidate_count": len(dossiers), "family_count": len(families), "routes": [model_dump(item, mode="json") for item in route_results]},
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
            "bridge_plan": self._bridge_plan_context() if route == "cross_domain_bridge" else {"bridge_domains": []},
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

    def _bridge_plan_context(self) -> dict[str, Any]:
        """Pass workspace-confirmed Bridge identifiers to the dedicated route."""

        path = self.store.path("literature/bridge_domain_plan.json")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"bridge_domains": []}
        if not isinstance(payload, dict) or str(payload.get("source") or "").strip().casefold() == "none":
            return {"bridge_domains": []}
        domains = payload.get("bridge_domains") if isinstance(payload.get("bridge_domains"), list) else []
        return {
            "source": str(payload.get("source") or ""),
            "bridge_domains": [item for item in domains if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()],
        }

    async def _run_evolution_round(
        self,
        *,
        population: PopulationSnapshot,
        dossiers: list[CandidateDossier],
        route_results: list[RouteGenerationResult],
        run_config: T4RunConfig,
        round_number: int,
        requested_parent_ids: list[str] | None = None,
        mutation_limit: int | None = None,
        requested_crossover_pairs: list[tuple[str, str]] | None = None,
        allow_crossover: bool | None = None,
        operation_label: str = "evolution",
        provided_plans: list[EvolutionPlan] | None = None,
        provided_children: list[CandidateDossier] | None = None,
        allow_seed_children: bool = False,
    ) -> EvolutionRunResult:
        if self.store.phase_is_complete(
            phase=EvolutionPhase.SURVIVAL,
            generation=round_number,
            input_fingerprint=population.input_fingerprint,
            run_config_fingerprint=population.run_config_fingerprint,
        ):
            output_population = self.store.read_population(f"P{round_number}")
            output_dossiers = self._load_dossiers(output_population.active_candidate_ids)
            scores = self._load_scores(f"U{round_number}", output_population.active_candidate_ids)
            portfolio = self._load_portfolio()
            state = self._set_waiting_state(
                output_population,
                run_config,
                display_ids=_portfolio_ids(portfolio),
                completed_rounds=round_number,
            )
            await self._report(
                EvolutionPhase.SURVIVAL,
                "reused",
                {
                    "round_number": round_number,
                    "population_id": output_population.population_id,
                    "active_count": len(output_dossiers),
                    "portfolio_count": len(_portfolio_ids(portfolio)),
                },
            )
            return EvolutionRunResult(
                population=output_population,
                portfolio=portfolio,
                state=state,
                route_results=route_results,
                active_dossiers=output_dossiers,
                active_scores=scores,
            )

        score_batch = f"SB-P{population.generation}"
        await self._report(
            EvolutionPhase.SCORING,
            "started",
            {"round_number": round_number, "population_id": population.population_id, "candidate_count": len(dossiers)},
        )
        scores_current = await self._score(dossiers, score_batch, run_config=run_config)
        self._write_scores(population.population_id, scores_current)
        families_current = self._load_or_build_families(dossiers, generation=population.generation)
        await self._report(
            EvolutionPhase.SCORING,
            "completed",
            {"round_number": round_number, "population_id": population.population_id, "candidate_count": len(scores_current)},
        )
        await self._report(
            EvolutionPhase.EVOLUTION_PLANNING,
            "started",
            {"round_number": round_number, "population_id": population.population_id},
        )
        if requested_parent_ids is None:
            parent_ids, parent_reasons = select_evolution_parents(
                dossiers,
                scores_current,
                families_current,
                maximum=self.settings.offspring.mutation_maximum,
                profile_weight=run_config.target_profile.portfolio_profile_weight,
            )
        else:
            known_ids = {item.candidate_id for item in dossiers}
            unknown = [candidate_id for candidate_id in requested_parent_ids if candidate_id not in known_ids]
            if unknown:
                raise ValueError("requested evolution Parent is not active: " + ", ".join(unknown))
            parent_ids = list(dict.fromkeys(requested_parent_ids))
            parent_reasons = {candidate_id: operation_label for candidate_id in parent_ids}
        crossover_decisions: list[CrossoverCompatibilityDecision] = []
        if provided_plans is None:
            effective_mutation_limit = self.settings.offspring.mutation_maximum if mutation_limit is None else max(0, mutation_limit)
            mutation_plans = compile_mutation_plans(
                parent_ids,
                scores_current,
                round_number=round_number,
                limit=effective_mutation_limit,
            )
            crossover_allowed = run_config.allow_crossover if allow_crossover is None else allow_crossover
            if crossover_allowed and self.settings.offspring.crossover_maximum:
                pairs = requested_crossover_pairs if requested_crossover_pairs is not None else _candidate_pairs(parent_ids)
                if pairs:
                    crossover_decisions = await self.scorer.review_crossover_pairs(candidates=_copies(dossiers), pairs=pairs[:3])
            crossover_plans = compile_crossover_plans(
                crossover_decisions,
                round_number=round_number,
                limit=min(run_config.max_crossover_children, self.settings.offspring.crossover_maximum) if crossover_allowed else 0,
            )
            plans = mutation_plans + crossover_plans
        else:
            plans = list(provided_plans)
            mutation_plans = [plan for plan in plans if plan.child_type == "mutation"]
            crossover_plans = [plan for plan in plans if plan.child_type == "crossover"]
        if requested_crossover_pairs is not None and not crossover_plans:
            self.store.write_json(
                f"ideation/evolution/plans/round_{round_number}.json",
                {
                    "schema_version": "1.0.0",
                    "semantics": "t4_evolution_plan_batch",
                    "operation": operation_label,
                    "parent_selection": {"ids": parent_ids, "reasons": parent_reasons},
                    "plans": [],
                    "crossover_decisions": [model_dump(item, mode="json") for item in crossover_decisions],
                    "status": "no_approved_crossover",
                },
            )
            raise ValueError("The requested Crossover did not pass the independent Compatibility Check")
        self.store.write_json(
            f"ideation/evolution/plans/round_{round_number}.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_evolution_plan_batch",
                "operation": operation_label,
                "parent_selection": {"ids": parent_ids, "reasons": parent_reasons},
                "plans": [model_dump(item, mode="json") for item in plans],
                "crossover_decisions": [model_dump(item, mode="json") for item in crossover_decisions],
            },
        )
        await self._report(
            EvolutionPhase.EVOLUTION_PLANNING,
            "completed",
            {
                "round_number": round_number,
                "parent_count": len(parent_ids),
                "mutation_count": len(mutation_plans),
                "crossover_count": len(crossover_plans),
            },
        )
        parent_lookup = {item.candidate_id: item for item in dossiers}
        await self._report(
            EvolutionPhase.OFFSPRING,
            "started",
            {"round_number": round_number, "planned_offspring": len(plans)},
        )
        children = list(provided_children) if provided_children is not None else await self.evolver.generate_offspring(plans=plans, parents=_copies(dossiers))
        self._validate_children(children, plans, parent_lookup, allow_seed_children=allow_seed_children)
        for child in children:
            self.store.write_candidate(child)
        union = [*dossiers, *children]
        await self._report(
            EvolutionPhase.OFFSPRING,
            "rescoring",
            {"round_number": round_number, "offspring_count": len(children), "union_count": len(union)},
        )
        union_scores = await self._score(union, f"SB-U{round_number}", run_config=run_config)
        self._write_scores(f"U{round_number}", union_scores)
        contracts = [validate_idea_contract(item) for item in union]
        deltas = []
        complexity = []
        plan_by_parent = {tuple(plan.parent_ids): plan for plan in plans}
        for child in children:
            plan = plan_by_parent.get(tuple(child.lineage.parent_ids))
            if plan is None and allow_seed_children and not child.lineage.parent_ids:
                continue
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
        output_population_id = f"P{round_number}"
        output_generation = round_number
        output_families = self._load_or_build_families(union, generation=output_generation)
        survivor_ids, archived_ids, survival = select_survivors(
            union,
            union_scores,
            contracts,
            deltas,
            complexity,
            output_families,
            target_size=run_config.active_population_size,
        )
        output_population = PopulationSnapshot(
            population_id=output_population_id,
            generation=output_generation,
            input_fingerprint=population.input_fingerprint,
            run_config_fingerprint=population.run_config_fingerprint,
            active_candidate_ids=survivor_ids,
            family_ids=[item.family_id for item in output_families],
            elite_candidate_ids=survivor_ids[:1],
            archived_candidate_ids=archived_ids,
            created_from_round=round_number,
        )
        self.store.write_population(output_population)
        self.store.write_round(
            RoundArtifact(
                round=round_number,
                input_population_id=population.population_id,
                output_population_id=output_population_id,
                input_fingerprint=population.input_fingerprint,
                run_config_fingerprint=population.run_config_fingerprint,
                parent_ids=parent_ids,
                offspring_ids=[item.candidate_id for item in children],
                survivor_ids=survivor_ids,
                archived_ids=archived_ids,
                plan_ids=[item.plan_id for item in plans],
                score_batch_ids=[score_batch, f"SB-U{round_number}"],
                completion_status="completed",
            )
        )
        self.store.write_json(
            f"ideation/evolution/round_{round_number}_diagnostics.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_evolution_diagnostics",
                "contracts": [model_dump(item, mode="json") for item in contracts],
                "gene_deltas": [model_dump(item, mode="json") for item in deltas],
                "complexity": [model_dump(item, mode="json") for item in complexity],
                "survival": survival,
            },
        )
        portfolio = select_portfolio(
            output_population,
            union_scores,
            output_families,
            maximum=run_config.final_top_k,
            profile_weight=run_config.target_profile.portfolio_profile_weight,
        )
        self.store.write_json("ideation/portfolio.json", model_dump(portfolio, mode="json"))
        state = self._set_waiting_state(
            output_population,
            run_config,
            display_ids=_portfolio_ids(portfolio),
            completed_rounds=round_number,
        )
        self.store.write_phase_marker(
            phase=EvolutionPhase.SURVIVAL,
            generation=round_number,
            input_fingerprint=output_population.input_fingerprint,
            run_config_fingerprint=output_population.run_config_fingerprint,
            artifact_paths=[
                f"ideation/populations/{output_population_id}.json",
                f"ideation/evolution/round_{round_number}.json",
                f"ideation/evolution/round_{round_number}_diagnostics.json",
                "ideation/portfolio.json",
            ],
        )
        active_dossiers = [item for item in union if item.candidate_id in set(output_population.active_candidate_ids)]
        active_scores = _select_scores(union_scores, output_population.active_candidate_ids)
        await self._report(
            EvolutionPhase.SURVIVAL,
            "completed",
            {
                "population_id": output_population.population_id,
                "round_number": round_number,
                "input_count": len(dossiers),
                "offspring_count": len(children),
                "active_count": len(active_dossiers),
                "archived_count": len(archived_ids),
                "portfolio_count": len(_portfolio_ids(portfolio)),
            },
        )
        return EvolutionRunResult(
            population=output_population,
            portfolio=portfolio,
            state=state,
            route_results=route_results,
            active_dossiers=active_dossiers,
            active_scores=active_scores,
        )

    async def _score(
        self,
        candidates: list[CandidateDossier],
        batch_id: str,
        *,
        run_config: T4RunConfig,
    ) -> list[ScoreReport]:
        reports = await self.scorer.score_population(candidates=_copies(candidates), scoring_batch_id=batch_id, blind=True)
        expected = {item.candidate_id for item in candidates}
        actual = {item.candidate_id for item in reports}
        if expected != actual:
            raise ValueError(f"independent scoring must cover exactly the population; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
        if any(not report.blind for report in reports):
            raise ValueError("independent population scores must be blind")
        mismatched_profile_fit = [
            report.candidate_id
            for report in reports
            if report.profile_fit.profile_type != run_config.target_profile.profile_type
        ]
        if mismatched_profile_fit:
            raise ValueError(
                "independent scoring returned Profile Fit for a different Target Profile: "
                + ", ".join(mismatched_profile_fit)
            )
        return reports

    def _write_scores(self, population_id: str, reports: list[ScoreReport]) -> None:
        self.store.write_json(
            f"ideation/scoring/{population_id}.json",
            {"schema_version": "1.0.0", "semantics": "t4_independent_score_batch", "scores": [model_dump(item, mode="json") for item in reports]},
        )

    def _load_scores(self, population_id: str, candidate_ids: list[str]) -> list[ScoreReport]:
        payload = self.store.read_model(f"ideation/scoring/{population_id}.json", _LooseArtifact).payload
        raw = payload.get("scores") if isinstance(payload.get("scores"), list) else []
        by_id = {
            report.candidate_id: report
            for item in raw
            if isinstance(item, dict)
            for report in [ScoreReport.model_validate(item)]
        }
        missing = [candidate_id for candidate_id in candidate_ids if candidate_id not in by_id]
        if missing:
            raise ValueError(f"score artifact {population_id} is missing active candidates: {missing}")
        return [by_id[candidate_id] for candidate_id in candidate_ids]

    def _load_active_scores(self, population: PopulationSnapshot) -> list[ScoreReport]:
        """Read the score batch that produced an active Population snapshot."""

        candidates = [f"U{population.generation}", population.population_id] if population.generation else ["P0"]
        for score_id in candidates:
            try:
                return self._load_scores(score_id, population.active_candidate_ids)
            except ValueError:
                continue
        raise ValueError(f"active Population {population.population_id} has no complete score batch")

    def _load_portfolio(self) -> PortfolioSelection:
        return self.store.read_model("ideation/portfolio.json", PortfolioSelection)

    async def _report(self, phase: EvolutionPhase, status: str, payload: dict[str, Any]) -> None:
        if self.progress_callback is None:
            return
        result = self.progress_callback(phase, status, payload)
        if hasattr(result, "__await__"):
            await result

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
                "configured_rounds": max(state.configured_rounds, completed_rounds),
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

    def _next_available_generation(self) -> int:
        """Allocate a new snapshot number without overwriting a rolled-back branch."""

        generations = [0]
        for path in self.store.path("ideation/populations").glob("P*.json"):
            match = re.fullmatch(r"P(\d+)", path.stem)
            if match:
                generations.append(int(match.group(1)))
        return max(generations) + 1

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
        output: list[CandidateDossier] | RouteGenerationResult | RouteGenerationPayload,
        *,
        repaired_once: bool = False,
    ) -> tuple[RouteGenerationResult, list[CandidateDossier]]:
        if isinstance(output, RouteGenerationPayload):
            result = output.result.model_copy(update={"repaired_once": output.result.repaired_once or repaired_once})
            return result, list(output.candidates)
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
    def _validate_children(
        children: list[CandidateDossier],
        plans: list,
        parents: dict[str, CandidateDossier],
        *,
        allow_seed_children: bool = False,
    ) -> None:
        plan_parent_sets = {tuple(plan.parent_ids) for plan in plans}
        child_ids = [item.candidate_id for item in children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("offspring contain duplicate candidate IDs")
        for child in children:
            if child.candidate_id in parents:
                raise ValueError("offspring must never overwrite a parent candidate")
            if allow_seed_children and not child.lineage.parent_ids and child.lineage.created_by == "generator":
                continue
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


def _select_scores(reports: list[ScoreReport], candidate_ids: list[str]) -> list[ScoreReport]:
    """Return one independent score for every active candidate in stable order."""

    by_id = {item.candidate_id: item for item in reports}
    missing = [candidate_id for candidate_id in candidate_ids if candidate_id not in by_id]
    if missing:
        raise ValueError(f"independent score batch is missing active candidates: {missing}")
    return [by_id[candidate_id] for candidate_id in candidate_ids]
