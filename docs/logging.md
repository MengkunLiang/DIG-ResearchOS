# ResearchOS Logs, Progress, And Diagnosis

ResearchOS has three layers of observability. The CLI answers "what is running
now"; logs answer "what happened"; traces answer "what did this run call and
return". The workspace is the final evidence of what actually exists.

Assume the project path is `./workspace/project-a` below.

## 1. Where To Look

| Location | Use it for |
| --- | --- |
| Terminal / CLI progress | Current task, phase, human gate, environment preflight, tool milestones, and stage summary. |
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

The CLI emits bounded progress events, not hidden chain-of-thought. Typical
events include:

```text
[运行中] T4 · step 1 | 模型请求已提交，正在等待下一组可执行动作。
[运行中] T4 · step 1 | 正在等待模型返回下一组可执行动作；本次调用已持续 12s。
[T4 Gate1 1/6] Pass1 候选池已落盘：ideation/_pass1_forward_candidates.json
[T4 Gate1 2/6] 正在接地复核并写入 Pass2
[Environment] T9 LaTeX preflight passed: backend=latexmk
[Gate] waiting for t4_gate1_selection_gate
[Summary] T2 completed: artifacts and next stage
```

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
事件，并更新同一个 `t4_progress.md`。`running` 只表示本轮正在执行或刚提交 provider/tool，
`done` 只表示文件已经实际存在；不会为了让界面好看而预先宣布 Pass 已完成。Skill 会话也以相同
原则保存 `awaiting_llm`、`tool_running`、`tool_completed`、`waiting_runtime` 等可观察 phase。

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
| Skill `WAITING_INPUT` | A standalone Skill is missing a declared upload or its request | Run `researchos skill-status`, upload to the displayed `user_inputs/...` path, then repeat `run-skill` with `--session-id ... --resume`. No LLM call was made. |
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
