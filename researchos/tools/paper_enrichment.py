"""论文数据增强工具。

自动补充缺失的字段，提高数据质量。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agents._common import load_jsonl, normalize_text_key


def enrich_papers(
    papers: list[dict[str, Any]],
    keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """增强论文数据，自动补充缺失字段。

    功能：
    1. 自动推断 source_type（根据 venue）
    2. 自动生成 why_relevant（基于 relevance_score 和关键词匹配）
    3. 转换 authors 格式（对象数组 -> 字符串数组）
    4. 补充缺失的必需字段（使用默认值）
    5. 标记数据质量（是否缺少 abstract）

    Args:
        papers: 原始论文列表
        keywords: 关键词列表（可选，用于生成更具体的 why_relevant）

    Returns:
        增强后的论文列表
    """
    enriched = []

    for paper in papers:
        # 1. 转换 authors 格式
        authors = paper.get("authors", [])
        if authors and isinstance(authors[0], dict):
            # 从对象数组转为字符串数组
            paper["authors"] = [a.get("name", "Unknown") for a in authors]
        elif not authors:
            paper["authors"] = ["Unknown"]

        # 2. 自动推断 source_type
        if "source_type" not in paper:
            venue = paper.get("venue", "").lower()
            if any(conf in venue for conf in ["neurips", "icml", "iclr", "cvpr", "acl", "emnlp", "naacl"]):
                paper["source_type"] = "top_conference"
            elif any(j in venue for j in ["nature", "science", "jmlr", "tacl"]):
                paper["source_type"] = "journal"
            elif "arxiv" in venue or paper.get("source") == "arxiv":
                paper["source_type"] = "preprint"
            elif "workshop" in venue:
                paper["source_type"] = "workshop"
            else:
                paper["source_type"] = "preprint"  # 默认

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
            else:
                # 默认关键词匹配分析
                common_kws = ["agent", "memory", "retrieval", "llm", "transformer", "attention"]
                for kw in common_kws:
                    if kw in title:
                        matched.append(f"标题包含「{kw}」")
                    if kw in abstract:
                        matched.append(f"摘要包含「{kw}」")

            if matched:
                # 有具体匹配时，使用具体的匹配原因
                reason = "；".join(matched[:3])  # 最多保留3个原因
                paper["why_relevant"] = reason
            elif score >= 0.8:
                paper["why_relevant"] = "高度相关：标题和摘要与研究方向高度匹配"
            elif score >= 0.6:
                paper["why_relevant"] = "相关：部分内容与研究方向相关"
            else:
                paper["why_relevant"] = "可能相关：包含部分相关关键词"

        # 4. 补充缺失的必需字段
        if "abstract" not in paper or not paper["abstract"]:
            paper["abstract"] = ""
            paper["_missing_abstract"] = True  # 标记数据质量问题

        if "access_score_estimate" not in paper:
            paper["access_score_estimate"] = _estimate_access_score(paper)

        if "access_score" not in paper:
            paper["access_score"] = paper["access_score_estimate"]

        if "evidence_level" not in paper:
            paper["evidence_level"] = _estimate_evidence_level(paper)

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

        # 5. 确保 year 是整数
        if "year" in paper and paper["year"]:
            try:
                paper["year"] = int(paper["year"])
            except (ValueError, TypeError):
                paper["year"] = 2024  # 默认值
        else:
            paper["year"] = 2024

        enriched.append(paper)

    return enriched


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
    2. relevance_score 与 access_score_estimate 联合排序；
    3. probe_pool 故意大于 target，给后续下载/解析失败留冗余；
    4. 输出单独 artifact，避免 T3 被整个 dedup 池绑死。
    """

    seed_path = workspace_dir / "user_seeds" / "seed_papers.jsonl"
    seed_papers = load_jsonl(seed_path) if seed_path.exists() else []
    local_pdf_dir = workspace_dir / "literature" / "pdfs"

    seed_keys: set[str] = set()
    seed_titles: list[str] = []
    for seed in seed_papers:
        seed_keys.update(_paper_match_keys(seed))
        seed_title = str(seed.get("title", "")).strip()
        if seed_title:
            seed_titles.append(str(seed.get("title", "")).strip())

    ranked_records: list[dict[str, Any]] = []
    for paper in papers:
        verification_status = str(paper.get("verification_status", "retrieved")).strip() or "retrieved"
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
        read_priority = round(
            (1.0 if is_seed else 0.0) * 100.0
            + relevance_score * 0.55
            + access_score * 0.25
            + verification_confidence * 0.20
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

    selected_count = min(len(ranked_records), max(probe_pool, deep_read_target, deep_read_min))
    queue_records = ranked_records[:selected_count]
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
        "queue_count": len(queue_records),
        "seed_total": len(seed_papers),
        "seed_titles": seed_titles[:20],
        "seed_in_queue": sum(1 for item in queue_records if item["seed_priority"]),
        "full_text_candidates": sum(
            1
            for item in queue_records
            if item["evidence_level"] in {"FULL_TEXT", "PARTIAL_TEXT"}
        ),
        "verified_candidates": sum(
            1
            for item in queue_records
            if item["verification_status"] in {"metadata_verified", "pdf_verified"}
        ),
    }
    return queue_records, metadata


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
    3. 哪些论文只有摘要，哪些几乎只有 metadata。
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
        records.append(
            {
                "paper_id": str(paper.get("canonical_id") or paper_id),
                "title": title,
                "source": paper.get("source", ""),
                "year": paper.get("year"),
                "relevance_score": round(float(paper.get("relevance_score", 0.0)), 2),
                "access_score_estimate": round(access_est, 2),
                "evidence_level": evidence_level,
                "verification_status": str(paper.get("verification_status", "retrieved")),
                "verification_confidence": round(float(paper.get("verification_confidence", 0.0)), 2),
                "has_local_pdf": has_local_pdf,
                "has_seed_pdf": has_seed_pdf,
                "recommended_action": _recommended_action(
                    has_local_pdf=has_local_pdf,
                    has_seed_pdf=has_seed_pdf,
                    evidence_level=evidence_level,
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

    lines = [
        "# Access Audit",
        "",
        f"- 候选论文总数: {len(records)}",
        f"- `literature/pdfs/` 本地 PDF: {local_pdf_count}",
        f"- `user_seeds/pdfs/` 可匹配的 seed PDF: {seed_pdf_count}",
        f"- `FULL_TEXT`: {evidence_counts['FULL_TEXT']}",
        f"- `PARTIAL_TEXT`: {evidence_counts['PARTIAL_TEXT']}",
        f"- `ABSTRACT_ONLY`: {evidence_counts['ABSTRACT_ONLY']}",
        f"- `METADATA_ONLY`: {evidence_counts['METADATA_ONLY']}",
        "",
        "## Top Candidates",
        "",
        "| Rank | Title | Source | Relevance | Access | Evidence | Verified | Local PDF | Seed PDF | Recommended Action |",
        "|---|---|---|---:|---:|---|---|---|---|---|",
    ]

    for idx, item in enumerate(records[:top_n], start=1):
        lines.append(
            "| {rank} | {title} | {source} | {rel:.2f} | {acc:.2f} | {evi} | {ver} ({conf:.2f}) | {local} | {seed} | {action} |".format(
                rank=idx,
                title=str(item["title"]).replace("|", "/"),
                source=str(item["source"]).replace("|", "/"),
                rel=float(item["relevance_score"]),
                acc=float(item["access_score_estimate"]),
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
    if str(paper.get("source_type", "")).casefold() in {"top_conference", "journal"}:
        score += 0.05

    return round(min(1.0, score), 2)


def _estimate_evidence_level(paper: dict[str, Any]) -> str:
    """基于 metadata 给出粗粒度证据等级。"""

    access_score = float(paper.get("access_score_estimate", _estimate_access_score(paper)))
    has_abstract = bool(str(paper.get("abstract", "")).strip())
    if access_score >= 0.8:
        return "FULL_TEXT"
    if access_score >= 0.55:
        return "PARTIAL_TEXT"
    if has_abstract:
        return "ABSTRACT_ONLY"
    return "METADATA_ONLY"


def _normalize_paper_filename(identifier: str) -> str:
    return identifier.replace(":", "_").replace("/", "_").replace("\\", "_").strip()


def _recommended_action(
    *,
    has_local_pdf: bool,
    has_seed_pdf: bool,
    evidence_level: str,
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
    if evidence_level in {"FULL_TEXT", "PARTIAL_TEXT"}:
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
    """分析去重率，给出建议。

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
        message = f"去重率 {dedup_rate*100:.1f}%，检索式多样性良好"
    elif dedup_rate < 0.8:
        status = "warning"
        message = f"去重率 {dedup_rate*100:.1f}%，检索式有一定重复，建议增加多样性"
    else:
        status = "critical"
        message = f"去重率 {dedup_rate*100:.1f}%，检索式重复度过高！建议重新设计检索式"

    return {
        "dedup_rate": round(dedup_rate, 2),
        "status": status,
        "message": message
    }
