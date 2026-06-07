"""论文数据增强工具。

只补齐 schema、provenance、可读性 hint 和队列优先级。source_type、
why_relevant、method_family、evidence_level 等需要学术判断的字段优先使用
LLM annotation；没有 annotation 时只写保守占位与复核标记。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from ..agents._common import load_jsonl, normalize_text_key
from ..literature_identity import find_matching_seed_pdf, paper_record_match_keys
from .citation_graph import (
    _build_node_alias_index,
    _dedupe_edges,
    _extract_edges,
    _extract_record_edges,
    _normalize_title_key,
    _paper_node,
)


SCREEN_RELATIONS_FOR_DEEP_READ = {
    "mechanism_bridge",
    "method_transfer",
    "evaluation_or_metric_bridge",
    "baseline_or_dataset_relevance",
}
SCREEN_RELATIONS_ALLOWED_FOR_QUEUE = SCREEN_RELATIONS_FOR_DEEP_READ | {"adjacent_application"}
SCREEN_RELATIONS = SCREEN_RELATIONS_ALLOWED_FOR_QUEUE | {"shared_keyword_only", "unrelated"}
SCREEN_ROLES = {"core", "theory_bridge", "adjacent", "baseline", "dataset", "benchmark", "none"}
DEEP_READ_TARGET_BUCKETS = {
    "seed",
    "target",  # legacy compatibility
    "mainline_deep",
    "bridge_deep",
}
DEEP_READ_TRIAGE_BUCKETS = {
    "overflow",  # legacy compatibility
    "mainline_screened",
    "bridge_screened",
}


def apply_semantic_screening(
    papers: list[dict[str, Any]],
    screenings: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge Agent-produced semantic screening into paper records.

    This function deliberately does not infer relation/core/bridge labels. It
    only validates and attaches structured judgments that the Scout LLM already
    produced, keeping resume deterministic and auditable.
    """

    screening_lookup = _build_screening_lookup(screenings)
    merged: list[dict[str, Any]] = []
    for paper in papers:
        record = dict(paper)
        screening = _lookup_screening(record, screening_lookup)
        if screening:
            record["semantic_screen"] = _normalize_semantic_screen(screening)
            if record["semantic_screen"].get("bridge_id"):
                record["bridge_id"] = record["semantic_screen"]["bridge_id"]
        merged.append(record)
    return merged


def enrich_papers(
    papers: list[dict[str, Any]],
    keywords: list[str] | None = None,
    domain_profile: dict[str, Any] | None = None,
    llm_annotations: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """增强论文数据，自动补充缺失字段。

    The function prioritizes caller/LLM annotations over heuristics. It should
    not be treated as a domain expert: source_type, method_family, relevance
    explanations, and domain tags are knowledge judgments and should be passed
    in through ``llm_annotations`` when available. The fallback logic only
    keeps records schema-compatible.

    功能：
    1. 应用 LLM annotation
    2. 保守补齐 source_type / why_relevant 等 schema 字段并标记复核
    3. 转换 authors 格式（对象数组 -> 字符串数组）
    4. 补充 metadata 可读性 hint
    5. 标记数据质量（是否缺少 abstract）

    Args:
        papers: 原始论文列表
        keywords: 关键词列表（可选，用于生成更具体的 why_relevant）
        domain_profile: LLM 归纳的领域 profile，用于保留 provenance，不用于硬编码结论
        llm_annotations: LLM 对论文的结构化标注。支持 dict[paper_id/title/doi, annotation]
            或包含 paper_id/id/title/doi 的对象数组。标注字段会优先写入 paper。

    Returns:
        增强后的论文列表
    """
    enriched = []
    annotation_lookup = _build_annotation_lookup(llm_annotations)

    for paper in papers:
        annotation = _lookup_annotation(paper, annotation_lookup)
        if annotation:
            for key in (
                "source_type",
                "why_relevant",
                "method_family",
                "domain_tags",
                "evidence_level",
                "access_score",
                "access_score_estimate",
                "relevance_rationale",
                "screening_decision",
                "semantic_screen",
            ):
                if key in annotation and annotation[key] not in (None, ""):
                    paper[key] = annotation[key]
            bucket = annotation.get("search_bucket") or annotation.get("query_bucket")
            if bucket not in (None, ""):
                paper["search_bucket"] = _normalize_search_bucket(bucket)
            source_bucket = annotation.get("source_bucket")
            if source_bucket not in (None, ""):
                paper["source_bucket"] = _normalize_source_bucket(source_bucket)
            if annotation.get("adjacent_field") is not None:
                paper["adjacent_field"] = bool(annotation.get("adjacent_field"))
            if annotation.get("cross_domain_retrieval_candidate") is not None:
                paper["cross_domain_retrieval_candidate"] = bool(annotation.get("cross_domain_retrieval_candidate"))
            paper["llm_annotation_applied"] = True

        # 1. 转换 authors 格式
        authors = _normalize_author_names(paper.get("authors", []))
        if authors:
            paper["authors"] = authors
        else:
            paper["authors"] = ["Unknown"]

        # 2. 保守推断 source_type。除 arXiv/workshop 这类直接 metadata 外，
        # venue prestige 属于领域判断，未知时标记给 LLM 复核。
        if "source_type" not in paper:
            venue = paper.get("venue", "").lower()
            source = str(paper.get("source", "")).casefold()
            if "arxiv" in venue or source == "arxiv":
                paper["source_type"] = "preprint"
            elif "workshop" in venue:
                paper["source_type"] = "workshop"
            else:
                paper["source_type"] = "unknown"
                paper.setdefault("_needs_llm_source_type", True)

        # 3. 自动生成 why_relevant
        if "why_relevant" not in paper:
            score = paper.get("relevance_score", 0.5)
            title = paper.get("title", "").lower()
            abstract = paper.get("abstract", "").lower()

            # 分析关键词匹配情况
            matched = []
            if keywords:
                for kw in keywords:
                    kw_lower = kw.lower()
                    if kw_lower in title:
                        matched.append(f"标题包含「{kw}」")
                    if kw_lower in abstract:
                        matched.append(f"摘要包含「{kw}」")
            elif domain_profile:
                for kw in _as_str_list(domain_profile.get("include_keywords"))[:12]:
                    kw_lower = kw.lower()
                    if kw_lower in title:
                        matched.append(f"标题包含领域 profile 术语「{kw}」")
                    if kw_lower in abstract:
                        matched.append(f"摘要包含领域 profile 术语「{kw}」")

            if matched:
                # 有具体匹配时，使用具体的匹配原因
                reason = "；".join(matched[:3])  # 最多保留3个原因
                paper["why_relevant"] = reason
            elif score >= 0.8:
                paper["why_relevant"] = "metadata priority hint 较高；需由 LLM 基于研究方向复核具体原因"
            elif score >= 0.6:
                paper["why_relevant"] = "metadata priority hint 中等；需由 LLM 复核具体关联"
            else:
                paper["why_relevant"] = "可能相关；缺少足够结构化证据，需复核"
            paper.setdefault("_needs_llm_relevance_review", True)

        if domain_profile and "domain_profile_used" not in paper:
            paper["domain_profile_used"] = {
                "domain": domain_profile.get("domain") or domain_profile.get("target_domain") or "",
                "profile_driven": True,
            }

        # 4. 补充缺失的必需字段
        if "abstract" not in paper or not paper["abstract"]:
            paper["abstract"] = ""
            paper["_missing_abstract"] = True  # 标记数据质量问题

        if "access_score_estimate" not in paper:
            paper["access_score_estimate"] = _estimate_access_score(paper)

        if "access_score" not in paper:
            paper["access_score"] = paper["access_score_estimate"]

        if "access_level_hint" not in paper:
            paper["access_level_hint"] = _estimate_access_level_hint(paper)

        if "evidence_level" not in paper:
            paper["evidence_level"] = "ABSTRACT_ONLY" if str(paper.get("abstract", "")).strip() else "METADATA_ONLY"
            paper.setdefault("_needs_reader_evidence_level", True)

        if "url" not in paper or not paper["url"]:
            # 尝试从 DOI 生成 URL
            doi = paper.get("doi", "")
            if doi:
                paper["url"] = f"https://doi.org/{doi}"
            else:
                paper["url"] = ""

        if "venue" not in paper:
            paper["venue"] = paper.get("source", "Unknown")

        if "citation_count" not in paper:
            paper["citation_count"] = 0

        if paper.get("search_bucket"):
            paper["search_bucket"] = _normalize_search_bucket(paper.get("search_bucket"))
        if paper.get("query_bucket") and not paper.get("search_bucket"):
            paper["search_bucket"] = _normalize_search_bucket(paper.get("query_bucket"))
        if paper.get("source_bucket"):
            paper["source_bucket"] = _normalize_source_bucket(paper.get("source_bucket"))
        if _is_cross_domain_retrieval_bucket(paper):
            # This is recall provenance only. It must not be read as a semantic
            # adjacent/core/theory decision; downstream admission requires
            # Scout LLM's semantic_screen.
            paper["cross_domain_retrieval_candidate"] = True

        semantic_screen = paper.get("semantic_screen")
        if isinstance(semantic_screen, dict):
            paper["semantic_screen"] = _normalize_semantic_screen(semantic_screen)

        # 5. 确保 year 是整数；缺失年份保持 None，避免把未知年份伪装成真实发表年。
        if "year" in paper and paper["year"]:
            try:
                paper["year"] = int(paper["year"])
            except (ValueError, TypeError):
                paper["year"] = None
        else:
            paper["year"] = None

        enriched.append(paper)

    return enriched


def _normalize_author_names(authors: Any) -> list[str]:
    if not authors:
        return []
    if isinstance(authors, str):
        return [authors.strip()] if authors.strip() else []
    if not isinstance(authors, list):
        text = str(authors).strip()
        return [text] if text else []
    names: list[str] = []
    for author in authors:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            name = str(
                author.get("name")
                or author.get("display_name")
                or author.get("author_name")
                or author.get("full_name")
                or ""
            ).strip()
        else:
            name = str(author).strip()
        if name:
            names.append(name)
    return names


def _build_annotation_lookup(
    annotations: dict[str, dict[str, Any]] | list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if not annotations:
        return lookup
    if isinstance(annotations, dict):
        iterable = []
        for key, value in annotations.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("paper_id", key)
                iterable.append(item)
    elif isinstance(annotations, list):
        iterable = [item for item in annotations if isinstance(item, dict)]
    else:
        return lookup
    for item in iterable:
        for key in _annotation_keys(item):
            lookup.setdefault(key, item)
    return lookup


def _lookup_annotation(
    paper: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not lookup:
        return {}
    for key in _annotation_keys(paper):
        if key in lookup:
            return lookup[key]
    return {}


def _annotation_keys(record: dict[str, Any]) -> set[str]:
    candidates = {
        record.get("paper_id"),
        record.get("id"),
        record.get("canonical_id"),
        record.get("doi"),
        record.get("title"),
    }
    return {normalize_text_key(str(item)) for item in candidates if str(item or "").strip()}


def _build_screening_lookup(
    screenings: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if isinstance(screenings, dict):
        iterable = []
        for key, value in screenings.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("paper_id", key)
                iterable.append(item)
    else:
        iterable = [item for item in screenings if isinstance(item, dict)]
    lookup: dict[str, dict[str, Any]] = {}
    for item in iterable:
        for key in _annotation_keys(item):
            lookup.setdefault(key, item)
    return lookup


def _lookup_screening(
    paper: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    for key in _annotation_keys(paper):
        if key in lookup:
            return lookup[key]
    return {}


def _normalize_semantic_screen(screening: dict[str, Any]) -> dict[str, Any]:
    relation = str(screening.get("relation_to_project") or "").strip()
    role = str(screening.get("role") or "").strip()
    confidence = str(screening.get("confidence") or "low").strip()
    normalized_relation = relation if relation in SCREEN_RELATIONS else "unrelated"
    normalized_role = role if role in SCREEN_ROLES else "none"
    requested_can_enter_core = bool(screening.get("can_enter_core"))
    requested_can_enter_deep_read = bool(screening.get("can_enter_deep_read"))
    normalized = {
        "relation_to_project": normalized_relation,
        "role": normalized_role,
        "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
        "bridge_id": str(screening.get("bridge_id") or "").strip() or None,
        "can_enter_core": (
            requested_can_enter_core
            and normalized_role == "core"
            and normalized_relation in SCREEN_RELATIONS_FOR_DEEP_READ
        ),
        "can_enter_deep_read": requested_can_enter_deep_read and normalized_relation in SCREEN_RELATIONS_ALLOWED_FOR_QUEUE,
        "rationale": str(screening.get("rationale") or "").strip(),
        "evidence_fields_used": [
            str(item)
            for item in screening.get("evidence_fields_used") or []
            if str(item).strip()
        ],
    }
    warnings: list[str] = []
    if relation and relation not in SCREEN_RELATIONS:
        warnings.append(f"unknown_relation_to_project:{relation}")
    if role and role not in SCREEN_ROLES:
        warnings.append(f"unknown_role:{role}")
    if requested_can_enter_core and normalized_role != "core":
        warnings.append("can_enter_core_true_but_role_not_core")
    if requested_can_enter_core and normalized_relation not in SCREEN_RELATIONS_FOR_DEEP_READ:
        warnings.append("can_enter_core_true_but_relation_not_core_allowed")
    if requested_can_enter_deep_read and normalized_relation not in SCREEN_RELATIONS_ALLOWED_FOR_QUEUE:
        warnings.append("can_enter_deep_read_true_but_relation_not_queue_allowed")
    if warnings:
        normalized["normalization_warnings"] = warnings
    return normalized


def _normalize_search_bucket(raw: Any) -> str:
    value = str(raw or "").strip().casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "adjacent": "adjacent_field",
        "nearby_field": "adjacent_field",
        "cross_domain": "adjacent_field",
        "theory": "theory_bridge",
        "theoretical": "theory_bridge",
    }
    return aliases.get(value, value)


def _normalize_source_bucket(raw: Any) -> str:
    value = str(raw or "").strip().casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "adjacent_field": "adjacent",
        "nearby_field": "adjacent",
        "cross_domain": "adjacent",
        "theory": "adjacent",
        "theory_bridge": "adjacent",
        "seed_paper": "seed",
        "snowball_reference": "snowball",
        "related_work": "snowball",
    }
    return aliases.get(value, value)


def _is_cross_domain_retrieval_bucket(paper: dict[str, Any]) -> bool:
    bucket = _normalize_search_bucket(paper.get("search_bucket") or paper.get("query_bucket"))
    source_bucket = _normalize_source_bucket(paper.get("source_bucket"))
    return bucket in {"adjacent_field", "theory_bridge"} or source_bucket in {"adjacent", "snowball"}


def _load_bridge_domain_plan(workspace_dir: Path) -> dict[str, Any]:
    """Load the confirmed T1 bridge manifest if present.

    The manifest is retrieval provenance only. This helper never invents
    bridge domains and never decides paper relevance.
    """

    path = workspace_dir / "literature" / "bridge_domain_plan.json"
    if not path.exists() or path.stat().st_size <= 0:
        return {"semantics": "bridge_domain_plan", "source": "auto", "bridge_domains": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"semantics": "bridge_domain_plan", "source": "invalid", "bridge_domains": []}
    if not isinstance(data, dict):
        return {"semantics": "bridge_domain_plan", "source": "invalid", "bridge_domains": []}
    domains = data.get("bridge_domains")
    if not isinstance(domains, list):
        data["bridge_domains"] = []
    return data


def _bridge_domains(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if str(plan.get("source") or "").strip().casefold() == "none":
        return []
    domains = plan.get("bridge_domains") if isinstance(plan, dict) else []
    return [item for item in domains or [] if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()]


def _confirmed_bridge_ids(plan: dict[str, Any]) -> list[str]:
    return [str(item.get("bridge_id") or "").strip() for item in _bridge_domains(plan)]


def _must_explore_bridge_ids(plan: dict[str, Any]) -> list[str]:
    return [
        str(item.get("bridge_id") or "").strip()
        for item in _bridge_domains(plan)
        if str(item.get("priority") or "").strip() == "must_explore"
    ]


def _bridge_priority(bridge_id: str | None, plan: dict[str, Any]) -> str:
    if not bridge_id:
        return ""
    for item in _bridge_domains(plan):
        if str(item.get("bridge_id") or "").strip() == bridge_id:
            return str(item.get("priority") or "").strip()
    return ""


def _must_explore_bridge_diagnostics(
    queue_records: list[dict[str, Any]],
    must_explore_bridge_ids: list[str],
) -> dict[str, dict[str, int]]:
    diagnostics: dict[str, dict[str, int]] = {}
    for bridge_id in must_explore_bridge_ids:
        related = [
            item
            for item in queue_records
            if item.get("bridge_id") == bridge_id
            or bridge_id in (item.get("recalled_by_bridges") or [])
            or bridge_id in (item.get("contributed_bridges") or [])
        ]
        diagnostics[bridge_id] = {
            "recalled_or_contributed": len(related),
            "active_target": sum(1 for item in related if not item.get("triaged_out")),
            "bridge_deep_active": sum(
                1
                for item in related
                if item.get("target_bucket") == "bridge_deep" and not item.get("triaged_out")
            ),
            "bridge_screened_backlog": sum(
                1
                for item in related
                if item.get("target_bucket") == "bridge_screened" or item.get("unscreened_bridge_backlog_candidate")
            ),
            "missing_semantic_screen": sum(1 for item in related if not item.get("semantic_screen")),
            "semantic_screen_excluded": sum(1 for item in related if item.get("semantic_screen_excluded_candidate")),
            "has_abstract": sum(1 for item in related if item.get("has_abstract")),
            "has_pdf_url_hint": sum(1 for item in related if item.get("has_pdf_url_hint")),
        }
    return diagnostics


def _bridge_sources_for_record(paper: dict[str, Any], semantic_screen: dict[str, Any]) -> list[str]:
    """Collect bridge provenance supplied by search tools or Scout LLM."""

    sources: list[str] = []
    for key in ("bridge_id", "recalled_by_bridges", "contributed_bridges"):
        raw = semantic_screen.get(key) if key == "bridge_id" else paper.get(key)
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            values = [str(item) for item in raw]
        else:
            values = []
        for value in values:
            bridge_id = str(value or "").strip()
            if bridge_id and bridge_id not in sources:
                sources.append(bridge_id)
    return sources


def _select_must_explore_bridge_records(
    ranked_records: list[dict[str, Any]],
    *,
    must_explore_bridge_ids: list[str],
    floor: int,
) -> list[dict[str, Any]]:
    """Reserve deep-read slots for confirmed must-explore bridges.

    Admission still requires Scout LLM semantic_screen to allow deep read; this
    function only prevents allowed bridge material from being squeezed out by
    ordinary ranking.
    """

    if floor <= 0 or not must_explore_bridge_ids:
        return []
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    for bridge_id in must_explore_bridge_ids:
        pool = [
            record
            for record in ranked_records
            if record.get("bridge_id") == bridge_id
            and record.get("cross_domain_candidate")
            and not record.get("seed_priority")
        ]
        pool.sort(
            key=lambda item: (
                -float(item.get("read_priority") or 0.0),
                -float(item.get("relevance_score") or 0.0),
                -float(item.get("access_score") or 0.0),
                str(item.get("title") or "").casefold(),
            )
        )
        for record in pool[:floor]:
            if id(record) in selected_ids:
                continue
            selected.append(record)
            selected_ids.add(id(record))
    return selected


def _bridge_pool_count(records: list[dict[str, Any]], bridge_id: str) -> int:
    if not bridge_id:
        return 0
    return sum(1 for record in records if str(record.get("bridge_id") or "") == bridge_id)


def _target_bucket_for_record(record: dict[str, Any], *, active_target: bool) -> str:
    if bool(record.get("seed_priority")):
        return "seed"
    if record.get("bridge_id") and record.get("cross_domain_candidate"):
        return "bridge_deep" if active_target else "bridge_screened"
    if record.get("unscreened_bridge_backlog_candidate"):
        return "bridge_screened"
    if record.get("semantic_screen_excluded_candidate") and record.get("bridge_id"):
        return "bridge_screened"
    return "mainline_deep" if active_target else "mainline_screened"


def _can_enter_active_target(record: dict[str, Any]) -> bool:
    """Return whether a queue record may count as an active T3 read target.

    Scout LLM screening is preferred, but T2 can legitimately end with many
    verified papers and only a partial semantic screen. In that case, metadata
    fallback records from the mainline pool may fill the active T3 budget so the
    Reader LLM can do the actual content-level review. Unscreened bridge query
    hits stay in screened backlog until Scout has made a semantic judgment.
    """

    if bool(record.get("seed_priority")):
        return True
    if bool(record.get("is_citation_hub")) and bool(record.get("citation_hub_protected_slot")):
        return True
    if bool(record.get("cross_domain_candidate")):
        return True
    if bool(record.get("semantic_screen", {}).get("can_enter_deep_read")):
        return True
    if bool(record.get("metadata_fallback_candidate")):
        return True
    return False


def _cap_bridge_screened_records(
    queue_records: list[dict[str, Any]],
    *,
    bridge_screened_cap: int,
) -> None:
    """Keep only the top screened backlog per bridge while preserving rank.

    This cap affects triaged bridge backlog only; active deep-read targets are
    left untouched.
    """

    if bridge_screened_cap < 0:
        return
    by_bridge: dict[str, list[dict[str, Any]]] = {}
    for record in queue_records:
        if record.get("target_bucket") != "bridge_screened":
            continue
        bridge_id = str(record.get("bridge_id") or "").strip()
        if not bridge_id:
            continue
        by_bridge.setdefault(bridge_id, []).append(record)
    for bridge_id, records in by_bridge.items():
        records.sort(
            key=lambda item: (
                -float(item.get("final_priority") or item.get("read_priority") or 0.0),
                -float(item.get("relevance_score") or 0.0),
                str(item.get("title") or "").casefold(),
            )
        )
        for record in records[bridge_screened_cap:]:
            record["triaged_out"] = True
            record["target_bucket"] = "bridge_screened"
            record["triaged_reason"] = "bridge_screened_cap_exceeded"


def _cap_mainline_screened_records(
    queue_records: list[dict[str, Any]],
    *,
    mainline_screened_cap: int,
) -> None:
    """Cap the retained mainline screened backlog for coverage/resume hygiene."""

    if mainline_screened_cap < 0:
        return
    records = [
        record
        for record in queue_records
        if record.get("target_bucket") == "mainline_screened"
    ]
    records.sort(
        key=lambda item: (
            -float(item.get("final_priority") or item.get("read_priority") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            str(item.get("title") or "").casefold(),
        )
    )
    for record in records[mainline_screened_cap:]:
        record["triaged_out"] = True
        record["target_bucket"] = "mainline_screened"
        record["triaged_reason"] = "mainline_screened_cap_exceeded"


def _final_priority(record: dict[str, Any]) -> float:
    """Compute stable final priority for protected and ordinary queue records."""

    if "final_priority" in record:
        try:
            return float(record.get("final_priority") or 0.0)
        except (TypeError, ValueError):
            pass
    venue_bonus = float(record.get("venue_diversity_bonus") or 0.0)
    return (
        float(record.get("read_priority") or 0.0)
        + 0.10 * venue_bonus
        + float(record.get("protected_slot_bonus") or 0.0)
        + float(record.get("citation_hub_bonus") or 0.0)
    )


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def build_deep_read_queue(
    papers: list[dict[str, Any]],
    workspace_dir: Path,
    *,
    deep_read_min: int = 35,
    deep_read_target: int = 35,
    deep_read_max: int = 45,
    probe_pool: int = 45,
    cross_domain_slots: int | None = None,
    citation_hub_slots: int | None = None,
    mainline_screened_cap: int = 90,
    bridge_deep_floor: int = 3,
    bridge_screened_cap: int = 7,
    bridge_pool_cap: int = 15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """为 T3 构建 deep-read 队列。

    设计目标：
    1. seed papers 必须最高优先级；
    2. metadata priority hint 与 access_score_estimate 联合排序；
    3. probe_pool 故意大于 target，给后续下载/解析失败留冗余；
    4. 输出单独 artifact，避免 T3 被整个 dedup 池绑死。

    注意：read_priority / evidence_level 在这里都是进入 T3 的优先级和可读性 hint。
    只有 Reader 真的读取 PDF 并记录 coverage 后，paper note 的 FULL/PARTIAL
    状态才是最终证据等级。
    """

    seed_path = workspace_dir / "user_seeds" / "seed_papers.jsonl"
    seed_papers = load_jsonl(seed_path) if seed_path.exists() else []
    local_pdf_dir = workspace_dir / "literature" / "pdfs"
    verified_path = workspace_dir / "literature" / "papers_verified.jsonl"
    verified_records = load_jsonl(verified_path) if verified_path.exists() else []
    bridge_plan = _load_bridge_domain_plan(workspace_dir)
    confirmed_bridge_ids = _confirmed_bridge_ids(bridge_plan)
    must_explore_bridge_ids = _must_explore_bridge_ids(bridge_plan)

    # 一旦 workspace 里已经存在 papers_verified，就必须优先把它当作权威池。
    # 这样即使上层 agent 误把 papers_dedup 传进来，也不会把未核验论文混进 T3 队列。
    authoritative_records, duplicate_prefer_verified_removed = _prefer_verified_records(
        candidate_records=papers,
        verified_records=verified_records,
    )
    authoritative_records, duplicate_queue_candidates_removed = _dedupe_records_for_queue(authoritative_records)
    duplicate_queue_candidates_removed += duplicate_prefer_verified_removed
    has_verified_pool = bool(verified_records)
    citation_hub_index = identify_citation_hubs(authoritative_records, workspace_dir)

    seed_keys: set[str] = set()
    seed_titles: list[str] = []
    for seed in seed_papers:
        seed_keys.update(_paper_match_keys(seed))
        seed_title = str(seed.get("title", "")).strip()
        if seed_title:
            seed_titles.append(str(seed.get("title", "")).strip())

    ranked_records: list[dict[str, Any]] = []
    for paper in authoritative_records:
        verification_status = str(paper.get("verification_status", "retrieved")).strip() or "retrieved"
        # 如果已经有 verified 池，deep-read queue 就只接受 verified 成员。
        # 否则 agent 很容易把 dedup 池里尚未核验的 retrieved 样本塞进来，最终又被 validator 拦住。
        if has_verified_pool and verification_status not in {"metadata_verified", "pdf_verified"}:
            continue
        # 失败样本不应继续进入 deep-read 队列，避免把 T3 预算浪费在坏样本上。
        if verification_status == "failed_verification":
            continue

        paper_id = str(paper.get("id", "")).strip()
        title = str(paper.get("title", "")).strip()
        key_candidates = _paper_match_keys(paper)
        is_seed = any(candidate and candidate in seed_keys for candidate in key_candidates)

        canonical_id = str(paper.get("canonical_id") or paper_id or "").strip()
        if not canonical_id or canonical_id == title:
            canonical_id = _stable_noopenalex_queue_id(paper)
        normalized_id = _normalize_paper_filename(canonical_id)
        readable_normalized_id = _normalize_paper_filename(paper_id)
        seed_pdf_path = find_matching_seed_pdf(paper, workspace_dir / "user_seeds" / "pdfs")
        has_seed_pdf = seed_pdf_path is not None
        has_local_pdf = bool(
            (normalized_id and (local_pdf_dir / f"{normalized_id}.pdf").exists())
            or (readable_normalized_id and (local_pdf_dir / f"{readable_normalized_id}.pdf").exists())
            or has_seed_pdf
        )
        access_est = float(paper.get("access_score_estimate", _estimate_access_score(paper)))
        access_score = max(access_est, 1.0 if has_local_pdf else access_est)
        access_level_hint = str(paper.get("access_level_hint", _estimate_access_level_hint(paper)))
        if has_seed_pdf:
            access_level_hint = "FULL_TEXT_LOCAL"
        relevance_score = float(paper.get("relevance_score", 0.0))
        verification_confidence = float(paper.get("verification_confidence", 0.0))
        verification_bonus = 0.25 if verification_status in {"metadata_verified", "pdf_verified"} else 0.0
        # methodological_signal 只是通用方法论文本 hint，不是领域相关性判断。
        meth_signal = float(paper.get("methodological_signal", 0.0))
        search_bucket = _normalize_search_bucket(paper.get("search_bucket") or paper.get("query_bucket"))
        source_bucket = _normalize_source_bucket(paper.get("source_bucket"))
        semantic_screen = paper.get("semantic_screen") if isinstance(paper.get("semantic_screen"), dict) else {}
        relation = str(semantic_screen.get("relation_to_project") or "").strip()
        semantic_role = str(semantic_screen.get("role") or "").strip()
        can_enter_core = (
            bool(semantic_screen.get("can_enter_core"))
            and semantic_role == "core"
            and relation in SCREEN_RELATIONS_FOR_DEEP_READ
        )
        can_enter_deep_read = (
            bool(semantic_screen.get("can_enter_deep_read"))
            and relation in SCREEN_RELATIONS_ALLOWED_FOR_QUEUE
        )
        bridge_sources = _bridge_sources_for_record(paper, semantic_screen)
        raw_bridge_id = str(semantic_screen.get("bridge_id") or paper.get("bridge_id") or "").strip() or None
        is_cross_domain_retrieval = (
            str(paper.get("retrieval_intent") or "").strip() == "cross_domain_bridge"
            or bool(raw_bridge_id)
            or search_bucket in {"adjacent_field", "theory_bridge"}
            or source_bucket in {"adjacent", "theory_bridge"}
        )
        has_semantic_screen = bool(semantic_screen)
        resolved_bridge_id = None if can_enter_core else raw_bridge_id
        cross_domain_candidate = (
            can_enter_deep_read
            and relation in SCREEN_RELATIONS_FOR_DEEP_READ
            and (
                semantic_role == "theory_bridge"
                or str(paper.get("retrieval_intent") or "") == "cross_domain_bridge"
                or bool(raw_bridge_id)
            )
            and not can_enter_core
        )
        metadata_fallback_candidate = (
            not is_seed
            and not has_semantic_screen
            and not is_cross_domain_retrieval
        )
        unscreened_bridge_backlog_candidate = (
            not is_seed
            and not has_semantic_screen
            and is_cross_domain_retrieval
        )
        semantic_screen_excluded_candidate = (
            not is_seed
            and has_semantic_screen
            and not can_enter_deep_read
        )
        hub = _lookup_citation_hub(paper, canonical_id, citation_hub_index)
        is_citation_hub = bool(hub) and (
            is_seed
            or can_enter_deep_read
            or (not has_semantic_screen and not semantic_screen_excluded_candidate)
        )
        hub_type = str(hub.get("hub_type") or "") if hub else ""
        hub_score = float(hub.get("hub_score") or 0.0) if hub else 0.0
        citation_hub_needs_reader_screening = bool(is_citation_hub and not has_semantic_screen and not is_seed)
        protected_slot_bonus = 0.12 if cross_domain_candidate else 0.0
        citation_hub_bonus = min(0.15, hub_score * 0.04) if is_citation_hub else 0.0
        read_priority = round(
            (1.0 if is_seed else 0.0) * 100.0
            + relevance_score * 0.50
            + access_score * 0.20
            + verification_confidence * 0.15
            + verification_bonus,
            4,
        )

        record = {
            "paper_id": canonical_id or paper_id,
            "title": title,
            "source": paper.get("source", ""),
            "year": paper.get("year"),
            "venue": paper.get("venue", ""),
            "relevance_score": round(relevance_score, 2),
            "access_score_estimate": round(access_est, 2),
            "access_score": round(access_score, 2),
            "evidence_level": paper.get("evidence_level", _estimate_evidence_level(paper)),
            "access_level_hint": access_level_hint,
            "seed_priority": is_seed,
            "has_local_pdf": has_local_pdf,
            "has_seed_pdf": has_seed_pdf,
            "seed_pdf_path": str(seed_pdf_path.relative_to(workspace_dir)) if seed_pdf_path else "",
            "why_relevant": paper.get("why_relevant", ""),
            "queue_reason": (
                "seed_paper" if is_seed
                else "screened_cross_domain_candidate" if cross_domain_candidate
                else "semantic_screen_deep_read_candidate" if can_enter_deep_read
                else "metadata_fallback_candidate" if metadata_fallback_candidate
                else "unscreened_bridge_backlog_candidate" if unscreened_bridge_backlog_candidate
                else "semantic_screen_excluded_candidate" if semantic_screen_excluded_candidate
                else "backlog_not_screened_for_deep_read"
            ),
            "normalized_id": normalized_id,
            "url": paper.get("url", ""),
            "doi": paper.get("doi", ""),
            "verification_status": verification_status,
            "verification_confidence": round(verification_confidence, 2),
            "read_priority": read_priority,
            "read_priority_semantics": "queue_priority_hint_not_final_relevance",
            "methodological_signal_hint": round(meth_signal, 2),
            "search_bucket": search_bucket,
            "source_bucket": source_bucket,
            "retrieval_intent": paper.get("retrieval_intent") or "primary",
            "bridge_id": resolved_bridge_id,
            "recalled_by_bridges": bridge_sources,
            "contributed_bridges": bridge_sources if can_enter_core else [bid for bid in bridge_sources if bid != resolved_bridge_id],
            "core_screen_passed": can_enter_core,
            "semantic_role": semantic_role,
            "relation_to_project": relation,
            "semantic_screen": semantic_screen,
            "cross_domain_retrieval_candidate": bool(paper.get("cross_domain_retrieval_candidate")),
            "cross_domain_candidate": cross_domain_candidate,
            "metadata_fallback_candidate": metadata_fallback_candidate,
            "unscreened_bridge_backlog_candidate": unscreened_bridge_backlog_candidate,
            "semantic_screen_excluded_candidate": semantic_screen_excluded_candidate,
            "adjacent_field": cross_domain_candidate,  # deprecated compatibility alias
            "protected_slot_bonus": protected_slot_bonus,
            "protected_bucket_bonus": protected_slot_bonus,  # deprecated compatibility alias
            "protected_slot": False,
            "is_citation_hub": is_citation_hub,
            "hub_type": hub_type,
            "hub_score": round(hub_score, 4),
            "citation_hub_bonus": round(citation_hub_bonus, 4),
            "citation_hub_protected_slot": False,
            "citation_hub_needs_reader_screening": citation_hub_needs_reader_screening,
            "bridge_must_protected_slot": False,
            "bridge_priority": _bridge_priority(resolved_bridge_id, bridge_plan),
        }
        if can_enter_core and raw_bridge_id:
            record["identity_resolution"] = "core_screen_passed_mainline_home"
        if is_citation_hub:
            record["queue_reason"] = (
                "citation_seed_neighbor"
                if hub_type == "seed_neighbor"
                else "citation_hub_needs_reader_screening"
                if citation_hub_needs_reader_screening
                else "citation_hub_deep_read_candidate"
            )
        record.update(_queue_evidence_hints(paper))
        if (
            is_seed
            or can_enter_deep_read
            or metadata_fallback_candidate
            or unscreened_bridge_backlog_candidate
            or semantic_screen_excluded_candidate
        ):
            ranked_records.append(record)

    ranked_records.sort(
        key=lambda item: (
            not item["seed_priority"],
            -item["read_priority"],
            -item["relevance_score"],
            -item["access_score"],
            str(item["title"]).casefold(),
        )
    )

    # --- protected slot selection: preserve LLM-screened cross-domain/theory material ---
    selected_count = min(len(ranked_records), max(probe_pool, deep_read_target, deep_read_min))
    # Keep every admitted verified record in the queue artifact. Active T3
    # reading is still capped by deep_read_target/deep_read_max, but overflow is
    # retained as shallow-read/backlog disposition so verified papers never
    # disappear silently between T2 and T3 abstract sweep.
    queue_retention_count = len(ranked_records)
    if cross_domain_slots is None:
        cross_domain_slot_count = min(4, max(1, int(round(deep_read_target * 0.20)))) if deep_read_target > 0 else 0
    else:
        cross_domain_slot_count = max(0, int(cross_domain_slots))
    cross_domain_slot_count = min(cross_domain_slot_count, max(0, selected_count))
    if citation_hub_slots is None:
        citation_hub_slot_count = min(3, max(1, int(round(deep_read_target * 0.12)))) if deep_read_target > 0 else 0
    else:
        citation_hub_slot_count = max(0, int(citation_hub_slots))
    citation_hub_slot_count = min(citation_hub_slot_count, max(0, selected_count))
    bridge_must_records = _select_must_explore_bridge_records(
        ranked_records,
        must_explore_bridge_ids=must_explore_bridge_ids,
        floor=max(0, int(bridge_deep_floor)),
    )
    for record in bridge_must_records:
        record["protected_slot"] = True
        record["bridge_must_protected_slot"] = True
        record["promoted_reason"] = "must_explore_bridge_deep_floor"

    protected_records = [
        record
        for record in ranked_records
        if (
            record.get("cross_domain_candidate")
            and not record.get("seed_priority")
            and id(record) not in {id(item) for item in bridge_must_records}
        )
    ][:cross_domain_slot_count]
    for record in protected_records:
        record["protected_slot"] = True
    citation_hub_records = [
        record
        for record in ranked_records
        if record.get("is_citation_hub")
        and not record.get("seed_priority")
        and id(record) not in {id(item) for item in [*bridge_must_records, *protected_records]}
    ]
    citation_hub_records.sort(
        key=lambda item: (
            _citation_hub_type_rank(str(item.get("hub_type") or "")),
            -float(item.get("hub_score") or 0.0),
            -float(item.get("read_priority") or 0.0),
            str(item.get("title") or "").casefold(),
        )
    )
    citation_hub_records = citation_hub_records[:citation_hub_slot_count]
    for record in citation_hub_records:
        record["citation_hub_protected_slot"] = True
    protected_ids = {id(record) for record in [*bridge_must_records, *protected_records, *citation_hub_records]}
    queue_records: list[dict[str, Any]] = [*bridge_must_records, *protected_records, *citation_hub_records]

    # --- venue diversity bonus: 动态选择，避免同一 venue 占据过多 queue 名额 ---
    venue_counts: dict[str, int] = {}
    for record in queue_records:
        venue = record.get("venue", "unknown") or "unknown"
        venue_counts[venue] = venue_counts.get(venue, 0) + 1
    for record in ranked_records:
        if len(queue_records) >= queue_retention_count:
            break
        if id(record) in protected_ids:
            continue
        venue = record.get("venue", "unknown") or "unknown"
        same_venue = venue_counts.get(venue, 0)
        # 0 同 venue: 1.0, 1 个: 0.7, 2 个: 0.4, >=3 个: 0.0
        venue_bonus = max(0.0, 1.0 - same_venue * 0.3)
        record["venue_diversity_bonus"] = round(venue_bonus, 2)
        # 把 venue bonus 加入 read_priority 作为最终排序依据
        record["final_priority"] = record["read_priority"] + 0.10 * venue_bonus + float(record.get("protected_slot_bonus") or 0.0)
        record["final_priority"] += float(record.get("citation_hub_bonus") or 0.0)
        queue_records.append(record)
        venue_counts[venue] = same_venue + 1
    for record in [*bridge_must_records, *protected_records, *citation_hub_records]:
        record["final_priority"] = _final_priority(record)

    # 重新按 final_priority 排序（seed 不参与 venue bonus，保持 seed 最高优先）
    queue_records.sort(
        key=lambda item: (
            not item["seed_priority"],
            not (item.get("hub_type") == "seed_neighbor" and item.get("citation_hub_protected_slot")),
            not item.get("bridge_must_protected_slot"),
            id(item) not in protected_ids,
            -_final_priority(item),
            -item["relevance_score"],
            -item["access_score"],
            str(item["title"]).casefold(),
        )
    )
    target_goal = max(0, int(deep_read_min), int(deep_read_target))
    target_cap = max(target_goal, int(deep_read_max))
    active_target_limit = min(
        len(queue_records),
        target_cap,
        max(target_goal, len(protected_ids)),
    )
    active_assigned = 0
    for idx, record in enumerate(queue_records, start=1):
        record["queue_rank"] = idx
        active_target = (
            active_assigned < active_target_limit
            and _can_enter_active_target(record)
        )
        if active_target:
            active_assigned += 1
        record["triaged_out"] = not active_target
        record["target_bucket"] = _target_bucket_for_record(record, active_target=active_target)
        if record["triaged_out"]:
            record.setdefault("triaged_reason", "probe_pool_triage_out")
        record["read_disposition"] = "deep_read" if active_target else "shallow_read"
        record["read_disposition_reason"] = (
            "active_t3_target"
            if active_target
            else "retained_for_t3_abstract_sweep_or_human_revisit"
        )

    _cap_mainline_screened_records(queue_records, mainline_screened_cap=mainline_screened_cap)
    _cap_bridge_screened_records(queue_records, bridge_screened_cap=bridge_screened_cap)

    must_explore_bridge_diagnostics = _must_explore_bridge_diagnostics(queue_records, must_explore_bridge_ids)
    metadata = {
        "deep_read_min": deep_read_min,
        "deep_read_target": deep_read_target,
        "deep_read_max": deep_read_max,
        "probe_pool": probe_pool,
        "mainline_screened_cap": mainline_screened_cap,
        "bridge_deep_floor": bridge_deep_floor,
        "bridge_screened_cap": bridge_screened_cap,
        "bridge_pool_cap": bridge_pool_cap,
        "source_pool": "papers_verified" if has_verified_pool else "caller_supplied_records",
        "queue_count": len(queue_records),
        "queue_retention_count": queue_retention_count,
        "active_target_limit": active_target_limit,
        "target_entry_count": sum(1 for item in queue_records if not item.get("triaged_out")),
        "triaged_out_count": sum(1 for item in queue_records if item.get("triaged_out")),
        "verified_pool_count": len(authoritative_records),
        "duplicate_queue_candidates_removed": duplicate_queue_candidates_removed,
        "verified_disposition_count": len(queue_records),
        "verified_disposition_coverage": round(
            len(queue_records) / max(1, len(authoritative_records)),
            4,
        ),
        "shallow_read_backlog_count": sum(
            1 for item in queue_records if item.get("read_disposition") == "shallow_read"
        ),
        "mainline_screened_retained": sum(
            1 for item in queue_records if item.get("target_bucket") == "mainline_screened"
        ),
        "mainline_screened_cap_exceeded": sum(
            1
            for item in queue_records
            if item.get("target_bucket") == "mainline_screened"
            and item.get("triaged_reason") == "mainline_screened_cap_exceeded"
        ),
        "bridge_screened_retained": sum(
            1 for item in queue_records if item.get("target_bucket") == "bridge_screened"
        ),
        "bridge_screened_cap_exceeded": sum(
            1
            for item in queue_records
            if item.get("target_bucket") == "bridge_screened"
            and item.get("triaged_reason") == "bridge_screened_cap_exceeded"
        ),
        "seed_total": len(seed_papers),
        "seed_titles": seed_titles[:20],
        "seed_in_queue": sum(1 for item in queue_records if item["seed_priority"]),
        "confirmed_bridge_ids": confirmed_bridge_ids,
        "must_explore_bridge_ids": must_explore_bridge_ids,
        "must_explore_bridge_target_counts": {
            bridge_id: sum(
                1
                for item in queue_records
                if item.get("bridge_id") == bridge_id
                and item.get("target_bucket") == "bridge_deep"
                and not item.get("triaged_out")
            )
            for bridge_id in must_explore_bridge_ids
        },
        "must_explore_bridge_diagnostics": must_explore_bridge_diagnostics,
        "must_explore_bridge_warnings": [
            (
                f"{bridge_id}: recalled={diag['recalled_or_contributed']} "
                f"but active_bridge_deep={diag['bridge_deep_active']}; "
                f"missing_semantic_screen={diag['missing_semantic_screen']}, "
                f"semantic_screen_excluded={diag['semantic_screen_excluded']}"
            )
            for bridge_id, diag in must_explore_bridge_diagnostics.items()
            if diag["recalled_or_contributed"] > 0 and diag["bridge_deep_active"] <= 0
        ],
        "protected_slot_target": cross_domain_slot_count,
        "protected_bucket_target": cross_domain_slot_count,  # deprecated alias for older workspace metadata
        "cross_domain_slots": cross_domain_slot_count,
        "citation_hub_slots": citation_hub_slot_count,
        "citation_hubs_detected": len(citation_hub_index),
        "citation_hub_in_queue": sum(1 for item in queue_records if item.get("is_citation_hub")),
        "citation_hub_in_target": sum(
            1
            for item in queue_records
            if item.get("is_citation_hub") and not item.get("triaged_out")
        ),
        "citation_hub_seed_neighbor_in_target": sum(
            1
            for item in queue_records
            if item.get("hub_type") == "seed_neighbor" and not item.get("triaged_out")
        ),
        "protected_slot_in_queue": sum(1 for item in queue_records if item.get("cross_domain_candidate")),
        "protected_bucket_in_queue": sum(1 for item in queue_records if item.get("cross_domain_candidate")),
        "protected_slot_in_target": sum(
            1
            for item in queue_records
            if item.get("cross_domain_candidate") and not item.get("triaged_out")
        ),
        "protected_bucket_in_target": sum(
            1
            for item in queue_records
            if item.get("cross_domain_candidate") and not item.get("triaged_out")
        ),
        "metadata_fallback_in_target": sum(
            1
            for item in queue_records
            if item.get("metadata_fallback_candidate") and not item.get("triaged_out")
        ),
        "metadata_fallback_in_queue": sum(
            1 for item in queue_records if item.get("metadata_fallback_candidate")
        ),
        "unscreened_bridge_backlog_count": sum(
            1 for item in queue_records if item.get("unscreened_bridge_backlog_candidate")
        ),
        "semantic_screen_excluded_in_queue": sum(
            1 for item in queue_records if item.get("semantic_screen_excluded_candidate")
        ),
        "full_text_candidates": sum(
            1
            for item in queue_records
            if item.get("access_level_hint") in {"LIKELY_FULL_TEXT", "POSSIBLE_FULL_TEXT"}
        ),
        "queue_with_abstract_hints": sum(1 for item in queue_records if item.get("has_abstract")),
        "queue_with_reference_hints": sum(1 for item in queue_records if int(item.get("reference_hint_count") or 0) > 0),
        "queue_with_pdf_url_hints": sum(1 for item in queue_records if item.get("has_pdf_url_hint")),
        "verified_candidates": sum(
            1
            for item in queue_records
            if item["verification_status"] in {"metadata_verified", "pdf_verified"}
        ),
        "screened_deep_read_candidates": sum(
            1
            for item in queue_records
            if (
                item.get("seed_priority")
                or (
                    item.get("semantic_screen", {}).get("can_enter_deep_read")
                    and item.get("relation_to_project") in SCREEN_RELATIONS_ALLOWED_FOR_QUEUE
                )
            )
        ),
    }
    return queue_records, metadata


def _queue_evidence_hints(paper: dict[str, Any]) -> dict[str, Any]:
    """Small evidence availability hints for queue/debugging.

    The queue remains a compact scheduling artifact. Full abstracts, PDF
    locations, and reference payloads stay in verified/dedup/raw and are merged
    by lookup_paper_record(queue_rank=...). Short provenance fields are kept
    here because they are required for resume/debugging and do not inflate the
    Reader context materially.
    """

    abstract = str(paper.get("abstract") or "").strip()
    reference_count = 0
    for key in ("references", "referenced_works", "related_works"):
        value = paper.get(key)
        if isinstance(value, list):
            reference_count = max(reference_count, len(value))
    try:
        reference_count = max(reference_count, int(paper.get("reference_count") or 0))
    except (TypeError, ValueError):
        pass

    pdf_hint_count = 0
    for key in (
        "pdf_url",
        "open_access_pdf_url",
        "oa_pdf_url",
        "best_pdf_url",
        "full_text_url",
        "pmc_pdf_url",
        "url_for_pdf",
        "landing_page_url",
    ):
        if str(paper.get(key) or "").strip():
            pdf_hint_count += 1
    for key in (
        "openAccessPdf",
        "open_access_pdf",
        "oa_pdf",
        "best_oa_location",
        "primary_location",
    ):
        if isinstance(paper.get(key), dict) and paper.get(key):
            pdf_hint_count += 1
    for key in ("locations", "oa_locations", "open_access_locations", "openAccessLocations", "open_access_pdfs"):
        value = paper.get(key)
        if isinstance(value, list):
            pdf_hint_count += len([item for item in value if item])

    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    provenance = paper.get("provenance") if isinstance(paper.get("provenance"), dict) else {}
    source_queries = _short_unique_values(
        paper.get("source_queries"),
        paper.get("source_query"),
        provenance.get("source_query"),
        limit=8,
    )
    source_tools = _short_unique_values(
        paper.get("source_tools"),
        paper.get("source_tool"),
        provenance.get("source_tool"),
        paper.get("source"),
        limit=8,
    )
    search_buckets = _short_unique_values(
        paper.get("search_buckets"),
        paper.get("query_buckets"),
        paper.get("search_bucket"),
        paper.get("query_bucket"),
        provenance.get("search_bucket"),
        provenance.get("query_bucket"),
        limit=8,
    )
    citation_source_ids = _short_unique_values(
        paper.get("citation_snowball_source_ids"),
        paper.get("citation_snowball_source_id"),
        provenance.get("snowball_source_id"),
        limit=6,
    )

    hints = {
        "has_abstract": bool(abstract),
        "abstract_chars": len(abstract),
        "reference_hint_count": reference_count,
        "has_pdf_url_hint": pdf_hint_count > 0,
        "pdf_url_hint_count": pdf_hint_count,
    }
    if source_queries:
        hints["source_query"] = source_queries[0]
        hints["source_queries"] = source_queries
    if source_tools:
        hints["source_tool"] = source_tools[0]
        hints["source_tools"] = source_tools
    if search_buckets:
        hints["search_buckets"] = search_buckets
    openalex_id = str(paper.get("openalex_id") or external_ids.get("OpenAlex") or "").strip()
    if openalex_id:
        hints["openalex_id"] = openalex_id
    arxiv_id = str(paper.get("arxiv_id") or external_ids.get("ArXiv") or "").strip()
    if arxiv_id:
        hints["arxiv_id"] = arxiv_id
    if citation_source_ids:
        hints["citation_snowball_source_id"] = citation_source_ids[0]
        hints["citation_snowball_source_ids"] = citation_source_ids
    return hints


def _short_unique_values(*values: Any, limit: int = 8, max_chars: int = 240) -> list[str]:
    result: list[str] = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            text = " ".join(str(item or "").split())
            if not text:
                continue
            if len(text) > max_chars:
                text = text[: max_chars - 3].rstrip() + "..."
            if text not in result:
                result.append(text)
            if len(result) >= limit:
                return result
    return result


def identify_citation_hubs(
    papers: list[dict[str, Any]],
    workspace_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Identify graph hubs from pool-internal citation edges.

    This is purely structural. It does not decide whether a paper is relevant;
    queue admission still requires seed priority or Scout LLM semantic_screen.
    """

    nodes = [_paper_node(record) for record in papers if isinstance(record, dict)]
    nodes = [node for node in nodes if node.get("id")]
    if not nodes:
        return {}
    node_by_id = {str(node["id"]): node for node in nodes}
    alias_to_id = _build_node_alias_index(nodes)
    title_to_id = {
        _normalize_title_key(str(node.get("title") or "")): str(node["id"])
        for node in nodes
        if node.get("title")
    }
    edge_payload = _load_citation_edge_payload(workspace_dir / "literature" / "citation_edges.json")
    edges = _extract_edges(
        edge_payload,
        node_by_id=node_by_id,
        title_to_id=title_to_id,
        alias_to_id=alias_to_id,
    )
    edges.extend(
        _extract_record_edges(
            nodes,
            node_by_id=node_by_id,
            title_to_id=title_to_id,
            alias_to_id=alias_to_id,
        )
    )
    edges = _dedupe_edges(edges)
    if not edges:
        return {}

    degree: dict[str, int] = {node_id: 0 for node_id in node_by_id}
    inbound: dict[str, int] = {node_id: 0 for node_id in node_by_id}
    neighbors: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for left, right in edges:
        if left not in node_by_id or right not in node_by_id:
            continue
        degree[left] += 1
        degree[right] += 1
        inbound[right] += 1
        neighbors[left].add(right)
        neighbors[right].add(left)

    seed_ids = {
        str(node["id"])
        for node in nodes
        if _node_is_seed(node)
    }
    hubs: dict[str, dict[str, Any]] = {}
    for node_id, node in node_by_id.items():
        node_degree = degree.get(node_id, 0)
        node_inbound = inbound.get(node_id, 0)
        if node_degree <= 0 and node_inbound <= 0:
            continue
        hub_type = ""
        if any(neighbor in seed_ids for neighbor in neighbors.get(node_id, set())):
            hub_type = "seed_neighbor"
        elif node_inbound >= 2:
            hub_type = "high_inbound"
        else:
            bridge_count = _neighbor_bucket_diversity(
                neighbors.get(node_id, set()),
                node_by_id=node_by_id,
            )
            if bridge_count >= 2:
                hub_type = "bridge_node"
        if not hub_type:
            continue
        score = float(node_inbound * 2 + node_degree)
        if hub_type == "seed_neighbor":
            score += 100.0
        elif hub_type == "bridge_node":
            score += 10.0
        hubs[node_id] = {
            "hub_type": hub_type,
            "hub_score": round(score, 4),
            "degree": node_degree,
            "inbound_degree": node_inbound,
            "neighbor_count": len(neighbors.get(node_id, set())),
        }
    return hubs


def _load_citation_edge_payload(path: Path) -> list[Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return payload
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def _lookup_citation_hub(
    paper: dict[str, Any],
    canonical_id: str,
    hub_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    for candidate in (
        canonical_id,
        paper.get("canonical_id"),
        paper.get("paper_id"),
        paper.get("id"),
        paper.get("doi"),
    ):
        key = str(candidate or "").strip()
        if key in hub_index:
            return hub_index[key]
    return {}


def _node_is_seed(node: dict[str, Any]) -> bool:
    source = str(node.get("source") or "").casefold()
    bucket = _normalize_source_bucket(node.get("source_bucket") or node.get("search_bucket"))
    return source == "user_seed" or bucket == "seed" or bool(node.get("seed_priority"))


def _neighbor_bucket_diversity(
    neighbor_ids: set[str],
    *,
    node_by_id: dict[str, dict[str, Any]],
) -> int:
    buckets: set[str] = set()
    for neighbor_id in neighbor_ids:
        node = node_by_id.get(neighbor_id)
        if not node:
            continue
        screen = node.get("semantic_screen") if isinstance(node.get("semantic_screen"), dict) else {}
        role = str(screen.get("role") or node.get("semantic_role") or "").strip()
        bucket = role or _normalize_source_bucket(node.get("source_bucket") or node.get("search_bucket"))
        if bucket:
            buckets.add(bucket)
    return len(buckets)


def _citation_hub_type_rank(hub_type: str) -> int:
    return {
        "seed_neighbor": 0,
        "bridge_node": 1,
        "high_inbound": 2,
    }.get(hub_type, 9)


def _prefer_verified_records(
    *,
    candidate_records: list[dict[str, Any]],
    verified_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """优先把候选池收敛到 workspace 内已核验过的论文记录。

    设计原因：
    - 上层 agent 可能把 papers_dedup 整池传进 build_deep_read_queue；
    - 但当前系统语义要求 T3 队列建立在 papers_verified 之上；
    - 因此这里要在工具层做一次“以 verified 为准”的硬约束，而不是只靠 prompt。
    """

    if not verified_records:
        return candidate_records, 0

    verified_by_key: dict[str, dict[str, Any]] = {}
    for record in verified_records:
        for key in _paper_match_keys(record):
            verified_by_key.setdefault(key, record)

    matched_records: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    duplicate_removed = 0
    for paper in candidate_records:
        matched = None
        for key in _paper_match_keys(paper):
            matched = verified_by_key.get(key)
            if matched is not None:
                break
        if matched is None:
            continue
        matched_identity = id(matched)
        if matched_identity in seen_ids:
            duplicate_removed += 1
            continue
        seen_ids.add(matched_identity)
        matched_records.append(matched)

    # 如果上层传入的候选集完全对不上，直接回退到完整 verified 池，
    # 避免因为调用参数不理想而意外生成空队列。
    return (matched_records or verified_records), duplicate_removed


def _dedupe_records_for_queue(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Final identity guard before building T3's reading queue.

    Upstream raw/dedup merging handles the common cases, but verified pools can
    still contain alias variants from OpenAlex/arXiv/DOI/title-only sources.
    T3 should never spend deep-read budget twice on the same paper. This helper
    removes duplicate queue candidates using the shared identity-key set; it
    does not infer scholarly relevance or merge semantic judgments.
    """

    kept: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    removed = 0
    for record in records:
        keys = _paper_match_keys(record)
        if keys and any(key in seen_keys for key in keys):
            removed += 1
            continue
        kept.append(record)
        seen_keys.update(keys)
    return kept, removed


def build_access_audit(
    papers: list[dict[str, Any]],
    workspace_dir: Path,
    *,
    top_n: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    """构建文献可读性审计清单。

    输出目标：
    1. 告诉用户当前候选池里多少篇有本地 PDF；
    2. 哪些论文最值得优先 probe / deep-read；
    3. 哪些论文按 metadata 看起来可能可读，哪些只有摘要或 metadata。
    """

    local_pdf_dir = workspace_dir / "literature" / "pdfs"
    seed_pdf_dir = workspace_dir / "user_seeds" / "pdfs"
    records: list[dict[str, Any]] = []

    for paper in papers:
        paper_id = str(paper.get("id", "")).strip()
        title = str(paper.get("title", "")).strip()
        canonical_id = str(paper.get("canonical_id") or "").strip()
        if not canonical_id or canonical_id == title:
            canonical_id = _stable_noopenalex_queue_id(paper)
        normalized_id = _normalize_paper_filename(canonical_id or paper_id)
        readable_normalized_id = _normalize_paper_filename(paper_id)
        seed_pdf_path = find_matching_seed_pdf(paper, seed_pdf_dir) if _can_use_seed_pdf(paper) else None
        has_seed_pdf = seed_pdf_path is not None
        has_literature_pdf = bool(
            (normalized_id and (local_pdf_dir / f"{normalized_id}.pdf").exists())
            or (readable_normalized_id and (local_pdf_dir / f"{readable_normalized_id}.pdf").exists())
        )

        access_est = float(paper.get("access_score_estimate", _estimate_access_score(paper)))
        evidence_level = str(paper.get("evidence_level", _estimate_evidence_level(paper)))
        access_level_hint = str(paper.get("access_level_hint", _estimate_access_level_hint(paper)))
        if has_seed_pdf:
            access_est = 1.0
            access_level_hint = "FULL_TEXT_LOCAL"
        records.append(
            {
                "paper_id": str(paper.get("canonical_id") or paper_id),
                "title": title,
                "source": paper.get("source", ""),
                "year": paper.get("year"),
                "relevance_score": round(float(paper.get("relevance_score", 0.0)), 2),
                "access_score_estimate": round(access_est, 2),
                "evidence_level": evidence_level,
                "access_level_hint": access_level_hint,
                "verification_status": str(paper.get("verification_status", "retrieved")),
                "verification_confidence": round(float(paper.get("verification_confidence", 0.0)), 2),
                "has_local_pdf": has_literature_pdf,
                "has_seed_pdf": has_seed_pdf,
                "seed_pdf_path": str(seed_pdf_path.relative_to(workspace_dir)) if seed_pdf_path else "",
                "recommended_action": _recommended_action(
                    has_local_pdf=has_literature_pdf,
                    has_seed_pdf=has_seed_pdf,
                    evidence_level=evidence_level,
                    access_level_hint=access_level_hint,
                    verification_status=str(paper.get("verification_status", "retrieved")),
                ),
            }
        )

    records.sort(
        key=lambda item: (
            not item["has_local_pdf"],
            not item["has_seed_pdf"],
            -item["access_score_estimate"],
            -item["relevance_score"],
            str(item["title"]).casefold(),
        )
    )

    local_pdf_count = sum(1 for item in records if item["has_local_pdf"])
    seed_pdf_count = sum(1 for item in records if item["has_seed_pdf"])
    evidence_counts: dict[str, int] = {
        level: sum(1 for item in records if item["evidence_level"] == level)
        for level in ["FULL_TEXT", "PARTIAL_TEXT", "ABSTRACT_ONLY", "METADATA_ONLY"]
    }
    access_hint_counts: dict[str, int] = {
        level: sum(1 for item in records if item["access_level_hint"] == level)
        for level in ["LIKELY_FULL_TEXT", "POSSIBLE_FULL_TEXT", "ABSTRACT_OR_METADATA", "METADATA_ONLY"]
    }

    lines = [
        "# Access Audit",
        "",
        f"- 候选论文总数: {len(records)}",
        f"- `literature/pdfs/` 本地 PDF: {local_pdf_count}",
        f"- `user_seeds/pdfs/` 可匹配的 seed PDF: {seed_pdf_count}",
        "- Evidence level 是 Reader 最终阅读状态；T2 阶段仅代表已有字段或保守占位。",
        f"- `FULL_TEXT`: {evidence_counts['FULL_TEXT']}",
        f"- `PARTIAL_TEXT`: {evidence_counts['PARTIAL_TEXT']}",
        f"- `ABSTRACT_ONLY`: {evidence_counts['ABSTRACT_ONLY']}",
        f"- `METADATA_ONLY`: {evidence_counts['METADATA_ONLY']}",
        f"- Access hint `FULL_TEXT_LOCAL`: {sum(1 for item in records if item['access_level_hint'] == 'FULL_TEXT_LOCAL')}",
        f"- Access hint `LIKELY_FULL_TEXT`: {access_hint_counts['LIKELY_FULL_TEXT']}",
        f"- Access hint `POSSIBLE_FULL_TEXT`: {access_hint_counts['POSSIBLE_FULL_TEXT']}",
        f"- Access hint `ABSTRACT_OR_METADATA`: {access_hint_counts['ABSTRACT_OR_METADATA']}",
        f"- Access hint `METADATA_ONLY`: {access_hint_counts['METADATA_ONLY']}",
        "",
        "## Top Candidates",
        "",
        "| Rank | Title | Source | Priority Hint | Access | Access Hint | Evidence | Verified | Local PDF | Seed PDF | Recommended Action |",
        "|---|---|---|---:|---:|---|---|---|---|---|---|",
    ]

    for idx, item in enumerate(records[:top_n], start=1):
        lines.append(
            "| {rank} | {title} | {source} | {rel:.2f} | {acc:.2f} | {hint} | {evi} | {ver} ({conf:.2f}) | {local} | {seed} | {action} |".format(
                rank=idx,
                title=str(item["title"]).replace("|", "/"),
                source=str(item["source"]).replace("|", "/"),
                rel=float(item["relevance_score"]),
                acc=float(item["access_score_estimate"]),
                hint=item["access_level_hint"],
                evi=item["evidence_level"],
                ver=item["verification_status"],
                conf=float(item["verification_confidence"]),
                local="yes" if item["has_local_pdf"] else "no",
                seed="yes" if item["has_seed_pdf"] else "no",
                action=item["recommended_action"],
            )
        )

    markdown = "\n".join(lines) + "\n"
    return records, markdown


def _estimate_access_score(paper: dict[str, Any]) -> float:
    """基于 metadata 估计资料可用性。"""

    score = 0.1
    source = str(paper.get("source", "")).casefold()
    url = str(paper.get("url", "")).strip()
    doi = str(paper.get("doi", "")).strip()
    abstract = str(paper.get("abstract", "")).strip()
    external_ids = paper.get("externalIds") or {}

    has_arxiv = (
        source == "arxiv"
        or "arxiv" in url.casefold()
        or bool(external_ids.get("ArXiv"))
        or str(paper.get("id", "")).startswith("arxiv:")
    )
    if has_arxiv:
        score += 0.45
    if doi:
        score += 0.05
    if url:
        score += 0.05
        if url.casefold().endswith(".pdf"):
            score += 0.25
    if abstract:
        score += 0.2
    if _has_explicit_pdf_signal(paper):
        score += 0.35

    return round(min(1.0, score), 2)


def _estimate_evidence_level(paper: dict[str, Any]) -> str:
    """保守推断当前记录实际持有的证据等级。

    不再根据 metadata access score 推断 FULL/PARTIAL；FULL/PARTIAL 只能由
    Reader 的 PDF coverage 产生。这里仅用于 schema 兼容和旧记录回退。
    """

    has_abstract = bool(str(paper.get("abstract", "")).strip())
    if has_abstract:
        return "ABSTRACT_ONLY"
    return "METADATA_ONLY"


def _estimate_access_level_hint(paper: dict[str, Any]) -> str:
    """基于 metadata 给出可读性 hint，不代表最终阅读证据等级。"""

    access_score = float(paper.get("access_score_estimate", _estimate_access_score(paper)))
    has_direct_pdf = _has_explicit_pdf_signal(paper)
    has_arxiv = (
        str(paper.get("source", "")).casefold() == "arxiv"
        or str(paper.get("id", "")).startswith("arxiv:")
        or bool((paper.get("externalIds") or {}).get("ArXiv"))
        or "arxiv.org" in str(paper.get("url", "")).casefold()
    )
    if (has_direct_pdf or has_arxiv) and access_score >= 0.8:
        return "LIKELY_FULL_TEXT"
    if has_direct_pdf or has_arxiv:
        return "POSSIBLE_FULL_TEXT"
    if str(paper.get("abstract", "")).strip():
        return "ABSTRACT_OR_METADATA"
    return "METADATA_ONLY"


def _has_explicit_pdf_signal(paper: dict[str, Any]) -> bool:
    for key in (
        "pdf_url",
        "open_access_pdf_url",
        "oa_pdf_url",
        "best_pdf_url",
        "full_text_url",
        "pmc_pdf_url",
        "url_for_pdf",
    ):
        value = str(paper.get(key) or "").strip()
        if value and (value.casefold().endswith(".pdf") or "pdf" in value.casefold()):
            return True
    for key in (
        "best_oa_location",
        "primary_location",
        "openAccessPdf",
        "open_access_pdf",
        "oa_pdf",
    ):
        value = paper.get(key)
        if isinstance(value, dict) and any(
            str(value.get(url_key) or "").strip()
            for url_key in ("pdf_url", "url_for_pdf", "oa_pdf_url", "best_pdf_url")
        ):
            return True
    for key in ("locations", "oa_locations", "open_access_locations", "openAccessLocations"):
        values = paper.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and any(
                    str(item.get(url_key) or "").strip()
                    for url_key in ("pdf_url", "url_for_pdf", "oa_pdf_url", "best_pdf_url")
                ):
                    return True
    return False


def _can_use_seed_pdf(paper: dict[str, Any]) -> bool:
    return bool(
        paper.get("seed_priority")
        or paper.get("has_seed_pdf")
        or str(paper.get("source") or "").casefold() == "user_seed"
        or str(paper.get("target_bucket") or "").casefold() == "seed"
    )


def _normalize_paper_filename(identifier: str) -> str:
    return identifier.replace(":", "_").replace("/", "_").replace("\\", "_").strip()


def _recommended_action(
    *,
    has_local_pdf: bool,
    has_seed_pdf: bool,
    evidence_level: str,
    access_level_hint: str,
    verification_status: str,
) -> str:
    # 未核验论文先做 metadata backfill / verification，避免直接把幻觉候选送进 T3。
    if verification_status == "retrieved":
        return "verify_metadata"
    if verification_status == "failed_verification":
        return "exclude_from_t3"
    if has_seed_pdf:
        return "read_seed_pdf"
    if has_local_pdf:
        return "read_local_pdf"
    if access_level_hint in {"LIKELY_FULL_TEXT", "POSSIBLE_FULL_TEXT"}:
        return "probe_pdf"
    if evidence_level == "ABSTRACT_ONLY":
        return "abstract_only"
    return "metadata_backlog"


def _paper_match_keys(paper: dict[str, Any]) -> set[str]:
    return {normalize_text_key(key) for key in paper_record_match_keys(paper)}


def _stable_noopenalex_queue_id(record: dict[str, Any]) -> str:
    from ..literature_identity import stable_noopenalex_id

    return stable_noopenalex_id(record)


def detect_duplicate_queries(queries: list[str], threshold: float = 0.7) -> dict[str, Any]:
    """检测检索式之间的重复度。

    Args:
        queries: 检索式列表
        threshold: 相似度阈值（0-1）

    Returns:
        {
            "duplicate_pairs": [(query1, query2, similarity), ...],
            "avg_similarity": float,
            "is_high_duplicate": bool
        }
    """
    from difflib import SequenceMatcher

    duplicate_pairs = []
    similarities = []

    for i, q1 in enumerate(queries):
        for j, q2 in enumerate(queries[i+1:], i+1):
            similarity = SequenceMatcher(None, q1.lower(), q2.lower()).ratio()
            similarities.append(similarity)

            if similarity >= threshold:
                duplicate_pairs.append((q1, q2, round(similarity, 2)))

    avg_similarity = sum(similarities) / len(similarities) if similarities else 0

    return {
        "duplicate_pairs": duplicate_pairs,
        "avg_similarity": round(avg_similarity, 2),
        "is_high_duplicate": avg_similarity > 0.6,  # 平均相似度 >60% 说明重复度高
        "warning": "检索式重复度过高，建议使用更多样化的关键词" if avg_similarity > 0.6 else None
    }


def analyze_dedup_rate(raw_count: int, dedup_count: int) -> dict[str, Any]:
    """分析去重率，给出机械覆盖 hint。

    Args:
        raw_count: 原始结果数量
        dedup_count: 去重后数量

    Returns:
        {
            "dedup_rate": float,
            "status": "good" | "warning" | "critical",
            "message": str
        }
    """
    if raw_count == 0:
        return {
            "dedup_rate": 0.0,
            "status": "critical",
            "message": "没有检索到任何论文"
        }

    dedup_rate = (raw_count - dedup_count) / raw_count

    if dedup_rate < 0.5:
        status = "good"
        message = f"去重率 {dedup_rate*100:.1f}%，raw query 重叠度较低"
    elif dedup_rate < 0.8:
        status = "warning"
        message = f"去重率 {dedup_rate*100:.1f}%，raw query 可能有一定重复；需由 Scout LLM 复核 coverage"
    else:
        status = "critical"
        message = f"去重率 {dedup_rate*100:.1f}%，raw query 高度重叠；需由 Scout LLM 重新审阅 domain_profile/query 设计"

    return {
        "dedup_rate": round(dedup_rate, 2),
        "status": status,
        "message": message
    }
