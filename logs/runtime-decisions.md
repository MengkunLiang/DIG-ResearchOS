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

## 2026-04-19

- 评估发现: 当前runtime完成度75-80%，架构优秀但存在15个P0阻塞问题。
  - 决策: 优先修复P0问题，确保runtime能支撑后续9个agent开发。
- 缺失文件补全: 创建pydantic_compat.py、agents/_common.py、schemas/validator.py。
  - 理由: 这些是agent开发的基础设施，必须先就位。
- Schema体系: 采用JSON Schema Draft 7标准，支持可选的jsonschema库校验。
  - 理由: 如果环境没有jsonschema，validator会优雅降级，不阻塞基本功能。
- 修复策略: 使用后台agent并行处理P0修复，主线程处理文档和配置。
  - 理由: 加速修复进度，预计2-3天完成所有P0问题。
