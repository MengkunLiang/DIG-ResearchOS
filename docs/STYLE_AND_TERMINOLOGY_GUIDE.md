# ResearchOS Style And Terminology Guide

> [English](STYLE_AND_TERMINOLOGY_GUIDE.md) | [中文说明](#中文说明)

This guide keeps user-facing documentation, terminal text, and Skills consistent. Write for a researcher operating a workspace, not for someone reading internal implementation details.

## Core Terms

| Preferred term | Meaning | Avoid when a clearer phrase exists |
| --- | --- | --- |
| workspace | One project's durable working directory and recovery boundary. | project folder when the distinction matters |
| stage | A named research workflow unit such as T3 or T4.5. | task in user-facing prose unless referring to a CLI command |
| Gate | A durable human decision point. | error, interruption, or approval when it is only a choice |
| Candidate | A complete, comparable research direction in T4. | idea when the stable Candidate identity matters |
| Portfolio | The selected set of Candidate Cards offered at T4 Gate1. | final ideas |
| handoff | T5's source-preserving execution contract. | prompt, brief, or instruction bundle |
| protocol readiness | Whether the compiled handoff authorizes real implementation and experiments. | handoff success alone |
| source resource | A dataset, repository, benchmark, baseline, weight, or other input before Phase B review. | experimental evidence |
| runnable asset | A deployed baseline or method under `external_executor/expr/`. | source resource |
| evidence status | The support level of a claim, note, or resource record. | quality score |

## Writing Rules

1. Lead with the researcher's outcome, then the command, path, or mechanism only when it helps them act.
2. Name the exact file path for a durable output, a blocking input, or a recovery report. Do not invent a path from a conventional name.
3. Distinguish a completed deterministic compilation from authorization to run an experiment. A T5 handoff can be compiled while protocol decisions remain pending.
4. Distinguish `proposed_not_verified` research claims from source-supported literature and discovered resources. Do not describe all project evidence with one global status.
5. Describe automatic repair or fallback as an in-progress informational state. Reserve error presentation for a failure that still requires an action or cannot be safely repaired.
6. Do not expose private prompt payloads or model reasoning. A concise cause, affected file, and next action are sufficient.
7. Do not say a PDF is "read" merely because it was downloaded or parsable. State the actual evidence level: full text, scoped/partial text, abstract-only, or metadata-only.
8. Never describe a planned metric, baseline, dataset, seed, budget, or result as established unless an audited project source explicitly supports it.

## Command Examples

Use a concrete workspace in examples:

```bash
python -m researchos.cli status --workspace ./workspace/project-a
python -m researchos.cli resume --workspace ./workspace/project-a
python -m researchos.cli validate --task T4 --workspace ./workspace/project-a
```

Explain the command category before presenting a rare stage identifier. For example, write "reopen the literature search settings" before showing `resume --from-task T2`.

## 中文说明

面向用户的中文优先使用“工作空间”“阶段”“确认关卡”“研究方向”“执行交接”“协议就绪”“源资源”“可运行资产”等自然表达；首次出现时可在括号中保留 `workspace`、`Gate`、`Candidate`、`handoff` 等英文术语。不要把内部 `schema`、`artifact`、`Agent`、`tool` 当作用户必须理解的概念。

写故障说明时必须包含三件事：实际原因、受影响的文件或范围、下一步由谁处理。可自动修补、可自动降级、只读探测未命中均应使用正常信息提示；只有需要用户补充材料、修复源码/环境或做研究决策时才显示为警告或错误。

对于 T5，必须区分“交接已经编译”“协议仍待确认”“已授权真实执行”。对 T2/T3，必须区分“已下载/可解析”“已读摘要”“已读全文或部分全文”。对 T4/T4.5，必须区分“待检验研究主张”和“已有文献证据”。
