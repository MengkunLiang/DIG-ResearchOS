from __future__ import annotations

"""Observable T4 candidate-generation telemetry.

This tool accepts only bounded public execution facts. It intentionally has no
free-form reasoning field, so the CLI can show useful candidate-level progress
without exposing model deliberation or confusing telemetry with evidence.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import Tool, ToolResult


T4_EXECUTION_EVENTS_PATH = Path("ideation/t4_execution_events.jsonl")
_PHASES = {"context_pack", "pass1_mainline", "pass1_supplement", "pass2_grounding", "scoring", "gate_cards"}
_STATUSES = {"started", "candidate_started", "candidate_completed", "channel_started", "channel_completed", "completed"}
_CHANNELS = {"mechanism_challenge", "reverse_operation", "subgroup_failure", "missing_area_exploration"}
_SCORE_KEYS = {"novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength"}


class LogT4IdeationProgressParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["context_pack", "pass1_mainline", "pass1_supplement", "pass2_grounding", "scoring", "gate_cards"]
    status: Literal["started", "candidate_started", "candidate_completed", "channel_started", "channel_completed", "completed"]
    candidate_id: str | None = Field(None, max_length=24)
    candidate_title: str | None = Field(None, max_length=96)
    channel: str | None = Field(None, max_length=64)
    completed: int | None = Field(None, ge=0, le=100)
    total: int | None = Field(None, ge=1, le=100)
    recommendation: str | None = Field(None, max_length=64)
    score_snapshot: dict[str, float | int] | None = None

class LogT4IdeationProgressTool(Tool):
    """Persist compact, auditable T4 execution milestones for the CLI."""

    name = "log_t4_ideation_progress"
    description = (
        "记录 T4 Pass1/Pass2/评分/候选卡片的可观察进度。只接受阶段、候选、通道、计数、"
        "落盘后的建议或评分快照；不得传入模型推理、隐藏草稿或未验证结论。"
    )
    parameters_schema = LogT4IdeationProgressParams

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = Path(workspace_dir)

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = LogT4IdeationProgressParams(**kwargs)
        event, error = _validated_event(params)
        if error:
            return ToolResult(ok=False, content=error, error="invalid_t4_progress_event")
        if event.get("score_snapshot") and not self._score_snapshot_is_persisted(event):
            return ToolResult(
                ok=False,
                content="score_snapshot requires the same candidate scores to be persisted in ideation/_candidate_directions.json first",
                error="t4_score_not_persisted",
            )
        path = self.workspace_dir / T4_EXECUTION_EVENTS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        event["at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return ToolResult(
            ok=True,
            content=_event_summary(event),
            data={"path": T4_EXECUTION_EVENTS_PATH.as_posix(), "event": event},
        )

    def _score_snapshot_is_persisted(self, event: dict[str, Any]) -> bool:
        candidate_id = str(event.get("candidate_id") or "").strip()
        if not candidate_id:
            return False
        path = self.workspace_dir / "ideation" / "_candidate_directions.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        candidates = payload.get("candidates") if isinstance(payload, dict) else []
        if not isinstance(candidates, list):
            return False
        expected = event.get("score_snapshot") if isinstance(event.get("score_snapshot"), dict) else {}
        for candidate in candidates:
            if not isinstance(candidate, dict) or str(candidate.get("id") or candidate.get("idea_id") or "") != candidate_id:
                continue
            scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
            return all(scores.get(key) == value for key, value in expected.items())
        return False


def _validated_event(params: LogT4IdeationProgressParams) -> tuple[dict[str, Any], str | None]:
    event = params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else params.dict(exclude_none=True)
    phase = str(event["phase"])
    status = str(event["status"])
    if phase not in _PHASES or status not in _STATUSES:
        return {}, "T4 progress phase/status is invalid"
    if status.startswith("candidate_") and not str(event.get("candidate_id") or "").strip():
        return {}, "candidate_started/candidate_completed requires candidate_id"
    channel = str(event.get("channel") or "").strip()
    if phase == "pass1_supplement" and status.startswith("channel_") and channel not in _CHANNELS:
        return {}, "supplement channel event requires a declared channel"
    completed = event.get("completed")
    total = event.get("total")
    if completed is not None and total is not None and int(completed) > int(total):
        return {}, "completed cannot exceed total"
    raw_scores = event.get("score_snapshot")
    if raw_scores is not None:
        if phase not in {"pass2_grounding", "scoring", "gate_cards"}:
            return {}, "score_snapshot is allowed only after grounding/scoring begins"
        cleaned: dict[str, int | float] = {}
        for key, value in raw_scores.items():
            if key not in _SCORE_KEYS or isinstance(value, bool):
                return {}, f"invalid score key: {key}"
            numeric = float(value)
            if numeric < 0 or numeric > 5:
                return {}, f"score {key} must be within 0..5"
            cleaned[key] = int(numeric) if numeric.is_integer() else numeric
        event["score_snapshot"] = cleaned
    for key in ("candidate_id", "candidate_title", "channel", "recommendation"):
        if key in event:
            event[key] = " ".join(str(event[key]).split())
    return event, None


def _event_summary(event: dict[str, Any]) -> str:
    phase = str(event.get("phase") or "T4")
    status = str(event.get("status") or "updated")
    subject = str(event.get("candidate_id") or event.get("channel") or "")
    count = ""
    if event.get("completed") is not None and event.get("total") is not None:
        count = f" {event['completed']}/{event['total']}"
    return f"T4 {phase}{count} {subject} {status}".strip()
