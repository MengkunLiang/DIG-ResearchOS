from __future__ import annotations

"""Stable user-facing semantics for state-machine tasks and artifacts."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StageProfile:
    title: str
    goal: str
    research_question: str
    operations: tuple[str, ...]
    branch_note: str = ""


_PROFILES: dict[str, StageProfile] = {
    "T1": StageProfile("T1 · Research Scope Initialization", "建立可审计研究范围与桥接检索边界", "研究问题、约束和跨域方向是否被明确且可执行？", ("规范项目范围", "归一化 seed", "生成 bridge 计划")),
    "T2": StageProfile("T2 · Literature Discovery & Domain Mapping", "构建可信文献池、引用结构和阅读队列", "哪些论文、领域关系和阅读优先级足以支撑后续综合？", ("Query portfolio", "检索与去重", "metadata 验证", "citation expansion", "阅读队列"), "结束后进入覆盖确认 Gate。"),
    "T3": StageProfile("T3 · Evidence-Grounded Literature Reading", "把候选论文转为可追溯的 section 级证据", "每篇论文实际支持什么，哪些内容仍不被支持？", ("访问级别判断", "PDF section 阅读", "证据卡", "比较表")),
    "T3.5": StageProfile("T3.5 · Literature Synthesis", "组织跨论文机制、贡献和张力", "领域的共同机制、边界、冲突和待补证据分别是什么？", ("method family", "contribution space", "mechanism clusters", "tension/transfer")),
    "T3.6-GATE-SURVEY": StageProfile("T3.6 · Survey Decision", "判断当前语料是否足以单独写成综述", "taxonomy 覆盖与语料充分性是否达到可审计 survey 的门槛？", ("corpus sufficiency", "taxonomy coverage", "human decision"), "Survey 是可选分支；无论是否选择，T3.5 synthesis 仍供 T4 使用。"),
    "T3.6-PLAN": StageProfile("T3.6 · Survey Taxonomy Plan", "把综合语料编译为 survey taxonomy 与章节计划", "现有方法家族、机制和证据能否形成可解释的分类结构？", ("corpus inventory", "taxonomy plan", "coverage gaps"), "不把检索覆盖缺口直接称作研究缺口。"),
    "T3.6-STATE": StageProfile("T3.6 · Survey Section State", "为 survey 的各章节绑定可用证据", "每个 taxonomy 分支是否拥有足够的具体论文和证据类型？", ("section state", "evidence binding", "citation plan")),
    "T3.6-VISUALS": StageProfile("T3.6 · Survey Taxonomy Visual", "生成唯一允许的事实性 taxonomy overview", "survey taxonomy 是否有足够明确的结构可被忠实可视化？", ("taxonomy structure", "explicit paper links", "visual manifest"), "默认最多一图；性能、相对提升、筛选分数和推断性热图一律禁止。"),
    "T3.6-ASSEMBLE": StageProfile("T3.6 · Survey Assembly", "拼装 survey 并执行引用、覆盖和编译审计", "当前综述是否覆盖 taxonomy、证据边界和模板编译要求？", ("assemble", "coverage audit", "citation audit", "LaTeX compile")),
    "T3.6-COMPILE": StageProfile("T3.6 · Survey Real Compilation", "以本地或 Docker TeX 后端真实编译 survey", "当前 TeX 源、图表与引用是否能生成可核验 PDF？", ("LaTeX preflight", "compile", "log/fingerprint audit")),
    "T4": StageProfile("T4 · Idea Generation & Candidate Governance", "从证据综合形成可选择、可反驳的研究方向", "哪些新方向由主线证据支持，哪些仍需要重构或补检？", ("主线前向发散", "bridge synthesis", "coverage supplements", "Pass 1", "Pass 2", "候选治理"), "Coverage supplements do not replace mainline reasoning."),
    "T4.5": StageProfile("T4.5 · Novelty & Collision Audit", "审计新颖性、机制差异与必须基线", "候选是否只是已有工作的常规组合，最低证据门槛是什么？", ("collision review", "mechanism tuple", "design-rationale tuple", "baseline requirements")),
    "T5-REBOOST-GATE": StageProfile("T5 · Research-to-Execution Reboost", "把研究意图重新编译为外部执行约束", "实验执行必须保留什么机制、基线和 claim 边界？", ("Pre-T5 inventory", "context reboost", "claim boundaries")),
    "T5-HANDOFF": StageProfile("T5 · External Experiment Handoff", "生成可审计的执行 handoff 与项目专属 Skill", "外部执行器应如何实现、验证并回传结果？", ("handoff pack", "project skill specialization", "execution contract")),
    "T5-SKILL-CUSTOMIZATION-GATE": StageProfile("T5 · Project Skill Review", "由人工确认项目专属执行 Skill 与职责", "生成的 root Skill 和 sub-skills 是否覆盖资源、基线、运行和回传边界？", ("skill inventory", "responsibility review", "human decision")),
    "T5-EXPR-MATERIAL-GATE": StageProfile("T5 · External Material Intake", "确认数据、仓库、权重和 baseline 材料是否就绪", "外部执行器是否已有真实运行所需的项目材料？", ("material inventory", "missing material notice", "human decision")),
    "T5-EXECUTOR-GATE": StageProfile("T5 · Executor Selection", "选择外部实验执行方式", "哪种执行器能够在既定资源、权限和审计契约下完成实验？", ("executor options", "side-effect confirmation", "human decision")),
    "T5-DRY-RUN": StageProfile("T5 · Executor Protocol Dry Run", "验证外部执行器文件协议", "在不做真实实验时，handoff、状态与 result pack 契约能否联通？", ("mock handoff", "protocol validation", "result-pack schema")),
    "T5-EXTERNAL-WAIT": StageProfile("T5 · External Executor Wait", "等待并验证外部执行器回传", "result pack 是否包含可审计的 run、config、log 和原始结果？", ("executor status", "result-pack readiness", "human/external wait")),
    "T7-INGEST": StageProfile("T7 · External Result Ingestion", "摄取外部实验的原始运行、配置和日志", "哪些运行是可被审计的实验事实？", ("result pack ingest", "run inventory", "evidence index")),
    "T7-AUDIT": StageProfile("T7 · Experiment Integrity Audit", "核验结果 provenance、公平性和基线覆盖", "哪些结果可以进入论文证据，哪些必须降级或拒绝？", ("integrity", "baseline coverage", "metric provenance", "fairness")),
    "T7-POST-NOVELTY": StageProfile("T7 · Post-Experiment Novelty Review", "以已实现方法和结果重审贡献边界", "计划贡献是否被实现和证据支持？", ("implemented-vs-planned", "collision update", "claim boundary")),
    "T7-CLAIMS": StageProfile("T7 · Result-to-Claim Compilation", "把审计结果转换为保守论文 claim", "每条 claim 的支持等级、限制与禁止表述是什么？", ("claim mapping", "evidence pack", "must-not-claim", "figure/table evidence")),
    "T7.5": StageProfile("T7.5 · PI Evidence Decision", "由人工确认下一步研究决策", "现有证据足以写作，还是应补实验、回到 Idea 或停止？", ("evidence sufficiency", "human decision")),
    "T8-RESOURCE": StageProfile("T8 · Manuscript Resource Index", "索引写作证据并建立章节对齐关系", "每个论点、章节、图表应使用哪些可追溯材料？", ("resource index", "evidence plan", "alignment matrix", "claim ledger")),
    "T8-STYLE-GATE": StageProfile("T8 · Venue and Writing Style", "确认稿件语言、venue 与叙事风格", "篇幅、论证方式和模板应如何匹配 UTD/期刊或 CCF-A 会议目标？", ("venue profile", "language policy", "human decision")),
    "T8-WRITE": StageProfile("T8 · Paper Storyline & Outline", "建立 venue-aware 研究叙事与章节结构", "研究动机、技术贡献和证据链如何组成一个可验证故事？", ("outline", "storyline", "contribution/evidence alignment")),
    "T8-SECTION-PLAN": StageProfile("T8 · Section Writing Plan", "初始化逐章节写作状态", "每个章节应承担何种论证职责并使用哪些证据？", ("paper state", "section outlines")),
    "T8-DRAFT": StageProfile("T8 · Manuscript Assembly", "拼装完整论文并进行写作审计", "当前草稿是否可追溯、连贯且符合证据边界？", ("assemble manuscript", "craft audit", "claim audit")),
    "T8-SELF-CHECK": StageProfile("T8 · Author Self-check", "在外部审稿前检查论证、引用和 claim 边界", "当前论文是否存在显著的证据错配、叙事断裂或格式问题？", ("self review", "claim/citation audit", "patch plan")),
    "T8-REVIEW-1": StageProfile("T8 · Review Round 1", "进行第一轮结构化论文审阅", "研究动机、技术贡献、实验和写作证据链有哪些高优先级问题？", ("review findings", "severity ranking", "patch list")),
    "T8-REVIEW-2": StageProfile("T8 · Review Round 2", "对修订后的论文做独立复审", "第一轮修订是否真正消除了问题，尚有哪些投稿阻塞项？", ("independent review", "remaining risks", "patch list")),
    "T8-REVISE-1": StageProfile("T8 · Revision Round 1", "按第一轮审稿问题修订并重审证据绑定", "每个 patch 是否可追溯到对应的证据、章节和审稿发现？", ("apply patches", "update state", "claim audit")),
    "T8-REVISE-2": StageProfile("T8 · Revision Round 2", "按第二轮审稿问题完成最终修订", "最终版本是否保持证据边界并消除剩余阻塞项？", ("apply patches", "final consistency", "claim audit")),
    "T8-PAPER-CLAIM-AUDIT": StageProfile("T8 · Final Claim Audit", "在提交前核验论文所有实质主张", "每个强 claim 是否仍与当前实验、引用和 source 版本一致？", ("claim ledger", "evidence match", "must-not-claim")),
    "T9": StageProfile("T9 · Submission Bundle & Real Compilation", "生成可提交 bundle 并证明真实编译", "当前提交版本的 PDF、依赖、引用和 claim audit 是否一致？", ("venue migration", "bundle manifest", "LaTeX compile", "fingerprint validation")),
}


def stage_profile(task_id: str) -> StageProfile:
    if task_id in _PROFILES:
        return _PROFILES[task_id]
    if task_id.startswith("T2-"):
        return StageProfile(f"{task_id} · Literature Decision Gate", "确认文献覆盖或语言/阅读参数", "当前覆盖计划是否适合研究目标？", ("读取阶段材料", "展示决策影响", "等待人工选择"))
    if task_id.startswith("T3.6-"):
        return StageProfile(f"{task_id} · Survey Branch", "构建、审查或编译 taxonomy-driven survey", "综述语料、taxonomy 与章节是否足以形成独立 survey？", ("taxonomy/corpus", "section state", "coverage/review", "compile"), "这是可选 Survey 分支，不替代主线 T4。")
    if task_id.startswith("T5-"):
        return StageProfile(f"{task_id} · External Execution Preparation", "准备外部实验或等待执行器回传", "当前实验材料、执行器与回传契约是否就绪？", ("artifact inventory", "handoff/executor status", "human decision if needed"))
    if task_id.startswith("T8-SEC-"):
        return StageProfile(f"{task_id} · Manuscript Section Draft", "写作一个有证据边界的论文章节", "本节是否只使用允许的主张、引用和实验事实？", ("section evidence", "draft", "state update"))
    if task_id.startswith("T8-REVIEW") or task_id.startswith("T8-REVISE") or task_id == "T8-SELF-CHECK":
        return StageProfile(f"{task_id} · Manuscript Review & Revision", "审查或修订当前论文版本", "发现的问题是否被追溯到证据、章节和具体 patch？", ("review findings", "patches", "audit"))
    return StageProfile(f"{task_id} · ResearchOS Stage", "推进当前研究工作流节点", "该节点的输入、判断和输出是否满足下游需要？", ("读取 Artifact", "执行阶段判断", "写入可审计结果"))


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
    ("paper_notes", "section 级论文阅读证据卡"),
    ("comparison_table.csv", "跨论文方法、证据和局限比较表"),
    ("synthesis_workbench.json", "综合阶段的机制、贡献和张力工作台"),
    ("synthesis.md", "面向人类的文献综合结论"),
    ("_candidate_directions.json", "T4 Gate1 的结构化候选池"),
    ("idea_scorecard.yaml", "选定方向的证据、风险和评分链"),
    ("hypotheses.md", "可证伪假设与观察预测"),
    ("exp_plan.yaml", "实验计划、基线、指标与停止条件"),
    ("handoff_pack.json", "外部执行器的研究意图与证据契约"),
    ("paper_card_evidence_index.json", "外部执行器按需读取的论文卡索引与证据使用边界"),
    ("result_pack.json", "外部执行器回传的结果包"),
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
    return "阶段 Artifact"


def artifact_consumers(task_id: str, path: str) -> str:
    prefix = Path(path).as_posix()
    if prefix.startswith("literature/paper_notes") or prefix.startswith("literature/notes_manifest"):
        return "T3.5 / T4 / T4.5 / T5 / 外部执行器 / T7 / T8"
    if prefix.startswith("literature/"):
        return "T3 / T3.5 / T4 / T4.5 / T5 / T7 / T8"
    if prefix.startswith("ideation/") or prefix.startswith("novelty/"):
        return "T4.5 / T5 / T7 / T8"
    if prefix.startswith("external_executor/"):
        return "外部执行器 / T7"
    if prefix.startswith("experiments/"):
        return "T7.5 / T8 / T9"
    if prefix.startswith("drafts/"):
        return "T8 / T9"
    if prefix.startswith("submission/"):
        return "投稿人工检查"
    return "后续状态机节点"
