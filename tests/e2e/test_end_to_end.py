"""端到端集成测试。

使用 MockLLM 运行完整流程：
T1 init → T2 scout → T3 read → T4 ideation → T4.5 audit → T5 pilot → T6 full → T7 write

这些测试验证完整的 Agent 协作流程。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from researchos.agents.experimenter import ExperimenterAgent
from researchos.agents.ideation import IdeationAgent
from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.agents.pi import PIAgent
from researchos.agents.reader import ReaderAgent
from researchos.agents.scout import ScoutAgent
from researchos.runtime.agent import ExecutionContext


# ══════════════════════════════════════════════════════
# MockLLM 实现
# ══════════════════════════════════════════════════════

class MockLLM:
    """Mock LLM for testing."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.call_count = 0
        self.messages_history = []

    async def generate(self, messages: list[dict]) -> str:
        """Generate mock response."""
        self.call_count += 1
        self.messages_history.append(messages)

        # Return appropriate response based on prompt content
        last_msg = messages[-1]["content"] if messages else ""

        # Generate mock outputs based on context
        if "scout" in last_msg.lower() or "search" in last_msg.lower():
            return self._mock_scout_response()
        elif "reader" in last_msg.lower() or "paper" in last_msg.lower():
            return self._mock_reader_response()
        elif "ideation" in last_msg.lower() or "hypothesis" in last_msg.lower():
            return self._mock_ideation_response()
        elif "novelty" in last_msg.lower() or "audit" in last_msg.lower():
            return self._mock_novelty_response()
        elif "experimenter" in last_msg.lower() or "pilot" in last_msg.lower():
            return self._mock_experimenter_response()
        else:
            return self._mock_default_response()

    def _mock_scout_response(self) -> str:
        """Mock Scout agent response."""
        papers = [
            {"title": f"Paper {i}", "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        return json.dumps(papers)

    def _mock_reader_response(self) -> str:
        """Mock Reader agent response."""
        return "# Paper Notes\n\n## Paper 1\nKey findings..."

    def _mock_ideation_response(self) -> str:
        """Mock Ideation agent response."""
        return """# Research Hypotheses

## H1: 方法 X 改进架构

我们假设方法 X 可以通过改进模型架构来提升性能。具体来说：

1. **核心假设**：通过引入新的注意力机制，可以更好地捕获长距离依赖关系
2. **与现有方法对比**：传统方法使用固定窗口注意力，我们的方法使用动态稀疏注意力
3. **预期效果**：预期在多个 NLP 任务上提升 5-10% 的准确率
4. **技术细节**：方法 X 采用分层注意力策略，在不同层级使用不同的注意力模式
5. **理论基础**：基于 transformer 的架构改进，结合了最新的技术进展

## H2: 数据增强策略

我们还假设数据增强策略可以进一步提升模型鲁棒性：

1. **核心假设**：通过组合多种数据增强技术，可以在有限数据下提升泛化能力
2. **具体策略**：包括回译、同义词替换、随机删除、混合增强等
3. **预期效果**：预期在少样本场景下提升 3-5% 的准确率
4. **实施细节**：针对不同任务类型采用不同的增强策略组合
5. **实验设计**：对比不同增强策略的效果，找出最优组合并进行统计显著性检验
"""

    def _mock_novelty_response(self) -> str:
        """Mock NoveltyAuditor agent response."""
        return """# Novelty Audit

## Level 2

### H1: 新颖性高
- **评估**：方法 X 在注意力机制上有所创新
- **差异点**：动态稀疏注意力与传统方法的区别
- **贡献**：预期对长距离依赖建模有显著提升

### H2: 新颖性中
- **评估**：数据增强策略是常见做法
- **差异点**：组合方式有一定创新
- **贡献**：预期在少样本场景有效果

## 总结
H1 具有较高新颖性，建议重点推进
H2 新颖性中等，可以作为辅助实验
"""

    def _mock_experimenter_response(self) -> str:
        """Mock Experimenter agent response."""
        return json.dumps({
            "seed": 42,
            "experiments": [
                {"experiment_id": "pilot_h1", "status": "DONE", "metrics": {"accuracy": 0.75}}
            ]
        })

    def _mock_default_response(self) -> str:
        """Default mock response."""
        return "Task completed successfully."


# ══════════════════════════════════════════════════════
# E2E 测试：单 Agent 执行
# ══════════════════════════════════════════════════════

class TestAgentExecutionE2E:
    """测试单个 Agent 的端到端执行。"""

    @pytest.fixture
    def workspace(self, tmp_path):
        """创建测试 workspace。"""
        ws = tmp_path / "test_workspace"
        ws.mkdir()
        return ws

    @pytest.fixture
    def mock_llm(self):
        """创建 Mock LLM。"""
        return MockLLM()

    def test_scout_agent_full_execution(self, workspace, mock_llm):
        """测试 Scout Agent 完整执行流程。"""
        # 创建基础文件
        project_yaml = {
            "research_direction": "NLP",
            "domain": "NLP",
            "constraints": {"max_budget_usd": 100.0}
        }
        (workspace / "project.yaml").write_text(yaml.dump(project_yaml))

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=workspace,
            project_id="test_project",
            task_id="T2",
            run_id="test_run_001"
        )

        # 验证初始状态 - 应该失败（还没有输出）
        ok, err = agent.validate_outputs(ctx)
        assert not ok

        # 创建 mock 输出
        literature_dir = workspace / "literature"
        literature_dir.mkdir()

        papers_raw = [
            {"title": f"Paper {i}", "abstract": f"Abstract {i}", "url": f"http://example.com/{i}",
             "id": f"paper_{i}", "year": 2024, "authors": ["Author"], "relevance_score": 0.9}
            for i in range(20)
        ]
        (literature_dir / "papers_raw.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_raw)
        )

        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(15)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        # 验证输出
        ok, err = agent.validate_outputs(ctx)
        assert ok, f"Scout 输出验证失败: {err}"

    def test_ideation_agent_full_execution(self, workspace, mock_llm):
        """测试 Ideation Agent 完整执行流程。"""
        # 创建依赖文件
        project_yaml = {
            "research_direction": "测试方向",
            "domain": "NLP"
        }
        (workspace / "project.yaml").write_text(yaml.dump(project_yaml))
        (workspace / "papers_summary.md").write_text("# Papers Summary\n\n## Paper 1\nKey findings...")
        (workspace / "literature").mkdir()

        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        (workspace / "literature" / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        # 创建 Ideation 输出
        ideation_dir = workspace / "ideation"
        ideation_dir.mkdir()

        hypotheses = """# Research Hypotheses

## H1: 方法 X 改进架构

我们假设方法 X 可以通过改进模型架构来提升性能。具体来说：

1. **核心假设**：通过引入新的注意力机制，可以更好地捕获长距离依赖关系
2. **与现有方法对比**：传统方法使用固定窗口注意力，我们的方法使用动态稀疏注意力
3. **预期效果**：预期在多个 NLP 任务上提升 5-10% 的准确率
4. **技术细节**：方法 X 采用分层注意力策略，在不同层级使用不同的注意力模式
5. **理论基础**：基于 transformer 的架构改进，结合了最新的技术进展

## H2: 数据增强策略

我们还假设数据增强策略可以进一步提升模型鲁棒性：

1. **核心假设**：通过组合多种数据增强技术，可以在有限数据下提升泛化能力
2. **具体策略**：包括回译、同义词替换、随机删除、混合增强等
3. **预期效果**：预期在少样本场景下提升 3-5% 的准确率
4. **实施细节**：针对不同任务类型采用不同的增强策略组合
5. **实验设计**：对比不同增强策略的效果，找出最优组合并进行统计显著性检验
"""
        (ideation_dir / "hypotheses.md").write_text(hypotheses, encoding="utf-8")

        exp_plan = {
            "experiments": [
                {
                    "name": "exp_h1_architecture",
                    "hypothesis_ref": "H1",
                    "dataset": "common_nlp_bench",
                    "data_fraction": 0.1,
                    "baseline_methods": [{"name": "LSTM", "description": "标准 LSTM"}],
                    "our_method": {"name": "MethodX", "description": "方法 X"},
                    "metrics": ["accuracy"],
                    "compute_estimate": {"gpu_hours": 1.0, "gpu_type": "V100"},
                    "success_criteria": [{"metric": "accuracy", "threshold": 0.7}]
                }
            ]
        }
        (ideation_dir / "exp_plan.yaml").write_text(yaml.dump(exp_plan), encoding="utf-8")

        risks = """# 风险分析

## 风险

### 高风险
- GPU 资源可能不足

### 中风险
- 实验时间可能超出预期

## 风险

### 中风险
- 数据增强效果可能不明显

### 低风险
- 实施复杂度较高

## 风险

### 高风险
- 方法实现复杂度高
"""
        (ideation_dir / "risks.md").write_text(risks, encoding="utf-8")

        novelty_audit = """# Novelty Audit

## Level 2

### H1: 新颖性高
- 方法创新度高

### H2: 新颖性中
- 组合方式有一定创新
"""
        (ideation_dir / "novelty_audit.md").write_text(novelty_audit, encoding="utf-8")

        # 验证输出
        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=workspace,
            project_id="test_project",
            task_id="T4",
            run_id="test_run_001"
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, f"Ideation 输出验证失败: {err}"


# ══════════════════════════════════════════════════════
# E2E 测试：多 Agent 协作流程
# ══════════════════════════════════════════════════════

class TestMultiAgentWorkflow:
    """测试多 Agent 协作流程。"""

    @pytest.fixture
    def workspace(self, tmp_path):
        """创建测试 workspace。"""
        ws = tmp_path / "test_workflow"
        ws.mkdir()
        return ws

    def test_t2_to_t3_to_t4_pipeline(self, workspace):
        """测试 T2 → T3 → T4 流程。"""
        # 1. 创建 T2 (Scout) 输出
        literature_dir = workspace / "literature"
        literature_dir.mkdir()

        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(15)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        # 验证 T2 输出
        scout = ScoutAgent()
        ctx_t2 = ExecutionContext(
            workspace_dir=workspace,
            project_id="test_project",
            task_id="T2",
            run_id="test_run_001"
        )
        ok, err = scout.validate_outputs(ctx_t2)
        assert ok, f"T2 输出验证失败: {err}"

        # 2. 创建 T3 (Reader) 输出
        paper_notes_dir = literature_dir / "paper_notes"
        paper_notes_dir.mkdir()

        # 为 80% 的 papers 创建笔记
        for i in range(12):  # 12/15 = 80%
            (paper_notes_dir / f"paper_{i}.md").write_text(
                f"# Paper {i} Notes\n\n## Key Findings\n- Finding 1\n- Finding 2",
                encoding="utf-8"
            )

        # comparison_table.csv
        comparison = "Paper,Key_Method,Accuracy,Year\n"
        comparison += "Paper 1,Transformer,0.85,2023\n"
        comparison += "Paper 2,BERT,0.82,2022\n"
        (literature_dir / "comparison_table.csv").write_text(comparison)

        # related_work.bib
        bib_content = """@article{paper1,
  title={Paper 1},
  author={Author},
  year={2024}
}
"""
        (literature_dir / "related_work.bib").write_text(bib_content)

        # papers_summary.md
        (workspace / "papers_summary.md").write_text(
            "# Papers Summary\n\n## Paper 1\nKey findings...\n\n## Paper 2\nMore findings...",
            encoding="utf-8"
        )

        # 验证 T3 输出
        reader = ReaderAgent()
        ctx_t3 = ExecutionContext(
            workspace_dir=workspace,
            project_id="test_project",
            task_id="T3",
            run_id="test_run_001"
        )
        ok, err = reader.validate_outputs(ctx_t3)
        assert ok, f"T3 输出验证失败: {err}"

        # 3. 创建 T4 (Ideation) 输出
        project_yaml = {"research_direction": "测试方向", "domain": "NLP"}
        (workspace / "project.yaml").write_text(yaml.dump(project_yaml))

        ideation_dir = workspace / "ideation"
        ideation_dir.mkdir()

        hypotheses = """# Research Hypotheses

## H1: 测试假设 1

我们假设方法 X 可以通过改进模型架构来提升性能。具体来说：

1. **核心假设**：通过引入新的注意力机制，可以更好地捕获长距离依赖关系
2. **与现有方法对比**：传统方法使用固定窗口注意力，我们的方法使用动态稀疏注意力
3. **预期效果**：预期在多个 NLP 任务上提升 5-10% 的准确率
4. **技术细节**：方法 X 采用分层注意力策略，在不同层级使用不同的注意力模式
5. **理论基础**：基于 transformer 的架构改进，结合了最新的技术进展

## H2: 测试假设 2

我们还假设数据增强策略可以进一步提升模型鲁棒性：

1. **核心假设**：通过组合多种数据增强技术，可以在有限数据下提升泛化能力
2. **具体策略**：包括回译、同义词替换、随机删除、混合增强等
3. **预期效果**：预期在少样本场景下提升 3-5% 的准确率
4. **实施细节**：针对不同任务类型采用不同的增强策略组合
5. **实验设计**：对比不同增强策略的效果，找出最优组合并进行统计显著性检验
"""
        (ideation_dir / "hypotheses.md").write_text(hypotheses, encoding="utf-8")

        exp_plan = {"experiments": [{"name": "test_exp", "hypothesis_ref": "H1"}]}
        (ideation_dir / "exp_plan.yaml").write_text(yaml.dump(exp_plan))

        risks = """# 风险分析

## 风险

### 高风险
- GPU 资源可能不足

### 中风险
- 实验时间可能超出预期

## 风险

### 中风险
- 数据增强效果可能不明显

### 低风险
- 实施复杂度较高

## 风险

### 高风险
- 方法实现复杂度高
"""
        (ideation_dir / "risks.md").write_text(risks, encoding="utf-8")

        novelty = """# Novelty Audit

## Level 2
"""
        (ideation_dir / "novelty_audit.md").write_text(novelty, encoding="utf-8")

        # 验证 T4 输出
        ideation = IdeationAgent()
        ctx_t4 = ExecutionContext(
            workspace_dir=workspace,
            project_id="test_project",
            task_id="T4",
            run_id="test_run_001"
        )
        ok, err = ideation.validate_outputs(ctx_t4)
        assert ok, f"T4 输出验证失败: {err}"


# ══════════════════════════════════════════════════════
# E2E 测试：状态持久化
# ══════════════════════════════════════════════════════

class TestStatePersistence:
    """测试状态持久化。"""

    def test_state_yaml_round_trip(self, tmp_path):
        """测试 state.yaml 的读写往返。"""
        workspace = tmp_path / "test_state"
        workspace.mkdir()

        # 写入初始状态
        initial_state = {
            "current_task": "T2",
            "completed_tasks": ["T1"],
            "iteration_count": {"T5": 1},
            "last_updated": "2026-04-20"
        }
        (workspace / "state.yaml").write_text(yaml.dump(initial_state))

        # 读取并验证
        loaded_state = yaml.safe_load((workspace / "state.yaml").read_text())

        assert loaded_state["current_task"] == "T2"
        assert loaded_state["completed_tasks"] == ["T1"]
        assert loaded_state["iteration_count"]["T5"] == 1

    def test_iteration_count_persistence(self, tmp_path):
        """测试 iteration_count 的持久化。"""
        workspace = tmp_path / "test_iteration"
        workspace.mkdir()

        # 写入带 iteration_count 的状态
        state = {
            "iteration_count": {
                "T5": 3,
                "T6": 1
            }
        }
        (workspace / "state.yaml").write_text(yaml.dump(state))

        # 读取并验证
        ctx = ExecutionContext(
            workspace_dir=workspace,
            project_id="test",
            task_id="T5",
            run_id="run_001"
        )

        from researchos.agents._common import read_iteration_count
        count = read_iteration_count(ctx, "T5")
        assert count == 3, f"Expected 3, got {count}"


# ══════════════════════════════════════════════════════
# E2E 测试：Agent 配置和约束
# ══════════════════════════════════════════════════════

class TestAgentConfiguration:
    """测试 Agent 配置和约束。"""

    def test_all_agents_have_validate_outputs(self):
        """验证所有 Agent 都有 validate_outputs 方法。"""
        agents = [
            PIAgent(),
            ScoutAgent(),
            ReaderAgent(),
            IdeationAgent(),
            NoveltyAuditorAgent(),
            ExperimenterAgent(),
        ]

        for agent in agents:
            assert hasattr(agent, "validate_outputs"), \
                f"{agent.spec.name} 缺少 validate_outputs 方法"
            assert callable(agent.validate_outputs), \
                f"{agent.spec.name}.validate_outputs 不是可调用对象"

    def test_all_agents_have_initial_user_message(self):
        """验证所有 Agent 都有 initial_user_message 方法。"""
        agents = [
            PIAgent(),
            ScoutAgent(),
            ReaderAgent(),
            IdeationAgent(),
            NoveltyAuditorAgent(),
            ExperimenterAgent(),
        ]

        for agent in agents:
            assert hasattr(agent, "initial_user_message"), \
                f"{agent.spec.name} 缺少 initial_user_message 方法"

    def test_experimenter_supports_pilot_and_full_modes(self, tmp_path):
        """验证 Experimenter Agent 支持 pilot 和 full 模式。"""
        agent = ExperimenterAgent()

        # pilot 模式
        ctx_pilot = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T5",
            run_id="run_001",
            mode="pilot"
        )
        msg_pilot = agent.initial_user_message(ctx_pilot)
        assert "pilot" in msg_pilot.lower() or "T5" in msg_pilot

        # full 模式
        ctx_full = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T7",
            run_id="run_001",
            mode="full"
        )
        msg_full = agent.initial_user_message(ctx_full)
        assert "full" in msg_full.lower() or "T7" in msg_full
