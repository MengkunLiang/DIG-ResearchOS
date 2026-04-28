# ResearchOS Logging and Trace Guide

这份文档专门回答两类问题：

- 日志到底在哪看
- 出问题时应该先看 stdout、log 还是 trace

对于 ResearchOS 来说，**真正有价值的调试信息通常不只在终端输出里，而在 workspace 的 `_runtime/` 目录里。**

---

## 1. 先记住两个位置

假设你的 workspace 是：

- `/home/liangmengkun/ResearchOS/workspace/local-test2`

那么最重要的两个位置是：

- 日志：
  - `/home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log`
- Trace：
  - `/home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/traces/*.jsonl`

简单理解：

- `researchos.log` 适合看整体运行过程和错误摘要
- `trace/*.jsonl` 适合看某一次 run 的逐步细节

---

## 2. 日志和 trace 的区别

### 2.1 `researchos.log`

它记录的是：

- startup summary
- environment warnings
- agent 启动/结束
- tool crash
- runtime error
- validator failure
- gate / resume / budget 相关信息

它更像“系统运行日志”。

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
tail -n 80 /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

如果看到：

- `tool_crashed`
- `Validation failed`
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
tail -f /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

### 4.2 只看最近 100 行

```bash
tail -n 100 /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

### 4.3 看错误和警告

```bash
grep -nE "ERROR|WARNING|tool_crashed|Validation failed|Budget exceeded|LLM failed" \
  /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

### 4.4 看某个 task 的相关日志

```bash
grep -n "T7" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
grep -n "T9" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

### 4.5 看 trace（人类可读）

```bash
cd /home/liangmengkun/ResearchOS
researchos trace T7_single_12345678 --workspace ./workspace/local-test2
```

### 4.6 看 trace（原始 JSONL）

```bash
cd /home/liangmengkun/ResearchOS
researchos trace T7_single_12345678 --workspace ./workspace/local-test2 --raw
```

### 4.7 直接 grep trace

```bash
grep -n "tool_crashed" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/traces/T8-REVIEW-1_single_0b0655e0.jsonl
grep -n "\"tool_name\"" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/traces/T3_single_678acc5c.jsonl
```

---

## 5. 你会在日志里看到什么

### 5.1 startup summary

典型会出现：

```text
[startup] workspace=/home/liangmengkun/ResearchOS/workspace/local-test2
[startup] state_machine=/home/liangmengkun/ResearchOS/config/state_machine.yaml
[startup] gates=/home/liangmengkun/ResearchOS/config/gates.yaml
[startup] model_routing=/home/liangmengkun/ResearchOS/config/model_routing.yaml
[startup] mcp_servers=2 mcp_tools=0
```

它很有用，因为能帮你确认：

- 当前用的是哪个 workspace
- 当前读的是哪套配置
- MCP 是否加载

### 5.2 LLM 路由信息

你会看到类似：

```text
LiteLLM completion() model= deepseek-ai/DeepSeek-V4-Flash; provider = openai
```

这是 provider/路由级日志，不一定表示错误。

真正需要关注的是：

- 是否 fallback 了
- 是否连续 timeout
- 是否 provider 忙

### 5.3 tool crash

典型形式：

```text
{"tool": "read_file", "event": "tool_crashed", ...}
```

这通常说明：

- tool 收到不合法输入
- tool 本身抛异常
- agent 用错了工具

### 5.4 validator failure

典型形式：

```text
Validation failed 5 times. Last reason: ...
```

这类问题要看两边：

- agent 实际写了什么
- validator 期望什么

### 5.5 budget exceeded

典型形式：

```text
stop_reason: budget
error: Budget exceeded on wall_seconds: 922/800
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
grep -n "T2" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
ls /home/liangmengkun/ResearchOS/workspace/local-test2/literature
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
ls /home/liangmengkun/ResearchOS/workspace/local-test2/literature/paper_notes
ls /home/liangmengkun/ResearchOS/workspace/local-test2/literature/deep_read_queue_pending.jsonl
```

再看日志里有没有：

- 恢复模式提示
- queue pending 生成

如果已有 note 但没生成 pending queue，说明恢复逻辑可能没生效。

## 6.3 T7 校验失败

先看：

```bash
tail -n 120 /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
cat /home/liangmengkun/ResearchOS/workspace/local-test2/experiments/results_summary.json
cat /home/liangmengkun/ResearchOS/workspace/local-test2/experiments/ablations.csv
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
ls /home/liangmengkun/ResearchOS/workspace/local-test2/submission/bundle
tail -n 200 /home/liangmengkun/ResearchOS/workspace/local-test2/submission/bundle/main.log
cat /home/liangmengkun/ResearchOS/workspace/local-test2/submission/migration_report.md
```

判断重点：

- 有没有 `main.pdf`
- 编译有没有真的重试修复
- `migration_report.md` 是否明确写了“编译状态: 成功”

---

## 7. Docker 模式下怎么看日志

即使在 Docker 模式下，日志也还是落在挂载出来的 workspace。

例如：

- 容器内：`/workspace/_runtime/logs/researchos.log`
- 宿主机：`/home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log`

因此最简单的方式通常还是在宿主机直接看：

```bash
tail -f /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

如果你确实要进容器：

```bash
docker exec -it <container-id> bash
tail -f /workspace/_runtime/logs/researchos.log
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
error: Validation failed 5 times. Last reason: ...
```

你下一步应该是：

1. 看 `researchos.log` 里最后一次 validator reason
2. 看该 run 的 trace 里 `finish_task` 前到底写了哪些文件
3. 打开对应 artifact，看内容是否真的满足 validator 规则

---

## 10. 一组非常实用的命令

实时看本地项目日志：

```bash
tail -f /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

查最近一次 T9 的错误：

```bash
grep -n "T9" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log | tail -n 30
```

查所有 tool crash：

```bash
grep -n "tool_crashed" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

查所有预算问题：

```bash
grep -n "Budget exceeded" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

查所有 validator 失败：

```bash
grep -n "Validation failed" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
```

查所有 LLM 失败：

```bash
grep -n "LLM failed" /home/liangmengkun/ResearchOS/workspace/local-test2/_runtime/logs/researchos.log
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
