from __future__ import annotations

from pathlib import Path

import yaml

from researchos.tools.external_experiment import SKILL_SUITE


EXTERNAL_SKILLS_ROOT = Path("skills/external_executor_skills")
BEGIN_MARKER = "<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->"
END_MARKER = "<!-- PROJECT-SPECIFIC-GUIDANCE:END -->"


def _split_skill(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    raw_meta, body = text.split("\n---\n", 1)
    return yaml.safe_load(raw_meta.removeprefix("---\n")), body


def _mapping() -> dict:
    return yaml.safe_load((EXTERNAL_SKILLS_ROOT / "skill_specialization.yaml").read_text(encoding="utf-8"))


def test_external_executor_skill_templates_match_specialization_mapping():
    mapping = _mapping()
    mapped_skills = list(mapping["skills"].keys())
    assert mapped_skills == SKILL_SUITE

    for skill_name in SKILL_SUITE:
        skill_dir = EXTERNAL_SKILLS_ROOT / skill_name
        skill_path = skill_dir / "SKILL.md"
        assert skill_path.exists(), skill_name
        meta, body = _split_skill(skill_path)
        assert meta["name"] == skill_name
        assert meta.get("description")
        assert body.count(BEGIN_MARKER) == 1
        assert body.count(END_MARKER) == 1
        assert body.index(BEGIN_MARKER) < body.index(END_MARKER)


def test_external_executor_support_files_exist():
    assert (EXTERNAL_SKILLS_ROOT / "schemas" / "project_skill_context.schema.json").exists()
    assert (EXTERNAL_SKILLS_ROOT / "skill_specialization.yaml").exists()

    for skill_name in SKILL_SUITE:
        skill_dir = EXTERNAL_SKILLS_ROOT / skill_name
        assert (skill_dir / "SKILL.md").exists()
        assert any(path.name in {"references", "scripts", "assets", "agents", "tests"} for path in skill_dir.iterdir())


def test_external_executor_root_skill_dispatches_hyphenated_children():
    body = (EXTERNAL_SKILLS_ROOT / "research-execution" / "SKILL.md").read_text(encoding="utf-8")
    for skill_name in SKILL_SUITE:
        if skill_name != "research-execution":
            assert skill_name in body


def test_legacy_skills_customization_is_not_part_of_new_suite():
    assert "skills_customization" not in SKILL_SUITE
    assert "skills_customization" not in _mapping()["skills"]
