import json

from researchos.schemas import validator


def test_validate_against_schema_reports_path(tmp_path, monkeypatch):
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "demo.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "meta": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                },
                "required": ["name", "meta"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "_SCHEMAS_DIR", schema_dir)
    validator._load_schema.cache_clear()

    ok, err = validator.validate_against_schema({"name": "demo", "meta": {}}, "demo")

    assert not ok
    assert err is not None
    assert "meta" in err


def test_validate_task_artifacts_uses_registered_checker(tmp_path):
    def checker(workspace):
        return True, [str(workspace)]

    validator.register_task_checker("T_TEST", checker)
    ok, errors = validator.validate_task_artifacts(tmp_path, "T_TEST")

    assert ok
    assert errors == [str(tmp_path)]
