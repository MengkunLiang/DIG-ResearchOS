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
    monkeypatch.setattr(validator, "SCHEMA_DIR", schema_dir)
    # _load_schema不是缓存函数，不需要cache_clear

    ok, err = validator.validate_record({"name": "demo", "meta": {}}, "demo")

    assert not ok
    assert err is not None
    assert "count" in err  # 缺少required字段count


def test_validate_task_artifacts_uses_registered_checker(tmp_path):
    workspace = tmp_path / "workspace"
    notes_dir = workspace / "literature" / "paper_notes"
    notes_dir.mkdir(parents=True)
    (workspace / "literature" / "comparison_table.csv").write_text(
        "id,title\nbad,Bad Note\n",
        encoding="utf-8",
    )
    (workspace / "literature" / "related_work.bib").write_text(
        "@article{bad,\n  title={Bad Note}\n}\n",
        encoding="utf-8",
    )
    (notes_dir / "bad.md").write_text(
        """# Bad Note

- **Status**: [FULL-TEXT]

## 1. Problem & Motivation
problem

## 2. Method Overview
method

## 3. Key Results
- Accuracy: 88.1 from the results section

## 4. Claims vs Evidence
| Claim | Evidence | Strength |
|-------|----------|----------|
| test | test | Strong |

## 5. Limitations
- limit

## 6. Relevance to Our Research
- relevant

## 7. Technical Details Worth Noting
- detail

## 8. Strengths
- strong

## 9. Weaknesses / Gaps
- weak

## 10. Key Quotes
> "quote"

## 11. My Questions
- question
""",
        encoding="utf-8",
    )

    ok, err = validator.validate_task_artifacts(workspace, "T3")

    assert not ok
    assert err is not None
    assert "Key Results" in err


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
