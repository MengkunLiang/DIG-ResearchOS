from __future__ import annotations


CLAUDE_CODE_TOOL_ALIASES: dict[str, str | None] = {
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "write_file",
    "Bash": "bash_run",
    "Glob": "glob_files",
    "Grep": "grep_search",
    "WebFetch": "web_fetch",
    "Task": None,
    "TodoWrite": None,
}


def translate_tool_names(
    claude_tools: list[str],
    *,
    available_tools: set[str],
) -> tuple[list[str], list[str]]:
    translated: list[str] = []
    warnings: list[str] = []
    for tool_name in claude_tools:
        if tool_name in available_tools:
            translated.append(tool_name)
            continue
        if tool_name.startswith("mcp__"):
            normalized = "mcp_" + tool_name[len("mcp__") :].replace("__", "_")
            if normalized in available_tools:
                translated.append(normalized)
            else:
                warnings.append(f"MCP tool not registered: {tool_name}")
            continue

        mapped = CLAUDE_CODE_TOOL_ALIASES.get(tool_name)
        if mapped is None:
            if tool_name in CLAUDE_CODE_TOOL_ALIASES:
                warnings.append(f"Tool '{tool_name}' intentionally not supported, skipped")
            else:
                warnings.append(f"Unknown Claude Code tool '{tool_name}', skipped")
            continue
        if mapped not in available_tools:
            warnings.append(f"Tool '{tool_name}' -> '{mapped}' not registered in runtime")
            continue
        translated.append(mapped)
    return translated, warnings
