import json

from researchos.agents.ideation import _validate_bridge_coverage_review
from researchos.orchestration.task_io_contract import resolve_outputs
from researchos.schemas import validator
from tests.unit.test_runner_basic import _write_gate1_selection, _write_t4_stage_visibility_artifacts


def _valid_t3_note(paper_id: str) -> str:
    return f"""# {paper_id}

- **ID**: {paper_id}
- **Authors**: A, B
- **Venue**: TestConf (2025)
- **Status**: [FULL-TEXT]

## 1. Problem & Motivation
problem

## 2. Method Overview
method

## 3. Key Results
- Accuracy: 88.1 [Evidence: p.4]

## 4. Claims vs Evidence
| Claim | Evidence | Strength |
|-------|----------|----------|
| test | p.4 | Strong |

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

## 12. Reading Coverage
- **PDF source**: literature/pdfs/{paper_id}.pdf
- **Pages read**: 1-10 / 10
- **Extraction calls**: extract_pdf_text pages 1-10
- **Truncation**: none
- **Status rationale**: All PDF pages were read without truncation.

## 13. Mechanism Claim
- **Stated mechanism**: The method improves performance through a mechanism.
- **Evidence type**: ablation_supported
- **Supporting artifact**: Table 2

## 14. Design Rationale
- **Rationale**: The method tests a mechanism-level design rationale.
- **Rationale evidence**: Table 2 supports the mechanism.
- **Rationale weakness**: Boundary conditions remain uncertain.

## 15. Artifact & Design Principles
- **Artifact type**: model component
- **Artifact description**: A component for representation learning.
- **Design principles**: isolate the mechanism and compare against controls.

## 16. Data View & Evaluation Mode
- **Data view**: benchmark examples grouped by condition.
- **Evaluation mode**: main metric and ablation.
- **Validity concern**: aggregate scores may hide subgroup failures.

## 17. Contribution Type
- **Contribution type**: improvement
- **Contribution character**: It improves an existing method via a mechanism claim.
- **Why not routine**: It changes the tested design rationale.

## 18. Boundary Conditions
- **Works when**: assumptions match the benchmark.
- **May fail when**: data shifts.
- **Untested boundary**: very small data regimes.

## 19. Cross-Paper Tension
- **Tension**: Some work treats the mechanism as general.
- **Competing rationale**: Simpler baselines may explain the gain.
- **Idea fuel**: Test the mechanism under boundary conditions.
"""


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
    assert "Reading Coverage" in err or "Key Results" in err


def test_validate_task_artifacts_ignores_guides_and_counts_bridge_notes(tmp_path):
    workspace = tmp_path / "workspace"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    bridge_dir = literature / "paper_notes_bridge" / "b1"
    notes_dir.mkdir(parents=True)
    bridge_dir.mkdir(parents=True)
    (notes_dir / "_DIR_GUIDE.md").write_text("# Guide\n", encoding="utf-8")
    (bridge_dir / "bridge_note.md").write_text(_valid_t3_note("bridge_note"), encoding="utf-8")
    (literature / "deep_read_queue.jsonl").write_text(
        json.dumps(
            {
                "paper_id": "bridge_note",
                "normalized_id": "bridge_note",
                "title": "Bridge Note",
                "relevance_score": 0.8,
                "access_score_estimate": 0.7,
                "access_score": 0.7,
                "evidence_level": "PARTIAL_TEXT",
                "seed_priority": False,
                "queue_rank": 1,
                "read_priority": 0.8,
                "target_bucket": "bridge_deep",
                "bridge_id": "b1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (literature / "comparison_table.csv").write_text("id,title\nbridge_note,Bridge Note\n", encoding="utf-8")
    (literature / "related_work.bib").write_text("@article{bridge,\n title={Bridge}\n}\n", encoding="utf-8")

    ok, err = validator.validate_task_artifacts(workspace, "T3")

    assert ok, err


def test_validate_task_artifacts_skips_optional_outputs(tmp_path):
    workspace = tmp_path / "workspace"
    ideation = workspace / "ideation"
    tuple_dir = ideation / "_mechanism_tuples"
    tuple_dir.mkdir(parents=True)
    (ideation / "_design_rationale_tuples").mkdir(parents=True)
    (ideation / "novelty_audit.md").write_text("# Audit\n", encoding="utf-8")

    ok, err = validator.validate_task_artifacts(workspace, "T4.5")

    assert ok
    assert err is None


def test_t4_bridge_coverage_review_is_conditional_output(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outputs = resolve_outputs(workspace, "T4")
    assert "bridge_coverage_review" not in outputs

    ok, err = _validate_bridge_coverage_review(workspace)

    assert ok
    assert err is None


def test_validate_t4_gate1_accepts_ready_candidate_pool_without_selection(tmp_path):
    workspace = tmp_path / "workspace"
    ideation = workspace / "ideation"
    ideation.mkdir(parents=True)
    _write_t4_stage_visibility_artifacts(ideation)

    ok, err = validator.validate_task_artifacts(workspace, "T4-GATE1")

    assert ok, err


def test_validate_t4_gate1_rejects_invalid_existing_selection(tmp_path):
    workspace = tmp_path / "workspace"
    ideation = workspace / "ideation"
    ideation.mkdir(parents=True)
    _write_t4_stage_visibility_artifacts(ideation)
    _write_gate1_selection(workspace, captured={"selection": "D1"})
    (ideation / "_gate1_user_selection.json").write_text('{"semantics":"bad"}\n', encoding="utf-8")

    ok, err = validator.validate_task_artifacts(workspace, "T4-GATE1")

    assert not ok
    assert err is not None
    assert "semantics" in err


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


def test_validate_prerequisites_requires_pre_t5_shared_artifacts_for_t4(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "project.yaml").write_text("project_id: p\nresearch_direction: Test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("# Synthesis\n", encoding="utf-8")

    ok, err = validator.validate_prerequisites(workspace, "T4")

    assert not ok
    assert err is not None
    assert "domain_map" in err
    assert "synthesis_workbench" in err


def test_validate_prerequisites_requires_t8_related_work_structured_inputs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "project.yaml").write_text("project_id: p\nresearch_direction: Test\n", encoding="utf-8")
    (workspace / "drafts" / "section_outlines").mkdir(parents=True)
    (workspace / "drafts" / "paper_state.json").write_text("{}\n", encoding="utf-8")
    (workspace / "drafts" / "section_outlines" / "related_work.md").write_text("# Related\n", encoding="utf-8")
    (workspace / "drafts" / "alignment_matrix.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature").mkdir(exist_ok=True)
    (workspace / "literature" / "synthesis.md").write_text("# Synthesis\n", encoding="utf-8")
    (workspace / "literature" / "related_work.bib").write_text("@article{x,\n title={X}\n}\n", encoding="utf-8")

    ok, err = validator.validate_prerequisites(workspace, "T8-SEC-RELATED")

    assert not ok
    assert err is not None
    assert "synthesis_workbench" in err
    assert "domain_map" in err
    assert "idea_scorecard" in err
