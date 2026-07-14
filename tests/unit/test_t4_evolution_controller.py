from __future__ import annotations

import json

import pytest

from researchos.ideation.config import OffspringDefaults, PopulationDefaults, RouteQuota, T4EvolutionSettings
from researchos.ideation.evolution_controller import IdeaEvolutionController
from researchos.ideation.state import T4ArtifactStore
from researchos.ideation.models import (
    CandidateDossier,
    CandidateLineage,
    CandidateMaturity,
    CandidateStatus,
    Contribution,
    EvolutionOperator,
    GeneDonorMap,
    HumanCompositionCompatibility,
    IdeaGene,
    IdeaGenome,
    OpportunityQuery,
    ProvisionalHypothesis,
    ScoreDimensions,
    ProfileFitAssessment,
    ScoreReport,
    T4RunConfig,
    TargetProfile,
)


def _write_inputs(workspace):
    (workspace / "literature" / "deep_read_notes").mkdir(parents=True)
    (workspace / "user_seeds").mkdir()
    (workspace / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("synthesis\n", encoding="utf-8")
    (workspace / "literature" / "synthesis_workbench.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "domain_map.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "comparison_table.csv").write_text("id,title\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_ideas.md").write_text("\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_constraints.md").write_text("\n", encoding="utf-8")
    (workspace / "literature" / "deep_read_notes" / "p1.md").write_text(
        "# Note\n\n## Mechanism\n\nBounded evidence for a fixture mechanism.\n", encoding="utf-8"
    )


def _candidate(candidate_id: str, route: str, *, parent_ids=None, mechanism="Fixture mechanism"):
    gene = lambda value: IdeaGene(value=value)
    return CandidateDossier(
        candidate_id=candidate_id,
        version=1,
        status=CandidateStatus.ACTIVE,
        maturity=CandidateMaturity.EVOLVED,
        genome=IdeaGenome(
            candidate_id=candidate_id,
            route=route,
            problem=gene("A bounded fixture problem."),
            opportunity=gene("A testable fixture opportunity."),
            challenged_assumption=gene("The baseline assumption can fail."),
            core_thesis=gene("A coherent mechanism changes the expected outcome."),
            mechanism=gene(mechanism),
            design_or_artifact=gene("A bounded fixture artifact."),
            contribution_package=gene("A mechanism and validation contribution."),
            hypothesis_bundle=gene("Two falsifiable fixture hypotheses."),
            validation_logic=gene("Use an active and disabling control."),
            boundary_conditions=gene("The effect weakens outside the target condition."),
            risks=gene("A non-specific control effect rejects the claim."),
        ),
        contributions=[
            Contribution(
                contribution_id=f"{candidate_id}-C1",
                statement="Make a bounded mechanism testable.",
                contribution_type="mechanism",
                what_changes_if_true="Future work must evaluate the stated boundary.",
            ),
            Contribution(
                contribution_id=f"{candidate_id}-C2",
                statement="Add a discriminating control.",
                contribution_type="design",
                what_changes_if_true="Validation can reject a non-specific explanation.",
            ),
        ],
        hypotheses=[
            ProvisionalHypothesis(
                hypothesis_id=f"{candidate_id}-H1",
                statement="The target condition changes the outcome.",
                mechanism="The stated mechanism reacts to the target condition.",
                observable_prediction="The target group differs from control.",
                discriminating_test="Disable the mechanism under the same condition.",
            ),
            ProvisionalHypothesis(
                hypothesis_id=f"{candidate_id}-H2",
                statement="The effect weakens outside the condition.",
                mechanism="The mechanism has a boundary.",
                observable_prediction="The non-target group has a smaller effect.",
                discriminating_test="Compare matched target and non-target groups.",
            ),
        ],
        lineage=CandidateLineage(
            candidate_id=candidate_id,
            parent_ids=parent_ids or [],
            route=route,
            created_by="evolver" if parent_ids else "generator",
        ),
    )


class FakeGenerator:
    async def plan_opportunities(self, *, evidence_summary, run_config):
        return [
            OpportunityQuery(
                opportunity_id=f"O{index}",
                type="mechanism_gap",
                one_line_summary=f"Fixture opportunity {index}",
                question="Which bounded mechanism is testable?",
                why_it_matters="The fixture requires a falsifiable explanation.",
                compatible_routes=["evidence_routed_literature", "informed_brainstorm"],
            )
            for index in range(1, 4)
        ]

    async def generate_route(self, *, route, opportunities, evidence_bundle, quota, repair):
        suffix = "L" if route == "evidence_routed_literature" else "B"
        return [_candidate(f"I{suffix}{index}", route, mechanism=f"{suffix} mechanism {index}") for index in range(1, quota + 1)]


class RegeneratingFakeGenerator(FakeGenerator):
    def __init__(self):
        self.calls: dict[str, int] = {}

    async def generate_route(self, *, route, opportunities, evidence_bundle, quota, repair):
        self.calls[route] = self.calls.get(route, 0) + 1
        if route == "evidence_routed_literature" and self.calls[route] > 1:
            return [_candidate(f"RR{index}", route, mechanism=f"Regenerated mechanism {index}") for index in range(1, quota + 1)]
        return await super().generate_route(
            route=route,
            opportunities=opportunities,
            evidence_bundle=evidence_bundle,
            quota=quota,
            repair=repair,
        )


class UnsupportedRegenerationFakeGenerator(RegeneratingFakeGenerator):
    async def generate_route(self, *, route, opportunities, evidence_bundle, quota, repair):
        self.calls[route] = self.calls.get(route, 0) + 1
        if route == "evidence_routed_literature" and self.calls[route] > 1:
            return []
        return await FakeGenerator.generate_route(
            self,
            route=route,
            opportunities=opportunities,
            evidence_bundle=evidence_bundle,
            quota=quota,
            repair=repair,
        )


class DuplicateRegenerationFakeGenerator(RegeneratingFakeGenerator):
    async def generate_route(self, *, route, opportunities, evidence_bundle, quota, repair):
        self.calls[route] = self.calls.get(route, 0) + 1
        if route == "evidence_routed_literature" and self.calls[route] > 1:
            return [_candidate("IL1", route, mechanism="Duplicate regenerated mechanism")]
        return await FakeGenerator.generate_route(
            self,
            route=route,
            opportunities=opportunities,
            evidence_bundle=evidence_bundle,
            quota=quota,
            repair=repair,
        )


class FakeScorer:
    async def score_population(self, *, candidates, scoring_batch_id, blind):
        reports = []
        for index, candidate in enumerate(candidates, start=1):
            readiness = 3.0 + index / 10
            reports.append(
                ScoreReport(
                    candidate_id=candidate.candidate_id,
                    scoring_batch_id=scoring_batch_id,
                    blind=blind,
                    scores=ScoreDimensions(
                        research_value=readiness,
                        mechanism_integrity=readiness,
                        contribution_distinctiveness=readiness,
                        evidence_calibration=readiness,
                        validation_tractability=readiness,
                    ),
                    overall_readiness=readiness,
                    score_uncertainty=0.2,
                    rationales={
                        "research_value": "reason",
                        "mechanism_integrity": "reason",
                        "contribution_distinctiveness": "reason",
                        "evidence_calibration": "reason",
                        "validation_tractability": "reason",
                    },
                    dominant_strength="A bounded mechanism.",
                    dominant_bottleneck="The validation needs a control.",
                    preserve_genes=["problem"],
                    modify_genes=["validation_logic"],
                    recommended_operators=[EvolutionOperator.REPAIR_VALIDATION],
                )
            )
        return reports

    async def review_crossover_pairs(self, *, candidates, pairs):
        return []


class ProfileAwareFakeScorer(FakeScorer):
    profile_type = "hybrid"

    async def score_population(self, *, candidates, scoring_batch_id, blind):
        reports = await super().score_population(
            candidates=candidates,
            scoring_batch_id=scoring_batch_id,
            blind=blind,
        )
        return [
            report.model_copy(
                update={
                    "profile_fit": ProfileFitAssessment(
                        profile_type=self.profile_type,
                        overall_fit=4.4,
                        dimensions={"fit": 4.4},
                        rationale=f"Independent fit assessment for {self.profile_type}.",
                    )
                }
            )
            for report in reports
        ]


class FakeEvolver:
    async def generate_offspring(self, *, plans, parents):
        by_id = {item.candidate_id: item for item in parents}
        children = []
        for index, plan in enumerate(plans, start=1):
            parent = by_id[plan.parent_ids[0]]
            children.append(
                _candidate(
                    f"M{plan.round}-{index}",
                    parent.genome.route,
                    parent_ids=plan.parent_ids,
                    mechanism=parent.genome.mechanism.value + " with a disabling control",
                )
            )
        return children


def _settings():
    return T4EvolutionSettings(
        route_quotas=[
            RouteQuota(route="evidence_routed_literature", minimum=1, maximum=1, required=True),
            RouteQuota(route="informed_brainstorm", minimum=1, maximum=1, required=True),
        ],
        population=PopulationDefaults(
            max_initial_population=6,
            active_population_target=3,
            active_population_minimum=1,
            active_population_maximum=3,
        ),
        offspring=OffspringDefaults(
            mutation_minimum=1,
            mutation_maximum=1,
            crossover_minimum=0,
            crossover_maximum=0,
            max_total=1,
        ),
    )


@pytest.mark.asyncio
async def test_controller_completes_default_p0_to_p1_with_role_separation(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    result = await controller.run(
        T4RunConfig(
            mode="standard",
            rounds=1,
            allow_crossover=False,
            final_top_k=2,
            max_initial_population=6,
            active_population_size=3,
            max_offspring_per_round=1,
            max_crossover_children=0,
            route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
        )
    )
    assert result.population.population_id == "P1"
    assert result.state.current_population_id == "P1"
    assert result.state.completed_rounds == 1
    assert len(result.route_results) == 2
    assert (tmp_path / "ideation" / "populations" / "P0.json").exists()
    assert (tmp_path / "ideation" / "populations" / "P1.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "round_1.json").exists()
    assert (tmp_path / "ideation" / "portfolio.json").exists()
    diagnostics = json.loads((tmp_path / "ideation" / "evolution" / "round_1_diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["gene_deltas"]
    assert diagnostics["contracts"]


@pytest.mark.asyncio
async def test_controller_reuses_completed_p0_without_regenerating_routes(tmp_path):
    _write_inputs(tmp_path)
    generator = FakeGenerator()
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=generator,
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="quick",
        rounds=0,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=0,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    second = await controller.run(config)
    assert first.population.population_id == second.population.population_id == "P0"
    assert second.state.completed_rounds == 0


@pytest.mark.asyncio
async def test_controller_reuses_completed_p1_without_another_evolution_round(tmp_path):
    _write_inputs(tmp_path)
    events = []

    async def progress(phase, status, payload):
        events.append((phase.value, status, payload))

    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
        progress_callback=progress,
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    second = await controller.run(config)

    assert first.population.population_id == second.population.population_id == "P1"
    assert second.active_dossiers
    assert {item.candidate_id for item in second.active_scores} == set(second.population.active_candidate_ids)
    assert any(status == "reused" for _phase, status, _payload in events)


@pytest.mark.asyncio
async def test_deep_mode_completes_a_second_population_update(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    result = await controller.run(
        T4RunConfig(
            mode="deep",
            rounds=2,
            allow_crossover=False,
            final_top_k=2,
            max_initial_population=6,
            active_population_size=3,
            max_offspring_per_round=1,
            max_crossover_children=0,
            route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
        )
    )

    assert result.population.population_id == "P2"
    assert result.state.completed_rounds == 2
    assert (tmp_path / "ideation" / "populations" / "P2.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "round_2.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "round_2_diagnostics.json").exists()


@pytest.mark.asyncio
async def test_continue_from_active_population_adds_one_generation_without_invalidating_p1(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    continued = await controller.continue_from_active_population(config)

    assert first.population.population_id == "P1"
    assert continued.population.population_id == "P2"
    assert (tmp_path / "ideation" / "populations" / "P1.json").exists()
    assert (tmp_path / "ideation" / "populations" / "P2.json").exists()
    assert continued.state.completed_rounds == 2
    assert continued.state.configured_rounds == 2


@pytest.mark.asyncio
async def test_profile_revision_preserves_core_score_and_creates_a_new_population_snapshot(tmp_path):
    _write_inputs(tmp_path)
    scorer = ProfileAwareFakeScorer()
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=scorer,
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
        target_profile=TargetProfile(profile_type="hybrid", confirmed_by_user=True),
    )
    first = await controller.run(config)
    old_scores = json.loads((tmp_path / "ideation" / "scoring" / "U1.json").read_text(encoding="utf-8"))["scores"]

    scorer.profile_type = "technical_cs"
    revised = config.model_copy(
        update={
            "target_profile": TargetProfile(
                profile_type="technical_cs",
                primary_orientation="technical_and_computational",
                confirmed_by_user=True,
            )
        }
    )
    T4ArtifactStore(tmp_path).write_run_config(revised)
    result = await controller.reprofile_active_population(revised)
    new_scores = json.loads((tmp_path / "ideation" / "scoring" / "P2.json").read_text(encoding="utf-8"))["scores"]

    assert first.population.population_id == "P1"
    assert result.population.population_id == "P2"
    assert (tmp_path / "ideation" / "populations" / "P1.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "profile_revisions" / "2.json").exists()
    old_by_id = {item["candidate_id"]: item["scores"] for item in old_scores}
    assert {item["candidate_id"]: item["scores"] for item in new_scores} == {
        item["candidate_id"]: old_by_id[item["candidate_id"]] for item in new_scores
    }
    assert {item["profile_fit"]["profile_type"] for item in new_scores} == {"technical_cs"}


@pytest.mark.asyncio
async def test_focus_active_candidate_creates_a_new_generation_with_a_single_parent_plan(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    focused_parent = first.population.active_candidate_ids[0]
    focused = await controller.focus_active_candidate(config, candidate_id=focused_parent)
    plan = json.loads((tmp_path / "ideation" / "evolution" / "plans" / "round_2.json").read_text(encoding="utf-8"))

    assert focused.population.population_id == "P2"
    assert plan["operation"] == "focus_evolution"
    assert plan["parent_selection"]["ids"] == [focused_parent]
    assert len(plan["plans"]) == 1
    assert plan["plans"][0]["parent_ids"] == [focused_parent]


@pytest.mark.asyncio
async def test_human_composed_candidate_is_scored_as_a_new_child_without_replacing_sources(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    source_ids = first.population.active_candidate_ids[:2]
    composed = _candidate("HC2-X", "human_composition", parent_ids=source_ids, mechanism="A reconciled composed mechanism")
    composed = composed.model_copy(
        update={
            "lineage": CandidateLineage(
                candidate_id="HC2-X",
                parent_ids=source_ids,
                route="human_composition",
                created_by="human_composition",
            )
        }
    )
    compatibility = HumanCompositionCompatibility(
        composition_id="HC-X",
        source_candidate_ids=source_ids,
        source_components=[f"{source_ids[0]}-H1", f"{source_ids[1]}-mechanism"],
        problem_compatibility="high",
        assumption_conflict="none",
        mechanism_compatibility="high",
        joint_testability="high",
        contribution_coherence="high",
        evidence_compatibility="high",
        complexity_risk="low",
        composition_type="complementary",
        recommended_action="compose",
        explanation_for_user="The selected elements support one bounded thesis with a shared discriminating validation path.",
        gene_donor_map=GeneDonorMap(
            donors={"problem": source_ids[0], "mechanism": source_ids[1]},
            synthesized_genes=["core_thesis", "validation_logic"],
        ),
    )

    result = await controller.integrate_human_composed_candidate(config, composition=compatibility, child=composed)
    plan = json.loads((tmp_path / "ideation" / "evolution" / "plans" / "round_2.json").read_text(encoding="utf-8"))

    assert result.population.population_id == "P2"
    assert plan["operation"] == "human_composition"
    assert plan["plans"][0]["parent_ids"] == source_ids
    assert (tmp_path / "ideation" / "candidates" / "HC2-X.v1.json").exists()
    for candidate_id in source_ids:
        assert list((tmp_path / "ideation" / "candidates").glob(f"{candidate_id}.v*.json"))


@pytest.mark.asyncio
async def test_route_regeneration_preserves_prior_population_and_creates_a_new_snapshot(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=RegeneratingFakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    regenerated = await controller.regenerate_route_from_active_population(
        config,
        route="evidence_routed_literature",
    )

    assert first.population.population_id == "P1"
    assert regenerated.population.population_id == "P2"
    assert (tmp_path / "ideation" / "populations" / "P1.json").exists()
    route_artifact = tmp_path / "ideation" / "evolution" / "routes" / "regeneration_2_evidence_routed_literature.json"
    assert route_artifact.exists()
    assert "RR1" in route_artifact.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_route_regeneration_rejects_unknown_route_without_changing_active_population(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    await controller.run(config)

    with pytest.raises(ValueError, match="unknown T4 generation Route"):
        await controller.regenerate_route_from_active_population(config, route="not_a_route")

    assert T4ArtifactStore(tmp_path).read_state().current_population_id == "P1"
    assert not (tmp_path / "ideation" / "populations" / "P2.json").exists()


@pytest.mark.asyncio
async def test_route_regeneration_preserves_unsupported_route_artifact_without_changing_population(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=UnsupportedRegenerationFakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    await controller.run(config)

    with pytest.raises(ValueError, match="did not produce a supported Candidate"):
        await controller.regenerate_route_from_active_population(config, route="evidence_routed_literature")

    artifact = tmp_path / "ideation" / "evolution" / "routes" / "regeneration_2_evidence_routed_literature.json"
    assert artifact.exists()
    assert T4ArtifactStore(tmp_path).read_state().current_population_id == "P1"
    assert not (tmp_path / "ideation" / "populations" / "P2.json").exists()


@pytest.mark.asyncio
async def test_route_regeneration_rejects_duplicate_candidate_ids_without_overwriting_population(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=DuplicateRegenerationFakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    await controller.run(config)

    with pytest.raises(ValueError, match="already in the active Population"):
        await controller.regenerate_route_from_active_population(config, route="evidence_routed_literature")

    assert T4ArtifactStore(tmp_path).read_state().current_population_id == "P1"
    assert not (tmp_path / "ideation" / "populations" / "P2.json").exists()


@pytest.mark.asyncio
async def test_route_regeneration_after_rollback_allocates_a_new_population_snapshot(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=RegeneratingFakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    await controller.run(config)
    await controller.continue_from_active_population(config)
    store = T4ArtifactStore(tmp_path)
    store.activate_population("P1")

    regenerated = await controller.regenerate_route_from_active_population(config, route="evidence_routed_literature")

    assert (tmp_path / "ideation" / "populations" / "P2.json").exists()
    assert regenerated.population.population_id == "P3"
    assert T4ArtifactStore(tmp_path).read_state().current_population_id == "P3"
