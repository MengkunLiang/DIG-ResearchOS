"""One canonical parser for the T4.5 novelty audit's Final Gate Verdict."""

from __future__ import annotations

import re


PASSING_FINAL_GATE_VERDICTS = frozenset(
    {
        "pass",
        "passed",
        "pass_to_experiment",
        "pass_with_required_baselines",
        "continue_to_t5",
        "continue_to_experiment",
    }
)
LEGACY_PASSING_FINAL_GATE_VERDICTS = frozenset({"go_t7", "continue_to_t7"})

_VERDICT_LINE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:\*\*)?\s*Final\s+Gate\s+Verdict\s*(?:\*\*)?\s*[:：]\s*(.+?)\s*$"
)
_VERDICT_HEADING = re.compile(r"(?im)^\s*#+\s*Final\s+Gate\s+Verdict\s*$")
_DECORATION = "`*_~'\"[](){}<>:;,. \t\r\n-"
_TOKEN = re.compile(r"[a-z0-9]+(?:[ _-]+[a-z0-9]+)*")


def extract_final_gate_verdict(text: str) -> str:
    """Return the last declared Final Gate Verdict value, preserving its text."""

    audit_text = str(text or "")
    matches = list(_VERDICT_LINE.finditer(audit_text))
    if matches:
        return matches[-1].group(1).strip()

    heading = _VERDICT_HEADING.search(audit_text)
    if heading:
        for line in audit_text[heading.end() :].splitlines()[:8]:
            candidate = line.strip().strip("*")
            if candidate and not candidate.startswith("#"):
                return candidate
    return ""


def normalize_final_gate_verdict(value: str) -> str:
    """Normalize a declared verdict without treating Markdown decoration as data."""

    candidate = str(value or "").casefold().strip(_DECORATION)
    match = _TOKEN.match(candidate)
    if match is None:
        return ""
    return re.sub(r"[ -]+", "_", match.group(0))


def is_passing_final_gate_verdict(value: str, *, allow_legacy: bool = False) -> bool:
    """Whether one declared verdict authorizes post-audit formalization."""

    passing = PASSING_FINAL_GATE_VERDICTS
    if allow_legacy:
        passing = passing | LEGACY_PASSING_FINAL_GATE_VERDICTS
    return normalize_final_gate_verdict(value) in passing
