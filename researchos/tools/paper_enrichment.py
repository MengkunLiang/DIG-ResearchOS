"""论文数据增强工具。

只补齐 schema、provenance、可读性 hint 和队列优先级。source_type、
why_relevant、method_family、evidence_level 等需要学术判断的字段优先使用
LLM annotation；没有 annotation 时只写保守占位与复核标记。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agents._common import load_jsonl, normalize_text_key


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
            ):
                if key in annotation and annotation[key] not in (None, ""):
                    paper[key] = annotation[key]
            bucket = annotation.get("search_bucket") or annotation.get("query_bucket")
            if bucket not in (None, ""):
                paper["search_bucket"] = _normalize_search_bucket(bucket)
            if annotation.get("adjacent_field") is not None:
                paper["adjacent_field"] = bool(annotation.get("adjacent_field"))
            paper["llm_annotation_applied"] = True

        # 1. 转换 authors 格式
        authors = paper.get("authors", [])
        if authors and isinstance(authors[0], dict):
            # 从对象数组转为字符串数组
            paper["authors"] = [a.get("name", "Unknown") for a in authors]
        elif not authors:
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
        if _is_protected_search_bucket(paper):
            paper["adjacent_field"] = True

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


def _is_protected_search_bucket(paper: dict[str, Any]) -> bool:
    if bool(paper.get("adjacent_field")):
        return True
    bucket = _normalize_search_bucket(paper.get("search_bucket") or paper.get("query_bucket"))
    return bucket in {"adjacent_field", "theory_bridge"}


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
    deep_read_min: int = 18,
    deep_read_target: int = 24,
    deep_read_max: int = 30,
    probe_pool: int = 45,
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

    # 一旦 workspace 里已经存在 papers_verified，就必须优先把它当作权威池。
    # 这样即使上层 agent 误把 papers_dedup 传进来，也不会把未核验论文混进 T3 队列。
    authoritative_records = _prefer_verified_records(
        candidate_records=papers,
        verified_records=verified_records,
    )
    has_verified_pool = bool(verified_records)

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

        canonical_id = str(paper.get("canonical_id") or paper_id or title).strip()
        normalized_id = _normalize_paper_filename(canonical_id)
        has_local_pdf = bool(normalized_id and (local_pdf_dir / f"{normalized_id}.pdf").exists())
        access_est = float(paper.get("access_score_estimate", _estimate_access_score(paper)))
        access_score = max(access_est, 1.0 if has_local_pdf else access_est)
        relevance_score = float(paper.get("relevance_score", 0.0))
        verification_confidence = float(paper.get("verification_confidence", 0.0))
        verification_bonus = 0.25 if verification_status in {"metadata_verified", "pdf_verified"} else 0.0
        # methodological_signal 只是通用方法论文本 hint，不是领域相关性判断。
        meth_signal = float(paper.get("methodological_signal", 0.0))
        search_bucket = _normalize_search_bucket(paper.get("search_bucket") or paper.get("query_bucket"))
        protected_bucket = _is_protected_search_bucket(paper)
        bucket_bonus = 0.12 if protected_bucket else 0.0
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
            "access_level_hint": paper.get("access_level_hint", _estimate_access_level_hint(paper)),
            "seed_priority": is_seed,
            "has_local_pdf": has_local_pdf,
            "why_relevant": paper.get("why_relevant", ""),
            "queue_reason": "seed_paper" if is_seed else "ranked_candidate",
            "normalized_id": normalized_id,
            "url": paper.get("url", ""),
            "doi": paper.get("doi", ""),
            "verification_status": verification_status,
            "verification_confidence": round(verification_confidence, 2),
            "read_priority": read_priority,
            "read_priority_semantics": "queue_priority_hint_not_final_relevance",
            "methodological_signal_hint": round(meth_signal, 2),
            "search_bucket": search_bucket,
            "adjacent_field": protected_bucket,
            "protected_bucket_bonus": bucket_bonus,
        }
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

    # --- protected bucket selection: preserve LLM-labeled adjacent/theory material ---
    selected_count = min(len(ranked_records), max(probe_pool, deep_read_target, deep_read_min))
    protected_target = min(
        max(1, int(round(deep_read_target * 0.15))),
        max(0, selected_count),
    )
    protected_records = [
        record
        for record in ranked_records
        if record.get("adjacent_field") and not record.get("seed_priority")
    ][:protected_target]
    protected_ids = {id(record) for record in protected_records}
    queue_records: list[dict[str, Any]] = list(protected_records)

    # --- venue diversity bonus: 动态选择，避免同一 venue 占据过多 queue 名额 ---
    venue_counts: dict[str, int] = {}
    for record in queue_records:
        venue = record.get("venue", "unknown") or "unknown"
        venue_counts[venue] = venue_counts.get(venue, 0) + 1
    for record in ranked_records:
        if len(queue_records) >= selected_count:
            break
        if id(record) in protected_ids:
            continue
        venue = record.get("venue", "unknown") or "unknown"
        same_venue = venue_counts.get(venue, 0)
        # 0 同 venue: 1.0, 1 个: 0.7, 2 个: 0.4, >=3 个: 0.0
        venue_bonus = max(0.0, 1.0 - same_venue * 0.3)
        record["venue_diversity_bonus"] = round(venue_bonus, 2)
        # 把 venue bonus 加入 read_priority 作为最终排序依据
        record["final_priority"] = record["read_priority"] + 0.10 * venue_bonus + float(record.get("protected_bucket_bonus") or 0.0)
        queue_records.append(record)
        venue_counts[venue] = same_venue + 1
    for record in protected_records:
        venue_bonus = float(record.get("venue_diversity_bonus") or 0.0)
        record["final_priority"] = record["read_priority"] + 0.10 * venue_bonus + float(record.get("protected_bucket_bonus") or 0.0)

    # 重新按 final_priority 排序（seed 不参与 venue bonus，保持 seed 最高优先）
    queue_records.sort(
        key=lambda item: (
            not item["seed_priority"],
            -item["final_priority"],
            -item["relevance_score"],
            -item["access_score"],
            str(item["title"]).casefold(),
        )
    )
    for idx, record in enumerate(queue_records, start=1):
        record["queue_rank"] = idx
        record["target_bucket"] = (
            "seed" if record["seed_priority"] else "target"
            if idx <= deep_read_target
            else "overflow"
        )

    metadata = {
        "deep_read_min": deep_read_min,
        "deep_read_target": deep_read_target,
        "deep_read_max": deep_read_max,
        "probe_pool": probe_pool,
        "source_pool": "papers_verified" if has_verified_pool else "caller_supplied_records",
        "queue_count": len(queue_records),
        "seed_total": len(seed_papers),
        "seed_titles": seed_titles[:20],
        "seed_in_queue": sum(1 for item in queue_records if item["seed_priority"]),
        "protected_bucket_target": protected_target,
        "protected_bucket_in_queue": sum(1 for item in queue_records if item.get("adjacent_field")),
        "full_text_candidates": sum(
            1
            for item in queue_records
            if item.get("access_level_hint") in {"LIKELY_FULL_TEXT", "POSSIBLE_FULL_TEXT"}
        ),
        "verified_candidates": sum(
            1
            for item in queue_records
            if item["verification_status"] in {"metadata_verified", "pdf_verified"}
        ),
    }
    return queue_records, metadata


def _prefer_verified_records(
    *,
    candidate_records: list[dict[str, Any]],
    verified_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """优先把候选池收敛到 workspace 内已核验过的论文记录。

    设计原因：
    - 上层 agent 可能把 papers_dedup 整池传进 build_deep_read_queue；
    - 但当前系统语义要求 T3 队列建立在 papers_verified 之上；
    - 因此这里要在工具层做一次“以 verified 为准”的硬约束，而不是只靠 prompt。
    """

    if not verified_records:
        return candidate_records

    verified_by_key: dict[str, dict[str, Any]] = {}
    for record in verified_records:
        for key in _paper_match_keys(record):
            verified_by_key.setdefault(key, record)

    matched_records: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
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
            continue
        seen_ids.add(matched_identity)
        matched_records.append(matched)

    # 如果上层传入的候选集完全对不上，直接回退到完整 verified 池，
    # 避免因为调用参数不理想而意外生成空队列。
    return matched_records or verified_records


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
        normalized_id = _normalize_paper_filename(paper_id or title)
        has_local_pdf = bool(normalized_id and (local_pdf_dir / f"{normalized_id}.pdf").exists())
        has_seed_pdf = False
        if seed_pdf_dir.exists() and title:
            title_key = normalize_text_key(title)
            for path in seed_pdf_dir.glob("*.pdf"):
                if title_key and title_key in normalize_text_key(path.stem):
                    has_seed_pdf = True
                    break

        access_est = float(paper.get("access_score_estimate", _estimate_access_score(paper)))
        evidence_level = str(paper.get("evidence_level", _estimate_evidence_level(paper)))
        access_level_hint = str(paper.get("access_level_hint", _estimate_access_level_hint(paper)))
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
                "has_local_pdf": has_local_pdf,
                "has_seed_pdf": has_seed_pdf,
                "recommended_action": _recommended_action(
                    has_local_pdf=has_local_pdf,
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
        score += 0.15
    if url:
        score += 0.1
        if url.casefold().endswith(".pdf"):
            score += 0.15
    if abstract:
        score += 0.2
    if paper.get("pdf_url"):
        score += 0.25

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
    if access_score >= 0.8:
        return "LIKELY_FULL_TEXT"
    if access_score >= 0.55:
        return "POSSIBLE_FULL_TEXT"
    if str(paper.get("abstract", "")).strip():
        return "ABSTRACT_OR_METADATA"
    return "METADATA_ONLY"


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
    if has_local_pdf:
        return "read_local_pdf"
    if has_seed_pdf:
        return "read_seed_pdf"
    if access_level_hint in {"LIKELY_FULL_TEXT", "POSSIBLE_FULL_TEXT"}:
        return "probe_pdf"
    if evidence_level == "ABSTRACT_ONLY":
        return "abstract_only"
    return "metadata_backlog"


def _paper_match_keys(paper: dict[str, Any]) -> set[str]:
    external_ids = paper.get("externalIds") or {}
    candidates = {
        str(paper.get("id", "")),
        str(paper.get("canonical_id", "")),
        str(paper.get("title", "")),
        str(paper.get("doi", "")),
        str(paper.get("url", "")),
        str(external_ids.get("ArXiv", "")),
        str(external_ids.get("DOI", "")),
    }
    return {normalize_text_key(candidate) for candidate in candidates if str(candidate).strip()}


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
