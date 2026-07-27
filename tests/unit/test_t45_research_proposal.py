from __future__ import annotations

import json
from pathlib import Path

from researchos.ideation.proposal import (
    PROPOSAL_MANIFEST_REL_PATH,
    PROPOSAL_REL_PATH,
    repair_t45_proposal_manifest,
    validate_t45_research_proposal,
)
from researchos.ideation.formalization import compile_t45_derived_artifacts
from researchos.ideation.novelty_verdict import (
    extract_final_gate_verdict,
    normalize_final_gate_verdict,
)
from researchos.tools.external_experiment import _build_reboost_pack, _validate_research_reboost_pack
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace, write


def test_t45_proposal_is_substantive_and_traceable(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)

    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation/novelty_audit.md")

    assert ok is True, error


def test_markdown_wrapped_passing_verdict_authorizes_t45_proposal(tmp_path: Path) -> None:
    """The auditor's documented Markdown form must not become a false failure."""

    populate_valid_t45_workspace(tmp_path)
    audit_path = tmp_path / "ideation" / "novelty_audit.md"
    write(
        audit_path,
        "# Novelty Audit\n\n## Final Gate Verdict\n\n"
        "**Final Gate Verdict**: `pass_with_required_baselines`\n",
    )
    proposal_path = tmp_path / PROPOSAL_REL_PATH
    proposal_path.touch()

    repaired, error = repair_t45_proposal_manifest(tmp_path, audit_path)
    ok, validation_error = validate_t45_research_proposal(tmp_path, audit_path)

    assert repaired is True, error
    assert ok is True, validation_error


def test_t45_final_gate_verdict_parser_accepts_markdown_and_never_defaults_to_pass(tmp_path: Path) -> None:
    """Formatting decoration is not a verdict, and an absent verdict is not approval."""

    markdown_audit = "## Final Gate Verdict\n\n**Final Gate Verdict**: `pass_with_required_baselines`\n"
    raw = extract_final_gate_verdict(markdown_audit)

    assert raw == "`pass_with_required_baselines`"
    assert normalize_final_gate_verdict(raw) == "pass_with_required_baselines"

    populate_valid_t45_workspace(tmp_path)
    audit_path = tmp_path / "ideation" / "novelty_audit.md"
    write(audit_path, "# Novelty Audit\n\nNo final verdict was recorded.\n")

    compiled, error = compile_t45_derived_artifacts(tmp_path, audit_path)

    assert compiled is False
    assert "require a passing Final Gate Verdict" in (error or "")


def test_manifest_metadata_is_repaired_from_validated_sources(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    manifest_path = tmp_path / PROPOSAL_MANIFEST_REL_PATH
    manifest_path.unlink()

    repaired, note = repair_t45_proposal_manifest(tmp_path, tmp_path / "ideation/novelty_audit.md")
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation/novelty_audit.md")

    assert repaired is True, note
    assert ok is True, error
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["candidate_id"] == "D1"
    assert manifest["t5_handoff"]["role"] == "planning_context_not_results"
    assert "ideation/research_blueprint.yaml" in manifest["traceability"]["source_artifacts"]


def test_manifest_repair_replaces_stale_selection_identity(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    manifest_path = tmp_path / PROPOSAL_MANIFEST_REL_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_id"] = "D9"
    manifest["selection_fingerprint"] = "stale-fingerprint"
    write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))

    repaired, note = repair_t45_proposal_manifest(tmp_path, tmp_path / "ideation/novelty_audit.md")
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert repaired is True, note
    assert repaired_manifest["candidate_id"] == "D1"
    assert repaired_manifest["selection_fingerprint"] == "unified-fingerprint"


def test_current_selected_candidate_overrides_a_stale_dossier_and_manifest(tmp_path: Path) -> None:
    """Old formalization artifacts cannot self-authorize a new Gate1 choice."""

    populate_valid_t45_workspace(tmp_path)
    write(
        tmp_path / "ideation" / "selected" / "selected_candidate.json",
        json.dumps(
            {
                "candidate_id": "D2",
                "selection_fingerprint": "new-selection-fingerprint",
                "candidate": {"id": "D2"},
            }
        ),
    )

    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")

    assert ok is False
    assert "selection-isolation receipt does not match" in (error or "")


def test_t5_carries_blueprint_backed_proposal_as_planning_context_not_results(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)

    pack, report = _build_reboost_pack(tmp_path)
    proposal = pack["context_reboost"]["research_context"]["proposal_context"]

    assert pack["generation_status"] == "completed"
    assert proposal["source_type"] == "formal_proposal"
    assert proposal["path"] == PROPOSAL_REL_PATH
    assert proposal["manifest_path"] == PROPOSAL_MANIFEST_REL_PATH
    assert proposal["t5_role"] == "planning_context_not_results"
    assert pack["context_reboost"]["research_context"]["active_claim_ids"] == ["TC1", "MC1"]
    source_ids = [item["source_id"] for item in pack["context_reboost"]["research_context"]["source_refs"]]
    assert "SRC_RESEARCH_BLUEPRINT" in source_ids
    assert "SRC_CLAIM_REGISTRY" in source_ids
    assert PROPOSAL_REL_PATH in report["source_files_used"]
    assert "research_proposal" in pack["writer_handoff_contract"]["must_not_use_as_final_fact_source"]

    handoff_path = tmp_path / "external_executor/handoff_pack.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    valid, error, _ = _validate_research_reboost_pack(tmp_path, handoff_path)

    assert valid is True, error


def test_t5_falls_back_when_a_present_proposal_fails_its_contract(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    manifest_path = tmp_path / PROPOSAL_MANIFEST_REL_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["t5_handoff"]["role"] = "result_source"
    write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))

    pack, _report = _build_reboost_pack(tmp_path)
    proposal = pack["context_reboost"]["research_context"]["proposal_context"]
    proposal_sources = [
        item
        for item in pack["source_manifest"]
        if item["path"] in {PROPOSAL_REL_PATH, PROPOSAL_MANIFEST_REL_PATH}
    ]

    assert proposal["source_type"] == "legacy_formalization_fallback"
    assert all(item["used"] is False for item in proposal_sources)
    assert all("does not satisfy" in item.get("omission_reason", "") for item in proposal_sources)
