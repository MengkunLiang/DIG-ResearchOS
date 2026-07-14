from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from researchos.ideation.config import load_t4_evolution_settings
from researchos.ideation.migration import migrate_legacy_candidate_pool
from researchos.ideation.models import (
    EvidenceAtom,
    EvidencePermission,
    EvidenceStatus,
    EvolutionOperator,
    EvolutionPlan,
    GeneDonorMap,
    IdeaGene,
    IdeaGenome,
    PopulationSnapshot,
    ReadingLevel,
    ScoreDimensions,
    ScoreReport,
    T4RunConfig,
)
from researchos.schemas.validator import validate_record


FINGERPRINT = "a" * 64


def _gene(value: str) -> IdeaGene:
    return IdeaGene(value=value)


def _genome(candidate_id: str = "I1") -> IdeaGenome:
    return IdeaGenome(
        candidate_id=candidate_id,
        route="evidence_routed_literature",
        problem=_gene("problem"),
        opportunity=_gene("opportunity"),
        challenged_assumption=_gene("assumption"),
        core_thesis=_gene("thesis"),
        mechanism=_gene("mechanism"),
        design_or_artifact=_gene("artifact"),
        contribution_package=_gene("contribution"),
        hypothesis_bundle=_gene("hypothesis"),
        validation_logic=_gene("validation"),
        boundary_conditions=_gene("boundary"),
        risks=_gene("risk"),
    )


def test_evidence_atom_rejects_abstract_mechanism_support():
    with pytest.raises(ValidationError, match="reading level cannot grant"):
        EvidenceAtom(
            atom_id="EA1",
            source_path="literature/shallow_read_notes/p1.md",
            section_key="core_approach",
            content="abstract evidence",
            reading_level=ReadingLevel.ABSTRACT_ONLY,
            evidence_status=EvidenceStatus.ABSTRACT_HINT,
            allowed_uses={EvidencePermission.MECHANISM_SUPPORT},
        )


def test_evidence_atom_accepts_full_text_mechanism_support():
    atom = EvidenceAtom(
        atom_id="EA1",
        source_path="literature/deep_read_notes/p1.md",
        section_key="mechanism",
        content="bounded evidence",
        reading_level=ReadingLevel.FULL_TEXT,
        evidence_status=EvidenceStatus.DIRECT_SUPPORT,
        allowed_uses={EvidencePermission.MECHANISM_SUPPORT},
    )
    assert atom.source_path == "literature/deep_read_notes/p1.md"


def test_genome_round_trip_and_population_integrity():
    genome = _genome()
    rebuilt = IdeaGenome.model_validate(genome.model_dump(mode="json"))
    assert rebuilt.candidate_id == "I1"
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        PopulationSnapshot(
            population_id="P0",
            generation=0,
            input_fingerprint=FINGERPRINT,
            run_config_fingerprint=FINGERPRINT,
            active_candidate_ids=["I1", "I1"],
        )


def test_score_requires_all_five_dimension_rationales():
    with pytest.raises(ValidationError, match="score rationales missing"):
        ScoreReport(
            candidate_id="I1",
            scoring_batch_id="SB1",
            scores=ScoreDimensions(
                research_value=3,
                mechanism_integrity=3,
                contribution_distinctiveness=3,
                evidence_calibration=3,
                validation_tractability=3,
            ),
            overall_readiness=3,
            score_uncertainty=0.2,
            rationales={"research_value": "reason"},
            dominant_strength="strength",
            dominant_bottleneck="bottleneck",
        )


def test_crossover_requires_two_parents_and_donor_map():
    with pytest.raises(ValidationError, match="gene_donor_map"):
        EvolutionPlan(
            plan_id="EP1",
            plan_fingerprint=FINGERPRINT,
            round=1,
            child_type="crossover",
            parent_ids=["I1", "I2"],
            operator=EvolutionOperator.CROSSOVER,
        )
    plan = EvolutionPlan(
        plan_id="EP1",
        plan_fingerprint=FINGERPRINT,
        round=1,
        child_type="crossover",
        parent_ids=["I1", "I2"],
        operator=EvolutionOperator.CROSSOVER,
        gene_donor_map=GeneDonorMap(donors={"mechanism": "I1", "validation_logic": "I2"}),
    )
    assert plan.gene_donor_map is not None


def test_run_config_prevents_disabled_crossover_budget():
    with pytest.raises(ValidationError, match="must be zero"):
        T4RunConfig(allow_crossover=False, max_crossover_children=1)


def test_system_t4_config_has_asymmetric_required_routes():
    settings = load_t4_evolution_settings()
    quotas = {item.route: item for item in settings.route_quotas}
    assert quotas["evidence_routed_literature"].minimum == 3
    assert quotas["informed_brainstorm"].minimum == 2
    assert quotas["cross_domain_bridge"].may_be_unsupported is True


def test_static_run_config_schema_validates_basics():
    ok, error = validate_record(
        {
            "mode": "standard",
            "rounds": 1,
            "allow_crossover": True,
            "final_top_k": 3,
        },
        "t4_run_config",
    )
    assert ok, error


def test_legacy_candidate_pool_migrates_to_traceable_p0(tmp_path):
    candidate_path = tmp_path / "ideation" / "_candidate_directions.json"
    candidate_path.parent.mkdir()
    candidate_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "id": "D1",
                        "idea_origin": "evidence_driven",
                        "target_problem": "observed problem",
                        "core_claim": "proposed thesis",
                        "mechanism": "proposed mechanism",
                        "prediction": "observable outcome",
                        "counterfactual": "falsifying outcome",
                        "supporting_papers": [
                            {
                                "source_file": "literature/deep_read_notes/p1.md",
                                "evidence_level": "FULL_TEXT",
                                "ref": "p1",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    population, genomes, lineages = migrate_legacy_candidate_pool(
        tmp_path,
        input_fingerprint=FINGERPRINT,
        run_config_fingerprint=FINGERPRINT,
    )
    assert population.population_id == "P0"
    assert population.active_candidate_ids == ["D1"]
    assert genomes[0].migration_quality == "legacy_partial"
    assert lineages[0].created_by == "legacy_migration"
