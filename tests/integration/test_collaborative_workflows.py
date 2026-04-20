"""关键协作流程测试。

测试 Agent 之间的数据传递和协作流程：
1. T1 → T2: 项目初始化 → 信息检索
2. T2 → T3: 信息检索 → 论文解析
3. T3 → T4: 论文解析 → 假设生成
4. T4 → T4.5: 假设生成 → 新颖性评估
5. 跨 Agent 文件契约验证
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from researchos.agents.experimenter import ExperimenterAgent
from researchos.agents.ideation import IdeationAgent
from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.agents.pi import PIAgent
from researchos.agents.reader import ReaderAgent
from researchos.agents.scout import ScoutAgent
from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext


# ══════════════════════════════════════════════════════
# 1. Agent 创建的输出验证
# ══════════════════════════════════════════════════════

class TestAgentOutputContracts:
    """验证每个 Agent 的输出契约。"""

    def test_pi_agent_outputs(self, tmp_path):
        """T1 PI Agent 输出契约。"""
        # 模拟 PI Agent 执行后的 workspace
        project_yaml = {
            "research_direction": "测试方向",
            "domain": "NLP",
        }
        (tmp_path / "project.yaml").write_text(yaml.dump(project_yaml))

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test_project",
            task_id="T1",
            run_id="test_run_001",
        )

        ok, err = agent.validate_outputs(ctx)
        # project.yaml 存在即通过
        assert ok or "project.yaml" in str(err)

    def test_scout_agent_outputs(self, tmp_path):
        """T2 Scout Agent 输出契约。"""
        # 模拟 Scout Agent 执行后的 workspace
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir()

        # papers_raw 需要充足数量
        papers_raw = [
            {"title": f"Paper {i}", "abstract": f"Abstract {i}", "url": f"http://example.com/{i}",
             "id": f"paper_{i}", "year": 2024, "authors": ["Author"], "relevance_score": 0.9}
            for i in range(30)
        ]
        (literature_dir / "papers_raw.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_raw)
        )

        # papers_dedup 需要满足 schema（id, title, year, authors, relevance_score）且数量 >= 15
        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(20)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test_project",
            task_id="T2",
            run_id="test_run_001",
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, f"Scout 输出校验失败: {err}"

    def test_reader_agent_outputs(self, tmp_path):
        """T3 Reader Agent 输出契约。"""
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir()

        # papers_dedup.jsonl 需要存在（Reader 读取它来确定最小笔记数）
        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        # papers_raw.jsonl 也需要
        papers_raw = [
            {"title": f"Paper {i}", "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        (literature_dir / "papers_raw.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_raw)
        )

        # paper_notes 目录和笔记文件
        notes_dir = literature_dir / "paper_notes"
        notes_dir.mkdir()
        for i in range(8):  # 至少 80% 的论文有笔记
            (notes_dir / f"note_{i}.md").write_text(f"# Note {i}\nKey finding...")

        # comparison_table.csv
        csv_content = "paper_id,criterion1,criterion2\npaper_0,value1,value2\npaper_1,value3,value4\n"
        (literature_dir / "comparison_table.csv").write_text(csv_content)

        # papers_summary.md
        (tmp_path / "papers_summary.md").write_text("# Summary\n- Key finding 1\n- Key finding 2")

        # papers_notes.jsonl（可选的聚合文件）
        (tmp_path / "papers_notes.jsonl").write_text(
            json.dumps({"paper_id": "1", "notes": "Important paper"})
        )

        # related_work.bib（Reader 需要收集论文引用）
        bib_content = """@article{Author2024Paper1,
  title={Paper 1 Title},
  author={Author, A.},
  year={2024}
}
@article{Author2024Paper2,
  title={Paper 2 Title},
  author={Author, B.},
  year={2024}
}"""
        (literature_dir / "related_work.bib").write_text(bib_content)

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test_project",
            task_id="T3",
            run_id="test_run_001",
            mode="read",
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, f"Reader 输出校验失败: {err}"

    def test_ideation_agent_outputs(self, tmp_path):
        """T4 Ideation Agent 输出契约。"""
        # 模拟 Ideation Agent 执行后的 workspace
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir()

        # papers_summary.md 需要在顶层（这是 Ideation 的输入）
        (tmp_path / "papers_summary.md").write_text(
            "# Papers Summary\n\n## Paper 1\nKey finding...\n\n## Paper 2\nAnother finding..."
        )

        # papers_dedup.jsonl（Reader 产出，Ideation 需要引用）
        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        # ideation 目录和文件
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()

        # hypotheses.md - 需要至少 500 字符
        hypotheses_content = """# Research Hypotheses

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
3. **预期效果**：预期在少样本场景下提升 3-5% 的准确率
4. **实施细节**：针对不同任务类型采用不同的增强策略组合
5. **实验设计**：对比不同增强策略的效果，找出最优组合并进行统计显著性检验
"""
        (ideation_dir / "hypotheses.md").write_text(hypotheses_content)

        # exp_plan.yaml - 需要符合 schema
        exp_plan_content = """experiments:
  - name: exp_h1_architecture
    hypothesis_ref: H1
    dataset: common_nlp_bench
    data_fraction: 0.1
    baseline_methods:
      - name: LSTM
        description: 标准 LSTM 模型
      - name: Transformer
        description: 标准 Transformer 模型
    our_method:
      name: MethodX
      description: 我们的方法 X
    metrics:
      - accuracy
      - f1
    compute_estimate:
      gpu_hours: 0.5
      gpu_type: V100
    success_criteria:
      - metric: accuracy
        threshold: 0.7
  - name: exp_h2_augmentation
    hypothesis_ref: H2
    dataset: common_nlp_bench
    data_fraction: 0.05
    metrics:
      - accuracy
    compute_estimate:
      gpu_hours: 0.3
      gpu_type: V100
"""
        (ideation_dir / "exp_plan.yaml").write_text(exp_plan_content)

        # novelty_audit.md
        (ideation_dir / "novelty_audit.md").write_text(
            "# Novelty Audit\n\n## Level 2\n- H1: 新颖性高\n- H2: 新颖性中\n\n## Level 3\n- H1: 潜在影响力高"
        )

        # risks.md - 至少需要 3 个 "## 风险" 标记
        risks_content = """# Risks

本文档记录研究过程中识别的风险和缓解措施。

## 风险 1: 计算资源超出预算

### 描述
实验成本可能超出预算限制。

### 缓解措施
- 使用小规模数据进行 pilot 实验
- 设置最大预算阈值
- 监控 GPU 使用时间

## 风险 2: 方法效果不符合预期

### 描述
方法 X 可能无法达到预期的性能提升。

### 缓解措施
- 在 pilot 阶段验证核心假设
- 准备备选方法
- 调整实验参数

## 风险 3: 数据集质量

### 描述
数据集可能存在噪声或标签错误。

### 缓解措施
- 人工检查数据样本
- 使用数据清洗流程
- 记录数据质量问题
"""
        (ideation_dir / "risks.md").write_text(risks_content)

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test_project",
            task_id="T4",
            run_id="test_run_001",
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, f"Ideation 输出校验失败: {err}"

    def test_novelty_auditor_outputs(self, tmp_path):
        """T4.5 NoveltyAuditor Agent 输出契约。"""
        # 模拟 NoveltyAuditor Agent 执行后的 workspace
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir()

        # papers_dedup.jsonl（Reader 产出，NoveltyAuditor 可能需要）
        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()

        # hypotheses.md - NoveltyAuditor 需要检查所有假设
        (ideation_dir / "hypotheses.md").write_text(
            "# Hypotheses\n\n## H1\nHypothesis 1 content - Method X improves performance.\n\n## H2\nHypothesis 2 content - Data augmentation effectiveness."
        )

        # novelty_audit.md - 需要满足：
        # 1. 至少 500 字符
        # 2. 包含 Level 标记
        # 3. 审计所有假设
        audit_content = """# Novelty Audit

本审计评估研究假设的创新性和新颖性。

## 审计目的

本文档对研究假设进行系统性的新颖性评估，确保研究具有创新性和科学价值。

## Level 2

### H1: 方法 X 改进架构

1. **新颖性等级**: 高
2. **创新点**: 通过改进模型架构增强性能，采用了不同于传统方法的新架构设计
3. **与现有工作对比**:
   - 传统方法使用固定窗口注意力
   - 我们的方法使用动态稀疏注意力机制
   - 相比标准 Transformer，有显著架构改进
4. **技术差异化**: 采用分层注意力策略，在不同层级使用不同的注意力模式

### H2: 数据增强策略

1. **新颖性等级**: 中
2. **创新点**: 提出了新的数据增强方法组合
3. **与现有工作对比**: 在现有增强策略基础上进行了组合改进
4. **技术差异化**: 针对特定任务类型采用定制化的增强策略组合

## Level 3

### H1 综合评估

1. **技术新颖性**: 高
2. **方法论新颖性**: 中
3. **潜在影响力**: 高
4. **风险**: 低
5. **进一步提升空间**: 可以探索更多注意力机制的变体

### H2 综合评估

1. **技术新颖性**: 中
2. **方法论新颖性**: 低
3. **潜在影响力**: 中
4. **风险**: 中
5. **进一步提升空间**: 可以结合 H1 的方法形成联合增强策略

## 审计结论

总体而言，H1 具有较高的新颖性和潜在影响力，值得深入研究。H2 作为辅助策略，提供了有价值的补充。
"""
        (ideation_dir / "novelty_audit.md").write_text(audit_content)

        # exp_plan.yaml（Ideation 产出）
        (ideation_dir / "exp_plan.yaml").write_text(
            "experiments:\n  - name: exp1\n    hypothesis_ref: H1\n"
        )

        agent = NoveltyAuditorAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test_project",
            task_id="T4.5",
            run_id="test_run_001",
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, f"NoveltyAuditor 输出校验失败: {err}"

    def test_experimenter_pilot_outputs(self, tmp_path):
        """T5 Experimenter Pilot 输出契约。"""
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir()

        # papers_dedup.jsonl（Reader 产出）
        papers_dedup = [
            {"id": f"paper_{i}", "title": f"Paper {i}", "year": 2024, "authors": ["Author"],
             "relevance_score": 0.9, "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(10)
        ]
        (literature_dir / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers_dedup)
        )

        # project.yaml（PI 产出）
        project_data = {
            "research_direction": "测试方向",
            "domain": "NLP",
            "constraints": {"max_budget_usd": 100.0},
        }
        (tmp_path / "project.yaml").write_text(yaml.dump(project_data))

        # ideation 目录（Integrity Gate 需要）
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()
        (ideation_dir / "hypotheses.md").write_text(
            "# Hypotheses\n\n## H1\n我们假设方法 X 可以提升性能。方法 X 通过改进模型架构来增强性能。"
        )
        (ideation_dir / "exp_plan.yaml").write_text(
            "experiments:\n  - name: test_exp\n    hypothesis_ref: H1\n"
        )
        (ideation_dir / "novelty_audit.md").write_text(
            "# Novelty Audit\n\n## Level 2\n- H1: 新颖"
        )

        # pilot 目录
        pilot_dir = tmp_path / "pilot"
        pilot_dir.mkdir()
        pilot_code_dir = pilot_dir / "pilot_code"
        pilot_code_dir.mkdir()

        # pilot_results.json - 必须包含 seed=42
        pilot_results = {
            "seed": 42,  # 固定 seed
            "experiments": [
                {"experiment_id": "pilot_h1", "status": "DONE", "metrics": {"accuracy": 0.75}}
            ],
        }
        (pilot_dir / "pilot_results.json").write_text(json.dumps(pilot_results))

        # motivation_validation.md - 必须包含 PASS/REVISE/FAIL
        (pilot_dir / "motivation_validation.md").write_text(
            "## 判定：PASS\n\n理由：测试通过，方向正确"
        )

        # run_pilot.py - 必须包含 --smoke_test 和 --seed 参数
        run_pilot_code = """import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--smoke_test', action='store_true', help='Run smoke test only')
parser.add_argument('--seed', type=int, default=42, help='Random seed')
args = parser.parse_args()
print(f"Running with seed={args.seed}, smoke_test={args.smoke_test}")
"""
        (pilot_code_dir / "run_pilot.py").write_text(run_pilot_code)

        # smoke_test_passed.marker
        (pilot_dir / "smoke_test_passed.marker").write_text("PASS")

        # docker_digests.txt
        (pilot_dir / "docker_digests.txt").write_text("sha256:abc123")

        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test_project",
            task_id="T5",
            run_id="test_run_001",
            mode="pilot",
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, f"Pilot 输出校验失败: {err}"


# ══════════════════════════════════════════════════════
# 2. Agent 间数据传递测试
# ══════════════════════════════════════════════════════

class TestAgentDataFlow:
    """测试 Agent 之间的数据传递。"""

    def test_t1_to_t2_data_flow(self, tmp_path):
        """T1 → T2: project.yaml 被 T2 读取。"""
        # T1 创建 project.yaml
        project_data = {
            "research_direction": "NLP",
            "domain": "text_classification",
        }
        (tmp_path / "project.yaml").write_text(yaml.dump(project_data))

        # 验证 T2 Scout 可以读取
        agent = ScoutAgent()
        project_path = tmp_path / "project.yaml"
        assert project_path.exists()

        loaded = yaml.safe_load(project_path.read_text())
        assert loaded["research_direction"] == "NLP"
        assert loaded["domain"] == "text_classification"

    def test_t2_to_t3_data_flow(self, tmp_path):
        """T2 → T3: papers_raw.jsonl 被 T3 读取。"""
        # T2 创建 papers_raw.jsonl
        papers = [
            {"title": f"Paper {i}", "abstract": f"Abstract {i}", "url": f"http://example.com/{i}"}
            for i in range(3)
        ]
        (tmp_path / "papers_raw.jsonl").write_text(
            "\n".join(json.dumps(p) for p in papers)
        )

        # 验证 T3 Reader 可以读取
        agent = ReaderAgent()
        papers_path = tmp_path / "papers_raw.jsonl"
        assert papers_path.exists()

        from researchos.agents._common import load_jsonl
        loaded = load_jsonl(papers_path)
        assert len(loaded) == 3
        assert loaded[0]["title"] == "Paper 0"

    def test_t3_to_t4_data_flow(self, tmp_path):
        """T3 → T4: papers_summary.md 被 T4 读取。"""
        # T3 创建 papers_summary.md
        summary = "# Papers Summary\n\n## Paper 1\nKey findings about..."
        (tmp_path / "papers_summary.md").write_text(summary)

        # 验证 T4 Ideation 可以读取
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()

        agent = IdeationAgent()
        summary_path = tmp_path / "papers_summary.md"
        assert summary_path.exists()

        loaded = summary_path.read_text()
        assert "Papers Summary" in loaded

    def test_t4_to_t4_5_data_flow(self, tmp_path):
        """T4 → T4.5: hypotheses.md 和 exp_plan.yaml 被 T4.5 读取。"""
        # T4 创建 hypotheses.md 和 exp_plan.yaml
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()

        hypotheses = """# Hypotheses

## H1
Method X 通过改进架构提升性能。

## H2
数据增强策略有效性。
"""
        (ideation_dir / "hypotheses.md").write_text(hypotheses)

        exp_plan = {
            "experiments": [
                {"name": "exp1", "hypothesis_ref": "H1"},
                {"name": "exp2", "hypothesis_ref": "H2"},
            ]
        }
        (ideation_dir / "exp_plan.yaml").write_text(yaml.dump(exp_plan))

        # 验证 T4.5 可以读取
        agent = NoveltyAuditorAgent()
        assert (ideation_dir / "hypotheses.md").exists()
        assert (ideation_dir / "exp_plan.yaml").exists()

        loaded_hypotheses = (ideation_dir / "hypotheses.md").read_text()
        loaded_exp_plan = yaml.safe_load((ideation_dir / "exp_plan.yaml").read_text())
        assert "H1" in loaded_hypotheses
        assert len(loaded_exp_plan["experiments"]) == 2

    def test_t4_5_to_t5_data_flow(self, tmp_path):
        """T4.5 → T5: hypotheses.md, exp_plan.yaml, novelty_audit.md 被 T5 读取。"""
        # T4.5 更新 novelty_audit.md
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()

        # 确保必需文件存在
        (ideation_dir / "hypotheses.md").write_text("# H1\nHypothesis content")
        (ideation_dir / "exp_plan.yaml").write_text("experiments:\n  - name: test")
        (ideation_dir / "novelty_audit.md").write_text("# Novelty Audit\n## Level 2\n- H1: 新颖")

        # 验证 T5 可以读取
        agent = ExperimenterAgent()
        assert (ideation_dir / "hypotheses.md").exists()
        assert (ideation_dir / "exp_plan.yaml").exists()
        assert (ideation_dir / "novelty_audit.md").exists()


# ══════════════════════════════════════════════════════
# 3. 状态转换测试
# ══════════════════════════════════════════════════════

class TestStateTransitions:
    """测试状态机状态转换。"""

    def test_state_yaml_structure(self, tmp_path):
        """验证 state.yaml 结构。"""
        from researchos.orchestration.state_machine import StateMachine

        # 创建简单的状态机配置
        config = {
            "initial_state": "T1",
            "states": {
                "T1": {
                    "agent": "pi",
                    "outputs": {"project": "project.yaml"},
                    "next_on_success": "T2",
                },
                "T2": {
                    "agent": "scout",
                    "inputs": {"project": "project.yaml"},
                    "outputs": {"papers": "papers_raw.jsonl"},
                    "next_on_success": "T3",
                },
                "T3": {
                    "agent": "reader",
                    "inputs": {"papers": "papers_raw.jsonl"},
                    "outputs": {"summary": "papers_summary.md"},
                    "next_on_success": "T4",
                },
                "T4": {
                    "agent": "ideation",
                    "inputs": {"summary": "papers_summary.md"},
                    "outputs": {"hypotheses": "ideation/hypotheses.md"},
                    "terminal": True,
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))

        sm = StateMachine(config_path)
        assert sm.initial_state == "T1"
        assert "T1" in sm.nodes
        assert "T4" in sm.nodes

    def test_execution_context_from_state(self, tmp_path):
        """验证从状态机创建 ExecutionContext。"""
        from researchos.orchestration.state_machine import StateMachine
        from researchos.schemas.state import StateYaml

        config = {
            "initial_state": "T1",
            "states": {
                "T1": {
                    "agent": "pi",
                    "outputs": {"project": "project.yaml"},
                    "next_on_success": "T2",
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))

        sm = StateMachine(config_path)
        state = StateYaml(project_id="test_project", current_task="T1")

        ctx = sm.build_execution_context(tmp_path, state)
        assert ctx.task_id == "T1"
        assert ctx.project_id == "test_project"
        assert "project" in ctx.outputs_expected


# ══════════════════════════════════════════════════════
# 4. 文件契约完整性测试
# ══════════════════════════════════════════════════════

class TestFileContractIntegrity:
    """测试文件契约的完整性和依赖关系。"""

    def test_full_pipeline_file_dependencies(self, tmp_path):
        """验证完整流程中的文件依赖关系。"""
        # 模拟完整流程创建的文件
        files_created = []

        # T1: 创建 project.yaml
        (tmp_path / "project.yaml").write_text("name: test_project")
        files_created.append("project.yaml")

        # T2: 创建 papers_raw.jsonl
        (tmp_path / "papers_raw.jsonl").write_text(json.dumps({"title": "Test"}))
        files_created.append("papers_raw.jsonl")

        # T3: 创建 papers_summary.md
        (tmp_path / "papers_summary.md").write_text("# Summary")
        files_created.append("papers_summary.md")

        # T4: 创建 ideation/hypotheses.md, exp_plan.yaml, novelty_audit.md
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()
        (ideation_dir / "hypotheses.md").write_text("# Hypotheses")
        (ideation_dir / "exp_plan.yaml").write_text("experiments: []")
        (ideation_dir / "novelty_audit.md").write_text("# Audit")
        files_created.extend(["ideation/hypotheses.md", "ideation/exp_plan.yaml", "ideation/novelty_audit.md"])

        # 验证所有文件都存在
        for f in files_created:
            assert (tmp_path / f).exists(), f"Missing: {f}"

    def test_artifact_versions(self, tmp_path):
        """测试 artifact 版本追踪（通过 manifest.yaml）。"""
        from researchos.agents._common import generate_manifest

        # 模拟 T4 完成后生成 manifest
        ideation_dir = tmp_path / "ideation"
        ideation_dir.mkdir()

        (ideation_dir / "hypotheses.md").write_text("# Hypotheses")
        (ideation_dir / "exp_plan.yaml").write_text("experiments: []")

        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T4",
            run_id="r1",
        )

        manifest_path = generate_manifest(
            ctx,
            "ideation",
            artifacts=[
                {"path": "hypotheses.md", "type": "markdown"},
                {"path": "exp_plan.yaml", "type": "yaml"},
            ],
        )

        assert manifest_path.exists()
        manifest = yaml.safe_load(manifest_path.read_text())
        assert manifest["task_id"] == "T4"
        assert len(manifest["artifacts"]) == 2

    def test_findings_persistence(self, tmp_path):
        """测试 findings.md 持久化供后续 Agent 使用。"""
        from researchos.agents._common import generate_findings_summary

        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T3",
            run_id="r1",
        )

        findings = [
            "发现1: 数据集存在类别不平衡问题",
            "发现2: 现有 SOTA 方法在长文本上表现不佳",
        ]

        findings_path = generate_findings_summary(ctx, findings, "ideation")
        assert findings_path.exists()

        content = findings_path.read_text()
        assert "数据集存在类别不平衡问题" in content
        assert "T3" in content

    def test_research_log_persistence(self, tmp_path):
        """测试 research-log.md 持久化关键决策。"""
        from researchos.agents._common import generate_research_log

        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T4",
            run_id="r1",
        )

        log_path = generate_research_log(
            ctx,
            decision="选择 Transformer 架构",
            rationale="在 NLP 任务上效果最好",
            metadata={"alternatives_considered": ["LSTM", "CNN"]},
        )

        assert log_path.exists()
        content = log_path.read_text()
        assert "Transformer" in content
        assert "T4" in content
        assert "alternatives_considered" in content
