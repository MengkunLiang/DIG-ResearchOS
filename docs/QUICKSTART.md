# ResearchOS Quick Start

这份文档只做一件事：**让你尽快把系统跑起来，并且知道下一步该看什么。**

如果你需要更详细的说明：

- 系统流程总览： [agent_pipeline.md](./agent_pipeline.md)
- Runtime 机制： [runtime.md](./runtime.md)
- 配置说明： [config.md](./config.md)
- Docker： [docker.md](./docker.md)
- 开发者调试： [dev.md](./dev.md)

---

## 1. 先决定你要哪种运行方式

ResearchOS 当前有两种主用法：

| 模式 | 适用场景 | 典型命令 |
| --- | --- | --- |
| 宿主机模式 | 本地开发、单阶段调试、改 prompt / 改 validator | `python -m researchos.cli ...` |
| Docker 模式 | T9 编译、legacy 内部实验调试、外部 executor 自行需要的隔离环境 | `bash infra/docker/run.sh ...` |

如果你现在的目标是：

- “先把系统理解清楚、单独调某个 task”  
  优先选宿主机模式
- “尽量减少环境干扰、尤其是实验和 LaTeX 编译”  
  优先选 Docker 模式

---

## 2. 宿主机模式：最快上手

### 2.1 安装

```bash
cd ResearchOS

conda create -n researchos python=3.11 -y
conda activate researchos

pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .
```

如果你还需要额外 PDF 处理能力：

```bash
pip install -r requirements-optional-pdf.txt
```

### 2.2 配置 `.env`

```bash
cd ResearchOS
cp .env.example .env
```

至少建议补这些：

```bash
SILICONFLOW_API_KEY=...
OPENROUTER_API_KEY=...
OPENAI_API_KEY=...
S2_API_KEY=...
RESEARCHER_EMAIL=your@email.com
```

### 2.3 校验配置

```bash
cd ResearchOS
python -m researchos.cli validate-config
```

预期输出里应包含：

```text
ok: true
```

### 2.4 跑 provider 自检

```bash
cd ResearchOS
python -m researchos.cli selftest
```

看点：

- SiliconFlow 是否可用
- OpenRouter / OpenAI 是否可用
- latency 是否异常高
- `pdfplumber` 这类关键 PDF 解析依赖是否就绪（影响 T3 / T9）

### 2.5 创建一个 workspace

```bash
cd ResearchOS
python -m researchos.cli init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 2.6 先跑最小 smoke test

```bash
cd ResearchOS
python -m researchos.cli run-task HELLO --workspace ./workspace/local-test2
```

成功后应看到：

- `workspace/local-test2/hello.txt`

### 2.7 从头跑完整 pipeline

```bash
cd ResearchOS
python -m researchos.cli run --workspace ./workspace/local-test2
```

### 2.8 恢复中断的 pipeline

```bash
cd ResearchOS
python -m researchos.cli resume --workspace ./workspace/local-test2
```

---

## 3. Docker 模式：更稳定的运行方式

### 3.1 构建镜像

```bash
cd ResearchOS
bash infra/docker/build.sh
```

### 3.2 运行自检

```bash
cd ResearchOS
bash infra/docker/run.sh selftest
```

### 3.3 初始化容器内 workspace

```bash
cd ResearchOS
bash infra/docker/run.sh init-workspace \
  --workspace /workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 3.4 在 Docker 中跑完整 pipeline

```bash
cd ResearchOS
bash infra/docker/run.sh run --workspace /workspace/local-test2
```

### 3.5 在 Docker 中恢复

```bash
cd ResearchOS
bash infra/docker/run.sh resume --workspace /workspace/local-test2
```

### 3.6 在 Docker 中单独调 T9

```bash
cd ResearchOS
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

### 3.7 一定要记住路径映射

Docker 模式下：

- 宿主机路径：`./workspace/local-test2`
- 容器内路径：`/workspace/local-test2`

它们指向的是同一份 workspace。

---

## 4. 最常用命令，一次看全

### 4.1 初始化 workspace

```bash
researchos init-workspace \
  --workspace ./workspace/local-test2 \
  --project-id local-test2 \
  --topic "reflective memory for llm agents"
```

### 4.2 运行完整链路

```bash
researchos run --workspace ./workspace/local-test2
```

### 4.3 恢复完整链路

```bash
researchos resume --workspace ./workspace/local-test2
```

### 4.4 单独跑一个阶段

```bash
researchos run-task T2 --workspace ./workspace/local-test2
researchos run-task T3 --workspace ./workspace/local-test2
researchos run-task T5-HANDOFF --workspace ./workspace/local-test2
researchos run-task T5-EXECUTOR-GATE --workspace ./workspace/local-test2
researchos run-task T5-DRY-RUN --workspace ./workspace/local-test2
researchos run-task T7-INGEST --workspace ./workspace/local-test2  # 已有 dry-run 或 T5-EXTERNAL-WAIT 验收结果后再跑
researchos run-task T7-AUDIT --workspace ./workspace/local-test2
researchos run-task T7-CLAIMS --workspace ./workspace/local-test2
researchos run-task T7.5 --workspace ./workspace/local-test2
researchos run-task T9 --workspace ./workspace/local-test2
```

如果这些 task 已经在同一个 workspace 里落过产物，再次运行时通常会优先基于已有 artifact 继续，而不是无条件从空白开始。

### 4.5 从其他 workspace 复制前置产物

```bash
researchos run-task T8-RESOURCE \
  --workspace ./workspace/scratch-write \
  --from ./workspace/local-test2
```

如果想从另一个 workspace 继承 T1/seed，并从 T2 重新跑完整后续流程：

```bash
researchos run \
  --workspace ./workspace/new-test5-t2-redo \
  --from ./workspace/new-test5 \
  --start-task T2
```

`run --from` 不复制旧 T2 输出，只复制目标 start task 的输入；`run-task --from` 则只运行一个 task，不推进完整状态机。

如果 T2 结果可信、只想从 T3 重新阅读：

```bash
researchos run \
  --workspace ./workspace/new-test5-t3-redo \
  --from ./workspace/new-test5 \
  --start-task T3
```

### 4.6 使用综述种子提纲

```bash
cp /mnt/data/reference/算法风险综述_种子提纲.md \
  ./workspace/algorithm-risk-survey/user_seeds/算法风险综述_种子提纲.md
```

系统会生成 `user_seeds/seed_outline_profile.json`，并把提纲中的框架、关键词和代表性方向
用于 T2 检索覆盖、T3 阅读维度和 T3.6 综述 taxonomy。`representative_literature_directions`
只是 query/taxonomy prior，不是 citation，也不会被写入 `seed_papers.jsonl`。

### 4.7 查看状态

```bash
researchos status --workspace ./workspace/local-test2
```

### 4.8 查看 trace

```bash
researchos trace T7_single_12345678 --workspace ./workspace/local-test2
researchos trace T7_single_12345678 --workspace ./workspace/local-test2 --raw
```

### 4.8 校验某阶段产物

```bash
researchos validate --workspace ./workspace/local-test2 --task T7-AUDIT
researchos validate --workspace ./workspace/local-test2 --task T7-CLAIMS
```

### 4.9 列出 skills

```bash
researchos list-skills --skills-root ./skills
```

### 4.10 运行 skill

```bash
researchos run-skill deepxiv "summarize recent memory papers for llm agents"
```

---

## 5. 第一次跑时，你应该看哪些文件

### 5.1 总状态

先看：

- `workspace/local-test2/state.yaml`
- `workspace/local-test2/_runtime/logs/researchos.log`

### 5.2 如果 T2 已经跑了

看：

- `workspace/local-test2/literature/papers_raw.jsonl`
- `workspace/local-test2/literature/papers_dedup.jsonl`
- `workspace/local-test2/literature/papers_verified.jsonl`
- `workspace/local-test2/literature/deep_read_queue.jsonl`
- `workspace/local-test2/literature/access_audit.md`

### 5.3 如果 T3 已经跑了

看：

- `workspace/local-test2/literature/paper_notes/`
- `workspace/local-test2/literature/comparison_table.csv`
- `workspace/local-test2/literature/related_work.bib`
- `workspace/local-test2/literature/deep_read_queue_pending.jsonl`

每篇 `paper_notes/*.md` 还应该包含 `## 12. Reading Coverage`。如果 note 标为 `[FULL-TEXT]`，重点检查：

- `Pages read` 是否覆盖完整页码，例如 `1-12 / 12` 或 `1-4, 5-8, 9-12 / 12`
- `Truncation` 是否明确最终无截断；如果初次调用被截断，必须说明已通过分块重读解决
- 如果 PDF 可得但只读了部分页，应标为 `[PARTIAL-TEXT]`，不能标为 `[FULL-TEXT]`

### 5.4 如果外部实验链已经跑了

看：

- `workspace/local-test2/external_executor/handoff_pack.json`
- `workspace/local-test2/external_executor/result_pack.json`
- `workspace/local-test2/experiments/results_summary.json`
- `workspace/local-test2/experiments/integrity_audit.json`
- `workspace/local-test2/drafts/result_to_claim.json`
- `workspace/local-test2/drafts/experiment_evidence_pack.json`
- `workspace/local-test2/experiments/iteration_log.md`

### 5.5 如果 T8/T9 已经跑了

看：

- `workspace/local-test2/drafts/paper.tex`
- `workspace/local-test2/drafts/review_rounds/`
- `workspace/local-test2/submission/bundle/`
- `workspace/local-test2/submission/migration_report.md`

---

## 6. 三个最实用的起手式

### 起手式 A：我只是想确认系统能跑

```bash
cd ResearchOS
python -m researchos.cli validate-config
python -m researchos.cli selftest
python -m researchos.cli run-task HELLO --workspace ./workspace/local-test2
```

### 起手式 B：我想调某个阶段

```bash
cd ResearchOS
python -m researchos.cli run-task T3 --workspace ./workspace/local-test2
```

### 起手式 C：我想继续之前中断的项目

```bash
cd ResearchOS
python -m researchos.cli resume --workspace ./workspace/local-test2
```

---

## 7. 常见问题

### 7.1 为什么 `researchos` 和 `python -m researchos.cli` 表现不一致？

通常是环境错配。优先用：

```bash
PYTHONPATH=. python -m researchos.cli ...
```

并重新：

```bash
pip install -e .
```

### 7.2 为什么中断后看起来“从头开始”？

先确认三件事：

1. 用的是不是同一个 workspace
2. 中断前关键 artifact 有没有真的落盘
3. 对应 task 有没有恢复逻辑

最稳的判断方式是直接看这些目录和文件是否还在：

- `literature/paper_notes/`
- `external_executor/`
- `experiments/`
- `drafts/`
- `submission/`
- `_runtime/resume/`

旧 workspace 可能还有 `pilot/`；它只用于显式 legacy 内部实验调试，新主链不依赖它。

### 7.3 为什么 `run-task` 不能自动接着跑到下一个阶段？

因为 `run-task` 的语义就是“只跑当前这个 task”。  
想测完整状态机，应该用：

```bash
researchos run --workspace ./workspace/local-test2
researchos resume --workspace ./workspace/local-test2
```

---

## 8. 接下来该读什么

- 想知道每个 Agent 的输入输出和内部逻辑： [agent_pipeline.md](./agent_pipeline.md)
- 想知道 runtime、tool、MCP、skills 怎么接： [runtime.md](./runtime.md)
- 想知道所有配置项： [config.md](./config.md)
- 想做本地开发和调试： [dev.md](./dev.md)
- 想看日志和 trace： [logging.md](./logging.md)
