"""Concrete ResearchOS Agent implementations and their registry entrypoint.

Import this package to resolve declared task-agent mappings; individual Agents
remain responsible for prompt construction and deterministic output checks.
"""

from .registry import AGENT_REGISTRY

__all__ = ["AGENT_REGISTRY"]
