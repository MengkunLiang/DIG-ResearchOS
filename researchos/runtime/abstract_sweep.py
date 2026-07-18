"""T3 Abstract Sweep — 摘要级轻量补读模块。

在 deep read 完成后，从 verified/dedup 池中再扫一批未被全文笔记覆盖的
论文，优先调用 Reader LLM 基于 title/abstract 生成精简 evidence note。
LLM 不可用时才退回确定性 fallback；无论哪种路径，输出都必须标为
ABSTRACT-ONLY，不能作为全文机制结论。
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import inspect
import json
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
from ..literature_resources import (
    ensure_resource_section,
    format_resource_section,
    refresh_resource_catalog,
    resource_records_from_paper_metadata,
)
from ..tools.paper_utils import deduplicate_papers
from ..tools.bibtex import dedupe_bibtex_entries, escape_bibtex_value, extract_bib_keys_from_text, stable_bib_key
from .progress import format_cli_message


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "enabled": False,
    # Default 120-paper retained pool minus the default 35 deep reads.
    "lite_paper_num": 85,
    "min_relevance": 0.0,
    "sources": ["papers_verified", "papers_dedup"],
    "exclude_already_read": True,
    "include_metadata_only": True,
    "exclude_semantic_excluded": True,
    "metadata_triage_report": "literature/metadata_triage.md",
    "manifest_path": "literature/shallow_read_manifest.json",
    "reading_upgrade_queue": "literature/reading_upgrade_queue.jsonl",
    "max_auto_full_text_pages": 100,
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

SHALLOW_READ_MANIFEST_REL_PATH = "literature/shallow_read_manifest.json"
READING_UPGRADE_QUEUE_REL_PATH = "literature/reading_upgrade_queue.jsonl"
WORKSPACE_LITERATURE_PARAMS_REL_PATH = "literature/literature_params.json"


AbstractReader = Callable[[dict[str, Any], str], str | Awaitable[str]]
AbstractBatchReader = Callable[[list[dict[str, Any]], str], Any | Awaitable[Any]]
MetadataTriageReader = Callable[[list[dict[str, Any]], str], str | Awaitable[str]]
PromptTokenCounter = Callable[[str], int]


def has_shallow_read_coverage_contract(workspace: Path) -> bool:
    """Whether this workspace explicitly adopted the T2/T3 shallow target.

    A normal current workflow writes ``literature_params.json`` at the T2
    confirmation gate. A historical workspace may instead only retain a prior
    shallow manifest. Either artifact means that downstream consumers must
    verify coverage. If neither exists, do not retrospectively impose the
    current global default on a legacy T3 resume that never selected a target.
    """

    literature_dir = workspace / "literature"
    return (
        (literature_dir / "literature_params.json").is_file()
        or (literature_dir / "shallow_read_manifest.json").is_file()
    )

ABSTRACT_CORE_HEADING = "## A. Core Approach / Perspective"
ABSTRACT_BRIDGE_HEADING = "## B. Bridge Point"
LEGACY_ABSTRACT_CORE_HEADING = "## A. 核心做法/视角"
LEGACY_ABSTRACT_BRIDGE_HEADING = "## B. 桥接点"


def _resolve_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    if raw:
        cfg.update(raw)
    return cfg


def _cap_shallow_target_to_distinct_reading_pool(
    workspace: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Keep deep and shallow reading within the confirmed distinct-paper pool.

    A confirmed T2/T3 plan defines ``active_pool_max`` as the number of
    different papers that must receive a reading record.  Protected seed,
    bridge, or citation-hub records can make the actual deep-read queue larger
    than its ordinary target.  In that case, the shallow target must shrink by
    the same amount.  It must not silently pull extra papers from backlog and
    turn, for example, a 20-paper plan into a 35-paper plan.
    """

    raw_target = config.get("lite_paper_num")
    if raw_target in (None, "", "all", "ALL", "all_readable", "ALL_READABLE", "unlimited", "UNLIMITED"):
        return config
    try:
        requested_target = max(0, int(raw_target))
    except (TypeError, ValueError):
        return config

    params_path = workspace / WORKSPACE_LITERATURE_PARAMS_REL_PATH
    queue_path = workspace / "literature" / "deep_read_queue.jsonl"
    if not params_path.is_file() or not queue_path.is_file():
        return config
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return config
    if not isinstance(params, dict):
        return config
    try:
        active_pool = int(((params.get("t2_finalize") or {}).get("active_pool_max")))
    except (AttributeError, TypeError, ValueError):
        return config
    if active_pool < 1:
        return config

    deep_count = sum(
        1
        for record in load_jsonl(queue_path)
        if str(record.get("read_disposition") or "").strip() == "deep_read"
        and record.get("triaged_out") is not True
    )
    remaining_shallow_capacity = max(0, active_pool - deep_count)
    if requested_target <= remaining_shallow_capacity:
        return config

    adjusted = dict(config)
    adjusted["configured_lite_paper_num"] = requested_target
    adjusted["lite_paper_num"] = remaining_shallow_capacity
    adjusted["distinct_pool_adjustment"] = {
        "active_pool_max": active_pool,
        "actual_deep_read_count": deep_count,
        "effective_shallow_target": remaining_shallow_capacity,
        "reason": "actual_deep_read_count_exceeds_the_planned_deep_read_allocation",
    }
    return adjusted


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def build_sweep_candidates(
    workspace: Path,
    config: dict[str, Any] | None = None,
) -> list[dict]:
    """从 verified/dedup/backlog 中筛选 abstract sweep 候选。"""

    cfg = _cap_shallow_target_to_distinct_reading_pool(workspace, _resolve_config(config))
    lite_raw = cfg.get("lite_paper_num")
    if lite_raw in (None, "", "all", "ALL", "all_readable", "ALL_READABLE", "unlimited", "UNLIMITED"):
        lite_num: int | None = None
    else:
        lite_num = int(lite_raw)
        if lite_num <= 0:
            return []
    min_rel = float(cfg.get("min_relevance", 0.4))
    sources = _normalize_sources(cfg.get("sources", ["papers_verified", "papers_dedup"]))
    primary_sources = [source for source in sources if not _is_backlog_source(source)]
    backlog_sources = [source for source in sources if _is_backlog_source(source)]
    if not primary_sources:
        primary_sources = sources
        backlog_sources = []
    exclude_read = cfg.get("exclude_already_read", True)
    include_metadata_only = bool(cfg.get("include_metadata_only", True))
    exclude_semantic_excluded = bool(cfg.get("exclude_semantic_excluded", False))
    queue_disposition = _load_queue_disposition(workspace)

    completed_keys: set[str] = set()
    if exclude_read:
        notes_dir = workspace / "literature" / "deep_read_notes"
        if notes_dir.exists():
            for note_path in notes_dir.glob("*.md"):
                if is_paper_note_file(note_path):
                    completed_keys.update(paper_note_match_keys(note_path))
        bridge_notes_dir = workspace / "literature" / "bridge_notes"
        if bridge_notes_dir.exists():
            for note_path in bridge_notes_dir.glob("**/*.md"):
                if is_paper_note_file(note_path):
                    completed_keys.update(paper_note_match_keys(note_path))

    # 已有 abstract note 的 paper ID（避免重复 sweep）
    existing_abstract_note_count = 0
    abstract_dir = workspace / "literature" / "shallow_read_notes"
    if abstract_dir.exists():
        for note_path in abstract_dir.glob("*.md"):
            if _is_abstract_note_card(note_path):
                existing_abstract_note_count += 1
                completed_keys.update(paper_note_match_keys(note_path))

    remaining_lite_slots = lite_num
    if lite_num is not None:
        remaining_lite_slots = max(0, lite_num - existing_abstract_note_count)
        if remaining_lite_slots <= 0:
            return []

    seen_keys: set[str] = set()
    primary_candidates = _filter_sweep_pool(
        workspace,
        primary_sources,
        cfg,
        completed_keys=completed_keys,
        queue_disposition=queue_disposition,
        seen_keys=seen_keys,
        exclude_read=exclude_read,
        include_metadata_only=include_metadata_only,
        exclude_semantic_excluded=exclude_semantic_excluded,
        min_rel=min_rel,
        allow_cap_exceeded_backlog=False,
        require_abstract=False,
    )
    _sort_sweep_candidates(primary_candidates)

    # `all_readable` means all eligible records in the retained active pool. Backlog
    # is only a bounded refill source for numeric targets, never an unbounded sweep.
    if remaining_lite_slots is None:
        return primary_candidates

    # ``lite_paper_num`` is an actual ABSTRACT-ONLY reading target, not a cap
    # shared with metadata triage.  The previous implementation selected a
    # mixed list first, so metadata-only records silently consumed the slots
    # that the researcher had asked to read.  Retain metadata triage as a
    # separate, non-evidence output, but fill reading coverage with records
    # that really have an abstract (or a future text-reading upgrade).
    readable_primary = [item for item in primary_candidates if _has_abstract(item)]
    metadata_primary = [item for item in primary_candidates if not _has_abstract(item)]
    selected = list(readable_primary[:remaining_lite_slots])
    refill_slots = remaining_lite_slots - len(selected)
    if refill_slots > 0 and _allow_readable_backlog_refill(cfg):
        if not backlog_sources:
            # A numeric coverage promise plus the explicit replacement policy
            # authorizes a bounded readable refill.  Requiring users to repeat
            # ``papers_backlog`` in ``sources`` made the policy a no-op.
            backlog_sources = ["papers_backlog"]
        backlog_candidates = _filter_sweep_pool(
            workspace,
            backlog_sources,
            cfg,
            completed_keys=completed_keys,
            queue_disposition=queue_disposition,
            seen_keys=seen_keys,
            exclude_read=exclude_read,
            include_metadata_only=False,
            exclude_semantic_excluded=exclude_semantic_excluded,
            min_rel=min_rel,
            allow_cap_exceeded_backlog=True,
            require_abstract=True,
        )
        _sort_sweep_candidates(backlog_candidates)
        selected.extend(backlog_candidates[:refill_slots])

    # Metadata-only records remain useful for acquisition triage, but never
    # reduce the requested number of readable shallow notes.  They only come
    # from the declared retained sources, never from an unbounded backlog.
    if include_metadata_only:
        selected.extend(metadata_primary)
    return selected


def _normalize_sources(raw: Any) -> list[str]:
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        items = ["papers_verified", "papers_dedup"]
    sources: list[str] = []
    for item in items:
        source = str(item or "").strip()
        if source and source not in sources:
            sources.append(source)
    return sources or ["papers_verified", "papers_dedup"]


def _is_backlog_source(source_name: str) -> bool:
    return source_name.strip() == "papers_backlog"


def _filter_sweep_pool(
    workspace: Path,
    sources: list[str],
    config: dict[str, Any],
    *,
    completed_keys: set[str],
    queue_disposition: dict[str, dict[str, Any]],
    seen_keys: set[str],
    exclude_read: bool,
    include_metadata_only: bool,
    exclude_semantic_excluded: bool,
    min_rel: float,
    allow_cap_exceeded_backlog: bool,
    require_abstract: bool,
) -> list[dict[str, Any]]:
    raw_pool: list[dict[str, Any]] = []
    for source_name in sources:
        path = workspace / "literature" / f"{source_name}.jsonl"
        if not path.exists():
            continue
        raw_pool.extend(load_jsonl(path))

    candidates: list[dict[str, Any]] = []
    for record in deduplicate_papers(raw_pool, doi_dedup=True, title_threshold=0.95):
        keys = _sweep_identity_keys(record)
        if not keys:
            continue
        if seen_keys & keys:
            continue
        if exclude_read and record_is_covered(record, completed_keys):
            seen_keys.update(keys)
            continue
        disposition = _lookup_queue_disposition(record, queue_disposition)
        if _is_deferred_by_queue_disposition(
            disposition,
            allow_cap_exceeded_backlog=allow_cap_exceeded_backlog,
        ):
            seen_keys.update(keys)
            continue
        if _is_pending_deep_read_disposition(disposition):
            seen_keys.update(keys)
            continue
        if _is_deferred_by_queue_disposition(
            _record_disposition(record),
            allow_cap_exceeded_backlog=allow_cap_exceeded_backlog,
        ):
            seen_keys.update(keys)
            continue
        if _is_duplicate_record(record):
            seen_keys.update(keys)
            continue
        if exclude_semantic_excluded and _is_semantic_excluded(record):
            seen_keys.update(keys)
            continue
        if not str(record.get("title") or "").strip():
            seen_keys.update(keys)
            continue
        has_abstract = bool(str(record.get("abstract") or "").strip())
        if require_abstract and not has_abstract:
            seen_keys.update(keys)
            continue
        relevance = _coerce_float(record.get("relevance_score"), 0.0)
        if relevance < min_rel:
            seen_keys.update(keys)
            continue
        if not include_metadata_only and not has_abstract:
            seen_keys.update(keys)
            continue
        enriched = dict(record)
        score, components = _sweep_priority(record, config)
        enriched["abstract_sweep_score"] = round(score, 4)
        enriched["abstract_sweep_score_components"] = components
        candidates.append(enriched)
        seen_keys.update(keys)
    return candidates


def _is_pending_deep_read_disposition(disposition: dict[str, Any]) -> bool:
    if not disposition:
        return False
    if str(disposition.get("read_disposition") or "").strip() != "deep_read":
        return False
    if disposition.get("triaged_out") is True:
        return False
    return True


def _sort_sweep_candidates(candidates: list[dict[str, Any]]) -> None:
    candidates.sort(
        key=lambda r: (
            -float(r.get("abstract_sweep_score") or 0.0),
            -float(r.get("relevance_score") or 0.0),
            -float((r.get("abstract_sweep_score_components") or {}).get("resource", 0.0)),
            -float((r.get("abstract_sweep_score_components") or {}).get("year", 0.0)),
            str(r.get("title") or ""),
        )
    )


def _abstract_sweep_plan_summary(workspace: Path, config: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    target = config.get("lite_paper_num")
    existing_notes = _existing_abstract_note_count(workspace)
    if target in (None, "", "all", "ALL", "all_readable", "ALL_READABLE", "unlimited", "UNLIMITED"):
        target_total: int | str | None = "all_readable"
        remaining_target: int | None = None
    else:
        try:
            target_total = max(0, int(target))
        except (TypeError, ValueError):
            target_total = target
        remaining_target = max(0, int(target_total) - existing_notes) if isinstance(target_total, int) else None
    queue_disposition = _load_queue_disposition(workspace)
    queue_counts: dict[str, int] = {}
    source_roles: dict[str, int] = {}
    for candidate in candidates:
        disposition = _lookup_queue_disposition(candidate, queue_disposition)
        read_disposition = str(disposition.get("read_disposition") or candidate.get("read_disposition") or "unknown")
        queue_counts[read_disposition] = queue_counts.get(read_disposition, 0) + 1
        role = str(candidate.get("t2_pool_role") or "unknown")
        source_roles[role] = source_roles.get(role, 0) + 1
    summary = {
        "target_total": target_total,
        "existing_shallow_read_notes": existing_notes,
        "remaining_target": remaining_target,
        "selected_for_this_run": len(candidates),
        "selected_readable_for_this_run": sum(1 for candidate in candidates if _has_abstract(candidate)),
        "selected_metadata_triage_for_this_run": sum(1 for candidate in candidates if not _has_abstract(candidate)),
        "candidate_queue_dispositions": queue_counts,
        "candidate_source_roles": source_roles,
    }
    adjustment = config.get("distinct_pool_adjustment")
    if isinstance(adjustment, dict):
        summary["distinct_pool_adjustment"] = adjustment
        summary["configured_target_total"] = config.get("configured_lite_paper_num")
    return summary


def _has_abstract(record: dict[str, Any]) -> bool:
    return bool(str(record.get("abstract") or "").strip())


def _existing_abstract_note_count(workspace: Path) -> int:
    abstract_dir = workspace / "literature" / "shallow_read_notes"
    if not abstract_dir.exists():
        return 0
    return sum(1 for note_path in abstract_dir.glob("*.md") if _is_abstract_note_card(note_path))


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

{ABSTRACT_CORE_HEADING}
LLM_REVIEW_REQUIRED. Abstract-only hint about the method, lens, or viewpoint:
{middle_hint}

{ABSTRACT_BRIDGE_HEADING}
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

{format_resource_section(resource_records_from_paper_metadata(paper))}

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
        f"{ABSTRACT_CORE_HEADING}\n"
        f"{ABSTRACT_BRIDGE_HEADING}\n"
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


def build_abstract_batch_reader_prompt(papers: list[dict[str, Any]]) -> str:
    """Build one structured prompt for a provider-context-sized abstract batch.

    The records retain separate ids and notes so batching only saves provider calls;
    it never combines the evidence status of separate papers.
    """

    records = []
    for paper in papers:
        records.append(
            {
                "id": _normalize_id(paper),
                "title": str(paper.get("title") or "Unknown").strip(),
                "authors": ", ".join(_normalize_author_names(paper.get("authors", []), limit=8)) or "Unknown",
                "year": _extract_year(paper),
                "venue": str(paper.get("venue") or "").strip(),
                "doi_or_arxiv": str(paper.get("doi") or paper.get("arxiv_id") or paper.get("id") or "").strip(),
                "why_relevant": str(paper.get("why_relevant") or "").strip(),
                "abstract": str(paper.get("abstract") or "").strip(),
            }
        )
    return (
        "You are ResearchOS Reader. Produce separate cautious ABSTRACT-ONLY paper notes for the records below. "
        "You have not read full text. Do not invent datasets, metrics, baseline details, numerical results, causal mechanisms, or citations. "
        "For every record return a complete Markdown note with its own id/title and these exact headings: "
        "`## 1. Problem & Motivation`, `## 2. Method Summary`, `## A. Core Approach / Perspective`, "
        "`## B. Bridge Point`, `## 3. Key Claimed Results`, `## Raw Abstract`, `## 13. Mechanism Claim`, and `## Source`. "
        "The mechanism section must contain `- **Evidence type**: abstract_claim_hint`. "
        "When unavailable from the abstract, write `not available from abstract`.\n\n"
        "Return JSON only, using this schema: "
        '{"notes":[{"paper_id":"exact input id","note_markdown":"complete Markdown note"}]}. '
        "Do not omit a record; return an empty note_markdown only when the input abstract is missing.\n\n"
        "Records:\n"
        + json.dumps(records, ensure_ascii=False)
    )


def plan_abstract_reader_batches(
    papers: list[dict[str, Any]],
    *,
    provider_context_window: int | None,
    prompt_token_counter: PromptTokenCounter | None = None,
) -> list[list[dict[str, Any]]]:
    """Pack abstracts against the active provider's declared context window.

    This intentionally has no fixed paper-count knob.  The model binding supplies
    the window; ResearchOS only reserves enough room for structured per-paper notes
    and uses the same binding's token counter when the caller provides one.
    """

    if not papers:
        return []
    if provider_context_window is None or provider_context_window <= 0:
        return [[paper] for paper in papers]

    def count(text: str) -> int:
        if prompt_token_counter is not None:
            try:
                value = int(prompt_token_counter(text))
                if value > 0:
                    return value
            except Exception:
                pass
        # Only used outside a live provider tokenizer, such as unit tests.
        return max(1, len(text) // 4)

    # The provider's window is authoritative.  Reserve response room based on
    # each source abstract rather than an arbitrary record cap; the remaining
    # slack protects JSON/Markdown serialization overhead.
    fixed_prompt_tokens = count(build_abstract_batch_reader_prompt([]))
    safety_reserve = max(512, int(provider_context_window * 0.08))
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_cost = fixed_prompt_tokens + safety_reserve
    for paper in papers:
        abstract_tokens = count(str(paper.get("abstract") or ""))
        expected_note_tokens = max(280, min(1200, int(abstract_tokens * 1.25) + 180))
        candidate_cost = count(json.dumps(
            {
                "id": _normalize_id(paper),
                "title": str(paper.get("title") or ""),
                "abstract": str(paper.get("abstract") or ""),
            },
            ensure_ascii=False,
        )) + expected_note_tokens
        if current and current_cost + candidate_cost > provider_context_window:
            batches.append(current)
            current = []
            current_cost = fixed_prompt_tokens + safety_reserve
        current.append(paper)
        current_cost += candidate_cost
    if current:
        batches.append(current)
    return batches


def parse_abstract_batch_reader_output(value: Any, papers: list[dict[str, Any]]) -> dict[str, str]:
    """Return a safe id->note mapping from a batch reader response."""

    allowed_ids = {_normalize_id(paper) for paper in papers if _normalize_id(paper)}
    payload: Any = value
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            payload = None
            for position, char in enumerate(text):
                if char != "{":
                    continue
                try:
                    payload, _ = decoder.raw_decode(text[position:])
                    break
                except json.JSONDecodeError:
                    continue
    if isinstance(payload, dict):
        entries = payload.get("notes") or payload.get("records") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []
    notes: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        paper_id = str(entry.get("paper_id") or entry.get("id") or "").strip()
        note = str(entry.get("note_markdown") or entry.get("note") or entry.get("markdown") or "").strip()
        if paper_id in allowed_ids and note:
            notes[paper_id] = note
    return notes


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
        ABSTRACT_CORE_HEADING,
        ABSTRACT_BRIDGE_HEADING,
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
    return ensure_resource_section(text, resource_records_from_paper_metadata(paper))


def repair_existing_abstract_note(note: str, paper: dict[str, Any] | None = None) -> str:
    """Repair an already written abstract sweep note without re-calling the LLM."""

    return normalize_abstract_reader_note(note, paper or {})


def repair_abstract_sweep_notes(workspace: Path) -> dict[str, Any]:
    """Deterministically repair shallow abstract-note formatting drift.

    This is intentionally narrow: it only fills required abstract-note structure
    and §13 fields. It does not rewrite claims or upgrade evidence strength.
    """

    abstract_dir = workspace / "literature" / "shallow_read_notes"
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
        r"(?m)^#{2,}\s*A\.\s*核心做法/视角\s*$": ABSTRACT_CORE_HEADING,
        r"(?m)^#{2,}\s*B\.\s*桥接点\s*$": ABSTRACT_BRIDGE_HEADING,
        r"(?m)^#{3,}\s*A\.\s*Core\s+Approach\s*/\s*Perspective\s*$": ABSTRACT_CORE_HEADING,
        r"(?m)^#{3,}\s*B\.\s*Bridge\s+Point\s*$": ABSTRACT_BRIDGE_HEADING,
        r"(?m)^#{2,}\s*A\.\s*Core\s+Approach\s*/\s*Viewpoint\s*$": ABSTRACT_CORE_HEADING,
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


_JOURNAL_VENUE_MARKERS = (
    "journal",
    "jmlr",
    "tacl",
    "nature",
    "science",
    "quarterly",
    "transactions",
    "information systems research",
    "management science",
    "marketing science",
    "organization science",
    "operations research",
    "production and operations management",
    "manufacturing & service operations management",
    "mis quarterly",
)


def _is_journal_record(paper: dict, venue_lower: str) -> bool:
    """Prefer explicit metadata, then recognize durable journal venue names.

    Crossref/INFORMS records frequently expose a journal title without the word
    ``journal``. Treating every non-empty venue as a conference produced an
    invalid ``@inproceedings`` entry for Information Systems Research and made
    downstream Survey validation fail after otherwise successful reading.
    """

    type_values = " ".join(
        str(paper.get(key) or "")
        for key in ("publication_type", "paper_type", "source_type", "type", "work_type")
    ).casefold()
    if any(marker in type_values for marker in ("journal-article", "journal article", "journal")):
        return True
    return any(marker in venue_lower for marker in _JOURNAL_VENUE_MARKERS)


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
    if _is_journal_record(paper, venue_lower):
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
    abstract_batch_reader: AbstractBatchReader | None = None,
    metadata_triage_reader: MetadataTriageReader | None = None,
    provider_context_window: int | None = None,
    prompt_token_counter: PromptTokenCounter | None = None,
) -> dict:
    """执行 abstract sweep，可注入 Reader LLM callback。"""

    return await _run_abstract_sweep_async(
        workspace,
        config,
        abstract_reader=abstract_reader,
        abstract_batch_reader=abstract_batch_reader,
        metadata_triage_reader=metadata_triage_reader,
        provider_context_window=provider_context_window,
        prompt_token_counter=prompt_token_counter,
    )


def _run_abstract_sweep_sync(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    abstract_reader: None = None,
) -> dict:
    """Synchronous compatibility path used by tests/offline runs."""

    cfg = _cap_shallow_target_to_distinct_reading_pool(workspace, _resolve_config(config))
    if not cfg.get("enabled", False):
        return {"enabled": False, "candidates_found": 0, "notes_generated": 0}

    candidates = build_sweep_candidates(workspace, cfg)
    if not candidates:
        plan_summary = _abstract_sweep_plan_summary(workspace, cfg, [])
        return _finalize_sweep_result(
            workspace,
            cfg,
            {
                "enabled": True,
                "reader_mode": "deterministic_fallback",
                "candidates_found": 0,
                "notes_generated": 0,
                "llm_notes_generated": 0,
                "fallback_notes_generated": 0,
                "metadata_triage_count": 0,
                "metadata_triage_llm": 0,
                "sweep_plan": plan_summary,
            },
            candidates=[],
        )
    plan_summary = _abstract_sweep_plan_summary(workspace, cfg, candidates)

    # 确保输出目录存在
    abstract_dir = workspace / "literature" / "shallow_read_notes"
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

    return _finalize_sweep_result(
        workspace,
        cfg,
        {
            "enabled": True,
            "reader_mode": "deterministic_fallback",
            "candidates_found": len(candidates),
            "sweep_plan": plan_summary,
            "notes_generated": notes_generated,
            "llm_notes_generated": 0,
            "fallback_notes_generated": notes_generated,
            "metadata_triage_count": len(metadata_only_papers),
            "metadata_triage_llm": 0,
            "metadata_triage_report": metadata_report_path,
            "output_dir": str(abstract_dir.relative_to(workspace)),
        },
        candidates=candidates,
    )


async def _run_abstract_sweep_async(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    abstract_reader: AbstractReader | None = None,
    abstract_batch_reader: AbstractBatchReader | None = None,
    metadata_triage_reader: MetadataTriageReader | None = None,
    provider_context_window: int | None = None,
    prompt_token_counter: PromptTokenCounter | None = None,
) -> dict:
    cfg = _cap_shallow_target_to_distinct_reading_pool(workspace, _resolve_config(config))
    if not cfg.get("enabled", False):
        return {"enabled": False, "candidates_found": 0, "notes_generated": 0}

    candidates = build_sweep_candidates(workspace, cfg)
    if not candidates:
        plan_summary = _abstract_sweep_plan_summary(workspace, cfg, [])
        return _finalize_sweep_result(
            workspace,
            cfg,
            {
                "enabled": True,
                "reader_mode": "llm_callback_no_outputs" if abstract_reader is not None else "deterministic_fallback",
                "candidates_found": 0,
                "notes_generated": 0,
                "llm_notes_generated": 0,
                "fallback_notes_generated": 0,
                "metadata_triage_count": 0,
                "metadata_triage_llm": 0,
                "sweep_plan": plan_summary,
            },
            candidates=[],
        )
    plan_summary = _abstract_sweep_plan_summary(workspace, cfg, candidates)

    abstract_dir = workspace / "literature" / "shallow_read_notes"
    abstract_dir.mkdir(parents=True, exist_ok=True)

    comparison_path = workspace / "literature" / "comparison_table.csv"
    bib_path = workspace / "literature" / "related_work.bib"

    notes_generated = 0
    llm_notes_generated = 0
    fallback_notes_generated = 0
    metadata_triage_count = 0
    metadata_triage_llm = 0
    reader_errors: list[dict[str, str]] = []
    llm_batch_calls = 0
    batch_fallback_count = 0
    rows_to_append: list[str] = []
    bib_entries: list[str] = []
    metadata_only_papers: list[dict[str, Any]] = []
    progress_cfg = cfg.get("progress") if isinstance(cfg.get("progress"), dict) else {}
    progress_enabled = bool(progress_cfg.get("enabled", True))
    try:
        progress_every = max(1, int(progress_cfg.get("print_every") or 10))
    except (TypeError, ValueError):
        progress_every = 10
    if progress_enabled:
        target_text = plan_summary.get("target_total")
        if isinstance(target_text, int):
            target_text = f"累计目标 {target_text}，已有 {plan_summary.get('existing_shallow_read_notes', 0)}，本轮剩余 {len(candidates)}"
        else:
            target_text = f"目标 {target_text}，本轮 retained/shallow 候选 {len(candidates)}"
        print(
            format_cli_message(
                "[Agent] Abstract sweep plan: "
                f"{target_text}; queue={plan_summary.get('candidate_queue_dispositions')}; "
                f"sources={plan_summary.get('candidate_source_roles')}"
            ),
            flush=True,
        )

    readable_papers: list[dict[str, Any]] = []
    for paper in candidates:
        if not _normalize_id(paper):
            continue
        if str(paper.get("abstract") or "").strip():
            readable_papers.append(paper)
        else:
            metadata_only_papers.append(paper)
            metadata_triage_count += 1

    batch_plan: list[list[dict[str, Any]]] = []
    if abstract_batch_reader is not None and len(readable_papers) > 1:
        batch_plan = plan_abstract_reader_batches(
            readable_papers,
            provider_context_window=provider_context_window,
            prompt_token_counter=prompt_token_counter,
        )
    if not batch_plan:
        batch_plan = [[paper] for paper in readable_papers]

    if progress_enabled and readable_papers:
        print(
            format_cli_message(
                "[Reader Agent] Abstract sweep batching: "
                f"provider_context={provider_context_window or 'unavailable'}; "
                f"papers={len(readable_papers)}; batches={len(batch_plan)}; "
                "batch size is derived from provider context, not a fixed paper cap."
            ),
            flush=True,
        )

    processed_readable = 0
    for batch_index, batch in enumerate(batch_plan, start=1):
        batch_notes: dict[str, str] = {}
        used_batch_reader = abstract_batch_reader is not None and len(batch) > 1
        if used_batch_reader:
            try:
                raw = await _call_abstract_batch_reader(
                    abstract_batch_reader,
                    batch,
                    build_abstract_batch_reader_prompt(batch),
                )
                batch_notes = parse_abstract_batch_reader_output(raw, batch)
                llm_batch_calls += 1
            except Exception as exc:  # pragma: no cover - defensive fallback
                reader_errors.append({"paper_id": "batch:" + str(batch_index), "error": repr(exc)[:300]})

        for paper in batch:
            paper_id = _normalize_id(paper)
            note_source = "deterministic_fallback"
            note_raw = batch_notes.get(paper_id, "")
            if note_raw:
                note = normalize_abstract_reader_note(note_raw, paper)
                note_source = "reader_llm_batch"
                llm_notes_generated += 1
            elif abstract_reader is not None:
                try:
                    note_raw = await _call_abstract_reader(abstract_reader, paper, build_abstract_reader_prompt(paper))
                    note = normalize_abstract_reader_note(note_raw, paper)
                    note_source = "reader_llm"
                    llm_notes_generated += 1
                    if used_batch_reader:
                        batch_fallback_count += 1
                except Exception as exc:  # pragma: no cover - defensive fallback
                    note = generate_abstract_note(paper)
                    reader_errors.append({"paper_id": paper_id, "error": repr(exc)[:300]})
                    fallback_notes_generated += 1
            else:
                note = generate_abstract_note(paper)
                fallback_notes_generated += 1

            note += f"\n<!-- abstract_sweep_note_source: {note_source} -->\n"
            (abstract_dir / f"{paper_id}.md").write_text(note, encoding="utf-8")
            rows_to_append.append(generate_comparison_row(paper))
            bib_entries.append(generate_bib_entry(paper))
            notes_generated += 1
            processed_readable += 1
        if progress_enabled:
            print(
                format_cli_message(
                    "[Reader Agent] Abstract sweep batch progress: "
                    f"batch {batch_index}/{len(batch_plan)}; papers={processed_readable}/{len(readable_papers)}; "
                    f"notes={notes_generated}; metadata_only={metadata_triage_count}; "
                    f"batch_fallbacks={batch_fallback_count}"
                ),
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
            "llm_batch_calls": llm_batch_calls,
            "batch_fallback_count": batch_fallback_count,
        },
    )

    return _finalize_sweep_result(
        workspace,
        cfg,
        {
            "enabled": True,
            "reader_mode": _reader_mode(
                abstract_reader=abstract_reader,
                metadata_triage_reader=metadata_triage_reader,
                llm_notes_generated=llm_notes_generated,
                metadata_triage_llm=metadata_triage_llm,
                llm_batch_calls=llm_batch_calls,
            ),
            "candidates_found": len(candidates),
            "sweep_plan": plan_summary,
            "notes_generated": notes_generated,
            "llm_notes_generated": llm_notes_generated,
            "fallback_notes_generated": fallback_notes_generated,
            "metadata_triage_count": metadata_triage_count,
            "metadata_triage_llm": metadata_triage_llm,
            "metadata_triage_report": metadata_report_path,
            "reader_errors": reader_errors[:10],
            "llm_batch_calls": llm_batch_calls,
            "batch_fallback_count": batch_fallback_count,
            "batching": {
                "provider_context_window": provider_context_window,
                "batch_count": len(batch_plan),
                "readable_paper_count": len(readable_papers),
                "mode": "provider_context_adaptive" if abstract_batch_reader is not None else "per_paper_reader",
            },
            "output_dir": str(abstract_dir.relative_to(workspace)),
        },
        candidates=candidates,
    )


def validate_abstract_sweep_coverage(
    workspace: Path,
    config: dict[str, Any] | None = None,
    *,
    require_manifest: bool = True,
) -> tuple[bool, str | None]:
    """Validate the durable shallow-reading contract independently of T3 LLM state.

    Metadata triage is deliberately excluded.  It is a resource-acquisition
    aid, not an abstract reading event and therefore cannot satisfy a numeric
    ``lite_paper_num`` selected by the researcher.
    """

    cfg = _cap_shallow_target_to_distinct_reading_pool(workspace, _resolve_config(config))
    if not cfg.get("enabled", False):
        return True, None
    target = _numeric_lite_target(cfg)
    manifest_rel = _manifest_rel_path(cfg)
    manifest_path = workspace / manifest_rel
    if not manifest_path.is_file():
        if require_manifest:
            return False, f"缺少 {manifest_rel}；摘要轻读覆盖尚未生成，不能确认 T3 完成。"
        return True, None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"{manifest_rel} 不可读取：{type(exc).__name__}。"
    if not isinstance(manifest, dict):
        return False, f"{manifest_rel} 必须是 JSON 对象。"

    note_entries = manifest.get("shallow_note_entries")
    if not isinstance(note_entries, list):
        return False, f"{manifest_rel} 缺少 shallow_note_entries，无法验证实际浅读文件。"
    invalid_paths: list[str] = []
    valid_count = 0
    for item in note_entries:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("path") or "").strip()
        if not rel_path:
            continue
        path = workspace / rel_path
        if _is_abstract_note_card(path):
            valid_count += 1
        else:
            invalid_paths.append(rel_path)
    if invalid_paths:
        return False, (
            f"摘要轻读 Manifest 引用了不可读或非 ABSTRACT-ONLY 的文件："
            + ", ".join(invalid_paths[:5])
        )

    if target is not None and valid_count < target:
        metadata_count = int(manifest.get("metadata_triage_count") or 0)
        return False, (
            f"浅读覆盖仅完成 {valid_count}/{target}；metadata-only triage {metadata_count} 篇不计入阅读覆盖。"
            "系统会从可读 backlog 补位；若仍不足，必须定向补检后再进入 T3.5。"
        )
    status = str(manifest.get("status") or "").strip().casefold()
    if status == "blocked":
        return False, str(manifest.get("blocking_reason") or "摘要轻读覆盖未完成。")
    return True, None


def _finalize_sweep_result(
    workspace: Path,
    config: dict[str, Any],
    result: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist the exact shallow-reading outcome before downstream validation."""

    manifest = _write_shallow_read_manifest(workspace, config, result, candidates)
    upgrade_queue = _write_reading_upgrade_queue(workspace, config)
    resource_catalog = refresh_resource_catalog(workspace)
    result.update(
        {
            "manifest_path": _manifest_rel_path(config),
            "shallow_read_note_count": int(manifest.get("actual_shallow_read_count") or 0),
            "shallow_read_target": manifest.get("target"),
            "unfulfilled_target": int(manifest.get("unfulfilled_target") or 0),
            "status": str(manifest.get("status") or "unknown"),
            "blocking_reason": str(manifest.get("blocking_reason") or ""),
            "reading_upgrade_queue": upgrade_queue.get("path", ""),
            "reading_upgrade_candidate_count": int(upgrade_queue.get("candidate_count") or 0),
            "resource_catalog": resource_catalog,
        }
    )
    return result


def _write_shallow_read_manifest(
    workspace: Path,
    config: dict[str, Any],
    result: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    entries = _current_shallow_note_entries(workspace)
    target = _numeric_lite_target(config)
    actual = len(entries)
    unfulfilled = max(0, target - actual) if target is not None else 0
    metadata_triage_count = int(result.get("metadata_triage_count") or 0)
    if unfulfilled:
        status = "blocked"
        blocking_reason = (
            f"浅读覆盖仅完成 {actual}/{target}；metadata-only triage {metadata_triage_count} 篇不计入阅读覆盖。"
            "应先从可读 backlog 补足；若可读资料仍不足，进入定向补检。"
        )
    else:
        status = "completed"
        blocking_reason = ""
    selected_readable = [_candidate_manifest_view(item) for item in candidates if _has_abstract(item)]
    selected_metadata = [_candidate_manifest_view(item) for item in candidates if not _has_abstract(item)]
    payload = {
        "schema_version": "1.0.0",
        "semantics": "researchos_shallow_read_coverage_manifest",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": target if target is not None else "all_readable",
        "target_semantics": (
            "numeric target counts only valid ABSTRACT-ONLY note cards; metadata-only triage never counts as reading coverage"
        ),
        "status": status,
        "actual_shallow_read_count": actual,
        "unfulfilled_target": unfulfilled,
        "metadata_triage_count": metadata_triage_count,
        "notes_generated_this_run": int(result.get("notes_generated") or 0),
        "existing_valid_notes_before_run": int(
            (result.get("sweep_plan") or {}).get("existing_shallow_read_notes") or 0
        ),
        "sweep_plan": result.get("sweep_plan") if isinstance(result.get("sweep_plan"), dict) else {},
        "selected_readable_candidates": selected_readable,
        "selected_metadata_triage_candidates": selected_metadata,
        "shallow_note_entries": entries,
        "blocking_reason": blocking_reason,
    }
    path = workspace / _manifest_rel_path(config)
    _write_json_atomically(path, payload)
    return payload


def _current_shallow_note_entries(workspace: Path) -> list[dict[str, Any]]:
    root = workspace / "literature" / "shallow_read_notes"
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md"), key=lambda item: item.as_posix()):
        if not _is_abstract_note_card(path):
            continue
        try:
            rel_path = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        entries.append(
            {
                "paper_id": _note_paper_id(path),
                "path": rel_path,
                "size": path.stat().st_size,
            }
        )
    return entries


def _is_abstract_note_card(path: Path) -> bool:
    if not path.is_file() or not is_paper_note_file(path):
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return "abstract-only" in text.casefold()


def _note_paper_id(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return path.stem
    match = re.search(r"(?im)^\s*-\s*\*\*ID\*\*\s*:\s*(.+?)\s*$", head)
    if match:
        value = match.group(1).strip().strip("`[]")
        if value:
            return value
    return path.stem


def _candidate_manifest_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": _normalize_id(record),
        "title": str(record.get("title") or "").strip(),
        "source_role": str(record.get("t2_pool_role") or "").strip(),
        "has_abstract": _has_abstract(record),
    }


def _numeric_lite_target(config: dict[str, Any]) -> int | None:
    raw = config.get("lite_paper_num")
    if raw in (None, "", "all", "ALL", "all_readable", "ALL_READABLE", "unlimited", "UNLIMITED"):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def _manifest_rel_path(config: dict[str, Any]) -> str:
    value = str(config.get("manifest_path") or SHALLOW_READ_MANIFEST_REL_PATH).replace("\\", "/").strip()
    if not value.startswith("literature/") or not value.endswith(".json"):
        return SHALLOW_READ_MANIFEST_REL_PATH
    return value


def _write_reading_upgrade_queue(workspace: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Offer real local PDFs for optional evidence upgrades without faking a read."""

    queue_rel = str(config.get("reading_upgrade_queue") or READING_UPGRADE_QUEUE_REL_PATH).replace("\\", "/").strip()
    if not queue_rel.startswith("literature/") or not queue_rel.endswith(".jsonl"):
        queue_rel = READING_UPGRADE_QUEUE_REL_PATH
    records = _literature_record_index(workspace)
    ranks = _deep_read_queue_ranks(workspace)
    max_pages = _positive_int(config.get("max_auto_full_text_pages"), default=100)
    entries: list[dict[str, Any]] = []
    for note in _current_shallow_note_entries(workspace):
        record = _match_record_for_note(note, records)
        if record is None:
            continue
        pdf_path, page_count = _local_pdf_details(workspace, record)
        if not pdf_path:
            continue
        queue_rank = _record_queue_rank(record, ranks)
        long_form = _record_is_long_form(record, page_count, max_pages)
        entries.append(
            {
                "paper_id": _normalize_id(record) or str(note.get("paper_id") or ""),
                "title": str(record.get("title") or "").strip(),
                "source_shallow_note": str(note.get("path") or ""),
                "local_pdf_path": pdf_path,
                "page_count": page_count,
                "queue_rank": queue_rank,
                "queue_path": "literature/deep_read_queue.jsonl" if queue_rank is not None else "",
                "recommended_scope": "targeted_partial_text" if long_form else "full_text_candidate",
                "evidence_boundary": "PDF availability is not reading evidence; record page coverage before changing evidence level.",
                "upgrade_status": "not_read",
            }
        )
    entries.sort(key=lambda item: (item.get("recommended_scope") != "full_text_candidate", str(item.get("paper_id") or "")))
    path = workspace / queue_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries), encoding="utf-8")
    temporary.replace(path)
    return {"path": queue_rel, "candidate_count": len(entries)}


def _literature_record_index(workspace: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for source in ("papers_dedup", "papers_backlog", "papers_verified"):
        path = workspace / "literature" / f"{source}.jsonl"
        if not path.is_file():
            continue
        for record in load_jsonl(path):
            if not isinstance(record, dict):
                continue
            for key in _sweep_identity_keys(record):
                existing = index.get(key)
                if existing is None or _record_has_local_pdf(workspace, record):
                    index[key] = record
    return index


def _deep_read_queue_ranks(workspace: Path) -> dict[str, int]:
    ranks: dict[str, int] = {}
    path = workspace / "literature" / "deep_read_queue.jsonl"
    for index, record in enumerate(load_jsonl(path), start=1):
        rank = _positive_int(record.get("queue_rank"), default=index)
        for key in _sweep_identity_keys(record):
            ranks.setdefault(key, rank)
    return ranks


def _match_record_for_note(note: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    paper_id = str(note.get("paper_id") or "").strip()
    candidates = {paper_id, _normalize_id({"id": paper_id})}
    source_path = str(note.get("path") or "")
    candidates.add(Path(source_path).stem)
    for value in candidates:
        normalized = str(value or "").strip()
        if normalized in index:
            return index[normalized]
        alias = re.sub(r"[^0-9A-Za-z]+", "_", normalized.casefold()).strip("_")
        if alias in index:
            return index[alias]
    return None


def _local_pdf_details(workspace: Path, record: dict[str, Any]) -> tuple[str, int | None]:
    acquisition = record.get("pdf_acquisition") if isinstance(record.get("pdf_acquisition"), dict) else {}
    candidates = (
        record.get("local_pdf_path"),
        acquisition.get("pdf_path"),
        record.get("seed_pdf_path"),
    )
    for value in candidates:
        rel_path = str(value or "").replace("\\", "/").lstrip("./")
        if rel_path and (workspace / rel_path).is_file():
            return rel_path, _page_count_from_record(record, acquisition)
    receipt = _pdf_acquisition_receipt(workspace, record)
    if receipt is not None:
        rel_path = str(receipt.get("pdf_path") or "").replace("\\", "/").lstrip("./")
        if rel_path and (workspace / rel_path).is_file():
            return rel_path, _page_count_from_record(record, receipt)
    return "", None


def _record_has_local_pdf(workspace: Path, record: dict[str, Any]) -> bool:
    return bool(_local_pdf_details(workspace, record)[0])


def _pdf_acquisition_receipt(workspace: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    path = workspace / "literature" / "pdf_acquisition_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    receipts = payload.get("receipts") if isinstance(payload, dict) else []
    record_keys = _sweep_identity_keys(record)
    for receipt in receipts if isinstance(receipts, list) else []:
        if not isinstance(receipt, dict):
            continue
        receipt_keys = {str(value).strip() for value in receipt.get("identity_keys") or [] if str(value).strip()}
        paper_id = str(receipt.get("paper_id") or "").strip()
        if paper_id:
            receipt_keys.add(paper_id)
            receipt_keys.add(_normalize_id({"id": paper_id}))
        if record_keys & receipt_keys:
            return receipt
    return None


def _page_count_from_record(record: dict[str, Any], acquisition: dict[str, Any]) -> int | None:
    for value in (acquisition.get("page_count"), record.get("page_count"), record.get("num_pages"), record.get("number_of_pages")):
        try:
            page_count = int(value)
        except (TypeError, ValueError):
            continue
        if page_count > 0:
            return page_count
    return None


def _record_queue_rank(record: dict[str, Any], ranks: dict[str, int]) -> int | None:
    for key in _sweep_identity_keys(record):
        if key in ranks:
            return ranks[key]
    return None


def _record_is_long_form(record: dict[str, Any], page_count: int | None, max_pages: int) -> bool:
    type_text = " ".join(
        str(record.get(key) or "").casefold()
        for key in ("type", "work_type", "publication_type", "document_type", "genre")
    )
    if any(token in type_text for token in ("book", "monograph", "book chapter", "edited volume")):
        return True
    return bool(page_count and page_count > max_pages)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(1, result)


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


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


async def _call_abstract_batch_reader(
    abstract_batch_reader: AbstractBatchReader,
    papers: list[dict[str, Any]],
    prompt: str,
) -> Any:
    try:
        signature = inspect.signature(abstract_batch_reader)
        if len(signature.parameters) <= 1:
            value = abstract_batch_reader(papers)  # type: ignore[misc]
        else:
            value = abstract_batch_reader(papers, prompt)
    except (TypeError, ValueError):
        value = abstract_batch_reader(papers, prompt)
    if inspect.isawaitable(value):
        value = await value
    return value


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
        f"- Provider-context abstract batches: {summary.get('llm_batch_calls', 0)}\n"
        f"- Per-paper fallback after batch: {summary.get('batch_fallback_count', 0)}\n"
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
    llm_batch_calls: int = 0,
) -> str:
    if llm_batch_calls > 0 and metadata_triage_llm > 0:
        return "reader_llm_batched+metadata_triage_llm"
    if llm_batch_calls > 0:
        return "reader_llm_batched"
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
