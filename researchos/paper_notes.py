"""Typed views over structured Markdown paper-reading notes.

The detailed Markdown note remains the evidence record.  These views are for
compact researcher-facing presentation and deliberately do not invent missing
scientific content or promote abstract-only material.
"""

from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field


class CompactPaperNoteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    title: str
    evidence_status: str
    problem: str = ""
    mechanism: str = ""
    finding: str = ""
    scientific_implication: str = ""
    engineering_implication: str = ""
    practical_implication: str = ""
    implication_provenance: dict[str, str] = Field(default_factory=dict)


_SECTION_RE = re.compile(r"(?ms)^##\s+(?P<title>.+?)\s*$\n?(?P<body>.*?)(?=^##\s+|\Z)")


def compact_paper_note_view(note_path: Path, *, workspace_dir: Path | None = None) -> CompactPaperNoteView:
    """Create a compact view without flattening the detailed evidence schema."""

    path = Path(note_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    raw_sections = {match.group("title").strip(): match.group("body") for match in _SECTION_RE.finditer(text)}
    sections = {title: " ".join(body.split()) for title, body in raw_sections.items()}
    implication = raw_sections.get("20. Implications & Field-level Provenance", "")
    relative = path.name
    if workspace_dir is not None:
        try:
            relative = path.relative_to(workspace_dir).as_posix()
        except ValueError:
            pass
    return CompactPaperNoteView(
        source_path=relative,
        title=_title(text, fallback=path.stem),
        evidence_status=_markdown_field(text, "Status"),
        problem=sections.get("1. Problem & Motivation", ""),
        mechanism=_markdown_field(raw_sections.get("13. Mechanism Claim", ""), "Stated mechanism"),
        finding=sections.get("3. Key Results", "") or sections.get("3. Key Claimed Results", ""),
        scientific_implication=_markdown_field(implication, "Scientific implication"),
        engineering_implication=_markdown_field(implication, "Engineering / deployment implication"),
        practical_implication=_markdown_field(implication, "Practical / managerial / business implication"),
        implication_provenance={
            "scientific": _markdown_field(implication, "Scientific basis"),
            "engineering": _markdown_field(implication, "Engineering basis"),
            "practical": _markdown_field(implication, "Practical basis"),
        },
    )


def _title(text: str, *, fallback: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    return match.group(1).strip() if match else fallback


def _markdown_field(text: str, label: str) -> str:
    match = re.search(rf"(?m)^- \*\*{re.escape(label)}\*\*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""
