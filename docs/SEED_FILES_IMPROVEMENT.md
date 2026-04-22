# Seed Files 改进方案

## 改进概述

本次改进优化了 ResearchOS 的种子数据管理流程，使用户能够更方便地提供种子论文和其他初始数据。

## 改进内容

### 1. 自动 PDF 扫描

**改进前**：
- 用户需要在 T1 对话中手动提供 PDF 路径
- 容易遗漏已放入目录的 PDF 文件
- 用户体验不够友好

**改进后**：
- T1 Agent 在第2轮对话后自动扫描 `user_seeds/pdfs/` 目录
- 发现 PDF 文件后主动询问用户是否使用
- 支持批量处理多个 PDF 文件
- 用户只需将 PDF 放入目录，无需手动提供路径

**实现位置**：
- `researchos/prompts/pi.j2` - 添加了自动扫描 PDF 的流程说明
- `researchos/runtime/workspace.py` - 更新了 README 说明推荐使用方式

### 2. 简化 seed_ensemble 配置

**改进前**：
- 用户可能误解 `seed_ensemble` 的含义（以为是论文信息）
- 容易将论文信息错误地放入 `seed_ensemble` 字段

**改进后**：
- 明确说明 `seed_ensemble` 是实验随机种子配置
- 提供默认值：`{tier1_seeds: [42, 123, 456], tier2_seeds: [789], tier3_seeds: [999]}`
- 用户通常不需要修改，直接使用默认值
- 在 prompt 中强调"只包含整数数组，不包含论文信息"

**实现位置**：
- `researchos/prompts/pi.j2` - 添加了详细的 seed_ensemble 说明

### 3. 完善 seed 文件使用说明

**改进前**：
- 用户不清楚各个 seed 文件的用途和使用时机
- README 中缺少使用阶段的说明

**改进后**：
- 在 `user_seeds/README.md` 中添加了"各文件的使用阶段"表格
- 明确说明每个文件在哪个 Agent 中使用
- 添加了用途说明

**文件使用阶段**：

| 文件 | 使用阶段 | 用途 |
|------|---------|------|
| `seed_papers.jsonl` | T1 生成，T2 使用 | 种子论文列表 |
| `seed_ideas.md` | T4 Ideation Agent | 作为候选研究方向 |
| `seed_constraints.md` | T2 Scout Agent | 文献检索约束 |
| `seed_external_resources.jsonl` | T5+ | 外部资源清单 |
| `pdfs/` | T1 自动扫描 | 存放 PDF 文件 |

## 使用方式

### 推荐工作流程

1. **初始化 workspace**：
   ```bash
   researchos init-workspace ./my-project
   ```

2. **放入 PDF 文件**（可选）：
   ```bash
   cp paper1.pdf paper2.pdf ./my-project/user_seeds/pdfs/
   ```

3. **运行 T1 Agent**：
   ```bash
   researchos run-task T1 --workspace ./my-project
   ```

4. **T1 自动流程**：
   - 第1轮：询问研究边界与约束
   - 第2轮：询问已有基础
   - **自动扫描 `pdfs/` 目录**
   - 询问是否使用发现的 PDF 文件
   - 第2.5轮：询问外部资源
   - 第3轮：确认并生成配置文件

### 提供种子论文的方式

**方式 1：直接放入 PDF（推荐）**
- 将 PDF 文件放入 `user_seeds/pdfs/` 目录
- T1 会自动识别并询问

**方式 2：在对话中提供 arXiv ID**
- 示例：`2601.03192`

**方式 3：在对话中提供 DOI**
- 示例：`10.1145/3534678.3539147`

**方式 4：手动编辑 seed_papers.jsonl**
- 复制 `seed_papers.jsonl.example` 为 `seed_papers.jsonl`
- 按照示例格式填写

## 技术细节

### seed_ensemble 的作用

`seed_ensemble` 是实验的随机种子配置，用于确保实验可复现性：

- `tier1_seeds`：主实验随机种子（headline results）
- `tier2_seeds`：消融实验随机种子（ablation studies）
- `tier3_seeds`：快速测试随机种子（quick tests）

**重要**：这与"种子论文"（seed papers）是完全不同的概念：
- **seed_ensemble**：整数数组，用于实验可复现性
- **seed_papers.jsonl**：论文信息，用于文献检索的起点

### 自动扫描实现

T1 Agent 在第2轮对话后执行以下步骤：

1. 使用 `list_files` 工具扫描 `user_seeds/pdfs/` 目录
2. 如果发现 PDF 文件：
   - 列出所有文件名
   - 使用 `ask_human` 询问用户
   - 用户确认后，逐个使用 `process_seed_paper` 处理
3. 如果没有发现 PDF：
   - 询问是否需要提供其他方式的种子论文

## 测试验证

测试脚本：`/home/liangmengkun/tmp/test_pdf_scan.py`

测试结果：
- ✅ T1 Agent 正确扫描 `user_seeds/pdfs/` 目录
- ✅ 正确识别 PDF 文件并询问用户
- ✅ 生成的 `project.yaml` 格式正确
- ✅ `seed_ensemble` 使用默认值（整数数组）

## 向后兼容性

本次改进完全向后兼容：
- 旧的工作流程（在对话中提供路径）仍然有效
- 手动编辑 `seed_papers.jsonl` 仍然有效
- 不影响现有的 workspace 和配置文件

## 未来改进方向

1. **PDF 元数据提取增强**：
   - 自动识别 PDF 中的 DOI
   - 自动提取标题、作者、年份
   - 支持更多 PDF 格式

2. **批量处理优化**：
   - 并行处理多个 PDF 文件
   - 显示处理进度

3. **智能推荐**：
   - 根据 PDF 内容推荐相关论文
   - 自动生成初步的研究方向

## 相关文件

- `researchos/runtime/workspace.py` - workspace 初始化和 README 生成
- `researchos/prompts/pi.j2` - T1 Agent prompt 模板
- `researchos/agents/pi.py` - T1 Agent 实现
- `docs/agents/T1_PI_AGENT.md` - T1 Agent 文档

## 更新日期

2026-04-22
