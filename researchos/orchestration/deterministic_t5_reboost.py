"""Deterministic T4.5-to-T5 reboost execution boundary.

T5 reboost compiles an executor handoff from already accepted T4.5 artifacts.
It is deliberately not an LLM writing turn: allowing a model to recreate the
same research contract made valid source packages depend on a second,
schema-fragile handoff draft and obscured the real upstream diagnostic.
"""

from __future__ import annotations

import time

from ..runtime.agent import AgentResult, ExecutionContext
from ..tools.external_experiment import CompileResearchReboostHandoffTool
from ..tools.workspace_policy import WorkspaceAccessPolicy


async def run_deterministic_t5_reboost(ctx: ExecutionContext) -> AgentResult:
    """Compile the authoritative T5 handoff without constructing an LLM client.

    The compiler retains all source/schema/semantic validation. On failure the
    result is a recoverable interruption, so the state machine presents the
    exact deterministic receipt instead of treating a compiler diagnostic as a
    provider outage or advancing with a partial handoff.
    """

    started = time.monotonic()
    policy = WorkspaceAccessPolicy(
        workspace_dir=ctx.workspace_dir,
        allowed_read_prefixes=["external_executor/"],
        allowed_write_prefixes=["external_executor/"],
        task_id=ctx.task_id,
    )
    tool_result = await CompileResearchReboostHandoffTool(policy).execute()
    duration = time.monotonic() - started
    produced = {
        name: path
        for name, path in ctx.outputs_expected.items()
        if path.exists()
    }
    if tool_result.ok:
        return AgentResult(
            ok=True,
            message="Research Reboost completed deterministic T4.5-to-T5 handoff compilation and validation.",
            outputs_produced=produced,
            steps_used=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            duration_seconds=duration,
            stop_reason=AgentResult.STOP_FINISHED,
            metadata={
                "completion_mode": "deterministic_t5_reboost",
                "llm_called": False,
                "compiler_receipt": dict(tool_result.data or {}),
            },
        )
    error = tool_result.content or tool_result.error or "deterministic T5 reboost compilation failed"
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
            "completion_mode": "deterministic_t5_reboost_failed",
            "llm_called": False,
            "compiler_receipt": dict(tool_result.data or {}),
            "runtime_recovery": {
                "schema_version": "1.0.0",
                "kind": "artifact_validation",
                "task_id": ctx.task_id,
                "run_id": ctx.run_id,
                "error_summary": " ".join(error.split())[:1200],
                "details": {
                    "source": "deterministic_t5_reboost_compiler",
                    "tool_error": tool_result.error or "",
                    "validation_report": str((tool_result.data or {}).get("report") or ""),
                },
            },
        },
    )
