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

from ..time_utils import recent_year_from
from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import (
    prepend_resume_prefix,
    load_project,
    read_text_file,
    validate_files_exist,
)
from .guidance import load_agent_guidance


class NoveltyAuditorAgent(Agent):
    """新颖性审计员。审计研究假设的新颖性，避免与已有工作重复。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "novelty_auditor",
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "list_files",
                        "search_papers",
                        "fetch_paper_metadata",
                        "extract_mechanism_tuple",
                        "compare_mechanism_tuples",
                        "extract_design_rationale_tuple",
                        "compare_design_rationale_tuples",
                        "finish_task",
                    ],
                    "max_steps": 60,
                    "max_tokens_total": 150_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    "allowed_read_prefixes": ["", "ideation/", "literature/"],
                    "allowed_write_prefixes": ["ideation/"],
                    "prompt_template": "novelty_auditor.j2",
                },
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
            recent_year_from=recent_year_from(1),
            temperature=self.spec.temperature,
            agent_guidance=load_agent_guidance("novelty-audit"),
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return prepend_resume_prefix(
            ctx,
            (
            "请执行 T4.5 新颖性审计。读取 ideation/hypotheses.md 和 literature/synthesis.md，"
            "对每个假设进行新颖性审计，搜索近期相关工作，判断新颖性等级，"
            "产出 ideation/novelty_audit.md；如果发现 High/Medium Overlap，"
            "还必须产出 ideation/collision_cases.md 归档潜在撞车案例。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出：文件存在 + 内容结构。"""
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        ws = ctx.workspace_dir
        audit_path = ws / "ideation" / "novelty_audit.md"
        collision_path = ws / "ideation" / "collision_cases.md"

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

        # 检查 mechanism tuples 目录。T4.5 必须显式保存每个假设的 tuple；
        # collision_cases 是条件输出，但 tuple 目录不是条件输出。
        tuples_dir = ws / "ideation" / "_mechanism_tuples"
        if not tuples_dir.is_dir():
            return False, "缺少 ideation/_mechanism_tuples/；T4.5 必须为每个假设保存 mechanism tuple"
        for anchor in anchors:
            anchor_lower = anchor.lower()
            has_tuple = any(
                anchor_lower in f.stem.lower()
                for f in tuples_dir.glob("*.json")
            )
            if not has_tuple:
                return False, (
                    f"ideation/_mechanism_tuples/ 缺少假设 {anchor} 的 mechanism tuple 文件"
                )

        design_tuple_dir = ws / "ideation" / "_design_rationale_tuples"
        if not design_tuple_dir.is_dir():
            return False, "缺少 ideation/_design_rationale_tuples/；T4.5 必须保存 design-rationale tuple"
        for anchor in anchors:
            anchor_lower = anchor.lower()
            has_tuple = any(
                anchor_lower in f.stem.lower()
                for f in design_tuple_dir.glob("*.json")
            )
            if not has_tuple:
                return False, (
                    f"ideation/_design_rationale_tuples/ 缺少假设 {anchor} 的 design-rationale tuple 文件"
                )

        for marker in [
            "Collision Axis",
            "Ambition Axis",
            "Contribution Distance",
            "Final Gate Verdict",
        ]:
            if marker not in audit_text:
                return False, f"novelty_audit.md 必须包含 {marker}"
        if re.search(r"(?i)contribution[_ -]?type\s*[:：]\s*routine", audit_text) and not re.search(
            r"(?i)(return to T4|回到T4|回退T4|reframe|needs reframing)",
            audit_text,
        ):
            return False, "routine contribution 必须明确要求回到 T4 或重新 framing"

        # 检查最终确认的 true_collision (high confidence) 必须对应 Level 0。
        # Tool 现在只返回 possible_* heuristic hints；不能因为 hint 自动判死刑。
        if "true_collision" in audit_text and "possible_true_collision" not in audit_text and "high confidence" in audit_text.lower():
            # 找到所有提到 true_collision 的假设段落
            for anchor in anchors:
                anchor_pattern = re.escape(anchor)
                section_match = re.search(
                    rf"(?ms)^#+\s*{anchor_pattern}\b.*?(?=^#+\s*H\d|\Z)",
                    audit_text,
                )
                if section_match:
                    section = section_match.group(0)
                    if "true_collision" in section and "high confidence" in section.lower():
                        if "Level 0" not in section and "Adjusted Level: 0" not in section:
                            return False, (
                                f"{anchor} 有 true_collision (high confidence) 但未标为 Level 0"
                            )

        if _audit_mentions_collision_case(audit_text):
            if not collision_path.exists():
                return False, "novelty_audit.md 提到 High/Medium Overlap，但缺少 ideation/collision_cases.md"
            collision_text = read_text_file(collision_path)
            if len(collision_text.strip()) < 50:
                return False, "collision_cases.md 过短；请归档 High/Medium Overlap 案例。"
            collision_signals = ("High Overlap", "Medium Overlap", "高度重叠", "中度重叠")
            if not any(signal in collision_text for signal in collision_signals):
                return False, "novelty_audit.md 提到 High/Medium Overlap，但 collision_cases.md 未归档对应案例"

        return True, None


def _audit_mentions_collision_case(audit_text: str) -> bool:
    """Return True when the audit appears to list a real High/Medium overlap.

    Headings such as "High Overlap: none" should not force a collision file.
    We only require one when the relevant section contains a non-empty case
    bullet, or when a case explicitly marks its similarity as High/Medium.
    """

    none_tokens = ("无", "none", "no ", "not found", "未发现")
    in_overlap_section = False
    for raw_line in audit_text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line:
            continue

        if re.search(r"\*\*相似度\*\*\s*:\s*(high|medium)\s+overlap", line, re.IGNORECASE):
            return True
        if re.search(r"\*\*相似度\*\*\s*:\s*(高度重叠|中度重叠)", line, re.IGNORECASE):
            return True

        if line.startswith("#"):
            in_overlap_section = bool(
                re.search(r"\b(high|medium)\s+overlap\b", line, re.IGNORECASE)
                or "高度重叠" in line
                or "中度重叠" in line
            )
            continue

        if in_overlap_section and line.startswith(("- **", "* **")):
            if any(token in lowered for token in none_tokens) or any(token in line for token in ("无", "未发现")):
                continue
            return True

    return False
