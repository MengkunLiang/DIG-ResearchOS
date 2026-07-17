"""Lossless recovery helpers for native T4 role responses.

This boundary repairs presentation differences only. It never creates
scientific content, evidence, scores, provenance, or lineage. Typed T4 models
and controller contracts remain the semantic safety boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import re
from typing import Any, Optional


class T4ValidationStatus(str, Enum):
    """Four-way outcome for native T4 LLM integration boundaries."""

    VALID = "valid"
    REPAIRABLE = "repairable"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class T4ValidationIssue:
    code: str
    severity: str
    field: str
    message: str
    repair_strategy: str
    blocking: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class T4ValidationResult:
    """A structured result that does not collapse usable content to failure."""

    status: T4ValidationStatus
    issues: list[T4ValidationIssue] = field(default_factory=list)
    usable_content: bool = False
    recommended_action: str = "block"
    payload: Optional[dict[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "issues": [issue.as_dict() for issue in self.issues],
            "usable_content": self.usable_content,
            "recommended_action": self.recommended_action,
        }


_FENCE_RE = re.compile(r"\x60\x60\x60(?:json|yaml|yml)?\s*(.*?)\x60\x60\x60", re.IGNORECASE | re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(?=\s*[}\]])")
_FIELD_ALIASES = {
    "candidateId": "candidate_id",
    "opportunityId": "opportunity_id",
    "scoringBatchId": "scoring_batch_id",
    "sourceRefs": "source_refs",
    "parentIds": "parent_ids",
    "planId": "plan_id",
    "schemaVersion": "schema_version",
}


def recover_t4_mapping(
    raw_response: str,
    *,
    array_field: str | None = None,
) -> T4ValidationResult:
    """Recover a JSON/YAML mapping from a common LLM envelope losslessly.

    An array is recoverable only when the role caller provides its expected
    envelope key. This avoids silently choosing one item from an ambiguous
    top-level list.
    """

    raw = str(raw_response or "")
    attempts: list[tuple[str, str]] = [("raw_json", raw.strip())]
    if array_field:
        array = _parse_array(raw.strip())
        if array is not None:
            return T4ValidationResult(
                status=T4ValidationStatus.REPAIRABLE,
                issues=[
                    T4ValidationIssue(
                        code="t4_top_level_array_wrapped",
                        severity="warning",
                        field="$",
                        message="Wrapped a role-specific top-level array in its expected envelope.",
                        repair_strategy="deterministic_normalization",
                    )
                ],
                usable_content=True,
                recommended_action="normalize",
                payload={array_field: array},
            )
    attempts.extend(("markdown_fence", item.strip()) for item in _FENCE_RE.findall(raw))
    # An array or scalar is not a role envelope. In particular, extracting the
    # first object from a JSON array would silently discard siblings and let a
    # role return the wrong top-level shape. Only prose-wrapped object output
    # is eligible for this narrow recovery path.
    embedded = None if raw.lstrip().startswith(("[", "\"")) else _extract_braced_mapping(raw)
    if embedded and embedded not in {item for _kind, item in attempts}:
        attempts.append(("embedded_mapping", embedded))

    for kind, candidate in attempts:
        parsed_result = _parse_mapping(candidate)
        if parsed_result is None:
            continue
        parsed, parser_kind = parsed_result
        payload, repairs = normalize_t4_payload(parsed)
        if array_field and array_field not in payload and _looks_like_single_item(payload, array_field):
            payload = {array_field: [payload]}
            repairs.append(
                (
                    "t4_single_item_wrapped",
                    array_field,
                    "Wrapped one role-specific item in its expected collection.",
                )
            )
        issues = [
            T4ValidationIssue(
                code=code,
                severity="warning",
                field=field_name,
                message=message,
                repair_strategy="deterministic_normalization",
            )
            for code, field_name, message in repairs
        ]
        if kind != "raw_json" or parser_kind != "json":
            issues.insert(
                0,
                T4ValidationIssue(
                    code="t4_tolerant_extraction" if kind != "raw_json" else "t4_tolerant_parse",
                    severity="warning",
                    field="$",
                    message="Recovered a structured response without changing scientific semantics.",
                    repair_strategy="tolerant_extraction" if kind != "raw_json" else "deterministic_normalization",
                ),
            )
        return T4ValidationResult(
            status=T4ValidationStatus.REPAIRABLE if issues else T4ValidationStatus.VALID,
            issues=issues,
            usable_content=True,
            recommended_action="normalize" if issues else "continue",
            payload=payload,
        )

    return T4ValidationResult(
        status=T4ValidationStatus.BLOCKED,
        issues=[
            T4ValidationIssue(
                code="t4_unparseable_role_response",
                severity="error",
                field="$",
                message="No safely parseable JSON or YAML mapping was found in the model response.",
                repair_strategy="schema_only_repair",
                blocking=True,
            )
        ],
        usable_content=False,
        recommended_action="repair",
    )


def normalize_t4_payload(value: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
    """Normalize known aliases and null-like values without filling fields."""

    repairs: list[tuple[str, str, str]] = []

    def visit(item: Any, path: str) -> Any:
        if isinstance(item, list):
            return [visit(child, path) for child in item]
        if not isinstance(item, dict):
            # "none" is a legitimate value in several T4 enums, for example
            # a composition's assumption conflict. Only the JSON literal
            # spelling is safe to normalize at this generic boundary.
            if isinstance(item, str) and item.strip().casefold() == "null":
                repairs.append(("t4_null_literal", path, "Normalized the JSON null literal string to null."))
                return None
            return item
        normalized: dict[str, Any] = {}
        for key, child in item.items():
            canonical = _FIELD_ALIASES.get(str(key), str(key))
            child_path = path + "." + canonical if path else canonical
            if canonical != key:
                repairs.append(("t4_field_alias", child_path, "Normalized a known field alias."))
            if canonical in normalized and normalized[canonical] != child:
                normalized[str(key)] = visit(child, str(key))
                repairs.append(("t4_conflicting_alias_preserved", child_path, "Preserved conflicting values for semantic validation."))
            else:
                normalized[canonical] = visit(child, child_path)
        return normalized

    return visit(value, ""), repairs


def _parse_mapping(candidate: str) -> Optional[tuple[dict[str, Any], str]]:
    if not candidate:
        return None
    for index, value in enumerate((candidate, _TRAILING_COMMA_RE.sub("", candidate))):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, "json" if index == 0 else "json_trailing_comma"
    # PyYAML accepts prose such as "Here is: {\"ok\": true}" as a mapping.
    # Treat YAML as a deliberate structured envelope only. Otherwise the
    # braced-object extractor can recover the unambiguous JSON payload.
    stripped = candidate.strip()
    looks_like_yaml = (
        "\n" in stripped
        and "{" not in stripped
        and "[" not in stripped
        and bool(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped))
    )
    if not looks_like_yaml:
        return None
    try:
        import yaml

        parsed = yaml.safe_load(candidate)
    except Exception:
        return None
    return (parsed, "yaml") if isinstance(parsed, dict) else None


def _parse_array(candidate: str) -> Optional[list[Any]]:
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def _looks_like_single_item(payload: dict[str, Any], array_field: str) -> bool:
    identity_fields = {
        "seeds": {"candidate_id", "id", "one_line_thesis", "core_thesis", "thesis"},
        "opportunities": {"opportunity_id", "id", "question"},
        "scores": {"candidate_id", "scoring_batch_id"},
        "children": {"candidate_id", "genome", "lineage"},
        "decisions": {"pair_id", "parent_ids"},
        "cards": {"candidate_id", "core_thesis"},
        "repairs": {"candidate_id", "rationales", "compatibility_rationales"},
    }
    return bool(identity_fields.get(array_field, set()) & set(payload))


def _extract_braced_mapping(value: str) -> Optional[str]:
    start = value.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(value[start:], start=start):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return None
