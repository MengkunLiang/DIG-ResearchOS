#!/usr/bin/env python3
"""Validate a ResearchOS handoff pack without third-party dependencies.

The structural validator implements the JSON Schema keywords used by the bundled
handoff schema. A second pass enforces ResearchOS cross-reference and status
semantics that JSON Schema alone cannot express cleanly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str


def pointer(path: Iterable[Any]) -> str:
    parts = []
    for item in path:
        parts.append(str(item).replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts) if parts else "/"


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


class SchemaValidator:
    """Small evaluator for the schema keywords used by this skill."""

    def __init__(self, root_schema: dict):
        self.root = root_schema
        self.findings: list[Finding] = []

    def error(self, path: list[Any], code: str, message: str) -> None:
        self.findings.append(Finding("error", code, pointer(path), message))

    def resolve_ref(self, ref: str) -> dict:
        if not ref.startswith("#/"):
            raise ValueError(f"only local schema references are supported: {ref}")
        node: Any = self.root
        for raw in ref[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            node = node[token]
        if not isinstance(node, dict):
            raise ValueError(f"schema reference does not resolve to an object: {ref}")
        return node

    def validate(self, value: Any, schema: dict | None = None, path: list[Any] | None = None) -> list[Finding]:
        schema = self.root if schema is None else schema
        path = [] if path is None else path
        if "$ref" in schema:
            return self.validate(value, self.resolve_ref(schema["$ref"]), path)

        for branch in schema.get("allOf", []):
            self.validate(value, branch, path)

        if "anyOf" in schema:
            matches = self._branch_matches(value, schema["anyOf"], path)
            if matches == 0:
                self.error(path, "schema.anyOf", "value does not match any allowed schema")
            return self.findings

        if "oneOf" in schema:
            matches = self._branch_matches(value, schema["oneOf"], path)
            if matches != 1:
                self.error(path, "schema.oneOf", f"value matches {matches} branches; expected exactly one")
            return self.findings

        if "const" in schema and value != schema["const"]:
            self.error(path, "schema.const", f"expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            self.error(path, "schema.enum", f"value {value!r} is not in the allowed enumeration")

        expected = schema.get("type")
        if expected is not None:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(json_type_matches(value, choice) for choice in choices):
                self.error(path, "schema.type", f"expected type {choices}, got {type(value).__name__}")
                return self.findings

        if isinstance(value, dict):
            self._validate_object(value, schema, path)
        elif isinstance(value, list):
            self._validate_array(value, schema, path)
        elif isinstance(value, str):
            self._validate_string(value, schema, path)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            self._validate_number(value, schema, path)
        return self.findings

    def _branch_matches(self, value: Any, branches: list[dict], path: list[Any]) -> int:
        matches = 0
        for branch in branches:
            probe = SchemaValidator(self.root)
            probe.validate(value, branch, path)
            matches += not probe.findings
        return matches

    def _validate_object(self, value: dict, schema: dict, path: list[Any]) -> None:
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                self.error(path, "schema.required", f"missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    self.error(path + [key], "schema.additionalProperties", "unexpected property")
        for key, child_schema in properties.items():
            if key in value:
                self.validate(value[key], child_schema, path + [key])

    def _validate_array(self, value: list, schema: dict, path: list[Any]) -> None:
        if len(value) < schema.get("minItems", 0):
            self.error(path, "schema.minItems", f"expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            self.error(path, "schema.maxItems", f"expected at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = set()
            for index, item in enumerate(value):
                encoded = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if encoded in seen:
                    self.error(path + [index], "schema.uniqueItems", "duplicate array item")
                seen.add(encoded)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                self.validate(item, item_schema, path + [index])

    def _validate_string(self, value: str, schema: dict, path: list[Any]) -> None:
        if len(value) < schema.get("minLength", 0):
            self.error(path, "schema.minLength", f"expected at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            self.error(path, "schema.maxLength", f"expected at most {schema['maxLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            self.error(path, "schema.pattern", f"value does not match pattern {schema['pattern']!r}")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone is required")
            except ValueError:
                self.error(path, "schema.format", "value is not a valid RFC 3339 date-time")

    def _validate_number(self, value: float, schema: dict, path: list[Any]) -> None:
        if "minimum" in schema and value < schema["minimum"]:
            self.error(path, "schema.minimum", f"value must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            self.error(path, "schema.maximum", f"value must be <= {schema['maximum']}")


REQUIRED_SOURCE_GROUPS = (
    ("project.yaml",),
    ("literature/synthesis.md",),
    ("literature/synthesis_workbench.json",),
    ("literature/domain_map.json",),
    ("literature/comparison_table.csv",),
    ("ideation/hypotheses.md",),
    ("ideation/exp_plan.yaml",),
    ("ideation/selected/selected_candidate.json", "ideation/idea_scorecard.yaml"),
    ("ideation/kill_criteria.yaml", "ideation/risks.md"),
    ("ideation/novelty_audit.md",),
)
REQUIRED_SOURCE_PATHS = {path for group in REQUIRED_SOURCE_GROUPS for path in group}


def _source_group_label(group: tuple[str, ...]) -> str:
    return " or ".join(group)


def _source_group_is_ready(entries_by_path: dict[str, dict], group: tuple[str, ...]) -> bool:
    return any(
        entry is not None
        and entry.get("availability") == "available"
        and entry.get("used") is True
        for entry in (entries_by_path.get(path) for path in group)
    )

REQUIRED_STOP_CONDITIONS = {
    "budget_exhausted",
    "improvement_plateau",
    "required_baseline_unavailable",
    "audited_target_reached",
    "implementation_blocked",
    "claim_must_be_narrowed",
}

REQUIRED_FORBIDDEN_CHANGES = {
    "replace_core_mechanism",
    "drop_required_baseline",
    "change_task_or_benchmark",
    "change_contribution_type",
}

REQUIRED_GATE_STAGES = {
    "context_alignment",
    "resource_mining",
    "baseline_reproduction",
    "claim_evidence_design",
    "method_refinement",
    "implementation",
    "code_protocol_review",
    "smoke_validation",
    "formal_run",
    "result_diagnosis",
    "module_attribution",
    "refinement_decision",
    "realized_method_packaging",
    "figure_table_packaging",
    "writer_handoff",
}

REQUIRED_WRITER_ARTIFACTS = {
    "realized_method_package",
    "result_to_claim",
    "evidence_pack",
    "result_diagnosis",
    "module_attribution",
    "final_framework_figure",
    "figure_table_inventory",
    "limitations",
    "reproducibility_manifest",
}


class SemanticValidator:
    def __init__(self, pack: dict, source_root: Path | None, verify_hashes: bool):
        self.pack = pack
        self.source_root = source_root
        self.verify_hashes = verify_hashes
        self.findings: list[Finding] = []

    def add(self, severity: str, code: str, path: str, message: str) -> None:
        self.findings.append(Finding(severity, code, path, message))

    def error(self, code: str, path: str, message: str) -> None:
        self.add("error", code, path, message)

    def warning(self, code: str, path: str, message: str) -> None:
        self.add("warning", code, path, message)

    def validate(self) -> list[Finding]:
        try:
            self._validate_sources()
            self._validate_proposal_context()
            ids = self._collect_ids()
            # A blocked pack is a recoverable protocol-gap record. It must be
            # structurally valid and explain the blocker, but it intentionally
            # has no executable experiment/claim graph to cross-reference.
            # Full graph and policy validation applies only once a completed
            # contract has source-backed datasets, metrics and claim mappings.
            if self.pack["generation_status"] == "completed":
                self._validate_references(ids)
                self._validate_ordering()
                self._validate_novelty_baselines(ids)
                self._validate_claim_experiment_coverage(ids)
                self._validate_method_coverage(ids)
                self._validate_required_policies()
            self._validate_status()
        except (KeyError, TypeError, AttributeError) as exc:
            self.error("semantic.skipped", "/", f"semantic validation stopped after malformed structure: {exc}")
        return self.findings

    def _validate_sources(self) -> None:
        entries = self.pack["source_manifest"]
        by_path = {entry["path"]: entry for entry in entries}
        missing_declarations = [
            _source_group_label(group)
            for group in REQUIRED_SOURCE_GROUPS
            if not any(path in by_path for path in group)
        ]
        for label in missing_declarations:
            self.error("source.required_not_declared", "/source_manifest", f"required source group is not declared: {label}")
        actual_coverage = sum(
            _source_group_is_ready(by_path, group) for group in REQUIRED_SOURCE_GROUPS
        ) / len(REQUIRED_SOURCE_GROUPS)
        declared_coverage = self.pack["validation_summary"]["required_source_coverage"]
        if abs(actual_coverage - declared_coverage) > 1e-9:
            self.error("source.coverage_mismatch", "/validation_summary/required_source_coverage", f"declared {declared_coverage}, computed {actual_coverage}")
        used_count = sum(bool(entry["used"]) for entry in entries)
        if used_count != self.pack["validation_summary"]["used_source_count"]:
            self.error("source.used_count_mismatch", "/validation_summary/used_source_count", f"declared {self.pack['validation_summary']['used_source_count']}, computed {used_count}")

        for index, entry in enumerate(entries):
            path = f"/source_manifest/{index}"
            if entry["availability"] == "available" and "content_sha256" not in entry:
                self.error("source.hash_missing", path, "available source must include content_sha256")
            if entry["availability"] != "available" and not entry.get("omission_reason"):
                self.error("source.omission_reason_missing", path, "unavailable source must include omission_reason")
            if entry["used"] and entry["availability"] != "available":
                self.error("source.used_but_unavailable", path, "a used source must be available")
            if self.verify_hashes and entry["availability"] == "available":
                self._verify_source_hash(entry, path)

    def _validate_proposal_context(self) -> None:
        """Keep the Proposal useful to T5 without promoting it to evidence."""

        context = self.pack["context_reboost"]["research_context"]["proposal_context"]
        source_type = context["source_type"]
        manifest_path = context["manifest_path"]
        source_ids = {item["source_id"] for item in context["source_refs"]}
        entries_by_path = {entry["path"]: entry for entry in self.pack["source_manifest"]}
        if source_type == "formal_proposal":
            if manifest_path != "ideation/proposal/proposal_manifest.json":
                self.error(
                    "proposal.manifest_path_invalid",
                    "/context_reboost/research_context/proposal_context/manifest_path",
                    "formal_proposal must declare ideation/proposal/proposal_manifest.json",
                )
            if context["path"] != "ideation/proposal/research_proposal.md":
                self.error(
                    "proposal.path_invalid",
                    "/context_reboost/research_context/proposal_context/path",
                    "formal_proposal must declare ideation/proposal/research_proposal.md",
                )
            for source_id in {"SRC_RESEARCH_PROPOSAL", "SRC_PROPOSAL_MANIFEST"}:
                if source_id not in source_ids:
                    self.error(
                        "proposal.source_ref_missing",
                        "/context_reboost/research_context/proposal_context/source_refs",
                        f"formal_proposal must retain {source_id}",
                    )
            for path in {"ideation/proposal/research_proposal.md", "ideation/proposal/proposal_manifest.json"}:
                entry = entries_by_path.get(path)
                if entry is None or entry["availability"] != "available" or entry["used"] is not True:
                    self.error(
                        "proposal.source_not_used",
                        "/source_manifest",
                        f"formal_proposal requires available, used source: {path}",
                    )
        elif source_type == "legacy_formalization_fallback":
            if manifest_path:
                self.error(
                    "proposal.fallback_manifest_unexpected",
                    "/context_reboost/research_context/proposal_context/manifest_path",
                    "legacy_formalization_fallback must not claim a formal proposal manifest",
                )
            if {"SRC_RESEARCH_PROPOSAL", "SRC_PROPOSAL_MANIFEST"} & source_ids:
                self.error(
                    "proposal.fallback_source_ref_unexpected",
                    "/context_reboost/research_context/proposal_context/source_refs",
                    "legacy_formalization_fallback must not present an invalid Proposal as formal context",
                )

    def _verify_source_hash(self, entry: dict, path: str) -> None:
        if self.source_root is None:
            self.warning("source.hash_not_verified", path, "--source-root was not supplied")
            return
        file_path = self.source_root / entry["path"]
        if not file_path.is_file():
            self.error("source.file_missing", path, f"declared available source is missing: {file_path}")
            return
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if digest != entry.get("content_sha256"):
            self.error("source.hash_mismatch", path, f"SHA-256 differs for {entry['path']}")

    def _id_map(self, items: list[dict], key: str, path: str) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for index, item in enumerate(items):
            value = item[key]
            if value in result:
                self.error("id.duplicate", f"{path}/{index}/{key}", f"duplicate ID: {value}")
            result[value] = item
        return result

    def _collect_ids(self) -> dict[str, dict[str, dict]]:
        method = self.pack["method_intent"]
        loop = self.pack["minimum_experiment_loop"]
        return {
            "sources": self._id_map(self.pack["source_manifest"], "source_id", "/source_manifest"),
            "modules": self._id_map(method["candidate_modules"], "module_id", "/method_intent/candidate_modules"),
            "ablations": self._id_map(method["mechanism_to_ablation_plan"], "ablation_id", "/method_intent/mechanism_to_ablation_plan"),
            "baselines": self._id_map(self.pack["baseline_matrix"], "baseline_id", "/baseline_matrix"),
            "claims": self._id_map(self.pack["claim_evidence_matrix"], "claim_id", "/claim_evidence_matrix"),
            "experiments": self._id_map(loop["required_experiments"], "experiment_id", "/minimum_experiment_loop/required_experiments"),
            "gates": self._id_map(loop["ordered_gates"], "gate_id", "/minimum_experiment_loop/ordered_gates"),
        }

    def _walk(self, value: Any, path: list[Any] | None = None) -> Iterable[tuple[list[Any], str, Any]]:
        path = [] if path is None else path
        if isinstance(value, dict):
            for key, child in value.items():
                yield path, key, child
                yield from self._walk(child, path + [key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from self._walk(child, path + [index])

    def _validate_id_list(self, values: list[str], valid: dict[str, dict], path: str, kind: str) -> None:
        for value in values:
            if value not in valid:
                self.error("ref.unresolved", path, f"unknown {kind} ID: {value}")

    def _validate_references(self, ids: dict[str, dict[str, dict]]) -> None:
        key_to_kind = {
            "module_ids": "modules",
            "related_module_ids": "modules",
            "must_preserve_module_ids": "modules",
            "candidate_module_ids": "modules",
            "depends_on_module_ids": "modules",
            "baseline_ids": "baselines",
            "required_baseline_ids": "baselines",
            "linked_claim_ids": "claims",
            "related_claim_ids": "claims",
            "claim_ids": "claims",
            "depends_on_gate_ids": "gates",
            "planned_ablation_ids": "ablations",
        }
        inferred_count = 0
        for parent_path, key, value in self._walk(self.pack):
            current_path = pointer(parent_path + [key])
            if key == "source_refs" and isinstance(value, list):
                has_inference = False
                for index, ref in enumerate(value):
                    source_id = ref.get("source_id") if isinstance(ref, dict) else None
                    if source_id not in ids["sources"]:
                        self.error("ref.unresolved_source", f"{current_path}/{index}/source_id", f"unknown source ID: {source_id}")
                    if isinstance(ref, dict) and ref.get("support_type") == "inferred":
                        has_inference = True
                inferred_count += int(has_inference)
            if key in key_to_kind and isinstance(value, list):
                kind = key_to_kind[key]
                self._validate_id_list(value, ids[kind], current_path, kind[:-1])
            if key == "experiment_id" and isinstance(value, str) and "/required_experiments/" not in current_path:
                if value not in ids["experiments"]:
                    self.error("ref.unresolved", current_path, f"unknown experiment ID: {value}")
            if key == "claim_id" and isinstance(value, str) and "/claim_evidence_matrix/" not in current_path:
                if value not in ids["claims"]:
                    self.error("ref.unresolved", current_path, f"unknown claim ID: {value}")
        declared_inferred = self.pack["validation_summary"]["inferred_statement_count"]
        if declared_inferred != inferred_count:
            self.error("source.inferred_count_mismatch", "/validation_summary/inferred_statement_count", f"declared {declared_inferred}, computed {inferred_count}")

    def _validate_ordering(self) -> None:
        checks = (
            (self.pack["method_intent"]["expected_algorithm_flow"], "order", "/method_intent/expected_algorithm_flow"),
            (self.pack["context_reboost"]["execution_priorities"], "priority", "/context_reboost/execution_priorities"),
            (self.pack["minimum_experiment_loop"]["ordered_gates"], "order", "/minimum_experiment_loop/ordered_gates"),
        )
        for items, key, path in checks:
            values = [item[key] for item in items]
            if values != sorted(values) or len(values) != len(set(values)):
                self.error("order.invalid", path, f"{key} values must be unique and ascending")
        gates = self.pack["minimum_experiment_loop"]["ordered_gates"]
        gate_order = {gate["gate_id"]: gate["order"] for gate in gates}
        for index, gate in enumerate(gates):
            for dependency in gate["depends_on_gate_ids"]:
                if dependency in gate_order and gate_order[dependency] >= gate["order"]:
                    self.error("order.forward_dependency", f"/minimum_experiment_loop/ordered_gates/{index}/depends_on_gate_ids", f"gate depends on non-earlier gate {dependency}")

    def _validate_novelty_baselines(self, ids: dict[str, dict[str, dict]]) -> None:
        required = self.pack["context_reboost"]["novelty_audit_resolution"]["required_baseline_ids"]
        for baseline_id in required:
            baseline = ids["baselines"].get(baseline_id)
            if baseline is None:
                continue
            if baseline["requirement"] != "required":
                self.error("novelty.baseline_not_required", "/context_reboost/novelty_audit_resolution/required_baseline_ids", f"novelty baseline {baseline_id} is not marked required")

    def _validate_claim_experiment_coverage(self, ids: dict[str, dict[str, dict]]) -> None:
        experiment_claims = {
            claim_id
            for experiment in ids["experiments"].values()
            if experiment["required"]
            for claim_id in experiment["claim_ids"]
        }
        for index, claim in enumerate(self.pack["claim_evidence_matrix"]):
            if claim["claim_id"] not in experiment_claims:
                self.error("claim.no_required_experiment", f"/claim_evidence_matrix/{index}", "claim is not covered by a required experiment")

        experiment_baselines = {
            baseline_id
            for experiment in ids["experiments"].values()
            if experiment["required"]
            for baseline_id in experiment["baseline_ids"]
        }
        for index, baseline in enumerate(self.pack["baseline_matrix"]):
            if baseline["requirement"] == "required" and baseline["baseline_id"] not in experiment_baselines:
                self.error("baseline.no_required_experiment", f"/baseline_matrix/{index}", "required baseline is not covered by a required experiment")

    def _validate_method_coverage(self, ids: dict[str, dict[str, dict]]) -> None:
        method = self.pack["method_intent"]
        preserved = set(self.pack["context_reboost"]["method_mechanism"]["must_preserve_module_ids"])
        candidates = set(self.pack["context_reboost"]["method_mechanism"]["candidate_module_ids"])
        for index, module in enumerate(method["candidate_modules"]):
            module_id = module["module_id"]
            if module["classification"] == "core":
                if module_id not in preserved:
                    self.error("method.core_not_preserved", f"/method_intent/candidate_modules/{index}", "core module must appear in must_preserve_module_ids")
                if not module["related_claim_ids"]:
                    self.error("method.core_without_claim", f"/method_intent/candidate_modules/{index}", "core module must link to at least one claim")
                if not module["planned_ablation_ids"]:
                    self.error("method.core_without_ablation", f"/method_intent/candidate_modules/{index}", "core module must link to at least one ablation or diagnostic")
            if module["classification"] == "candidate" and module_id not in candidates:
                self.error("method.candidate_not_declared", f"/method_intent/candidate_modules/{index}", "candidate module must appear in candidate_module_ids")

    def _validate_required_policies(self) -> None:
        stop_types = {item["condition"] for item in self.pack["iteration_budget"]["stop_conditions"]}
        for missing in sorted(REQUIRED_STOP_CONDITIONS - stop_types):
            self.error("policy.stop_condition_missing", "/iteration_budget/stop_conditions", f"missing required stop condition: {missing}")
        change_types = {item["change_type"] for item in self.pack["method_intent"]["forbidden_silent_changes"]}
        for missing in sorted(REQUIRED_FORBIDDEN_CHANGES - change_types):
            self.error("policy.forbidden_change_missing", "/method_intent/forbidden_silent_changes", f"missing required forbidden change: {missing}")
        final_fact_bans = set(self.pack["writer_handoff_contract"]["must_not_use_as_final_fact_source"])
        if "method_intent" not in final_fact_bans:
            self.error("writer.method_intent_not_banned", "/writer_handoff_contract/must_not_use_as_final_fact_source", "method_intent must be forbidden as a final fact source")
        if "research_context" not in final_fact_bans:
            self.error("writer.research_context_not_banned", "/writer_handoff_contract/must_not_use_as_final_fact_source", "research_context must be forbidden as a final fact source")
        if "research_proposal" not in final_fact_bans:
            self.error("writer.research_proposal_not_banned", "/writer_handoff_contract/must_not_use_as_final_fact_source", "research_proposal must be forbidden as a final fact source")

    def _validate_status(self) -> None:
        status = self.pack["generation_status"]
        summary_status = self.pack["validation_summary"]["status"]
        mismatches = self.pack["context_reboost"]["known_context_mismatches"]
        unresolved = self.pack["unresolved_items"]
        execution_contract = self.pack.get("execution_contract") if isinstance(self.pack.get("execution_contract"), dict) else {}
        readiness = execution_contract.get("execution_readiness") if isinstance(execution_contract.get("execution_readiness"), dict) else {}
        # Handoffs created before protocol readiness was split from compilation
        # remain valid as fully-ready legacy contracts. New packs always carry
        # the structured object and receive stricter checks below.
        readiness_status = str(readiness.get("status") or "ready")
        protocol_decision_required = readiness_status == "protocol_decision_required"
        has_blocker = any(item["blocking"] for item in unresolved) or any(item["severity"] == "blocking" for item in mismatches)
        needs_review = any(item["requires_human_review"] for item in mismatches) or any(item["severity"] == "material" for item in unresolved)
        validation_checks = self.pack["validation_summary"]["checks"]
        entries_by_path = {entry["path"]: entry for entry in self.pack["source_manifest"]}
        required_sources_ready = all(
            _source_group_is_ready(entries_by_path, group)
            for group in REQUIRED_SOURCE_GROUPS
        )

        if status == "completed":
            if not required_sources_ready:
                self.error("status.completed_missing_source", "/generation_status", "completed pack requires every required source to be available and used")
            if has_blocker or (needs_review and not protocol_decision_required):
                self.error("status.completed_with_open_issue", "/generation_status", "completed pack cannot contain blocking or human-review issues")
            if summary_status != "pass":
                self.error("status.summary_mismatch", "/validation_summary/status", "completed pack requires validation_summary.status=pass")
            if any(check["status"] in {"fail", "not_run"} for check in validation_checks):
                self.error("status.validation_check_open", "/validation_summary/checks", "completed pack cannot contain failed or unrun validation checks")
            required_nonempty = (
                (self.pack["method_intent"]["candidate_modules"], "/method_intent/candidate_modules"),
                (self.pack["baseline_matrix"], "/baseline_matrix"),
                (self.pack["claim_evidence_matrix"], "/claim_evidence_matrix"),
                (self.pack["minimum_experiment_loop"]["required_experiments"], "/minimum_experiment_loop/required_experiments"),
                (self.pack["minimum_experiment_loop"]["ordered_gates"], "/minimum_experiment_loop/ordered_gates"),
            )
            for items, path in required_nonempty:
                if not items:
                    self.error("status.completed_empty_contract", path, "completed pack requires at least one item")
            modules = self.pack["method_intent"]["candidate_modules"]
            if not any(module["classification"] == "core" for module in modules):
                self.error("status.completed_no_core_module", "/method_intent/candidate_modules", "completed pack requires a core module")
            experiments = self.pack["minimum_experiment_loop"]["required_experiments"]
            run_types = {experiment["run_type"] for experiment in experiments if experiment["required"]}
            for required_type in {"reproduction", "formal"}:
                if required_type not in run_types:
                    self.error("status.completed_missing_run_type", "/minimum_experiment_loop/required_experiments", f"completed pack requires a required {required_type} experiment")
            stages = {gate["stage"] for gate in self.pack["minimum_experiment_loop"]["ordered_gates"]}
            for missing in sorted(REQUIRED_GATE_STAGES - stages):
                self.error("status.completed_missing_gate", "/minimum_experiment_loop/ordered_gates", f"completed pack is missing gate stage: {missing}")
            artifact_types = {item["artifact_type"] for item in self.pack["writer_handoff_contract"]["required_artifacts"]}
            for missing in sorted(REQUIRED_WRITER_ARTIFACTS - artifact_types):
                self.error("writer.artifact_missing", "/writer_handoff_contract/required_artifacts", f"missing required writer artifact: {missing}")
            if readiness_status == "blocked":
                self.error("execution.readiness_mismatch", "/execution_contract/execution_readiness", "completed pack cannot have blocked execution readiness")
            elif protocol_decision_required:
                if readiness.get("formal_execution_allowed") is not False:
                    self.error("execution.protocol_gate_missing", "/execution_contract/execution_readiness/formal_execution_allowed", "protocol-decision handoff must forbid formal execution")
                decisions = readiness.get("required_decisions")
                if not isinstance(decisions, list) or not decisions:
                    self.error("execution.protocol_decisions_missing", "/execution_contract/execution_readiness/required_decisions", "protocol-decision handoff must list explicit decisions")
                allowed_stages = set(readiness.get("allowed_stages") or [])
                forbidden_stages = set(readiness.get("blocked_stages") or [])
                if not {"context_alignment", "resource_and_baseline_preparation"}.issubset(allowed_stages):
                    self.error("execution.protocol_stage_missing", "/execution_contract/execution_readiness/allowed_stages", "protocol-decision handoff may permit only preparatory context/resource stages")
                if not {"implementation", "experiment_run", "writer_handoff"}.issubset(forbidden_stages):
                    self.error("execution.protocol_stage_not_blocked", "/execution_contract/execution_readiness/blocked_stages", "protocol-decision handoff must block implementation, formal runs, and writer handoff")
            elif readiness_status == "ready" and readiness and readiness.get("formal_execution_allowed") is not True:
                self.error("execution.readiness_mismatch", "/execution_contract/execution_readiness/formal_execution_allowed", "ready handoff must allow formal execution")
        elif status == "blocked" and summary_status not in {"blocked", "not_validated"}:
            self.error("status.summary_mismatch", "/validation_summary/status", "blocked pack requires blocked or not_validated summary")
        elif status == "needs_review" and summary_status not in {"needs_review", "not_validated"}:
            self.error("status.summary_mismatch", "/validation_summary/status", "needs_review pack requires needs_review or not_validated summary")
        if status == "blocked" and required_sources_ready and not has_blocker:
            self.error("status.blocked_without_reason", "/generation_status", "blocked pack requires a missing required source or explicit blocking issue")
        if status == "needs_review" and not needs_review:
            self.error("status.review_without_reason", "/generation_status", "needs_review pack requires a material unresolved item or human-review mismatch")


def load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"cannot read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} root must be a JSON object: {path}")
    return value


def parse_args() -> argparse.Namespace:
    default_schema = Path(__file__).resolve().parent.parent / "references" / "handoff_pack.schema.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True, type=Path, help="handoff_pack.json to validate")
    parser.add_argument("--schema", type=Path, default=default_schema, help="schema path")
    parser.add_argument("--source-root", type=Path, help="project root used to verify source hashes")
    parser.add_argument("--no-verify-hashes", action="store_true", help="skip on-disk SHA-256 verification")
    parser.add_argument("--report", type=Path, help="write machine-readable validation report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        pack = load_json(args.handoff, "handoff")
        schema = load_json(args.schema, "schema")
        structural = SchemaValidator(schema).validate(pack)
        semantic = [] if structural else SemanticValidator(pack, args.source_root, not args.no_verify_hashes).validate()
        findings = structural + semantic
        errors = [finding for finding in findings if finding.severity == "error"]
        warnings = [finding for finding in findings if finding.severity == "warning"]
        report = {
            "validator_version": "research_reboost_validator.v1",
            "valid": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "findings": [asdict(finding) for finding in findings],
        }
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text, encoding="utf-8")
        sys.stdout.write(text)
        return 0 if not errors else 1
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
