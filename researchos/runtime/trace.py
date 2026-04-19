from __future__ import annotations

"""运行 trace 写入与展示。

与简单的“把对象 dump 成 JSONL”不同，这里明确区分事件类型，
让 trace 同时满足两类需求：
- 机器可解析：便于后续做审计、回归、可视化；
- 人类可阅读：CLI `trace` 命令可以直接输出调试友好的摘要。
"""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

from .message import Message


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceWriter:
    """逐行写入 trace 的 JSONL 事件流。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._seq = 0

    def _next_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        return {
            "seq": self._seq,
            "ts": _now_iso(),
            "type": event_type,
            "payload": payload,
        }

    def _write(self, payload: dict[str, Any]) -> None:
        self._fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._fh.flush()

    def write_run_start(
        self,
        *,
        run_id: str,
        agent_name: str,
        project_id: str,
        task_id: str,
        workspace_dir: Path,
    ) -> None:
        """写入一次 run 的起始事件。"""

        self._write(
            self._next_event(
                "run_start",
                {
                    "run_id": run_id,
                    "agent_name": agent_name,
                    "project_id": project_id,
                    "task_id": task_id,
                    "workspace_dir": str(workspace_dir),
                },
            )
        )

    def write_message(self, message: Message) -> None:
        self._write(self._next_event("message", message.to_trace_dict()))

    def write_llm_response(self, response: Any, assistant_message: Message) -> None:
        payload = {
            "model_used": getattr(response, "model_used", None),
            "endpoint_used": getattr(response, "endpoint_used", None),
            "tokens_in": getattr(response, "tokens_in", 0),
            "tokens_out": getattr(response, "tokens_out", 0),
            "cost_usd": getattr(response, "cost_usd", 0.0),
            "duration_ms": getattr(response, "duration_ms", 0),
            "assistant": assistant_message.to_trace_dict(),
        }
        self._write(self._next_event("llm_response", payload))

    def close(self, result: Any | None = None) -> None:
        if result is not None:
            if is_dataclass(result):
                payload = asdict(result)
            else:
                payload = getattr(result, "__dict__", str(result))
            self._write(self._next_event("run_end", self._json_safe(payload)))
        self._fh.close()

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        return value


def render_trace_for_humans(trace_path: Path) -> str:
    """把 trace JSONL 转成便于阅读的文本。"""

    lines: list[str] = []
    for raw_line in trace_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        event = json.loads(raw_line)
        event_type = event.get("type", "unknown")
        seq = event.get("seq", "?")
        payload = event.get("payload", {})

        if event_type == "run_start":
            lines.append(
                f"[{seq}] RUN START task={payload.get('task_id')} agent={payload.get('agent_name')} run_id={payload.get('run_id')}"
            )
            continue
        if event_type == "message":
            role = payload.get("role", "?")
            step = payload.get("step", "?")
            content = (payload.get("content") or "").strip()
            if len(content) > 120:
                content = content[:117] + "..."
            tool_name = payload.get("name")
            tool_call_id = payload.get("tool_call_id")
            suffix = []
            if tool_name:
                suffix.append(f"name={tool_name}")
            if tool_call_id:
                suffix.append(f"tool_call_id={tool_call_id}")
            suffix_text = " " + " ".join(suffix) if suffix else ""
            lines.append(f"[{seq}] MESSAGE role={role} step={step}{suffix_text} content={content!r}")
            continue
        if event_type == "llm_response":
            lines.append(
                f"[{seq}] LLM model={payload.get('model_used')} endpoint={payload.get('endpoint_used')} "
                f"tokens={payload.get('tokens_in', 0)}/{payload.get('tokens_out', 0)} "
                f"cost=${payload.get('cost_usd', 0.0):.4f}"
            )
            continue
        if event_type == "run_end":
            lines.append(
                f"[{seq}] RUN END stop_reason={payload.get('stop_reason')} "
                f"ok={payload.get('ok')} steps={payload.get('steps_used')}"
            )
            continue
        lines.append(f"[{seq}] {event_type.upper()} {payload}")
    return "\n".join(lines)
