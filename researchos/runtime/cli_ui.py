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
from rich.table import Table
from rich.text import Text


_BRAND_WIDTH = 96
_DIG_FRAME_MARKS = ("D", "DI", "DIG")
_DIG_GLYPHS = {
    "D": (
        "██████╗ ",
        "██╔══██╗",
        "██║  ██║",
        "██║  ██║",
        "██████╔╝",
        "╚═════╝ ",
    ),
    "I": (
        "██╗",
        "██║",
        "██║",
        "██║",
        "██║",
        "╚═╝",
    ),
    "G": (
        " ██████╗ ",
        "██╔════╝ ",
        "██║  ███╗",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ),
}
_DIG_COLORS = {
    "D": ("bright_cyan", "rgb(0, 44, 67)"),
    "I": ("spring_green2", "rgb(0, 54, 38)"),
    "G": ("bright_magenta", "rgb(55, 18, 65)"),
}


def _logo_line(mark: str, line_index: int) -> Text:
    """Render one large, shaded line of the progressive DIG mark."""

    text = Text()
    for index, character in enumerate(mark[:3]):
        foreground, background = _DIG_COLORS[character]
        # The dim leading/trailing block gives the terminal glyph a consistent
        # offset extrusion while the colored face remains readable without it.
        text.append("░", style=f"dim {foreground}")
        text.append(
            _DIG_GLYPHS[character][line_index],
            style=f"bold {foreground} on {background}",
        )
        text.append("▓", style=f"dim {foreground}")
        if index < len(mark[:3]) - 1:
            text.append("  ")
    return text


def _banner_renderable(command_name: str, *, width: int, frame_mark: str = "DIG") -> Panel:
    """Build the large DIG mark and its restrained ResearchOS product context."""

    logo_lines = [Align.center(_logo_line(frame_mark, line)) for line in range(6)]
    logo_shadow = Text("╲" + "▄" * 31 + "╱", style="dim cyan")

    product_name = Text("ResearchOS", style="bold bright_white")
    product_name.append("  ·  Research Workflow Runtime", style="dim")

    subline = Text()
    subline.append("DIG", style="bold bright_cyan")
    subline.append("  ·  BUAA", style="dim")
    subline.append("\nEvidence-bound research workflow", style="italic dim")

    status = Text()
    status.append("RESEARCH WORKFLOW", style="bold cyan")
    status.append("  |  ", style="dim")
    status.append("command: ", style="dim")
    status.append(command_name, style="bold yellow")

    body = Group(
        *logo_lines,
        Align.center(logo_shadow),
        Text(""),
        Align.center(product_name),
        Align.center(subline),
        Text(""),
        Align.center(status),
    )
    return Panel(
        body,
        title="[bold bright_cyan]DIG · BUAA[/]",
        subtitle="[dim]ResearchOS · Research Workflow[/]",
        box=box.ROUNDED,
        border_style="bright_cyan",
        padding=(1, 4),
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
    """Render the final DIG / BUAA ResearchOS brand panel.

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
    model_settings: Path | None = None,
    model_routing: Path | None = None,
    skill_roots: list[Path] | None = None,
    skill_count: int | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
) -> str:
    """Return a compact plain-text fallback for the startup card."""

    missing = [item for item in skill_roots or [] if not item.exists()]
    lines: list[str] = []
    if workspace_dir is not None:
        lines.append(f"项目目录：{workspace_dir}")
    if state_machine is not None:
        lines.append("研究流程：已加载")
    if model_settings or model_routing:
        lines.append("模型设置：已加载")
    if skill_roots:
        count = "已发现" if skill_count is None else f"{skill_count} 个可用"
        suffix = "；当前项目没有额外 Skill" if missing else ""
        lines.append(f"Skill：{count}{suffix}")
    lines.append(
        f"MCP：{mcp_server_count} 个服务，{mcp_tool_count} 个扩展 Tool"
        if mcp_server_count
        else "MCP：未启用额外服务"
    )
    return "\n".join(lines)


def render_startup_summary(
    *,
    workspace_dir: Path | None,
    state_machine: Path | None = None,
    gates: Path | None = None,
    model_settings: Path | None = None,
    model_routing: Path | None = None,
    skill_roots: list[Path] | None = None,
    skill_count: int | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
    verbose: bool = False,
    no_color: bool = False,
) -> str:
    """Render a researcher-facing Rich startup card, with paths only on request."""

    width = max(88, min(144, shutil.get_terminal_size(fallback=(120, 40)).columns))
    configured_model_path = model_settings or model_routing
    existing = [item for item in skill_roots or [] if item.exists()]
    missing = [item for item in skill_roots or [] if not item.exists()]
    facts = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    facts.add_column(style="bold cyan", no_wrap=True)
    facts.add_column(overflow="fold")
    if workspace_dir is not None:
        facts.add_row("项目目录", str(workspace_dir))
    if state_machine is not None:
        facts.add_row("研究流程", "已加载，Gate 已就绪")
    if configured_model_path is not None:
        facts.add_row("模型设置", "已加载")
    if skill_roots:
        count = "已发现" if skill_count is None else f"{skill_count} 个可用"
        suffix = "；当前项目没有额外 Skill" if missing else ""
        facts.add_row("Skill", count + suffix)
    if mcp_server_count:
        facts.add_row("MCP", f"{mcp_server_count} 个服务，{mcp_tool_count} 个扩展 Tool")
    else:
        facts.add_row("MCP", "未启用额外服务")

    body: list[object] = [facts]
    if verbose:
        details = Table(title="配置位置", box=box.SIMPLE_HEAVY, show_header=False, expand=True)
        details.add_column(style="dim", no_wrap=True)
        details.add_column(overflow="fold")
        if state_machine is not None:
            details.add_row("State machine", str(state_machine))
        if gates is not None:
            details.add_row("Gate config", str(gates))
        if configured_model_path is not None:
            details.add_row("Model settings", str(configured_model_path))
        if existing:
            details.add_row("Skill source", ", ".join(str(item) for item in existing))
        if missing:
            details.add_row("Project Skill", "尚未生成：" + ", ".join(str(item) for item in missing))
        body.append(details)
    else:
        body.append(Text("使用 --verbose 查看配置路径和项目专属 Skill 状态。", style="dim"))

    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=not no_color,
        color_system=None if no_color else "truecolor",
        no_color=no_color,
        width=width,
        highlight=False,
        _environ={"COLUMNS": str(width), "LINES": "40"},
    )
    console.print(Panel(Group(*body), title="本次启动", border_style="cyan", expand=True))
    return buffer.getvalue().rstrip()
