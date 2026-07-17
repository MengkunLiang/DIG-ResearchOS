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
    ready, reason = validate_candidate_selection_ready(candidate)
    if not ready:
        raise ValueError(reason or "Selected Candidate is not ready for Pre-Novelty compilation")
    selection_warnings = candidate_selection_warnings(candidate)
    hypotheses = candidate.get("candidate_hypotheses") if isinstance(candidate.get("candidate_hypotheses"), list) else []
    hypotheses = [item for item in hypotheses if isinstance(item, dict)]
    if not 1 <= len(hypotheses) <= 4:
        raise ValueError("Pre-Novelty compilation requires 1-4 LLM-authored draft hypotheses")
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
        "selection_warnings": selection_warnings,
    }
    store.write_json("ideation/selected/selected_candidate.json", selected_payload)
    brief = {
        "schema_version": "1.0.0",
        "semantics": "t4_pre_novelty_hypothesis_brief",
        "selection_fingerprint": selection_fingerprint,
        "candidate_id": selected_candidate_id,
        "status": "draft_for_novelty_review",
        # A Gate1 selection is an instruction to perform the novelty audit,
        # not a claim that its empirical support is complete.  Preserve the
        # screening concern so T4.5 can audit it explicitly instead of
        # returning a mature Candidate to T4 without an actionable path.
        "selection_warnings": selection_warnings,
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


def validate_candidate_selection_ready(candidate: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate the minimum structural contract for a T4.5 audit handoff.

    Gate1 may present a fully explained, independently scored IdeaSeed whose
    evidence and design still need a novelty audit.  ``maturity=seed`` is a
    lifecycle label, not a reason to make a confirmed selection loop back to
    T4.  T4.5 is precisely where the provisional mechanism, one initial
    falsifiable hypothesis, and evidence limitations are audited.  Missing
    scientific structure remains a hard block; provisionality is carried in
    the Pre-Novelty warnings instead.
    """

    candidate_id = str(candidate.get("id") or candidate.get("idea_id") or "Candidate").strip()
    native_lifecycle_fields = {"maturity", "scoring_status", "evolution_score", "candidate_status"}
    if not any(field in candidate for field in native_lifecycle_fields):
        # Historical Gate1 workspaces predate the native Population lifecycle.
        # They already store a human-visible candidate, hypotheses, and their
        # own selection/resume contract. Do not reinterpret a missing native
        # maturity field as ``seed`` and strand a previously valid workspace.
        # New T4 projections always carry lifecycle fields, so the strict
        # selection checks below remain mandatory for native Evolution.
        return True, None
    # ``revise_before_selection`` is an evidence/validation recommendation,
    # rather than a lifecycle or structural defect.  T4.5 is the dedicated
    # pre-novelty audit which must receive and assess that concern.  Treating
    # it as a hard rejection made an explicit "推进 D1" appear to succeed at
    # confirmation, then silently reopen T4 forever.
    final_card = candidate.get("final_idea_card") if isinstance(candidate.get("final_idea_card"), dict) else {}
    if not final_card:
        # The runtime repairs the Portfolio Card with the LLM before opening
        # Gate1. This guard protects old/corrupt workspaces as well: a
        # renderer may never substitute a title, score, or risk sentence for
        # the missing LLM decision narrative.
        return (
            False,
            f"Candidate {candidate_id} 缺少已完成的 LLM Portfolio Idea Card；"
            "请先完成定向卡片富化或继续演化，再进入 Pre-Novelty selection。",
        )
    if str(candidate.get("scoring_status") or "").strip() == "unscored":
        return (
            False,
            f"Candidate {candidate_id} has no independent score after bounded retry. Retry scoring or review it before Pre-Novelty selection.",
        )
    if not isinstance(candidate.get("evolution_score"), dict):
        return False, f"Candidate {candidate_id} has no independent scoring record for Pre-Novelty selection."
    hypotheses = candidate.get("candidate_hypotheses") if isinstance(candidate.get("candidate_hypotheses"), list) else []
    if not 1 <= len([item for item in hypotheses if isinstance(item, dict)]) <= 4:
        return False, f"Candidate {candidate_id} needs 1-4 LLM-authored draft hypotheses before Pre-Novelty selection."
    if not str(candidate.get("core_claim") or candidate.get("pitch") or "").strip():
        return False, f"Candidate {candidate_id} lacks a traceable core thesis for Pre-Novelty selection."
    return True, None


def candidate_selection_warnings(candidate: dict[str, Any]) -> list[str]:
    """Return non-blocking Gate1 concerns that T4.5 must audit.

    These warnings deliberately do not replace ``validate_candidate_selection_ready``:
    malformed or incomplete Candidate artifacts still block T4.5.  They only
    carry forward substantive concerns such as missing evidence anchors,
    recommended validation upgrades, and model-authored uncertainty notes.
    """

    warnings: list[str] = []
    screening = candidate.get("pass2_screening") if isinstance(candidate.get("pass2_screening"), dict) else {}
    recommendation = str(screening.get("screening_recommendation") or "").strip()
    screening_warning = str(screening.get("selection_warning") or candidate.get("selection_warning") or "").strip()
    if recommendation and recommendation != "proceed":
        if screening_warning:
            warnings.append(screening_warning)
        else:
            warnings.append(
                f"Gate1 screening recommendation is {recommendation}; T4.5 must review the stated evidence, validation, or scoring concern."
            )
    maturity = str(candidate.get("maturity") or "").strip().casefold()
    if maturity == "seed":
        warnings.append(
            "该候选仍处于探索性 IdeaSeed 生命周期；T4.5 必须审计其机制边界、证据缺口和后续富化需求，不得将其直接视为已成熟的研究方案。"
        )
    hypotheses = candidate.get("candidate_hypotheses") if isinstance(candidate.get("candidate_hypotheses"), list) else []
    hypothesis_count = len([item for item in hypotheses if isinstance(item, dict)])
    if hypothesis_count == 1:
        warnings.append(
            "当前仅有一条草案假设；T4.5 应核验该假设的可证伪性，并判断是否需要补充竞争机制、边界条件或额外验证假设。"
        )
    for value in candidate.get("warnings") if isinstance(candidate.get("warnings"), list) else []:
        text = str(value).strip()
        if text:
            warnings.append(text)
    # Preserve ordering while avoiding duplicate messages in the brief and UI.
    return list(dict.fromkeys(warnings))


def candidate_selection_readiness(
    workspace_dir: Path,
    *,
    candidate_id: str,
) -> tuple[bool, str | None]:
    """Read one Gate1 Candidate and evaluate its Pre-Novelty readiness."""

    return validate_candidate_selection_ready(_load_candidate(workspace_dir, candidate_id))


def candidate_selection_warnings_for_workspace(
    workspace_dir: Path,
    *,
    candidate_id: str,
) -> list[str]:
    """Read one Gate1 Candidate and return its non-blocking T4.5 warnings."""

    return candidate_selection_warnings(_load_candidate(workspace_dir, candidate_id))


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
        try:
            existing = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            existing = None
        mode = (
            "legacy_migrated"
            if isinstance(existing, dict) and existing.get("status") == "legacy_migrated_for_novelty_review"
            else "native_or_existing"
        )
        return {"hypothesis_brief": "ideation/hypothesis_brief.yaml", "mode": mode}
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


def validate_legacy_t45_brief_source(workspace_dir: Path) -> tuple[bool, str | None]:
    """Confirm that a migrated brief still represents its formal legacy source."""

    workspace = Path(workspace_dir)
    brief_path = workspace / "ideation" / "hypothesis_brief.yaml"
    hypotheses_path = workspace / "ideation" / "hypotheses.md"
    try:
        brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return False, f"cannot read legacy-migrated hypothesis brief: {exc}"
    if not isinstance(brief, dict) or brief.get("status") != "legacy_migrated_for_novelty_review":
        return True, None
    if not hypotheses_path.is_file() or hypotheses_path.stat().st_size <= 0:
        return False, "legacy-migrated T4.5 brief no longer has ideation/hypotheses.md as its source"
    current = stable_fingerprint({"hypotheses": hypotheses_path.read_text(encoding="utf-8", errors="replace")})
    if str(brief.get("selection_fingerprint") or "") != current:
        return False, "legacy hypotheses.md changed after the Pre-Novelty migration; rerun the novelty audit"
    return True, None


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
    warnings = candidate_selection_warnings(candidate)
    if warnings:
        lines.extend(["## Gate1 Warnings To Audit", *[f"- {warning}" for warning in warnings], ""])
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
