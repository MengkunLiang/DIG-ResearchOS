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
| 多 Agent 协作链 | ✅ 已验证 | T3→T4→T5 通过 |

### 1.2 真实 API 测试结果

| Agent | Task | 状态 | 耗时 | API调用 | 成本 |
|-------|------|------|------|---------|------|
| hello | HELLO | ✅ | 3149ms | 1 | $0.0008 |
| pi | T1 | ✅ | 3346ms | 2 | $0.0116 |
| scout | T2 | ✅ | 2290ms | 1 | $0.0080 |
| reader | T3 | ✅ | 1501ms | 1 | $0.0042 |
| ideation | T4 | ✅ | 2601ms | 1 | $0.0132 |
| novelty_auditor | T4.5 | ✅ | 2464ms | 1 | $0.0100 |
| novelty | T6 | ✅ | 2630ms | 1 | $0.0109 |
| writer | T8-WRITE | ✅ | 5872ms | 1 | $0.0111 |
| reviewer | T8-REVIEW-1 | ✅ | 5224ms | 1 | $0.0099 |
| submission | T9 | ✅ | 2554ms | 1 | $0.0038 |

**总计**: 10/10 通过，总耗时 31.6s，总成本 $0.0835

### 1.3 已加载 Skills

| Skill | allowed_tools |
|-------|---------------|
| deepxiv | Bash(*), Read, Write |
| paper-compile | Bash(*), Read, Write, Edit, Grep, Glob |
| paper-write | Bash(*), Read, Write, Edit, Grep, Glob, Agent, WebSearch, WebFetch, mcp__codex__* |

### 1.4 测试命令

```bash
# 运行全部 Agent 真实 API 测试
python scripts/test_all_agents_real_api.py

# 测试单个 Agent
python scripts/test_all_agents_real_api.py --agent hello
python scripts/test_all_agents_real_api.py --agent pi
python scripts/test_all_agents_real_api.py --agent reader

# 详细日志模式
python scripts/test_all_agents_real_api.py --verbose

# 指定 workspace
python scripts/test_all_agents_real_api.py --workspace /tmp/my_tests

# 验证 Skills 加载
python -c "from researchos.skills.loader import discover_skills; from pathlib import Path; print(discover_skills(Path('skills')))"

# 单元测试
python -m pytest tests/unit/ -v
```

### 1.5 关键配置文件

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

### 2.4 多 Agent 协作链测试 (T3→T4→T5)

```bash
python scripts/test_collab_chain.py --workspace /tmp/collab_chain_test6 --verbose
```

**测试结果**:

| 阶段 | 状态 | 耗时 | API调用 | 成本 | 产出 |
|------|------|------|---------|------|------|
| T3-Reader | ✅ | 24307ms | 1 | $0.0286 | synthesis.md (4119 chars) |
| T4-Ideation | ✅ | 26290ms | 2 | $0.0551 | hypotheses.md (1195 bytes) + exp_plan.yaml (3506 bytes) |
| T5-Experimenter-Pilot | ✅ | 2231ms | 1 | $0.0073 | 处理 exp_plan.yaml |

**总计**: 3/3 通过，总耗时 52828ms，总 API 调用 4 次，总成本 $0.0909

**数据流验证**:
- T3→synthesis.md: ✅
- T4→hypotheses.md: ✅
- T4→exp_plan.yaml: ✅ (valid YAML with 2 experiments)
- T5→pilot_results: ✅

**关键修复点**:
1. T3-Reader: 在用户消息中直接包含 paper_notes 内容
2. T4-Ideation: 明确说明 synthesis 内容已直接提供，不要尝试 read_file
3. T4-Ideation Round 2: 明确要求生成完整的 hypotheses.md 和 exp_plan.yaml 格式
4. T5-Experimenter: 添加 domain 字段到 project.yaml
5. T5-Experimenter: 修复 exp_plan_path 为空时 Path("") 的问题

**测试命令**:
```bash
python scripts/test_collab_chain.py [--workspace PATH] [--verbose]
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