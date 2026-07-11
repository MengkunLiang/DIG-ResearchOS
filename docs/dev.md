# ResearchOS Developer Guide

本文档面向第一次接手 ResearchOS 代码库、需要本地开发、调试、扩展和排障的开发者。

如果你更关心“这个系统整体怎么工作”，先看：

- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/runtime.md](./runtime.md)
- [docs/config.md](./config.md)
- [docs/docker.md](./docker.md)

如果你更关心“作为用户怎么部署和使用”，看：

- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)

---

## 1. 开发者先建立的心智模型

ResearchOS 不是一个“写几个 prompt 然后调用 LLM”的轻量脚本仓库，而是一个：

- 以 `workspace` 为事实源
- 以 `state_machine` 为流程骨架
- 以 `agent + tool` 为执行主体
- 以 `artifact validation` 为收敛机制
- 以 `trace / logs / resume` 为可调试基础

的研究流程运行时。

理解它时，建议从这五层看：

1. CLI 层
2. Runtime 层
3. Orchestration 层
4. Agent / Prompt 层
5. Tool / Workspace 层

典型调用链：

```text
researchos run-task T3
 -> cli.py
 -> SingleTaskRunner
 -> StateMachine / Task I-O Contract
 -> AgentRunner
 -> ReaderAgent
 -> Tools
 -> workspace artifacts
 -> validator
```

---

## 2. 本地开发环境

### 2.1 推荐环境

- Linux / WSL / 容器化 Linux
- Python 3.11
- Conda
- 可选：Docker

这个仓库在本机开发时，推荐使用专门的 conda 环境。

如果你沿用当前维护环境，可直接：

```bash
conda activate researchos
```

如果你是新机器，推荐自己创建一个独立环境：

```bash
conda create -n researchos python=3.11 -y
conda activate researchos
```

### 2.2 安装依赖

最常用的开发安装方式：

```bash
cd ResearchOS
pip install -r requirements.txt
pip install -e .
```

说明：

- `requirements.txt`：唯一依赖文件，包含运行时、LLM 路由、PDF/BibTeX 处理和 pytest 开发测试依赖
- `pip install -e .`：确保 `researchos` 命令和当前源码目录绑定
- 默认开发环境不安装 CUDA/PyTorch/本地训练栈；真实实验依赖由外部执行器或项目自定义环境管理

### 2.3 环境变量

复制模板：

```bash
cp .env.example .env
```

至少建议配置：

```bash
SILICONFLOW_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...
S2_API_KEY=...
RESEARCHER_EMAIL=your@email
```

注意：

- `.env` 应主要放密钥和身份信息
- 运行参数应尽量放在 `config/*.yaml`
- 如果 `researchos` 和 `python -m researchos.cli` 表现不一致，优先检查是不是环境错配

---

## 3. 第一次拉起项目时的检查顺序

建议严格按这个顺序做，而不是一上来直接跑 T9。

### 3.1 配置校验

```bash
cd ResearchOS
python -m researchos.cli validate-config
```

预期：

- 输出 `ok: true`

如果这里都过不了，不要继续跑 task。

### 3.2 模型连通性检查

```bash
python -m researchos.cli selftest
```

建议重点看：

- SiliconFlow 是否可用
- OpenRouter / OpenAI 是否可用
- latency 是否异常高
- `pdfplumber` 等关键 PDF 解析依赖是否就绪（影响 T3 / T9）

### 3.3 创建一个最小 workspace

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/dev-smoke \
  --project-id dev-smoke \
  --topic "runtime smoke test"
```

### 3.4 跑 HELLO

```bash
python -m researchos.cli run-task HELLO --workspace ./workspace/dev-smoke
```

预期：

- `hello.txt` 生成
- 任务成功结束

`HELLO` 只验证 runtime 最小闭环，不会触发真实检索、阅读、综合或写作 agent。

### 3.5 跑真实 pipeline smoke

开发联调真实主链时，用 `run_smoke`。它会启动真实状态机，默认从 `T2` 开始，但把候选池、精读目标和摘要轻读量压小，并把状态机节点的模型层级临时降到 `medium`，便于快速观察 CLI 进度、工具调用说明、产物写入和恢复行为。

```bash
python -m researchos.cli run_smoke \
  --workspace ./workspace/dev-smoke-t2 \
  --from ./workspace/dev-smoke \
  --active-pool-max 20 \
  --deep-read-target 3 \
  --abstract-sweep 5 \
  --skip-startup-selftest
```

`run_smoke` 会写入目标 workspace 的 `literature/literature_params.json` 和 `literature/literature_params_confirmation.json`。已有参数文件默认保留；需要覆盖时加 `--force-smoke-params`。这个模式用于快速集成测试，不用于正式论文覆盖。

### 3.6 看 trace 和状态

```bash
python -m researchos.cli status --workspace ./workspace/dev-smoke
python -m researchos.cli trace <run_id> --workspace ./workspace/dev-smoke
```

如果 `HELLO` 都不稳定，不要继续往上排查 agent 逻辑。

---

## 4. 开发时最常用的 CLI 命令

### 4.1 `init-workspace`

初始化标准目录结构。

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/demo \
  --project-id demo \
  --topic "memory systems for llm agents"
```

典型 case：

- 新建一个最小调试工程 `./workspace/demo`
- 后续先跑 `HELLO`、再跑 `T1`

### 4.2 `run`

从当前状态推进完整 pipeline。

```bash
python -m researchos.cli run --workspace ./workspace/demo
```

典型 case：

- 你已经准备好 `project.yaml` 和必要 seeds
- 想让系统从当前状态一路往下推进，而不是手工一个个敲 task

### 4.3 `resume`

恢复一个被 gate 暂停、预算中断或人工中断后的 workspace。

```bash
python -m researchos.cli resume --workspace ./workspace/demo
```

典型 case：

- `T7.5` 已经生成 `evaluation/evaluation_decision.md`，现在要继续
- `T9` 编译前半段已经写出 bundle，想继续收敛
- provider 超时后，你想基于现有产物接着跑

### 4.4 `run-task`

单独跑某一个 task，用于本地调试。

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/demo
python -m researchos.cli run-task T7.5 --workspace ./workspace/demo
python -m researchos.cli run-task T9 --workspace ./workspace/demo
```

典型 case：

- `T3`：调 PDF 获取、全文覆盖、Reading Coverage 和续跑逻辑
- `T7.5`：调 PI evaluate 和 `next_task`
- `T9`：调投稿包编译、修复与验收

### 4.5 `validate`

校验某个 workspace 当前产物是否符合约定。

```bash
python -m researchos.cli validate --workspace ./workspace/demo --task T7-AUDIT
```

典型 case：

- 你怀疑 `T7-AUDIT` 结果已经写出来了，但状态机仍说失败
- 想单独验证 `results_summary.json`、`integrity_audit.json` 和 evidence index 是否满足规则

### 4.6 `status`

看当前状态机状态。

```bash
python -m researchos.cli status --workspace ./workspace/demo
```

典型 case：

- 想确认现在停在 `T6` 还是 `T7.5`
- 想看是否存在 `pending_gate`

### 4.7 `trace`

查看某一次运行的 JSONL trace。

```bash
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/demo
python -m researchos.cli trace T7_single_xxxxxxxx --workspace ./workspace/demo --raw
```

典型 case：

- 想确认 agent 到底调用了哪些 tool
- 想看 validator 为什么失败
- 想复盘某次 run 的逐步行为

### 4.8 `list-skills` / `run-skill`

技能运行时调试：

```bash
python -m researchos.cli list-skills --skills-root ./skills
python -m researchos.cli run-skill paper-compile "compile the paper in ./workspace/local-test2/drafts"
```

典型 case：

- 验证 `SKILL.md` 是否被发现
- 验证 paper 相关 skill 当前能否真实执行

---

## 5. Workspace 目录怎么读

开发时不要只盯着 stdout。真正重要的是 workspace。

### 5.1 研究产物目录

- `user_seeds/`
- `literature/`
- `ideation/`
- `novelty/`
- `external_executor/`
- `experiments/`
- `evaluation/`
- `drafts/`
- `submission/`

`pilot/` 只属于 legacy 内部实验兼容目录；新主链默认不创建，也不应作为真实实验入口。

### 5.2 Runtime 目录

默认在：

- `workspace/<name>/_runtime/`

重点看：

- `_runtime/logs/researchos.log`
- `_runtime/logs/researchos-debug.log`
- `_runtime/traces/*.jsonl`
- `_runtime/resume/*.json`

### 5.3 典型排障顺序

1. 先看 CLI 最后的错误摘要
2. 再看 `_runtime/logs/researchos.log` 的人类时间线
3. 如果是底层异常，再看 `_runtime/logs/researchos-debug.log`
4. 再看具体 `trace`
5. 最后看 workspace 里哪些 artifact 实际落了盘

---

## 6. 单任务调试的推荐方式

### 6.1 原则

调某个阶段时，尽量做到：

- 固定 workspace
- 固定输入产物
- 只改一类东西
- 每次改完立刻 `run-task`
- 必要时用 `validate`

### 6.2 `--from` 的用法

如果你要在另一个 workspace 上只复用上游产物并只跑一个 task：

```bash
python -m researchos.cli run-task T8-RESOURCE \
  --workspace ./workspace/scratch \
  --from ./workspace/local-test2
```

这会把当前 task 的前置 artifact 复制过来，再执行本 task。

如果你要从某个中间 task 开始跑完整后续 pipeline，用 `run --from --start-task`：

```bash
python -m researchos.cli run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5 \
  --start-task T2
```

`run --from` 不指定 `--start-task` 时默认从 `T2` 开始。它会按 `T2` 的输入契约复制 `project.yaml`、`user_seeds/` 中的 seed 文件与 `pdfs/`、以及 `literature/bridge_domain_plan.json`，初始化新 workspace 的 `state.yaml` 为 `current_task: T2`，然后继续完整状态机。

如果目标是开发快速联调，而不是正式重跑覆盖，使用等价的 smoke 入口：

```bash
python -m researchos.cli run_smoke \
  --workspace ./workspace/new-test5-smoke \
  --from ./workspace/new-test5 \
  --start-task T2
```

这会保留真实 pipeline 行为，但用 workspace-local smoke 参数减少 T2/T3 的候选和阅读量。

适合：

- 单独复现某阶段 bug
- 不污染主 workspace
- 保留 T1 和 seed，但重新做 T2 并继续 T3/T4/后续主链

### 6.3 推荐单任务调试顺序

- `HELLO`：验证 runtime 最小闭环
- `T2`：验证搜索、去重、verification 和队列生成
- `T3`：验证 PDF 获取、全文覆盖、Reading Coverage 和续跑
- `T5-HANDOFF/T5-EXECUTOR-GATE/T5-DRY-RUN/T5-EXTERNAL-WAIT/T7-INGEST/T7-AUDIT/T7-POST-NOVELTY/T7-CLAIMS`：验证外部实验协议、执行器选择、mock dry-run 或真实外部等待、摄取、诚信审计、实验后 novelty 复核和 result-to-claim
- legacy `LEGACY-T5-PILOT/LEGACY-T7-FULL`：仅在显式兼容调试时验证内部实验恢复、预算 gate、Docker
- `T7.5`：验证 PI 评估与 `next_task`
- `T8-RESOURCE`：验证资源索引、证据计划和图表计划
- `T8-SECTION-PLAN`：验证 `paper_state.json` 和每章局部大纲
- `T8-SEC-*`：逐个验证单章节草稿；每次只写一个 section
- `T8-DRAFT`：验证章节拼装和 manuscript audit
- `T8-REVIEW-1/2`：验证 reviewer 逻辑
- `T9`：验证 bundle 生成、编译、修复重试

---

## 7. 各任务成功后应该检查什么

这部分对开发者非常重要。不要只看“exit code = 0”。

| Task | 关键成功目标 | 最先检查的文件 |
| --- | --- | --- |
| `HELLO` | runtime 最小闭环 | `hello.txt` |
| `T1` | `project.yaml` 合法且信息完整 | `project.yaml`, `state.yaml` |
| `T2` | 保留候选集、已核验保留候选、deep-read 队列都落盘 | `papers_dedup.jsonl`, `papers_verified.jsonl`, `deep_read_queue.jsonl`, `access_audit.md` |
| `T3` | note/table/bib 同步增长、PDF 可用时全文覆盖、且支持续跑 | `paper_notes/`, `comparison_table.csv`, `related_work.bib`, `deep_read_queue_pending.jsonl` |
| `T3.5` | synthesis 分阶段产物和最终综合结构完整 | `literature/synthesis_workbench.json`, `literature/synthesis_outline.md`, `literature/synthesis_draft.md`, `literature/synthesis.md` |
| `T4` | hypotheses / exp_plan / Gate1 cards / selected idea brief / idea scorecard / gate decisions / risks 成对齐 | `ideation/hypotheses.md`, `ideation/exp_plan.yaml`, `ideation/_gate1_candidate_cards.md`, `ideation/selected_idea_brief.md`, `ideation/idea_scorecard.yaml`, `ideation/rejected_ideas.md`, `ideation/gate_decisions.json`, `ideation/idea_rationales.json`, `ideation/risks.md` |
| `T4.5` | novelty audit 生成；如有 High/Medium Overlap 则归档 collision cases | `ideation/novelty_audit.md`, `ideation/collision_cases.md`（条件产物） |
| `T5-HANDOFF` | 外部执行协议、AGENTS/CLAUDE、prompt、schema 和 allowed paths 完整 | `external_executor/handoff_pack.json`, `AGENTS.md`, `CLAUDE.md`, `executor_prompt.md`, `codex_prompt.md`, `claude_code_prompt.md`, `expected_outputs_schema.json`, `allowed_paths.txt` |
| `T5-EXECUTOR-GATE` | 执行器选择写入并 patch mode 占位 | `external_executor/executor_selection.json`, `AGENTS.md`, `CLAUDE.md` |
| `T5-EXTERNAL-WAIT` | 外部 result pack/status 就绪后写验收报告 | `external_executor/wait_acceptance_report.json` |
| `T5-DRY-RUN` | mock result/status/manifest/raw/config/log 协议跑通，且明确 mock_only | `external_executor/result_pack.json`, `executor_status.json`, `run_manifest.json`, `raw_results/`, `configs/`, `logs/` |
| `T7-INGEST` | 外部 result pack 被规范化为 ResearchOS 下游结果 | `experiments/results_summary.json`, `experiments/run_records.jsonl`, `experiments/evidence_index.json`, `experiments/ingest_report.json` |
| `T7-AUDIT` | provenance/hash/metric source/mock 标记和 required baseline coverage 被审计 | `experiments/integrity_audit.json`, `experiments/experiment_fairness_review.md` |
| `T7-POST-NOVELTY` | 实验后 novelty/collision 复核产物存在 | `novelty/post_experiment_novelty_check.json`, `novelty/post_experiment_collision_cases.md` |
| `T7-CLAIMS` | result-to-claim、must-not-claim 和写作证据包生成 | `experiments/experimental_claims.json`, `drafts/result_to_claim.json`, `drafts/experiment_evidence_pack.json`, `drafts/must_not_claim.md`, `experiments/iteration_log.md` |
| `T7.5` | evaluation decision 能给出 `next_task` | `evaluation/evaluation_decision.md` |
| `T8-RESOURCE` | 写作资源、章节、证据和图表计划生成 | `drafts/manuscript_resource_index.json`, `drafts/section_plan.json`, `drafts/evidence_plan.json`, `drafts/figure_table_plan.json` |
| `T8-WRITE` | 论文论证大纲生成 | `drafts/outline.md` |
| `T8-SECTION-PLAN` | 逐章节写作共享状态和局部大纲生成 | `drafts/paper_state.json`, `drafts/section_outlines/*.md` |
| `T8-SEC-*` | 单章节草稿完成；每个节点只写一个 section | `drafts/sections/<section>.tex` |
| `T8-DRAFT` | 章节拼装、全局融合、机械审计完成 | `drafts/paper.tex`, `drafts/manuscript_audit.md` |
| `T8-SELF-CHECK` | 作者自查完成 | `drafts/self_check.md` |
| `T8-REVIEW-1/2` | 审稿意见生成 | `drafts/review_rounds/round_1.md`, `round_2.md` |
| `T8-REVISE-1/2` | 主稿按审稿意见修订 | `drafts/paper.tex` |
| `T8-PAPER-CLAIM-AUDIT` | T9 前实验 claim、数字和证据边界审计通过 | `drafts/paper_claim_audit.md`, `drafts/paper_claim_audit.json`, `drafts/result_to_claim.json`, `drafts/experiment_evidence_pack.json` |
| `T9` | bundle 生成且编译成功 | `submission/bundle/main.tex`, `main.pdf`, `migration_report.md` |

### 7.1 T2 重点看什么

- `papers_raw` 和保留候选 `papers_dedup` 是否分离；raw 可以很大，`papers_dedup`/`papers_verified` 应控制在保留候选数上限内
- `literature/literature_params.json` 是否记录本轮 `active_pool_max/deep_read_target/abstract_sweep_target` 以及 `literature_quality`；英文稿应看到 `include_chinese_literature=false/auto` 时非 seed 中文论文被过滤到 backlog，中文/双语项目只允许显式权威中文来源或用户 seed 进入 active pool
- `papers_verified` 和 `papers_backlog` 是否生成；backlog 用来解释保留候选集之外的候选，没有静默丢弃
- `verification_failures` 是否合理
- `deep_read_queue` 是否确实优先 seed 和高可读性论文
- `search_log.md` 是否展示 Query / Bucket / Bridge / Tool / Calls / Results / Persisted 表，以及 Bridge Domain Query/Plan 覆盖表；重复 query 应合并到 Calls
- Active 切分前轻量补全是否合理：`search_log.md` 应出现 `Active 切分前轻量补全`，说明 input/candidate/skipped_by_cap、abstract_after、pdf_hint_after、reference_hint_after。它只做 bounded metadata/OA/abstract hint，不做 snowball 或学术判断。
- OpenAlex 标题兜底补全、OpenAlex DOI/OA 详情补全、Crossref DOI 详情补全、多源摘要回填的 `eligible/candidate/attempted/skipped_by_cap/filled/failed/remaining_missing_*` 是否合理；候选切分前轻量补全面向排序后的候选前缀，后续详情补全只面向保留候选集，不是全 raw 池无限补全
- OpenAlex/Crossref citation snowball 的 `reference_items_seen`、`non_doi_references_skipped`、`reference_openalex_ids_seen`、`title_references_resolved`、`skipped_by_refs_per_source_cap`、`skipped_by_max_candidates_cap`、`skipped_existing_snowball_records`、`raw_persisted/raw_merged` 是否合理；snowball 是 bounded one-hop，不是全量引用扩展，重复 finalize 不应继续增长 raw
- `deep_read_queue_meta.json` 中 `verified_disposition_coverage` 是否为 `1.0`，`queue_with_pdf_url_hints`、`queue_with_reference_hints`、`citation_hub_in_target`、`must_explore_bridge_diagnostics` 是否符合预期
- `deep_read_queue.jsonl` 是否保留短 provenance：`source_query/source_queries`、`source_tool/source_tools`、`search_buckets`、`openalex_id`、citation snowball 来源；摘要全文仍应通过 `lookup_paper_record` 从 verified/raw 合并读取
- 如果 search tool 返回很多论文但 raw/dedup/verified 很少，优先检查 raw merge、hidden cap、schema skipped records 和 `researchos.log` 的 `retained_raw_count`
- `papers_raw.jsonl` 是可恢复 metadata cache。finalize 后应看到 `search_log.md` 的 `T2 raw 元数据缓存回写`，并确认 `raw_cache_records_merged/appended` 合理；否则 resume/re-finalize 可能重新依赖网络回填。
- 如果包含中文文献，抽查记录里的 `paper_language`、`chinese_authority_status`、`authority_review_needed`、`literature_quality_policy.reason` 和 `citation_allowed`。普通普刊/来源不明中文论文可以作为候选或复核线索保留，但不应在缺少人工/真实目录复核时进入英文稿 citation pool。

### 7.2 T3 重点看什么

- 是否只复用结构合格的已有 `paper_notes`
- 是否正确生成 `deep_read_queue_pending.jsonl`
- CLI 是否持续显示 `T3 deep read progress: x/y`，abstract sweep 是否显示候选处理进度；这两者是确定性输出，不依赖 LLM 自己汇报
- PDF 可用的 note 是否包含 `## 12. Reading Coverage`
- `[FULL-TEXT]` note 的 `Pages read` 是否类似 `1-N / N` 或 `1-4, 5-8, 9-N / N`，且 `Truncation` 明确最终无截断；`all pages` 这类无总页数/范围的描述不能通过
- `notes_manifest.json` 是否抽取了 `citation_quality_score`、`citation_use` 和 `citation_quality_rationale`；后续 T3.5/T4/T8 应优先使用 `score>=0.55` 且 `core_evidence/supporting_context` 的论文，低分或 `do_not_cite` 只能做背景/补资源线索
- seed PDF 应表现为 `has_seed_pdf=true`、`seed_pdf_path=...`、`access_level_hint=FULL_TEXT_LOCAL`、`evidence_level=FULL_TEXT`。不要新增 `seed_pdf` evidence enum；旧 `comparison_table.csv` 如果显示 `ABSTRACT_ONLY`，T3 validator 会用 note/access audit 兜底修复
- `comparison_table.csv` 是否持续可追加
- `related_work.bib` 是否没有粘连/损坏

### 7.3 外部实验链重点看什么

- `T5-HANDOFF` 是否写出 handoff pack、AGENTS/CLAUDE、expected schema、allowed paths 和 Codex/Claude/manual prompt
- `T5-EXECUTOR-GATE` 是否写出 executor_selection，并且 AGENTS/CLAUDE/prompt 不再包含 `UNSET`
- `T5-EXTERNAL-WAIT` 缺 result pack 时是否 PAUSED，写回后 resume 是否生成 wait_acceptance_report
- `T5-DRY-RUN` 是否写出 `mock_only=true` 的 result pack、status、run manifest、raw/config/log
- 真实 executor 路径是否绑定 `executor_selection.json`；如果选择 `codex_cli` / `claude_code_window` / `manual`，旧的 `mock_only/dry_run` result pack 必须被拒绝
- `T7-INGEST` 是否把 result pack 规范化成 `results_summary.json`、`run_records.jsonl` 和 `evidence_index.json`
- `T7-AUDIT` 是否检查 artifact 存在、sha256、metric source、run manifest 和 required baseline coverage
- `T7-POST-NOVELTY` 是否写出 post_experiment novelty/collision 复核
- `T7-CLAIMS` 是否写出 `drafts/result_to_claim.json`、`drafts/experiment_evidence_pack.json`、`drafts/must_not_claim.md` 和 `drafts/claim_support_matrix.csv`
- 如果预算触顶，是否先进入 gate，而不是直接硬停

legacy `T5/T7` 显式调试时再检查已有代码目录、Docker digest 和内部实验 resume state。

### 7.4 T7.5 重点看什么

- `evaluation_decision.md` 是否含 `Situation`、`Options`、`next_task`
- `next_task` 能否被状态机解析

### 7.5 T9 重点看什么

- 是否真正尝试编译
- 编译失败后是否修复并重试
- 最终是否产出 `main.pdf`
- `migration_report.md` 是否明确记录编译结果和修复过程
- `submission/bundle/bundle_manifest.json` 是否证明 bundle 仍对应当前 `drafts/paper.tex` 和 `literature/related_work.bib`
- `prepare_submission_bundle` 是否已重写 `\bibliography{...}` / `\addbibresource{...}` 到 bundle-local references，并把 read policy 允许且后缀为 `.pdf/.png/.jpg/.jpeg/.svg` 的 `\includegraphics{drafts/...}`、`experiments/...`、`evaluation/...`、`figures/...` 路径复制并重写到 `submission/bundle/figures/`；目标文件名必须带内容 hash，避免静默覆盖
- `compile_report.json.attempts` / `attempt_count` 是否记录真实编译历史；same-hash source failure 是否要求先改 TeX/依赖；成功缓存是否同时匹配 main.tex、依赖 fingerprint、PDF/log hash、mtime 和 size
- resume 到 T9 时，如果已有合法 bundle，是否通过 `t9_submission_prefinalize` 在环境检查和 LLM 前直接完成

---

## 8. 本地调试的几种典型路径

### 8.1 调 prompt

推荐步骤：

1. 找到对应 prompt
2. 改 prompt
3. 用固定 workspace `run-task`
4. 看 trace 中 tool 调用和最终 validator

常见文件：

- `researchos/prompts/*.j2`

### 8.2 调 validator

推荐步骤：

1. 找 agent 的 `validate_outputs`
2. 用现有 workspace 直接复现
3. 补单测
4. 再跑 `run-task`

常见文件：

- `researchos/agents/*.py`
- `tests/unit/test_*`

### 8.3 调工具

推荐步骤：

1. 先单测工具
2. 再让 agent 间接调用
3. 观察 tool trace 是否符合预期

常见文件：

- `researchos/tools/*.py`
- `researchos/tools/builtin.py`

### 8.4 调状态机

推荐步骤：

1. 改 `config/system_config/state_machine.yaml`
2. 跑 `validate-config`
3. 必要时单测 `StateMachine`
4. 再用 `run` / `resume` 验证完整链

常见文件：

- `config/system_config/state_machine.yaml`
- `researchos/orchestration/state_machine.py`
- `researchos/orchestration/task_io_contract.py`

#### T4 Gate1 中间态

T4 有一个正式的中间完成态：`completion_mode=t4_gate1_ready`。当 `IdeationAgent` 已经写好 `_pass1_forward_candidates.json`、`_pass2_grounding_review.json`、`_candidate_directions.json`、`_gate1_candidate_cards.md` 和 `_gate1_selection_brief.md`，但还没有用户选择时，runner 会跳过完整 T4 artifact 校验，状态机转入 `T4-GATE1` immediate gate。旧 workspace 若已有结构化候选池但缺卡片，runtime 会从 `_candidate_directions.json` 自动补写 `_gate1_candidate_cards.md`，避免为了展示升级而重跑长 T4。

调试时不要把这个状态当作 T4 最终完成。`T4-GATE1` 默认展示 Markdown 候选卡片的路径、字符数和短摘要，完整内容在 `ideation/_gate1_candidate_cards.md`；JSON 只保留为机器可读附录路径，不直接刷给用户。CLI 可直接输入 `D1`、`D1+D3`、`merge D1+D3`、`new: ...` 或 `reanalyze: ...`。gate 会写 `ideation/_gate1_user_selection.json` 和一个即时 `selected_idea_brief.md` stub，selection 会绑定候选池 fingerprint；如果候选池在等待期间变化，resume 会重新展示 Gate1。随后回到 T4 生成最终 `hypotheses.md`、`exp_plan.yaml`、`idea_scorecard.yaml`、`selected_idea_brief.md` 和 `gate_decisions.json`。T4 的 resume prefinalize 只有在这些最终产物晚于 `_gate1_user_selection.json` 时才会复用，避免形式上经过 Gate1、实际上复用旧假设；最终 `selected_idea_brief.md` 不能保留 “待 T4 后半段补全” 这类 stub 文案。T4 的迭代死锁 hash 使用稳定 SHA256，并把 Gate1 selection fingerprint 纳入 post-Gate1 参数，避免旧的 pre-Gate1 失败记录阻塞用户选择后的 T4 后半段。

---

## 9. 技能（skills）开发与调试

### 9.1 当前支持什么

ResearchOS 当前支持：

- `SKILL.md` frontmatter 发现
- `list-skills`
- `run-skill`
- Claude 风格工具别名到 runtime 工具的映射

当前仓库内的 paper 类 skill：

- `skills/paper-compile`
- `skills/paper-write`
- `skills/deepxiv`

### 9.2 如何验证 skill runtime

```bash
python -m researchos.cli list-skills --skills-root ./skills
```

再用某个具体 skill：

```bash
python -m researchos.cli run-skill deepxiv "summarize recent papers about memory for llm agents"
```

### 9.3 skill 调试时常见问题

- `No skills found`
  原因通常是 `skills-root` 错了，或者没有 `SKILL.md`

- 工具别名无法解析
  看 `researchos/skills/tool_aliases.py`

- skill 声明了 runtime 没实现的高级工具
  例如 `Agent`、`WebSearch`、某些 MCP tool

这时 skill 可能会降级，而不是完全不可用。

---

## 10. MCP 开发与调试

### 10.1 配置入口

- `config/mcp.example.yaml`
- `config/mcp.yaml`

### 10.2 调试顺序

1. 确认 server 配置能被加载
2. 跑启动自检
3. 看 startup summary 里 `mcp_servers` / `mcp_tools`
4. 再验证具体 agent 能否使用 MCP tool

### 10.3 常见问题

- server 启动了但没注册 tool
- skill 里声明了 MCP 工具，但当前 runtime 没这个 tool
- 容器环境和宿主机环境的 MCP 配置不一致

---

## 11. Docker 开发调试

当问题与以下内容相关时，优先用 Docker 复现：

- 宿主机依赖漂移
- 部署脚本或 bind mount 行为
- Native/Docker workspace 兼容性

基本命令：

```bash
cd ResearchOS
cp .env.example .env
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

重点排查：

- Docker image 是否是最新
- `/app/workspace` 是否正确挂载
- `.env` 是否透传
- 容器内是否真在跑当前仓库代码

---

## 12. 测试命令建议

### 12.1 跑单个测试文件

```bash
cd ResearchOS
python -m pytest tests/unit/test_writer_reviewer_submission.py -q
```

### 12.2 跑某一类测试

```bash
python -m pytest tests/unit -q
```

### 12.3 改完配置或状态机后建议至少做的事

```bash
python -m researchos.cli validate-config
python -m pytest tests/unit/test_state_machine_runtime_features.py -q
```

### 12.4 改完 skill runtime 后建议做的事

```bash
python -m pytest tests/unit/test_list_skills.py tests/unit/test_skills_runtime.py tests/unit/test_skill_tool_discovery.py -q
python -m researchos.cli list-skills --skills-root ./skills
```

---

## 13. 常见问题与处理建议

### 13.1 `researchos` 命令和源码行为不一致

优先用：

```bash
PYTHONPATH=. python -m researchos.cli ...
```

然后重新：

```bash
pip install -e .
```

### 13.2 provider 一直超时

检查：

- `config/model_routing.yaml` 是否有 fallback
- `config/user_settings.yaml: runtime.retry_policy.llm_retries` 是否过大
- 是否误用了只含一个候选的 profile

### 13.3 任务中断后从头跑

检查：

- 是否用的是同一个 workspace
- 对应 task 是否已接入 resume 逻辑
- `_runtime/resume/*.json` 是否生成
- 关键 artifact 是否真的落盘

### 13.4 `run-task` 能过，`run/resume` 不对

常见原因：

- 状态机下一跳有问题
- gate 配置与状态机不一致
- `run-task` 不推进 FSM，但 `run/resume` 会推进

### 13.5 tool 看起来“存在”，但 agent 不会用

先检查：

- `agent_params.yaml` 的 `agents.<agent>.tools.tool_names` 是否把工具暴露给了该 agent
- tool 是否注册在 `builtin.py`
- prompt 是否明确告诉 agent 该怎么用

---

## 14. 推荐阅读顺序

对于新开发者，推荐按这个顺序读：

1. [README.zh-CN.md](../README.zh-CN.md) 或 [README.md](../README.md)
2. [docs/agent_pipeline.md](./agent_pipeline.md)
3. [docs/runtime.md](./runtime.md)
4. [docs/config.md](./config.md)
5. [docs/docker.md](./docker.md)
6. [docs/logging.md](./logging.md)

如果你在本地维护当前仓库，建议再结合：

- [../tmp/researchos-local-debug-guide.md](../tmp/researchos-local-debug-guide.md)

一起使用。
