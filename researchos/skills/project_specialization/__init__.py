"""Public entrypoints for compiling project-specific executor Skill suites.

Only the specialization service and its typed result are exported so callers
cannot bypass source validation or atomic publication through internals.
"""

from __future__ import annotations

from .compiler import specialize_project_skills
from .types import SpecializationResult

__all__ = ["SpecializationResult", "specialize_project_skills"]
