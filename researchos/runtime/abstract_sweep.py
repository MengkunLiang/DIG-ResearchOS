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
from ..time_utils import current_utc_year
from ..literature_identity import (
    is_paper_note_file,
    paper_record_match_keys,
    paper_note_match_keys,
    record_is_covered,
)
from ..tools.paper_utils import deduplicate_papers
from ..tools.bibtex import dedupe_bibtex_entries, escape_bibtex_value, extract_bib_keys_from_text, stable_bib_key


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "enabled": False,
    "lite_paper_num": 120,
    "min_relevance": 0.0,
    "sources": ["papers_verified", "papers_dedup"],
    "exclude_already_read": True,
    "include_metadata_only": True,
    "exclude_semantic_excluded": True,
    "metadata_triage_report": "literature/metadata_triage.md",
    "priority_weights": {
        "relevance": 0.70,
        "resource": 0.20,
        "year": 0.10,
    },
    "progress": {
        "enabled": True,
        "print_every": 10,
    },
}


AbstractReader = Callable[[dict[str, Any], str], str | Awaitable[str]]
MetadataTriageReader = Callable[[list[dict[str, Any]], str], str | Awaitable[str]]


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
    """从 verified/dedup/backlog 中筛选 abstract sweep 候选。"""

    cfg = _resolve_config(config)
    lite_raw = cfg.get("lite_paper_num")
    if lite_raw in (None, "", "all", "ALL", "all_readable", "ALL_READABLE", "unlimited", "UNLIMITED"):
        lite_num: int | None = None
    else:
        lite_num = int(lite_raw)
        if lite_num <= 0:
            lite_num = None
    min_rel = float(cfg.get("min_relevance", 0.4))
    sources = cfg.get("sources", ["papers_verified", "papers_dedup"])
    exclude_read = cfg.get("exclude_already_read", True)
    include_metadata_only = bool(cfg.get("include_metadata_only", True))
    exclude_semantic_excluded = bool(cfg.get("exclude_semantic_excluded", False))
    queue_disposition = _load_queue_disposition(workspace)

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
    raw_pool: list[dict] = []
    for source_name in sources:
        path = workspace / "literature" / f"{source_name}.jsonl"
        if not path.exists():
            continue
        raw_pool.extend(load_jsonl(path))
    pool: list[dict] = []
    seen_keys: set[str] = set()
    for record in deduplicate_papers(raw_pool, doi_dedup=True, title_threshold=0.95):
        keys = _sweep_identity_keys(record)
        if not keys:
            continue
        if seen_keys & keys:
            continue
        seen_keys.update(keys)
        pool.append(record)

    # 筛选
    candidates: list[dict] = []
    for record in pool:
        rid = _normalize_id(record)
        if exclude_read and record_is_covered(record, completed_keys):
            continue
        disposition = _lookup_queue_disposition(record, queue_disposition)
        if _is_deferred_by_queue_disposition(disposition, allow_cap_exceeded_backlog=_allow_readable_backlog_refill(cfg)):
            continue
        if _is_deferred_by_queue_disposition(
            _record_disposition(record),
            allow_cap_exceeded_backlog=_allow_readable_backlog_refill(cfg),
        ):
            continue
        if _is_duplicate_record(record):
            continue
        if exclude_semantic_excluded and _is_semantic_excluded(record):
            continue
        if not str(record.get("title") or "").strip():
            continue
        relevance = _coerce_float(record.get("relevance_score"), 0.0)
        if relevance < min_rel:
            continue
        if not include_metadata_only and not record.get("abstract", "").strip():
            continue
        enriched = dict(record)
        score, components = _sweep_priority(record, cfg)
        enriched["abstract_sweep_score"] = round(score, 4)
        enriched["abstract_sweep_score_components"] = components
        candidates.append(enriched)

    candidates.sort(
        key=lambda r: (
            -float(r.get("abstract_sweep_score") or 0.0),
            -float(r.get("relevance_score") or 0.0),
            -float((r.get("abstract_sweep_score_components") or {}).get("resource", 0.0)),
            -float((r.get("abstract_sweep_score_components") or {}).get("year", 0.0)),
            str(r.get("title") or ""),
        )
    )
    return candidates if lite_num is None else candidates[:lite_num]


def _load_queue_disposition(workspace: Path) -> dict[str, dict[str, Any]]:
    """Load T3 queue disposition hints keyed by paper identity variants."""

    path = workspace / "literature" / "deep_read_queue.jsonl"
    if not path.exists():
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for record in load_jsonl(path):
        disposition = {
            "read_disposition": str(record.get("read_disposition") or ""),
            "triaged_reason": str(record.get("triaged_reason") or ""),
            "target_bucket": str(record.get("target_bucket") or ""),
            "triaged_out": bool(record.get("triaged_out")),
        }
        for key in _sweep_identity_keys(record):
            lookup.setdefault(key, disposition)
    return lookup


def _lookup_queue_disposition(
    record: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not lookup:
        return {}
    for key in _sweep_identity_keys(record):
        if key in lookup:
            return lookup[key]
    return {}


def _record_disposition(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "read_disposition": str(record.get("read_disposition") or ""),
        "triaged_reason": str(record.get("triaged_reason") or ""),
        "target_bucket": str(record.get("target_bucket") or ""),
        "triaged_out": bool(record.get("triaged_out")),
    }


def _is_deferred_by_queue_disposition(
    disposition: dict[str, Any],
    *,
    allow_cap_exceeded_backlog: bool = False,
) -> bool:
    if not disposition:
        return False
    reason = str(disposition.get("triaged_reason") or "")
    cap_exceeded = {"bridge_pool_cap_exceeded", "t2_active_pool_cap_exceeded"}
    if reason in cap_exceeded and allow_cap_exceeded_backlog:
        return False
    if reason in {*cap_exceeded, "domain_profile_filtered"}:
        return True
    read_disposition = str(disposition.get("read_disposition") or "")
    if read_disposition == "backlog" and allow_cap_exceeded_backlog:
        return False
    return read_disposition in {"deferred", "backlog"}


def _allow_readable_backlog_refill(config: dict[str, Any]) -> bool:
    policy = str(config.get("metadata_replacement_policy") or "").strip().casefold()
    if policy in {
        "replace_metadata_only_with_readable_backlog_when_available",
        "readable_backlog_refill",
        "refill",
    }:
        return True
    return bool(config.get("allow_readable_backlog_refill"))


def _sweep_priority(record: dict[str, Any], config: dict[str, Any] | None = None) -> tuple[float, dict[str, float]]:
    """Score abstract sweep candidates by relevance, resource availability, and recency."""

    weights = _priority_weights(config)
    relevance = max(0.0, min(1.0, _coerce_float(record.get("relevance_score"), 0.0)))
    resource = _resource_availability_score(record)
    year = _year_recency_score(record)
    score = weights["relevance"] * relevance + weights["resource"] * resource + weights["year"] * year
    return score, {
        "relevance": round(relevance, 4),
        "resource": round(resource, 4),
        "year": round(year, 4),
        "weight_relevance": round(weights["relevance"], 4),
        "weight_resource": round(weights["resource"], 4),
        "weight_year": round(weights["year"], 4),
    }


def _priority_weights(config: dict[str, Any] | None = None) -> dict[str, float]:
    raw = (config or {}).get("priority_weights") or _DEFAULT_CONFIG["priority_weights"]
    if not isinstance(raw, dict):
        raw = _DEFAULT_CONFIG["priority_weights"]
    relevance = max(0.0, _coerce_float(raw.get("relevance"), 0.70))
    resource = max(0.0, _coerce_float(raw.get("resource"), 0.20))
    year = max(0.0, _coerce_float(raw.get("year"), 0.10))
    total = relevance + resource + year
    if total <= 0:
        return {"relevance": 0.70, "resource": 0.20, "year": 0.10}
    return {
        "relevance": relevance / total,
        "resource": resource / total,
        "year": year / total,
    }


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resource_availability_score(record: dict[str, Any]) -> float:
    score = 0.0
    for field in ("access_score", "access_score_estimate"):
        score = max(score, _coerce_float(record.get(field), 0.0))

    hint = str(record.get("access_level_hint") or "").strip().upper()
    score = max(
        score,
        {
            "FULL_TEXT_LOCAL": 1.0,
            "LIKELY_FULL_TEXT": 0.85,
            "POSSIBLE_FULL_TEXT": 0.65,
            "ABSTRACT_OR_METADATA": 0.35,
            "METADATA_ONLY": 0.15,
        }.get(hint, 0.0),
    )
    if str(record.get("abstract") or "").strip():
        score = max(score, 0.45)
    if record.get("has_local_pdf") or record.get("has_seed_pdf"):
        score = max(score, 1.0)
    if any(record.get(key) for key in ("open_access_pdf_url", "pdf_url", "arxiv_id")):
        score = max(score, 0.85)
    return max(0.0, min(1.0, score))


def _year_recency_score(record: dict[str, Any]) -> float:
    year = _extract_year(record)
    if year is None:
        return 0.0
    current = current_utc_year()
    if year >= current - 2:
        return 1.0
    if year >= current - 5:
        return 0.8
    if year >= current - 10:
        return 0.5
    if year >= current - 20:
        return 0.25
    return 0.1


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


def _normalize_author_names(authors: Any, *, limit: int | None = None) -> list[str]:
    """Normalize heterogeneous author payloads for notes and BibTeX."""

    if not authors:
        return []
    if isinstance(authors, str):
        items: list[Any] = [authors]
    elif isinstance(authors, list):
        items = authors
    else:
        items = [authors]

    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(
                item.get("name")
                or item.get("display_name")
                or item.get("author_name")
                or item.get("full_name")
                or ""
            ).strip()
        else:
            name = str(item).strip()
        if name:
            names.append(name)
        if limit is not None and len(names) >= limit:
            break
    return names


def _sweep_identity_keys(record: dict[str, Any]) -> set[str]:
    keys = paper_record_match_keys(record)
    rid = _normalize_id(record)
    if rid:
        keys.add(rid.casefold())
    return {key for key in keys if key}


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
    author_names = _normalize_author_names(paper.get("authors", []))
    if author_names:
        author_str = ", ".join(author_names[:5])
        if len(author_names) > 5:
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
    author_text = ", ".join(_normalize_author_names(paper.get("authors", []), limit=8))
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


def build_metadata_triage_prompt(papers: list[dict[str, Any]]) -> str:
    """Build a batch LLM prompt for metadata-only candidates."""

    lines = []
    for idx, paper in enumerate(papers, 1):
        components = paper.get("abstract_sweep_score_components") or {}
        lines.append(
            "\n".join(
                [
                    f"{idx}. id={_normalize_id(paper)}",
                    f"   title={str(paper.get('title') or '').strip()}",
                    f"   year={_extract_year(paper) or 'unknown'} venue={paper.get('venue') or 'unknown'}",
                    f"   doi_or_arxiv={paper.get('doi') or paper.get('arxiv_id') or paper.get('id') or ''}",
                    f"   source={paper.get('source') or paper.get('source_bucket') or paper.get('search_bucket') or ''}",
                    f"   access_hint={paper.get('access_level_hint') or ''} access_score={paper.get('access_score') or paper.get('access_score_estimate') or ''}",
                    f"   sweep_score={paper.get('abstract_sweep_score') or ''} components={components}",
                    f"   why_relevant={paper.get('why_relevant') or ''}",
                ]
            )
        )
    return (
        "你是 ResearchOS Reader。下面是一批没有 abstract/full text 的 metadata-only 论文候选。"
        "请只基于 title/year/venue/DOI/source/access_hint 做批量 triage，不要假装读过摘要或全文，"
        "不要编造方法细节、实验结果或机制。\n\n"
        "输出 Markdown，必须包含这些标题：\n"
        "## Metadata-only Triage Summary\n"
        "## Likely Useful To Upgrade\n"
        "## Low Evidence / Defer\n"
        "## Resource Acquisition Suggestions\n"
        "## Do Not Use As Evidence\n\n"
        "每条建议要引用候选 id 或标题，并说明是基于 metadata-only 判断。"
        "如果某条只是标题相似但资源弱，请明确建议先获取 abstract/PDF 后再决定。\n\n"
        "Candidates:\n"
        + "\n\n".join(lines)
    )


def normalize_metadata_triage_report(report: str, papers: list[dict[str, Any]]) -> str:
    """Repair metadata triage report format without making evidence claims."""

    text = str(report or "").strip()
    if not text:
        text = _generate_metadata_triage_fallback(papers)
    required = [
        "## Metadata-only Triage Summary",
        "## Likely Useful To Upgrade",
        "## Low Evidence / Defer",
        "## Resource Acquisition Suggestions",
        "## Do Not Use As Evidence",
    ]
    if not text.startswith("# "):
        text = "# Metadata-only Literature Triage\n\n" + text
    for heading in required:
        if heading not in text:
            text += f"\n\n{heading}\nmetadata-only review required; no abstract/full text available."
    if "Metadata-only" not in text and "metadata-only" not in text:
        text += "\n\n> Scope: metadata-only triage; not evidence for mechanisms or claims.\n"
    return text.strip() + "\n"


def _generate_metadata_triage_fallback(papers: list[dict[str, Any]]) -> str:
    rows = []
    for paper in papers:
        rows.append(
            "- `{}` — {} ({}, {}), access={}, score={}".format(
                _normalize_id(paper),
                str(paper.get("title") or "Unknown").strip(),
                _extract_year(paper) or "unknown",
                paper.get("venue") or "unknown",
                paper.get("access_level_hint") or "unknown",
                paper.get("abstract_sweep_score") or "",
            )
        )
    return (
        "# Metadata-only Literature Triage\n\n"
        "## Metadata-only Triage Summary\n"
        "The following candidates lacked abstracts/full text during T3 abstract sweep. "
        "They are retained only as resource-acquisition or upgrade candidates.\n\n"
        "## Likely Useful To Upgrade\n"
        + ("\n".join(rows) if rows else "- No metadata-only candidates.\n")
        + "\n\n## Low Evidence / Defer\n"
        "- Defer any claim-level use until abstract or PDF evidence is acquired.\n\n"
        "## Resource Acquisition Suggestions\n"
        "- Try DOI/OpenAlex/venue/arXiv/manual lookup for high-score candidates first.\n\n"
        "## Do Not Use As Evidence\n"
        "- Do not cite these metadata-only candidates as support for mechanisms, datasets, or results.\n"
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

    text = _normalize_abstract_note_headings(text)

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
    text = _ensure_abstract_mechanism_claim_fields(text)
    if "LLM_REVIEW_REQUIRED" in text:
        text = text.replace("LLM_REVIEW_REQUIRED. ", "")
        text = text.replace("LLM_REVIEW_REQUIRED", "abstract-only review")
    return text.strip() + "\n"


def repair_existing_abstract_note(note: str, paper: dict[str, Any] | None = None) -> str:
    """Repair an already written abstract sweep note without re-calling the LLM."""

    return normalize_abstract_reader_note(note, paper or {})


def repair_abstract_sweep_notes(workspace: Path) -> dict[str, Any]:
    """Deterministically repair shallow abstract-note formatting drift.

    This is intentionally narrow: it only fills required abstract-note structure
    and §13 fields. It does not rewrite claims or upgrade evidence strength.
    """

    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    if not abstract_dir.exists():
        return {"checked": 0, "repaired": 0}

    checked = 0
    repaired = 0
    for note_path in sorted(abstract_dir.glob("*.md")):
        if note_path.name.startswith("_"):
            continue
        old_text = note_path.read_text(encoding="utf-8")
        new_text = repair_existing_abstract_note(old_text)
        checked += 1
        if new_text != old_text:
            note_path.write_text(new_text, encoding="utf-8")
            repaired += 1
    return {"checked": checked, "repaired": repaired}


def _ensure_abstract_mechanism_claim_fields(text: str) -> str:
    """Ensure §13 contains all fields required by Reader validation."""

    defaults = [
        "- **Stated mechanism**: not available from abstract",
        "- **Evidence type**: abstract_claim_hint",
        "- **Supporting artifact**: abstract metadata only",
    ]
    match = re.search(
        r"(?ms)^## 13\. Mechanism Claim\s*(?P<section>.*?)(?=^##\s+(?:\d+\.|[A-Z]\.)|^## Source\b|\Z)",
        text,
    )
    if match is None:
        return text.rstrip() + "\n\n## 13. Mechanism Claim\n" + "\n".join(defaults) + "\n"

    section = match.group("section")
    additions: list[str] = []
    if "- **Stated mechanism**:" not in section:
        additions.append(defaults[0])
    if "- **Evidence type**:" not in section:
        additions.append(defaults[1])
    elif "abstract_claim_hint" not in section and "claimed_untested" not in section:
        section = re.sub(
            r"(?m)^- \*\*Evidence type\*\*:.*$",
            defaults[1],
            section,
            count=1,
        )
    if "- **Supporting artifact**:" not in section:
        additions.append(defaults[2])
    if not additions:
        return text[:match.start("section")] + section + text[match.end("section"):]

    section = section.rstrip() + "\n" + "\n".join(additions) + "\n"
    return text[:match.start("section")] + section + text[match.end("section"):].lstrip("\n")


def _normalize_abstract_note_headings(text: str) -> str:
    """Normalize common LLM heading drift in abstract-only notes."""

    replacements = {
        r"(?m)^#{3,}\s*A\.\s*核心做法/视角\s*$": "## A. 核心做法/视角",
        r"(?m)^#{3,}\s*B\.\s*桥接点\s*$": "## B. 桥接点",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    return text


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
    title_raw = str(paper.get("title") or "").strip()
    bib_key_seed = paper.get("doi") or paper.get("arxiv_id") or paper_id or title_raw
    bib_key = stable_bib_key(bib_key_seed, fallback="abstract_note")
    title = escape_bibtex_value(title_raw or "Untitled abstract-screened record")
    year = _extract_year(paper) or "XXXX"
    venue = escape_bibtex_value(paper.get("venue", ""))
    author_names = _normalize_author_names(paper.get("authors", []), limit=10)
    if author_names:
        author_str = " and ".join(escape_bibtex_value(name) for name in author_names)
    else:
        author_str = ""

    # 判断 entry type
    venue_lower = str(paper.get("venue", "")).lower()
    if any(j in venue_lower for j in ["journal", "jmlr", "tacl", "nature", "science", "quarterly", "transactions"]):
        entry_type = "article"
    elif venue:
        entry_type = "inproceedings"
    else:
        entry_type = "misc"

    entry = f"""@{entry_type}{{{bib_key},
  title = {{{title}}},
  year = {{{year}}},
"""
    if author_str:
        entry += f"  author = {{{author_str}}},\n"
    if venue:
        if entry_type == "article":
            entry += f"  journal = {{{venue}}},\n"
        elif entry_type == "inproceedings":
            entry += f"  booktitle = {{{venue}}},\n"
        else:
            entry += f"  howpublished = {{{venue}}},\n"

    # 尝试加 DOI / URL
    doi = escape_bibtex_value(paper.get("doi", ""))
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
    metadata_triage_reader: MetadataTriageReader | None = None,
) -> dict:
    """执行 abstract sweep，可注入 Reader LLM callback。"""

    return await _run_abstract_sweep_async(
        workspace,
        config,
        abstract_reader=abstract_reader,
        metadata_triage_reader=metadata_triage_reader,
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
    metadata_only_papers: list[dict[str, Any]] = []

    for paper in candidates:
        paper_id = _normalize_id(paper)
        if not paper_id:
            continue
        if not str(paper.get("abstract") or "").strip():
            metadata_only_papers.append(paper)
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

    metadata_report_path = ""
    if metadata_only_papers:
        report_rel = str(cfg.get("metadata_triage_report") or "literature/metadata_triage.md")
        report_path = workspace / report_rel
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = normalize_metadata_triage_report("", metadata_only_papers)
        report += (
            "\n<!-- metadata_triage_source: deterministic_fallback; "
            f"candidate_count: {len(metadata_only_papers)} -->\n"
        )
        report_path.write_text(report, encoding="utf-8")
        metadata_report_path = report_rel

    _append_access_audit_summary(
        workspace,
        {
            "notes_generated": notes_generated,
            "llm_notes_generated": 0,
            "fallback_notes_generated": notes_generated,
            "metadata_triage_count": len(metadata_only_papers),
            "metadata_triage_llm": 0,
            "reader_errors": 0,
        },
    )

    return {
        "enabled": True,
        "reader_mode": "deterministic_fallback",
        "candidates_found": len(candidates),
        "notes_generated": notes_generated,
        "llm_notes_generated": 0,
        "fallback_notes_generated": notes_generated,
        "metadata_triage_count": len(metadata_only_papers),
        "metadata_triage_llm": 0,
        "metadata_triage_report": metadata_report_path,
        "output_dir": str(abstract_dir.relative_to(workspace)),
    }


async def _run_abstract_sweep_async(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    abstract_reader: AbstractReader | None = None,
    metadata_triage_reader: MetadataTriageReader | None = None,
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
    metadata_triage_count = 0
    metadata_triage_llm = 0
    reader_errors: list[dict[str, str]] = []
    rows_to_append: list[str] = []
    bib_entries: list[str] = []
    metadata_only_papers: list[dict[str, Any]] = []
    progress_cfg = cfg.get("progress") if isinstance(cfg.get("progress"), dict) else {}
    progress_enabled = bool(progress_cfg.get("enabled", True))
    try:
        progress_every = max(1, int(progress_cfg.get("print_every") or 10))
    except (TypeError, ValueError):
        progress_every = 10

    for candidate_index, paper in enumerate(candidates, start=1):
        paper_id = _normalize_id(paper)
        if not paper_id:
            continue

        note_source = "deterministic_fallback"
        has_abstract = bool(str(paper.get("abstract") or "").strip())
        if not has_abstract:
            metadata_only_papers.append(paper)
            metadata_triage_count += 1
            if progress_enabled and (candidate_index % progress_every == 0 or candidate_index == len(candidates)):
                print(
                    f"[Agent] Abstract sweep progress: {candidate_index}/{len(candidates)} candidates, "
                    f"notes={notes_generated}, metadata_only={metadata_triage_count}",
                    flush=True,
                )
            continue
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
        if progress_enabled and (candidate_index % progress_every == 0 or candidate_index == len(candidates)):
            print(
                f"[Agent] Abstract sweep progress: {candidate_index}/{len(candidates)} candidates, "
                f"notes={notes_generated}, metadata_only={metadata_triage_count}",
                flush=True,
            )

    metadata_report_path = ""
    if metadata_only_papers:
        report_rel = str(cfg.get("metadata_triage_report") or "literature/metadata_triage.md")
        report_path = workspace / report_rel
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_source = "deterministic_fallback"
        if metadata_triage_reader is not None:
            prompt = build_metadata_triage_prompt(metadata_only_papers)
            try:
                report_raw = await _call_metadata_triage_reader(metadata_triage_reader, metadata_only_papers, prompt)
                report = normalize_metadata_triage_report(report_raw, metadata_only_papers)
                report_source = "reader_llm"
                metadata_triage_llm = 1
            except Exception as exc:  # pragma: no cover - defensive fallback
                report = normalize_metadata_triage_report("", metadata_only_papers)
                reader_errors.append({"paper_id": "metadata_triage", "error": repr(exc)[:300]})
        else:
            report = normalize_metadata_triage_report("", metadata_only_papers)
        report += f"\n<!-- metadata_triage_source: {report_source}; candidate_count: {len(metadata_only_papers)} -->\n"
        report_path.write_text(report, encoding="utf-8")
        metadata_report_path = report_rel

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
            "metadata_triage_count": metadata_triage_count,
            "metadata_triage_llm": metadata_triage_llm,
            "reader_errors": len(reader_errors),
        },
    )

    return {
        "enabled": True,
        "reader_mode": _reader_mode(
            abstract_reader=abstract_reader,
            metadata_triage_reader=metadata_triage_reader,
            llm_notes_generated=llm_notes_generated,
            metadata_triage_llm=metadata_triage_llm,
        ),
        "candidates_found": len(candidates),
        "notes_generated": notes_generated,
        "llm_notes_generated": llm_notes_generated,
        "fallback_notes_generated": fallback_notes_generated,
        "metadata_triage_count": metadata_triage_count,
        "metadata_triage_llm": metadata_triage_llm,
        "metadata_triage_report": metadata_report_path,
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


async def _call_metadata_triage_reader(
    metadata_triage_reader: MetadataTriageReader,
    papers: list[dict[str, Any]],
    prompt: str,
) -> str:
    try:
        signature = inspect.signature(metadata_triage_reader)
        if len(signature.parameters) <= 1:
            value = metadata_triage_reader(papers)  # type: ignore[misc]
        else:
            value = metadata_triage_reader(papers, prompt)
    except (TypeError, ValueError):
        value = metadata_triage_reader(papers, prompt)
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
        f"- metadata-only triage candidates: {summary.get('metadata_triage_count', 0)}\n"
        f"- metadata-only triage LLM batches: {summary.get('metadata_triage_llm', 0)}\n"
        f"- Reader errors: {summary.get('reader_errors', 0)}\n"
        "- evidence level: ABSTRACT_ONLY / abstract_claim_hint; not full-text evidence\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _reader_mode(
    *,
    abstract_reader: AbstractReader | None,
    metadata_triage_reader: MetadataTriageReader | None,
    llm_notes_generated: int,
    metadata_triage_llm: int,
) -> str:
    if llm_notes_generated > 0 and metadata_triage_llm > 0:
        return "reader_llm+metadata_triage_llm"
    if llm_notes_generated > 0:
        return "reader_llm"
    if metadata_triage_llm > 0:
        return "metadata_triage_llm"
    if abstract_reader is not None or metadata_triage_reader is not None:
        return "llm_callback_no_outputs"
    return "deterministic_fallback"


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
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    existing_keys = set(extract_bib_keys_from_text(existing))
    new_entries: list[str] = []
    for entry in entries:
        keys = extract_bib_keys_from_text(entry)
        if not keys or keys[0] in existing_keys:
            continue
        existing_keys.add(keys[0])
        new_entries.append(entry.strip())
    if not new_entries:
        return
    combined = existing.rstrip() + "\n\n" + "\n\n".join(new_entries) + "\n"
    path.write_text(dedupe_bibtex_entries(combined), encoding="utf-8")
