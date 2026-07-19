from __future__ import annotations

"""Stable user-facing semantics for state-machine tasks and artifacts."""

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class StageProfile:
    title: str
    goal: str
    research_question: str
    operations: tuple[str, ...]
    branch_note: str = ""


_PROFILES: dict[str, StageProfile] = {
    "T1": StageProfile("T1 · 研究范围初始化", "建立可审计研究范围与跨领域检索边界", "研究问题、约束和跨领域方向是否已经明确且可执行？", ("明确项目范围", "整理种子论文", "生成跨领域检索计划")),
    "T2": StageProfile("T2 · 文献检索与领域映射", "构建可信文献池、引用关系和阅读队列", "哪些论文、领域关系和阅读优先级足以支撑后续综合？", ("设计检索组合", "检索与去重", "核验论文信息", "扩展参考文献", "建立阅读队列"), "完成后会请你确认文献覆盖是否足够。"),
    "T3": StageProfile("T3 · 文献阅读", "把候选论文整理为可追溯的论文阅读笔记", "每篇论文实际支持什么，哪些内容仍没有足够依据？", ("检查论文是否可读", "阅读 PDF", "整理阅读笔记", "更新比较表")),
    "T3.5": StageProfile("T3.5 · 文献综合", "组织跨论文的机制、贡献和分歧", "领域的共同机制、适用边界、冲突和待补证据分别是什么？", ("归纳方法类别", "梳理贡献空间", "聚类机制", "识别冲突与迁移关系")),
    "T3.6-GATE-SURVEY": StageProfile("T3.6 · 是否撰写综述", "判断当前文献是否足以单独写成综述", "分类框架覆盖度和材料充分性是否达到可核验综述的要求？", ("检查材料充分性", "检查分类覆盖度", "等待你的决定"), "综述是可选分支；无论是否选择，T3.5 的文献综合仍会供 T4 使用。"),
    "T3.6-PLAN": StageProfile("T3.6 · 综述分类框架规划", "把综合材料整理为综述分类框架和章节计划", "现有方法类别、机制和证据能否形成易理解的分类结构？", ("盘点文献材料", "规划分类框架", "识别覆盖不足"), "检索覆盖不足不等同于研究领域的真实空白。"),
    "T3.6-STATE": StageProfile("T3.6 · 综述章节依据整理", "为综述各章节绑定可用依据", "每个分类分支是否拥有足够的具体论文和证据类型？", ("整理章节状态", "绑定论文依据", "规划引用")),
    "T3.6-VISUALS": StageProfile(
        "T3.6 · 综述分类图",
        "将综述的研究脉络和分类结构整理为一张概览图",
        "不同研究路径如何组织，各分支由哪些代表性论文支撑？",
        ("整理分类层次", "标注代表性论文", "生成概览图与图注"),
        "默认最多生成一张概览图；图中只呈现已确定的分类关系与代表性文献，不制作性能比较、相对提升、筛选分数或推断性热图。",
    ),
    "T3.6-ASSEMBLE": StageProfile("T3.6 · 综述拼装与审计", "拼装综述并检查引用、覆盖和编译情况", "当前综述是否覆盖分类框架、依据边界和模板编译要求？", ("拼装综述", "检查覆盖情况", "检查引用", "编译 LaTeX")),
    "T3.6-COMPILE": StageProfile("T3.6 · 综述真实编译", "使用本地或 Docker TeX 环境真实编译综述", "当前 TeX 源文件、图表与引用是否能生成可核验 PDF？", ("编译前检查", "编译", "检查日志与版本指纹")),
    "T4": StageProfile("T4 · 研究方向生成与比较", "从文献综合形成可选择、可反驳的研究方向", "哪些新方向由主线证据支持，哪些仍需要重构或补充检索？", ("从主线证据发散", "综合跨领域材料", "整理补充方向", "第一轮生成", "第二轮核验", "生成候选比较卡"), "补充方向用于检验和扩展主线，不会替代主线依据。"),
    "T4.5": StageProfile("T4.5 · 新颖性与相似工作审计", "审计新颖性、机制差异和必需基线", "候选是否只是已有工作的常规组合，最低依据门槛是什么？", ("检查相似工作", "核验机制差异", "核验设计理由", "确认必需基线")),
    "T5-REBOOST-GATE": StageProfile("T5 · 研究意图整理为实验约束", "把 T4.5 已确认的研究意图编译为外部执行时必须遵守的约束", "实验执行必须保留哪些机制、基线和论文主张边界？", ("核对 T4.5 正式材料", "编译研究上下文", "校验主张边界")),
    "T5-HANDOFF": StageProfile("T5 · 外部实验交接", "生成可审计的执行说明", "外部执行器应如何实现、验证并回传结果？", ("生成交接说明", "准备执行约定", "保存可恢复状态")),
    "T5-SPECIALIZE-EXECUTOR-SKILLS": StageProfile("T5 · 生成项目专属 Skill", "从已确认的交接材料生成项目专属执行 Skill", "每个执行 Skill 是否有清晰职责、输入边界和回传要求？", ("构建项目上下文", "生成 Skill Suite", "校验发布结果")),
    "T5-PROTOCOL-GATE": StageProfile("T5 · 实验协议就绪确认", "区分交接已编译与真实实验已获授权", "当前研究 setting、资源与执行约束是否足以开始实现和正式运行？", ("展示已编译协议", "列出待确认决策", "等待你的决定"), "协议待定时可保留 handoff 和准备材料，但不得让执行器自行选择仿真框架、backbone、种子、样本规模或预算。"),
    "T5-EXPR-MATERIAL-GATE": StageProfile("T5 · 外部实验材料准备", "确认数据、代码仓库、模型权重和基线材料是否就绪", "外部执行器是否已有真实运行所需的项目材料？", ("盘点材料", "说明缺少材料", "等待你的决定")),
    "T5-EXECUTOR-GATE": StageProfile("T5 · 选择外部实验执行方式", "选择外部实验的执行方式", "哪种执行方式能够在既定资源、权限和审计约定下完成实验？", ("展示执行方式", "确认可能的副作用", "等待你的决定")),
    "T5-DRY-RUN": StageProfile("T5 · 外部执行协议演练", "验证外部执行器之间的文件交接是否正常", "在不做真实实验时，交接说明、状态和结果文件能否正常衔接？", ("模拟交接", "检查文件协议", "检查结果文件格式")),
    "T5-EXTERNAL-WAIT": StageProfile("T5 · 等待外部执行器回传", "等待并验证外部执行器回传给 T8 的材料", "核心 executor_research_report.md 和支持材料是否已经就绪？", ("检查执行状态", "检查 T8 交接报告", "等待外部执行完成")),
    "T8-RESOURCE": StageProfile("T8 · 论文写作资料索引", "索引写作依据并建立章节对齐关系", "每个论点、章节和图表应使用哪些可追溯材料？", ("整理资料索引", "规划证据使用", "建立章节对齐表", "维护主张清单")),
    "T8-STYLE-GATE": StageProfile("T8 · 确认投稿目标与写作风格", "确认稿件语言、投稿目标和叙事风格", "篇幅、论证方式和模板如何匹配目标期刊或会议？", ("查看投稿目标", "确认语言策略", "等待你的决定")),
    "T8-WRITE": StageProfile("T8 · 论文叙事与大纲", "建立与投稿目标匹配的研究叙事和章节结构", "研究动机、技术贡献和证据链如何组成可验证的故事？", ("撰写大纲", "组织叙事", "对齐贡献与证据")),
    "T8-SECTION-PLAN": StageProfile("T8 · 章节写作计划", "初始化逐章节写作状态", "每个章节应承担什么论证职责，并使用哪些依据？", ("建立论文状态", "规划章节大纲")),
    "T8-DRAFT": StageProfile("T8 · 论文拼装", "拼装完整论文并进行写作审计", "当前草稿是否可追溯、连贯且符合证据边界？", ("拼装论文", "检查写作质量", "检查论文主张")),
    "T8-SELF-CHECK": StageProfile("T8 · 作者自检", "在外部审稿前检查论证、引用和主张边界", "当前论文是否存在明显的证据错配、叙事断裂或格式问题？", ("作者自检", "检查主张与引用", "制定修改计划")),
    "T8-REVIEW-1": StageProfile("T8 · 第一轮论文审阅", "进行第一轮结构化论文审阅", "研究动机、技术贡献、实验和写作证据链有哪些高优先级问题？", ("整理审阅发现", "按严重程度排序", "制定修改清单")),
    "T8-REVIEW-2": StageProfile("T8 · 第二轮论文审阅", "对修订后的论文进行独立复审", "第一轮修订是否真正解决了问题，尚有哪些投稿阻塞项？", ("独立复审", "识别剩余风险", "制定修改清单")),
    "T8-REVISE-1": StageProfile("T8 · 第一轮修订", "按第一轮审阅问题修订并重新检查证据绑定", "每项修改是否可以追溯到对应的依据、章节和审阅意见？", ("应用修改", "更新状态", "检查论文主张")),
    "T8-REVISE-2": StageProfile("T8 · 第二轮修订", "按第二轮审阅问题完成最终修订", "最终版本是否保持证据边界并消除剩余阻塞项？", ("应用修改", "检查最终一致性", "检查论文主张")),
    "T8-PAPER-CLAIM-AUDIT": StageProfile("T8 · 最终论文主张审计", "在提交前核验论文所有实质主张", "每个强主张是否仍与当前实验、引用和原始材料一致？", ("维护主张清单", "核验证据匹配", "标注禁止表述")),
    "T9": StageProfile("T9 · 投稿包与真实编译", "生成可提交的投稿包并证明真实编译成功", "当前投稿版本的 PDF、依赖、引用和主张审计是否一致？", ("迁移投稿模板", "生成投稿清单", "编译 LaTeX", "核验版本指纹")),
}


def stage_profile(task_id: str) -> StageProfile:
    if task_id in _PROFILES:
        return _PROFILES[task_id]
    if task_id.startswith("T2-"):
        return StageProfile(f"{task_id} · 文献方案确认", "确认文献覆盖或语言与阅读参数", "当前覆盖计划是否适合研究目标？", ("读取已有材料", "说明不同选择的影响", "等待你的决定"))
    if task_id.startswith("T3.6-"):
        return StageProfile(f"{task_id} · 综述分支", "构建、审查或编译基于分类框架的综述", "综述文献、分类框架和章节是否足以形成独立综述？", ("检查分类与文献", "整理章节依据", "检查覆盖与审阅", "编译"), "这是可选的综述分支，不替代主线 T4。")
    if task_id.startswith("T5-"):
        return StageProfile(f"{task_id} · 外部执行准备", "准备外部实验或等待外部执行器回传", "当前实验材料、执行方式与回传要求是否就绪？", ("盘点文件", "检查交接与执行状态", "必要时等待你的决定"))
    if task_id.startswith("T8-SEC-"):
        return StageProfile(f"{task_id} · 论文章节起草", "写作一个遵守证据边界的论文章节", "本节是否只使用允许的主张、引用和实验事实？", ("读取章节依据", "起草章节", "更新状态"))
    if task_id.startswith("T8-REVIEW") or task_id.startswith("T8-REVISE") or task_id == "T8-SELF-CHECK":
        return StageProfile(f"{task_id} · 论文审阅与修订", "审查或修订当前论文版本", "发现的问题是否已经追溯到依据、章节和具体修改？", ("整理审阅发现", "执行修改", "重新审计"))
    if task_id.startswith("SKILL_INTAKE_"):
        skill_name = task_id.removeprefix("SKILL_INTAKE_")
        return StageProfile(
            f"Skill Intake · {skill_name}",
            "以多轮人机交互收集并整理启动材料",
            "研究材料是否已被放入可验证路径，并足以开始该原子能力？",
            ("材料检查", "定向追问", "受限整理", "等待确认"),
            "本阶段不会生成论文、实验结果或最终 Skill 产物。",
        )
    if task_id.startswith("SKILL_"):
        skill_name = task_id.removeprefix("SKILL_")
        return StageProfile(
            f"Skill · {skill_name}",
            "在明确输入、证据边界和输出契约下执行原子科研能力",
            "已提供材料实际支持什么，哪些结论仍需用户补充或标为未验证？",
            ("读取已验证材料", "执行专属分析", "记录风险/未支持项", "写入可恢复产物"),
            "工具调用、输入边界和产物路径会单独记录；不把工具提示当作学术结论。",
        )
    return StageProfile(f"{task_id} · ResearchOS 当前步骤", "推进当前研究工作流步骤", "本步骤的输入、判断和输出是否满足后续需要？", ("读取已有文件", "完成本步判断", "写入可核验结果"))


def stage_display_name(task_id: str) -> str:
    """Return a user-facing stage name without exposing internal task codes."""

    title = stage_profile(task_id).title
    return re.sub(r"^T\d+(?:\.\d+)?(?:-[A-Z0-9-]+)?\s*·\s*", "", title).strip() or "ResearchOS"


_ARTIFACT_MEANINGS: tuple[tuple[str, str], ...] = (
    ("project.yaml", "项目研究问题、范围、约束和目标 venue"),
    ("seed_papers.jsonl", "人工提供或确认的核心论文 seed"),
    ("bridge_domain_plan.json", "已确认的跨域检索与迁移计划"),
    ("papers_raw.jsonl", "各来源的原始检索命中，尚未完成筛选"),
    ("papers_dedup.jsonl", "去重后保留的候选文献池"),
    ("papers_verified.jsonl", "元数据已核验、可进入阅读处置的候选"),
    ("papers_backlog.jsonl", "暂未进入 active pool 的候选及其保留原因"),
    ("deep_read_queue.jsonl", "T3 的优先精读队列与保护 slot"),
    ("domain_map.json", "引用/领域结构提示，不是质量或新颖性结论"),
    ("deep_read_notes", "论文阅读笔记"),
    ("comparison_table.csv", "跨论文方法、证据和局限比较表"),
    ("synthesis_workbench.json", "综合阶段的机制、贡献和张力工作台"),
    ("synthesis.md", "面向人类的文献综合结论"),
    ("_candidate_directions.json", "T4 供人工选择的结构化候选池"),
    ("idea_scorecard.yaml", "选定方向的证据、风险和评分链"),
    ("hypotheses.md", "可证伪假设与观察预测"),
    ("research_proposal.md", "T4.5 通过审计后的完整研究方案与 T5 planning context"),
    ("proposal_manifest.json", "Proposal 的来源追溯、审计状态和 T5 交接边界"),
    ("exp_plan.yaml", "实验计划、基线、指标与停止条件"),
    ("handoff_pack.json", "外部执行器的研究意图与证据契约"),
    ("paper_card_evidence_index.json", "外部执行器按需读取的论文卡索引与证据使用边界"),
    ("executor_research_report.md", "T5 交给 T8 的核心外部执行研究报告"),
    ("result_pack.json", "外部执行器回传的支持性结果包"),
    ("integrity_audit.json", "实验 provenance、完整性与公平性审计"),
    ("result_to_claim.json", "实验事实到允许论文 claim 的映射"),
    ("experiment_evidence_pack.json", "写作可用的规范化实验证据"),
    ("manuscript_resource_index.json", "Writer 实际可用资料的索引"),
    ("alignment_matrix.json", "贡献、章节、证据的内部对齐关系"),
    ("paper_state.json", "逐章节写作状态与已绑定证据"),
    ("compile_report.json", "真实 LaTeX 编译尝试及当前结果"),
    ("bundle_manifest.json", "投稿 bundle 与源文件的 fingerprint"),
)


def artifact_meaning(path: str) -> str:
    normalized = Path(path).as_posix()
    for needle, meaning in _ARTIFACT_MEANINGS:
        if normalized.endswith(needle) or f"/{needle}" in normalized:
            return meaning
    if normalized.endswith(".jsonl"):
        return "结构化记录集合，供下游阶段过滤、审计或汇总"
    if normalized.endswith(".json") or normalized.endswith(".yaml"):
        return "结构化阶段状态或审计结果"
    if normalized.endswith(".csv"):
        return "可比较的表格数据"
    if normalized.endswith(".tex"):
        return "LaTeX 章节或论文源文件"
    if normalized.endswith(".md"):
        return "面向人工的阶段结论、审计或阅读材料"
    return "当前步骤的文件"


def artifact_consumers(task_id: str, path: str) -> str:
    prefix = Path(path).as_posix()
    if prefix.startswith("literature/deep_read_notes") or prefix.startswith("literature/notes_manifest"):
        return "T3.5 / T4 / T4.5 / T5 / 外部执行器 / T8"
    if prefix.startswith("literature/"):
        return "T3 / T3.5 / T4 / T4.5 / T5 / T8"
    if prefix.startswith("ideation/") or prefix.startswith("novelty/"):
        return "T4.5 / T5 / T8"
    if prefix.startswith("external_executor/"):
        return "外部执行器 / T5-EXTERNAL-WAIT / T8"
    if prefix.startswith("experiments/"):
        return "T8 / T9 / 旧内部实验兼容检查"
    if prefix.startswith("drafts/"):
        return "T8 / T9"
    if prefix.startswith("submission/"):
        return "投稿人工检查"
    return "后续步骤"
