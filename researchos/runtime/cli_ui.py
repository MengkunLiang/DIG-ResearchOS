from __future__ import annotations

"""Small, terminal-safe brand and startup helpers for the ResearchOS CLI."""

from pathlib import Path
import io
import shutil
import sys
import time
from typing import TextIO

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text


_BRAND_WIDTH = 84
_DIG_FRAME_MARKS = ("D", "DI", "DIG")


def _brand_mark(mark: str) -> Text:
    """Return the compact DIG mark with stable, restrained lab colors."""

    colors = ("bright_cyan", "spring_green2", "bright_magenta")
    text = Text()
    for index, character in enumerate(mark[:3]):
        text.append(character, style=f"bold {colors[index]}")
        if index < len(mark[:3]) - 1:
            text.append(" ")
    return text


def _banner_renderable(command_name: str, *, width: int, frame_mark: str = "DIG") -> Panel:
    """Build a compact product identity rather than an ASCII-art splash screen."""

    headline = _brand_mark(frame_mark)
    headline.append("   ResearchOS", style="bold bright_white")

    subline = Text()
    subline.append("DIG Lab", style="bold bright_cyan")
    subline.append("  /  Digital Intelligence Group", style="dim")
    subline.append("\nResearch intelligence operating system", style="italic dim")

    status = Text()
    status.append("RESEARCH WORKFLOW", style="bold cyan")
    status.append("  |  ", style="dim")
    status.append("command: ", style="dim")
    status.append(command_name, style="bold yellow")

    body = Group(
        Align.center(headline),
        Text(""),
        Align.center(subline),
        Text(""),
        Align.center(status),
    )
    return Panel(
        body,
        title="[bold bright_cyan]DIG LAB[/]",
        subtitle="[dim]ResearchOS[/]",
        box=box.ROUNDED,
        border_style="bright_cyan",
        padding=(1, 3),
        width=width,
    )


def _render_banner(command_name: str, *, color: bool, frame_mark: str = "DIG") -> str:
    """Render a startup frame into a string so all output paths share one design."""

    buffer = io.StringIO()
    terminal_width = shutil.get_terminal_size(fallback=(_BRAND_WIDTH, 40)).columns
    width = max(64, min(_BRAND_WIDTH, terminal_width))
    console = Console(
        file=buffer,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
        width=width,
        highlight=False,
        _environ={"COLUMNS": str(width), "LINES": "40"},
    )
    console.print(Align.center(_banner_renderable(command_name, width=width, frame_mark=frame_mark)))
    return buffer.getvalue().rstrip()


def render_final_banner(command_name: str, *, color: bool = False) -> str:
    """Render the final DIG Lab / ResearchOS brand panel.

    ``color=False`` deliberately produces portable output for redirected logs,
    CI, and shell pipes. Interactive calls opt in to Rich ANSI styling.
    """

    return _render_banner(command_name, color=color)


def show_startup_banner(
    command_name: str,
    *,
    stream: TextIO | None = None,
    no_banner: bool = False,
    default_no_banner: bool = False,
    no_color: bool = False,
    sleep_seconds: float = 0.055,
) -> None:
    """Show a brief DIG-to-ResearchOS introduction without log noise.

    - ``--no-banner`` and runtime policy suppress all output.
    - Non-TTY and ``--no-color`` modes print one portable static panel.
    - TTY mode uses exactly three compact marks (D -> DI -> DIG), then leaves
      the final ResearchOS panel in the scrollback. It never clears the screen.
    """

    if no_banner or default_no_banner:
        return

    target = stream or sys.stdout
    is_tty = hasattr(target, "isatty") and target.isatty()
    use_color = bool(is_tty and not no_color)
    if not is_tty or no_color:
        target.write(render_final_banner(command_name, color=False) + "\n")
        target.flush()
        return

    previous_lines = 0
    for mark in _DIG_FRAME_MARKS:
        frame = _render_banner(command_name, color=use_color, frame_mark=mark)
        if previous_lines:
            target.write(f"\x1b[{previous_lines}F")
        target.write(frame + "\n")
        target.flush()
        previous_lines = frame.count("\n") + 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)


def format_startup_summary(
    *,
    workspace_dir: Path | None,
    state_machine: Path | None = None,
    gates: Path | None = None,
    model_routing: Path | None = None,
    skill_roots: list[Path] | None = None,
    skill_count: int | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
) -> str:
    """Generate the machine-oriented startup summary that follows the brand panel."""

    lines: list[str] = []
    if workspace_dir is not None:
        lines.append(f"[startup] workspace={workspace_dir}")
    if state_machine is not None:
        lines.append(f"[startup] state_machine={state_machine}")
    if gates is not None:
        lines.append(f"[startup] gates={gates}")
    if model_routing is not None:
        lines.append(f"[startup] model_routing={model_routing}")
    if skill_roots:
        existing = [item for item in skill_roots if item.exists()]
        missing = [item for item in skill_roots if not item.exists()]
        discovered = "unknown" if skill_count is None else str(skill_count)
        lines.append(
            "[startup] skills="
            f"discovered={discovered} roots={len(skill_roots)} existing={len(existing)} missing={len(missing)}"
        )
        if existing:
            lines.append("[startup] skill_roots_existing=" + ", ".join(str(item) for item in existing))
        if missing:
            lines.append("[startup] skill_roots_missing=" + ", ".join(str(item) for item in missing))
    lines.append(f"[startup] mcp_servers={mcp_server_count} mcp_tools={mcp_tool_count}")
    return "\n".join(lines)
