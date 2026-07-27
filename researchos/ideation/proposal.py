"""Post-novelty research-proposal contract shared by T4.5 and T5.

The proposal is a planning artifact. It consolidates the selected T4 direction
and the accepted T4.5 formalization without becoming empirical evidence or an
alternative source of truth for the executable experiment plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..schemas.validator import validate_record
from .formalization import (
    BLUEPRINT_REL_PATH,
    CLAIM_REGISTRY_REL_PATH,
    ORIENTATION_CONFIG_REL_PATH,
    ORIENTATION_REVIEW_REL_PATH,
    collect_t45_quality_diagnostics,
    format_t45_repairable_quality_warnings,
    load_orientation_configuration,
    validate_orientation_review,
    validate_research_proposal_text,
    validate_t45_selection_isolation,
    validate_t45_formalization_core,
)


PROPOSAL_REL_PATH = "ideation/proposal/research_proposal.md"
PROPOSAL_MANIFEST_REL_PATH = "ideation/proposal/proposal_manifest.json"
PROPOSAL_SEMANTICS = "t45_research_proposal"
PROPOSAL_STATUS = "formalized_after_novelty_pass"

PROPOSAL_REQUIRED_SOURCE_ARTIFACTS = (
    "ideation/selected/selected_candidate.json",
    ORIENTATION_CONFIG_REL_PATH,
    BLUEPRINT_REL_PATH,
    CLAIM_REGISTRY_REL_PATH,
    "ideation/hypotheses.md",
    "ideation/research_dossier.json",
    "ideation/exp_plan.yaml",
    "ideation/novelty_audit.md",
    "ideation/validation_map.yaml",
    "ideation/kill_criteria.yaml",
    ORIENTATION_REVIEW_REL_PATH,
)

_PASS_VERDICTS = {
    "pass",
    "passed",
    "pass_to_experiment",
    "pass_with_required_baselines",
    "continue_to_t5",
    "continue_to_experiment",
}

PROPOSAL_SECTION_KEYS = (
    "motivation",
    "gap_and_challenges",
    "approach",
    "claims",
    "evaluation",
    "contributions",
    "risks",
)

_DEFAULT_SECTION_SOURCES = {
    "motivation": ("ideation/selected/selected_candidate.json", BLUEPRINT_REL_PATH, "literature/synthesis.md"),
    "gap_and_challenges": (BLUEPRINT_REL_PATH, "literature/synthesis.md", "ideation/novelty_audit.md"),
    "approach": (BLUEPRINT_REL_PATH, CLAIM_REGISTRY_REL_PATH),
    "claims": (CLAIM_REGISTRY_REL_PATH, "ideation/hypotheses.md"),
    "evaluation": (BLUEPRINT_REL_PATH, "ideation/exp_plan.yaml", "ideation/validation_map.yaml"),
    "contributions": (BLUEPRINT_REL_PATH, "ideation/contribution_hypothesis_map.yaml"),
    "risks": (BLUEPRINT_REL_PATH, "ideation/kill_criteria.yaml"),
}


def proposal_artifact_paths(workspace: Path) -> dict[str, Path]:
    """Return canonical T4.5 proposal artifacts relative to one workspace."""

    return {
        "research_proposal": workspace / PROPOSAL_REL_PATH,
        "proposal_manifest": workspace / PROPOSAL_MANIFEST_REL_PATH,
    }


def _normalized_audit_verdict(audit_text: str) -> tuple[str, str]:
    matches = list(re.finditer(
        r"(?im)^\s*(?:#+\s*)?(?:\*\*)?\s*Final\s+Gate\s+Verdict\s*(?:\*\*)?\s*[:：]\s*(.+?)\s*$",
        audit_text,
    ))
    match = matches[-1] if matches else None
    raw_verdict = match.group(1).strip() if match else ""
    normalized = re.split(
        r"[^a-z0-9_]+",
        raw_verdict.casefold().replace("-", "_").replace(" ", "_"),
        maxsplit=1,
    )[0]
    return raw_verdict, normalized


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _selected_candidate_field(selected: dict[str, Any], *keys: str) -> str:
    candidate = selected.get("candidate") if isinstance(selected.get("candidate"), dict) else {}
    for container in (selected, candidate):
        for key in keys:
            value = container.get(key) if isinstance(container, dict) else None
            if str(value or "").strip():
                return str(value).strip()
    return ""


def repair_t45_proposal_manifest(workspace: Path, audit_path: Path) -> tuple[bool, str | None]:
    """Repair deterministic Proposal manifest omissions without inventing prose.

    This deliberately only derives metadata from existing T4/T4.5 artifacts.
    Missing scholarly argument, facts, hypotheses, or Proposal sections remains
    an LLM repair because a template must never manufacture those claims.
    """

    paths = proposal_artifact_paths(workspace)
    proposal_path = paths["research_proposal"]
    if not proposal_path.is_file() or proposal_path.stat().st_size <= 0:
        return False, None
    if not audit_path.is_file() or audit_path.stat().st_size <= 0:
        return False, None
    exp_plan_path = workspace / "ideation" / "exp_plan.yaml"
    try:
        exp_plan = yaml.safe_load(exp_plan_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False, "cannot repair proposal manifest before exp_plan.yaml is readable"
    if not isinstance(exp_plan, dict):
        return False, "cannot repair proposal manifest before exp_plan.yaml is an object"
    exp_plan_ok, _exp_plan_error = validate_record(exp_plan, "exp_plan")
    if not exp_plan_ok:
        return False, "cannot repair proposal manifest before exp_plan.yaml passes its schema"
    formalization_ok, formalization_error = validate_t45_formalization_core(workspace)
    if not formalization_ok:
        return False, (
            "cannot repair proposal manifest before the unified blueprint and claim registry pass validation"
            + (f": {formalization_error}" if formalization_error else "")
        )
    raw_verdict, normalized_verdict = _normalized_audit_verdict(
        audit_path.read_text(encoding="utf-8", errors="replace")
    )
    if normalized_verdict not in _PASS_VERDICTS:
        return False, None

    manifest_path = paths["proposal_manifest"]
    manifest = _json_object(manifest_path)
    original = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    dossier = _json_object(workspace / "ideation" / "research_dossier.json")
    selected = _json_object(workspace / "ideation" / "selected" / "selected_candidate.json")
    candidate_id = _first_text(
        _selected_candidate_field(selected, "candidate_id", "id", "idea_id", "selected_id"),
        dossier.get("candidate_id"),
        manifest.get("candidate_id"),
    )
    selection_fingerprint = _first_text(
        _selected_candidate_field(selected, "selection_fingerprint", "fingerprint"),
        dossier.get("selection_fingerprint"),
        manifest.get("selection_fingerprint"),
    )
    # These two values identify the selected T4 route.  Leave semantic repair
    # to the auditor if a legacy or corrupted workspace cannot recover them.
    if not candidate_id or not selection_fingerprint:
        return False, None

    manifest["semantics"] = PROPOSAL_SEMANTICS
    manifest.setdefault("schema_version", "1.0.0")
    manifest["status"] = PROPOSAL_STATUS
    manifest["proposal_path"] = PROPOSAL_REL_PATH
    manifest["candidate_id"] = candidate_id
    manifest["selection_fingerprint"] = selection_fingerprint
    manifest["novelty_audit_verdict"] = raw_verdict

    section_map = manifest.get("section_source_map")
    section_map = section_map if isinstance(section_map, dict) else {}
    normalized_map: dict[str, list[str]] = {}
    for section, defaults in _DEFAULT_SECTION_SOURCES.items():
        current = section_map.get(section)
        retained = [
            str(path).strip()
            for path in (current if isinstance(current, list) else [])
            if str(path).strip() and (workspace / str(path).strip()).is_file()
        ]
        additions = [path for path in defaults if (workspace / path).is_file()]
        normalized_map[section] = list(dict.fromkeys([*retained, *additions]))
    manifest["section_source_map"] = normalized_map

    traceability = manifest.get("traceability")
    traceability = traceability if isinstance(traceability, dict) else {}
    existing_sources = traceability.get("source_artifacts")
    retained_sources = [
        str(path).strip()
        for path in (existing_sources if isinstance(existing_sources, list) else [])
        if str(path).strip() and (workspace / str(path).strip()).exists()
    ]
    mapped_sources = [path for section_sources in normalized_map.values() for path in section_sources]
    required_sources = [
        path for path in PROPOSAL_REQUIRED_SOURCE_ARTIFACTS if (workspace / path).exists()
    ]
    traceability["source_artifacts"] = list(
        dict.fromkeys([*retained_sources, *mapped_sources, *required_sources])
    )
    manifest["traceability"] = traceability

    handoff = manifest.get("t5_handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    existing_preserve = handoff.get("preserve") if isinstance(handoff.get("preserve"), list) else []
    handoff["role"] = "planning_context_not_results"
    handoff["must_include"] = True
    handoff["preserve"] = list(
        dict.fromkeys(
            [
                *(str(item).strip() for item in existing_preserve if str(item).strip()),
                "required baselines",
                "claim boundaries",
                "kill criteria",
                "unknown fields",
            ]
        )
    )
    manifest["t5_handoff"] = handoff

    repaired = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    if repaired == original and manifest_path.is_file():
        return False, None
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, "Proposal manifest deterministic metadata repair completed"


def validate_t45_research_proposal(
    workspace: Path,
    audit_path: Path,
    *,
    require_accepted_lineage: bool = False,
) -> tuple[bool, str | None]:
    """Validate a researcher-facing Proposal against the unified blueprint.

    The Proposal is intentionally not validated as an audit transcript.  The
    novelty report remains authoritative for collision labels and nearest-work
    evidence, while this function checks the actual research argument, its
    structured sources, and the T5 handoff metadata.
    """

    if not audit_path.exists() or audit_path.stat().st_size <= 0:
        return False, "novelty_audit.md is required before a post-novelty proposal"
    audit_text = audit_path.read_text(encoding="utf-8", errors="replace")
    _raw_verdict, normalized_verdict = _normalized_audit_verdict(audit_text)
    if normalized_verdict not in _PASS_VERDICTS:
        return False, "research_proposal.md requires a passing Final Gate Verdict in novelty_audit.md"

    formalization_ok, formalization_error = validate_t45_formalization_core(workspace)
    if not formalization_ok:
        return False, formalization_error
    lineage_ok, lineage_error = validate_t45_selection_isolation(
        workspace,
        require_accepted=require_accepted_lineage,
    )
    if not lineage_ok:
        return False, lineage_error

    paths = proposal_artifact_paths(workspace)
    missing = [name for name, path in paths.items() if not path.exists() or path.stat().st_size <= 0]
    if missing:
        return False, "post-novelty proposal is missing: " + ", ".join(missing)
    stale = [name for name, path in paths.items() if path.stat().st_mtime < audit_path.stat().st_mtime]
    if stale:
        return False, "post-novelty proposal predates the novelty audit: " + ", ".join(stale)

    proposal_path = paths["research_proposal"]
    proposal_text = proposal_path.read_text(encoding="utf-8", errors="replace")
    try:
        blueprint = yaml.safe_load((workspace / BLUEPRINT_REL_PATH).read_text(encoding="utf-8")) or {}
        registry = yaml.safe_load((workspace / CLAIM_REGISTRY_REL_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return False, f"Cannot read blueprint or claim registry for Proposal validation: {exc}"
    if not isinstance(blueprint, dict) or not isinstance(registry, dict):
        return False, "research_blueprint.yaml and claim_registry.yaml must both contain objects"
    proposal_error = validate_research_proposal_text(
        proposal_text,
        blueprint=blueprint,
        registry=registry,
        orientation=load_orientation_configuration(workspace),
    )
    if proposal_error:
        return False, proposal_error

    manifest_path = paths["proposal_manifest"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"proposal_manifest.json cannot be read: {exc}"
    if not isinstance(manifest, dict) or manifest.get("semantics") != PROPOSAL_SEMANTICS:
        return False, "proposal_manifest.json semantics is invalid"
    if manifest.get("status") != PROPOSAL_STATUS:
        return False, "proposal_manifest.json is not marked as an accepted audit result"
    if manifest.get("proposal_path") != PROPOSAL_REL_PATH:
        return False, "proposal_manifest.json must point to research_proposal.md"
    if not str(manifest.get("candidate_id") or "").strip():
        return False, "proposal_manifest.json is missing candidate_id"
    if not str(manifest.get("selection_fingerprint") or "").strip():
        return False, "proposal_manifest.json is missing selection_fingerprint"
    dossier = _json_object(workspace / "ideation" / "research_dossier.json")
    selected = _json_object(workspace / "ideation" / "selected" / "selected_candidate.json")
    expected_candidate_id = _first_text(
        _selected_candidate_field(selected, "candidate_id", "id", "idea_id", "selected_id"),
        dossier.get("candidate_id"),
    )
    if expected_candidate_id and str(manifest.get("candidate_id")).strip() != expected_candidate_id:
        return False, "proposal_manifest.json candidate_id does not match the current selected Candidate"
    expected_selection_fingerprint = _first_text(
        _selected_candidate_field(selected, "selection_fingerprint", "fingerprint"),
        dossier.get("selection_fingerprint"),
    )
    if (
        expected_selection_fingerprint
        and str(manifest.get("selection_fingerprint")).strip() != expected_selection_fingerprint
    ):
        return False, "proposal_manifest.json selection_fingerprint does not match the current selected Candidate"
    manifest_verdict = str(manifest.get("novelty_audit_verdict") or "").strip()
    if not manifest_verdict:
        return False, "proposal_manifest.json is missing novelty_audit_verdict"
    _unused_manifest_verdict, normalized_manifest_verdict = _normalized_audit_verdict(
        f"Final Gate Verdict: {manifest_verdict}"
    )
    if normalized_manifest_verdict != normalized_verdict:
        return False, "proposal_manifest.json novelty_audit_verdict does not match novelty_audit.md"

    section_map = manifest.get("section_source_map")
    if not isinstance(section_map, dict):
        return False, "proposal_manifest.json section_source_map must be an object"
    missing_mappings = [
        key
        for key in PROPOSAL_SECTION_KEYS
        if not isinstance(section_map.get(key), list) or not section_map[key]
    ]
    if missing_mappings:
        return False, "proposal_manifest.json lacks section source mappings: " + ", ".join(missing_mappings)

    section_paths = [
        str(path).strip()
        for paths_for_section in section_map.values()
        if isinstance(paths_for_section, list)
        for path in paths_for_section
        if str(path).strip()
    ]
    missing_section_paths = [rel for rel in section_paths if not (workspace / rel).is_file()]
    if missing_section_paths:
        return False, "proposal_manifest.json section sources are missing: " + ", ".join(missing_section_paths[:5])

    traceability = manifest.get("traceability")
    if not isinstance(traceability, dict) or not isinstance(traceability.get("source_artifacts"), list):
        return False, "proposal_manifest.json traceability.source_artifacts is invalid"
    source_artifacts = [str(item).strip() for item in traceability["source_artifacts"] if str(item).strip()]
    if not source_artifacts:
        return False, "proposal_manifest.json must retain source artifacts"
    missing_sources = [rel for rel in source_artifacts if not (workspace / rel).exists()]
    if missing_sources:
        return False, "proposal_manifest.json references missing source artifacts: " + ", ".join(missing_sources[:5])
    unmapped_section_sources = [rel for rel in section_paths if rel not in source_artifacts]
    if unmapped_section_sources:
        return False, "proposal_manifest.json traceability omits section sources: " + ", ".join(unmapped_section_sources[:5])
    missing_required_sources = [rel for rel in PROPOSAL_REQUIRED_SOURCE_ARTIFACTS if rel not in source_artifacts]
    if missing_required_sources:
        return False, "proposal_manifest.json is missing required T4/T4.5 sources: " + ", ".join(missing_required_sources)

    handoff = manifest.get("t5_handoff")
    if not isinstance(handoff, dict):
        return False, "proposal_manifest.json is missing t5_handoff"
    if handoff.get("role") != "planning_context_not_results":
        return False, "proposal_manifest.json t5_handoff role must preserve the planning-only boundary"
    if handoff.get("must_include") is not True:
        return False, "proposal_manifest.json must require T5 handoff inclusion"
    preserved = handoff.get("preserve") if isinstance(handoff.get("preserve"), list) else []
    required_preservation = {"required baselines", "claim boundaries", "kill criteria", "unknown fields"}
    if not required_preservation.issubset({str(item).strip().casefold() for item in preserved}):
        return False, "proposal_manifest.json t5_handoff must preserve baselines, claim boundaries, kill criteria, and unknown fields"
    review_ok, review_error = validate_orientation_review(workspace)
    if not review_ok:
        return False, review_error
    quality_warning = format_t45_repairable_quality_warnings(collect_t45_quality_diagnostics(workspace))
    if quality_warning:
        return False, quality_warning
    return True, None


def proposal_source_ref() -> dict[str, str]:
    """Stable source reference used in the T5 handoff contract."""

    return {
        "source_id": "SRC_RESEARCH_PROPOSAL",
        "locator": PROPOSAL_REL_PATH,
        "note": "Post-novelty proposal consolidates planning context, constraints, and conditional implications",
        "support_type": "reconciled",
    }


def proposal_manifest_source_ref() -> dict[str, str]:
    """Stable manifest reference used alongside the human-readable proposal."""

    return {
        "source_id": "SRC_PROPOSAL_MANIFEST",
        "locator": PROPOSAL_MANIFEST_REL_PATH,
        "note": "Proposal provenance, source traceability, and the planning-only T5 transfer boundary",
        "support_type": "reconciled",
    }
