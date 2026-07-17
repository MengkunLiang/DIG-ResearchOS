#!/usr/bin/env python3
"""Audit ResearchOS documentation against local files and the current CLI.

The checker is intentionally read-only. It validates Markdown links, local paths,
anchors, documented ResearchOS commands/options/task IDs, terminology, and likely
fixed-width prose wrapping. The prose audit covers the published documentation,
Skills, Jinja prompts, root READMEs, and Markdown examples. It reports findings as
text or JSON and writes a report only when the caller explicitly passes ``--report``.

Examples:

    python scripts/check_docs.py
    python scripts/check_docs.py --strict --report tmp/debug/08_documentation_audit/docs_quality.json
    python scripts/check_docs.py --format json
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+['\"][^)]*['\"])?\)")
HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(?P<heading>.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*```(?P<language>[A-Za-z0-9_+-]*)\s*$")
TASK_TOKEN_RE = re.compile(r"^(?:T\d+(?:\.\d+)?(?:-[A-Z0-9-]+)?|LEGACY-[A-Z0-9-]+)$")
MARKDOWN_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
MARKDOWN_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
JINJA_CONTROL_RE = re.compile(r"^\s*(?:\{%|{{|{#|%}|}}|#})")
LATEX_DISPLAY_RE = re.compile(r"^\s*(?:\$\$|\\\[|\\\]|\\begin\{|\\end\{)")
SHELL_TRANSCRIPT_RE = re.compile(r"^\s*(?:[$#>]\s|\([^)]*\)\s+[^\s]+@[^:]+:|(?:python|conda|researchos|git|rg|pytest)\s)")
SENTENCE_TERMINATOR_RE = re.compile(r"[.!?。！？;；:：]\s*(?:<!--.*-->)?$")
WORD_ONLY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+/#.-]*$")
PROSE_MIN_WRAP_WIDTH = 54

# These phrases are documented as undesirable in the Goal.  They are warnings
# rather than failures because a historical example or a prose explanation may need
# to quote one of them while explaining why it is obsolete.
TERMINOLOGY_WARNINGS: dict[str, str] = {
    "基于分类法的调查": "改用“基于分类框架组织的领域综述”或按语境写“文献综述”。",
    "候选人池": "改用“Candidate Population”或“候选池”。",
    "研究调查生成": "改用“研究方向生成”或说明具体任务。",
    "恢复点指针": "改用“已保存的恢复状态”并说明 Resume 行为。",
    "智能化赋能": "删除空泛宣传词，改写为实际机制或结果。",
    "一站式": "删除宣传式概述，写清具体输入、产物和边界。",
    "全链路": "删除宣传式概述，写清具体阶段或责任边界。",
    "多维度协同": "删除空泛概述，说明具体模块和交互关系。",
}

LEGACY_PATH_TOKENS = (
    "literature/paper_notes",
    "literature/paper_notes_abstract",
    "literature/abstract_notes",
    "literature/paper_notes_bridge",
    "cross_domain_catelog",
    "cross_domain_catelogs",
)
LEGACY_CONTEXT_RE = re.compile(r"(?i)\b(?:legacy|deprecated|migration|migrate|old|compatibility|typo|inventory)\b|旧|迁移|兼容|已废弃|拼写|清单")
PROHIBITED_PHRASE_CONTEXT_RE = re.compile(r"不要使用|不得使用|禁止|不写|避免|改用|删除|弃用|废弃|banned|avoid|do not use", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    line: int
    message: str


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _github_anchor(value: str) -> str:
    """Approximate GitHub's heading slug rules for local Markdown anchors."""

    text = re.sub(r"<[^>]+>", "", value or "")
    text = re.sub(r"[`*_~]", "", text).strip().casefold()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _anchors(text: str) -> set[str]:
    counts: Counter[str] = Counter()
    anchors: set[str] = set()
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = _github_anchor(match.group("heading"))
        if not base:
            continue
        suffix = counts[base]
        counts[base] += 1
        anchors.add(base if suffix == 0 else f"{base}-{suffix}")
    return anchors


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _iter_markdown_files(docs_root: Path) -> Iterable[Path]:
    yield from sorted(path for path in docs_root.rglob("*.md") if path.is_file())


def _iter_prose_audit_files(repo_root: Path, docs_root: Path) -> Iterable[Path]:
    """Yield every prose-bearing source covered by the hard-wrap audit.

    The Quality Gate treats Skills and Jinja prompts as researcher-facing
    guidance, rather than limiting the review to ``docs/``. Source code and
    generated workspaces are intentionally excluded: their line layout has a
    different contract and must not make a prose report noisy.
    """

    candidates: set[Path] = set(_iter_markdown_files(docs_root))
    for relative_root, suffixes in (
        ("skills", {".md"}),
        ("researchos/agent_guidance", {".md"}),
        ("researchos/prompts", {".j2"}),
        ("examples", {".md"}),
    ):
        root = repo_root / relative_root
        if root.is_dir():
            candidates.update(path for path in root.rglob("*") if path.is_file() and path.suffix in suffixes)
    candidates.update(path for path in repo_root.glob("README*.md") if path.is_file())
    yield from sorted(candidates)


def _prose_scope(path: Path, repo_root: Path) -> str:
    relative = _repo_relative(path, repo_root)
    if relative.startswith("researchos/prompts/"):
        return "prompt"
    if relative.startswith(("skills/", "researchos/agent_guidance/")):
        return "skill"
    if relative.startswith("examples/"):
        return "example"
    return "documentation"


def _is_ascii_art_line(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 8:
        return False
    drawing = sum(character in "┌┐└┘├┤┬┴┼─│═║╭╮╰╯━┃" for character in stripped)
    return drawing >= max(5, len(stripped) // 3)


def _is_prose_structure(value: str) -> bool:
    """Return whether a source line has layout semantics that must be kept."""

    stripped = value.strip()
    if not stripped:
        return True
    return bool(
        HEADING_RE.match(value)
        or MARKDOWN_TABLE_RE.match(value)
        or stripped.startswith((">", "<!--", "<details", "</details", "---", "..."))
        or JINJA_CONTROL_RE.match(value)
        or LATEX_DISPLAY_RE.match(value)
        or SHELL_TRANSCRIPT_RE.match(value)
        or _is_ascii_art_line(value)
        or value.rstrip().endswith("\\")
    )


def _iter_plain_prose_lines(text: str) -> Iterator[tuple[int, str, str]]:
    """Yield source lines which can be assessed as ordinary prose.

    Markdown list markers remain part of the yielded line. This lets the audit
    identify an accidental physical continuation of a list item while still
    excluding the list's own semantic boundary from any automatic treatment.
    """

    lines = text.splitlines()
    in_fence = False
    in_front_matter = len(lines) > 0 and lines[0].strip() == "---"
    in_latex_display = False
    for index, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if in_front_matter:
            if index > 1 and stripped in {"---", "..."}:
                in_front_matter = False
            continue
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped in {"$$", "\\[", "\\]"}:
            in_latex_display = not in_latex_display
            continue
        if in_latex_display or _is_prose_structure(raw):
            continue
        content = MARKDOWN_LIST_RE.sub("", raw, count=1).strip()
        if not content:
            continue
        yield index, raw, content


def _prose_wrap_findings(path: Path, text: str, repo_root: Path) -> list[Finding]:
    """Report likely fixed-width prose wrapping without rewriting the file.

    The signal is deliberately conservative: a non-terminal line needs to be
    at least ``PROSE_MIN_WRAP_WIDTH`` characters and the following physical
    line must be ordinary prose. A separate narrow rule catches isolated
    English term splits such as ``AI`` followed by ``Agent``.
    """

    findings: list[Finding] = []
    prose = list(_iter_plain_prose_lines(text))
    scope = _prose_scope(path, repo_root)
    for (line, raw, content), (next_line, next_raw, next_content) in zip(prose, prose[1:]):
        # A blank line or a Markdown structure between the records means they
        # were not consecutive in source and therefore not a wrapped sentence.
        if next_line != line + 1:
            continue
        if MARKDOWN_LIST_RE.match(next_raw):
            continue
        # A long Markdown list item is a semantic unit, not a fixed-width
        # paragraph. Only assess it when the next physical line is an indented
        # continuation of the *same* list item. A new marker or non-indented
        # line begins a distinct item and must remain untouched.
        if MARKDOWN_LIST_RE.match(raw):
            current_indent = len(raw) - len(raw.lstrip())
            following_indent = len(next_raw) - len(next_raw.lstrip())
            if MARKDOWN_LIST_RE.match(next_raw) or following_indent <= current_indent:
                continue
        if WORD_ONLY_RE.fullmatch(content) and re.match(r"^[A-Za-z][A-Za-z0-9+/#.-]*(?:\s|$)", next_content):
            findings.append(
                Finding(
                    "WARN",
                    "split_english_term",
                    _repo_relative(path, repo_root),
                    line,
                    f"疑似把英文术语拆成两行：{content!r} / {next_content.split(maxsplit=1)[0]!r}。",
                )
            )
            continue
        if len(content) < PROSE_MIN_WRAP_WIDTH or SENTENCE_TERMINATOR_RE.search(content):
            continue
        if not re.match(r"^[\"'“‘(\[]*[A-Za-z0-9\u4e00-\u9fff]", next_content):
            continue
        kind = "hard_wrapped_prompt_prose" if scope == "prompt" else "hard_wrapped_prose"
        findings.append(
            Finding(
                "WARN",
                kind,
                _repo_relative(path, repo_root),
                line,
                "疑似固定列宽续行；普通段落应由 Markdown/终端软换行，而不是在源文件中按宽度折行。",
            )
        )
    return findings


def _link_findings(path: Path, text: str, repo_root: Path, anchor_cache: dict[Path, set[str]]) -> list[Finding]:
    findings: list[Finding] = []
    for match in MARKDOWN_LINK_RE.finditer(text):
        raw = match.group("target").strip().strip("<>")
        line = _line_number(text, match.start())
        if not raw or raw.startswith(("https://", "http://", "mailto:", "tel:")):
            continue
        destination, separator, anchor = raw.partition("#")
        if any(token in destination for token in ("{", "}", "$")):
            findings.append(Finding("WARN", "templated_link", _repo_relative(path, repo_root), line, f"无法静态核验模板化链接: {raw}"))
            continue
        target = path if not destination else (path.parent / unquote(destination)).resolve()
        if not target.exists():
            findings.append(Finding("ERROR", "missing_link_target", _repo_relative(path, repo_root), line, f"链接目标不存在: {raw}"))
            continue
        if separator and anchor:
            if not target.is_file() or target.suffix.casefold() != ".md":
                findings.append(Finding("ERROR", "invalid_link_anchor", _repo_relative(path, repo_root), line, f"锚点只能指向 Markdown 文件: {raw}"))
                continue
            anchors = anchor_cache.get(target)
            if anchors is None:
                anchors = _anchors(target.read_text(encoding="utf-8", errors="replace"))
                anchor_cache[target] = anchors
            if _github_anchor(unquote(anchor)) not in anchors:
                findings.append(Finding("ERROR", "missing_link_anchor", _repo_relative(path, repo_root), line, f"链接锚点不存在: {raw}"))
    return findings


def _shell_blocks(text: str) -> Iterable[tuple[int, str]]:
    """Yield logical commands from every fenced block that contains one.

    Documentation often omits a fence language for terminal examples.  The
    caller recognises only explicit ResearchOS CLI prefixes, so accepting all
    fenced blocks here broadens coverage without treating prose or JSON as a
    command.
    """

    active = False
    lines: list[tuple[int, str]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(raw)
        if match:
            if active:
                yield from _logical_shell_lines(lines)
                active = False
                lines = []
            else:
                active = True
            continue
        if active:
            lines.append((line_number, raw))


def _logical_shell_lines(lines: list[tuple[int, str]]) -> Iterable[tuple[int, str]]:
    start_line: int | None = None
    parts: list[str] = []
    for line_number, raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if start_line is None:
            start_line = line_number
        continued = stripped.endswith("\\")
        parts.append(stripped[:-1].rstrip() if continued else stripped)
        if continued:
            continue
        yield start_line, " ".join(parts)
        start_line = None
        parts = []
    if parts and start_line is not None:
        yield start_line, " ".join(parts)


def _cli_parser_data() -> tuple[set[str], dict[str, set[str]], set[str]]:
    from researchos.cli import build_parser
    from researchos.orchestration.state_machine import StateMachine

    parser = build_parser()
    subparser_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    commands = set(subparser_action.choices)
    options = {
        name: {option for action in command_parser._actions for option in action.option_strings}
        for name, command_parser in subparser_action.choices.items()
    }
    machine = StateMachine(
        REPO_ROOT / "config" / "system_config" / "state_machine.yaml",
        REPO_ROOT / "config" / "system_config" / "gates.yaml",
    )
    task_ids = set(machine.nodes)
    return commands, options, task_ids


def _documented_cli_findings(path: Path, text: str, repo_root: Path, commands: set[str], options: dict[str, set[str]], task_ids: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, command_line in _shell_blocks(text):
        try:
            tokens = shlex.split(command_line, comments=True)
        except ValueError:
            findings.append(Finding("WARN", "unparseable_shell_example", _repo_relative(path, repo_root), line_number, f"无法解析 shell 示例: {command_line}"))
            continue
        prefix_index = -1
        if "researchos" in tokens:
            candidate_index = tokens.index("researchos")
            if candidate_index + 1 < len(tokens) and tokens[candidate_index + 1] in commands:
                prefix_index = candidate_index + 1
        if prefix_index < 0:
            for index in range(len(tokens) - 2):
                if tokens[index:index + 3] == ["python", "-m", "researchos.cli"]:
                    prefix_index = index + 3
                    break
        if prefix_index < 0 or prefix_index >= len(tokens):
            continue
        command = tokens[prefix_index]
        if command not in commands:
            findings.append(Finding("ERROR", "unknown_cli_command", _repo_relative(path, repo_root), line_number, f"CLI 子命令不存在: {command}"))
            continue
        for token in tokens[prefix_index + 1:]:
            if not token.startswith("--") or token == "--":
                continue
            option = token.split("=", 1)[0]
            if option not in options[command]:
                findings.append(Finding("ERROR", "unknown_cli_option", _repo_relative(path, repo_root), line_number, f"`{command}` 不支持参数 {option}"))
        if command == "run-task":
            positional = next((token for token in tokens[prefix_index + 1:] if not token.startswith("-") and not token.startswith("<")), "")
            _check_task_token(findings, path, repo_root, line_number, positional, task_ids)
        for index, token in enumerate(tokens):
            option = token.split("=", 1)[0]
            if option not in {"--start-task", "--from-task"}:
                continue
            value = token.split("=", 1)[1] if "=" in token else (tokens[index + 1] if index + 1 < len(tokens) else "")
            _check_task_token(findings, path, repo_root, line_number, value, task_ids)
    return findings


def _check_task_token(findings: list[Finding], path: Path, repo_root: Path, line: int, value: str, task_ids: set[str]) -> None:
    from researchos.orchestration.task_aliases import resolve_public_stage_alias

    token = str(value).strip()
    if not token or token.startswith(("<", "[", "$")) or not TASK_TOKEN_RE.match(token):
        return
    if resolve_public_stage_alias(token) not in task_ids:
        findings.append(Finding("ERROR", "unknown_task_id", _repo_relative(path, repo_root), line, f"状态机不存在 task: {token}"))


def _terminology_findings(path: Path, text: str, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for phrase, guidance in TERMINOLOGY_WARNINGS.items():
        for match in re.finditer(re.escape(phrase), text):
            line = _line_number(text, match.start())
            source_line = text.splitlines()[line - 1]
            if PROHIBITED_PHRASE_CONTEXT_RE.search(source_line):
                continue
            findings.append(Finding("WARN", "terminology", _repo_relative(path, repo_root), line, f"发现“{phrase}”。{guidance}"))
    source_lines = text.splitlines()
    for token in LEGACY_PATH_TOKENS:
        for match in re.finditer(re.escape(token), text, flags=re.IGNORECASE):
            line = _line_number(text, match.start())
            # A long ``rg`` inventory pattern can span several continuation
            # lines.  Keep enough local context to recognize that this is an
            # audit/search example, not a live path recommendation.
            start = max(0, line - 9)
            end = min(len(source_lines), line + 2)
            local_context = "\n".join(source_lines[start:end])
            if source_lines[line - 1].lstrip().startswith("rg ") or LEGACY_CONTEXT_RE.search(local_context):
                continue
            findings.append(Finding("WARN", "legacy_path_without_context", _repo_relative(path, repo_root), line, f"旧路径或拼写变体“{token}”缺少 legacy/migration 语境。"))
    return findings


def audit_docs(repo_root: Path, docs_root: Path, *, include_prose_audit: bool = True) -> list[Finding]:
    repo_root = repo_root.resolve()
    docs_root = docs_root.resolve()
    findings: list[Finding] = []
    if not docs_root.is_dir():
        return [Finding("ERROR", "missing_docs_root", _repo_relative(docs_root, repo_root), 0, "docs 根目录不存在。")]
    required = docs_root / "STYLE_AND_TERMINOLOGY_GUIDE.md"
    if not required.is_file():
        findings.append(Finding("ERROR", "missing_terminology_guide", _repo_relative(required, repo_root), 0, "缺少 Goal 要求的 STYLE_AND_TERMINOLOGY_GUIDE.md。"))

    commands, options, task_ids = _cli_parser_data()
    anchor_cache: dict[Path, set[str]] = {}
    for path in _iter_markdown_files(docs_root):
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(_link_findings(path, text, repo_root, anchor_cache))
        findings.extend(_documented_cli_findings(path, text, repo_root, commands, options, task_ids))
        findings.extend(_terminology_findings(path, text, repo_root))
    if include_prose_audit:
        for path in _iter_prose_audit_files(repo_root, docs_root):
            text = path.read_text(encoding="utf-8", errors="replace")
            findings.extend(_prose_wrap_findings(path, text, repo_root))
    return sorted(findings, key=lambda item: (item.severity != "ERROR", item.path, item.line, item.code))


def _render_text(findings: list[Finding]) -> str:
    if not findings:
        return "Documentation quality gate: PASS (no findings)."
    lines = ["Documentation quality gate:"]
    for item in findings:
        location = f"{item.path}:{item.line}" if item.line else item.path
        lines.append(f"- [{item.severity}] {item.code} · {location} · {item.message}")
    counts = Counter(item.severity for item in findings)
    lines.append(f"Summary: errors={counts['ERROR']}, warnings={counts['WARN']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit local ResearchOS researcher-facing prose without modifying it.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root; default is inferred from this script.")
    parser.add_argument("--docs-root", default="docs", help="Documentation directory relative to --repo-root.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--report", help="Optional JSON report path. No report is written unless this is supplied.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for terminology and contextual-path warnings too.")
    parser.add_argument(
        "--no-prose-audit",
        action="store_true",
        help="Skip the syntax-aware hard-wrap audit for docs, Skills, prompts, READMEs, and examples.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    docs_root = Path(args.docs_root)
    if not docs_root.is_absolute():
        docs_root = repo_root / docs_root
    findings = audit_docs(repo_root, docs_root, include_prose_audit=not args.no_prose_audit)
    counts = Counter(item.severity for item in findings)
    payload = {
        "semantics": "researchos_documentation_quality_audit",
        "repo_root": str(repo_root),
        "docs_root": str(docs_root.resolve()),
        "prose_audit_enabled": not args.no_prose_audit,
        "prose_audit_scopes": ["docs", "skills", "researchos/agent_guidance", "researchos/prompts", "README*.md", "examples"],
        "summary": {"errors": counts["ERROR"], "warnings": counts["WARN"], "finding_count": len(findings)},
        "findings": [asdict(item) for item in findings],
    }
    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = repo_root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_text(findings))
    return 1 if counts["ERROR"] or (args.strict and counts["WARN"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
