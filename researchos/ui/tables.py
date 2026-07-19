"""Shared Rich tables for concise, scan-friendly operational output."""

from __future__ import annotations

from typing import Any

from rich.box import Box
from rich.table import Table


# Rich's presets either leave a blank row between entries or add vertical and
# outer borders. Operational lists need neither: the heavy header separates
# labels from content, while a thin rule makes wrapped rows easy to follow.
LIGHTWEIGHT_RULED_BOX = Box(
    "    \n"
    "    \n"
    " ━━ \n"
    "    \n"
    " ── \n"
    " ── \n"
    "    \n"
    "    \n"
)


def lightweight_ruled_table(
    *,
    title: str | None = None,
    header_style: str = "bold cyan",
    border_style: str | None = None,
    expand: bool = True,
    **kwargs: Any,
) -> Table:
    """Create an operational table without a dense cell grid.

    Use this for decision guides, artifact inventories, and short workflow
    status lists. Research comparison matrices and Candidate Cards deliberately
    use their stronger local table styles instead.
    """

    return Table(
        title=title,
        box=LIGHTWEIGHT_RULED_BOX,
        show_header=True,
        show_lines=True,
        show_edge=False,
        header_style=header_style,
        border_style=border_style,
        expand=expand,
        **kwargs,
    )
