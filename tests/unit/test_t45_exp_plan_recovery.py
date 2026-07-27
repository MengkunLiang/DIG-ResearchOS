from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from researchos.agents.novelty_auditor import _t45_exp_plan_recovery_instruction
from researchos.ideation.formalization import validate_blueprint_and_claim_registry
from researchos.ideation.proposal import repair_t45_proposal_manifest
from researchos.runtime.progress import summarize_tool_result
from researchos.tools.filesystem import EditFileTool, WriteFileTool
from researchos.tools.formalization_validation import ValidateT45FormalizationSourcesTool
from researchos.tools.structured_file import WriteStructuredFileTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace


def _policy(workspace: Path, *, task_id: str = "T4.5-FORMALIZE") -> WorkspaceAccessPolicy:
    return WorkspaceAccessPolicy(
        workspace_dir=workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=["ideation/"],
        task_id=task_id,
    )


def test_t45_cannot_publish_formalization_before_structured_exp_plan(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "exp_plan.yaml").unlink()
    receipt = tmp_path / "ideation/post_novelty_formalization.json"
    original_receipt = receipt.read_text(encoding="utf-8")
    tool = WriteFileTool(_policy(tmp_path))

    result = asyncio.run(
        tool.execute(
            path="ideation/post_novelty_formalization.json",
            content='{"status": "formalized_after_novelty_pass"}',
        )
    )

    assert result.ok is False
    assert result.error == "t45_formalization_requires_structured_sources"
    assert result.data["required_path"] == "ideation/exp_plan.yaml"
    assert "exp_plan" in result.data["validation_error"]
    assert receipt.read_text(encoding="utf-8") == original_receipt


def test_t45_allows_formalization_after_valid_exp_plan(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    tool = WriteFileTool(_policy(tmp_path))

    result = asyncio.run(
        tool.execute(
            path="ideation/post_novelty_formalization.json",
            content='{"status": "formalized_after_novelty_pass"}',
        )
    )

    assert result.ok is True
    assert (tmp_path / "ideation/post_novelty_formalization.json").is_file()


def test_t45_structured_source_validator_reports_ready_without_writing(tmp_path: Path) -> None:
    """The Formalizer needs a deterministic checkpoint before composing prose."""

    populate_valid_t45_workspace(tmp_path)
    result = asyncio.run(ValidateT45FormalizationSourcesTool(_policy(tmp_path)).execute())

    assert result.ok is True
    assert result.data["valid"] is True
    assert "may now write" in result.content


def test_t45_structured_source_validator_explains_utd_synchronized_claim_repair(tmp_path: Path) -> None:
    """A UTD claim gap is real, but its repair must not be presented as a file-missing error."""

    populate_valid_t45_workspace(tmp_path, orientation="utd")
    registry_path = tmp_path / "ideation" / "claim_registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    for claim in registry["claims"]:
        claim["claim_type"] = "mechanism"
    registry_path.write_text(yaml.safe_dump(registry, allow_unicode=True, sort_keys=False), encoding="utf-8")

    result = asyncio.run(ValidateT45FormalizationSourcesTool(_policy(tmp_path)).execute())

    assert result.ok is True
    assert result.data["valid"] is False
    assert "UTD formalization" in result.data["validation_error"]
    assert "minimal synchronized change set" in result.content


def test_edit_file_cannot_bypass_t45_structured_output_guard(tmp_path: Path) -> None:
    """The compatibility surface must retain WriteFileTool's schema boundary."""

    populate_valid_t45_workspace(tmp_path)
    tool = EditFileTool(_policy(tmp_path))

    result = asyncio.run(
        tool.execute(
            path="ideation/exp_plan.yaml",
            content="not a schema-valid experiment plan",
        )
    )

    assert result.ok is False
    assert result.error == "structured_output_requires_write_structured_file"


def test_blueprint_writer_canonicalizes_known_formalization_aliases(tmp_path: Path) -> None:
    """A valid idea must not be rejected only because an LLM used safe aliases."""

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    approach = blueprint["proposed_approach"]
    approach["design_rationales"] = [
        {
            "component_refs": ["COMP1"],
            "rationale": "Task-conditioned features are necessary because static representations cannot separate contextual shift from genuine treatment response.",
        },
        {
            "component_refs": ["COMP2"],
            "rationale": "Feedback measurement is necessary because an observed outcome shift could otherwise be attributed to direct treatment response.",
        },
    ]
    approach["alternatives_considered"] = [
        {
            "simpler_alternative": "A static feedback-blind estimator",
            "why_insufficient": "It cannot distinguish feedback-mediated response changes from the contextual representation shift that the proposed design must test.",
        }
    ]

    result = asyncio.run(
        WriteStructuredFileTool(_policy(tmp_path)).execute(
            path="ideation/research_blueprint.yaml",
            data=blueprint,
            schema_name="research_blueprint",
            format="yaml",
        )
    )

    assert result.ok is True
    assert result.data["normalizations"]
    persisted = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    rationales = persisted["proposed_approach"]["design_rationales"]
    assert {item["component_id"] for item in rationales} == {"COMP1", "COMP2"}
    alternative = persisted["proposed_approach"]["alternatives_considered"][0]
    assert alternative["alternative"] == "A static feedback-blind estimator"
    assert "reason_not_sufficient" in alternative
    valid, error = validate_blueprint_and_claim_registry(tmp_path)
    assert valid is True, error


def test_blueprint_writer_rejects_missing_design_comparison_without_overwriting(tmp_path: Path) -> None:
    """The schema must reject an incomplete alternative before prose is blocked later."""

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    original = blueprint_path.read_text(encoding="utf-8")
    blueprint = yaml.safe_load(original)
    blueprint["proposed_approach"]["alternatives_considered"] = [
        {"alternative": "A simpler static estimator"}
    ]

    result = asyncio.run(
        WriteStructuredFileTool(_policy(tmp_path)).execute(
            path="ideation/research_blueprint.yaml",
            data=blueprint,
            schema_name="research_blueprint",
            format="yaml",
        )
    )

    assert result.ok is False
    assert result.error == "schema_validation_failed"
    assert "reason_not_sufficient" in result.content
    assert blueprint_path.read_text(encoding="utf-8") == original


def test_proposal_manifest_repair_does_not_legalize_missing_exp_plan(tmp_path: Path) -> None:
    proposal = tmp_path / "ideation/proposal/research_proposal.md"
    proposal.parent.mkdir(parents=True)
    proposal.write_text("# Proposal\n", encoding="utf-8")
    audit = tmp_path / "ideation/novelty_audit.md"
    audit.write_text("Final Gate Verdict: pass\n", encoding="utf-8")

    changed, reason = repair_t45_proposal_manifest(tmp_path, audit)

    assert changed is False
    assert reason == "cannot repair proposal manifest before exp_plan.yaml is readable"


def test_t45_resume_message_focuses_missing_exp_plan() -> None:
    workspace = Path(__file__).resolve().parents[2] / "tests" / "manual" / "_nonexistent_t45_workspace"

    instruction = _t45_exp_plan_recovery_instruction(workspace)

    assert "exp_plan.yaml" in instruction
    assert "write_structured_file" in instruction
    assert "不要再次改写 novelty_audit.md" in instruction


def test_structured_parameter_failure_is_actionable_in_cli() -> None:
    summary, path = summarize_tool_result(
        tool_name="write_structured_file",
        ok=False,
        content="Parameter validation error: path must be a string",
        data={
            "path": None,
            "required_path": "ideation/exp_plan.yaml",
            "required_schema": "exp_plan",
        },
        error="parameter_validation",
    )

    assert "结构化文件未调用" in summary
    assert "exp_plan" in summary
    assert path == "ideation/exp_plan.yaml"


def test_blueprint_parameter_failure_names_the_actual_first_source() -> None:
    summary, path = summarize_tool_result(
        tool_name="write_structured_file",
        ok=False,
        content="write_structured_file 参数无效",
        data={
            "path": None,
            "required_path": "ideation/research_blueprint.yaml",
            "required_schema": "research_blueprint",
        },
        error="parameter_validation",
    )

    assert "research_blueprint" in summary
    assert path == "ideation/research_blueprint.yaml"
