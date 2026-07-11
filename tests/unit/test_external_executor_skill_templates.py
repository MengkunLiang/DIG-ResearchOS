from __future__ import annotations

from pathlib import Path

import yaml

from researchos.tools.external_experiment import EXTERNAL_RESULT_REQUIRED_FIELDS, SKILL_SUITE


EXTERNAL_SKILLS_ROOT = Path("skills/external_executor_skills")

REQUIRED_SECTIONS = [
    "## Use for",
    "## Do not use for",
    "## Reads",
    "## Writes",
    "## Workflow",
    "## Output contract",
    "## Evidence rules",
    "## Stop conditions",
]


def _split_skill(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    raw_meta, body = text.split("\n---\n", 1)
    return yaml.safe_load(raw_meta.removeprefix("---\n")), body


def test_external_executor_skill_templates_keep_required_boundaries():
    for skill_name in SKILL_SUITE:
        skill_path = EXTERNAL_SKILLS_ROOT / skill_name / "SKILL.md"
        assert skill_path.exists(), skill_name
        meta, body = _split_skill(skill_path)
        assert meta["name"] == skill_name
        assert "finish_task" in meta["allowed_tools"]
        assert "external_executor/" in meta["allowed_write_prefixes"]
        for section in REQUIRED_SECTIONS:
            assert section in body, f"{skill_name} missing {section}"


def test_external_executor_root_skill_covers_flow_and_result_contract():
    body = (EXTERNAL_SKILLS_ROOT / "research_execution" / "SKILL.md").read_text(encoding="utf-8")
    for expected in [
        "context_alignment",
        "resource_and_baseline_mining",
        "baseline_reproduction",
        "experiment_design",
        "method_refinement",
        "implementation",
        "code_and_protocol_review",
        "experiment_iteration",
        "result_diagnosis",
        "module_attribution",
        "figure_table_packaging",
        "writer_handoff",
    ]:
        assert expected in body
    dispatch_line = next(line for line in body.splitlines() if "Dispatch child skills" in line)
    assert dispatch_line.index("baseline_reproduction") < dispatch_line.index("implementation")
    assert "Builder-Reviewer" in body

    contract = (Path("skills/shared-references/result-pack-contract.md")).read_text(encoding="utf-8")
    for field in EXTERNAL_RESULT_REQUIRED_FIELDS:
        assert f"`{field}`" in contract


def test_external_executor_skill_support_files_exist():
    required_paths = [
        "skills/skills_customization/SKILL.md",
        "skills/skills_customization/references/customization_checklist.md",
        "skills/shared-references/external-executor-protocol.md",
        "skills/shared-references/result-pack-contract.md",
        "skills/shared-references/evidence-rules.md",
        "skills/shared-references/scope-drift-policy.md",
        "skills/shared-references/builder-reviewer-loop.md",
        "skills/external_executor_skills/research_execution/references/execution_loop.md",
        "skills/external_executor_skills/research_execution/references/stop_conditions.md",
        "skills/external_executor_skills/research_execution/assets/result_pack_skeleton.json",
        "skills/external_executor_skills/experiment_design/assets/claim_evidence_matrix_template.json",
        "skills/external_executor_skills/figure_table_packaging/assets/framework_figure_spec_template.json",
        "skills/external_executor_skills/writer_handoff/assets/executor_status_template.json",
        "skills/external_executor_skills/writer_handoff/assets/run_manifest_template.json",
        "skills/external_executor_skills/research_execution/scripts/check_required_result_pack_fields.py",
        "skills/external_executor_skills/experiment_iteration/scripts/index_artifacts.py",
        "skills/external_executor_skills/figure_table_packaging/scripts/check_figure_table_refs.py",
    ]
    for rel_path in required_paths:
        assert Path(rel_path).exists(), rel_path


def test_skills_customization_controller_is_self_contained():
    meta, body = _split_skill(Path("skills/skills_customization/SKILL.md"))
    assert meta["name"] == "skills_customization"
    assert "write_file" in meta["allowed_tools"]
    assert meta["allowed_write_prefixes"] == ["external_executor/skills/"]
    assert "ResearchOS calls the configured LLM provider directly" in body
    assert "external_executor/skills/customization_report.json" in body
    for skill_name in SKILL_SUITE:
        assert skill_name in Path("skills/skills_customization/references/customization_checklist.md").read_text(encoding="utf-8")
