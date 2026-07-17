"""Typed, recoverable errors for T4 role-adapter boundaries."""

from __future__ import annotations


class T4RoleResponseFormatError(ValueError):
    """A model answered, but did not provide one usable JSON object.

    Provider, network, and authentication failures deliberately use their own
    runtime exceptions.  This type identifies only a completed model response
    that needs one schema-directed repair attempt.
    """

    def __init__(self, message: str, *, content: str) -> None:
        super().__init__(message)
        self.response_excerpt = " ".join(str(content or "").split())[:4000]
