from __future__ import annotations

import json
from pathlib import Path

from researchos.tools.seed_paper_processor import (
    ProcessSeedPaperTool,
    _choose_pdf_title,
    _is_likely_pdf_header_or_journal_title,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _policy(workspace: Path) -> WorkspaceAccessPolicy:
    return WorkspaceAccessPolicy(
        workspace_dir=workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )


def test_chinese_pdf_title_filter_rejects_journal_mastheads_and_issue_headers() -> None:
    assert _is_likely_pdf_header_or_journal_title("《管理世界》（月刊）")
    assert _is_likely_pdf_header_or_journal_title("第21卷第7期 管 理 科 学 学 报 Vol．21 No．7")
    assert not _is_likely_pdf_header_or_journal_title("数智赋能：信息系统研究的新跃迁")


def test_chinese_pdf_title_selection_prefers_real_title_over_header() -> None:
    selection = _choose_pdf_title(
        metadata_title="《管理世界》（月刊）",
        first_page_text="\n".join(
            [
                "第21卷第7期 管 理 科 学 学 报 Vol．21 No．7",
                "数智赋能：信息系统研究的新跃迁",
                "作者甲 作者乙",
            ]
        ),
        filename_stem="管理世界-下载稿",
    )

    assert selection["title"] == "数智赋能：信息系统研究的新跃迁"
    assert selection["title_source"] == "first_page"
    assert "《管理世界》（月刊）" in selection["rejected_title_candidates"]


def test_chinese_pdf_title_selection_uses_filename_when_only_header_exists() -> None:
    selection = _choose_pdf_title(
        metadata_title="第21卷第7期 管 理 科 学 学 报 Vol．21 No．7",
        first_page_text="《管理世界》（月刊）\n2024年第1期",
        filename_stem="组织算法风险治理综述",
    )

    assert selection["title"] == "组织算法风险治理综述"
    assert selection["title_source"] == "filename"
    assert selection["title_confidence"] in {"heuristic_medium", "heuristic_high"}


def test_chinese_pdf_title_selection_strips_short_author_suffix_from_filename() -> None:
    selection = _choose_pdf_title(
        metadata_title="《管理世界》（月刊）",
        first_page_text="《管理世界》（月刊）\n2024年第1期",
        filename_stem="大数据环境下的决策范式转变与使能创新_陈国青",
    )

    assert selection["title"] == "大数据环境下的决策范式转变与使能创新"
    assert selection["title_source"] == "filename"


async def test_process_seed_paper_jsonl_preserves_chinese_and_title_diagnostics(tmp_workspace: Path) -> None:
    pdf_path = tmp_workspace / "seed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    tool = ProcessSeedPaperTool(_policy(tmp_workspace))

    async def fake_extract(_path: Path) -> dict[str, object]:
        return {
            "title": "数智赋能：信息系统研究的新跃迁",
            "authors": ["张三", "李四"],
            "year": 2024,
            "title_source": "first_page",
            "title_confidence": "heuristic_high",
            "rejected_title_candidates": ["《管理世界》（月刊）"],
        }

    tool._extract_pdf_metadata = fake_extract  # type: ignore[method-assign]

    result = await tool.execute(source="pdf_path", value=str(pdf_path), role="anchor")

    assert result.ok, result.content
    seed_text = (tmp_workspace / "user_seeds" / "seed_papers.jsonl").read_text(encoding="utf-8")
    assert "数智赋能" in seed_text
    assert "\\u6570" not in seed_text
    record = json.loads(seed_text)
    assert record["title_source"] == "first_page"
    assert record["rejected_title_candidates"] == ["《管理世界》（月刊）"]
