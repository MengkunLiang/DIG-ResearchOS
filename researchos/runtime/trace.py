from __future__ import annotations

from pathlib import Path
import json
from typing import Any
from dataclasses import asdict, is_dataclass

from .message import Message


class TraceWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def _write(self, payload: dict[str, Any]) -> None:
        self._fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._fh.flush()

    def write_message(self, message: Message) -> None:
        self._write({"type": "message", "payload": message.to_trace_dict()})

    def write_llm_response(self, response: Any, assistant_message: Message) -> None:
        payload = {
            "type": "llm_response",
            "payload": {
                "model_used": getattr(response, "model_used", None),
                "endpoint_used": getattr(response, "endpoint_used", None),
                "tokens_in": getattr(response, "tokens_in", 0),
                "tokens_out": getattr(response, "tokens_out", 0),
                "cost_usd": getattr(response, "cost_usd", 0.0),
                "duration_ms": getattr(response, "duration_ms", 0),
                "assistant": assistant_message.to_trace_dict(),
            },
        }
        self._write(payload)

    def close(self, result: Any | None = None) -> None:
        if result is not None:
            if is_dataclass(result):
                payload = asdict(result)
            else:
                payload = getattr(result, "__dict__", str(result))
            self._write({"type": "result", "payload": self._json_safe(payload)})
        self._fh.close()

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        return value
