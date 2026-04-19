# ResearchOS Working Agreement

## Mission
- Primary goal: build the ResearchOS runtime first.
- Try to complete as much of the current runtime as reasonably possible within the task scope, not just a superficial scaffold.
- Treat `/home/liangmengkun/reference_materials/ResearchOS_Runtime_Dev_Spec.md` as the main runtime specification.
- Treat `/home/liangmengkun/reference_materials/ResearchOS_v3_complete.md` as the broader system design reference.
- Treat `ResearchOS_v3_complete.md` as a provisional design reference rather than a frozen final spec. If parts of it are unreasonable or need adjustment to support a stronger runtime design, explain the reasoning and refine the implementation accordingly.
- If the two documents conflict, explicitly state the conflict, choose a reasonable resolution, and continue.

## Paths
- Only create, modify, or delete project code inside `/home/liangmengkun/ResearchOS`.
- Read design references from `/home/liangmengkun/reference_materials`.
- Put downloads, caches, generated artifacts, and temporary files only under `/home/liangmengkun/downloads` or `/home/liangmengkun/tmp`.
- Do not write files under `/root`.

## Environment
- Use the conda environment `/home/liangmengkun/.conda/envs/researchos`.
- Before installing Python dependencies, ensure the active environment is `researchos`.
- Prefer `conda` for foundational packages such as `numpy`, `pandas`, and similar runtime dependencies.
- Use `pip` mainly for packages that are unavailable or inconvenient in `conda`.

## Workflow
- For difficult tasks, start in plan mode before coding.
- Inspect the current repository structure before implementation.
- Read both reference documents before proposing architecture.
- First analyze the requirements and propose a runtime module breakdown.
- Build a minimal runnable runtime skeleton first, then extend modules incrementally.
- After the initial skeleton is in place, continue implementing core runtime capabilities as far as possible.
- Avoid unrelated refactors; prefer the smallest change that moves the runtime forward.
- If documentation requirements are forgotten or uncertain, review the repository and existing design materials before finalizing the task.

## Architecture Priorities
- Design the runtime to support the broader ResearchOS system, not just a narrow demo.
- Keep module boundaries explicit and extensible for future system components.
- Favor clear interfaces, strong defaults, and testable components.

## Multi-Agent Rules
- Use subagents only for bounded, non-overlapping work.
- Do not let multiple live threads modify the same files at the same time.
- Use subagents mainly for exploration, isolated implementation slices, testing, or review.
- If useful and available, split the work across multiple agents and provide a final integrated summary.

## Quality Gates
- Run the relevant build, test, lint, and type-check commands before claiming completion.
- Do not mark the work done if required checks fail.
- Add or update the minimum necessary tests for new runtime behavior.

## Documentation
- Maintain a Chinese usage document at `/home/liangmengkun/ResearchOS/README.zh-CN.md`.
- Final documentation should cover at least:
  - 项目简介
  - 环境要求
  - 安装步骤
  - 配置说明
  - 运行方式
  - 测试方式
  - 目录结构
  - 常见问题
  - 已知限制
- Final task summary should clearly include:
  - runtime architecture overview
  - implemented modules
  - how to run and test
  - known limitations and next steps

## Done When
- A runnable runtime skeleton exists.
- Core runtime modules are implemented according to the spec.
- Relevant checks pass, or any unavoidable failures are explicitly explained.
- The Chinese README is updated to match the implemented behavior and includes the required sections above.

## Git
- Target repository: `https://github.com/MengkunLiang/DIG-ResearchOS`
- Commit changes with clear messages.
- Push is allowed after checks and documentation updates are complete.
- It is acceptable to commit and push directly when the implementation is ready.
- Before finishing, summarize the implementation, verification results, and next recommended steps.

