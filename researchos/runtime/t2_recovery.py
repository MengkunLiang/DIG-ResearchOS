from __future__ import annotations

"""T2 运行期恢复与确定性收尾。

当 Scout Agent 已经拿到了足够的检索结果，但 LLM 在去重/写文件前中断时，
这里提供一条纯代码路径，把 `papers_raw.jsonl` 收敛为 T2 所需的其余产物。
"""

import asyncio
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

import yaml

from ..tools.paper_enrichment import apply_semantic_screening, build_access_audit, build_deep_read_queue, enrich_papers
from ..tools.citation_graph import build_domain_map
from ..tools.abstract_utils import clean_abstract
from ..tools.crossref_api import _extract_crossref_references
from ..tools.openalex_api import _work_to_paper as _openalex_work_to_paper
from ..tools.paper_save_tools import (
    SavePapersDedupTool,
    SavePapersRawTool,
    _merge_raw_records,
    _raw_record_identity_keys,
)
from ..literature_identity import normalize_loose_identity_key, paper_record_match_keys, stable_noopenalex_id
from ..tools.seed_paper_processor import _choose_pdf_title, _is_likely_pdf_header_or_journal_title
from ..tools.paper_utils import (
    deduplicate_papers,
    filter_by_domain,
    generate_search_log,
    score_papers,
)
from ..tools.scout_progress import ScoutProgressLogger
from ..tools.workspace_policy import WorkspaceAccessPolicy
from .literature_quality import apply_literature_quality_policy
from .t2_config import (
    T2FinalizeConfig,
    load_deep_read_queue_config,
    load_literature_quality_policy,
    load_t2_finalize_config,
)
from ..time_utils import current_utc_year, format_year_window, recent_year_from

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - depends on runtime env
    httpx = None


SEARCH_TOOL_NAMES = frozenset(
    {
        "multi_source_search",
        "search_papers",
        "semantic_scholar_search",
        "arxiv_search",
        "openalex_search",
        "crossref_search",
        "elsevier_scopus_search",
        "informs_search",
        "fetch_outgoing_citations",
    }
)

T2_FINALIZE_MANIFEST_REL_PATH = "literature/t2_finalize_manifest.json"
T2_FINALIZE_SOFT_TEXT_INPUTS = {"seed_ideas", "seed_constraints"}
T2_FINALIZE_INPUT_PATHS = {
    "project": "project.yaml",
    "papers_raw": "literature/papers_raw.jsonl",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
    "seed_papers": "user_seeds/seed_papers.jsonl",
    "seed_pdfs": "user_seeds/pdfs",
    "legacy_seed_papers_dir": "seeds/T2_scout/papers",
    "legacy_seed_constraints": "seeds/T2_scout/constraints.md",
    "seed_ideas": "user_seeds/seed_ideas.md",
    "seed_constraints": "user_seeds/seed_constraints.md",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "seed_external_resources": "user_seeds/seed_external_resources.jsonl",
    "literature_pdfs": "literature/pdfs",
    "agent_params_config": "config/agent_params.yaml",
    "user_settings_config": "config/user_settings.yaml",
}

_DEFAULT_T2_FINALIZE_CONFIG = T2FinalizeConfig()

_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _load_project(workspace_dir: Path) -> dict[str, Any]:
    project_path = workspace_dir / "project.yaml"
    if not project_path.exists():
        return {}
    data = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _load_bridge_domain_plan(workspace_dir: Path) -> dict[str, Any]:
    path = workspace_dir / "literature" / "bridge_domain_plan.json"
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _bridge_priorities_from_plan(plan: dict[str, Any]) -> dict[str, str]:
    domains = plan.get("bridge_domains") if isinstance(plan, dict) else []
    if not isinstance(domains, list):
        return {}
    return {
        str(item.get("bridge_id") or "").strip(): str(item.get("priority") or "should_explore").strip()
        for item in domains
        if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()
    }


def _first_text(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return default
    if isinstance(value, str):
        return value.strip()
    if value in (None, "", [], {}):
        return default
    return str(value).strip()


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, "", [], {}):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _year_from_crossref_payload(payload: dict[str, Any]) -> int | None:
    issued = (
        payload.get("published-print")
        or payload.get("published-online")
        or payload.get("published")
        or payload.get("issued")
        or payload.get("created")
    )
    parts = issued.get("date-parts") if isinstance(issued, dict) else None
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return None
    return _safe_int(parts[0][0], None)


def _crossref_author_dicts(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_authors = payload.get("author") or []
    if not isinstance(raw_authors, list):
        return []
    authors: list[dict[str, str]] = []
    for author in raw_authors[:10]:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            given = str(author.get("given") or "").strip()
            family = str(author.get("family") or "").strip()
            name = f"{given} {family}".strip() or str(author.get("name") or "").strip()
        else:
            name = str(author).strip()
        if name:
            authors.append({"name": name})
    return authors


def _normalize_keywords(project: dict[str, Any]) -> list[str]:
    raw_keywords = project.get("keywords") or []
    keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
    if keywords:
        return keywords
    direction = str(project.get("research_direction", "")).strip()
    if not direction:
        return []
    # 退化情况下，用研究方向整句做弱关键词。
    return [direction]


def _keyword_aliases(keyword: str) -> list[str]:
    tokens = [token for token in keyword.lower().replace("/", " ").split() if token and token not in _STOPWORDS]
    aliases = {keyword.lower().strip()}
    aliases.update(token for token in tokens if len(token) >= 4)
    return [alias for alias in aliases if alias]


def _project_domain_profile(project: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicit domain profile if the project provides one.

    T2 recovery must not infer discipline-specific filters from hardcoded
    keyword lists. If users or an upstream LLM want profile-driven filtering,
    they can store it in project.yaml under ``domain_profile`` or
    ``literature_domain_profile``.
    """

    for key in ("domain_profile", "literature_domain_profile"):
        profile = project.get(key)
        if isinstance(profile, dict):
            return profile
    return None


def _select_active_candidate_pool(
    scored_papers: list[dict[str, Any]],
    workspace_dir: Path,
    *,
    config: T2FinalizeConfig | None = None,
    max_count: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Split the recovered pool into a retained T2 candidate set and a backlog.

    `papers_raw.jsonl` is the complete audit trail. `papers_dedup.jsonl` is the
    retained candidate set consumed by T3/T3.5, and Scout validation caps it at
    120. Older recovery code kept every verified paper in `papers_dedup`, which
    made the validator fail and pushed hundreds of weak bridge hits into later
    LLM stages. This selector keeps seeds and high-signal material retained while
    writing the rest to `papers_backlog.jsonl` for audit/revisit.
    """

    cfg = config or _DEFAULT_T2_FINALIZE_CONFIG
    resolved_max_count = max_count if max_count is not None else cfg.active_pool_max
    if not scored_papers:
        return [], [], {
            "active_pool_max": resolved_max_count,
            "input_count": 0,
            "active_count": 0,
            "backlog_count": 0,
        }

    max_count = max(1, int(resolved_max_count))
    bridge_plan = _load_bridge_domain_plan(workspace_dir)
    confirmed_bridges = [
        str(item.get("bridge_id") or "").strip()
        for item in (bridge_plan.get("bridge_domains") if isinstance(bridge_plan.get("bridge_domains"), list) else [])
        if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()
    ]
    bridge_priorities = _bridge_priorities_from_plan(bridge_plan)
    skipped_bridge_ids = {
        bridge_id
        for bridge_id, priority in bridge_priorities.items()
        if priority in {"no_cross", "skip", "defer", "drop"}
    }
    seed_key_sets = [paper_record_match_keys(seed) for seed in _load_seed_papers(workspace_dir)]

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    selection_reasons: Counter[str] = Counter()
    bridge_active_counts: Counter[str] = Counter()

    def record_keys(record: dict[str, Any]) -> set[str]:
        keys = paper_record_match_keys(record)
        title_key = normalize_loose_identity_key(_paper_title_text(record.get("title")))
        if title_key:
            keys.add(f"title:{title_key}")
        year = str(record.get("year") or "").strip()
        if title_key and year:
            keys.add(f"title_year:{title_key}|{year}")
        return {key for key in keys if key}

    def is_seed(record: dict[str, Any]) -> bool:
        if bool(record.get("seed_priority")) or str(record.get("source") or "") == "user_seed":
            return True
        keys = record_keys(record)
        return any(keys and seed_keys and keys & seed_keys for seed_keys in seed_key_sets)

    def bridge_ids(record: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for key in ("bridge_id", "recalled_by_bridges", "contributed_bridges"):
            raw = record.get(key)
            candidates = raw if isinstance(raw, list) else [raw]
            for item in candidates:
                text = str(item or "").strip()
                if text and text not in values:
                    values.append(text)
        screen = record.get("semantic_screen") if isinstance(record.get("semantic_screen"), dict) else {}
        text = str(screen.get("bridge_id") or "").strip()
        if text and text not in values:
            values.append(text)
        return values

    def is_screened_deep(record: dict[str, Any]) -> bool:
        screen = record.get("semantic_screen") if isinstance(record.get("semantic_screen"), dict) else {}
        return bool(screen.get("can_enter_deep_read"))

    def is_snowball(record: dict[str, Any]) -> bool:
        return (
            str(record.get("retrieval_intent") or "").strip() == "citation_snowball"
            or str(record.get("search_bucket") or record.get("source_bucket") or "").strip() == "snowball"
            or bool(record.get("citation_snowball_source_id") or record.get("citation_snowball_source_ids"))
        )

    def rank_key(record: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -float(record.get("relevance_score", 0.0) or 0.0),
            -int(record.get("citation_count", 0) or 0),
            -int(record.get("year", 0) or 0),
            _paper_title_text(record.get("title")).casefold(),
        )

    def add_records(
        records: list[dict[str, Any]],
        reason: str,
        *,
        limit: int | None = None,
        predicate: Any | None = None,
    ) -> None:
        added = 0
        for record in sorted(records, key=rank_key):
            if predicate is not None and not predicate(record):
                continue
            if len(selected) >= max_count:
                break
            if limit is not None and added >= max(0, int(limit)):
                break
            keys = record_keys(record)
            if keys and selected_keys & keys:
                continue
            active = dict(record)
            active["t2_pool_role"] = "active"
            active["active_pool_reason"] = reason
            selected.append(active)
            selected_keys.update(keys)
            for bridge_id in bridge_ids(active):
                bridge_active_counts[bridge_id] += 1
            selection_reasons[reason] += 1
            added += 1

    def bridge_cap_allows(record: dict[str, Any]) -> bool:
        if is_seed(record):
            return True
        ids = [bridge_id for bridge_id in bridge_ids(record) if bridge_id in bridge_priorities]
        if not ids:
            return True
        for bridge_id in ids:
            priority = bridge_priorities.get(bridge_id) or "should_explore"
            if bridge_id in skipped_bridge_ids:
                return False
            cap = (
                cfg.must_bridge_active_pool_cap_per_bridge
                if priority == "must_explore"
                else cfg.should_bridge_active_pool_cap_per_bridge
            )
            if cap <= 0 or bridge_active_counts[bridge_id] >= cap:
                return False
        return True

    seeds = [paper for paper in scored_papers if is_seed(paper)]
    add_records(seeds, "seed")

    screened = [paper for paper in scored_papers if not is_seed(paper) and is_screened_deep(paper)]
    add_records(
        screened,
        "semantic_screen_deep_read",
        limit=cfg.screened_active_pool_cap,
        predicate=bridge_cap_allows,
    )

    for bridge_id in confirmed_bridges:
        priority = bridge_priorities.get(bridge_id) or "should_explore"
        if priority in {"no_cross", "skip", "defer", "drop"}:
            selection_reasons[f"bridge_skipped:{bridge_id}:{priority}"] += 0
            continue
        cap = (
            cfg.must_bridge_active_pool_cap_per_bridge
            if priority == "must_explore"
            else cfg.should_bridge_active_pool_cap_per_bridge
        )
        if cap <= 0:
            selection_reasons[f"bridge_no_active_slots:{bridge_id}:{priority}"] += 0
            continue
        bridge_pool = [
            paper
            for paper in scored_papers
            if not is_seed(paper)
            and not is_screened_deep(paper)
            and bridge_id in bridge_ids(paper)
        ]
        add_records(bridge_pool, f"bridge_recall:{bridge_id}:{priority}", limit=cap, predicate=bridge_cap_allows)

    add_records(
        [paper for paper in scored_papers if not is_seed(paper) and is_snowball(paper)],
        "citation_snowball",
        limit=cfg.snowball_active_pool_cap,
        predicate=bridge_cap_allows,
    )
    add_records(scored_papers, "metadata_priority_fill", predicate=bridge_cap_allows)

    backlog: list[dict[str, Any]] = []
    for record in scored_papers:
        keys = record_keys(record)
        if keys and selected_keys & keys:
            continue
        item = dict(record)
        item["t2_pool_role"] = "backlog"
        item["triaged_out"] = True
        item["triaged_reason"] = "t2_active_pool_cap_exceeded"
        item["read_disposition"] = "backlog"
        item["read_disposition_reason"] = "retained_in_papers_backlog_for_audit_or_later_revisit"
        backlog.append(item)

    metadata = {
        "active_pool_max": max_count,
        "input_count": len(scored_papers),
        "active_count": len(selected),
        "backlog_count": len(backlog),
        "selection_reasons": dict(selection_reasons),
        "confirmed_bridge_ids": confirmed_bridges,
        "bridge_priorities": bridge_priorities,
        "bridge_active_pool_cap_per_bridge": cfg.bridge_active_pool_cap_per_bridge,
        "must_bridge_active_pool_cap_per_bridge": cfg.must_bridge_active_pool_cap_per_bridge,
        "should_bridge_active_pool_cap_per_bridge": cfg.should_bridge_active_pool_cap_per_bridge,
        "skipped_bridge_ids": sorted(skipped_bridge_ids),
        "screened_active_pool_cap": cfg.screened_active_pool_cap,
        "snowball_active_pool_cap": cfg.snowball_active_pool_cap,
    }
    return selected, backlog, metadata


def _normalize_match_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _paper_title_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            text = _paper_title_text(item)
            if text:
                return text
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def _file_fingerprint(workspace_dir: Path, rel_path: str) -> dict[str, Any]:
    path = _resolve_fingerprint_path(workspace_dir, rel_path)
    item: dict[str, Any] = {"path": rel_path, "exists": path.exists()}
    if path.exists() and path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        item["sha256"] = digest.hexdigest()
        item["size"] = path.stat().st_size
    elif path.exists() and path.is_dir():
        item["kind"] = "dir"
        children = [child for child in path.rglob("*") if child.is_file()]
        item["file_count"] = len(children)
        digest = hashlib.sha256()
        for child in sorted(children, key=lambda p: p.relative_to(path).as_posix()):
            rel = child.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            try:
                stat = child.stat()
                digest.update(str(stat.st_size).encode("ascii"))
                digest.update(b"\0")
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        item["sha256"] = digest.hexdigest()
    return item


def _resolve_fingerprint_path(workspace_dir: Path, rel_path: str) -> Path:
    workspace_path = workspace_dir / rel_path
    if workspace_path.exists() or not rel_path.startswith("config/"):
        return workspace_path
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / rel_path


def t2_input_fingerprints(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    workspace_dir = workspace_dir.resolve()
    return {label: _file_fingerprint(workspace_dir, rel_path) for label, rel_path in T2_FINALIZE_INPUT_PATHS.items()}


def write_t2_finalize_manifest(workspace_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "version": 1,
        "semantics": "t2_finalize_input_fingerprints",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_fingerprints": t2_input_fingerprints(workspace_dir),
        "summary": {
            "raw_count": summary.get("raw_count"),
            "dedup_count": summary.get("dedup_count"),
            "backlog_count": summary.get("backlog_count"),
            "query_count": summary.get("query_count"),
            "t2_finalize_config": summary.get("t2_finalize_config"),
            "deep_read_queue_config": summary.get("deep_read_queue_config"),
        },
    }
    path = workspace_dir / T2_FINALIZE_MANIFEST_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def validate_t2_finalize_manifest(workspace_dir: Path) -> tuple[bool, str | None]:
    manifest_path = workspace_dir / T2_FINALIZE_MANIFEST_REL_PATH
    if not manifest_path.exists() or manifest_path.stat().st_size <= 0:
        return False, "缺少 literature/t2_finalize_manifest.json，T2 需要重新收尾"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"t2_finalize_manifest.json 解析失败: {exc}"
    if not isinstance(manifest, dict):
        return False, "t2_finalize_manifest.json 顶层必须是对象"
    if manifest.get("semantics") != "t2_finalize_input_fingerprints":
        return False, "t2_finalize_manifest.json semantics 不正确"
    previous = manifest.get("input_fingerprints")
    if not isinstance(previous, dict):
        return False, "t2_finalize_manifest.json 缺少 input_fingerprints"
    current = t2_input_fingerprints(workspace_dir)
    stale: list[str] = []
    for label, item in current.items():
        prior = previous.get(label)
        if not isinstance(prior, dict):
            stale.append(label)
            continue
        if bool(prior.get("exists")) != bool(item.get("exists")):
            stale.append(label)
            continue
        if item.get("exists") and item.get("sha256") and str(prior.get("sha256") or "") != str(item.get("sha256") or ""):
            stale.append(label)
    if stale:
        hard_stale = [label for label in stale if label not in T2_FINALIZE_SOFT_TEXT_INPUTS]
        if not hard_stale:
            return True, None
        return False, "t2_finalize_manifest.json 对应输入已变化，需要重新跑 T2: " + ", ".join(stale)
    return True, None


def _log_t2_progress(workspace_dir: Path, config: T2FinalizeConfig, event: str, **fields: Any) -> None:
    """Best-effort update for `literature/temp/scout_progress.md`."""

    if not config.progress_enabled:
        return
    try:
        ScoutProgressLogger(workspace_dir, config.progress_file).log_runtime_event(event, **fields)
    except Exception:
        return


def _candidate_record_identity_keys(record: dict[str, Any]) -> set[str]:
    keys = set(paper_record_match_keys(record))
    title_key = normalize_loose_identity_key(_paper_title_text(record.get("title")))
    if title_key:
        keys.add(f"title:{title_key}")
        year = str(record.get("year") or "").strip()
        if year:
            keys.add(f"title_year:{title_key}|{year}")
    return {key for key in keys if key}


def _candidate_pool_identity_keys(records: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        keys.update(_candidate_record_identity_keys(record))
    return keys


def _is_seed_like_recovered_record(record: dict[str, Any], seed_key_sets: list[set[str]]) -> bool:
    if bool(record.get("seed_priority")) or str(record.get("source") or "") == "user_seed":
        return True
    keys = _candidate_record_identity_keys(record)
    return any(keys and seed_keys and keys & seed_keys for seed_keys in seed_key_sets)


def _as_active_pool_backlog_record(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item["t2_pool_role"] = "backlog"
    item["triaged_out"] = True
    item["triaged_reason"] = "t2_active_pool_cap_exceeded"
    item["read_disposition"] = "backlog"
    item["read_disposition_reason"] = "retained_in_papers_backlog_for_audit_or_later_revisit"
    return item


def _domain_filter_backlog_record(record: dict[str, Any], domain_profile: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item["t2_pool_role"] = "backlog"
    item["triaged_out"] = True
    item["triaged_reason"] = "domain_profile_filtered"
    item["read_disposition"] = "backlog"
    item["read_disposition_reason"] = "excluded_from_active_pool_by_domain_profile_retained_for_audit"
    item.setdefault("domain_filter", {})
    if isinstance(item["domain_filter"], dict):
        item["domain_filter"].update(
            {
                "profile_driven": True,
                "filtered_out": True,
                "target_domain": str(domain_profile.get("target_domain") or domain_profile.get("domain") or "profile"),
            }
        )
    return item


def _quality_policy_backlog_record(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item["t2_pool_role"] = "backlog"
    item["triaged_out"] = True
    quality = item.get("literature_quality_policy") if isinstance(item.get("literature_quality_policy"), dict) else {}
    item["triaged_reason"] = str(quality.get("reason") or "literature_quality_policy_filtered")
    item["read_disposition"] = "backlog"
    item["read_disposition_reason"] = "excluded_from_active_pool_by_literature_quality_policy"
    return item


def _ensure_paper_schema_defaults(record: dict[str, Any], *, fallback_score: float = 0.35) -> dict[str, Any]:
    """Fill required paper JSONL fields before validation-facing writes."""

    item = dict(record)
    title = _paper_title_text(item.get("title")) or "Untitled paper"
    item["title"] = title
    item.setdefault("id", str(item.get("canonical_id") or stable_noopenalex_id(item)))
    item.setdefault("source", str(item.get("source_tool") or item.get("verification_source") or "unknown"))
    authors = item.get("authors")
    if isinstance(authors, list):
        normalized_authors = []
        for author in authors:
            if isinstance(author, dict):
                name = str(author.get("name") or author.get("display_name") or "").strip()
            else:
                name = str(author or "").strip()
            if name:
                normalized_authors.append(name)
        item["authors"] = normalized_authors
    elif isinstance(authors, str) and authors.strip():
        item["authors"] = [authors.strip()]
    else:
        item["authors"] = ["Unknown"]
    item.setdefault("venue", str(item.get("journal") or item.get("container_title") or "unknown"))
    item.setdefault("source_type", "unknown")
    try:
        score = float(item.get("relevance_score"))
    except (TypeError, ValueError):
        score = float(fallback_score)
    item["relevance_score"] = max(0.0, min(1.0, score))
    item.setdefault("why_relevant", str(item.get("basis_summary") or item.get("source_query") or "retained for T2 audit/backlog"))
    item.setdefault("abstract", "")
    try:
        citation_count = int(item.get("citation_count") or 0)
    except (TypeError, ValueError):
        citation_count = 0
    item["citation_count"] = max(0, citation_count)
    item.setdefault("url", str(item.get("pdf_url") or ""))
    return item


def _ensure_paper_schema_defaults_many(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_ensure_paper_schema_defaults(record) for record in records]


def _merge_literature_quality_meta(
    base: dict[str, Any],
    update: dict[str, Any],
    *,
    stage_name: str,
) -> dict[str, Any]:
    merged = dict(base)
    for key in ("input_count", "kept_count", "filtered_count"):
        merged[key] = int(merged.get(key) or 0) + int(update.get(key) or 0)
    reasons = dict(merged.get("reason_counts") or {})
    for reason, count in (update.get("reason_counts") or {}).items():
        reasons[str(reason)] = int(reasons.get(str(reason)) or 0) + int(count or 0)
    merged["reason_counts"] = reasons
    stages = list(merged.get("stages") or [])
    stages.append(
        {
            "stage": stage_name,
            "input_count": int(update.get("input_count") or 0),
            "kept_count": int(update.get("kept_count") or 0),
            "filtered_count": int(update.get("filtered_count") or 0),
            "reason_counts": update.get("reason_counts") or {},
        }
    )
    merged["stages"] = stages
    return merged


def _filter_records_by_identity(
    records: list[dict[str, Any]],
    excluded_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    excluded_keys = _candidate_pool_identity_keys(excluded_records)
    if not excluded_keys:
        return records
    return [
        record
        for record in records
        if not (_candidate_record_identity_keys(record) & excluded_keys)
    ]


def _dedupe_backlog_against_active_and_backlog(
    *,
    active_records: list[dict[str, Any]],
    backlog_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    active_keys = _candidate_pool_identity_keys(active_records)
    seen_backlog_keys: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in backlog_records:
        keys = _candidate_record_identity_keys(record)
        if keys and active_keys & keys:
            continue
        if keys and seen_backlog_keys & keys:
            continue
        result.append(record)
        seen_backlog_keys.update(keys)
    return result


def _cap_active_pool_after_seed_repair(
    active_records: list[dict[str, Any]],
    backlog_records: list[dict[str, Any]],
    workspace_dir: Path,
    active_pool_meta: dict[str, Any],
    *,
    max_count: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep seed repair from accidentally bypassing the T2 retained-candidate cap."""

    if max_count is None:
        max_count = int(active_pool_meta.get("active_pool_max") or _DEFAULT_T2_FINALIZE_CONFIG.active_pool_max)
    max_count = max(1, int(max_count))
    if len(active_records) <= max_count:
        backlog_records = _dedupe_backlog_against_active_and_backlog(
            active_records=active_records,
            backlog_records=backlog_records,
        )
        active_pool_meta["active_count"] = len(active_records)
        active_pool_meta["backlog_count"] = len(backlog_records)
        return active_records, backlog_records, active_pool_meta

    seed_key_sets = [_candidate_record_identity_keys(seed) for seed in _load_seed_papers(workspace_dir)]
    seed_records = [
        record
        for record in active_records
        if _is_seed_like_recovered_record(record, seed_key_sets)
    ]
    non_seed_records = [
        record
        for record in active_records
        if not _is_seed_like_recovered_record(record, seed_key_sets)
    ]
    if len(seed_records) >= max_count:
        capped_active = seed_records[:max_count]
        overflow = [*seed_records[max_count:], *non_seed_records]
    else:
        remaining = max_count - len(seed_records)
        capped_active = [*seed_records, *non_seed_records[:remaining]]
        overflow = non_seed_records[remaining:]

    repaired_backlog = [
        *(_as_active_pool_backlog_record(record) for record in overflow),
        *backlog_records,
    ]
    repaired_backlog = _dedupe_backlog_against_active_and_backlog(
        active_records=capped_active,
        backlog_records=repaired_backlog,
    )
    active_pool_meta["seed_repair_overflow_count"] = len(overflow)
    active_pool_meta["active_count"] = len(capped_active)
    active_pool_meta["backlog_count"] = len(repaired_backlog)
    return capped_active, repaired_backlog, active_pool_meta


def _merge_enriched_records_back_to_raw(raw_path: Path, enriched: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist deterministic T2 metadata repairs back to papers_raw.jsonl.

    T2 finalize starts from raw on every resume. If OpenAlex/Crossref/PDF/citation
    repairs only live in dedup/verified, a later finalize can regress whenever a
    network call fails. This merge keeps raw as the durable metadata cache while
    preserving search provenance already accumulated there.
    """

    existing: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            row = len(existing)
            existing.append(record)
            for key in _raw_record_identity_keys(record):
                index.setdefault(key, row)

    merged_count = 0
    appended_count = 0
    for record in enriched:
        if not isinstance(record, dict):
            continue
        keys = _raw_record_identity_keys(record)
        match_idx = next((index[key] for key in keys if key in index), None)
        if match_idx is None:
            match_idx = len(existing)
            existing.append(dict(record))
            appended_count += 1
        else:
            before = json.dumps(existing[match_idx], ensure_ascii=False, sort_keys=True)
            existing[match_idx] = _merge_raw_records(existing[match_idx], record)
            after = json.dumps(existing[match_idx], ensure_ascii=False, sort_keys=True)
            if after != before:
                merged_count += 1
        for key in _raw_record_identity_keys(existing[match_idx]):
            index.setdefault(key, match_idx)

    _write_jsonl(raw_path, existing)
    return {
        "raw_cache_records_after": len(existing),
        "raw_cache_records_merged": merged_count,
        "raw_cache_records_appended": appended_count,
    }


def _researcher_email() -> str:
    return (
        os.environ.get("RESEARCHER_EMAIL")
        or os.environ.get("OPENALEX_MAILTO")
        or "researcher@example.com"
    ).strip()


def _crossref_headers() -> dict[str, str]:
    return {"User-Agent": f"ResearchOS/0.1.0 (mailto:{_researcher_email()})"}


def _record_doi(record: dict[str, Any]) -> str:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    candidates = [
        record.get("doi"),
        external_ids.get("DOI"),
        record.get("canonical_id"),
        record.get("id"),
        record.get("url"),
    ]
    for candidate in candidates:
        doi = str(candidate or "").strip()
        doi = (
            doi.removeprefix("https://doi.org/")
            .removeprefix("http://doi.org/")
            .removeprefix("doi:")
        )
        if doi.startswith("10."):
            return doi
    return ""


def _record_openalex_id(record: dict[str, Any]) -> str:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    for candidate in (
        record.get("canonical_id"),
        record.get("id"),
        record.get("openalex_id"),
        external_ids.get("OpenAlex"),
        record.get("url"),
    ):
        value = str(candidate or "").strip()
        if value.startswith("https://openalex.org/") or value.startswith("https://api.openalex.org/works/"):
            value = value.rstrip("/").split("/")[-1]
        if value.startswith("W") and value[1:].isdigit():
            return value
    return ""


def _record_has_pdf_hint(record: dict[str, Any]) -> bool:
    for key in (
        "pdf_url",
        "open_access_pdf_url",
        "oa_pdf_url",
        "best_pdf_url",
        "full_text_url",
        "pmc_pdf_url",
        "url_for_pdf",
    ):
        if str(record.get(key) or "").strip():
            return True
    for key in ("best_oa_location", "primary_location", "openAccessPdf", "open_access_pdf", "oa_pdf"):
        value = record.get(key)
        if isinstance(value, dict) and any(str(value.get(k) or "").strip() for k in ("pdf_url", "url_for_pdf", "url")):
            return True
    for key in ("locations", "oa_locations", "open_access_locations", "openAccessLocations", "open_access_pdfs"):
        value = record.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _openalex_detail_url(identifier: str) -> str:
    """Build a cheap OpenAlex detail endpoint for a work id or DOI.

    OpenAlex accepts DOI lookups as ``/works/doi:10.x%2F...``. Passing a full
    ``https://doi.org/...`` URL to ``/works/{id}`` can be treated as an
    expensive search-like request and hit paid-budget rate limits, which in
    practice prevents DOI records from receiving OA/PDF/reference backfill.
    """

    value = str(identifier or "").strip()
    if not value:
        return "https://api.openalex.org/works/"
    if value.startswith("https://openalex.org/") or value.startswith("https://api.openalex.org/works/"):
        value = value.rstrip("/").split("/")[-1]
    if value.startswith("W") and value[1:].isdigit():
        return f"https://api.openalex.org/works/{quote(value, safe='')}"

    doi = (
        value.removeprefix("https://doi.org/")
        .removeprefix("http://doi.org/")
        .removeprefix("doi:")
    )
    if doi.startswith("10."):
        return f"https://api.openalex.org/works/doi:{quote(doi, safe='')}"
    return f"https://api.openalex.org/works/{quote(value, safe='')}"


def _merge_openalex_metadata(target: dict[str, Any], openalex_paper: dict[str, Any]) -> dict[str, bool]:
    filled = {
        "openalex_id": False,
        "abstract": False,
        "references": False,
        "pdf_hints": False,
    }

    openalex_id = _record_openalex_id(openalex_paper)
    if openalex_id and _record_openalex_id(target) != openalex_id:
        target["canonical_id"] = openalex_id
        target["canonical_id_source"] = "openalex"
        target["no_openalex_id"] = False
        filled["openalex_id"] = True

    external_ids = target.get("externalIds") if isinstance(target.get("externalIds"), dict) else {}
    incoming_external = openalex_paper.get("externalIds") if isinstance(openalex_paper.get("externalIds"), dict) else {}
    if incoming_external:
        target["externalIds"] = {
            **external_ids,
            **{key: value for key, value in incoming_external.items() if value not in (None, "", [], {})},
        }

    incoming_abstract = clean_abstract(openalex_paper.get("abstract"))
    if incoming_abstract and len(incoming_abstract) > len(clean_abstract(target.get("abstract"))):
        target["abstract"] = incoming_abstract
        target["_abstract_backfilled_from"] = "openalex_recovery"
        target.pop("_missing_abstract", None)
        filled["abstract"] = True

    incoming_doi = _record_doi(openalex_paper)
    if incoming_doi and not _record_doi(target):
        target["doi"] = incoming_doi

    for key in ("year", "venue", "doi", "url"):
        if target.get(key) in (None, "", [], {}) and openalex_paper.get(key) not in (None, "", [], {}):
            target[key] = openalex_paper[key]

    try:
        target["citation_count"] = max(int(target.get("citation_count") or 0), int(openalex_paper.get("citation_count") or 0))
    except (TypeError, ValueError):
        pass

    for key in ("referenced_works", "related_works"):
        incoming = openalex_paper.get(key)
        if isinstance(incoming, list) and incoming:
            current = target.get(key) if isinstance(target.get(key), list) else []
            merged: list[Any] = []
            seen: set[str] = set()
            for item in [*current, *incoming]:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    merged.append(item)
            if len(merged) > len(current):
                target[key] = merged
                filled["references"] = True
    if target.get("referenced_works"):
        target["refs_unavailable"] = False

    had_pdf_hint = _record_has_pdf_hint(target)
    for key in (
        "best_oa_location",
        "primary_location",
        "locations",
        "open_access",
        "pdf_url",
        "open_access_pdf_url",
    ):
        if openalex_paper.get(key) not in (None, "", [], {}) and target.get(key) in (None, "", [], {}):
            target[key] = openalex_paper[key]
    filled["pdf_hints"] = not had_pdf_hint and _record_has_pdf_hint(target)

    provenance = target.get("provenance") if isinstance(target.get("provenance"), dict) else {}
    incoming_provenance = openalex_paper.get("provenance") if isinstance(openalex_paper.get("provenance"), dict) else {}
    if incoming_provenance:
        provenance.setdefault("openalex_source_id", incoming_provenance.get("source_id"))
        provenance.setdefault("openalex_source_url", incoming_provenance.get("source_url"))
        provenance.setdefault("openalex_backfilled", True)
        target["provenance"] = provenance

    return filled


async def _backfill_recovered_openalex_metadata(
    papers: list[dict[str, Any]],
    *,
    max_papers: int | None = None,
    max_concurrency: int = 8,
) -> dict[str, Any]:
    """Bounded OpenAlex repair for DOI/OpenAlex records.

    This is mechanical metadata acquisition only: OpenAlex id, abstract,
    references/related works, and OA/PDF locations. It does not decide
    relevance or evidence claims.
    """

    if httpx is None:
        return {"enabled": False, "reason": "httpx_missing"}

    eligible: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for paper in papers:
        doi = _record_doi(paper)
        openalex_id = _record_openalex_id(paper)
        if not doi and not openalex_id:
            continue
        identifier = openalex_id or f"https://doi.org/{doi}"
        if identifier.casefold() in seen_ids:
            continue
        needs_openalex = not openalex_id
        needs_abstract = not clean_abstract(paper.get("abstract"))
        needs_refs = not (paper.get("referenced_works") or paper.get("related_works"))
        needs_pdf = not _record_has_pdf_hint(paper)
        if not (needs_openalex or needs_abstract or needs_refs or needs_pdf):
            continue
        seen_ids.add(identifier.casefold())
        eligible.append(paper)

    candidates = (
        eligible
        if max_papers is None or max_papers < 0
        else eligible[: max(0, int(max_papers))]
    )

    stats: dict[str, Any] = {
        "enabled": True,
        "eligible_count": len(eligible),
        "candidate_count": len(candidates),
        "attempted": 0,
        "openalex_id_filled": 0,
        "abstract_filled": 0,
        "references_filled": 0,
        "pdf_hints_filled": 0,
        "failed": 0,
        "skipped_by_cap": max(0, len(eligible) - len(candidates)),
    }
    if not candidates:
        stats.update(_openalex_backfill_remaining_stats(eligible))
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(client: "httpx.AsyncClient", paper: dict[str, Any]) -> None:
        doi = _record_doi(paper)
        openalex_id = _record_openalex_id(paper)
        identifier = openalex_id or f"https://doi.org/{doi}"
        url = _openalex_detail_url(identifier)
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(url, params={"mailto": _researcher_email()})
                response.raise_for_status()
                work = response.json()
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                failures = paper.setdefault("_metadata_backfill_failures", [])
                if isinstance(failures, list):
                    failures.append("openalex_detail_failed")
                return
            filled = _merge_openalex_metadata(paper, _openalex_work_to_paper(work))
            if filled["openalex_id"]:
                stats["openalex_id_filled"] = int(stats["openalex_id_filled"]) + 1
            if filled["abstract"]:
                stats["abstract_filled"] = int(stats["abstract_filled"]) + 1
            if filled["references"]:
                stats["references_filled"] = int(stats["references_filled"]) + 1
            if filled["pdf_hints"]:
                stats["pdf_hints_filled"] = int(stats["pdf_hints_filled"]) + 1

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, paper) for paper in candidates))
    stats.update(_openalex_backfill_remaining_stats(eligible))
    return stats


async def _backfill_recovered_crossref_metadata(
    papers: list[dict[str, Any]],
    *,
    max_papers: int | None = None,
    max_concurrency: int = 8,
) -> dict[str, Any]:
    """Bounded DOI metadata repair for deterministic T2 recovery/finalize.

    This only fetches mechanical Crossref fields: abstract, DOI title/year,
    reference DOI/title aliases, and reference counts. It does not decide
    relevance or whether a reference should be read.
    """

    if httpx is None:
        return {"enabled": False, "reason": "httpx_missing"}

    eligible: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    for paper in papers:
        doi = _record_doi(paper)
        if not doi or doi.casefold() in seen_dois:
            continue
        needs_abstract = not clean_abstract(paper.get("abstract"))
        # Crossref references are DOI/title aliases. They are complementary to
        # OpenAlex `referenced_works` W-id graph edges and should be fetched
        # even when OpenAlex already supplied graph edges.
        needs_refs = not paper.get("references")
        if not needs_abstract and not needs_refs:
            continue
        seen_dois.add(doi.casefold())
        eligible.append(paper)

    candidates = (
        eligible
        if max_papers is None or max_papers < 0
        else eligible[: max(0, int(max_papers))]
    )

    stats: dict[str, Any] = {
        "enabled": True,
        "eligible_count": len(eligible),
        "candidate_count": len(candidates),
        "attempted": 0,
        "abstract_filled": 0,
        "references_filled": 0,
        "failed": 0,
        "skipped_by_cap": max(0, len(eligible) - len(candidates)),
        "skipped_after_cap": max(0, len(eligible) - len(candidates)),
    }
    if not candidates:
        stats.update(_crossref_backfill_remaining_stats(eligible))
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(client: "httpx.AsyncClient", paper: dict[str, Any]) -> None:
        doi = _record_doi(paper)
        if not doi:
            return
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    f"https://api.crossref.org/works/{quote(doi, safe='')}",
                    headers=_crossref_headers(),
                )
                response.raise_for_status()
                message = response.json().get("message", {})
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                failures = paper.setdefault("_metadata_backfill_failures", [])
                if isinstance(failures, list):
                    failures.append("crossref_detail_failed")
                return

            abstract = clean_abstract(message.get("abstract"))
            if abstract and not clean_abstract(paper.get("abstract")):
                paper["abstract"] = abstract
                paper["_abstract_backfilled_from"] = "crossref_recovery"
                paper.pop("_missing_abstract", None)
                stats["abstract_filled"] = int(stats["abstract_filled"]) + 1

            references = _extract_crossref_references(message)
            if references:
                current_refs = paper.get("references") if isinstance(paper.get("references"), list) else []
                merged_refs = _dedupe_reference_payload([*current_refs, *references])
                if len(merged_refs) > len(current_refs):
                    paper["references"] = merged_refs
                    stats["references_filled"] = int(stats["references_filled"]) + 1
                # Keep OpenAlex W ids in referenced_works and Crossref DOI/title
                # aliases in references. If referenced_works is empty, expose
                # the Crossref aliases there too for legacy graph consumers.
                if not paper.get("referenced_works"):
                    paper["referenced_works"] = references
            paper["reference_count"] = message.get("reference-count", len(references))

            title = _first_text(message.get("title"))
            if title and not str(paper.get("title") or "").strip():
                paper["title"] = title
            if not paper.get("year"):
                year = _year_from_crossref_payload(message)
                if year is not None:
                    paper["year"] = year

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, paper) for paper in candidates))
    stats.update(_crossref_backfill_remaining_stats(eligible))
    return stats


def _openalex_backfill_remaining_stats(papers: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "remaining_without_openalex_id": sum(1 for paper in papers if not _record_openalex_id(paper)),
        "remaining_missing_abstract": sum(1 for paper in papers if not clean_abstract(paper.get("abstract"))),
        "remaining_missing_references": sum(
            1 for paper in papers if not (paper.get("referenced_works") or paper.get("references"))
        ),
        "remaining_missing_pdf_hints": sum(1 for paper in papers if not _record_has_pdf_hint(paper)),
    }


def _crossref_backfill_remaining_stats(papers: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "remaining_missing_abstract": sum(1 for paper in papers if not clean_abstract(paper.get("abstract"))),
        "remaining_missing_references": sum(1 for paper in papers if not paper.get("references")),
    }


def _dedupe_reference_payload(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    merged: list[Any] = []
    for item in items:
        if item in (None, "", [], {}):
            continue
        if isinstance(item, dict):
            key = str(item.get("doi") or item.get("DOI") or item.get("id") or item.get("openalex_id") or item.get("title") or item).strip().casefold()
        else:
            key = str(item).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


async def _backfill_recovered_openalex_title_metadata(
    papers: list[dict[str, Any]],
    *,
    title_match_threshold: float = 0.92,
    max_papers: int | None = None,
    max_concurrency: int = 6,
) -> dict[str, Any]:
    """Backfill OpenAlex metadata for title-only records.

    DOI/OpenAlex detail lookup covers records that already carry a stable
    identifier. Seed PDFs and some search tools often only provide a title,
    which used to leave the highest-priority papers with no abstract, DOI, PDF
    hints, or citation edges. This helper does a conservative title search and
    only merges fields when the top OpenAlex title is a high-confidence match.
    It is still mechanical metadata acquisition, not relevance judgment.
    """

    if httpx is None:
        return {"enabled": False, "reason": "httpx_missing"}

    eligible: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for paper in papers:
        title = str(paper.get("title") or "").strip()
        title_key = _normalize_match_key(title)
        if not title_key or title_key in {"unknown", "untitled", "untitled seed paper"}:
            continue
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        needs_identifier = not (_record_doi(paper) or _record_openalex_id(paper))
        if not needs_identifier:
            continue
        needs_abstract = not clean_abstract(paper.get("abstract"))
        needs_refs = not (paper.get("referenced_works") or paper.get("references"))
        needs_pdf = not _record_has_pdf_hint(paper)
        if needs_abstract or needs_refs or needs_pdf:
            eligible.append(paper)

    candidates = (
        eligible
        if max_papers is None or max_papers < 0
        else eligible[: max(0, int(max_papers))]
    )
    stats: dict[str, Any] = {
        "enabled": True,
        "eligible_count": len(eligible),
        "candidate_count": len(candidates),
        "attempted": 0,
        "matched": 0,
        "doi_filled": 0,
        "openalex_id_filled": 0,
        "abstract_filled": 0,
        "references_filled": 0,
        "pdf_hints_filled": 0,
        "failed": 0,
        "skipped_low_similarity": 0,
        "skipped_by_cap": max(0, len(eligible) - len(candidates)),
    }
    if not candidates:
        stats.update(_openalex_backfill_remaining_stats(eligible))
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(client: "httpx.AsyncClient", paper: dict[str, Any]) -> None:
        title = str(paper.get("title") or "").strip()
        title_key = _normalize_match_key(title)
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    "https://api.openalex.org/works",
                    params={"search": title, "per-page": 3, "mailto": _researcher_email()},
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                failures = paper.setdefault("_metadata_backfill_failures", [])
                if isinstance(failures, list):
                    failures.append("openalex_title_search_failed")
                return

            best_work: dict[str, Any] | None = None
            best_similarity = 0.0
            for work in results if isinstance(results, list) else []:
                if not isinstance(work, dict):
                    continue
                candidate_title = str(work.get("title") or "").strip()
                similarity = SequenceMatcher(None, title_key, _normalize_match_key(candidate_title)).ratio()
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_work = work

            if not best_work or best_similarity < title_match_threshold:
                stats["skipped_low_similarity"] = int(stats["skipped_low_similarity"]) + 1
                return

            had_doi = bool(_record_doi(paper))
            filled = _merge_openalex_metadata(paper, _openalex_work_to_paper(best_work))
            if not had_doi and _record_doi(paper):
                stats["doi_filled"] = int(stats["doi_filled"]) + 1
            if filled["openalex_id"]:
                stats["openalex_id_filled"] = int(stats["openalex_id_filled"]) + 1
            if filled["abstract"]:
                stats["abstract_filled"] = int(stats["abstract_filled"]) + 1
            if filled["references"]:
                stats["references_filled"] = int(stats["references_filled"]) + 1
            if filled["pdf_hints"]:
                stats["pdf_hints_filled"] = int(stats["pdf_hints_filled"]) + 1
            paper["_metadata_backfilled_from_title"] = "openalex"
            paper["_metadata_title_match_similarity"] = round(best_similarity, 4)
            stats["matched"] = int(stats["matched"]) + 1

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, paper) for paper in candidates))
    stats.update(_openalex_backfill_remaining_stats(eligible))
    return stats


def _crossref_message_to_snowball_paper(
    message: dict[str, Any],
    *,
    source_record: dict[str, Any],
    ref_doi: str,
) -> dict[str, Any] | None:
    title = _first_text(message.get("title"))
    if not title:
        return None

    authors = _crossref_author_dicts(message)
    year = _year_from_crossref_payload(message)

    doi = str(message.get("DOI") or ref_doi or "").strip()
    references = _extract_crossref_references(message)
    source_id = str(source_record.get("canonical_id") or source_record.get("id") or source_record.get("doi") or "").strip()
    source_title = str(source_record.get("title") or source_id or "unknown source").strip()
    venue = _first_text(message.get("container-title"))
    paper: dict[str, Any] = {
        "id": f"doi:{doi}" if doi else title,
        "source": "crossref_snowball",
        "title": title,
        "authors": authors or [{"name": "Unknown"}],
        "year": year,
        "abstract": clean_abstract(message.get("abstract")),
        "venue": venue,
        "doi": doi,
        "citation_count": _safe_int(message.get("is-referenced-by-count"), 0) or 0,
        "url": str(message.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
        "externalIds": {"DOI": doi} if doi else {},
        "references": references,
        "referenced_works": references,
        "reference_count": int(message.get("reference-count") or len(references)),
        "retrieval_intent": "citation_snowball",
        "search_bucket": "snowball",
        "source_bucket": "snowball",
        "source_query": f"Crossref one-hop references from {source_title}",
        "source_tool": "crossref_snowball_backfill",
        "citation_snowball_source_id": source_id,
        "citation_snowball_source_title": source_title,
        "provenance": {
            "source_tool": "crossref_snowball_backfill",
            "source_id": doi,
            "source_url": str(message.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
            "snowball_source_id": source_id,
            "id_source": "doi",
        },
    }
    return paper


def _is_citation_snowball_record(paper: dict[str, Any]) -> bool:
    source_tools = {
        str(item or "").strip()
        for item in [
            paper.get("source_tool"),
            paper.get("source"),
            *((paper.get("source_tools") or []) if isinstance(paper.get("source_tools"), list) else []),
        ]
        if str(item or "").strip()
    }
    return (
        str(paper.get("retrieval_intent") or "").strip() == "citation_snowball"
        or str(paper.get("search_bucket") or paper.get("source_bucket") or "").strip() == "snowball"
        or bool(paper.get("citation_snowball_source_id") or paper.get("citation_snowball_source_ids"))
        or any(source_tool.endswith("snowball_backfill") for source_tool in source_tools)
        or "crossref_reference_title_openalex_backfill" in source_tools
    )


def _existing_snowball_record_count(papers: list[dict[str, Any]], source_tools: set[str]) -> int:
    known_snowball_tools = {
        "openalex_snowball_backfill",
        "crossref_snowball_backfill",
        "crossref_reference_title_openalex_backfill",
    }
    count = 0
    for paper in papers:
        paper_source_tools = {
            str(item or "").strip()
            for item in [
                paper.get("source_tool"),
                paper.get("source"),
                *((paper.get("source_tools") or []) if isinstance(paper.get("source_tools"), list) else []),
            ]
            if str(item or "").strip()
        }
        if paper_source_tools & source_tools:
            count += 1
            continue
        if (
            _is_citation_snowball_record(paper)
            and not (paper_source_tools & known_snowball_tools)
            and any(source_tool in known_snowball_tools or source_tool.endswith("snowball_backfill") for source_tool in source_tools)
        ):
            count += 1
    return count


def _existing_snowball_source_ids(papers: list[dict[str, Any]], source_tools: set[str]) -> set[str]:
    known_snowball_tools = {
        "openalex_snowball_backfill",
        "crossref_snowball_backfill",
        "crossref_reference_title_openalex_backfill",
    }
    source_ids: set[str] = set()
    for paper in papers:
        paper_source_tools = {
            str(item or "").strip()
            for item in [
                paper.get("source_tool"),
                paper.get("source"),
                *((paper.get("source_tools") or []) if isinstance(paper.get("source_tools"), list) else []),
            ]
            if str(item or "").strip()
        }
        is_matching_tool = bool(paper_source_tools & source_tools)
        is_generic_matching = (
            _is_citation_snowball_record(paper)
            and not (paper_source_tools & known_snowball_tools)
            and any(source_tool in known_snowball_tools or source_tool.endswith("snowball_backfill") for source_tool in source_tools)
        )
        if not (is_matching_tool or is_generic_matching):
            continue
        for raw_source in [
            paper.get("citation_snowball_source_id"),
            paper.get("snowball_source_id"),
            *((paper.get("citation_snowball_source_ids") or []) if isinstance(paper.get("citation_snowball_source_ids"), list) else []),
        ]:
            source_id = str(raw_source or "").strip()
            if source_id:
                source_ids.add(source_id)
    return source_ids


def _is_allowed_snowball_source(paper: dict[str, Any]) -> bool:
    """Only expand citation neighbors from high-confidence source papers.

    Snowballing from every metadata fallback paper turns broad query noise into
    more noise. Keep it tied to user seeds or Scout's semantic_screen decisions.
    """

    if _is_citation_snowball_record(paper):
        return False
    if bool(paper.get("seed_priority")) or str(paper.get("source") or "") == "user_seed":
        return True
    if str(paper.get("active_pool_reason") or "") == "seed":
        return True
    screen = paper.get("semantic_screen") if isinstance(paper.get("semantic_screen"), dict) else {}
    return bool(screen.get("can_enter_deep_read"))


async def _expand_crossref_snowball_candidates(
    papers: list[dict[str, Any]],
    *,
    existing_papers: list[dict[str, Any]] | None = None,
    max_sources: int = 12,
    refs_per_source: int = 8,
    max_candidates: int = 40,
    max_concurrency: int = 6,
    title_match_threshold: float = 0.90,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add bounded one-hop DOI reference candidates from Crossref metadata.

    This repairs the failure mode where OpenAlex/S2 are rate-limited and the
    citation graph becomes empty even though Crossref records contain reference
    DOI/title aliases. It is metadata acquisition only; downstream semantic
    screening/queue rules still decide whether candidates are read deeply.
    """

    stats: dict[str, Any] = {
        "enabled": httpx is not None,
        "source_candidates": 0,
        "sources_used": 0,
        "reference_items_seen": 0,
        "reference_dois_seen": 0,
        "reference_titles_seen": 0,
        "title_references_resolved": 0,
        "non_doi_references_skipped": 0,
        "skipped_existing_or_duplicate_reference_dois": 0,
        "skipped_existing_or_duplicate_reference_titles": 0,
        "skipped_by_refs_per_source_cap": 0,
        "skipped_by_max_candidates_cap": 0,
        "attempted": 0,
        "added": 0,
        "failed": 0,
    }
    if httpx is None:
        stats["reason"] = "httpx_missing"
        return [], stats

    known_papers = [*papers, *(existing_papers or [])]
    snowball_tools = {"crossref_snowball_backfill", "crossref_reference_title_openalex_backfill"}
    existing_snowball_records = _existing_snowball_record_count(known_papers, snowball_tools)
    already_expanded_source_ids = _existing_snowball_source_ids(known_papers, snowball_tools)
    if existing_snowball_records > 0:
        stats["skipped_existing_snowball_records"] = existing_snowball_records
        stats["skipped_existing_snowball_source_count"] = len(already_expanded_source_ids)

    existing_dois = {_record_doi(paper).casefold() for paper in known_papers if _record_doi(paper)}
    selected_sources: list[dict[str, Any]] = []
    for paper in papers:
        if not _is_allowed_snowball_source(paper):
            continue
        source_id = str(paper.get("id") or paper.get("canonical_id") or paper.get("doi") or paper.get("title") or "").strip()
        if source_id and source_id in already_expanded_source_ids:
            continue
        refs = paper.get("referenced_works") or paper.get("references") or []
        if not isinstance(refs, list) or not refs:
            continue
        selected_sources.append(paper)
    selected_sources.sort(
        key=lambda paper: (
            not bool(paper.get("seed_priority") or paper.get("source") == "user_seed"),
            not bool(isinstance(paper.get("semantic_screen"), dict) and paper["semantic_screen"].get("can_enter_deep_read")),
            -float(paper.get("relevance_score", 0.0) or 0.0),
            str(paper.get("title") or "").casefold(),
        )
    )
    stats["source_candidates"] = len(selected_sources)
    selected_sources = selected_sources[:max_sources]
    stats["sources_used"] = len(selected_sources)

    doi_ref_jobs: list[tuple[str, dict[str, Any]]] = []
    title_ref_jobs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    seen_ref_dois: set[str] = set()
    seen_ref_titles: set[str] = set()
    existing_titles = {_normalize_match_key(paper.get("title")) for paper in known_papers if _normalize_match_key(paper.get("title"))}
    hit_max_candidates_cap = False
    for source in selected_sources:
        refs = source.get("referenced_works") or source.get("references") or []
        per_source_count = 0
        skipped_after_source_cap = False
        for ref in refs:
            stats["reference_items_seen"] = int(stats["reference_items_seen"]) + 1
            if per_source_count >= refs_per_source:
                skipped_after_source_cap = True
                continue
            if len(doi_ref_jobs) + len(title_ref_jobs) >= max_candidates:
                hit_max_candidates_cap = True
                continue
            if not isinstance(ref, dict):
                stats["non_doi_references_skipped"] = int(stats["non_doi_references_skipped"]) + 1
                continue
            doi = str(ref.get("doi") or ref.get("DOI") or ref.get("id") or "").strip()
            doi = (
                doi.removeprefix("https://doi.org/")
                .removeprefix("http://doi.org/")
                .removeprefix("doi:")
            )
            if not doi or not doi.startswith("10."):
                title_key = _normalize_match_key(ref.get("title") or ref.get("article-title") or ref.get("unstructured"))
                if title_key:
                    stats["reference_titles_seen"] = int(stats["reference_titles_seen"]) + 1
                    if title_key in existing_titles or title_key in seen_ref_titles:
                        stats["skipped_existing_or_duplicate_reference_titles"] = int(
                            stats["skipped_existing_or_duplicate_reference_titles"]
                        ) + 1
                        continue
                    seen_ref_titles.add(title_key)
                    title_ref_jobs.append((title_key, ref, source))
                    per_source_count += 1
                    continue
                stats["non_doi_references_skipped"] = int(stats["non_doi_references_skipped"]) + 1
                continue
            doi_key = doi.casefold()
            if doi_key in existing_dois or doi_key in seen_ref_dois:
                stats["skipped_existing_or_duplicate_reference_dois"] = int(
                    stats["skipped_existing_or_duplicate_reference_dois"]
                ) + 1
                continue
            seen_ref_dois.add(doi_key)
            doi_ref_jobs.append((doi, source))
            per_source_count += 1
        if skipped_after_source_cap:
            stats["skipped_by_refs_per_source_cap"] = int(stats["skipped_by_refs_per_source_cap"]) + 1
    if hit_max_candidates_cap:
        stats["skipped_by_max_candidates_cap"] = max(
            int(stats["skipped_by_max_candidates_cap"]),
            max(0, int(stats["reference_items_seen"]) - max_candidates),
        )

    stats["reference_dois_seen"] = len(doi_ref_jobs)
    if not doi_ref_jobs and not title_ref_jobs:
        return [], stats

    semaphore = asyncio.Semaphore(max_concurrency)
    added: list[dict[str, Any]] = []

    async def _one_doi(client: "httpx.AsyncClient", doi: str, source: dict[str, Any]) -> None:
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    f"https://api.crossref.org/works/{quote(doi, safe='')}",
                    headers=_crossref_headers(),
                )
                response.raise_for_status()
                message = response.json().get("message", {})
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                return
            paper = _crossref_message_to_snowball_paper(message, source_record=source, ref_doi=doi)
            if not paper:
                stats["failed"] = int(stats["failed"]) + 1
                return
            added.append(paper)
            stats["added"] = int(stats["added"]) + 1

    async def _one_title(client: "httpx.AsyncClient", title_key: str, ref: dict[str, Any], source: dict[str, Any]) -> None:
        title = str(ref.get("title") or ref.get("article-title") or ref.get("unstructured") or "").strip()
        if not title:
            stats["failed"] = int(stats["failed"]) + 1
            return
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    "https://api.openalex.org/works",
                    params={"search": title, "per-page": 3, "mailto": _researcher_email()},
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                return
            best_paper: dict[str, Any] | None = None
            best_similarity = 0.0
            for work in results if isinstance(results, list) else []:
                candidate_title = str(work.get("title") or "").strip() if isinstance(work, dict) else ""
                similarity = SequenceMatcher(None, title_key, _normalize_match_key(candidate_title)).ratio()
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_paper = _openalex_work_to_paper(work)
            if not best_paper or best_similarity < title_match_threshold:
                stats["failed"] = int(stats["failed"]) + 1
                return
            source_id = str(source.get("canonical_id") or source.get("id") or source.get("doi") or "").strip()
            source_title = str(source.get("title") or source_id or "unknown source").strip()
            best_paper["source"] = "openalex_title_snowball"
            best_paper["source_tool"] = "crossref_reference_title_openalex_backfill"
            best_paper["retrieval_intent"] = "citation_snowball"
            best_paper["search_bucket"] = "snowball"
            best_paper["source_bucket"] = "snowball"
            best_paper["source_query"] = f"OpenAlex title match for Crossref reference from {source_title}"
            best_paper["citation_snowball_source_id"] = source_id
            best_paper["citation_snowball_source_title"] = source_title
            best_paper["citation_snowball_match_similarity"] = round(best_similarity, 4)
            provenance = best_paper.get("provenance") if isinstance(best_paper.get("provenance"), dict) else {}
            provenance.update(
                {
                    "source_tool": "crossref_reference_title_openalex_backfill",
                    "snowball_source_id": source_id,
                    "snowball_source_title": source_title,
                    "reference_title": title,
                    "title_match_similarity": round(best_similarity, 4),
                }
            )
            best_paper["provenance"] = provenance
            added.append(best_paper)
            stats["title_references_resolved"] = int(stats["title_references_resolved"]) + 1
            stats["added"] = int(stats["added"]) + 1

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(
            *(_one_doi(client, doi, source) for doi, source in doi_ref_jobs),
            *(_one_title(client, title_key, ref, source) for title_key, ref, source in title_ref_jobs),
        )
    return added, stats


def _normalize_openalex_ref_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("https://openalex.org/") or text.startswith("https://api.openalex.org/works/"):
        text = text.rstrip("/").split("/")[-1]
    return text if text.startswith("W") and text[1:].isdigit() else ""


async def _expand_openalex_snowball_candidates(
    papers: list[dict[str, Any]],
    *,
    existing_papers: list[dict[str, Any]] | None = None,
    max_sources: int = 12,
    refs_per_source: int = 8,
    max_candidates: int = 40,
    max_concurrency: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add bounded one-hop OpenAlex reference/related-work candidates.

    OpenAlex detail backfill often gives `referenced_works`/`related_works`
    even when Crossref reference DOI aliases are sparse. Resolving a small,
    deterministic one-hop set prevents the citation graph from becoming a
    decorative artifact: useful graph neighbors can enter the verified pool,
    then T3/T3.5/T8 can see them as evidence candidates. This function only
    acquires metadata; queue admission still depends on seed priority,
    Scout semantic_screen, or explicit backlog/abstract-sweep rules.
    """

    stats: dict[str, Any] = {
        "enabled": httpx is not None,
        "source_candidates": 0,
        "sources_used": 0,
        "reference_items_seen": 0,
        "reference_openalex_ids_seen": 0,
        "related_openalex_ids_seen": 0,
        "non_openalex_references_skipped": 0,
        "skipped_existing_or_duplicate_openalex_ids": 0,
        "skipped_by_refs_per_source_cap": 0,
        "skipped_by_max_candidates_cap": 0,
        "attempted": 0,
        "added": 0,
        "failed": 0,
    }
    if httpx is None:
        stats["reason"] = "httpx_missing"
        return [], stats

    known_papers = [*papers, *(existing_papers or [])]
    snowball_tools = {"openalex_snowball_backfill"}
    existing_snowball_records = _existing_snowball_record_count(known_papers, snowball_tools)
    already_expanded_source_ids = _existing_snowball_source_ids(known_papers, snowball_tools)
    if existing_snowball_records > 0:
        stats["skipped_existing_snowball_records"] = existing_snowball_records
        stats["skipped_existing_snowball_source_count"] = len(already_expanded_source_ids)

    existing_openalex_ids = {_record_openalex_id(paper) for paper in known_papers if _record_openalex_id(paper)}
    selected_sources = [
        paper
        for paper in papers
        if isinstance(paper.get("referenced_works"), list) or isinstance(paper.get("related_works"), list)
        if _is_allowed_snowball_source(paper)
        if str(paper.get("id") or paper.get("canonical_id") or paper.get("doi") or paper.get("title") or "").strip()
        not in already_expanded_source_ids
    ]
    selected_sources.sort(
        key=lambda paper: (
            not bool(paper.get("seed_priority") or paper.get("source") == "user_seed"),
            not bool(isinstance(paper.get("semantic_screen"), dict) and paper["semantic_screen"].get("can_enter_deep_read")),
            -float(paper.get("relevance_score", 0.0) or 0.0),
            str(paper.get("title") or "").casefold(),
        )
    )
    stats["source_candidates"] = len(selected_sources)
    selected_sources = selected_sources[:max_sources]
    stats["sources_used"] = len(selected_sources)

    jobs: list[tuple[str, str, dict[str, Any]]] = []
    seen_work_ids: set[str] = set()
    hit_max_candidates_cap = False
    for source in selected_sources:
        refs: list[tuple[str, Any]] = []
        refs.extend(("referenced_work", item) for item in (source.get("referenced_works") or []))
        refs.extend(("related_work", item) for item in (source.get("related_works") or []))
        per_source_count = 0
        skipped_after_source_cap = False
        for edge_type, ref in refs:
            stats["reference_items_seen"] = int(stats["reference_items_seen"]) + 1
            if per_source_count >= refs_per_source:
                skipped_after_source_cap = True
                continue
            if len(jobs) >= max_candidates:
                hit_max_candidates_cap = True
                continue
            if isinstance(ref, dict):
                raw_ref_id = ref.get("id") or ref.get("openalex_id")
            else:
                raw_ref_id = ref
            work_id = _normalize_openalex_ref_id(raw_ref_id)
            if not work_id:
                stats["non_openalex_references_skipped"] = int(stats["non_openalex_references_skipped"]) + 1
                continue
            if edge_type == "related_work":
                stats["related_openalex_ids_seen"] = int(stats["related_openalex_ids_seen"]) + 1
            else:
                stats["reference_openalex_ids_seen"] = int(stats["reference_openalex_ids_seen"]) + 1
            if work_id in existing_openalex_ids or work_id in seen_work_ids:
                stats["skipped_existing_or_duplicate_openalex_ids"] = int(
                    stats["skipped_existing_or_duplicate_openalex_ids"]
                ) + 1
                continue
            seen_work_ids.add(work_id)
            jobs.append((work_id, edge_type, source))
            per_source_count += 1
        if skipped_after_source_cap:
            stats["skipped_by_refs_per_source_cap"] = int(stats["skipped_by_refs_per_source_cap"]) + 1
    if hit_max_candidates_cap:
        stats["skipped_by_max_candidates_cap"] = max(
            int(stats["skipped_by_max_candidates_cap"]),
            max(0, int(stats["reference_items_seen"]) - max_candidates),
        )
    if not jobs:
        return [], stats

    semaphore = asyncio.Semaphore(max_concurrency)
    added: list[dict[str, Any]] = []

    async def _one(client: "httpx.AsyncClient", work_id: str, edge_type: str, source: dict[str, Any]) -> None:
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    f"https://api.openalex.org/works/{work_id}",
                    params={"mailto": _researcher_email()},
                )
                response.raise_for_status()
                paper = _openalex_work_to_paper(response.json())
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                return
            if not paper or not str(paper.get("title") or "").strip():
                stats["failed"] = int(stats["failed"]) + 1
                return
            source_id = str(source.get("canonical_id") or source.get("id") or source.get("doi") or "").strip()
            source_title = str(source.get("title") or source_id or "unknown source").strip()
            paper["source"] = "openalex_snowball"
            paper["source_tool"] = "openalex_snowball_backfill"
            paper["retrieval_intent"] = "citation_snowball"
            paper["search_bucket"] = "snowball"
            paper["source_bucket"] = "snowball"
            paper["source_query"] = f"OpenAlex one-hop {edge_type} from {source_title}"
            paper["citation_snowball_source_id"] = source_id
            paper["citation_snowball_source_title"] = source_title
            provenance = paper.get("provenance") if isinstance(paper.get("provenance"), dict) else {}
            provenance.update(
                {
                    "source_tool": "openalex_snowball_backfill",
                    "snowball_source_id": source_id,
                    "snowball_source_title": source_title,
                    "snowball_edge_type": edge_type,
                }
            )
            paper["provenance"] = provenance
            added.append(paper)
            stats["added"] = int(stats["added"]) + 1

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, work_id, edge_type, source) for work_id, edge_type, source in jobs))
    return added, stats


async def _backfill_recovered_multisource_abstracts(
    papers: list[dict[str, Any]],
    policy: WorkspaceAccessPolicy,
    *,
    title_match_threshold: float = 0.88,
    max_concurrency: int = 6,
) -> dict[str, Any]:
    """Use the same multi-source abstract repair as the Scout tool in finalize.

    This keeps T2 recovery/finalize from depending on the LLM remembering to
    call `backfill_paper_abstracts` before semantic screening. The helper only
    fills missing abstracts; it does not change relevance, source_type, queue
    admission, or any knowledge-bearing judgment.
    """

    if httpx is None:
        return {"enabled": False, "reason": "httpx_missing"}

    missing = [paper for paper in papers if not clean_abstract(paper.get("abstract"))]
    by_source: dict[str, int] = {}
    stats: dict[str, Any] = {
        "enabled": True,
        "candidate_count": len(missing),
        "attempted_single": 0,
        "filled": 0,
        "remaining_missing_abstract": len(missing),
        "by_source": by_source,
    }
    if not missing:
        return stats

    from ..tools.paper_enrichment_tool import BackfillPaperAbstractsTool

    helper = BackfillPaperAbstractsTool(policy)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        await helper._s2_batch_backfill(client, missing, by_source)
        still_missing = [paper for paper in missing if not clean_abstract(paper.get("abstract"))]
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _one(paper: dict[str, Any]) -> None:
            async with semaphore:
                await helper._backfill_single(
                    client,
                    paper,
                    by_source,
                    title_threshold=title_match_threshold,
                    enable_title_fallback=True,
                )

        stats["attempted_single"] = len(still_missing)
        if still_missing:
            await asyncio.gather(*(_one(paper) for paper in still_missing))

    stats["filled"] = sum(by_source.values())
    stats["remaining_missing_abstract"] = sum(1 for paper in papers if not clean_abstract(paper.get("abstract")))
    return stats


async def _pre_active_light_backfill(
    papers: list[dict[str, Any]],
    policy: WorkspaceAccessPolicy,
    config: T2FinalizeConfig,
) -> dict[str, Any]:
    """Best-effort metadata repair before retained-candidate/backlog selection.

    This pass is intentionally bounded and non-semantic. It gives near-frontier
    raw/dedup candidates a fair chance to receive DOI/OpenAlex/abstract/OA
    hints before `papers_dedup.jsonl` is capped into the retained-candidate set. Expensive
    citation snowball still runs only after active selection.
    """

    limit = int(config.pre_active_light_backfill_max)
    if limit == 0 or not papers:
        return {
            "enabled": False,
            "reason": "disabled_or_empty",
            "input_count": len(papers),
            "candidate_count": 0,
        }
    candidates = papers if limit < 0 else papers[: max(0, limit)]
    if not candidates:
        return {
            "enabled": False,
            "reason": "empty_candidate_slice",
            "input_count": len(papers),
            "candidate_count": 0,
        }

    title_stats = await _backfill_recovered_openalex_title_metadata(
        candidates,
        max_concurrency=config.metadata_backfill_max_concurrency,
    )
    openalex_stats = await _backfill_recovered_openalex_metadata(
        candidates,
        max_concurrency=config.metadata_backfill_max_concurrency,
    )
    abstract_stats = await _backfill_recovered_multisource_abstracts(
        candidates,
        policy,
        title_match_threshold=config.abstract_backfill_title_match_threshold,
        max_concurrency=config.abstract_backfill_max_concurrency,
    )
    return {
        "enabled": True,
        "input_count": len(papers),
        "candidate_count": len(candidates),
        "skipped_by_cap": max(0, len(papers) - len(candidates)),
        "openalex_title_backfill": title_stats,
        "openalex_detail_backfill": openalex_stats,
        "multisource_abstract_backfill": abstract_stats,
        "abstract_after": sum(1 for paper in candidates if clean_abstract(paper.get("abstract"))),
        "pdf_hint_after": sum(1 for paper in candidates if _record_has_pdf_hint(paper)),
        "reference_hint_after": sum(
            1 for paper in candidates if paper.get("referenced_works") or paper.get("references")
        ),
    }


async def _persist_snowball_candidates(
    policy: WorkspaceAccessPolicy,
    candidates: list[dict[str, Any]],
    stats: dict[str, Any],
) -> dict[str, Any]:
    """Persist one snowball source and attach accurate raw append stats."""

    if not candidates:
        stats["raw_persist_ok"] = True
        stats["raw_persisted"] = 0
        stats["raw_merged"] = 0
        stats["raw_persisted_or_merged"] = 0
        return stats
    raw_save_result = await SavePapersRawTool(policy).execute(papers=candidates, append=True)
    raw_persisted = int((raw_save_result.data or {}).get("count") or 0) if raw_save_result.ok else 0
    raw_merged = int((raw_save_result.data or {}).get("merged_count") or 0) if raw_save_result.ok else 0
    stats["raw_persist_ok"] = bool(raw_save_result.ok)
    stats["raw_persisted"] = raw_persisted
    stats["raw_merged"] = raw_merged
    stats["raw_persisted_or_merged"] = raw_persisted + raw_merged
    if not raw_save_result.ok:
        stats["raw_persist_error"] = raw_save_result.error or raw_save_result.content
    return stats


def _load_seed_papers(workspace_dir: Path) -> list[dict[str, Any]]:
    return [
        _repair_seed_record_title(record, workspace_dir)
        for record in _load_jsonl(workspace_dir / "user_seeds" / "seed_papers.jsonl")
    ]


def _seed_title_from_pdf_path(seed: dict[str, Any]) -> str:
    pdf_path = str(seed.get("pdf_path") or seed.get("seed_pdf_path") or "").strip()
    if not pdf_path:
        return ""
    title = _choose_pdf_title(
        metadata_title="",
        first_page_text="",
        filename_stem=Path(pdf_path).stem,
    )
    return str(title.get("title") or "").strip()


def _repair_seed_record_title(seed: dict[str, Any], workspace_dir: Path | None = None) -> dict[str, Any]:
    """Repair legacy seed records whose title is a journal masthead/page header.

    Older T1 runs sometimes stored Chinese PDF mastheads such as
    ``《管理世界》（月刊）`` as seed paper titles.  T2 recovery must not propagate
    those into IDs, queues, BibTeX, or notes.  Only repair when the existing
    title is clearly a PDF header/journal title and the PDF filename gives a
    usable title.
    """

    record = dict(seed)
    title = str(record.get("title") or "").strip()
    if title and not _is_likely_pdf_header_or_journal_title(title):
        return record

    repaired_title = _seed_title_from_pdf_path(record)
    if not repaired_title or repaired_title == title or _is_likely_pdf_header_or_journal_title(repaired_title):
        return record

    record["title"] = repaired_title
    record["original_title"] = title
    record["title_source"] = "pdf_filename_repair"
    record["title_confidence"] = "heuristic_medium"
    record["metadata_review_required"] = True
    record["title_repair_reason"] = "legacy_seed_title_was_pdf_header_or_journal_masthead"
    if workspace_dir is not None:
        rel_pdf = str(record.get("pdf_path") or record.get("seed_pdf_path") or "")
        pdf_abs = workspace_dir / rel_pdf if rel_pdf else None
        if pdf_abs is not None and pdf_abs.exists():
            record.setdefault("has_seed_pdf", True)
    return record


def _seed_to_recovery_paper(seed: dict[str, Any]) -> dict[str, Any]:
    arxiv_id = str(seed.get("arxiv_id", "")).strip()
    paper_id = f"arxiv:{arxiv_id}" if arxiv_id and not arxiv_id.startswith("arxiv:") else arxiv_id
    if not paper_id:
        paper_id = str(seed.get("doi") or seed.get("id") or "").strip()
    canonical_id = paper_id if paper_id.startswith("arxiv:") else stable_noopenalex_id({**seed, "id": paper_id})
    canonical_id_source = "arxiv_noopenalex" if paper_id.startswith("arxiv:") else "noopenalex_fallback"
    if not paper_id:
        paper_id = canonical_id
    url = str(seed.get("url") or "").strip()
    try:
        seed_year = int(seed["year"]) if seed.get("year") else None
    except (TypeError, ValueError):
        seed_year = None
    recovered = {
        "id": paper_id,
        "canonical_id": canonical_id,
        "preferred_id_source": "arxiv" if arxiv_id else "doi" if seed.get("doi") else "seed_fallback",
        "canonical_id_source": canonical_id_source,
        "no_openalex_id": True,
        "source": "user_seed",
        "title": str(seed.get("title", "")).strip() or "Untitled seed paper",
        "authors": seed.get("authors") or ["Unknown"],
        "year": seed_year,
        "abstract": str(seed.get("abstract") or ""),
        "venue": str(seed.get("venue") or "user_seed"),
        "citation_count": int(seed.get("citation_count") or 0),
        "doi": str(seed.get("doi") or ""),
        "url": url,
        "externalIds": {"ArXiv": arxiv_id} if arxiv_id else {},
        "source_type": "preprint",
        "relevance_score": 1.0,
        "why_relevant": str(seed.get("why_relevant") or "用户提供的高优先级 seed paper"),
        "provenance": {
            "source_tool": "user_seed",
            "source_id": paper_id,
            "source_url": url,
            "canonical_id": canonical_id,
            "id_source": canonical_id_source,
        },
    }
    for key in (
        "title_source",
        "title_confidence",
        "metadata_review_required",
        "title_repair_reason",
        "seed_pdf_path",
        "pdf_path",
        "has_seed_pdf",
        "has_local_pdf",
        "access_level_hint",
        "access_score",
    ):
        if key in seed and seed[key] not in (None, ""):
            recovered[key] = seed[key]
    return recovered


def _ensure_seed_papers(
    selected_papers: list[dict[str, Any]],
    candidate_papers: list[dict[str, Any]],
    workspace_dir: Path,
) -> list[dict[str, Any]]:
    """确保恢复路径不会丢掉用户 seed papers。"""

    seeds = _load_seed_papers(workspace_dir)
    if not seeds:
        return selected_papers

    selected = list(selected_papers)
    selected_title_keys = {_normalize_match_key(paper.get("title")) for paper in selected}
    candidates_by_title = {
        _normalize_match_key(paper.get("title")): paper
        for paper in candidate_papers
        if str(paper.get("title", "")).strip()
    }

    for seed in seeds:
        seed_key = _normalize_match_key(seed.get("title"))
        if not seed_key or seed_key in selected_title_keys:
            continue
        recovered = dict(candidates_by_title.get(seed_key) or _seed_to_recovery_paper(seed))
        recovered["relevance_score"] = max(float(recovered.get("relevance_score", 0.0)), 1.0)
        recovered["why_relevant"] = str(
            recovered.get("why_relevant") or seed.get("why_relevant") or "用户提供的高优先级 seed paper"
        )
        selected.insert(0, recovered)
        selected_title_keys.add(seed_key)

    # Seed repair must not become a hidden pool cap. T2 queue construction is
    # responsible for deep-read limits; verified overflow still feeds
    # shallow abstract sweep, citation diagnostics, and resume decisions.
    return selected


def _build_recovered_verified_papers(
    papers: list[dict[str, Any]],
    workspace_dir: Path,
) -> list[dict[str, Any]]:
    """基于已落盘来源 metadata 生成恢复用 verified 池。

    恢复路径不额外访问外部 API；它只把已经带有 DOI/arXiv/source provenance
    的真实检索记录标为 source metadata verified，供 T3 继续消费可追溯记录。
    """

    local_pdf_dir = workspace_dir / "literature" / "pdfs"
    verified: list[dict[str, Any]] = []
    for paper in papers:
        title = str(paper.get("title") or "").strip()
        raw_id = str(paper.get("id") or "").strip()
        raw_canonical_id = str(paper.get("canonical_id") or "").strip()
        canonical_id = (
            raw_canonical_id if raw_canonical_id and raw_canonical_id != title
            else raw_id if raw_id and raw_id != title
            else stable_noopenalex_id(paper)
        )
        if not canonical_id:
            continue
        normalized_id = canonical_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        has_local_pdf = bool(normalized_id and (local_pdf_dir / f"{normalized_id}.pdf").exists())
        record = dict(paper)
        record["canonical_id"] = canonical_id
        record.setdefault("preferred_id_source", "source_id")
        record["verification_status"] = "pdf_verified" if has_local_pdf else "metadata_verified"
        record["verification_method"] = "recovered_source_metadata"
        record["verification_source"] = str(
            (record.get("provenance") or {}).get("source_tool") or record.get("source") or "unknown"
        )
        record["verification_confidence"] = 0.9 if has_local_pdf else 0.72
        record["verification_title_similarity"] = 1.0
        record["verification_year_match"] = True
        verified.append(record)
    return verified


def _build_recovered_citation_edges(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build cheap citation-edge hints from already persisted metadata.

    Recovery/finalize deliberately avoids extra network calls. When raw records
    already contain referenced_works/related_works, we preserve them; otherwise
    the domain map still records buckets and emits a warning.
    """

    payload: list[dict[str, Any]] = []
    for paper in papers:
        source_id = str(paper.get("canonical_id") or paper.get("id") or "").strip()
        if not source_id:
            continue
        refs = paper.get("referenced_works") or paper.get("references") or []
        related = paper.get("related_works") or paper.get("related") or []
        snowball_source_ids: list[str] = []
        for raw_source_id in [
            paper.get("citation_snowball_source_id"),
            *(paper.get("citation_snowball_source_ids") if isinstance(paper.get("citation_snowball_source_ids"), list) else []),
        ]:
            snowball_source_id = str(raw_source_id or "").strip()
            if snowball_source_id and snowball_source_id not in snowball_source_ids:
                snowball_source_ids.append(snowball_source_id)
        for snowball_source_id in snowball_source_ids:
            payload.append(
                {
                    "source_id": snowball_source_id,
                    "referenced_works": [source_id],
                    "related_works": [],
                    "source": "crossref_snowball_backfill",
                    "edge_semantics": "source_paper_references_snowball_candidate",
                }
            )
        if not refs and not related:
            continue
        payload.append(
            {
                "source_id": source_id,
                "referenced_works": refs,
                "related_works": related,
                "source": "recovered_existing_metadata",
            }
        )
    return payload


def _merge_citation_edge_payload(existing: list[Any], recovered: list[dict[str, Any]]) -> list[Any]:
    """Merge live citation edges with recovered metadata edges without losing direction."""

    merged: list[Any] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_objects: set[str] = set()

    def _add(item: Any) -> None:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            left, right = str(item[0] or "").strip(), str(item[1] or "").strip()
            if not left or not right or left == right:
                return
            key = (left, right)
            if key in seen_pairs:
                return
            seen_pairs.add(key)
            merged.append([left, right])
            return
        if isinstance(item, dict):
            source = str(item.get("source_id") or item.get("source") or item.get("paper_id") or item.get("id") or "").strip()
            targets: list[str] = []
            for field in ("referenced_works", "related_works", "references", "related"):
                value = item.get(field)
                if isinstance(value, list):
                    for target in value:
                        if isinstance(target, dict):
                            target_id = str(target.get("canonical_id") or target.get("paper_id") or target.get("id") or target.get("doi") or target.get("title") or "").strip()
                        else:
                            target_id = str(target or "").strip()
                        if target_id:
                            targets.append(target_id)
            if source and targets:
                object_key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if object_key not in seen_objects:
                    seen_objects.add(object_key)
                    merged.append(item)
                for target in targets:
                    if target and target != source:
                        seen_pairs.add((source, target))
                return
            object_key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if object_key not in seen_objects:
                seen_objects.add(object_key)
                merged.append(item)
            return

    for payload in (existing, recovered):
        for item in payload:
            _add(item)
    return merged


def _extract_existing_semantic_screenings(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover Scout LLM semantic_screen fields already persisted in raw/dedup records."""

    screenings: list[dict[str, Any]] = []
    for paper in papers:
        screen = paper.get("semantic_screen")
        if not isinstance(screen, dict):
            continue
        screening = dict(screen)
        for key in ("paper_id", "id", "canonical_id", "doi", "title"):
            value = paper.get(key)
            if value not in (None, ""):
                screening.setdefault(key, value)
        screenings.append(screening)
    return screenings


def _iter_t2_trace_paths(workspace_dir: Path) -> list[Path]:
    trace_dir = workspace_dir / "_runtime" / "traces"
    if not trace_dir.exists():
        return []
    return sorted(trace_dir.glob("*.jsonl"))


def extract_t2_search_history(trace_paths: list[Path]) -> tuple[list[str], dict[str, int], int, list[dict[str, Any]]]:
    """从 trace 中恢复检索式和结构化 provenance。

    旧版只返回 query/count，会丢失 bridge_id、query_bucket 和 source/tool。
    这里保留旧的 queries/query_results 兼容字段，同时返回 search_records
    供 search_log 展示 bridge/source 覆盖。
    """

    ordered_queries: list[str] = []
    query_results: dict[str, int] = {}
    search_records: list[dict[str, Any]] = []
    parsed_traces = 0

    for trace_path in trace_paths:
        if not trace_path.exists():
            continue
        is_t2_trace = trace_path.stem.lower().startswith("t2")
        pending_queries: dict[str, dict[str, Any]] = {}
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "run_start":
                payload = event.get("payload", {})
                is_t2_trace = payload.get("task_id") == "T2" or is_t2_trace
                if is_t2_trace:
                    parsed_traces += 1
                continue
            if not is_t2_trace:
                continue
            if event.get("type") != "message":
                continue

            payload = event.get("payload", {})
            role = payload.get("role")
            if role == "assistant":
                for tool_call in payload.get("tool_calls") or []:
                    tool_name = tool_call.get("name")
                    if tool_name not in SEARCH_TOOL_NAMES:
                        continue
                    arguments = tool_call.get("arguments") or {}
                    query = str(arguments.get("query", "")).strip()
                    if not query and tool_name == "fetch_outgoing_citations":
                        identifier = str(arguments.get("openalex_id_or_doi") or "").strip()
                        if identifier:
                            query = f"citation:{identifier}"
                    pending_queries[str(tool_call.get("id", ""))] = {
                        "query": query,
                        "tool_name": tool_name,
                        "query_bucket": str(
                            arguments.get("query_bucket") or arguments.get("search_bucket") or ""
                        ).strip(),
                        "bridge_id": str(arguments.get("bridge_id") or "").strip(),
                    }
                continue

            if role != "tool" or payload.get("name") not in SEARCH_TOOL_NAMES:
                continue

            metadata = payload.get("metadata") or {}
            if metadata.get("is_error"):
                continue
            data = metadata.get("data") or {}
            papers = data.get("papers") or []
            count = len(papers) if isinstance(papers, list) else 0
            tool_call_id = str(payload.get("tool_call_id", ""))
            pending = pending_queries.get(tool_call_id, {})
            query = str(pending.get("query") or data.get("query") or "").strip()
            if not query and payload.get("name") == "fetch_outgoing_citations":
                identifier = str(data.get("source_id") or data.get("openalex_id_or_doi") or "").strip()
                if identifier:
                    query = f"citation:{identifier}"
            if not query:
                continue
            if query not in query_results:
                ordered_queries.append(query)
                query_results[query] = 0
            query_results[query] += count
            auto_persist = metadata.get("auto_persist_raw") if isinstance(metadata, dict) else {}
            if not isinstance(auto_persist, dict):
                auto_persist = {}
            persisted_count = int(
                auto_persist.get("retained_count")
                or auto_persist.get("count")
                or 0
            )
            search_records.append(
                {
                    "query": query,
                    "tool_name": payload.get("name") or pending.get("tool_name") or "",
                    "query_bucket": pending.get("query_bucket") or data.get("query_bucket") or data.get("search_bucket") or "",
                    "bridge_id": pending.get("bridge_id") or data.get("bridge_id") or "",
                    "result_count": count,
                    "persisted_count": persisted_count,
                    "source_stats": data.get("source_stats") if isinstance(data.get("source_stats"), dict) else {},
                }
            )

    return ordered_queries, query_results, parsed_traces, search_records


def _search_records_from_raw(raw_papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback search history from persisted raw provenance."""

    grouped: dict[tuple[str, str, str, str], int] = Counter()
    for paper in raw_papers:
        provenance = paper.get("provenance") if isinstance(paper.get("provenance"), dict) else {}
        queries = _raw_values(paper.get("source_queries")) or _raw_values(paper.get("source_query")) or _raw_values(provenance.get("source_query"))
        buckets = (
            _raw_values(paper.get("search_buckets"))
            or _raw_values(paper.get("query_buckets"))
            or _raw_values(paper.get("search_bucket") or paper.get("query_bucket"))
            or _raw_values(provenance.get("search_bucket") or provenance.get("query_bucket"))
        )
        bridge_ids = (
            _raw_values(paper.get("recalled_by_bridges"))
            or _raw_values(paper.get("bridge_ids"))
            or _raw_values(paper.get("bridge_id"))
            or _raw_values(provenance.get("bridge_id"))
        )
        tools = (
            _raw_values(paper.get("source_tools"))
            or _raw_values(paper.get("source_tool"))
            or _raw_values(provenance.get("source_tool"))
            or _raw_values(paper.get("source"))
        )
        if not queries and not buckets and not bridge_ids and not tools:
            continue
        max_len = max(len(queries), len(buckets), len(bridge_ids), len(tools), 1)
        for idx in range(max_len):
            query = queries[idx] if idx < len(queries) else queries[0] if queries else "[unknown query]"
            bucket = buckets[idx] if idx < len(buckets) else buckets[0] if buckets else ""
            bridge_id = _raw_bridge_value_at(bridge_ids, buckets, queries, idx)
            tool = tools[idx] if idx < len(tools) else tools[0] if tools else "unknown"
            grouped[(query or "[unknown query]", bucket, bridge_id, tool or "unknown")] += 1

    records: list[dict[str, Any]] = []
    for (query, bucket, bridge_id, tool), count in sorted(grouped.items()):
        records.append(
            {
                "query": query,
                "query_bucket": bucket,
                "bridge_id": bridge_id,
                "tool_name": tool,
                "result_count": count,
                "persisted_count": count,
                "source": "papers_raw_provenance",
            }
        )
    return records


def _raw_values(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_query_audit_key(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold()).split())


def _dedupe_query_list(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for query in queries:
        text = " ".join(str(query or "").split())
        if not text:
            continue
        key = _normalize_query_audit_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _dedupe_search_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        query = " ".join(str(record.get("query") or "").split())
        bucket = str(record.get("query_bucket") or record.get("search_bucket") or "").strip()
        bridge_id = str(record.get("bridge_id") or "").strip()
        tool = str(record.get("tool_name") or record.get("source_tool") or record.get("source") or "").strip()
        if not query and not bucket and not bridge_id and not tool:
            continue
        # Only keep bridge attribution for bridge-like query buckets. Provenance
        # lists from merged raw rows otherwise create false bridge/query pairs.
        if bucket not in {"theory_bridge", "adjacent_field", "snowball"}:
            bridge_id = ""
        key = (_normalize_query_audit_key(query), bucket, bridge_id, tool)
        if key not in grouped:
            item = dict(record)
            item["query"] = query or "[unknown query]"
            item["query_bucket"] = bucket
            item["bridge_id"] = bridge_id
            item["tool_name"] = tool
            item["duplicate_call_count"] = int(item.get("duplicate_call_count") or 1)
            grouped[key] = item
            order.append(key)
            continue
        existing = grouped[key]
        existing["duplicate_call_count"] = int(existing.get("duplicate_call_count") or 1) + int(record.get("duplicate_call_count") or 1)
        existing["result_count"] = int(existing.get("result_count") or existing.get("count") or 0) + int(record.get("result_count") or record.get("count") or 0)
        existing["persisted_count"] = int(existing.get("persisted_count") or 0) + int(record.get("persisted_count") or 0)
        source_stats = record.get("source_stats") if isinstance(record.get("source_stats"), dict) else {}
        if source_stats:
            merged_stats = existing.get("source_stats") if isinstance(existing.get("source_stats"), dict) else {}
            for source, value in source_stats.items():
                try:
                    merged_stats[source] = int(merged_stats.get(source) or 0) + int(value or 0)
                except (TypeError, ValueError):
                    merged_stats.setdefault(source, value)
            existing["source_stats"] = merged_stats
    return [grouped[key] for key in order]


def _raw_bridge_value_at(
    bridge_ids: list[str],
    buckets: list[str],
    queries: list[str],
    idx: int,
) -> str:
    if not bridge_ids:
        return ""
    if len(bridge_ids) == 1:
        bucket = buckets[idx] if idx < len(buckets) else buckets[0] if len(buckets) == 1 else ""
        if bucket in {"theory_bridge", "adjacent_field"}:
            return bridge_ids[0]
        if len(queries) <= 1 and len(buckets) <= 1:
            return bridge_ids[0]
        return ""
    # Merged raw records may carry several source_queries/search_buckets and a
    # separate set of recalled bridge ids. If list lengths no longer align, do
    # not guess the bridge for this row; a wrong bridge id in search_log is worse
    # than an unspecified one because it can drive repeated bridge queries.
    if len(bridge_ids) in {len(queries), len(buckets)} and idx < len(bridge_ids):
        return bridge_ids[idx]
    return ""


def generate_missing_areas_report(
    project: dict[str, Any],
    papers: list[dict[str, Any]],
    *,
    current_year: int | None = None,
) -> str:
    """基于关键词覆盖和分布特征生成确定性的缺口分析初稿。"""

    runtime_year = current_year if current_year is not None else current_utc_year()
    recent_start_year = recent_year_from(2, current_year=runtime_year)
    recent_label = format_year_window(2, current_year=runtime_year)
    research_direction = str(project.get("research_direction", "未指定")).strip() or "未指定"
    keywords = _normalize_keywords(project)
    keyword_counts: dict[str, int] = {}

    for keyword in keywords:
        aliases = _keyword_aliases(keyword)
        count = 0
        for paper in papers:
            text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
            if any(alias in text for alias in aliases):
                count += 1
        keyword_counts[keyword] = count

    recent_count = 0
    missing_abstract_count = 0
    source_counter: Counter[str] = Counter()
    year_counter: Counter[int] = Counter()
    for paper in papers:
        year = paper.get("year")
        if isinstance(year, int):
            year_counter[year] += 1
            if year >= recent_start_year:
                recent_count += 1
        if paper.get("_missing_abstract"):
            missing_abstract_count += 1
        source_counter[str(paper.get("source_type", "unknown"))] += 1

    total = len(papers)
    high_coverage_threshold = max(4, total // 12) if total else 4
    low_coverage_threshold = max(2, total // 20) if total else 2
    covered_keywords = [kw for kw, count in keyword_counts.items() if count >= high_coverage_threshold]
    missing_keywords = [kw for kw, count in keyword_counts.items() if count < low_coverage_threshold]

    retrieval_coverage_hints: list[str] = []
    source_type_review_count = source_counter.get("unknown", 0)
    if total and recent_count < max(5, total // 4):
        retrieval_coverage_hints.append(f"{recent_label} 的最新论文占比偏低，近期进展覆盖可能不足。")
    if total and source_type_review_count > total // 3:
        retrieval_coverage_hints.append("source_type 需要 LLM 复核的论文比例偏高，后续应补充领域 venue/profile 判断。")
    if total and missing_abstract_count > total // 3:
        retrieval_coverage_hints.append("缺少摘要的论文比例偏高，T3 精读前建议补齐关键 metadata。")

    lines = [
        "# 文献缺口分析",
        "",
        "> 本文件由 runtime 基于 `papers_dedup.jsonl` 自动生成，",
        "> 依据关键词覆盖、年份分布和来源分布做初步判断，可作为 T3/T4 的起点，",
        "> 不是人工精读后的最终结论。",
        "",
        "## 当前覆盖概况",
        "",
        f"- 研究方向: {research_direction}",
        f"- 去重后论文数: {total} 篇",
        f"- {recent_label} 最近论文: {recent_count} 篇",
        f"- source_type 待 LLM 复核: {source_type_review_count} 篇",
        "- 注：本文件只描述检索覆盖和 metadata 完整性，不宣称真实研究空白。",
        "",
        "## 覆盖较好的主题",
        "",
    ]

    if covered_keywords:
        for keyword in covered_keywords:
            lines.append(f"- `{keyword}`: {keyword_counts[keyword]} 篇论文显式提及")
    else:
        lines.append("- 当前还没有明显高覆盖的单一主题，说明论文池较分散。")

    lines.extend(["", "## 覆盖不足的主题", ""])
    if missing_keywords:
        for keyword in missing_keywords:
            lines.append(f"- `{keyword}`: 仅 {keyword_counts[keyword]} 篇论文显式提及，建议继续补检")
    else:
        lines.append("- 当前项目关键词都至少获得了基础覆盖，但仍建议人工检查是否存在语义漏网项。")

    lines.extend(["", "## Retrieval Coverage Hints", ""])
    if retrieval_coverage_hints:
        lines.extend(f"- {item}" for item in retrieval_coverage_hints)
    else:
        lines.append("- 当前去重论文池在年份和 metadata 完整性上没有明显覆盖提示。")

    # --- 检索覆盖提示（结构化，供 T3/T4 复核，不是研究缺口结论） ---
    gap_entries: list[dict[str, str]] = []
    gap_counter = 0

    # 从低覆盖关键词生成补检/复核提示
    for keyword in missing_keywords:
        gap_counter += 1
        count = keyword_counts[keyword]
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": f"`{keyword}` 相关检索覆盖不足",
            "what": f"在 {total} 篇去重论文中，仅 {count} 篇显式提及 `{keyword}`，远低于高覆盖阈值 {high_coverage_threshold}。",
            "why": "这是检索覆盖提示，不等于真实研究缺口；需要 Reader/Ideation LLM 基于精读材料确认是否有科学问题。",
            "direction": f"围绕 `{keyword}` 设计补检 query，或在 T3 精读时记录该主题是否实际出现。",
            "difficulty": "Medium",
        })

    # 从结构性覆盖问题生成补检/复核提示
    if total and recent_count < max(5, total // 4):
        gap_counter += 1
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": f"{recent_label} 最新论文覆盖不足",
            "what": f"{recent_label} 论文仅 {recent_count} 篇（占比 {recent_count / max(1, total) * 100:.0f}%），最新进展覆盖可能不足。",
            "why": "这是时间覆盖提示，不等于近期一定存在未覆盖突破。",
            "direction": f"针对 {recent_label} 做一轮专题补检，或由 LLM 判断当前领域是否确实需要近期补搜。",
            "difficulty": "Low",
        })
    if total and source_type_review_count > total // 3:
        gap_counter += 1
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": "source_type 复核不足",
            "what": f"有 {source_type_review_count} 篇论文的 source_type 为 unknown 或需要 LLM 复核。",
            "why": "source_type 属于领域 profile 判断，不能由 runtime 仅凭 venue 名称替代。",
            "direction": "由 Scout/Reader LLM 基于 domain_profile 标注相关 venue/source_type，必要时补搜目标领域代表 venue。",
            "difficulty": "Medium",
        })
    if total and missing_abstract_count > total // 3:
        gap_counter += 1
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": "摘要缺失论文比例偏高",
            "what": f"有 {missing_abstract_count} 篇论文（占比 {missing_abstract_count / max(1, total) * 100:.0f}%）缺少摘要，无法进行内容级分析。",
            "why": "缺少摘要的论文无法参与关键词覆盖分析和 abstract sweep，可能导致覆盖评估偏差。",
            "direction": "对缺失摘要的关键论文手动补充 metadata，或在 T3 精读时优先处理这些论文。",
            "difficulty": "Low",
        })

    # 从覆盖过度集中生成补检/复核提示
    if covered_keywords and len(covered_keywords) >= 3:
        # 检查覆盖是否过于集中在少数关键词
        top_keyword = max(keyword_counts.items(), key=lambda x: x[1])
        if top_keyword[1] > max(10, total // 3):
            gap_counter += 1
            gap_entries.append({
                "id": f"提示 {gap_counter}",
                "title": f"检索视角过于集中在 `{top_keyword[0]}`",
                "what": f"`{top_keyword[0]}` 有 {top_keyword[1]} 篇论文，占论文池的 {top_keyword[1] / max(1, total) * 100:.0f}%，其余主题覆盖稀疏。",
                "why": "检索视角过度集中可能导致 Reader 看到的证据范围较窄，但是否构成研究机会需要 LLM 判断。",
                "direction": "让 LLM 基于 domain_profile 判断是否需要相邻领域、替代术语或不同评估场景的补检。",
                "difficulty": "Low",
            })

    if gap_entries:
        lines.extend(["", "## Retrieval Coverage Hints（不是研究缺口结论）", ""])
        lines.append("> 以下提示由 runtime 基于关键词覆盖和分布特征自动生成，只能用于补检或让 T3/T4 复核；不能直接宣称领域空白。")
        lines.append("")
        for gap in gap_entries:
            lines.append(f"### {gap['id']}: {gap['title']}")
            lines.append(f"- **覆盖缺口**: {gap['what']}")
            lines.append(f"- **为什么需要复核**: {gap['why']}")
            lines.append(f"- **建议动作**: {gap['direction']}")
            lines.append(f"- **难度**: {gap['difficulty']}")
            lines.append("")

    lines.extend(["", "## 建议在 T3/T4 继续确认的问题", ""])
    follow_ups = []
    if missing_keywords:
        follow_ups.append(f"优先围绕 {', '.join(f'`{item}`' for item in missing_keywords[:3])} 继续补检或在精读时标注缺口。")
    if recent_count < max(5, total // 4) and total:
        follow_ups.append(f"重点确认 {recent_label} 的最新工作，避免只依赖旧综述或早期系统。")
    if source_type_review_count > total // 3 and total:
        follow_ups.append("让 LLM 基于 domain_profile 复核 source_type/venue，而不是依赖 runtime 自动判断。")
    if not follow_ups:
        follow_ups.append("按论文笔记进一步确认：哪些机制被反复验证，哪些只停留在概念或系统描述。")
    lines.extend(f"- {item}" for item in follow_ups)

    if year_counter:
        lines.extend(["", "## 年份分布（Top 5）", ""])
        for year, count in year_counter.most_common(5):
            lines.append(f"- {year}: {count} 篇")

    return "\n".join(lines) + "\n"


async def finalize_t2_outputs(
    workspace_dir: Path,
    *,
    trace_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """根据现有 raw 结果，确定性补齐 T2 产物。"""

    workspace_dir = workspace_dir.resolve()
    t2_config = load_t2_finalize_config(workspace_dir)
    queue_config = load_deep_read_queue_config(workspace_dir)
    literature_quality_policy = load_literature_quality_policy(workspace_dir)
    raw_path = workspace_dir / "literature" / "papers_raw.jsonl"
    raw_papers = _load_jsonl(raw_path)
    if not raw_papers:
        if t2_config.progress_update_on_finalize:
            _log_t2_progress(
                workspace_dir,
                t2_config,
                "finalize_failed",
                reason="papers_raw_missing_or_empty",
                raw_count=0,
            )
        return {
            "ok": False,
            "reason": "papers_raw_missing_or_empty",
            "raw_count": 0,
        }
    if t2_config.progress_update_on_finalize:
        _log_t2_progress(
            workspace_dir,
            t2_config,
            "finalize_started",
            raw_count=len(raw_papers),
            active_pool_max=t2_config.active_pool_max,
            deep_read_target=queue_config.deep_read_target,
            deep_read_max=queue_config.deep_read_max,
        )

    project = _load_project(workspace_dir)
    keywords = _normalize_keywords(project)
    domain_profile = _project_domain_profile(project)
    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=["", "literature/", "user_seeds/", "seeds/"],
        allowed_write_prefixes=["literature/", "literature/temp/"],
    )

    dedup_papers = deduplicate_papers(
        raw_papers,
        doi_dedup=True,
        title_threshold=t2_config.dedup_title_threshold,
    )
    domain_filtered_backlog: list[dict[str, Any]] = []
    if domain_profile:
        pre_domain_filter_papers = dedup_papers
        dedup_papers = filter_by_domain(
            dedup_papers,
            target_domain=str(domain_profile.get("target_domain") or domain_profile.get("domain") or "profile"),
            domain_profile=domain_profile,
        )
        kept_keys: set[str] = set()
        for paper in dedup_papers:
            kept_keys.update(paper_record_match_keys(paper))
            title_key = normalize_loose_identity_key(_paper_title_text(paper.get("title")))
            if title_key:
                kept_keys.add(f"title:{title_key}")
        for paper in pre_domain_filter_papers:
            keys = set(paper_record_match_keys(paper))
            title_key = normalize_loose_identity_key(_paper_title_text(paper.get("title")))
            if title_key:
                keys.add(f"title:{title_key}")
            if keys and keys & kept_keys:
                continue
            domain_filtered_backlog.append(_domain_filter_backlog_record(paper, domain_profile))
    # Seed records may be absent from papers_raw or may only contain a local
    # PDF/title. Insert them before deterministic metadata repair so DOI/arXiv
    # and title-based backfill can improve seed abstracts instead of leaving
    # the highest-priority papers with the weakest metadata.
    dedup_papers = _ensure_seed_papers(dedup_papers, dedup_papers + raw_papers, workspace_dir)
    dedup_papers, quality_filtered_backlog, literature_quality_meta = apply_literature_quality_policy(
        dedup_papers,
        literature_quality_policy,
        workspace_dir=workspace_dir,
    )
    quality_filtered_backlog = [_quality_policy_backlog_record(item) for item in quality_filtered_backlog]

    pre_active_scored_papers = score_papers(dedup_papers, keywords)
    pre_active_scored_papers = sorted(
        pre_active_scored_papers,
        key=lambda paper: (
            float(paper.get("relevance_score", 0.0)),
            int(paper.get("citation_count", 0) or 0),
            int(paper.get("year", 0) or 0),
        ),
        reverse=True,
    )
    pre_active_light_backfill = await _pre_active_light_backfill(
        pre_active_scored_papers,
        policy,
        t2_config,
    )
    pre_active_scored_papers = score_papers(pre_active_scored_papers, keywords)
    provisional_scored_papers = sorted(
        pre_active_scored_papers,
        key=lambda paper: (
            float(paper.get("relevance_score", 0.0)),
            int(paper.get("citation_count", 0) or 0),
            int(paper.get("year", 0) or 0),
        ),
        reverse=True,
    )
    dedup_papers, pre_backfill_backlog_papers, active_pool_meta = _select_active_candidate_pool(
        provisional_scored_papers,
        workspace_dir,
        config=t2_config,
    )
    if t2_config.progress_update_on_finalize:
        _log_t2_progress(
            workspace_dir,
            t2_config,
            "active_pool_pre_backfill",
            input_count=active_pool_meta.get("input_count"),
            active_count=active_pool_meta.get("active_count"),
            backlog_count=active_pool_meta.get("backlog_count"),
            active_pool_max=active_pool_meta.get("active_pool_max"),
        )

    openalex_title_backfill = await _backfill_recovered_openalex_title_metadata(
        dedup_papers,
        max_concurrency=t2_config.metadata_backfill_max_concurrency,
    )
    openalex_backfill = await _backfill_recovered_openalex_metadata(
        dedup_papers,
        max_concurrency=t2_config.metadata_backfill_max_concurrency,
    )
    multisource_abstract_backfill = await _backfill_recovered_multisource_abstracts(
        dedup_papers,
        policy,
        title_match_threshold=t2_config.abstract_backfill_title_match_threshold,
        max_concurrency=t2_config.abstract_backfill_max_concurrency,
    )
    metadata_backfill = await _backfill_recovered_crossref_metadata(
        dedup_papers,
        max_concurrency=t2_config.metadata_backfill_max_concurrency,
    )
    raw_papers_for_snowball_dedup = _load_jsonl(raw_path)
    openalex_snowball_candidates, openalex_citation_backfill = await _expand_openalex_snowball_candidates(
        dedup_papers,
        existing_papers=raw_papers_for_snowball_dedup,
        max_sources=t2_config.snowball_max_sources,
        refs_per_source=t2_config.snowball_refs_per_source,
        max_candidates=t2_config.snowball_max_candidates,
        max_concurrency=t2_config.snowball_max_concurrency,
    )
    openalex_snowball_attempted = int(openalex_citation_backfill.get("attempted") or len(openalex_snowball_candidates))
    remaining_snowball_cap = max(0, int(t2_config.snowball_max_candidates) - openalex_snowball_attempted)
    if remaining_snowball_cap > 0:
        crossref_snowball_candidates, citation_backfill = await _expand_crossref_snowball_candidates(
            dedup_papers,
            existing_papers=[*raw_papers_for_snowball_dedup, *openalex_snowball_candidates],
            max_sources=t2_config.snowball_max_sources,
            refs_per_source=t2_config.snowball_refs_per_source,
            max_candidates=remaining_snowball_cap,
            max_concurrency=t2_config.snowball_max_concurrency,
            title_match_threshold=t2_config.snowball_title_match_threshold,
        )
    else:
        crossref_snowball_candidates = []
        citation_backfill = {
            "enabled": True,
            "source_candidates": 0,
            "sources_used": 0,
            "reference_items_seen": 0,
            "reference_dois_seen": 0,
            "reference_titles_seen": 0,
            "title_references_resolved": 0,
            "non_doi_references_skipped": 0,
            "skipped_by_global_snowball_cap": True,
            "global_snowball_cap": t2_config.snowball_max_candidates,
            "attempted": 0,
            "added": 0,
            "failed": 0,
        }
    snowball_candidates = [*openalex_snowball_candidates, *crossref_snowball_candidates]
    pre_snowball_dedup_papers = list(dedup_papers)
    await _persist_snowball_candidates(
        policy,
        openalex_snowball_candidates,
        openalex_citation_backfill,
    )
    await _persist_snowball_candidates(
        policy,
        crossref_snowball_candidates,
        citation_backfill,
    )
    if snowball_candidates:
        raw_papers = _load_jsonl(raw_path)
        dedup_papers = deduplicate_papers(
            [*dedup_papers, *snowball_candidates],
            doi_dedup=True,
            title_threshold=t2_config.dedup_title_threshold,
        )
        if domain_filtered_backlog:
            dedup_papers = _filter_records_by_identity(dedup_papers, domain_filtered_backlog)
        if snowball_candidates:
            # Newly resolved snowball records may have DOI/title only; run the
            # same mechanical repair once more before scoring/verification.
            post_snowball_title_backfill = await _backfill_recovered_openalex_title_metadata(
                dedup_papers,
                max_concurrency=t2_config.metadata_backfill_max_concurrency,
            )
            post_snowball_openalex_backfill = await _backfill_recovered_openalex_metadata(
                dedup_papers,
                max_concurrency=t2_config.metadata_backfill_max_concurrency,
            )
            post_snowball_abstract_backfill = await _backfill_recovered_multisource_abstracts(
                dedup_papers,
                policy,
                title_match_threshold=t2_config.abstract_backfill_title_match_threshold,
                max_concurrency=t2_config.abstract_backfill_max_concurrency,
            )
            post_snowball_crossref_backfill = await _backfill_recovered_crossref_metadata(
                dedup_papers,
                max_concurrency=t2_config.metadata_backfill_max_concurrency,
            )
        else:
            post_snowball_title_backfill = {"enabled": True, "candidate_count": 0, "attempted": 0}
            post_snowball_openalex_backfill = {"enabled": True, "candidate_count": 0, "attempted": 0}
            post_snowball_abstract_backfill = {"enabled": True, "candidate_count": 0, "filled": 0}
            post_snowball_crossref_backfill = {"enabled": True, "candidate_count": 0, "attempted": 0}
    else:
        post_snowball_title_backfill = {"enabled": True, "candidate_count": 0, "attempted": 0}
        post_snowball_openalex_backfill = {"enabled": True, "candidate_count": 0, "attempted": 0}
        post_snowball_abstract_backfill = {"enabled": True, "candidate_count": 0, "filled": 0}
        post_snowball_crossref_backfill = {"enabled": True, "candidate_count": 0, "attempted": 0}

    if snowball_candidates:
        candidate_keys = _candidate_pool_identity_keys(snowball_candidates)
        pre_snowball_keys = _candidate_pool_identity_keys(pre_snowball_dedup_papers)
        snowball_records_after_dedup = [
            paper
            for paper in dedup_papers
            if (_candidate_record_identity_keys(paper) & candidate_keys)
            and not (_candidate_record_identity_keys(paper) & pre_snowball_keys)
        ]
        non_snowball_records_after_dedup = [
            paper
            for paper in dedup_papers
            if not ((_candidate_record_identity_keys(paper) & candidate_keys) and not (_candidate_record_identity_keys(paper) & pre_snowball_keys))
        ]
        kept_snowball_records, post_quality_filtered, post_literature_quality_meta = apply_literature_quality_policy(
            snowball_records_after_dedup,
            literature_quality_policy,
            workspace_dir=workspace_dir,
        )
        literature_quality_meta = _merge_literature_quality_meta(
            literature_quality_meta,
            post_literature_quality_meta,
            stage_name="post_snowball",
        )
        quality_filtered_backlog.extend(_quality_policy_backlog_record(item) for item in post_quality_filtered)
        dedup_papers = deduplicate_papers(
            [*pre_snowball_dedup_papers, *non_snowball_records_after_dedup, *kept_snowball_records],
            doi_dedup=True,
            title_threshold=t2_config.dedup_title_threshold,
        )

    scored_papers = score_papers(dedup_papers, keywords)
    # Sort for deterministic queue priority only. `relevance_score` is a
    # metadata priority hint and is not used as an exclusion threshold.
    scored_papers = sorted(
        [*scored_papers, *pre_backfill_backlog_papers],
        key=lambda paper: (
            float(paper.get("relevance_score", 0.0)),
            int(paper.get("citation_count", 0) or 0),
            int(paper.get("year", 0) or 0),
        ),
        reverse=True,
    )
    final_papers, backlog_papers, active_pool_meta = _select_active_candidate_pool(
        scored_papers,
        workspace_dir,
        config=t2_config,
    )
    active_pool_meta["pre_backfill_active_count"] = len(dedup_papers)
    active_pool_meta["pre_backfill_backlog_count"] = len(pre_backfill_backlog_papers)
    active_pool_meta["domain_profile_filtered_count"] = len(domain_filtered_backlog)
    active_pool_meta["literature_quality_filtered_count"] = len(quality_filtered_backlog)
    final_papers = _ensure_seed_papers(final_papers, scored_papers + raw_papers, workspace_dir)
    final_papers = _filter_records_by_identity(final_papers, domain_filtered_backlog)
    final_papers = deduplicate_papers(
        final_papers,
        doi_dedup=True,
        title_threshold=t2_config.dedup_title_threshold,
    )
    backlog_papers = [*backlog_papers, *domain_filtered_backlog, *quality_filtered_backlog]
    final_papers, backlog_papers, active_pool_meta = _cap_active_pool_after_seed_repair(
        final_papers,
        backlog_papers,
        workspace_dir,
        active_pool_meta,
        max_count=t2_config.active_pool_max,
    )
    raw_screenings = _extract_existing_semantic_screenings(raw_papers + dedup_papers + final_papers)
    enriched_papers = enrich_papers(final_papers, keywords, domain_profile=domain_profile)
    if raw_screenings:
        enriched_papers = apply_semantic_screening(enriched_papers, raw_screenings)

    backlog_papers = enrich_papers(backlog_papers, keywords, domain_profile=domain_profile) if backlog_papers else []
    if raw_screenings and backlog_papers:
        backlog_papers = apply_semantic_screening(backlog_papers, raw_screenings)
    backlog_papers = _ensure_paper_schema_defaults_many(backlog_papers)
    backlog_path = workspace_dir / "literature" / "papers_backlog.jsonl"
    _write_jsonl(backlog_path, backlog_papers)
    active_pool_meta["active_count"] = len(enriched_papers)
    active_pool_meta["backlog_count"] = len(backlog_papers)
    if t2_config.progress_update_on_finalize:
        _log_t2_progress(
            workspace_dir,
            t2_config,
            "active_pool_final",
            input_count=active_pool_meta.get("input_count"),
            active_count=active_pool_meta.get("active_count"),
            backlog_count=active_pool_meta.get("backlog_count"),
            active_pool_max=active_pool_meta.get("active_pool_max"),
            selection_reasons=json.dumps(active_pool_meta.get("selection_reasons") or {}, ensure_ascii=False, sort_keys=True),
        )

    save_result = await SavePapersDedupTool(policy).execute(papers=enriched_papers, append=False)
    if not save_result.ok:
        if t2_config.progress_update_on_finalize:
            _log_t2_progress(
                workspace_dir,
                t2_config,
                "finalize_failed",
                reason="save_papers_dedup_failed",
                raw_count=len(raw_papers),
                active_count=len(enriched_papers),
            )
        return {
            "ok": False,
            "reason": "save_papers_dedup_failed",
            "error": save_result.error or save_result.content,
            "raw_count": len(raw_papers),
        }

    verified_papers = _build_recovered_verified_papers(enriched_papers, workspace_dir)
    verified_path = workspace_dir / "literature" / "papers_verified.jsonl"
    failures_path = workspace_dir / "literature" / "verification_failures.jsonl"
    _write_jsonl(verified_path, verified_papers)
    _write_jsonl(failures_path, [])
    raw_cache_merge = _merge_enriched_records_back_to_raw(raw_path, enriched_papers)
    raw_papers = _load_jsonl(raw_path)

    recovered_citation_edges = _build_recovered_citation_edges(verified_papers)
    citation_edges_path = workspace_dir / "literature" / "citation_edges.json"
    existing_citation_edges: list[Any] = []
    if citation_edges_path.exists():
        try:
            loaded_edges = json.loads(citation_edges_path.read_text(encoding="utf-8"))
            if isinstance(loaded_edges, list):
                existing_citation_edges = loaded_edges
        except Exception:
            existing_citation_edges = []
    citation_edges = _merge_citation_edge_payload(existing_citation_edges, recovered_citation_edges)
    citation_edges_path.write_text(
        json.dumps(citation_edges, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    domain_map = build_domain_map(
        papers_verified=verified_papers,
        citation_edges=citation_edges,
    )
    domain_map_path = workspace_dir / "literature" / "domain_map.json"
    domain_map_path.write_text(
        json.dumps(domain_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    queue_records, queue_meta = build_deep_read_queue(
        verified_papers,
        workspace_dir,
        deep_read_min=queue_config.deep_read_min,
        deep_read_target=queue_config.deep_read_target,
        deep_read_max=queue_config.deep_read_max,
        probe_pool=queue_config.probe_pool,
        mainline_screened_cap=queue_config.mainline_screened_cap,
        bridge_deep_floor=queue_config.bridge_deep_floor,
        bridge_screened_cap=queue_config.bridge_screened_cap,
        bridge_pool_cap=queue_config.bridge_pool_cap,
        citation_hub_slots=queue_config.citation_hub_slots,
    )
    queue_path = workspace_dir / "literature" / "deep_read_queue.jsonl"
    queue_meta_path = workspace_dir / "literature" / "deep_read_queue_meta.json"
    _write_jsonl(queue_path, queue_records)
    queue_meta_path.write_text(
        json.dumps(queue_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audit_records, audit_markdown = build_access_audit(
        verified_papers,
        workspace_dir,
        top_n=t2_config.access_audit_top_n,
    )
    access_audit_path = workspace_dir / "literature" / "access_audit.md"
    access_audit_jsonl_path = workspace_dir / "literature" / "access_audit.jsonl"
    _write_jsonl(access_audit_jsonl_path, audit_records)
    access_audit_path.write_text(audit_markdown, encoding="utf-8")

    history_paths = trace_paths if trace_paths is not None else _iter_t2_trace_paths(workspace_dir)
    queries, query_results, trace_count, search_records = extract_t2_search_history(history_paths)
    raw_search_records = _search_records_from_raw(raw_papers)
    if not search_records:
        search_records = raw_search_records
    else:
        record_index = {
            (
                str(item.get("query") or ""),
                str(item.get("query_bucket") or ""),
                str(item.get("bridge_id") or ""),
                str(item.get("tool_name") or item.get("source_tool") or ""),
            ): idx
            for idx, item in enumerate(search_records)
        }
        for item in raw_search_records:
            key = (
                str(item.get("query") or ""),
                str(item.get("query_bucket") or ""),
                str(item.get("bridge_id") or ""),
                str(item.get("tool_name") or item.get("source_tool") or ""),
            )
            existing_idx = record_index.get(key)
            if existing_idx is None:
                search_records.append(item)
                record_index[key] = len(search_records) - 1
                continue
            existing = search_records[existing_idx]
            raw_persisted = int(item.get("persisted_count") or 0)
            existing_persisted = int(existing.get("persisted_count") or 0)
            if raw_persisted > existing_persisted:
                existing["persisted_count"] = raw_persisted
            if not existing.get("source") and item.get("source"):
                existing["source"] = item.get("source")

    search_records = _dedupe_search_records(search_records)
    if not queries:
        queries = [str(item.get("query") or "") for item in search_records if str(item.get("query") or "").strip()]
    else:
        queries = _dedupe_query_list(queries)
    if not queries:
        queries = ["[Recovered] 原始 query 历史不可用"]
        query_results = None

    search_log = generate_search_log(
        raw_count=len(raw_papers),
        dedup_count=len(enriched_papers),
        queries=queries,
        query_results=query_results,
        search_records=search_records,
        bridge_plan=_load_bridge_domain_plan(workspace_dir),
    )
    search_log += "\n## 说明\n\n"
    search_log += "- 此文件由 runtime 基于当前 `papers_raw.jsonl` 和可解析的 T2 trace 自动重建。\n"
    search_log += f"- 解析到的 T2 trace 数量: {trace_count}\n"
    search_log += (
        "- T2 保留候选集: "
        f"input={active_pool_meta.get('input_count')}, "
        f"retained={active_pool_meta.get('active_count')}, "
        f"backlog={active_pool_meta.get('backlog_count')}, "
        f"max={active_pool_meta.get('active_pool_max')}, "
        f"selection_reasons={active_pool_meta.get('selection_reasons')}; "
        "`papers_raw.jsonl` 保留全量检索审计，`papers_dedup.jsonl`/`papers_verified.jsonl` "
        "只保存本轮保留候选集，超额、domain-profile 或 literature-quality 排除候选写入 "
        "`papers_backlog.jsonl` 供审计或人工/显式回捞，不会被普通 abstract sweep 自动读回。\n"
    )
    search_log += (
        "- 文献语言/来源质量策略: "
        f"enabled={literature_quality_meta.get('enabled')}, "
        f"manuscript_language={literature_quality_meta.get('manuscript_language')}, "
        f"include_chinese_literature={literature_quality_meta.get('include_chinese_literature')}, "
        f"input={literature_quality_meta.get('input_count')}, "
        f"kept={literature_quality_meta.get('kept_count')}, "
        f"filtered={literature_quality_meta.get('filtered_count')}, "
        f"reasons={literature_quality_meta.get('reason_counts')}。\n"
    )
    search_log += (
        "- T2/T3 阈值配置来源: "
        "`config/agent_params.yaml` 中 `agents.scout.behavior.t2_finalize` "
        "和 `agents.reader.modes.read.behavior`；"
        f"finish_finalize_min_raw={t2_config.finish_finalize_min_raw}, "
        f"active_pool_max={t2_config.active_pool_max}, "
        f"dedup_title_threshold={t2_config.dedup_title_threshold}, "
        f"pre_active_light_backfill_max={t2_config.pre_active_light_backfill_max}, "
        f"must_bridge_cap={t2_config.must_bridge_active_pool_cap_per_bridge}, "
        f"should_bridge_cap={t2_config.should_bridge_active_pool_cap_per_bridge}, "
        f"snowball_max_candidates={t2_config.snowball_max_candidates}, "
        f"deep_read_target={queue_config.deep_read_target}, "
        f"deep_read_max={queue_config.deep_read_max}, "
        f"mainline_screened_cap={queue_config.mainline_screened_cap}。\n"
    )
    search_log += (
        "- Active 切分前轻量补全: "
        f"enabled={pre_active_light_backfill.get('enabled')}, "
        f"input={pre_active_light_backfill.get('input_count')}, "
        f"candidate={pre_active_light_backfill.get('candidate_count')}, "
        f"skipped_by_cap={pre_active_light_backfill.get('skipped_by_cap')}, "
        f"abstract_after={pre_active_light_backfill.get('abstract_after')}, "
        f"pdf_hint_after={pre_active_light_backfill.get('pdf_hint_after')}, "
        f"reference_hint_after={pre_active_light_backfill.get('reference_hint_after')}\n"
    )
    search_log += (
        "- OpenAlex 标题兜底补全: "
        f"enabled={openalex_title_backfill.get('enabled')}, "
        f"eligible={openalex_title_backfill.get('eligible_count')}, "
        f"candidate={openalex_title_backfill.get('candidate_count')}, "
        f"attempted={openalex_title_backfill.get('attempted')}, "
        f"matched={openalex_title_backfill.get('matched')}, "
        f"doi_filled={openalex_title_backfill.get('doi_filled')}, "
        f"openalex_id_filled={openalex_title_backfill.get('openalex_id_filled')}, "
        f"abstract_filled={openalex_title_backfill.get('abstract_filled')}, "
        f"references_filled={openalex_title_backfill.get('references_filled')}, "
        f"pdf_hints_filled={openalex_title_backfill.get('pdf_hints_filled')}, "
        f"skipped_low_similarity={openalex_title_backfill.get('skipped_low_similarity')}, "
        f"failed={openalex_title_backfill.get('failed')}, "
        f"remaining_missing_abstract={openalex_title_backfill.get('remaining_missing_abstract')}, "
        f"remaining_missing_pdf_hints={openalex_title_backfill.get('remaining_missing_pdf_hints')}\n"
    )
    search_log += (
        "- OpenAlex DOI/OA 详情补全: "
        f"enabled={openalex_backfill.get('enabled')}, "
        f"eligible={openalex_backfill.get('eligible_count')}, "
        f"candidate={openalex_backfill.get('candidate_count')}, "
        f"attempted={openalex_backfill.get('attempted')}, "
        f"skipped_by_cap={openalex_backfill.get('skipped_by_cap')}, "
        f"openalex_id_filled={openalex_backfill.get('openalex_id_filled')}, "
        f"abstract_filled={openalex_backfill.get('abstract_filled')}, "
        f"references_filled={openalex_backfill.get('references_filled')}, "
        f"pdf_hints_filled={openalex_backfill.get('pdf_hints_filled')}, "
        f"failed={openalex_backfill.get('failed')}, "
        f"remaining_missing_abstract={openalex_backfill.get('remaining_missing_abstract')}, "
        f"remaining_missing_pdf_hints={openalex_backfill.get('remaining_missing_pdf_hints')}\n"
    )
    search_log += (
        "- 多源摘要回填: "
        f"enabled={multisource_abstract_backfill.get('enabled')}, "
        f"candidate={multisource_abstract_backfill.get('candidate_count')}, "
        f"attempted_single={multisource_abstract_backfill.get('attempted_single')}, "
        f"filled={multisource_abstract_backfill.get('filled')}, "
        f"remaining_missing_abstract={multisource_abstract_backfill.get('remaining_missing_abstract')}, "
        f"by_source={multisource_abstract_backfill.get('by_source')}\n"
    )
    search_log += (
        "- Crossref DOI 详情补全: "
        f"enabled={metadata_backfill.get('enabled')}, "
        f"eligible={metadata_backfill.get('eligible_count')}, "
        f"candidate={metadata_backfill.get('candidate_count')}, "
        f"attempted={metadata_backfill.get('attempted')}, "
        f"skipped_by_cap={metadata_backfill.get('skipped_by_cap')}, "
        f"abstract_filled={metadata_backfill.get('abstract_filled')}, "
        f"references_filled={metadata_backfill.get('references_filled')}, "
        f"failed={metadata_backfill.get('failed')}, "
        f"remaining_missing_abstract={metadata_backfill.get('remaining_missing_abstract')}, "
        f"remaining_missing_references={metadata_backfill.get('remaining_missing_references')}\n"
    )
    search_log += (
        "- OpenAlex citation snowball 补全: "
        f"enabled={openalex_citation_backfill.get('enabled')}, "
        f"sources_used={openalex_citation_backfill.get('sources_used')}, "
        f"reference_items_seen={openalex_citation_backfill.get('reference_items_seen')}, "
        f"reference_openalex_ids_seen={openalex_citation_backfill.get('reference_openalex_ids_seen')}, "
        f"related_openalex_ids_seen={openalex_citation_backfill.get('related_openalex_ids_seen')}, "
        f"non_openalex_references_skipped={openalex_citation_backfill.get('non_openalex_references_skipped')}, "
        f"skipped_by_refs_per_source_cap={openalex_citation_backfill.get('skipped_by_refs_per_source_cap')}, "
        f"skipped_by_max_candidates_cap={openalex_citation_backfill.get('skipped_by_max_candidates_cap')}, "
        f"attempted={openalex_citation_backfill.get('attempted')}, "
        f"added={openalex_citation_backfill.get('added')}, "
        f"raw_persisted_or_merged={openalex_citation_backfill.get('raw_persisted_or_merged')}, "
        f"failed={openalex_citation_backfill.get('failed')}\n"
    )
    search_log += (
        "- Crossref citation snowball 补全: "
        f"enabled={citation_backfill.get('enabled')}, "
        f"sources_used={citation_backfill.get('sources_used')}, "
        f"reference_items_seen={citation_backfill.get('reference_items_seen')}, "
        f"reference_dois_seen={citation_backfill.get('reference_dois_seen')}, "
        f"reference_titles_seen={citation_backfill.get('reference_titles_seen')}, "
        f"title_references_resolved={citation_backfill.get('title_references_resolved')}, "
        f"non_doi_references_skipped={citation_backfill.get('non_doi_references_skipped')}, "
        f"skipped_existing_or_duplicate_reference_titles={citation_backfill.get('skipped_existing_or_duplicate_reference_titles')}, "
        f"skipped_by_refs_per_source_cap={citation_backfill.get('skipped_by_refs_per_source_cap')}, "
        f"skipped_by_max_candidates_cap={citation_backfill.get('skipped_by_max_candidates_cap')}, "
        f"attempted={citation_backfill.get('attempted')}, "
        f"added={citation_backfill.get('added')}, "
        f"raw_persisted={citation_backfill.get('raw_persisted')}, "
        f"failed={citation_backfill.get('failed')}\n"
    )
    search_log += (
        "- Snowball 后二次补全: "
        f"title_attempted={post_snowball_title_backfill.get('attempted')}, "
        f"title_matched={post_snowball_title_backfill.get('matched')}, "
        f"openalex_attempted={post_snowball_openalex_backfill.get('attempted')}, "
        f"openalex_refs_filled={post_snowball_openalex_backfill.get('references_filled')}, "
        f"abstract_filled={post_snowball_abstract_backfill.get('filled')}, "
        f"crossref_attempted={post_snowball_crossref_backfill.get('attempted')}, "
        f"crossref_refs_filled={post_snowball_crossref_backfill.get('references_filled')}, "
        f"crossref_abstract_filled={post_snowball_crossref_backfill.get('abstract_filled')}\n"
    )
    search_log += (
        "- T2 raw 元数据缓存回写: "
        f"records_after={raw_cache_merge.get('raw_cache_records_after')}, "
        f"merged={raw_cache_merge.get('raw_cache_records_merged')}, "
        f"appended={raw_cache_merge.get('raw_cache_records_appended')}\n"
    )
    if query_results is None:
        search_log += "- 本次未能恢复可靠的 query 历史，因此只保留了总量统计。\n"

    search_log_path = workspace_dir / "literature" / "search_log.md"
    search_log_path.write_text(search_log, encoding="utf-8")

    missing_areas_path = workspace_dir / "literature" / "missing_areas.md"
    missing_areas_path.write_text(
        generate_missing_areas_report(project, enriched_papers),
        encoding="utf-8",
    )

    if t2_config.progress_update_on_finalize:
        _log_t2_progress(
            workspace_dir,
            t2_config,
            "finalize_done",
            raw_count=len(raw_papers),
            active_count=len(enriched_papers),
            backlog_count=len(backlog_papers),
            queue_count=len(queue_records),
            query_count=len(queries),
        )

    summary = {
        "ok": True,
        "raw_count": len(raw_papers),
        "dedup_count": len(enriched_papers),
        "backlog_count": len(backlog_papers),
        "active_pool": active_pool_meta,
        "t2_finalize_config": t2_config.to_dict(),
        "deep_read_queue_config": queue_config.to_dict(),
        "literature_quality_policy": literature_quality_policy.to_dict(),
        "literature_quality": literature_quality_meta,
        "query_count": len(queries),
        "trace_count": trace_count,
        "openalex_backfill": openalex_backfill,
        "pre_active_light_backfill": pre_active_light_backfill,
        "openalex_title_backfill": openalex_title_backfill,
        "multisource_abstract_backfill": multisource_abstract_backfill,
        "metadata_backfill": metadata_backfill,
        "openalex_citation_backfill": openalex_citation_backfill,
        "post_snowball_title_backfill": post_snowball_title_backfill,
        "post_snowball_openalex_backfill": post_snowball_openalex_backfill,
        "post_snowball_abstract_backfill": post_snowball_abstract_backfill,
        "post_snowball_crossref_backfill": post_snowball_crossref_backfill,
        "citation_backfill": citation_backfill,
        "raw_cache_merge": raw_cache_merge,
        "paths": {
            "papers_dedup": str(workspace_dir / "literature" / "papers_dedup.jsonl"),
            "papers_verified": str(verified_path),
            "papers_backlog": str(backlog_path),
            "verification_failures": str(failures_path),
            "deep_read_queue": str(queue_path),
            "domain_map": str(domain_map_path),
            "citation_edges": str(citation_edges_path),
            "access_audit": str(access_audit_path),
            "search_log": str(search_log_path),
            "missing_areas": str(missing_areas_path),
        },
    }
    write_t2_finalize_manifest(workspace_dir, summary)
    summary["paths"]["t2_finalize_manifest"] = str(workspace_dir / T2_FINALIZE_MANIFEST_REL_PATH)
    return summary
