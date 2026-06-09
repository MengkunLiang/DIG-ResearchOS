# ResearchOS

ResearchOS 是一个面向研究工作流的 artifact-first runtime。它的目标不是“做一个能聊天的 Agent”，而是把一个研究项目从 idea 一路推进到：

- 文献检索
- 深度阅读
- 文献综合
- 假设生成
- 新颖性审计
- 外部实验协议编译
- 外部执行器 dry-run / handoff
- 结果摄取、诚信审计和 result-to-claim
- PI 评估
- 论文写作 / 审稿 / 修订
- 投稿包构建与编译

如果只记一句话，可以记成：

```text
想法
 -> 文献池
 -> 精读与综述
 -> 假设与实验计划
 -> 新颖性预审
 -> External Experiment Handoff / Dry-run / Ingest / Audit / Claims
 -> PI Evaluate
 -> Resource Index / Writing / Review / Revise
 -> Submission Bundle
```

## 当前系统能做什么

当前主链路是：

```text
T1
 -> T2-PARAM-GATE
 -> T2
 -> T3
 -> T3.5
 -> T3.6-GATE-SURVEY
    -> no: T4
    -> yes: T3.6-PLAN -> T3.6-GATE-OUTLINE -> T3.6-GATE-CORPUS
            -> optional T3.6-EXPAND
            -> T3.6-STATE
            -> T3.6-SEC-* section-by-section
            -> T3.6-ASSEMBLE -> T3.6-REVIEW -> T3.6-COMPILE -> T3.6-FEED -> T4
 -> T4
    -> candidate pool ready: T4-GATE1 -> user chooses/selects/merges/reanalyzes -> T4
    -> final hypotheses ready: T4.5
    -> pass*: T5-HANDOFF
    -> reframe/drop/unknown: T4.5-HUMAN-REVIEW -> user chooses T5-HANDOFF/T4/done
 -> T5-HANDOFF
 -> T5-EXECUTOR-GATE
    -> mock_dry_run: T5-DRY-RUN
    -> codex_cli / claude_code_window / manual: T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
 -> T7.5
 -> human gate
 -> T8-STYLE-GATE
 -> T8-RESOURCE
 -> T8-WRITE
 -> T8-SECTION-PLAN
 -> T8-SEC-METHOD
 -> T8-SEC-EXPERIMENTS
 -> T8-SEC-RELATED
 -> T8-SEC-ANALYSIS
 -> T8-SEC-INTRO
 -> T8-SEC-CONCLUSION
 -> T8-SEC-ABSTRACT
 -> T8-DRAFT
 -> T8-SELF-CHECK
 -> T8-REVIEW-1
 -> T8-REVISE-1
 -> T8-REVIEW-2
 -> T8-REVISE-2
 -> T8-PAPER-CLAIM-AUDIT
 -> T9
 -> done
```

已经接好的核心能力包括：

- `run` / `resume` 完整流水线
- `run-task` 单阶段调试
- 多阶段断点恢复
- artifact 校验
- T4 假设生成会同时落盘 `ideation/idea_scorecard.yaml`、`ideation/rejected_ideas.md`、`ideation/gate_decisions.json` 和 `ideation/idea_rationales.json`，记录每个 idea 的证据链和决策链
- T2 前会先进入 `T2-PARAM-GATE`，显示当前检测到的任务类型、推荐档位、各档实际参数和自定义输入说明，再确认“保留候选数、精读目标、摘要轻读目标”，并写入 `literature/literature_params.json`；直接回车采用当前推荐项。这不是网络请求 cap：对保留候选会尽可能补 DOI、摘要、OpenAlex/Crossref/Semantic Scholar/arXiv 和 PDF hints；内部 safety cap 只防异常失控。T2 正常路径由检索工具返回值触发 runtime 自动保存 raw。Scout 负责设计 query 和 LLM `semantic_screen`，`expand_queries` 只做机械合并/去重，不内置领域知识。runtime 收尾会先把全量 `papers_raw.jsonl` 去重成审计池，对排序后的前置候选做 bounded light backfill，再按 `literature/literature_params.json` 或默认配置中的 `active_pool_max` 选出保留候选写入 `papers_dedup.jsonl` / `papers_verified.jsonl`，超额候选写入 `papers_backlog.jsonl`。`must_explore` 与 `should_explore` bridge 有不同保留上限；同一 bridge 即使命中 semantic screen、citation snowball 或 metadata fill，也不能绕过 cap，`no_cross/skip` 不强制进入 T3。每篇保留候选都必须有 deep_read、shallow_read 或 backlog/deferred 去向，不能因为缺少 `semantic_screen` 被静默丢弃。T2 收尾会写 `literature/t2_finalize_manifest.json`，把 `project.yaml`、seed 文件/PDF、`bridge_domain_plan.json`、PDF 目录和配置 hash 绑定；这些输入或 coverage 参数变更后旧 T2 产物不能被 resume 误复用。
- T2 不再把重复 raw 命中静默跳过：同一论文来自 core query、bridge query、OpenAlex、Crossref、arXiv、citation snowball 时，会合并 `source_queries`、`search_buckets`、`recalled_by_bridges`、references、OpenAlex OA/PDF hints 和 snowball 来源。`search_log.md` 会展示 Query、Bucket、Bridge、Tool/Source、Calls、Results、Persisted，并单独列出 Bridge Domain Query/Plan 覆盖表；重复 query 会按 normalized query + bucket + bridge + tool 合并，用 Calls 暴露重复调用次数。
- citation graph 会转成实际 T3 证据链：OpenAlex/Crossref references 写入 `citation_edges.json`，bounded one-hop snowball 候选进入 raw，并在保留候选集仍有名额时进入 verified/queue；结构 hub 会占用小额 `citation_hub_slots` 保护槽或以普通 ranking 进入精读目标。缺少 semantic screen 的 hub 会标记 `citation_hub_needs_reader_screening=true`，由 T3 Reader 复核；已有 LLM screen 明确排除的 hub 不强制 deep-read。
- T3 论文阅读以当前队列的 `queue_rank` 为工作单位：`lookup_paper_record(queue_rank=...)` 取单篇 metadata，`save_paper_note(queue_rank=..., content=...)` 自动生成 note 路径、即时校验并刷新 `literature/notes_manifest.json`。`require_deep_read_target=true` 时必须读满 `deep_read_target` 才能进入 T3.5；只有明确配置为 false 才允许达到 `deep_read_min` 后放行。resume 时 `deep_read_queue_pending.jsonl` 会把当前待读项重排为 1..N，同时保留 `original_queue_rank` 指回完整 `deep_read_queue.jsonl`，避免 pending rank 和原始 rank 混淆导致重复阅读。`notes_manifest.json` 同样绑定 T2 队列、verified/dedup、domain_map、seed profile/PDF、`literature/pdfs/` 和配置 hash；换 seed、补 PDF 或改 deep-read 参数后，旧 note 不能直接让 T3 跳过。每篇 `paper_notes/*.md` 都必须记录 `## 12. Reading Coverage`，PDF 可用时必须覆盖到最后一页，只有完整页码覆盖且最终无截断时才能标记 `[FULL-TEXT]`
- T3.5 文献综合会先通过 `build_synthesis_workbench` 从 `paper_notes/`、`paper_notes_abstract/` 和 `metadata_triage.md` 生成 `synthesis_workbench.json`、`synthesis_outline.md` 和 `synthesis_draft.md`，再产出 `synthesis.md`。`abstract-only` 字段会带 `evidence_level/allowed_use`，`weak_evidence_summary` 会靠前展示给 T4；`metadata_triage.md` 只作为补资源/升级阅读线索，不能作为 family、trend、mechanism 或 claim 证据。
- T3.6 是可选综述论文支线：T3.5 后通过状态机级 immediate gate 问“是否撰写综述论文”，选择 yes 后按 taxonomy 规划、人工确认、逐 section 写作、拼装审阅、LaTeX 编译和导出 `survey_insights.json` 的方式执行；它不是把 `synthesis.md` 转成 TeX。默认写成紧凑专业综述，taxonomy 类放在 `Taxonomy` 和 `Comparative Analysis` 内部，而不是为每个类拆出独立 theme 大章；`survey_plan.sectioning_policy` 会在 PLAN 阶段被校验，缺失或 compact 模式下仍输出 `theme_*` 会被拒绝。`T3.6-SEC-THEME-*` 只是兼容/显式长综述槽位，默认 skipped。`decision.json` 或 `corpus_decision.json` 缺失/损坏时会回到对应 gate；上游 synthesis/corpus 变更后旧 gate 决策也会失效。每个 section、`survey_state.json`、`survey_audit.json`、`survey_review_actions.json` 和 `survey_compile_report.json` 都有 input fingerprint 或 TeX/PDF/log/dependency hash 绑定；Abstract 源文件只能写纯正文，禁止正式引用、`\section{Abstract}` 和 `\begin{abstract}`，section 禁止 placeholder、内部 `C1/CID/ResearchOS alignment` 标号和未知 BibTeX key，旧 PDF/log 不能在 TeX 或 references 变化后放行。`survey_insights.json` 会合并 `survey_plan` 与 `survey_state.shared_facts` 中的 `resource_upgrade_needs`，把 taxonomy/challenge/future hints 传给 T4。T4 可以生成 `survey_driven` 候选，但 weak-only 候选必须标 `not_supported_by_current_evidence`，不能被选中或绑定最终 hypothesis。
- T4 现在有正式状态机 Gate1：先由 `IdeationAgent` 生成 `_pass1_forward_candidates.json`、`_pass2_grounding_review.json`、`_candidate_directions.json` 和 `_gate1_selection_brief.md`；runtime 校验候选池后转入 `T4-GATE1`，把用户选择写入 `ideation/_gate1_user_selection.json`，再回到 T4 生成最终 `hypotheses.md`、`exp_plan.yaml`、scorecard、决策链和风险。最终 T4 产物必须晚于 Gate1 选择，候选池在等待期间变化会重新展示 gate，且 T4/T4.5 的复用 freshness 会把 `literature/bridge_domain_plan.json` 纳入输入，避免 bridge 计划变化后复用旧 idea。
- 当前主链从 `T4.5` 进入外部实验链：`T5-HANDOFF -> T5-EXECUTOR-GATE -> T5-DRY-RUN/T5-EXTERNAL-WAIT -> T7-INGEST -> T7-AUDIT -> T7-POST-NOVELTY -> T7-CLAIMS`。ResearchOS 负责编译协议、选择执行器、生成 Codex/Claude/manual prompt、摄取结果、审计证据、实验后 novelty 复核和生成 result-to-claim；真实实验由外部执行器在隔离路径完成。`PARTIAL_RESULTS_READY` 默认不能通过 `T5-EXTERNAL-WAIT` 进入 T7，除非显式允许 partial，并且后续 claim 必须降级处理。
- 旧 `T5`/`T6`/`T7` 仅保留为 legacy 兼容节点；普通 `run-task T5/T6/T7` 会报 retired，显式旧内部实验调试需使用 `LEGACY-* --allow-legacy`
- T4.5 的非通过或不确定 verdict 不会自动拒绝，也不会自动回 T4，而是进入人工决策 gate；用户可以选择继续外部实验链、回 T4 重构或结束
- T8 写作已经拆成 `T8-STYLE-GATE -> T8-RESOURCE -> T8-WRITE -> T8-SECTION-PLAN -> T8-SEC-* -> T8-DRAFT -> T8-SELF-CHECK -> review/revise -> T8-PAPER-CLAIM-AUDIT`，会先确认 IS/CCF-A/both 风格，再生成资源索引、证据计划、图表计划、`paper_state.json` 和每章局部大纲，再用一个节点只写一个 section 的方式逐章生成正文，最后拼装审计；Limitations 已并入 Conclusion
- T8/T9 的实验 claim 必须经过 evidence pack 闭环：`drafts/experiment_evidence_pack.json` 归一化外部执行器证据，`drafts/result_to_claim.json` 把指标转成可写 claim，`drafts/paper_claim_audit.md/json` 在 T9 前检查正文数字、强 claim、mock-only evidence 和 forbidden wording。`paper_claim_audit.json` 必须带 `input_fingerprints`，绑定当前 `paper.tex`、evidence pack 和 result-to-claim hash；旧 audit 不允许放行新正文，审计失败回 `T8-REVISE-2` 定向修订。`craft_audit.json` 还会拦截 Abstract 正式引用、Abstract section heading、正文/注释中的 `C1`/`CID` 内部标号、`TODO/TBD/PLACEHOLDER/LLM review required` 等规划残留；T9 对 `submission/bundle/main.tex` 再做一次同类脏标记扫描，防止模板迁移阶段把内部标号带进最终投稿包。
- CLI 人工输入现在会区分真实回答和无输入；预算扩限 gate 支持 `1/2`、`继续/停止`、`确认/stop` 等输入
- LLM profile / tier / fallback / retry
- human gate
- skill 发现与 `run-skill`
- MCP server 加载与工具注册
- 外部执行器 handoff / mock dry-run / result ingest / evidence audit
- Docker 辅助 LaTeX 编译；legacy 内部实验或外部 executor 自行需要时也可使用 Docker
- trace / logs / resume 快照

## 三个最重要的概念

### 1. Workspace 是唯一事实源

ResearchOS 不靠“模型记住上次说了什么”来恢复进度，而是靠 workspace 中已经落盘的文件。

典型目录：

- `user_seeds/`
- `literature/`
- `resources/`
- `ideation/`
- `novelty/`
- `external_executor/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`
- `_runtime/`

`init-workspace`、`run`、`resume` 和 `run-task` 都会幂等刷新标准目录树，并为每个 workspace 子目录写入 `_DIR_GUIDE.md`。当前 guide 是表格格式：第一张表说明目录用途、生成阶段、下游使用方、可编辑范围和校验规则；第二张表列出关键文件/子目录及用途。已有自定义 `_DIR_GUIDE.md` 不会被覆盖。

新 workspace 默认只创建当前主链会用到的目录；`pilot/`、顶层 `reviews/`、workspace 内 `skills/` 属于 legacy/optional 目录，不再默认创建。旧 workspace 如果已经存在这些目录，runtime 会补一份 legacy guide，但不会删除产物。`external_executor/workdir`、`resources/repos`、PDF/figure 等可能包含外部代码或资产的子树不会被递归写入 guide。

### 2. `run/resume` 和 `run-task` 不是一回事

- `run` / `resume`
  会推进完整状态机，会处理 gate，会自动进入下一阶段
- `run-task`
  只跑一个任务，不推进整个工作流

### 3. Agent 只是系统的一层

ResearchOS 不是“一个 Agent 做完所有事”，而是由这些东西共同组成：

- `StateMachine`
- `AgentRunner`
- 多个 task-specific agent
- `ToolRegistry`
- workspace artifact
- validator

例如 T4 不只产出 `ideation/hypotheses.md`、`ideation/exp_plan.yaml` 和 `ideation/risks.md`，还会产出 `ideation/idea_scorecard.yaml`、`ideation/rejected_ideas.md`、`ideation/gate_decisions.json` 和 `ideation/idea_rationales.json`。这些文件用于追踪每个 idea 从哪里来、解决什么 gap、和哪些工作最像、为什么不同、为什么被选中或淘汰，以及后续什么时候应该继续或停止。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `researchos/agents/` | 各阶段 agent |
| `researchos/runtime/` | runner、LLM client、trace、logger、config |
| `researchos/orchestration/` | 状态机、gate、任务 I/O 契约 |
| `researchos/tools/` | 内置工具、MCP adapter、filesystem、paper tools |
| `researchos/skills/` | skill loader、alias、runner |
| `config/` | 日常设置、模型路由、agent 参数、runtime 配置，以及 `system_config/` 系统契约 |
| `docs/` | 详细文档 |
| `infra/docker/` | Docker 构建与运行脚本 |
| `tests/` | 单测与真实环境测试 |
| `workspace/` | 默认本地 workspace |

## 安装方式

### 方式 A：宿主机安装

适合：

- 本地开发
- 单阶段调试
- 配置排查

```bash
git clone <your-repo-url> ResearchOS
cd ResearchOS

conda create -n researchos python=3.11 -y
conda activate researchos

pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .
```

如果你还需要额外 PDF 能力：

```bash
pip install -r requirements-optional-pdf.txt
```

如果你发现 `researchos` 命令和当前源码行为不一致，优先用：

```bash
PYTHONPATH=/绝对路径/ResearchOS python -m researchos.cli ...
```

### 方式 B：Docker 安装

适合：

- T5 / T7 实验执行
- T9 LaTeX 编译
- 避免宿主机依赖漂移
- 追求更稳定的复现

```bash
cd ResearchOS
bash infra/docker/build.sh
```

然后通过 wrapper 运行：

```bash
bash infra/docker/run.sh selftest
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

完整说明见 [docs/docker.md](./docs/docker.md)。

## 环境变量

先复制模板：

```bash
cp .env.example .env
```

最常用的变量：

| 变量 | 作用 |
| --- | --- |
| `SILICONFLOW_API_KEY` | SiliconFlow 模型 |
| `SILICONFLOW_BASE_URL` | SiliconFlow 兼容基地址 |
| `OPENROUTER_API_KEY` | OpenRouter provider |
| `OPENAI_API_KEY` | OpenAI 官方或兼容接口 |
| `OPENAI_BASE_URL` | OpenAI 兼容基地址 |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `S2_API_KEY` | Semantic Scholar |
| `ELSEVIER_API_KEY` | Elsevier Scopus 搜索 |
| `ELSEVIER_INSTTOKEN` | Elsevier 机构授权令牌，可选 |
| `RESEARCHER_EMAIL` | 文献 API 身份邮箱 |
| `GITHUB_TOKEN` | 可选，MCP / GitHub 相关能力 |

原则：

- 密钥写 `.env`
- 日常运行参数只写 `config/user_settings.yaml`
- 状态机、human gate、CDR schema 和 venue style map 已移入 `config/system_config/`；这些文件会生效，但属于系统契约，普通用户一般不需要编辑
- `config/agent_params.yaml` 是 agent capability registry，只放工具、权限、prompt/schema、behavior、mode 说明；旧扁平字段仍兼容，但不是推荐写法

完整配置说明见 [docs/config.md](./docs/config.md)。

## 5 分钟快速开始

### 1. 校验配置

```bash
cd ResearchOS
python -m researchos.cli validate-config
```

### 2. 跑 provider 自检

```bash
python -m researchos.cli selftest
```

现在这条命令除了检查 provider 连通性，也会检查关键 PDF 解析依赖是否就绪。

### 3. 初始化 workspace

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 4. 跑一个最小 smoke task

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/local-test2
```

### 5. 跑完整流水线

```bash
python -m researchos.cli run --workspace ./workspace/local-test2
```

### 6. 恢复中断任务

```bash
python -m researchos.cli resume --workspace ./workspace/local-test2
```

`resume` 只恢复同一个 workspace 中已经暂停/中断的状态。如果你想保留旧 workspace
的 T1、用户 seed、PDF 和 bridge plan，但丢弃旧 T2 检索结果，从 T2 重新跑完整后续主链，
请新建一个目标 workspace，用 `run --from`：

```bash
python -m researchos.cli run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5 \
  --start-task T2
```

也可以省略 `--start-task`，此时 `run --from` 默认从 `T2` 开始：

```bash
python -m researchos.cli run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5
```

注意：目标 workspace 不能已有 `state.yaml`。这个命令只复制 T2 的前置输入，不复制
旧的 `papers_raw.jsonl`、`papers_verified.jsonl`、`deep_read_queue.jsonl` 等 T2 输出。

### 7. 使用综述种子提纲

如果你把中文综述提纲放进 `user_seeds/`，例如：

```bash
cp /mnt/data/reference/算法风险综述_种子提纲.md \
  ./workspace/algorithm-risk-survey/user_seeds/算法风险综述_种子提纲.md
```

T1/T2/T3.6 会把它规范化为 `user_seeds/seed_outline_profile.json`，并派生
`seed_ideas.md`、`seed_constraints.md` 和 `seed_external_resources.jsonl`。
提纲中的 `representative_literature_directions` 只是检索方向和 taxonomy 先验，
不是已验证 citation，也不会写入 `seed_papers.jsonl`。T2 会据此启用 `survey`
文献流程 profile，覆盖中英文 query、管理/IS/OR、human-AI decision-making、
AI governance/model risk management、XAI/fairness/accountability 等角度；T3.6
会把 `framework`、`sections` 和 `query_profile` 作为综述 taxonomy/scope 的强先验。

## 常见使用方式

### 场景 1：完整跑一个项目

最适合正式使用，能够走完整状态机和 gate。

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "reflective memory for long-horizon llm agents"

python -m researchos.cli run --workspace ./workspace/local-test2

"LightGCN作为一个轻量化的图推荐框架，其最大问题是在稀疏数据上的鲁棒性不足，能否通过引入嵌入空间中的对比学习改善其在稀疏数据上的泛化能力和鲁棒性"
```

如果过程中因为 gate、预算扩限或人工中断暂停：

```bash
python -m researchos.cli resume --workspace ./workspace/local-test2
```

### 场景 2：单独调某个阶段

最适合开发调试。

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6 --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6-GATE-SURVEY --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6-PLAN --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6-STATE --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6-SEC-TAXONOMY --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6-ASSEMBLE --workspace ./workspace/local-test2
python -m researchos.cli run-task T3.6-COMPILE --workspace ./workspace/local-test2
python -m researchos.cli run-task T5-HANDOFF --workspace ./workspace/local-test2
python -m researchos.cli run-task T5-EXECUTOR-GATE --workspace ./workspace/local-test2
python -m researchos.cli run-task T5-DRY-RUN --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-INGEST --workspace ./workspace/local-test2  # 必须已有 dry-run 或 T5-EXTERNAL-WAIT 验收结果
python -m researchos.cli run-task T7-AUDIT --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-POST-NOVELTY --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-CLAIMS --workspace ./workspace/local-test2
python -m researchos.cli run-task T7.5 --workspace ./workspace/local-test2
python -m researchos.cli run-task T9 --workspace ./workspace/local-test2
```

### 场景 3：只跑外部实验协议 dry-run

这不会运行真实大实验，只验证 handoff、result pack、ingest、audit 和 result-to-claim 能否端到端打通。

```bash
python -m researchos.cli run-task T5-HANDOFF --workspace ./workspace/local-test2
python -m researchos.cli run-task T5-EXECUTOR-GATE --workspace ./workspace/local-test2
python -m researchos.cli run-task T5-DRY-RUN --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-INGEST --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-AUDIT --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-POST-NOVELTY --workspace ./workspace/local-test2
python -m researchos.cli run-task T7-CLAIMS --workspace ./workspace/local-test2
```

真实实验时，`T5-EXECUTOR-GATE` 选择 `codex_cli` / `claude_code_window` / `manual` 后会进入 `T5-EXTERNAL-WAIT` 并暂停。外部 Codex/Claude/manual executor 读取 `external_executor/codex_prompt.md`、`claude_code_prompt.md` 或 `manual_instructions.md`，按 `expected_outputs_schema.json` 写 `external_executor/result_pack.json`、`executor_status.json`、`run_manifest.json` 等文件；然后执行 `researchos resume --workspace ...`，验收通过后才进入 `T7-INGEST`。

也可以从另一个 workspace 复制上游产物：

```bash
python -m researchos.cli run-task T8-RESOURCE \
  --workspace ./workspace/scratch \
  --from ./workspace/local-test2
```

如果不是单任务调试，而是想保留旧 workspace 的 T1/seed、从 T2 重新开始并继续完整主链：

```bash
python -m researchos.cli run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5 \
  --start-task T2
```

这里 `run --from` 会复制 `T2` 的前置输入，不复制旧 T2 检索产物。若省略 `--start-task`，默认从 `T2` 开始。

如果 T2 已经可信，只想丢弃旧 T3 阅读结果并从 T3 继续完整主链：

```bash
python -m researchos.cli run \
  --workspace ./workspace/new-test5-t3-redo \
  --from ./workspace/new-test5 \
  --start-task T3
```

这会复制 T3 的声明输入，包括 `project.yaml`、T2 文献池、`deep_read_queue.jsonl`、
`domain_map.json`、`access_audit.md`、`missing_areas.md` 和可选 seed profile；不会复制旧的
`paper_notes/`、`comparison_table.csv` 或 `related_work.bib`。

说明：

- `run/resume` 用来推进完整状态机
- `run-task` 只跑当前阶段
- 但在同一个 workspace 上重跑 `run-task` 时，很多阶段会优先基于已有 artifact 继续
- T3.6 的完整分支建议用 `run/resume` 跑，因为其中包含多个 `ask_human` gate；`run-task` 更适合调试单个 section、assemble 或 compile

### 场景 3：查看状态和 trace

```bash
python -m researchos.cli status --workspace ./workspace/local-test2
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/local-test2
python -m researchos.cli validate --workspace ./workspace/local-test2 --task T7-AUDIT
```

## 测试方式

常用快速回归：

```bash
python -m py_compile researchos/tools/human_gate.py researchos/tools/ask_human.py researchos/agents/reader.py researchos/tools/literature_synthesis.py researchos/runtime/orchestrator.py

pytest -q \
  tests/unit/test_reader_agent.py \
  tests/unit/test_t3_recovery.py \
  tests/unit/test_ask_human_tool.py \
  tests/unit/test_human_gate.py \
  tests/unit/test_runner_basic.py
```

T5 之前链路相关回归：

```bash
pytest -q \
  tests/unit/test_scout_agent.py \
  tests/unit/test_paper_save_tools.py \
  tests/unit/test_ideation_agent.py \
  tests/unit/test_novelty_auditor_agent.py \
  tests/unit/test_schema_validator.py \
  tests/unit/test_cli_runners.py
```

如果当前沙箱禁止绑定本地端口，`test_runtime_extended_tools.py` 中的 `web_fetch` 本地 HTTP server 测试会因为 socket 权限失败；在普通本机或允许 loopback socket 的 CI 中再跑完整文件。

## Skills

ResearchOS 现在支持独立 skill 运行，基于 `SKILL.md`。

常用命令：

```bash
python -m researchos.cli list-skills --skills-root ./skills
python -m researchos.cli run-skill deepxiv "summarize recent memory papers for llm agents"
```

当前仓库自带的 paper 相关 skill 包括：

- `paper-compile`
- `paper-write`
- `deepxiv`

当前状态说明：

- skill 发现已经基于 `SKILL.md` frontmatter
- `Bash(*)`、`Glob(*)`、`Grep(*)` 这类别名会被翻译成 runtime tool
- 如果某个 skill 依赖当前 runtime 没注册的高级工具，它可能会降级，而不是完全不可用

更多说明见：

- [docs/runtime.md](./docs/runtime.md)
- [docs/dev.md](./docs/dev.md)

## MCP

ResearchOS 可以加载 MCP server 配置，并把 MCP tool 暴露给 agent。

关键文件：

- `config/mcp.example.yaml`
- `config/mcp.yaml`

启动时 CLI summary 会显示：

- `mcp_servers`
- `mcp_tools`

完整说明见：

- [docs/runtime.md](./docs/runtime.md)
- [docs/config.md](./docs/config.md)

## 预算、Fallback、恢复、Human Gate

这些是当前 runtime 最重要的几个增强点。

### 预算

每个 task 都有预算，包括：

- 最大步数
- token 预算
- wall time 预算

达到预算上限时，runtime 可以弹出 gate，询问是否扩限继续。

### Fallback

`config/model_routing.yaml` 支持 profile 内多候选模型。

典型行为：

1. 先尝试主模型
2. 主模型失败后立即尝试 fallback
3. 一轮候选都失败后才进入下一轮 retry

### 恢复

当前多个关键阶段都有恢复逻辑。例如：

- T3 会基于 `notes_manifest.json` 和已有且结构合格的 note 重建 pending deep-read queue；缺少 `Reading Coverage`、`Claims vs Evidence`、`Key Quotes`、CDR 字段或 `[FULL-TEXT]` 页码不完整的旧 note 会继续留在待处理队列中
- T3 的 pending queue/meta 会在成功、预算/步数暂停和校验修复暂停等退出路径刷新；`completed_note_count` 是目录中结构合格 note 文件数，`completed_queue_entry_count` 是 deep-read queue 中已完成条目数，历史重复 stub 不会计入完成数，也不会在有效覆盖已满足时拖死整体验证
- T3.5 会复用未过期的 `synthesis_workbench.json` / `synthesis_outline.md` / `synthesis_draft.md`，避免重跑时重复生成结构化脚手架
- T3.6 会复用 `drafts/survey/survey_plan.json`、`survey_state.json`、`section_outlines/`、`sections/*.tex`、`survey_audit.json`、`survey_review_actions.json` 和 `survey_compile_report.json`，但只在 input fingerprint、TeX/PDF/log hash 和 dependency fingerprint 仍匹配当前文件时复用；中断后会按 section 继续，不需要重写整个 survey。默认 compact 结构会让 `theme_1` 到 `theme_4` skipped；只有 `survey_plan.sectioning_policy.mode=standalone_theme_sections` 且通过用户确认时才写少量独立主题章。
- T4.5 已有合格 `novelty_audit.md` 和 `_mechanism_tuples/` 时会执行 resume prefinalize，跳过不必要的 LLM 续跑；`collision_cases.md` 仍只在 High/Medium Overlap 时条件要求
- 外部实验链会基于已有 `external_executor/`、`experiments/` 和 `drafts/result_to_claim.json` / `drafts/experiment_evidence_pack.json` 重建 resume state
- legacy `T5` / `T7` 只有通过 `LEGACY-T5-PILOT` / `LEGACY-T7-FULL --allow-legacy` 显式调试时，才会基于已有内部实验代码和结果目录重建 resume state
- T7.5 / T8 / T9 会优先复用现有产物，而不是假装它们不存在
- 如果上次进程异常退出导致 `state.yaml` 停在 `RUNNING`，`resume` 会自动把最近 run
  标记为 `INTERRUPTED` 并转为 `PAUSED` 后继续
- T7 在进入 LLM 前检查 Docker/GPU 环境；T9 在进入 LLM 前检查 `latexmk` 或 Docker
  统一镜像，环境缺失时会暂停等待修复而不是消耗 LLM 步数

### Human Gate

状态机里支持人工确认节点。当前典型场景包括：

- T7.5 的 PI 评估后分流
- 提交前 / 最终决策类阶段

注意：

- 只有 `run` / `resume` 才会完整体现这些 gate
- `run-task` 只能单独执行某个阶段，不会继续推进完整状态机
- `ask_human` 的 CLI 输入完成后用单独一行 `END` 或 Ctrl+D 提交；非空回答提交后会立即显示 `已收到输入，继续处理...` 和一整行 `-----` 分隔线，避免误以为终端卡住
- 如果 `ask_human` 收到空回答，会最多重试 3 次；连续空回答或非交互环境拿不到输入时，runtime 才会暂停任务并写入 `state.yaml`
- 每个 task/agent 开始时会输出一整行 `==== <task_id> | <agent_name> ==== ` 风格分隔线，便于在长输出中判断当前切换到了哪里
- 只要同一轮会调用 `ask_human`，Agent 本轮正文会默认显示；如果模型把问题写成“请确认以上/这些方向”这类依赖前文的短句，runtime 会自动把本轮正文并入输入问题，避免用户只看到空输入框或缺上下文问题
- 如果同一轮同时包含 `ask_human` 和其它工具，runtime 会先只执行 `ask_human`，并把同轮其它工具延后到下一次模型响应，避免“还没等用户输入就继续写文件/搜索/finish”的并发执行问题
- 如果 Agent 文本里明确要求“请选择/请确认/请提供”等人工决策但忘记调用 `ask_human`，runtime 会自动桥接成 `ask_human` 并在问题开头解释原因；普通状态说明（例如“我来检查已有材料”）不会触发输入框
- 预算扩限 gate 支持数字序号，也支持 `继续`、`确认`、`停止`、`stop` 等常用输入
- `finish_task` 后输出校验多次失败会暂停为可恢复状态，并保留最后一次校验错误；后续可用 `resume` 继续定向修复，而不是直接进入不可恢复 `FAILED`

## 文档导航

建议按角色阅读：

- 系统流程总览：[docs/agent_pipeline.md](./docs/agent_pipeline.md)
- Runtime 实现：[docs/runtime.md](./docs/runtime.md)
- Docker 使用：[docs/docker.md](./docs/docker.md)
- 配置说明：[docs/config.md](./docs/config.md)
- 开发者手册：[docs/dev.md](./docs/dev.md)
- 各阶段与各 Agent 细节：[docs/agent_pipeline.md](./docs/agent_pipeline.md)

## 当前实现状态

当前代码库已经能跑，但它仍然是一个持续演进的研究运行时。

对当前状态，最准确的预期是：

- pipeline 基本可运行
- 关键阶段已具备断点恢复
- T2 正常路径由检索工具返回值触发 runtime 自动保存 raw；Scout 可以在 `semantic_screen` 前调用 `backfill_paper_abstracts`，但 runtime finalize 会先对排序后的 dedup 候选执行 bounded light backfill，再按 `literature/literature_params.json` 的保留候选数分出保留候选 / backlog，随后对保留候选执行 DOI/OpenAlex 详情补全、多源摘要回填、Crossref 详情补全和 title-only seed/弱记录的高置信 OpenAlex 标题补全。OpenAlex/Crossref citation snowball 是从 seed 或 semantic-screened 来源触发的一次性 bounded one-hop 补充，并分别记录 cap、persisted、merged、skipped_existing_snowball_records 和 failed。随后 runtime 确定性完成保留候选集、已核验保留候选、deep-read/shallow-read queue、`papers_backlog.jsonl` 和审计文件。启动时会用 `inspect_user_seeds` 区分真实 seed 与 `_DIR_GUIDE.md`/模板/空文件。`search_log.md` 会显示候选切分前轻量补全、attempted、failed、skipped_by_cap、remaining_missing_*、保留候选/backlog 规模、OpenAlex/Crossref snowball 统计和 title fallback 解析数，避免“检索到了但不知道为什么没摘要/PDF/引用”的黑箱状态。
- T2 会把“检索式规划为空”和“搜索工具收到空 query”都当作硬错误：`expand_queries` / `detect_duplicate_queries` 返回 `empty_query_plan`，搜索工具返回 `empty_query`，`log_scout_progress` 不再允许把缺失 query/source/count 的状态说明写成 `检索 '' -> 0 篇`；`literature/temp/scout_progress.md` 现在由 runtime 在搜索 raw 落盘和 deterministic finalize 时自动刷新，`researchos.log` 会记录 search tool 的 `reported_paper_count`、`persisted_raw_delta`、`raw_count_after` 和 `append_status`
- T3 `fetch_paper_pdf` 会从本地 literature metadata、OpenAlex OA locations、Unpaywall、arXiv 和 DOI fallback 解析候选 PDF；OpenAlex DOI 查询使用 `works/doi:...` endpoint，避免 DOI 斜杠被当成路径。`[FULL-TEXT]` 校验只认 `extract_pdf_text` 完整页码覆盖和最终无截断，例如 `1-4, 5-8, 9-10 / 10`；`all pages` 这类无总页数证明的泛化描述不会通过。
- T3 deep read 后会运行 Reader LLM abstract sweep：默认从保留候选中的 shallow 候选写入 `literature/paper_notes_abstract/`，纯 metadata-only 候选批量写入 `literature/metadata_triage.md` 作为补资源/升级阅读线索；综述 gate 可设置 `all_readable` 并允许从 `papers_backlog.jsonl` 回捞有摘要/PDF 的候选补足可读覆盖。确定性模板只作为失败兜底
- T3.5 已具备分阶段 synthesis workbench，而不是只靠一次 LLM prompt 直接写完整综述；workbench 会把 abstract-only 和 metadata-only 材料放入 `weak_evidence_and_resource_upgrade` 并给相关 hint 标注 `evidence_level/allowed_use`，供 T3.6/T4 识别弱证据和补资源需求。T3.6 再把 `survey_plan.resource_upgrade_needs` 与 `survey_state.shared_facts.resource_upgrade_needs` 合并导出到 `ideation/survey_insights.json`，避免进入 T4 时断层。
- T5 真实外部执行器路径会把 `executor_selection.json`、`result_pack.json`、`executor_status.json` 和 `run_manifest.json` 绑定校验；如果用户先跑过 mock dry-run 后又选择 `codex_cli` / `claude_code_window` / `manual`，旧的 `mock_only/dry_run` result pack 不会被真实路径 ingest。
- T9 已经改成“编译失败则修复并重试”的投稿包阶段；`prepare_submission_bundle` 会写 `submission/bundle/bundle_manifest.json`，记录当前 `drafts/paper.tex`、`literature/related_work.bib`、bundle 内 main/references 和 copied figures 的 hash，避免旧 bundle 在源文件变更后被误用。该工具会把 `\bibliography{...}` 规范成 `\bibliography{references}`，把 `\addbibresource{...}` 规范成 `\addbibresource{references.bib}`，并复制/重写受 read policy 允许且后缀为 `.pdf/.png/.jpg/.jpeg/.svg` 的 `\includegraphics{drafts/...}`、`experiments/...`、`evaluation/...`、`figures/...` 路径到 `submission/bundle/figures/`；目标文件名会带内容 hash，避免同名覆盖。`latex_compile` 会保留 `submission/compile_report.json.attempts` / `attempt_count` 历史，同一 TeX 与依赖 fingerprint 的成功只有在 PDF/log/hash/mtime/size 全部匹配时才复用，同一 hash 的源级失败不会重复编译，必须修改 TeX 或依赖后再重试。Submission validator 会检查 bundle main.tex 的 placeholder/CID 泄露，resume 时只有投稿包、compile report、craft audit 和 claim audit 全部对应当前文件时才会在 LLM 前确定性完成。
- provider 稳定性仍会影响长任务
- 部分配置字段是真正接线的，部分只是声明或部分接线
- 某些 skills 如果依赖未注册能力，会以降级模式运行

## 已知限制

- T4 Gate1 已拆成状态机级 `T4-GATE1` immediate gate；Gate2/计划确认仍在 T4 agent 内通过 artifact 与人类反馈记录完成。
- T3.6 complete 素材范围当前是一次性补检计划和 LLM 审阅记录，不会自动回到 T2/T3 做无限检索；需要真正扩大语料时，应由用户确认后单独补跑检索/阅读。T3.6 taxonomy plan 会把 metadata-only 材料放入 resource upgrade needs，而不是当作综述核心证据。
- 中文管理学文献目前依赖 OpenAlex/Crossref/Semantic Scholar/INFORMS/Scopus 可用覆盖和用户 seed；系统没有内置 CNKI/万方官方 API，不能伪造中文论文 metadata。法规、标准和治理框架（如 EU AI Act、NIST AI RMF、ISO/IEC 42001/23894、中国算法治理规定）会进入 `seed_external_resources.jsonl` 作为 official-source verification 线索，不进入 `papers_dedup.jsonl`，正文引用前必须由官方来源或已检索文献支撑。
- T4.5 novelty 审计仍依赖 LLM 生成搜索策略，但非通过 verdict 已进入人工决策 gate，避免自动拒绝或死循环回退。
- 长任务仍受 provider 稳定性、速率限制和 PDF 解析质量影响。
- Docker / LaTeX / 本地 HTTP 测试依赖宿主环境权限；沙箱环境可能无法覆盖全部集成路径。

## 常见问题

### 为什么 `researchos` 和 `python -m researchos.cli` 行为不一致？

通常是因为 shell 里命中的 console script 与当前源码目录不一致。

优先用：

```bash
PYTHONPATH=/绝对路径/ResearchOS python -m researchos.cli ...
```

或者重新：

```bash
pip install -e .
```

### 为什么中断后会从头跑？

常见原因：

- 你换了 workspace
- 中断前关键 artifact 还没落盘
- 该阶段有恢复逻辑，但预期文件缺失或损坏

### 为什么 `run-task` 和 `run` 表现不一样？

因为 `run-task` 只跑一个阶段，不推进整个状态机。

如果你想测：

- gate
- 自动下一阶段
- `T7 -> T7.5 -> human gate -> T8`

应该用 `run` 或 `resume`。

### 出错后先看哪里？

推荐顺序：

1. CLI 最后的错误摘要
2. `workspace/<name>/_runtime/logs/researchos.log`
3. `workspace/<name>/_runtime/logs/researchos-debug.log`
4. `workspace/<name>/_runtime/traces/*.jsonl`
5. 对应 task 产物目录

控制台默认保持简洁；`--quiet` 只显示状态、暂停、错误和最终结果，`--verbose` 显示更多 Agent 文本与 step 摘要。LiteLLM 的 INFO 噪音默认不会进入控制台或 `researchos.log`。

## 进一步阅读

如果你准备继续扩展这个项目，建议继续阅读：

- `CLAUDE.md`
- `config/README.md`
- `docs/agent_pipeline.md`
