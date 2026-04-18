# Runtime Decisions

## Initial Notes

- Use this file to record major architectural decisions, spec conflicts, tradeoffs, and rationale.
- Prefer concise milestone-based entries over long raw notes.
# Runtime Decisions

## 2026-04-18

- 冲突: `ResearchOS_v3_complete.md` 的早期 Phase 1 只要求简单 tier routing；`ResearchOS_Runtime_Dev_Spec.md` v3.3 要求 `Endpoint + Profile + task-level override + tool parallelism`。
  - 取舍: 以后者为准，因为仓库工作约定明确指定它是主 runtime 规格。
- 冲突: 旧文档偏向 `uv` 工作流，但仓库规则要求优先使用 conda 环境 `researchos`。
  - 取舍: 项目使用标准 `pyproject.toml` 便于安装，但所有执行与验证命令显式走 `researchos` 环境。
- 范围决策: 当前仓库为空，本轮优先落地可运行 runtime、工具层、mock/test、HelloAgent、最小 state machine 与 CLI，不直接实现 9 个研究 agent 的完整业务逻辑。
- 依赖取舍: `litellm` 及其链路中的 `tiktoken` 在当前环境里会触发 Rust 编译依赖。
  - 取舍: 将 `litellm` 改为 `.[llm]` 可选依赖；默认 `.[dev]` 保证 Mock runtime、测试和文档示例可运行。真实 provider 接入时再显式安装 `.[llm]`。
