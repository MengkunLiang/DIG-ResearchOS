from __future__ import annotations

"""T2 Scout Agent — 文献侦察员

业务需求：
- 基于project.yaml的研究方向和用户seed papers，跨多源检索学术论文
- 优先使用MCP工具（mcp_semantic_scholar_search, mcp_arxiv_search）
- MCP失败时降级到search_papers
- 实现两阶段去重：DOI精确匹配 + 标题相似度≥0.9
- 产出30-80篇去重后的论文池

输入：
- project.yaml: 研究方向、关键词
- user_seeds/seed_papers.jsonl: 用户提供的种子论文（可选）
- user_seeds/seed_constraints.md: 检索约束（可选）

输出：
- literature/papers_raw.jsonl: 原始检索结果
- literature/papers_dedup.jsonl: active candidate pool（上限见 agents.scout.behavior.t2_finalize.active_pool_max）
- literature/deep_read_queue.jsonl: T3精读队列
- literature/search_log.md: 检索审计日志
- literature/missing_areas.md: 缺口分析

契约详见 sections_revised §T2 和 Agent Dev Spec §7。
"""

from pathlib import Path
import json

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec, get_agent_params
from ..runtime.t2_config import detect_manuscript_profile, load_t2_finalize_config
from ..runtime.prompts import render_prompt
from ..literature_identity import is_placeholder_text, paper_record_match_keys
from ..tools.pdf_metadata import scan_seed_papers
from ..tools.paper_utils import (
    deduplicate_papers,
    score_papers,
    expand_queries,
    filter_by_domain,
    generate_search_log,
)
from .guidance import load_agent_guidance
from ._common import (
    ensure_seed_outline_profile,
    load_project,
    load_jsonl,
    prepend_resume_prefix,
    validate_files_exist,
    validate_jsonl_schema,
    read_text_file,
)


class ScoutAgent(Agent):
    """文献侦察员。跨源检索+去重，产出论文池。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "scout",
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "write_structured_file",
                        "ask_human",
                        "inspect_user_seeds",
                        "normalize_seed_outline",
                        "append_papers_raw",
                        "process_papers_raw",
                        "save_papers_raw",
                        "save_papers_dedup",
                        "multi_source_search",
                        "search_papers",
                        "fetch_paper_metadata",
                        "finish_task",
                        "deduplicate_papers",
                        "score_papers",
                        "expand_queries",
                        "filter_by_domain",
                        "generate_search_log",
                        "enrich_papers",
                        "backfill_paper_abstracts",
                        "apply_semantic_screening",
                        "detect_duplicate_queries",
                        "analyze_dedup_rate",
                        "build_verified_papers",
                        "build_access_audit",
                        "build_deep_read_queue",
                        "fetch_outgoing_citations",
                        "build_domain_map",
                        "semantic_scholar_search",
                        "semantic_scholar_get_paper",
                        "arxiv_search",
                        "openalex_search",
                        "openalex_get_work",
                        "crossref_search",
                        "crossref_get_work",
                        "elsevier_scopus_search",
                        "informs_search",
                        "log_scout_progress",
                    ],
                    "max_steps": 50,
                    "max_tokens_total": 150_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.5,
                    "allowed_read_prefixes": [
                        "",
                        "literature/",
                        "user_seeds/",
                        "seeds/",
                        "_runtime/resume/",
                    ],
                    "allowed_write_prefixes": ["literature/", "literature/temp/", "user_seeds/"],
                    "prompt_template": "scout.j2",
                    "structured_outputs": {
                        "literature/papers_dedup.jsonl": "papers_dedup",
                        "literature/papers_verified.jsonl": "papers_verified",
                        "literature/papers_backlog.jsonl": "papers_dedup",
                        "literature/papers_raw.jsonl": "papers_raw",
                    },
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目信息和seed papers。"""
        ensure_seed_outline_profile(ctx.workspace_dir)
        project = load_project(ctx)

        # 合并新旧 seed 来源。不要只看某一个目录；用户仍然常把 PDF 放在
        # user_seeds/pdfs/，这些 PDF 必须在 T2 query 设计阶段可见。
        seed_papers_path = ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl"
        seed_papers = load_jsonl(seed_papers_path) if seed_papers_path.exists() else []
        for seed_pdf_dir in (
            ctx.workspace_dir / "seeds" / "T2_scout" / "papers",
            ctx.workspace_dir / "user_seeds" / "pdfs",
        ):
            if seed_pdf_dir.exists() and any(seed_pdf_dir.glob("*.pdf")):
                seed_papers.extend(scan_seed_papers(seed_pdf_dir))
        seed_papers = _dedupe_seed_papers(seed_papers)

        # 读取约束条件（支持新旧两种路径）
        seed_constraints_new = ctx.workspace_dir / "seeds" / "T2_scout" / "constraints.md"
        seed_constraints_old = ctx.workspace_dir / "user_seeds" / "seed_constraints.md"
        if seed_constraints_new.exists():
            seed_constraints = read_text_file(seed_constraints_new, default="")
        else:
            seed_constraints = read_text_file(seed_constraints_old, default="")

        seed_ideas = read_text_file(
            ctx.workspace_dir / "user_seeds" / "seed_ideas.md",
            default="",
        )
        if is_placeholder_text(seed_ideas):
            seed_ideas = ""
        if is_placeholder_text(seed_constraints):
            seed_constraints = ""
        seed_outline_profile = read_text_file(
            ctx.workspace_dir / "user_seeds" / "seed_outline_profile.json",
            default="",
        )
        external_resources = _load_external_resources(
            ctx.workspace_dir / "user_seeds" / "seed_external_resources.jsonl"
        )
        bridge_domain_plan = read_text_file(
            ctx.workspace_dir / "literature" / "bridge_domain_plan.json",
            default="",
        )

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            seed_paper_count=len(seed_papers),
            seed_papers=seed_papers[:10],  # 只传前10篇避免context过大
            seed_constraints=seed_constraints[:1000],  # 限制长度
            seed_ideas=seed_ideas[:2000],
            has_seed_ideas=bool(seed_ideas.strip()),
            seed_outline_profile_preview=seed_outline_profile[:6000],
            has_seed_outline_profile=bool(seed_outline_profile.strip()),
            manuscript_profile=detect_manuscript_profile(ctx.workspace_dir),
            external_resources=external_resources[:10],
            external_resource_count=len(external_resources),
            has_external_resources=bool(external_resources),
            bridge_domain_plan_preview=bridge_domain_plan[:3000],
            has_bridge_domain_plan=bool(bridge_domain_plan.strip()),
            agent_guidance=load_agent_guidance("literature-scout"),
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息，简短指令。"""
        return prepend_resume_prefix(
            ctx,
            (
            "请按 system prompt 执行 T2 文献普查。"
            "研究方向已写入 project.yaml。"
            "如有用户种子论文会在 user_seeds/seed_papers.jsonl 里，"
            "也请参考 seed_ideas.md 和 seed_external_resources.jsonl。"
            "如果 literature/bridge_domain_plan.json 存在，请把它作为跨领域召回计划使用；"
            "你负责完成高质量多源检索并让 raw 结果落盘；"
            "raw 数量足够只是必要条件，你还要判断 query/source/bucket 覆盖是否足够；"
            "覆盖足够后调用 finish_task，runtime 才会完成去重、metadata verification 和 deep_read_queue.jsonl，"
            "最终 papers_dedup 会按 config/agent_params.yaml 的 agents.scout.behavior.t2_finalize.active_pool_max 控制 active pool。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 数量 + schema + 去重效果。"""
        # 1. 先让基类检查文件存在
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        # 2. 内容级校验：必需字段（先检查，避免后续处理出错）
        dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
        dedup_records = load_jsonl(dedup_path)
        required_fields = ["id", "title", "year", "authors", "relevance_score"]
        for i, record in enumerate(dedup_records):
            for field in required_fields:
                if field not in record:
                    return False, f"papers_dedup 第 {i+1} 行缺少字段: {field}"

        # 3. 校验papers_dedup数量和schema
        t2_config = load_t2_finalize_config(ctx.workspace_dir)
        try:
            params = get_agent_params("scout")
        except Exception:
            params = {}
        expected_outputs = params.get("expected_outputs") if isinstance(params.get("expected_outputs"), dict) else {}
        dedup_min = _safe_int(expected_outputs.get("papers_dedup_min"), 10, minimum=0)
        ok, err = validate_jsonl_schema(
            dedup_path,
            "papers_dedup",
            min_count=dedup_min,
            max_count=t2_config.active_pool_max,
        )
        if not ok:
            return False, err

        # 4. 去重效果检查（dedup <= raw）
        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if raw_path.exists():
            raw_count = len(load_jsonl(raw_path))
            dedup_count = len(dedup_records)
            if dedup_count > raw_count:
                return (
                    False,
                    f"papers_dedup({dedup_count}) > papers_raw({raw_count})，去重异常",
                )

        # 5. 校验 verified artifact：这是 T3 的真实性护栏，不应缺失。
        verified_path = ctx.workspace_dir / "literature" / "papers_verified.jsonl"
        ok, err = validate_jsonl_schema(
            verified_path,
            "papers_verified",
            min_count=min(dedup_min, len(dedup_records)),
            max_count=len(dedup_records),
        )
        if not ok:
            return False, err

        failure_path = ctx.workspace_dir / "literature" / "verification_failures.jsonl"
        ok, err = validate_jsonl_schema(
            failure_path,
            "verification_failure",
            min_count=0,
        )
        if not ok:
            return False, err

        verified_records = load_jsonl(verified_path)
        # 6. 校验 deep_read_queue：必须建立在 verified 池之上。
        queue_path = ctx.workspace_dir / "literature" / "deep_read_queue.jsonl"
        ok, err = validate_jsonl_schema(
            queue_path,
            "deep_read_queue",
            min_count=min(dedup_min, len(verified_records)),
            max_count=len(verified_records),
        )
        if not ok:
            return False, err

        queue_records = load_jsonl(queue_path)
        ok, err = _validate_queue_membership_and_disposition(
            verified_records=verified_records,
            queue_records=queue_records,
        )
        if not ok:
            return False, err

        protected_verified = [
            item
            for item in verified_records
            if _is_semantic_screened_protected_candidate(item)
        ]
        if protected_verified and not any(_is_semantic_screened_protected_candidate(item) for item in queue_records):
            return False, "verified 池包含 semantic_screen 允许的跨域/theory 候选，但 deep_read_queue 未保留任何对应候选"
        if protected_verified and not any(
            _is_semantic_screened_protected_candidate(item)
            and not bool(item.get("triaged_out"))
            and str(item.get("target_bucket") or "") != "overflow"
            for item in queue_records
        ):
            return False, "deep_read_queue 保留了 semantic_screen 允许的跨域/theory 候选，但未放入 target/seed 阅读区"

        ok, err = _validate_bridge_recall_and_screen_coverage(
            ctx.workspace_dir,
            raw_records=load_jsonl(raw_path) if raw_path.exists() else [],
            verified_records=verified_records,
            queue_records=queue_records,
        )
        if not ok:
            return False, err

        seed_in_queue = any(bool(item.get("seed_priority")) for item in queue_records)
        seed_path = ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl"
        if seed_path.exists() and load_jsonl(seed_path) and not seed_in_queue:
            return False, "存在用户 seed papers，但 deep_read_queue 没有保留任何 seed 论文"

        # 7. 校验 missing_areas.md 的 retrieval coverage hint 结构。
        domain_map_path = ctx.workspace_dir / "literature" / "domain_map.json"
        if not domain_map_path.exists():
            return False, "缺少 literature/domain_map.json，T2 必须产出引用图领域地图供 T3.5/T4/T8 复用"
        try:
            domain_map = json.loads(domain_map_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"domain_map.json 解析失败: {exc}"
        if domain_map.get("semantics") != "domain_map_for_synthesis_and_ideation_not_final_gaps":
            return False, "domain_map.json semantics 不正确"
        for field in ("core", "theory_bridge", "adjacent", "boundary", "citation_edges", "bucket_assignments"):
            if field not in domain_map:
                return False, f"domain_map.json 缺少字段: {field}"
        if not isinstance(domain_map.get("bucket_assignments"), dict):
            return False, "domain_map.json bucket_assignments 必须是对象"
        mapped_ids = set(str(key) for key in domain_map.get("bucket_assignments", {}).keys())
        verified_overlap = {
            str(item.get("canonical_id") or item.get("id") or "").strip()
            for item in verified_records
        }
        verified_overlap = {paper_id.replace(":", "_").replace("/", "_") for paper_id in verified_overlap if paper_id}
        def _norm(value: str) -> str:
            return value.replace("https://openalex.org/", "").replace("https://api.openalex.org/works/", "").replace(":", "_").replace("/", "_").strip("_")

        normalized_mapped = {_norm(item) for item in mapped_ids}
        if verified_records and not (normalized_mapped & verified_overlap):
            return False, "domain_map.json 与 papers_verified 没有可匹配论文ID"

        # 8. 校验 missing_areas.md 的 retrieval coverage hint 结构。
        missing_path = ctx.workspace_dir / "literature" / "missing_areas.md"
        if missing_path.exists():
            content = missing_path.read_text(encoding="utf-8")
            if "## Retrieval Coverage Hints" in content:
                import re
                hint_sections = re.split(r"### 提示 \d+", content)
                for i, section in enumerate(hint_sections[1:], 1):
                    required_bullets = ["覆盖缺口", "为什么需要复核", "建议动作", "难度"]
                    missing_bullets = [
                        b for b in required_bullets
                        if f"**{b}**" not in section and f"- **{b}**" not in section
                    ]
                    if missing_bullets:
                        return False, f"missing_areas.md 提示 {i} 缺少必需字段: {', '.join(missing_bullets)}"
            if "## 可探索缺口" in content or "为什么是缺口" in content or "可探索方向" in content:
                return False, "missing_areas.md 使用了旧的研究缺口模板，请改为 Retrieval Coverage Hints 结构"

        return True, None


def _load_external_resources(path: Path) -> list[dict[str, str]]:
    """读取外部资源清单，忽略非法行。"""
    if not path.exists():
        return []

    resources: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            resource = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(resource, dict):
            continue
        resources.append(
            {
                "type": str(resource.get("type", "")).strip(),
                "name": str(resource.get("name", "")).strip(),
                "source": str(resource.get("source", "")).strip(),
                "notes": str(resource.get("notes", "")).strip(),
            }
        )
    return resources


def _dedupe_seed_papers(seed_papers: list[dict]) -> list[dict]:
    """Deduplicate seed metadata from jsonl and PDF scans."""

    deduped: list[dict] = []
    seen: set[str] = set()
    for paper in seed_papers:
        if not isinstance(paper, dict):
            continue
        key = _seed_paper_key(paper)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(paper)
    return deduped


def _seed_paper_key(paper: dict) -> str:
    for field in ("doi", "arxiv_id", "title", "id"):
        value = str(paper.get(field) or "").strip().casefold()
        if value:
            return " ".join(value.split())
    return ""


def _is_semantic_screened_protected_candidate(record: dict[str, object]) -> bool:
    protected_relations = {
        "mechanism_bridge",
        "method_transfer",
        "evaluation_or_metric_bridge",
        "baseline_or_dataset_relevance",
    }
    screen = record.get("semantic_screen")
    if isinstance(screen, dict):
        relation = str(screen.get("relation_to_project") or "").strip()
        has_bridge_identity = bool(_record_bridge_ids(record))
        return (
            bool(screen.get("can_enter_deep_read"))
            and relation in protected_relations
            and has_bridge_identity
        )
    return False


def _validate_queue_membership_and_disposition(
    *,
    verified_records: list[dict],
    queue_records: list[dict],
) -> tuple[bool, str | None]:
    """Ensure verified papers have a full deep/shallow reading disposition.

    T2 may not LLM-screen every verified paper before finish, but no verified
    paper should vanish. Every verified record must either be an active deep-read
    target or retained as a shallow/backlog queue record for T3 abstract sweep
    and later human revisit.
    """

    verified_key_sets = [_record_keys(record) for record in verified_records]
    queue_key_sets = [_record_keys(record) for record in queue_records]

    verified_matched = [False for _ in verified_records]
    queue_outside_verified: list[str] = []
    for queue_record, queue_keys in zip(queue_records, queue_key_sets):
        matched = False
        for idx, verified_keys in enumerate(verified_key_sets):
            if queue_keys and verified_keys and queue_keys & verified_keys:
                verified_matched[idx] = True
                matched = True
                break
        if not matched:
            queue_outside_verified.append(_display_queue_record(queue_record))

    if queue_outside_verified:
        return (
            False,
            "deep_read_queue 中存在不在 papers_verified 里的论文: "
            + ", ".join(queue_outside_verified[:6]),
        )

    missing = [
        _display_queue_record(record)
        for record, matched in zip(verified_records, verified_matched)
        if not matched
    ]
    if missing:
        return (
            False,
            f"papers_verified 中有 {len(missing)}/{len(verified_records)} 篇没有进入 deep_read_queue 的深读/浅读处置: "
            + ", ".join(missing[:8])
            + "。T2 deterministic 收尾必须保留 verified 100% 去向覆盖；请重新 finalize T2 或 resume 修复 queue。",
        )

    active_count = sum(
        1
        for item in queue_records
        if not bool(item.get("triaged_out")) and str(item.get("target_bucket") or "") != "overflow"
    )
    shallow_count = len(queue_records) - active_count
    if verified_records and len(queue_records) < len(verified_records):
        return (
            False,
            f"deep_read_queue 仅保留 {len(queue_records)}/{len(verified_records)} 篇 verified 论文；"
            "必须 100% 保留为 deep_read 或 shallow_read/backlog。",
        )
    if queue_records and active_count <= 0:
        return False, "deep_read_queue 没有任何 active deep-read target"
    if len(queue_records) > active_count and shallow_count <= 0:
        return False, "deep_read_queue 有截断候选但未保留 shallow/backlog 处置"
    return True, None


def _record_keys(record: dict) -> set[str]:
    keys = paper_record_match_keys(record)
    for key in ("paper_id", "canonical_id", "id", "normalized_id", "doi", "title"):
        value = str(record.get(key) or "").strip()
        if value:
            keys.add(" ".join(value.casefold().split()))
            keys.add(value.replace(":", "_").replace("/", "_").casefold())
    return {key for key in keys if key}


def _display_queue_record(record: dict) -> str:
    return str(
        record.get("normalized_id")
        or record.get("paper_id")
        or record.get("canonical_id")
        or record.get("id")
        or record.get("title")
        or "unknown"
    ).strip()


def _validate_bridge_recall_and_screen_coverage(
    workspace_dir: Path,
    *,
    raw_records: list[dict],
    verified_records: list[dict],
    queue_records: list[dict],
) -> tuple[bool, str | None]:
    plan_path = workspace_dir / "literature" / "bridge_domain_plan.json"
    if not plan_path.exists():
        return True, None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"bridge_domain_plan.json 解析失败，无法检查 T2 bridge 覆盖: {exc}"
    if str(plan.get("source") or "").strip().casefold() == "none":
        return True, None
    domains = plan.get("bridge_domains") if isinstance(plan, dict) else []
    if not isinstance(domains, list) or not domains:
        return True, None
    # The official bridge_domain_plan.json is written only after the T1 user
    # gate. Therefore every entry in it is a confirmed bridge, regardless of
    # whether its origin was user-provided, auto-suggested, or mixed.
    confirmed = [
        item
        for item in domains
        if isinstance(item, dict)
        and str(item.get("bridge_id") or "").strip()
    ]
    if not confirmed:
        return True, None

    must_ids = {
        str(item.get("bridge_id") or "").strip()
        for item in confirmed
        if str(item.get("priority") or "").strip() == "must_explore"
    }
    recalled_by_bridge = _bridge_hit_counts(raw_records)
    screen_by_bridge = _bridge_screen_counts([*verified_records, *queue_records])
    target_by_bridge = _bridge_target_counts(queue_records)

    missing_recall = sorted(bridge_id for bridge_id in must_ids if recalled_by_bridge.get(bridge_id, 0) <= 0)
    if missing_recall:
        return False, (
            "T2 bridge 召回层 FAIL：must_explore bridge 没有任何 raw 命中: "
            + ", ".join(missing_recall)
            + "。请为每个 must_explore bridge 使用带 bridge_id 的专属 query 重新检索。"
        )

    missing_screen = sorted(bridge_id for bridge_id in must_ids if screen_by_bridge.get(bridge_id, 0) <= 0)
    if missing_screen:
        return False, (
            "T2 bridge screen 层 FAIL：must_explore bridge 有召回但没有任何 semantic_screen 允许进入 deep-read 的候选: "
            + ", ".join(missing_screen)
            + "。请让 Scout 基于标题/摘要/source_query 做语义筛选；不要只依赖 bridge_id。"
        )

    missing_target = sorted(bridge_id for bridge_id in must_ids if target_by_bridge.get(bridge_id, 0) <= 0)
    if missing_target:
        return False, (
            "T2 bridge 队列层 FAIL：must_explore bridge 通过 screen 但没有进入非 triaged deep-read 目标: "
            + ", ".join(missing_target)
        )
    return True, None


def _record_bridge_ids(record: dict) -> set[str]:
    ids: set[str] = set()
    for key in ("bridge_id", "recalled_by_bridges", "contributed_bridges"):
        value = record.get(key)
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = [str(item) for item in value]
        else:
            values = []
        for item in values:
            bridge_id = str(item or "").strip()
            if bridge_id:
                ids.add(bridge_id)
    screen = record.get("semantic_screen")
    if isinstance(screen, dict):
        bridge_id = str(screen.get("bridge_id") or "").strip()
        if bridge_id:
            ids.add(bridge_id)
    return ids


def _bridge_hit_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for bridge_id in _record_bridge_ids(record):
            counts[bridge_id] = counts.get(bridge_id, 0) + 1
    return counts


def _bridge_screen_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if not _is_semantic_screened_protected_candidate(record):
            continue
        for bridge_id in _record_bridge_ids(record):
            counts[bridge_id] = counts.get(bridge_id, 0) + 1
    return counts


def _bridge_target_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if bool(record.get("triaged_out")) or str(record.get("target_bucket") or "") == "overflow":
            continue
        for bridge_id in _record_bridge_ids(record):
            if record.get("target_bucket") == "bridge_deep" or _is_semantic_screened_protected_candidate(record):
                counts[bridge_id] = counts.get(bridge_id, 0) + 1
    return counts


def _safe_int(value, default: int, *, minimum: int | None = None) -> int:
    try:
        result = int(float(str(value).strip())) if value not in (None, "", [], {}) else int(default)
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    return result
