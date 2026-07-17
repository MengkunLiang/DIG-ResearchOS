"""Experimenter Agent — 外部实验协议主链与 legacy 内部实验兼容模式

业务需求：
- 主链模式：reboost / handoff / executor_gate / external_wait / dry_run
- 主链语义：ResearchOS 编译协议、选择外部执行器，并等待外部执行器生成 T8 handoff 报告
- 兼容模式：pilot（legacy T5）仍可显式 run-task 调用；旧 full 内部实验不再是公开节点
- Pilot 兼容模式：小规模验证实验，强制 smoke test，产出 motivation_validation.md
- Full 兼容模式：完整实验；消融、seed policy 和迭代检查只在项目协议明确要求时执行
- 读取 ideation/exp_plan.yaml（T4 的输出）
- 主链不在 ResearchOS runtime 中实现或运行真实实验；真实执行由 Codex/Claude Code/manual executor 在隔离路径完成
- ResearchOS 不接受执行器自然语言总结作为事实；T8 核心输入是 external_executor/executor_research_report.md，并可回查 raw artifact、config、log、hash

外部实验主链输出：
- T5-REBOOST-GATE/T5-REBOOST: external_executor/handoff_pack.json、external_executor/report/reboost_report.json、external_executor/report/reboost_validation_report.json、paper_card_evidence_index.json、expected_outputs_schema.json、allowed_paths.txt、AGENTS.md、CLAUDE.md
- T5-SPECIALIZE-EXECUTOR-SKILLS: schema-validated project_skill_context.yaml、skill_specialization_report.json、skills/
- specialize-executor-skills: 同一确定性编译器的显式 CLI 入口；可选的 LLM 增强只可在 suite 已发布后运行
- T5-HANDOFF: legacy 兼容入口，生成 handoff；其项目 Skill suite 也由专门的 specialization 节点发布
- T5-EXECUTOR-GATE: external_executor/report/executor_selection.json
- T5-EXTERNAL-WAIT: external_executor/wait_acceptance_report.json
- T5-DRY-RUN: external_executor/result_pack.json、executor_status.json、run_manifest.json、heartbeat.json、raw_results/configs/logs
- T5-to-T8 handoff: external_executor/executor_research_report.md
- Supporting context for T8: external_executor/ and optional legacy experiments/

Legacy Pilot 模式（T5）输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置

Legacy Pilot 模式（T5）输出：
- pilot/pilot_plan.yaml: 试点实验计划
- pilot/pilot_code/run_pilot.py: 可执行的试点代码（必须支持 --smoke_test 和 --seed 参数）
- pilot/pilot_results.json: 试点结果（必须记录与 pilot_plan 一致、且有来源的 seed）
- pilot/motivation_validation.md: 动机验证报告（必须包含 PASS/REVISE/FAIL 判定）
- pilot/smoke_test_passed.marker: 烟测通过标记（鲁棒性要求 §3.1）
- pilot/docker_digests.txt: 已实际使用的 Docker 镜像 digest

Legacy Full 模式（旧内部完整实验，当前主链不再调用）输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置
- ideation/novelty_audit.md: T4.5 新颖性预审结果（direct-full 主入口必需）
- literature/synthesis.md: 文献综合（direct-full 主入口必需）
- pilot/pilot_results.json: 试点结果（可选）
- novelty/novelty_report.md: 新颖性最终报告（可选）

Legacy Full 模式（旧内部完整实验，当前主链不再调用）输出：
- experiments/results_summary.json: 实验结果汇总（必须包含 quality_status 字段）
- experiments/iteration_log.md: 实验迭代日志
- experiments/ablations.csv: 仅在已声明消融协议时生成的结果
- experiments/seed_ensemble_summary.json: 仅在项目明确声明多 seed policy 时生成的汇总
- experiments/iteration_diversity_check.md: 实际迭代与非结果导向修改理由
- experiments/runs/{run_id}/: 每个实验的详细结果
- experiments/code/run_exp.py: 可执行的实验代码
- experiments/docker_digests.txt: Docker 镜像 digest

契约详见 docs/cn/agent_pipeline.md、docs/experiment_module_redesign.md 和 docs/external_executor_protocol.md。
"""

from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path
from typing import Any

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.agent_params import get_agent_mode_params
from ..runtime.logger import get_logger
from ..runtime.prompts import render_prompt
from ..schemas.validator import validate_record
from ..schemas.validator import validate_task_artifacts
from ..tools.docker_exec import check_docker_environment, get_default_image, load_project_config
from ..tools.external_experiment import (
    EXECUTOR_SELECTION_PATH,
    LEGACY_EXECUTOR_SELECTION_PATH,
    SKILL_SUITE,
    research_reboost_skill_prompt_excerpt,
    validate_context_reboost_handoff,
)
from ._common import (
    generate_findings_summary,
    generate_manifest,
    generate_research_log,
    load_project,
    prepend_resume_prefix,
    read_text_file,
    validate_files_exist,
)

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════
# Integrity Gate（来源: academic-research-skills）
# ══════════════════════════════════════════════════════

def run_integrity_gate(ctx: ExecutionContext) -> tuple[bool, str | None]:
    """预审阶段验证假设完整性。

    来源: academic-research-skills - Stage 2.5 Integrity Gate
    检查项:
    - hypotheses.md 存在且非空
    - novelty_audit.md 存在（T4.5 通过）
    - exp_plan.yaml 格式正确
    """
    ws = ctx.workspace_dir
    issues = []

    # 1. 检查 hypotheses.md
    hypotheses_path = ws / "ideation" / "hypotheses.md"
    if not hypotheses_path.exists():
        issues.append("缺少 ideation/hypotheses.md")
    else:
        content = hypotheses_path.read_text(encoding="utf-8")
        if len(content.strip()) < 50:
            issues.append("ideation/hypotheses.md 内容过少")

    # 2. 检查 novelty_audit.md（T4.5 通过标志）
    audit_path = ws / "ideation" / "novelty_audit.md"
    if not audit_path.exists():
        issues.append("缺少 ideation/novelty_audit.md（T4.5 尚未通过）")
    else:
        audit_content = read_text_file(audit_path)
        # 检查是否包含新颖性等级（Level 1-3）
        if not any(f"Level {i}" in audit_content for i in range(4)):
            issues.append("ideation/novelty_audit.md 缺少新颖性等级标注")

    # 3. 检查 exp_plan.yaml 格式
    exp_plan_path = ws / "ideation" / "exp_plan.yaml"
    if not exp_plan_path.exists():
        issues.append("缺少 ideation/exp_plan.yaml")
    else:
        try:
            exp_plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8"))
            if not isinstance(exp_plan, dict):
                issues.append("ideation/exp_plan.yaml 格式错误（应为 dict）")
            elif "experiments" not in exp_plan:
                issues.append("ideation/exp_plan.yaml 缺少 experiments 字段")
            elif not isinstance(exp_plan.get("experiments"), list):
                issues.append("ideation/exp_plan.yaml 中 experiments 应为列表")
            elif len(exp_plan.get("experiments", [])) == 0:
                issues.append("ideation/exp_plan.yaml 中 experiments 为空")
        except yaml.YAMLError as e:
            issues.append(f"ideation/exp_plan.yaml 解析失败: {e}")

    if issues:
        return False, f"Integrity Gate 失败: {issues}"
    return True, None


def run_experimenter_preflight(ctx: ExecutionContext) -> tuple[bool, str | None]:
    """在进入 LLM 前做实验输入契约检查，避免拿明显非法计划跑实验。"""

    if ctx.mode in EXTERNAL_EXPERIMENT_MODES:
        return True, None

    if ctx.task_id != "T5":
        return True, None

    ok, err = run_integrity_gate(ctx)
    if not ok:
        return False, err

    ws = ctx.workspace_dir
    project = load_project(ctx)
    raw_budget = (project.get("constraints") or {}).get("max_budget_usd") if isinstance(project, dict) else None
    try:
        max_budget = float(raw_budget) if raw_budget is not None else None
    except (TypeError, ValueError):
        max_budget = None
    exp_plan_path = ws / "ideation" / "exp_plan.yaml"
    try:
        plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return False, f"T5 preflight 无法读取 exp_plan.yaml: {exc}"

    budget_check = plan.get("budget_check") or {}
    if isinstance(budget_check, dict) and budget_check.get("over_budget") is True:
        return False, "T5 preflight 失败：exp_plan.yaml budget_check.over_budget=true"

    declared_total = plan.get("total_estimated_cost_usd")
    if max_budget is not None and declared_total is not None and float(declared_total) > max_budget:
        return False, (
            f"T5 preflight 失败：实验计划总成本 ${float(declared_total):.2f} "
            f"超过项目预算 ${max_budget:.2f}"
        )

    total_from_experiments = 0.0
    has_complete_cost_estimates = True
    for exp in plan.get("experiments", []) or []:
        estimate = exp.get("compute_estimate", {}) or {}
        cost = estimate.get("estimated_cost_usd")
        if cost is None:
            has_complete_cost_estimates = False
            continue
        try:
            total_from_experiments += float(cost)
        except (TypeError, ValueError):
            has_complete_cost_estimates = False
    if max_budget is not None and has_complete_cost_estimates and total_from_experiments > max_budget:
        return False, (
            f"T5 preflight 失败：实验计划逐项成本总和 ${total_from_experiments:.2f} "
            f"超过项目预算 ${max_budget:.2f}"
        )

    mode = "pilot" if ctx.task_id == "T5" else "full"
    mode_params = get_agent_mode_params("experimenter", mode)
    if mode_params.get("docker_required", True):
        project_config = load_project_config(ws)
        ok, err, details = check_docker_environment(
            project_config=project_config,
            image=get_default_image(),
            require_gpu=bool(mode_params.get("gpu_required", False)),
        )
        if not ok:
            ctx.extra["environment_blocker"] = details
            return False, err

        if bool(mode_params.get("gpu_required", False)) and not details.get("gpu_runtime_available", False):
            gpu_hours_requested = any(
                float(((exp.get("compute_estimate") or {}).get("gpu_hours") or 0) or 0) > 0
                for exp in plan.get("experiments", []) or []
                if isinstance(exp, dict)
            )
            project_allows_cpu = bool(
                (project_config.get("compute_budget") or {}).get("allow_cpu_fallback", False)
            )
            if gpu_hours_requested and not project_allows_cpu:
                return False, (
                    "WAITING_ENVIRONMENT: 实验计划声明需要 GPU，但 Docker 未检测到 nvidia runtime。"
                    "请安装/配置 nvidia-container-toolkit，或在 project.yaml 中显式设置 "
                    "compute_budget.allow_cpu_fallback: true 后 resume。"
                )

    return True, None


# ══════════════════════════════════════════════════════
# 7 AI Research Failure Modes（来源: academic-research-skills）
# ══════════════════════════════════════════════════════

FAILURE_MODE_CHECKS = {
    "FM1": "Implementation Bugs - 检查 loss 是否发散",
    "FM2": "Hallucinated Results - 交叉验证关键数字",
    "FM3": "Shortcut Reliance - 消融实验是否分离组件",
    "FM4": "Bug-as-Insight Reframing - 检查结果是否符合预期",
    "FM5": "Methodology Fabrication - 验证方法描述与实现一致",
    "FM6": "Frame-Lock - 检查是否有多视角分析",
    "FM7": "Citation Hallucinations - 验证引用存在",
}


def check_failure_modes(results: dict, log_content: str) -> list[dict]:
    """检查 7 个 AI Research Failure Mode。

    来源: academic-research-skills - 7 AI Research Failure Modes

    Returns:
        发现的问题列表，每项包含 id, mode, description, severity
    """
    issues = []
    experiments = results.get("experiments", [])

    # FM1: Implementation Bugs - 检查 loss 是否发散
    for exp in experiments:
        metrics = exp.get("metrics", {})
        loss = metrics.get("loss") or metrics.get("final_loss")
        if loss is not None and (loss > 100 or loss < 0 or loss != loss):  # nan check
            issues.append({
                "id": "FM1",
                "mode": "Implementation Bugs",
                "description": f"实验 {exp.get('experiment_id', 'unknown')} 的 loss 异常: {loss}",
                "severity": "HIGH",
            })

    # FM1: Also check logs for divergence patterns
    if "loss: nan" in log_content.lower() or "loss: inf" in log_content.lower():
        issues.append({
            "id": "FM1",
            "mode": "Implementation Bugs",
            "description": "日志中发现 nan/inf loss",
            "severity": "HIGH",
        })

    # FM2: Hallucinated Results - only verify numeric integrity here. Metric
    # scales vary by project, so generic accuracy/BLEU ranges would turn a
    # convention into an unsupported quality judgment.
    for exp in experiments:
        metrics = exp.get("metrics", {})
        for metric, value in metrics.items():
            if isinstance(value, (int, float)):
                if value != value or value in {float("inf"), float("-inf")}:
                    issues.append({
                        "id": "FM2",
                        "mode": "Non-finite Metric",
                        "description": f"实验 {exp.get('experiment_id', 'unknown')} 的 {metric} 不是有限数值",
                        "severity": "HIGH",
                    })

    # FM3: Shortcuts can only be assessed against a declared ablation plan.
    # This structural check records that variants exist but does not invent a
    # minimum number of components.
    ablations = results.get("ablation_results", [])
    if len(ablations) > 0:
        component_count = len(set(a.get("ablation_type", "") for a in ablations))
        if component_count == 0:
            issues.append({
                "id": "FM3",
                "mode": "Shortcut Reliance",
                "description": "消融记录未声明可识别的操作；无法核验其是否对应预先定义的机制检验",
                "severity": "MEDIUM",
            })

    # FM4: Do not classify a large/small delta by a generic threshold. A
    # surprising value is an audit cue only when a project-specific range or
    # preregistered expectation is present in the source-backed protocol.

    # FM5: Methodology Fabrication - 验证方法描述与实现一致
    # 这个需要更深入的分析，这里做简单检查
    for exp in experiments:
        method = exp.get("method", "")
        if method:
            # 简单检查：如果方法名与实现不一致
            run_dir = exp.get("run_dir", "")
            if run_dir:
                config_path = Path(run_dir) / "config.yaml"
                if config_path.exists():
                    try:
                        config = yaml.safe_load(config_path.read_text())
                        config_method = config.get("method", {}).get("name", "")
                        if config_method and method.lower() != config_method.lower():
                            issues.append({
                                "id": "FM5",
                                "mode": "Methodology Fabrication",
                                "description": f"实验 {exp.get('experiment_id')} 的方法名不一致: {method} vs {config_method}",
                                "severity": "LOW",
                            })
                    except Exception:
                        pass

    # FM6: Frame-Lock - 检查是否有多视角分析
    # 检查 iteration_log 是否包含多视角分析
    if "多视角" not in log_content and "perspective" not in log_content.lower():
        if "ablation" in log_content.lower() or len(experiments) >= 3:
            issues.append({
                "id": "FM6",
                "mode": "Frame-Lock",
                "description": "实验日志缺少多视角分析",
                "severity": "LOW",
            })

    # FM7: Citation Hallucinations - 验证引用存在
    # 这个主要在文档输出阶段检查，这里做占位
    # 如果需要检查，可以在 findings 或 summary 中查找未定义的引用
    for exp in experiments:
        notes = exp.get("notes", "")
        if notes:
            # 简单检查：是否有疑似幻觉引用模式
            import re
            citations = re.findall(r'\[.*?\d{4}.*?\]', notes)
            if len(citations) > 0:
                # 检查这些引用是否在相关工作中有对应
                # 这里只做简单警告
                for cite in citations[:2]:  # 只检查前2个
                    if cite not in log_content:  # 如果在主日志中未出现
                        issues.append({
                            "id": "FM7",
                            "mode": "Citation Hallucinations",
                            "description": f"发现疑似引用 {cite}，需验证是否存在于文献中",
                            "severity": "LOW",
                        })
                        break

    return issues


EXTERNAL_EXPERIMENT_MODES = {
    "reboost",
    "handoff",
    "executor_gate",
    "external_wait",
    "dry_run",
    "result_ingest",
    "integrity_audit",
    "post_novelty",
    "result_to_claim",
}


def _read_json_artifact(ws: Path, rel_path: str) -> tuple[dict[str, Any] | None, str | None]:
    path = ws / rel_path
    if not path.exists():
        return None, f"缺少 {rel_path}"
    if path.stat().st_size <= 0:
        return None, f"{rel_path} 为空"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"{rel_path} 不是合法 JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{rel_path} 顶层必须是对象"
    return data, None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_external_files(ws: Path, rel_paths: list[str]) -> tuple[bool, str | None]:
    missing = [rel for rel in rel_paths if not (ws / rel).exists()]
    if missing:
        return False, "缺少外部实验产物: " + ", ".join(missing)
    empty = [rel for rel in rel_paths if (ws / rel).is_file() and (ws / rel).stat().st_size <= 0]
    if empty:
        return False, "外部实验产物为空: " + ", ".join(empty)
    return True, None


def _read_executor_selection_artifact(ws: Path) -> tuple[dict[str, Any] | None, str | None, str]:
    errors: list[str] = []
    for rel in (EXECUTOR_SELECTION_PATH, LEGACY_EXECUTOR_SELECTION_PATH):
        selection, err = _read_json_artifact(ws, rel)
        if err is None:
            return selection, None, rel
        errors.append(err)
    return None, errors[0] if errors else f"{EXECUTOR_SELECTION_PATH} 缺失", EXECUTOR_SELECTION_PATH


def _validate_external_handoff(ws: Path, *, require_specialization: bool = False) -> tuple[bool, str | None]:
    required = [
        "external_executor/handoff_pack.json",
        "external_executor/expected_outputs_schema.json",
        "external_executor/allowed_paths.txt",
        "external_executor/input_manifest.json",
        "external_executor/AGENTS.md",
        "external_executor/CLAUDE.md",
        "external_executor/README.md",
        "external_executor/_DIR_GUIDE.md",
        "external_executor/job_state.json",
        "external_executor/expr",
    ]
    if require_specialization:
        required.extend(
            [
                "external_executor/project_skill_context.yaml",
                "external_executor/schemas/project_skill_context.schema.json",
                "external_executor/skills/research-execution/SKILL.md",
                "external_executor/report/skill_specialization_report.json",
            ]
        )
    ok, err = _require_external_files(ws, required)
    if not ok:
        return False, err
    handoff, err = _read_json_artifact(ws, "external_executor/handoff_pack.json")
    if err:
        return False, err
    if handoff.get("schema_version") == "external_executor_handoff.v1" and isinstance(handoff.get("source_manifest"), list):
        ok, err = validate_context_reboost_handoff(ws)
        if not ok:
            return False, err
        schema, err = _read_json_artifact(ws, "external_executor/expected_outputs_schema.json")
        if err:
            return False, err
        if schema.get("semantics") != "expected_external_executor_outputs_schema":
            return False, "expected_outputs_schema.json semantics 不正确"
        allowed_text = (ws / "external_executor" / "allowed_paths.txt").read_text(encoding="utf-8", errors="replace")
        if "external_executor/" not in allowed_text:
            return False, "external_executor/allowed_paths.txt 必须包含 external_executor/ 边界"
        return True, None
    if handoff.get("semantics") not in {
        "external_experiment_handoff_pack_not_execution_result",
        "external_experiment_handoff_contract",
    }:
        return False, "external_executor/handoff_pack.json semantics 不正确"
    if handoff.get("execution_mode") not in {"unselected", "dry_run", "external"}:
        return False, "handoff_pack.execution_mode 必须是 unselected/dry_run/external"
    context_reboost = handoff.get("context_reboost")
    if not isinstance(context_reboost, dict):
        return False, "handoff_pack 缺少 context_reboost"
    method_intent = handoff.get("method_intent")
    if not isinstance(method_intent, dict):
        return False, "handoff_pack 缺少 method_intent"
    if method_intent.get("status") != "draft_intent_only" or method_intent.get("not_final_method_source") is not True:
        return False, "handoff_pack.method_intent 必须标记 draft_intent_only 且不是最终 Method 来源"
    if not isinstance(handoff.get("baseline_matrix"), list):
        return False, "handoff_pack 缺少 baseline_matrix"
    if not isinstance(handoff.get("claim_evidence_matrix"), list) or not handoff.get("claim_evidence_matrix"):
        return False, "handoff_pack 缺少非空 claim_evidence_matrix"
    contract = handoff.get("experiment_contract")
    if not isinstance(contract, dict):
        return False, "handoff_pack 缺少 experiment_contract"
    metrics = contract.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        return False, "handoff_pack.experiment_contract.metrics 必须是非空列表"
    seeds = contract.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        return False, "handoff_pack.experiment_contract.seeds 必须是非空列表"
    required_baselines = contract.get("required_baselines")
    if required_baselines is not None and not isinstance(required_baselines, list):
        return False, "handoff_pack.experiment_contract.required_baselines 必须是列表"
    outputs = handoff.get("executor_outputs")
    if not isinstance(outputs, dict) or outputs.get("result_pack") != "external_executor/result_pack.json":
        return False, "handoff_pack.executor_outputs.result_pack 必须指向 external_executor/result_pack.json"
    source_artifacts = handoff.get("source_artifacts")
    if not isinstance(source_artifacts, list) or not source_artifacts:
        return False, "handoff_pack.source_artifacts 必须记录上游来源"
    allowed_paths = handoff.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not any("external_executor/" in str(item) for item in allowed_paths):
        return False, "handoff_pack.allowed_paths 必须包含 external_executor/"
    schema, err = _read_json_artifact(ws, "external_executor/expected_outputs_schema.json")
    if err:
        return False, err
    if schema.get("semantics") != "expected_external_executor_outputs_schema":
        return False, "expected_outputs_schema.json semantics 不正确"
    required_fields = schema.get("required")
    for field in (
        "context_alignment",
        "resources",
        "baseline_reproduction",
        "experiment_runs",
        "result_diagnosis",
        "module_attribution",
        "realized_method_package",
        "final_framework_figure",
        "figure_table_inventory",
        "writer_handoff",
    ):
        if not isinstance(required_fields, list) or field not in required_fields:
            return False, f"expected_outputs_schema.json required 缺少 {field}"
    selection, err, selection_rel = _read_executor_selection_artifact(ws)
    if err:
        return False, err
    if not selection or selection.get("semantics") != "external_executor_selection":
        return False, f"{selection_rel} semantics 不正确"
    manifest, err = _read_json_artifact(ws, "external_executor/input_manifest.json")
    if err:
        return False, err
    if manifest.get("semantics") != "external_executor_input_manifest":
        return False, "external_executor/input_manifest.json semantics 不正确"
    for rel in ("external_executor/AGENTS.md", "external_executor/CLAUDE.md", "external_executor/README.md"):
        text = (ws / rel).read_text(encoding="utf-8", errors="replace")
        if "dry_run: UNSET" not in text and "dry_run:" not in text:
            return False, f"{rel} 必须包含执行模式说明"
        if "external_executor/result_pack.json" not in text and rel != "external_executor/README.md":
            return False, f"{rel} 必须明确 result_pack 输出协议"
    agents_text = (ws / "external_executor" / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    if "external_executor/skills/research-execution/SKILL.md" not in agents_text:
        return False, "external_executor/AGENTS.md 必须能一句话启动 root skill"
    if require_specialization:
        context_path = ws / "external_executor" / "project_skill_context.yaml"
        schema_path = ws / "external_executor" / "schemas" / "project_skill_context.schema.json"
        if not context_path.is_file():
            return False, "external_executor/project_skill_context.yaml 缺失"
        if not schema_path.is_file():
            return False, "external_executor/schemas/project_skill_context.schema.json 缺失"
        report, err = _read_json_artifact(ws, "external_executor/report/skill_specialization_report.json")
        if err:
            return False, err
        ok, err = _validate_specialization_report(report)
        if not ok:
            return False, err
        for skill_name in SKILL_SUITE:
            ok, err = _validate_specialized_skill_file(ws, skill_name)
            if not ok:
                return False, err
    job_state, err = _read_json_artifact(ws, "external_executor/job_state.json")
    if err:
        return False, err
    if job_state.get("semantics") != "external_executor_job_state":
        return False, "external_executor/job_state.json semantics 不正确"
    return True, None


def _validate_reboost_handoff_controls(ws: Path) -> tuple[bool, str | None]:
    required = [
        "external_executor/handoff_pack.json",
        "external_executor/report/reboost_report.json",
        "external_executor/report/reboost_validation_report.json",
        "external_executor/paper_card_evidence_index.json",
        "external_executor/expected_outputs_schema.json",
        "external_executor/allowed_paths.txt",
        "external_executor/AGENTS.md",
        "external_executor/CLAUDE.md",
    ]
    ok, err = _require_external_files(ws, required)
    if not ok:
        return False, err

    schema, err = _read_json_artifact(ws, "external_executor/expected_outputs_schema.json")
    if err:
        return False, err
    if schema.get("semantics") != "expected_external_executor_outputs_schema":
        return False, "expected_outputs_schema.json semantics 不正确"

    validation, err = _read_json_artifact(ws, "external_executor/report/reboost_validation_report.json")
    if err:
        return False, err
    if validation.get("valid") is not True:
        return False, "external_executor/report/reboost_validation_report.json 必须 valid=true"

    paper_index, err = _read_json_artifact(ws, "external_executor/paper_card_evidence_index.json")
    if err:
        return False, err
    if paper_index.get("semantics") != "paper_card_evidence_index":
        return False, "external_executor/paper_card_evidence_index.json semantics 不正确"

    allowed_text = (ws / "external_executor" / "allowed_paths.txt").read_text(
        encoding="utf-8",
        errors="replace",
    )
    if "external_executor/" not in allowed_text or "no  researchos/" not in allowed_text:
        return False, "external_executor/allowed_paths.txt 必须包含外部执行边界和 ResearchOS 源码保护"

    agents_text = (ws / "external_executor" / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    if "external_executor/skills/research-execution/SKILL.md" not in agents_text:
        return False, "external_executor/AGENTS.md 必须能一句话启动 root skill"
    if "external_executor/result_pack.json" not in agents_text:
        return False, "external_executor/AGENTS.md 必须明确 result_pack 输出协议"

    claude_text = (ws / "external_executor" / "CLAUDE.md").read_text(encoding="utf-8", errors="replace")
    if "external_executor/result_pack.json" not in claude_text:
        return False, "external_executor/CLAUDE.md 必须明确 result_pack 输出协议"
    return True, None


def _validate_specialized_skill_file(ws: Path, skill_name: str) -> tuple[bool, str | None]:
    skill_path = ws / "external_executor" / "skills" / skill_name / "SKILL.md"
    if not skill_path.is_file():
        return False, f"external_executor/skills/{skill_name}/SKILL.md 缺失"
    text = skill_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return False, f"external_executor/skills/{skill_name}/SKILL.md 缺少 YAML frontmatter"
    try:
        raw_frontmatter = text.split("\n---\n", 1)[0].removeprefix("---\n")
        frontmatter = yaml.safe_load(raw_frontmatter) or {}
    except Exception as exc:
        return False, f"external_executor/skills/{skill_name}/SKILL.md frontmatter 解析失败: {exc}"
    if not isinstance(frontmatter, dict) or frontmatter.get("name") != skill_name:
        return False, f"external_executor/skills/{skill_name}/SKILL.md frontmatter name 必须为 {skill_name}"
    if text.count("<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->") != 1 or text.count("<!-- PROJECT-SPECIFIC-GUIDANCE:END -->") != 1:
        return False, f"external_executor/skills/{skill_name}/SKILL.md Project-Specific Guidance marker 不完整"
    if "## Project-Specific Guidance" not in text:
        return False, f"external_executor/skills/{skill_name}/SKILL.md 缺少 Project-Specific Guidance"
    return True, None


def _validate_specialization_report(report: dict[str, Any]) -> tuple[bool, str | None]:
    if report.get("schema_version") != "skill_specialization_report.v1":
        return False, "external_executor/report/skill_specialization_report.json schema_version 不正确"
    if report.get("status") not in {"ready", "incomplete"}:
        return False, "external_executor/report/skill_specialization_report.json status 必须为 ready 或 incomplete"
    if report.get("context_file") != "external_executor/project_skill_context.yaml":
        return False, "skill_specialization_report.context_file 不正确"
    if report.get("context_schema") != "external_executor/schemas/project_skill_context.schema.json":
        return False, "skill_specialization_report.context_schema 不正确"
    if report.get("skills_total") != len(SKILL_SUITE):
        return False, "skill_specialization_report.skills_total 必须为 13"
    if report.get("skills_specialized") != len(SKILL_SUITE):
        return False, "skill_specialization_report.skills_specialized 必须为 13"
    skills = report.get("skills")
    if not isinstance(skills, list):
        return False, "skill_specialization_report.skills 必须是列表"
    reported = {str(item.get("skill_name")) for item in skills if isinstance(item, dict)}
    missing = [name for name in SKILL_SUITE if name not in reported]
    if missing:
        return False, "skill_specialization_report 缺少 skill（未覆盖 skill）: " + ", ".join(missing)
    return True, None


def _validate_external_executor_selection(ws: Path) -> tuple[bool, str | None]:
    selection, err, selection_rel = _read_executor_selection_artifact(ws)
    if err:
        return False, err
    if not selection or selection.get("semantics") != "external_executor_selection":
        return False, f"{selection_rel} semantics 不正确"
    selected = selection.get("selected_executor")
    if selected not in {"mock_dry_run", "codex_cli", "claude_code_window", "manual"}:
        return False, "executor_selection.selected_executor 必须是 mock_dry_run/codex_cli/claude_code_window/manual"
    if selection.get("next_state") not in {"T5-DRY-RUN", "T5-EXTERNAL-WAIT"}:
        return False, "executor_selection.next_state 不正确"
    for rel in (
        "external_executor/AGENTS.md",
        "external_executor/CLAUDE.md",
    ):
        path = ws / rel
        if path.exists() and "UNSET" in path.read_text(encoding="utf-8", errors="replace"):
            return False, f"{rel} 仍包含 UNSET，T5-EXECUTOR-GATE 未完成 mode patch"
    return True, None


def _validate_external_wait(ws: Path) -> tuple[bool, str | None]:
    report, err = _read_json_artifact(ws, "external_executor/wait_acceptance_report.json")
    if err:
        return False, err
    if report.get("semantics") != "external_executor_wait_acceptance_report" or report.get("ok") is not True:
        return False, "wait_acceptance_report.json 必须 ok=true"
    return True, None


def _validate_external_dry_run(ws: Path) -> tuple[bool, str | None]:
    required = [
        "external_executor/handoff_pack.json",
        EXECUTOR_SELECTION_PATH,
        "external_executor/result_pack.json",
        "external_executor/executor_status.json",
        "external_executor/run_manifest.json",
        "external_executor/heartbeat.json",
        "external_executor/raw_results",
        "external_executor/configs",
        "external_executor/logs",
    ]
    ok, err = _require_external_files(ws, required)
    if not ok:
        return False, err
    ok, err = _validate_external_executor_selection(ws)
    if not ok:
        return False, err
    selection, err, _selection_rel = _read_executor_selection_artifact(ws)
    if err:
        return False, err
    if not selection or selection.get("selected_executor") != "mock_dry_run":
        return False, "T5-DRY-RUN 只能在 T5-EXECUTOR-GATE 选择 mock_dry_run 后运行"
    result_pack, err = _read_json_artifact(ws, "external_executor/result_pack.json")
    if err:
        return False, err
    if result_pack.get("semantics") != "external_executor_result_pack":
        return False, "external_executor/result_pack.json semantics 不正确"
    if result_pack.get("dry_run") is not True or result_pack.get("mock_only") is not True:
        return False, "dry-run result_pack 必须显式 dry_run=true 且 mock_only=true"
    for field in (
        "schema_version",
        "executor_status",
        "context_alignment",
        "resources",
        "baseline_reproduction",
        "experiment_runs",
        "result_diagnosis",
        "module_attribution",
        "realized_method_package",
        "final_framework_figure",
        "figure_table_inventory",
        "writer_handoff",
    ):
        if field not in result_pack:
            return False, f"dry-run result_pack 缺少 required 字段 {field}"
    metrics = result_pack.get("metrics")
    if not isinstance(metrics, list):
        return False, "result_pack.metrics 必须是列表"
    if not metrics and result_pack.get("mock_only") is not True:
        return False, "真实执行的 result_pack.metrics 必须是非空列表"
    artifacts = result_pack.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False, "result_pack.artifacts 必须是非空列表"
    artifact_by_path: dict[str, dict[str, Any]] = {}
    for idx, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict):
            return False, f"result_pack.artifacts[{idx}] 必须是对象"
        rel = str(artifact.get("path") or "")
        if not rel:
            return False, f"result_pack.artifacts[{idx}] 缺少 path"
        path = ws / rel
        if not path.exists():
            return False, f"result_pack.artifacts[{idx}] 指向不存在文件: {rel}"
        expected_hash = artifact.get("sha256")
        if expected_hash and path.is_file() and expected_hash != _sha256_file(path):
            return False, f"result_pack.artifacts[{idx}] hash 不匹配: {rel}"
        artifact_by_path[rel] = artifact
    for idx, metric in enumerate(metrics, start=1):
        if not isinstance(metric, dict):
            return False, f"result_pack.metrics[{idx}] 必须是对象"
        for key in ("metric_id", "name", "value", "source_artifact"):
            if metric.get(key) in (None, ""):
                return False, f"result_pack.metrics[{idx}] 缺少 {key}"
        if metric.get("mock_only") is not True:
            return False, f"result_pack.metrics[{idx}] 必须标记 mock_only=true"
        if str(metric.get("source_artifact")) not in artifact_by_path:
            return False, f"result_pack.metrics[{idx}].source_artifact 未被 artifacts 索引"
    manifest, err = _read_json_artifact(ws, "external_executor/run_manifest.json")
    if err:
        return False, err
    if manifest.get("semantics") != "external_executor_run_manifest":
        return False, "external_executor/run_manifest.json semantics 不正确"
    if manifest.get("dry_run") is not True or manifest.get("mock_only") is not True:
        return False, "run_manifest 必须显式 dry_run=true 且 mock_only=true"
    status, err = _read_json_artifact(ws, "external_executor/executor_status.json")
    if err:
        return False, err
    if status.get("semantics") != "external_executor_status":
        return False, "executor_status.json semantics 不正确"
    if status.get("status") != "done":
        return False, "executor_status.status 必须是 done"
    if status.get("accepted") is True:
        return False, "dry-run executor_status.accepted 不能为 true；执行器 done 不等于 ResearchOS accepted"
    if status.get("run_manifest") != "external_executor/run_manifest.json":
        return False, "executor_status.run_manifest 必须指向 external_executor/run_manifest.json"
    return True, None


def _validate_external_ingest(ws: Path) -> tuple[bool, str | None]:
    required = [
        "experiments/results_summary.json",
        "experiments/run_records.jsonl",
        "experiments/evidence_index.json",
        "experiments/ingest_report.json",
    ]
    ok, err = _require_external_files(ws, required)
    if not ok:
        return False, err
    summary, err = _read_json_artifact(ws, "experiments/results_summary.json")
    if err:
        return False, err
    if summary.get("semantics") != "external_executor_results_summary":
        return False, "experiments/results_summary.json semantics 不正确"
    if summary.get("source") != "external_executor":
        return False, "experiments/results_summary.json source 必须是 external_executor"
    metrics_obj = summary.get("metrics")
    metric_records = summary.get("metric_records")
    mock_only = summary.get("mock_only") is True
    if not isinstance(metrics_obj, dict) or (not metrics_obj and not mock_only):
        return False, "experiments/results_summary.json 必须包含 metrics 对象；非 mock 执行必须非空"
    if not isinstance(metric_records, list) or (not metric_records and not mock_only):
        return False, "experiments/results_summary.json 必须包含 metric_records 列表；非 mock 执行必须非空"
    if not isinstance(summary.get("experiments"), list) or not summary.get("experiments"):
        return False, "experiments/results_summary.json 必须包含非空 experiments"
    if summary.get("ingest_report_ref") != "experiments/ingest_report.json":
        return False, "results_summary.ingest_report_ref 必须指向 experiments/ingest_report.json"
    evidence, err = _read_json_artifact(ws, "experiments/evidence_index.json")
    if err:
        return False, err
    if evidence.get("semantics") != "external_experiment_evidence_index":
        return False, "experiments/evidence_index.json semantics 不正确"
    for field in (
        "baseline_reproduction",
        "resources",
        "experiment_runs",
        "realized_method_package",
        "final_framework_figure",
        "figure_table_inventory",
        "writer_handoff",
    ):
        if field not in evidence:
            return False, f"experiments/evidence_index.json 缺少 {field}"
    report, err = _read_json_artifact(ws, "experiments/ingest_report.json")
    if err:
        return False, err
    if report.get("semantics") != "external_result_ingest_report" or report.get("ok") is not True:
        return False, "experiments/ingest_report.json 必须是 ok=true 的 ingest report"
    ok, err = _validate_external_binding_fingerprints(ws, summary, evidence, report)
    if not ok:
        return False, err
    run_records = (ws / "experiments" / "run_records.jsonl").read_text(encoding="utf-8", errors="replace")
    if "external_executor_result_pack" not in run_records:
        return False, "experiments/run_records.jsonl 必须保存原始 external_executor_result_pack"
    return True, None


def _validate_external_binding_fingerprints(
    ws: Path,
    summary: dict[str, Any],
    evidence: dict[str, Any],
    report: dict[str, Any],
) -> tuple[bool, str | None]:
    required = {
        "executor_selection_ref": "selection_sha256",
        "result_pack_ref": "result_pack_sha256",
        "executor_status_ref": "executor_status_sha256",
    }
    for rel_key, hash_key in required.items():
        rel = str(summary.get(rel_key) or evidence.get(rel_key) or report.get(rel_key) or "").strip()
        expected_hash = str(summary.get(hash_key) or evidence.get(hash_key) or report.get(hash_key) or "").strip()
        if not rel or not expected_hash:
            return False, f"external executor binding 缺少外部执行器绑定字段: {rel_key}/{hash_key}"
        path = ws / rel
        if not path.exists() or not path.is_file():
            return False, f"external executor binding 绑定的外部源文件不存在: {rel}"
        if _sha256_file(path) != expected_hash:
            return False, f"external executor binding 外部源文件 hash 不匹配: {rel}"
    selected = str(summary.get("selected_executor") or report.get("selected_executor") or "").strip()
    if selected not in {"mock_dry_run", "codex_cli", "claude_code_window", "manual"}:
        return False, "external executor binding 缺少合法 selected_executor"
    return True, None


def _validate_external_integrity_audit(ws: Path) -> tuple[bool, str | None]:
    audit, err = _read_json_artifact(ws, "experiments/integrity_audit.json")
    if err:
        return False, err
    if audit.get("semantics") != "external_experiment_integrity_audit":
        return False, "experiments/integrity_audit.json semantics 不正确"
    if audit.get("status") not in {"pass", "mock_only", "fail"}:
        return False, "integrity_audit.status 必须是 pass/mock_only/fail"
    if not isinstance(audit.get("issues"), list):
        return False, "integrity_audit.issues 必须是列表"
    if audit.get("mock_only") is True and audit.get("status") != "mock_only":
        return False, "mock_only 审计状态必须是 mock_only"
    coverage = audit.get("required_baseline_coverage")
    if not isinstance(coverage, dict):
        return False, "integrity_audit 必须包含 required_baseline_coverage"
    if coverage.get("status") not in {"complete", "incomplete", "missing", "no_required_baselines", "mock_only"}:
        return False, "required_baseline_coverage.status 不正确"
    summary, err = _read_json_artifact(ws, "experiments/results_summary.json")
    if err:
        return False, err
    evidence, err = _read_json_artifact(ws, "experiments/evidence_index.json")
    if err:
        return False, err
    report, err = _read_json_artifact(ws, "experiments/ingest_report.json")
    if err:
        return False, err
    ok, err = _validate_external_binding_fingerprints(ws, summary, evidence, {**report, **audit})
    if not ok:
        return False, err
    fairness = ws / "experiments" / "experiment_fairness_review.md"
    if not fairness.exists() or fairness.stat().st_size <= 0:
        return False, "缺少 experiments/experiment_fairness_review.md"
    result_audit, err = _read_json_artifact(ws, "experiments/result_audit.json")
    if err:
        return False, err
    if result_audit.get("semantics") != "external_experiment_result_audit":
        return False, "experiments/result_audit.json semantics 不正确"
    for field in ("baseline_fairness", "metric_provenance", "mock_dry_run", "result_figure_provenance"):
        if not isinstance(result_audit.get(field), dict):
            return False, f"experiments/result_audit.json 缺少 {field}"
    for rel, semantics in {
        "experiments/method_audit.json": "external_method_intent_vs_realized_audit",
        "experiments/framework_figure_audit.json": "external_framework_figure_audit",
        "drafts/method_writing_resources.json": "audited_method_writing_resources",
    }.items():
        data, err = _read_json_artifact(ws, rel)
        if err:
            return False, err
        allowed_semantics = {semantics}
        if rel == "drafts/method_writing_resources.json":
            allowed_semantics.add("method_writing_resources")
            allowed_semantics.add("external_method_writing_resources")
        if data.get("semantics") not in allowed_semantics:
            return False, f"{rel} semantics 不正确"
        if rel == "experiments/method_audit.json":
            consistency = data.get("method_consistency_audit")
            if not isinstance(consistency, dict):
                return False, "experiments/method_audit.json 缺少 method_consistency_audit"
            for field in (
                "method_intent_matches_realized_method",
                "realized_method_matches_code",
                "framework_figure_matches_code",
                "ablation_matches_modules",
                "contribution_drift",
                "requires_post_novelty_check",
                "required_action",
            ):
                if field not in consistency:
                    return False, f"method_consistency_audit 缺少 {field}"
        if rel == "drafts/method_writing_resources.json" and not isinstance(data.get("method_writing_resources"), dict):
            return False, "drafts/method_writing_resources.json 缺少 method_writing_resources"
    if audit.get("contribution_drift") not in {"none", "minor", "major", "unknown"}:
        return False, "integrity_audit.contribution_drift 不正确"
    return True, None


def _validate_post_experiment_novelty(ws: Path) -> tuple[bool, str | None]:
    check, err = _read_json_artifact(ws, "novelty/post_experiment_novelty_check.json")
    if err:
        return False, err
    if check.get("semantics") != "post_experiment_novelty_check":
        return False, "post_experiment_novelty_check.json semantics 不正确"
    if check.get("novelty_after_implementation") not in {"strong", "moderate", "weak", "collision_risk"}:
        return False, "post_experiment_novelty_check.novelty_after_implementation 不正确"
    if check.get("recommended_next_task") not in {"T8-RESOURCE", "T5-REBOOST-GATE", "T5-HANDOFF", "T4"}:
        return False, "post_experiment_novelty_check.recommended_next_task 不正确"
    if check.get("contribution_drift") not in {"none", "minor", "major", "unknown"}:
        return False, "post_experiment_novelty_check.contribution_drift 不正确"
    if check.get("required_action") not in {"none", "update_method", "rerun_experiment", "rerun_novelty", "human_review", "narrow_claim"}:
        return False, "post_experiment_novelty_check.required_action 不正确"
    collision = ws / "novelty" / "post_experiment_collision_cases.md"
    if not collision.exists() or collision.stat().st_size <= 0:
        return False, "缺少 novelty/post_experiment_collision_cases.md"
    return True, None


def _validate_external_result_to_claim(ws: Path) -> tuple[bool, str | None]:
    required = [
        "experiments/experimental_claims.json",
        "drafts/result_to_claim.json",
        "drafts/experiment_evidence_pack.json",
        "experiments/iteration_log.md",
        "drafts/must_not_claim.md",
        "drafts/claim_support_matrix.csv",
        "drafts/limitations_from_experiments.md",
        "drafts/figure_table_evidence_map.json",
        "drafts/method_writing_resources.json",
    ]
    ok, err = _require_external_files(ws, required)
    if not ok:
        return False, err
    claims, err = _read_json_artifact(ws, "experiments/experimental_claims.json")
    if err:
        return False, err
    mirror, err = _read_json_artifact(ws, "drafts/result_to_claim.json")
    if err:
        return False, err
    for rel, data in {
        "experiments/experimental_claims.json": claims,
        "drafts/result_to_claim.json": mirror,
    }.items():
        if data.get("semantics") != "mechanical_result_to_claim_map_not_final_scientific_judgment":
            return False, f"{rel} semantics 不正确"
        mappings = data.get("claim_mappings")
        if not isinstance(mappings, list):
            return False, f"{rel}.claim_mappings 必须是列表"
        if not mappings:
            if data.get("mock_only") is not True:
                return False, f"{rel} 非 mock 执行必须包含非空 claim_mappings"
            must_not_claim = data.get("global_must_not_claim")
            if not isinstance(must_not_claim, list) or not must_not_claim:
                return False, f"{rel} 的空 mock claim_mappings 必须附带 global_must_not_claim"
        for idx, mapping in enumerate(mappings, start=1):
            if not isinstance(mapping, dict):
                return False, f"{rel} claim_mappings[{idx}] 必须是对象"
            if mapping.get("support_status") not in {"supported", "weak", "unsupported_mock_only"}:
                return False, f"{rel} claim_mappings[{idx}].support_status 无效"
            if mapping.get("claim_strength") not in {"strong", "moderate", "weak", "unsupported", None}:
                return False, f"{rel} claim_mappings[{idx}].claim_strength 无效"
            if not isinstance(mapping.get("metric_refs"), list) or not mapping.get("metric_refs"):
                return False, f"{rel} claim_mappings[{idx}] 缺少 metric_refs"
    pack, err = _read_json_artifact(ws, "drafts/experiment_evidence_pack.json")
    if err:
        return False, err
    if pack.get("semantics") != "normalized_experiment_evidence_pack":
        return False, "drafts/experiment_evidence_pack.json semantics 不正确"
    if pack.get("source") != "external_executor":
        return False, "experiment_evidence_pack.source 必须是 external_executor"
    if not isinstance(pack.get("metrics"), list) or (not pack.get("metrics") and pack.get("mock_only") is not True):
        return False, "experiment_evidence_pack.metrics 必须是列表；非 mock 执行必须非空"
    if not isinstance(pack.get("claims"), list) or (not pack.get("claims") and pack.get("mock_only") is not True):
        return False, "experiment_evidence_pack.claims 必须是列表；非 mock 执行必须非空"
    log = (ws / "experiments" / "iteration_log.md").read_text(encoding="utf-8", errors="replace")
    if "External Experiment Iteration Log" not in log:
        return False, "experiments/iteration_log.md 必须记录 External Experiment Iteration Log"
    return True, None


class ExperimenterAgent(Agent):
    """实验执行 Agent。支持当前 T5 外部执行链和 legacy pilot 调试。

    - pilot 模式：小规模验证实验，强制 smoke test，使用 pilot_plan 中明确声明的 seed
    - full 模式：旧内部完整实验兼容代码路径；当前主状态机和 run-task 不再暴露
    """

    def __init__(self, mode: str | None = None):
        super().__init__(
            build_agent_spec(
                "experimenter",
                mode=mode,
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "write_structured_file",
                        "list_files",
                        "append_file",
                        "build_experiment_handoff_pack",
                        "compile_research_reboost_handoff",
                        "specialize_executor_skills",
                        "select_external_executor",
                        "wait_for_external_executor_result",
                        "mock_external_dry_run",
                        "ingest_external_results",
                        "audit_experiment_integrity",
                        "build_post_experiment_novelty_check",
                        "map_results_to_claims",
                        "build_experiment_evidence_pack",
                        "finish_task",
                    ],
                    "max_steps": 150,
                    "max_tokens_total": 800_000,
                    "max_wall_seconds": 14400,
                    "max_validation_retries": 2,
                    "temperature": 0.3,
                    "allowed_read_prefixes": ["", "ideation/", "experiments/", "pilot/", "literature/", "external_executor/", "drafts/"],
                    "allowed_write_prefixes": ["experiments/", "pilot/", "external_executor/", "drafts/"],
                    "prompt_template": "experimenter.j2",
                    "pre_hooks": [run_experimenter_preflight],
                    "structured_outputs": {
                        "experiments/results_summary.json": "results_summary",
                        "pilot/pilot_results.json": "pilot_results",
                        "pilot/pilot_plan.yaml": "pilot_plan",
                    },
                },
            )
        )
        self._mode = mode

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染 system prompt，根据 mode 动态生成不同的指令。

        - pilot 模式：添加 smoke test、小规模数据、motivation validation 指令
        - full 模式：添加 ablation、seed ensemble、迭代多样性指令
        """
        mode = ctx.mode or "full"
        project = load_project(ctx)
        ws = ctx.workspace_dir

        # 读取实验计划
        exp_plan_path = ws / "ideation" / "exp_plan.yaml"
        exp_plan = {}
        if exp_plan_path.exists():
            try:
                exp_plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8"))
            except Exception:
                exp_plan = {}

        # 读取假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")

        # 读取 pilot 结果（full 模式可能需要）
        pilot_results = {}
        pilot_results_path = ws / "pilot" / "pilot_results.json"
        if mode == "full" and pilot_results_path.exists():
            try:
                pilot_results = json.loads(pilot_results_path.read_text(encoding="utf-8"))
            except Exception:
                pilot_results = {}

        # 读取新颖性报告和必须补充的基线（full 模式可能需要）
        novelty_report = ""
        novelty_audit = ""
        must_add_baselines = ""
        direct_full_mode = False
        if mode == "full":
            direct_full_mode = bool(ctx.extra.get("experiment_entrypoint") == "direct_full_from_t45")
            novelty_audit = read_text_file(
                ws / "ideation" / "novelty_audit.md",
                default="",
            )[:2000]
            for novelty_report_path in (
                ws / "novelty" / "novelty_report.md",
                ws / "ideation" / "novelty_report.md",
            ):
                if novelty_report_path.exists():
                    novelty_report = read_text_file(novelty_report_path, default="")[:1000]
                    break
            must_add_baselines = read_text_file(
                ws / "novelty" / "must_add_baselines.md",
                default="",
            )[:1500]
            if not novelty_report and novelty_audit:
                novelty_report = (
                    "[T6 skipped] Using T4.5 novelty audit as the current novelty-risk input.\n\n"
                    + novelty_audit[:1200]
                )

        # Seeds and budgets are research protocol inputs, not runtime defaults.
        # Preserve their absence so the prompt can request an explicit source
        # rather than silently converting a missing policy into an experiment.
        seed_ensemble = project.get("seed_ensemble") if isinstance(project.get("seed_ensemble"), dict) else {}
        budget_hint = (
            "仅使用 project.yaml constraints、ideation/exp_plan.yaml 或人工确认中可追溯的预算与资源限制；"
            "未声明时标记 unknown，并在执行前请求补充，不得使用系统默认的步数、时长或 token 配额。"
        )

        resume_state = {
            "resume_mode": bool(ctx.extra.get("resume_mode")),
            "resume_state_path": str(ctx.extra.get("resume_state_path", "")),
            "resume_existing_outputs": list(ctx.extra.get("resume_existing_outputs", [])),
            "resume_missing_outputs": list(ctx.extra.get("resume_missing_outputs", [])),
            "resume_existing_code_files": list(ctx.extra.get("resume_existing_code_files", [])),
            "resume_has_existing_code": bool(ctx.extra.get("resume_has_existing_code")),
            "resume_reason": str(ctx.extra.get("resume_reason", "")),
        }

        # 注意: mode 由 render_prompt 内部通过 ctx.mode 传入，此处不再重复传递
        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            exp_plan=exp_plan,
            hypotheses_preview=hypotheses[:2000],
            experiment_count=len(exp_plan.get("experiments", [])),
            pilot_results=pilot_results,
            novelty_report_preview=novelty_report,
            novelty_audit_preview=novelty_audit,
            must_add_baselines_preview=must_add_baselines,
            research_reboost_skill=research_reboost_skill_prompt_excerpt() if mode == "reboost" else "",
            direct_full_mode=direct_full_mode,
            skipped_optional_tasks=list(ctx.extra.get("skipped_optional_tasks", [])),
            budget_hint=budget_hint,
            seed_ensemble=seed_ensemble,
            resume_state=resume_state,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息，根据 mode 生成不同的指令。"""
        mode = ctx.mode or "full"

        if mode == "reboost":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行 T5-REBOOST：使用系统 prompt 中的 `skills/research-reboost` skill contract，"
                    "调用当前 LLM API 完成 Pre-T5 → external executor handoff 的语义重编译。"
                    "不要要求用户手动拉起 Codex CLI，不要执行实验、实现代码、选择执行器或写 result_pack。\n\n"
                    "系统 prompt 已加载 `skills/research-reboost` 的 Skill contract 和 references；"
                    "不要用 read_file 读取仓库级 `skills/...`、`references/...` 或 `scripts/...`。"
                    "先读取当前 workspace 的 Pre-T5 源文件，由当前 LLM 编译完整 `handoff_pack` 对象。"
                    "然后调用 `compile_research_reboost_handoff(handoff_pack=...)`。该工具会按 "
                    "`references/handoff_pack.schema.json` 和 `scripts/validate_handoff.py` 校验并 pretty-print "
                    "`external_executor/handoff_pack.json`，同时写 `external_executor/report/reboost_report.json`、"
                    "`external_executor/report/reboost_validation_report.json`、paper_card_evidence_index、"
                    "expected schema、allowed paths、AGENTS/CLAUDE。executor_selection 由后续 "
                    "T5-EXECUTOR-GATE 按所选执行器生成；不会生成 Codex/Claude/manual prompt 文件。项目专属 executor Skills 会在下一步 "
                    "`T5-SPECIALIZE-EXECUTOR-SKILLS` 中由确定性编译器单独发布。\n\n"
                    "完成后读取 `external_executor/report/reboost_report.json`；如果 validation_ok=true，"
                    "调用 finish_task。`compile_research_reboost_handoff` 是 handoff pack 的唯一写入者；"
                    "不要用 write_file 修改它，不要手写压缩成一行的 JSON；不要生成或改写 "
                    "`external_executor/skills/`。"
                ),
            )
        if mode == "handoff":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行外部实验 T5-HANDOFF：调用 build_experiment_handoff_pack，"
                    "读取并保留 T5-REBOOST-GATE 已写入的 external_executor/handoff_pack.json#context_reboost，"
                    "然后补全 external_executor/handoff_pack.json、expected_outputs_schema.json、"
                    "allowed_paths.txt、AGENTS.md、CLAUDE.md、README.md、job_state.json。"
                    "不要运行真实实验；执行器选择由 "
                    "T5-EXECUTOR-GATE 完成。"
                ),
            )
        if mode == "executor_gate":
            return prepend_resume_prefix(
                ctx,
                (
                    "T5 的人工 gate 节点通常由状态机 immediate gate 处理，不会启动 LLM。"
                    "若当前是 T5-EXECUTOR-GATE 且被直接 run-task 调用，请调用 "
                    "select_external_executor 写入真实选择，并确保 external_executor/*.md 中不再包含 UNSET，然后 finish_task。"
                ),
            )
        if mode == "external_wait":
            return prepend_resume_prefix(
                ctx,
                (
                    "T5-EXTERNAL-WAIT 是确定性等待节点，runtime 会在 LLM 前检查 "
                    "external_executor/result_pack.json 和 executor_status.json。"
                    "如果直接进入本 prompt，请调用 wait_for_external_executor_result；缺结果时不要伪造，"
                    "应暂停等待外部执行器写回。"
                ),
            )
        if mode == "dry_run":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行外部实验 T5-DRY-RUN：调用 mock_external_dry_run，"
                    "生成 schema-compatible 的 external_executor/result_pack.json 和 executor_status.json。"
                    "这是协议 dry-run，不是真实实验。"
                ),
            )
        if mode == "result_ingest":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行旧结果摄取兼容流程：调用 ingest_external_results，把 external_executor/result_pack.json "
                    "规范化为 experiments/results_summary.json、run_records.jsonl、evidence_index.json "
                    "和 ingest_report.json。不要相信自然语言总结。"
                ),
            )
        if mode == "integrity_audit":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行旧实验完整性审计兼容流程：调用 audit_experiment_integrity，审计 experiments/results_summary.json "
                    "和 evidence_index.json 的 provenance、mock_only、metric、seed、artifact 引用和 required baseline 覆盖。"
                ),
            )
        if mode == "post_novelty":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行旧实验后 novelty 复核兼容流程：调用 build_post_experiment_novelty_check，"
                    "基于 results_summary、integrity_audit、required_baselines 和 novelty_audit 生成 "
                    "novelty/post_experiment_novelty_check.json 与 post_experiment_collision_cases.md。"
                    "该节点不自动拒绝 idea，只把 claim 降级边界写成 artifact。"
                ),
            )
        if mode == "result_to_claim":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行旧 result-to-claim 兼容流程：调用 map_results_to_claims，再调用 build_experiment_evidence_pack，"
                    "生成 experiments/experimental_claims.json、drafts/result_to_claim.json、"
                    "drafts/experiment_evidence_pack.json、drafts/must_not_claim.md、"
                    "drafts/claim_support_matrix.csv、drafts/limitations_from_experiments.md、"
                    "drafts/figure_table_evidence_map.json 和 experiments/iteration_log.md。"
                ),
            )

        if mode == "pilot":
            if ctx.extra.get("resume_mode"):
                return prepend_resume_prefix(
                    ctx,
                    (
                    "请继续 T5 Pilot 实验任务。\n"
                    "先检查 pilot/ 下已有代码和已有输出，只补尚未完成的产物；"
                    "如果 pilot/pilot_code 已存在，默认复用并继续执行 smoke test/实验/结果整理，"
                    "不要无谓重写全部代码。"
                    ),
                )
            return prepend_resume_prefix(
                ctx,
                (
                "请按 system prompt 执行 T5 Pilot 实验任务。\n"
                "实验计划在 ideation/exp_plan.yaml 中。\n"
                "请先从 ideation/exp_plan.yaml 或人工确认材料中提取数据比例与 seed；若任一项未声明，"
                "暂停并请求补充，不得写默认值。随后执行已声明的小规模试点与 smoke test，生成 pilot/pilot_plan.yaml、pilot/pilot_results.json 和 "
                "pilot/motivation_validation.md（必须包含 PASS/REVISE/FAIL 判定）。"
                ),
            )
        else:
            if ctx.extra.get("resume_mode"):
                return prepend_resume_prefix(
                    ctx,
                    (
                    "请继续旧内部完整实验兼容任务。\n"
                    "优先复用 experiments/ 下已有代码、运行目录和中间结果，只补剩余的 summary / ablation / log 产物，"
                    "不要从头重建整个实验目录。"
                    ),
                )
            return prepend_resume_prefix(
                ctx,
                (
                "请按 system prompt 执行旧内部完整实验兼容任务。\n"
                "实验计划在 ideation/exp_plan.yaml 中。\n"
                "注意：默认主链现在使用外部执行器报告直连 T8；本模式仅用于显式 legacy 调试。"
                "如果 pilot 或 novelty_final 产物不存在，"
                "请基于 hypotheses、exp_plan、ideation/novelty_audit.md、synthesis.md 和 comparison_table.csv "
                "只编译已被这些材料或人工确认支持的实验协议，并在 iteration_log.md 中明确记录 direct_full_from_t45。\n"
                "消融、baseline、指标、seed、数据集和资源上限均必须有可追溯来源；缺失时写入 blocker 并请求补充，"
                "不要以常见实验配置代替协议。生成实际运行支持的 experiments/results_summary.json 和 iteration_log；"
                "仅在协议明确要求时生成 ablation 或 seed ensemble 多-seed 汇总。"
                ),
            )

    @staticmethod
    def _legacy_full_protocol_requirements(ws: Path) -> dict[str, object]:
        """Read explicit legacy-only validation requirements without defaults.

        The external-executor path owns the current formal protocol.  This
        compatibility branch must therefore never reinterpret a missing
        ablation or seed policy as a generic research requirement.
        """

        plan_path = ws / "ideation" / "exp_plan.yaml"
        try:
            plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        except Exception:
            plan = {}
        plan = plan if isinstance(plan, dict) else {}
        requirements = plan.get("validation_requirements")
        requirements = requirements if isinstance(requirements, dict) else {}
        required_outputs = plan.get("required_outputs")
        output_names = {str(item).strip() for item in required_outputs} if isinstance(required_outputs, list) else set()

        ablation_spec = plan.get("ablation_plan") or requirements.get("ablation")
        require_ablation = bool(ablation_spec) or "experiments/ablations.csv" in output_names
        min_count_raw = requirements.get("minimum_ablation_count")
        if isinstance(ablation_spec, dict):
            min_count_raw = ablation_spec.get("minimum_count", min_count_raw)
        try:
            minimum_ablation_count = max(0, int(min_count_raw)) if min_count_raw is not None else 0
        except (TypeError, ValueError):
            minimum_ablation_count = 0
        # An explicit positive minimum is itself an explicit ablation
        # requirement, even when a separate ``ablation_plan`` block is absent.
        require_ablation = require_ablation or minimum_ablation_count > 0

        project_path = ws / "project.yaml"
        try:
            project = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
        except Exception:
            project = {}
        seed_policy = project.get("seed_ensemble") if isinstance(project, dict) else {}
        declared_seed_count = 0
        if isinstance(seed_policy, dict):
            declared_seed_count = len(
                {
                    int(seed)
                    for tier in ("tier1_seeds", "tier2_seeds", "tier3_seeds")
                    for seed in (seed_policy.get(tier) or [])
                    if isinstance(seed, int)
                }
            )
        require_seed_summary = (
            bool(requirements.get("require_seed_ensemble_summary"))
            or "experiments/seed_ensemble_summary.json" in output_names
            or declared_seed_count > 1
        )
        return {
            "require_ablation": require_ablation,
            "minimum_ablation_count": minimum_ablation_count,
            "require_seed_summary": require_seed_summary,
            "declared_seed_count": declared_seed_count,
        }

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """验证 Experimenter 输出，包含完整的鲁棒性检查。

        根据 mode 执行不同的验证逻辑：
        - pilot 模式：Integrity Gate → smoke_test_passed.marker → motivation 判定
        - full 模式：Failure Mode 检查 → ablation → seed ensemble → 迭代多样性

        来源: academic-research-skills - Integrity Gate + 7 AI Research Failure Modes
        """
        mode = ctx.mode or "full"
        ws = ctx.workspace_dir
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        if mode == "reboost":
            ok, err = validate_context_reboost_handoff(ws)
            if not ok:
                return False, err
            report, report_err = _read_json_artifact(ws, "external_executor/report/reboost_report.json")
            if report_err:
                return False, report_err
            if report.get("semantics") != "external_executor_context_reboost_report":
                return False, "external_executor/report/reboost_report.json semantics 不正确"
            if report.get("handoff_pack") != "external_executor/handoff_pack.json":
                return False, "external_executor/report/reboost_report.json 必须指向 handoff_pack"
            return _validate_reboost_handoff_controls(ws)
        if mode == "handoff":
            return _validate_external_handoff(ws)
        if mode == "executor_gate":
            return _validate_external_executor_selection(ws)
        if mode == "external_wait":
            return _validate_external_wait(ws)
        if mode == "dry_run":
            return _validate_external_dry_run(ws)
        if mode == "result_ingest":
            return _validate_external_ingest(ws)
        if mode == "integrity_audit":
            return _validate_external_integrity_audit(ws)
        if mode == "post_novelty":
            return _validate_post_experiment_novelty(ws)
        if mode == "result_to_claim":
            return _validate_external_result_to_claim(ws)

        if mode == "pilot":
            # ═══════════════════════════════════════════════════════
            # Pilot 模式验证
            # ═══════════════════════════════════════════════════════

            # 0. Integrity Gate 检查（来源: academic-research-skills）
            # 为什么需要：在投入资源前验证假设完整性，避免无效实验
            # 检查逻辑：hypotheses.md + novelty_audit.md + exp_plan.yaml
            # 失败影响：实验前提条件不满足，可能浪费资源
            ig_ok, ig_err = run_integrity_gate(ctx)
            if not ig_ok:
                return False, f"Integrity Gate 失败: {ig_err}"

            # 1. 基本文件存在性检查
            required_files = [
                "pilot/pilot_plan.yaml",
                "pilot/pilot_results.json",
                "pilot/motivation_validation.md",
                "pilot/pilot_code/run_pilot.py",
            ]
            ok, err = validate_files_exist(ctx, required_files)
            if not ok:
                return False, err

            # 2. Smoke test 检查（§3.1 - 鲁棒性要求）
            # 为什么需要：确保代码在小规模数据上能正常运行，避免浪费资源在有 bug 的代码上
            # 检查逻辑：必须存在 smoke_test_passed.marker 文件
            # 失败影响：无法保证代码质量，可能在 full 模式浪费大量资源
            smoke_marker = ws / "pilot" / "smoke_test_passed.marker"
            if not smoke_marker.exists():
                return False, "缺少 pilot/smoke_test_passed.marker，未执行烟测（§3.1）"

            # 3. Motivation validation 判定检查
            # 为什么需要：确保 pilot 实验验证了研究动机，避免无意义的 full 实验
            # 检查逻辑：motivation_validation.md 必须包含明确的 PASS/REVISE/FAIL 判定
            # 失败影响：无法判断是否应该继续 full 实验
            validation = read_text_file(ws / "pilot" / "motivation_validation.md")
            if not any(x in validation for x in ["PASS", "REVISE", "FAIL"]):
                return False, "pilot/motivation_validation.md 必须包含明确判定（PASS/REVISE/FAIL）"

            # 4. Seed source check: reproducibility requires a seed explicitly
            # declared in the source-backed pilot plan, not a global default.
            try:
                results = json.loads((ws / "pilot" / "pilot_results.json").read_text(encoding="utf-8"))
            except Exception as e:
                return False, f"pilot/pilot_results.json 解析失败: {e}"

            try:
                pilot_plan = yaml.safe_load((ws / "pilot" / "pilot_plan.yaml").read_text(encoding="utf-8")) or {}
            except Exception as e:
                return False, f"pilot/pilot_plan.yaml 解析失败: {e}"
            seed_ok, seed_err = self._validate_pilot_seed(results, pilot_plan)
            if not seed_ok:
                return False, seed_err

            if ctx.task_id == "T5" and not ctx.extra.get("artifact_validation"):
                ok, err = validate_task_artifacts(ctx.task_id, ws)
                if not ok:
                    return False, err

            # 5. Docker digest 检查（§8.2 - 复现保证）
            # 为什么需要：记录 Docker 镜像的精确版本，确保环境可复现
            # 检查逻辑：必须存在 docker_digests.txt 文件
            # 失败影响：无法保证环境的精确复现
            digest_file = ws / "pilot" / "docker_digests.txt"
            if not digest_file.exists():
                return False, "缺少 pilot/docker_digests.txt（§8.2）"
            digest_text = read_text_file(digest_file)
            if not self._has_reproducible_docker_evidence(digest_text):
                return False, "pilot/docker_digests.txt 必须记录真实 Docker 镜像 digest（包含 sha256，不能是本地占位说明）"
            docker_success_count = ctx.extra.get("docker_exec_success_count")
            if docker_success_count is not None and int(docker_success_count or 0) < 1:
                return False, "T5 要求至少一次成功的 docker_exec 执行，不能只用 bash_run 本地运行"

            code_write_count = int(ctx.extra.get("pilot_code_write_count", 0) or 0)
            audit_ok, audit_err = self._validate_experiment_audit(ws, required=code_write_count > 1)
            if not audit_ok:
                return False, audit_err

            # 6. 代码参数检查
            # 为什么需要：确保生成的代码支持必需的参数（--smoke_test, --seed）
            # 检查逻辑：检查 run_pilot.py 是否包含参数解析代码
            # 失败影响：代码无法在不同模式下运行
            pilot_code = read_text_file(ws / "pilot" / "pilot_code" / "run_pilot.py")
            if "--smoke_test" not in pilot_code and "smoke_test" not in pilot_code:
                logger.warning("pilot/pilot_code/run_pilot.py 可能缺少 --smoke_test 参数支持")
            if "--seed" not in pilot_code and "seed" not in pilot_code:
                logger.warning("pilot/pilot_code/run_pilot.py 可能缺少 --seed 参数支持")

            # 9. Material Passport（来源: academic-research-skills）
            # 为什么需要：记录制品来源和元数据，支持跨会话追踪
            # 检查逻辑：生成 manifest.yaml，记录输出文件和校验和
            # 用途：后续 Agent 可以验证输入文件是否变化
            generate_manifest(
                ctx,
                output_dir="pilot",
                artifacts=[
                    {"path": "pilot_results.json", "type": "json"},
                    {"path": "motivation_validation.md", "type": "markdown"},
                    {"path": "pilot_plan.yaml", "type": "yaml"},
                    {"path": "experiment_audit.json", "type": "json"},
                    {"path": "smoke_test_passed.marker", "type": "marker"},
                    {"path": "docker_digests.txt", "type": "text"},
                ],
                inputs=[
                    {"path": "ideation/hypotheses.md", "required": True},
                    {"path": "ideation/exp_plan.yaml", "required": True},
                ],
            )

            return True, None

        else:  # full 模式
            # ═══════════════════════════════════════════════════════
            # Full 模式验证
            # ═══════════════════════════════════════════════════════

            # 1. 基本文件存在性检查
            protocol = self._legacy_full_protocol_requirements(ws)
            required_files = [
                "experiments/results_summary.json",
                "experiments/iteration_log.md",
                "experiments/docker_digests.txt",
            ]
            if protocol["require_ablation"]:
                required_files.append("experiments/ablations.csv")
            if protocol["require_seed_summary"]:
                required_files.append("experiments/seed_ensemble_summary.json")
            ok, err = validate_files_exist(ctx, required_files)
            if not ok:
                return False, err

            digest_file = ws / "experiments" / "docker_digests.txt"
            digest_text = read_text_file(digest_file)
            if not self._has_reproducible_docker_evidence(digest_text):
                return False, (
                    "experiments/docker_digests.txt 必须记录真实 Docker 镜像 digest"
                    "（包含 sha256，不能是本地占位说明）"
                )
            docker_success_count = ctx.extra.get("docker_exec_success_count")
            if docker_success_count is not None and int(docker_success_count or 0) < 1:
                return False, "旧内部完整实验要求至少一次成功的 docker_exec 执行，不能只用 bash_run 本地运行"

            # 2. Ablation validation follows only the declared experiment
            # protocol.  A missing plan does not authorize a generic three-row
            # table or a fabricated component-removal study.
            ablations_path = ws / "experiments" / "ablations.csv"
            if protocol["require_ablation"]:
                try:
                    ablation_lines = ablations_path.read_text(encoding="utf-8").strip().split("\n")
                    ablation_count = max(0, len(ablation_lines) - 1)
                    required_count = int(protocol["minimum_ablation_count"])
                    if required_count and ablation_count < required_count:
                        return False, (
                            "experiments/ablations.csv 未满足 exp_plan.yaml 已声明的 minimum_ablation_count="
                            f"{required_count}；当前 {ablation_count} 条"
                        )
                except Exception as exc:
                    return False, f"experiments/ablations.csv 读取失败: {exc}"

            # 3. Results summary 解析和基本检查
            try:
                results = json.loads((ws / "experiments" / "results_summary.json").read_text(encoding="utf-8"))
            except Exception as e:
                return False, f"experiments/results_summary.json 解析失败: {e}"

            if "experiments" not in results:
                return False, "experiments/results_summary.json 必须包含 'experiments' 字段"

            experiments = results.get("experiments", [])
            if len(experiments) == 0:
                return False, "experiments/results_summary.json 必须包含至少 1 个实验结果"

            # 3.5 7 AI Research Failure Mode 检查（来源: academic-research-skills）
            # 为什么需要：检测常见的 AI 错误模式，确保结果可靠性
            # 检查逻辑：FM1-FM7 共 7 个检查项
            # 失败影响：警告但不阻断，允许用户决定是否接受结果
            log_content = ""
            log_path = ws / "experiments" / "iteration_log.md"
            if log_path.exists():
                log_content = read_text_file(log_path)

            fm_issues = check_failure_modes(results, log_content)
            if fm_issues:
                high_severity = [i for i in fm_issues if i["severity"] == "HIGH"]
                if high_severity:
                    # HIGH 级别问题记录到 results_summary
                    logger.warning(f"发现 {len(high_severity)} 个 HIGH 严重性问题: {high_severity}")
                    # 可选：写入 findings.md
                    generate_findings_summary(
                        ctx,
                        findings=[f"Failure Mode 问题: {i['description']}" for i in fm_issues],
                        output_dir="experiments/",
                    )
                    # 生成 research log
                    generate_research_log(
                        ctx,
                        decision="Failure Mode 检查发现 HIGH 严重性问题",
                        rationale=f"发现 {len(high_severity)} 个 HIGH 和 {len(fm_issues)-len(high_severity)} 个中低级别问题",
                        metadata={"issues": fm_issues},
                    )

            # 4. Seed validation does not impose a tier-specific count.  A
            # summary is required only when the project explicitly selected a
            # multi-seed policy or named this artifact as a required output.
            if protocol["require_seed_summary"]:
                ensemble_path = ws / "experiments" / "seed_ensemble_summary.json"
                if not ensemble_path.exists():
                    return False, (
                        "项目协议声明了多 seed policy，但缺少 experiments/seed_ensemble_summary.json"
                    )

            # 5. Iteration diversity records actual iteration history.
            # 为什么需要：防止重复调参，确保每次迭代都有实质性改进
            # 检查逻辑：必须存在 iteration_diversity_check.md 文件
            # 失败影响：可能浪费资源在重复实验上
            diversity_path = ws / "experiments" / "iteration_diversity_check.md"
            if not diversity_path.exists():
                return False, "缺少 experiments/iteration_diversity_check.md（§5.1）"

            # 6. Silent failure 检查（§3.2 - 鲁棒性要求）
            # 为什么需要：检测 nan loss、OOM 等静默失败，避免误判实验成功
            # 检查逻辑：检查每个实验的 quality_status 字段
            # 失败影响：警告但不失败，允许用户决定是否接受有问题的结果
            for exp in experiments:
                quality_status = exp.get("quality_status", "ok")
                if quality_status == "questionable":
                    logger.warning(
                        f"实验 {exp.get('experiment_id', 'unknown')} 有质量问题（nan/inf/OOM），"
                        f"请检查 logs（§3.2）"
                    )

            # 8. Iteration log length check
            log_path = ws / "experiments" / "iteration_log.md"
            log_content = read_text_file(log_path)
            if len(log_content) < 100:
                return False, f"experiments/iteration_log.md 过短（{len(log_content)} 字符）"

            # 9. Material Passport（来源: academic-research-skills）
            # 为什么需要：记录制品来源和元数据，支持跨会话追踪
            # 检查逻辑：生成 manifest.yaml，记录输出文件和校验和
            # 用途：后续 Agent 可以验证输入文件是否变化
            generate_manifest(
                ctx,
                output_dir="experiments",
                artifacts=[
                    {"path": "results_summary.json", "type": "json"},
                    {"path": "iteration_log.md", "type": "markdown"},
                    {"path": "iteration_diversity_check.md", "type": "markdown"},
                    {"path": "docker_digests.txt", "type": "text"},
                ]
                + ([{"path": "ablations.csv", "type": "csv"}] if protocol["require_ablation"] else [])
                + ([{"path": "seed_ensemble_summary.json", "type": "json"}] if protocol["require_seed_summary"] else []),
                inputs=[
                    {"path": "ideation/hypotheses.md", "required": True},
                    {"path": "ideation/exp_plan.yaml", "required": True},
                    {"path": "pilot/pilot_results.json", "required": True},
                ],
            )

            return True, None

    @staticmethod
    def _validate_pilot_seed(results: dict, pilot_plan: dict) -> tuple[bool, str | None]:
        """Require result seeds to match an explicitly declared pilot-plan policy."""

        declared: set[int] = set()
        if isinstance(pilot_plan, dict):
            if isinstance(pilot_plan.get("seed"), int):
                declared.add(int(pilot_plan["seed"]))
            for plan_experiment in pilot_plan.get("experiments", []) or []:
                if isinstance(plan_experiment, dict) and isinstance(plan_experiment.get("seed"), int):
                    declared.add(int(plan_experiment["seed"]))
        if not declared:
            return False, "pilot_plan 未声明 seed；请由 exp_plan 或人工输入提供 seed policy，不能使用系统默认值"

        observed: list[tuple[str, object]] = []
        if results.get("seed") is not None:
            observed.append(("top_level", results.get("seed")))
        for idx, experiment in enumerate(results.get("experiments", []) or [], start=1):
            if isinstance(experiment, dict):
                observed.append((str(experiment.get("experiment_id") or f"#{idx}"), experiment.get("seed")))
        if not observed:
            return False, "pilot_results.json 必须记录顶层或逐实验 seed，并与 pilot_plan 一致"
        invalid = [name for name, value in observed if not isinstance(value, int) or int(value) not in declared]
        if invalid:
            return False, (
                "pilot_results 的 seed 未在 pilot_plan 声明: "
                + ", ".join(invalid[:5])
                + f"；pilot_plan 允许的 seed={sorted(declared)}"
            )
        return True, None

    @staticmethod
    def _has_reproducible_docker_evidence(digest_text: str) -> bool:
        lowered = digest_text.lower()
        if any(marker in lowered for marker in ["local build", "no remote digest", "未使用", "not used"]):
            return False
        return "sha256:" in lowered or "@sha256" in lowered

    @staticmethod
    def _validate_experiment_audit(ws: Path, *, required: bool) -> tuple[bool, str | None]:
        audit_path = ws / "pilot" / "experiment_audit.json"
        if not audit_path.exists():
            if required:
                return False, "多次重写 pilot 代码后必须生成 pilot/experiment_audit.json，说明每次修改原因"
            return True, None
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"pilot/experiment_audit.json 解析失败: {exc}"

        revisions = audit.get("code_revisions", [])
        if not isinstance(revisions, list) or not revisions:
            return False, "pilot/experiment_audit.json 必须包含非空 code_revisions"
        forbidden = {"outcome_hacking", "make_results_match_hypothesis", "result_chasing"}
        for idx, revision in enumerate(revisions, start=1):
            reason_type = str(revision.get("reason_type", "")).strip()
            if not reason_type:
                return False, f"experiment_audit 第 {idx} 条缺少 reason_type"
            if reason_type in forbidden:
                return False, f"experiment_audit 第 {idx} 条显示结果导向式改写: {reason_type}"
        return True, None
