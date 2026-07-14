# 仓库、Workspace 与所有权边界

> [中文](../cn/project_structure.md) | [English](../en/project_structure.md)

ResearchOS 将受版本控制的系统代码与用户拥有的工作区分开。切勿将仓库固定装置、其他项目的工作区或模型示例视为当前项目的输入证据。

## 仓库

```text
DIG-ResearchOS/
├── researchos/                 Python runtime, agents, tools, schemas, CLI
├── config/                     Runtime and system contracts
│   ├── model_settings.yaml     本地 provider/model 设置，由 configure-llm 创建
│   ├── mcp.yaml                可选 MCP server 列表
│   └── system_config/          Runtime 默认值、Agent 契约、state machine、gate、schema
├── skills/                     Discoverable atomic and integrated public Skills
│   └── external_executor_skills/  External executor assets; separate ownership
├── prompts/                    Agent prompt templates
├── docs/                       Maintained usage and developer documentation
├── deploy/                     Docker Compose definition
├── infra/docker/               ResearchOS image and TeX environment
├── scripts/                    Maintained repository utilities
├── requirements.txt            Python dependencies
├── pyproject.toml              Package metadata and tool configuration
└── environment.yml             Conda environment definition
```

在此检出中，仓库策略忽略 `AGENTS.md`、`BACKGROUND.md`、本地 `.env`、`workspace/` 和 `tests/`。它们在本地可用（如果适用），但不是发布构件。

## 工作区

```text
workspace/<project>/
├── project.yaml                Research scope and user constraints
├── state.yaml                  Current state-machine position and gates
├── user_inputs/                Human-provided Skill intake and follow-ups
├── user_seeds/                 Optional user seed materials
├── literature/                 Retrieval, paper cards, queues, synthesis
├── ideation/                   T4 candidates, selection, hypotheses, audits
├── drafts/                     Survey/manuscript sections, claims, reviews
├── external_executor/          T5 handoff and executor return contract
├── experiments/                Ingested run evidence and claim mappings
├── submission/                 Final bundle, compile report, fingerprints
└── _runtime/                   Logs, traces, event JSONL, Skill sessions/workflow state
```

### 所有权规则

| 路径 | 写入者 | 用途 |
| --- | --- | --- |
| `project.yaml`、`user_seeds/`、`user_inputs/` | 人工引导式输入 | 范围、约束、提供的材料 |
| `literature/`、`ideation/`、`drafts/` | 验证后的 ResearchOS | 可审计的研究构件 |
| `external_executor/` | ResearchOS + 选定的外部执行器 | 交接和协议约束的返回文件 |
| `experiments/` | 结果摄取和审计工具 | 观察到的结果，而非模型猜测 |
| `_runtime/` | 仅运行时 | 操作状态；请勿编辑以更改研究结论 |

### T4 Ideation Artifact

`ideation/` 是带版本的决策记录，而不是临时目录。选择之前，它包含 `evidence/`（Evidence Index、Opportunity Map 和按 Route 划分的 Bundle）、`populations/`（`P0`、`P1` 及后续快照）、`genomes/`、`families/`、`scoring/`、`evolution/`（plan、offspring、contract、diagnostic、state 和操作结果）、`candidates/`、`archive/` 与 `human_directives/`。保留的 `_pass1_forward_candidates.json`、`_pass2_grounding_review.json`、`_candidate_directions.json`、`_gate1_candidate_cards.md` 和 `_gate1_selection_brief.md` 是供 Gate1 consumer 使用的兼容投影，不能替代 Population snapshot。

当用户选择完整 Candidate 后，`hypothesis_brief.yaml`、`selected/hypothesis_lineage.json`、`selected/t45_search_targets.json` 和 `selected/pre_novelty_brief.md` 描述的是 Pre-Novelty 研究方案。它们保存谱系并限定 T4.5 的定向审计范围，但不授权 T5 执行。只有 T4.5 audit 明确通过后，系统才会创建或更新正式的 `hypotheses.md`、`exp_plan.yaml`、`contribution_hypothesis_map.yaml`、`validation_map.yaml`、`kill_criteria.yaml` 与 `post_novelty_formalization.json`。

`literature/deep_read_notes/`、`literature/shallow_read_notes/` 和 `literature/bridge_notes/` 是唯一的 live Paper Note 根目录。deep 与 Bridge note 可以提供受已读范围约束的 full/partial-reading evidence；shallow note 只能用于 abstract-level recall。旧 `paper_notes*` 目录只由显式的 workspace migration layer 处理：迁移会记录报告，绝不把旧路径当作第二套 live source。发生同名内容冲突的旧 note 会保留在 `literature/note_migration_conflicts/` 供人工复核，而不是悄悄复制到证据根目录中造成重复。

`ideation/t4_target_profile.json` 记录研究者确认的 Publication Orientation。`ideation/final_cards/portfolio_cards.json` 只为最终 Portfolio Candidate 保存不改变科学内容、且与 profile 对齐的 Impact Translation。Candidate Dossier 与 Population snapshot 仍是科学事实来源；final card 必须原样回显其中的 thesis、contribution IDs 与 hypothesis IDs。

在提示、构件和 Skill 契约中使用相对于工作区的路径。在未记录其来源并在目标项目约束下验证之前，请勿在不同项目间复制构件。
