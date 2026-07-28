"""Bounded semantic adjudication receipts for ambiguous T3.6 survey checks.

T3.6 keeps deterministic release checks for document structure, evidence,
citations, BibTeX, provenance, and PDF compilation.  This module deliberately
addresses only a small class of multilingual prose false positives.  Every
accepted decision is bound to the current source hashes and exact quotations,
so a later source change immediately invalidates the exception.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


T36_SEMANTIC_ADJUDICATION_REL_PATH = "_runtime/t36_semantic_adjudications.json"
T36_SEMANTIC_ADJUDICATION_SEMANTICS = "t36_bounded_semantic_adjudications"

_AUDIT_SEMANTIC_CHECKS = frozenset(
    {
        "survey_language_consistency",
        "compact_theme_content_absorbed",
    }
)


def semantic_adjudication_scope(error: str) -> dict[str, Any] | None:
    """Return a narrow, prose-only scope for one T3.6 validation error.

    This allowlist intentionally excludes missing files, schemas, state
    fingerprints, citation coverage and alignment, bibliography integrity,
    internal-process leakage, graphics, TeX syntax, compiler reports, and PDF
    validation.  An LLM must never be able to waive those contracts.
    """

    message = str(error or "").strip()
    normalized = message.casefold()
    if not message:
        return None

    audit_match = re.fullmatch(
        r"T36 audit semantic check:\s*([A-Za-z0-9_]+)",
        message,
        flags=re.IGNORECASE,
    )
    if audit_match:
        check_name = audit_match.group(1)
        if check_name in _AUDIT_SEMANTIC_CHECKS:
            return {
                "artifact": "drafts/survey/survey.tex",
                "dependency_paths": (
                    "drafts/survey/survey.tex",
                    "drafts/survey/survey_plan.json",
                    "drafts/survey/survey_state.json",
                    "drafts/survey/writing_template.json",
                ),
                "requirement": check_name,
                "audit_check": check_name,
            }

    if "survey_review.md 缺少审阅维度:" in message:
        return {
            "artifact": "drafts/survey/survey_review.md",
            "dependency_paths": (
                "drafts/survey/survey_review.md",
                "drafts/survey/survey_review_actions.json",
                "drafts/survey/survey_audit.json",
            ),
            "requirement": "review_dimension_semantics",
        }

    section_match = re.search(r"survey section ([a-z0-9_\-]+) 语言不一致", message, flags=re.IGNORECASE)
    if section_match:
        section_id = section_match.group(1).replace("-", "_")
        return {
            "artifact": f"drafts/survey/sections/{section_id}.tex",
            "dependency_paths": (
                f"drafts/survey/sections/{section_id}.tex",
                "drafts/survey/survey_state.json",
                "drafts/survey/writing_template.json",
            ),
            "requirement": "section_language_semantics",
        }

    # A validator may report the aggregate audit message while the detailed
    # current checks are gathered separately below.  Do not treat the broad
    # string itself as adjudicable: only named allowlisted checks may pass.
    if "survey_audit.json 存在硬失败" in normalized:
        return None
    return None


def collect_t36_semantic_errors(workspace: Path) -> list[str]:
    """Expose every current audit false-positive candidate independently."""

    path = Path(workspace) / "drafts" / "survey" / "survey_audit.json"
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    checks = audit.get("checks") if isinstance(audit, dict) else []
    if not isinstance(checks, list):
        return []
    return [
        f"T36 audit semantic check: {name}"
        for item in checks
        if isinstance(item, dict)
        and item.get("passed") is False
        and (name := str(item.get("name") or "")) in _AUDIT_SEMANTIC_CHECKS
    ]


def semantic_adjudication_fingerprints(
    workspace: Path,
    dependency_paths: tuple[str, ...] | list[str],
) -> dict[str, str]:
    """Fingerprint every source that can change a bounded decision."""

    root = Path(workspace)
    fingerprints: dict[str, str] = {}
    for relative_path in dependency_paths:
        path = root / relative_path
        if not path.is_file():
            fingerprints[relative_path] = "missing"
            continue
        try:
            fingerprints[relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            fingerprints[relative_path] = "unreadable"
    return fingerprints


def load_t36_semantic_adjudications(workspace: Path) -> list[dict[str, Any]]:
    """Load valid-shaped runtime-owned decisions conservatively."""

    path = Path(workspace) / T36_SEMANTIC_ADJUDICATION_REL_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("semantics") != T36_SEMANTIC_ADJUDICATION_SEMANTICS:
        return []
    decisions = payload.get("decisions")
    return [item for item in decisions if isinstance(item, dict)] if isinstance(decisions, list) else []


def accepted_t36_semantic_errors(workspace: Path) -> set[str]:
    """Return only fresh, quote-backed semantic exceptions."""

    accepted: set[str] = set()
    root = Path(workspace)
    for decision in load_t36_semantic_adjudications(root):
        if decision.get("verdict") != "satisfied":
            continue
        error = str(decision.get("validator_error") or "").strip()
        scope = semantic_adjudication_scope(error)
        if not error or scope is None:
            continue
        fingerprints = decision.get("source_fingerprints")
        if not isinstance(fingerprints, dict):
            continue
        if fingerprints != semantic_adjudication_fingerprints(root, tuple(scope["dependency_paths"])):
            continue
        if not _evidence_is_current(root, str(scope["artifact"]), decision.get("evidence")):
            continue
        accepted.add(error)
    return accepted


def accepted_t36_semantic_audit_checks(workspace: Path) -> set[str]:
    """Map fresh decisions back to the exact audit checks they may waive."""

    accepted: set[str] = set()
    for error in accepted_t36_semantic_errors(workspace):
        scope = semantic_adjudication_scope(error)
        if scope and isinstance(scope.get("audit_check"), str):
            accepted.add(str(scope["audit_check"]))
    return accepted


def persist_t36_semantic_adjudication(
    workspace: Path,
    *,
    validator_error: str,
    artifact: str,
    requirement: str,
    evidence: list[dict[str, str]],
    adjudicator_reason: str,
    model: str,
) -> dict[str, Any]:
    """Persist one accepted decision after local quote verification."""

    root = Path(workspace)
    scope = semantic_adjudication_scope(validator_error)
    if scope is None or scope["artifact"] != artifact:
        raise ValueError("semantic adjudication scope does not match validator error")
    if not _evidence_is_current(root, artifact, evidence):
        raise ValueError("semantic adjudication evidence is not present in the current artifact")

    existing = load_t36_semantic_adjudications(root)
    decision = {
        "validator_error": validator_error,
        "artifact": artifact,
        "requirement": requirement,
        "verdict": "satisfied",
        "evidence": evidence,
        "adjudicator_reason": adjudicator_reason[:1200],
        "model": model,
        "source_fingerprints": semantic_adjudication_fingerprints(root, tuple(scope["dependency_paths"])),
        "adjudicated_at": datetime.now(timezone.utc).isoformat(),
    }
    retained = [
        item
        for item in existing
        if str(item.get("validator_error") or "").strip() != validator_error
    ]
    retained.append(decision)
    path = root / T36_SEMANTIC_ADJUDICATION_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "semantics": T36_SEMANTIC_ADJUDICATION_SEMANTICS,
                "schema_version": "1.0.0",
                "decisions": retained,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return decision


def _evidence_is_current(workspace: Path, artifact: str, evidence: Any) -> bool:
    if not isinstance(evidence, list) or not evidence or len(evidence) > 3:
        return False
    try:
        text = (Path(workspace) / artifact).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            return False
        quote = str(item.get("quote") or "").strip()
        explanation = str(item.get("explanation") or "").strip()
        if len(quote) < 20 or len(quote) > 900 or len(explanation) < 12 or quote not in text:
            return False
    return True
