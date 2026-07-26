from __future__ import annotations

from researchos.ideation.final_card_diagnostics import (
    FinalCardFailureKind,
    classify_final_card_exception,
)
from researchos.ideation.llm_roles import _normalize_candidate_dossier_payload
from researchos.runtime.errors import LLMProviderError, RecoverableRuntimePause
from researchos.runtime.orchestrator import AgentRunner


def test_generic_bad_request_is_not_reported_as_a_context_limit() -> None:
    error = LLMProviderError(
        "Configured model test is unavailable. Errors: "
        "[BadRequestError('unsupported parameter: response_format')]"
    )

    assert AgentRunner._provider_error_category(error) == "request_schema"
    assert "不是已确认的上下文错误" in AgentRunner._public_provider_error_message(error)


def test_explicit_context_limit_has_its_own_public_category() -> None:
    error = LLMProviderError("BadRequestError('context_length_exceeded: maximum context length is 128000')")

    assert AgentRunner._provider_error_category(error) == "context_limit"
    assert "上下文长度" in AgentRunner._public_provider_error_message(error)


def test_final_card_classification_preserves_bad_request_distinction() -> None:
    provider = LLMProviderError("BadRequestError('unsupported parameter: response_format')")
    try:
        raise RecoverableRuntimePause("模型拒绝了本次请求（请求格式、模型能力或内容策略）；这不是已确认的上下文错误。") from provider
    except RecoverableRuntimePause as pause:
        diagnostic = classify_final_card_exception(
            pause,
            stage="initial_generation",
            candidate_ids=["C1"],
        )

    assert diagnostic.kind == FinalCardFailureKind.LLM_REQUEST_REJECTED
    assert diagnostic.repair_scheduled is False
    assert "does not prove a context overflow" in diagnostic.message


def test_integral_dotted_candidate_versions_are_losslessly_normalized() -> None:
    normalized = _normalize_candidate_dossier_payload(
        {"version": "1.0.0", "genome": {"version": "2.0"}}
    )

    assert normalized["version"] == 1
    assert normalized["genome"]["version"] == 2


def test_non_integral_or_non_numeric_versions_stay_for_strict_validation() -> None:
    normalized = _normalize_candidate_dossier_payload(
        {"version": "1.2", "genome": {"version": "v2"}}
    )

    assert normalized["version"] == "1.2"
    assert normalized["genome"]["version"] == "v2"
