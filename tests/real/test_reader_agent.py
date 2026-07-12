"""Reader Agent Integration Tests.

测试文献阅读 Agent（T3 read 模式和 T3.5 synthesize 模式）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.reader import ReaderAgent


def _structured_note(paper_id: str, *, abstract_only: bool = False) -> str:
    status = "[ABSTRACT-ONLY]" if abstract_only else "[FULL-TEXT]"
    evidence_line = (
        "- N/A for abstract-only note\n"
        if abstract_only
        else "- Accuracy improves over the baseline. [Evidence: Results section]\n"
    )
    if abstract_only:
        coverage = """- **PDF source**: not available
- **Pages read**: 0 / unknown
- **Extraction calls**: none
- **Truncation**: none
- **Status rationale**: PDF was unavailable, so this note is based on metadata and abstract only.
"""
    else:
        coverage = f"""- **PDF source**: literature/pdfs/{paper_id}.pdf
- **Pages read**: 1-10 / 10
- **Extraction calls**: extract_pdf_text pages 1-4, 5-8, 9-10
- **Truncation**: first preview was truncated, but chunked rereads covered all pages; final truncation: none.
- **Status rationale**: All PDF pages were read via chunked extraction.
"""
    return f"""# {paper_id}

- **ID**: {paper_id}
- **Authors**: Author A, Author B
- **Venue**: TestConf (2025)
- **DOI/arXiv**: arxiv:2501.00001
- **Citations**: 10
- **Verification**: metadata_verified (confidence: 0.95)
- **Status**: {status}

## 1. Problem & Motivation
The paper studies a relevant problem and motivates it with a concrete systems bottleneck.

## 2. Method Overview
The method combines a compact model component with a calibrated retrieval or routing step.

## 3. Key Results
{evidence_line}

## 4. Claims vs Evidence
| Claim | Evidence | Strength |
|-------|----------|----------|
| The approach improves efficiency | Reported controlled experiments | Strong |

## 5. Limitations
- The evaluation is limited to a small set of benchmark conditions.

## 6. Relevance to Our Research
- The design informs our own efficiency-oriented research direction.

## 7. Technical Details Worth Noting
- The implementation details include reproducible seeds and ablation-ready components.

## 8. Strengths
- Strong empirical framing.

## 9. Weaknesses / Gaps
- Limited stress testing.

## 10. Key Quotes
> "Representative quote."

## 11. My Questions
- How stable is the method under distribution shift?

## 12. Reading Coverage
{coverage}

## 13. Mechanism Claim
- **Stated mechanism**: The method improves the target metric through a separable representation or routing mechanism.
- **Evidence type**: ablation_supported
- **Supporting artifact**: Results table and ablation discussion.

## 14. Design Rationale
- **Rationale**: The design tests whether the proposed mechanism changes the relevant behavior rather than only adding capacity.
- **Rationale evidence**: The paper reports controlled results and connects them to a method component.
- **Rationale weakness**: The evidence may not cover all boundary conditions.

## 15. Artifact & Design Principles
- **Artifact type**: model component
- **Artifact description**: A reproducible component that changes model behavior.
- **Design principles**: isolate the mechanism; compare against a simple control; report ablations.

## 16. Data View & Evaluation Mode
- **Data view**: benchmark examples grouped by task condition.
- **Evaluation mode**: main metric plus ablation evidence.
- **Validity concern**: Aggregate metrics may hide subgroup-specific failures.

## 17. Contribution Type
- **Contribution type**: improvement
- **Contribution character**: The work improves an existing method by making a mechanism-level design claim.
- **Why not routine**: It is not only an implementation tweak because it states a testable mechanism and boundary.

## 18. Boundary Conditions
- **Works when**: the benchmark assumptions match the paper setting.
- **May fail when**: data distribution or compute constraints differ substantially.
- **Untested boundary**: very small or shifted data regimes.

## 19. Cross-Paper Tension
- **Tension**: Some related work treats the mechanism as broadly useful while others imply condition-specific effects.
- **Competing rationale**: Simpler baselines may explain part of the reported gain.
- **Idea fuel**: Test whether the mechanism remains useful under a boundary condition.
"""


class TestReaderAgent:
    """Reader Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = ReaderAgent()
        assert agent is not None
        assert agent.spec.name == "reader"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = ReaderAgent()
        # reader agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 reader agent 没有 docker_exec 工具。"""
        agent = ReaderAgent()
        # reader agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt_read_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 read 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            '{"id": "p1", "title": "Paper 1"}\n'
            '{"id": "p2", "title": "Paper 2"}\n',
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_system_prompt_synthesize_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 synthesize 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper_notes
        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "p1.md").write_text("# Paper 1\n\nNotes...", encoding="utf-8")
        (notes_dir / "p2.md").write_text("# Paper 2\n\nNotes...", encoding="utf-8")

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message_read_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 read 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "read" in msg.lower() or "paper" in msg.lower()

    def test_agent_initial_user_message_synthesize_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 synthesize 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "synthesize" in msg.lower() or "synthesis" in msg.lower()


class TestReaderAgentValidateReadOutputs:
    """Reader Agent T3 (read) 输出验证测试。"""

    def test_validate_read_outputs_no_notes(self, standard_workspace: Path, project_yaml: Path):
        """测试无笔记时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            '{"id": "p1", "title": "Paper 1"}\n'
            '{"id": "p2", "title": "Paper 2"}\n'
            '{"id": "p3", "title": "Paper 3"}\n',
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "paper_notes" in err

    def test_validate_read_outputs_insufficient_notes(self, standard_workspace: Path, project_yaml: Path):
        """测试笔记数量不足时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl（10 篇论文）
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.parent.mkdir(parents=True, exist_ok=True)
        papers_dedup.write_text(
            "\n".join(f'{{"id": "p{i}", "title": "Paper {i}"}}' for i in range(10)) + "\n",
            encoding="utf-8",
        )

        # 创建 paper_notes（只有 3 篇笔记，不足默认 100% fallback 覆盖）
        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (notes_dir / f"p{i}.md").write_text(_structured_note(f"p{i}"), encoding="utf-8")

        # 创建 comparison_table.csv 和 related_work.bib
        ct = standard_workspace / "literature" / "comparison_table.csv"
        ct.write_text("Method,Accuracy\nMethod1,0.9\n", encoding="utf-8")

        bib = standard_workspace / "literature" / "related_work.bib"
        bib.write_text("@article{key1,\n  title={Title}\n}", encoding="utf-8")

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "笔记" in err or "note" in err.lower()

    def test_validate_read_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl（5 篇论文）
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            "\n".join(f'{{"id": "p{i}", "title": "Paper {i}"}}' for i in range(5)) + "\n",
            encoding="utf-8",
        )

        # 创建 paper_notes（5 篇笔记，满足默认 100% fallback 覆盖）
        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (notes_dir / f"p{i}.md").write_text(_structured_note(f"p{i}"), encoding="utf-8")

        # 创建 comparison_table.csv
        ct = standard_workspace / "literature" / "comparison_table.csv"
        ct.write_text("Method,Accuracy\nMethod1,0.9\n", encoding="utf-8")

        # 创建 related_work.bib
        bib = standard_workspace / "literature" / "related_work.bib"
        bib.write_text("@article{key1,\n  title={Title}\n}", encoding="utf-8")

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, err


class TestReaderAgentValidateSynthesizeOutputs:
    """Reader Agent T3.5 (synthesize) 输出验证测试。"""

    def test_validate_synthesize_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试无 synthesis.md 时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "synthesis.md" in err

    def test_validate_synthesize_missing_sections(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少必需章节时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建不完整的 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "Only a brief intro.\n",
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "章节" in err or "section" in err.lower()

    def test_validate_synthesize_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 6):
            (notes_dir / f"p{i}.md").write_text(_structured_note(f"p{i}", abstract_only=True), encoding="utf-8")

        # 创建完整的 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Literature Synthesis\n\n"
            "## Method Families\n\n"
            "Several method families have been proposed in recent years. "
            "These include family A based on approach X, family B using technique Y, "
            "and family C employing method Z. Each family has distinct characteristics "
            "and trade-offs that are important for practitioners to understand. "
            "Family A tends to excel in scenarios requiring high accuracy but demands "
            "significant computational resources. Family B offers a balance between "
            "performance and efficiency, making it suitable for practical applications. "
            "Family C represents the latest advances in the field, combining elements "
            "from both previous approaches while introducing novel techniques "
            "[note:p1] [note:p2].\n\n"
            "## Shared Assumptions\n\n"
            "All methods assume X as their fundamental premise. "
            "This includes the availability of training data, the assumption that "
            "patterns in the data are generalizable, and that evaluation metrics "
            "appropriately capture the desired outcomes. These assumptions are critical "
            "for understanding the limitations and potential failure modes of each approach "
            "[note:p2] [note:p3].\n\n"
            "## Contribution-Space Map\n\n"
            "The contribution space is organized around competing design rationales, "
            "artifact choices, evaluation modes, and boundary conditions rather than "
            "only metric trade-offs. Several papers share a routine improvement frame, "
            "while others expose underexplored design-rationale gaps and cross-paper tensions "
            "[note:p3] [note:p4].\n\n"
            "## Trends & Cross-Paper Contradictions\n\n"
            "Trends include A and B. Cross-paper contradictions show that some methods "
            "interpret the same evidence through incompatible design rationales, which "
            "creates fuel for reframing in the next ideation stage [note:p4] [note:p5].\n\n"
            "## Technology Trends\n\n"
            "Trends include A and B. Emerging approaches are focusing on reducing "
            "computational requirements while maintaining accuracy. "
            "There is also growing interest in interpretability and fairness. "
            "These trends reflect the maturation of the field and its increasing "
            "practical relevance across various application domains.\n\n"
            "## Research Questions\n\n"
            "[note:p1] How to improve X? This question remains open and requires further investigation.\n\n"
            "[note:p2] What about Y? Addressing this could lead to significant improvements.\n\n"
            "[note:p3] What is the relationship between Z and W? Understanding this could unlock new approaches.\n\n"
            "[note:p4] How do methods perform under distribution shift? This is crucial for real-world deployment.\n\n"
            "[note:p5] Can we achieve better efficiency without sacrificing accuracy? This is an ongoing challenge.\n\n"
            "This is a long enough synthesis document with multiple sections that references many papers.\n"
            "It references [note:p1], [note:p2], [note:p3], [note:p4], and [note:p5] from the paper notes.\n",
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True
