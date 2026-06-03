# Tool 与 LLM 边界

ResearchOS 的原则不是“所有东西都工具化”，也不是“所有东西都靠 prompt”。正确边界是：

- 机械、重复、可验证、无需学科知识的步骤做成 tool。
- 需要学术判断、领域知识、贡献判断、baseline fairness 判断的步骤交给 LLM/人工。
- LLM 判断必须落盘为 plan/review/annotation artifact，再由 tool 合并、校验、索引。

## 推荐三段式

```text
deterministic skeleton
 -> LLM semantic plan / annotations
 -> deterministic merge + validation
```

示例：

- `domain_graph_skeleton.json`：工具构建引用/metadata 骨架。
- `domain_map_plan.json`：LLM 判断 paper role、edge role、transfer plausibility。
- `domain_map.json`：工具把 skeleton + plan 合并并校验 provenance。

## 已落地的边界

- `T5-HANDOFF`：工具抽取 metrics、seeds、required baseline 和 artifact hash；不判断 scientific fairness。
- `T5-EXECUTOR-GATE`：状态机 gate 选择执行器，不调用 LLM。
- `T5-EXTERNAL-WAIT`：runtime 检查 result pack/status，缺失则暂停，不让 LLM 猜进度。
- `T7-AUDIT`：工具检查 provenance、hash、mock flag 和 baseline coverage；公平性解释写入 review scaffold。
- `T7-CLAIMS`：工具生成 claim strength、must-not-claim 和 support matrix；最终论文 wording 仍由 Writer/Reviewer 基于这些 artifact 判断。
- `T8-PAPER-CLAIM-AUDIT`：工具检查 paper claim 与 evidence pack，不替代审稿人的科学判断。

## 不应做的事

- 不要在工具内部隐藏调用 LLM。
- 不要用硬编码知识替代 LLM 的领域判断。
- 不要让 validator 事后承担所有写作规范；prompt、artifact plan、writer workflow 都要提前约束。
- 不要让 mock/dry-run 结果在 T8 被写成真实实验证据。
