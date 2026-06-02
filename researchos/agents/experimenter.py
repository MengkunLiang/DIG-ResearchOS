"""Experimenter Agent — 外部实验协议主链与 legacy 内部实验兼容模式

业务需求：
- 主链模式：handoff / dry_run / result_ingest / integrity_audit / result_to_claim
- 主链语义：ResearchOS 编译协议、选择外部执行器、摄取和审计结果，再生成 result-to-claim
- 兼容模式：pilot（T5）和 full（T7）仍可显式 run-task 调用
- Pilot 兼容模式：小规模验证实验，强制 smoke test，产出 motivation_validation.md
- Full 兼容模式：完整实验，强制 ablation，支持 seed ensemble 和迭代多样性检查
- 读取 ideation/exp_plan.yaml（T4 的输出）
- 主链不在 ResearchOS runtime 中实现或运行真实实验；真实执行由 Codex/Claude Code/manual executor 在隔离路径完成
- ResearchOS 不接受执行器自然语言总结作为事实，只接受 raw artifact、config、log、hash、ingest/audit/result-to-claim

外部实验主链输出：
- T5-HANDOFF: external_executor/handoff_pack.json、executor_prompt.md、codex_prompt.md、claude_code_prompt.md、expected_outputs_schema.json、allowed_paths.txt
- T5-DRY-RUN: external_executor/result_pack.json、executor_status.json、run_manifest.json、heartbeat.json、raw_results/configs/logs
- T7-INGEST: experiments/results_summary.json、run_records.jsonl、evidence_index.json、ingest_report.json
- T7-AUDIT: experiments/integrity_audit.json
- T7-CLAIMS: experiments/experimental_claims.json、drafts/result_to_claim.json、drafts/experiment_evidence_pack.json、experiments/iteration_log.md

Legacy Pilot 模式（T5）输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置

Legacy Pilot 模式（T5）输出：
- pilot/pilot_plan.yaml: 试点实验计划
- pilot/pilot_code/run_pilot.py: 可执行的试点代码（必须支持 --smoke_test 和 --seed 参数）
- pilot/pilot_results.json: 试点结果（必须包含 seed=42）
- pilot/motivation_validation.md: 动机验证报告（必须包含 PASS/REVISE/FAIL 判定）
- pilot/smoke_test_passed.marker: 烟测通过标记（鲁棒性要求 §3.1）
- pilot/docker_digests.txt: Docker 镜像 digest（鲁棒性要求 §8.2）

Legacy Full 模式（T7）输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置
- ideation/novelty_audit.md: T4.5 新颖性预审结果（direct-full 主入口必需）
- literature/synthesis.md: 文献综合（direct-full 主入口必需）
- pilot/pilot_results.json: 试点结果（可选）
- novelty/novelty_report.md: 新颖性最终报告（可选）

Legacy Full 模式（T7）输出：
- experiments/results_summary.json: 实验结果汇总（必须包含 quality_status 字段）
- experiments/iteration_log.md: 实验迭代日志
- experiments/ablations.csv: Ablation 结果（最少 3 条，鲁棒性要求 §5.3）
- experiments/seed_ensemble_summary.json: Seed ensemble 汇总（鲁棒性要求 §3.3）
- experiments/iteration_diversity_check.md: 迭代多样性检查（鲁棒性要求 §5.1）
- experiments/runs/{run_id}/: 每个实验的详细结果
- experiments/code/run_exp.py: 可执行的实验代码
- experiments/docker_digests.txt: Docker 镜像 digest

契约详见 docs/agent_pipeline.md、docs/experiment_module_redesign.md 和 docs/external_executor_protocol.md。
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

    if ctx.task_id not in {"T5", "T7"}:
        return True, None

    ok, err = run_integrity_gate(ctx)
    if not ok:
        return False, err

    if ctx.task_id == "T7":
        ws = ctx.workspace_dir
        required_direct_inputs = [
            "ideation/novelty_audit.md",
            "literature/synthesis.md",
        ]
        missing = [rel for rel in required_direct_inputs if not (ws / rel).exists()]
        if missing:
            return False, f"legacy T7 preflight 失败：direct-full 缺少必要输入 {missing}"

    ws = ctx.workspace_dir
    project = load_project(ctx)
    max_budget = float(project.get("constraints", {}).get("max_budget_usd", 100.0) or 100.0)
    exp_plan_path = ws / "ideation" / "exp_plan.yaml"
    try:
        plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return False, f"T5 preflight 无法读取 exp_plan.yaml: {exc}"

    budget_check = plan.get("budget_check") or {}
    if isinstance(budget_check, dict) and budget_check.get("over_budget") is True:
        return False, "T5 preflight 失败：exp_plan.yaml budget_check.over_budget=true"

    declared_total = plan.get("total_estimated_cost_usd")
    if declared_total is not None and float(declared_total) > max_budget:
        return False, (
            f"T5 preflight 失败：实验计划总成本 ${float(declared_total):.2f} "
            f"超过项目预算 ${max_budget:.2f}"
        )

    total_from_experiments = 0.0
    for exp in plan.get("experiments", []) or []:
        estimate = exp.get("compute_estimate", {}) or {}
        cost = estimate.get("estimated_cost_usd")
        gpu_hours = float(estimate.get("gpu_hours", 0) or 0)
        total_from_experiments += float(cost) if cost is not None else gpu_hours * 3.0
    if total_from_experiments > max_budget:
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

    # FM2: Hallucinated Results - 交叉验证关键数字
    # 检查关键指标是否在合理范围内
    for exp in experiments:
        metrics = exp.get("metrics", {})
        for metric, value in metrics.items():
            if isinstance(value, (int, float)):
                # 通用合理性检查
                if metric.lower() in ["accuracy", "precision", "recall", "f1"]:
                    if not 0 <= value <= 1.5:  # 允许一些误差
                        issues.append({
                            "id": "FM2",
                            "mode": "Hallucinated Results",
                            "description": f"实验 {exp.get('experiment_id', 'unknown')} 的 {metric}={value} 超出合理范围",
                            "severity": "MEDIUM",
                        })
                elif metric.lower() in ["bleu", "rouge", "meteor"]:
                    if not 0 <= value <= 100:
                        issues.append({
                            "id": "FM2",
                            "mode": "Hallucinated Results",
                            "description": f"实验 {exp.get('experiment_id', 'unknown')} 的 {metric}={value} 超出合理范围",
                            "severity": "MEDIUM",
                        })

    # FM3: Shortcut Reliance - 消融实验是否分离组件
    # 检查 ablation 实验是否真正分离了组件
    ablations = results.get("ablation_results", [])
    if len(ablations) > 0:
        # 检查是否有足够的 ablation 变体
        component_count = len(set(a.get("ablation_type", "") for a in ablations))
        if component_count < 2:
            issues.append({
                "id": "FM3",
                "mode": "Shortcut Reliance",
                "description": "消融实验数量不足，可能未充分分离组件",
                "severity": "MEDIUM",
            })

    # FM4: Bug-as-Insight Reframing - 检查结果是否符合预期
    # 简单检查：如果 improvement 太大，可能需要怀疑
    for exp in experiments:
        improvement = exp.get("improvement", {})
        for metric, delta in improvement.items():
            if isinstance(delta, (int, float)) and abs(delta) > 0.5:  # 50% 提升太离谱
                issues.append({
                    "id": "FM4",
                    "mode": "Bug-as-Insight Reframing",
                    "description": f"实验 {exp.get('experiment_id', 'unknown')} 的 {metric} 提升 {delta} 过大，需验证",
                    "severity": "LOW",
                })

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
    "handoff",
    "dry_run",
    "result_ingest",
    "integrity_audit",
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


def _validate_external_handoff(ws: Path) -> tuple[bool, str | None]:
    required = [
        "external_executor/handoff_pack.json",
        "external_executor/executor_prompt.md",
        "external_executor/expected_outputs_schema.json",
        "external_executor/allowed_paths.txt",
        "external_executor/executor_selection.json",
        "external_executor/input_manifest.json",
        "external_executor/codex_prompt.md",
        "external_executor/claude_code_prompt.md",
        "external_executor/manual_instructions.md",
    ]
    ok, err = _require_external_files(ws, required)
    if not ok:
        return False, err
    handoff, err = _read_json_artifact(ws, "external_executor/handoff_pack.json")
    if err:
        return False, err
    if handoff.get("semantics") != "external_experiment_handoff_pack_not_execution_result":
        return False, "external_executor/handoff_pack.json semantics 不正确"
    if handoff.get("execution_mode") not in {"dry_run", "external"}:
        return False, "handoff_pack.execution_mode 必须是 dry_run/external"
    contract = handoff.get("experiment_contract")
    if not isinstance(contract, dict):
        return False, "handoff_pack 缺少 experiment_contract"
    metrics = contract.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        return False, "handoff_pack.experiment_contract.metrics 必须是非空列表"
    seeds = contract.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        return False, "handoff_pack.experiment_contract.seeds 必须是非空列表"
    outputs = handoff.get("executor_outputs")
    if not isinstance(outputs, dict) or outputs.get("result_pack") != "external_executor/result_pack.json":
        return False, "handoff_pack.executor_outputs.result_pack 必须指向 external_executor/result_pack.json"
    source_artifacts = handoff.get("source_artifacts")
    if not isinstance(source_artifacts, list) or not source_artifacts:
        return False, "handoff_pack.source_artifacts 必须记录上游来源"
    allowed_paths = handoff.get("allowed_paths")
    if not isinstance(allowed_paths, list) or "external_executor/" not in allowed_paths:
        return False, "handoff_pack.allowed_paths 必须包含 external_executor/"
    schema, err = _read_json_artifact(ws, "external_executor/expected_outputs_schema.json")
    if err:
        return False, err
    if schema.get("semantics") != "expected_external_executor_outputs_schema":
        return False, "expected_outputs_schema.json semantics 不正确"
    selection, err = _read_json_artifact(ws, "external_executor/executor_selection.json")
    if err:
        return False, err
    if selection.get("semantics") != "external_executor_selection":
        return False, "external_executor/executor_selection.json semantics 不正确"
    manifest, err = _read_json_artifact(ws, "external_executor/input_manifest.json")
    if err:
        return False, err
    if manifest.get("semantics") != "external_executor_input_manifest":
        return False, "external_executor/input_manifest.json semantics 不正确"
    prompt = (ws / "external_executor" / "executor_prompt.md").read_text(encoding="utf-8", errors="replace")
    if "external_executor/result_pack.json" not in prompt:
        return False, "executor_prompt.md 必须明确要求写 external_executor/result_pack.json"
    for rel in ("external_executor/codex_prompt.md", "external_executor/claude_code_prompt.md", "external_executor/manual_instructions.md"):
        text = (ws / rel).read_text(encoding="utf-8", errors="replace")
        if "external_executor/result_pack.json" not in text:
            return False, f"{rel} 必须明确 result_pack 输出协议"
    return True, None


def _validate_external_dry_run(ws: Path) -> tuple[bool, str | None]:
    required = [
        "external_executor/handoff_pack.json",
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
    result_pack, err = _read_json_artifact(ws, "external_executor/result_pack.json")
    if err:
        return False, err
    if result_pack.get("semantics") != "external_executor_result_pack":
        return False, "external_executor/result_pack.json semantics 不正确"
    if result_pack.get("dry_run") is not True or result_pack.get("mock_only") is not True:
        return False, "dry-run result_pack 必须显式 dry_run=true 且 mock_only=true"
    metrics = result_pack.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        return False, "result_pack.metrics 必须是非空列表"
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
    if not isinstance(summary.get("metrics"), list) or not summary.get("metrics"):
        return False, "experiments/results_summary.json 必须包含非空 metrics"
    if not isinstance(summary.get("experiments"), list) or not summary.get("experiments"):
        return False, "experiments/results_summary.json 必须包含非空 experiments"
    if summary.get("ingest_report_ref") != "experiments/ingest_report.json":
        return False, "results_summary.ingest_report_ref 必须指向 experiments/ingest_report.json"
    evidence, err = _read_json_artifact(ws, "experiments/evidence_index.json")
    if err:
        return False, err
    if evidence.get("semantics") != "external_experiment_evidence_index":
        return False, "experiments/evidence_index.json semantics 不正确"
    report, err = _read_json_artifact(ws, "experiments/ingest_report.json")
    if err:
        return False, err
    if report.get("semantics") != "external_result_ingest_report" or report.get("ok") is not True:
        return False, "experiments/ingest_report.json 必须是 ok=true 的 ingest report"
    run_records = (ws / "experiments" / "run_records.jsonl").read_text(encoding="utf-8", errors="replace")
    if "external_executor_result_pack" not in run_records:
        return False, "experiments/run_records.jsonl 必须保存原始 external_executor_result_pack"
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
    return True, None


def _validate_external_result_to_claim(ws: Path) -> tuple[bool, str | None]:
    required = [
        "experiments/experimental_claims.json",
        "drafts/result_to_claim.json",
        "drafts/experiment_evidence_pack.json",
        "experiments/iteration_log.md",
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
        if not isinstance(mappings, list) or not mappings:
            return False, f"{rel} 必须包含非空 claim_mappings"
        for idx, mapping in enumerate(mappings, start=1):
            if not isinstance(mapping, dict):
                return False, f"{rel} claim_mappings[{idx}] 必须是对象"
            if mapping.get("support_status") not in {"supported", "weak", "unsupported_mock_only"}:
                return False, f"{rel} claim_mappings[{idx}].support_status 无效"
            if not isinstance(mapping.get("metric_refs"), list) or not mapping.get("metric_refs"):
                return False, f"{rel} claim_mappings[{idx}] 缺少 metric_refs"
    pack, err = _read_json_artifact(ws, "drafts/experiment_evidence_pack.json")
    if err:
        return False, err
    if pack.get("semantics") != "normalized_experiment_evidence_pack":
        return False, "drafts/experiment_evidence_pack.json semantics 不正确"
    if pack.get("source") != "external_executor":
        return False, "experiment_evidence_pack.source 必须是 external_executor"
    if not isinstance(pack.get("metrics"), list) or not pack.get("metrics"):
        return False, "experiment_evidence_pack.metrics 必须是非空列表"
    if not isinstance(pack.get("claims"), list) or not pack.get("claims"):
        return False, "experiment_evidence_pack.claims 必须是非空列表"
    log = (ws / "experiments" / "iteration_log.md").read_text(encoding="utf-8", errors="replace")
    if "External Experiment Iteration Log" not in log:
        return False, "experiments/iteration_log.md 必须记录 External Experiment Iteration Log"
    return True, None


class ExperimenterAgent(Agent):
    """实验执行 Agent。支持 pilot（T5）和 full（T7）两种模式。

    - pilot 模式：小规模验证实验，强制 smoke test，固定 seed=42
    - full 模式：完整实验，强制 ablation（最少 3 条），支持 seed ensemble
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
                        "mock_external_dry_run",
                        "ingest_external_results",
                        "audit_experiment_integrity",
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

        # 读取 seed_ensemble 配置（§2.5）
        seed_ensemble = project.get("seed_ensemble", {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        })

        # 根据 mode 设置预算提示
        if mode == "pilot":
            budget_hint = "建议 100 步内完成，2 小时内，400K tokens"
        else:
            budget_hint = "最多 150 步，4 小时，600K tokens"

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
            direct_full_mode=direct_full_mode,
            skipped_optional_tasks=list(ctx.extra.get("skipped_optional_tasks", [])),
            budget_hint=budget_hint,
            seed_ensemble=seed_ensemble,
            resume_state=resume_state,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息，根据 mode 生成不同的指令。"""
        mode = ctx.mode or "full"

        if mode == "handoff":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行外部实验 T5-HANDOFF：调用 build_experiment_handoff_pack，"
                    "生成 external_executor/handoff_pack.json、executor_prompt.md、"
                    "expected_outputs_schema.json 和 allowed_paths.txt。不要运行真实实验。"
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
                    "请执行 T7-INGEST：调用 ingest_external_results，把 external_executor/result_pack.json "
                    "规范化为 experiments/results_summary.json、run_records.jsonl、evidence_index.json "
                    "和 ingest_report.json。不要相信自然语言总结。"
                ),
            )
        if mode == "integrity_audit":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行 T7-AUDIT：调用 audit_experiment_integrity，审计 experiments/results_summary.json "
                    "和 evidence_index.json 的 provenance、mock_only、metric、seed 与 artifact 引用。"
                ),
            )
        if mode == "result_to_claim":
            return prepend_resume_prefix(
                ctx,
                (
                    "请执行 T7-CLAIMS：调用 map_results_to_claims，再调用 build_experiment_evidence_pack，"
                    "生成 experiments/experimental_claims.json、drafts/result_to_claim.json、"
                    "drafts/experiment_evidence_pack.json 和 experiments/iteration_log.md。"
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
                "请执行小规模试点实验（5-10% 数据），强制执行 smoke test，"
                "使用固定 seed=42，生成 pilot/pilot_plan.yaml、pilot/pilot_results.json 和 "
                "pilot/motivation_validation.md（必须包含 PASS/REVISE/FAIL 判定）。"
                ),
            )
        else:
            if ctx.extra.get("resume_mode"):
                return prepend_resume_prefix(
                    ctx,
                    (
                    "请继续 legacy T7 Full 实验任务。\n"
                    "优先复用 experiments/ 下已有代码、运行目录和中间结果，只补剩余的 summary / ablation / log 产物，"
                    "不要从头重建整个实验目录。"
                    ),
                )
            return prepend_resume_prefix(
                ctx,
                (
                "请按 system prompt 执行 legacy T7 Full 实验任务。\n"
                "实验计划在 ideation/exp_plan.yaml 中。\n"
                "注意：默认主链现在使用外部实验 handoff/ingest/audit/claims；本模式仅用于显式 legacy 调试。"
                "如果 pilot 或 novelty_final 产物不存在，"
                "请基于 hypotheses、exp_plan、ideation/novelty_audit.md、synthesis.md 和 comparison_table.csv "
                "建立完整实验计划，并在 iteration_log.md 中明确记录 direct_full_from_t45。\n"
                "请执行完整实验，包含至少 3 条 ablation 实验，"
                "使用 seed ensemble（headline: 3 seeds, final_method: 2 seeds），"
                "生成 experiments/results_summary.json、experiments/ablations.csv 和 "
                "experiments/iteration_log.md。"
                ),
            )

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

        if mode == "handoff":
            return _validate_external_handoff(ws)
        if mode == "dry_run":
            return _validate_external_dry_run(ws)
        if mode == "result_ingest":
            return _validate_external_ingest(ws)
        if mode == "integrity_audit":
            return _validate_external_integrity_audit(ws)
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

            # 4. 固定 seed 检查（§3.3 - 可复现性）
            # 为什么需要：pilot 实验必须可复现，便于调试和验证
            # 检查逻辑：pilot_results.json 中的 seed 必须为 42
            # 失败影响：无法保证 pilot 实验的可复现性
            try:
                results = json.loads((ws / "pilot" / "pilot_results.json").read_text(encoding="utf-8"))
            except Exception as e:
                return False, f"pilot/pilot_results.json 解析失败: {e}"

            seed_ok, seed_err = self._validate_pilot_seed(results)
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
            required_files = [
                "experiments/results_summary.json",
                "experiments/iteration_log.md",
                "experiments/ablations.csv",
                "experiments/docker_digests.txt",
            ]
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
                return False, "T7 要求至少一次成功的 docker_exec 执行，不能只用 bash_run 本地运行"

            # 2. Ablation 最少 3 条（§5.3 - 鲁棒性要求）
            # 为什么需要：ablation 实验是验证方法有效性的关键，至少需要 3 条才能充分验证
            # 检查逻辑：ablations.csv 至少包含 3 条记录（不含表头）
            # 失败影响：无法充分验证方法的有效性，论文可能被拒
            ablations_path = ws / "experiments" / "ablations.csv"
            try:
                ablation_lines = ablations_path.read_text(encoding="utf-8").strip().split("\n")
                ablation_count = len(ablation_lines) - 1  # 减去表头
                if ablation_count < 3:
                    return False, f"experiments/ablations.csv 必须至少 3 条，当前 {ablation_count} 条（§5.3）"
            except Exception as e:
                return False, f"experiments/ablations.csv 读取失败: {e}"

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

            # 4. Seed ensemble 检查（§3.3 - 鲁棒性要求）
            # 为什么需要：多 seed 平均可以减少随机性影响，提高结果可靠性
            # 检查逻辑：headline 实验至少 3 个 seed，final_method 实验至少 2 个 seed
            # 失败影响：结果可能受随机性影响，不够可靠
            def _seed_count(exp: dict) -> int:
                """兼容两种结果格式：`seed_runs`（新）与 `seeds`（旧/简化）。"""

                seed_runs = exp.get("seed_runs")
                if isinstance(seed_runs, list) and seed_runs:
                    return len(seed_runs)
                seeds = exp.get("seeds")
                if isinstance(seeds, list):
                    return len(seeds)
                return 0

            # 检查 headline 实验（必须 3 个 seed）
            headline_exps = [e for e in experiments if e.get("tier") == "headline"]
            for exp in headline_exps:
                seed_count = _seed_count(exp)
                if seed_count < 3:
                    return False, (
                        f"headline 实验 {exp.get('experiment_id', 'unknown')} 必须至少 3 个 seed，"
                        f"当前 {seed_count} 个（§3.3）"
                    )

            # 检查 final_method 实验（必须 2 个 seed）
            final_exps = [e for e in experiments if e.get("tier") == "final_method"]
            for exp in final_exps:
                seed_count = _seed_count(exp)
                if seed_count < 2:
                    return False, (
                        f"final_method 实验 {exp.get('experiment_id', 'unknown')} 必须至少 2 个 seed，"
                        f"当前 {seed_count} 个（§3.3）"
                    )

            # 5. 迭代多样性检查（§5.1 - 鲁棒性要求）
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

            # 7. Seed ensemble summary 检查
            # 为什么需要：汇总多 seed 实验的统计信息，便于分析
            # 检查逻辑：必须存在 seed_ensemble_summary.json 文件
            # 失败影响：缺少多 seed 实验的统计分析
            ensemble_path = ws / "experiments" / "seed_ensemble_summary.json"
            if not ensemble_path.exists():
                return False, "缺少 experiments/seed_ensemble_summary.json（§3.3）"

            # 8. Iteration log 长度检查
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
                    {"path": "ablations.csv", "type": "csv"},
                    {"path": "seed_ensemble_summary.json", "type": "json"},
                    {"path": "iteration_diversity_check.md", "type": "markdown"},
                    {"path": "docker_digests.txt", "type": "text"},
                ],
                inputs=[
                    {"path": "ideation/hypotheses.md", "required": True},
                    {"path": "ideation/exp_plan.yaml", "required": True},
                    {"path": "pilot/pilot_results.json", "required": True},
                ],
            )

            return True, None

    @staticmethod
    def _validate_pilot_seed(results: dict) -> tuple[bool, str | None]:
        """兼容顶层 seed 和逐实验 seed，但必须全为 42。"""

        top_seed = results.get("seed")
        if top_seed is not None and top_seed != 42:
            return False, "pilot 模式必须使用固定 seed=42（顶层 seed 不是 42）"

        experiments = results.get("experiments", [])
        if isinstance(experiments, list) and experiments:
            missing_or_wrong = [
                exp.get("experiment_id", f"#{idx + 1}")
                for idx, exp in enumerate(experiments)
                if exp.get("seed") != 42
            ]
            if missing_or_wrong:
                return False, (
                    "pilot 模式每个实验都必须记录 seed=42，异常实验: "
                    + ", ".join(str(item) for item in missing_or_wrong[:5])
                )
            return True, None

        if top_seed == 42:
            return True, None
        return False, "pilot_results.json 必须包含顶层 seed=42 或每个实验的 seed=42"

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
