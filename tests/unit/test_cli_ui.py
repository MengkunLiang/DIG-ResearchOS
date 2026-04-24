from __future__ import annotations

from io import StringIO
from pathlib import Path

from researchos.runtime.cli_ui import format_startup_summary, render_final_banner, show_startup_banner


class _FakeStream(StringIO):
    def isatty(self) -> bool:
        return False


def test_render_final_banner_contains_dig_and_command():
    banner = render_final_banner("run-task")

    assert "DIG Lab" in banner
    assert "ResearchOS" in banner
    assert "command=run-task" in banner


def test_show_startup_banner_falls_back_to_static_output_for_non_tty():
    stream = _FakeStream()

    show_startup_banner("run", stream=stream, sleep_seconds=0)

    output = stream.getvalue()
    assert "DIG Lab" in output
    assert "command=run" in output


def test_show_startup_banner_respects_runtime_default_no_banner():
    stream = _FakeStream()

    show_startup_banner("run", stream=stream, default_no_banner=True, sleep_seconds=0)

    assert stream.getvalue() == ""


def test_format_startup_summary_renders_paths():
    summary = format_startup_summary(
        workspace_dir=Path("/tmp/workspace"),
        state_machine=Path("/tmp/fsm.yaml"),
        gates=Path("/tmp/gates.yaml"),
        model_routing=Path("/tmp/model_routing.yaml"),
        skill_roots=[Path("/tmp/skills")],
        mcp_server_count=2,
        mcp_tool_count=5,
    )

    assert "workspace=/tmp/workspace" in summary
    assert "mcp_servers=2 mcp_tools=5" in summary
