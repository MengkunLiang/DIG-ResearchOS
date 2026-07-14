from __future__ import annotations

import pytest

from researchos.ideation.directives import parse_idea_directive, persist_idea_directive
from researchos.ideation.models import PopulationSnapshot
from tests.unit.test_t4_population_evolution import FINGERPRINT


def _population():
    return PopulationSnapshot(
        population_id="P1",
        generation=1,
        input_fingerprint=FINGERPRINT,
        run_config_fingerprint=FINGERPRINT,
        active_candidate_ids=["I1", "I2"],
    )


def test_single_candidate_directive_is_explicit_and_confirmation_bound():
    directive = parse_idea_directive("Use I1 for the next stage", candidate_ids={"I1", "I2"})

    assert directive.action == "select_candidate"
    assert directive.target_candidate_ids == ["I1"]
    assert directive.confirmation_required is True


def test_multiple_candidates_are_not_implicitly_merged():
    with pytest.raises(ValueError, match="ambiguous"):
        parse_idea_directive("Select I1 and I2", candidate_ids={"I1", "I2"})

    directive = parse_idea_directive(
        "Keep I1 and I2 in parallel",
        candidate_ids={"I1", "I2"},
        llm_payload={"action": "keep_parallel", "target_candidate_ids": ["I1", "I2"]},
    )
    assert directive.action == "keep_parallel"
    assert directive.target_candidate_ids == ["I1", "I2"]


def test_component_composition_requires_component_level_references_and_persists_fingerprint(tmp_path):
    directive = parse_idea_directive(
        "Compose I1-H1 and I2-H1 as a new candidate",
        candidate_ids={"I1", "I2"},
    )
    assert directive.action == "compose_from_components"
    assert directive.component_refs == ["I1-H1", "I2-H1"]

    path = persist_idea_directive(tmp_path, directive=directive, population=_population())
    artifact = tmp_path / path
    assert artifact.exists()
    assert '"population_id": "P1"' in artifact.read_text(encoding="utf-8")


def test_route_regeneration_directive_extracts_a_declared_route_without_project_defaults():
    directive = parse_idea_directive(
        "Regenerate the cross-domain bridge route",
        candidate_ids={"I1", "I2"},
    )

    assert directive.action == "regenerate_route"
    assert directive.requested_route == "cross_domain_bridge"
