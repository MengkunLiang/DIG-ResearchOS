from __future__ import annotations

import io

from rich.console import Console

from researchos.orchestration.state_machine import StateMachine
from researchos.ideation.config import load_t4_evolution_settings
from researchos.ideation.evidence import build_idea_evidence_index
from researchos.ideation.prerun import default_run_config, inspect_t4_inputs, parse_t4_prerun_intent
from researchos.ideation.models import EvidencePermission, ReadingLevel
from researchos.ui.idea_prerun_renderer import render_t4_prerun
from researchos.runtime.system_config import system_config_path


def _write_required_inputs(workspace):
    (workspace / "literature" / "paper_notes").mkdir(parents=True)
    (workspace / "literature" / "paper_notes_abstract").mkdir()
    (workspace / "literature" / "paper_notes_bridge").mkdir()
    (workspace / "user_seeds").mkdir()
    (workspace / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("synthesis\n", encoding="utf-8")
    (workspace / "literature" / "synthesis_workbench.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "domain_map.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "comparison_table.csv").write_text("id,title\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_ideas.md").write_text("# Seed ideas\n- candidate\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_constraints.md").write_text("# Seed constraints\n- boundary\n", encoding="utf-8")


def test_preflight_uses_actual_materials_and_marks_abstract_bridge_warning(tmp_path):
    _write_required_inputs(tmp_path)
    (tmp_path / "literature" / "paper_notes" / "p1.md").write_text("# Paper\n\n## Mechanism\n\nfull evidence\n", encoding="utf-8")
    (tmp_path / "literature" / "paper_notes_abstract" / "p2.md").write_text("# Paper [ABSTRACT]\n\n## Core\n\nabstract evidence\n", encoding="utf-8")
    (tmp_path / "literature" / "paper_notes_bridge" / "b1.md").write_text("# Bridge [ABSTRACT]\n\n## Transfer\n\nbridge hint\n", encoding="utf-8")
    inspection = inspect_t4_inputs(tmp_path)
    assert inspection.status == "ready_with_warnings"
    assert inspection.materials["core_deep_cards"] == 1
    assert inspection.materials["core_abstract_cards"] == 1
    assert inspection.materials["bridge_abstract_cards"] == 1
    assert any("abstract-only" in warning for warning in inspection.warnings)


def test_prerun_parser_and_renderer_have_user_facing_consequences(tmp_path):
    _write_required_inputs(tmp_path)
    directive = parse_t4_prerun_intent("Run Deep without crossover and show 2 candidates")
    assert directive.mode == "deep"
    assert directive.allow_crossover is False
    assert directive.final_top_k == 2
    assert directive.needs_confirmation is True
    config = default_run_config(load_t4_evolution_settings(), directive)
    inspection = inspect_t4_inputs(tmp_path)
    stream = io.StringIO()
    render_t4_prerun(inspection, config, console=Console(file=stream, force_terminal=False, width=120))
    rendered = stream.getvalue()
    assert "Research Idea Formation & Evolution" in rendered
    assert "P0" in rendered and "P1" in rendered
    assert "rollback" in rendered.casefold()
    assert "{\"" not in rendered


def test_evidence_index_includes_shallow_recall_without_elevating_permission(tmp_path):
    _write_required_inputs(tmp_path)
    (tmp_path / "literature" / "paper_notes" / "p1.md").write_text(
        "# Full Note\n\n## Mechanism Claim\n\nA bounded mechanism statement.\n",
        encoding="utf-8",
    )
    (tmp_path / "literature" / "paper_notes_abstract" / "p2.md").write_text(
        "# Abstract Note [ABSTRACT]\n\n## Core Approach\n\nA shallow inspiration.\n",
        encoding="utf-8",
    )
    result = build_idea_evidence_index(tmp_path)
    atoms = result["atoms"]
    assert len(atoms) >= 2
    abstract_atoms = [atom for atom in atoms if atom.reading_level == ReadingLevel.ABSTRACT_ONLY]
    assert abstract_atoms
    assert EvidencePermission.MECHANISM_SUPPORT not in abstract_atoms[0].allowed_uses
    assert EvidencePermission.FINAL_CLAIM in abstract_atoms[0].forbidden_uses
    assert (tmp_path / "ideation" / "evidence" / "evidence_index.jsonl").exists()
    assert result["summary"]["counts_by_reading_level"]["full_text"] >= 1


def test_t4_prerun_gate_stays_inside_t4_and_rechecks_changed_inputs(tmp_path):
    _write_required_inputs(tmp_path)
    machine = StateMachine(system_config_path("state_machine.yaml"), system_config_path("gates.yaml"))
    state = machine.create_initial_state("test")
    state.current_task = "T4"
    assert machine.should_pause_for_immediate_gate(state, workspace_dir=tmp_path)
    state = machine.pause_for_immediate_gate(state, workspace_dir=tmp_path)
    assert state.current_task == "T4"
    assert state.pending_gate is not None
    assert state.pending_gate.gate_id == "t4_prerun_gate"
    assert "t4_prerun" in state.pending_gate.presentation

    state = machine.resolve_pending_gate(
        state,
        {"option_id": "start_standard", "captured": {}},
        workspace_dir=tmp_path,
    )
    assert state.current_task == "T4"
    assert state.status == "RUNNING"
    assert (tmp_path / "ideation" / "t4_run_config.json").exists()
    assert not machine.should_pause_for_immediate_gate(state, workspace_dir=tmp_path)

    (tmp_path / "literature" / "synthesis.md").write_text("changed synthesis\n", encoding="utf-8")
    assert machine.should_pause_for_immediate_gate(state, workspace_dir=tmp_path)
