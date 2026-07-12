from __future__ import annotations

"""Durable, machine-readable events for the user-facing research timeline."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


EVENT_SCHEMA_VERSION = "researchos_observability_event.v1"


@dataclass(frozen=True)
class ObservabilityEvent:
    """One bounded fact presented to a researcher and retained for recovery."""

    event_type: str
    run_id: str
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: str = EVENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventStore:
    """Append JSONL events without mixing them into debug logs or trace payloads."""

    def __init__(self, workspace: Path, *, runtime_dir_name: str = "_runtime") -> None:
        self.workspace = Path(workspace)
        self.events_dir = self.workspace / runtime_dir_name / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        safe_run_id = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in run_id)
        return self.events_dir / f"{safe_run_id}.jsonl"

    def append(self, event: ObservabilityEvent) -> Path:
        path = self.path_for(event.run_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
        return path

    def recent_for_task(self, *, task_id: str, limit: int = 40) -> list[dict[str, Any]]:
        """Return recent events across prior runs, used only for resume summaries."""

        events: list[dict[str, Any]] = []
        for path in sorted(self.events_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                rows = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for row in reversed(rows):
                try:
                    decoded = json.loads(row)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict) and decoded.get("task_id") == task_id:
                    events.append(decoded)
                    if len(events) >= limit:
                        return events
        return events
