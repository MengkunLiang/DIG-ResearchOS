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
- literature/papers_dedup.jsonl: 去重后论文池（15-120篇）
- literature/deep_read_queue.jsonl: T3精读队列
- literature/search_log.md: 检索审计日志
- literature/missing_areas.md: 缺口分析

契约详见 sections_revised §T2 和 Agent Dev Spec §7。
"""

from pathlib import Path
import json

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
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
                    "allowed_write_prefixes": ["literature/", "literature/temp/"],
                    "prompt_template": "scout.j2",
                    "structured_outputs": {
                        "literature/papers_dedup.jsonl": "papers_dedup",
                        "literature/papers_verified.jsonl": "papers_verified",
                        "literature/papers_raw.jsonl": "papers_raw",
                    },
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目信息和seed papers。"""
        project = load_project(ctx)

        # 优先扫描 seeds/T2_scout/papers/ 目录中的 PDF 文件
        seed_pdf_dir = ctx.workspace_dir / "seeds" / "T2_scout" / "papers"
        if seed_pdf_dir.exists() and any(seed_pdf_dir.glob("*.pdf")):
            seed_papers = scan_seed_papers(seed_pdf_dir)
        else:
            # 降级到旧的 user_seeds/seed_papers.jsonl
            seed_papers_path = ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl"
            seed_papers = load_jsonl(seed_papers_path) if seed_papers_path.exists() else []

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
        external_resources = _load_external_resources(
            ctx.workspace_dir / "user_seeds" / "seed_external_resources.jsonl"
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
            external_resources=external_resources[:10],
            external_resource_count=len(external_resources),
            has_external_resources=bool(external_resources),
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
            "你负责完成高质量多源检索并让 raw 结果落盘；"
            "raw 达标后 runtime 会自动完成去重、metadata verification 和 deep_read_queue.jsonl，"
            "最终 papers_dedup 控制在 10-120 篇。"
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
        ok, err = validate_jsonl_schema(
            dedup_path,
            "papers_dedup",
            min_count=10,  # 降低要求：10篇高质量论文优于15篇低质量论文
            max_count=120,  # 太多说明没按relevance裁剪
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
            min_count=min(10, len(dedup_records)),
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
        verified_ids = {
            str(item.get("canonical_id") or item.get("id") or "").strip()
            for item in verified_records
        }
        verified_ids = {paper_id for paper_id in verified_ids if paper_id}

        # 6. 校验 deep_read_queue：必须建立在 verified 池之上。
        queue_path = ctx.workspace_dir / "literature" / "deep_read_queue.jsonl"
        ok, err = validate_jsonl_schema(
            queue_path,
            "deep_read_queue",
            min_count=min(10, len(verified_records)),
            max_count=len(verified_records),
        )
        if not ok:
            return False, err

        queue_records = load_jsonl(queue_path)
        queue_ids = {str(item.get("paper_id", "")).strip() for item in queue_records}
        if any(paper_id and paper_id not in verified_ids for paper_id in queue_ids):
            return False, "deep_read_queue 中存在不在 papers_verified 里的论文"

        protected_verified = [
            item
            for item in verified_records
            if _is_protected_literature_bucket(item)
        ]
        if protected_verified and not any(_is_protected_literature_bucket(item) for item in queue_records):
            return False, "verified 池包含 adjacent/theory/snowball 论文，但 deep_read_queue 未保留任何跨域/桥接候选"
        if protected_verified and not any(
            _is_protected_literature_bucket(item)
            and str(item.get("target_bucket") or "") != "overflow"
            for item in queue_records
        ):
            return False, "deep_read_queue 保留了跨域/桥接候选，但未放入 target/seed 阅读区"

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
        for field in ("core", "adjacent", "boundary", "citation_edges", "bucket_assignments"):
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


def _is_protected_literature_bucket(record: dict[str, object]) -> bool:
    if bool(record.get("adjacent_field")):
        return True
    bucket = str(record.get("search_bucket") or record.get("query_bucket") or "").strip().casefold()
    bucket = bucket.replace("-", "_").replace(" ", "_")
    source_bucket = str(record.get("source_bucket") or "").strip().casefold()
    source_bucket = source_bucket.replace("-", "_").replace(" ", "_")
    return bucket in {"adjacent_field", "theory_bridge"} or source_bucket in {"adjacent", "snowball"}
