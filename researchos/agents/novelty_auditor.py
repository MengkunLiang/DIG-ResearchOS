"""T4.5 Novelty Auditor Agent — 新颖性审计员

业务需求：
- 基于T4产出的hypotheses.md和T3.5产出的synthesis.md
- 对每个假设进行新颖性审计
- 检查是否与已有工作重复
- 使用search_papers搜索近期相关工作
- 产出novelty_audit.md报告

输入：
- ideation/hypotheses.md: T4产出的研究假设
- literature/synthesis.md: T3.5产出的文献综述
- literature/comparison_table.csv: 已有方法对比表

输出：
- ideation/novelty_audit.md: 新颖性审计报告
- ideation/collision_cases.md: 潜在撞车案例（如果有）
"""

from __future__ import annotations

import re
from pathlib import Path

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.agent_params import get_agent_params
from ..runtime.prompts import render_prompt
from ._common import (
    load_project,
    read_text_file,
    validate_files_exist,
)


class NoveltyAuditorAgent(Agent):
    """新颖性审计员。审计研究假设的新颖性，避免与已有工作重复。"""

    def __init__(self):
        params = get_agent_params("novelty_auditor")
        super().__init__(
            AgentSpec(
                name="novelty_auditor",
                model_tier=params.get("model_tier", "heavy"),
                llm_profile=None,
                tool_names=[
                    "read_file",
                    "write_file",
                    "list_files",
                    "search_papers",
                    "fetch_paper_metadata",
                    "finish_task",
                ],
                max_steps=params.get("max_steps", 60),
                max_tokens_total=params.get("max_tokens_total", 150_000),
                max_wall_seconds=params.get("max_wall_seconds", 600),
                max_validation_retries=params.get("max_validation_retries", 3),
                temperature=0.3,
                allowed_read_prefixes=["", "ideation/", "literature/"],
                allowed_write_prefixes=["ideation/"],
                prompt_template="novelty_auditor.j2",
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目信息、假设和文献综述。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir

        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")
        synthesis = read_text_file(ws / "literature" / "synthesis.md", default="")
        comparison_table = read_text_file(ws / "literature" / "comparison_table.csv", default="")

        # 提取假设anchor
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses, re.MULTILINE)

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            hypotheses_preview=hypotheses[:5000],
            synthesis_preview=synthesis[:3000],
            comparison_table_preview=comparison_table[:1000],
            hypothesis_count=len(anchors),
            hypothesis_anchors=anchors,
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return (
            "请执行 T4.5 新颖性审计。读取 ideation/hypotheses.md 和 literature/synthesis.md，"
            "对每个假设进行新颖性审计，搜索近期相关工作，判断新颖性等级，"
            "产出 ideation/novelty_audit.md 和 ideation/collision_cases.md（如有撞车风险）。"
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 内容结构。"""
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        ws = ctx.workspace_dir
        audit_path = ws / "ideation" / "novelty_audit.md"

        # 检查novelty_audit.md存在且有内容
        if not audit_path.exists():
            return False, "缺少 ideation/novelty_audit.md"

        audit_text = read_text_file(audit_path)
        if len(audit_text) < 500:
            return False, f"novelty_audit.md 过短({len(audit_text)} 字符)"

        # 检查是否包含新颖性等级标记
        level_markers = ["Level 0", "Level 1", "Level 2", "Level 3"]
        has_level = any(marker in audit_text for marker in level_markers)
        if not has_level:
            return False, "novelty_audit.md 必须包含新颖性等级（Level 0-3）"

        # 检查是否审计了所有假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses, re.MULTILINE)

        for anchor in anchors:
            if anchor not in audit_text:
                return False, f"novelty_audit.md 缺少对假设 {anchor} 的审计"

        return True, None
