"""T1/T7.5 PI Agent - 项目初始化与评估

T1 (init模式): 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据
T7.5 (evaluate模式): 评估实验结果，决定后续路径

契约详见 ResearchOS_Agent_Dev_Spec.md §6
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import ensure_seed_outline_profile, prepend_resume_prefix, read_text_file
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
                        "inspect_user_seeds",
                        "normalize_seed_outline",
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
                        "literature/",
                        "user_seeds/",
                        "experiments/",
                        "ideation/",
                        "evaluation/",
                    ],
                    "allowed_write_prefixes": ["", "literature/", "user_seeds/", "evaluation/"],
                    "prompt_template": "pi.j2",
                    "structured_outputs": {
                        "project.yaml": "project",
                        "literature/bridge_domain_plan.json": "bridge_domain_plan",
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
            ensure_seed_outline_profile(ctx.workspace_dir)
            seed_outline_profile = read_text_file(
                ctx.workspace_dir / "user_seeds" / "seed_outline_profile.json",
                default="",
            )
            seed_external_resources = read_text_file(
                ctx.workspace_dir / "user_seeds" / "seed_external_resources.jsonl",
                default="",
            )
            # T1模式：用户topic从extra中获取
            # 注意：必须显式传递 user_topic，因为模板中直接使用 {{ user_topic }}
            context_vars["user_topic"] = ctx.extra.get("user_topic", "")
            context_vars["seed_outline_profile_preview"] = seed_outline_profile[:6000]
            context_vars["has_seed_outline_profile"] = bool(seed_outline_profile.strip())
            context_vars["seed_external_resources_preview"] = seed_external_resources[:2000]
            context_vars["has_seed_external_resources"] = bool(seed_external_resources.strip())
        elif mode == "evaluate":
            # T7.5模式：读取实验结果
            results_path = ctx.workspace_dir / "experiments" / "results_summary.json"
            iteration_log_path = ctx.workspace_dir / "experiments" / "iteration_log.md"
            integrity_audit_path = ctx.workspace_dir / "experiments" / "integrity_audit.json"
            result_to_claim_path = ctx.workspace_dir / "drafts" / "result_to_claim.json"
            evidence_pack_path = ctx.workspace_dir / "drafts" / "experiment_evidence_pack.json"
            exp_plan_path = ctx.workspace_dir / "ideation" / "exp_plan.yaml"

            context_vars["has_results"] = results_path.exists()
            context_vars["has_iteration_log"] = iteration_log_path.exists()
            context_vars["has_integrity_audit"] = integrity_audit_path.exists()
            context_vars["has_result_to_claim"] = result_to_claim_path.exists()
            context_vars["has_experiment_evidence_pack"] = evidence_pack_path.exists()
            context_vars["has_exp_plan"] = exp_plan_path.exists()

        return render_prompt(self.spec.prompt_template, ctx, **context_vars)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据mode返回不同的初始消息。"""
        mode = ctx.mode or "init"

        if mode == "init":
            return prepend_resume_prefix(
                ctx,
                (
                f"请开始T1项目初始化流程。\n\n"
                f"T1 的目标是把用户的研究意图整理成 project.yaml 和 user_seeds/ 下的种子文件。"
                f"runtime 会在第一次 LLM 调用前先完成一次 T1 启动补充 gate；"
                f"你收到 gate 回答后，必须先调用 list_files/read_file 检查 user_seeds/ 中已有材料，"
                f"再继续后续分轮访谈。不要把“检查材料”这类状态说明当成问题，"
                f"但任何需要用户选择、确认或补充的地方都必须调用 ask_human。\n\n"
                f"分轮访谈要求：\n"
                f"- 第1轮：明确研究边界与约束；ask_human 的 question 必须说明为什么需要回答，以及需要回答哪些字段。\n"
                f"- 第2轮：收集已有基础（论文、想法、约束），并说明可直接引用已发现的 seed 文件。\n"
                f"- 第2.5轮：收集外部资源（数据集、代码仓库、benchmark、预训练模型等）。\n"
                f"- 第3轮：展示草案并确认，然后生成所有文件。\n\n"
                f"重要：必须严格按照 prompt 中的 project.yaml 格式要求生成文件，"
                f"包含所有必需字段（project_id, research_direction, created_at）；keywords 和 constraints 应来自人工材料。"
                f"seed_ensemble 仅在用户明确提供 seed policy 时写入，不能使用系统默认值。"
                ),
            )
        elif mode == "evaluate":
            return prepend_resume_prefix(
                ctx,
                (
                "请开始T7.5外部实验评估流程。\n\n"
                "请读取 experiments/results_summary.json、experiments/integrity_audit.json、"
                "drafts/result_to_claim.json、drafts/experiment_evidence_pack.json、"
                "experiments/iteration_log.md 和 ideation/exp_plan.yaml，评估实验证据链，判断Situation (A/B/C/D)，"
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
        # 这里只检查 project.yaml、state.yaml 和 bridge_domain_plan.json

        ok, err = self._validate_bridge_domain_plan(ctx)
        if not ok:
            return False, err

        return True, None

    def _validate_bridge_domain_plan(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        plan_path = ctx.workspace_dir / "literature" / "bridge_domain_plan.json"
        if not plan_path.exists():
            if "bridge_domain_plan" not in ctx.outputs_expected:
                return True, None
            return False, "缺少 literature/bridge_domain_plan.json；T1 必须写入空计划或用户确认后的桥接计划"
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"bridge_domain_plan.json 解析失败: {exc}"
        if not isinstance(plan, dict) or plan.get("semantics") != "bridge_domain_plan":
            return False, "bridge_domain_plan.json semantics 必须为 bridge_domain_plan"
        domains = plan.get("bridge_domains")
        if not isinstance(domains, list):
            return False, "bridge_domain_plan.json bridge_domains 必须是数组"
        plan_source = str(plan.get("source") or "").strip()
        if not domains and plan_source not in {"none", "auto", "user", "mixed"}:
            return False, f"bridge_domain_plan 空计划 source 非法: {plan_source}"
        if domains and plan_source == "none":
            return False, "bridge_domain_plan source=none 时 bridge_domains 必须为空"
        for index, item in enumerate(domains, start=1):
            if not isinstance(item, dict):
                return False, f"bridge_domain_plan 第 {index} 项必须是对象"
            priority = str(item.get("priority") or "").strip()
            source = str(item.get("source") or "").strip()
            if priority not in {"must_explore", "should_explore"}:
                return False, f"bridge_domain_plan 第 {index} 项 priority 非法: {priority}"
            if source not in {"user", "auto"}:
                return False, (
                    "bridge_domain_plan 是已确认桥接清单，条目 source 只能记录候选来源 user/auto；"
                    "是否确认由正式写入 literature/bridge_domain_plan.json 表示，用户选择不交叉时写 source=none 的空计划"
                )
            if not str(item.get("bridge_id") or "").strip():
                return False, f"bridge_domain_plan 第 {index} 项缺少 bridge_id"
            queries = item.get("queries") or []
            if not isinstance(queries, list):
                return False, f"bridge_domain_plan 第 {index} 项 queries 必须是数组"
            if not [query for query in queries if str(query).strip()]:
                return False, f"bridge_domain_plan 第 {index} 项 queries 不能为空；T2 需要专属 bridge query"
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

        # T7.5 后续会把 human gate 的“按 PI 推荐推进”绑定到这里的 next_task。
        # 因此评估报告必须显式给出至少一个 next_task，避免状态机无法解析推荐路径。
        if "next_task" not in content:
            return False, "evaluation_decision.md必须包含至少一个 next_task 字段"

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

        seed_ensemble 是可选的实验协议输入。缺失时不得补造默认值；
        只有真正进入需要随机种子的执行协议时，才应请求人工提供。
        """
        seed_ensemble = project_data.get("seed_ensemble")

        # 它是可选输入；缺失时保留未知，而不是安装隐式 seed policy。
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

        VALID_TYPES = {
            "dataset",
            "baseline_repo",
            "pretrained_model",
            "docker_image",
            "tool",
            "script",
            "regulation",
            "standard",
            "governance_framework",
            "model_risk_management",
            "official_report",
            "web_resource",
            "other",
        }
        VALID_SOURCE_PREFIXES = {
            "huggingface:",
            "github:",
            "docker:",
            "pip:",
            "url:",
            "local:",
            "doi:",
            "official:",
            "official_source_lookup_required",
        }

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
