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
    IngestExternalResultsTool,
    MapResultsToClaimsTool,
    MockExternalDryRunTool,
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
        "Final Gate Verdict: pass_to_experiment\nLevel 2\n",
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
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/"],
        ["external_executor/", "experiments/", "drafts/"],
    )

    handoff = await BuildExperimentHandoffPackTool(policy).execute()
    assert handoff.ok
    assert (tmp_path / "external_executor" / "handoff_pack.json").exists()
    assert (tmp_path / "external_executor" / "executor_selection.json").exists()
    assert (tmp_path / "external_executor" / "input_manifest.json").exists()
    assert (tmp_path / "external_executor" / "codex_prompt.md").exists()
    assert (tmp_path / "external_executor" / "claude_code_prompt.md").exists()
    assert (tmp_path / "external_executor" / "manual_instructions.md").exists()

    dry_run = await MockExternalDryRunTool(policy).execute()
    assert dry_run.ok
    result_pack = json.loads((tmp_path / "external_executor" / "result_pack.json").read_text(encoding="utf-8"))
    assert result_pack["semantics"] == "external_executor_result_pack"
    assert result_pack["dry_run"] is True
    assert result_pack["mock_only"] is True
    assert result_pack["run_manifest"] == "external_executor/run_manifest.json"
    assert (tmp_path / "external_executor" / "run_manifest.json").exists()
    assert (tmp_path / "external_executor" / "raw_results" / "mock_results.json").exists()
    assert result_pack["metrics"][0]["source_artifact"] == "external_executor/raw_results/mock_results.json"
    assert any(item.get("sha256") for item in result_pack["artifacts"])

    ingest = await IngestExternalResultsTool(policy).execute()
    assert ingest.ok
    summary = json.loads((tmp_path / "experiments" / "results_summary.json").read_text(encoding="utf-8"))
    assert summary["source"] == "external_executor"
    assert summary["quality_status"] == "mock_only"

    audit = await AuditExperimentIntegrityTool(policy).execute()
    assert audit.ok
    audit_data = json.loads((tmp_path / "experiments" / "integrity_audit.json").read_text(encoding="utf-8"))
    assert audit_data["status"] == "mock_only"

    claims = await MapResultsToClaimsTool(policy).execute()
    assert claims.ok
    result_to_claim = json.loads((tmp_path / "drafts" / "result_to_claim.json").read_text(encoding="utf-8"))
    assert result_to_claim["semantics"] == "mechanical_result_to_claim_map_not_final_scientific_judgment"
    assert result_to_claim["claim_mappings"][0]["support_status"] == "unsupported_mock_only"

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
    assert paper_audit_json["mock_only"] is True
    assert paper_audit_json["summary"]["fail_count"] >= 1


@pytest.mark.asyncio
async def test_experimenter_validates_external_modes(tmp_path: Path):
    _write_minimal_workspace(tmp_path)
    policy = WorkspaceAccessPolicy(
        tmp_path,
        ["", "ideation/", "literature/", "external_executor/", "experiments/", "drafts/"],
        ["external_executor/", "experiments/", "drafts/"],
    )
    agent = ExperimenterAgent()

    await BuildExperimentHandoffPackTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T5-HANDOFF", "r", mode="handoff")
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

    await MapResultsToClaimsTool(policy).execute()
    await BuildExperimentEvidencePackTool(policy).execute()
    ctx = ExecutionContext(tmp_path, "p", "T7-CLAIMS", "r", mode="result_to_claim")
    assert agent.validate_outputs(ctx) == (True, None)
