from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_project_skill_context_schema_is_valid_draft_2020_12():
    schema_path = Path("skills/external_executor_skills/schemas/project_skill_context.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == "project_skill_context.v1"
    assert "skills" not in schema["properties"]
