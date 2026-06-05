"""T3 Abstract Sweep — 摘要级轻量补读模块。

在 deep read 完成后，从 verified/dedup 池中再扫一批未被全文笔记覆盖的
论文，优先调用 Reader LLM 基于 title/abstract 生成精简 evidence note。
LLM 不可用时才退回确定性 fallback；无论哪种路径，输出都必须标为
ABSTRACT-ONLY，不能作为全文机制结论。
"""

from __future__ import annotations

import csv
import io
import inspect
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..agents._common import load_jsonl
from ..literature_identity import (
    is_paper_note_file,
    paper_note_match_keys,
    record_is_covered,
)


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "enabled": False,
    "lite_paper_num": 40,
    "min_relevance": 0.4,
    "sources": ["papers_verified", "papers_dedup"],
    "exclude_already_read": True,
}


AbstractReader = Callable[[dict[str, Any], str], str | Awaitable[str]]


def _resolve_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    if raw:
        cfg.update(raw)
    return cfg


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def build_sweep_candidates(
    workspace: Path,
    config: dict[str, Any] | None = None,
) -> list[dict]:
    """从 papers_verified/papers_dedup 中筛选 abstract sweep 候选。"""

    cfg = _resolve_config(config)
    lite_num = int(cfg.get("lite_paper_num", 40))
    min_rel = float(cfg.get("min_relevance", 0.4))
    sources = cfg.get("sources", ["papers_verified", "papers_dedup"])
    exclude_read = cfg.get("exclude_already_read", True)

    completed_keys: set[str] = set()
    if exclude_read:
        notes_dir = workspace / "literature" / "paper_notes"
        if notes_dir.exists():
            for note_path in notes_dir.glob("*.md"):
                if is_paper_note_file(note_path):
                    completed_keys.update(paper_note_match_keys(note_path))
        bridge_notes_dir = workspace / "literature" / "paper_notes_bridge"
        if bridge_notes_dir.exists():
            for note_path in bridge_notes_dir.glob("**/*.md"):
                if is_paper_note_file(note_path):
                    completed_keys.update(paper_note_match_keys(note_path))

    # 已有 abstract note 的 paper ID（避免重复 sweep）
    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    if abstract_dir.exists():
        for note_path in abstract_dir.glob("*.md"):
            if is_paper_note_file(note_path):
                completed_keys.update(paper_note_match_keys(note_path))

    # 加载候选池
    pool: list[dict] = []
    seen_ids: set[str] = set()
    for source_name in sources:
        path = workspace / "literature" / f"{source_name}.jsonl"
        if not path.exists():
            continue
        for record in load_jsonl(path):
            rid = _normalize_id(record)
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            pool.append(record)

    # 筛选
    candidates: list[dict] = []
    for record in pool:
        rid = _normalize_id(record)
        if exclude_read and record_is_covered(record, completed_keys):
            continue
        if _is_duplicate_record(record):
            continue
        if _is_semantic_excluded(record):
            continue
        if not str(record.get("title") or "").strip():
            continue
        relevance = float(record.get("relevance_score", 0))
        if relevance < min_rel:
            continue
        if not record.get("abstract", "").strip():
            continue
        candidates.append(record)

    # 按 relevance_score 降序
    candidates.sort(key=lambda r: float(r.get("relevance_score", 0)), reverse=True)
    return candidates[:lite_num]


def _normalize_id(record: dict) -> str:
    """提取并规范化 paper ID。"""
    raw = str(
        record.get("normalized_id")
        or record.get("paper_id")
        or record.get("id")
        or ""
    ).strip()
    if not raw:
        return ""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", raw.replace(":", "_").replace("/", "_")).strip("_")


def _is_duplicate_record(record: dict[str, Any]) -> bool:
    """Return true only for explicit duplicate markers."""

    if record.get("duplicate_of") or record.get("is_duplicate") is True or record.get("duplicate") is True:
        return True
    status = str(record.get("dedup_status") or record.get("duplicate_status") or "").strip().casefold()
    return status in {"duplicate", "merged_duplicate", "excluded_duplicate"}


def _is_semantic_excluded(record: dict[str, Any]) -> bool:
    """Skip records Scout explicitly screened out as unrelated/shared-keyword only."""

    screen = record.get("semantic_screen")
    if not isinstance(screen, dict):
        return False
    relation = str(
        screen.get("relation_to_project")
        or screen.get("relation")
        or ""
    ).strip().casefold()
    if relation in {"shared_keyword_only", "unrelated"}:
        return True
    if screen.get("can_enter_deep_read") is False and not record.get("seed_priority"):
        return True
    return False


# ---------------------------------------------------------------------------
# Note generation
# ---------------------------------------------------------------------------

def generate_abstract_note(paper: dict) -> str:
    """从 paper record 生成精简 abstract-only note。"""

    title = paper.get("title", "Unknown").strip()
    paper_id = _normalize_id(paper)
    year = _extract_year(paper)
    venue = paper.get("venue", "").strip() or "unknown"
    authors = paper.get("authors", [])
    if isinstance(authors, list) and authors:
        author_str = ", ".join(str(a) for a in authors[:5])
        if len(authors) > 5:
            author_str += " et al."
    else:
        author_str = "Unknown"
    abstract = paper.get("abstract", "").strip()
    relevance = paper.get("relevance_score", "")

    # 从 abstract 切分内容；这些只是位置片段，不能当成 LLM 理解后的论文笔记。
    sentences = _split_sentences(abstract)
    opening_hint = _extract_problem(sentences)
    middle_hint = _extract_method(sentences)
    closing_hint = _extract_results(sentences)

    note = f"""# {title}

- **ID**: {paper_id}
- **Authors**: {author_str}
- **Venue**: {venue} ({year})
- **DOI/arXiv**: {paper.get('doi', '') or paper.get('arxiv_id', '') or paper.get('id', '')}
- **Relevance**: {relevance}
- **Status**: [ABSTRACT-ONLY]

## 1. Problem & Motivation
LLM_REVIEW_REQUIRED. Abstract opening snippet:
{opening_hint}

## 2. Method Summary
LLM_REVIEW_REQUIRED. Abstract middle snippet:
{middle_hint}

## A. 核心做法/视角
LLM_REVIEW_REQUIRED. Abstract-only hint about the method, lens, or viewpoint:
{middle_hint}

## B. 桥接点
LLM_REVIEW_REQUIRED. Explain how this paper may connect to the target domain or adjacent transfer:
{paper.get('why_relevant', '') or paper.get('source_bucket', '') or 'review required before use'}

## 3. Key Claimed Results
LLM_REVIEW_REQUIRED. Abstract closing snippet:
{closing_hint}

## Raw Abstract
{abstract or "(no abstract available)"}

## 13. Mechanism Claim
- **Stated mechanism**: LLM_REVIEW_REQUIRED (abstract-only hint; do not use as consensus evidence)
- **Evidence type**: abstract_claim_hint
- **Supporting artifact**: not verified (abstract only)

## Source
- Read from: abstract / metadata only
- No PDF extraction performed
- Original abstract length: {len(abstract)} chars
- Review status: LLM must inspect before using this paper for mechanism or novelty claims
"""
    return note


def build_abstract_reader_prompt(paper: dict[str, Any]) -> str:
    """Build the per-paper Reader LLM prompt for abstract-only note generation."""

    title = str(paper.get("title") or "Unknown").strip()
    paper_id = _normalize_id(paper)
    authors = paper.get("authors", [])
    if isinstance(authors, list):
        author_text = ", ".join(
            str(item.get("name") if isinstance(item, dict) else item).strip()
            for item in authors[:8]
            if str(item.get("name") if isinstance(item, dict) else item).strip()
        )
    else:
        author_text = str(authors or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    metadata = {
        "id": paper_id,
        "title": title,
        "authors": author_text or "Unknown",
        "year": _extract_year(paper),
        "venue": paper.get("venue") or "",
        "doi_or_arxiv": paper.get("doi") or paper.get("arxiv_id") or paper.get("id") or "",
        "relevance_score": paper.get("relevance_score", ""),
        "semantic_screen": paper.get("semantic_screen", {}),
        "source_bucket": paper.get("source_bucket") or paper.get("search_bucket") or "",
        "why_relevant": paper.get("why_relevant") or "",
    }
    return (
        "你是 ResearchOS Reader。请只基于下面的 title、metadata 和 abstract 做摘要级简读，"
        "不要假装读过全文，不要补造实验数字、数据集或机制细节。\n\n"
        "输出必须是 Markdown，并严格包含以下 section 标题：\n"
        "## 1. Problem & Motivation\n"
        "## 2. Method Summary\n"
        "## A. 核心做法/视角\n"
        "## B. 桥接点\n"
        "## 3. Key Claimed Results\n"
        "## Raw Abstract\n"
        "## 13. Mechanism Claim\n"
        "## Source\n\n"
        "写作要求：\n"
        "- 文件开头用 `# {title}`。\n"
        "- 元数据中必须包含 `- **ID**: ...`、`- **Title**: ...` 和 `- **Status**: [ABSTRACT-ONLY]`。\n"
        "- `## 13. Mechanism Claim` 里必须写 `- **Evidence type**: abstract_claim_hint`。\n"
        "- 明确区分 abstract 声称、你的谨慎理解、以及需要全文验证的内容。\n"
        "- 如果 abstract 没有结果或机制，不要编造，写 `not available from abstract`。\n\n"
        f"Metadata:\n{metadata}\n\n"
        f"Abstract:\n{abstract}\n"
    )


def normalize_abstract_reader_note(note: str, paper: dict[str, Any]) -> str:
    """Repair shallow formatting omissions in an LLM abstract note."""

    text = str(note or "").strip()
    if not text:
        return generate_abstract_note(paper)

    title = str(paper.get("title") or "Unknown").strip()
    paper_id = _normalize_id(paper)
    metadata_lines: list[str] = []
    if not text.lstrip().startswith("# "):
        metadata_lines.append(f"# {title}")
    if "- **ID**:" not in text:
        metadata_lines.append(f"- **ID**: {paper_id}")
    if "- **Title**:" not in text:
        metadata_lines.append(f"- **Title**: {title}")
    if "- **Status**:" not in text:
        metadata_lines.append("- **Status**: [ABSTRACT-ONLY]")
    if metadata_lines:
        text = "\n".join(metadata_lines) + "\n\n" + text

    required_sections = [
        "## 1. Problem & Motivation",
        "## 2. Method Summary",
        "## A. 核心做法/视角",
        "## B. 桥接点",
        "## 3. Key Claimed Results",
        "## Raw Abstract",
        "## 13. Mechanism Claim",
        "## Source",
    ]
    for heading in required_sections:
        if heading not in text:
            if heading == "## Raw Abstract":
                text += f"\n\n{heading}\n{paper.get('abstract', '').strip() or '(no abstract available)'}"
            elif heading == "## 13. Mechanism Claim":
                text += (
                    "\n\n## 13. Mechanism Claim\n"
                    "- **Stated mechanism**: not available from abstract\n"
                    "- **Evidence type**: abstract_claim_hint\n"
                    "- **Supporting artifact**: abstract metadata only"
                )
            elif heading == "## Source":
                text += (
                    "\n\n## Source\n"
                    "- Read from: abstract / metadata only\n"
                    "- No PDF extraction performed\n"
                    "- Review status: abstract-only LLM read; verify before using for mechanism claims"
                )
            else:
                text += f"\n\n{heading}\nnot available from abstract"

    if "[ABSTRACT-ONLY]" not in text:
        text = text.replace("- **Status**:", "- **Status**: [ABSTRACT-ONLY] ", 1)
    if "abstract_claim_hint" not in text:
        text += "\n- **Evidence type**: abstract_claim_hint\n"
    if "LLM_REVIEW_REQUIRED" in text:
        text = text.replace("LLM_REVIEW_REQUIRED. ", "")
        text = text.replace("LLM_REVIEW_REQUIRED", "abstract-only review")
    return text.strip() + "\n"


def _extract_year(paper: dict) -> int | None:
    for field in ("year", "publication_year"):
        val = paper.get(field)
        if val:
            match = re.search(r"\b(19|20)\d{2}\b", str(val))
            if match:
                return int(match.group(0))
    venue = paper.get("venue", "")
    match = re.search(r"\b(19|20)\d{2}\b", str(venue))
    return int(match.group(0)) if match else None


def _split_sentences(text: str) -> list[str]:
    """按句号/分号切分 abstract。"""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in parts if s.strip()]


def _extract_problem(sentences: list[str]) -> str:
    """取前 2-3 句作为 Problem & Motivation。"""
    if not sentences:
        return "(no abstract available)"
    return " ".join(sentences[:min(3, len(sentences))])


def _extract_method(sentences: list[str]) -> str:
    """取中间句子作为 Method Summary。"""
    if len(sentences) <= 3:
        return "(method details not available from abstract alone)"
    start = min(3, len(sentences) - 1)
    end = min(start + 3, len(sentences))
    return " ".join(sentences[start:end])


def _extract_results(sentences: list[str]) -> str:
    """取最后 1-2 句作为 Key Claimed Results。"""
    if len(sentences) <= 5:
        return "(results not detailed in abstract)"
    return " ".join(sentences[-2:])


# ---------------------------------------------------------------------------
# comparison_table.csv row generation
# ---------------------------------------------------------------------------

def generate_comparison_row(paper: dict) -> str:
    """生成含 evidence_level 的 CSV 行。"""

    paper_id = _normalize_id(paper)
    title = _csv_escape(paper.get("title", ""))
    year = _extract_year(paper) or ""
    venue = _csv_escape(paper.get("venue", ""))
    method_family = ""  # abstract sweep 不分类，留给 T3.5
    dataset = ""
    key_metric = ""
    metric_value = ""
    baseline_of_ours = ""
    relevance = paper.get("relevance_score", "")

    return (
        f"{paper_id},{title},{year},{venue},{method_family},"
        f"{dataset},{key_metric},{metric_value},{baseline_of_ours},"
        f"{relevance},ABSTRACT_ONLY"
    )


def _csv_escape(value: str) -> str:
    """CSV 安全转义。"""
    value = str(value).strip()
    if "," in value or '"' in value or "\n" in value:
        return '"' + value.replace('"', '""') + '"'
    return value


# ---------------------------------------------------------------------------
# BibTeX entry generation
# ---------------------------------------------------------------------------

def generate_bib_entry(paper: dict) -> str:
    """从 paper record 生成 BibTeX 条目。"""

    paper_id = _normalize_id(paper)
    bib_key = re.sub(r"[^a-zA-Z0-9]", "", paper_id)[:40]
    title = paper.get("title", "Unknown").strip()
    year = _extract_year(paper) or "XXXX"
    venue = paper.get("venue", "").strip()
    authors = paper.get("authors", [])
    if isinstance(authors, list) and authors:
        author_str = " and ".join(str(a) for a in authors[:10])
    else:
        author_str = "Unknown"

    # 判断 entry type
    venue_lower = venue.lower()
    if any(j in venue_lower for j in ["journal", "jmlr", "tacl", "nature", "science"]):
        entry_type = "article"
    else:
        entry_type = "inproceedings"

    entry = f"""@{entry_type}{{{bib_key},
  title = {{{title}}},
  author = {{{author_str}}},
  year = {{{year}}},
"""
    if venue:
        if entry_type == "article":
            entry += f"  journal = {{{venue}}},\n"
        else:
            entry += f"  booktitle = {{{venue}}},\n"

    # 尝试加 DOI / URL
    doi = paper.get("doi", "")
    arxiv_id = paper.get("arxiv_id", "") or paper.get("id", "")
    if doi:
        entry += f"  doi = {{{doi}}},\n"
    elif arxiv_id and "arxiv" in str(arxiv_id).lower():
        entry += f"  url = {{https://arxiv.org/abs/{arxiv_id}}},\n"

    entry += "}\n"
    return entry


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_abstract_sweep(
    workspace: Path,
    config: dict[str, Any] | None = None,
) -> dict:
    """执行 deterministic fallback abstract sweep，返回统计摘要。"""

    return _run_abstract_sweep_sync(workspace, config, abstract_reader=None)


async def run_abstract_sweep_with_reader(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    abstract_reader: AbstractReader | None = None,
) -> dict:
    """执行 abstract sweep，可注入 Reader LLM callback。"""

    return await _run_abstract_sweep_async(
        workspace,
        config,
        abstract_reader=abstract_reader,
    )


def _run_abstract_sweep_sync(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    abstract_reader: None = None,
) -> dict:
    """Synchronous compatibility path used by tests/offline runs."""

    cfg = _resolve_config(config)
    if not cfg.get("enabled", False):
        return {"enabled": False, "candidates_found": 0, "notes_generated": 0}

    candidates = build_sweep_candidates(workspace, cfg)
    if not candidates:
        return {"enabled": True, "candidates_found": 0, "notes_generated": 0}

    # 确保输出目录存在
    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    abstract_dir.mkdir(parents=True, exist_ok=True)

    comparison_path = workspace / "literature" / "comparison_table.csv"
    bib_path = workspace / "literature" / "related_work.bib"

    notes_generated = 0
    rows_to_append: list[str] = []
    bib_entries: list[str] = []

    for paper in candidates:
        paper_id = _normalize_id(paper)
        if not paper_id:
            continue

        # 生成并写入 abstract note
        note = generate_abstract_note(paper)
        note_path = abstract_dir / f"{paper_id}.md"
        note_path.write_text(note, encoding="utf-8")

        # 生成 CSV 行和 BibTeX
        rows_to_append.append(generate_comparison_row(paper))
        bib_entries.append(generate_bib_entry(paper))
        notes_generated += 1

    # 追加到 comparison_table.csv
    if rows_to_append:
        _append_csv_rows(comparison_path, rows_to_append)

    # 追加到 related_work.bib
    if bib_entries:
        _append_bib_entries(bib_path, bib_entries)

    return {
        "enabled": True,
        "reader_mode": "deterministic_fallback",
        "candidates_found": len(candidates),
        "notes_generated": notes_generated,
        "llm_notes_generated": 0,
        "fallback_notes_generated": notes_generated,
        "output_dir": str(abstract_dir.relative_to(workspace)),
    }


async def _run_abstract_sweep_async(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    abstract_reader: AbstractReader | None = None,
) -> dict:
    cfg = _resolve_config(config)
    if not cfg.get("enabled", False):
        return {"enabled": False, "candidates_found": 0, "notes_generated": 0}

    candidates = build_sweep_candidates(workspace, cfg)
    if not candidates:
        return {"enabled": True, "candidates_found": 0, "notes_generated": 0}

    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    abstract_dir.mkdir(parents=True, exist_ok=True)

    comparison_path = workspace / "literature" / "comparison_table.csv"
    bib_path = workspace / "literature" / "related_work.bib"

    notes_generated = 0
    llm_notes_generated = 0
    fallback_notes_generated = 0
    reader_errors: list[dict[str, str]] = []
    rows_to_append: list[str] = []
    bib_entries: list[str] = []

    for paper in candidates:
        paper_id = _normalize_id(paper)
        if not paper_id:
            continue

        note_source = "deterministic_fallback"
        if abstract_reader is not None:
            prompt = build_abstract_reader_prompt(paper)
            try:
                note_raw = await _call_abstract_reader(abstract_reader, paper, prompt)
                note = normalize_abstract_reader_note(note_raw, paper)
                note_source = "reader_llm"
                llm_notes_generated += 1
            except Exception as exc:  # pragma: no cover - defensive fallback
                note = generate_abstract_note(paper)
                reader_errors.append({"paper_id": paper_id, "error": repr(exc)[:300]})
                fallback_notes_generated += 1
        else:
            note = generate_abstract_note(paper)
            fallback_notes_generated += 1

        note += f"\n<!-- abstract_sweep_note_source: {note_source} -->\n"
        note_path = abstract_dir / f"{paper_id}.md"
        note_path.write_text(note, encoding="utf-8")

        rows_to_append.append(generate_comparison_row(paper))
        bib_entries.append(generate_bib_entry(paper))
        notes_generated += 1

    if rows_to_append:
        _append_csv_rows(comparison_path, rows_to_append)
    if bib_entries:
        _append_bib_entries(bib_path, bib_entries)
    _append_access_audit_summary(
        workspace,
        {
            "notes_generated": notes_generated,
            "llm_notes_generated": llm_notes_generated,
            "fallback_notes_generated": fallback_notes_generated,
            "reader_errors": len(reader_errors),
        },
    )

    return {
        "enabled": True,
        "reader_mode": "reader_llm" if abstract_reader is not None else "deterministic_fallback",
        "candidates_found": len(candidates),
        "notes_generated": notes_generated,
        "llm_notes_generated": llm_notes_generated,
        "fallback_notes_generated": fallback_notes_generated,
        "reader_errors": reader_errors[:10],
        "output_dir": str(abstract_dir.relative_to(workspace)),
    }


async def _call_abstract_reader(
    abstract_reader: AbstractReader,
    paper: dict[str, Any],
    prompt: str,
) -> str:
    try:
        signature = inspect.signature(abstract_reader)
        if len(signature.parameters) <= 1:
            value = abstract_reader(paper)  # type: ignore[misc]
        else:
            value = abstract_reader(paper, prompt)
    except (TypeError, ValueError):
        value = abstract_reader(paper, prompt)
    if inspect.isawaitable(value):
        value = await value
    return str(value or "")


def _append_access_audit_summary(workspace: Path, summary: dict[str, Any]) -> None:
    path = workspace / "literature" / "access_audit.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        "\n\n## T3 Abstract Sweep\n"
        f"- abstract notes generated: {summary.get('notes_generated', 0)}\n"
        f"- Reader LLM notes: {summary.get('llm_notes_generated', 0)}\n"
        f"- deterministic fallback notes: {summary.get('fallback_notes_generated', 0)}\n"
        f"- Reader errors: {summary.get('reader_errors', 0)}\n"
        "- evidence level: ABSTRACT_ONLY / abstract_claim_hint; not full-text evidence\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _append_csv_rows(path: Path, rows: list[str]) -> None:
    """追加 CSV 行，如果文件不存在则先写 header。"""
    if not path.exists():
        header = (
            "id,title,year,venue,method_family,dataset,"
            "key_metric,metric_value,baseline_of_ours,relevance_score,evidence_level\n"
        )
        path.write_text(header, encoding="utf-8")

    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(row + "\n")


def _append_bib_entries(path: Path, entries: list[str]) -> None:
    """追加 BibTeX 条目。"""
    with path.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write("\n" + entry)
