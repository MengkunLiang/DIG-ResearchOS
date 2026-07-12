"""Research-facing process observability for CLI runs.

The package is intentionally read-only with respect to research artifacts.  It
observes declared inputs, actual tool access, durable outputs, and existing
structured artifacts without changing research decisions or schemas.
"""

from .events import EventStore, ObservabilityEvent
from .reporter import StageReporter

__all__ = ["EventStore", "ObservabilityEvent", "StageReporter"]
