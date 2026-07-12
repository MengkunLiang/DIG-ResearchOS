from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from researchos.skills.project_specialization.context_builder import (
    build_project_skill_context,
    ensure_metadata_for_injections,
)


def _schema() -> dict:
    return json.loads(
        Path("skills/external_executor_skills/schemas/project_skill_context.schema.json").read_text(encoding="utf-8")
    )


def _mapping() -> dict:
    return yaml.safe_load(Path("skills/external_executor_skills/skill_specialization.yaml").read_text(encoding="utf-8"))


def _minimal_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "ideation").mkdir(parents=True)
    (ws / "novelty").mkdir()
    (ws / "external_executor").mkdir()
    (ws / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "project_id": "demo",
                "title": "Demo",
                "topic": "Source goal",
                "domain": "ML",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (ws / "ideation" / "hypotheses.md").write_text(
        "# Hypotheses\n\nCentral hypothesis: Source hypothesis.\n",
        encoding="utf-8",
    )
    (ws / "ideation" / "exp_plan.yaml").write_text(
        yaml.safe_dump({"task": "classification", "primary_metrics": ["Accuracy"]}, sort_keys=False),
        encoding="utf-8",
    )
    (ws / "external_executor" / "handoff_pack.json").write_text(
        json.dumps(
            {
                "schema_version": "external_executor_handoff.v1",
                "context_reboost": {
                    "project_goal": "Handoff goal",
                    "central_hypothesis": "Handoff hypothesis.",
                    "claim_evidence_matrix": [
                        {"claim_id": "C1", "claim": "Claim", "reviewer_question": "Question?"}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (ws / "external_executor" / "expected_outputs_schema.json").write_text(
        json.dumps({"schema_version": "external_executor_result.v1", "required": ["result_pack"]}),
        encoding="utf-8",
    )
    (ws / "external_executor" / "allowed_paths.txt").write_text("rw  external_executor/workdir/\n", encoding="utf-8")
    (ws / "external_executor" / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    return ws


def test_context_builder_uses_source_override_and_schema_round_trip(tmp_path: Path):
    schema = _schema()
    context = build_project_skill_context(workspace=_minimal_workspace(tmp_path), schema=schema)
    ensure_metadata_for_injections(context, _mapping())

    assert context["project"]["goal"] == "Source goal"
    assert context["field_metadata"]["project.goal"]["status"] == "confirmed_from_source"
    assert context["field_metadata"]["project.goal"]["handoff_value_ignored"] == "Handoff goal"
    assert context["research"]["central_hypothesis"] == "Source hypothesis."
    assert "skills" not in context
    Draft202012Validator(schema).validate(context)


def test_context_builder_marks_missing_injected_fields_uncertain(tmp_path: Path):
    context = build_project_skill_context(workspace=_minimal_workspace(tmp_path), schema=_schema())
    ensure_metadata_for_injections(context, _mapping())

    metadata = context["field_metadata"]["method.core_mechanism"]
    assert metadata["status"] == "uncertain"
    assert metadata["note"]
