from __future__ import annotations

"""Shared abstract normalization helpers.

These helpers only clean repeatable metadata formats. They do not infer
relevance, source quality, evidence level, or scholarly importance.
"""

from html import unescape
import re
from typing import Any


def clean_abstract(value: Any) -> str:
    """Normalize abstracts from APIs such as Crossref, Europe PMC, and S2."""

    text = str(value or "").strip()
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).strip()


def abstract_from_openalex_index(value: Any) -> str:
    """Rebuild OpenAlex abstract_inverted_index into normal text."""

    if not isinstance(value, dict) or not value:
        return ""
    positions: dict[int, str] = {}
    for word, raw_positions in value.items():
        if not isinstance(raw_positions, list):
            continue
        for raw_position in raw_positions:
            try:
                positions[int(raw_position)] = str(word)
            except (TypeError, ValueError):
                continue
    return " ".join(positions[index] for index in sorted(positions)).strip()
