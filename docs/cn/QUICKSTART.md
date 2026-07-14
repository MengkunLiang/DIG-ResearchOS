# 快速开始与恢复

> [中文](../cn/QUICKSTART.md) | [English](../en/QUICKSTART.md)

本指南提供可复制的操作路径。首先阅读根目录下的 README，了解本地/Docker 选择及安装前提条件。

## 1. 起飞前检查

```bash
python -m researchos.cli configure-llm
python -m researchos.cli validate-config
python -m researchos.cli doctor --workspace ./workspace/project-a
python -m researchos.cli selftest
```

在进行 T3.6/T9 的 PDF 工作前，必须先运行 `doctor`。它会报告实际的本地或 Docker TeX 路径，而不仅仅是 Python 能否导入某个包。

## 2. 创建与启动

```bash
python -m researchos.cli init-workspace \
  --workspace ./workspace/project-a \
  --project-id project-a \
  --topic "your research topic"

python -m researchos.cli run --workspace ./workspace/project-a
```

终端会先显示 DIG · BUAA / ResearchOS 面板，然后显示阶段启动面板。在每个门控处，先阅读输入项、决策表、产物路径以及风险/不支持说明，再回答。`--no-banner` 仅供脚本使用；`--no-color` 用于无 ANSI 输出。

## 3. T2 文献参数

在 T2 门控处，选择一个预设配置或输入一句话。未指定的字段将保持推荐配置。

```text
候选 30 篇，精读 15 篇，摘要轻读 15 篇；英文稿，不搜索中文文献。
```

确认面板会写入 `literature/literature_params.json`。英文稿件语言本身并不排除中文文献；当此事项重要时，应明确声明收录策略。

## 4. 综述分支

T3.6 是可选的。T3.5 之后的门控首先询问是跳过综述、用当前语料库撰写，还是在综述规划前请求一次有针对性的补充。偏好设置会持久化到 `drafts/survey/decision.json` 中；这并不表示新检索到的记录已可直接用于撰写。若选择进行，顺序为：分类计划 -> 大纲/语料库门控 -> 可选的有针对性扩展计划 -> 综述状态 -> 一个确定性的分类图 -> 各章节 -> 汇编/审查 -> 实际的 TeX 编译。

唯一允许的综述图是：

```text
drafts/survey/figures/fig_taxonomy_overview.pdf
```

它仅使用明确的分类标签和直接解析出的笔记卡片链接。它不比较性能、基线、检索相关性分数或推断的风险。渲染器首选 Times New Roman，并在该字体不可用时选择有记录的学术衬线字体作为备选。

如果章节验证器暂停，先检查再进行修改：

```bash
python -m researchos.cli validate \
  --task T3.6-SEC-INTRO \
  --workspace ./workspace/project-a
```

如果文件有效，`resume` 继续。不要为同一个确定性错误重复添加验证重试。

## 5. 恢复暂停的项目

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
python -m researchos.cli workspace-status --workspace-root ./workspace
```

`workspace-status` 在一个 Rich 表格中组合显示持久的 `state.yaml`、近期的 `_runtime/events/*.jsonl` 以及一个建议性的本地进程匹配。默认扫描只保留工作空间、任务、状态、活动、事件时长和门控；添加 `--verbose` 可查看最终的 error/event 详情。只有“本地执行” (local execution) 表示此主机上仍有一个未停止的 ResearchOS 进程。状态为 `RUNNING` 但显示“已停止/已暂停” (stopped/suspended) 的情况并非活跃工作：在选择 `resume` 之前，请检查工作区和终端作业。进程信息仅为建议性；状态文件和持久事件才是恢复的可靠依据。

`status` 默认显示简洁的项目摘要：当前步骤、状态、待定决策、最新可操作消息和下一个命令。仅在调试需要完整的原始 `state.yaml` 时使用 `status --detail`。

## 命令索引

| 命令 | 用途 | 常用形式 |
| --- | --- | --- |
| `init-workspace` | 创建项目工作区和基线输入 | `init-workspace --workspace <dir> --project-id <id> --topic <topic>` |
| `run` | 运行完整流水线；可选择从其他项目复用已验证的前提条件 | `run --workspace <dir>`; `run --workspace <new> --from <source> --start-task T4` |
| `run_smoke` | 运行一个真实工具的冒烟工作流 | `run_smoke --workspace <dir>` |
| `resume` | 继续已暂停的项目 | `resume --workspace <dir>`; 使用 `--from-task <task>` 进行同一工作区的有目的重新进入 |
| `run-task` | 诊断或执行单个任务而不推进主流水线 | `run-task T4 --workspace <dir>` |
| `status` / `workspace-status` | 检查单个项目或工作区根目录；`status --detail` 打印原始状态 | `status --workspace <dir>`; `workspace-status --workspace-root ./workspace` |
| `configure-llm` / `selftest` | 配置并检查所有阶段共用的 provider/model connection | `configure-llm`; `selftest` |
| `doctor` | 检查本地/Docker/TeX 依赖 | `doctor --workspace <dir>` |
| `trace` / `validate` | 检查有边界的运行摘要或验证已存储的任务结果 | `trace <run-id> --workspace <dir>`; `validate --task T4 --workspace <dir>` |
| `audit-survey` | 重建确定性的综述覆盖率审计 | `audit-survey --workspace <dir>` |
| `validate-config` | 检查状态机、门控、路由和运行时配置 | `validate-config` |
| `specialize-executor-skills` | 生成或验证项目特定的 T5 executor Skill 套件 | `specialize-executor-skills --workspace <dir> --deterministic` |
| `list-skills` / `browse-skills` / `describe-skill` | 发现 Skill 并检查其契约 | `describe-skill <skill> --workspace <dir>` |
| `run-skill` / `skill-status` | 启动/继续一个独立的 Skill 会话或检查会话状态 | `run-skill <skill> --workspace <dir> --session-id <id> --resume` |

典型暂停处理：

| 暂停情况 | 修复方法 | 继续方式 |
| --- | --- | --- |
| 人工门控 | 阅读终端决策界面并做出选择 | 输入后 `resume` 自动进行，或重新运行 `resume` |
| Skill 缺少材料 | 添加/回答所请求的 `user_inputs/<skill>/...` 文件 | `run-skill ... --session-id <id> --resume` |
| 提供方故障 | 检查 `model_settings.yaml` 或 `.env`，确认连接正确后等待服务恢复 | `resume` |
| TeX 环境 | 运行 `doctor`，安装主机 TeX 或构建 Docker 镜像 | `resume` |
| 验证错误 | 运行 `validate --task <task>`，修复指定的产物 | `resume` |
| 外部 executor 等待 | 写出所声明的 executor 结果包 | `resume` |

## 6. 调试单个阶段

`run-task` 不会推进整个流水线：

```bash
python -m researchos.cli run-task T3 --workspace ./workspace/project-a
python -m researchos.cli run-task T3.6-SEC-INTRO --workspace ./workspace/project-a
python -m researchos.cli run-task T9 --workspace ./workspace/project-a
```

修复产物后使用 `validate`。使用 `trace <run-id>` 查看先前运行的有限人工渲染摘要，并检查 `_runtime/logs/researchos.log` 以获取详细的操作时间线。

对于 T4，模型会基于 Workspace 证据撰写 Candidate 框架、机制、2–4 条 Draft Hypotheses、Contribution、评分解释和面向研究者的 Portfolio 文案。Standard mode 完成完整的 `P0 -> P1` Evolution Round，而不是只改写一次文本。Rich 面板会展示 Evidence Routing、Opportunity Map、Multi-route Generation、Independent Scoring、Evolution Planning、Offspring & Rescoring 和 Survival & Portfolio，不显示原始 JSON 或隐藏推理。provider 调用进行中时，终端会在 12 秒后显示低频 Live Runtime 面板，此后每 30 秒刷新一次。

## 7. T5 Executor 技能与恢复

T5 现在将项目特定的 executor 技能套件作为其正常 reboost 编译器事务的一部分发布。有效的 reboost 在进入 executor 选择前，会写入交接文件、预期的输出契约、`external_executor/project_skill_context.yaml`、`external_executor/skill_specialization_report.json` 以及全部 13 个 `external_executor/skills/*/SKILL.md` 文件。报告记录了确定性编译器及其验证；LLM 特化是可选的优化，而非隐藏的前提条件。

对于由旧版本创建、且已在 `T5-EXTERNAL-WAIT` 状态下暂停但没有 `external_executor/skills/` 的工作区，可在不调用模型的情况下发布相同的可审计套件，然后验证 executor 门控：

```bash
python -m researchos.cli specialize-executor-skills \
  --workspace ./workspace/project-a --deterministic

python -m researchos.cli validate --task T5-EXECUTOR-GATE \
  --workspace ./workspace/project-a

# Validate the published context, report, and 13 Skills without calling an LLM.
python -m researchos.cli specialize-executor-skills \
  --workspace ./workspace/project-a --validate-only
```

要从正常的流水线入口同时重建 reboost 交接和套件，请使用受支持的状态重新进入命令，而非编辑 `state.yaml`：

```bash
python -m researchos.cli resume --workspace ./workspace/project-a \
  --from-task T5-REBOOST-GATE
```

## 8. 启动引导式 Skill

```bash
python -m researchos.cli browse-skills --workspace ./workspace/project-a
python -m researchos.cli run-skill pdf-note-card --workspace ./workspace/project-a
```

TTY 输入收集材料并要求明确执行。自动化必须是明确的：

```bash
python -m researchos.cli run-skill pdf-note-card \
  --workspace ./workspace/project-a \
  --non-interactive
```

在非交互模式下，缺少材料会生成 `WAITING_INPUT` 且不会初始化任何 LLM 提供方。参见 [skills.md](skills.md)。

对于完整的领域或审阅工作流，使用集成 Skill。它首先询问是否可以搜索缺失的文献，在同一次会话中记录可见的子阶段，并在进入综述准备或假设选择前再次询问：

```bash
python -m researchos.cli run-skill domain-synthesis-studio \
  "综合此领域；先检查语料，不足时询问是否定向检索，再决定是否准备 Survey" \
  --workspace ./workspace/project-a --session-id field-review

python -m researchos.cli skill-status --workspace ./workspace/project-a
```

`skill-status` 面板显示活跃的集成阶段、已完成的产物、证据边界以及确切的同会话恢复命令。

## 9. 谨慎复用其他项目

创建新的目标工作区，且仅通过受支持的初始化路径引用来源：

```bash
python -m researchos.cli run \
  --workspace ./workspace/project-b \
  --from ./workspace/project-a \
  --start-task T4
```

目标保留其自己的状态、门控、日志和输出产物。在复用文献、声明或协议细节之前，确认其来源。

## 10. 笔记卡片选择与再访

T2 的选择并非删除。身份已验证的记录仍保留在 `papers_verified.jsonl`、`papers_backlog.jsonl` 和 `deep_read_queue.jsonl` 中；`triaged_out` 仅意味着某条记录不消耗当前的深度阅读目标。T3 优先处理待处理队列，并可能在摘要扫描中使用可读的积压材料，而不会将其升级为全文证据。

T4 会先索引已有的主线和 Bridge 论文阅读笔记，再形成 Opportunity Map。全文/部分全文笔记可以在已读范围内锚定机制或设计理由；摘要层笔记也会参与召回、taxonomy、Bridge 发现、候选机制和升级阅读请求，但不能确认机制或强 Claim。Controller 会为不同 Route 构建不同的 Evidence Bundle，而不是向每个 Route 输入同一份冗长上下文。来源路径、阅读等级、不确定性和升级阅读要求会保存在 `ideation/evidence/`；未被当前 Candidate 使用的笔记不会被删除。尚未形成阅读笔记的已核验/积压记录，在完成适当深度的阅读前仍只是 metadata/abstract 层线索。
