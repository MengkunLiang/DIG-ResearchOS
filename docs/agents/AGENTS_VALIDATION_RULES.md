# ResearchOS Agent 验证规则总览

本文档定义 ResearchOS 中 Agent 的验证规则，包括输出验证、handoff 验证和质量门控。

---

## 1. 输出验证规则

每个 Agent 必须实现 `validate_outputs()` 方法，验证其输出文件的完整性和格式。

### 1.1 通用验证模式

```python
def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
    """验证 Agent 输出的完整性和格式"""
    ws = ctx.workspace_dir

    # 1. 必需文件检查
    required_files = [
        "output/file1.md",
        "output/file2.json",
    ]
    ok, err = validate_files_exist(ctx, required_files)
    if not ok:
        return False, err

    # 2. 内容长度检查
    file1_path = ws / "output" / "file1.md"
    file1_text = read_text_file(file1_path)
    if len(file1_text) < MIN_LENGTH:
        return False, f"文件过短({len(file1_text)} 字符)"

    # 3. 格式验证（关键词、结构等）
    required_markers = ["Level 0", "Level 1", "Level 2", "Level 3"]
    has_markers = any(marker in file1_text for marker in required_markers)
    if not has_markers:
        return False, "缺少必需的标记"

    return True, None
```

### 1.2 各 Agent 验证规则

| Agent | Task | 必需输出 | 最小长度 | 特殊检查 |
|-------|------|---------|---------|----------|
| PI | T1, T7.5 | project.yaml, state.yaml | 200字符 | YAML 格式正确 |
| Scout | T2 | papers_dedup.jsonl, search_log.md | 500字符 | 至少15篇论文 |
| Reader | T3 | paper_notes/*.md, comparison_table.csv | - | 至少3篇笔记 |
| Reader | T3.5 | synthesis.md | 2000字符 | 5个必需章节 |
| Ideation | T4 | hypotheses.md, exp_plan.yaml | 500字符 | H1/H2/H3锚点 |
| NoveltyAuditor | T4.5 | novelty_audit.md | 500字符 | 新颖性等级 |
| Experimenter | T5 | pilot_results.json, motivation_validation.md | 200字符 | JSON 格式 |
| Novelty | T6 | novelty_report.md, must_add_baselines.md | 500字符 | Level 标记 |
| Experimenter | T7 | results_summary.json, iteration_log.md | 300字符 | 实验数量 |

---

## 2. Handoff 验证规则

Handoff 是 Agent 之间的交接点，需要验证上游输出的完整性和下游的输入要求。

### 2.1 Handoff 检查点

| Handoff | 上游输出 | 下游输入 | 验证内容 |
|---------|---------|---------|----------|
| T3→T3.5 | paper_notes/, comparison_table.csv | paper_notes/, comparison_table.csv | 至少3篇论文笔记 |
| T3.5→T4 | synthesis.md | synthesis.md | 5个必需章节存在 |
| T4→T4.5 | hypotheses.md, exp_plan.yaml | hypotheses.md | H1/H2/H3 锚点 |
| T4.5→T5 | novelty_audit.md | hypotheses.md, exp_plan.yaml | 审计通过 |
| T5→T6 | pilot_results.json | pilot_results.json | 至少1个实验 |
| T6→T7 | novelty_report.md | novelty_report.md | PASS/REVISE决策 |

### 2.2 验证实现

```python
def validate_handoff(
    workspace_dir: Path,
    from_agent: str,
    to_agent: str,
) -> tuple[bool, str | None]:
    """验证 handoff 的完整性"""

    # T4.5 → T5: 检查 novelty_audit.md 是否存在且有 PASS 决策
    if from_agent == "novelty_auditor" and to_agent == "experimenter":
        audit_path = workspace_dir / "ideation" / "novelty_audit.md"
        if not audit_path.exists():
            return False, "缺少新颖性审计报告"

        audit_text = read_text_file(audit_path)
        # 检查是否有 FAIL 决策
        if "FAIL" in audit_text:
            return False, "新颖性审计未通过，跳过 Pilot 实验"

        return True, None

    # T5 → T6: 检查 pilot_results.json 是否存在
    if from_agent == "experimenter" and to_agent == "novelty":
        results_path = workspace_dir / "pilot" / "pilot_results.json"
        if not results_path.exists():
            return False, "缺少 Pilot 实验结果"

        return True, None

    # 默认：检查必需文件存在
    return True, None
```

### 2.3 Gate T6-DECIDE 决策

| 决策 | 条件 | 后续动作 |
|------|------|----------|
| PASS | 所有假设 Level 2+ 且 Pilot 充分验证 | 进入 T7 完整实验 |
| REVISE | 存在 Level 1 假设或 Pilot 部分验证 | 修改假设或补充验证 |
| FAIL | 存在 Level 0 假设或 Pilot 未验证核心假设 | 重新构思 |

---

## 3. Integrity Gate

Integrity Gate 是 Pilot 实验前的质量预审，确保假设和审计的完整性。

### 3.1 T4.5 → T5 Integrity Gate

在执行 Pilot 实验前，必须验证：

1. **假设完整性**: hypotheses.md 包含所有 H 锚点
2. **审计完成**: novelty_audit.md 存在且有新性等级
3. **无高风险撞车**: collision_cases.md 不存在或无 High Overlap
4. **实验计划可用**: exp_plan.yaml 格式正确且可执行

### 3.2 实现

```python
def pre_pilot_integrity_check(
    workspace_dir: Path,
) -> tuple[bool, str | None, list[str]]:
    """
    返回: (是否通过, 错误消息, 警告列表)
    """
    issues = []
    warnings = []

    # 1. 检查假设文件
    hypotheses_path = workspace_dir / "ideation" / "hypotheses.md"
    if not hypotheses_path.exists():
        issues.append("缺少 hypotheses.md")
    else:
        hypotheses_text = read_text_file(hypotheses_path)
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses_text, re.MULTILINE)
        if not anchors:
            issues.append("假设文件缺少 H 锚点")

    # 2. 检查新颖性审计
    audit_path = workspace_dir / "ideation" / "novelty_audit.md"
    if not audit_path.exists():
        warnings.append("缺少 novelty_audit.md，跳过 T4.5 审计")
    else:
        audit_text = read_text_file(audit_path)
        if "FAIL" in audit_text:
            issues.append("新颖性审计未通过")
        if "Level 0" in audit_text:
            warnings.append("发现 Level 0 假设")

    # 3. 检查撞车案例
    collision_path = workspace_dir / "ideation" / "collision_cases.md"
    if collision_path.exists():
        collision_text = read_text_file(collision_path)
        if "高风险" in collision_text or "High Overlap" in collision_text:
            warnings.append("存在高风险撞车案例")

    # 4. 检查实验计划
    exp_plan_path = workspace_dir / "ideation" / "exp_plan.yaml"
    if not exp_plan_path.exists():
        issues.append("缺少实验计划")
    elif not is_valid_yaml(exp_plan_path):
        issues.append("实验计划格式错误")

    if issues:
        return False, "; ".join(issues), warnings
    return True, None, warnings
```

---

## 4. 7 AI Research Failure Modes

在实验验证阶段，需要检测常见的 AI 研究错误模式。

### 4.1 失败模式列表

| 编号 | 模式 | 描述 | 检测方法 |
|------|------|------|----------|
| FM1 | Implementation Bugs | 实现错误导致结果异常 | 检查 loss 曲线、数值稳定性 |
| FM2 | Hallucinated Results | 幻觉结果，与假设不符 | 交叉验证关键数字 |
| FM3 | Shortcut Reliance | 依赖捷径而非核心方法 | 消融实验是否分离关键组件 |
| FM4 | Bug-as-Insight Reframing | 将 bug 当作发现 | 检查结果是否符合预期 |
| FM5 | Methodology Fabrication | 方法论伪造 | 验证方法描述与实现一致 |
| FM6 | Frame-Lock | 框架锁定，无法跳出 | 检查是否有多视角分析 |
| FM7 | Citation Hallucinations | 虚假引用 | 验证引用存在于文献库 |

### 4.2 检测实现

```python
FAILURE_MODE_CHECKLIST = [
    {
        "id": "FM1",
        "name": "Implementation Bugs",
        "check": "检查 loss 是否发散、数值是否异常",
    },
    {
        "id": "FM2",
        "name": "Hallucinated Results",
        "check": "验证关键指标是否在合理范围内",
    },
    {
        "id": "FM3",
        "name": "Shortcut Reliance",
        "check": "确认消融实验覆盖所有关键组件",
    },
    {
        "id": "FM4",
        "name": "Bug-as-Insight Reframing",
        "check": "检查结果是否符合方法预期",
    },
    {
        "id": "FM5",
        "name": "Methodology Fabrication",
        "check": "验证方法描述与代码实现一致",
    },
    {
        "id": "FM6",
        "name": "Frame-Lock",
        "check": "确认有多视角分析和对比",
    },
    {
        "id": "FM7",
        "name": "Citation Hallucinations",
        "check": "验证所有引用存在于 related_work.bib",
    },
]


def check_failure_modes(experiment_results: dict) -> list[str]:
    """检测实验结果中的失败模式"""
    detected = []

    # 检查每个实验
    for exp in experiment_results.get("experiments", []):
        metrics = exp.get("metrics", {})

        # FM1: 检查数值异常
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                if abs(value) > 1e6:  # 异常大
                    detected.append(f"FM1: {exp['experiment_id']} 数值异常 ({key}={value})")

        # FM2: 检查结果与预期偏离
        expected = exp.get("expected_results", {})
        actual = exp.get("metrics", {})
        if expected and actual:
            for key in expected:
                if key in actual:
                    # 检查偏离是否超过50%
                    ratio = abs(actual[key] - expected[key]) / (abs(expected[key]) + 1e-6)
                    if ratio > 0.5:
                        detected.append(
                            f"FM2: {exp['experiment_id']} {key} 偏离预期超过50%"
                        )

    return detected
```

---

## 5. Citation Verification

引用验证确保论文中引用的文献真实存在。

### 5.1 验证规则

1. **所有引用必须存在于 related_work.bib**
2. **引用格式正确**: `\cite{key}`, `\citep{key}`, `\citet{key}`
3. **关键声明有引用支撑**

### 5.2 实现

```python
def validate_citations(
    paper_path: Path,
    bib_path: Path,
) -> tuple[bool, str | None, list[str]]:
    """
    验证论文中的引用
    返回: (是否通过, 错误消息, 警告列表)
    """
    paper_text = paper_path.read_text()
    bib_text = bib_path.read_text()

    # 1. 提取 BibTeX 中的所有 key
    bib_keys = set(re.findall(r"@\w+\{(\w+),", bib_text))

    # 2. 提取论文中的所有引用
    cited_keys = set(re.findall(r"\\cite[pt]?\{([^}]+)\}", paper_text))
    cited_keys = {k.strip() for chunk in cited_keys for k in chunk.split(",")}

    # 3. 检查缺失的引用
    missing = cited_keys - bib_keys
    if missing:
        return False, f"引用了不存在的 BibTeX key: {missing}", []

    # 4. 检查未使用的引用（警告）
    unused = bib_keys - cited_keys
    if unused:
        warnings = [f"未使用的引用: {unused}"]
    else:
        warnings = []

    return True, None, warnings
```

---

## 6. Material Passport

Material Passport 记录每个产物的来源和元数据，增强可追溯性。

### 6.1 Manifest 格式

```yaml
# manifest.yaml
manifest_version: "1.0"
created_at: "2024-04-20T10:30:00Z"
agent: "novelty"
task_id: "T6"

artifacts:
  - path: "novelty/novelty_report.md"
    type: "markdown"
    size_bytes: 8192
    checksum: "sha256:abc123..."

  - path: "novelty/must_add_baselines.md"
    type: "markdown"
    size_bytes: 2048
    checksum: "sha256:def456..."

inputs:
  - path: "ideation/hypotheses.md"
    required: true
    checksum: "sha256:xyz789..."

  - path: "pilot/pilot_results.json"
    required: true

dependencies:
  - agent: "novelty_auditor"
    task_id: "T4.5"
    output: "ideation/novelty_audit.md"
```

### 6.2 生成函数

```python
def generate_manifest(
    workspace_dir: Path,
    agent_name: str,
    task_id: str,
    artifacts: list[dict],
    inputs: list[dict],
) -> dict:
    """生成 Material Passport manifest"""
    import hashlib
    from datetime import datetime

    manifest = {
        "manifest_version": "1.0",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "agent": agent_name,
        "task_id": task_id,
        "artifacts": [],
        "inputs": inputs,
    }

    for artifact in artifacts:
        path = workspace_dir / artifact["path"]
        if path.exists():
            content = path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            manifest["artifacts"].append({
                "path": artifact["path"],
                "type": artifact.get("type", "unknown"),
                "size_bytes": len(content),
                "checksum": f"sha256:{checksum}",
            })

    return manifest
```

---

## 7. 验证流程图

```
                    ┌─────────────┐
                    │   T4.5      │
                    │ NoveltyAudit│
                    └──────┬──────┘
                           │
                           ▼
                   ┌───────────────┐
                   │ Integrity Gate│
                   │ (T4.5→T5)    │
                   └───────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
        ┌──────────┐            ┌──────────┐
        │  PASS    │            │  FAIL    │
        │继续Pilot │            │ 停止流程 │
        └────┬─────┘            └──────────┘
             │
             ▼
        ┌─────────────┐
        │    T5       │
        │ Experimenter│
        │  (pilot)   │
        └──────┬──────┘
               │
               ▼
        ┌─────────────┐
        │ pilot_results│
        │   .json     │
        └──────┬──────┘
               │
               ▼
        ┌─────────────┐
        │    T6       │
        │  Novelty    │
        │ (最终验证)  │
        └──────┬──────┘
               │
               ▼
        ┌─────────────┐
        │ T6-DECIDE   │
        │   Gate      │
        └──────┬──────┘
               │
      ┌────────┼────────┐
      │        │        │
      ▼        ▼        ▼
┌──────────┐┌──────────┐┌──────────┐
│  PASS    ││  REVISE  ││   FAIL   │
│  → T7    ││→ 修改假设 ││ → 重新   │
│          ││          ││  构思    │
└──────────┘└──────────┘└──────────┘
```

---

## 8. 鲁棒性增强验证规则

ResearchOS 实现了多项鲁棒性增强功能，以下是相关的验证规则：

### 8.1 T4 Hypothesis Pre-mortem 验证

**位置**: T4 Ideation Agent，阶段 A.5

**验证内容**:
- 检查是否执行了三维检查（物理/数学约束、已知反例、资源可行性）
- 检查是否识别了 High 风险
- 检查是否提供了缓解方案

**失败处理**: 如果发现 High 风险且无缓解方案，提示用户重新选择方向

### 8.2 Runtime Budget Drift 验证

**位置**: StateMachine，每个 task 完成后

**验证内容**:
- 检查累计花费是否超过预算 70%（警告）
- 检查累计花费是否超过预算 90%（严重警告）

**失败处理**: 记录警告日志，超过 90% 时写入警告文件

### 8.3 T1 Ethical Screening 验证

**位置**: T1 PI Agent，`validate_outputs` 阶段

**验证内容**:
- 检查研究方向是否包含敏感词（武器、监控、操纵、隐私侵犯、歧视等）
- 检查是否涉及敏感领域

**失败处理**: 返回警告并要求用户确认

**实现示例**:
```python
def _check_ethical_concerns(self, project_yaml_path: Path) -> tuple[bool, str | None]:
    """检查敏感研究方向"""
    sensitive_keywords = [
        "weapon", "surveillance", "manipulation", "privacy invasion", "discrimination",
        "武器", "监控", "操纵", "隐私侵犯", "歧视"
    ]
    
    project_text = read_text_file(project_yaml_path).lower()
    
    for keyword in sensitive_keywords:
        if keyword in project_text:
            return False, f"检测到敏感词: {keyword}，请确认研究方向的伦理合规性"
    
    return True, None
```

### 8.4 T1 External Resources 验证

**位置**: T1 PI Agent，第 2.5 轮对话后

**验证内容**:
- 检查 `user_seeds/seed_external_resources.jsonl` 是否存在
- 验证资源格式（type, source, description）
- 验证 source 前缀（http://, https://, file://, git://, docker://）

**失败处理**: 返回格式错误信息，要求 Agent 修正

**实现示例**:
```python
def _validate_external_resources(self, resources_path: Path) -> tuple[bool, str | None]:
    """验证外部资源格式"""
    if not resources_path.exists():
        return True, None  # 可选文件
    
    valid_types = ["dataset", "baseline_repo", "pretrained_model", "docker_image", "tool", "script", "other"]
    valid_prefixes = ["http://", "https://", "file://", "git://", "docker://"]
    
    for line in read_jsonl(resources_path):
        if "type" not in line or line["type"] not in valid_types:
            return False, f"无效的资源类型: {line.get('type')}"
        
        if "source" not in line:
            return False, "缺少 source 字段"
        
        if not any(line["source"].startswith(prefix) for prefix in valid_prefixes):
            return False, f"无效的 source 前缀: {line['source']}"
    
    return True, None
```

### 8.5 T8 声明追溯验证

**位置**: T8 Writer Agent，post-hooks

**验证内容**:
- 检查论文中的每个声明是否有对应的实验结果
- 验证论文中的数值与实验结果一致

**失败处理**: 返回不一致的声明列表，要求 Agent 修正

**状态**: ✅ 已实现（见 `researchos/agents/writer.py` 的 `validate_outputs` 方法）

### 8.6 T6 机制相似度搜索验证

**位置**: T6 Novelty Agent

**验证内容**:
- 检查是否搜索了近期相关工作
- 检查是否基于 Pilot 实验结果验证新颖性
- 检查是否补充了必须的基线方法

**失败处理**: 返回缺失的基线方法列表

### 8.7 迭代死锁检测验证

**位置**: AgentRunner 主循环

**验证内容**:
- 检查连续空回复次数是否超过 `max_empty_reply`
- 检查验证失败重试次数是否超过 `max_validation_retries`

**失败处理**: 终止 Agent 执行，返回错误信息

**实现示例**:
```python
# 在 AgentRunner 主循环中
empty_reply_count = 0
validation_retry_count = 0

while not finished:
    response = llm_client.call(messages)
    
    if not response.content:
        empty_reply_count += 1
        if empty_reply_count >= max_empty_reply:
            raise AgentError("Agent 连续空回复，可能陷入死锁")
    else:
        empty_reply_count = 0
    
    # 验证输出
    ok, err = agent.validate_outputs(ctx)
    if not ok:
        validation_retry_count += 1
        if validation_retry_count >= max_validation_retries:
            raise AgentError("输出验证失败次数过多，可能陷入死锁")
    else:
        validation_retry_count = 0
```

---

## 9. 容器环境验证

### 9.1 容器检测验证

**位置**: Runtime 初始化

**验证内容**:
- 检查 `/.dockerenv` 文件是否存在
- 检查 `/proc/1/cgroup` 是否包含 docker 标识

**行为**:
- 容器内：直接执行命令，避免嵌套 Docker
- 宿主机：使用 Docker 隔离执行（如果需要）

**实现示例**:
```python
def is_in_container() -> bool:
    """检测是否在容器内运行"""
    # 方法 1: 检查 /.dockerenv
    if Path("/.dockerenv").exists():
        return True
    
    # 方法 2: 检查 /proc/1/cgroup
    try:
        with open("/proc/1/cgroup", "r") as f:
            return "docker" in f.read()
    except:
        return False
```

---

## 10. 参考

- Agent 实现: `/home/liangmengkun/ResearchOS/researchos/agents/`
- 验证函数: `/home/liangmengkun/ResearchOS/researchos/agents/_common.py`
- 状态机配置: `/home/liangmengkun/ResearchOS/config/state_machine.yaml`
- 鲁棒性增强测试: `/home/liangmengkun/ResearchOS/tests/unit/test_robustness_enhancements.py`
- 外部参考: [academic-research-skills](https://github.com/Imbad0202/academic-research-skills)
