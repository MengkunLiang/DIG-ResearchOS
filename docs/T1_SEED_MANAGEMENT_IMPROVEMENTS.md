# T1 Agent 种子文献管理改进总结

## 最新更新
2026-04-22 晚：修复欢迎信息显示和 write_structured_file 参数问题

## 改进日期
2026-04-22

## 问题背景

用户在使用 T1 Agent 时遇到以下问题：

1. **seed_papers.jsonl 为空**
   - 用户放了 PDF 文件在 `user_seeds/pdfs/memrl.pdf`
   - 用户在对话中提供了 arXiv ID `2602.08234`
   - `process_seed_paper` 工具成功获取了论文信息
   - 但 `seed_papers.jsonl` 文件仍然是空的（0 字节）

2. **project.yaml 格式错误**
   - `seed_ensemble` 字段包含了论文信息（title, authors, source 等）
   - 应该只包含整数数组（tier1_seeds, tier2_seeds, tier3_seeds）

3. **Agent 没有自动扫描 PDF**
   - 用户把 PDF 放在 `user_seeds/pdfs/` 目录
   - Agent 没有主动发现和询问
   - 需要用户在对话中说"我已经上传了"

4. **缺少用户引导**
   - init 之后没有提示用户可以准备种子数据
   - 用户不知道推荐的使用方式

## 根本原因分析

### 问题 1：seed_papers.jsonl 为空

**原因**：
- `process_seed_paper` 工具只负责提取元数据并返回
- **没有自动写入** `seed_papers.jsonl` 文件
- 需要 Agent 手动调用 `write_file` 来写入，但 Agent 没有这样做

**证据**（从 trace 日志）：
```json
{
  "name": "process_seed_paper",
  "arguments": {"source": "arxiv_id", "value": "2602.08234"},
  "result": {
    "ok": true,
    "content": "✅ 成功从 arXiv 获取论文信息...",
    "data": {"paper": {...}}
  }
}
```
- 工具返回了论文信息，但没有写入文件

### 问题 2：seed_ensemble 格式错误

**原因**：
- Agent 使用了旧版本的 prompt（在我们更新之前启动）
- 旧 prompt 没有明确说明 `seed_ensemble` 的格式
- Agent 误解了含义，把论文信息放入了 `seed_ensemble`

**错误示例**：
```yaml
seed_ensemble:
  - source: arxiv_id
    value: "2602.08234"
    title: "SkillRL: ..."
```

**正确格式**：
```yaml
seed_ensemble:
  tier1_seeds: [42, 123, 456]
  tier2_seeds: [789]
  tier3_seeds: [999]
```

### 问题 3：没有自动扫描

**原因**：
- Agent 使用了旧版本的 prompt
- 新版本 prompt 包含自动扫描逻辑，但当时 Agent 还没重启

### 问题 4：缺少用户引导

**原因**：
- prompt 中没有欢迎信息和准备指南
- 用户不知道推荐的使用方式

## 解决方案

### 修复 1：process_seed_paper 自动写入文件

**文件**：`researchos/tools/seed_paper_processor.py`

**改动**：
1. 添加 `_append_to_seed_papers()` 方法
2. 在所有处理方法中自动追加到 `seed_papers.jsonl`
3. 支持 PDF、arXiv ID、DOI、标题等所有来源

**代码**：
```python
async def _append_to_seed_papers(self, paper_info: dict[str, Any]) -> None:
    """将论文信息追加到 seed_papers.jsonl 文件。"""
    seed_papers_path = self.policy.workspace_dir / "user_seeds" / "seed_papers.jsonl"
    seed_papers_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(seed_papers_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(paper_info, ensure_ascii=False) + "\n")
```

**测试结果**：
```bash
✅ 成功从 arXiv 获取论文信息
   arXiv ID: 2602.08234
   标题: SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning
   已追加到: user_seeds/seed_papers.jsonl

✅ seed_papers.jsonl 包含 2 篇论文:
  1. SkillRL: Evolving Agents via Recursive Skill-Augmented Reinf...
  2. MemRL: Self-Evolving Agents via Runtime Reinforcement Learni...
```

### 修复 2：更新 prompt 明确 seed_ensemble 格式

**文件**：`researchos/prompts/pi.j2`

**改动**：
1. 在顶部添加 "🚨🚨🚨 CRITICAL: 避免常见错误" 部分
2. 明确说明 seed_ensemble 只包含整数数组
3. 提供正确和错误的示例对比

**关键内容**：
```
❌ 错误 2：在 seed_ensemble 中包含论文信息或项目描述
- 错误示例：`seed_ensemble: {topic: "...", seed_papers: [...]}`
- 正确示例：`seed_ensemble: {tier1_seeds: [42, 123, 456], tier2_seeds: [789], tier3_seeds: [999]}`
- seed_ensemble 只包含整数数组，不包含任何其他信息

🎯 关于 seed_ensemble 的说明：
- `seed_ensemble` 是实验的随机种子配置，用于确保实验可复现
- **不是**论文信息，论文信息应该写入 `seed_papers.jsonl`
- 默认值：`{tier1_seeds: [42, 123, 456], tier2_seeds: [789], tier3_seeds: [999]}`
- 用户通常不需要修改，直接使用默认值即可
```

### 修复 3：添加自动扫描流程

**文件**：`researchos/prompts/pi.j2`

**改动**：
1. 在第2轮对话后添加自动扫描步骤
2. 使用 `list_files` 工具扫描 `user_seeds/pdfs/` 目录
3. 主动询问用户是否使用发现的 PDF

**关键流程**：
```
**⚠️ 重要流程（第2轮对话后立即执行）**：
1. **立即扫描 `user_seeds/pdfs/` 目录**（使用 `list_files` 工具）
2. 如果发现 PDF 文件：
   - 使用 `ask_human` 询问："我发现了 X 篇 PDF 文件：[文件名列表]。是否将这些作为种子论文？"
   - 用户确认后，逐个使用 `process_seed_paper` 处理
3. 如果没有发现 PDF 文件：
   - 继续询问用户是否需要提供其他方式的种子论文
```

### 修复 4：添加欢迎信息和用户引导

**文件**：`researchos/prompts/pi.j2`

**改动**：
1. 添加第0轮欢迎信息
2. 展示种子数据准备指南
3. 说明推荐的使用方式

**欢迎信息**：
```
欢迎使用 ResearchOS！我是 PI Agent，将帮助你初始化研究项目。

在开始之前，你可以准备以下种子数据（可选）：

📄 **种子论文**（推荐方式）：
   - 将 PDF 文件放入 `user_seeds/pdfs/` 目录
   - 我会自动扫描并识别
   - 或者在对话中提供 arXiv ID / DOI

💡 **初步想法**：
   - 如果有初步的研究想法，可以写入 `user_seeds/seed_ideas.md`
   - 或在对话中告诉我

⚠️ **硬约束**：
   - 如果有必须遵守的技术或方法约束，可以写入 `user_seeds/seed_constraints.md`
   - 或在对话中告诉我

现在让我们开始三轮对话来明确你的研究方向！
```

### 修复 5：更新 workspace README

**文件**：`researchos/runtime/workspace.py`

**改动**：
1. 强调推荐方式（直接放入 PDF）
2. 添加使用阶段表格
3. 说明各 seed 文件的用途

**关键内容**：
```markdown
### 1. 提供种子论文（推荐方式）

**🎯 推荐方式：直接放入 PDF 文件（自动识别）**
- **将 PDF 文件放入 `pdfs/` 目录**
- **T1 Agent 会自动扫描并识别所有 PDF 文件**
- **无需手动提供路径或编辑配置文件**
- 支持批量：一次性放入多个 PDF，T1 会逐个处理

## 各文件的使用阶段

| 文件 | 使用阶段 | 用途 |
|------|---------|------|
| `seed_papers.jsonl` | T1 生成，T2 使用 | 种子论文列表 |
| `seed_ideas.md` | T4 Ideation Agent | 作为候选研究方向 |
| `seed_constraints.md` | T2 Scout Agent | 文献检索约束 |
| `seed_external_resources.jsonl` | T5+ | 外部资源清单 |
| `pdfs/` | T1 自动扫描 | 存放 PDF 文件 |
```

### 修复 6：修复欢迎信息显示问题

**文件**：`researchos/prompts/pi.j2`

**问题**：
- Agent 没有输出第0轮的欢迎信息
- 直接调用 ask_human 工具开始第1轮对话

**改动**：
1. 修改第0轮标题为 "🚨 第0轮：欢迎与引导（必须首先执行）"
2. 添加明确指令："⚠️ CRITICAL: 在调用任何工具之前，必须先输出以下完整的欢迎文本！"
3. 在欢迎信息后添加："**重要**：输出完这段欢迎信息后，再开始第1轮对话（调用 ask_human 工具）。"

**关键内容**：
```
### 🚨 第0轮：欢迎与引导（必须首先执行）

**⚠️ CRITICAL: 在调用任何工具之前，必须先输出以下完整的欢迎文本！**

你必须在第一次响应中，**在调用任何工具之前**，先输出以下完整的欢迎文本：

---

欢迎使用 ResearchOS！我是 PI Agent，将帮助你初始化研究项目。
...
```

### 修复 7：修复 write_structured_file 参数错误

**文件**：`researchos/prompts/pi.j2`

**问题**：
- Agent 使用 `content` 参数调用 write_structured_file
- 应该使用 `data` 参数
- 导致工具调用失败："Parameter validation error: 1 validation error for WriteStructuredFileParams\ndata\n  Field required"

**改动**：
1. 修改章节标题为 "🚨🚨🚨 使用 write_structured_file 工具生成 project.yaml 🚨🚨🚨"
2. 添加子标题 "⚠️⚠️⚠️ CRITICAL: 参数名是 `data` 不是 `content` ⚠️⚠️⚠️"
3. 添加工具对比说明
4. 提供详细的 JSON 格式示例（正确和错误）
5. 在多处强调这个最常见的错误

**关键内容**：
```
### ⚠️⚠️⚠️ CRITICAL: 参数名是 `data` 不是 `content` ⚠️⚠️⚠️

**最常见的错误**：使用 `content` 参数而不是 `data` 参数！

**工具对比**：
- `write_file` 工具 → 使用 `content` 参数（字符串）
- `write_structured_file` 工具 → 使用 `data` 参数（对象）⚠️⚠️⚠️

**工具参数（只有4个）**：
1. `path` - 文件路径（字符串）
2. `schema_name` - Schema 名称（字符串）
3. `format` - 输出格式（字符串："yaml" 或 "json"）
4. `data` - **项目配置对象**（包含所有项目字段）⚠️⚠️⚠️ 注意：是 `data` 不是 `content`

### ✅✅✅ 正确的调用方式（必须完全按照这个格式）：

```json
{
  "path": "project.yaml",
  "schema_name": "project",
  "format": "yaml",
  "data": {  // ⚠️ 注意：是 data 不是 content
    "project_id": "{{ project_id }}",
    ...
  }
}
```

### ❌❌❌ 错误示例 1（使用了 content 而不是 data）：

```json
{
  "path": "project.yaml",
  "schema_name": "project",
  "format": "yaml",
  "content": {  // ❌❌❌ 错误：参数名应该是 data，不是 content
    "project_id": "...",
    ...
  }
}
```
```

**文件**：`researchos/tools/human_gate.py`

**改动**：
- `ask_clarification` 方法支持多行输入
- 支持 END 标记或 Ctrl+D 结束输入

**代码**：
```python
async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
    print(question)
    if suggestions:
        print(json.dumps(suggestions, indent=2, ensure_ascii=False))
    print("请输入回答（多行输入请在最后输入单独一行 'END' 结束，或直接按 Ctrl+D）:")
    
    lines = []
    try:
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
    except EOFError:
        pass
    
    return "\n".join(lines).strip()
```

## 测试验证

### 测试 1：process_seed_paper 自动写入

**测试脚本**：`/home/liangmengkun/tmp/test_seed_paper_fix.py`

**结果**：
```
✅ 成功从 arXiv 获取论文信息
✅ seed_papers.jsonl 包含 2 篇论文
```

### 测试 2：完整 T1 流程

**测试脚本**：`/home/liangmengkun/tmp/test_pdf_scan.py`

**结果**：
```
✅ T1 Agent 正确扫描 user_seeds/pdfs/ 目录
✅ 正确识别 PDF 文件并询问用户
✅ 生成的 project.yaml 格式正确
✅ seed_ensemble 使用默认值（整数数组）
```

### 测试 3：欢迎信息和工具参数修复

**测试脚本**：`/home/liangmengkun/tmp/test_pi_welcome_and_tool.py`

**测试目标**：
1. 验证第0轮欢迎信息是否正确显示
2. 验证 Agent 是否正确使用 write_structured_file 工具（data 参数）

**预期结果**：
```
✅ 找到欢迎信息（包含 "欢迎使用 ResearchOS"）
✅ 正确使用 write_structured_file（使用 data 参数）
❌ 如果使用了 content 参数，会报错
```

## 推荐使用方式

### 方式 1：推荐工作流程（最简单）

```bash
# 1. 初始化 workspace
researchos init-workspace ./my-project

# 2. 放入 PDF 文件
cp paper1.pdf paper2.pdf ./my-project/user_seeds/pdfs/

# 3. 运行 T1 Agent
researchos run-task T1 --workspace ./my-project

# T1 会：
# - 展示欢迎信息和准备指南
# - 第2轮后自动扫描 pdfs/ 目录
# - 询问："我发现了 2 篇 PDF 文件：paper1.pdf, paper2.pdf。是否将这些作为种子论文？"
# - 自动写入 seed_papers.jsonl
# - 生成正确格式的 project.yaml
```

### 方式 2：在对话中提供

```bash
# 运行 T1
researchos run-task T1 --workspace ./my-project

# 在第2轮对话中提供 arXiv ID 或 DOI
# Agent: "有哪些相关论文已经读过？"
# 用户: "2602.08234, 2601.03192"

# T1 会：
# - 自动获取论文元数据
# - 自动写入 seed_papers.jsonl
```

### 方式 3：手动编辑（不推荐）

```bash
# 手动编辑 seed_papers.jsonl
cat > ./my-project/user_seeds/seed_papers.jsonl <<'EOF'
{"title": "Paper 1", "authors": ["Author 1"], "year": 2024, "role": "anchor", "why_relevant": "..."}
{"title": "Paper 2", "authors": ["Author 2"], "year": 2025, "role": "reference", "why_relevant": "..."}
EOF

# 运行 T1
researchos run-task T1 --workspace ./my-project
```

## 向后兼容性

所有改进完全向后兼容：
- ✅ 旧的工作流程（在对话中提供路径）仍然有效
- ✅ 手动编辑 `seed_papers.jsonl` 仍然有效
- ✅ 不影响现有的 workspace 和配置文件
- ✅ 不影响其他 Agent 的行为

## 相关文件

### 修改的文件
- `researchos/tools/seed_paper_processor.py` - 添加自动写入功能
- `researchos/prompts/pi.j2` - 添加欢迎信息和自动扫描流程
- `researchos/runtime/workspace.py` - 更新 README 说明
- `researchos/tools/human_gate.py` - 修复多行输入

### 新增的文件
- `docs/SEED_FILES_IMPROVEMENT.md` - 改进文档
- `tests/unit/test_structured_file_tool.py` - 单元测试

### 测试文件
- `/home/liangmengkun/tmp/test_seed_paper_fix.py` - 测试 process_seed_paper
- `/home/liangmengkun/tmp/test_pdf_scan.py` - 测试完整 T1 流程
- `/home/liangmengkun/tmp/test_pi_welcome_and_tool.py` - 测试欢迎信息和工具参数修复

## Git 提交记录

```bash
e649086 feat: 改进 seed 文件管理和自动 PDF 扫描
c301908 fix: process_seed_paper 工具自动写入 seed_papers.jsonl
c0873a8 feat: T1 Agent 添加欢迎信息和主动 PDF 扫描提示
```

## 下一步改进方向

1. **PDF 元数据提取增强**
   - 自动识别 PDF 中的 DOI
   - 自动提取标题、作者、年份
   - 支持更多 PDF 格式

2. **批量处理优化**
   - 并行处理多个 PDF 文件
   - 显示处理进度

3. **智能推荐**
   - 根据 PDF 内容推荐相关论文
   - 自动生成初步的研究方向

4. **更好的错误处理**
   - PDF 解析失败时的降级方案
   - 更清晰的错误信息

## 用户反馈

**问题**：
> "为什么会说没找到呢，我已经在 /home/liangmengkun/ResearchOS/workspace/local-test/user_seeds/pdfs/memrl.pdf 上传一个了呀，很奇怪"

**解决**：
- 修复了 `process_seed_paper` 工具不写入文件的问题
- 添加了自动扫描流程
- 添加了欢迎信息和用户引导

**问题**：
> "不能自动扫描种子文献，还得我来说？init之后，应该提示用户可以进行种子添加等，要有一系列详细输出。"

**解决**：
- 添加了第0轮欢迎信息
- 第2轮后立即自动扫描
- 主动询问用户是否使用发现的 PDF

**问题**（2026-04-22 晚）：
> "好像跟你文档里描述的不一样...为什么没有这块呢？"（指第0轮欢迎信息）

**解决**：
- 修改 prompt，明确要求 Agent 在第一次响应时先输出欢迎信息
- 添加 "🚨 第0轮：欢迎与引导（必须首先执行）" 标题
- 强调 "在调用任何工具之前，必须先输出以下完整的欢迎文本"

**问题**（2026-04-22 晚）：
> "好像write_structured_file是有问题的，反倒是write_file能通过校验"

**根本原因**：
- Agent 使用了错误的参数名 `content` 而不是 `data`
- 从 trace 日志可以看到：`"arguments": {"path": "...", "content": {...}}`
- 应该是：`"arguments": {"path": "...", "data": {...}}`

**解决**：
- 在 prompt 顶部添加 "🚨🚨🚨 CRITICAL: 参数名是 `data` 不是 `content`"
- 添加工具对比说明：write_file 用 `content`，write_structured_file 用 `data`
- 提供详细的正确和错误示例（JSON 格式）
- 在多处强调这个最常见的错误

---

**文档创建时间**: 2026-04-22  
**ResearchOS 版本**: 0.1.0  
**作者**: Claude Opus 4.7
