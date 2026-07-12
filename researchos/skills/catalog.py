from __future__ import annotations

"""Human-facing catalog metadata and terminal rendering for standalone skills."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import parse_skill_interaction


@dataclass(frozen=True)
class SkillCatalogProfile:
    category: str
    workflow_stage: str
    action_hint: str


_PROFILES: dict[str, SkillCatalogProfile] = {
    "research-scope": SkillCatalogProfile("研究起点", "主题与材料", "澄清问题、边界和可用材料"),
    "literature-query-plan": SkillCatalogProfile("文献与知识", "检索设计", "先设计可复现检索问题与 query 组合"),
    "literature-evidence-scout": SkillCatalogProfile("文献与知识", "证据补检", "为特定 claim 或章节寻找可核验来源"),
    "literature-resource-scout": SkillCatalogProfile("文献与知识", "资源盘点", "核验数据、基线、代码和复现约束"),
    "paper-note-review": SkillCatalogProfile("文献与知识", "笔记核验", "从已有笔记卡回查 section 级证据"),
    "survey-visuals": SkillCatalogProfile("专业综述", "图表生成", "基于文献表生成可复现的综述图"),
    "idea-fanout-jury": SkillCatalogProfile("Idea 与假设", "候选治理", "发散、接地、评分并提交人工选择"),
    "hypothesis-compiler": SkillCatalogProfile("Idea 与假设", "假设编译", "把选定方向变成可证伪假设和验证计划"),
    "experiment-design-review": SkillCatalogProfile("实验与证据", "实验设计", "审查研究问题、对照、指标、停止条件和风险"),
    "paper-outline": SkillCatalogProfile("论文写作", "论证结构", "先建立章节、贡献和证据映射"),
    "paper-write": SkillCatalogProfile("论文写作", "初稿", "按章节起草并运行证据/写作审计"),
    "paper-polish": SkillCatalogProfile("审阅与修订", "语言与结构", "保留原稿，生成可追溯的润色副本"),
    "paper-revision": SkillCatalogProfile("审阅与修订", "审稿回复", "逐条处理评论并记录修改和证据边界"),
    "paper-claim-audit": SkillCatalogProfile("审阅与修订", "Claim 审计", "检查数字、强断言和 mock-only 证据"),
    "citation-provenance-audit": SkillCatalogProfile("审阅与修订", "引用审计", "检查引用键、笔记 provenance 与可主张范围"),
    "paper-compile": SkillCatalogProfile("交付与投稿", "真实编译", "打包、编译 PDF 并保留实际报告"),
    "submission-readiness": SkillCatalogProfile("交付与投稿", "提交检查", "审查匿名化、引用、PDF 与提交材料"),
    "reference-project-miner": SkillCatalogProfile("工程研究", "参考项目", "从本地项目提取可迁移机制"),
    "method-builder": SkillCatalogProfile("外部执行器", "兼容指导", "外部执行器的历史兼容入口"),
}

_CATEGORY_ORDER = (
    "研究起点",
    "文献与知识",
    "专业综述",
    "Idea 与假设",
    "实验与证据",
    "论文写作",
    "审阅与修订",
    "交付与投稿",
    "工程研究",
    "外部执行器",
    "其他",
)


def profile_for_skill(name: str) -> SkillCatalogProfile:
    return _PROFILES.get(
        name,
        SkillCatalogProfile("其他", "独立能力", "查看完整输入契约后启动"),
    )


def ordered_skills(skills: Iterable[Any]) -> list[Any]:
    """Return skills in user workflow order, with stable fallback ordering."""

    order = {category: index for index, category in enumerate(_CATEGORY_ORDER)}
    return sorted(
        skills,
        key=lambda skill: (order.get(profile_for_skill(skill.name).category, len(order)), skill.name),
    )


def render_skill_catalog(
    *,
    skills: Iterable[Any],
    workspace: Path,
    index_by_name: dict[str, int] | None = None,
    heading: str = "ResearchOS · 独立 Skill 目录",
    notice: str | None = None,
) -> str:
    """Render a scan-friendly card catalog without calling an LLM."""

    ordered = ordered_skills(skills)
    width = 88
    lines = ["", "═" * width, heading, "═" * width]
    lines.append(f"工作区：{workspace}")
    lines.append("先查看卡片与输入要求，再选择运行；缺输入只会创建可恢复会话，不会调用 LLM。")
    if notice:
        lines.append(notice)
    current_category = ""
    for ordinal, skill in enumerate(ordered, start=1):
        index = (index_by_name or {}).get(skill.name, ordinal)
        profile = profile_for_skill(skill.name)
        interaction = parse_skill_interaction(skill.metadata)
        if profile.category != current_category:
            current_category = profile.category
            lines.extend(["", f"╭─ {current_category} " + "─" * max(1, width - len(current_category) - 4) + "╮"])
        mode = "引导式" if interaction and interaction.mode == "guided" else "兼容"
        required = len(interaction.required_inputs) if interaction else 0
        optional = len(interaction.optional_inputs) if interaction else 0
        outputs = len(interaction.outputs) if interaction else len(skill.metadata.get("outputs_expected") or {})
        # Guided skills declare a Chinese-first operational summary.  Prefer it
        # over package metadata so the catalog is directly usable in a CLI.
        description = _compact(interaction.summary if interaction and interaction.summary else skill.description, 118)
        lines.append("┌" + "─" * (width - 2) + "┐")
        lines.append(f"│ [{index:02d}] {skill.name} · {mode} · {profile.workflow_stage}")
        lines.append(f"│      {_compact(description, 72)}")
        lines.append(f"│      输入：必需 {required} / 可选 {optional} | 输出：{outputs}")
        lines.append(f"│      适用：{_compact(profile.action_hint, 72)}")
        lines.append(f"│      查看：researchos describe-skill {skill.name} --workspace <workspace>")
        lines.append("└" + "─" * (width - 2) + "┘")
    lines.extend(
        [
            "",
            "操作：`researchos describe-skill <名称>` 查看完整契约；"
            "`researchos browse-skills --workspace <workspace>` 进行终端选择；"
            "`researchos skill-status` 查看正在运行或可恢复的会话。",
            "═" * width,
        ]
    )
    return "\n".join(lines)


def search_skills(skills: Iterable[Any], query: str) -> list[Any]:
    """Return workflow-ordered Skills matching all query tokens."""

    tokens = [token.casefold() for token in str(query or "").split() if token.strip()]
    if not tokens:
        return []
    matches: list[Any] = []
    for skill in ordered_skills(skills):
        interaction = parse_skill_interaction(skill.metadata)
        profile = profile_for_skill(skill.name)
        searchable = " ".join(
            [
                skill.name,
                skill.description,
                profile.category,
                profile.workflow_stage,
                profile.action_hint,
                interaction.summary if interaction else "",
            ]
        ).casefold()
        if all(token in searchable for token in tokens):
            matches.append(skill)
    return matches


def skills_in_category(skills: Iterable[Any], category: str) -> list[Any]:
    """Return Skills whose workflow category contains the requested label."""

    needle = str(category or "").strip().casefold()
    if not needle:
        return []
    return [
        skill
        for skill in ordered_skills(skills)
        if needle in profile_for_skill(skill.name).category.casefold()
    ]


def catalog_entries(skills: Iterable[Any]) -> list[dict[str, object]]:
    """Return stable catalog metadata for CLI JSON/YAML output and tests."""

    entries: list[dict[str, object]] = []
    for index, skill in enumerate(ordered_skills(skills), start=1):
        interaction = parse_skill_interaction(skill.metadata)
        profile = profile_for_skill(skill.name)
        entries.append(
            {
                "index": index,
                "name": skill.name,
                "category": profile.category,
                "workflow_stage": profile.workflow_stage,
                "action_hint": profile.action_hint,
                "mode": interaction.mode if interaction else "legacy",
                "required_input_count": len(interaction.required_inputs) if interaction else 0,
                "optional_input_count": len(interaction.optional_inputs) if interaction else 0,
                "output_count": len(interaction.outputs) if interaction else len(skill.metadata.get("outputs_expected") or {}),
            }
        )
    return entries


def _compact(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."
