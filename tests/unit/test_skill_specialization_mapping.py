from __future__ import annotations

import json
from pathlib import Path

import yaml

from researchos.skills.project_specialization.validation import validate_mapping
from researchos.tools.external_experiment import SKILL_SUITE


def test_skill_specialization_mapping_matches_schema_and_templates():
    root = Path("skills/external_executor_skills")
    schema = json.loads((root / "schemas" / "project_skill_context.schema.json").read_text(encoding="utf-8"))
    mapping = yaml.safe_load((root / "skill_specialization.yaml").read_text(encoding="utf-8"))

    errors = validate_mapping(schema=schema, mapping=mapping, template_root=root)

    assert errors == []
    assert list(mapping["skills"].keys()) == SKILL_SUITE
    assert len(mapping["skills"]) == 13
