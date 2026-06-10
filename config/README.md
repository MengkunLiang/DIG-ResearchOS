# ResearchOS 配置系统

本目录按“单一参数所有权”组织。普通模型/预算/超时只改 `user_settings.yaml` 和 `.env`；文献流程的机械阈值只改 `agent_params.yaml` 中对应 `behavior` 块。不要把同一个参数在多张表里重复配置。

## 文件职责

| 文件 | 职责 | 日常是否修改 |
| --- | --- | --- |
| `user_settings.yaml` | `llm.*` 模型参数、`budget.*` 预算参数、`runtime.*` timeout/retry/budget escalation | 是，日常参数唯一入口 |
| `runtime.yaml` | workspace、日志、UI、human interface、web_fetch allowlist、Docker 镜像与执行环境基础设置 | 偶尔 |
| `model_routing.yaml` | endpoint、profile、primary/fallback 候选链、上下文截断策略 | 只在新增 provider/model/fallback 时改 |
| `agent_params.yaml` | agent capability registry：工具、读写权限、prompt/schema、behavior、mode 说明；T2/T3 机械阈值的唯一配置处 | 开发 agent/tool 或调整文献流程阈值时改 |
| `system_config/state_machine.yaml` | 状态机拓扑、输入输出、gate、分支、节点 extra | 系统契约；普通用户不要改 |
| `system_config/gates.yaml` | human gate 展示、选项和附加上下文 | 系统契约；普通用户不要改 |
| `system_config/cdr_schema.yaml` | CDR idea/writing schema，供 Reader、Ideation、Writer prompt 使用 | 系统契约；普通用户不要改 |
| `system_config/venue_style_map.yaml` | T8 style gate 根据 target venue 给出 IS/CCF-A 默认建议 | 系统契约；普通用户不要改 |
| `mcp.yaml` / `mcp.example.yaml` | MCP server 描述 | 需要 MCP 时改 |

## 快速开始

1. 配置密钥：

```bash
cp ../.env.example ../.env
nano ../.env
```

常用变量包括 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`SILICONFLOW_API_KEY`、`SILICONFLOW_BASE_URL`、`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ANTHROPIC_API_KEY`、`S2_API_KEY`、`ELSEVIER_API_KEY`、`RESEARCHER_EMAIL`。

2. 日常切模型、预算或超时：

```bash
nano config/user_settings.yaml
```

3. 验证配置：

```bash
python -m researchos.cli validate-config
```

## 参数所有权

| 参数类型 | 唯一日常入口 | 说明 |
| --- | --- | --- |
| 默认 profile | `user_settings.yaml: llm.default_profile` | 例如 `deepseek`、`siliconflow_only`、`mixed` |
| Agent 模型档位 | `user_settings.yaml: llm.agents.<agent>.tier` | `heavy` / `medium` / `light` |
| Agent 直接模型绑定 | `user_settings.yaml: llm.agents.<agent>.model + endpoint` | 会绕过 profile fallback，只在确实需要固定模型时用 |
| Agent temperature/max_context | `user_settings.yaml: llm.agents.<agent>.*` | mode 内可单独覆盖 |
| Agent step/token/wall budget | `user_settings.yaml: budget.agents.<agent>.*` | 支持 `unlimited_budget: true` |
| LLM timeout/retry/cooldown | `user_settings.yaml: runtime.timeouts` 和 `runtime.retry_policy` | 包含 provider 连续超时后的 cooldown |
| budget escalation | `user_settings.yaml: runtime.budget_escalation` | 触顶后是否 ask_human 扩限 |
| CLI quiet/verbose 默认值 | `runtime.yaml: ui.quiet/ui.verbose` | 只影响终端展示，不影响 `researchos.log` / trace |
| T2/T3 文献流程阈值 | `agent_params.yaml: agents.scout.behavior.t2_finalize` / `agents.reader.modes.read.behavior` | 保留候选数、去重、补资源并发、snowball、deep-read queue、abstract sweep |
| 工具列表和读写权限 | `agent_params.yaml` | 能力声明，不是预算表 |
| endpoint/API base/API key env | `model_routing.yaml` + `.env` | 路由候选定义与密钥分离 |
| 状态机输入输出和分支 | `system_config/state_machine.yaml` | 系统契约；默认不要在这里写 LLM/budget 参数 |

## `user_settings.yaml`

这是普通用户最该看的文件。默认配置采用分表写法，避免同一个 agent 块里混入模型和预算：

```yaml
llm:
  default_profile: deepseek
  defaults:
    profile: deepseek
    tier: medium
  agents:
    scout:
      tier: medium
      temperature: 0.5
      max_context: 1280000
    writer:
      tier: heavy
    survey_writer:
      tier: heavy
    reviewer:
      tier: heavy
    submission:
      tier: heavy

budget:
  defaults:
    unlimited_budget: true
  agents:
    scout:
      max_steps: 300
      max_tokens_total: 50000000
      max_wall_seconds: 36000
    writer:
      max_steps: 1500
      modes:
        section_draft:
          max_steps: 80
          max_tokens_total: 50000000

runtime:
  timeouts:
    llm_call: 90
    max_tool_call: 180
    docker_operation: 7200
    latex_compile: 1800
  retry_policy:
    llm_retries: 10
    llm_retry_delay: 1
    llm_timeout_cooldown_seconds: 60
    llm_timeout_pause_after_cooldowns: 0
  budget_escalation:
    enabled: true
    max_extensions_per_run: null
```

兼容层仍能读取旧的 `agents.<agent>` 混合简写，但 checked-in 默认配置不再使用。日常使用请按 `llm.*` 和 `budget.*` 分开写；`max_tokens` 会被归一化为 `max_tokens_total`，`tags: [unlimited_budget]` 与 `unlimited_budget: true` 等价。

## `model_routing.yaml`

这里只定义可选的模型路由候选：

```yaml
endpoints:
  deepseek:
    provider: openai
    api_key_env: DEEPSEEK_API_KEY
    api_base_env: DEEPSEEK_BASE_URL

profiles:
  deepseek:
    heavy:
      primary:
        model: deepseek-v4-pro
        endpoint: deepseek
        max_context: 128000
      fallback:
        - model: deepseek-v4-flash
          endpoint: deepseek
          max_context: 128000
```

切全局默认不要改这里，改 `user_settings.yaml: llm.default_profile`。新增 provider、fallback 或上下文窗口候选时才改这里。

## `agent_params.yaml`

这是能力注册表，主要放：

- `tools.tool_names`
- `tools.allowed_read_prefixes`
- `tools.allowed_write_prefixes`
- `prompt.prompt_template`
- `prompt.structured_outputs`
- `prompt.expected_outputs`
- `behavior.*`，例如 `scout.behavior.t2_finalize`、`reader.modes.read.behavior.abstract_sweep`、`submission.behavior.max_compile_attempts`

`submission.behavior.max_compile_attempts` 控制 T9 对当前 TeX + dependency fingerprint 的 LaTeX 编译尝试上限。它不是普通 LLM budget；日常预算仍在 `user_settings.yaml: budget.*`。
- `modes.<mode>.description/prompt/behavior/tools`

兼容层仍能读取旧的 `llm` / `budget` 字段，但 checked-in 默认配置不再把它们放这里。不要把日常模型和预算参数写回 `agent_params.yaml`，否则又会出现多表参数冲突。

### T2/T3 文献流程参数表

这些参数只在一个位置配置，runtime、validator、resume 和工具默认值都从这里读取；不要再在 `state_machine.yaml` 或 prompt expected outputs 里重复写同一个阈值。
代码中函数签名保留的数值只作配置缺失/损坏时的 fallback；正常 CLI 进程会从本表读取。修改 YAML 后重新运行命令即可生效。

| 参数 | 默认值 | 位置 | 作用 |
| --- | --- | --- | --- |
| `finish_finalize_min_raw` | `30` | `agents.scout.behavior.t2_finalize` | Scout 调用 `finish_task` 后，runtime 至少看到多少 raw 才执行确定性收尾；下限为 10 |
| `active_pool_max` | `120` | `agents.scout.behavior.t2_finalize` | 保留候选数上限；`papers_dedup.jsonl` / `papers_verified.jsonl` 只保存本轮进入后续阅读处置的候选，超额进入 `papers_backlog.jsonl` |
| `screened_active_pool_cap` | `60` | `agents.scout.behavior.t2_finalize` | `semantic_screen.can_enter_deep_read=true` 主线候选在保留候选集中的优先保留上限 |
| `bridge_active_pool_cap_per_bridge` | `15` | `agents.scout.behavior.t2_finalize` | 兼容旧字段；未设置 must cap 时作为 `must_explore` bridge 的默认保留上限 |
| `must_bridge_active_pool_cap_per_bridge` | `15` | `agents.scout.behavior.t2_finalize` | 每个 `priority=must_explore` bridge 进入保留候选集的召回保留上限 |
| `should_bridge_active_pool_cap_per_bridge` | `5` | `agents.scout.behavior.t2_finalize` | 每个 `priority=should_explore` bridge 进入保留候选集的召回保留上限；低于 must，避免桥接召回挤掉主线 |
| `snowball_active_pool_cap` | `12` | `agents.scout.behavior.t2_finalize` | citation snowball 候选进入保留候选集的优先保留上限 |
| `dedup_title_threshold` | `0.95` | `agents.scout.behavior.t2_finalize` | raw、snowball 和最终保留候选集去重时的标题相似度阈值 |
| `access_audit_top_n` | `50` | `agents.scout.behavior.t2_finalize` | `access_audit.md` 展示的 top 论文数量 |
| `pre_active_light_backfill_max` | `220` | `agents.scout.behavior.t2_finalize` | 保留候选/backlog 切分前先对全 dedup 池前 N 篇做轻量标题/DOI/OA/摘要补全；`0` 关闭，`-1` 不限量 |
| `metadata_backfill_max_concurrency` | `6` | `agents.scout.behavior.t2_finalize` | OpenAlex/Crossref/title metadata 回填并发上限 |
| `abstract_backfill_title_match_threshold` | `0.88` | `agents.scout.behavior.t2_finalize` | 多源摘要回填的标题匹配阈值 |
| `abstract_backfill_max_concurrency` | `6` | `agents.scout.behavior.t2_finalize` | 多源摘要回填并发上限 |
| `snowball_max_sources` | `12` | `agents.scout.behavior.t2_finalize` | citation snowball 最多从多少个高置信来源扩展 |
| `snowball_refs_per_source` | `8` | `agents.scout.behavior.t2_finalize` | 每个 snowball 来源最多解析多少条引用/相关工作 |
| `snowball_max_candidates` | `40` | `agents.scout.behavior.t2_finalize` | OpenAlex 与 Crossref 共享的全局 snowball 解析尝试上限；OpenAlex 消耗后只把剩余额度给 Crossref |
| `snowball_max_concurrency` | `6` | `agents.scout.behavior.t2_finalize` | snowball metadata 解析并发上限 |
| `snowball_title_match_threshold` | `0.90` | `agents.scout.behavior.t2_finalize` | Crossref 引用标题转 OpenAlex 候选时的最低标题相似度 |
| `progress.enabled` | `true` | `agents.scout.behavior.progress` | 是否写 `literature/temp/scout_progress.md` |
| `progress.update_on_tool_results` | `true` | `agents.scout.behavior.progress` | 搜索工具自动落盘 raw 后是否同步写 progress |
| `progress.update_on_finalize` | `true` | `agents.scout.behavior.progress` | T2 deterministic finalize 开始、保留候选/backlog 切分、完成/失败时是否同步写 progress |
| `literature_quality.manuscript_language` | `auto` | `agents.scout.behavior.literature_quality` 或 workspace `literature/literature_params.json` | 写作语言策略；英文稿默认不主动检索中文论文 |
| `literature_quality.include_chinese_literature` | `auto` | 同上 | 是否允许中文论文进入候选池；`false` 表示英文稿只保留用户 seed 作为背景/复核线索 |
| `literature_quality.chinese_literature_policy` | `authoritative_or_seed` | 同上 | 中文论文准入底线；只允许 WJCI、SCI/SSCI、EI、北大核心、CSSCI、CSCD、AMI 顶级/权威/核心等显式权威来源或用户 seed |
| `deep_read_min` | `35` | `agents.reader.modes.read.behavior` 或 workspace `literature/literature_params.json` | 最低精读线；预算/资源异常时的最低可接受结构合格 deep-read note 数 |
| `deep_read_target` | `35` | `agents.reader.modes.read.behavior` 或 workspace `literature/literature_params.json` | 精读目标；`require_deep_read_target=true` 时 T3 必须读满目标 |
| `deep_read_max` | `45` | `agents.reader.modes.read.behavior` | 精读目标硬上限，保护位也在该上限内计数 |
| `probe_pool` | `45` | `agents.reader.modes.read.behavior` | T3 优先 probe 的候选池大小 |
| `mainline_screened_cap` | `90` | `agents.reader.modes.read.behavior` | 主线 shallow/screened backlog 在 `deep_read_queue.jsonl` 中保留的上限 |
| `bridge_deep_floor` | `3` | `agents.reader.modes.read.behavior` | 每个 must_explore bridge 通过 screen 后的精读保底 |
| `bridge_screened_cap` | `7` | `agents.reader.modes.read.behavior` | 每个 bridge 的 shallow/screened backlog 保留上限 |
| `bridge_pool_cap` | `15` | `agents.reader.modes.read.behavior` | 每个 bridge 在 deep-read queue 中保留的候选总上限 |
| `citation_hub_slots` | `3` | `agents.reader.modes.read.behavior` | citation graph 枢纽节点保护槽，仍需 Reader 复核 |

完整 `run` 会在 T2 前进入 `T2-PARAM-GATE`。Gate 会先展示当前 workspace 检测到的任务类型、推荐档位、以及每个选项实际写入的 `active_pool_max`、`deep_read_min/target/max`、`require_deep_read_target`、`abstract_sweep_target`、`manuscript_language` 和 `include_chinese_literature`；直接回车采用当前推荐项。确认后写入 `literature/literature_params.json`，该文件优先于全局 yaml。你也可以直接输入一句自然语言修改，例如“英文稿，不要中文论文，候选数300”。当前 Reader 的 `modes.read.behavior.abstract_sweep` 默认用于覆盖 T3 deep read 后尚未读完的保留候选；`papers_backlog.jsonl` 是覆盖账本和人工/显式回捞池，综述 gate 可允许从中回捞有摘要/PDF 的候选补足可读覆盖：

- `expected_notes_ratio: 1.0` 是无 queue 旧 workspace 的 fallback 比例，表示输入池默认必须 100% 有笔记；新主流程仍优先用 `deep_read_queue` 区分精读目标和 shallow/backlog。
- `lite_paper_num: 120` 表示研究论文默认最多处理 120 篇 abstract sweep 候选；综述 gate 可写 `all_readable`，表示尽量读完保留候选中所有可读摘要。
- `sources: [papers_verified, papers_dedup]` 表示默认只覆盖保留候选；如 gate 写入 `metadata_replacement_policy=replace_metadata_only_with_readable_backlog_when_available`，可从 backlog 回捞有摘要/PDF 的候选补足可读覆盖。
- `min_relevance: 0.0` 表示不靠 metadata priority hint 丢弃候选。
- `priority_weights` 默认 `relevance/resource/year = 0.70/0.20/0.10`，用于在候选预算内排序：`relevance_score` 仍是检索/元数据优先级提示，资源可获得性和发表年限只影响“先读谁/先补谁”。
- `include_metadata_only: true` 表示缺摘要但有标题的论文会进入 `literature/metadata_triage.md` 批量 triage；正常完成路径调用 Reader LLM 做 metadata-only 审阅，中断/LLM 失败时用确定性 fallback。它不会生成逐篇 note、BibTeX 或 comparison evidence。
- `metadata_triage_report: literature/metadata_triage.md` 是 metadata-only triage report 路径；该报告只能作为补资源/升级阅读线索，不能进入 claim evidence。
- `exclude_semantic_excluded: true` 表示 Scout 已明确判为 `shared_keyword_only/unrelated` 或禁止 deep-read 的论文默认不再进入 abstract note、BibTeX 和 comparison table，避免污染 T3.5/T8 语料；如需做排除线索复核，可在项目配置中显式设为 `false`。

中文文献策略是质量底线，不是召回建议。英文稿默认不设计中文 query，也不让非 seed 中文论文进入 active pool；中文/双语或显式允许中文文献时，T2 只接受显式权威来源线索（WJCI、SCI/SSCI、EI、北大核心、CSSCI、CSCD、AMI 顶级/权威/核心等）或用户 seed。用户 seed 中文论文可以保留阅读，但若不满足写作语言或权威来源要求，会被打上 `citation_allowed=false` 或 `user_seed_chinese_literature_needs_authority_review`，后续 Writer 不应把它作为英文稿核心证据。

这组参数只控制阅读/补资源优先级；论文是否能作为学术证据仍由 Reader/Writer 的 LLM 判断、evidence level 和 claim audit 控制。

T3 deep read note 还会进入引用质量账本。Reader prompt 要求每篇 deep note 写 `Citation Quality Score`、`Citation Use` 和 `Citation Quality Rationale`；`notes_manifest.json` 会抽取这些字段，旧 note 缺失时按 evidence level 给保守 fallback。T3.5/T4/T8 只应把 `citation_quality_score >= 0.55` 且 `citation_use` 为 `core_evidence/supporting_context` 的论文作为核心论证材料，低分、`do_not_cite`、`citation_allowed=false` 或中文英文稿不匹配的材料只能作为背景、风险或补资源线索。

#### `behavior_profiles.survey`

默认 T2/T3 参数等同 `research_article`。runtime 会从 workspace 自动选择 profile：

- `project.yaml.metadata.manuscript_type/project_type/article_type/paper_type`
- `user_seeds/seed_outline_profile.json` 的 `manuscript_type/project_type`

命中 `survey` / `综述` / `review` / `taxonomy-driven` 时，启用
`agents.scout.behavior.behavior_profiles.survey` 与
`agents.reader.modes.read.behavior.behavior_profiles.survey`。当前 survey profile 主要放宽：

| 项 | research_article | survey |
| --- | --- | --- |
| T2 保留候选数 | `active_pool_max=120` | `180` |
| T2 screened cap | `screened_active_pool_cap=60` | `90` |
| T2 候选切分前资源补全 | `pre_active_light_backfill_max=220` | `360` |
| T2 citation snowball | `snowball_max_sources=12, max_candidates=40` | `18, 70` |
| T3 deep-read | `35/35/45` | `45/55/65` |
| T3 abstract sweep | `lite_paper_num=120` | `180`，并可扫描 `papers_backlog` |

用户 Markdown 综述提纲经 `normalize_seed_outline` 生成 `seed_outline_profile.json` 后会自动触发 survey profile。提纲里的 `representative_literature_directions` 只是 query/taxonomy prior，不是已验证 citation，不会写入 `seed_papers.jsonl`。

### T2 metadata / citation backfill 参数归属

T2 的 OpenAlex DOI/OA 详情补全、Crossref DOI 详情补全、多源摘要回填、raw cache merge 是 deterministic finalize 的机械步骤。runtime 会先对排序后的 dedup 候选执行一轮 bounded light backfill，再切保留候选/backlog，随后只对保留候选做更完整的详情补全和 citation snowball。保留候选数、bridge priority、snowball 进入保留候选集的配额和 progress 开关在上表的 `agents.scout.behavior.t2_finalize/progress`。Scout 的模型只负责 query 设计和语义筛选，不负责手写这些阈值。

质量排障优先看 `literature/temp/scout_progress.md`、`literature/search_log.md` 和 `_runtime/logs/researchos.log`，尤其是保留候选/backlog 规模、`eligible/candidate/attempted/skipped_by_cap/failed/remaining_missing_*`、`raw_persisted/raw_merged`、`skipped_existing_snowball_records` 和 `T2 raw 元数据缓存回写`。

### 日志与控制台

- `runtime.yaml: ui.quiet=true`：控制台只显示状态跳转、暂停、错误和最终结果
- `runtime.yaml: ui.verbose=true`：控制台额外显示 Agent 文本输出和 step token 摘要
- `workspace/<name>/_runtime/logs/researchos.log`：统一人类时间线，由 runtime 写入，不受 quiet 影响
- `workspace/<name>/_runtime/logs/researchos-debug.log`：底层 Python/structlog 调试日志
- `workspace/<name>/_runtime/traces/*.jsonl`：机器级完整 trace

LiteLLM INFO 默认被 runtime 压到 WARNING；正常情况下不会刷屏，也不会进入 `researchos.log`。

## `state_machine.yaml`

真实文件在 `config/system_config/state_machine.yaml`。这里定义 workflow，不是参数表。默认主链不应写 `llm` / `budget`。只有临时调试或确实需要固定某个 task 时，才使用 task 级强覆盖：

```yaml
states:
  HELLO:
    agent: hello
    llm:
      profile: hello_fast
```

如果 `user_settings.yaml` 改了 profile 但运行不生效，优先检查 `validate-config` 输出里的 `state_machine_llm_overrides`。

## 写作链 heavy

展示质量相关的写作链默认走 heavy：

- `llm.agents.writer.tier: heavy`：T8 主论文写作
- `llm.agents.survey_writer.tier: heavy`：T3.6 综述支线写作
- `llm.agents.reviewer.tier: heavy`：审稿式检查和修订建议
- `llm.agents.submission.tier: heavy`：T9 投稿包、LaTeX 修复和最终格式检查

T3.6 和 T8 是两个独立 agent、独立 workspace 目录和独立状态机分支。T3.6 产物写在 `drafts/survey/` 并可导出 `ideation/survey_insights.json` 给 T4；T8 主论文写在 `drafts/sections/`、`drafts/paper.tex` 和 `submission/` 链路中。改 `survey_writer` 不会自动改变 `writer`，反之亦然。

## 验证与排查

```bash
python -m researchos.cli validate-config
python -m researchos.cli selftest --profile deepseek
```

常见问题：

- 模型没有切过去：检查 `state_machine_llm_overrides` 是否有 task 级强覆盖。
- DeepSeek OpenAI-compatible 调用失败：确认 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 profile 中的模型名匹配。
- 预算仍然触顶：检查 `user_settings.yaml: budget.defaults` 或 `budget.agents.<agent>` 是否设置 `unlimited_budget: true`，以及 `runtime.budget_escalation.enabled` 是否开启。
- T9/Docker 超时：检查 `runtime.timeouts.docker_operation` 与 `runtime.timeouts.latex_compile`，普通 `max_tool_call` 不应承担长实验或 TeX 编译上限。

更多细节见 [docs/config.md](../docs/config.md)。
