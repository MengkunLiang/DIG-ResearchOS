"""T3/T3.5 Reader Agent 单元测试。

测试覆盖：
1. read模式基本流程
2. synthesize模式基本流程
3. validate_outputs - read模式
4. validate_outputs - synthesize模式
5. 边界情况处理
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.agents.reader import ReaderAgent, _validate_abstract_note_structure, _validate_note_structure
from researchos.literature_identity import paper_note_match_keys
from researchos.runtime.agent import ExecutionContext


def _structured_note(paper_id: str, *, abstract_only: bool = False) -> str:
    status = "[ABSTRACT-ONLY]" if abstract_only else "[FULL-TEXT]"
    verification = "metadata_verified (confidence: 0.95)"
    evidence_line = "- N/A for abstract-only note\n" if abstract_only else "- Accuracy: 88.1 [Evidence: Results section]\n"
    if abstract_only:
        coverage = """- **PDF source**: not available
- **Pages read**: 0 / unknown
- **Extraction calls**: none
- **Truncation**: none
- **Status rationale**: PDF was not available; note is based on abstract and metadata.
"""
    else:
        coverage = f"""- **PDF source**: literature/pdfs/{paper_id}.pdf
- **Pages read**: 1-10 / 10
- **Extraction calls**: extract_pdf_text pages 1-10
- **Truncation**: none
- **Status rationale**: All PDF pages were read without truncation.
"""
    return f"""# {paper_id}

- **ID**: {paper_id}
- **Authors**: A, B
- **Venue**: TestConf (2025)
- **DOI/arXiv**: arxiv:2501.00001
- **Citations**: 10
- **Verification**: {verification}
- **Status**: {status}

## 1. Problem & Motivation
problem

## 2. Method Overview
method

## 3. Key Results
{evidence_line}

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

## 12. Reading Coverage
{coverage}

## 13. Mechanism Claim
- **Stated mechanism**: The method improves performance through better feature extraction
- **Evidence type**: ablation_supported
- **Supporting artifact**: Table 2

## 14. Design Rationale
- **Rationale**: The method is designed to test whether feature extraction changes explain the reported result.
- **Rationale evidence**: The paper reports an ablation and a result table connected to the mechanism.
- **Rationale weakness**: The note does not prove whether the same rationale holds outside the tested setup.

## 15. Artifact & Design Principles
- **Artifact type**: model component
- **Artifact description**: A lightweight component that changes representation learning behavior.
- **Design principles**: isolate the claimed mechanism; compare with a simpler control.

## 16. Data View & Evaluation Mode
- **Data view**: benchmark examples grouped by task condition.
- **Evaluation mode**: main metric plus ablation evidence.
- **Validity concern**: Aggregate scores may hide subgroup-specific failures.

## 17. Contribution Type
- **Contribution type**: improvement
- **Contribution character**: The work improves an existing method by clarifying when the mechanism helps.
- **Why not routine**: It makes a mechanism-level design claim rather than only changing implementation details.

## 18. Boundary Conditions
- **Works when**: the benchmark setting matches the paper's assumptions.
- **May fail when**: data distribution or compute constraints differ substantially.
- **Untested boundary**: very small data regimes.

## 19. Cross-Paper Tension
- **Tension**: Some papers treat the mechanism as general while others suggest condition-specific behavior.
- **Competing rationale**: Simpler baselines may explain part of the reported gain.
- **Idea fuel**: Test whether the mechanism remains useful under a boundary condition.
"""


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()

    # 创建必需的目录结构
    (workspace / "literature").mkdir()
    (workspace / "literature" / "paper_notes").mkdir()

    return workspace


@pytest.fixture
def reader_agent():
    """创建Reader Agent实例。"""
    return ReaderAgent()


def test_reader_agent_spec(reader_agent):
    """测试Reader Agent的AgentSpec配置。"""
    spec = reader_agent.spec
    assert spec.name == "reader"
    assert spec.model_tier in {"medium", "heavy"}
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "lookup_paper_record" in spec.tool_names
    assert "fetch_paper_pdf" in spec.tool_names
    assert "extract_pdf_text" in spec.tool_names
    assert "save_paper_note" in spec.tool_names
    assert spec.temperature == 0.5
    assert "literature/" in spec.allowed_read_prefixes
    assert "_runtime/resume/" in spec.allowed_read_prefixes
    assert "literature/" in spec.allowed_write_prefixes


def test_reader_system_prompt_read_mode(reader_agent, temp_workspace):
    """测试read模式的system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("direction: Test research direction\n")

    # 创建papers_dedup.jsonl
    dedup_path = temp_workspace / "literature" / "papers_dedup.jsonl"
    dedup_path.write_text('{"id": "test1", "title": "Test Paper"}\n')

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "Reader Agent" in prompt
    assert "T3" in prompt or "深度阅读" in prompt
    assert "paper_notes" in prompt
    assert "不能只读前几页" in prompt
    assert "Reading Coverage" in prompt


def test_reader_system_prompt_read_mode_includes_seed_priority(reader_agent, temp_workspace):
    """测试read模式会把 seed papers 标成最高优先级。"""
    (temp_workspace / "project.yaml").write_text("direction: Test research direction\n")
    (temp_workspace / "literature" / "papers_dedup.jsonl").write_text(
        '{"id": "paper1", "title": "Seed Paper A"}\n{"id": "paper2", "title": "Other Paper"}\n'
    )
    (temp_workspace / "user_seeds").mkdir(exist_ok=True)
    (temp_workspace / "user_seeds" / "seed_papers.jsonl").write_text(
        '{"title": "Seed Paper A", "role": "anchor"}\n{"title": "Seed Paper Missing", "role": "anchor"}\n'
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "最高优先级必读对象" in prompt
    assert "Seed Paper A" in prompt
    assert "尚未在 `papers_dedup.jsonl` 中匹配到" in prompt


def test_reader_system_prompt_read_mode_includes_seed_outline_profile(reader_agent, temp_workspace):
    (temp_workspace / "project.yaml").write_text("direction: 智能算法风险综述\n", encoding="utf-8")
    (temp_workspace / "literature" / "papers_dedup.jsonl").write_text(
        '{"id": "paper1", "title": "Algorithm Auditing for Management Decisions"}\n',
        encoding="utf-8",
    )
    (temp_workspace / "user_seeds").mkdir(exist_ok=True)
    (temp_workspace / "user_seeds" / "seed_outline_profile.json").write_text(
        json.dumps(
            {
                "semantics": "user_seed_outline_profile",
                "manuscript_type": "survey",
                "framework": {
                    "risk_generation_chain": ["场景", "数据", "模型", "决策", "反馈"],
                    "perspectives": ["理论", "技术", "管理", "治理"],
                    "taxonomy_hint": "理论 / 技术 / 管理 / 治理 × 场景 -> 数据 -> 模型 -> 决策 -> 反馈",
                },
                "representative_literature_directions": [
                    {"direction": "algorithm auditing", "use_as": "query_direction_not_verified_citation"}
                ],
                "literature_seed_policy": {"directions_are_verified_citations": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    prompt = reader_agent.system_prompt(ctx)

    assert "用户 seed outline profile" in prompt
    assert "阅读维度先验" in prompt
    assert "不是已验证 citation" in prompt
    assert "理论/技术/管理/治理" in prompt or "理论" in prompt
    assert "风险生成链条" in prompt


def test_reader_system_prompt_synthesize_mode(reader_agent, temp_workspace):
    """测试synthesize模式的system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("direction: Test research direction\n")

    # 创建paper_notes目录和一些笔记
    notes_dir = temp_workspace / "literature" / "paper_notes"
    (notes_dir / "note1.md").write_text("# Test Note 1")
    (notes_dir / "note2.md").write_text("# Test Note 2")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "Reader Agent" in prompt
    assert "T3.5" in prompt or "综合" in prompt
    assert "synthesis.md" in prompt


def test_reader_initial_user_message_read_mode(reader_agent, temp_workspace):
    """测试read模式的初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    msg = reader_agent.initial_user_message(ctx)
    assert "T3" in msg or "深度阅读" in msg
    assert "papers_dedup.jsonl" in msg
    assert "全文读到最后一页" in msg


def test_reader_initial_user_message_read_mode_resume(reader_agent, temp_workspace):
    """测试read模式在已有进度时提示继续执行。"""
    (temp_workspace / "literature" / "paper_notes" / "done_paper.md").write_text("# done")
    (temp_workspace / "literature" / "deep_read_queue_pending.jsonl").write_text(
        '{"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 1}\n'
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
        extra={"is_resume": True, "resume_reason": "interrupted"},
    )

    msg = reader_agent.initial_user_message(ctx)
    assert "继续T3" in msg
    assert "只处理尚未完成的论文" in msg
    assert "deep_read_queue_pending.jsonl" in msg
    assert "补齐已有笔记缺失的表格/Bib条目" in msg
    assert "seed papers 必须最高优先级" in msg
    assert "覆盖到最后一页" in msg


def test_reader_initial_user_message_synthesize_mode(reader_agent, temp_workspace):
    """测试synthesize模式的初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    msg = reader_agent.initial_user_message(ctx)
    assert "T3.5" in msg or "综合" in msg
    assert "synthesis.md" in msg


def test_validate_outputs_read_mode_success(reader_agent, temp_workspace):
    """测试read模式输出校验（成功场景）。"""
    # 创建 deep_read_queue 和对应笔记
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+1}, "read_priority": 0.8, "target_bucket": "target"}}'
            for i in range(18)
        )
        + "\n"
    )

    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(18):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"))

    # 创建comparison_table.csv
    ct_path = temp_workspace / "literature" / "comparison_table.csv"
    ct_path.write_text("id,title,year\ntest1,Test Paper,2023\n")

    # 创建related_work.bib
    bib_path = temp_workspace / "literature" / "related_work.bib"
    bib_path.write_text("@article{test2023,\n  title={Test},\n  year={2023}\n}\n")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_abstract_note_rejects_nested_ab_bridge_headings(temp_workspace):
    """`### A/B` must not satisfy the required abstract-only `## A/B` sections."""

    note_path = temp_workspace / "literature" / "paper_notes_abstract" / "bad.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        """# Bad

- **ID**: bad
- **Status**: [ABSTRACT-ONLY]

## 1. Problem & Motivation
Problem.

## 2. Method Summary
Method.

### A. 核心做法/视角
View.

### B. 桥接点
Bridge.

## 3. Key Claimed Results
Result.

## 13. Mechanism Claim
- **Stated mechanism**: not available from abstract
- **Evidence type**: abstract_claim_hint
- **Supporting artifact**: abstract metadata only

## Source
- Read from: abstract / metadata only
""",
        encoding="utf-8",
    )

    ok, err = _validate_abstract_note_structure(note_path)
    assert not ok
    assert "## A. 核心做法/视角" in str(err)


def test_validate_outputs_read_mode_fallback_requires_full_input_pool(reader_agent, temp_workspace):
    """没有 deep_read_queue 的旧 workspace 也不能再按 80% 放过输入论文。"""

    verified_path = temp_workspace / "literature" / "papers_verified.jsonl"
    verified_path.write_text(
        "\n".join(f'{{"id": "paper{i}", "title": "Paper {i}"}}' for i in range(4)) + "\n",
        encoding="utf-8",
    )
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(3):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"), encoding="utf-8")
    (temp_workspace / "literature" / "comparison_table.csv").write_text(
        "id,title,year\ntest1,Test Paper,2023\n",
        encoding="utf-8",
    )
    (temp_workspace / "literature" / "related_work.bib").write_text(
        "@article{test2023,\n  title={Test},\n  year={2023}\n}\n",
        encoding="utf-8",
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-fallback-ratio",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)

    assert not ok
    assert "至少需要4篇" in (err or "")


def test_validate_note_structure_rejects_numeric_key_results_without_evidence(tmp_path):
    """Key Results 里出现数字时，必须用统一的 [Evidence: ...] 格式标注来源。"""
    note_path = tmp_path / "bad_note.md"
    note_path.write_text(
        _structured_note("bad_note").replace(
            "- Accuracy: 88.1 [Evidence: Results section]",
            "- Accuracy: 88.1 from the results section",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert not ok
    assert "Key Results" in err
    assert "[Evidence: ...]" in err


def test_validate_note_structure_allows_non_numeric_dataset_names_without_evidence(tmp_path):
    """AI2-THOR、3D 这类标识符不应被误判为指标数字。"""
    note_path = tmp_path / "dataset_note.md"
    note_path.write_text(
        _structured_note("dataset_note").replace(
            "- Accuracy: 88.1 [Evidence: Results section]",
            "- Dataset: AI2-THOR\n"
            "- Representation: 3D scene graph\n"
            "- **Efficiency (throughput with Llama-3.1-8b)**:\n"
            "- Steepest learning curve: Claude Sonnet 4.5\n"
            "- Accuracy: 88.1 [Evidence: Results section]",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert ok, f"Validation failed: {err}"


def test_validate_note_structure_allows_explained_contribution_type(tmp_path):
    """Contribution type 可带解释，validator 不应把 LLM 的知识性说明误杀。"""
    note_path = tmp_path / "explained_contribution.md"
    note_path.write_text(
        _structured_note("explained_contribution").replace(
            "- **Contribution type**: improvement",
            "- **Contribution type**: improvement (with invention elements)",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert ok, f"Validation failed: {err}"


def test_note_identity_keys_only_use_h1_and_metadata(tmp_path):
    """note identity 不应把二级章节标题混入匹配 key。"""
    note_path = tmp_path / "Causal-Invariant Cross-Domain Out-of-Distribution Recommendation.md"
    note_path.write_text(_structured_note("arxiv:2501.5052"), encoding="utf-8")

    keys = paper_note_match_keys(note_path)

    assert "arxiv:2501.5052" in keys
    assert "causal invariant cross domain out of distribution recommendation" in keys
    assert "1 problem motivation" not in keys
    assert "10 key quotes" not in keys


def test_validate_note_structure_requires_reading_coverage(tmp_path):
    """全文类 note 必须记录 PDF 阅读覆盖范围。"""
    note_path = tmp_path / "missing_coverage.md"
    note_path.write_text(
        _structured_note("missing_coverage").replace(
            "\n## 12. Reading Coverage\n"
            "- **PDF source**: literature/pdfs/missing_coverage.pdf\n"
            "- **Pages read**: 1-10 / 10\n"
            "- **Extraction calls**: extract_pdf_text pages 1-10\n"
            "- **Truncation**: none\n"
            "- **Status rationale**: All PDF pages were read without truncation.\n",
            "",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert not ok
    assert "Reading Coverage" in err


def test_validate_note_structure_rejects_full_text_partial_page_coverage(tmp_path):
    """FULL-TEXT 不能只读部分页码。"""
    note_path = tmp_path / "partial_coverage.md"
    note_path.write_text(
        _structured_note("partial_coverage").replace(
            "- **Pages read**: 1-10 / 10",
            "- **Pages read**: 1-8 / 10",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert not ok
    assert "FULL-TEXT" in err


def test_validate_note_structure_rejects_full_text_all_pages_without_numeric_total(tmp_path):
    """泛化的 all pages 不能替代可核验的页码范围。"""
    note_path = tmp_path / "all_pages_without_total.md"
    note_path.write_text(
        _structured_note("all_pages_without_total").replace(
            "- **Pages read**: 1-10 / 10",
            "- **Pages read**: all pages",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert not ok
    assert "FULL-TEXT" in err


def test_validate_note_structure_rejects_full_text_missing_first_page(tmp_path):
    """FULL-TEXT 必须从第一页覆盖到最后一页。"""
    note_path = tmp_path / "missing_first_page.md"
    note_path.write_text(
        _structured_note("missing_first_page").replace(
            "- **Pages read**: 1-10 / 10",
            "- **Pages read**: 2-10 / 10",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert not ok
    assert "FULL-TEXT" in err


def test_validate_note_structure_accepts_chunked_full_text_reread(tmp_path):
    """分块重读覆盖完整 PDF 时，FULL-TEXT 不应被 Truncation 误判。"""
    note_path = tmp_path / "chunked_coverage.md"
    note_path.write_text(
        _structured_note("chunked_coverage")
        .replace("- **Pages read**: 1-10 / 10", "- **Pages read**: 1-4, 5-8, 9-10 / 10")
        .replace(
            "- **Extraction calls**: extract_pdf_text pages 1-10",
            "- **Extraction calls**: extract_pdf_text pages 1-10 truncated, then pages 1-4, 5-8, 9-10",
        )
        .replace(
            "- **Truncation**: none",
            "- **Truncation**: first full call truncated; final chunked re-read covered all pages with no truncation",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert ok, f"Validation failed: {err}"


def test_validate_note_structure_accepts_chinese_final_untruncated(tmp_path):
    """中文“最终未截断”应被视为明确无最终截断。"""
    note_path = tmp_path / "chinese_untruncated.md"
    note_path.write_text(
        _structured_note("chinese_untruncated").replace(
            "- **Truncation**: none",
            "- **Truncation**: 第一次调用被截断；分块重读覆盖全部页面，最终未截断",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert ok, f"Validation failed: {err}"


def test_validate_note_structure_rejects_unresolved_truncation(tmp_path):
    """历史截断如果没有明确最终解决，仍不能标 FULL-TEXT。"""
    note_path = tmp_path / "still_truncated.md"
    note_path.write_text(
        _structured_note("still_truncated").replace(
            "- **Truncation**: none",
            "- **Truncation**: first full call truncated; chunked reread incomplete",
        ),
        encoding="utf-8",
    )

    ok, err = _validate_note_structure(note_path)

    assert not ok
    assert "Truncation" in err


def test_validate_outputs_read_mode_missing_notes(reader_agent, temp_workspace):
    """测试read模式输出校验（缺少笔记）。"""
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+1}, "read_priority": 0.8, "target_bucket": "target"}}'
            for i in range(18)
        )
        + "\n"
    )

    # 只创建5篇笔记（少于 deep_read_min）
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(5):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"))

    # 创建其他必需文件
    ct_path = temp_workspace / "literature" / "comparison_table.csv"
    ct_path.write_text("id,title,year\ntest1,Test Paper,2023\n")

    bib_path = temp_workspace / "literature" / "related_work.bib"
    bib_path.write_text("@article{test2023,\n  title={Test},\n  year={2023}\n}\n")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "deep_read_queue" in err or "至少需要完成" in err


def test_validate_outputs_read_mode_requires_target_when_configured(reader_agent, temp_workspace, monkeypatch):
    """T3 默认不应在达到 min 但未达到 target 时提前放行。"""

    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    records = [
        {
            "paper_id": f"paper{i}",
            "normalized_id": f"paper{i}",
            "title": f"Paper {i}",
            "relevance_score": 0.8,
            "access_score_estimate": 0.7,
            "access_score": 0.7,
            "evidence_level": "PARTIAL_TEXT",
            "seed_priority": False,
            "queue_rank": i + 1,
            "read_priority": 0.8,
            "target_bucket": "target",
            "read_disposition": "deep_read",
        }
        for i in range(6)
    ]
    queue_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(3):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"), encoding="utf-8")
    (temp_workspace / "literature" / "comparison_table.csv").write_text("id,title,year\npaper0,Paper 0,2025\n", encoding="utf-8")
    (temp_workspace / "literature" / "related_work.bib").write_text("@article{p0,title={P0},year={2025}}\n", encoding="utf-8")
    (temp_workspace / "literature" / "literature_params.json").write_text(
        json.dumps(
            {
                "semantics": "workspace_literature_coverage_parameters_for_t2_t3",
                "reader": {
                    "deep_read_min": 3,
                    "deep_read_target": 5,
                    "deep_read_max": 6,
                    "require_deep_read_target": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-target",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)

    assert not ok
    assert "至少需要完成 5 篇" in (err or "")
    assert "目标" in (err or "")


def test_validate_outputs_read_mode_reports_matched_invalid_queue_note(reader_agent, temp_workspace):
    """同名 note 存在但结构不合格时，应明确报结构问题，而不是让用户误以为没读。"""
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    records = [
        {
            "paper_id": f"paper{i}",
            "normalized_id": f"paper{i}",
            "title": f"Paper {i}",
            "relevance_score": 0.8,
            "access_score_estimate": 0.7,
            "access_score": 0.7,
            "evidence_level": "PARTIAL_TEXT",
            "seed_priority": False,
            "queue_rank": i + 1,
            "read_priority": 0.8,
            "target_bucket": "target",
        }
        for i in range(18)
    ]
    queue_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(17):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"), encoding="utf-8")
    (notes_dir / "paper17.md").write_text(
        _structured_note("paper17").replace("\n## 10. Key Quotes\n> \"quote\"\n", "\n"),
        encoding="utf-8",
    )
    (temp_workspace / "literature" / "comparison_table.csv").write_text(
        "id,title,year\ntest1,Test Paper,2023\n",
        encoding="utf-8",
    )
    (temp_workspace / "literature" / "related_work.bib").write_text(
        "@article{test2023,\n  title={Test},\n  year={2023}\n}\n",
        encoding="utf-8",
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-invalid-matched-note",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)

    assert not ok
    assert "已匹配但结构不合格" in err
    assert "paper17.md" in err
    assert "## 10. Key Quotes" in err


def test_validate_outputs_read_mode_requires_seed_queue_coverage(reader_agent, temp_workspace):
    """测试read模式输出校验会要求队列中的 seed paper 优先完成。"""
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            [
                '{"paper_id": "seed_paper", "normalized_id": "seed_paper", "title": "Seed Paper", "relevance_score": 0.95, "access_score_estimate": 0.9, "access_score": 1.0, "evidence_level": "FULL_TEXT", "seed_priority": true, "queue_rank": 1, "read_priority": 100.9, "target_bucket": "seed"}',
            ]
            + [
                f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+2}, "read_priority": 0.8, "target_bucket": "target"}}'
                for i in range(17)
            ]
        )
        + "\n"
    )

    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(17):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"))
    (notes_dir / "overflow_note.md").write_text(_structured_note("overflow_note"))

    ct_path = temp_workspace / "literature" / "comparison_table.csv"
    ct_path.write_text("id,title,year\ntest1,Test Paper,2023\n")
    bib_path = temp_workspace / "literature" / "related_work.bib"
    bib_path.write_text("@article{test2023,\n  title={Test},\n  year={2023}\n}\n")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "seed papers" in err


def test_validate_outputs_read_mode_ignores_non_queue_duplicate_stub(reader_agent, temp_workspace):
    """非队列重复 stub 不应拖死已有合格 T3 产物。"""
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+1}, "read_priority": 0.8, "target_bucket": "target"}}'
            for i in range(18)
        )
        + "\n"
    )
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(18):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"), encoding="utf-8")
    (notes_dir / "paper0_duplicate_stub.md").write_text(
        "# duplicate\n\nThis duplicate stub points to paper0 but is not a complete note.\n",
        encoding="utf-8",
    )
    (temp_workspace / "literature" / "comparison_table.csv").write_text(
        "id,title,year\ntest1,Test Paper,2023\n",
        encoding="utf-8",
    )
    (temp_workspace / "literature" / "related_work.bib").write_text(
        "@article{test2023,\n  title={Test},\n  year={2023}\n}\n",
        encoding="utf-8",
    )
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-duplicate-stub",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)

    assert ok, f"Validation failed: {err}"


def test_reader_system_prompt_read_mode_includes_resume_progress(reader_agent, temp_workspace):
    """测试read模式prompt会暴露已有进度，指导断点续跑。"""
    (temp_workspace / "project.yaml").write_text("direction: Test research direction\n")
    (temp_workspace / "literature" / "papers_dedup.jsonl").write_text(
        '{"id": "paper1", "title": "Test Paper 1"}\n{"id": "paper2", "title": "Test Paper 2"}\n'
    )
    (temp_workspace / "literature" / "paper_notes" / "paper1.md").write_text("# Paper 1")
    (temp_workspace / "literature" / "deep_read_queue_pending.jsonl").write_text(
        '{"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 1, "title": "Test Paper 2"}\n'
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
        extra={"is_resume": True, "resumed_from_run_id": "t3-run-001", "resume_reason": "interrupted"},
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "当前已有进度" in prompt
    assert "已有 1 篇笔记" in prompt
    assert "deep_read_queue_pending.jsonl" in prompt
    assert "先做账目对齐" in prompt
    assert "恢复时先补阅读覆盖" in prompt
    assert "只补未完成论文" in prompt


def test_validate_outputs_synthesize_mode_success(reader_agent, temp_workspace):
    """测试synthesize模式输出校验（成功场景）。"""
    # 创建synthesis.md，包含5个必需章节和论文引用
    syn_path = temp_workspace / "literature" / "synthesis.md"
    synthesis_content = """# 文献综述

## 方法家族分类

根据对现有文献的深入分析，我们发现当前方法主要可分为以下三类：

### 1. Attention-based方法
这类方法通过注意力机制来捕捉序列中的长距离依赖关系。如[paper_001]和[paper_002]所展示的，注意力机制能够有效地建模全局上下文信息。Transformer架构[paper_003]是这一类别的代表性工作，它通过多头自注意力机制实现了并行计算，同时保持了强大的建模能力。在后续的研究中，BERT[paper_004]和GPT系列[paper_005]进一步推动了预训练语言模型的发展，展示了大规模预训练的有效性。

### 2. Convolution-based方法
基于卷积神经网络的方法通过局部感受野来提取特征。如[paper_006]所示，卷积操作在图像处理领域取得了巨大成功，近年来也被广泛应用于自然语言处理任务[paper_007]。TextCNN[paper_008]是这一类别的代表性工作，它通过多尺度卷积核来捕捉不同范围的n-gram特征。

### 3. Hybrid方法
混合方法试图结合注意力机制和卷积操作的优点。如[paper_009]所提出的方法，通过层次化设计实现了效率和性能的平衡。Conformer[paper_010]和EfficientFormer[paper_011]等模型代表了这一方向的重要进展。

## 共同假设

通过分析这些方法，我们发现它们共享以下基本假设[paper_012]：

1. **局部特征重要性**：无论是注意力权重还是卷积核，都强调局部特征的重要性。这表明在自然语言处理中，上下文信息的重要性是不均匀的，某些词汇和短语对语义理解贡献更大。

2. **层级表示学习**：通过多层网络的堆叠，可以学习到越来越抽象的特征表示。底层网络捕捉词汇和语法特征，高层网络捕捉语义和语用特征。这一假设在大多数深度学习模型中都得到验证[paper_013]。

3. **表示的平滑性**：连续的表示空间有助于模型的泛化能力。通过将离散的语言符号映射到连续的向量空间，模型能够更好地捕捉词语之间的语义相似性[paper_014]。

## 贡献空间地图

根据[paper_015]和[paper_016]的详细评估，我们将贡献空间按 design rationale、artifact 类型、data view 和 evaluation mode 拆分。在准确率方面，Transformer-based方法表现最佳，但计算复杂度较高；在贡献定位方面，轻量级模型如[paper_017]提出的方法更像对部署约束的改进，而不是全新问题表述。

具体来说，在标准的GLUE基准测试[paper_018]上，BERT和RoBERTa等大型预训练模型达到了人类水平的表现，但在资源受限的场景下，这些模型的部署面临挑战。DistilBERT[paper_019]通过知识蒸馏将模型大小减少40%，同时保持95%的性能。

## 技术趋势

当前研究呈现以下主要趋势：

1. **模型压缩**：通过知识蒸馏、量化等技术减小模型尺寸[paper_020]。这种方法可以在保持一定性能的同时显著降低推理成本。

2. **高效注意力**：设计更高效的注意力机制，如稀疏注意力[paper_021]、线性注意力[paper_022]等。这些方法试图在保持注意力机制优点的同时降低其计算复杂度。

3. **自适应计算**：根据输入复杂度动态调整计算量[paper_023]。对于简单的输入使用较少的计算资源，对于复杂的输入使用更多的计算资源。

4. **多模态融合**：将文本与图像、语音等其他模态的信息进行融合[paper_024]。CLIP[paper_025]和GPT-4V[paper_026]代表了这一方向的重要进展。

## 跨论文矛盾与张力

跨论文矛盾主要体现在统一架构假设与场景自适应假设之间。一组论文认为更强的全局表示足以解释性能提升[paper_024]，另一组论文则表明 evaluation mode、资源约束和部署边界会改变 artifact 的真实价值[paper_025]。这种张力可以直接为 T4 提供问题重构燃料：如果一个方法只在高资源设置下有效，它的 contribution_type 可能只是 routine improvement；如果它改变了边界条件下的设计原则，则可能形成更强贡献[paper_026]。

## 可操作研究问题

基于以上分析，我们提出以下值得深入研究的问题：

1. 如何设计同时兼顾效率和准确率的统一架构？这需要在模型设计和训练策略上进行创新。

2. 如何利用预训练语言模型的强大能力？few-shot和zero-shot学习[paper_027]为我们提供了新的研究方向。

3. 如何在资源受限的场景下部署高性能模型？模型压缩[paper_028]和知识蒸馏[paper_029]是关键的技术手段。

4. 如何处理分布外数据？当前模型在分布内数据上表现优异，但在分布外数据上性能下降明显[paper_030]。这是一个重要的研究方向，因为它直接关系到模型的鲁棒性和实际应用价值。

在深入分析现有文献后，我们注意到一个有趣的现象：尽管不同的方法在架构设计上存在显著差异，但它们在许多任务上都取得了相当接近的性能。这暗示着可能存在一个性能上界，而当前的模型已经接近这个上界。

未来的研究应该关注如何打破这个上界。可能的途径包括：设计更有效的预训练任务[paper_031]、探索新的模型架构[paper_032]、利用外部知识[paper_033]等。

此外，我们还注意到模型评估协议的不一致性。不同的论文使用不同的评估指标、数据划分和训练设置，这使得直接比较不同方法变得困难。建立统一的评估标准和基准测试[paper_034]对于推动领域发展至关重要。

最后，模型的可解释性也是一个重要议题。虽然深度模型取得了巨大成功，但它们的决策过程往往不透明。理解模型如何做出预测[paper_035]不仅有助于改进模型设计，也能增强用户对模型的信任。

总之，这一领域充满了机遇和挑战。我们期待看到更多创新性的工作来解决上述问题，推动自然语言处理技术的进一步发展。

"""  # 超过 2500 字符，确保通过验证

    syn_path.write_text(synthesis_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_synthesize_mode_accepts_real_paper_ids(reader_agent, temp_workspace):
    """测试synthesize模式接受真实paper_notes风格的带点号论文ID。"""
    syn_path = temp_workspace / "literature" / "synthesis.md"
    refs = (
        "[arxiv_2507.07957] [arxiv_2604.07798] [10.2139_ssrn.6616122] "
        "[10.5281_zenodo.19425474] [doi_10.1145/3626772.3657844]"
    )
    synthesis_content = f"""# 文献综述

## 方法家族分类
这些方法可以分为分层记忆、多智能体记忆和轻量化记忆三类，代表论文包括 {refs}。

## 共同假设
共同假设包括长期状态可外化、检索质量决定推理质量、以及压缩会带来信息损失。

## 贡献空间地图
贡献空间地图显示，高准确率系统通常需要更复杂的路由，但真正的贡献差异要按 design rationale、artifact 类型、data view 和 evaluation mode 区分。

## 技术趋势
技术趋势包括分层化、轻量化、无状态企业部署和显式可审计记忆。

## 跨论文矛盾与张力
跨论文矛盾包括可审计长期记忆与压缩成本之间的张力，以及统一路由假设与用户状态差异之间的张力。

## 可操作研究问题
可操作研究问题包括如何统一分层记忆结构、如何控制跨会话记忆漂移、以及如何评估长期用户偏好。

{refs}
""" + "这是一段用于满足长度校验的综合分析。" * 160

    syn_path.write_text(synthesis_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_synthesize_mode_accepts_note_anchors_and_mapped_cites(reader_agent, temp_workspace):
    """T3.5 Markdown 可用 [note:...]，也可用能映射到真实 note 的 BibTeX key。"""
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for paper_id in ["paper0", "paper1", "paper2", "paper3"]:
        (notes_dir / f"{paper_id}.md").write_text(_structured_note(paper_id), encoding="utf-8")
    (notes_dir / "memory_note.md").write_text(
        _structured_note("arxiv:2507.07957"),
        encoding="utf-8",
    )
    (temp_workspace / "literature" / "related_work.bib").write_text(
        "@article{memory2025, title={arxiv:2507.07957}, author={A, Ann}, journal={J}, year={2025}}\n",
        encoding="utf-8",
    )
    refs = "[note:paper0] [note:paper1] [paper2] [note:paper3] \\cite{memory2025}"
    synthesis_content = f"""# 文献综述

## 方法家族分类
方法家族分类基于真实 note anchor 展开，覆盖代表性论文 {refs}。

## 共同假设
共同假设包括可观测状态稳定、反馈可迁移和机制可复用 {refs}。

## 贡献空间地图
贡献空间地图按 design rationale、artifact 类型、data view 和 evaluation mode 区分 {refs}。

## 技术趋势
技术趋势表现为更强的审计性、更细的边界条件和更明确的部署约束 {refs}。

## 跨论文矛盾与张力
跨论文矛盾来自统一机制假设与场景边界假设之间的差异 {refs}。

## 可操作研究问题
可操作研究问题包括如何把这些机制转化为可测量设计选择 {refs}。
""" + "这一段继续展开机制、边界、证据强度和研究机会。" * 180
    (temp_workspace / "literature" / "synthesis.md").write_text(synthesis_content, encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-note-anchor",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_synthesize_mode_does_not_count_unmapped_cites(reader_agent, temp_workspace):
    """孤立 BibTeX key 不能伪装成真实已读 paper note 引用。"""
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for paper_id in ["paper0", "paper1", "paper2", "paper3", "paper4"]:
        (notes_dir / f"{paper_id}.md").write_text(_structured_note(paper_id), encoding="utf-8")
    (temp_workspace / "literature" / "related_work.bib").write_text(
        "\n".join(
            f"@article{{unmapped{i}, title={{Unmapped {i}}}, author={{A, Ann}}, journal={{J}}, year={{2025}}}}"
            for i in range(6)
        )
        + "\n",
        encoding="utf-8",
    )
    refs = "[note:paper0] \\cite{unmapped0,unmapped1,unmapped2,unmapped3,unmapped4}"
    synthesis_content = f"""# 文献综述

## 方法家族分类
方法家族分类不能依赖无法映射到已读 note 的 citation key {refs}。

## 共同假设
共同假设需要真实 note 证据，而不是孤立 BibTeX key {refs}。

## 贡献空间地图
贡献空间地图按 design rationale、artifact 类型、data view 和 evaluation mode 区分 {refs}。

## 技术趋势
技术趋势需要来自 paper note 的证据 anchor {refs}。

## 跨论文矛盾与张力
跨论文矛盾也需要真实已读材料支撑 {refs}。

## 可操作研究问题
可操作研究问题不能只由 unmapped citation key 生成 {refs}。
""" + "这一段继续展开机制、边界、证据强度和研究机会。" * 180
    (temp_workspace / "literature" / "synthesis.md").write_text(synthesis_content, encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-unmapped-cites",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "真实已读论文引用过少" in (err or "")


def test_validate_outputs_synthesize_mode_missing_sections(reader_agent, temp_workspace):
    """测试synthesize模式输出校验（缺少章节）。"""
    # 创建synthesis.md，但缺少某些章节
    syn_path = temp_workspace / "literature" / "synthesis.md"
    synthesis_content = """# 文献综述

## 方法家族分类
这是方法家族分类章节...

## 共同假设
这是共同假设章节...

""" + "x" * 2000

    syn_path.write_text(synthesis_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "缺少" in err or "章节" in err
