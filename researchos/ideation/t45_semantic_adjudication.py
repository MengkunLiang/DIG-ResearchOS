"""Bounded LLM adjudication receipts for ambiguous T4.5 prose checks.

Deterministic validation remains authoritative for structural, evidence, and
execution contracts. This module exists only for the small class of
researcher-facing prose requirements where a literal keyword or bilingual
surface form can under-recognize an otherwise present argument.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


T45_SEMANTIC_ADJUDICATION_REL_PATH = "_runtime/t45_semantic_adjudications.json"
T45_SEMANTIC_ADJUDICATION_SEMANTICS = "t45_bounded_semantic_adjudications"

_PROPOSAL_PATH = "ideation/proposal/research_proposal.md"
_HYPOTHESES_PATH = "ideation/hypotheses.md"
_PROPOSAL_DEPENDENCIES = (
    _PROPOSAL_PATH,
    "ideation/research_blueprint.yaml",
    "ideation/claim_registry.yaml",
    "ideation/exp_plan.yaml",
)
_HYPOTHESES_DEPENDENCIES = (
    _HYPOTHESES_PATH,
    "ideation/claim_registry.yaml",
)


def semantic_adjudication_scope(error: str) -> dict[str, Any] | None:
    """Return the precise prose-only scope eligible for semantic adjudication.

    The allowlist deliberately excludes file/schema integrity, audit authority,
    identity lineage, explicit IDs, experimental mappings, anti-padding checks,
    and audit-language leaks. Those conditions remain deterministic hard gates.
    """

    message = str(error or "").strip()
    normalized = message.casefold()
    if not message:
        return None

    hypothesis_markers = (
        "in hypotheses.md is only a short assertion",
        "in hypotheses.md is missing:",
    )
    if any(marker in normalized for marker in hypothesis_markers):
        return {
            "artifact": _HYPOTHESES_PATH,
            "dependency_paths": _HYPOTHESES_DEPENDENCIES,
            "requirement": "claim_argument_completeness",
        }

    proposal_markers = (
        "proposal uses noncanonical or merged sectioning",
        "proposal has concise labeled sections",
        "proposal does not explain challenge",
        "does not state a readable central insight",
        "states its central insight after the first technical component",
        "does not explain the simpler alternative",
        "research design and evaluation does not include",
        "research design and evaluation does not connect the technical study",
        "expected contributions and implications lacks a concrete technical contribution",
        "expected contributions and implications names no practical actor",
        "practical significance section does not name an affected actor",
        "risks, limitations and execution plan lists risks without",
        "ccf-a proposal lacks a complete computational method or system artifact",
        "utd proposal lacks the mandatory substantive technical artifact",
        "utd proposal reduces its technical artifact to calling an existing llm/api",
        "hybrid proposal has no explicit link",
    )
    if any(marker in normalized for marker in proposal_markers):
        return {
            "artifact": _PROPOSAL_PATH,
            "dependency_paths": _PROPOSAL_DEPENDENCIES,
            "requirement": "proposal_argument_semantics",
        }
    return None


def semantic_adjudication_fingerprints(workspace: Path, dependency_paths: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Fingerprint every source that can change the scoped semantic judgment."""

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


def load_t45_semantic_adjudications(workspace: Path) -> list[dict[str, Any]]:
    """Read valid-shaped, runtime-owned adjudication decisions conservatively."""

    path = Path(workspace) / T45_SEMANTIC_ADJUDICATION_REL_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("semantics") != T45_SEMANTIC_ADJUDICATION_SEMANTICS:
        return []
    decisions = payload.get("decisions")
    return [item for item in decisions if isinstance(item, dict)] if isinstance(decisions, list) else []


def accepted_t45_semantic_errors(workspace: Path) -> set[str]:
    """Return only still-fresh, evidence-backed semantic override errors."""

    accepted: set[str] = set()
    root = Path(workspace)
    for decision in load_t45_semantic_adjudications(root):
        if decision.get("verdict") != "satisfied":
            continue
        error = str(decision.get("validator_error") or "").strip()
        scope = semantic_adjudication_scope(error)
        if not error or scope is None:
            continue
        fingerprints = decision.get("source_fingerprints")
        if not isinstance(fingerprints, dict):
            continue
        expected = semantic_adjudication_fingerprints(root, tuple(scope["dependency_paths"]))
        if fingerprints != expected:
            continue
        evidence = decision.get("evidence")
        if not _evidence_is_current(root, str(scope["artifact"]), evidence):
            continue
        accepted.add(error)
    return accepted


def persist_t45_semantic_adjudication(
    workspace: Path,
    *,
    validator_error: str,
    artifact: str,
    requirement: str,
    evidence: list[dict[str, str]],
    adjudicator_reason: str,
    model: str,
) -> dict[str, Any]:
    """Persist one accepted LLM adjudication only after local verification."""

    root = Path(workspace)
    scope = semantic_adjudication_scope(validator_error)
    if scope is None or scope["artifact"] != artifact:
        raise ValueError("semantic adjudication scope does not match validator error")
    if not _evidence_is_current(root, artifact, evidence):
        raise ValueError("semantic adjudication evidence is not present in the current artifact")

    path = root / T45_SEMANTIC_ADJUDICATION_REL_PATH
    existing = load_t45_semantic_adjudications(root)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "semantics": T45_SEMANTIC_ADJUDICATION_SEMANTICS,
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
    """Require quoted evidence to be verifiably present in the scoped artifact."""

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
