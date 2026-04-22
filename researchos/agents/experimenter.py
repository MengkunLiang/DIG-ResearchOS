"""T5/T7 Experimenter Agent — 实验执行与结果收集（支持 pilot 和 full 模式）

业务需求：
- 支持两种模式：pilot（T5）和 full（T7）
- Pilot 模式：小规模验证实验，强制 smoke test，产出 motivation_validation.md
- Full 模式：完整实验，强制 ablation，支持 seed ensemble 和迭代多样性检查
- 读取 ideation/exp_plan.yaml（T4 的输出）
- 执行实验计划中的每个实验
- 收集实验结果和日志

Pilot 模式（T5）输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置

Pilot 模式（T5）输出：
- pilot/pilot_plan.yaml: 试点实验计划
- pilot/pilot_code/run_pilot.py: 可执行的试点代码（必须支持 --smoke_test 和 --seed 参数）
- pilot/pilot_results.json: 试点结果（必须包含 seed=42）
- pilot/motivation_validation.md: 动机验证报告（必须包含 PASS/REVISE/FAIL 判定）
- pilot/smoke_test_passed.marker: 烟测通过标记（鲁棒性要求 §3.1）
- pilot/docker_digests.txt: Docker 镜像 digest（鲁棒性要求 §8.2）

Full 模式（T7）输入：
- ideation/exp_plan.yaml: 实验计划
- ideation/hypotheses.md: 研究假设
- project.yaml: 项目配置
- pilot/pilot_results.json: 试点结果（可选）
- ideation/novelty_report.md: 新颖性报告（可选）

Full 模式（T7）输出：
- experiments/results_summary.json: 实验结果汇总（必须包含 quality_status 字段）
- experiments/iteration_log.md: 实验迭代日志
- experiments/ablations.csv: Ablation 结果（最少 3 条，鲁棒性要求 §5.3）
- experiments/seed_ensemble_summary.json: Seed ensemble 汇总（鲁棒性要求 §3.3）
- experiments/iteration_diversity_check.md: 迭代多样性检查（鲁棒性要求 §5.1）
- experiments/runs/{run_id}/: 每个实验的详细结果
- experiments/code/run_exp.py: 可执行的实验代码
- experiments/docker_digests.txt: Docker 镜像 digest

契约详见 ResearchOS v4.0 完整实现设计文档 §T5/T6/T7。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog
import yaml

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.prompts import render_prompt
from ..schemas.validator import validate_record
from ._common import (
    generate_findings_summary,
    generate_manifest,
    generate_research_log,
    load_project,
    read_text_file,
    validate_files_exist,
)

logger = structlog.get_logger(__name__)


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


class ExperimenterAgent(Agent):
    """实验执行 Agent。支持 pilot（T5）和 full（T7）两种模式。

    - pilot 模式：小规模验证实验，强制 smoke test，固定 seed=42
    - full 模式：完整实验，强制 ablation（最少 3 条），支持 seed ensemble
    """

    def __init__(self):
        super().__init__(
            AgentSpec(
                name="experimenter",
                model_tier="medium",
                tool_names=[
                    "read_file",
                    "write_file",
                    "write_structured_file",
                    "list_files",
                    "append_file",
                    "bash_run",
                    "docker_exec",
                    "finish_task",
                ],
                # Full 模式的上限（pilot 会在 system_prompt 中说明更严格的限制）
                max_steps=150,
                max_tokens_total=600_000,
                max_wall_seconds=14400,  # 4 小时
                temperature=0.3,
                allowed_read_prefixes=["", "ideation/", "experiments/", "pilot/", "literature/"],
                allowed_write_prefixes=["experiments/", "pilot/"],
                prompt_template="experimenter.j2",
                structured_outputs={
                    "experiments/results_summary.json": "results_summary",
                    "pilot/pilot_results.json": "pilot_results",
                    "pilot/pilot_plan.yaml": "pilot_plan",
                },
            )
        )

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

        # 读取新颖性报告（full 模式可能需要）
        novelty_report = ""
        novelty_report_path = ws / "ideation" / "novelty_report.md"
        if mode == "full" and novelty_report_path.exists():
            novelty_report = read_text_file(novelty_report_path, default="")[:1000]

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
            budget_hint=budget_hint,
            seed_ensemble=seed_ensemble,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息，根据 mode 生成不同的指令。"""
        mode = ctx.mode or "full"

        if mode == "pilot":
            return (
                "请按 system prompt 执行 T5 Pilot 实验任务。\n"
                "实验计划在 ideation/exp_plan.yaml 中。\n"
                "请执行小规模试点实验（5-10% 数据），强制执行 smoke test，"
                "使用固定 seed=42，生成 pilot/pilot_results.json 和 "
                "pilot/motivation_validation.md（必须包含 PASS/REVISE/FAIL 判定）。"
            )
        else:
            return (
                "请按 system prompt 执行 T7 Full 实验任务。\n"
                "实验计划在 ideation/exp_plan.yaml 中。\n"
                "请执行完整实验，包含至少 3 条 ablation 实验，"
                "使用 seed ensemble（headline: 3 seeds, final_method: 2 seeds），"
                "生成 experiments/results_summary.json、experiments/ablations.csv 和 "
                "experiments/iteration_log.md。"
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

            if results.get("seed") != 42:
                return False, "pilot 模式必须使用固定 seed=42（§3.3）"

            # 5. Docker digest 检查（§8.2 - 复现保证）
            # 为什么需要：记录 Docker 镜像的精确版本，确保环境可复现
            # 检查逻辑：必须存在 docker_digests.txt 文件
            # 失败影响：无法保证环境的精确复现
            digest_file = ws / "pilot" / "docker_digests.txt"
            if not digest_file.exists():
                return False, "缺少 pilot/docker_digests.txt（§8.2）"

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

            # 检查 headline 实验（必须 3 个 seed）
            headline_exps = [e for e in experiments if e.get("tier") == "headline"]
            for exp in headline_exps:
                seed_runs = exp.get("seed_runs", [])
                if len(seed_runs) < 3:
                    return False, (
                        f"headline 实验 {exp.get('experiment_id', 'unknown')} 必须至少 3 个 seed，"
                        f"当前 {len(seed_runs)} 个（§3.3）"
                    )

            # 检查 final_method 实验（必须 2 个 seed）
            final_exps = [e for e in experiments if e.get("tier") == "final_method"]
            for exp in final_exps:
                seed_runs = exp.get("seed_runs", [])
                if len(seed_runs) < 2:
                    return False, (
                        f"final_method 实验 {exp.get('experiment_id', 'unknown')} 必须至少 2 个 seed，"
                        f"当前 {len(seed_runs)} 个（§3.3）"
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
