"""论文处理工具。

这里的函数只处理机械、可复现的文献管线步骤：去重、query 合并、
metadata priority hint 等。领域语义、相关性解释、方法家族和最终取舍
必须来自调用方 LLM 或用户提供的 profile，不能在工具层写死。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..time_utils import current_utc_year, format_year_window

# ---------------------------------------------------------------------------
# Methodological signal keywords
# ---------------------------------------------------------------------------

METHODOLOGICAL_KEYWORDS: list[str] = [
    # Rethinking / revisiting
    "rethinking", "revisiting", "revisit", "reexamining",
    "re-examining", "re-thinking",
    # Negative results / limitations
    "limitation", "fails", "failure mode", "negative result",
    "does not improve", "doesn't work", "no improvement",
    "underperform", "shortcoming", "pitfall",
    # Mechanism / understanding
    "why does", "understanding", "analyzing",
    "explanation", "mechanism", "ablation study",
    "factor analysis", "disentangling",
    # Reverse / removal
    "without", "removing", "ablating", "dropping",
    "disable", "turn off", "masking",
]


def compute_methodological_signal(paper: dict[str, Any]) -> float:
    """计算标题/摘要里的方法论提示强度。

    这是通用文本 hint，用于把 review/rethinking/ablation 等论文排到更容易
    被 T3/T4 看到的位置；它不是领域相关性判断，也不代表论文一定重要。
    Returns 0.0 ~ 1.0。
    """
    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    hits = sum(1 for kw in METHODOLOGICAL_KEYWORDS if kw in text)
    if hits == 0:
        return 0.0
    if hits == 1:
        return 0.5
    return 1.0


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
    current_year: int | None = None,
) -> list[dict[str, Any]]:
    """为论文列表生成检索优先级 hint。

    这个函数输出的 `relevance_score` 是历史字段名，语义上应理解为
    metadata/search priority hint，而不是最终学术相关性。真正的 why_relevant、
    source_type、method_family 和是否进入核心综述，应由 Scout/Reader LLM 基于
    domain_profile 和论文内容判断。

    机械维度：
    - source_type: 来源类型权重；缺失时为 unknown 中性 hint，不推断成 preprint
    - year: 年份权重（越新越高）
    - citation_count: 引用数权重
    - keyword_match: 关键词匹配度

    Args:
        papers: 论文列表
        keywords: 关键词列表
        weights: 各维度权重；默认不把 methodological_signal 纳入排序，只作为 hint 输出

    Returns:
        添加了 relevance_score 字段和分项 hint 的论文列表
    """
    if weights is None:
        weights = {
            "source_type": 0.15,
            "year": 0.25,
            "citation": 0.10,
            "keyword": 0.40,
            "methodological_signal": 0.0,
            "venue_diversity_bonus": 0.10,
        }

    scoring_year = current_year if current_year is not None else current_utc_year()

    for paper in papers:
        scores = {}
        year_raw = paper.get("year")
        if year_raw and not isinstance(year_raw, int):
            try:
                paper["year"] = int(year_raw)
            except (TypeError, ValueError):
                paper["year"] = None

        # 1. source_type 权重
        source_type = paper.get("source_type") or "unknown"
        source_type_map = {
            "top_conference": 1.0,
            "journal": 0.8,
            "preprint": 0.6,
            "workshop": 0.5,
            "unknown": 0.5,
            "blog": 0.3,
        }
        scores["source_type"] = source_type_map.get(source_type, 0.5)

        # 2. year 权重
        year = paper.get("year")
        if year and isinstance(year, int):
            year_diff = scoring_year - year
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

        # 5. methodological_signal
        scores["methodological_signal"] = compute_methodological_signal(paper)
        if scores["methodological_signal"]:
            paper["methodological_signal_hint"] = scores["methodological_signal"]

        # 6. venue_diversity_bonus (默认 0.5；build_deep_read_queue 中动态调整)
        scores["venue_diversity_bonus"] = 0.5

        # 计算加权总分
        relevance_score = sum(scores[k] * weights[k] for k in scores)
        paper["relevance_score"] = round(relevance_score, 2)
        paper["priority_score_hint"] = paper["relevance_score"]
        paper["relevance_score_semantics"] = "metadata_priority_hint_requires_llm_review"
        paper["relevance_score_components"] = {key: round(value, 3) for key, value in scores.items()}
        paper["methodological_signal"] = scores["methodological_signal"]

    return papers


def expand_queries(
    seed_papers: list[dict[str, Any]],
    topic: str,
    max_queries: int = 10,
    current_year: int | None = None,
    domain_profile: dict[str, Any] | None = None,
    llm_queries: list[str] | None = None,
    domain_hints: list[str] | None = None,
) -> list[str]:
    """基于种子论文和主题扩展检索式。

    This tool only performs mechanical query assembly and deduplication.
    Domain-specific synonyms, ambiguity guards, venue/category names, and
    cross-field terminology should come from the calling LLM through
    ``domain_profile``, ``domain_hints``, or ``llm_queries``. The function
    deliberately avoids built-in AI/CS keyword expansions so it does not
    hardcode research judgment into the tool layer.

    Args:
        seed_papers: 种子论文列表（可以为空列表）
        topic: 研究主题
        max_queries: 最大检索式数量
        domain_profile: LLM 归纳出的领域 profile，可包含 query_prefixes,
            query_variants, include_keywords, exclude_keywords, ambiguity_terms,
            related_concepts, venue_terms 等字段。
        llm_queries: LLM 已经设计好的检索式，工具会合并去重。
        domain_hints: LLM 给出的短领域限定词或概念，工具会与 topic 组合。

    Returns:
        检索式列表
    """
    queries = []

    # 1. 基础查询：主题本身
    queries.append(topic)

    profile = domain_profile or {}
    if llm_queries:
        queries.extend(q for q in llm_queries if str(q).strip())

    # 2. 从种子论文标题提取关键术语
    if seed_papers:
        for paper in seed_papers[:3]:  # 只用前 3 篇
            title = paper.get("title", "")
            # 提取标题中的关键短语（简单实现：提取大写开头的连续词）
            key_phrases = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", title)
            for phrase in key_phrases[:2]:  # 每篇论文最多 2 个短语
                if len(phrase.split()) >= 2:  # 至少 2 个词
                    queries.append(phrase)

    # 3. 合并 LLM 提供的领域限定词、别名和相关概念。
    hint_terms: list[str] = []
    hint_terms.extend(domain_hints or [])
    for key in (
        "query_prefixes",
        "query_variants",
        "include_keywords",
        "related_concepts",
        "ambiguity_guards",
        "venue_terms",
    ):
        hint_terms.extend(_as_str_list(profile.get(key)))

    for term in hint_terms[: max(0, max_queries * 2)]:
        cleaned = str(term).strip()
        if not cleaned:
            continue
        if topic.lower() in cleaned.lower():
            queries.append(cleaned)
        else:
            queries.append(f"{cleaned} {topic}")

    # 4. 添加年份限定（最近论文）。这是机械时间窗口，不是领域判断。
    queries.append(f"{topic} {format_year_window(2, current_year=current_year)}")
    queries.append(f"{topic} {format_year_window(2, current_year=current_year, lag_years=1)}")

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
    target_domain: str = "general",
    domain_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 LLM 提供的领域 profile 做保守过滤。

    The tool no longer contains a built-in CS/AI classifier. If the caller does
    not provide a profile, papers are returned unchanged. The LLM should first
    infer the target domain, inclusion/exclusion concepts, and ambiguity risks
    from project context, then pass those terms here for repeatable filtering.

    Args:
        papers: 论文列表
        target_domain: 目标领域标签，仅用于记录/兼容，不触发内置领域知识
        domain_profile: LLM 提供的 profile，可包含 include_keywords,
            include_venues, exclude_keywords, exclude_venues, keep_if_uncertain,
            min_include_matches。

    Returns:
        过滤后的论文列表
    """
    profile = domain_profile or {}
    include_keywords = _lower_terms(profile.get("include_keywords"))
    include_venues = _lower_terms(profile.get("include_venues") or profile.get("venue_terms"))
    exclude_keywords = _lower_terms(profile.get("exclude_keywords"))
    exclude_venues = _lower_terms(profile.get("exclude_venues"))
    if not any((include_keywords, include_venues, exclude_keywords, exclude_venues)):
        return papers

    min_include_matches = int(profile.get("min_include_matches") or 1)
    keep_if_uncertain = bool(profile.get("keep_if_uncertain", False))

    result = []
    for paper in papers:
        venue = str(paper.get("venue", "")).casefold()
        text = " ".join(
            str(paper.get(key, ""))
            for key in ("title", "abstract", "keywords", "source", "source_type")
        ).casefold()

        if any(term in venue for term in exclude_venues) or any(term in text for term in exclude_keywords):
            continue

        positive_matches = sum(1 for term in include_keywords if term in text)
        positive_matches += sum(1 for term in include_venues if term in venue)
        has_positive_profile = bool(include_keywords or include_venues)
        if not has_positive_profile or positive_matches >= min_include_matches or keep_if_uncertain:
            kept = dict(paper)
            kept.setdefault("domain_filter", {})
            if isinstance(kept["domain_filter"], dict):
                kept["domain_filter"].update(
                    {
                        "target_domain": target_domain,
                        "positive_matches": positive_matches,
                        "profile_driven": True,
                    }
                )
            result.append(kept)

    return result


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _lower_terms(value: Any) -> list[str]:
    return [term.casefold().strip() for term in _as_str_list(value) if term.casefold().strip()]


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
