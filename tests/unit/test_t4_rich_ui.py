from __future__ import annotations

import io

import pytest
from rich.console import Console

from researchos.ideation.models import EvolutionPhase, T4RunConfig
from researchos.ideation.prerun import T4InputInspection
from researchos.tools.human_gate import CLIHumanInterface
from researchos.ui.idea_evolution_renderer import render_t4_evolution_phase
from researchos.ui.idea_prerun_renderer import render_t4_prerun


def _render_phase(phase: EvolutionPhase, payload: dict[str, object]) -> str:
    buffer = io.StringIO()
    render_t4_evolution_phase(
        phase,
        "completed",
        payload,
        console=Console(file=buffer, color_system=None, width=120, highlight=False),
    )
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("phase", "payload", "label"),
    [
        (EvolutionPhase.EVIDENCE_ROUTING, {"atom_count": 8, "counts_by_reading_level": {"full_text": 2, "abstract_only": 6}, "counts_by_domain_role": {"bridge": 1}, "reading_upgrade_candidates": []}, "Evidence Routing"),
        (EvolutionPhase.OPPORTUNITY_MAP, {"opportunity_count": 3, "evidence_atoms": 8, "types": ["mechanism_gap"]}, "Opportunity Map"),
        (EvolutionPhase.FORMATION, {"routes": [{"route": "informed_brainstorm", "status": "supported", "candidate_ids": ["I1"]}], "candidate_count": 4}, "Multi-route Generation"),
        (EvolutionPhase.GENOME_FAMILY, {"population_id": "P0", "candidate_count": 4, "family_count": 3}, "Idea Genome & Family"),
        (EvolutionPhase.SCORING, {"population_id": "P0", "candidate_count": 4}, "Independent Scoring"),
        (EvolutionPhase.EVOLUTION_PLANNING, {"parent_count": 3, "mutation_count": 2, "crossover_count": 1}, "Evolution Planning"),
        (EvolutionPhase.OFFSPRING, {"planned_offspring": 3, "offspring_count": 3, "union_count": 7}, "Offspring & Rescoring"),
        (EvolutionPhase.SURVIVAL, {"population_id": "P1", "input_count": 4, "offspring_count": 3, "active_count": 4, "archived_count": 3, "portfolio_count": 2}, "Survival & Portfolio"),
    ],
)
def test_golden_t4_phase_ui_is_researcher_facing_and_never_leaks_raw_payload(phase, payload, label):
    rendered = _render_phase(phase, payload)

    assert "T4 · Round" in rendered
    assert label in rendered
    assert '"atom_count"' not in rendered
    assert '"candidate_count"' not in rendered
    assert "{" not in rendered


def test_golden_t4_prerun_ui_explains_standard_mode_without_raw_json():
    inspection = T4InputInspection(
        status="ready_with_warnings",
        input_fingerprint="a" * 64,
        materials={
            "core_deep_cards": 4,
            "core_abstract_cards": 7,
            "bridge_deep_cards": 1,
            "bridge_abstract_cards": 2,
            "synthesis_workbench": "available",
            "domain_map": "available",
            "user_seed_ideas": "loaded",
            "user_constraints": "loaded",
        },
        artifact_paths={"project": "project.yaml"},
        warnings=["Bridge evidence needs a reading upgrade before it can support a mechanism claim."],
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=True,
        final_top_k=2,
        max_initial_population=8,
        active_population_size=4,
        max_offspring_per_round=3,
        max_crossover_children=1,
        route_quotas={"evidence_routed_literature": 2, "informed_brainstorm": 2},
    )
    buffer = io.StringIO()
    render_t4_prerun(inspection, config, console=Console(file=buffer, color_system=None, width=120, highlight=False))
    rendered = buffer.getvalue()

    assert "Research Idea Formation & Evolution" in rendered
    assert "One complete P0 -> P1 Evolution Round" in rendered
    assert "resume and rollback" in rendered
    assert "Choose how to run T4" in rendered
    assert '"materials"' not in rendered


def test_golden_t4_gate_operation_views_explain_consequences_without_json(capsys):
    interface = CLIHumanInterface(no_color=True)
    interface._render_t4_directive_confirmation(
        {
            "action": "Create a Human-composed Candidate",
            "what_happens": "ResearchOS will use the reviewed Gene Donor Map, independently score the new Candidate, and preserve its sources.",
            "estimated_time": "A model-backed compatibility and scoring operation.",
            "version_policy": "Source Candidates remain available and can be rolled back to their current Population.",
            "next_stage": "Return to Gate1 for a comparison before any Candidate reaches T4.5.",
            "candidate_ids": ["I1", "I2"],
            "component_refs": ["I1-H1", "I2-H2"],
        }
    )
    interface._render_t4_directive_result(
        {
            "title": "Compatibility Check complete",
            "summary": "The requested components are compatible enough to create one new Candidate after confirmation.",
            "composition": {
                "composition_id": "HC-1",
                "composition_type": "complementary",
                "recommended_action": "compose",
                "explanation": "One bounded Core Thesis remains possible.",
                "gene_donor_map": {"problem": "I1", "mechanism": "I2"},
                "required_repairs": ["Keep the validation path within the documented resource boundary."],
            },
            "artifact": "ideation/human_compositions/HC-1/composition_plan.json",
        }
    )
    rendered = capsys.readouterr().out

    assert "Confirm this operation" in rendered
    assert "Compatibility Check" in rendered
    assert "Gene Donor Map" in rendered
    assert "Source Candidates remain available" in rendered
    assert '"composition_id"' not in rendered
