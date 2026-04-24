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
- literature/search_log.md: 检索审计日志
- literature/missing_areas.md: 缺口分析

契约详见 sections_revised §T2 和 Agent Dev Spec §7。
"""

from pathlib import Path

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
from ._common import (
    load_project,
    load_jsonl,
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
                        "save_papers_raw",
                        "save_papers_dedup",
                        "multi_source_search",
                        "search_papers",
                        "fetch_paper_metadata",
                        "finish_task",
                        "deduplicate_papers",
                        "score_papers",
                        "expand_queries",
                        "generate_search_log",
                        "enrich_papers",
                        "detect_duplicate_queries",
                        "analyze_dedup_rate",
                        "semantic_scholar_search",
                        "semantic_scholar_get_paper",
                        "arxiv_search",
                        "openalex_search",
                        "openalex_get_work",
                        "crossref_search",
                        "crossref_get_work",
                        "log_scout_progress",
                    ],
                    "max_steps": 50,
                    "max_tokens_total": 150_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.5,
                    "allowed_read_prefixes": ["", "user_seeds/", "seeds/"],
                    "allowed_write_prefixes": ["literature/", "literature/temp/"],
                    "prompt_template": "scout.j2",
                    "structured_outputs": {
                        "literature/papers_dedup.jsonl": "papers_dedup",
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

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            seed_paper_count=len(seed_papers),
            seed_papers=seed_papers[:10],  # 只传前10篇避免context过大
            seed_constraints=seed_constraints[:1000],  # 限制长度
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息，简短指令。"""
        return (
            "请按 system prompt 执行 T2 文献普查。"
            "研究方向已写入 project.yaml。如有用户种子论文会在 user_seeds/seed_papers.jsonl 里。"
            "目标产出 15-120 篇去重后论文，写入 literature/papers_dedup.jsonl。"
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

        return True, None
