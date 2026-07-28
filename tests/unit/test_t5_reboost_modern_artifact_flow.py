from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from researchos.orchestration.state_machine import StateMachine
from researchos.orchestration.t5_t8_bridge import (
    accept_and_ingest_t5_handoff,
    prepare_t8_state,
    validate_t8_entry_state,
)
from researchos.schemas.state import GateState, StateYaml
from researchos.skills.audit import audit_skill_suite
from researchos.tools.external_experiment import (
    CompileResearchReboostHandoffTool,
    _allowed_path_rules_for_external_executor,
    _build_reboost_pack,
    _validate_research_reboost_pack,
    build_executor_selection_payload,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy
from researchos.runtime.workspace import initialize_workspace
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _modern_t45_workspace(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)


def _state_machine() -> StateMachine:
    repo_root = Path(__file__).resolve().parents[2]
    return StateMachine(
        repo_root / "config/system_config/state_machine.yaml",
        repo_root / "config/system_config/gates.yaml",
    )


def _compile_reboost_handoff(workspace: Path) -> None:
    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace,
        allowed_read_prefixes=["external_executor/"],
        allowed_write_prefixes=["external_executor/"],
        task_id="T5-REBOOST-GATE",
    )
    result = asyncio.run(CompileResearchReboostHandoffTool(policy).execute())
    assert result.ok, result.content


def _run_executor_script(skill: str, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "skills" / "external_executor_skills" / skill / "scripts" / script),
            *args,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_reboost_accepts_current_t45_artifacts_and_nested_protocol_fields(tmp_path: Path) -> None:
    _modern_t45_workspace(tmp_path)

    pack, report = _build_reboost_pack(tmp_path)

    assert pack["generation_status"] == "completed"
    assert report["missing_required_sources"] == []
    assert pack["validation_summary"]["required_source_coverage"] == 1.0
    assert {item["path"] for item in pack["source_manifest"] if item["requirement"] == "required"} >= {
        "ideation/research_blueprint.yaml",
        "ideation/claim_registry.yaml",
        "ideation/orientation_review.json",
    }
    assert len(pack["claim_evidence_matrix"]) == 2
    assert len(pack["minimum_experiment_loop"]["required_experiments"]) == 2

    handoff_path = tmp_path / "external_executor/handoff_pack.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    ok, error, _report = _validate_research_reboost_pack(tmp_path, handoff_path)

    assert ok is True, error


def test_repository_skill_suite_contract_audit_is_clean() -> None:
    report = audit_skill_suite(Path(__file__).resolve().parents[2])

    assert report["status"] == "pass"
    assert report["summary"] == {
        "total_skills": 56,
        "public_skills": 43,
        "external_executor_skills": 13,
        "failed_skills": 0,
        "script_help_checked": 0,
    }


def test_context_alignment_and_protocol_review_scripts_smoke_current_t5_contract(tmp_path: Path) -> None:
    """Exercise the two executor Skills that cannot be covered by LLM output alone."""

    _modern_t45_workspace(tmp_path)
    _compile_reboost_handoff(tmp_path)
    ext = tmp_path / "external_executor"
    selection = build_executor_selection_payload(selected_executor="codex_cli")
    _write(ext / "report" / "executor_selection.json", json.dumps(selection, ensure_ascii=False))

    preflight = _run_executor_script(
        "context-alignment",
        "preflight_context.py",
        "--workspace",
        str(tmp_path),
    )
    assert preflight.returncode == 0, preflight.stderr
    preflight_report = json.loads((ext / "report/phase_A/preflight_context.json").read_text(encoding="utf-8"))
    assert preflight_report["status"] in {"pass", "warning"}
    assert preflight_report["checks"]["capabilities"]["selected_executor"] == "codex_cli"

    inventory = _run_executor_script(
        "context-alignment",
        "inventory_sources.py",
        "--workspace",
        str(tmp_path),
    )
    assert inventory.returncode == 0, inventory.stderr
    inventory_report = json.loads((ext / "report/phase_A/context_source_inventory.json").read_text(encoding="utf-8"))
    assert len(inventory_report["alignment_fingerprint"]) == 64
    assert any(item["path"] == "external_executor/handoff_pack.json" for item in inventory_report["sources"])

    worktree = ext / "expr/implementation/ITER-SMOKE/IMPL-SMOKE/worktree"
    _write(worktree / "model.py", "def score(value):\n    return eval(value)\n")
    snapshot = _run_executor_script(
        "code-and-protocol-review",
        "snapshot_review_inputs.py",
        "--workspace",
        str(tmp_path),
        "--iteration-id",
        "ITER-SMOKE",
        "--path",
        "external_executor/expr/implementation/ITER-SMOKE/IMPL-SMOKE/worktree",
        "--output",
        "external_executor/reviews/ITER-SMOKE/input_snapshot.json",
    )
    assert snapshot.returncode == 0, snapshot.stderr
    snapshot_payload = json.loads((ext / "reviews/ITER-SMOKE/input_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot_payload["entries"][0]["path"].endswith("model.py")

    scan = _run_executor_script(
        "code-and-protocol-review",
        "scan_code_risks.py",
        "--workspace",
        str(tmp_path),
        "--snapshot",
        "external_executor/reviews/ITER-SMOKE/input_snapshot.json",
        "--output",
        "external_executor/reviews/ITER-SMOKE/static_candidates.json",
    )
    assert scan.returncode == 0, scan.stderr
    candidates = json.loads((ext / "reviews/ITER-SMOKE/static_candidates.json").read_text(encoding="utf-8"))
    assert any(item["rule_id"] == "DYNAMIC_EXECUTION" for item in candidates["candidates"])


def test_reboost_accepts_outcome_fields_and_derived_t45_baseline_maps(tmp_path: Path) -> None:
    """T5 must understand the canonical T4.5 field shape, not only legacy aliases."""

    _modern_t45_workspace(tmp_path)
    exp_plan_path = tmp_path / "ideation" / "exp_plan.yaml"
    exp_plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8"))
    for experiment in exp_plan["experiments"]:
        experiment.pop("metrics", None)
        experiment.pop("required_baselines", None)
        # Current Formalizer output can place the complete protocol in a
        # nested design object. T5 must not treat it as a legacy plan missing
        # its metrics or comparisons.
        experiment["design"] = {
            "primary_outcome": "Declared primary independent-performance outcome",
            "secondary_outcomes": ["Declared process-mediator outcome"],
            "baselines": ["Nested declared comparator"],
        }
    exp_plan_path.write_text(yaml.safe_dump(exp_plan, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # The current T4.5 compiler publishes the cross-claim baseline list here.
    # It is an authoritative declared comparison set, not a T5 inference.
    validation_map_path = tmp_path / "ideation" / "validation_map.yaml"
    validation_map = yaml.safe_load(validation_map_path.read_text(encoding="utf-8"))
    validation_map["baselines"] = [
        {
            "label": "Declared static comparison",
            "role": "static_guardrail_comparator",
            "description": "Required counterfactual declared by the formal research package.",
            "claim_refs": ["TC1", "MC1"],
        }
    ]
    validation_map_path.write_text(yaml.safe_dump(validation_map, allow_unicode=True, sort_keys=False), encoding="utf-8")

    pack, report = _build_reboost_pack(tmp_path)

    assert pack["generation_status"] == "completed"
    assert report["protocol_missing_fields"] == []
    metrics = pack["context_reboost"]["study_scope"]["metrics"]
    assert "Declared primary independent-performance outcome" in metrics
    assert "Declared process-mediator outcome" in metrics
    declared_baseline = next(
        item for item in pack["baseline_matrix"] if item["name"] == "Declared static comparison"
    )
    assert declared_baseline["role"] == "nearest_work"
    assert declared_baseline["source_refs"][0]["source_id"] == "SRC_VALIDATION_MAP"
    assert "static_guardrail_comparator" in declared_baseline["rationale"]
    assert any(item["name"] == "Nested declared comparator" for item in pack["baseline_matrix"])
    assert any(item["name"] == "Causal forest" for item in pack["baseline_matrix"])


def test_stale_t5_runtime_recovery_gate_self_resolves_after_valid_recompile(tmp_path: Path) -> None:
    """A persisted failure panel must not trap an already-valid T5 handoff."""

    _modern_t45_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_path,
        allowed_read_prefixes=["external_executor/"],
        allowed_write_prefixes=["external_executor/"],
        task_id="T5-REBOOST-GATE",
    )
    result = asyncio.run(CompileResearchReboostHandoffTool(policy).execute())
    assert result.ok, result.content
    receipt_path = tmp_path / "_runtime" / "recovery" / "t5-reboost-gate_runtime_recovery.json"
    _write(
        receipt_path,
        json.dumps(
            {
                "semantics": "runtime_recovery_directive",
                "target_task": "T5-REBOOST-GATE",
                "error_summary": "A prior compiler version rejected an obsolete baseline role.",
            }
        ),
    )

    state = StateYaml(
        project_id="test",
        current_task="T5-REBOOST-GATE",
        status="PAUSED",
        pending_gate=GateState(
            gate_id="runtime_recovery_gate",
            presented_at="2026-07-28T00:00:00+00:00",
            presentation={
                "runtime_recovery": {
                    "kind": "runtime",
                    "target_task": "T5-REBOOST-GATE",
                    "error_summary": "A prior compiler version rejected an obsolete baseline role.",
                }
            },
            options=[],
        ),
        task_context={"runtime_recovery": {"target_task": "T5-REBOOST-GATE"}},
    )

    refreshed = _state_machine().refresh_pending_gate_presentation(state, workspace_dir=tmp_path)

    assert refreshed.current_task == "T5-REBOOST-GATE"
    assert refreshed.status == "RUNNING"
    assert refreshed.pending_gate is None
    assert "runtime_recovery" not in refreshed.task_context
    assert refreshed.task_context["t5_reboost_recovery_resolved"]["resolution"] == (
        "existing_t5_reboost_artifacts_pass_task_checker"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "resolved"
    assert receipt["resolution"] == "existing_t5_reboost_artifacts_pass_task_checker"
    assert receipt["superseded_by"]["validation"] == "independent_task_artifact_checker"


def test_t4_reselection_cannot_enter_t8_using_retained_t5_artifacts(tmp_path: Path) -> None:
    """A reopened Candidate choice is a hard boundary for old execution evidence."""

    _modern_t45_workspace(tmp_path)
    StateYaml(
        project_id="test",
        current_task="T4-GATE1",
        status="PAUSED",
        task_context={
            "t4_gate1_reselection": {
                "semantics": "t4_explicit_gate1_reselection_boundary",
            }
        },
    ).dump_yaml(tmp_path / "state.yaml")
    before = (tmp_path / "state.yaml").read_text(encoding="utf-8")

    entry_ok, entry_error = validate_t8_entry_state(tmp_path)
    assert entry_ok is False
    assert entry_error is not None
    assert "T5-EXTERNAL-WAIT" in entry_error

    receipt = accept_and_ingest_t5_handoff(tmp_path)
    assert receipt["ok"] is False
    assert receipt["errors"][0]["code"] == "t8_entry_state_not_authorized"
    assert not (tmp_path / "drafts/t5_t8_handoff.json").exists()
    assert (tmp_path / "state.yaml").read_text(encoding="utf-8") == before

    with pytest.raises(ValueError, match="T5-EXTERNAL-WAIT"):
        prepare_t8_state(tmp_path, {"ok": True})


def test_reboost_keeps_legacy_scorecard_and_risk_artifacts_as_fallbacks(tmp_path: Path) -> None:
    _modern_t45_workspace(tmp_path)
    (tmp_path / "ideation/selected/selected_candidate.json").unlink()
    (tmp_path / "ideation/kill_criteria.yaml").unlink()
    _write(tmp_path / "ideation/idea_scorecard.yaml", "scores: {}\n")
    _write(tmp_path / "ideation/risks.md", "# Risks\nLegacy risk record.\n")

    pack, report = _build_reboost_pack(tmp_path)

    assert pack["generation_status"] == "blocked"
    assert report["missing_required_sources"] == []
    assert any("T4.5 formalization" in item["question"] for item in pack["unresolved_items"])


def test_reboost_preserves_post_novelty_research_context_without_promoting_it_to_results(tmp_path: Path) -> None:
    _modern_t45_workspace(tmp_path)
    _write(
        tmp_path / "ideation/research_dossier.json",
        json.dumps(
            {
                "semantics": "t45_research_dossier",
                "status": "formalized_after_novelty_pass",
                "candidate_id": "D1",
                    "selection_fingerprint": "unified-fingerprint",
                "novelty_audit_verdict": "pass_to_experiment",
                "central_thesis": {
                    "statement": "Planning structure changes uplift estimation.",
                    "evidence_status": "proposed_not_verified",
                    "source_paths": ["ideation/hypotheses.md"],
                },
                "research_problem": {
                    "statement": "Human-trained uplift models may misstate agentic-commerce treatment effects.",
                    "evidence_status": "proposed_not_verified",
                    "source_paths": ["ideation/selected/selected_candidate.json"],
                },
                "why_it_matters": {
                    "scholarly": [{"statement": "The study tests a structural source of transport failure."}],
                    "practical": [{"statement": "If supported, teams can audit planning interventions before deployment."}],
                    "commercial": [{"statement": "If supported, promotion design can be calibrated to agent decision paths."}],
                    "stakeholders_or_processes": [{"statement": "Marketing analysts and agent-commerce policy owners."}],
                },
                "contributions": [{"id": "C1", "statement": "Identify planning mismatch as a candidate mechanism."}],
                "hypotheses": [{"id": "H1", "statement": "Planning changes create larger estimation error."}],
                "evidence_boundary": {"statement": "The mechanism remains unverified."},
                "novelty_boundary": {"statement": "Compare against required baselines.", "required_baselines": []},
                "risks_and_kill_criteria": [{"statement": "Planning reuse may not occur in commerce."}],
                "traceability": {"source_artifacts": ["ideation/hypotheses.md"]},
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(tmp_path / "ideation/validation_map.yaml", "hypotheses: []\n")
    _write(tmp_path / "ideation/contribution_hypothesis_map.yaml", "contributions: []\n")

    pack, _report = _build_reboost_pack(tmp_path)
    context = pack["context_reboost"]["research_context"]

    assert context["research_problem"] == "The problem asks when a computational estimator remains identifiable and useful after the decision-maker changes."
    assert context["commercial_implications"] == [
        "If supported, promotion design can be calibrated to agent decision paths."
    ]
    assert context["evidence_status"] == "proposed_not_verified"
    assert "SRC_RESEARCH_DOSSIER" in [item["source_id"] for item in context["source_refs"]]
    assert "research_context" in pack["writer_handoff_contract"]["must_not_use_as_final_fact_source"]

    handoff_path = tmp_path / "external_executor/handoff_pack.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    ok, error, _report = _validate_research_reboost_pack(tmp_path, handoff_path)

    assert ok is True, error


def test_external_executor_rules_authorize_cross_skill_iteration_artifacts() -> None:
    rules = set(_allowed_path_rules_for_external_executor())

    assert "rw  external_executor/expr/" in rules
    assert "rw  external_executor/raw_results/" in rules
    assert "rw  external_executor/runs/" in rules
    assert "rw  external_executor/reviews/" in rules
    assert "rw  external_executor/report/phase_D/" in rules
    assert "rw  external_executor/report/phase_A/" in rules
    assert "rw  external_executor/report/phase_F/" in rules
    assert "rw  external_executor/report/run_manifest.json" in rules
    assert "rw  external_executor/report/" not in rules
    assert "rw  external_executor/figure/" in rules
    assert "rw  external_executor/table/" in rules


def test_workspace_initialization_creates_phase_report_directories(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, create_project_file=False)

    report = tmp_path / "external_executor/report"
    for phase in "ABCDEF":
        assert (report / f"phase_{phase}").is_dir()


def test_directory_guides_explain_current_skill_suite_and_t8_boundary(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, create_project_file=False)

    executor_guide = (tmp_path / "external_executor/_DIR_GUIDE.md").read_text(encoding="utf-8")
    suite_guide = (tmp_path / "external_executor/skills/_DIR_GUIDE.md").read_text(encoding="utf-8")
    phase_f_guide = (tmp_path / "external_executor/report/phase_F/_DIR_GUIDE.md").read_text(encoding="utf-8")
    figure_guide = (tmp_path / "external_executor/figure/_DIR_GUIDE.md").read_text(encoding="utf-8")

    assert "2026-07-skill-suite-t5-t8-v2" in executor_guide
    assert "six-file Writer Handoff" in executor_guide
    assert "T5-EXTERNAL-WAIT" in executor_guide
    assert "twelve child Skill" in suite_guide
    assert "writer_handoff_validation.json" in phase_f_guide
    assert "manifest" in figure_guide
