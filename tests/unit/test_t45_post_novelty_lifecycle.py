from __future__ import annotations

import json

import yaml

from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.runtime.agent import ExecutionContext


def _brief(workspace):
    path = workspace / "ideation" / "hypothesis_brief.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "semantics": "t4_pre_novelty_hypothesis_brief",
                "status": "draft_for_novelty_review",
                "draft_hypotheses": [
                    {"id": "H1", "statement": "A bounded primary claim."},
                    {"id": "H2", "statement": "A bounded mechanism claim."},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _audit(verdict: str) -> str:
    return (
        "# Novelty audit\n\n"
        "## H1\n\nLevel 2\n\n"
        "## H2\n\nLevel 2\n\n"
        "## CDR Gate\n\n"
        "Collision Axis: pass\n"
        "Ambition Axis: pass\n"
        "Contribution Distance: medium\n"
        f"Final Gate Verdict: {verdict}\n\n"
        + "Evidence-bound audit detail. " * 40
    )


def _tuples(workspace):
    mechanism = workspace / "ideation" / "_mechanism_tuples"
    design = workspace / "ideation" / "_design_rationale_tuples"
    mechanism.mkdir(parents=True)
    design.mkdir(parents=True)
    for hypothesis_id in ("H1", "H2"):
        (mechanism / f"{hypothesis_id}.json").write_text("{}\n", encoding="utf-8")
        (design / f"{hypothesis_id}.json").write_text("{}\n", encoding="utf-8")


def _formal_bundle(workspace):
    artifacts = {
        "hypotheses": "ideation/hypotheses.md",
        "exp_plan": "ideation/exp_plan.yaml",
        "contribution_hypothesis_map": "ideation/contribution_hypothesis_map.yaml",
        "validation_map": "ideation/validation_map.yaml",
        "kill_criteria": "ideation/kill_criteria.yaml",
    }
    for rel in artifacts.values():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("formalized after audit\n", encoding="utf-8")
    (workspace / "ideation" / "post_novelty_formalization.json").write_text(
        json.dumps(
            {
                "semantics": "t45_post_novelty_formalization",
                "status": "formalized_after_novelty_pass",
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )


def test_t45_pass_requires_and_accepts_formal_bundle_after_audit(tmp_path):
    (tmp_path / "project.yaml").write_text("project_id: lifecycle\n", encoding="utf-8")
    _brief(tmp_path)
    audit = tmp_path / "ideation" / "novelty_audit.md"
    audit.write_text(_audit("pass_to_experiment"), encoding="utf-8")
    _tuples(tmp_path)
    _formal_bundle(tmp_path)

    result = NoveltyAuditorAgent().validate_outputs(
        ExecutionContext(workspace_dir=tmp_path, project_id="lifecycle", task_id="T4.5", run_id="t45")
    )

    assert result == (True, None)


def test_t45_nonpass_does_not_require_a_formal_execution_bundle(tmp_path):
    (tmp_path / "project.yaml").write_text("project_id: lifecycle\n", encoding="utf-8")
    _brief(tmp_path)
    (tmp_path / "ideation" / "novelty_audit.md").write_text(_audit("return_to_t4"), encoding="utf-8")
    _tuples(tmp_path)

    result = NoveltyAuditorAgent().validate_outputs(
        ExecutionContext(workspace_dir=tmp_path, project_id="lifecycle", task_id="T4.5", run_id="t45")
    )

    assert result == (True, None)
    assert not (tmp_path / "ideation" / "hypotheses.md").exists()
