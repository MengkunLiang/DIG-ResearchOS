# ResearchOS Logs, Progress, And Diagnosis

ResearchOS has four layers of observability. The CLI answers "what research
activity is running now and what did it conclude"; the event stream records
bounded user-facing facts; logs answer "what happened operationally"; traces
answer "what did this run call and return". The workspace is the final evidence
of what actually exists.

Assume the project path is `./workspace/project-a` below.

## 1. Where To Look

| Location | Use it for |
| --- | --- |
| Terminal / CLI progress | Current task, phase, human gate, environment preflight, tool milestones, and stage summary. |
| `_runtime/events/<run_id>.jsonl` | Versioned, machine-readable researcher-facing events: inputs, bounded calculation summaries, decisions, warnings, artifact manifests, and human actions. Written for every run. |
| `state.yaml` | Current task, `PAUSED`/`WAITING_HUMAN` status, and pending human gate. |
| `_runtime/logs/researchos.log` | Compact cross-run timeline and error summaries. |
| `_runtime/logs/researchos-debug.log` | Runtime-level diagnostics when the normal log is insufficient. |
| `_runtime/traces/<run_id>.jsonl` | A detailed, machine-readable recording of one run. |
| `_runtime/resume/` | Saved recovery context for interrupted tasks. |
| `_runtime/skill_sessions/<session-id>.json` | A standalone Skill's request, deterministic input-check result, selected source paths, persisted observable progress (`step`/`phase`/`tool_name`), final output status, metrics, and trace path. |
| Stage artifacts | The actual research, writing, compile, and evidence outputs. |

```bash
python -m researchos.cli status --workspace ./workspace/project-a
tail -n 100 ./workspace/project-a/_runtime/logs/researchos.log
```

## 2. CLI Progress Model

The CLI uses one **Stage Start -> Stage Progress -> Stage Summary** protocol for
`run`, `resume`, and `run-task`, including immediate Human Gates. It emits
bounded research-facing facts, not hidden chain-of-thought and not raw tool
payloads.

Interactive terminals render these panels with Rich truecolor accents so stage
starts, warnings, compilation, and Artifact manifests remain scannable in long
runs. Color never changes the event/log/artifact content. Use `--no-color` for
ANSI-free CI output, redirection, or terminals without color support.

Agent-visible Markdown is rendered in a bounded panel after terminal-safety
normalization: ANSI sequences are excluded from the public view and keycap
emoji numbering is converted to ordinary ordered-list numbering. Tool calls use
stable colors by operation category (read, retrieval, artifact write, build,
human gate). Tool Results use green for completed bounded outcomes, amber for
`SKIPPED` optional inputs and `DEGRADED` retrievable-source conditions, and red
only for blocking failures. A provider `429`, timeout, or connection error is
not silently hidden: its source, retryability, fallback availability, and any
`Retry-After` cooldown remain in trace/events and the T2 source-health summary.
Raw Tool payloads remain in trace/debug logs, not the interactive
console. Standalone `SKILL_*` runs use the same event protocol, including their
input/output manifest and session-backed final summary.

Human Gates retain ordinary line input so they work over SSH and in tmux, but
their decision context, risk approval, and clarification headers use the same
colored panel convention. Selectable options and upload/recovery commands stay
plain and copyable; `--no-color` removes all panel ANSI styling too.

### Stage Start

Every declared research stage begins with a panel containing:

- stage number/name, goal, research question, and planned operations;
- an input Artifact table with path, meaning, required/available status,
  records/size, and current purpose;
- expected outputs with meaning and downstream consumer;
- branch notes, including optional Survey or external-executor paths.

`declared input` means the state-machine I/O contract permits or requires a
file. `actual read` is recorded only after a runtime tool accesses a path under
the workspace. A declared file is never presented as read just because it
exists.

### Stage Progress

Progress reports statistics, rankings, decisions, and bounded Top-N records
only when they have a durable or tool-result basis. Examples include:

- T2 query/source/bucket counts, metadata verification, score hints, citation
  structure, protected reading slots, backlog, and access warnings;
- T3 per-paper evidence coverage, pages, extraction calls, truncation outcome,
  §13 mechanism evidence type, boundaries, and unresolved fields;
- T3.5 contribution/mechanism/tension/transfer workbench summaries;
- T4 mainline versus bridge versus coverage-supplement candidate origins,
  Pass1 -> Pass2 recommendation changes, `unsupported` channels, score/risk
  summaries, and candidate-card paths;
- T5 execution contract/Skill inventory, T7 run failures/baseline coverage/
  claim support, T8 section evidence alignment, and T9 compile/fingerprint
  state.

The following distinctions are intentional:

- a retrieval coverage gap is **not** a research gap;
- a citation graph/hub/ranking is a reading-priority hint, not paper quality;
- a tool cluster is not a scholarly mechanism conclusion;
- `prior_art: none` is high uncertainty, not proof of novelty;
- T4 coverage supplements check angles that mainline reasoning can miss; they
  are neither mandatory templates nor a replacement for mainline reasoning;
- `unsupported`, `abstract-only`, `metadata-only`, and `LLM_REVIEW_REQUIRED`
  remain visible rather than being silently removed.

### Stage Summary

Each stage ends with conclusions, key statistics, decision/risk notes, actual
reads, and an **Artifact Manifest**. Output dispositions are:

| Disposition | Meaning |
| --- | --- |
| `created` | The output did not exist at stage start and now exists. |
| `updated` | It existed but its content fingerprint changed. |
| `reused` | It existed and its content fingerprint did not change. |
| `missing` / `optional_missing` | A required or optional Artifact was absent. |
| `invalid` | The artifact could not be parsed for inspection. |
| `invalidated` | The Agent ended, but outer state-machine Artifact validation rejected the result. It cannot enter the next stage. |

Resume prints a short recovery summary and only reports the pending run. It
does not replay all old tool progress; the earlier event JSONL remains available
for inspection.

Typical terminal facts include:

```text
[运行中] T4 · step 1 | 模型请求已提交，正在等待下一组可执行动作。
[运行中] T4 · step 1 | 正在等待模型返回下一组可执行动作；本次调用已持续 12s。
[T4 Gate1 1/6] Pass1 候选池已落盘：ideation/_pass1_forward_candidates.json
[T4 Gate1 2/6] 正在接地复核并写入 Pass2
[Environment] T9 LaTeX preflight passed: backend=latexmk
[Gate] waiting for t4_gate1_selection_gate
[Summary] T2 completed: artifacts and next stage
```

To control density:

```bash
python -m researchos.cli run --workspace ./workspace/project-a --verbosity concise
python -m researchos.cli resume --workspace ./workspace/project-a --verbosity detailed
python -m researchos.cli run-task T4 --workspace ./workspace/project-a --no-color
python -m researchos.cli run-task T2 --workspace ./workspace/project-a --json-events
```

| Option | Effect |
| --- | --- |
| `--verbosity concise` | Goal, required conclusions, manifest, warnings, and required human action. |
| `--verbosity normal` | Default; includes input tables, main statistics, bounded Top-N, decisions, and manifest. |
| `--verbosity detailed` | Adds bounded per-query/per-paper/per-bridge/per-candidate rows. It never dumps full payloads. |
| `--no-color` | Disables ANSI output for SSH capture/CI. |
| `--json-events` | Mirrors the same bounded event JSON to stdout. Durable `_runtime/events/*.jsonl` exists regardless. Avoid this mode at interactive Gates. |

Each stage summary should state:

- completed task and next task or pause reason
- files produced and their meaning
- files that require user review
- recovery command and relevant log/trace path when paused

Large artifacts are represented by a path and structured summary in CLI output.
The complete candidate cards, scores, evidence, and references remain in the
workspace so terminal output does not repeat the same text across gates.

当一次模型调用超过 12 秒，普通 CLI 先显示一次等待心跳，随后每 20 秒更新；工具返回后再显示
实际工具、写入路径和结果。T4 的 checkpoint 还会同步写入 `ideation/t4_progress.md`，因此 provider
超时或进程重启后仍能看到真实完成数。`--verbose` 会额外显示更密的工具叙述。不要期待或依赖
模型的私有推理：所有进度均来自已落盘动作、工具调用、校验和 runtime 状态。

T4 Gate1 对六个必需 artifact 会额外显示 `1/6 写入中` 与 `1/6 已保存` 这类 durable-write
事件，并更新同一个 `t4_progress.md`。在 Pass1、四个补充通道、Pass2、评分和卡片阶段，
`log_t4_ideation_progress` 会追加 `ideation/t4_execution_events.jsonl`：它只记录候选 ID、短标题、
通道、完成数、已落盘的 Pass2 建议和分数，CLI 会逐项展示；runtime 也会在候选 JSON 实际落盘后
回读并补发候选级摘要，因此模型漏报事件不会让界面静默。`running` 只表示本轮正在执行或刚提交
provider/tool，`done` 只表示文件已经实际存在；不会为了让界面好看而预先宣布 Pass 已完成。Skill
会话也以相同原则保存 `awaiting_llm`、`tool_running`、`tool_completed`、`waiting_runtime` 等可观察 phase。

T2 还会在阶段总结中给出 `Retrieval Source Health`：来源可用、已返回记录、暂时降级、限流或冷却期会
分别显示。只要其它来源已经提供足够的可审计语料，单个来源的 `DEGRADED` 不会被显示为整个 T2 失败；
只有必需输入缺失或所有可用检索路径耗尽才是阻塞性失败。`user_seeds/seed_external_resources.jsonl` 这类
声明为可选的输入不存在时显示 `SKIPPED`，不会再给出“检查日志并 resume”的错误建议。

## 3. Normal Diagnosis Order

1. Read the last CLI block: it identifies the task and pause/failure class.
2. Run `status` to confirm `state.yaml` rather than guessing from old output.
3. Read the last part of `researchos.log`.
4. Render the matching trace if the tool or provider sequence matters.
5. Inspect the artifact named in the validation error.
6. Repair the real cause, then use `resume`.

```bash
python -m researchos.cli status --workspace ./workspace/project-a
tail -n 120 ./workspace/project-a/_runtime/logs/researchos.log
python -m researchos.cli trace <run_id> --workspace ./workspace/project-a
```

Raw trace JSONL is available when needed:

```bash
python -m researchos.cli trace <run_id> --workspace ./workspace/project-a --raw
```

## 4. Pause Classes And Repairs

| CLI signal | Meaning | First action |
| --- | --- | --- |
| `WAITING_HUMAN` / `[Gate]` | A human decision is required | Answer the displayed gate, then `resume`. |
| `WAITING_ENVIRONMENT` | A required local/Docker tool is absent or unhealthy | Run `doctor`, repair the named backend, then `resume`. |
| provider timeout/unavailable | All configured LLM candidates failed temporarily | Check `selftest`/provider configuration, then `resume`; current artifacts remain. |
| `VALIDATION_FAILED` | Output exists but does not satisfy its contract | Read the named artifact and validator reason; rerun/resume the responsible task. |
| `cached_compile_failure_same_tex` | Same TeX dependency fingerprint already failed | Change the source/style/bibliography, then compile again. |
| stale `RUNNING` state | Process stopped without normal completion | `resume` marks it interrupted and rebuilds context. |
| Skill `WAITING_INPUT` | A standalone Skill is missing a declared upload or its request | In noninteractive mode, run `researchos skill-status`, upload to the displayed `user_inputs/...` path, then repeat `run-skill` with `--session-id ... --resume`; no LLM call was made. In a TTY, `--interactive` may instead run a restricted intake turn that only stages human-provided material under the same path. |
| Skill `WAITING_RUNTIME` | Input is valid but provider/selftest/runtime setup could not start | Inspect `skill-status` for the persisted provider/environment error, repair it, then repeat the displayed `run-skill ... --resume` command. |
| Skill follow-up request | Deterministic paths existed but the running Skill found a semantic material gap | Read `user_inputs/<skill>/_followup_request.md`, provide the requested answer/file, then resume the same Skill session. |

### LaTeX Failures

For T3.6/T9, inspect the backend before asking an agent to retry:

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
```

Then inspect the real compile files:

```bash
tail -n 120 ./workspace/project-a/drafts/survey/survey.log
tail -n 120 ./workspace/project-a/submission/bundle/main.log
```

`waiting_environment_latexmk_missing` means the old runtime could not find a
local compiler. Current `auto` uses host `latexmk`, then host `tectonic`, then
the configured Docker TeX image. In Compose, the image itself must contain TeX.
See [docker.md](docker.md) for exact repairs. Never hand-write a PDF or compile
report: hashes and dependency fingerprints will reject it.

For a TeX syntax error, `latex_compile` now runs `latexmk` with `-halt-on-error`
and `-file-line-error`. The CLI reports the real compilation start, then the tool
returns the first actionable source-line error; it does not silently continue
until the 1800-second ceiling. Native timeout cleanup terminates the compiler
process group, and multiple compiles requested in one agent action are serialized.

### LLM Provider Failures

The log includes the profile/tier and fallback summary but not secrets. Use:

```bash
python -m researchos.cli selftest
python -m researchos.cli validate-config
```

Do not delete partial stage artifacts after a temporary provider failure. The
recovery system uses those artifacts to avoid regenerating completed work.

## 5. Useful Commands

```bash
# Live compact log
tail -f ./workspace/project-a/_runtime/logs/researchos.log

# Search only pauses and validation failures
rg -n 'PAUSED|WAITING_|VALIDATION_FAILED|ERROR' \
  ./workspace/project-a/_runtime/logs/researchos.log

# List recent traces
ls -lt ./workspace/project-a/_runtime/traces | head

# Validate current task artifacts without running an agent
python -m researchos.cli validate --workspace ./workspace/project-a

# Validate a specific stage's contract
python -m researchos.cli validate --workspace ./workspace/project-a --task T9

# Inspect standalone-Skill sessions without contacting an LLM
python -m researchos.cli skill-status --workspace ./workspace/project-a
python -m researchos.cli describe-skill paper-revision --workspace ./workspace/project-a
```

## 6. Privacy And Size

`researchos.log` is intentionally compact. Traces can contain tool arguments,
artifact paths, and model/tool outputs, so treat `_runtime/` as project data.
Do not paste full traces into issue reports with `.env`, private seeds, or
unpublished manuscripts. Share the smallest relevant error summary and redact
paths or source content as needed.

For task-specific artifact semantics, use [agent_pipeline.md](agent_pipeline.md).
For recovery commands and environment setup, use the root README and
[docker.md](docker.md).
