from __future__ import annotations

"""Human-facing catalog metadata and terminal rendering for standalone skills."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import io
from pathlib import Path
import shutil
from typing import Any, Iterable
import unicodedata

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .contracts import parse_skill_interaction
from .presentation import brief_skill_copy, humanize_skill_copy


@dataclass(frozen=True)
class SkillCatalogProfile:
    category: str
    workflow_stage: str
    action_hint: str


_CATEGORY_SUMMARIES = {
    "研究起点": "确定问题边界并把人工材料登记为可追溯输入。",
    "论文导入与阅读": "从标识解析、PDF 阅读到可核验的论文阅读笔记。",
    "文献与知识": "设计检索、补齐证据、治理引用并形成可比较知识。",
    "专业综述": "从已审计 taxonomy 生成结构性综述产物。",
    "Idea 与假设": "从文献综合出发治理候选方向，再编译为可证伪假设。",
    "实验与证据": "把研究问题、对照、指标、风险和证据边界落实为方案。",
    "论文写作": "建立科研叙事、章节结构、论证与可审计初稿。",
    "审阅与修订": "检查 claim、证据、引用、审稿意见与 venue 约束。",
    "交付与投稿": "真实编译、提交前检查与可复现实物打包。",
    "工程研究": "从本地参考工程提取可迁移机制和实施线索。",
    "外部执行器": "历史兼容入口；实际外部执行应遵守 T5 handoff 契约。",
}

# Two protected executor-facing compatibility Skills intentionally do not
# expose a guided interaction summary.  Keep their catalog label useful to a
# researcher instead of showing their implementation-facing package metadata.
_LEGACY_SKILL_SUMMARIES = {
    "research-reboost": "将已确认的研究计划整理为外部实验执行所需的交接材料。",
}

_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "research-scope": ("选题", "主题", "研究问题", "scope", "topic", "research question"),
    "research-material-ingest": ("上传", "导入", "材料", "pdf", "数据", "代码", "ingest"),
    "paper-identifier-resolver": ("doi", "arxiv", "论文", "标识", "identifier", "paper id"),
    "pdf-note-card": ("论文阅读", "读论文", "pdf", "笔记卡", "文献卡", "paper note"),
    "paper-section-evidence": ("论文", "section", "证据", "取证", "claim", "pdf"),
    "paper-note-review": ("笔记卡", "文献卡", "论文", "核验", "paper note"),
    "paper-comparison": ("论文比较", "文献比较", "对比", "paper comparison"),
    "citation-graph-explorer": ("引文", "引用图", "citation", "graph", "文献"),
    "literature-query-plan": ("文献", "检索", "query", "search", "literature"),
    "literature-evidence-scout": ("文献", "补检", "证据", "citation", "literature", "claim"),
    "literature-resource-scout": ("文献", "资源", "基线", "代码", "数据集", "resource"),
    "literature-evidence-matrix": ("文献", "证据矩阵", "综述", "literature", "matrix"),
    "citation-library-curator": ("引用", "文献", "bibtex", "reference", "citation"),
    "literature-gap-map": ("文献", "缺口", "research gap", "literature", "gap"),
    "survey-visuals": ("综述", "taxonomy", "分类图", "survey", "图"),
    "idea-fanout-jury": ("idea", "ideas", "创新", "创新点", "选题", "方向", "假设", "hypothesis", "novelty"),
    "t4-evolution": ("t4", "idea", "ideas", "研究方向", "候选", "evolution", "mutation", "crossover", "gate1", "恢复"),
    "hypothesis-compiler": ("idea", "创新", "假设", "hypothesis", "研究方向", "机制"),
    "experiment-design-review": ("实验", "实验设计", "baseline", "指标", "experiment"),
    "paper-outline": ("论文写作", "大纲", "outline", "writing", "paper"),
    "paper-write": ("论文写作", "写作", "初稿", "write", "draft", "paper"),
    "paper-polish": ("润色", "语言", "polish", "writing"),
    "paper-revision": ("修改", "审稿回复", "revision", "review"),
    "paper-claim-audit": ("claim", "主张", "审计", "论文", "evidence"),
    "citation-provenance-audit": ("引用", "文献", "citation", "reference", "provenance"),
    "claim-evidence-map": ("claim", "证据", "主张", "evidence", "map"),
    "paper-peer-review": ("审稿", "同行评审", "review", "paper"),
    "venue-fit-review": ("会议", "期刊", "venue", "fit", "投稿"),
    "paper-compile": ("latex", "编译", "pdf", "compile", "投稿"),
    "submission-readiness": ("投稿", "提交", "submission", "ready"),
    "domain-synthesis-studio": ("领域综合", "领域", "综述", "synthesis", "taxonomy", "文献综合"),
    "literature-comparison-studio": ("文献比较", "论文比较", "对比", "doi", "comparison"),
    "cross-domain-idea-studio": ("跨域", "cross-domain", "idea", "创新", "桥接", "迁移"),
    "literature-review-studio": ("文献综述", "综述", "survey", "literature review", "检索"),
    "survey-evidence-package": ("综述", "survey", "taxonomy", "语料", "综述写作"),
    "related-work-builder": ("related work", "相关工作", "文献综述", "写作", "citation"),
    "paper-reading-workbench": ("论文阅读", "读论文", "pdf", "doi", "笔记卡", "paper reading"),
    "research-landscape-report": ("领域地图", "领域综合", "research landscape", "机会", "文献"),
    "draft-evidence-repair": ("证据修复", "claim", "引用", "草稿", "审计", "revision"),
}


_PROFILES: dict[str, SkillCatalogProfile] = {
    "research-scope": SkillCatalogProfile("研究起点", "主题与材料", "澄清问题、边界和可用材料"),
    "research-material-ingest": SkillCatalogProfile("研究起点", "材料导入", "登记用户的 PDF、数据、代码和使用边界"),
    "paper-identifier-resolver": SkillCatalogProfile("论文导入与阅读", "标识解析", "从 DOI、arXiv 或标题建立可追溯论文记录"),
    "pdf-note-card": SkillCatalogProfile("论文导入与阅读", "PDF 笔记卡", "上传一篇 PDF 并生成论文阅读笔记"),
    "paper-section-evidence": SkillCatalogProfile("论文导入与阅读", "定向取证", "核验一篇论文中的问题、主张或结果"),
    "paper-note-review": SkillCatalogProfile("论文导入与阅读", "笔记核验", "从已有论文阅读笔记核验一项主张"),
    "paper-comparison": SkillCatalogProfile("论文导入与阅读", "论文比较", "比较多个笔记卡的机制、方法、证据与限制"),
    "citation-graph-explorer": SkillCatalogProfile("论文导入与阅读", "引文图谱", "从 DOI/OpenAlex 种子做有边界的一跳扩展"),
    "literature-query-plan": SkillCatalogProfile("文献与知识", "检索设计", "先设计可复现检索问题与 query 组合"),
    "literature-evidence-scout": SkillCatalogProfile("文献与知识", "证据补检", "为特定 claim 或章节寻找可核验来源"),
    "literature-resource-scout": SkillCatalogProfile("文献与知识", "资源盘点", "核验数据、基线、代码和复现约束"),
    "literature-evidence-matrix": SkillCatalogProfile("文献与知识", "证据矩阵", "把一组笔记卡整理为综述/idea 可用比较矩阵"),
    "citation-library-curator": SkillCatalogProfile("文献与知识", "引用库整理", "审计 BibTeX、重复项、冲突和可核验状态"),
    "literature-gap-map": SkillCatalogProfile("文献与知识", "缺口治理", "区分检索不足与证据支持的未解问题"),
    "survey-visuals": SkillCatalogProfile("专业综述", "分类图生成", "仅从已审计 taxonomy 生成一张结构性概览图"),
    "idea-fanout-jury": SkillCatalogProfile("Idea 与假设", "候选治理", "发散、接地、评分并提交人工选择"),
    "t4-evolution": SkillCatalogProfile("Idea 与假设", "原生 T4 Evolution", "检查并启动、恢复或查看原生候选演化流程"),
    "hypothesis-compiler": SkillCatalogProfile("Idea 与假设", "假设编译", "把选定方向变成可证伪假设和验证计划"),
    "experiment-design-review": SkillCatalogProfile("实验与证据", "实验设计", "审查研究问题、对照、指标、停止条件和风险"),
    "paper-outline": SkillCatalogProfile("论文写作", "论证结构", "先建立章节、贡献和证据映射"),
    "paper-write": SkillCatalogProfile("论文写作", "初稿", "按章节起草并运行证据/写作审计"),
    "paper-polish": SkillCatalogProfile("审阅与修订", "语言与结构", "保留原稿，生成可追溯的润色副本"),
    "paper-revision": SkillCatalogProfile("审阅与修订", "审稿回复", "逐条处理评论并记录修改和证据边界"),
    "paper-claim-audit": SkillCatalogProfile("审阅与修订", "Claim 审计", "检查数字、强断言和 mock-only 证据"),
    "citation-provenance-audit": SkillCatalogProfile("审阅与修订", "引用审计", "检查引用键、笔记 provenance 与可主张范围"),
    "claim-evidence-map": SkillCatalogProfile("审阅与修订", "证据映射", "批量把待写主张定位到证据位置与允许措辞"),
    "paper-peer-review": SkillCatalogProfile("审阅与修订", "同行审阅", "按证据、贡献、方法、实验和写作生成修订优先级"),
    "venue-fit-review": SkillCatalogProfile("审阅与修订", "Venue 契合", "对照人工提供的 venue 要求审查稿件"),
    "paper-compile": SkillCatalogProfile("交付与投稿", "真实编译", "打包、编译 PDF 并保留实际报告"),
    "submission-readiness": SkillCatalogProfile("交付与投稿", "提交检查", "审查匿名化、引用、PDF 与提交材料"),
    "reference-project-miner": SkillCatalogProfile("工程研究", "参考项目", "从本地项目提取可迁移机制"),
    "method-builder": SkillCatalogProfile("外部执行器", "兼容指导", "外部执行器的历史兼容入口"),
    "research-reboost": SkillCatalogProfile("外部执行器", "交接重整", "外部实验 handoff 的兼容 Skill"),
    "project-skill-specialization": SkillCatalogProfile("外部执行器", "Skill 专项编译", "从 T5 handoff 生成并校验项目专属执行器 Skill Suite"),
    "domain-synthesis-studio": SkillCatalogProfile("文献与知识", "领域综合", "从范围、补检到证据约束的领域综合与后续路径选择"),
    "literature-comparison-studio": SkillCatalogProfile("文献与知识", "集成文献对比", "从 DOI/PDF/卡片开始建立可追溯比较与决策"),
    "cross-domain-idea-studio": SkillCatalogProfile("Idea 与假设", "跨域候选治理", "用桥接证据、迁移风险和人工选择构建跨域 Idea"),
    "literature-review-studio": SkillCatalogProfile("专业综述", "综述工作台", "从综述问题到可审计语料、taxonomy 与 Survey handoff"),
    "survey-evidence-package": SkillCatalogProfile("专业综述", "Survey 证据准备", "在写作前审计 taxonomy、语料充分性和补检路径"),
    "related-work-builder": SkillCatalogProfile("论文写作", "Related Work", "用可回查的文献卡和引用库构建章节"),
    "paper-reading-workbench": SkillCatalogProfile("论文导入与阅读", "阅读工作台", "从 PDF/DOI 到多篇阅读卡和跨论文学习"),
    "research-landscape-report": SkillCatalogProfile("文献与知识", "领域地图", "展示事实性结构、覆盖、张力和证据约束机会"),
    "draft-evidence-repair": SkillCatalogProfile("审阅与修订", "证据修复", "定位草稿 claim、引用和证据缺口并制定修复计划"),
}

_CATEGORY_ORDER = (
    "研究起点",
    "论文导入与阅读",
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
    # Borders are structural only. Keep prose whole and let the terminal wrap
    # it at the actual viewport instead of splitting sentences by character.
    width = 88
    lines = ["═" * width, heading, "═" * width]
    lines.extend(_wrap_catalog_text(f"工作区：{workspace}", width=width))
    lines.extend(
        _wrap_catalog_text("先按用途选择能力；启动后会检查材料，缺少时只询问下一项需要补充的内容。", width=width)
    )
    if notice:
        lines.extend(_wrap_catalog_text(notice, width=width))
    current_category = ""
    for ordinal, skill in enumerate(ordered, start=1):
        index = (index_by_name or {}).get(skill.name, ordinal)
        profile = profile_for_skill(skill.name)
        interaction = parse_skill_interaction(skill.metadata)
        if profile.category != current_category:
            current_category = profile.category
            lines.append(f"【{current_category}】")
        mode = "引导式" if interaction and interaction.mode == "guided" else "兼容"
        required = len(interaction.required_inputs) if interaction else 0
        optional = len(interaction.optional_inputs) if interaction else 0
        outputs = len(interaction.outputs) if interaction else len(skill.metadata.get("outputs_expected") or {})
        # Guided skills declare a Chinese-first operational summary.  Prefer it
        # over package metadata so the catalog is directly usable in a CLI.
        description = _catalog_summary(skill, interaction)
        lines.append(f"[{index:02d}] {skill.name} · {mode}")
        lines.append(f"用途：{description}")
        lines.append(f"材料：必需 {required} 项；可选 {optional} 项。输出：预计 {outputs} 个文件。")
        lines.append(f"查看说明：researchos describe-skill {skill.name} --workspace <workspace>")
    lines.extend(
        [
            "",
            "═" * width,
        ]
    )
    footer = (
        "操作：`researchos describe-skill <名称>` 查看使用说明；"
        "`researchos browse-skills --workspace <workspace>` 进行终端选择；"
        "`researchos skill-status` 查看正在运行或可恢复的会话。"
    )
    lines[-1:-1] = _wrap_catalog_text(footer, width=width)
    return "\n".join(lines)


def render_skill_catalog_rich(
    *,
    skills: Iterable[Any],
    workspace: Path,
    index_by_name: dict[str, int] | None = None,
    heading: str = "ResearchOS · 独立 Skill 目录",
    notice: str | None = None,
    no_color: bool = False,
) -> str:
    """Render a contained, scan-first Skill directory for human terminals."""

    ordered = ordered_skills(skills)
    by_category: dict[str, list[tuple[int, Any]]] = {}
    for ordinal, skill in enumerate(ordered, start=1):
        index = (index_by_name or {}).get(skill.name, ordinal)
        by_category.setdefault(profile_for_skill(skill.name).category, []).append((index, skill))

    category_colors = {
        "研究起点": "cyan",
        "论文导入与阅读": "blue",
        "文献与知识": "green",
        "专业综述": "magenta",
        "Idea 与假设": "bright_magenta",
        "实验与证据": "yellow",
        "论文写作": "bright_cyan",
        "审阅与修订": "bright_red",
        "交付与投稿": "bright_green",
        "工程研究": "white",
        "外部执行器": "bright_yellow",
    }
    intro: list[Any] = [
        Text(f"工作区：{workspace}", style="dim"),
        Text("按研究流程浏览可用能力。启动后会检查材料；缺少时只询问下一项需要补充的内容。"),
    ]
    if notice:
        intro.append(Text(notice, style="yellow"))
    renderables: list[Any] = [Panel(Group(*intro), title=heading, border_style="cyan", expand=True)]
    for category in _CATEGORY_ORDER:
        entries = by_category.get(category)
        if not entries:
            continue
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=5, justify="right", style=f"bold {category_colors.get(category, 'cyan')}")
        table.add_column(ratio=1, overflow="fold")
        for index, skill in entries:
            interaction = parse_skill_interaction(skill.metadata)
            mode = "引导式交互" if interaction and interaction.mode == "guided" else "兼容入口"
            required = len(interaction.required_inputs) if interaction else 0
            optional = len(interaction.optional_inputs) if interaction else 0
            outputs = len(interaction.outputs) if interaction else len(skill.metadata.get("outputs_expected") or {})
            description = _catalog_summary(skill, interaction)
            details = Text()
            details.append(skill.name + "\n", style="bold")
            details.append("用途：", style="bold dim")
            details.append(_compact(description, 180) + "\n")
            details.append(f"{mode} · 必需材料 {required} 项 · 可选补充 {optional} 项 · 输出 {outputs} 项", style="dim")
            table.add_row(
                f"[{index:02d}]",
                details,
                end_section=True,
            )
        header = Text()
        header.append(f"{category}\n", style=f"bold {category_colors.get(category, 'cyan')}")
        header.append(f"{len(entries)} 项研究能力 · {humanize_skill_copy(_CATEGORY_SUMMARIES.get(category, '按需查看各 Skill 的使用说明。'))}", style="dim")
        renderables.append(Panel(table, title=header, border_style=category_colors.get(category, "cyan"), expand=True, padding=(0, 1)))
    renderables.append(
        Panel(
            Text(
                "查看完整说明：researchos describe-skill <名称>；交互浏览：researchos browse-skills；"
                "恢复会话：researchos skill-status。",
                style="dim",
            ),
            border_style="dim",
            expand=True,
        )
    )
    return _render_rich_catalog(Group(*renderables), no_color=no_color)


def _render_rich_catalog(renderable: Any, *, no_color: bool) -> str:
    width = max(100, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=not no_color,
        color_system=None if no_color else "truecolor",
        no_color=no_color,
        width=width,
        highlight=False,
        _environ={"COLUMNS": str(width), "LINES": "40"},
    )
    console.print(renderable)
    return buffer.getvalue().rstrip()


def search_skill_matches(skills: Iterable[Any], query: str) -> list[tuple[Any, str]]:
    """Rank bilingual, fuzzy local matches without calling a provider."""

    needle = _normalize_search(query)
    if not needle:
        return []
    ranked: list[tuple[int, Any, str]] = []
    for skill in ordered_skills(skills):
        interaction = parse_skill_interaction(skill.metadata)
        profile = profile_for_skill(skill.name)
        aliases = _SEARCH_ALIASES.get(skill.name, ())
        fields = {
            "名称": skill.name,
            "分类": profile.category,
            "流程位置": profile.workflow_stage,
            "用途": " ".join((skill.description, profile.action_hint, interaction.summary if interaction else "")),
            "别名": " ".join(aliases),
        }
        normalized = {label: _normalize_search(value) for label, value in fields.items()}
        score = 0
        reasons: list[str] = []
        for label, value in normalized.items():
            if needle == value:
                score += 300
                reasons.append(f"{label} 完全匹配")
            elif needle in value:
                score += 180 if label in {"名称", "别名", "分类"} else 100
                reasons.append(f"{label} 包含“{query.strip()}”")
        ascii_terms = [term for term in needle.split() if term]
        if len(ascii_terms) > 1 and all(any(term in value for value in normalized.values()) for term in ascii_terms):
            score += 80
            reasons.append("关键词组合匹配")
        if len(needle.replace(" ", "")) >= 2:
            fuzzy_candidates = [
                normalized["名称"],
                normalized["分类"],
                normalized["流程位置"],
                *(_normalize_search(alias) for alias in aliases),
            ]
            best = max(SequenceMatcher(None, needle, value).ratio() for value in fuzzy_candidates if value)
            if best >= 0.58:
                score += int(best * 75)
                if not reasons:
                    reasons.append("本地模糊匹配")
        if score:
            ranked.append((score, skill, "；".join(dict.fromkeys(reasons[:2])) or "本地相关度匹配"))
    ranked.sort(key=lambda item: (-item[0], ordered_skills([item[1]])[0].name))
    return [(skill, reason) for _score, skill, reason in ranked]


def search_skills(skills: Iterable[Any], query: str) -> list[Any]:
    """Return ranked skills; use ``search_skill_matches`` to show reasons."""

    return [skill for skill, _reason in search_skill_matches(skills, query)]


def _normalize_search(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = normalized.replace("-", " ").replace("_", " ").replace("/", " ")
    return " ".join(normalized.split())


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
    """Normalize catalog text without hiding part of a Skill description."""

    del limit
    return " ".join(str(value or "").split())


def _catalog_summary(skill: Any, interaction: Any) -> str:
    """Choose the shortest researcher-facing description for a catalog row."""

    summary = interaction.summary if interaction and interaction.summary else _LEGACY_SKILL_SUMMARIES.get(skill.name, skill.description)
    return brief_skill_copy(summary)


def _wrap_catalog_text(value: object, *, width: int) -> list[str]:
    """Normalize one catalog line without inserting presentation-only breaks."""

    del width
    return [" ".join(str(value or "").split())]
