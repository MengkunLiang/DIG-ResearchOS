#!/usr/bin/env python3
"""Normalize only high-confidence fixed-width prose wrapping.

This is deliberately separate from ``check_docs.py`` because the quality gate is
read-only. The normalizer is syntax-aware enough to preserve Markdown headings,
tables, code fences, YAML front matter, LaTeX display blocks, block quotes, ASCII
art, shell transcripts, and distinct list items. It only joins an ordinary prose
line to its immediate physical continuation when the same high-confidence signal
used by the documentation audit is present.

Run without ``--write`` first. A normalisation report should be retained under
``tmp/debug/08_documentation_audit/`` when used for a repository-wide cleanup.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from check_docs import (
    MARKDOWN_LIST_RE,
    PROSE_MIN_WRAP_WIDTH,
    SENTENCE_TERMINATOR_RE,
    WORD_ONLY_RE,
    _iter_plain_prose_lines,
    _iter_prose_audit_files,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _should_join(current: str, following: str, current_raw: str, following_raw: str) -> bool:
    """Return whether two adjacent ordinary-prose lines are one paragraph."""

    if MARKDOWN_LIST_RE.match(following_raw):
        return False
    if WORD_ONLY_RE.fullmatch(current) and WORD_ONLY_RE.match(following):
        return True
    if len(current) < PROSE_MIN_WRAP_WIDTH or SENTENCE_TERMINATOR_RE.search(current):
        return False
    return bool(following and following[0] in '"\'“‘([' or following[0].isalnum() or "\u4e00" <= following[0] <= "\u9fff")


def normalize_text(text: str) -> tuple[str, int]:
    """Join high-confidence physical prose wraps and return the change count."""

    lines = text.splitlines(keepends=True)
    records = {line: (raw, content) for line, raw, content in _iter_plain_prose_lines(text)}
    normalized: list[str] = []
    index = 1
    changes = 0
    while index <= len(lines):
        raw = lines[index - 1]
        record = records.get(index)
        if record is None:
            normalized.append(raw)
            index += 1
            continue
        current_raw, current = record
        # A paragraph can contain more than two fixed-width source lines. Keep
        # joining while the next line has the same ordinary-prose classification.
        while index + 1 in records:
            following_raw, following = records[index + 1]
            if not _should_join(current, following, current_raw, following_raw):
                break
            current_raw = current_raw.rstrip("\r\n").rstrip() + " " + following.strip()
            current = MARKDOWN_LIST_RE.sub("", current_raw, count=1).strip()
            index += 1
            changes += 1
        newline = "\n" if raw.endswith("\n") else ""
        normalized.append(current_raw.rstrip("\r\n") + newline)
        index += 1
    return "".join(normalized), changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize only fixed-width prose wrapping in researcher-facing text.")
    parser.add_argument("paths", nargs="*", help="Optional files or directories. Defaults to the Quality Gate prose scope.")
    parser.add_argument("--write", action="store_true", help="Apply changes. Without this flag the command is a dry run.")
    args = parser.parse_args(argv)

    if args.paths:
        selected: set[Path] = set()
        for raw in args.paths:
            path = Path(raw).resolve()
            if path.is_file():
                selected.add(path)
            elif path.is_dir():
                selected.update(item for item in path.rglob("*") if item.is_file() and item.suffix in {".md", ".j2"})
        files = sorted(selected)
    else:
        files = list(_iter_prose_audit_files(REPO_ROOT, REPO_ROOT / "docs"))

    changed_files = 0
    joined_lines = 0
    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        normalized, changes = normalize_text(source)
        if not changes:
            continue
        changed_files += 1
        joined_lines += changes
        print(f"{path.relative_to(REPO_ROOT)}: {changes} joined prose continuations")
        if args.write:
            path.write_text(normalized, encoding="utf-8")
    mode = "applied" if args.write else "would apply"
    print(f"{mode}: {joined_lines} joins across {changed_files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
