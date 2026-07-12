from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.skills.project_specialization import specialize_project_skills, specialize_project_skills_with_llm
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, MockLLMClient


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "ideation").mkdir(parents=True)
    (ws / "novelty").mkdir()
    (ws / "external_executor").mkdir()
    (ws / "project.yaml").write_text(
        yaml.safe_dump({"project_id": "demo", "title": "Demo", "topic": "Demo goal", "domain": "ML"}),
        encoding="utf-8",
    )
    (ws / "ideation" / "hypotheses.md").write_text(
        "# Hypotheses\n\nCentral hypothesis: Demo hypothesis.\n",
        encoding="utf-8",
    )
    (ws / "ideation" / "exp_plan.yaml").write_text(
        yaml.safe_dump(
            {
                "task": "classification",
                "primary_metrics": [{"name": "Accuracy", "direction": "higher_is_better", "aggregation": "mean"}],
                "seed_policy": {"seeds": [1, 2, 3]},
                "minimum_experiment_loop": ["run baseline", "run ours"],
                "protocol_constraints": ["same split"],
                "statistical_policy": {"summary": "mean"},
                "interpretation_boundaries": {"positive": "higher accuracy"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (ws / "novelty" / "novelty_audit.md").write_text(
        "# Novelty\n\n## Claim boundaries\n- Demo only.\n\n## Must not claim\n- Universal superiority.\n",
        encoding="utf-8",
    )
    handoff = {
        "schema_version": "external_executor_handoff.v1",
        "context_reboost": {
            "project_goal": "Demo goal",
            "central_hypothesis": "Demo hypothesis.",
            "method_mechanism": {
                "core_mechanism": "demo mechanism",
                "must_preserve_components": [{"component_id": "M1", "name": "Core", "intended_role": "test"}],
            },
            "required_baselines": [{"baseline_id": "B1", "name": "Baseline", "reason_included": "closest"}],
            "claim_evidence_matrix": [
                {"claim_id": "C1", "claim": "Demo claim", "reviewer_question": "Does it work?"}
            ],
            "minimum_experiment_loop": ["run baseline", "run ours"],
            "iteration_budget": {"max_iterations": 2},
            "claim_boundaries": ["Demo only."],
            "writer_handoff_contract": ["method", "results"],
            "stop_conditions": ["budget exhausted"],
            "human_review_triggers": ["scope drift"],
        },
        "method_intent": {
            "core_mechanism": "demo mechanism",
            "allowed_refinements": ["parameter tuning"],
            "forbidden_silent_changes": ["new objective"],
            "mechanism_to_ablation": [{"mechanism": "demo", "planned_test": "remove demo"}],
            "implementation_acceptance": ["tests pass"],
            "scope_change_triggers": ["new dataset required"],
            "attribution_requirements": ["ablation evidence"],
        },
        "metrics": [{"name": "Accuracy", "direction": "higher_is_better", "aggregation": "mean"}],
        "required_baselines": [{"baseline_id": "B1", "name": "Baseline"}],
        "seeds": [1, 2, 3],
        "workspace_relative_workdir": "external_executor/workdir",
        "executor_outputs_contract": {
            "must_write": ["external_executor/result_pack.json", "external_executor/run_manifest.json"]
        },
    }
    (ws / "external_executor" / "handoff_pack.json").write_text(json.dumps(handoff), encoding="utf-8")
    (ws / "external_executor" / "expected_outputs_schema.json").write_text(
        json.dumps({"schema_version": "external_executor_result.v1", "required": ["result_pack"]}),
        encoding="utf-8",
    )
    (ws / "external_executor" / "allowed_paths.txt").write_text(
        "rw  external_executor/workdir/\nno  researchos/\n",
        encoding="utf-8",
    )
    (ws / "external_executor" / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    return ws


def test_compiler_generates_context_report_and_13_skills(tmp_path: Path):
    ws = _workspace(tmp_path)
    result = specialize_project_skills(workspace=ws, repo_root=Path.cwd())

    assert result.status in {"ready", "incomplete"}
    assert (ws / "external_executor" / "project_skill_context.yaml").exists()
    assert (ws / "external_executor" / "schemas" / "project_skill_context.schema.json").exists()
    report = json.loads((ws / "external_executor" / "skill_specialization_report.json").read_text(encoding="utf-8"))
    assert report["skills_total"] == 13
    assert report["skills_specialized"] == 13
    assert len(list((ws / "external_executor" / "skills").glob("*/SKILL.md"))) == 13
    root_skill = (ws / "external_executor" / "skills" / "research-execution" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Project-Specific Guidance" in root_skill


def test_compiler_dry_run_does_not_publish(tmp_path: Path):
    ws = _workspace(tmp_path)
    result = specialize_project_skills(workspace=ws, repo_root=Path.cwd(), dry_run=True)

    assert result.status in {"ready", "incomplete"}
    assert not (ws / "external_executor" / "project_skill_context.yaml").exists()
    assert not (ws / "external_executor" / "skills").exists()


def test_compiler_refuses_running_executor(tmp_path: Path):
    ws = _workspace(tmp_path)
    (ws / "external_executor" / "executor_status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")

    result = specialize_project_skills(workspace=ws, repo_root=Path.cwd())

    assert result.status == "failed"
    assert result.errors[0]["code"] == "active_executor_suite_cannot_be_replaced"


@pytest.mark.asyncio
async def test_llm_specializer_calls_llm_and_updates_project_skills(tmp_path: Path):
    ws = _workspace(tmp_path)
    skill_names = list(
        yaml.safe_load(Path("skills/external_executor_skills/skill_specialization.yaml").read_text(encoding="utf-8"))[
            "skills"
        ].keys()
    )
    llm_payload = {
        "skills": {
            name: {
                "focus": f"LLM focus for {name}",
                "priorities": [f"LLM priority for {name}"],
                "constraints": ["Do not invent results."],
                "completion_criteria": ["Write auditable artifacts."],
                "uncertainty_handling": ["Escalate missing context."],
            }
            for name in skill_names
        }
    }
    llm = MockLLMClient(
        [
            FakeRawCompletion(
                message=FakeLLMMessage(content=json.dumps(llm_payload)),
                prompt_tokens=120,
                completion_tokens=80,
                cost_usd=0.01,
            )
        ]
    )

    result = await specialize_project_skills_with_llm(workspace=ws, repo_root=Path.cwd(), llm_client=llm)

    assert llm.call_count == 1
    assert result.status in {"ready", "incomplete"}
    report = json.loads((ws / "external_executor" / "skill_specialization_report.json").read_text(encoding="utf-8"))
    assert report["specialization_method"] == "llm_assisted_project_specialization"
    assert report["llm_specialization"]["enabled"] is True
    assert report["llm_specialization"]["skills_specialized"] == 13
    root_skill = (ws / "external_executor" / "skills" / "research-execution" / "SKILL.md").read_text(encoding="utf-8")
    assert "### LLM project specialization" in root_skill
    assert "LLM focus for research-execution" in root_skill
