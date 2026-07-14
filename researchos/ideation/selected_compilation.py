"""Compile a selected Candidate into pre-novelty artifacts without finalizing claims.

The compiler only organizes LLM-authored Candidate fields and durable scores.
It does not invent hypotheses, upgrade evidence, or create a final Experiment
Plan. Formal hypothesis and experiment artifacts remain downstream work.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import yaml

from .state import T4ArtifactStore, stable_fingerprint


def compile_pre_novelty_hypothesis_brief(
    workspace_dir: Path,
    *,
    selection_fingerprint: str,
    selected_candidate_id: str,
) -> dict[str, str]:
    """Persist a selected Candidate's draft bundle for T4.5 novelty review."""

    store = T4ArtifactStore(workspace_dir)
    candidate = _load_candidate(workspace_dir, selected_candidate_id)
    hypotheses = candidate.get("candidate_hypotheses") if isinstance(candidate.get("candidate_hypotheses"), list) else []
    hypotheses = [item for item in hypotheses if isinstance(item, dict)]
    if not 2 <= len(hypotheses) <= 4:
        raise ValueError("Pre-Novelty compilation requires 2-4 LLM-authored draft hypotheses")
    contributions = candidate.get("contributions") if isinstance(candidate.get("contributions"), list) else []
    minimum = candidate.get("minimum_experiment") if isinstance(candidate.get("minimum_experiment"), dict) else {}
    source_paths = _source_paths(candidate)
    selected_payload = {
        "schema_version": "1.0.0",
        "semantics": "t4_selected_research_idea_pre_novelty",
        "selection_fingerprint": selection_fingerprint,
        "candidate_id": selected_candidate_id,
        "candidate": candidate,
        "candidate_fingerprint": stable_fingerprint(candidate),
    }
    store.write_json("ideation/selected/selected_candidate.json", selected_payload)
    brief = {
        "schema_version": "1.0.0",
        "semantics": "t4_pre_novelty_hypothesis_brief",
        "selection_fingerprint": selection_fingerprint,
        "candidate_id": selected_candidate_id,
        "status": "draft_for_novelty_review",
        "core_thesis": str(candidate.get("core_claim") or candidate.get("pitch") or "").strip(),
        "mechanism": str(candidate.get("mechanism") or "").strip(),
        "contributions": contributions,
        "draft_hypotheses": hypotheses,
        "minimum_validation": minimum,
        "evidence_boundary": {
            "basis_summary": str(candidate.get("basis_summary") or "").strip(),
            "basis_sources": candidate.get("basis_sources") if isinstance(candidate.get("basis_sources"), list) else [],
            "supporting_papers": candidate.get("supporting_papers") if isinstance(candidate.get("supporting_papers"), list) else [],
        },
        "formalization_rule": "T4.5 must review novelty before formal hypotheses, contribution-hypothesis mapping, validation map, kill criteria, and experiment plan are finalized.",
    }
    _write_yaml(store.path("ideation/hypothesis_brief.yaml"), brief)
    lineage = {
        "schema_version": "1.0.0",
        "semantics": "t4_pre_novelty_hypothesis_lineage",
        "selection_fingerprint": selection_fingerprint,
        "candidate_id": selected_candidate_id,
        "hypotheses": [
            {
                "hypothesis_id": str(item.get("id") or "").strip(),
                "source_candidate_id": selected_candidate_id,
                "source_file": "ideation/_candidate_directions.json",
                "evidence_status": str(item.get("evidence_status") or "unknown").strip(),
            }
            for item in hypotheses
        ],
    }
    store.write_json("ideation/selected/hypothesis_lineage.json", lineage)
    search_targets = {
        "schema_version": "1.0.0",
        "semantics": "t4_pre_novelty_search_targets",
        "selection_fingerprint": selection_fingerprint,
        "candidate_id": selected_candidate_id,
        "targets": _search_targets(candidate, hypotheses),
        "source_paths": source_paths,
    }
    store.write_json("ideation/selected/t45_search_targets.json", search_targets)
    store.path("ideation/selected/pre_novelty_brief.md").parent.mkdir(parents=True, exist_ok=True)
    store.path("ideation/selected/pre_novelty_brief.md").write_text(
        _render_pre_novelty_brief(candidate, hypotheses, source_paths), encoding="utf-8"
    )
    return {
        "selected_candidate": "ideation/selected/selected_candidate.json",
        "hypothesis_brief": "ideation/hypothesis_brief.yaml",
        "hypothesis_lineage": "ideation/selected/hypothesis_lineage.json",
        "search_targets": "ideation/selected/t45_search_targets.json",
        "brief": "ideation/selected/pre_novelty_brief.md",
    }


def selected_candidate_id_from_gate_input(workspace_dir: Path, captured: dict[str, Any]) -> str | None:
    """Resolve an explicit complete Candidate ID; never infer a merge."""

    try:
        data = json.loads((Path(workspace_dir) / "ideation/_candidate_directions.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    candidates = data.get("candidates") if isinstance(data, dict) else []
    known = [str(item.get("id") or item.get("idea_id") or "").strip() for item in candidates if isinstance(item, dict)]
    known = [item for item in known if item]
    raw = " ".join(str(value) for value in captured.values() if str(value).strip())
    found = [candidate_id for candidate_id in known if re.search(rf"(?<![A-Za-z0-9._:-]){re.escape(candidate_id)}(?![A-Za-z0-9._:-])", raw)]
    return found[0] if len(found) == 1 else None


def ensure_t45_pre_novelty_brief(workspace_dir: Path) -> dict[str, str]:
    """Provide a Pre-Novelty input for legacy workspaces without deleting files.

    Native T4 always produces ``hypothesis_brief.yaml`` at Gate1.  Older
    workspaces may only contain a previously compiled ``hypotheses.md``.  This
    migration copies its declared hypotheses into a clearly marked
    ``legacy_migrated`` brief so T4.5 can use the same input contract.  It does
    not reinterpret, improve, or overwrite the legacy formal artifacts.
    """

    workspace = Path(workspace_dir)
    brief_path = workspace / "ideation" / "hypothesis_brief.yaml"
    if brief_path.exists() and brief_path.stat().st_size > 0:
        return {"hypothesis_brief": "ideation/hypothesis_brief.yaml", "mode": "native_or_existing"}
    hypotheses_path = workspace / "ideation" / "hypotheses.md"
    if not hypotheses_path.exists() or hypotheses_path.stat().st_size <= 0:
        raise ValueError("T4.5 requires ideation/hypothesis_brief.yaml; no legacy hypotheses.md is available for migration")
    text = hypotheses_path.read_text(encoding="utf-8", errors="replace")
    hypotheses = _legacy_hypotheses(text)
    if not hypotheses:
        raise ValueError("legacy hypotheses.md contains no identifiable H1–Hk headings for T4.5 migration")
    store = T4ArtifactStore(workspace)
    legacy_fingerprint = stable_fingerprint({"hypotheses": text})
    brief = {
        "schema_version": "1.0.0",
        "semantics": "t4_pre_novelty_hypothesis_brief",
        "selection_fingerprint": legacy_fingerprint,
        "candidate_id": "legacy_migrated",
        "status": "legacy_migrated_for_novelty_review",
        "core_thesis": "",
        "mechanism": "",
        "contributions": [],
        "draft_hypotheses": hypotheses,
        "minimum_validation": {},
        "evidence_boundary": {"basis_summary": "Migrated from an existing workspace; audit original sources before treating a claim as supported.", "basis_sources": [], "supporting_papers": []},
        "formalization_rule": "This migration preserves prior hypotheses for novelty review. Any new formalization must occur only after T4.5 accepts the audit.",
        "migration_source": "ideation/hypotheses.md",
    }
    _write_yaml(brief_path, brief)
    selected_path = workspace / "ideation" / "selected" / "selected_candidate.json"
    if not selected_path.exists():
        store.write_json(
            "ideation/selected/selected_candidate.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_selected_research_idea_pre_novelty",
                "selection_fingerprint": legacy_fingerprint,
                "candidate_id": "legacy_migrated",
                "candidate": {"source": "ideation/hypotheses.md"},
                "candidate_fingerprint": legacy_fingerprint,
            },
        )
    targets_path = workspace / "ideation" / "selected" / "t45_search_targets.json"
    if not targets_path.exists():
        store.write_json(
            "ideation/selected/t45_search_targets.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_pre_novelty_search_targets",
                "selection_fingerprint": legacy_fingerprint,
                "candidate_id": "legacy_migrated",
                "targets": [{"kind": "legacy_hypothesis", "text": item["statement"]} for item in hypotheses],
                "source_paths": ["ideation/hypotheses.md"],
            },
        )
    return {"hypothesis_brief": "ideation/hypothesis_brief.yaml", "mode": "legacy_migrated"}


def _load_candidate(workspace_dir: Path, candidate_id: str) -> dict[str, Any]:
    path = Path(workspace_dir) / "ideation/_candidate_directions.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read selected Candidate pool: {exc}") from exc
    candidates = data.get("candidates") if isinstance(data, dict) else []
    for candidate in candidates if isinstance(candidates, list) else []:
        if isinstance(candidate, dict) and str(candidate.get("id") or candidate.get("idea_id") or "").strip() == candidate_id:
            return candidate
    raise ValueError(f"selected Candidate {candidate_id} is not present in the current Gate1 pool")


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _source_paths(candidate: dict[str, Any]) -> list[str]:
    paths = ["ideation/_candidate_directions.json"]
    for item in candidate.get("supporting_papers") if isinstance(candidate.get("supporting_papers"), list) else []:
        if isinstance(item, dict) and str(item.get("source_file") or "").strip():
            paths.append(str(item["source_file"]).strip())
    return list(dict.fromkeys(paths))


def _search_targets(candidate: dict[str, Any], hypotheses: list[dict[str, Any]]) -> list[dict[str, str]]:
    targets = [
        {"kind": "core_thesis", "text": str(candidate.get("core_claim") or candidate.get("pitch") or "").strip()},
        {"kind": "mechanism", "text": str(candidate.get("mechanism") or "").strip()},
        {"kind": "problem", "text": str(candidate.get("target_problem") or "").strip()},
    ]
    targets.extend({"kind": "draft_hypothesis", "text": str(item.get("statement") or "").strip()} for item in hypotheses)
    return [item for item in targets if item["text"]]


def _render_pre_novelty_brief(candidate: dict[str, Any], hypotheses: list[dict[str, Any]], source_paths: list[str]) -> str:
    lines = ["# Pre-Novelty Hypothesis Brief", "", f"## Selected Candidate", str(candidate.get("display_title") or candidate.get("title") or ""), "", "## Core Thesis", str(candidate.get("core_claim") or candidate.get("pitch") or ""), "", "## Mechanism", str(candidate.get("mechanism") or ""), "", "## Draft Hypotheses"]
    for item in hypotheses:
        lines.extend([f"### {item.get('id') or 'Draft hypothesis'}", str(item.get("statement") or ""), "", f"Mechanism: {item.get('mechanism') or ''}", f"Prediction: {item.get('observable_prediction') or item.get('prediction') or ''}", f"Discriminating test: {item.get('discriminating_test') or item.get('test') or ''}", ""])
    lines.extend(["## Evidence Boundary", str(candidate.get("basis_summary") or ""), "", "## Files", *[f"- `{path}`" for path in source_paths], ""])
    return "\n".join(lines)


def _legacy_hypotheses(text: str) -> list[dict[str, str]]:
    """Extract declared H headings conservatively from a legacy markdown file."""

    matches = list(re.finditer(r"(?im)^#+\s*(H\d+)\b[^\n]*", text))
    hypotheses: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        body = text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        statement = " ".join(body.split())
        if not statement:
            statement = match.group(0).strip("# ")
        hypotheses.append(
            {
                "id": match.group(1),
                "statement": statement,
                "mechanism": "legacy_not_reparsed",
                "observable_prediction": "legacy_not_reparsed",
                "discriminating_test": "legacy_not_reparsed",
                "evidence_status": "legacy_migrated_requires_review",
            }
        )
    return hypotheses
