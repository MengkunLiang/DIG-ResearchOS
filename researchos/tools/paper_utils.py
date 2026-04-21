"""论文处理工具。

提供确定性的论文去重、评分、查询扩展等功能。
这些功能不应该由 LLM 执行，而应该由确定性算法执行。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


def deduplicate_papers(
    papers: list[dict[str, Any]],
    doi_dedup: bool = True,
    title_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """去重论文列表。

    Args:
        papers: 论文列表
        doi_dedup: 是否进行 DOI 精确去重
        title_threshold: 标题相似度阈值（0-1），超过此值视为重复

    Returns:
        去重后的论文列表
    """
    if not papers:
        return []

    result = []
    seen_dois = set()
    seen_titles = []

    for paper in papers:
        # DOI 精确去重
        if doi_dedup and paper.get("doi"):
            doi = paper["doi"].strip().lower()
            if doi and doi in seen_dois:
                continue
            if doi:
                seen_dois.add(doi)

        # 标题相似度去重
        title = paper.get("title", "").strip()
        if not title:
            continue

        is_duplicate = False
        for seen_title in seen_titles:
            similarity = SequenceMatcher(None, title.lower(), seen_title.lower()).ratio()
            if similarity >= title_threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            seen_titles.append(title)
            result.append(paper)

    return result


def score_papers(
    papers: list[dict[str, Any]],
    keywords: list[str],
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """为论文列表评分。

    评分维度：
    - source_type: 来源类型权重
    - year: 年份权重（越新越高）
    - citation_count: 引用数权重
    - keyword_match: 关键词匹配度

    Args:
        papers: 论文列表
        keywords: 关键词列表
        weights: 各维度权重，默认 {"source_type": 0.2, "year": 0.3, "citation": 0.2, "keyword": 0.3}

    Returns:
        添加了 relevance_score 字段的论文列表
    """
    if weights is None:
        weights = {
            "source_type": 0.2,
            "year": 0.3,
            "citation": 0.2,
            "keyword": 0.3,
        }

    current_year = 2026  # 可以从系统获取

    for paper in papers:
        scores = {}

        # 1. source_type 权重
        source_type = paper.get("source_type", "preprint")
        source_type_map = {
            "top_conference": 1.0,
            "journal": 0.8,
            "preprint": 0.6,
            "workshop": 0.5,
            "blog": 0.3,
        }
        scores["source_type"] = source_type_map.get(source_type, 0.5)

        # 2. year 权重
        year = paper.get("year")
        if year and isinstance(year, int):
            year_diff = current_year - year
            if year_diff <= 0:
                scores["year"] = 1.0
            elif year_diff == 1:
                scores["year"] = 0.9
            elif year_diff == 2:
                scores["year"] = 0.8
            elif year_diff <= 5:
                scores["year"] = 0.6
            else:
                scores["year"] = 0.4
        else:
            scores["year"] = 0.5

        # 3. citation_count 权重
        citation_count = paper.get("citation_count", 0)
        if citation_count >= 100:
            scores["citation"] = 1.0
        elif citation_count >= 50:
            scores["citation"] = 0.8
        elif citation_count >= 10:
            scores["citation"] = 0.6
        else:
            scores["citation"] = 0.4

        # 4. keyword 匹配度
        title = paper.get("title", "").lower()
        abstract = paper.get("abstract", "").lower()
        text = f"{title} {abstract}"

        matched_keywords = sum(1 for kw in keywords if kw.lower() in text)
        scores["keyword"] = min(1.0, matched_keywords / max(1, len(keywords)))

        # 计算加权总分
        relevance_score = sum(scores[k] * weights[k] for k in scores)
        paper["relevance_score"] = round(relevance_score, 2)

    return papers


def expand_queries(
    seed_papers: list[dict[str, Any]],
    topic: str,
    max_queries: int = 10,
) -> list[str]:
    """基于种子论文和主题扩展检索式。

    Args:
        seed_papers: 种子论文列表（可以为空列表）
        topic: 研究主题
        max_queries: 最大检索式数量

    Returns:
        检索式列表
    """
    queries = []

    # 1. 基础查询：主题本身
    queries.append(topic)

    # 2. 从种子论文标题提取关键术语
    if seed_papers:
        for paper in seed_papers[:3]:  # 只用前 3 篇
            title = paper.get("title", "")
            # 提取标题中的关键短语（简单实现：提取大写开头的连续词）
            key_phrases = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", title)
            for phrase in key_phrases[:2]:  # 每篇论文最多 2 个短语
                if len(phrase.split()) >= 2:  # 至少 2 个词
                    queries.append(phrase)

    # 3. 添加领域限定词（避免歧义）
    domain_variants = []
    if "memory" in topic.lower() or "retrieval" in topic.lower():
        # 避免与心理学混淆
        domain_variants = [
            f"LLM {topic}",
            f"AI {topic}",
            f"machine learning {topic}",
        ]
    elif "agent" in topic.lower():
        domain_variants = [
            f"AI {topic}",
            f"autonomous {topic}",
            f"intelligent {topic}",
        ]

    queries.extend(domain_variants[:3])

    # 4. 添加年份限定（最近论文）
    queries.append(f"{topic} 2024-2026")
    queries.append(f"{topic} 2023-2025")

    # 去重并限制数量
    unique_queries = []
    seen = set()
    for q in queries:
        q_lower = q.lower().strip()
        if q_lower and q_lower not in seen:
            seen.add(q_lower)
            unique_queries.append(q)

    return unique_queries[:max_queries]


def filter_by_domain(
    papers: list[dict[str, Any]],
    target_domain: str = "cs",
) -> list[dict[str, Any]]:
    """按领域过滤论文。

    Args:
        papers: 论文列表
        target_domain: 目标领域（"cs" 或 "bio"）

    Returns:
        过滤后的论文列表
    """
    if target_domain == "cs":
        # CS 领域的会议/期刊
        cs_venues = {
            "neurips", "icml", "iclr", "cvpr", "iccv", "eccv",
            "acl", "emnlp", "naacl", "coling",
            "aaai", "ijcai", "kdd", "www", "sigir",
            "arxiv", "jmlr", "tacl",
        }

        # CS 领域的关键词
        cs_keywords = {
            "neural", "learning", "model", "algorithm", "network",
            "deep", "machine", "ai", "artificial intelligence",
            "computer", "computational", "llm", "transformer",
        }

        result = []
        for paper in papers:
            venue = paper.get("venue", "").lower()
            title = paper.get("title", "").lower()
            abstract = paper.get("abstract", "").lower()

            # 检查 venue
            is_cs_venue = any(v in venue for v in cs_venues)

            # 检查关键词
            text = f"{title} {abstract}"
            has_cs_keywords = any(kw in text for kw in cs_keywords)

            if is_cs_venue or has_cs_keywords:
                result.append(paper)

        return result

    # 其他领域暂不支持
    return papers


def generate_search_log(
    raw_count: int,
    dedup_count: int,
    queries: list[str],
    query_results: dict[str, int] | None = None,
) -> str:
    """生成检索日志（基于实际数据，不允许编造）。

    Args:
        raw_count: 原始检索结果数量
        dedup_count: 去重后数量
        queries: 使用的检索式列表
        query_results: 每个检索式的结果数量（可选）

    Returns:
        Markdown 格式的检索日志
    """
    log = "# T2 Scout 检索日志\n\n"

    # 检索式
    log += "## 检索式\n\n"
    for i, query in enumerate(queries, 1):
        if query_results and query in query_results:
            count = query_results[query]
            log += f"{i}. \"{query}\" → {count} 篇\n"
        else:
            log += f"{i}. \"{query}\"\n"

    # 统计数据
    log += "\n## 检索统计\n\n"
    log += f"- 原始结果: {raw_count} 篇\n"
    log += f"- 去重后: {dedup_count} 篇\n"
    log += f"- 去重率: {(1 - dedup_count / max(1, raw_count)) * 100:.1f}%\n"

    return log
