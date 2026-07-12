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
| Docker 模式 | 固定 Python 运行环境、便于部署给普通用户、需要容器隔离但仍使用同一套 CLI | `docker compose -f deploy/compose.yaml run --rm researchos ...` |

如果你现在的目标是：

- “先把系统理解清楚、单独调某个 task”  
  优先选宿主机模式
- “想用固定镜像运行 ResearchOS，但 workspace 仍留在宿主机”
  优先选 Docker 模式

---

## 2. 宿主机模式：最快上手

### 2.1 安装

```bash
cd ResearchOS

conda create -n researchos python=3.11 -y
conda activate researchos

pip install -r requirements.txt
pip install -e .
```

`pyproject.toml` 是 Python 包元数据来源；`requirements.txt` 保持与其 runtime/dev 依赖一致，供本地 pip 与 Docker 在复制源码前缓存安装。两者包含 ResearchOS runtime、LLM 路由、PDF/BibTeX 处理、确定性综述数据图（matplotlib）和单元测试依赖。默认安装不包含 CUDA、PyTorch、WandB 或本地实验训练栈；真实实验由外部执行器或项目自定义环境负责。也可以使用 `conda env create -f environment.yml` 创建等价的本地环境。

`requirements.txt` 不包含 TeX Live。若要在宿主机真实编译 T3.6 综述或 T9 投稿包，安装系统级 TeX：

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

本地运行时 `auto` 优先使用这些本机工具；本机缺失时会回退到配置的 Docker TeX 镜像。运行
`python -m researchos.cli doctor --workspace ./workspace/local-test2` 可查看实际后端。

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
- 终端中的 Stage Start 输入/输出表和 Stage Summary Artifact Manifest
- `workspace/local-test2/_runtime/events/HELLO_single_<run-id>.jsonl`

`HELLO` 只验证 runtime、工具调用、写文件和 finish_task 的最小闭环，不会进入真实文献检索、阅读或写作流程。

想查看终端过程展示的不同密度：

```bash
python -m researchos.cli run-task HELLO \
  --workspace ./workspace/local-test2 \
  --verbosity detailed \
  --no-color
```

`normal` 是默认模式；`concise` 保留输入、结论、Artifact Manifest 和人工操作；`detailed`
增加受限的 per-query/per-paper/per-candidate 信息。`--json-events` 会把已经写入
`_runtime/events/` 的同一事件额外镜像到 stdout，适合机器集成，不建议与交互 Gate 混用。详细语义见 [logging.md](./logging.md)。

### 2.7 跑真实快速联调 `run_smoke`

开发时如果想快速暴露 T2/T3/T4 等真实 pipeline 问题，用 `run_smoke`。它会跑真实状态机，但把文献覆盖参数压小，并把状态机节点的模型层级临时降到 `medium`，适合快速看 CLI 输出、工具接线、workspace 产物和恢复逻辑。

```bash
cd ResearchOS
python -m researchos.cli run_smoke \
  --workspace ./workspace/smoke-t2 \
  --from ./workspace/local-test2 \
  --active-pool-max 20 \
  --deep-read-target 3 \
  --abstract-sweep 5 \
  --skip-startup-selftest
```

默认情况下，`run_smoke` 从 `T2` 开始，会在目标 workspace 写入：

- `literature/literature_params.json`
- `literature/literature_params_confirmation.json`

如果目标 workspace 已经有 `literature/literature_params.json`，`run_smoke` 不会覆盖；需要重写 smoke 参数时显式加 `--force-smoke-params`。这个模式用于真实快速联调，不代表最终文献覆盖质量。

### 2.8 从头跑完整 pipeline

```bash
cd ResearchOS
python -m researchos.cli run --workspace ./workspace/local-test2
```

### 2.9 恢复中断的 pipeline

```bash
cd ResearchOS
python -m researchos.cli resume --workspace ./workspace/local-test2
```

---

## 3. Docker 模式：更稳定的运行方式

### 3.1 准备部署文件

```bash
cd ResearchOS
cp .env.example .env
mkdir -p workspace
```

### 3.2 构建镜像并运行 doctor

```bash
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Compose 镜像已内置 `latexmk`、pdfLaTeX、XeLaTeX、BibTeX 和中文 TeX 包，T3.6/T9 在容器内直接编译，不依赖 Docker-in-Docker。首次构建较大；若 Linux 上 Docker bridge 下载包明显慢于宿主机，可用：

```bash
docker build --network=host -t researchos/system:latest -f infra/docker/Dockerfile .
```

### 3.3 初始化容器内 workspace

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace \
  --workspace /app/workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

### 3.4 在 Docker 中跑完整 pipeline

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/local-test2
```

### 3.5 在 Docker 中恢复

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/local-test2
```

### 3.6 在 Docker 中单独调任务

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  run-task HELLO --workspace /app/workspace/local-test2
```

### 3.7 一定要记住路径映射

Docker 模式下：

- 宿主机路径：`./workspace/local-test2`
- 容器内路径：`/app/workspace/local-test2`

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
researchos run-task T7-INGEST --workspace ./workspace/local-test2  # 外部执行器写回结果并 resume 验收后再跑
researchos run-task T7-AUDIT --workspace ./workspace/local-test2
researchos run-task T7-POST-NOVELTY --workspace ./workspace/local-test2
researchos run-task T7-CLAIMS --workspace ./workspace/local-test2
researchos run-task T7.5 --workspace ./workspace/local-test2
researchos run-task T9 --workspace ./workspace/local-test2
```

如果这些 task 已经在同一个 workspace 里落过产物，再次运行时通常会优先基于已有 artifact 继续，而不是无条件从空白开始。

T5 外部实验链路推荐按三步手动执行：

```bash
researchos run-task T5-REBOOST --workspace ./workspace/local-test2
researchos specialize-executor-skills --workspace ./workspace/local-test2
researchos run-task T5-EXECUTOR-GATE --workspace ./workspace/local-test2
```

三步含义：

- `T5-REBOOST`：调用当前 LLM provider 读取 Pre-T5 产物，生成 `external_executor/handoff_pack.json#context_reboost` 和 `external_executor/reboost_report.json`，并补全 `AGENTS.md`、`codex_prompt.md`、`expected_outputs_schema.json`、`allowed_paths.txt` 等 handoff 控制文件
- `specialize-executor-skills`：调用当前 LLM provider，读取根目录 `skills/external_executor_skills/`、handoff、schema 和来源 artifact，生成 `external_executor/project_skill_context.yaml`、`external_executor/schemas/project_skill_context.schema.json`、`external_executor/skills/` 和 `external_executor/skill_specialization_report.json`
- `T5-EXECUTOR-GATE`：选择 Codex CLI 后退出 ResearchOS，按提示在 workspace 根目录启动 Codex，并输入：`请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research-execution/SKILL.md。`

Codex 完成后必须写出 `external_executor/result_pack.json`、`external_executor/executor_status.json` 和 `external_executor/run_manifest.json`。随后回到 ResearchOS：

```bash
researchos resume --workspace ./workspace/local-test2
```

Skill suite 专属化也支持预演或检查已有 LLM 专属化产物：

```bash
researchos specialize-executor-skills --workspace ./workspace/local-test2 --dry-run
researchos specialize-executor-skills --workspace ./workspace/local-test2 --validate-only
```

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

`run --from` 不复制旧 T2 输出，只复制目标 start task 的输入；`run-task --from` 则只运行一个 task，不推进完整状态机。开发联调时如果想从 T2 开始跑真实主链但减少候选数和阅读量，优先用 `run_smoke --from`。

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

### 4.9 校验某阶段产物

```bash
researchos validate --workspace ./workspace/local-test2 --task T7-AUDIT
researchos validate --workspace ./workspace/local-test2 --task T7-INGEST
researchos validate --workspace ./workspace/local-test2 --task T7-POST-NOVELTY
researchos validate --workspace ./workspace/local-test2 --task T7-CLAIMS
```

### 4.10 查看可用 Skill 与输入契约

```bash
researchos list-skills --skills-root ./skills
researchos browse-skills --workspace ./workspace/local-test2 --skills-root ./skills
researchos describe-skill paper-outline --workspace ./workspace/local-test2
```

`list-skills` 只读取并校验根目录 `skills/*/SKILL.md`；不会调用 LLM。它会按研究流程显示卡片；
`browse-skills` 可输入序号/名称看完整功能与上传契约，输入 `run <序号>` 才开始引导会话；还支持
`搜索 文献` / `search citation`、`分类 论文写作` / `category 论文写作`、`all`、`help` 和 `q`。
`describe-skill` 是开始前的首选命令：它会展示必需/可选上传文件、每个文件应放在
workspace 的哪个相对路径、输出产物及恢复方式。

### 4.11 启动可恢复的论文 Skill

先按契约准备输入。`paper-outline` 的最小输入是：

```text
workspace/local-test2/user_inputs/paper-outline/brief.md
```

该文件至少写明问题、方法设想、已有证据、目标读者和限制。然后启动会话：

```bash
researchos run-skill paper-outline \
  "为英文实证论文建立 NeurIPS 风格大纲" \
  --workspace ./workspace/local-test2 \
  --session-id outline-v1
```

若文件缺失或格式不对，CLI 只写
`_runtime/skill_sessions/outline-v1.json` 与 `user_inputs/paper-outline/_intake.md` 并返回等待输入；这是非交互模式，不会消耗 LLM。独立 workspace 的 `_intake.md` 是上传清单。项目 workspace 会自动发现候选文件，但运行时仍会语义核验；若材料不足，会写 `user_inputs/paper-outline/_followup_request.md`，说明需要补什么及其路径。补齐后：

```bash
researchos run-skill paper-outline \
  --workspace ./workspace/local-test2 \
  --session-id outline-v1 \
  --resume

researchos skill-status --workspace ./workspace/local-test2
```

TTY 终端默认可在任务说明缺失时输入多行内容；如果必需材料也缺失，受限 intake Agent 会询问上传或粘贴，并只将人提供的内容整理到 `user_inputs/<skill>/` 后重检。材料通过后还必须明确输入“执行”才启动 Skill；以单独一行 `END` 提交任务说明：

```bash
researchos run-skill literature-evidence-scout \
  --workspace ./workspace/local-test2 \
  --session-id intro-evidence
```

常用学术 Skill 的顺序是：`research-scope`（范围）→ `paper-identifier-resolver`（DOI/arXiv/标题解析）或
`pdf-note-card`（单篇 PDF 笔记）→ `paper-comparison` / `literature-evidence-matrix`（比较与证据矩阵）→
`literature-query-plan` 或 `literature-evidence-scout`（检索）→ `paper-note-review`（回查笔记 section）→
`idea-fanout-jury` / `hypothesis-compiler`（Idea/假设）→ `experiment-design-review`
（实验设计审查）→ `paper-outline` / `paper-write`（论证和初稿）→
`claim-evidence-map` / `citation-library-curator` / `citation-provenance-audit` / `paper-peer-review` /
`paper-polish` / `paper-revision`（审阅修订）→
`paper-compile` / `submission-readiness`（真实 PDF 和投稿核对）。综述可用
`survey-visuals`：它最多只生成一张由 `survey_plan.json` 显式 taxonomy 和直接 paper-ID 链接支撑的
150 DPI vector PDF；不生成跨论文性能、相对提升、T2 筛选分数或安全热图。taxonomy 不足时写 `skipped`
manifest，不生成装饰图。每一步都可单独 `describe-skill <名称>` 查询，不需要猜文件名。

完整原子能力、输入路径和边界见 [skills.md](skills.md)。例如，单篇 PDF 先放到
`user_inputs/pdf-note-card/paper.pdf`，DOI/arXiv/标题则一行一个放到
`user_inputs/paper-identifier-resolver/identifiers.md`；两者均可由 TTY 默认 intake Agent
整理人工粘贴内容，并在同一 session 中恢复。

`paper-outline` 和 `paper-write` 会生成 `drafts/writing_storyline.md`，再按
`drafts/writing_style.json` 的 venue profile 组织论文。UTD/IS/INFORMS 强调 rationale、机制、设计知识与有边界的实践含义；NeurIPS/ICML/ICLR/KDD 强调技术瓶颈、core insight、方法和 ablation/analysis/failure evidence。profile 中的章节密度只是内部提示，投稿页数与格式仍以当前官方 CFP/template 为准。

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
- `workspace/local-test2/external_executor/project_skill_context.yaml`
- `workspace/local-test2/external_executor/schemas/project_skill_context.schema.json`
- `workspace/local-test2/external_executor/skill_specialization_report.json`
- `workspace/local-test2/external_executor/skills/research-execution/SKILL.md`
- `workspace/local-test2/external_executor/expr/MATERIALS_CHECKLIST.json`
- `workspace/local-test2/external_executor/result_pack.json`
- `workspace/local-test2/external_executor/executor_status.json`
- `workspace/local-test2/external_executor/run_manifest.json`
- `workspace/local-test2/experiments/results_summary.json`
- `workspace/local-test2/experiments/result_audit.json`
- `workspace/local-test2/experiments/method_audit.json`
- `workspace/local-test2/experiments/integrity_audit.json`
- `workspace/local-test2/experiments/evidence_index.json`
- `workspace/local-test2/novelty/post_experiment_novelty_check.json`
- `workspace/local-test2/drafts/result_to_claim.json`
- `workspace/local-test2/drafts/method_writing_resources.json`
- `workspace/local-test2/drafts/must_not_claim.md`
- `workspace/local-test2/drafts/claim_support_matrix.csv`
- `workspace/local-test2/drafts/experiment_evidence_pack.json`
- `workspace/local-test2/experiments/iteration_log.md`

### 5.5 如果 T8/T9 已经跑了

看：

- `workspace/local-test2/drafts/paper.tex`
- `workspace/local-test2/drafts/writing_style.json`
- `workspace/local-test2/drafts/writing_storyline.md`
- `workspace/local-test2/drafts/craft_audit.md`
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
