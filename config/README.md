# ResearchOS 配置系统

本目录按“单一参数所有权”组织。普通模型/预算/超时只改 `user_settings.yaml` 和 `.env`；文献流程的机械阈值只改 `agent_params.yaml` 中对应 `behavior` 块。不要把同一个参数在多张表里重复配置。

## 文件职责

| 文件 | 职责 | 日常是否修改 |
| --- | --- | --- |
| `user_settings.yaml` | `llm.*` 模型参数、`budget.*` 预算参数、`runtime.*` timeout/retry/budget escalation | 是，日常参数唯一入口 |
| `runtime.yaml` | workspace、日志、UI、human interface、web_fetch allowlist、Docker 镜像与执行环境基础设置 | 偶尔 |
| `model_routing.yaml` | endpoint、profile、primary/fallback 候选链、上下文截断策略 | 只在新增 provider/model/fallback 时改 |
| `agent_params.yaml` | agent capability registry：工具、读写权限、prompt/schema、behavior、mode 说明；T2/T3 机械阈值的唯一配置处 | 开发 agent/tool 或调整文献流程阈值时改 |
| `state_machine.yaml` | 状态机拓扑、输入输出、gate、分支、节点 extra | 改流程时改 |
| `gates.yaml` | human gate 展示、选项和附加上下文 | 改 gate 时改 |
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
| T2/T3 文献流程阈值 | `agent_params.yaml: agents.scout.behavior.t2_finalize` / `agents.reader.modes.read.behavior` | active pool、去重、补资源并发、snowball、deep-read queue、abstract sweep |
| 工具列表和读写权限 | `agent_params.yaml` | 能力声明，不是预算表 |
| endpoint/API base/API key env | `model_routing.yaml` + `.env` | 路由候选定义与密钥分离 |
| 状态机输入输出和分支 | `state_machine.yaml` | 默认不要在这里写 LLM/budget 参数 |

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
| `active_pool_max` | `120` | `agents.scout.behavior.t2_finalize` | `papers_dedup.jsonl` / `papers_verified.jsonl` 的 active candidate pool 上限；超额进入 `papers_backlog.jsonl` |
| `screened_active_pool_cap` | `60` | `agents.scout.behavior.t2_finalize` | `semantic_screen.can_enter_deep_read=true` 候选在 active pool 中的优先保留上限 |
| `bridge_active_pool_cap_per_bridge` | `15` | `agents.scout.behavior.t2_finalize` | 兼容旧字段；未设置 must cap 时作为 `must_explore` bridge 的默认 active 保留上限 |
| `must_bridge_active_pool_cap_per_bridge` | `15` | `agents.scout.behavior.t2_finalize` | 每个 `priority=must_explore` bridge 在 active pool 中的召回保留上限 |
| `should_bridge_active_pool_cap_per_bridge` | `5` | `agents.scout.behavior.t2_finalize` | 每个 `priority=should_explore` bridge 在 active pool 中的召回保留上限；低于 must，避免桥接召回挤掉主线 |
| `snowball_active_pool_cap` | `12` | `agents.scout.behavior.t2_finalize` | citation snowball 候选在 active pool 中的优先保留上限 |
| `dedup_title_threshold` | `0.95` | `agents.scout.behavior.t2_finalize` | raw、snowball 和最终 active pool 去重时的标题相似度阈值 |
| `access_audit_top_n` | `50` | `agents.scout.behavior.t2_finalize` | `access_audit.md` 展示的 top 论文数量 |
| `pre_active_light_backfill_max` | `220` | `agents.scout.behavior.t2_finalize` | active/backlog 切分前先对全 dedup 池前 N 篇做轻量标题/DOI/OA/摘要补全；`0` 关闭，`-1` 不限量 |
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
| `progress.update_on_finalize` | `true` | `agents.scout.behavior.progress` | T2 deterministic finalize 开始、active/backlog 切分、完成/失败时是否同步写 progress |
| `deep_read_min` | `35` | `agents.reader.modes.read.behavior` | T3 validator 最少需要完成的结构合格 deep-read note 数 |
| `deep_read_target` | `35` | `agents.reader.modes.read.behavior` | T3 目标精读数，会写入 prompt、queue meta 和校验提示 |
| `deep_read_max` | `45` | `agents.reader.modes.read.behavior` | active deep-read target 上限，保护位也在该上限内计数 |
| `probe_pool` | `45` | `agents.reader.modes.read.behavior` | T3 优先 probe 的候选池大小 |
| `mainline_screened_cap` | `90` | `agents.reader.modes.read.behavior` | 主线 shallow/screened backlog 在 `deep_read_queue.jsonl` 中保留的上限 |
| `bridge_deep_floor` | `3` | `agents.reader.modes.read.behavior` | 每个 must_explore bridge 通过 screen 后的 active deep-read 保底 |
| `bridge_screened_cap` | `7` | `agents.reader.modes.read.behavior` | 每个 bridge 的 shallow/screened backlog 保留上限 |
| `bridge_pool_cap` | `15` | `agents.reader.modes.read.behavior` | 每个 bridge 在 deep-read queue 中保留的候选总上限 |
| `citation_hub_slots` | `3` | `agents.reader.modes.read.behavior` | citation graph 枢纽节点保护槽，仍需 Reader 复核 |

当前 Reader 的 `modes.read.behavior.abstract_sweep` 默认用于覆盖 T3 deep read 后尚未读完的 active verified/dedup 论文；`papers_backlog.jsonl` 是覆盖账本和人工/显式回捞池，默认不自动扫回证据链：

- `expected_notes_ratio: 1.0` 是无 queue 旧 workspace 的 fallback 比例，表示输入池默认必须 100% 有笔记；新主流程仍优先用 `deep_read_queue` 区分 active deep-read 和 shallow/backlog。
- `lite_paper_num: 120` 表示每轮最多处理 120 篇 abstract sweep 候选，包含有 abstract 的逐篇 note 和 metadata-only 的批量 triage；这是候选预算 cap，不是最终相关性判断。
- `sources: [papers_verified, papers_dedup]` 表示默认只覆盖 active pool；如显式加入 `papers_backlog`，`read_disposition=deferred/backlog` 或 `triaged_reason=bridge_pool_cap_exceeded/t2_active_pool_cap_exceeded/domain_profile_filtered` 的记录仍会被跳过，只保留覆盖审计和人工回捞语义。
- `min_relevance: 0.0` 表示不靠 metadata priority hint 丢弃候选。
- `priority_weights` 默认 `relevance/resource/year = 0.70/0.20/0.10`，用于在候选预算内排序：`relevance_score` 仍是检索/元数据优先级提示，资源可获得性和发表年限只影响“先读谁/先补谁”。
- `include_metadata_only: true` 表示缺摘要但有标题的论文会进入 `literature/metadata_triage.md` 批量 triage；正常完成路径调用 Reader LLM 做 metadata-only 审阅，中断/LLM 失败时用确定性 fallback。它不会生成逐篇 note、BibTeX 或 comparison evidence。
- `metadata_triage_report: literature/metadata_triage.md` 是 metadata-only triage report 路径；该报告只能作为补资源/升级阅读线索，不能进入 claim evidence。
- `exclude_semantic_excluded: true` 表示 Scout 已明确判为 `shared_keyword_only/unrelated` 或禁止 deep-read 的论文默认不再进入 abstract note、BibTeX 和 comparison table，避免污染 T3.5/T8 语料；如需做排除线索复核，可在项目配置中显式设为 `false`。

这组参数只控制阅读/补资源优先级；论文是否能作为学术证据仍由 Reader/Writer 的 LLM 判断、evidence level 和 claim audit 控制。

#### `behavior_profiles.survey`

默认 T2/T3 参数等同 `research_article`。runtime 会从 workspace 自动选择 profile：

- `project.yaml.metadata.manuscript_type/project_type/article_type/paper_type`
- `user_seeds/seed_outline_profile.json` 的 `manuscript_type/project_type`

命中 `survey` / `综述` / `review` / `taxonomy-driven` 时，启用
`agents.scout.behavior.behavior_profiles.survey` 与
`agents.reader.modes.read.behavior.behavior_profiles.survey`。当前 survey profile 主要放宽：

| 项 | research_article | survey |
| --- | --- | --- |
| T2 active pool | `active_pool_max=120` | `180` |
| T2 screened cap | `screened_active_pool_cap=60` | `90` |
| T2 pre-active backfill | `pre_active_light_backfill_max=220` | `360` |
| T2 citation snowball | `snowball_max_sources=12, max_candidates=40` | `18, 70` |
| T3 deep-read | `35/35/45` | `45/55/65` |
| T3 abstract sweep | `lite_paper_num=120` | `180`，并可扫描 `papers_backlog` |

用户 Markdown 综述提纲经 `normalize_seed_outline` 生成 `seed_outline_profile.json` 后会自动触发 survey profile。提纲里的 `representative_literature_directions` 只是 query/taxonomy prior，不是已验证 citation，不会写入 `seed_papers.jsonl`。

### T2 metadata / citation backfill 参数归属

T2 的 OpenAlex DOI/OA 详情补全、Crossref DOI 详情补全、多源摘要回填、raw cache merge 是 deterministic finalize 的机械步骤。runtime 会先对排序后的 dedup 候选执行一轮 bounded light backfill，再切 active/backlog，随后只对 active 做更完整的详情补全和 citation snowball。active pool、bridge priority、snowball 进入 active 的配额和 progress 开关在上表的 `agents.scout.behavior.t2_finalize/progress`。Scout 的模型只负责 query 设计和语义筛选，不负责手写这些阈值。

质量排障优先看 `literature/temp/scout_progress.md`、`literature/search_log.md` 和 `_runtime/logs/researchos.log`，尤其是 active/backlog 规模、`eligible/candidate/attempted/skipped_by_cap/failed/remaining_missing_*`、`raw_persisted/raw_merged`、`skipped_existing_snowball_records` 和 `T2 raw 元数据缓存回写`。

### 日志与控制台

- `runtime.yaml: ui.quiet=true`：控制台只显示状态跳转、暂停、错误和最终结果
- `runtime.yaml: ui.verbose=true`：控制台额外显示 Agent 文本输出和 step token 摘要
- `workspace/<name>/_runtime/logs/researchos.log`：统一人类时间线，由 runtime 写入，不受 quiet 影响
- `workspace/<name>/_runtime/logs/researchos-debug.log`：底层 Python/structlog 调试日志
- `workspace/<name>/_runtime/traces/*.jsonl`：机器级完整 trace

LiteLLM INFO 默认被 runtime 压到 WARNING；正常情况下不会刷屏，也不会进入 `researchos.log`。

## `state_machine.yaml`

这里定义 workflow，不是参数表。默认主链不应写 `llm` / `budget`。只有临时调试或确实需要固定某个 task 时，才使用 task 级强覆盖：

```yaml
states:
  HELLO:
    agent: hello
    llm:
      profile: hello_fast
```

如果 `user_settings.yaml` 改了 profile 但运行不生效，优先检查 `validate-config` 输出里的 `state_machine_llm_overrides`。

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
