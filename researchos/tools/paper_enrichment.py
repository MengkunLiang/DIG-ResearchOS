"""论文数据增强工具。

自动补充缺失的字段，提高数据质量。
"""

from __future__ import annotations

from typing import Any


def enrich_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """增强论文数据，自动补充缺失字段。

    功能：
    1. 自动推断 source_type（根据 venue）
    2. 自动生成 why_relevant（基于 relevance_score）
    3. 转换 authors 格式（对象数组 -> 字符串数组）
    4. 补充缺失的必需字段（使用默认值）
    5. 标记数据质量（是否缺少 abstract）

    Args:
        papers: 原始论文列表

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
            if score >= 0.8:
                paper["why_relevant"] = "高度相关：标题和摘要与研究方向高度匹配"
            elif score >= 0.6:
                paper["why_relevant"] = "相关：部分内容与研究方向相关"
            else:
                paper["why_relevant"] = "可能相关：包含部分相关关键词"

        # 4. 补充缺失的必需字段
        if "abstract" not in paper or not paper["abstract"]:
            paper["abstract"] = ""
            paper["_missing_abstract"] = True  # 标记数据质量问题

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
