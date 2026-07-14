from __future__ import annotations

import io

from rich.console import Console

from researchos.ideation.models import EvolutionPhase
from researchos.ui.idea_evolution_renderer import render_t4_evolution_phase


def _render(phase, status, payload) -> str:
    buffer = io.StringIO()
    render_t4_evolution_phase(phase, status, payload, console=Console(file=buffer, width=140, highlight=False))
    return buffer.getvalue()


def test_evidence_routing_rich_panel_has_researcher_facing_metrics_only():
    rendered = _render(
        EvolutionPhase.EVIDENCE_ROUTING,
        "completed",
        {
            "atom_count": 12,
            "counts_by_reading_level": {"full_text": 4, "partial_text": 2, "abstract_only": 6},
            "counts_by_domain_role": {"core": 9, "bridge": 3},
            "reading_upgrade_candidates": ["EA-1", "EA-2"],
        },
    )

    assert "Evidence Routing" in rendered
    assert "Paper-note sections indexed" in rendered
    assert "12" in rendered
    assert "EA-1" not in rendered
    assert "{" not in rendered


def test_survival_rich_panel_explains_population_change_without_candidate_content():
    rendered = _render(
        EvolutionPhase.SURVIVAL,
        "completed",
        {"population_id": "P1", "p0_count": 12, "offspring_count": 4, "active_count": 7, "archived_count": 9, "portfolio_count": 3},
    )

    assert "Survival & Portfolio" in rendered
    assert "P1 active candidates" in rendered
    assert "Visible Portfolio" in rendered
    assert "candidate title" not in rendered.casefold()
