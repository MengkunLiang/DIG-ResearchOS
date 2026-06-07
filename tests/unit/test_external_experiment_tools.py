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
    SelectExternalExecutorTool,
    _sha256,
    validate_external_executor_ready,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


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

    audit = await AuditExperimentIntegrityTool(policy).execute()
    assert audit.ok
    audit_data = json.loads((tmp_path / "experiments" / "integrity_audit.json").read_text(encoding="utf-8"))
    assert audit_data["status"] == "mock_only"
    assert audit_data["required_baseline_coverage"]["status"] == "mock_only"
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


def test_external_wait_rejects_path_outside_allowed_paths(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    ext = tmp_path / "external_executor"
    ext.mkdir(parents=True)
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
        json.dumps({"semantics": "external_executor_status", "status": "done"}),
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
        json.dumps({"semantics": "external_executor_status", "status": "done"}),
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
                        "sha256": _sha256(raw),
                    }
                ],
                "runs": [{"run_id": "real"}],
                "run_manifest": "external_executor/run_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    (ext / "executor_status.json").write_text(
        json.dumps({"semantics": "external_executor_status", "current_state": "PARTIAL_RESULTS_READY"}),
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
        json.dumps({"semantics": "external_executor_status", "status": "done"}),
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
