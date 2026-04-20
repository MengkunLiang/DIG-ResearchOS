# ResearchOS 系统评测报告

> **评测日期**: 2026-04-20
> **评测范围**: ResearchOS Runtime 全组件
> **评测模式**: Multi-Agent Mode + 真实 API 测试

---

## 一、系统概览

### 1.1 组件状态

| 模块 | 状态 | 说明 |
|------|------|------|
| T1-T9 Agent | ✅ 全部实现 | 11 个 agent 类，全部测试通过 |
| 单元测试 | ✅ 264 个通过 | 覆盖率良好 |
| 集成测试 | ✅ 通过 | pipeline 验证 |
| Skills 系统 | ✅ 已实现 | loader/agent/runner 完整 |
| Docker 执行 | ✅ 双模式 | container-native / host |
| CLI 界面 | ✅ DIG Lab 风格 | ASCII art 品牌化 |
| 模型路由 | ✅ 已修复 | gpt-5 → gpt-4o |
| Gates 配置 | ✅ 已补全 | 关键 gate 已配置 |

### 1.2 关键配置文件

| 文件 | 修复内容 |
|------|----------|
| `config/model_routing.yaml` | gpt-5 → gpt-4o |
| `config/gates.yaml` | 补全 budget_gate, quality_gate, novelty_gate, submission_gate |
| `researchos/skills/loader.py` | 支持 allowed-tools 逗号分隔字符串 |
| `researchos/runtime/cli_ui.py` | DIG Lab ASCII art 品牌化 |

---

## 二、测试结果

### 2.1 单元测试结果

```
264 passed in 7.35s
```

**覆盖率分析**:
- T1 PI Agent: ✅ 通过 (init/evaluate 模式)
- T2 Scout Agent: ✅ 通过
- T3 Reader Agent: ✅ 通过 (read/synthesize 模式)
- T4 Ideation Agent: ✅ 通过
- T4.5 NoveltyAuditor: ✅ 通过
- T5-T6 Experimenter/Novelty: ✅ 通过
- T7 Experimenter (full): ✅ 通过
- T8 Writer/Reviewer: ✅ 通过
- T9 Submission: ✅ 通过

### 2.2 CLI 功能测试

```bash
# selftest - LLM 连接测试
$ python -m researchos.cli selftest
✓ relay: ok=true, latency_ms=3138

# init-workspace - workspace 初始化
$ python -m researchos.cli --workspace /tmp/test_eval init-workspace
✓ workspace created

# run-task HELLO - 简单 workflow 测试
$ python -m researchos.cli --workspace /tmp/test_eval run-task HELLO
✓ outputs: ['hello_file']
✓ 成功生成 hello.txt
```

### 2.3 Skills 系统测试

```python
# Skills 加载测试
>>> from researchos.skills.loader import discover_skills
>>> from pathlib import Path
>>> skills = discover_skills(Path('/home/liangmengkun/ResearchOS/skills'))
>>> for name, skill in skills.items():
...     print(f'{name}: allowed_tools={skill.allowed_tools}')

deepxiv: allowed_tools=['Bash(*)', 'Read', 'Write']
paper-compile: allowed_tools=['Bash(*)', 'Read', 'Write', 'Edit', 'Grep', 'Glob']
paper-write: allowed_tools=['Bash(*)', 'Read', 'Write', 'Edit', 'Grep', 'Glob', 'Agent', ...]
```

---

## 三、已修复的 Bug

### Bug 1: model_routing.yaml 配置错误
- **位置**: `config/model_routing.yaml:28`
- **问题**: `model: "gpt-5"` — gpt-5 不存在
- **修复**: 改为 `gpt-4o`
- **影响**: heavy tier agent 现可正常运行

### Bug 2: gates.yaml 完全空置
- **位置**: `config/gates.yaml`
- **问题**: 无任何 gate 配置
- **修复**: 添加 budget_gate, quality_gate, novelty_gate, submission_gate
- **影响**: T6/T9 等关键节点现可验证

### Bug 3: Skills 加载失败
- **位置**: `researchos/skills/loader.py`
- **问题**: allowed-tools 格式不兼容（逗号分隔字符串）
- **修复**: 添加字符串到列表的解析逻辑
- **影响**: Skills 现可正确加载

### Bug 4: CLI 测试断言过期
- **位置**: `tests/unit/test_cli_ui.py`
- **问题**: 断言中引用旧的 "ResearchOS Runtime Boot" 文本
- **修复**: 更新为 "DIG Lab" 品牌检查
- **影响**: 测试现已通过

---

## 四、内容质量审查

### 4.1 审查标准

| 问题类型 | 检测方法 | 严重程度 |
|----------|----------|----------|
| **文献幻觉** | 引用 key 在 bib 中不存在 | P0 |
| **数字幻觉** | 论文中的数字不在 results_summary.json | P0 |
| **逻辑矛盾** | 前后章节结论相互矛盾 | P0 |
| **不可复现** | 声称的结果无法从方法复现 | P1 |
| **格式错误** | LaTeX 编译失败 | P1 |

### 4.2 单元测试中的验证

| Agent | 验证规则 |
|-------|----------|
| PI | project.yaml schema 验证、ethical screening |
| Scout | 论文数量 >=5、去重检查 |
| Reader | paper_notes 存在、comparison_table 可解析 |
| Ideation | hypotheses.md 非空、exp_plan.yaml 有效 |
| Experimenter | results_summary.json 有效、实验次数 >=3 |
| Writer | paper.tex 含 documentclass、引用有效 |
| Submission | 匿名化检查（email/github/acknowledgments） |

---

## 五、已知限制

1. **T1 需要用户交互**: PIAgent 在 init 模式需要用户输入研究方向
2. **Docker 执行需要 docker CLI**: 无 docker 环境时自动 fallback 到 host 模式
3. **Skills 依赖外部工具**: paper-compile 需要 latexmk，deepxiv 需要 deepxiv-sdk

---

## 六、验收标准达成情况

- ✅ 所有配置可被正常解析
- ✅ CLI 启动无报错
- ✅ 264 个测试全部通过
- ✅ Skills 系统可正常运行
- ✅ DIG Lab CLI 品牌化完成
- ✅ gates.yaml 配置补全
- ✅ model_routing.yaml 修复

---

## 七、后续建议

1. **真实项目测试**: 在实际研究项目上运行完整 pipeline
2. **MCP 工具完善**: 扩展 MCP 连接器支持更多外部服务
3. **文档补全**: 完善用户快速入门文档
4. **性能优化**: 考虑添加缓存层减少 API 调用