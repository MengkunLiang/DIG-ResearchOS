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


def test_validate_prerequisites_only_requires_declared_required_inputs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "project.yaml").write_text("project_id: demo\n", encoding="utf-8")

    ok, err = validator.validate_prerequisites(workspace, "T2")

    assert ok
    assert err is None


def test_validate_prerequisites_reports_missing_required_inputs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ok, err = validator.validate_prerequisites(workspace, "T3")

    assert not ok
    assert err is not None
    assert "project" in err
    assert "papers_dedup" in err
