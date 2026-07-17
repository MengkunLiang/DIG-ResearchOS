"""Failure taxonomy for the LLM-authored T4 Final Idea Card boundary.

The Final Card is intentionally stricter than exploratory Population creation:
it is the first surface on which a researcher is asked to make a selection.
That does not make a missing display explanation a Population failure.  This
module keeps the failure reason machine-readable so the runtime can retry the
Card Compiler, or state the exact structural prerequisite that must be repaired
before a retry is safe.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable

from pydantic import ValidationError

from ..runtime.errors import LLMProviderError, RecoverableRuntimePause
from .errors import T4RoleResponseFormatError


class FinalCardFailureKind(str, Enum):
    """Concrete causes that must not be collapsed into a generic Gate1 error."""

    LLM_TIMEOUT = "llm_timeout"
    LLM_PROVIDER_FAILURE = "llm_provider_failure"
    LLM_CONFIGURATION_FAILURE = "llm_configuration_failure"
    LLM_EMPTY_RESPONSE = "llm_empty_response"
    RESPONSE_PARSE_FAILURE = "llm_response_parse_failure"
    SCHEMA_MISMATCH = "llm_card_schema_mismatch"
    COVERAGE_MISMATCH = "llm_card_coverage_mismatch"
    IMMUTABLE_FIELD_MISMATCH = "llm_card_immutable_field_mismatch"
    PROFILE_MISMATCH = "llm_card_profile_mismatch"
    SOURCE_DATA_MISSING = "source_data_missing"
    STALE_POPULATION_OR_CARD = "stale_population_or_card"
    UNEXPECTED = "unexpected_final_card_failure"


@dataclass(frozen=True)
class FinalCardFailureDiagnostic:
    """A bounded, persistable explanation of one Final Card failure.

    ``repair_scheduled`` means that retrying the Final Card Compiler is the
    next retained recovery path.  For a source or stale-state error, the
    schedule has an explicit prerequisite rather than pretending an LLM can
    reconstruct canonical state that is absent or inconsistent.
    """

    kind: FinalCardFailureKind
    stage: str
    candidate_ids: tuple[str, ...]
    message: str
    cause_type: str
    cause_message: str
    repair_scheduled: bool
    recovery_action: str
    repair_prerequisite: str = ""
    response_excerpt: str = ""
    prior_failure: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        result["candidate_ids"] = list(self.candidate_ids)
        return result


class FinalCardCompilationFailure(ValueError):
    """Typed compiler failure that retains its recovery classification."""

    def __init__(self, diagnostic: FinalCardFailureDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"Final Card {diagnostic.kind.value} during {diagnostic.stage}: {diagnostic.message}"
        )


def classify_final_card_exception(
    error: BaseException,
    *,
    stage: str,
    candidate_ids: Iterable[str] = (),
    prior_failure: FinalCardFailureDiagnostic | None = None,
) -> FinalCardFailureDiagnostic:
    """Classify one failure without changing its source content or state.

    Exception class is preferred over text.  The compact text checks cover
    persisted-artifact errors produced by older compatibility layers where the
    original exception type is no longer available.
    """

    if isinstance(error, FinalCardCompilationFailure):
        return error.diagnostic

    chain = tuple(_exception_chain(error))
    cause = next((item for item in chain if isinstance(item, T4RoleResponseFormatError)), None)
    if isinstance(cause, T4RoleResponseFormatError):
        return _diagnostic(
            kind=FinalCardFailureKind.RESPONSE_PARSE_FAILURE,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The model returned content, but it did not contain a safely parseable card envelope.",
            recovery_action="invoke_final_card_semantic_repair",
            response_excerpt=cause.response_excerpt,
            prior_failure=prior_failure,
        )

    provider = next((item for item in chain if isinstance(item, LLMProviderError)), None)
    pause = next((item for item in chain if isinstance(item, RecoverableRuntimePause)), None)
    provider_text = _normalized_text(provider or pause or error)
    if provider is not None or pause is not None:
        if _contains_provider_configuration_failure(provider_text):
            return _diagnostic(
                kind=FinalCardFailureKind.LLM_CONFIGURATION_FAILURE,
                stage=stage,
                candidate_ids=candidate_ids,
                error=error,
                message="The Final Card LLM request was rejected by provider configuration or authorization, so another card call cannot repair it yet.",
                recovery_action="ask_human_to_fix_provider_configuration_then_retry_final_card_compiler",
                repair_prerequisite="Fix the configured model, credential, permission, or context setting before retrying the Card Compiler.",
                repair_scheduled=False,
                prior_failure=prior_failure,
            )
        if _contains_timeout(provider_text):
            return _diagnostic(
                kind=FinalCardFailureKind.LLM_TIMEOUT,
                stage=stage,
                candidate_ids=candidate_ids,
                error=error,
                message="The Final Card LLM call timed out before it produced a complete response.",
                recovery_action="retry_final_card_compiler_after_provider_recovery",
                prior_failure=prior_failure,
            )
        if _contains_empty_response(provider_text):
            return _diagnostic(
                kind=FinalCardFailureKind.LLM_EMPTY_RESPONSE,
                stage=stage,
                candidate_ids=candidate_ids,
                error=error,
                message="The Final Card LLM call completed without usable response content.",
                recovery_action="retry_final_card_compiler",
                prior_failure=prior_failure,
            )
        return _diagnostic(
            kind=FinalCardFailureKind.LLM_PROVIDER_FAILURE,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The Final Card LLM provider was unavailable before the card explanation could be compiled.",
            recovery_action="retry_final_card_compiler_after_provider_recovery",
            prior_failure=prior_failure,
        )

    text = _normalized_text(error)
    lower = text.casefold()
    if _looks_like_stale_state(lower):
        return _diagnostic(
            kind=FinalCardFailureKind.STALE_POPULATION_OR_CARD,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The saved Final Card or Portfolio does not match the current active Population.",
            recovery_action="reload_current_population_then_retry_final_card_compiler",
            repair_prerequisite="Refresh the active Population and Portfolio identity before invoking the Card Compiler.",
            prior_failure=prior_failure,
        )
    if _looks_like_missing_source(lower):
        return _diagnostic(
            kind=FinalCardFailureKind.SOURCE_DATA_MISSING,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The Final Card Compiler is missing canonical Candidate, Portfolio, or run-configuration input.",
            recovery_action="restore_source_data_then_retry_final_card_compiler",
            repair_prerequisite="Restore the referenced native T4 artifact; do not derive card prose from a stale projection.",
            prior_failure=prior_failure,
        )
    if _looks_like_coverage_mismatch(lower):
        return _diagnostic(
            kind=FinalCardFailureKind.COVERAGE_MISMATCH,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The LLM card set did not cover exactly the active Portfolio Candidates.",
            recovery_action="retry_final_card_compiler_with_exact_portfolio",
            prior_failure=prior_failure,
        )
    if _looks_like_profile_mismatch(lower):
        return _diagnostic(
            kind=FinalCardFailureKind.PROFILE_MISMATCH,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The saved Final Card deck was written for a different publication orientation and must be recompiled for the current profile.",
            recovery_action="preserve_prior_profile_deck_then_recompile_final_cards_for_current_profile",
            prior_failure=prior_failure,
        )
    if _looks_like_immutable_mismatch(lower):
        return _diagnostic(
            kind=FinalCardFailureKind.IMMUTABLE_FIELD_MISMATCH,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The LLM card changed an immutable Candidate identity or membership echo.",
            recovery_action="retry_final_card_compiler_with_canonical_candidate_echoes",
            prior_failure=prior_failure,
        )
    if isinstance(error, (ValidationError, TypeError, ValueError)) or "validation error" in lower:
        return _diagnostic(
            kind=FinalCardFailureKind.SCHEMA_MISMATCH,
            stage=stage,
            candidate_ids=candidate_ids,
            error=error,
            message="The LLM returned a card whose required explanation fields or field shape did not match the Final Card contract.",
            recovery_action="invoke_final_card_semantic_repair",
            prior_failure=prior_failure,
        )
    return _diagnostic(
        kind=FinalCardFailureKind.UNEXPECTED,
        stage=stage,
        candidate_ids=candidate_ids,
        error=error,
        message="The Final Card Compiler stopped for an unexpected reason; the Population remains preserved and the diagnostic should be inspected before retrying.",
        recovery_action="inspect_final_card_diagnostic_then_retry_compiler",
        prior_failure=prior_failure,
    )


def classify_final_card_readiness_error(
    error: str | None,
    *,
    candidate_ids: Iterable[str] = (),
) -> FinalCardFailureDiagnostic:
    """Classify a persisted readiness result before deciding how to resume."""

    return classify_final_card_exception(
        ValueError(str(error or "Final Card readiness did not provide a reason")),
        stage="readiness_validation",
        candidate_ids=candidate_ids,
    )


def _diagnostic(
    *,
    kind: FinalCardFailureKind,
    stage: str,
    candidate_ids: Iterable[str],
    error: BaseException,
    message: str,
    recovery_action: str,
    repair_prerequisite: str = "",
    response_excerpt: str = "",
    repair_scheduled: bool = True,
    prior_failure: FinalCardFailureDiagnostic | None = None,
) -> FinalCardFailureDiagnostic:
    return FinalCardFailureDiagnostic(
        kind=kind,
        stage=stage,
        candidate_ids=tuple(str(item) for item in candidate_ids if str(item).strip()),
        message=message,
        cause_type=type(error).__name__,
        cause_message=_normalized_text(error)[:1600],
        repair_scheduled=repair_scheduled,
        recovery_action=recovery_action,
        repair_prerequisite=repair_prerequisite,
        response_excerpt=" ".join(str(response_excerpt or "").split())[:4000],
        prior_failure=prior_failure.as_dict() if prior_failure is not None else None,
    )


def _exception_chain(error: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, BaseException) else None


def _normalized_text(error: BaseException | object) -> str:
    return " ".join(str(error or "").split())


def _contains_timeout(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in ("timeout", "timed out", "readtimeout", "connecttimeout", "gateway timeout", "超时"))


def _contains_empty_response(text: str) -> bool:
    lowered = text.casefold()
    return "empty response" in lowered or "empty reply" in lowered or "空响应" in lowered


def _contains_provider_configuration_failure(text: str) -> bool:
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in (
            "authentication",
            "invalid_api_key",
            "invalid api key",
            "unauthorized",
            "permission denied",
            "permissiondenied",
            "context_length",
            "context window",
            "badrequest",
            "bad request",
        )
    )


def _looks_like_stale_state(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "different population",
            "belongs to a different population",
            "does not belong to the active",
            "portfolio and active population artifacts disagree",
            "projected final card is stale",
            "final card is stale",
            "state and active population identifiers disagree",
        )
    )


def _looks_like_missing_source(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "no supplied portfolio candidate",
            "no visible candidate",
            "artifact is missing",
            "cannot find the active candidate dossier",
            "cannot read the active candidate dossier",
            "native state required",
            "missing canonical candidate",
            "candidate dossier",
            "portfolio references candidates outside",
        )
    )


def _looks_like_coverage_mismatch(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "must cover exactly the portfolio",
            "coverage does not match",
            "missing=",
            "extra=",
            "duplicate candidate id",
        )
    )


def _looks_like_immutable_mismatch(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "changed the core thesis",
            "changed contribution membership",
            "changed hypothesis membership",
        )
    )


def _looks_like_profile_mismatch(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "deck profile does not match",
            "card profile does not match",
            "profile mismatch",
            "profile does not match the current run config",
        )
    )


__all__ = [
    "FinalCardCompilationFailure",
    "FinalCardFailureDiagnostic",
    "FinalCardFailureKind",
    "classify_final_card_exception",
    "classify_final_card_readiness_error",
]
