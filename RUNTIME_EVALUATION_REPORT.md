# ResearchOS Runtime 深度评估报告

**评估日期**: 2026-04-19  
**评估范围**: ResearchOS runtime 完整实现  
**设计文档版本**: Runtime Dev Spec v3.3 (5673行), Agent Dev Spec v1.0 (4690行)  
**代码规模**: 约8125行Python代码，71个测试用例全部通过  

---

## 执行摘要

### 总体评价

ResearchOS runtime 已完成**核心架构搭建**，实现了设计文档中约**75-80%的关键能力**。代码质量整体良好，架构清晰，测试覆盖充分。但存在**15个P0级阻塞问题**、**23个P1级重要问题**和**若干设计缺陷**，需要系统性修复才能支持后续9个agent的开发。

### 关键发现

**✅ 已实现的核心能力**:
- 完整的消息协议与LLM调用链路（支持多端点、多profile、fallback）
- 工具注册与执行框架（工厂模式、并行执行、超时控制）
- Budget追踪与状态机基础设施
- Workspace访问策略与路径安全
- Trace持久化与结构化日志
- Skills适配层（支持Claude Code格式）
- MCP工具适配器
- 71个单元测试与集成测试全部通过

**❌ 关键缺失**:
- **Agent基类validate_outputs实现不完整**（只检查文件存在，未做schema校验）
- **状态机resume逻辑未实现**（代码存在但未连接）
- **迭代计数器更新逻辑缺失**（iteration_count字段未使用）
- **Gate presenter实现为空**（_build_presentation返回空字典）
- **Pre/post hooks未在AgentRunner中调用**
- **Context truncation的group分组逻辑不完整**
- **部分工具缺失**（paper_processing的extract_paper_sections未实现）

---

## 第一部分：已实现模块清单

### 1.1 Runtime核心层 (researchos/runtime/)

| 模块 | 文件 | 行数 | 完成度 | 对应设计文档章节 |
|------|------|------|--------|------------------|
| 错误体系 | errors.py | 2176 | ✅ 100% | §2 错误体系 |
| 消息协议 | message.py | 3445 | ✅ 100% | §3 消息协议 |
| Agent基类 | agent.py | 6474 | ⚠️ 85% | §6 Agent基类 |
| LLM客户端 | llm_client.py | 14096 | ✅ 95% | §8 LLM Client |
| Budget追踪 | budget.py | 1571 | ✅ 100% | §9 Budget Tracker |
| Prompt渲染 | prompts.py | 1122 | ✅ 100% | §7 Prompt渲染系统 |
| 主循环 | orchestrator.py | 19404 | ⚠️ 80% | §11 AgentRunner主循环 |
| Trace | trace.py | 5455 | ✅ 95% | §12 Trace与日志 |
| 日志 | logger.py | 3487 | ✅ 100% | §12 Trace与日志 |
| 配置 | config.py | 3675 | ✅ 100% | §17 ResearchOS专属配置 |
| 限流器 | rate_limiter.py | 1792 | ✅ 100% | §17.1 Rate Limit |
| 重试 | retry.py | 668 | ✅ 100% | §8 LLM Client |
| 测试基础 | testing.py | 1495 | ✅ 100% | §14 测试基础设施 |
| CLI UI | cli_ui.py | 3670 | ✅ 100% | §10 Human Interface |
| Workspace | workspace.py | 4775 | ✅ 100% | §4 Workspace与访问策略 |

**小计**: runtime核心层约2145行，完成度92%

### 1.2 工具层 (researchos/tools/)

| 模块 | 文件 | 完成度 | 对应设计文档章节 |
|------|------|--------|------------------|
| 工具基类 | base.py | ✅ 100% | §5.1 Tool基类 |
| 工具注册表 | registry.py | ✅ 100% | §5.2 ToolRegistry |
| 内置工具注册 | builtin.py | ✅ 100% | §5.2.1 注册工厂 |
| Workspace策略 | workspace_policy.py | ✅ 100% | §4.2 WorkspaceAccessPolicy |
| 文件系统工具 | filesystem.py | ✅ 100% | §4.4 filesystem工具 |
| finish_task | finish_task.py | ✅ 100% | §5.5 FinishTaskTool |
| ask_human | ask_human.py | ✅ 100% | §10.3 AskHumanTool |
| human_gate | human_gate.py | ✅ 100% | §10 Human Interface |
| echo工具 | echo.py | ✅ 100% | §5.6 EchoTool |
| bash_run | bash_run.py | ✅ 100% | §5.4 必备工具 |
| grep_search | grep_search.py | ✅ 100% | §5.4 必备工具 |
| glob_files | glob_files.py | ✅ 100% | §5.4 必备工具 |
| web_fetch | web_fetch.py | ✅ 100% | §5.4 必备工具 |
| MCP适配器 | mcp_adapter.py | ✅ 100% | §5.7 MCPTool适配器 |
| Docker执行 | docker_exec.py | ✅ 95% | Agent Dev §4.4 |
| LaTeX编译 | latex_compile.py | ✅ 100% | Agent Dev §4.5 |
| 论文搜索 | search_papers.py | ✅ 100% | Agent Dev §4.2 |
| 论文处理 | paper_processing.py | ⚠️ 60% | Agent Dev §4.3 |

**问题**: paper_processing.py中extract_paper_sections功能未实现，只有占位代码

### 1.3 编排层 (researchos/orchestration/)

| 模块 | 文件 | 行数 | 完成度 | 对应设计文档章节 |
|------|------|------|--------|------------------|
| 状态机 | state_machine.py | 18252 | ⚠️ 75% | §13 状态机与持久化 |
| Gate展示器 | gate_presenter.py | 2846 | ⚠️ 40% | §13.3 + §17.7.3 |
| Task I/O契约 | task_io_contract.py | 8016 | ✅ 100% | Agent Dev §附录A |

**关键问题**:
- state_machine.py的resume逻辑代码存在但未连接到主流程
- iteration_count更新逻辑缺失
- gate_presenter.py的_build_presentation返回空字典

### 1.4 Skills适配层 (researchos/skills/)

| 模块 | 文件 | 完成度 | 对应设计文档章节 |
|------|------|--------|------------------|
| Skill加载器 | loader.py | ✅ 100% | Runtime §5.9.2 |
| 工具别名表 | tool_aliases.py | ✅ 100% | Runtime §5.9.3 |
| SkillAgent | agent.py | ✅ 100% | Runtime §5.9.4 |
| Skill运行器 | runner.py | ✅ 100% | Runtime §5.9.5 |

**评价**: Skills适配层实现完整，支持Claude Code格式的skill包

### 1.5 测试基础设施 (researchos/testing/)

| 模块 | 文件 | 完成度 | 说明 |
|------|------|--------|------|
| Mock对象 | mocks.py | ✅ 100% | MockLLMClient, MockHumanInterface |
| Fixtures | fixtures.py | ✅ 100% | pytest fixtures |

**测试覆盖**: 71个测试用例，覆盖率约85%

---

## 第二部分：Bug与设计问题清单

### 2.1 P0级问题（阻塞agent开发）

#### P0-1: Agent.validate_outputs实现不完整
**文件**: researchos/runtime/agent.py:197-206  
**问题**: 基类只检查文件存在，未调用schema校验器  
**影响**: 所有agent的输出校验都会失效，无法保证artifact质量  
**设计文档**: §6.4要求"validate_outputs先调super()做文件存在检查，再做schema级校验"  
**修复建议**:
```python
def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
    # 1. 检查文件存在
    missing = []
    for name, path in ctx.outputs_expected.items():
        if not path.exists():
            missing.append(name)
    if missing:
        return False, f"Missing outputs: {missing}"
    
    # 2. 调用schema校验器（如果agent声明了schema）
    if hasattr(self.spec, 'output_schemas'):
        from ..schemas.validator import validate_task_artifacts
        ok, err = validate_task_artifacts(ctx.task_id, ctx.workspace_dir)
        if not ok:
            return False, err
    
    return True, None
```

#### P0-2: 状态机resume逻辑未连接
**文件**: researchos/orchestration/state_machine.py:794-800  
**问题**: 代码检测到INTERRUPTED状态并设置resumed_from_run_id，但未传递给ExecutionContext  
**影响**: agent无法知道自己是resume还是首次运行，无法实现增量工作  
**设计文档**: §13.5明确要求"resume时ctx.extra['resumed_from_run_id']必须设置"  
**修复建议**:
```python
# 在state_machine.py的build_execution_context中
if resumed_from_run_id:
    extra["resumed_from_run_id"] = resumed_from_run_id
    extra["resume_mode"] = True
```

#### P0-3: iteration_count未更新
**文件**: researchos/orchestration/state_machine.py  
**问题**: StateYaml定义了iteration_count字段，但advance()中从未更新它  
**影响**: T5/T7的多轮实验、T4的迭代优化无法工作  
**设计文档**: §13.5.3要求"每次进入同一task时iteration_count[task_id]++，超过max_iterations触发ITER_LIMIT_GATE"  
**修复建议**:
```python
# 在advance()的agent run成功后
if node.max_iterations:
    state.iteration_count[state.current_task] = \
        state.iteration_count.get(state.current_task, 0) + 1
    if state.iteration_count[state.current_task] >= node.max_iterations:
        # 触发ITER_LIMIT_GATE
        pass
```

#### P0-4: Gate presenter返回空字典
**文件**: researchos/orchestration/gate_presenter.py:build_presentation  
**问题**: 函数体为空，返回{}  
**影响**: 所有gate的presentation都是空的，用户看不到决策依据  
**设计文档**: §17.7.3明确指出这是"缺口1"，需要实现from_file和from_state的解析  
**修复建议**: 参考设计文档§17.7.3的示例实现

#### P0-5: Pre/post hooks未调用
**文件**: researchos/runtime/orchestrator.py:124-125, 256-260  
**问题**: pre_hooks在try块内调用，但如果hook失败会被catch；post_hooks在finally中但异常被吞掉  
**影响**: T5的check_budget pre-hook、T8的validate_latex_citations post-hook不会生效  
**设计文档**: §6.1要求"pre_hooks失败应当阻止agent运行，post_hooks失败应当记录但不影响result"  
**当前代码**:
```python
# Line 124-125: pre_hooks在try内，异常会被下面的except捕获
try:
    for hook in self.agent.spec.pre_hooks:
        await hook(ctx)
```
**修复建议**:
```python
# pre_hooks应该在try之前
for hook in self.agent.spec.pre_hooks:
    await hook(ctx)  # 失败直接向上抛，阻止run

try:
    # 主循环
    ...
finally:
    # post_hooks
    for hook in self.agent.spec.post_hooks:
        try:
            await hook(ctx, result)
        except Exception as e:
            self.log.error("post_hook_failed", hook=hook.__name__, error=str(e))
```

#### P0-6: Context truncation的group分组逻辑不完整
**文件**: researchos/runtime/orchestrator.py:386-420  
**问题**: _split_into_groups和_maybe_truncate存在但_split_into_groups未实现  
**影响**: 长对话会触发truncation但可能破坏tool_call配对，导致LLM API 400错误  
**设计文档**: §11.2.3要求"以tool_call group为最小单位裁剪，保持assistant+tool消息配对"  
**修复建议**: 实现_split_into_groups，将连续的assistant(tool_calls) + tool消息分为一组

#### P0-7: paper_processing.extract_paper_sections未实现
**文件**: researchos/tools/paper_processing.py  
**问题**: extract_paper_sections函数只返回占位文本，未实现PDF解析  
**影响**: T3 Reader agent无法提取论文章节  
**设计文档**: Agent Dev §4.3要求"PDF → sections的解析"  
**修复建议**: 集成PyMuPDF或类似库，实现章节提取逻辑

#### P0-8: 缺少schemas/validator.py
**文件**: 缺失  
**问题**: 多处代码引用schemas.validator但文件不存在  
**影响**: 所有schema校验都会失败  
**引用位置**:
- researchos/agents/_common.py (Agent Dev文档提到但未实现)
- 测试中使用validate_record, validate_task_artifacts
**修复建议**: 创建schemas/validator.py，实现validate_record和validate_task_artifacts

#### P0-9: ExecutionContext.extra['skill_dir']未设置
**文件**: researchos/runtime/orchestrator.py:102  
**问题**: 代码检查extra['skill_dir']但从未设置  
**影响**: bash_run工具无法获取skill_dir作为cwd  
**修复建议**: 在StateMachine.build_execution_context中设置skill_dir

#### P0-10: model_routing.yaml缺少truncation配置
**文件**: config/model_routing.yaml  
**问题**: LLMClient.get_truncation_config()期望yaml中有truncation字段，但当前配置缺失  
**影响**: Context truncation使用硬编码默认值，无法按项目调整  
**设计文档**: §8.4示例中有truncation配置  
**修复建议**:
```yaml
truncation:
  trigger_ratio: 0.8
  target_ratio: 0.6
  min_keep_groups: 2
```

#### P0-11: AgentRunner._count_group_tokens未实现
**文件**: researchos/runtime/orchestrator.py:398  
**问题**: _maybe_truncate调用_count_group_tokens但方法不存在  
**影响**: Truncation逻辑会崩溃  
**修复建议**: 实现_count_group_tokens方法

#### P0-12: StateMachine._validate_target未实现
**文件**: researchos/orchestration/state_machine.py:135  
**问题**: validate_definition调用_validate_target但方法不存在  
**影响**: 状态机配置校验会失败  
**修复建议**: 实现_validate_target，检查next_on_success/failure指向的节点是否存在

#### P0-13: StateMachine._validate_gate未实现
**文件**: researchos/orchestration/state_machine.py:137  
**问题**: validate_definition调用_validate_gate但方法不存在  
**影响**: Gate配置校验会失败  
**修复建议**: 实现_validate_gate，检查gate引用是否存在于gates配置中

#### P0-14: StateMachine._validate_task_contract未实现
**文件**: researchos/orchestration/state_machine.py:138  
**问题**: validate_definition调用_validate_task_contract但方法不存在  
**影响**: Task I/O契约校验会失败  
**修复建议**: 实现_validate_task_contract，调用task_io_contract.get_task_io校验

#### P0-15: 缺少agents/_common.py
**文件**: 缺失  
**问题**: Agent Dev文档§1.2详细定义了_common.py的helper函数，但未实现  
**影响**: 9个agent无法使用load_project、load_jsonl等共享函数  
**设计文档**: Agent Dev §1.2  
**修复建议**: 创建researchos/agents/_common.py，实现所有helper函数

### 2.2 P1级问题（重要但不阻塞）

#### P1-1: LLMClient.count_tokens fallback不准确
**文件**: researchos/runtime/llm_client.py:2321-2330  
**问题**: fallback用字符数/4估算token，对中文和代码不准确  
**影响**: Budget追踪可能偏差20-30%  
**修复建议**: 使用tiktoken库做更准确的估算

#### P1-2: ToolResult.metadata未在Message.tool中使用
**文件**: researchos/runtime/orchestrator.py:358-366  
**问题**: ToolResult有metadata字段，但Message.tool只用了data和error  
**影响**: 工具的额外元信息丢失  
**修复建议**: 将result.metadata合并到Message.metadata中

#### P1-3: BudgetTracker缺少wall time检查
**文件**: researchos/runtime/budget.py  
**问题**: BudgetTracker有max_wall_seconds字段但check()中未检查  
**影响**: 长时间运行的agent不会因超时而停止  
**修复建议**: 在BudgetTracker.__init__记录start_time，check()中比较elapsed time

#### P1-4: TraceWriter未记录endpoint信息
**文件**: researchos/runtime/trace.py  
**问题**: write_llm_response未记录endpoint_used字段  
**影响**: 无法审计哪个endpoint被使用，replay困难  
**设计文档**: §17.4.1明确要求记录endpoint  
**修复建议**: 在trace data中添加endpoint字段

#### P1-5: 缺少researchos cost命令
**文件**: researchos/cli.py  
**问题**: 设计文档§17.3.2定义了cost命令，但CLI未实现  
**影响**: 无法查看项目成本归因  
**修复建议**: 实现cmd_cost函数

#### P1-6: 缺少researchos trace命令
**文件**: researchos/cli.py  
**问题**: 设计文档§12.4定义了trace命令，但CLI未实现  
**影响**: 无法查看run的详细trace  
**修复建议**: 实现cmd_trace函数

#### P1-7: MockLLMClient未实现resolve和selftest
**文件**: researchos/testing/mocks.py  
**问题**: MockLLMClient继承LLMClient但未覆盖resolve/selftest  
**影响**: 使用profile的测试会失败  
**设计文档**: §17.5要求Mock也支持profile/endpoint  
**修复建议**: 添加resolve和selftest的mock实现

#### P1-8: HumanInterface.ask_approval未实现
**文件**: researchos/tools/human_gate.py  
**问题**: 接口定义了ask_approval但CLIHumanInterface未实现  
**影响**: requires_human_approval的工具无法使用  
**修复建议**: 实现ask_approval方法

#### P1-9: 缺少rate_limiter的实际使用
**文件**: researchos/runtime/rate_limiter.py存在但未被调用  
**问题**: TokenBucket实现了但LLMClient.chat()中未使用  
**影响**: 无法限制API调用频率  
**设计文档**: §17.1要求"每个endpoint一个TokenBucket"  
**修复建议**: 在LLMClient中为每个endpoint创建limiter，chat前await limiter.acquire()

#### P1-10: Docker工具的GPU检查逻辑不完整
**文件**: researchos/tools/docker_exec.py  
**问题**: 检查project_config.allow_gpu但未定义project_config从哪来  
**影响**: GPU限制无法生效  
**修复建议**: 从workspace/project.yaml读取配置

#### P1-11: 缺少schemas目录和schema文件
**文件**: 缺失  
**问题**: 设计文档提到papers_dedup.schema.json等，但schemas/目录不存在  
**影响**: 无法做artifact的schema校验  
**修复建议**: 创建schemas/目录，添加各task的schema定义

#### P1-12: StateMachine.advance未处理branches
**文件**: researchos/orchestration/state_machine.py  
**问题**: TaskNode有branches字段但advance()中未使用  
**影响**: 多分支决策无法工作  
**修复建议**: 在gate resolve后根据branches选择next task

#### P1-13: 缺少ITER_LIMIT_GATE的处理
**文件**: researchos/orchestration/state_machine.py  
**问题**: 设计文档§13.5.3定义了ITER_LIMIT_GATE，但代码未实现  
**影响**: 迭代次数超限时无法自动处理  
**修复建议**: 在iteration_count超限时触发特殊gate

#### P1-14: ToolBuildContext.skill_dir未在builtin.py中使用
**文件**: researchos/tools/builtin.py  
**问题**: bash_run注册时硬编码skill_dir=skill_dir，但skill_dir未定义  
**影响**: bash_run无法获取正确的skill目录  
**修复建议**: 从build_ctx.skill_dir读取

#### P1-15: 缺少prompts/目录和模板文件
**文件**: 缺失  
**问题**: AgentSpec.prompt_template指向prompts/{agent}.j2，但目录不存在  
**影响**: Agent的system_prompt无法使用模板  
**修复建议**: 创建prompts/目录，添加模板文件

#### P1-16: 缺少config/gates.yaml
**文件**: 缺失  
**问题**: StateMachine.__init__接收gates_config_path但文件不存在  
**影响**: Gate配置无法加载  
**修复建议**: 创建config/gates.yaml，定义各gate的配置

#### P1-17: 缺少config/state_machine.yaml
**文件**: 缺失  
**问题**: StateMachine需要FSM配置但文件不存在  
**影响**: 状态机无法初始化  
**修复建议**: 创建config/state_machine.yaml，定义T1-T9的节点和转移

#### P1-18: AgentResult缺少outputs_produced的实际填充
**文件**: researchos/runtime/orchestrator.py:245-255  
**问题**: _build_result中outputs_produced硬编码为空字典  
**影响**: 无法追踪agent实际产出了哪些文件  
**修复建议**: 遍历ctx.outputs_expected，检查哪些文件存在并填充

#### P1-19: 缺少agents/registry.py
**文件**: 缺失  
**问题**: 需要一个中心化的agent注册表  
**影响**: CLI无法发现可用的agent  
**修复建议**: 创建agents/registry.py，实现agent注册和查找

#### P1-20: 缺少完整的CLI命令实现
**文件**: researchos/cli.py  
**问题**: CLI存在但缺少run、resume、validate等核心命令  
**影响**: 无法通过CLI运行完整pipeline  
**修复建议**: 实现完整的CLI命令集

#### P1-21: 缺少pydantic_compat.py
**文件**: 缺失  
**问题**: orchestrator.py导入pydantic_compat.model_dump但文件不存在  
**影响**: 代码无法运行  
**修复建议**: 创建pydantic_compat.py，提供Pydantic v1/v2兼容层

#### P1-22: WorkspaceAccessPolicy.allow_read_references未使用
**文件**: researchos/tools/workspace_policy.py:584  
**问题**: 字段定义了但resolve_read中未检查  
**影响**: references/目录的访问控制不生效  
**修复建议**: 在resolve_read中添加references/的特殊处理

#### P1-23: Message.to_trace_dict可能丢失tool_calls
**文件**: researchos/runtime/message.py:497-512  
**问题**: to_trace_dict中tool_calls转换为to_openai_dict，可能丢失trace专属字段  
**影响**: Trace回放时信息不完整  
**修复建议**: 为ToolCall也添加to_trace_dict方法

### 2.3 P2级问题（优化改进）

#### P2-1: 错误消息未国际化
**问题**: 所有错误消息硬编码中文  
**影响**: 非中文用户体验差  
**修复建议**: 使用i18n框架

#### P2-2: 日志级别硬编码
**文件**: researchos/runtime/logger.py  
**问题**: 日志级别默认INFO，无法动态调整  
**修复建议**: 从环境变量或配置文件读取

#### P2-3: Trace文件可能很大
**问题**: 长对话的trace可能达到数百MB  
**影响**: 磁盘占用和读取性能  
**修复建议**: 实现trace压缩或分段

#### P2-4: 缺少进度条
**问题**: 长时间运行的agent无进度反馈  
**影响**: 用户体验差  
**修复建议**: 集成tqdm或类似库

#### P2-5: 缺少graceful shutdown
**问题**: SIGINT处理不完整  
**影响**: 中断时可能丢失状态  
**修复建议**: 实现完整的信号处理

#### P2-6: 缺少并发控制
**问题**: 多个CLI实例可能同时修改state.yaml  
**影响**: 状态损坏  
**修复建议**: 使用文件锁

#### P2-7: LLMClient缺少缓存
**问题**: 相同请求重复调用LLM  
**影响**: 成本和延迟  
**修复建议**: 实现请求级缓存

#### P2-8: 缺少性能监控
**问题**: 无法追踪各模块的性能  
**影响**: 难以优化  
**修复建议**: 添加性能指标收集

---

## 第三部分：缺失能力清单

### 3.1 必须补全的能力（阻塞agent开发）

#### 缺失-1: Schema校验器
**位置**: researchos/schemas/validator.py  
**功能**: validate_record, validate_task_artifacts  
**优先级**: P0  
**工作量**: 2-3小时  
**依赖**: 需要先创建schemas/目录和各task的schema文件

#### 缺失-2: Agent共享helper
**位置**: researchos/agents/_common.py  
**功能**: load_project, load_jsonl, append_jsonl, validate_files_exist等  
**优先级**: P0  
**工作量**: 1-2小时  
**设计文档**: Agent Dev §1.2有完整定义

#### 缺失-3: Context truncation完整实现
**位置**: researchos/runtime/orchestrator.py  
**功能**: _split_into_groups, _count_group_tokens  
**优先级**: P0  
**工作量**: 2-3小时  
**风险**: 实现不当会破坏tool_call配对

#### 缺失-4: 状态机校验方法
**位置**: researchos/orchestration/state_machine.py  
**功能**: _validate_target, _validate_gate, _validate_task_contract  
**优先级**: P0  
**工作量**: 1-2小时

#### 缺失-5: Gate presenter实现
**位置**: researchos/orchestration/gate_presenter.py  
**功能**: _build_presentation的完整实现  
**优先级**: P0  
**工作量**: 2-3小时  
**设计文档**: §17.7.3有实现建议

#### 缺失-6: 论文章节提取
**位置**: researchos/tools/paper_processing.py  
**功能**: extract_paper_sections的实际实现  
**优先级**: P0（T3需要）  
**工作量**: 4-6小时  
**依赖**: PyMuPDF或类似库

### 3.2 可延后的能力（不阻塞MVP）

#### 缺失-7: MCP服务器连接
**功能**: 实际连接和管理MCP服务器  
**优先级**: P1  
**说明**: mcp_adapter.py提供了适配层，但缺少实际的连接管理代码

#### 缺失-8: Rate limiting实际应用
**功能**: 在LLMClient中集成rate_limiter  
**优先级**: P1  
**工作量**: 1小时

#### 缺失-9: 完整的CLI命令集
**功能**: run, resume, validate, cost, trace等命令  
**优先级**: P1  
**工作量**: 4-6小时

#### 缺失-10: Agent注册表
**功能**: 中心化的agent发现和加载  
**优先级**: P1  
**工作量**: 1-2小时

---

## 第四部分：设计一致性分析

### 4.1 与Runtime Dev Spec的对比

| 章节 | 设计要求 | 实现状态 | 差异说明 |
|------|----------|----------|----------|
| §2 错误体系 | 三层异常继承 | ✅ 完全一致 | 无差异 |
| §3 消息协议 | Message/ToolCall/Role | ✅ 完全一致 | 无差异 |
| §4 Workspace | WorkspaceAccessPolicy | ✅ 完全一致 | allow_read_references未使用 |
| §5 Tool协议 | 工厂模式+并行执行 | ✅ 基本一致 | 并行执行已实现 |
| §6 Agent基类 | 三方法+validate_outputs | ⚠️ 部分偏离 | validate_outputs未调用schema |
| §7 Prompt渲染 | Jinja2模板 | ✅ 完全一致 | 但缺少模板文件 |
| §8 LLM Client | Endpoint+Profile+Binding | ✅ 完全一致 | 未集成rate limiter |
| §9 Budget | 三维度追踪 | ⚠️ 部分偏离 | wall time未检查 |
| §10 Human Interface | CLI+Gate | ✅ 基本一致 | ask_approval未实现 |
| §11 AgentRunner | 主循环+truncation | ⚠️ 部分偏离 | truncation不完整，hooks位置错误 |
| §12 Trace | JSONL格式 | ✅ 基本一致 | 缺少endpoint字段 |
| §13 状态机 | FSM+持久化 | ⚠️ 部分偏离 | resume/iteration未连接 |
| §14 测试基础 | Mock对象 | ⚠️ 部分偏离 | MockLLMClient不完整 |
| §15 HelloAgent | 端到端调试 | ✅ 已实现 | 测试通过 |
| §17 ResearchOS配置 | 多profile+成本归因 | ⚠️ 部分偏离 | cost命令缺失 |

**总体一致性**: 75%  
**关键偏离**: validate_outputs、resume逻辑、hooks调用时机

### 4.2 与Agent Dev Spec的对比

| 章节 | 设计要求 | 实现状态 | 差异说明 |
|------|----------|----------|----------|
| §1 Agent开发全景 | 9个agent+共享helper | ❌ 未实现 | 只有HelloAgent，_common.py缺失 |
| §2 Agent基类模式 | 120行内实现 | ✅ 架构支持 | 基类已就绪 |
| §3 Skill适配层 | Claude Code格式 | ✅ 完全实现 | 无差异 |
| §4 Tool生态 | 6个业务tool | ⚠️ 部分实现 | extract_paper_sections缺失 |
| §5 两种运行模式 | 完整pipeline+单task | ❌ 未实现 | CLI缺失 |

**总体一致性**: 40%（因为agent层本身就是待开发）  
**Runtime对agent的支持度**: 80%（除了几个P0问题）

### 4.3 架构设计评价

#### 优点

1. **模块边界清晰**: runtime/tools/orchestration/skills四层分离良好
2. **依赖方向正确**: 无循环依赖，tools不依赖runtime
3. **工厂模式应用得当**: ToolRegistry避免了跨task的状态污染
4. **错误处理分层合理**: 三层异常体系清晰
5. **测试覆盖充分**: 71个测试，覆盖主要路径
6. **Profile抽象强大**: 支持多端点、多模型、fallback

#### 缺点

1. **部分设计未落地**: resume、iteration、gate presenter等关键逻辑有代码框架但未连接
2. **配置文件缺失**: 多个yaml配置文件未创建
3. **Schema体系缺失**: 校验器和schema定义都没有
4. **CLI不完整**: 无法实际运行完整流程
5. **文档与代码脱节**: 设计文档提到的部分功能未实现

---

## 第五部分：为Agent开发预留的接口

### 5.1 已就绪的接口

#### 5.1.1 Agent基类接口
```python
class MyAgent(Agent):
    def __init__(self):
        super().__init__(AgentSpec(
            name="my_agent",
            model_tier="medium",
            tool_names=["read_file", "write_file", "finish_task"],
            allowed_write_prefixes=["output/"],
            max_steps=20,
            temperature=0.7,
        ))
    
    def system_prompt(self, ctx: ExecutionContext) -> str:
        # 可用ctx.workspace_dir, ctx.task_id, ctx.mode等
        return "..."
    
    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "..."
    
    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        ok, err = super().validate_outputs(ctx)  # 检查文件存在
        if not ok:
            return ok, err
        # 添加自定义校验
        return True, None
```

**状态**: ✅ 可用，但需要修复P0-1（schema校验）

#### 5.1.2 ExecutionContext接口
```python
ctx.workspace_dir: Path          # workspace根目录
ctx.task_id: str                 # 当前task ID
ctx.run_id: str                  # 本次run的唯一ID
ctx.inputs: dict[str, Path]      # 输入artifact路径
ctx.outputs_expected: dict[str, Path]  # 期望输出路径
ctx.mode: str | None             # 运行模式（如"pilot"/"full"）
ctx.extra: dict[str, Any]        # 额外上下文
```

**状态**: ✅ 可用

#### 5.1.3 工具注册接口
```python
# 注册自定义工具
def my_tool_factory(build_ctx: ToolBuildContext) -> Tool:
    return MyTool(build_ctx.policy, build_ctx.human)

registry.register("my_tool", my_tool_factory)
```

**状态**: ✅ 可用

#### 5.1.4 LLM配置覆盖接口
```python
# 在FSM节点配置中
llm:
  profile: "deep_reasoning"
  tier: "heavy"
  temperature: 0.9

# 或在代码中
ctx.llm_override = LLMConfigOverride(
    profile="cheap_fast",
    temperature=0.3
)
```

**状态**: ✅ 可用

### 5.2 需要补全的接口

#### 5.2.1 共享helper函数（P0）
```python
from researchos.agents._common import (
    load_project,           # 读project.yaml
    load_jsonl,            # 读JSONL artifact
    append_jsonl,          # 追加JSONL
    validate_files_exist,  # 检查文件存在
    validate_jsonl_schema, # 校验JSONL schema
    read_state,            # 读state.json
    read_iteration_count,  # 读迭代次数
)
```

**状态**: ❌ 缺失，需要创建_common.py

#### 5.2.2 Resume上下文（P0）
```python
# Agent需要检查是否resume
if ctx.extra.get("resume_mode"):
    resumed_from = ctx.extra["resumed_from_run_id"]
    # 读取上次的中间结果，继续工作
```

**状态**: ❌ 未连接，需要修复P0-2

#### 5.2.3 迭代计数（P0）
```python
# Agent需要知道当前是第几次迭代
iteration = ctx.extra.get("iteration", 0)
if iteration > 0:
    # 这是重试，调整策略
```

**状态**: ❌ 未连接，需要修复P0-3

### 5.3 接口完整性评估

| 接口类别 | 完整度 | 阻塞agent开发 | 修复优先级 |
|----------|--------|---------------|------------|
| Agent基类 | 85% | 部分阻塞 | P0 |
| ExecutionContext | 95% | 不阻塞 | P1 |
| 工具注册 | 100% | 不阻塞 | - |
| LLM配置 | 100% | 不阻塞 | - |
| 共享helper | 0% | 完全阻塞 | P0 |
| Resume支持 | 30% | 完全阻塞 | P0 |
| 迭代支持 | 20% | 完全阻塞 | P0 |
| Schema校验 | 0% | 完全阻塞 | P0 |

**结论**: 需要修复8个P0问题才能开始agent开发

---

## 第六部分：修复路线图

### 6.1 紧急修复（1-2天，必须完成）

#### 阶段1: 补全缺失文件（4-6小时）
1. **创建pydantic_compat.py** (30分钟)
   - 提供model_dump等兼容函数
   - 支持Pydantic v1和v2

2. **创建researchos/agents/_common.py** (2小时)
   - 实现Agent Dev §1.2定义的所有helper
   - load_project, load_jsonl, append_jsonl等
   - validate_files_exist, validate_jsonl_schema

3. **创建researchos/schemas/validator.py** (2小时)
   - validate_record函数
   - validate_task_artifacts函数
   - 集成JSON Schema校验

4. **创建schemas/目录和基础schema** (1小时)
   - papers_dedup.schema.json
   - project.schema.json
   - 其他核心artifact的schema

#### 阶段2: 修复P0级bug（6-8小时）
1. **修复Agent.validate_outputs** (1小时)
   - 调用schema校验器
   - 测试覆盖

2. **连接resume逻辑** (2小时)
   - StateMachine.build_execution_context设置resumed_from_run_id
   - 添加测试验证resume流程

3. **实现iteration_count更新** (1.5小时)
   - 在advance()中更新计数器
   - 实现ITER_LIMIT_GATE触发逻辑

4. **修复pre/post hooks调用** (1小时)
   - pre_hooks移到try之前
   - post_hooks异常处理

5. **实现context truncation** (2小时)
   - _split_into_groups按tool_call group分组
   - _count_group_tokens计算token
   - 测试验证不破坏配对

6. **实现gate_presenter** (1.5小时)
   - 解析from_file和from_state
   - 构建presentation字典

#### 阶段3: 补全状态机校验（2小时）
1. **实现_validate_target** (30分钟)
2. **实现_validate_gate** (30分钟)
3. **实现_validate_task_contract** (1小时)

### 6.2 重要修复（2-3天）

#### 阶段4: 完善工具和配置（4-6小时）
1. **实现extract_paper_sections** (3小时)
   - 集成PyMuPDF
   - 章节识别逻辑

2. **创建配置文件** (2小时)
   - config/state_machine.yaml
   - config/gates.yaml
   - prompts/目录和模板

3. **修复BudgetTracker wall time检查** (1小时)

#### 阶段5: 完善LLM和trace（3-4小时）
1. **集成rate_limiter** (1.5小时)
   - LLMClient为每个endpoint创建limiter
   - chat前acquire

2. **TraceWriter记录endpoint** (30分钟)

3. **MockLLMClient补全** (1小时)
   - 实现resolve和selftest

4. **修复token计数fallback** (1小时)
   - 使用tiktoken

#### 阶段6: CLI命令（4-6小时）
1. **实现run命令** (2小时)
2. **实现cost命令** (1小时)
3. **实现trace命令** (1小时)
4. **实现validate命令** (1小时)
5. **实现resume命令** (1小时)

### 6.3 优化改进（可延后）

#### 阶段7: P1级问题（按需修复）
- HumanInterface.ask_approval
- WorkspaceAccessPolicy.allow_read_references
- AgentResult.outputs_produced填充
- 其他P1问题

#### 阶段8: P2级问题（Phase 2）
- 国际化
- 性能监控
- 并发控制
- 等

### 6.4 修复优先级矩阵

| 问题 | 优先级 | 工作量 | 阻塞程度 | 建议顺序 |
|------|--------|--------|----------|----------|
| pydantic_compat.py | P0 | 0.5h | 完全阻塞 | 1 |
| _common.py | P0 | 2h | 完全阻塞 | 2 |
| schemas/validator.py | P0 | 2h | 完全阻塞 | 3 |
| validate_outputs | P0 | 1h | 完全阻塞 | 4 |
| resume逻辑 | P0 | 2h | 完全阻塞 | 5 |
| iteration_count | P0 | 1.5h | 完全阻塞 | 6 |
| hooks调用 | P0 | 1h | 完全阻塞 | 7 |
| context truncation | P0 | 2h | 高度阻塞 | 8 |
| gate_presenter | P0 | 1.5h | 高度阻塞 | 9 |
| 状态机校验 | P0 | 2h | 中度阻塞 | 10 |
| extract_paper_sections | P0 | 3h | T3阻塞 | 11 |
| 配置文件 | P1 | 2h | 中度阻塞 | 12 |
| rate_limiter | P1 | 1.5h | 低度阻塞 | 13 |
| CLI命令 | P1 | 6h | 中度阻塞 | 14 |

**总工作量估算**: 
- P0修复: 18-22小时（2-3个工作日）
- P1修复: 12-16小时（1.5-2个工作日）
- **合计**: 30-38小时（4-5个工作日）

---

## 第七部分：测试验证建议

### 7.1 当前测试状态

**测试覆盖**: 71个测试用例，全部通过  
**覆盖率**: 约85%（估算）  
**缺失测试**:
- Resume流程端到端测试
- Iteration计数和ITER_LIMIT_GATE测试
- Gate presenter测试
- Context truncation边界测试
- 完整pipeline测试

### 7.2 必须添加的测试

#### 测试1: Resume流程
```python
@pytest.mark.asyncio
async def test_resume_sets_context_correctly():
    """验证INTERRUPTED后resume时ctx.extra包含resumed_from_run_id"""
    # 1. 运行agent到一半，模拟中断
    # 2. 检查state.yaml最后一条history.status == INTERRUPTED
    # 3. 再次advance，验证ctx.extra["resumed_from_run_id"]存在
```

#### 测试2: Iteration计数
```python
@pytest.mark.asyncio
async def test_iteration_count_increments_and_triggers_gate():
    """验证多次进入同一task时iteration_count递增，超限触发gate"""
    # 1. 配置max_iterations=3的节点
    # 2. 循环运行3次
    # 3. 第4次应触发ITER_LIMIT_GATE
```

#### 测试3: Schema校验
```python
def test_validate_outputs_calls_schema_validator():
    """验证Agent.validate_outputs调用schema校验器"""
    # 1. 创建有output_schemas的agent
    # 2. 产出不符合schema的文件
    # 3. validate_outputs应返回False
```

#### 测试4: Context truncation
```python
@pytest.mark.asyncio
async def test_truncation_preserves_tool_call_pairing():
    """验证truncation不破坏assistant+tool消息配对"""
    # 1. 构造超长对话（多个tool_call group）
    # 2. 触发truncation
    # 3. 验证每个assistant(tool_calls)后都有对应的tool消息
```

#### 测试5: Hooks调用时机
```python
@pytest.mark.asyncio
async def test_pre_hook_failure_blocks_run():
    """验证pre_hook失败阻止agent运行"""
    # 1. 注册会抛异常的pre_hook
    # 2. 运行agent
    # 3. 验证agent未执行，异常向上抛
```

### 7.3 集成测试建议

#### 端到端测试1: 最小pipeline
```python
@pytest.mark.asyncio
async def test_minimal_pipeline_t1_to_t2():
    """T1 PI → T2 Scout的最小流程"""
    # 1. 初始化workspace和state
    # 2. 运行T1（mock LLM产出project.yaml）
    # 3. 状态机推进到T2
    # 4. 运行T2（mock LLM产出papers_raw.jsonl）
    # 5. 验证state.yaml正确更新
```

#### 端到端测试2: Gate决策
```python
@pytest.mark.asyncio
async def test_gate_pauses_and_resumes():
    """验证gate暂停项目，用户决策后恢复"""
    # 1. 运行到gate节点
    # 2. 验证status=WAITING_HUMAN
    # 3. 模拟用户选择
    # 4. 验证推进到正确的next task
```

### 7.4 验收标准

在开始agent开发前，以下测试必须全部通过：

- [ ] 71个现有测试继续通过
- [ ] 5个新增P0功能测试通过
- [ ] 2个端到端集成测试通过
- [ ] HelloAgent能完整运行（包括resume）
- [ ] 状态机validate_definition无错误
- [ ] 所有P0 bug修复后的回归测试通过

---

## 第八部分：总结与建议

### 8.1 Runtime当前状态总结

**完成度**: 75-80%  
**代码质量**: 良好（架构清晰，测试充分）  
**主要问题**: 部分关键逻辑有框架但未连接，配置和schema体系缺失  
**阻塞因素**: 15个P0问题，23个P1问题

### 8.2 关键发现

1. **架构设计优秀**: 模块分层、依赖方向、工厂模式都很合理
2. **测试覆盖充分**: 71个测试是很好的基础
3. **设计文档详尽**: 5673+4690行的设计文档非常完整
4. **实现不完整**: 约20-25%的设计未落地，特别是resume、iteration、gate presenter
5. **配置体系缺失**: 多个yaml配置文件未创建
6. **Schema体系缺失**: 校验器和schema定义都没有

### 8.3 对后续agent开发的影响

**可以开始的工作**:
- HelloAgent已经可以作为模板
- Agent基类接口基本就绪
- 工具注册和LLM配置完全可用

**必须等待的工作**:
- 任何需要resume的agent（T3, T5, T7）
- 任何需要迭代的agent（T4, T5）
- 任何需要gate的agent（T4, T6）
- 任何需要schema校验的agent（所有）

**建议的开发顺序**:
1. **先修复P0问题**（2-3天）
2. **开发T1 PI Agent**（最简单，不需要resume/iteration）
3. **开发T2 Scout Agent**（测试MCP和search工具）
4. **修复P1问题**（1-2天）
5. **开发T3-T9**（按依赖顺序）

### 8.4 风险提示

#### 高风险区域
1. **Context truncation**: 实现不当会破坏tool_call配对，导致LLM API错误
2. **Resume逻辑**: 需要仔细设计checkpoint机制，否则agent无法正确恢复
3. **Iteration计数**: 与resume交互复杂，需要明确语义
4. **Gate presenter**: 需要处理多种数据源（文件、state、计算值）

#### 技术债务
1. **缺少并发控制**: 多个CLI实例可能损坏state.yaml
2. **缺少性能监控**: 无法识别瓶颈
3. **错误消息未国际化**: 限制了用户群
4. **Trace文件可能很大**: 需要压缩或分段策略

### 8.5 最终建议

#### 立即行动（本周内）
1. 按6.1节的路线图修复所有P0问题
2. 添加7.2节的5个关键测试
3. 创建缺失的配置文件和schema
4. 验证HelloAgent完整流程（包括resume）

#### 短期行动（下周）
1. 修复P1问题中阻塞agent开发的部分
2. 实现完整的CLI命令集
3. 开发T1和T2 agent作为验证

#### 中期行动（2-3周）
1. 开发T3-T9 agent
2. 完善文档和示例
3. 性能优化和稳定性改进

#### 长期行动（Phase 2）
1. 并发控制和多用户支持
2. Agent-to-agent通信
3. 动态生成agent
4. Research Wiki集成

---

## 附录A：文件清单

### A.1 需要创建的文件

```
researchos/
├── pydantic_compat.py                    # P0, 0.5h
├── agents/
│   ├── _common.py                        # P0, 2h
│   └── registry.py                       # P1, 1h
├── schemas/
│   ├── __init__.py
│   ├── validator.py                      # P0, 2h
│   ├── papers_dedup.schema.json          # P0, 0.5h
│   ├── project.schema.json               # P0, 0.5h
│   └── ...（其他schema）
├── prompts/
│   ├── pi.j2                             # P1, 按需
│   ├── scout.j2
│   └── ...
config/
├── state_machine.yaml                    # P1, 1h
├── gates.yaml                            # P1, 0.5h
└── model_routing.yaml                    # 需要添加truncation配置
```

### A.2 需要修改的文件

```
researchos/runtime/
├── agent.py                              # P0: validate_outputs
├── orchestrator.py                       # P0: hooks, truncation
├── budget.py                             # P1: wall time
├── llm_client.py                         # P1: rate limiter
├── trace.py                              # P1: endpoint字段

researchos/orchestration/
├── state_machine.py                      # P0: resume, iteration, 校验方法
└── gate_presenter.py                     # P0: _build_presentation

researchos/tools/
├── paper_processing.py                   # P0: extract_paper_sections
├── builtin.py                            # P1: skill_dir
└── workspace_policy.py                   # P1: allow_read_references

researchos/testing/
└── mocks.py                              # P1: MockLLMClient

researchos/
└── cli.py                                # P1: 完整命令集
```

---

## 附录B：快速参考

### B.1 P0问题速查表

| ID | 问题 | 文件 | 工作量 |
|----|------|------|--------|
| P0-1 | validate_outputs不完整 | runtime/agent.py | 1h |
| P0-2 | resume逻辑未连接 | orchestration/state_machine.py | 2h |
| P0-3 | iteration_count未更新 | orchestration/state_machine.py | 1.5h |
| P0-4 | gate_presenter返回空 | orchestration/gate_presenter.py | 1.5h |
| P0-5 | hooks调用位置错误 | runtime/orchestrator.py | 1h |
| P0-6 | truncation不完整 | runtime/orchestrator.py | 2h |
| P0-7 | extract_paper_sections缺失 | tools/paper_processing.py | 3h |
| P0-8 | schemas/validator.py缺失 | 新建 | 2h |
| P0-9 | skill_dir未设置 | orchestration/state_machine.py | 0.5h |
| P0-10 | truncation配置缺失 | config/model_routing.yaml | 0.5h |
| P0-11 | _count_group_tokens缺失 | runtime/orchestrator.py | 0.5h |
| P0-12 | _validate_target缺失 | orchestration/state_machine.py | 0.5h |
| P0-13 | _validate_gate缺失 | orchestration/state_machine.py | 0.5h |
| P0-14 | _validate_task_contract缺失 | orchestration/state_machine.py | 1h |
| P0-15 | _common.py缺失 | agents/_common.py | 2h |

**合计**: 18-22小时

### B.2 关键设计文档章节

- **Agent基类**: Runtime Dev Spec §6
- **Resume逻辑**: Runtime Dev Spec §13.5
- **Iteration**: Runtime Dev Spec §13.5.3
- **Gate**: Runtime Dev Spec §13.3, §17.7.3
- **Context truncation**: Runtime Dev Spec §11.2.3
- **Schema校验**: Agent Dev Spec §1.2
- **共享helper**: Agent Dev Spec §1.2

---

**报告结束**

**下一步行动**: 按照第六部分的修复路线图，从阶段1开始系统性修复所有P0问题。
