from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext
from researchos.tools.external_experiment import (
    AuditExperimentIntegrityTool,
    AuditPaperClaimsTool,
    BuildExperimentEvidencePackTool,
    BuildExperimentHandoffPackTool,
    BuildPostExperimentNoveltyCheckTool,
    IngestExternalResultsTool,
    MapResultsToClaimsTool,
    MockExternalDryRunTool,
    SKILL_SUITE,
    SelectExternalExecutorTool,
    _sha256,
    validate_context_reboost_handoff,
    validate_external_executor_ready,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _write_executor_selection(ws: Path, selected_executor: str) -> None:
    (ws / "external_executor").mkdir(parents=True, exist_ok=True)
    (ws / "external_executor" / "executor_selection.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "external_executor_selection",
                "selected_executor": selected_executor,
                "real_experiment_allowed": selected_executor != "mock_dry_run",
            }
        ),
        encoding="utf-8",
    )


def _write_minimal_workspace(ws: Path) -> None:
    (ws / "ideation").mkdir(parents=True)
    (ws / "literature").mkdir(parents=True)
    (ws / "project.yaml").write_text(
        "project_id: ext-test\n"
        "target_venue: NeurIPS\n"
        "seed_ensemble:\n"
        "  tier1_seeds: [42]\n",
        encoding="utf-8",
    )
    (ws / "ideation" / "hypotheses.md").write_text(
        "# Hypotheses\n\nH1: The proposed protocol can improve task_score under a controlled evaluation.\n",
        encoding="utf-8",
    )
    (ws / "ideation" / "novelty_audit.md").write_text(
        "Final Gate Verdict: pass_with_required_baselines\nLevel 2\n\n"
        "## Required Baselines\n\n"
        "- Baseline: SimGCL\n"
        "  Reason: canonical contrastive baseline.\n"
        "  Acceptable substitute: XSimGCL.\n"
        "  Claims blocked if missing: outperforms prior work, state-of-the-art\n",
        encoding="utf-8",
    )
    (ws / "ideation" / "exp_plan.yaml").write_text(
        "experiments:\n"
        "  - name: protocol_smoke\n"
        "    metrics:\n"
        "      - name: task_score\n"
        "    compute_estimate:\n"
        "      gpu_hours: 0\n",
        encoding="utf-8",
    )
    (ws / "literature" / "synthesis.md").write_text("## Method Families\nMock synthesis.\n", encoding="utf-8")
    (ws / "literature" / "comparison_table.csv").write_text("paper,method\nA,B\n", encoding="utf-8")


def _minimal_real_result_pack(raw: Path) -> dict:
    base = raw.parents[1]
    config = base / "configs" / "real_config.json"
    log = base / "logs" / "real.log"
    model = base / "workdir" / "model.py"
    figure = base / "figures" / "framework.svg"
    for path, content in [
        (config, '{"seed": 42}\n'),
        (log, "metric task_score=0.8\n"),
        (model, "class Core: pass\n"),
        (figure, "<svg></svg>\n"),
    ]:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    metric = {
        "metric_id": "m1",
        "name": "task_score",
        "value": 0.8,
        "source_artifact": "external_executor/raw_results/real.json",
        "config": "external_executor/configs/real_config.json",
        "log": "external_executor/logs/real.log",
        "seed": 42,
        "dataset_split": "test",
        "metric_direction": "higher_is_better",
        "run_id": "real",
    }
    return {
        "schema_version": "external_executor_result_pack.v1",
        "semantics": "external_executor_result_pack",
        "run_id": "real",
        "executor": "manual",
        "dry_run": False,
        "mock_only": False,
        "executor_status": {"current_state": "PARTIAL_RESULTS_READY"},
        "context_alignment": {"status": "pass"},
        "resources": {},
        "baseline_reproduction": [{"baseline_name": "SimGCL", "status": "completed"}],
        "experiment_runs": [{"run_id": "real", "status": "completed", "run_type": "formal"}],
        "metrics": [metric],
        "artifacts": [
            {
                "path": "external_executor/raw_results/real.json",
                "kind": "raw_results",
                "role": "real_raw_results",
                "sha256": _sha256(raw),
            },
            {
                "path": "external_executor/configs/real_config.json",
                "kind": "config",
                "role": "real_config",
                "sha256": _sha256(config),
            },
            {
                "path": "external_executor/logs/real.log",
                "kind": "log",
                "role": "real_log",
                "sha256": _sha256(log),
            },
        ],
        "baseline_coverage": {"status": "complete", "missing_baselines": []},
        "result_diagnosis": {},
        "module_attribution": {"ours_effective_modules": [{"module_id": "M1", "effect": "positive"}]},
        "realized_method_package": {
            "status": "implemented",
            "final_method_name": "RealMethod",
            "one_sentence_method": "A realized method.",
            "implemented_modules": [
                {
                    "module_id": "M1",
                    "name": "Core",
                    "code_paths": ["external_executor/workdir/model.py"],
                    "evidence_refs": ["external_executor/raw_results/real.json"],
                }
            ],
            "actual_algorithm_flow": [{"step": 1, "description": "Run core", "related_module": "M1"}],
        },
        "final_framework_figure": {
            "figure_id": "fig:framework",
            "status": "ready_for_T7_audit",
            "path": "external_executor/figures/framework.svg",
            "nodes": [{"id": "n1", "label": "Core", "module_id": "M1", "code_refs": ["external_executor/workdir/model.py"]}],
            "evidence_mapping": [{"figure_element": "n1", "source_ref": "external_executor/workdir/model.py"}],
            "caption_draft": "Framework.",
        },
        "figure_table_inventory": {
            "figures": [
                {
                    "figure_id": "fig:framework",
                    "path": "external_executor/figures/framework.svg",
                    "evidence_refs": ["external_executor/workdir/model.py"],
                }
            ],
            "tables": [
                {
                    "table_id": "tab:main",
                    "source_result": "external_executor/raw_results/real.json",
                }
            ],
        },
        "writer_handoff": {},
        "runs": [{"run_id": "real"}],
        "run_manifest": "external_executor/run_manifest.json",
    }


def _minimal_context_reboost() -> dict:
    return {
        "project_goal": "Evaluate an LLM-generated context re-boost.",
        "central_hypothesis": "LLM re-boosted hypothesis.",
        "method_mechanism": {
            "core_mechanism": "LLM-preserved mechanism",
            "must_preserve_components": ["mechanism"],
            "candidate_components": ["candidate"],
            "allowed_refinements": ["documented implementation refinement"],
            "forbidden_scope_changes": ["drop_required_baseline_without_claim_risk"],
        },
        "required_baselines": [{"baseline_name": "SimGCL", "reason_required": "novelty audit"}],
        "baseline_matrix": [{"baseline_name": "SimGCL", "status": "required"}],
        "claim_evidence_matrix": [{"claim": "C1", "metric": "task_score", "evidence_strength": "weak_until_run"}],
        "minimum_experiment_loop": [{"step": "baseline_reproduction"}],
        "iteration_budget": {"max_rounds": 3, "stop_conditions": ["budget_exhausted", "claim_must_be_narrowed"]},
        "claim_boundaries": [{"claim": "Do not claim SOTA until SimGCL is reproduced."}],
        "writer_handoff_contract": ["realized_method_package", "result_diagnosis"],
        "source_files_used": ["project.yaml", "ideation/hypotheses.md", "ideation/exp_plan.yaml"],
        "known_context_mismatches": [],
    }


def _write_reboost_outputs(ws: Path, context_reboost: dict | None = None) -> None:
    ext = ws / "external_executor"
    ext.mkdir(parents=True, exist_ok=True)
    context = context_reboost or _minimal_context_reboost()
    (ext / "handoff_pack.json").write_text(
        json.dumps(
            {
                "schema_version": "external_executor_handoff.v1",
                "semantics": "external_experiment_handoff_contract",
                "status": "context_reboost_completed",
                "context_reboost": context,
                "baseline_matrix": context["baseline_matrix"],
                "claim_evidence_matrix": context["claim_evidence_matrix"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (ext / "reboost_report.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "external_executor_context_reboost_report",
                "handoff_pack": "external_executor/handoff_pack.json",
                "source_files_used": context["source_files_used"],
                "missing_optional_sources": [],
                "known_context_mismatches": context["known_context_mismatches"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_reboost_validation_accepts_required_handoff_shape(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    _write_reboost_outputs(tmp_path)

    assert validate_context_reboost_handoff(tmp_path) == (True, None)
    ctx = ExecutionContext(tmp_path, "p", "T5-REBOOST-GATE", "r", mode="reboost")
    assert ExperimenterAgent().validate_outputs(ctx) == (True, None)


def test_reboost_prompt_uses_external_bridge_mode(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ctx = ExecutionContext(tmp_path, "p", "T5-REBOOST-GATE", "r", mode="reboost")

    prompt = ExperimenterAgent(mode="reboost").system_prompt(ctx)
    message = ExperimenterAgent(mode="reboost").initial_user_message(ctx)

    assert "External Experiment Bridge" in prompt
    assert "handoff_pack.json#context_reboost" in prompt
    assert "直接调用当前 LLM 能力" in message
    assert "context_reboost" in message


def test_reboost_validation_rejects_empty_claim_evidence_matrix(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    context = _minimal_context_reboost()
    context["claim_evidence_matrix"] = []
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "handoff_pack.json").write_text(
        json.dumps(
            {
                "schema_version": "external_executor_handoff.v1",
                "context_reboost": context,
                "baseline_matrix": context["baseline_matrix"],
                "claim_evidence_matrix": [],
            }
        ),
        encoding="utf-8",
    )

    ok, err = validate_context_reboost_handoff(tmp_path)
    assert not ok
    assert "claim_evidence_matrix" in (err or "")


@pytest.mark.asyncio
async def test_external_experiment_mock_dry_run_to_claim_audit(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )

    handoff = await BuildExperimentHandoffPackTool(policy).execute()
    assert handoff.ok
    assert (tmp_path / "external_executor" / "handoff_pack.json").exists()
    handoff_pack = json.loads((tmp_path / "external_executor" / "handoff_pack.json").read_text(encoding="utf-8"))
    assert handoff_pack["semantics"] == "external_experiment_handoff_contract"
    assert handoff_pack["required_baselines"][0]["baseline_name"] == "SimGCL"
    assert handoff_pack["workspace_relative_workdir"] == "external_executor/workdir"
    assert handoff_pack["workspace_relative_prompt"] == "external_executor/codex_prompt.md"
    assert "external_executor/workdir" in handoff_pack["host_workdir_hint"]
    assert (tmp_path / "novelty" / "required_baselines.json").exists()
    assert (tmp_path / "external_executor" / "executor_selection.json").exists()
    assert (tmp_path / "external_executor" / "input_manifest.json").exists()
    assert (tmp_path / "external_executor" / "codex_prompt.md").exists()
    assert (tmp_path / "external_executor" / "claude_code_prompt.md").exists()
    assert (tmp_path / "external_executor" / "manual_instructions.md").exists()
    assert (tmp_path / "external_executor" / "AGENTS.md").exists()
    assert (tmp_path / "external_executor" / "CLAUDE.md").exists()
    assert "UNSET" in (tmp_path / "external_executor" / "AGENTS.md").read_text(encoding="utf-8")

    selection = await SelectExternalExecutorTool(policy).execute(selected_executor="mock_dry_run")
    assert selection.ok
    assert "UNSET" not in (tmp_path / "external_executor" / "AGENTS.md").read_text(encoding="utf-8")

    dry_run = await MockExternalDryRunTool(policy).execute()
    assert dry_run.ok
    result_pack = json.loads((tmp_path / "external_executor" / "result_pack.json").read_text(encoding="utf-8"))
    assert result_pack["semantics"] == "external_executor_result_pack"
    assert result_pack["dry_run"] is True
    assert result_pack["mock_only"] is True
    assert result_pack["evidence_grade"] == "mock_only"
    assert result_pack["baseline_coverage"]["status"] == "mock_only"
    assert result_pack["run_manifest"] == "external_executor/run_manifest.json"
    assert (tmp_path / "external_executor" / "run_manifest.json").exists()
    assert (tmp_path / "external_executor" / "raw_results" / "mock_results.json").exists()
    assert result_pack["metrics"][0]["source_artifact"] == "external_executor/raw_results/mock_results.json"
    assert any(item.get("sha256") for item in result_pack["artifacts"])
    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )
    assert readiness["ok"] is True

    ingest = await IngestExternalResultsTool(policy).execute()
    assert ingest.ok
    summary = json.loads((tmp_path / "experiments" / "results_summary.json").read_text(encoding="utf-8"))
    assert summary["source"] == "external_executor"
    assert summary["quality_status"] == "mock_only"
    assert summary["selected_executor"] == "mock_dry_run"
    assert summary["executor_selection_ref"] == "external_executor/executor_selection.json"
    assert summary["result_pack_ref"] == "external_executor/result_pack.json"
    assert summary["executor_status_ref"] == "external_executor/executor_status.json"
    assert summary["selection_sha256"] == _sha256(tmp_path / "external_executor" / "executor_selection.json")
    assert summary["result_pack_sha256"] == _sha256(tmp_path / "external_executor" / "result_pack.json")
    assert summary["executor_status_sha256"] == _sha256(tmp_path / "external_executor" / "executor_status.json")
    evidence_index = json.loads((tmp_path / "experiments" / "evidence_index.json").read_text(encoding="utf-8"))
    assert evidence_index["result_pack_sha256"] == summary["result_pack_sha256"]
    assert "external_executor/raw_results/mock_results.json" in evidence_index["raw_result_files"]
    assert "external_executor/configs/mock_config.json" in evidence_index["config_files"]
    assert "external_executor/logs/mock_dry_run.log" in evidence_index["log_files"]
    assert evidence_index["scanned_artifacts"]["raw_results"]
    ingest_report = json.loads((tmp_path / "experiments" / "ingest_report.json").read_text(encoding="utf-8"))
    assert ingest_report["selection_sha256"] == summary["selection_sha256"]

    audit = await AuditExperimentIntegrityTool(policy).execute()
    assert audit.ok
    audit_data = json.loads((tmp_path / "experiments" / "integrity_audit.json").read_text(encoding="utf-8"))
    assert audit_data["status"] == "mock_only"
    assert audit_data["required_baseline_coverage"]["status"] == "mock_only"
    assert audit_data["result_pack_sha256"] == summary["result_pack_sha256"]
    assert (tmp_path / "experiments" / "result_audit.json").exists()
    result_audit = json.loads((tmp_path / "experiments" / "result_audit.json").read_text(encoding="utf-8"))
    assert result_audit["semantics"] == "external_experiment_result_audit"
    assert result_audit["metric_provenance"]["audited_metric_ids"]
    method_audit = json.loads((tmp_path / "experiments" / "method_audit.json").read_text(encoding="utf-8"))
    assert "method_consistency_audit" in method_audit
    method_resources = json.loads((tmp_path / "drafts" / "method_writing_resources.json").read_text(encoding="utf-8"))
    assert "method_writing_resources" in method_resources
    assert "method_consistency_audit" in method_resources["method_writing_resources"]
    assert (tmp_path / "experiments" / "experiment_fairness_review.md").exists()

    post_novelty = await BuildPostExperimentNoveltyCheckTool(policy).execute()
    assert post_novelty.ok
    post_novelty_data = json.loads((tmp_path / "novelty" / "post_experiment_novelty_check.json").read_text(encoding="utf-8"))
    assert post_novelty_data["semantics"] == "post_experiment_novelty_check"
    assert "mock_only_results_cannot_support_empirical_novelty" in post_novelty_data["claim_downgrades_required"]

    claims = await MapResultsToClaimsTool(policy).execute()
    assert claims.ok
    result_to_claim = json.loads((tmp_path / "drafts" / "result_to_claim.json").read_text(encoding="utf-8"))
    assert result_to_claim["semantics"] == "mechanical_result_to_claim_map_not_final_scientific_judgment"
    assert result_to_claim["schema_semantics"] == "result_to_claim_mapping_not_paper_text"
    assert result_to_claim["claim_mappings"][0]["support_status"] == "unsupported_mock_only"
    assert result_to_claim["claim_mappings"][0]["claim_strength"] == "unsupported"
    assert (tmp_path / "drafts" / "must_not_claim.md").exists()
    assert (tmp_path / "drafts" / "claim_support_matrix.csv").exists()

    pack = await BuildExperimentEvidencePackTool(policy).execute()
    assert pack.ok
    evidence_pack = json.loads((tmp_path / "drafts" / "experiment_evidence_pack.json").read_text(encoding="utf-8"))
    assert evidence_pack["semantics"] == "normalized_experiment_evidence_pack"
    assert evidence_pack["mock_only"] is True
    assert evidence_pack["metrics"]

    (tmp_path / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Experiments}\n"
        "The protocol dry-run reports task_score=0.71.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    paper_audit = await AuditPaperClaimsTool(policy).execute()
    assert paper_audit.ok
    paper_audit_json = json.loads((tmp_path / "drafts" / "paper_claim_audit.json").read_text(encoding="utf-8"))
    assert paper_audit_json["semantics"] == "paper_claim_audit_against_experiment_evidence_pack"
    assert paper_audit_json["input_fingerprints"]["paper_sha256"]
    assert paper_audit_json["mock_only"] is True
    assert paper_audit_json["summary"]["fail_count"] >= 1


@pytest.mark.asyncio
async def test_paper_claim_audit_fails_unsupported_experiment_number(tmp_path: Path):
    (tmp_path / "drafts").mkdir(parents=True)
    (tmp_path / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\section{Experiments} The final accuracy is 0.83."
        "\\end{document}",
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "experiment_evidence_pack.json").write_text(
        json.dumps(
            {
                "semantics": "normalized_experiment_evidence_pack",
                "mock_only": False,
                "metrics": [{"name": "accuracy", "value": 0.71}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "result_to_claim.json").write_text(
        json.dumps(
            {
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "claim_mappings": [],
                "global_must_not_claim": [],
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_path, ["", "drafts/"], ["drafts/"])

    result = await AuditPaperClaimsTool(policy).execute()

    assert result.ok
    audit = json.loads((tmp_path / "drafts" / "paper_claim_audit.json").read_text(encoding="utf-8"))
    assert audit["summary"]["fail_count"] == 1
    assert audit["issues"][0]["issue"] == "number_not_in_evidence_pack"
    assert audit["input_fingerprints"]["evidence_pack_sha256"]


@pytest.mark.asyncio
async def test_paper_claim_audit_fails_unsupported_small_integer_percentage(tmp_path: Path):
    (tmp_path / "drafts").mkdir(parents=True)
    (tmp_path / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\section{Experiments} The method improves accuracy by 15%."
        "\\end{document}",
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "experiment_evidence_pack.json").write_text(
        json.dumps(
            {
                "semantics": "normalized_experiment_evidence_pack",
                "mock_only": False,
                "metrics": [{"name": "accuracy_delta", "value": 0.04}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "result_to_claim.json").write_text(
        json.dumps(
            {
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "claim_mappings": [],
                "global_must_not_claim": [],
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_path, ["", "drafts/"], ["drafts/"])

    result = await AuditPaperClaimsTool(policy).execute()

    assert result.ok
    audit = json.loads((tmp_path / "drafts" / "paper_claim_audit.json").read_text(encoding="utf-8"))
    assert audit["summary"]["fail_count"] == 1
    assert audit["issues"][0]["number"] == "15%"


@pytest.mark.asyncio
async def test_paper_claim_audit_accepts_percentage_equivalent_metric(tmp_path: Path):
    (tmp_path / "drafts").mkdir(parents=True)
    (tmp_path / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\section{Experiments} The final accuracy is 92.3%."
        "\\end{document}",
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "experiment_evidence_pack.json").write_text(
        json.dumps(
            {
                "semantics": "normalized_experiment_evidence_pack",
                "mock_only": False,
                "metrics": [{"name": "accuracy", "value": 0.923}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "result_to_claim.json").write_text(
        json.dumps(
            {
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "claim_mappings": [],
                "global_must_not_claim": [],
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_path, ["", "drafts/"], ["drafts/"])

    result = await AuditPaperClaimsTool(policy).execute()

    assert result.ok
    audit = json.loads((tmp_path / "drafts" / "paper_claim_audit.json").read_text(encoding="utf-8"))
    assert audit["summary"]["fail_count"] == 0


@pytest.mark.asyncio
async def test_paper_claim_audit_preserves_negative_metric_sign(tmp_path: Path):
    (tmp_path / "drafts").mkdir(parents=True)
    (tmp_path / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\section{Experiments} The treatment effect estimate is -0.20."
        "\\end{document}",
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "experiment_evidence_pack.json").write_text(
        json.dumps(
            {
                "semantics": "normalized_experiment_evidence_pack",
                "mock_only": False,
                "metrics": [{"name": "effect", "value": -0.2}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "drafts" / "result_to_claim.json").write_text(
        json.dumps(
            {
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "claim_mappings": [],
                "global_must_not_claim": [],
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_path, ["", "drafts/"], ["drafts/"])

    result = await AuditPaperClaimsTool(policy).execute()

    assert result.ok
    audit = json.loads((tmp_path / "drafts" / "paper_claim_audit.json").read_text(encoding="utf-8"))
    assert audit["summary"]["fail_count"] == 0


@pytest.mark.asyncio
async def test_experimenter_validates_external_modes(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    agent = ExperimenterAgent()

    await BuildExperimentHandoffPackTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T5-HANDOFF", "r", mode="handoff")
    assert agent.validate_outputs(ctx) == (True, None)


@pytest.mark.asyncio
async def test_handoff_copies_repository_skill_templates_to_workspace(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )

    result = await BuildExperimentHandoffPackTool(policy).execute()

    assert result.ok
    manifest_path = tmp_path / "external_executor" / "skills" / "template_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["semantics"] == "external_executor_skill_template_manifest"
    assert manifest["customization_required"] is True
    assert manifest["customization_task"] == "T5-SKILL-CUSTOMIZATION-GATE"
    assert manifest["template_root"] == "skills/external_executor_skills"
    assert len(manifest["copied_skills"]) == 13
    assert manifest["customization_skill"]["destination"] == "external_executor/skills/skills_customization/SKILL.md"
    copied_names = {item["skill"] for item in manifest["copied_skills"]}
    assert "research_execution" in copied_names
    assert (tmp_path / "external_executor" / "skills" / "research_execution" / "SKILL.md").exists()
    assert (tmp_path / "external_executor" / "skills" / "skills_customization" / "SKILL.md").exists()
    customization_text = (
        tmp_path / "external_executor" / "skills" / "skills_customization" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "customization_report.json" in customization_text
    assert "configured LLM API" in manifest["customization_skill"]["instruction"]
    assert manifest["run_instruction"] == (
        "python -m researchos.cli run-task T5-SKILL-CUSTOMIZATION-GATE --workspace <workspace>"
    )
    assert manifest["report_path"] == "external_executor/skills/customization_report.json"
    assert manifest["shared_references"] == "external_executor/skills/shared-references"
    assert (
        tmp_path
        / "external_executor"
        / "skills"
        / "shared-references"
        / "result-pack-contract.md"
    ).exists()
    assert (
        tmp_path
        / "external_executor"
        / "skills"
        / "research_execution"
        / "references"
        / "execution_loop.md"
    ).exists()
    assert (
        tmp_path
        / "external_executor"
        / "skills"
        / "experiment_design"
        / "assets"
        / "claim_evidence_matrix_template.json"
    ).exists()
    assert "resume_instruction" not in manifest


@pytest.mark.asyncio
async def test_experimenter_validates_skill_customization_report(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    await BuildExperimentHandoffPackTool(policy).execute()
    report_path = tmp_path / "external_executor" / "skills" / "customization_report.json"
    report_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "external_executor_skill_customization_report",
                "handoff_pack": "external_executor/handoff_pack.json",
                "customized_skills": [{"skill": name} for name in SKILL_SUITE],
                "unchanged_or_skipped": [],
                "project_specific_fields_used": ["context_reboost", "baseline_matrix"],
                "next_instruction": "python -m researchos.cli resume --workspace <workspace>",
            }
        ),
        encoding="utf-8",
    )

    agent = ExperimenterAgent(mode="skill_customization")
    ctx = ExecutionContext(
        tmp_path,
        "p",
        "T5-SKILL-CUSTOMIZATION-GATE",
        "r",
        mode="skill_customization",
        outputs_expected={"skill_customization_report": report_path},
    )

    assert agent.validate_outputs(ctx) == (True, None)


@pytest.mark.asyncio
async def test_experimenter_rejects_incomplete_skill_customization_report(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    await BuildExperimentHandoffPackTool(policy).execute()
    report_path = tmp_path / "external_executor" / "skills" / "customization_report.json"
    report_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "external_executor_skill_customization_report",
                "handoff_pack": "external_executor/handoff_pack.json",
                "customized_skills": [{"skill": SKILL_SUITE[0]}],
                "unchanged_or_skipped": [],
                "project_specific_fields_used": [],
            }
        ),
        encoding="utf-8",
    )

    agent = ExperimenterAgent(mode="skill_customization")
    ctx = ExecutionContext(
        tmp_path,
        "p",
        "T5-SKILL-CUSTOMIZATION-GATE",
        "r",
        mode="skill_customization",
        outputs_expected={"skill_customization_report": report_path},
    )

    ok, err = agent.validate_outputs(ctx)
    assert ok is False
    assert "未覆盖 skill" in (err or "")


@pytest.mark.asyncio
async def test_handoff_preserves_existing_context_reboost(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True)
    existing_context = _minimal_context_reboost()
    (ext / "handoff_pack.json").write_text(
        json.dumps(
            {
                "schema_version": "external_executor_handoff.v1",
                "semantics": "external_experiment_handoff_contract",
                "status": "context_reboost_completed",
                "context_reboost": existing_context,
                "baseline_matrix": existing_context["baseline_matrix"],
                "claim_evidence_matrix": existing_context["claim_evidence_matrix"],
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )

    result = await BuildExperimentHandoffPackTool(policy).execute()

    assert result.ok
    handoff = json.loads((ext / "handoff_pack.json").read_text(encoding="utf-8"))
    assert handoff["context_reboost"]["project_goal"] == "Evaluate an LLM-generated context re-boost."
    assert handoff["context_reboost"]["method_mechanism"]["core_mechanism"] == "LLM-preserved mechanism"
    assert handoff["baseline_matrix"] == existing_context["baseline_matrix"]
    assert handoff["claim_evidence_matrix"] == existing_context["claim_evidence_matrix"]


def test_external_wait_rejects_missing_referenced_artifact(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )

    import asyncio

    asyncio.run(BuildExperimentHandoffPackTool(policy).execute())
    asyncio.run(SelectExternalExecutorTool(policy).execute(selected_executor="mock_dry_run"))
    asyncio.run(MockExternalDryRunTool(policy).execute())
    (tmp_path / "external_executor" / "raw_results" / "mock_results.json").unlink()

    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )

    assert readiness["ok"] is False
    assert any("referenced artifact missing" in issue for issue in readiness["issues"])
    assert (tmp_path / "external_executor" / "wait_rejection_report.md").exists()


def test_external_wait_rejects_stale_mock_pack_after_real_executor_selection(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )

    import asyncio

    asyncio.run(BuildExperimentHandoffPackTool(policy).execute())
    asyncio.run(SelectExternalExecutorTool(policy).execute(selected_executor="mock_dry_run"))
    asyncio.run(MockExternalDryRunTool(policy).execute())
    asyncio.run(SelectExternalExecutorTool(policy).execute(selected_executor="codex_cli"))

    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )

    assert readiness["ok"] is False
    assert readiness["selected_executor"] == "codex_cli"
    assert any("mock_only/dry_run result_pack" in issue for issue in readiness["issues"])


def test_external_wait_rejects_unfinalized_or_missing_executor_binding(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True)
    _write_executor_selection(tmp_path, "UNSET")
    raw = ext / "raw_results" / "real.json"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"metric": 0.8}\n', encoding="utf-8")
    (ext / "allowed_paths.txt").write_text(
        "rw  external_executor/raw_results/\n"
        "rw  external_executor/result_pack.json\n"
        "rw  external_executor/executor_status.json\n"
        "rw  external_executor/run_manifest.json\n",
        encoding="utf-8",
    )
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": [],
                "logs": [],
                "runs": [{"run_id": "real"}],
            }
        ),
        encoding="utf-8",
    )
    (ext / "result_pack.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_result_pack",
                "run_id": "real",
                "dry_run": False,
                "mock_only": False,
                "metrics": [
                    {
                        "metric_id": "m1",
                        "name": "task_score",
                        "value": 0.8,
                        "source_artifact": "external_executor/raw_results/real.json",
                    }
                ],
                "artifacts": [{"path": "external_executor/raw_results/real.json", "sha256": _sha256(raw)}],
                "runs": [{"run_id": "real"}],
                "run_manifest": "external_executor/run_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "status": "done"}),
        encoding="utf-8",
    )

    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )

    assert readiness["ok"] is False
    assert any("invalid or not finalized" in issue for issue in readiness["issues"])


def test_external_wait_rejects_status_self_acceptance(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )

    import asyncio

    asyncio.run(BuildExperimentHandoffPackTool(policy).execute())
    asyncio.run(SelectExternalExecutorTool(policy).execute(selected_executor="mock_dry_run"))
    asyncio.run(MockExternalDryRunTool(policy).execute())
    status_path = tmp_path / "external_executor" / "executor_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["accepted"] = True
    status_path.write_text(json.dumps(status), encoding="utf-8")

    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )

    assert readiness["ok"] is False
    assert any("accepted cannot be true" in issue for issue in readiness["issues"])


def test_external_wait_rejects_path_outside_allowed_paths(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True)
    _write_executor_selection(tmp_path, "manual")
    (ext / "allowed_paths.txt").write_text(
        "rw  external_executor/raw_results/\n"
        "rw  external_executor/configs/\n"
        "rw  external_executor/logs/\n"
        "rw  external_executor/result_pack.json\n"
        "rw  external_executor/executor_status.json\n"
        "rw  external_executor/run_manifest.json\n"
        "no  drafts/\n",
        encoding="utf-8",
    )
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "leak.json").write_text("{}\n", encoding="utf-8")
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["drafts/leak.json"],
                "configs": [],
                "logs": [],
            }
        ),
        encoding="utf-8",
    )
    (ext / "result_pack.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_result_pack",
                "run_id": "real",
                "executor": "manual",
                "dry_run": False,
                "mock_only": False,
                "metrics": [
                    {
                        "metric_id": "m1",
                        "name": "task_score",
                        "value": 0.8,
                        "source_artifact": "drafts/leak.json",
                    }
                ],
                "artifacts": [],
                "run_manifest": "external_executor/run_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "executor": "manual", "status": "done"}),
        encoding="utf-8",
    )

    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )

    assert readiness["ok"] is False
    assert any("path not allowed" in issue for issue in readiness["issues"])


def test_external_wait_rejects_missing_allowed_paths(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True)
    _write_executor_selection(tmp_path, "manual")
    raw = ext / "raw_results" / "real.json"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"metric": 0.8}\n', encoding="utf-8")
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": [],
                "logs": [],
                "runs": [{"run_id": "real"}],
            }
        ),
        encoding="utf-8",
    )
    (ext / "result_pack.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_result_pack",
                "run_id": "real",
                "executor": "manual",
                "dry_run": False,
                "mock_only": False,
                "metrics": [
                    {
                        "metric_id": "m1",
                        "name": "task_score",
                        "value": 0.8,
                        "source_artifact": "external_executor/raw_results/real.json",
                    }
                ],
                "artifacts": [
                    {
                        "path": "external_executor/raw_results/real.json",
                        "kind": "raw_results",
                        "role": "real_raw_results",
                        "sha256": "unused",
                    }
                ],
                "runs": [{"run_id": "real"}],
                "run_manifest": "external_executor/run_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "executor": "manual", "status": "done"}),
        encoding="utf-8",
    )

    readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )

    assert readiness["ok"] is False
    assert any("allowed_paths" in issue for issue in readiness["issues"])


def test_external_wait_rejects_partial_results_by_default(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True, exist_ok=True)
    _write_executor_selection(tmp_path, "manual")
    raw = ext / "raw_results" / "real.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('{"metric": 0.8}\n', encoding="utf-8")
    (ext / "configs").mkdir(exist_ok=True)
    (ext / "logs").mkdir(exist_ok=True)
    (ext / "allowed_paths.txt").write_text(
        "rw  external_executor/raw_results/\n"
        "rw  external_executor/configs/\n"
        "rw  external_executor/logs/\n"
        "rw  external_executor/result_pack.json\n"
        "rw  external_executor/executor_status.json\n"
        "rw  external_executor/run_manifest.json\n",
        encoding="utf-8",
    )
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": [],
                "logs": [],
                "runs": [{"run_id": "real"}],
            }
        ),
        encoding="utf-8",
    )
    (ext / "result_pack.json").write_text(
        json.dumps(_minimal_real_result_pack(raw)),
        encoding="utf-8",
    )
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "executor": "manual", "current_state": "PARTIAL_RESULTS_READY"}),
        encoding="utf-8",
    )

    default_readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
    )
    allowed_readiness = validate_external_executor_ready(
        tmp_path,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
        allow_partial_results=True,
    )

    assert default_readiness["ok"] is False
    assert any("PARTIAL_RESULTS_READY" in issue for issue in default_readiness["issues"])
    assert allowed_readiness["ok"] is True
    assert allowed_readiness["partial_results_allowed"] is True


@pytest.mark.asyncio
async def test_ingest_external_results_revalidates_wait_contract(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True)
    _write_executor_selection(tmp_path, "manual")
    raw = ext / "raw_results" / "real.json"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"metric": 0.8}\n', encoding="utf-8")
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": [],
                "logs": [],
                "runs": [{"run_id": "real"}],
            }
        ),
        encoding="utf-8",
    )
    (ext / "result_pack.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_result_pack",
                "run_id": "real",
                "executor": "manual",
                "dry_run": False,
                "mock_only": False,
                "metrics": [
                    {
                        "metric_id": "m1",
                        "name": "task_score",
                        "value": 0.8,
                        "source_artifact": "external_executor/raw_results/real.json",
                    }
                ],
                "artifacts": [],
                "runs": [{"run_id": "real"}],
                "run_manifest": "external_executor/run_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "executor": "manual", "status": "done"}),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "external_executor/", "experiments/"],
        ["external_executor/", "experiments/"],
    )

    result = await IngestExternalResultsTool(policy).execute()

    assert result.ok is False
    assert result.error == "external_result_not_ready"
    assert not (tmp_path / "experiments" / "results_summary.json").exists()


@pytest.mark.asyncio
async def test_t7_real_ingest_audit_and_claims_close_evidence(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    await BuildExperimentHandoffPackTool(policy).execute()
    await SelectExternalExecutorTool(policy).execute(selected_executor="manual")
    ext = tmp_path / "external_executor"
    raw = ext / "raw_results" / "real.json"
    config = ext / "configs" / "real_config.json"
    log = ext / "logs" / "real.log"
    model = ext / "workdir" / "model.py"
    figure = ext / "figures" / "framework.svg"
    table = ext / "tables" / "main.csv"
    patch_log = ext / "patches" / "patch_log.jsonl"
    for path, content in [
        (raw, '{"task_score": 0.8}\n'),
        (config, '{"seed": 42}\n'),
        (log, "metric task_score=0.8\n"),
        (model, "class Core: pass\n"),
        (figure, "<svg></svg>\n"),
        (table, "metric,value\ntask_score,0.8\n"),
        (patch_log, '{"patch":"model"}\n'),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": ["external_executor/configs/real_config.json"],
                "logs": ["external_executor/logs/real.log"],
                "artifacts": [{"path": "external_executor/workdir/model.py", "sha256": _sha256(model)}],
                "runs": [{"run_id": "real", "status": "completed"}],
            }
        ),
        encoding="utf-8",
    )
    result_pack = _minimal_real_result_pack(raw)
    result_pack["custom_unknown_field"] = {"kept_for_evidence_index": True}
    (ext / "result_pack.json").write_text(json.dumps(result_pack), encoding="utf-8")
    (ext / "executor_status.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_status",
                "executor": "manual",
                "status": "done",
                "accepted": False,
                "dry_run": False,
                "mock_only": False,
            }
        ),
        encoding="utf-8",
    )

    ingest = await IngestExternalResultsTool(policy).execute()
    assert ingest.ok
    evidence_index = json.loads((tmp_path / "experiments" / "evidence_index.json").read_text(encoding="utf-8"))
    assert "external_executor/figures/framework.svg" in evidence_index["figure_files"]
    assert "external_executor/tables/main.csv" in evidence_index["table_files"]
    assert "external_executor/patches/patch_log.jsonl" in evidence_index["patch_files"]
    assert evidence_index["extra_fields"]["custom_unknown_field"]["kept_for_evidence_index"] is True
    run_records = (tmp_path / "experiments" / "run_records.jsonl").read_text(encoding="utf-8")
    assert "external_executor_run_record" in run_records
    assert "external_executor_result_pack" in run_records

    audit = await AuditExperimentIntegrityTool(policy).execute()
    assert audit.ok
    result_audit = json.loads((tmp_path / "experiments" / "result_audit.json").read_text(encoding="utf-8"))
    assert result_audit["status"] == "pass"
    assert result_audit["metric_provenance"]["audited_metric_ids"] == ["m1"]
    method_audit = json.loads((tmp_path / "experiments" / "method_audit.json").read_text(encoding="utf-8"))
    consistency = method_audit["method_consistency_audit"]
    assert method_audit["contribution_drift"] == "none"
    assert consistency["method_intent_matches_realized_method"] is True
    assert consistency["realized_method_matches_code"] is True
    assert consistency["framework_figure_matches_code"] is True
    framework_audit = json.loads((tmp_path / "experiments" / "framework_figure_audit.json").read_text(encoding="utf-8"))
    assert framework_audit["status"] == "pass"

    claims = await MapResultsToClaimsTool(policy).execute()
    assert claims.ok
    result_to_claim = json.loads((tmp_path / "drafts" / "result_to_claim.json").read_text(encoding="utf-8"))
    assert result_to_claim["claim_mappings"][0]["claim_strength"] == "strong"
    method_resources = json.loads((tmp_path / "drafts" / "method_writing_resources.json").read_text(encoding="utf-8"))
    assert method_resources["method_writing_resources"]["method_consistency_audit"]["framework_figure_matches_code"] is True


@pytest.mark.asyncio
async def test_t7_claims_exclude_metrics_that_fail_result_audit(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    await BuildExperimentHandoffPackTool(policy).execute()
    await SelectExternalExecutorTool(policy).execute(selected_executor="manual")
    ext = tmp_path / "external_executor"
    raw = ext / "raw_results" / "real.json"
    config = ext / "configs" / "real_config.json"
    log = ext / "logs" / "real.log"
    model = ext / "workdir" / "model.py"
    figure = ext / "figures" / "framework.svg"
    for path, content in [
        (raw, '{"task_score": 0.8}\n'),
        (config, '{"seed": 42}\n'),
        (log, "metric task_score=0.8\n"),
        (model, "class Core: pass\n"),
        (figure, "<svg></svg>\n"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": ["external_executor/configs/real_config.json"],
                "logs": [],
                "runs": [{"run_id": "real", "status": "completed"}],
            }
        ),
        encoding="utf-8",
    )
    result_pack = _minimal_real_result_pack(raw)
    result_pack["metrics"][0].pop("log")
    result_pack["artifacts"] = [item for item in result_pack["artifacts"] if item["kind"] != "log"]
    (ext / "result_pack.json").write_text(json.dumps(result_pack), encoding="utf-8")
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "executor": "manual", "status": "done", "accepted": False}),
        encoding="utf-8",
    )

    await IngestExternalResultsTool(policy).execute()
    await AuditExperimentIntegrityTool(policy).execute()
    result_audit = json.loads((tmp_path / "experiments" / "result_audit.json").read_text(encoding="utf-8"))
    assert result_audit["status"] == "fail"
    assert result_audit["metric_provenance"]["audited_metric_ids"] == []

    claims = await MapResultsToClaimsTool(policy).execute()
    assert claims.ok
    result_to_claim = json.loads((tmp_path / "drafts" / "result_to_claim.json").read_text(encoding="utf-8"))
    assert result_to_claim["claim_mappings"] == []
    assert result_to_claim["excluded_metric_ids"] == ["m1"]
    assert "failed result audit" in "\n".join(result_to_claim["global_must_not_claim"])


@pytest.mark.asyncio
async def test_t7_method_audit_detects_contribution_drift(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    await BuildExperimentHandoffPackTool(policy).execute()
    await SelectExternalExecutorTool(policy).execute(selected_executor="manual")
    ext = tmp_path / "external_executor"
    raw = ext / "raw_results" / "real.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('{"task_score": 0.8}\n', encoding="utf-8")
    result_pack = _minimal_real_result_pack(raw)
    result_pack["realized_method_package"]["implemented_modules"][0]["module_id"] = "M2"
    result_pack["final_framework_figure"]["nodes"][0]["module_id"] = "M2"
    (ext / "run_manifest.json").write_text(
        json.dumps(
            {
                "semantics": "external_executor_run_manifest",
                "run_id": "real",
                "executor": "manual",
                "raw_results": ["external_executor/raw_results/real.json"],
                "configs": ["external_executor/configs/real_config.json"],
                "logs": ["external_executor/logs/real.log"],
                "runs": [{"run_id": "real", "status": "completed"}],
            }
        ),
        encoding="utf-8",
    )
    (ext / "result_pack.json").write_text(json.dumps(result_pack), encoding="utf-8")
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "executor": "manual", "status": "done", "accepted": False}),
        encoding="utf-8",
    )

    await IngestExternalResultsTool(policy).execute()
    await AuditExperimentIntegrityTool(policy).execute()

    method_audit = json.loads((tmp_path / "experiments" / "method_audit.json").read_text(encoding="utf-8"))
    consistency = method_audit["method_consistency_audit"]
    assert method_audit["contribution_drift"] == "minor"
    assert consistency["method_intent_matches_realized_method"] is False
    assert consistency["requires_post_novelty_check"] is True
    assert consistency["required_action"] == "narrow_claim"


@pytest.mark.asyncio
async def test_t7_ingest_validator_rejects_stale_external_binding(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    agent = ExperimenterAgent()

    await BuildExperimentHandoffPackTool(policy).execute()
    await SelectExternalExecutorTool(policy).execute(selected_executor="mock_dry_run")
    await MockExternalDryRunTool(policy).execute()
    await IngestExternalResultsTool(policy).execute()
    result_pack_path = tmp_path / "external_executor" / "result_pack.json"
    result_pack = json.loads(result_pack_path.read_text(encoding="utf-8"))
    result_pack["limitations"].append("tampered after ingest")
    result_pack_path.write_text(json.dumps(result_pack), encoding="utf-8")

    ctx = ExecutionContext(tmp_path, "p", "T7-INGEST", "r", mode="result_ingest")
    ok, err = agent.validate_outputs(ctx)

    assert ok is False
    assert "hash" in (err or "")


@pytest.mark.asyncio
async def test_experimenter_external_chain_validates_modes(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/", "novelty/", "resources/"],
        ["external_executor/", "experiments/", "drafts/", "novelty/"],
    )
    agent = ExperimenterAgent()

    await BuildExperimentHandoffPackTool(policy).execute()
    await SelectExternalExecutorTool(policy).execute(selected_executor="mock_dry_run")
    ctx = ExecutionContext(tmp_path, "p", "T5-EXECUTOR-GATE", "r", mode="executor_gate")
    assert agent.validate_outputs(ctx) == (True, None)

    await MockExternalDryRunTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T5-DRY-RUN", "r", mode="dry_run")
    assert agent.validate_outputs(ctx) == (True, None)

    result_pack_path = tmp_path / "external_executor" / "result_pack.json"
    tampered = json.loads(result_pack_path.read_text(encoding="utf-8"))
    tampered["artifacts"][0]["sha256"] = "bad-hash"
    result_pack_path.write_text(json.dumps(tampered), encoding="utf-8")
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "hash" in err
    await MockExternalDryRunTool(policy).execute()

    await IngestExternalResultsTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T7-INGEST", "r", mode="result_ingest")
    assert agent.validate_outputs(ctx) == (True, None)

    await AuditExperimentIntegrityTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T7-AUDIT", "r", mode="integrity_audit")
    assert agent.validate_outputs(ctx) == (True, None)

    await BuildPostExperimentNoveltyCheckTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T7-POST-NOVELTY", "r", mode="post_novelty")
    assert agent.validate_outputs(ctx) == (True, None)

    await MapResultsToClaimsTool(policy).execute()
    await BuildExperimentEvidencePackTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T7-CLAIMS", "r", mode="result_to_claim")
    assert agent.validate_outputs(ctx) == (True, None)
