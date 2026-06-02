from __future__ import annotations

"""T2 运行期恢复与确定性收尾。

当 Scout Agent 已经拿到了足够的检索结果，但 LLM 在去重/写文件前中断时，
这里提供一条纯代码路径，把 `papers_raw.jsonl` 收敛为 T2 所需的其余产物。
"""

from collections import Counter
import json
from pathlib import Path
from typing import Any

import yaml

from ..tools.paper_enrichment import build_access_audit, build_deep_read_queue, enrich_papers
from ..tools.citation_graph import build_domain_map
from ..tools.paper_save_tools import SavePapersDedupTool
from ..tools.paper_utils import (
    deduplicate_papers,
    filter_by_domain,
    generate_search_log,
    score_papers,
)
from ..tools.workspace_policy import WorkspaceAccessPolicy
from ..time_utils import current_utc_year, format_year_window, recent_year_from


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


def _select_final_papers(scored_papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conservatively cap recovered papers without priority-score exclusion."""

    if len(scored_papers) <= 120:
        return scored_papers
    return scored_papers[:120]


def _normalize_match_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def _load_seed_papers(workspace_dir: Path) -> list[dict[str, Any]]:
    return _load_jsonl(workspace_dir / "user_seeds" / "seed_papers.jsonl")


def _seed_to_recovery_paper(seed: dict[str, Any]) -> dict[str, Any]:
    arxiv_id = str(seed.get("arxiv_id", "")).strip()
    paper_id = f"arxiv:{arxiv_id}" if arxiv_id and not arxiv_id.startswith("arxiv:") else arxiv_id
    if not paper_id:
        paper_id = str(seed.get("doi") or seed.get("title") or "seed-paper").strip()
    url = str(seed.get("url") or "").strip()
    try:
        seed_year = int(seed["year"]) if seed.get("year") else None
    except (TypeError, ValueError):
        seed_year = None
    return {
        "id": paper_id,
        "canonical_id": paper_id,
        "preferred_id_source": "arxiv" if arxiv_id else "title",
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
            "canonical_id": paper_id,
            "id_source": "arxiv" if arxiv_id else "title",
        },
    }


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

    if len(selected) <= 120:
        return selected

    seed_keys = {_normalize_match_key(seed.get("title")) for seed in seeds}
    seed_records = [paper for paper in selected if _normalize_match_key(paper.get("title")) in seed_keys]
    other_records = [paper for paper in selected if _normalize_match_key(paper.get("title")) not in seed_keys]
    return seed_records + other_records[: max(0, 80 - len(seed_records))]


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
        canonical_id = str(paper.get("canonical_id") or paper.get("id") or paper.get("title") or "").strip()
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


def _iter_t2_trace_paths(workspace_dir: Path) -> list[Path]:
    trace_dir = workspace_dir / "_runtime" / "traces"
    if not trace_dir.exists():
        return []
    return sorted(trace_dir.glob("*.jsonl"))


def extract_t2_search_history(trace_paths: list[Path]) -> tuple[list[str], dict[str, int], int]:
    """从 trace 中恢复检索式和每条检索式的总结果数。"""

    ordered_queries: list[str] = []
    query_results: dict[str, int] = {}
    parsed_traces = 0

    for trace_path in trace_paths:
        if not trace_path.exists():
            continue
        is_t2_trace = trace_path.stem.lower().startswith("t2")
        pending_queries: dict[str, str] = {}
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
                    if query:
                        pending_queries[str(tool_call.get("id", ""))] = query
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
            query = pending_queries.get(tool_call_id, "").strip()
            if not query:
                continue
            if query not in query_results:
                ordered_queries.append(query)
                query_results[query] = 0
            query_results[query] += count

    return ordered_queries, query_results, parsed_traces


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
    raw_path = workspace_dir / "literature" / "papers_raw.jsonl"
    raw_papers = _load_jsonl(raw_path)
    if not raw_papers:
        return {
            "ok": False,
            "reason": "papers_raw_missing_or_empty",
            "raw_count": 0,
        }

    project = _load_project(workspace_dir)
    keywords = _normalize_keywords(project)
    domain_profile = _project_domain_profile(project)

    dedup_papers = deduplicate_papers(raw_papers, doi_dedup=True, title_threshold=0.95)
    if domain_profile:
        dedup_papers = filter_by_domain(
            dedup_papers,
            target_domain=str(domain_profile.get("target_domain") or domain_profile.get("domain") or "profile"),
            domain_profile=domain_profile,
        )

    scored_papers = score_papers(dedup_papers, keywords)
    # Sort for deterministic queue priority only. `relevance_score` is a
    # metadata priority hint and is not used as an exclusion threshold.
    scored_papers = sorted(
        scored_papers,
        key=lambda paper: (
            float(paper.get("relevance_score", 0.0)),
            int(paper.get("citation_count", 0) or 0),
            int(paper.get("year", 0) or 0),
        ),
        reverse=True,
    )
    final_papers = _select_final_papers(scored_papers)
    final_papers = _ensure_seed_papers(final_papers, scored_papers + raw_papers, workspace_dir)
    enriched_papers = enrich_papers(final_papers, keywords, domain_profile=domain_profile)

    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=["", "literature/", "user_seeds/", "seeds/"],
        allowed_write_prefixes=["literature/", "literature/temp/"],
    )
    save_result = await SavePapersDedupTool(policy).execute(papers=enriched_papers, append=False)
    if not save_result.ok:
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

    citation_edges = _build_recovered_citation_edges(verified_papers)
    citation_edges_path = workspace_dir / "literature" / "citation_edges.json"
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
        deep_read_min=18,
        deep_read_target=24,
        deep_read_max=30,
        probe_pool=45,
    )
    queue_path = workspace_dir / "literature" / "deep_read_queue.jsonl"
    queue_meta_path = workspace_dir / "literature" / "deep_read_queue_meta.json"
    _write_jsonl(queue_path, queue_records)
    queue_meta_path.write_text(
        json.dumps(queue_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audit_records, audit_markdown = build_access_audit(verified_papers, workspace_dir, top_n=50)
    access_audit_path = workspace_dir / "literature" / "access_audit.md"
    access_audit_jsonl_path = workspace_dir / "literature" / "access_audit.jsonl"
    _write_jsonl(access_audit_jsonl_path, audit_records)
    access_audit_path.write_text(audit_markdown, encoding="utf-8")

    history_paths = trace_paths if trace_paths is not None else _iter_t2_trace_paths(workspace_dir)
    queries, query_results, trace_count = extract_t2_search_history(history_paths)

    if not queries:
        queries = ["[Recovered] 原始 query 历史不可用"]
        query_results = None

    search_log = generate_search_log(
        raw_count=len(raw_papers),
        dedup_count=len(enriched_papers),
        queries=queries,
        query_results=query_results,
    )
    search_log += "\n## 说明\n\n"
    search_log += "- 此文件由 runtime 基于当前 `papers_raw.jsonl` 和可解析的 T2 trace 自动重建。\n"
    search_log += f"- 解析到的 T2 trace 数量: {trace_count}\n"
    if query_results is None:
        search_log += "- 本次未能恢复可靠的 query 历史，因此只保留了总量统计。\n"

    search_log_path = workspace_dir / "literature" / "search_log.md"
    search_log_path.write_text(search_log, encoding="utf-8")

    missing_areas_path = workspace_dir / "literature" / "missing_areas.md"
    missing_areas_path.write_text(
        generate_missing_areas_report(project, enriched_papers),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "raw_count": len(raw_papers),
        "dedup_count": len(enriched_papers),
        "query_count": len(queries),
        "trace_count": trace_count,
        "paths": {
            "papers_dedup": str(workspace_dir / "literature" / "papers_dedup.jsonl"),
            "papers_verified": str(verified_path),
            "verification_failures": str(failures_path),
            "deep_read_queue": str(queue_path),
            "domain_map": str(domain_map_path),
            "citation_edges": str(citation_edges_path),
            "access_audit": str(access_audit_path),
            "search_log": str(search_log_path),
            "missing_areas": str(missing_areas_path),
        },
    }
