from __future__ import annotations

"""CLI 启动界面辅助。

这里集中放与命令行视觉表现相关的逻辑，避免把 `cli.py` 变成一大坨
“参数解析 + 启动检查 + ANSI 控制字符”混杂在一起的代码。
"""

import os
from pathlib import Path
import sys
import time
from typing import TextIO


# 3D 风格 DIG Lab 品牌标识
# 使用 Unicode 块字符和阴影效果创造立体感
_DIG_FRAMES = [
    # Frame 1: 字母 D 主体 + 3D 阴影
    "\n".join(
        [
            "                    ",
            "   ██████████       ",
            "  ████████████      ",
            "  ██          ██     ",
            "  ██           ██    ",
            "  ██           ██    ",
            "  ██           ██    ",
            "  ██          ██     ",
            "  ████████████      ",
            "   ██████████       ",
            "                    ",
        ]
    ),
    # Frame 2: D + 字母 I 主体 + 阴影
    "\n".join(
        [
            "                    ",
            "   ██████████  ██   ",
            "  ████████████ ██   ",
            "  ██          ██    ",
            "  ██           ██  ",
            "  ██           ██  ",
            "  ██           ██  ",
            "  ██          ██    ",
            "  ████████████ ██   ",
            "   ██████████  ██   ",
            "              ██    ",
            "              ██    ",
        ]
    ),
    # Frame 3: DIG 完整 + Lab 文字
    "\n".join(
        [
            "                    ",
            "   ██████████  ██   ",
            "  ████████████ ██   ",
            "  ██          ██ ██ ",
            "  ██           ██ ██",
            "  ██      ███  ██ ██",
            "  ██      █ █  ██ ██",
            "  ██      ███  ██ ██",
            "  ████████████ ██ ██",
            "   ██████████  ██ ██",
            "              ██ ██  ",
            "              ██ ██  ",
        ]
    ),
    # Frame 4: 带底部装饰的最终版本
    "\n".join(
        [
            " ╔═══════════════════════════════════════╗",
            " ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄  ║",
            " ║ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀█  ║",
            " ║ █  ████████████ █ █  ██████████ █ █ █  ║",
            " ║ █ █▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █ █  ║",
            " ║ █ █        ██████ █        ████ █ █  ║",
            " ║ █ █   ███  ██████ █   ███  ████ █ █  ║",
            " ║ █ █   █ █  ██████ █   █ █  ████ █ █  ║",
            " ║ █ █   ███  ██████ █   ███  ████ █ █  ║",
            " ║ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ █▄█  ║",
            " ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▀▀▀  ║",
            " ╚═══════════════════════════════════════╝",
        ]
    ),
]


def render_final_banner(command_name: str) -> str:
    """渲染最终展示的 banner 文本。"""
    lines = [
        " ╔══════════════════════════════════════════════════╗",
        " ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄         ║",
        " ║ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀█         ║",
        " ║ █  ████████████ █ █  ██████████ █ █ █         ║",
        " ║ █ █▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █ █         ║",
        " ║ █ █        ██████ █        ████ █ █         ║",
        " ║ █ █   ███  ██████ █   ███  ████ █ █         ║",
        " ║ █ █   █ █  ██████ █   █ █  ████ █ █         ║",
        " ║ █ █   ███  ██████ █   ███  ████ █ █         ║",
        " ║ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ █▄█         ║",
        " ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▀▀▀         ║",
        " ╚══════════════════════════════════════════════════╝",
        "",
        f"  DIG Lab - ResearchOS  |  command={command_name}",
    ]
    return "\n".join(lines)


def show_startup_banner(
    command_name: str,
    *,
    stream: TextIO | None = None,
    no_banner: bool = False,
    sleep_seconds: float = 0.06,
) -> None:
    """显示启动动画。

    行为约定：
    - 若 `--no-banner` 或环境变量 `RESEARCHOS_NO_BANNER=1`，则完全静默；
    - 若不是 TTY，则退化为只打印最终静态 banner，避免 CI / pipe 中出现一串动画残影；
    - 若是 TTY，则按帧覆盖刷新，形成“逐步堆出 DIG” 的启动效果。
    """

    if no_banner or os.getenv("RESEARCHOS_NO_BANNER") == "1":
        return

    target = stream or sys.stdout
    is_tty = hasattr(target, "isatty") and target.isatty()
    if not is_tty:
        target.write(render_final_banner(command_name) + "\n")
        target.flush()
        return

    # 在交互终端里按帧覆盖刷新。这里不清整屏，只回到 banner 起点重画，
    # 减少对用户已有滚动内容的破坏。
    line_count = _DIG_FRAMES[-1].count("\n") + 2
    for index, frame in enumerate(_DIG_FRAMES):
        if index > 0:
            target.write(f"\x1b[{line_count}F")
        target.write(frame + "\n\n")
        target.flush()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    target.write(f"DIG Lab - ResearchOS  |  command={command_name}\n")
    target.flush()


def format_startup_summary(
    *,
    workspace_dir: Path | None,
    state_machine: Path | None = None,
    gates: Path | None = None,
    model_routing: Path | None = None,
    skill_roots: list[Path] | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
) -> str:
    """生成启动摘要，供 CLI 在 banner 后打印。"""

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
        roots = ", ".join(str(item) for item in skill_roots)
        lines.append(f"[startup] skill_roots={roots}")
    lines.append(
        f"[startup] mcp_servers={mcp_server_count} mcp_tools={mcp_tool_count}"
    )
    return "\n".join(lines)
