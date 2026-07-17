"""Human-facing Final Idea Card readiness checks for native T4 Gate1.

Low-level Gate1 artifact validation intentionally stays permissive enough to
preserve an exploratory Population after an LLM display failure.  This module
adds the separate boundary needed immediately before opening a researcher
decision surface: every visible Portfolio Candidate must have one completed,
typed, LLM-authored Final Idea Card.  It never writes, repairs, or derives
research content.  Callers must route a failed result to bounded LLM repair
and then a Human Recovery Gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from ..pydantic_compat import model_dump, model_validate
from .models import CandidateDossier, FinalIdeaCardTranslation, PortfolioSelection
from .state import T4ArtifactStore


PORTFOLIO_PATH = "ideation/portfolio.json"
PORTFOLIO_CARDS_PATH = "ideation/final_cards/portfolio_cards.json"
PORTFOLIO_CARDS_SEMANTICS = "t4_final_idea_card_translations"
FINAL_CARD_COMPATIBILITY_RECEIPT_PATH = "ideation/final_cards/final_card_compatibility_migration.json"
FINAL_CARD_PROFILE_ARCHIVE_DIR = "ideation/final_cards/profile_history"
FINAL_CARD_PROFILE_REFRESH_RECEIPT_PATH = "ideation/final_cards/profile_refresh_receipt.json"


def portfolio_candidate_ids(portfolio: PortfolioSelection) -> list[str]:
    """Return the ordered, user-visible Portfolio membership.

    This is a state/identity helper only.  It does not rank or infer research
    relationships between Candidates.
    """

    return [
        candidate_id
        for candidate_id in (
            portfolio.lead_id,
            *portfolio.alternative_ids,
            *portfolio.high_upside_ids,
        )
        if candidate_id
    ]


def validate_t4_portfolio_final_cards(workspace_dir: Path) -> tuple[bool, str | None]:
    """Verify that Gate1 can render complete LLM Final Cards for its Portfolio.

    A failure is *recoverable*: native Candidates, scores, lineage, and partial
    artifacts remain valid.  The caller should invoke the Final Card Compiler
    with its bounded repair budget and, if that remains unsuccessful, present a
    Human Recovery Gate rather than opening a partial Idea Card.
    """

    workspace = Path(workspace_dir)
    # ``real_world_significance`` was introduced after some completed T4
    # workspaces had already persisted their LLM-authored Cards.  Those Cards
    # contain the same researcher-facing meaning in ``why_it_matters`` but
    # cannot be parsed by the stricter current schema.  Apply the narrowly
    # scoped, auditable compatibility migration before judging readiness.  It
    # never invents prose and never fills any other mandatory field; genuinely
    # incomplete Cards still take the bounded LLM-repair/Human-Recovery path.
    migrate_legacy_final_card_schema(workspace)
    portfolio_raw, error = _read_json_object(workspace / PORTFOLIO_PATH, "Portfolio")
    if error:
        return False, error
    assert portfolio_raw is not None
    try:
        portfolio = model_validate(PortfolioSelection, portfolio_raw)
    except (TypeError, ValueError) as exc:
        return False, f"T4 Portfolio artifact is invalid: {exc}"

    expected_ids = portfolio_candidate_ids(portfolio)
    if not expected_ids:
        return False, "T4 Portfolio has no visible Candidate requiring a Final Idea Card"
    if len(expected_ids) != len(set(expected_ids)):
        return False, "T4 Portfolio contains duplicate Candidate IDs; Final Card coverage is ambiguous"

    cards_payload, error = _read_json_object(workspace / PORTFOLIO_CARDS_PATH, "Final Idea Card")
    if error:
        return False, error
    assert cards_payload is not None
    if str(cards_payload.get("semantics") or "").strip() != PORTFOLIO_CARDS_SEMANTICS:
        return False, "T4 Final Idea Card artifact has an unexpected semantics value"
    if str(cards_payload.get("status") or "").strip().casefold() != "completed":
        return False, "T4 Final Idea Card compilation is not completed; bounded LLM repair is required before Gate1"
    if str(cards_payload.get("population_id") or "").strip() != portfolio.population_id:
        return False, "T4 Final Idea Card artifact belongs to a different Population"

    raw_cards = cards_payload.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        return False, "T4 Final Idea Card artifact has no completed cards for the current Portfolio"
    if any(not isinstance(card, dict) for card in raw_cards):
        return False, "T4 Final Idea Card artifact contains a non-object card"

    cards_by_id: dict[str, FinalIdeaCardTranslation] = {}
    for raw_card in raw_cards:
        candidate_id = str(raw_card.get("candidate_id") or "").strip()
        if not candidate_id:
            return False, "T4 Final Idea Card is missing its Candidate ID"
        if candidate_id in cards_by_id:
            return False, f"T4 Final Idea Card artifact contains duplicate Candidate ID {candidate_id}"
        try:
            cards_by_id[candidate_id] = model_validate(FinalIdeaCardTranslation, raw_card)
        except (TypeError, ValueError) as exc:
            return False, f"T4 Final Idea Card for {candidate_id} is incomplete or invalid: {exc}"

    actual_ids = set(cards_by_id)
    expected = set(expected_ids)
    if actual_ids != expected:
        missing = sorted(expected - actual_ids)
        extra = sorted(actual_ids - expected)
        fragments: list[str] = []
        if missing:
            fragments.append("missing=" + ", ".join(missing))
        if extra:
            fragments.append("extra=" + ", ".join(extra))
        return False, "T4 Final Idea Card coverage does not match the current Portfolio (" + "; ".join(fragments) + ")"

    # A final card is a non-mutating translation of the active native
    # Candidate. This identity check follows basic Card parsing so a malformed
    # or deferred Card reports its actual repair need rather than a secondary
    # missing-state error.
    try:
        store = T4ArtifactStore(workspace)
        state = store.read_state()
        population = store.read_population(state.current_population_id)
        run_config = store.read_run_config()
    except (TypeError, ValueError) as exc:
        return False, f"T4 native state required for Final Card readiness is unavailable: {exc}"
    if state.current_population_id != portfolio.population_id:
        return False, "T4 Portfolio does not belong to the active native Population"
    if population.population_id != portfolio.population_id:
        return False, "T4 Portfolio and active Population artifacts disagree"
    unknown_portfolio_ids = sorted(set(expected_ids) - set(population.active_candidate_ids))
    if unknown_portfolio_ids:
        return False, "T4 Portfolio references Candidates outside the active Population: " + ", ".join(unknown_portfolio_ids)
    dossier_by_id, dossier_error = _load_active_dossiers(store, expected_ids)
    if dossier_error:
        return False, dossier_error

    expected_profile_type = str(run_config.target_profile.profile_type or "").strip()
    saved_profile_type = _profile_type_from_payload(cards_payload.get("target_profile"))
    if saved_profile_type and saved_profile_type != expected_profile_type:
        return False, (
            "T4 Final Idea Card deck profile does not match the current run config "
            f"(saved={saved_profile_type}; current={expected_profile_type}); "
            "the researcher-facing cards must be recompiled for the current publication orientation"
        )

    for candidate_id in expected_ids:
        card = cards_by_id[candidate_id]
        dossier = dossier_by_id[candidate_id]
        if card.profile_type != expected_profile_type:
            return False, (
                "T4 Final Idea Card profile does not match the current run config "
                f"for {candidate_id} (saved={card.profile_type}; current={expected_profile_type}); "
                "the researcher-facing card must be recompiled"
            )
        if card.core_thesis != str(dossier.genome.core_thesis.value):
            return False, f"T4 Final Idea Card changed the active Candidate core thesis for {candidate_id}"
        contribution_ids = [item.contribution_id for item in dossier.contributions]
        if card.contribution_ids != contribution_ids:
            return False, f"T4 Final Idea Card contribution membership is stale for {candidate_id}"
        hypothesis_ids = [item.hypothesis_id for item in dossier.hypotheses]
        if card.hypothesis_ids != hypothesis_ids:
            return False, f"T4 Final Idea Card hypothesis membership is stale for {candidate_id}"

    directions_error = _validate_projected_final_cards(workspace, cards_by_id)
    if directions_error:
        return False, directions_error

    return True, None


def archive_final_card_profile_mismatch(
    workspace_dir: Path,
    *,
    current_profile_type: str,
) -> dict[str, Any] | None:
    """Preserve a completed card deck before it is recompiled for a new profile.

    Changing a publication orientation does not change a Candidate's science,
    but it does change the audience-specific explanation in its Final Card.
    Keep the old LLM-authored deck in a content-addressed archive so the
    compiler can replace the active deck without erasing the historical view.
    The operation is idempotent and never alters the active source file.
    """

    workspace = Path(workspace_dir)
    cards_path = workspace / PORTFOLIO_CARDS_PATH
    payload, error = _read_json_object(cards_path, "Final Idea Card")
    if error or payload is None:
        return None
    expected = str(current_profile_type or "").strip()
    if not expected:
        return None
    raw_cards = payload.get("cards")
    card_profiles = {
        str(card.get("profile_type") or "").strip()
        for card in raw_cards
        if isinstance(card, dict) and str(card.get("profile_type") or "").strip()
    } if isinstance(raw_cards, list) else set()
    deck_profile = _profile_type_from_payload(payload.get("target_profile"))
    saved_profiles = sorted({*card_profiles, *({deck_profile} if deck_profile else set())})
    if not saved_profiles or saved_profiles == [expected]:
        return None

    try:
        source_bytes = cards_path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(source_bytes).hexdigest()
    archive_relative = f"{FINAL_CARD_PROFILE_ARCHIVE_DIR}/{digest}.json"
    archive_path = workspace / archive_relative
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archived_now = False
    if not archive_path.exists():
        archive_path.write_bytes(source_bytes)
        archived_now = True
    receipt = {
        "schema_version": "1.0.0",
        "semantics": "t4_final_card_profile_refresh",
        "active_cards_path": PORTFOLIO_CARDS_PATH,
        "archived_cards_path": archive_relative,
        "archived_content_sha256": digest,
        "saved_profile_types": saved_profiles,
        "current_profile_type": expected,
        "action": "preserved_prior_profile_deck_before_llm_recompile",
        "archived_now": archived_now,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(workspace / FINAL_CARD_PROFILE_REFRESH_RECEIPT_PATH, receipt)
    return receipt


def _profile_type_from_payload(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("profile_type") or "").strip()


def migrate_legacy_final_card_schema(workspace_dir: Path) -> dict[str, Any]:
    """Repair the one known non-destructive Final Card schema migration.

    A historical card schema did not persist ``real_world_significance`` even
    though the previous LLM prompt already required ``why_it_matters``.  The
    latter is copied verbatim, rather than derived from a score, Candidate
    genome, or renderer fallback.  The same value must be placed in the
    Gate1 projection so the typed portfolio artifact and the public Candidate
    surface remain byte-for-byte aligned after Pydantic normalization.

    The migration is intentionally conservative and idempotent: no source
    file is deleted, no field other than the missing compatibility field is
    altered, and an unreadable/insufficient source remains untouched.
    """

    workspace = Path(workspace_dir)
    cards_path = workspace / PORTFOLIO_CARDS_PATH
    directions_path = workspace / "ideation/_candidate_directions.json"
    receipt_path = workspace / FINAL_CARD_COMPATIBILITY_RECEIPT_PATH
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "semantics": "t4_final_card_compatibility_migration",
        "compatibility_rule": "missing real_world_significance <- why_it_matters (verbatim LLM-authored text)",
        "cards_path": PORTFOLIO_CARDS_PATH,
        "projection_path": "ideation/_candidate_directions.json",
        "migrated_candidate_ids": [],
        "projection_updated_candidate_ids": [],
        "unmigrated_candidate_ids": [],
    }
    cards_payload, cards_error = _read_json_object(cards_path, "Final Idea Card")
    if cards_error or cards_payload is None:
        result["status"] = "not_applicable"
        result["reason"] = cards_error or "Final Idea Card payload is unavailable"
        return result
    raw_cards = cards_payload.get("cards")
    if not isinstance(raw_cards, list):
        result["status"] = "not_applicable"
        result["reason"] = "Final Idea Card payload has no cards list"
        return result

    changed_cards: dict[str, str] = {}
    for card in raw_cards:
        if not isinstance(card, dict):
            continue
        candidate_id = str(card.get("candidate_id") or "").strip()
        current = str(card.get("real_world_significance") or "").strip()
        if current:
            continue
        source = str(card.get("why_it_matters") or "").strip()
        if not candidate_id or not source:
            if candidate_id:
                result["unmigrated_candidate_ids"].append(candidate_id)
            continue
        card["real_world_significance"] = source
        changed_cards[candidate_id] = source
        result["migrated_candidate_ids"].append(candidate_id)

    if not changed_cards:
        result["status"] = "already_current"
        return result

    # Stage both documents before replacing either; a projection mismatch must
    # never be created by a partial migration write.
    directions_payload, directions_error = _read_json_object(directions_path, "Gate1 candidate projection")
    if directions_error or directions_payload is None:
        result["status"] = "blocked"
        result["reason"] = directions_error or "Gate1 candidate projection is unavailable"
        return result
    candidates = directions_payload.get("candidates")
    if not isinstance(candidates, list):
        result["status"] = "blocked"
        result["reason"] = "Gate1 candidate projection has no candidates list"
        return result
    projected_by_id = {
        str(candidate.get("id") or candidate.get("idea_id") or "").strip(): candidate
        for candidate in candidates
        if isinstance(candidate, dict)
    }
    missing_projection = [candidate_id for candidate_id in changed_cards if candidate_id not in projected_by_id]
    if missing_projection:
        result["status"] = "blocked"
        result["reason"] = "Gate1 candidate projection is missing: " + ", ".join(missing_projection)
        return result
    for candidate_id, value in changed_cards.items():
        candidate = projected_by_id[candidate_id]
        projected_card = candidate.get("final_idea_card")
        if not isinstance(projected_card, dict):
            result["status"] = "blocked"
            result["reason"] = f"Gate1 candidate projection has no Final Card for {candidate_id}"
            return result
        projected_card["real_world_significance"] = value
        result["projection_updated_candidate_ids"].append(candidate_id)

    try:
        _atomic_write_json(cards_path, cards_payload)
        _atomic_write_json(directions_path, directions_payload)
        result["status"] = "migrated"
        result["migrated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(receipt_path, result)
    except OSError as exc:
        # The original old-schema files stay intact unless their corresponding
        # atomic replace succeeds.  Readiness will remain blocked and route to
        # the ordinary repair flow rather than treating a failed migration as
        # a usable Card.
        result["status"] = "write_failed"
        result["reason"] = str(exc)
    return result


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json_object(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file() or path.stat().st_size <= 0:
        return None, f"T4 {label} artifact is missing: {path.as_posix()}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"T4 {label} artifact cannot be read: {exc}"
    if not isinstance(payload, dict):
        return None, f"T4 {label} artifact must be a JSON object"
    return payload, None


def _load_active_dossiers(
    store: T4ArtifactStore,
    candidate_ids: list[str],
) -> tuple[dict[str, CandidateDossier], str | None]:
    dossiers: dict[str, CandidateDossier] = {}
    candidate_root = store.path("ideation/candidates")
    for candidate_id in candidate_ids:
        matches = sorted(candidate_root.glob(f"{candidate_id}.v*.json"))
        if not matches:
            return {}, f"T4 Final Card readiness cannot find the active Candidate dossier for {candidate_id}"
        try:
            dossier = store.read_model(matches[-1].relative_to(store.workspace_dir), CandidateDossier)
        except (TypeError, ValueError) as exc:
            return {}, f"T4 Final Card readiness cannot read the active Candidate dossier for {candidate_id}: {exc}"
        if dossier.candidate_id != candidate_id:
            return {}, f"T4 Candidate dossier identity mismatch for {candidate_id}"
        dossiers[candidate_id] = dossier
    return dossiers, None


def _validate_projected_final_cards(
    workspace: Path,
    cards_by_id: dict[str, FinalIdeaCardTranslation],
) -> str | None:
    payload, error = _read_json_object(workspace / "ideation/_candidate_directions.json", "Gate1 candidate projection")
    if error:
        return error
    assert payload is not None
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        return "T4 Gate1 candidate projection has no candidates list"
    projected = {
        str(item.get("id") or item.get("idea_id") or "").strip(): item
        for item in raw_candidates
        if isinstance(item, dict) and str(item.get("id") or item.get("idea_id") or "").strip()
    }
    for candidate_id, card in cards_by_id.items():
        candidate = projected.get(candidate_id)
        if candidate is None:
            return f"T4 Gate1 candidate projection is missing Portfolio Candidate {candidate_id}"
        raw_card = candidate.get("final_idea_card")
        if not isinstance(raw_card, dict):
            return f"T4 Gate1 candidate projection is missing the completed Final Card for {candidate_id}"
        try:
            projected_card = model_validate(FinalIdeaCardTranslation, raw_card)
        except (TypeError, ValueError) as exc:
            return f"T4 Gate1 projected Final Card is invalid for {candidate_id}: {exc}"
        if model_dump(projected_card, mode="json") != model_dump(card, mode="json"):
            return f"T4 Gate1 projected Final Card is stale for {candidate_id}"
    return None


__all__ = [
    "PORTFOLIO_CARDS_PATH",
    "PORTFOLIO_CARDS_SEMANTICS",
    "PORTFOLIO_PATH",
    "FINAL_CARD_COMPATIBILITY_RECEIPT_PATH",
    "FINAL_CARD_PROFILE_ARCHIVE_DIR",
    "FINAL_CARD_PROFILE_REFRESH_RECEIPT_PATH",
    "archive_final_card_profile_mismatch",
    "migrate_legacy_final_card_schema",
    "portfolio_candidate_ids",
    "validate_t4_portfolio_final_cards",
]
