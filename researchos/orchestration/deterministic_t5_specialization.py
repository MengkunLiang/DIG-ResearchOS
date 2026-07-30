"""Deterministic publication boundary for the T5 executor Skill Suite.

The project-specific executor Skills are a source-bound projection of an
already accepted T5 handoff and repository-owned templates.  They are not a
research-writing decision.  Keeping this step behind an ``AgentRunner`` made
the runtime construct an LLM binding and prompt before it ran the same local
compiler, which could make a healthy handoff appear blocked by an unrelated
provider/configuration failure.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from ..runtime.agent import AgentResult, ExecutionContext
from ..skills.project_specialization.compiler import specialize_project_skills
from ..skills.project_specialization.task_adapter import (
    _repo_root_from_ctx,
    validate_project_skill_specialization_outputs,
    write_deterministic_project_skill_specialization_execution,
)


def _compact_specialization_errors(items: Iterable[Any], *, limit: int = 5) -> str:
    """Render compiler diagnostics without exposing an unbounded report dump."""

    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            text = str(item).strip()
        else:
            text = ": ".join(
                str(item.get(key) or "").strip()
                for key in ("code", "path", "message")
                if str(item.get(key) or "").strip()
            )
        if text:
            parts.append(text)
        if len(parts) >= limit:
            break
    return "; ".join(parts) or "unknown project Skill specialization failure"


async def run_deterministic_t5_specialization(ctx: ExecutionContext) -> AgentResult:
    """Build or reuse the executor Skill Suite without an LLM client.

    The compiler itself remains strict: it checks the T5 handoff, source
    context, template markers, schema, atomic publication, and the final
    workspace outputs.  A failure is returned as a durable recovery payload;
    it never falls back to asking a model to repeat local shell diagnostics.
    """

    started = time.monotonic()
    repo_root = _repo_root_from_ctx(ctx)
    existing = validate_project_skill_specialization_outputs(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
    )
    reused = existing.ok
    build_errors: list[dict[str, Any]] = []
    if not reused:
        build = specialize_project_skills(
            workspace=ctx.workspace_dir,
            repo_root=repo_root,
            dry_run=False,
            validate_only=False,
        )
        build_errors = [item for item in build.errors if isinstance(item, dict)]

    validation = validate_project_skill_specialization_outputs(
        workspace=ctx.workspace_dir,
        repo_root=repo_root,
    )
    execution_record: dict[str, Any] = {}
    execution_error = ""
    try:
        execution_record = write_deterministic_project_skill_specialization_execution(
            workspace=ctx.workspace_dir,
            repo_root=repo_root,
        )
    except Exception as exc:  # pragma: no cover - defensive durable diagnosis
        execution_error = str(exc) or type(exc).__name__

    duration = time.monotonic() - started
    produced = {
        name: path
        for name, path in ctx.outputs_expected.items()
        if path.exists()
    }
    validation_errors = [item for item in validation.errors if isinstance(item, dict)]
    execution_status = str(execution_record.get("status") or "")
    if build_errors or not validation.ok or execution_error or execution_status not in {"ready", "incomplete"}:
        detail = _compact_specialization_errors([*build_errors, *validation_errors])
        if execution_error:
            detail = "; ".join(part for part in (detail, f"execution record: {execution_error}") if part)
        if execution_status and execution_status not in {"ready", "incomplete"}:
            detail = "; ".join(part for part in (detail, f"execution record status: {execution_status}") if part)
        error = (
            "T5 项目专属 Skill Suite 未能通过确定性发布/校验；未调用模型进行无依据的 shell 诊断。"
            f"具体原因：{detail[:1200]}。"
            "请查看 external_executor/report/skill_specialization_report.json 和 "
            "external_executor/report/skill_specialization_execution.json；修复其明确指出的上游输入、schema 或模板问题后 resume。"
        )
        return AgentResult(
            ok=False,
            message=error,
            outputs_produced=produced,
            steps_used=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            duration_seconds=duration,
            stop_reason=AgentResult.STOP_INTERRUPTED,
            error=error,
            metadata={
                "completion_mode": "deterministic_t5_specialization_failed",
                "llm_called": False,
                "project_skill_specialization_validation": validation.to_record(),
                "runtime_recovery": {
                    "schema_version": "1.0.0",
                    "kind": "artifact_validation",
                    "task_id": ctx.task_id,
                    "run_id": ctx.run_id,
                    "error_summary": " ".join(error.split())[:1200],
                    "details": {
                        "source": "deterministic_t5_project_skill_specialization",
                        "report": "external_executor/report/skill_specialization_report.json",
                        "execution": "external_executor/report/skill_specialization_execution.json",
                    },
                },
            },
        )

    required_uncertain = list(validation.required_uncertain_fields)
    status_note = (
        "所有执行设置已明确。"
        if validation.report_status == "ready"
        else f"已保留 {len(required_uncertain)} 项待确认字段；它们会在 T5 协议确认中显示，且不会授权正式实验。"
    )
    return AgentResult(
        ok=True,
        message="项目专属 executor Skill Suite 已确定性发布并校验。" + status_note,
        outputs_produced=produced,
        steps_used=0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        duration_seconds=duration,
        stop_reason=AgentResult.STOP_FINISHED,
        metadata={
            "completion_mode": "deterministic_t5_specialization",
            "llm_called": False,
            "project_skill_specialization_reused": reused,
            "project_skill_specialization_validation": validation.to_record(),
            "required_uncertain_count": len(required_uncertain),
        },
    )
