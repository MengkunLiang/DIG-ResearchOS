from __future__ import annotations

"""Mechanism tuple extraction and comparison tools for T4.5 novelty auditing.

Design:
- extract_mechanism_tuple: persistence/normalization helper. The agent extracts
  the mechanism via LLM reasoning, optionally supplies its own normalized labels,
  then calls this tool to save the tuple.
- compare_mechanism_tuples: deterministic similarity hint. It must not be treated
  as a final novelty verdict; the Novelty Auditor LLM makes the final call.
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..runtime.errors import ToolAccessDenied
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Evidence labels are intentionally generic and provenance-preserving. The tool
# does not maintain an input-signal ontology; any domain-specific normalization
# must be supplied by the Novelty Auditor LLM.

_EVIDENCE_TYPE_NORMALIZE: dict[str, str] = {
    "theory": "theory",
    "theoretical": "theory",
    "theoretically": "theory",
    "simulation": "simulation",
    "simulated": "simulation",
    "empirical": "empirical",
    "experiment": "empirical",
    "experimental": "empirical",
    "ablation": "empirical",
    "ablation_supported": "empirical",
    "theoretically_justified": "theory",
    "empirical_correlation": "empirical_correlation",
    "abstract_claim_hint": "abstract_claim_hint",
    "none": "none",
    "no evidence": "none",
    "untested": "claimed_untested",
    "claimed": "claimed_untested",
    "claimed_untested": "claimed_untested",
}


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------

class ExtractMechanismTupleParams(BaseModel):
    mechanism: str = Field(
        ...,
        min_length=1,
        description=(
            "The causal claim extracted by the agent. "
            "Single sentence: 'X causes Y' or 'X is responsible for Y'. "
            "NOT a method description like 'adding X improves Y'."
        ),
    )
    claimed_effect: str = Field(
        ...,
        min_length=1,
        description="What improves if the mechanism is correct.",
    )
    input_signal: str = Field(
        ...,
        min_length=1,
        description=(
            "What the mechanism operates on. Free text from the LLM; the tool "
            "does not constrain this to a built-in taxonomy."
        ),
    )
    normalized_input_signal: str | None = Field(
        None,
        description=(
            "Optional LLM-provided normalized input signal. Prefer this when the domain "
            "needs labels outside the fallback heuristic vocabulary."
        ),
    )
    evidence_type: str = Field(
        "none",
        description=(
            "How the mechanism is supported. Free text — the tool will normalize to: "
            "theory | simulation | empirical | empirical_correlation | "
            "claimed_untested | abstract_claim_hint | none"
        ),
    )
    normalized_evidence_type: str | None = Field(
        None,
        description="Optional LLM-provided normalized evidence type.",
    )
    source_type: Literal["hypothesis", "paper_abstract"] = Field(
        ...,
        description="Whether this tuple comes from a research hypothesis or a paper abstract.",
    )
    source_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier (e.g., H1, arxiv_2301.12345).",
    )
    output_dir: str = Field(
        "ideation/_mechanism_tuples",
        description="Relative workspace directory to save the tuple JSON.",
    )


class CompareMechanismTuplesParams(BaseModel):
    tuple_a_path: str = Field(
        ...,
        min_length=1,
        description="Relative workspace path to the first mechanism tuple JSON file.",
    )
    tuple_b_path: str = Field(
        ...,
        min_length=1,
        description="Relative workspace path to the second mechanism tuple JSON file.",
    )
    llm_assessment: dict[str, Any] | None = Field(
        None,
        description=(
            "Optional LLM assessment to attach to the result, e.g. input_relation, "
            "mechanism_relation, final_verdict, confidence, rationale. The tool's own "
            "result remains a hint, not an authoritative verdict."
        ),
    )


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_input_signal(raw: str) -> str:
    """Return raw free text as the fallback input-signal label."""
    return raw.strip() or "unspecified"


def _normalize_evidence_type(raw: str) -> str:
    """Return a fallback evidence-type hint without erasing uncertainty."""
    lowered = raw.lower().strip()
    for key, normalized in _EVIDENCE_TYPE_NORMALIZE.items():
        if key in lowered:
            return normalized
    return "none"


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase alphanumeric tokens."""
    return {t for t in re.split(r"[^a-zA-Z0-9]+", text.lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _are_related_signals(sig_a: str, sig_b: str) -> bool:
    if sig_a == sig_b:
        return True
    tokens_a = _tokenize(sig_a)
    tokens_b = _tokenize(sig_b)
    return _jaccard(tokens_a, tokens_b) >= 0.35


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class ExtractMechanismTupleTool(Tool):
    name = "extract_mechanism_tuple"
    description = (
        "Normalize and save a mechanism tuple (input_signal, mechanism, claimed_effect, evidence_type) "
        "to a JSON file. The agent should extract these fields from hypothesis text or paper abstract "
        "via its own analysis, then call this tool to normalize and persist the result."
    )
    parameters_schema = ExtractMechanismTupleParams
    timeout_seconds = 15.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ExtractMechanismTupleParams(**kwargs)

        normalized_input_signal = (params.normalized_input_signal or "").strip() or _normalize_input_signal(params.input_signal)
        normalized_evidence_type = (params.normalized_evidence_type or "").strip() or _normalize_evidence_type(params.evidence_type)

        tuple_data = {
            "source_id": params.source_id,
            "source_type": params.source_type,
            "input_signal_raw": params.input_signal,
            "input_signal": normalized_input_signal,
            "input_signal_normalization_source": "llm" if params.normalized_input_signal else "heuristic_hint",
            "mechanism": params.mechanism.strip(),
            "claimed_effect": params.claimed_effect.strip(),
            "evidence_type_raw": params.evidence_type,
            "evidence_type": normalized_evidence_type,
            "evidence_type_normalization_source": "llm" if params.normalized_evidence_type else "heuristic_hint",
        }

        # Save to file
        try:
            output_dir = self.policy.resolve_write(params.output_dir)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", params.source_id)
        output_path = output_dir / f"{safe_id}.json"
        output_path.write_text(
            json.dumps(tuple_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        return ToolResult(
            ok=True,
            content=(
                f"Mechanism tuple saved to {output_path.relative_to(self.policy.workspace_dir)}\n"
                f"  input_signal: {params.input_signal} → {normalized_input_signal} "
                f"({tuple_data['input_signal_normalization_source']})\n"
                f"  mechanism: {tuple_data['mechanism'][:80]}\n"
                f"  claimed_effect: {tuple_data['claimed_effect'][:80]}\n"
                f"  evidence_type: {params.evidence_type} → {normalized_evidence_type}"
            ),
            data=tuple_data,
        )


class CompareMechanismTuplesTool(Tool):
    name = "compare_mechanism_tuples"
    description = (
        "Compare two mechanism tuples (from JSON files) and return a mechanical "
        "similarity hint. This is not a final novelty verdict; no LLM call is made."
    )
    parameters_schema = CompareMechanismTuplesParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = CompareMechanismTuplesParams(**kwargs)

        try:
            path_a = self.policy.resolve_read(params.tuple_a_path)
            path_b = self.policy.resolve_read(params.tuple_b_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        if not path_a.exists():
            return ToolResult(ok=False, content=f"Tuple file not found: {params.tuple_a_path}", error="not_found")
        if not path_b.exists():
            return ToolResult(ok=False, content=f"Tuple file not found: {params.tuple_b_path}", error="not_found")

        try:
            tuple_a = json.loads(path_a.read_text(encoding="utf-8"))
            tuple_b = json.loads(path_b.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return ToolResult(ok=False, content=f"Failed to read tuple: {exc}", error="parse_error")

        result = compare_mechanism_tuples(tuple_a, tuple_b)
        if params.llm_assessment:
            result["llm_assessment"] = params.llm_assessment

        return ToolResult(
            ok=True,
            content=(
                f"Heuristic hint: {result['heuristic_verdict']} (confidence: {result['heuristic_confidence']})\n"
                f"  input_match: {result['input_match']}\n"
                f"  mechanism_similarity_hint: {result['mechanism_similarity_hint']}\n"
                f"  reasoning: {result['reasoning']}\n"
                "  final verdict must be made by the Novelty Auditor LLM."
            ),
            data=result,
        )


def compare_mechanism_tuples(tuple_a: dict, tuple_b: dict) -> dict:
    """Pure-code comparison of two mechanism tuples.

    Returns a dict with keys:
        input_match: same | related | different
        mechanism_similarity_hint: same | related | different
        heuristic_verdict: possible_true_collision | possible_mechanism_collision |
            possible_explanatory_competition | likely_distinct
        heuristic_confidence: high | medium | low
        reasoning: str
    """

    sig_a = tuple_a.get("input_signal", "other")
    sig_b = tuple_b.get("input_signal", "other")
    mech_a = tuple_a.get("mechanism", "")
    mech_b = tuple_b.get("mechanism", "")

    # Input signal match
    if sig_a == sig_b:
        input_match = "same"
    elif _are_related_signals(sig_a, sig_b):
        input_match = "related"
    else:
        input_match = "different"

    input_jaccard = _jaccard(_tokenize(sig_a), _tokenize(sig_b))

    # Mechanism similarity (Jaccard on tokens)
    tokens_a = _tokenize(mech_a)
    tokens_b = _tokenize(mech_b)
    jaccard = _jaccard(tokens_a, tokens_b)

    if jaccard >= 0.6:
        mech_hint = "same"
    elif jaccard >= 0.3:
        mech_hint = "related"
    else:
        mech_hint = "different"

    # Verdict from matrix
    verdict, confidence, reasoning = _compute_verdict(
        input_match, mech_hint, jaccard, sig_a, sig_b, mech_a, mech_b
    )

    return {
        "input_match": input_match,
        "input_similarity_hint": round(input_jaccard, 3),
        "mechanism_similarity_hint": mech_hint,
        "mechanism_jaccard": round(jaccard, 3),
        "heuristic_verdict": verdict,
        "heuristic_confidence": confidence,
        "verdict": verdict,
        "confidence": confidence,
        "requires_llm_judgment": True,
        "reasoning": reasoning,
        "source_a": tuple_a.get("source_id", "?"),
        "source_b": tuple_b.get("source_id", "?"),
    }


def _compute_verdict(
    input_match: str,
    mech_hint: str,
    jaccard: float,
    sig_a: str,
    sig_b: str,
    mech_a: str,
    mech_b: str,
) -> tuple[str, str, str]:
    """Compute verdict, confidence, and reasoning from comparison dimensions."""

    # same input + same mechanism → possible true collision
    if input_match == "same" and mech_hint == "same":
        conf = "high" if jaccard >= 0.7 else "medium"
        return (
            "possible_true_collision",
            conf,
            f"Both use very similar LLM-provided input-signal labels ({sig_a}) and highly similar mechanisms (Jaccard={jaccard:.2f}). "
            f"This is a high-priority collision hint, but the LLM must verify context, novelty, and contribution boundaries.",
        )

    # different/related input + same mechanism → possible mechanism collision
    if input_match != "same" and mech_hint == "same":
        conf = "medium" if input_match == "related" else "medium"
        return (
            "possible_mechanism_collision",
            conf,
            f"Similar mechanism text (Jaccard={jaccard:.2f}) but different/related LLM-provided input-signal labels "
            f"({sig_a} vs {sig_b}). The LLM should decide whether this is transfer, baseline, or true overlap.",
        )

    # same input + different mechanism → possible explanatory competition
    if input_match == "same" and mech_hint == "different":
        return (
            "possible_explanatory_competition",
            "medium",
            f"Both use very similar LLM-provided input-signal labels ({sig_a}) but propose different mechanisms. "
            f"This may be a useful contrast, but the LLM must inspect the paper claims.",
        )

    # same input + related mechanism → possible mechanism collision (borderline)
    if input_match == "same" and mech_hint == "related":
        return (
            "possible_mechanism_collision",
            "medium",
            f"Same LLM-provided input-signal label ({sig_a}) with related mechanisms (Jaccard={jaccard:.2f}). "
            f"Needs LLM inspection to determine if truly distinct.",
        )

    # related input + related mechanism → possible mechanism collision (low confidence)
    if input_match == "related" and mech_hint == "related":
        return (
            "possible_mechanism_collision",
            "low",
            f"Related input-signal labels ({sig_a} ~ {sig_b}) and related mechanisms "
            f"(Jaccard={jaccard:.2f}). Possible overlap but uncertain.",
        )

    # Everything else → likely distinct
    return (
        "likely_distinct",
        "low",
        f"Input-signal labels differ ({sig_a} vs {sig_b}) and mechanisms are distinct "
        f"(Jaccard={jaccard:.2f}). This is a low collision hint, not proof of novelty.",
    )
