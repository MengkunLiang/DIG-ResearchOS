"""测试鲁棒性增强功能（§8.1, §10.1-10.2, §7.1）"""

import json
from pathlib import Path
import pytest
import yaml
import logging

from researchos.agents.pi import PIAgent
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.agent import ExecutionContext
from researchos.schemas.state import StateYaml, BudgetCumulative


@pytest.fixture(autouse=True)
def configure_logging_for_tests():
    """配置日志以便caplog能捕获structlog输出"""
    from researchos.runtime.logger import configure_logging
    configure_logging(level="DEBUG", json_logs=False)
    yield


class TestEthicalScreening:
    """测试T1 Ethical screening（§8.1）"""

    def test_ethical_screening_detects_weapons(self):
        """测试检测武器相关敏感词"""
        agent = PIAgent()

        project_data = {
            "project_id": "test",
            "research_direction": "developing bioweapon detection systems",
            "keywords": ["bioweapon", "detection"],
            "created_at": "2024-01-01T00:00:00Z"
        }

        ok, err = agent._check_ethical_concerns(project_data)
        assert not ok
        assert "weapons:bioweapon" in err
        assert "敏感研究方向" in err

    def test_ethical_screening_detects_surveillance(self):
        """测试检测监控相关敏感词"""
        agent = PIAgent()

        project_data = {
            "project_id": "test",
            "research_direction": "facial recognition for tracking people",
            "keywords": ["surveillance", "tracking"],
            "created_at": "2024-01-01T00:00:00Z"
        }

        ok, err = agent._check_ethical_concerns(project_data)
        assert not ok
        assert "surveillance" in err

    def test_ethical_screening_passes_normal_research(self):
        """测试正常研究方向通过检查"""
        agent = PIAgent()

        project_data = {
            "project_id": "test",
            "research_direction": "improving natural language understanding",
            "keywords": ["NLP", "transformers", "language models"],
            "created_at": "2024-01-01T00:00:00Z"
        }

        ok, err = agent._check_ethical_concerns(project_data)
        assert ok
        assert err is None


class TestExternalResources:
    """测试T1外部资源管理（§10.1-10.2）"""

    def test_validate_external_resources_valid_format(self, tmp_path):
        """测试验证合法的外部资源格式"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text(
            '{"type": "dataset", "name": "ImageNet", "source": "huggingface:imagenet-1k", "purpose": "benchmark"}\n'
            '{"type": "baseline_repo", "name": "ResNet", "source": "github:pytorch/vision", "purpose": "baseline"}\n'
            '{"type": "pretrained_model", "name": "BERT", "source": "huggingface:bert-base-uncased", "purpose": "encoder"}\n'
        )

        ok, err = agent._validate_external_resources(resources_file)
        assert ok
        assert err is None

    def test_validate_external_resources_accepts_governance_and_standard_resources(self, tmp_path):
        """综述种子提纲派生的法规/标准/治理框架资源应合法。"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text(
            '{"type": "regulation", "name": "EU AI Act", "source": "official_source_lookup_required"}\n'
            '{"type": "governance_framework", "name": "NIST AI RMF", "source": "official:nist"}\n'
            '{"type": "standard", "name": "ISO/IEC 42001", "source": "official:iso"}\n',
            encoding="utf-8",
        )

        ok, err = agent._validate_external_resources(resources_file)

        assert ok, err

    def test_validate_external_resources_invalid_type(self, tmp_path):
        """测试检测非法的资源类型"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text(
            '{"type": "invalid_type", "name": "Test", "source": "github:test/repo", "purpose": "test"}\n'
        )

        ok, err = agent._validate_external_resources(resources_file)
        assert not ok
        assert "type 'invalid_type' 不合法" in err

    def test_validate_external_resources_invalid_source(self, tmp_path):
        """测试检测非法的source格式"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text(
            '{"type": "dataset", "name": "Test", "source": "invalid_source", "purpose": "test"}\n'
        )

        ok, err = agent._validate_external_resources(resources_file)
        assert not ok
        assert "source格式不合法" in err

    def test_validate_external_resources_missing_field(self, tmp_path):
        """测试检测缺少必需字段"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text(
            '{"type": "dataset", "name": "Test"}\n'  # 缺少source字段
        )

        ok, err = agent._validate_external_resources(resources_file)
        assert not ok
        assert "缺少'source'字段" in err

    def test_validate_external_resources_empty_file(self, tmp_path):
        """测试空文件也是合法的"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text("")

        ok, err = agent._validate_external_resources(resources_file)
        assert ok
        assert err is None

    def test_validate_external_resources_invalid_json(self, tmp_path):
        """测试检测非法JSON"""
        agent = PIAgent()

        resources_file = tmp_path / "seed_external_resources.jsonl"
        resources_file.write_text(
            'not a valid json\n'
        )

        ok, err = agent._validate_external_resources(resources_file)
        assert not ok
        assert "JSON解析失败" in err


class TestBudgetDriftWarning:
    """测试Runtime预算漂移预警（§7.1）"""

    def test_budget_drift_warning_70_percent(self, tmp_path, caplog):
        """测试70%预算警告"""
        import logging

        # 创建state_machine配置
        config_file = tmp_path / "state_machine.yaml"
        config_file.write_text(yaml.dump({
            "initial_state": "T1",
            "states": {
                "T1": {"agent": "pi", "next_on_success": "done"},
                "done": {"terminal": True}
            }
        }))

        sm = StateMachine(config_file)

        # 创建project.yaml
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        project_file = workspace / "project.yaml"
        project_file.write_text(yaml.dump({
            "project_id": "test",
            "research_direction": "test",
            "keywords": ["test"],
            "constraints": {"max_budget_usd": 100.0},
            "created_at": "2024-01-01T00:00:00Z"
        }))

        # 创建state，累计花费75美元（75%）
        state = StateYaml(
            project_id="test",
            current_task="T1",
            budget_cumulative=BudgetCumulative(
                tokens_total=0,
                cost_usd_total=75.0,
                gpu_hours_used=0
            )
        )

        # 先调用start_task添加history
        state = sm.start_task(state, "test_run_id")

        # 模拟一次任务完成，增加0美元（触发检查）
        from researchos.runtime.agent import AgentResult
        result = AgentResult(
            ok=True,
            message="done",
            outputs_produced={},
            steps_used=1,
            tokens_in=100,
            tokens_out=100,
            cost_usd=0.0,
            duration_seconds=1.0,
            stop_reason="finished"
        )

        # 设置caplog捕获WARNING级别
        with caplog.at_level(logging.WARNING):
            # 调用advance触发预算检查
            sm.advance(state, result, workspace_dir=workspace)

        # 检查日志输出（structlog会输出到标准logging）
        assert any("预算警告" in str(record.msg) for record in caplog.records)

    def test_budget_drift_warning_90_percent(self, tmp_path, caplog):
        """测试90%预算严重警告"""
        import logging

        config_file = tmp_path / "state_machine.yaml"
        config_file.write_text(yaml.dump({
            "initial_state": "T1",
            "states": {
                "T1": {"agent": "pi", "next_on_success": "done"},
                "done": {"terminal": True}
            }
        }))

        sm = StateMachine(config_file)

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        project_file = workspace / "project.yaml"
        project_file.write_text(yaml.dump({
            "project_id": "test",
            "research_direction": "test",
            "keywords": ["test"],
            "constraints": {"max_budget_usd": 100.0},
            "created_at": "2024-01-01T00:00:00Z"
        }))

        state = StateYaml(
            project_id="test",
            current_task="T1",
            budget_cumulative=BudgetCumulative(
                tokens_total=0,
                cost_usd_total=95.0,  # 95%
                gpu_hours_used=0
            )
        )

        # 先调用start_task添加history
        state = sm.start_task(state, "test_run_id")

        from researchos.runtime.agent import AgentResult
        result = AgentResult(
            ok=True,
            message="done",
            outputs_produced={},
            steps_used=1,
            tokens_in=100,
            tokens_out=100,
            cost_usd=0.0,
            duration_seconds=1.0,
            stop_reason="finished"
        )

        with caplog.at_level(logging.WARNING):
            sm.advance(state, result, workspace_dir=workspace)

        # 检查日志输出
        assert any("预算严重超支警告" in str(record.msg) for record in caplog.records)

        # 检查是否写入了警告文件
        warning_file = workspace / ".researchos" / "budget_warning.txt"
        assert warning_file.exists()
        content = warning_file.read_text()
        assert "预算严重超支警告" in content
        assert "95.0" in content

    def test_budget_drift_no_warning_below_threshold(self, tmp_path, caplog):
        """测试低于70%不触发警告"""
        import logging

        config_file = tmp_path / "state_machine.yaml"
        config_file.write_text(yaml.dump({
            "initial_state": "T1",
            "states": {
                "T1": {"agent": "pi", "next_on_success": "done"},
                "done": {"terminal": True}
            }
        }))

        sm = StateMachine(config_file)

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        project_file = workspace / "project.yaml"
        project_file.write_text(yaml.dump({
            "project_id": "test",
            "research_direction": "test",
            "keywords": ["test"],
            "constraints": {"max_budget_usd": 100.0},
            "created_at": "2024-01-01T00:00:00Z"
        }))

        state = StateYaml(
            project_id="test",
            current_task="T1",
            budget_cumulative=BudgetCumulative(
                tokens_total=0,
                cost_usd_total=50.0,  # 50%
                gpu_hours_used=0
            )
        )

        # 先调用start_task添加history
        state = sm.start_task(state, "test_run_id")

        from researchos.runtime.agent import AgentResult
        result = AgentResult(
            ok=True,
            message="done",
            outputs_produced={},
            steps_used=1,
            tokens_in=100,
            tokens_out=100,
            cost_usd=0.0,
            duration_seconds=1.0,
            stop_reason="finished"
        )

        with caplog.at_level(logging.WARNING):
            sm.advance(state, result, workspace_dir=workspace)

        # 检查没有预算警告
        assert not any("预算警告" in str(record.msg) for record in caplog.records)


class TestHypothesisPreMortem:
    """测试T4 Hypothesis pre-mortem（§4.1）"""

    def test_ideation_prompt_contains_premortem_section(self):
        """测试ideation.j2包含pre-mortem检查章节"""
        from pathlib import Path

        prompt_file = Path("researchos/prompts/ideation.j2")
        assert prompt_file.exists(), "ideation.j2文件不存在"

        content = prompt_file.read_text(encoding="utf-8")

        # 检查是否包含Pre-mortem章节
        assert "Pre-mortem" in content or "pre-mortem" in content or "premortem" in content

        # 检查是否包含三个维度的检查
        assert "物理/数学约束检查" in content or "物理" in content
        assert "已知反例检查" in content or "反例" in content
        assert "资源可行性检查" in content or "可行性" in content

        # 检查是否包含风险评级
        assert "风险评级" in content or "Low / Medium / High" in content

        # 检查是否包含缓解方案
        assert "缓解方案" in content or "缓解" in content

    def test_ideation_prompt_premortem_placement(self):
        """测试pre-mortem检查在Gate1和Gate2之间"""
        from pathlib import Path

        prompt_file = Path("researchos/prompts/ideation.j2")
        content = prompt_file.read_text(encoding="utf-8")

        # 找到Gate1和Gate2的位置
        gate1_pos = content.find("T4-DECIDE-1")
        gate2_pos = content.find("T4-DECIDE-2")
        premortem_pos = content.find("Pre-mortem")

        assert gate1_pos > 0, "找不到Gate1 (T4-DECIDE-1)"
        assert gate2_pos > 0, "找不到Gate2 (T4-DECIDE-2)"
        assert premortem_pos > 0, "找不到Pre-mortem章节"

        # 验证pre-mortem在Gate1和Gate2之间
        assert gate1_pos < premortem_pos < gate2_pos, \
            "Pre-mortem检查应该在Gate1和Gate2之间"

    def test_ideation_prompt_premortem_output_file(self):
        """测试pre-mortem检查要求输出到文件"""
        from pathlib import Path

        prompt_file = Path("researchos/prompts/ideation.j2")
        content = prompt_file.read_text(encoding="utf-8")

        # 检查是否要求写入_premortem.md文件
        assert "_premortem.md" in content or "premortem" in content.lower()
