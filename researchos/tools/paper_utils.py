"""论文处理工具。

这里的函数只处理机械、可复现的文献管线步骤：去重、query 合并、
metadata priority hint 等。领域语义、相关性解释、方法家族和最终取舍
必须来自调用方 LLM 或用户提供的 profile，不能在工具层写死。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from collections import Counter, defaultdict
from typing import Any

from ..time_utils import current_utc_year, format_year_window
from ..literature_identity import normalize_loose_identity_key, paper_record_match_keys

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
    text = f"{_paper_text(paper.get('title'))} {_paper_text(paper.get('abstract'))}".lower()
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
    """去重论文列表，并合并重复记录的 mechanical provenance。

    Args:
        papers: 论文列表
        doi_dedup: 是否进行 DOI 精确去重
        title_threshold: 标题相似度阈值（0-1），超过此值视为重复

    Returns:
        去重后的论文列表
    """
    if not papers:
        return []

    result: list[dict[str, Any]] = []
    identity_to_index: dict[str, int] = {}
    seen_titles: list[tuple[str, int]] = []

    for paper in papers:
        if not isinstance(paper, dict):
            continue
        # DOI 精确去重
        duplicate_index: int | None = None
        if doi_dedup:
            for key in _dedup_identity_keys(paper):
                if key in identity_to_index:
                    duplicate_index = identity_to_index[key]
                    break

        # 标题相似度去重
        title = _paper_title_text(paper.get("title"))
        if not title:
            continue
        title_key = _normalized_title_for_dedup(title)

        if duplicate_index is None:
            for seen_title, seen_index in seen_titles:
                if not title_key or not seen_title:
                    continue
                similarity = SequenceMatcher(None, title_key, seen_title).ratio()
                if similarity >= title_threshold:
                    duplicate_index = seen_index
                    break

        if duplicate_index is not None:
            result[duplicate_index] = _merge_duplicate_paper_records(
                result[duplicate_index],
                paper,
            )
            for key in _dedup_identity_keys(result[duplicate_index]):
                identity_to_index.setdefault(key, duplicate_index)
            continue

        index = len(result)
        result.append(dict(paper))
        if doi_dedup:
            for key in _dedup_identity_keys(paper):
                identity_to_index.setdefault(key, index)
        seen_titles.append((title_key, index))

    return result


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


def _paper_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(text for item in value if (text := _paper_text(item)))
    if isinstance(value, dict):
        return " ".join(text for item in value.values() if (text := _paper_text(item)))
    if value is None:
        return ""
    return str(value)


def _normalized_title_for_dedup(title: Any) -> str:
    """Normalize titles before fuzzy matching.

    Raw API titles differ mostly by punctuation, Unicode dashes, HTML entities,
    or capitalization. Matching on the loose normalized title keeps DOI/arXiv
    exact matching as the primary key while still merging obvious same-title
    records across Crossref/OpenAlex/arXiv.
    """

    key = normalize_loose_identity_key(str(title or ""))
    if len(key) < 12 or len(key.split()) < 3:
        return ""
    return key


def _dedup_identity_keys(paper: dict[str, Any]) -> set[str]:
    """Return conservative exact identity keys for deduplication.

    This intentionally excludes loose title-only keys; near-title matching is
    handled separately by `title_threshold`. Including DOI/OpenAlex/arXiv/S2
    aliases here prevents the same paper from surviving as two records when
    one source stores the identifier under `externalIds`.
    """

    keys: set[str] = set()
    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    doi = _normalize_doi_key(paper.get("doi") or external_ids.get("DOI"))
    if doi:
        keys.add(f"doi:{doi}")
    for candidate in (
        paper.get("canonical_id"),
        paper.get("id"),
        paper.get("paperId"),
        external_ids.get("OpenAlex"),
        paper.get("openalex_id"),
    ):
        value = str(candidate or "").strip()
        if value.startswith("https://openalex.org/") or value.startswith("https://api.openalex.org/works/"):
            value = value.rstrip("/").split("/")[-1]
        if value.startswith("W") and value[1:].isdigit():
            keys.add(f"openalex:{value}")
    arxiv = str(external_ids.get("ArXiv") or paper.get("arxiv_id") or "").strip().casefold()
    if arxiv:
        keys.add(f"arxiv:{arxiv.removeprefix('arxiv:')}")
    corpus_id = str(
        external_ids.get("CorpusId")
        or external_ids.get("CorpusID")
        or paper.get("corpusId")
        or paper.get("CorpusId")
        or ""
    ).strip()
    if corpus_id:
        keys.add(f"semantic_scholar_corpus:{corpus_id.casefold()}")
    # Keep compatibility with the shared matcher for exact identifier tokens.
    for key in paper_record_match_keys(paper):
        if key.startswith(("doi ", "doi:", "arxiv ", "arxiv:")):
            keys.add(key)
        elif key.startswith("w") and key[1:].isdigit():
            keys.add(f"openalex:{key.upper()}")
    return {key for key in keys if key}


def _normalize_doi_key(value: Any) -> str:
    doi = str(value or "").strip().casefold()
    for prefix in ("doi:", "https://doi.org/", "http://doi.org/", "https://dx.doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


def _merge_duplicate_paper_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge duplicate paper records without adding scholarly judgment.

    The merge only preserves factual/provenance metadata: identifiers, search
    buckets, bridge sources, references, abstracts, and open-access/PDF hints.
    It deliberately does not decide relevance or change semantic_screen unless
    the existing record lacks one.
    """

    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
            continue

        if key == "abstract":
            if len(str(value)) > len(str(current)):
                merged[key] = value
        elif key == "citation_count":
            try:
                merged[key] = max(int(current or 0), int(value or 0))
            except (TypeError, ValueError):
                pass
        elif key == "externalIds" and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {
                **current,
                **{k: v for k, v in value.items() if v not in (None, "", [], {})},
            }
        elif key in {
            "references",
            "referenced_works",
            "related_works",
            "locations",
            "oa_locations",
            "open_access_locations",
            "openAccessLocations",
            "open_access_pdfs",
            "recalled_by_bridges",
            "contributed_bridges",
        } and isinstance(current, list) and isinstance(value, list):
            merged[key] = _dedupe_list_payload([*current, *value])
        elif key in {
            "openAccessPdf",
            "open_access_pdf",
            "oa_pdf",
            "open_access",
            "best_oa_location",
            "primary_location",
        } and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **{k: v for k, v in value.items() if v not in (None, "", [], {})}}
        elif key in {"bridge_id", "search_bucket", "query_bucket", "source_bucket", "source_query", "source_tool"}:
            plural_key = {
                "bridge_id": "bridge_ids",
                "search_bucket": "search_buckets",
                "query_bucket": "query_buckets",
                "source_bucket": "source_buckets",
                "source_query": "source_queries",
                "source_tool": "source_tools",
            }[key]
            _append_unique_scalar(merged, plural_key, current)
            _append_unique_scalar(merged, plural_key, value)
            if key == "bridge_id":
                _append_unique_scalar(merged, "recalled_by_bridges", current)
                _append_unique_scalar(merged, "recalled_by_bridges", value)
        elif key in {"citation_snowball_source_id", "citation_snowball_source_title"}:
            _append_unique_scalar(merged, f"{key}s", current)
            _append_unique_scalar(merged, f"{key}s", value)
        elif key in {
            "pdf_url",
            "open_access_pdf_url",
            "oa_pdf_url",
            "best_pdf_url",
            "full_text_url",
            "pmc_pdf_url",
            "url_for_pdf",
            "landing_page_url",
        }:
            _append_unique_scalar(merged, f"{key}s", current)
            _append_unique_scalar(merged, f"{key}s", value)
        elif key == "source":
            sources = [item for item in str(current).split("+") if item]
            incoming_source = str(value)
            if incoming_source and incoming_source not in sources:
                sources.append(incoming_source)
                merged[key] = "+".join(sources)

    _normalize_merged_bridge_fields(merged)
    return merged


def _append_unique_scalar(record: dict[str, Any], key: str, value: Any) -> None:
    values = record.get(key)
    if not isinstance(values, list):
        values = []
    if isinstance(value, (list, tuple, set)):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = [str(value or "").strip()]
    for candidate in candidates:
        if candidate and candidate not in values:
            values.append(candidate)
    if values:
        record[key] = values


def _normalize_merged_bridge_fields(record: dict[str, Any]) -> None:
    bridge_ids: list[str] = []
    for key in ("bridge_id", "bridge_ids", "recalled_by_bridges", "contributed_bridges"):
        value = record.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            bridge_id = str(item or "").strip()
            if bridge_id and bridge_id not in bridge_ids:
                bridge_ids.append(bridge_id)
    if bridge_ids:
        record.setdefault("bridge_id", bridge_ids[0])
        record["recalled_by_bridges"] = bridge_ids


def _dedupe_list_payload(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = str(item)
        if isinstance(item, dict):
            key = str(item.get("doi") or item.get("id") or item.get("url") or item.get("title") or item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

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
        title = _paper_text(paper.get("title")).lower()
        abstract = _paper_text(paper.get("abstract")).lower()
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
    queries: list[str] = []

    # 1. 基础查询：主题本身
    topic = " ".join(str(topic or "").split())
    if topic:
        queries.append(topic)

    profile = domain_profile or {}
    if llm_queries:
        queries.extend(q for q in llm_queries if str(q).strip())

    # 2. 从种子论文标题提取机械 fallback 检索式。
    # 如果 project topic 缺失但用户给了真实 seed paper，至少用 seed 标题
    # 作为可追溯 query；领域扩展仍由 LLM 通过 llm_queries/domain_profile 提供。
    if seed_papers:
        for paper in seed_papers[:5]:
            title = " ".join(_paper_title_text(paper.get("title")).split())
            if len(title) >= 4:
                queries.append(title)
        for paper in seed_papers[:3]:  # 只用前 3 篇
            title = _paper_title_text(paper.get("title"))
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
    if topic:
        queries.append(f"{topic} {format_year_window(2, current_year=current_year)}")
        queries.append(f"{topic} {format_year_window(2, current_year=current_year, lag_years=1)}")

    # 去重并限制数量
    unique_queries = []
    seen = set()
    for q in queries:
        q = " ".join(str(q or "").split())
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
    search_records: list[dict[str, Any]] | None = None,
    bridge_plan: dict[str, Any] | None = None,
    *,
    unique_count: int | None = None,
) -> str:
    """生成检索日志（基于实际数据，不允许编造）。

    Args:
        raw_count: 原始检索结果数量
        dedup_count: 去重后数量
        queries: 使用的检索式列表
        query_results: 每个检索式的结果数量（可选）
        search_records: 结构化检索记录（可选），包含 query/tool/bucket/bridge_id/source_stats
        bridge_plan: 已确认的 bridge_domain_plan.json（可选），用于展示计划覆盖，不做语义判断

    Returns:
        Markdown 格式的检索日志
    """
    log = "# T2 Scout 检索日志\n\n"

    records = _normalize_search_records(search_records, queries, query_results)

    # 检索式
    log += "## 检索式\n\n"
    if records:
        log += "| # | Query | Bucket | Bridge | Tool/Source | Calls | Results | Persisted |\n"
        log += "|---:|---|---|---|---|---:|---:|---:|\n"
        for i, record in enumerate(records, 1):
            query = _md_cell(record.get("query") or "")
            bucket = _md_cell(record.get("query_bucket") or record.get("search_bucket") or "unspecified")
            bridge_id = _md_cell(record.get("bridge_id") or "-")
            tool = _md_cell(record.get("tool_name") or record.get("source_tool") or record.get("source") or "-")
            calls = int(record.get("duplicate_call_count") or record.get("call_count") or 1)
            count = int(record.get("result_count") or record.get("count") or 0)
            persisted = int(record.get("persisted_count") or 0)
            log += f"| {i} | {query} | {bucket} | {bridge_id} | {tool} | {calls} | {count} | {persisted} |\n"
    else:
        for i, query in enumerate(queries, 1):
            if query_results and query in query_results:
                count = query_results[query]
                log += f"{i}. \"{query}\" → {count} 篇\n"
            else:
                log += f"{i}. \"{query}\"\n"

    if records:
        log += "\n## Bucket 覆盖\n\n"
        bucket_counts: Counter[str] = Counter()
        bucket_results: Counter[str] = Counter()
        bucket_persisted: Counter[str] = Counter()
        for record in records:
            bucket = str(record.get("query_bucket") or record.get("search_bucket") or "unspecified")
            bucket_counts[bucket] += int(record.get("duplicate_call_count") or record.get("call_count") or 1)
            bucket_results[bucket] += int(record.get("result_count") or record.get("count") or 0)
            bucket_persisted[bucket] += int(record.get("persisted_count") or 0)
        log += "| Bucket | Query Calls | Results | Persisted |\n"
        log += "|---|---:|---:|---:|\n"
        for bucket, call_count in sorted(bucket_counts.items()):
            log += f"| {_md_cell(bucket)} | {call_count} | {bucket_results[bucket]} | {bucket_persisted[bucket]} |\n"

        bridge_records = [record for record in records if str(record.get("bridge_id") or "").strip()]
        if bridge_records:
            log += "\n## Bridge Domain Query 覆盖\n\n"
            bridge_counts: Counter[str] = Counter()
            bridge_results: Counter[str] = Counter()
            bridge_persisted: Counter[str] = Counter()
            bridge_queries: dict[str, list[str]] = defaultdict(list)
            for record in bridge_records:
                bridge_id = str(record.get("bridge_id") or "").strip()
                bridge_counts[bridge_id] += int(record.get("duplicate_call_count") or record.get("call_count") or 1)
                bridge_results[bridge_id] += int(record.get("result_count") or record.get("count") or 0)
                bridge_persisted[bridge_id] += int(record.get("persisted_count") or 0)
                query = str(record.get("query") or "").strip()
                if query and query not in bridge_queries[bridge_id]:
                    bridge_queries[bridge_id].append(query)
            log += "| Bridge | Query Calls | Results | Persisted | Queries |\n"
            log += "|---|---:|---:|---:|---|\n"
            for bridge_id in sorted(bridge_counts):
                query_preview = "; ".join(bridge_queries[bridge_id][:4])
                if len(bridge_queries[bridge_id]) > 4:
                    query_preview += f"; ... (+{len(bridge_queries[bridge_id]) - 4})"
                log += (
                    f"| {_md_cell(bridge_id)} | {bridge_counts[bridge_id]} | "
                    f"{bridge_results[bridge_id]} | {bridge_persisted[bridge_id]} | "
                    f"{_md_cell(query_preview)} |\n"
                )

        planned_bridges = _normalize_bridge_plan_entries(bridge_plan)
        if planned_bridges:
            bridge_counts = Counter()
            bridge_results = Counter()
            bridge_persisted = Counter()
            bridge_queries: dict[str, list[str]] = defaultdict(list)
            for record in records:
                bridge_id = str(record.get("bridge_id") or "").strip()
                if not bridge_id:
                    continue
                bridge_counts[bridge_id] += int(record.get("duplicate_call_count") or record.get("call_count") or 1)
                bridge_results[bridge_id] += int(record.get("result_count") or record.get("count") or 0)
                bridge_persisted[bridge_id] += int(record.get("persisted_count") or 0)
                query = str(record.get("query") or "").strip()
                if query and query not in bridge_queries[bridge_id]:
                    bridge_queries[bridge_id].append(query)

            log += "\n## Bridge Domain Plan 覆盖\n\n"
            log += "| Bridge | Priority | Planned Queries | Actual Query Calls | Persisted | Status |\n"
            log += "|---|---|---|---:|---:|---|\n"
            for bridge in planned_bridges:
                bridge_id = str(bridge.get("bridge_id") or "").strip()
                priority = str(bridge.get("priority") or "unspecified").strip() or "unspecified"
                planned_queries = bridge.get("queries") if isinstance(bridge.get("queries"), list) else []
                planned_preview = "; ".join(str(item).strip() for item in planned_queries[:3] if str(item).strip())
                if len(planned_queries) > 3:
                    planned_preview += f"; ... (+{len(planned_queries) - 3})"
                calls = bridge_counts[bridge_id]
                persisted = bridge_persisted[bridge_id]
                status = "covered" if persisted > 0 else "missing"
                if priority == "skip":
                    status = "skipped_by_user"
                log += (
                    f"| {_md_cell(bridge_id)} | {_md_cell(priority)} | {_md_cell(planned_preview)} | "
                    f"{calls} | {persisted} | {status} |\n"
                )

        log += "\n## Source/Tool 覆盖\n\n"
        source_counts: Counter[str] = Counter()
        source_results: Counter[str] = Counter()
        source_persisted: Counter[str] = Counter()
        for record in records:
            source = str(record.get("tool_name") or record.get("source_tool") or record.get("source") or "unknown")
            source_counts[source] += int(record.get("duplicate_call_count") or record.get("call_count") or 1)
            source_results[source] += int(record.get("result_count") or record.get("count") or 0)
            source_persisted[source] += int(record.get("persisted_count") or 0)
        log += "| Tool/Source | Calls | Results | Persisted |\n"
        log += "|---|---:|---:|---:|\n"
        for source, call_count in sorted(source_counts.items()):
            log += f"| {_md_cell(source)} | {call_count} | {source_results[source]} | {source_persisted[source]} |\n"

    # 统计数据
    log += "\n## 检索统计\n\n"
    log += f"- 原始结果: {raw_count} 篇\n"
    if unique_count is not None:
        unique_count = max(0, min(int(unique_count), raw_count))
        log += f"- 去重后唯一记录: {unique_count} 篇\n"
        log += f"- 已合并重复记录: {max(0, raw_count - unique_count)} 篇\n"
    log += f"- 当前阅读候选: {dedup_count} 篇\n"
    log += "- 说明: 当前阅读候选按本轮阅读计划选择；未进入本轮的候选仍会保留，供后续回看。\n"

    return log


def _normalize_search_records(
    search_records: list[dict[str, Any]] | None,
    queries: list[str],
    query_results: dict[str, int] | None,
) -> list[dict[str, Any]]:
    if search_records:
        normalized: list[dict[str, Any]] = []
        for record in search_records:
            if not isinstance(record, dict):
                continue
            query = " ".join(str(record.get("query") or "").split())
            if not query and not record.get("bridge_id") and not record.get("query_bucket"):
                continue
            item = dict(record)
            item["query"] = query or "[unknown query]"
            normalized.append(item)
        return normalized

    if not query_results:
        return []
    records = []
    for query in queries:
        records.append(
            {
                "query": query,
                "result_count": int(query_results.get(query, 0)),
                "persisted_count": int(query_results.get(query, 0)),
            }
        )
    return records


def _normalize_bridge_plan_entries(bridge_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(bridge_plan, dict):
        return []
    if str(bridge_plan.get("source") or "").strip().casefold() == "none":
        return []
    domains = bridge_plan.get("bridge_domains")
    if not isinstance(domains, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in domains:
        if not isinstance(item, dict):
            continue
        bridge_id = str(item.get("bridge_id") or "").strip()
        if not bridge_id:
            continue
        entries.append(item)
    return entries


def _md_cell(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("|", "/") or "-"
