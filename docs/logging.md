# ResearchOS Logging and Trace Guide

这份文档专门回答两类问题：

- 日志到底在哪看
- 出问题时应该先看 stdout、log 还是 trace

对于 ResearchOS 来说，**真正有价值的调试信息通常不只在终端输出里，而在 workspace 的 `_runtime/` 目录里。**

---

## 1. 先记住两个位置

假设你的 workspace 是：

- `./workspace/local-test2`

那么最重要的两个位置是：

- 日志：
  - `./workspace/local-test2/_runtime/logs/researchos.log`
- Trace：
  - `./workspace/local-test2/_runtime/traces/*.jsonl`

简单理解：

- `researchos.log` 适合看整体运行过程和错误摘要
- `trace/*.jsonl` 适合看某一次 run 的逐步细节

### 1.1 `run_id` 一般长什么样

你在 trace、状态和日志里会看到类似：

- `T3_single_678acc5c`
- `T7_single_0b0655e0`
- `T8-REVIEW-1_single_0b0655e0`

大致可以这样读：

- 前缀：task 名
- `single`：单任务 runner
- 后缀：本次运行的短 ID

如果是完整 pipeline，run_id 的命名会不同，但同样可以在：

- `_runtime/traces/`
- `researchos status`
- `researchos trace <run_id> ...`

之间互相对上。

---

## 2. 日志和 trace 的区别

### 2.1 `researchos.log`

它是人类可读的统一运行时间线，一行一个事件，不写完整 prompt、完整 response 或大 JSON。典型事件包括：

- `RUN_START` / `RUN_END`
- `TASK_START` / `TASK_END`
- `STATE_TRANSITION`
- `AGENT_STEP`
- `LLM_CALL` / `LLM_RESULT`
- `TOOL_CALL` / `TOOL_RESULT`
- `FINISH_REQUESTED`
- `FINALIZE_STARTED` / `FINALIZE_DONE`
- `VALIDATION_PASS` / `VALIDATION_FAILED` / `VALIDATION_RETRY`
- `ASK_HUMAN` / `HUMAN_GATE`
- `PAUSED` / `RESUME`
- `ERROR`

它更像“人能直接读的运行时间线”。底层 Python/structlog 调试日志写在 `researchos-debug.log`；机器级完整细节仍在 `trace/*.jsonl`。

### 2.2 `trace/*.jsonl`

它记录的是某一个 run 的详细流水：

- step 编号
- LLM 请求与响应
- tool call
- tool result
- finish_task
- validate retry

它更像“单次执行录像”。

---

## 3. 什么时候先看哪个

推荐顺序：

1. 先看 CLI 最后的错误摘要
2. 再看 `researchos.log`
3. 再看对应 `trace`
4. 最后看 workspace 产物是否真的落盘

### 3.1 例子：任务直接崩溃

先看：

```bash
tail -n 80 ./workspace/local-test2/_runtime/logs/researchos.log
```

如果看到：

- `ERROR`
- `VALIDATION_FAILED`
- `raw_persistence_mismatch`
- `Budget exceeded`
- `LLM failed`

就知道该往哪条线继续查。

### 3.2 例子：看起来“跑过了”，但结果不对

优先看：

- `trace/<run_id>.jsonl`
- 对应 artifact 文件

因为这类问题往往是：

- tool 用错了
- 写进了错误文件
- validator 规则和产物结构不一致

---

## 4. 最常用的查看命令

### 4.1 实时看日志

```bash
tail -f ./workspace/local-test2/_runtime/logs/researchos.log
```

### 4.2 只看最近 100 行

```bash
tail -n 100 ./workspace/local-test2/_runtime/logs/researchos.log
```

### 4.3 看错误和警告

```bash
grep -nE "ERROR|VALIDATION_FAILED|PAUSED|Budget exceeded|LLM failed|raw_persistence_mismatch" \
  ./workspace/local-test2/_runtime/logs/researchos.log
```

### 4.4 看某个 task 的相关日志

```bash
grep -n "T7" ./workspace/local-test2/_runtime/logs/researchos.log
grep -n "T9" ./workspace/local-test2/_runtime/logs/researchos.log
```

### 4.5 看 trace（人类可读）

```bash
cd ResearchOS
researchos trace T7_single_12345678 --workspace ./workspace/local-test2
```

### 4.6 看 trace（原始 JSONL）

```bash
cd ResearchOS
researchos trace T7_single_12345678 --workspace ./workspace/local-test2 --raw
```

### 4.7 直接 grep trace

```bash
grep -n "\"tool_result\"" ./workspace/local-test2/_runtime/traces/T8-REVIEW-1_single_0b0655e0.jsonl
grep -n "\"tool_name\"" ./workspace/local-test2/_runtime/traces/T3_single_678acc5c.jsonl
```

---

## 5. 你会在日志里看到什么

### 5.1 task / state summary

典型会出现：

```text
2026-06-04 17:42:10 | RUN_START | run_id=T2_single_xxx task=T2 agent=scout
2026-06-04 17:42:10 | TASK_START | task=T2 agent=scout mode=scout
2026-06-04 17:42:16 | TOOL_CALL | step=1 tool=openalex_search args={"query":"...","source":"openalex_search","max":20}
```

它能帮你确认当前 workspace、task、agent、状态跳转和工具调用。

### 5.2 LLM 路由信息

默认情况下，LiteLLM 的 INFO 噪音不会进入控制台或 `researchos.log`。ResearchOS 只记录紧凑的 `LLM_CALL` / `LLM_RESULT`，例如 profile、tier、model、endpoint、token 和 duration。只有 provider 连续超时、fallback 全失败或不可恢复错误时，CLI 和日志才会出现错误摘要。

如果你仍然看到 `LiteLLM completion() ...` 大量刷屏，优先检查是否有外部脚本手动打开了 LiteLLM debug，或运行环境中覆盖了 logging level。

### 5.3 tool result

搜索工具的 `TOOL_RESULT` 会额外记录：

- `query`
- `source`
- `reported_paper_count`
- `persisted_raw_delta`
- `raw_count_after`
- `append_status`

如果工具返回了论文但 raw 没有落盘，会出现 `raw_persistence_mismatch` 或 `raw_append_failed`，这比只看 `papers_raw.jsonl` 更容易定位问题。

T2 里需要特别区分三类“没有论文”的情况：

- `empty_query_plan`：`expand_queries` 或 `detect_duplicate_queries` 没有拿到任何非空检索式。这是上游 query 规划问题，应回到 `project.yaml`、真实 seed、`domain_profile` 和 `llm_queries`，必要时 `ask_human` 补研究边界。
- `empty_query`：某个搜索工具实际收到空 query。这是工具调用参数错误，不是 API 正常返回 0 篇。
- `reported_paper_count=0` 且 `query/source` 非空：这是某个真实检索式在某个 source 上没有命中，可以扩大/改写 query 或换 source。

旧版 `scout_progress.md` 可能出现过 `检索 '' -> 0 篇 (来源: )`。这类记录通常不是 OpenAlex/Crossref/arXiv 真正执行了空检索，而是模型把普通状态说明误写成 `log_scout_progress(action="search_result", detail="...")`，旧工具把缺失的 `query/source/count` 默认成空字符串和 0。现在 `log_scout_progress(action="search_result")` 必须显式提供非空 `query`、非空 `source` 和 `count`，否则返回 `invalid_progress_event`。

`literature/temp/scout_progress.md` 现在也由 runtime 自动追加关键进度，不只依赖 Scout LLM 主动调用工具。T2 搜索工具自动保存 raw 后会写 `runtime_search_result`，T2 deterministic finalize 会写 `runtime_finalize_started`、`runtime_active_pool_pre_backfill`、`runtime_active_pool_final`、`runtime_finalize_done` 或 `runtime_finalize_failed`。如果这个文件不更新，优先检查 `config/agent_params.yaml -> agents.scout.behavior.progress.enabled/update_on_tool_results/update_on_finalize`。

### 5.4 validator failure

典型形式：

```text
2026-06-04 17:45:02 | VALIDATION_FAILED | task=T3 step=71 reason="deep_read_queue 仅完成 7/11 篇..."
```

这类问题要看两边：

- agent 实际写了什么
- validator 期望什么

### 5.5 budget exceeded

典型形式：

```text
2026-06-04 17:46:33 | PAUSED | task=T3 reason="Budget exceeded on wall_seconds: 922/800"
```

这时需要判断：

- 当前 task 是否启用了预算扩限 gate
- 是否应该直接 `resume`
- 是否需要调小 prompt 或减少搜索/工具调用

---

## 6. 常见排障路径

## 6.1 T2 检索结果不对

先看：

```bash
grep -n "T2" ./workspace/local-test2/_runtime/logs/researchos.log
ls ./workspace/local-test2/literature
```

再看：

- `papers_raw.jsonl`
- `papers_dedup.jsonl`
- `papers_verified.jsonl`
- `deep_read_queue.jsonl`
- `access_audit.md`

判断重点：

- raw 有没有写
- dedup 数量是否异常
- verification 是否全失败
- queue 是否没保 seed papers

## 6.2 T3 没有续跑

先看：

```bash
ls ./workspace/local-test2/literature/paper_notes
ls ./workspace/local-test2/literature/deep_read_queue_pending.jsonl
```

再看日志里有没有：

- 恢复模式提示
- queue pending 生成

如果已有 note 但没生成 pending queue，说明恢复逻辑可能没生效。

`deep_read_queue_pending_meta.json` 是恢复快照。正常情况下，T3 在成功、`max_steps`、budget 暂停和校验修复暂停后都会刷新它。排查时重点看：

- `refresh_reason`：最近一次刷新来自 `runner_exit:finished`、`runner_exit:max_steps`、`runner_exit:budget`，还是旧的 `context_build`
- `valid_note_file_count` / `invalid_note_file_count`：是否有历史重复 stub 或结构不合格 note
- `completed_note_key_count`：是否明显大于 note 文件数；这是多 ID/标题/DOI 匹配 key，不等于论文篇数
- `pending_queue_count`：是否仍包含已读论文；若有，通常是 note 内部 ID/DOI/arXiv 缺失或写法和 queue 无法匹配

## 6.3 T7 校验失败

先看：

```bash
tail -n 120 ./workspace/local-test2/_runtime/logs/researchos.log
cat ./workspace/local-test2/experiments/results_summary.json
cat ./workspace/local-test2/experiments/ablations.csv
```

常见问题：

- headline 实验 seed 数不够
- ablation 数不够
- 结果结构和 validator 预期不一致

## 6.4 T8 reviewer 读目录报错

现在应该优先用：

- `list_files` 查看目录
- `read_file` 读取具体文件

如果日志里再出现：

- `IsADirectoryError`

通常是：

- agent 仍在错误地用 `read_file` 读目录
- prompt / tool set / tool choice 还需继续调

## 6.5 T9 编译失败

先看：

```bash
ls ./workspace/local-test2/submission/bundle
tail -n 200 ./workspace/local-test2/submission/bundle/main.log
cat ./workspace/local-test2/submission/migration_report.md
```

判断重点：

- 有没有 `main.pdf`
- 编译有没有真的重试修复
- `migration_report.md` 是否明确写了“编译状态: 成功”

---

## 7. Docker 模式下怎么看日志

即使在 Docker 模式下，日志也还是落在挂载出来的 workspace。

例如：

- 容器内：`/workspace/local-test2/_runtime/logs/researchos.log`
- 宿主机：`./workspace/local-test2/_runtime/logs/researchos.log`

因此最简单的方式通常还是在宿主机直接看：

```bash
tail -f ./workspace/local-test2/_runtime/logs/researchos.log
```

如果你确实要进容器：

```bash
docker exec -it <container-id> bash
tail -f /workspace/local-test2/_runtime/logs/researchos.log
```

---

## 8. 日志级别和输出格式

当前 runtime 的日志行为主要受：

- `config/runtime.yaml`
  - `logging.level`
  - `logging.json`

影响。

推荐：

- 本地开发：
  - `level: DEBUG`
  - `json: false`
- 日常运行：
  - `level: INFO`
  - `json: true`

如果想临时调高 CLI 日志级别：

```bash
researchos run-task T3 --workspace ./workspace/local-test2 --log-level DEBUG
```

---

## 9. 日志、trace、artifact 三者怎么配合看

最有效的顺序是：

1. `stdout/stderr`
2. `researchos.log`
3. `trace/*.jsonl`
4. 实际输出文件

举个例子：

如果终端最后提示：

```text
Project paused: Validation failed 3 times. Last reason: ...
```

你下一步应该是：

1. 看 `researchos.log` 里最后一次 validator reason
2. 看该 run 的 trace 里 `finish_task` 前到底写了哪些文件
3. 打开对应 artifact，看内容是否真的满足 validator 规则

---

## 10. 一组非常实用的命令

实时看本地项目日志：

```bash
tail -f ./workspace/local-test2/_runtime/logs/researchos.log
```

查最近一次 T9 的错误：

```bash
grep -n "T9" ./workspace/local-test2/_runtime/logs/researchos.log | tail -n 30
```

查所有 tool crash：

```bash
grep -n "ERROR" ./workspace/local-test2/_runtime/logs/researchos.log
```

查所有预算问题：

```bash
grep -n "Budget exceeded" ./workspace/local-test2/_runtime/logs/researchos.log
```

查所有 validator 失败：

```bash
grep -n "VALIDATION_FAILED" ./workspace/local-test2/_runtime/logs/researchos.log
```

查所有 LLM 失败：

```bash
grep -nE "LLM failed|kind=llm_provider|LLM_CALL|LLM_RESULT" ./workspace/local-test2/_runtime/logs/researchos.log
```

查 T2 搜索是否落盘：

```bash
grep -nE "TOOL_RESULT.*(openalex_search|crossref_search|arxiv_search|multi_source_search|informs_search)" \
  ./workspace/local-test2/_runtime/logs/researchos.log
grep -nE "raw_persistence_mismatch|raw_append_failed|empty_query|empty_query_plan|invalid_progress_event" \
  ./workspace/local-test2/_runtime/logs/researchos.log
grep -nE "runtime_search_result|runtime_active_pool|runtime_finalize" \
  ./workspace/local-test2/literature/temp/scout_progress.md
```

---

## 11. 最后怎么理解这套日志系统

ResearchOS 的日志系统不是“可有可无的附属品”，而是恢复、排障、验证这条链路的一部分。

最实用的经验是：

- 不要只看终端最后一屏
- 不要只看 log，不看 artifact
- 不要只看 artifact，不看 trace

真正有效的是三者结合：

- log 看整体
- trace 看细节
- artifact 看事实

接下来如果你要继续排障，建议顺着读：

- [dev.md](./dev.md)
- [agent_pipeline.md](./agent_pipeline.md)
- [runtime.md](./runtime.md)
