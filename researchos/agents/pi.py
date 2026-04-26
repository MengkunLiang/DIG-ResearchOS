"""T1/T7.5 PI Agent - 项目初始化与评估

T1 (init模式): 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据
T7.5 (evaluate模式): 评估实验结果，决定后续路径

契约详见 ResearchOS_Agent_Dev_Spec.md §6
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import prepend_resume_prefix, read_text_file
from ..schemas.validator import validate_record


class PIAgent(Agent):
    """项目初始化与评估Agent。

    两种模式:
    - init (T1): 三轮对话产出project.yaml和seed文件
    - evaluate (T7.5): 评估实验结果，给出后续建议
    """

    def __init__(self, mode: str | None = None):
        super().__init__(
            build_agent_spec(
                "pi",
                mode=mode,
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "write_structured_file",
                        "list_files",
                        "ask_human",
                        "finish_task",
                        "process_seed_paper",
                    ],
                    "max_steps": 30,
                    "max_tokens_total": 100_000,
                    "max_wall_seconds": 1800,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    "allowed_read_prefixes": [
                        "",
                        "user_seeds/",
                        "experiments/",
                        "ideation/",
                        "evaluation/",
                    ],
                    "allowed_write_prefixes": ["", "user_seeds/", "evaluation/"],
                    "prompt_template": "pi.j2",
                    "structured_outputs": {
                        "project.yaml": "project",
                    },
                },
            )
        )
        self._mode = mode

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """根据mode渲染不同的system prompt。"""
        mode = ctx.mode or "init"

        # 准备上下文变量
        context_vars = {}

        if mode == "init":
            # T1模式：用户topic从extra中获取
            # 注意：必须显式传递 user_topic，因为模板中直接使用 {{ user_topic }}
            context_vars["user_topic"] = ctx.extra.get("user_topic", "")
        elif mode == "evaluate":
            # T7.5模式：读取实验结果
            results_path = ctx.workspace_dir / "experiments" / "results_summary.json"
            iteration_log_path = ctx.workspace_dir / "experiments" / "iteration_log.md"
            exp_plan_path = ctx.workspace_dir / "ideation" / "exp_plan.yaml"

            context_vars["has_results"] = results_path.exists()
            context_vars["has_iteration_log"] = iteration_log_path.exists()
            context_vars["has_exp_plan"] = exp_plan_path.exists()

        return render_prompt(self.spec.prompt_template, ctx, **context_vars)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据mode返回不同的初始消息。"""
        mode = ctx.mode or "init"

        if mode == "init":
            # 不要在这里透露 user_topic，让 Agent 从第1轮对话开始询问
            # user_topic 会在 system prompt 中提供作为背景信息
            return prepend_resume_prefix(
                ctx,
                (
                f"请开始T1项目初始化流程。\n\n"
                f"请严格按照system prompt中的三轮对话流程执行：\n"
                f"- 第1轮：明确研究边界与约束\n"
                f"- 第2轮：收集已有基础（论文、想法、约束）\n"
                f"- 第2.5轮：收集外部资源\n"
                f"- 第3轮：确认并生成所有文件\n\n"
                f"重要：必须严格按照 prompt 中的 project.yaml 格式要求生成文件，"
                f"包含所有必需字段（project_id, research_direction, keywords, constraints, created_at, seed_ensemble）。"
                ),
            )
        elif mode == "evaluate":
            return prepend_resume_prefix(
                ctx,
                (
                "请开始T7.5实验评估流程。\n\n"
                "请读取experiments/results_summary.json、experiments/iteration_log.md "
                "和ideation/exp_plan.yaml，评估实验结果，判断Situation (A/B/C/D)，"
                "并给出后续Options建议。"
                ),
            )
        else:
            return prepend_resume_prefix(ctx, f"未知模式: {mode}")

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出文件。"""
        # 先调用基类检查文件存在
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        mode = ctx.mode or "init"

        if mode == "init":
            return self._validate_init_outputs(ctx)
        elif mode == "evaluate":
            return self._validate_evaluate_outputs(ctx)
        else:
            return False, f"未知模式: {mode}"

    def _validate_init_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验T1 init模式的输出。"""
        # 1. 检查project.yaml存在且符合schema
        project_path = ctx.workspace_dir / "project.yaml"
        if not project_path.exists():
            return False, "缺少project.yaml"

        try:
            project_data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"project.yaml解析失败: {e}"

        # 校验schema
        ok, err = validate_record(project_data, "project")
        if not ok:
            return False, f"project.yaml不符合schema: {err}"

        # 校验 seed_ensemble 格式
        ok, err = self._validate_seed_ensemble(project_data)
        if not ok:
            return False, err

        # 2. Ethical screening (§8.1)
        ok, err = self._check_ethical_concerns(project_data)
        if not ok:
            return False, err

        # 3. seed 文件是可选的，不强制要求
        # 如果用户没有提供种子数据，Agent 可以不创建这些文件
        # 这里只检查 project.yaml 和 state.yaml

        return True, None

    def _validate_evaluate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验T7.5 evaluate模式的输出。"""
        # 检查evaluation_decision.md存在且包含必需内容
        decision_path = ctx.workspace_dir / "evaluation" / "evaluation_decision.md"
        if not decision_path.exists():
            return False, "缺少evaluation/evaluation_decision.md"

        content = read_text_file(decision_path)

        # 必须包含Situation判定
        if "Situation" not in content:
            return False, "evaluation_decision.md必须包含'Situation'章节"

        # 必须包含后续建议
        if not any(keyword in content for keyword in ["Option", "next_task", "建议"]):
            return False, "evaluation_decision.md必须包含后续Options建议"

        return True, None

    def _check_ethical_concerns(self, project_data: dict) -> tuple[bool, str | None]:
        """检查研究方向是否涉及敏感领域（§8.1）。

        检查project.yaml的research_direction和keywords是否包含敏感词。
        如果检测到敏感词，返回警告信息。
        """
        # 敏感词列表（可扩展）
        SENSITIVE_KEYWORDS = {
            "weapons": ["weapon", "explosive", "bioweapon", "biological weapon", "chemical weapon"],
            "surveillance": ["surveillance", "tracking people", "monitoring people", "facial recognition"],
            "manipulation": ["manipulation", "deception", "fake news generation", "deepfake"],
            "privacy": ["privacy invasion", "data breach", "unauthorized access"],
            "discrimination": ["discrimination", "bias amplification", "unfair targeting"],
        }

        direction = project_data.get("research_direction", "").lower()
        keywords = [k.lower() for k in project_data.get("keywords", [])]

        concerns = []
        for category, words in SENSITIVE_KEYWORDS.items():
            for word in words:
                if word in direction or any(word in kw for kw in keywords):
                    concerns.append((category, word))

        if concerns:
            concern_str = ", ".join([f"{cat}:{word}" for cat, word in concerns])
            return False, f"检测到敏感研究方向: {concern_str}。请确认研究目的符合伦理规范。"

        return True, None

    def _validate_seed_ensemble(self, project_data: dict) -> tuple[bool, str | None]:
        """验证 seed_ensemble 格式是否正确。

        seed_ensemble 应该只包含随机种子（整数数组），
        不应该包含论文信息（title, authors, source, doi 等）。

        注意：如果 seed_ensemble 不存在，会使用默认值（schema default），
        所以这里只检查存在且格式错误的情况。
        """
        seed_ensemble = project_data.get("seed_ensemble")

        # 如果 seed_ensemble 不存在，使用默认值，不报错
        if not seed_ensemble:
            return True, None

        if not isinstance(seed_ensemble, dict):
            return False, f"seed_ensemble 必须是对象，实际类型: {type(seed_ensemble).__name__}"

        # 检查是否包含论文相关字段（不应该有）
        paper_fields = ["title", "authors", "source", "doi", "arxiv_id", "url", "year", "abstract", "venue", "papers"]
        found_paper_fields = [f for f in paper_fields if f in seed_ensemble]
        if found_paper_fields:
            return False, f"seed_ensemble 不应包含论文信息，发现字段: {found_paper_fields}。论文信息应写入 seed_papers.jsonl。"

        # 如果有 tier 字段，检查格式
        if "tier1_seeds" in seed_ensemble or "tier2_seeds" in seed_ensemble or "tier3_seeds" in seed_ensemble:
            required_fields = ["tier1_seeds", "tier2_seeds", "tier3_seeds"]
            for field in required_fields:
                if field in seed_ensemble:
                    if not isinstance(seed_ensemble[field], list):
                        return False, f"seed_ensemble.{field} 必须是数组，实际类型: {type(seed_ensemble[field]).__name__}"
                    if not all(isinstance(x, int) for x in seed_ensemble[field]):
                        return False, f"seed_ensemble.{field} 必须是整数数组"

        return True, None

    def _validate_external_resources(self, path: Path) -> tuple[bool, str | None]:
        """验证seed_external_resources.jsonl格式（§10.1-10.2）。"""
        import json

        VALID_TYPES = {"dataset", "baseline_repo", "pretrained_model", "docker_image", "tool", "script", "other"}
        VALID_SOURCE_PREFIXES = {"huggingface:", "github:", "docker:", "pip:", "url:", "local:"}

        try:
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                return True, None  # 空文件也是合法的

            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if not line.strip():
                    continue

                try:
                    resource = json.loads(line)
                except json.JSONDecodeError as e:
                    return False, f"seed_external_resources.jsonl第{i}行JSON解析失败: {e}"

                # 检查必需字段
                if "type" not in resource:
                    return False, f"seed_external_resources.jsonl第{i}行缺少'type'字段"
                if "name" not in resource:
                    return False, f"seed_external_resources.jsonl第{i}行缺少'name'字段"
                if "source" not in resource:
                    return False, f"seed_external_resources.jsonl第{i}行缺少'source'字段"

                # 检查type是否合法
                if resource["type"] not in VALID_TYPES:
                    return False, f"seed_external_resources.jsonl第{i}行type '{resource['type']}' 不合法，必须是: {VALID_TYPES}"

                # 检查source格式
                source = resource["source"]
                if not any(source.startswith(prefix) for prefix in VALID_SOURCE_PREFIXES):
                    return False, f"seed_external_resources.jsonl第{i}行source格式不合法，必须以以下前缀之一开头: {VALID_SOURCE_PREFIXES}"

            return True, None

        except Exception as e:
            return False, f"seed_external_resources.jsonl验证失败: {e}"
