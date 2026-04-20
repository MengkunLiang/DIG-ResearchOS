"""Writer/Reviewer/Submission Agent 单元测试"""

import json
from pathlib import Path

import pytest

from researchos.agents.writer import WriterAgent
from researchos.agents.reviewer import ReviewerAgent
from researchos.agents.submission import SubmissionAgent, check_anonymization


class MockExecutionContext:
    """模拟 ExecutionContext"""

    def __init__(self, mode: str, workspace_dir: Path, extra: dict = None):
        self.mode = mode
        self.workspace_dir = workspace_dir
        self.agent_name = "test"
        self.task_id = "T8-WRITE" if "write" in mode else f"T8-{mode.upper()}" if mode else "T8"
        self.extra = extra or {}
        if "phase" not in self.extra and mode:
            self.extra["phase"] = mode
        if "round" not in self.extra and "review" in mode:
            self.extra["round"] = 1


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时 workspace"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature").mkdir()
    (ws / "experiments").mkdir()
    (ws / "ideation").mkdir()
    (ws / "drafts").mkdir()
    (ws / "drafts" / "figures").mkdir()
    (ws / "drafts" / "review_rounds").mkdir()
    (ws / "submission").mkdir()
    (ws / "references").mkdir()
    (ws / "references" / "venue_templates").mkdir()
    (ws / "project.yaml").write_text(
        "name: test_project\nresearch_direction: AI\ntarget_venue: neurips2026"
    )
    return ws


# ══════════════════════════════════════════════════════
# Writer Agent Tests
# ══════════════════════════════════════════════════════

def test_writer_agent_initialization():
    """测试 WriterAgent 初始化"""
    agent = WriterAgent()
    assert agent.spec.name == "writer"
    assert agent.spec.max_steps == 60
    assert agent.spec.max_tokens_total == 500_000
    assert "write_file" in agent.spec.tool_names
    assert "drafts/" in agent.spec.allowed_write_prefixes


def test_writer_outline_phase_initial_message(temp_workspace):
    """测试 outline 模式的初始消息"""
    agent = WriterAgent()
    ctx = MockExecutionContext("outline", temp_workspace, {"phase": "outline"})
    msg = agent.initial_user_message(ctx)

    assert "Phase 1" in msg
    assert "outline.md" in msg
    assert "标题候选" in msg


def test_writer_draft_phase_initial_message(temp_workspace):
    """测试 draft 模式的初始消息"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    msg = agent.initial_user_message(ctx)

    assert "Phase 2" in msg
    assert "paper.tex" in msg
    assert "results_summary.json" in msg


def test_writer_validate_outputs_outline_success(temp_workspace):
    """测试 outline 模式验证成功"""
    agent = WriterAgent()
    ctx = MockExecutionContext("outline", temp_workspace, {"phase": "outline"})

    # 创建符合要求的 outline
    outline_content = """# 论文大纲

## 标题候选
1. Test Title 1
2. Test Title 2

## Abstract
- 问题：描述
- 方法：方案
- 结果：效果

## 1. Introduction
介绍章节内容

## 2. Related Work
相关工作章节
"""
    (temp_workspace / "drafts" / "outline.md").write_text(outline_content)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_writer_validate_outputs_outline_too_short(temp_workspace):
    """测试 outline 模式内容过短"""
    agent = WriterAgent()
    ctx = MockExecutionContext("outline", temp_workspace, {"phase": "outline"})

    (temp_workspace / "drafts" / "outline.md").write_text("Too short")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "过短" in err


def test_writer_validate_outputs_draft_success(temp_workspace):
    """测试 draft 模式验证成功"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})

    # 创建符合要求的 paper.tex
    paper_content = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\title{Test Paper}
\author{}
\maketitle
\begin{abstract}
This is an abstract.
\end{abstract}
\section{Introduction}
Content here.
\section{Method}
Method description.
\section{Experiments}
Experimental results.
\section{Conclusion}
Conclusion.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)

    # 创建相关的 bib 文件
    (temp_workspace / "literature" / "related_work.bib").write_text(
        "@article{test2024,\n  author={Test Author},\n  title={Test Title},\n  year={2024}\n}"
    )

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_writer_validate_outputs_draft_missing_documentclass(temp_workspace):
    """测试 draft 模式缺少 documentclass"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})

    # 提供完整的LaTeX结构但没有 documentclass 命令
    paper_content = (
        r"\begin{document}"
        r"\section{Test Section}"
        r"This is a test paper content."
        r"\end{document}"
    )
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "documentclass" in err


def test_writer_validate_outputs_draft_invalid_citations(temp_workspace):
    """测试 draft 模式引用不存在的 BibTeX key"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})

    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test}
\section{Intro}
Some text \cite{nonexistent2024}.
\section{Method}
More text.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)

    # 创建空 bib 文件
    (temp_workspace / "literature" / "related_work.bib").write_text("")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "nonexistent2024" in err


# ══════════════════════════════════════════════════════
# Reviewer Agent Tests
# ══════════════════════════════════════════════════════

def test_reviewer_agent_initialization():
    """测试 ReviewerAgent 初始化"""
    agent = ReviewerAgent()
    assert agent.spec.name == "reviewer"
    assert agent.spec.max_steps == 30
    assert agent.spec.max_tokens_total == 300_000
    assert "read_file" in agent.spec.tool_names
    assert "drafts/review_rounds/" in agent.spec.allowed_write_prefixes


def test_reviewer_initial_message(temp_workspace):
    """测试审稿初始消息"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})
    msg = agent.initial_user_message(ctx)

    assert "Reviewer" in msg
    assert "round_1.md" in msg
    assert "内容完整性" in msg


def test_reviewer_validate_outputs_success(temp_workspace):
    """测试审稿报告验证成功"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})

    report_content = """# 审稿报告 - Round 1

生成时间: 2024-01-25 10:30:00

## 总体评价

这篇论文整体质量良好。

**推荐**: Minor Revision

## 主要问题（Major Issues）

### 问题1: 相关工作不完整
**位置**: Section 2
**描述**: 缺少重要引用
**建议**: 补充相关工作
**严重程度**: Medium

## 次要问题（Minor Issues）

### 问题1: 格式问题
**位置**: Abstract
**描述**: 格式不规范
**建议**: 修正格式
**严重程度**: Low

## 总结

论文需要小修后提交。
"""
    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_reviewer_validate_outputs_too_short(temp_workspace):
    """测试审稿报告内容过短"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})

    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text("Too short")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "过短" in err


def test_reviewer_validate_outputs_missing_sections(temp_workspace):
    """测试审稿报告缺少必需章节"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})

    report_content = """# 审稿报告 - Round 1

生成时间: 2024-01-25

## 总体评价

这篇论文整体质量良好。

## 主要问题

问题描述

"""
    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "次要问题" in err or "缺少必需章节" in err


# ══════════════════════════════════════════════════════
# Submission Agent Tests
# ══════════════════════════════════════════════════════

def test_submission_agent_initialization():
    """测试 SubmissionAgent 初始化"""
    agent = SubmissionAgent()
    assert agent.spec.name == "submission"
    assert agent.spec.max_steps == 40
    assert agent.spec.max_tokens_total == 200_000
    assert "docker_exec" in agent.spec.tool_names
    assert "submission/" in agent.spec.allowed_write_prefixes
    # 检查 pre_hooks 包含 check_anonymization 函数
    hook_names = [h.__name__ if callable(h) else str(h) for h in agent.spec.pre_hooks]
    assert "check_anonymization" in hook_names


def test_submission_initial_message(temp_workspace):
    """测试投稿准备初始消息"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)
    msg = agent.initial_user_message(ctx)

    assert "Submission" in msg
    assert "neurips" in msg
    assert "迁移" in msg


def test_submission_validate_outputs_success(temp_workspace):
    """测试投稿包验证成功"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    # 创建bundle目录和必需文件
    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")

    # 创建迁移报告
    report_content = """# 投稿迁移报告

生成时间: 2024-01-26 15:30:00
目标会议: neurips2026

## 迁移摘要

- 源文件: drafts/paper.tex
- 目标模板: neurips2026
- 迁移状态: 成功
- 编译状态: 成功
- 匿名化检查: 通过

## 文件清单

- main.tex
- references.bib

## 投稿检查清单

- [x] 主论文
- [x] 参考文献
"""
    (temp_workspace / "submission" / "migration_report.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_submission_validate_outputs_missing_bundle(temp_workspace):
    """测试投稿包缺少bundle目录"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "bundle" in err.lower()


def test_submission_validate_outputs_missing_main_tex(temp_workspace):
    """测试投稿包缺少main.tex"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "main.tex" in err


def test_submission_validate_outputs_report_too_short(temp_workspace):
    """测试迁移报告内容过短"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    # 创建bundle目录和必需文件
    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")

    # 创建过短的报告
    (temp_workspace / "submission" / "migration_report.md").write_text("Too short")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "migration_report.md" in err


# ══════════════════════════════════════════════════════
# Anonymization Check Tests
# ══════════════════════════════════════════════════════

def test_check_anonymization_clean_paper(temp_workspace):
    """测试匿名化检查 - 干净的论文"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
\section{Introduction}
This is a test paper about machine learning.
\section{Method}
We propose a new approach.
\section{Experiments}
Results show effectiveness.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert ok
    assert err is None


def test_check_anonymization_email_detected(temp_workspace):
    """测试匿名化检查 - 检测到邮箱"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
\author{Test Author}
\maketitle
Contact: author@example.com for questions.
\section{Method}
Our method is described here.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "email" in err


def test_check_anonymization_github_detected(temp_workspace):
    """测试匿名化检查 - 检测到GitHub链接"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
Code is available at github.com/test/project.
\section{Method}
Our implementation follows standard practices.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "github" in err


def test_check_anonymization_url_detected(temp_workspace):
    """测试匿名化检查 - 检测到URL"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
Results available at https://example.com/results
\section{Method}
Our method shows promise.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "url" in err


def test_check_anonymization_acknowledgments_detected(temp_workspace):
    """测试匿名化检查 - 检测到致谢"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
\section{Acknowledgments}
We thank the reviewers for their helpful comments.
\section{Method}
Our method is described here.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "acknowledgments" in err or "致谢" in err


def test_check_anonymization_no_paper_file(temp_workspace):
    """测试匿名化检查 - 没有paper.tex文件"""
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert ok  # 应该通过，因为没有文件时不报错