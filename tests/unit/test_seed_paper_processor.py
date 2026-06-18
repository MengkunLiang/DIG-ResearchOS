from __future__ import annotations

import json
from pathlib import Path

from researchos.tools.seed_paper_processor import (
    ProcessSeedPaperTool,
    _choose_pdf_title,
    _is_likely_pdf_header_or_journal_title,
    _is_likely_pdf_abstract_or_body_line,
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


def test_chinese_pdf_title_selection_prefers_filename_when_filename_is_deliberate_title_author_pattern() -> None:
    selection = _choose_pdf_title(
        metadata_title="《管理世界》（月刊）",
        first_page_text="\n".join(
            [
                "【摘要】 本文从澄清课程群的内涵入手，探讨我国高校课程群建设的主体、客体和重心。",
                "（1. 上海体育大学武术学院，上海 200438；2. 上海体育大学中国体育历史研究院，上海 200438）",
                "第1 作者简介：周丽，女，硕士，副教授，硕士生导师，研究方向为民族传统体育。",
            ]
        ),
        filename_stem="高等体育院校民族民间体育课程体系优化研究_周丽",
    )

    assert selection["title"] == "高等体育院校民族民间体育课程体系优化研究"
    assert selection["title_source"] == "filename"
    assert selection["title_confidence"] in {"heuristic_medium", "heuristic_high"}


def test_pdf_title_selection_uses_filename_over_abstract_or_affiliation_noise() -> None:
    selection = _choose_pdf_title(
        metadata_title="",
        first_page_text="\n".join(
            [
                "【摘要】 本文从澄清课程群的内涵入手，探讨我国高校课程群建设的主体、客体和重心。",
                "（1. 上海体育大学武术学院，上海 200438；2. 上海体育大学中国体育历史研究院，上海 200438）",
                "第1 作者简介：周丽，女，硕士，副教授，硕士生导师，研究方向为民族传统体育。",
            ]
        ),
        filename_stem="高等体育院校民族民间体育课程体系优化研究_周丽",
    )

    assert selection["title"] == "高等体育院校民族民间体育课程体系优化研究"
    assert selection["title_source"] == "filename"
    assert selection["metadata_review_required"] is True


def test_pdf_title_filter_rejects_abstract_body_and_english_journal_masthead() -> None:
    assert _is_likely_pdf_abstract_or_body_line(
        "本文从澄清课程群的内涵入手，探讨我国高校课程群建设的主体、客体和重心。"
    )
    assert _is_likely_pdf_header_or_journal_title("JOURNAL OF NATIONAL ACADEMY OF EDUCATION ADMINISTRATION")
    assert not _is_likely_pdf_header_or_journal_title("大学生体质健康促进机制研究")


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


async def test_process_seed_paper_does_not_append_duplicate_when_existing_manual_record_is_better(
    tmp_workspace: Path,
) -> None:
    seed_dir = tmp_workspace / "user_seeds"
    seed_dir.mkdir(parents=True)
    (seed_dir / "seed_papers.jsonl").write_text(
        json.dumps(
            {
                "title": "论高校课程群建设",
                "authors": ["李慧仙"],
                "year": 2023,
                "pdf_path": "user_seeds/pdfs/论高校课程群建设_李慧仙.pdf",
                "title_source": "manual_correction",
                "title_confidence": "high",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    pdf_path = tmp_workspace / "论高校课程群建设_李慧仙.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    tool = ProcessSeedPaperTool(_policy(tmp_workspace))

    async def fake_extract(_path: Path) -> dict[str, object]:
        return {
            "title": "【摘要】 本文从澄清课程群的内涵入手",
            "authors": [],
            "year": 2023,
            "title_source": "first_page",
            "title_confidence": "heuristic_medium",
            "metadata_review_required": True,
        }

    tool._extract_pdf_metadata = fake_extract  # type: ignore[method-assign]

    result = await tool.execute(source="pdf_path", value=str(pdf_path), role="anchor")

    assert result.ok, result.content
    records = [
        json.loads(line)
        for line in (seed_dir / "seed_papers.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    assert records[0]["title"] == "论高校课程群建设"
