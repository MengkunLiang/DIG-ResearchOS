# ResearchOS 中文文档导航

> [中文](../cn/README.md) | [English](../en/README.md)

这是一个维护中的英文文档集合，而非仅包含指针的参考。中文对应文档为 [../cn/README.md](../cn/README.md)；两者描述相同的代码、契约和工作空间产物。项目经过审计的工作空间仍是其持久的事实来源。

| 需求 | 从以下开始 | 然后阅读 |
| --- | --- | --- |
| 安装、初始化、运行、恢复 | [QUICKSTART.md](QUICKSTART.md) | [../../README.zh-CN.md](../../README.zh-CN.md) |
| 检查哪些工作空间处于活跃状态 | [QUICKSTART.md](QUICKSTART.md) | `workspace-status --workspace-root ./workspace` |
| 理解 T1-T9、门控、分支和产物 | [agent_pipeline.md](agent_pipeline.md) | [agent_pipeline_detail.md](agent_pipeline_detail.md) |
| 诊断日志、跟踪、Survey/T4/T5 失败 | [logging.md](logging.md) | [runtime.md](runtime.md) |
| 运行基于 DOI/arXiv/URL/主题的技能 | [skills.md](skills.md) | [QUICKSTART.md](QUICKSTART.md) |
| 配置 provider/model 或查看系统默认值 | [config.md](config.md) | `config/README.md` |
| 使用原生或 Compose 并修复 TeX | [docker.md](docker.md) | [logging.md](logging.md) |
| 理解仓库/工作空间所有权 | [project_structure.md](project_structure.md) | [runtime.md](runtime.md) |
| 扩展工具、智能体、模式、状态机或技能 | [dev.md](dev.md) | [runtime.md](runtime.md) |
| 查看部署、配置示例或维护脚本 | [../../deploy/README.md](../../deploy/README.md) | [../../config/README.md](../../config/README.md) · [../../scripts/README.md](../../scripts/README.md) |

## 操作原则

1. 同一时间仅一个写入者拥有一个工作空间。`run`、`resume`、`run-task`、技能、工具和门控均写入相同的产物/事件模型。
2. 命名的指标、基线、数据集、命令或结果需要当前项目中有可追溯的支持。AUUC/Qini 在有来源时允许使用；基于主题的协议猜测不允许。
3. `run-task` 隔离一个节点，`validate` 检查存储的产物，而 `audit-survey` 在实际修复后重新运行确定性的调查审计。
4. T4 在 Evidence-Routed `P0 -> P1` 工作流中使用模型编写的 Candidate 框架、机制、2–4 条 Draft Hypotheses、Contribution 和建议。运行时代码强制 Evidence Permission、schema、lineage、评分职责分离并呈现 Rich 公开进度；它不会用模板替代研究思路。
5. T3.6/T9 验收需要实际进行 TeX 编译。修复指定的环境问题并恢复，而非增加散文重试次数。
6. `workspace-status` 是运行概览。`state.yaml` 和 `_runtime/events` 是恢复权威来源；一个存在但已停止的进程并非活跃执行。

## 语言链接

- [中文文档地图](../cn/README.md)
- [中文详细管线指南](../cn/agent_pipeline_detail.md)
- [英文详细管线指南](agent_pipeline_detail.md)
- [文档根目录](../../README.zh-CN.md)
