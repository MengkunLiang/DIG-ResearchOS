from __future__ import annotations

"""Conservative identity helpers for literature records and T3 notes.

The same paper can appear as a title, DOI, arXiv id, OpenAlex id, URL, or a
filesystem-safe stem. These helpers intentionally generate several full-record
keys, then compare records by set intersection. They do not perform substring
matching or domain-knowledge classification.
"""

from pathlib import Path
import re
import hashlib
from typing import Any


GUIDE_OR_TEMPLATE_NAMES = {
    "readme.md",
    "_dir_guide.md",
    "dir_guide.md",
    "guide.md",
    "template.md",
}


def is_workspace_guide_or_template(path: Path) -> bool:
    """Return true for files created as workspace guides or examples.

    ResearchOS workspaces intentionally contain `_DIR_GUIDE.md`, README files,
    `.example` templates, and empty placeholder seed files. Those files are
    useful to humans but must not count as seed material, notes, or validated
    research artifacts.
    """

    name = path.name.strip()
    lower_name = name.casefold()
    if not name:
        return True
    if lower_name in GUIDE_OR_TEMPLATE_NAMES:
        return True
    if name.startswith("_"):
        return True
    if lower_name.endswith(".example"):
        return True
    if lower_name.endswith(".example.md") or lower_name.endswith(".template.md"):
        return True
    return False


def is_placeholder_text(text: str) -> bool:
    """Return true for default empty/placeholder markdown or jsonl content."""

    stripped_lines = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        stripped_lines.append(clean)
    if not stripped_lines:
        return True
    body = "\n".join(stripped_lines).strip()
    return body.casefold() in {"（暂无）", "(暂无)", "暂无", "none", "n/a", "null"}


def is_paper_note_file(path: Path) -> bool:
    """Return true for real T3 paper note files, not directory guides/docs."""

    if not path.is_file() or path.suffix.lower() != ".md":
        return False
    if is_workspace_guide_or_template(path):
        return False
    return True


def normalize_identity_key(value: str) -> str:
    """Case-fold and collapse whitespace while preserving meaningful symbols."""

    if not value:
        return ""
    return " ".join(str(value).casefold().split())


def normalize_loose_identity_key(value: str) -> str:
    """Drop punctuation differences for title/file-stem comparisons."""

    if not value:
        return ""
    text = str(value).casefold()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def canonical_note_id(raw: Any) -> str:
    """Return the filesystem-safe canonical ID used by T3 note artifacts.

    ResearchOS keeps scholarly identifiers in their original form in paper
    records, e.g. ``noopenalex::...``, ``arxiv:...`` or DOI strings. T3 note
    files need a stable safe stem. All T3 note filename normalization should
    flow through this helper instead of ad hoc ``replace`` chains.
    """

    text = str(raw or "").strip()
    if not text:
        return ""
    text = text.replace("::", "__")
    text = text.replace(":", "_").replace("/", "_").replace("\\", "_")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._-")


def record_note_id(record: dict[str, Any]) -> str:
    """Return the preferred T3 note stem for a paper/queue record."""

    for key in ("normalized_id", "paper_id", "canonical_id", "id", "doi"):
        value = canonical_note_id(record.get(key))
        if value:
            return value
    title = str(record.get("title") or "").strip()
    if title:
        digest = hashlib.sha1(title.casefold().encode("utf-8")).hexdigest()[:16]
        return f"noopenalex__{digest}"
    return ""


def normalize_openalex_work_id(value: Any) -> str:
    """Return a compact OpenAlex work id (W...) when one is present."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("https://openalex.org/") or text.startswith("https://api.openalex.org/works/"):
        text = text.rstrip("/").split("/")[-1]
    if re.fullmatch(r"W\d+", text):
        return text
    return ""


def stable_noopenalex_id(record: dict[str, Any]) -> str:
    """Build a stable non-OpenAlex fallback id without using the raw title as an id."""

    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    parts = [
        str(record.get("doi") or external_ids.get("DOI") or "").strip().casefold(),
        str(record.get("title") or "").strip().casefold(),
        " ".join(str(item).strip().casefold() for item in record.get("authors") or [] if str(item).strip())[:240],
        str(record.get("year") or "").strip(),
    ]
    digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"noopenalex::{digest}"


def add_identity_key_variants(keys: set[str], value: Any) -> None:
    raw = str(value or "").strip()
    if not raw:
        return

    candidates = {
        raw,
        canonical_note_id(raw),
        raw.replace(":", "_").replace("/", "_"),
        raw.replace("_", " "),
        raw.replace("_", ":"),
        raw.replace("_", "/"),
        raw.replace("-", " "),
    }
    lowered = raw.lower()
    if lowered.startswith("arxiv_"):
        candidates.add("arxiv:" + raw[len("arxiv_"):])
        candidates.add(raw[len("arxiv_"):])
    if lowered.startswith("arxiv:"):
        arxiv_id = raw.split(":", 1)[1].strip()
        candidates.add("arxiv_" + arxiv_id)
        candidates.add(arxiv_id)
    if lowered.startswith("https://doi.org/") or lowered.startswith("http://doi.org/"):
        candidates.add(raw.split("doi.org/", 1)[1])
    if lowered.startswith("doi:"):
        candidates.add(raw.split(":", 1)[1])

    for token in extract_identifier_tokens(raw):
        candidates.add(token)

    for candidate in candidates:
        strict = normalize_identity_key(candidate)
        loose = normalize_loose_identity_key(candidate)
        if strict:
            keys.add(strict)
        if loose:
            keys.add(loose)


def extract_identifier_tokens(value: str) -> list[str]:
    text = str(value or "")
    tokens: list[str] = []
    for token in re.findall(
        r"(?:arxiv:\s*)?\d{4}\.\d{4,5}(?:v\d+)?|10\.\d{4,9}/[^\s,;)\]]+",
        text,
        flags=re.IGNORECASE,
    ):
        cleaned = token.replace(" ", "").rstrip(".,;")
        if not cleaned.lower().startswith("arxiv:") and re.fullmatch(
            r"\d{4}\.\d{4,5}(?:v\d+)?",
            cleaned,
        ):
            cleaned = f"arxiv:{cleaned}"
        tokens.append(cleaned)
    return tokens


def paper_record_match_keys(record: dict[str, Any]) -> set[str]:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    candidates = [
        record.get("normalized_id"),
        record.get("paper_id"),
        record.get("id"),
        record.get("paperId"),
        record.get("canonical_id"),
        record.get("openalex_id"),
        record.get("title"),
        record.get("doi"),
        record.get("url"),
        external_ids.get("ArXiv"),
        external_ids.get("DOI"),
        external_ids.get("OpenAlex"),
        external_ids.get("CorpusId"),
    ]
    keys: set[str] = set()
    for candidate in candidates:
        add_identity_key_variants(keys, candidate)
    return {key for key in keys if key}


def pdf_stem_match_score(record: dict[str, Any], stem: str) -> float:
    """Score whether a messy PDF filename likely matches a paper record.

    This is intentionally identity matching, not scholarly relevance. It
    tolerates author/year prefixes, Chinese separators such as "等", punctuation
    differences, and truncated filenames by using title token overlap.
    """

    title = str(record.get("title") or "").strip()
    if not title or not stem:
        return 0.0
    title_key = normalize_loose_identity_key(title)
    stem_key = normalize_loose_identity_key(stem)
    if not title_key or not stem_key:
        return 0.0
    if title_key == stem_key:
        return 1.0
    if title_key in stem_key or stem_key in title_key:
        shorter = min(len(title_key), len(stem_key))
        longer = max(len(title_key), len(stem_key))
        return max(0.82, shorter / max(1, longer))

    title_tokens = _meaningful_title_tokens(title_key)
    stem_tokens = _meaningful_title_tokens(stem_key)
    if not title_tokens or not stem_tokens:
        return 0.0
    overlap = title_tokens & stem_tokens
    recall = len(overlap) / max(1, len(title_tokens))
    precision = len(overlap) / max(1, len(stem_tokens))
    if len(overlap) >= 4 and recall >= 0.58:
        return max(recall, (2 * recall * precision) / max(0.001, recall + precision))
    return 0.0


def find_matching_seed_pdf(
    record: dict[str, Any],
    seed_pdf_dir: Path,
    *,
    threshold: float = 0.58,
) -> Path | None:
    """Find the best matching user seed PDF for a paper record."""

    if not seed_pdf_dir.exists() or not seed_pdf_dir.is_dir():
        return None
    best_path: Path | None = None
    best_score = 0.0
    for path in sorted(seed_pdf_dir.glob("*.pdf")):
        score = pdf_stem_match_score(record, path.stem)
        if score > best_score:
            best_score = score
            best_path = path
    if best_path is not None and best_score >= threshold:
        return best_path
    return None


def paper_note_match_keys(note_path: Path) -> set[str]:
    keys: set[str] = set()
    add_identity_key_variants(keys, note_path.stem)
    try:
        content = note_path.read_text(encoding="utf-8")
    except OSError:
        return keys

    for line in content.splitlines()[:120]:
        stripped = line.strip()
        if stripped.startswith("# "):
            add_identity_key_variants(keys, stripped.lstrip("#").strip())
            continue
        match = re.match(
            r"-\s+\*\*(ID|Normalized ID|DOI/arXiv|Title)\*\*:\s*(.+)$",
            stripped,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.match(r"(?:Paper\s+)?Title\s*:\s*(.+)$", stripped, flags=re.IGNORECASE)
            if match:
                add_identity_key_variants(keys, match.group(1).strip())
            continue
        value = match.group(2).strip()
        add_identity_key_variants(keys, value)
    return {key for key in keys if key}


def record_is_covered(record: dict[str, Any], completed_keys: set[str]) -> bool:
    record_keys = paper_record_match_keys(record)
    if record_keys & completed_keys:
        return True

    # Conservative title-overlap fallback for duplicate seed aliases. Different
    # noopenalex fallback IDs may be generated for the same paper when one
    # source lacks DOI/authors. Do not force a second deep read if the note title
    # and record title are clearly the same paper, including truncated titles.
    title_key = normalize_loose_identity_key(str(record.get("title") or ""))
    if not title_key:
        return False
    title_tokens = _meaningful_title_tokens(title_key)
    if len(title_tokens) < 4:
        return False
    for completed_key in completed_keys:
        candidate = normalize_loose_identity_key(completed_key)
        if not candidate or len(candidate) < 24:
            continue
        if title_key == candidate:
            return True
        if title_key in candidate or candidate in title_key:
            shorter = min(len(title_key), len(candidate))
            longer = max(len(title_key), len(candidate))
            if shorter / max(1, longer) >= 0.55:
                return True
        candidate_tokens = _meaningful_title_tokens(candidate)
        if len(candidate_tokens) < 4:
            continue
        overlap = title_tokens & candidate_tokens
        recall = len(overlap) / max(1, len(title_tokens))
        precision = len(overlap) / max(1, len(candidate_tokens))
        if len(overlap) >= 5 and (recall >= 0.82 or precision >= 0.82):
            return True
    return False


def display_record_key(record: dict[str, Any]) -> str:
    value = (
        record.get("normalized_id")
        or record.get("paper_id")
        or record.get("id")
        or record.get("title")
        or "unknown"
    )
    return normalize_identity_key(str(value))


def _meaningful_title_tokens(value: str) -> set[str]:
    stop = {
        "a",
        "an",
        "and",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "towards",
        "toward",
        "via",
        "with",
        "等",
    }
    tokens = set()
    for token in value.split():
        if token in stop:
            continue
        if re.fullmatch(r"(19|20)\d{2}", token):
            continue
        if len(token) <= 1:
            continue
        tokens.add(token)
    return tokens
