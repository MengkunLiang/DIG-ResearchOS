from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from .policies import default_resource_acquisition_policy
from .source_readers import read_allowed_paths, read_json_if_exists, read_text, read_yaml_if_exists
from .validation import get_by_dotted_path, injected_paths, is_empty_value, resolve_ref, schema_type, set_by_dotted_path


MISSING = object()


def build_project_skill_context(
    *,
    workspace: Path,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    ws = workspace.resolve()
    context = _build_skeleton(schema)
    context["$schema"] = "external_executor/schemas/project_skill_context.schema.json"
    context["schema_version"] = "project_skill_context.v1"
    context["generated_at"] = _now_iso()
    context["source_artifacts"] = _source_artifacts(ws)
    context["field_metadata"] = {}

    handoff = read_json_if_exists(ws / "external_executor" / "handoff_pack.json")
    project = read_yaml_if_exists(ws / "project.yaml")
    exp_plan = read_yaml_if_exists(ws / "ideation" / "exp_plan.yaml")
    expected_outputs = read_json_if_exists(ws / "external_executor" / "expected_outputs_schema.json")
    allowed_paths = read_allowed_paths(ws / "external_executor" / "allowed_paths.txt")
    agents_text = read_text(ws / "external_executor" / "AGENTS.md")
    hypotheses_text = read_text(ws / "ideation" / "hypotheses.md")
    novelty_text = read_text(ws / "novelty" / "novelty_audit.md") or read_text(ws / "ideation" / "novelty_audit.md")

    _apply_handoff(context, handoff)
    _apply_project_source(context, project, handoff)
    _apply_hypothesis_source(context, hypotheses_text, handoff)
    _apply_novelty_source(context, novelty_text, handoff)
    _apply_experiment_source(context, exp_plan, handoff)
    _apply_execution_sources(context, allowed_paths, agents_text, handoff)
    _apply_output_sources(context, expected_outputs, handoff)
    _apply_known_materials(context, ws)
    return context


def ensure_metadata_for_injections(context: MutableMapping[str, Any], mapping: Mapping[str, Any]) -> None:
    metadata = context.setdefault("field_metadata", {})
    if not isinstance(metadata, MutableMapping):
        context["field_metadata"] = {}
        metadata = context["field_metadata"]
    for path in injected_paths(mapping):
        try:
            value = get_by_dotted_path(context, path)
        except KeyError:
            continue
        existing = metadata.get(path)
        if isinstance(existing, MutableMapping):
            if existing.get("status") in {"confirmed", "confirmed_from_source"} and is_empty_value(value):
                existing["status"] = "uncertain"
                existing["note"] = "The field is empty after source resolution."
            if existing.get("status") == "uncertain" and not str(existing.get("note") or "").strip():
                existing["note"] = "The field could not be resolved from current project sources."
            continue
        set_context_field(
            context,
            metadata,
            path=path,
            value=value,
            status="uncertain" if is_empty_value(value) else "confirmed",
            sources=[],
            note="The field could not be resolved from current project sources." if is_empty_value(value) else None,
        )


def set_context_field(
    context: MutableMapping[str, Any],
    metadata: MutableMapping[str, Any],
    *,
    path: str,
    value: Any,
    status: str,
    sources: Sequence[str],
    note: str | None = None,
    handoff_value_ignored: Any = MISSING,
) -> None:
    if status in {"confirmed", "confirmed_from_source"} and is_empty_value(value):
        status = "uncertain"
        note = note or "The field is empty after source resolution."
    set_by_dotted_path(context, path, value)
    entry: dict[str, Any] = {"status": status, "sources": list(sources)}
    if note:
        entry["note"] = note
    if handoff_value_ignored is not MISSING:
        entry["handoff_value_ignored"] = handoff_value_ignored
    metadata[path] = entry


def _apply_handoff(context: MutableMapping[str, Any], handoff: Mapping[str, Any]) -> None:
    metadata = context["field_metadata"]
    context_reboost = _mapping(handoff.get("context_reboost"))
    method_intent = _mapping(handoff.get("method_intent"))
    experiment_contract = _mapping(handoff.get("experiment_contract"))
    sources = ["external_executor/handoff_pack.json"]

    handoff_rules = {
        "project.goal": _first_resolved(
            _get(context_reboost, "project_goal.statement"),
            context_reboost.get("project_goal"),
            handoff.get("experiment_intent_oneliner"),
        ),
        "research.central_hypothesis": _first_resolved(
            _get(context_reboost, "central_hypothesis.statement"),
            context_reboost.get("central_hypothesis"),
        ),
        "research.claim_boundaries": _string_list(
            _first_resolved(
                context_reboost.get("claim_boundaries"),
                _get(handoff, "claim_boundaries.novelty_boundary"),
                _get(handoff, "claim_boundaries.conditional_claims"),
            )
        ),
        "research.must_not_claim": _string_list(
            _first_resolved(
                context_reboost.get("must_not_claim"),
                _get(handoff, "claim_boundaries.must_not_claim"),
                handoff.get("must_not_claim"),
            )
        ),
        "research.core_claims": _claims_from_matrix(_first_resolved(context_reboost.get("claim_evidence_matrix"), handoff.get("claim_evidence_matrix"))),
        "research.reviewer_questions": _reviewer_questions(_first_resolved(context_reboost.get("claim_evidence_matrix"), handoff.get("claim_evidence_matrix"))),
        "method.central_mechanism_hypothesis": _first_resolved(
            _get(context_reboost, "method_mechanism.central_mechanism_hypothesis"),
            method_intent.get("central_mechanism_hypothesis"),
        ),
        "method.core_mechanism": _first_resolved(
            _get(context_reboost, "method_mechanism.core_mechanism"),
            method_intent.get("core_mechanism"),
        ),
        "method.must_preserve_components": _object_list(
            _first_resolved(
                _get(context_reboost, "method_mechanism.must_preserve_components"),
                method_intent.get("must_preserve_components"),
            )
        ),
        "method.candidate_components": _object_list(
            _first_resolved(method_intent.get("candidate_components"), method_intent.get("candidate_modules"))
        ),
        "method.expected_algorithm_flow": _object_list(method_intent.get("expected_algorithm_flow")),
        "method.allowed_refinements": _string_list(method_intent.get("allowed_refinements")),
        "method.forbidden_silent_changes": _string_list(method_intent.get("forbidden_silent_changes")),
        "method.mechanism_to_ablation": _object_list(
            _first_resolved(
                method_intent.get("mechanism_to_ablation"),
                method_intent.get("mechanism_to_ablation_plan"),
            )
        ),
        "method.implementation_questions": _string_list(method_intent.get("implementation_questions")),
        "method.implementation_acceptance": _string_list(method_intent.get("implementation_acceptance")),
        "method.scope_change_triggers": _string_list(method_intent.get("scope_change_triggers")),
        "method.attribution_requirements": _string_list(method_intent.get("attribution_requirements")),
        "method.initial_framework_figure_intent": _mapping(
            _first_resolved(
                method_intent.get("initial_framework_figure_intent"),
                method_intent.get("initial_framework_figure_sketch"),
            )
        ),
        "baselines.required": _baseline_list(
            _first_resolved(
                context_reboost.get("required_baselines"),
                context_reboost.get("baseline_matrix"),
                handoff.get("baseline_matrix"),
                handoff.get("required_baselines"),
            )
        ),
        "baselines.optional": _baseline_list(_first_resolved(context_reboost.get("optional_baselines"), handoff.get("optional_baselines"))),
        "baselines.replacement_policy": _mapping(_first_resolved(context_reboost.get("baseline_replacement_policy"), handoff.get("baseline_replacement_policy"))),
        "baselines.identity_requirements": _string_list(context_reboost.get("baseline_identity_requirements")),
        "baselines.fairness_constraints": _string_list(context_reboost.get("baseline_fairness_constraints")),
        "baselines.expected_reference_results": _object_list(context_reboost.get("expected_reference_results")),
        "baselines.allowed_repairs": _string_list(context_reboost.get("allowed_repairs")),
        "baselines.forbidden_repairs": _string_list(context_reboost.get("forbidden_repairs")),
        "baselines.reproduction_acceptance": _string_list(context_reboost.get("reproduction_acceptance")),
        "baselines.known_risks": _string_list(context_reboost.get("baseline_known_risks")),
        "experiment.minimum_experiment_loop": _minimum_loop(
            _first_resolved(context_reboost.get("minimum_experiment_loop"), handoff.get("minimum_experiment_loop"))
        ),
        "experiment.claim_evidence_matrix": _object_list(_first_resolved(context_reboost.get("claim_evidence_matrix"), handoff.get("claim_evidence_matrix"))),
        "experiment.primary_metrics": _metrics(_first_resolved(handoff.get("metrics"), experiment_contract.get("metrics"))),
        "experiment.seed_policy": _seed_policy(_first_resolved(handoff.get("seeds"), experiment_contract.get("seeds"))),
        "execution.max_iterations": _max_iterations(_first_resolved(context_reboost.get("iteration_budget"), handoff.get("iteration_budget"))),
        "execution.budget": _mapping(_first_resolved(context_reboost.get("iteration_budget"), handoff.get("iteration_budget"))),
        "execution.stop_conditions": _string_list(context_reboost.get("stop_conditions")),
        "execution.human_review_triggers": _string_list(context_reboost.get("human_review_triggers")),
        "outputs.writer_handoff_requirements": _string_list(
            _first_resolved(context_reboost.get("writer_handoff_contract"), handoff.get("writer_handoff_contract"))
        ),
        "outputs.handoff_readiness_requirements": _string_list(context_reboost.get("handoff_readiness_requirements")),
    }
    for path, value in handoff_rules.items():
        if value is not MISSING and not is_empty_value(value):
            set_context_field(context, metadata, path=path, value=value, status="confirmed", sources=sources)
    if not is_empty_value(handoff.get("workspace_relative_workdir")):
        set_context_field(
            context,
            metadata,
            path="implementation.work_root",
            value=handoff.get("workspace_relative_workdir"),
            status="confirmed",
            sources=sources,
        )
    policy = _resource_acquisition_policy(handoff, context_reboost)
    set_context_field(
        context,
        metadata,
        path="resources.acquisition_policy",
        value=policy,
        status="confirmed",
        sources=sources,
    )


def _apply_project_source(context: MutableMapping[str, Any], project: Mapping[str, Any], handoff: Mapping[str, Any]) -> None:
    metadata = context["field_metadata"]
    project_handoff = _mapping(handoff.get("project"))
    rules = {
        "project.project_id": _first_resolved(project.get("project_id"), project.get("name")),
        "project.title": _first_resolved(project.get("title"), project.get("name"), project.get("project_id")),
        "project.goal": _first_resolved(project.get("topic"), project.get("goal"), project.get("research_goal")),
        "project.domain": _first_resolved(project.get("domain"), project.get("field")),
    }
    handoff_values = {
        "project.project_id": _first_resolved(project_handoff.get("project_id"), handoff.get("project_id")),
        "project.title": project_handoff.get("title"),
        "project.goal": _first_resolved(_get(handoff, "context_reboost.project_goal"), handoff.get("experiment_intent_oneliner")),
        "project.domain": project_handoff.get("domain"),
    }
    for path, value in rules.items():
        handoff_value = handoff_values.get(path, MISSING)
        _set_authoritative(context, metadata, path, value, handoff_value, "project.yaml")


def _apply_hypothesis_source(context: MutableMapping[str, Any], text: str, handoff: Mapping[str, Any]) -> None:
    metadata = context["field_metadata"]
    central = _extract_labeled_value(
        text,
        ["central hypothesis", "central_hypothesis", "hypothesis", "研究假设", "中心假设"],
    )
    source = "ideation/hypotheses.md"
    _set_authoritative(
        context,
        metadata,
        "research.central_hypothesis",
        central,
        _get(handoff, "context_reboost.central_hypothesis"),
        source,
    )
    claims = _extract_bullets_after_heading(text, ["core claims", "claims", "核心主张"])
    if claims:
        _set_authoritative(
            context,
            metadata,
            "research.core_claims",
            [{"claim_id": f"C{idx}", "statement": claim, "priority": "unknown"} for idx, claim in enumerate(claims, start=1)],
            _claims_from_matrix(_get(handoff, "context_reboost.claim_evidence_matrix")),
            source,
        )


def _apply_novelty_source(context: MutableMapping[str, Any], text: str, handoff: Mapping[str, Any]) -> None:
    metadata = context["field_metadata"]
    source = "novelty/novelty_audit.md" if text else "novelty/novelty_audit.md"
    boundaries = _extract_bullets_after_heading(text, ["claim boundaries", "claim boundary", "边界"])
    if boundaries:
        _set_authoritative(context, metadata, "research.claim_boundaries", boundaries, _get(handoff, "context_reboost.claim_boundaries"), source)
    must_not = _extract_bullets_after_heading(text, ["must not claim", "must-not-claim", "不能声称"])
    if must_not:
        _set_authoritative(context, metadata, "research.must_not_claim", must_not, _get(handoff, "context_reboost.must_not_claim"), source)
    novelty = _extract_bullets_after_heading(text, ["novelty boundary", "novelty boundaries", "新颖性边界"])
    if novelty:
        _set_authoritative(context, metadata, "research.novelty_boundary", novelty, MISSING, source)


def _apply_experiment_source(context: MutableMapping[str, Any], exp_plan: Mapping[str, Any], handoff: Mapping[str, Any]) -> None:
    metadata = context["field_metadata"]
    source = "ideation/exp_plan.yaml"
    rules = {
        "experiment.task": _first_resolved(exp_plan.get("task"), exp_plan.get("experiment_task")),
        "experiment.benchmarks": _object_list(_first_resolved(exp_plan.get("benchmarks"), exp_plan.get("benchmark"))),
        "experiment.datasets": _object_list(_first_resolved(exp_plan.get("datasets"), exp_plan.get("dataset"))),
        "experiment.splits": _object_list(exp_plan.get("splits")),
        "experiment.preprocessing": _string_list(exp_plan.get("preprocessing")),
        "experiment.primary_metrics": _metrics(_first_resolved(exp_plan.get("primary_metrics"), exp_plan.get("metrics"))),
        "experiment.secondary_metrics": _metrics(exp_plan.get("secondary_metrics")),
        "experiment.seed_policy": _mapping(_first_resolved(exp_plan.get("seed_policy"), exp_plan.get("seeds"))),
        "experiment.minimum_experiment_loop": _minimum_loop(exp_plan.get("minimum_experiment_loop")),
        "experiment.required_experiment_types": _string_list(exp_plan.get("required_experiment_types")),
        "experiment.comparison_axes": _string_list(exp_plan.get("comparison_axes")),
        "experiment.practical_thresholds": _numeric_mapping(exp_plan.get("practical_thresholds")),
        "experiment.important_subsets": _string_list(exp_plan.get("important_subsets")),
        "experiment.known_confounders": _string_list(exp_plan.get("known_confounders")),
        "experiment.interpretation_boundaries": _mapping(exp_plan.get("interpretation_boundaries")),
        "experiment.protocol_constraints": _string_list(exp_plan.get("protocol_constraints")),
        "experiment.statistical_policy": _mapping(exp_plan.get("statistical_policy")),
    }
    for path, value in rules.items():
        _set_authoritative(context, metadata, path, value, _get(handoff, path), source)


def _apply_execution_sources(
    context: MutableMapping[str, Any],
    allowed_paths: list[str],
    agents_text: str,
    handoff: Mapping[str, Any],
) -> None:
    metadata = context["field_metadata"]
    allowed = [line for line in allowed_paths if not line.lower().startswith("no ")]
    forbidden = [line for line in allowed_paths if line.lower().startswith("no ")]
    if allowed:
        _set_authoritative(context, metadata, "execution.allowed_paths", allowed, handoff.get("allowed_paths"), "external_executor/allowed_paths.txt")
    if forbidden:
        _set_authoritative(context, metadata, "execution.forbidden_paths", forbidden, MISSING, "external_executor/allowed_paths.txt")
        _set_authoritative(context, metadata, "implementation.protected_paths", forbidden, MISSING, "external_executor/allowed_paths.txt")
    security = _extract_bullets_after_heading(agents_text, ["hard boundaries", "security", "do not"])
    if security:
        _set_authoritative(context, metadata, "resources.security_constraints", security, MISSING, "external_executor/AGENTS.md")
    if not is_empty_value(handoff.get("executor_outputs_contract")):
        contract = _mapping(handoff.get("executor_outputs_contract"))
        must_write = _string_list(contract.get("must_write"))
        if must_write:
            set_context_field(
                context,
                metadata,
                path="outputs.required_artifact_types",
                value=must_write,
                status="confirmed",
                sources=["external_executor/handoff_pack.json"],
            )


def _apply_output_sources(context: MutableMapping[str, Any], expected_outputs: Mapping[str, Any], handoff: Mapping[str, Any]) -> None:
    metadata = context["field_metadata"]
    source = "external_executor/expected_outputs_schema.json"
    schema_version = _first_resolved(expected_outputs.get("schema_version"), expected_outputs.get("version"))
    if not is_empty_value(schema_version):
        _set_authoritative(context, metadata, "outputs.result_schema_version", schema_version, MISSING, source)
    required = _string_list(
        _first_resolved(expected_outputs.get("required"), expected_outputs.get("required_fields"), expected_outputs.get("must_write"))
    )
    if required:
        _set_authoritative(context, metadata, "outputs.required_result_sections", required, MISSING, source)
    handoff_contract = _mapping(handoff.get("executor_outputs_contract"))
    must_write = _string_list(handoff_contract.get("must_write"))
    if must_write:
        set_context_field(
            context,
            metadata,
            path="outputs.t7_requirements",
            value=must_write,
            status="confirmed",
            sources=["external_executor/handoff_pack.json"],
        )


def _apply_known_materials(context: MutableMapping[str, Any], workspace: Path) -> None:
    metadata = context["field_metadata"]
    materials: list[dict[str, Any]] = []
    for rel in ("resources", "external_executor/expr"):
        root = workspace / rel
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if path.name.startswith("."):
                continue
            materials.append(
                {
                    "resource_id": path.name,
                    "name": path.name,
                    "resource_type": "directory" if path.is_dir() else "file",
                    "path": path.relative_to(workspace).as_posix(),
                }
            )
    if materials:
        set_context_field(
            context,
            metadata,
            path="resources.known_materials",
            value=materials,
            status="confirmed_from_source",
            sources=["resources", "external_executor/expr"],
        )


def _resource_acquisition_policy(handoff: Mapping[str, Any], context_reboost: Mapping[str, Any]) -> dict[str, Any]:
    for value in (
        handoff.get("resource_acquisition_policy"),
        context_reboost.get("resource_acquisition_policy"),
        _get(handoff, "execution_contract.resource_acquisition_policy"),
    ):
        if isinstance(value, Mapping) and value:
            policy = dict(value)
            break
    else:
        policy = default_resource_acquisition_policy()
    default = default_resource_acquisition_policy()
    policy.update(
        {
            "mode": "github_and_reimplementation",
            "network_allowed": True,
            "github_access_allowed": True,
            "dataset_download_allowed": True,
            "baseline_reimplementation_allowed": True,
        }
    )
    policy.setdefault("allowed_domains", default["allowed_domains"])
    policy.setdefault("material_absence_policy", default["material_absence_policy"])
    return policy


def _set_authoritative(
    context: MutableMapping[str, Any],
    metadata: MutableMapping[str, Any],
    path: str,
    value: Any,
    handoff_value: Any,
    source: str,
) -> None:
    if value is MISSING or is_empty_value(value):
        if path not in metadata:
            try:
                existing = get_by_dotted_path(context, path)
            except KeyError:
                existing = None
            set_context_field(
                context,
                metadata,
                path=path,
                value=existing,
                status="uncertain",
                sources=[source],
                note=f"The field could not be resolved from {source}.",
            )
        return
    if handoff_value is not MISSING and not is_empty_value(handoff_value) and not _normalized_equal(value, handoff_value):
        set_context_field(
            context,
            metadata,
            path=path,
            value=value,
            status="confirmed_from_source",
            sources=[source],
            note="Source artifact overrides the handoff value.",
            handoff_value_ignored=handoff_value,
        )
    else:
        set_context_field(context, metadata, path=path, value=value, status="confirmed", sources=[source])


def _build_skeleton(schema: Mapping[str, Any], node: Mapping[str, Any] | None = None) -> Any:
    node = resolve_ref(schema, node or schema)
    if "const" in node:
        return node["const"]
    types = schema_type(node)
    if "object" in types or "properties" in node:
        result: dict[str, Any] = {}
        properties = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
        for key in node.get("required", []) or []:
            child = properties.get(key)
            result[key] = _build_skeleton(schema, child) if isinstance(child, Mapping) else {}
        return result
    if "array" in types:
        return []
    if "null" in types:
        return None
    if "integer" in types or "number" in types:
        return None
    if "boolean" in types:
        return None
    return None


def _source_artifacts(workspace: Path) -> dict[str, str | None]:
    rels = {
        "handoff": "external_executor/handoff_pack.json",
        "project": "project.yaml",
        "hypothesis": "ideation/hypotheses.md",
        "experiment_plan": "ideation/exp_plan.yaml",
        "novelty_audit": "novelty/novelty_audit.md",
        "expected_outputs": "external_executor/expected_outputs_schema.json",
        "agents_policy": "external_executor/AGENTS.md",
        "allowed_paths": "external_executor/allowed_paths.txt",
        "synthesis": "literature/synthesis.md",
        "risks": "ideation/risks.md",
        "idea_scorecard": "ideation/idea_scorecard.yaml",
    }
    return {key: rel if (workspace / rel).exists() else None for key, rel in rels.items()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> dict[str, Any]:
    if value is MISSING:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return {"items": value}
    if is_empty_value(value):
        return {}
    return {"value": value}


def _numeric_mapping(value: Any) -> dict[str, float]:
    if value is MISSING:
        return {}
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[str(key)] = float(item)
    return result


def _object_list(value: Any) -> list[dict[str, Any]]:
    if value is MISSING:
        return []
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                result.append(dict(item))
            elif not is_empty_value(item):
                result.append({"value": item})
        return result
    if isinstance(value, Mapping):
        return [dict(value)]
    return []


def _string_list(value: Any) -> list[str]:
    if value is MISSING:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                selected = _first_resolved(
                    item.get("boundary"),
                    item.get("claim"),
                    item.get("statement"),
                    item.get("description"),
                    item.get("artifact_type"),
                    item.get("name"),
                    item.get("value"),
                )
                if selected is MISSING:
                    selected = json.dumps(dict(item), ensure_ascii=False, sort_keys=True)
                result.append(str(selected).strip())
            elif str(item).strip():
                result.append(str(item).strip())
        return [item for item in result if item]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        if "\n" in value:
            return [line.strip("-* \t") for line in value.splitlines() if line.strip("-* \t")]
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    return []


def _baseline_list(value: Any) -> list[dict[str, Any]]:
    if value is MISSING:
        return []
    raw = _object_list(value) if not isinstance(value, list) else value
    result: list[dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, str):
            result.append({"baseline_id": f"B{idx}", "name": item, "reason_included": ""})
            continue
        if isinstance(item, Mapping):
            payload = dict(item)
            name = _first_resolved(payload.get("name"), payload.get("baseline_name"), payload.get("method"), payload.get("id"))
            baseline_id = _first_resolved(payload.get("baseline_id"), payload.get("id"), f"B{idx}")
            payload.setdefault("baseline_id", baseline_id)
            payload.setdefault("name", name)
            result.append(payload)
    return result


def _metrics(value: Any) -> list[dict[str, Any]]:
    if value is MISSING:
        return []
    if isinstance(value, Mapping) and "metrics" in value:
        value = value["metrics"]
    result: list[dict[str, Any]] = []
    for idx, item in enumerate(value if isinstance(value, list) else _string_list(value), start=1):
        if isinstance(item, Mapping):
            payload = dict(item)
            payload.setdefault("name", payload.get("metric") or payload.get("id") or f"metric_{idx}")
            payload.setdefault("direction", payload.get("direction") or "unknown")
            payload.setdefault("aggregation", payload.get("aggregation") or "unspecified")
            result.append(payload)
        elif not is_empty_value(item):
            result.append({"name": str(item), "direction": "unknown", "aggregation": "unspecified"})
    return result


def _seed_policy(value: Any) -> dict[str, Any]:
    if value is MISSING:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return {"seeds": value}
    if not is_empty_value(value):
        return {"seeds": [value]}
    return {}


def _minimum_loop(value: Any) -> list[str]:
    if value is MISSING:
        return []
    if isinstance(value, Mapping):
        result: list[str] = []
        for item in value.get("required_experiments", []) or []:
            if isinstance(item, Mapping):
                result.append(str(_first_resolved(item.get("purpose"), item.get("experiment_id"), item.get("run_type"))))
        for item in value.get("ordered_gates", []) or []:
            if isinstance(item, Mapping):
                result.append(str(_first_resolved(item.get("stage"), item.get("gate_id"))))
        return [item for item in result if item and item != str(MISSING)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                result.append(str(item.get("step") or item.get("name") or json.dumps(item, ensure_ascii=False, sort_keys=True)))
            elif not is_empty_value(item):
                result.append(str(item))
        return result
    return _string_list(value)


def _claims_from_matrix(value: Any) -> list[dict[str, Any]]:
    if value is MISSING:
        return []
    result: list[dict[str, Any]] = []
    for idx, item in enumerate(value if isinstance(value, list) else [], start=1):
        if isinstance(item, Mapping):
            result.append(
                {
                    "claim_id": str(_first_resolved(item.get("claim_id"), item.get("id"), f"C{idx}")),
                    "statement": str(_first_resolved(item.get("claim"), item.get("statement"), item.get("claim_statement"), "")),
                    "priority": str(_first_resolved(item.get("priority"), "unknown")),
                }
            )
    return [item for item in result if item["statement"]]


def _reviewer_questions(value: Any) -> list[str]:
    if value is MISSING:
        return []
    questions: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, Mapping):
            question = _first_resolved(item.get("reviewer_question"), item.get("question"), item.get("evidence_needed"))
            if not is_empty_value(question):
                questions.append(str(question))
    return questions


def _max_iterations(value: Any) -> int | None:
    if value is MISSING:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        for key in ("max_iterations", "iterations", "iteration_budget"):
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool):
                return item
    return None


def _get(data: Mapping[str, Any], dotted_path: str) -> Any:
    try:
        return get_by_dotted_path(data, dotted_path)
    except KeyError:
        return MISSING


def _first_resolved(*values: Any) -> Any:
    for value in values:
        if value is MISSING:
            continue
        if not is_empty_value(value):
            return value
    return MISSING


def _normalized_equal(left: Any, right: Any) -> bool:
    return _normalize(left) == _normalize(right)


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _extract_labeled_value(text: str, labels: Sequence[str]) -> str | object:
    if not text:
        return MISSING
    for line in text.splitlines():
        clean = line.strip().strip("-* ")
        clean = re.sub(r"^\*\*(.+?)\*\*", r"\1", clean)
        for label in labels:
            pattern = rf"^{re.escape(label)}\s*[:：-]\s*(.+)$"
            match = re.match(pattern, clean, flags=re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()
    return MISSING


def _extract_bullets_after_heading(text: str, labels: Sequence[str]) -> list[str]:
    if not text:
        return []
    lines = text.splitlines()
    collecting = False
    bullets: list[str] = []
    for line in lines:
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line.strip())
        if heading:
            title = heading.group(1).strip().lower()
            if collecting:
                break
            collecting = any(label.lower() in title for label in labels)
            continue
        if not collecting:
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if bullet:
            bullets.append(bullet.group(1).strip())
    return bullets
