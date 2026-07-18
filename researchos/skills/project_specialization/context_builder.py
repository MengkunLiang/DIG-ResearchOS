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
    study_scope = _mapping(context_reboost.get("study_scope"))
    method_mechanism = _mapping(context_reboost.get("method_mechanism"))
    method_intent = _mapping(handoff.get("method_intent"))
    experiment_contract = _mapping(handoff.get("experiment_contract"))
    baseline_matrix = _object_list(_first_resolved(context_reboost.get("baseline_matrix"), handoff.get("baseline_matrix")))
    claim_matrix = _object_list(_first_resolved(context_reboost.get("claim_evidence_matrix"), handoff.get("claim_evidence_matrix")))
    minimum_loop = _mapping(_first_resolved(context_reboost.get("minimum_experiment_loop"), handoff.get("minimum_experiment_loop")))
    iteration_budget = _mapping(_first_resolved(context_reboost.get("iteration_budget"), handoff.get("iteration_budget")))
    claim_boundaries = _mapping(handoff.get("claim_boundaries"))
    execution_contract = _mapping(handoff.get("execution_contract"))
    writer_handoff = _mapping(handoff.get("writer_handoff_contract"))
    paper_card_policy = _mapping(handoff.get("paper_card_evidence_policy"))
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
        "research.contribution_type": _first_resolved(
            _get(method_mechanism, "contribution_intent"),
            _get(claim_boundaries, "method_vs_engineering_boundary"),
        ),
        "research.novelty_boundary": _string_list(_first_resolved(_get(claim_boundaries, "novelty_boundary"), context_reboost.get("claim_boundaries"))),
        "research.claim_boundaries": _claim_boundaries(claim_boundaries, context_reboost),
        "research.must_not_claim": _string_list(
            _first_resolved(
                context_reboost.get("must_not_claim"),
                claim_boundaries.get("must_not_claim"),
                handoff.get("must_not_claim"),
            )
        ),
        "research.core_claims": _claims_from_matrix(claim_matrix),
        "research.reviewer_questions": _reviewer_questions(claim_matrix),
        "method.central_mechanism_hypothesis": _first_resolved(
            _get(method_mechanism, "central_mechanism_hypothesis"),
            method_intent.get("central_mechanism_hypothesis"),
        ),
        "method.core_mechanism": _first_resolved(
            _get(method_mechanism, "core_mechanism"),
            method_intent.get("core_mechanism"),
        ),
        "method.must_preserve_components": _must_preserve_components(method_mechanism, method_intent),
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
        "method.implementation_acceptance": _first_non_empty_list(
            _string_list(method_intent.get("implementation_acceptance")),
            _implementation_acceptance(context_reboost, writer_handoff),
        ),
        "method.scope_change_triggers": _first_non_empty_list(
            _string_list(method_intent.get("scope_change_triggers")),
            _scope_change_triggers(execution_contract, claim_boundaries),
        ),
        "method.attribution_requirements": _first_non_empty_list(
            _string_list(method_intent.get("attribution_requirements")),
            _attribution_requirements(method_intent, claim_matrix),
        ),
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
        "baselines.replacement_policy": _first_resolved(
            _mapping(_first_resolved(context_reboost.get("baseline_replacement_policy"), handoff.get("baseline_replacement_policy"))),
            _baseline_replacement_policy(baseline_matrix),
        ),
        "baselines.identity_requirements": _first_non_empty_list(
            _string_list(context_reboost.get("baseline_identity_requirements")),
            _baseline_identity_requirements(baseline_matrix),
        ),
        "baselines.fairness_constraints": _first_non_empty_list(
            _string_list(context_reboost.get("baseline_fairness_constraints")),
            _baseline_fairness_constraints(baseline_matrix),
        ),
        "baselines.expected_reference_results": _object_list(context_reboost.get("expected_reference_results")),
        "baselines.allowed_repairs": _string_list(context_reboost.get("allowed_repairs")),
        "baselines.forbidden_repairs": _first_non_empty_list(
            _string_list(context_reboost.get("forbidden_repairs")),
            _baseline_forbidden_repairs(baseline_matrix),
        ),
        "baselines.reproduction_acceptance": _first_non_empty_list(
            _string_list(context_reboost.get("reproduction_acceptance")),
            _baseline_reproduction_acceptance(baseline_matrix),
        ),
        "baselines.known_risks": _string_list(context_reboost.get("baseline_known_risks")),
        "experiment.minimum_experiment_loop": _minimum_loop(
            _first_resolved(context_reboost.get("minimum_experiment_loop"), handoff.get("minimum_experiment_loop"))
        ),
        "experiment.claim_evidence_matrix": _object_list(claim_matrix),
        "experiment.task": _experiment_task(study_scope, handoff.get("minimum_experiment_loop")),
        "experiment.benchmarks": _benchmarks_from_minimum_loop(handoff.get("minimum_experiment_loop"), study_scope),
        "experiment.datasets": _datasets_from_study_scope(study_scope),
        "experiment.splits": _splits_from_study_scope(study_scope),
        "experiment.primary_metrics": _metrics(_first_resolved(handoff.get("metrics"), experiment_contract.get("metrics"), study_scope.get("metrics"))),
        "experiment.seed_policy": _seed_policy(_first_resolved(handoff.get("seeds"), experiment_contract.get("seeds"), _seeds_from_scope(study_scope))),
        "experiment.required_experiment_types": _required_experiment_types(minimum_loop, study_scope),
        "experiment.comparison_axes": _comparison_axes(claim_matrix, baseline_matrix),
        "experiment.important_subsets": _important_subsets(study_scope),
        "experiment.known_confounders": _known_confounders(context_reboost),
        "experiment.interpretation_boundaries": _interpretation_boundaries(claim_matrix, claim_boundaries),
        "experiment.protocol_constraints": _protocol_constraints(study_scope, execution_contract, baseline_matrix),
        "experiment.statistical_policy": _statistical_policy(claim_matrix, study_scope),
        "resources.resource_requirements": _resource_requirements(study_scope, baseline_matrix, writer_handoff),
        "resources.acceptance_criteria": _resource_acceptance_criteria(execution_contract, writer_handoff, baseline_matrix),
        "resources.license_constraints": _resource_license_constraints(execution_contract),
        "implementation.work_root": _work_root_from_execution_contract(execution_contract),
        "implementation.ablation_switch_requirements": _ablation_switch_requirements(method_intent),
        "execution.max_iterations": _max_iterations(_first_resolved(context_reboost.get("iteration_budget"), handoff.get("iteration_budget"))),
        "execution.budget": _mapping(_first_resolved(context_reboost.get("iteration_budget"), handoff.get("iteration_budget"))),
        "execution.stop_conditions": _string_list(_first_resolved(context_reboost.get("stop_conditions"), iteration_budget.get("stop_conditions"))),
        "execution.human_review_triggers": _first_non_empty_list(
            _string_list(context_reboost.get("human_review_triggers")),
            _human_review_triggers(execution_contract, claim_boundaries),
        ),
        "execution.isolation_requirements": _isolation_requirements(execution_contract),
        "execution.allowed_run_levels": _allowed_run_levels(execution_contract),
        "execution.resume_policy": _resume_policy(iteration_budget, execution_contract),
        "outputs.required_artifact_types": _required_artifact_types(writer_handoff),
        "outputs.realized_method_requirements": _realized_method_requirements(writer_handoff),
        "outputs.framework_figure_requirements": _framework_figure_requirements(method_intent, writer_handoff),
        "outputs.required_figure_types": _required_figure_types(writer_handoff),
        "outputs.required_table_types": _required_table_types(writer_handoff),
        "outputs.visual_traceability_requirements": _visual_traceability_requirements(writer_handoff),
        "outputs.writer_handoff_requirements": _writer_handoff_requirements(writer_handoff),
        "outputs.t7_requirements": _t7_requirements(writer_handoff),
        "outputs.handoff_readiness_requirements": _handoff_readiness_requirements(writer_handoff),
        "outputs.evidence_ceiling_policy": _evidence_ceiling_policy(claim_matrix, claim_boundaries, paper_card_policy),
    }
    for path, value in handoff_rules.items():
        if value is not MISSING and not is_empty_value(value):
            set_context_field(context, metadata, path=path, value=value, status="confirmed", sources=sources)
    deployment_dir = handoff.get("workspace_relative_deployment_dir")
    if not is_empty_value(deployment_dir):
        set_context_field(
            context,
            metadata,
            path="implementation.work_root",
            value=deployment_dir,
            status="confirmed",
            sources=sources,
        )
    elif not is_empty_value(handoff.get("workspace_relative_workdir")) and str(handoff.get("workspace_relative_workdir")).strip() not in {".", "./"}:
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
        "experiment.task": _first_resolved(exp_plan.get("task"), exp_plan.get("experiment_task"), exp_plan.get("goal"), _task_from_exp_plan(exp_plan)),
        "experiment.benchmarks": _object_list(_first_resolved(exp_plan.get("benchmarks"), exp_plan.get("benchmark"), _benchmarks_from_exp_plan(exp_plan))),
        "experiment.datasets": _object_list(_first_resolved(exp_plan.get("datasets"), exp_plan.get("dataset"), _datasets_from_exp_plan(exp_plan))),
        "experiment.splits": _object_list(_first_resolved(exp_plan.get("splits"), _splits_from_exp_plan(exp_plan))),
        "experiment.preprocessing": _first_non_empty_list(_string_list(exp_plan.get("preprocessing")), _preprocessing_from_exp_plan(exp_plan)),
        "experiment.primary_metrics": _metrics(_first_resolved(exp_plan.get("primary_metrics"), exp_plan.get("metrics"), _metrics_from_exp_plan(exp_plan, primary=True))),
        "experiment.secondary_metrics": _metrics(_first_resolved(exp_plan.get("secondary_metrics"), _metrics_from_exp_plan(exp_plan, primary=False))),
        "experiment.seed_policy": _mapping(_first_resolved(exp_plan.get("seed_policy"), exp_plan.get("seeds"), _seed_policy_from_exp_plan(exp_plan))),
        "experiment.minimum_experiment_loop": _minimum_loop(_first_resolved(exp_plan.get("minimum_experiment_loop"), _minimum_loop_from_exp_plan(exp_plan))),
        "experiment.required_experiment_types": _first_non_empty_list(_string_list(exp_plan.get("required_experiment_types")), _required_experiment_types_from_exp_plan(exp_plan)),
        "experiment.comparison_axes": _first_non_empty_list(_string_list(exp_plan.get("comparison_axes")), _comparison_axes_from_exp_plan(exp_plan)),
        "experiment.practical_thresholds": _first_resolved(_numeric_mapping(exp_plan.get("practical_thresholds")), _practical_thresholds_from_exp_plan(exp_plan)),
        "experiment.important_subsets": _first_non_empty_list(_string_list(exp_plan.get("important_subsets")), _important_subsets_from_exp_plan(exp_plan)),
        "experiment.known_confounders": _string_list(exp_plan.get("known_confounders")),
        "experiment.interpretation_boundaries": _mapping(exp_plan.get("interpretation_boundaries")),
        "experiment.protocol_constraints": _first_non_empty_list(_string_list(exp_plan.get("protocol_constraints")), _protocol_constraints_from_exp_plan(exp_plan)),
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
    work_root = _work_root_from_path_lines(allowed)
    if work_root:
        _set_authoritative(context, metadata, "implementation.work_root", work_root, _get(handoff, "implementation.work_root"), "external_executor/allowed_paths.txt")
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
    writer_handoff = _mapping(handoff.get("writer_handoff_contract"))
    handoff_contract = _mapping(handoff.get("executor_outputs_contract"))
    output_sources = [source]
    if writer_handoff:
        output_sources.append("external_executor/handoff_pack.json")

    artifact_types = _required_artifact_types(writer_handoff, expected_outputs)
    if artifact_types:
        set_context_field(
            context,
            metadata,
            path="outputs.required_artifact_types",
            value=artifact_types,
            status="confirmed",
            sources=output_sources,
        )
    t7_requirements = _first_non_empty_list(_string_list(handoff_contract.get("must_write")), _t7_requirements(writer_handoff, expected_outputs))
    if t7_requirements:
        set_context_field(
            context,
            metadata,
            path="outputs.t7_requirements",
            value=t7_requirements,
            status="confirmed",
            sources=output_sources,
        )
    output_rules = {
        "implementation.metric_output_contract": _metric_output_contract(expected_outputs),
        "implementation.logging_requirements": _logging_requirements(expected_outputs),
        "implementation.testing_requirements": _testing_requirements(expected_outputs),
        "implementation.review_requirements": _review_requirements(writer_handoff, expected_outputs),
        "outputs.realized_method_requirements": _realized_method_requirements(writer_handoff, expected_outputs),
        "outputs.visual_traceability_requirements": _visual_traceability_requirements(writer_handoff, expected_outputs),
        "outputs.writer_handoff_requirements": _writer_handoff_requirements(writer_handoff, expected_outputs),
        "outputs.handoff_readiness_requirements": _handoff_readiness_requirements(writer_handoff, expected_outputs),
    }
    for path, value in output_rules.items():
        if not is_empty_value(value):
            set_context_field(context, metadata, path=path, value=value, status="confirmed", sources=output_sources)


def _apply_known_materials(context: MutableMapping[str, Any], workspace: Path) -> None:
    metadata = context["field_metadata"]
    materials: list[dict[str, Any]] = []
    scanned_sources: list[str] = []
    for rel in ("resource", "resources"):
        root = workspace / rel
        if not root.exists():
            continue
        scanned_sources.append(rel)
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
            sources=scanned_sources,
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


def _first_non_empty_list(*values: Any) -> list[Any]:
    for value in values:
        if value is MISSING or is_empty_value(value):
            continue
        if isinstance(value, list):
            return value
        converted = _string_list(value)
        if converted:
            return converted
    return []


def _dedupe_strings(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value).split())
        if text and text not in result:
            result.append(text)
    return result


def _claim_boundaries(claim_boundaries: Mapping[str, Any], context_reboost: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    items.extend(_string_list(context_reboost.get("claim_boundaries")))
    items.extend(_string_list(claim_boundaries.get("novelty_boundary")))
    method_boundary = claim_boundaries.get("method_vs_engineering_boundary")
    if not is_empty_value(method_boundary):
        items.extend(_string_list(method_boundary))
    for claim in _object_list(claim_boundaries.get("conditional_claims")):
        claim_id = str(_first_resolved(claim.get("claim_id"), "claim")).strip()
        strength = str(_first_resolved(claim.get("maximum_strength"), "bounded")).strip()
        conditions = "; ".join(_string_list(claim.get("conditions")))
        items.append(f"{claim_id} is capped at {strength} strength when: {conditions}" if conditions else f"{claim_id} is capped at {strength} strength")
    items.extend(_string_list(_get(context_reboost, "novelty_audit_resolution.claim_constraints")))
    return _dedupe_strings(items)


def _must_preserve_components(method_mechanism: Mapping[str, Any], method_intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = _object_list(_first_resolved(method_mechanism.get("must_preserve_components"), method_intent.get("must_preserve_components")))
    if direct:
        return [_normalize_component(item) for item in direct]

    must_ids = _dedupe_strings(
        [
            *_string_list(method_mechanism.get("must_preserve_module_ids")),
            *_string_list(method_intent.get("must_preserve_module_ids")),
        ]
    )
    candidates = _object_list(_first_resolved(method_intent.get("candidate_components"), method_intent.get("candidate_modules")))
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(_first_resolved(candidate.get("component_id"), candidate.get("module_id"), candidate.get("id"), "")).strip()
        if must_ids and candidate_id not in must_ids:
            continue
        payload = _normalize_component(candidate)
        payload.setdefault("component_id", candidate_id)
        if not payload.get("intended_role"):
            payload["intended_role"] = str(_first_resolved(candidate.get("intended_role"), candidate.get("classification"), candidate.get("mechanism"), "preserve declared module"))
        result.append(payload)
    if not result:
        invariants = _object_list(method_mechanism.get("mechanism_invariants"))
        for idx, module_id in enumerate(must_ids, start=1):
            invariant = invariants[idx - 1] if idx <= len(invariants) else {}
            result.append(
                {
                    "component_id": module_id,
                    "name": module_id,
                    "intended_role": str(_first_resolved(invariant.get("statement"), "Preserve module declared in must_preserve_module_ids.")),
                }
            )
    return _dedupe_objects(result, ["component_id", "name"])


def _normalize_component(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    if "component_id" not in payload and "module_id" in payload:
        payload["component_id"] = payload["module_id"]
    if "intended_role" not in payload:
        payload["intended_role"] = _first_resolved(payload.get("role"), payload.get("classification"), payload.get("mechanism"), "")
    return payload


def _baseline_replacement_policy(baselines: list[dict[str, Any]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for baseline in baselines:
        policy = _mapping(baseline.get("substitution_policy"))
        if not policy:
            continue
        entries.append(
            {
                "baseline_id": baseline.get("baseline_id"),
                "name": baseline.get("name"),
                "allowed": policy.get("allowed"),
                "approval_required": policy.get("approval_required"),
                "conditions": policy.get("conditions") or [],
                "candidate_substitutes": policy.get("candidate_substitutes") or [],
            }
        )
    if not entries:
        return {}
    return {
        "default": "Do not replace required baselines silently; follow each baseline substitution_policy.",
        "per_baseline": entries,
    }


def _baseline_identity_requirements(baselines: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for baseline in baselines:
        baseline_id = str(baseline.get("baseline_id") or baseline.get("id") or "baseline")
        name = str(baseline.get("name") or baseline_id)
        source = baseline.get("implementation_source")
        target = baseline.get("reproduction_target")
        if not is_empty_value(source):
            items.append(f"{baseline_id} ({name}) identity must preserve implementation source/provenance: {source}.")
        if not is_empty_value(target):
            items.append(f"{baseline_id} ({name}) reproduction target: {target}.")
    return _dedupe_strings(items)


def _baseline_fairness_constraints(baselines: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    key_labels = {
        "same_data_split": "Use the same data split across baselines.",
        "same_metric_definition": "Use the same metric definition across baselines.",
        "same_tuning_budget": "Use the same tuning budget across baselines.",
        "same_evaluation_protocol": "Use the same evaluation protocol across baselines.",
    }
    for baseline in baselines:
        contract = _mapping(baseline.get("fairness_contract"))
        for key, label in key_labels.items():
            if contract.get(key) is True:
                items.append(label)
        items.extend(_string_list(contract.get("additional_constraints")))
    return _dedupe_strings(items)


def _baseline_forbidden_repairs(baselines: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for baseline in baselines:
        policy = _mapping(baseline.get("substitution_policy"))
        if policy.get("allowed") is False:
            approval = str(policy.get("approval_required") or "human")
            items.append(f"Do not replace {baseline.get('baseline_id')} ({baseline.get('name')}) without {approval} approval.")
    items.extend(
        item
        for item in _baseline_fairness_constraints(baselines)
        if "Do not weaken" in item or "without human review" in item
    )
    return _dedupe_strings(items)


def _baseline_reproduction_acceptance(baselines: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for baseline in baselines:
        target = baseline.get("reproduction_target")
        if not is_empty_value(target):
            items.append(f"{baseline.get('baseline_id')} ({baseline.get('name')}): {target}")
    items.extend(_baseline_fairness_constraints(baselines))
    return _dedupe_strings(items)


def _experiment_task(study_scope: Mapping[str, Any], minimum_loop: Any) -> str | object:
    return _first_resolved(study_scope.get("target_setting"), "; ".join(_string_list(study_scope.get("tasks"))), _minimum_loop(minimum_loop))


def _benchmarks_from_minimum_loop(minimum_loop: Any, study_scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    loop = _mapping(minimum_loop)
    experiments = _object_list(loop.get("required_experiments"))
    result: list[dict[str, Any]] = []
    for idx, item in enumerate(experiments, start=1):
        result.append(
            {
                "id": str(_first_resolved(item.get("experiment_id"), item.get("id"), f"E{idx}")),
                "name": str(_first_resolved(item.get("purpose"), item.get("run_type"), f"experiment_{idx}")),
                "version": str(_first_resolved(item.get("run_type"), "predeclared")),
            }
        )
    if not result:
        for idx, task in enumerate(_string_list(study_scope.get("tasks")), start=1):
            result.append({"id": f"T{idx}", "name": task, "version": "study_scope"})
    return result


def _datasets_from_study_scope(study_scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"dataset_id": f"D{idx}", "name": name, "version": "study_scope"} for idx, name in enumerate(_string_list(study_scope.get("datasets")), start=1)]


def _splits_from_study_scope(study_scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    splits: list[dict[str, Any]] = []
    for idx, item in enumerate(_string_list(study_scope.get("datasets")), start=1):
        if "split" in item.lower():
            splits.append({"split_id": f"S{idx}", "dataset_id": f"D{idx}", "description": item})
    return splits


def _seeds_from_scope(study_scope: Mapping[str, Any]) -> dict[str, Any] | object:
    for constraint in _string_list(study_scope.get("constraints")):
        if "seed" not in constraint.lower():
            continue
        seeds = [int(item) for item in re.findall(r"\b\d+\b", constraint)]
        if seeds:
            return {"seeds": seeds, "source": constraint}
    return MISSING


def _required_experiment_types(minimum_loop: Mapping[str, Any], study_scope: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for experiment in _object_list(minimum_loop.get("required_experiments")):
        items.append(str(_first_resolved(experiment.get("run_type"), experiment.get("purpose"), experiment.get("experiment_id"), "")))
    items.extend(_string_list(study_scope.get("tasks")))
    return _dedupe_strings(items)


def _comparison_axes(claim_matrix: list[dict[str, Any]], baselines: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for claim in claim_matrix:
        for requirement in _object_list(claim.get("evidence_requirements")):
            items.extend(_string_list(requirement.get("comparison")))
            metric = requirement.get("metric_or_observation")
            setting = requirement.get("dataset_or_setting")
            if not is_empty_value(metric) or not is_empty_value(setting):
                items.append(f"{claim.get('claim_id')}: {metric} on {setting}")
    if baselines:
        items.append("Compare proposed method against every required baseline under the same split, seed policy, and metric definition.")
    return _dedupe_strings(items)


def _important_subsets(study_scope: Mapping[str, Any]) -> list[str]:
    return [
        metric
        for metric in _string_list(study_scope.get("metrics"))
        if any(token in metric.lower() for token in ("subgroup", "few-shot", "zero-shot", "10%", "20%", "full target"))
    ]


def _known_confounders(context_reboost: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for risk in _object_list(context_reboost.get("risk_register")):
        description = _first_resolved(risk.get("category"), risk.get("description"), risk.get("risk_id"))
        if description is not MISSING:
            items.append(str(description))
    for mismatch in _object_list(context_reboost.get("known_context_mismatches")):
        topic = _first_resolved(mismatch.get("topic"), mismatch.get("mismatch_id"))
        if topic is not MISSING:
            items.append(str(topic))
    return _dedupe_strings(items)


def _interpretation_boundaries(claim_matrix: list[dict[str, Any]], claim_boundaries: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("support_criteria", "weaken_criteria", "falsification_criteria", "prohibited_interpretations"):
        values: list[str] = []
        for claim in claim_matrix:
            values.extend(_string_list(claim.get(key)))
        if values:
            result[key] = _dedupe_strings(values)
    must_not = _string_list(claim_boundaries.get("must_not_claim"))
    if must_not:
        result["must_not_claim"] = must_not
    conditional = _object_list(claim_boundaries.get("conditional_claims"))
    if conditional:
        result["conditional_claims"] = conditional
    return result


def _protocol_constraints(study_scope: Mapping[str, Any], execution_contract: Mapping[str, Any], baselines: list[dict[str, Any]]) -> list[str]:
    items = _string_list(study_scope.get("constraints"))
    for rule in _object_list(execution_contract.get("authority_rules")):
        action = rule.get("action")
        authority = rule.get("authority")
        if not is_empty_value(action) and not is_empty_value(authority):
            items.append(f"{action}: {authority}")
    items.extend(_baseline_fairness_constraints(baselines))
    return _dedupe_strings(items)


def _statistical_policy(claim_matrix: list[dict[str, Any]], study_scope: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    constraints = _string_list(study_scope.get("constraints"))
    if constraints:
        result["fixed_protocol_constraints"] = "; ".join(constraints)
    for key in ("support_criteria", "weaken_criteria", "falsification_criteria"):
        values: list[str] = []
        for claim in claim_matrix:
            values.extend(_string_list(claim.get(key)))
        if values:
            result[key] = "; ".join(_dedupe_strings(values))
    return result


def _resource_requirements(study_scope: Mapping[str, Any], baselines: list[dict[str, Any]], writer_handoff: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for idx, name in enumerate(_string_list(study_scope.get("datasets")), start=1):
        result.append({"requirement_id": f"D{idx}", "resource_type": "dataset", "name": name, "required": True})
    for baseline in baselines:
        source = baseline.get("implementation_source")
        result.append(
            {
                "requirement_id": str(baseline.get("baseline_id") or baseline.get("name")),
                "resource_type": "baseline_implementation",
                "name": str(baseline.get("name") or baseline.get("baseline_id")),
                "required": True,
                "source": "" if is_empty_value(source) else str(source),
            }
        )
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        result.append(
            {
                "requirement_id": str(artifact.get("artifact_id") or artifact.get("artifact_type")),
                "resource_type": "output_artifact",
                "name": str(artifact.get("artifact_type") or artifact.get("artifact_id")),
                "required": True,
            }
        )
    return _dedupe_objects(result, ["requirement_id", "name"])


def _resource_acceptance_criteria(execution_contract: Mapping[str, Any], writer_handoff: Mapping[str, Any], baselines: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    policy = _mapping(execution_contract.get("resource_policy"))
    if policy.get("license_checks_required") is True:
        items.append("Resource and external-code use requires license checks.")
    if policy.get("checksum_required") is True:
        items.append("Downloaded or prepared resources require checksums.")
    if policy.get("citation_required") is True:
        items.append("External resources require citation/provenance records.")
    items.extend(_baseline_reproduction_acceptance(baselines))
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        fields = ", ".join(_string_list(artifact.get("required_fields")))
        if fields:
            items.append(f"{artifact.get('artifact_type')} must include {fields}.")
    return _dedupe_strings(items)


def _resource_license_constraints(execution_contract: Mapping[str, Any]) -> list[str]:
    policy = _mapping(execution_contract.get("resource_policy"))
    items: list[str] = []
    if policy.get("authenticated_resources_allowed") is False:
        items.append("Authenticated resources are not allowed.")
    if policy.get("license_checks_required") is True:
        items.append("License checks are required before using datasets, code, or baselines.")
    if policy.get("public_resources_allowed") is True:
        items.append("Public resources are allowed within the declared path and license constraints.")
    return items


def _work_root_from_execution_contract(execution_contract: Mapping[str, Any]) -> str | object:
    return _work_root_from_path_lines(_string_list(execution_contract.get("write_paths")))


def _work_root_from_path_lines(paths: Sequence[str]) -> str | object:
    for raw in paths:
        path = re.sub(r"^(?:rw|ro)\s+", "", str(raw).strip()).strip()
        if path.rstrip("/") == "external_executor/expr":
            return "external_executor/expr"
    for raw in paths:
        path = re.sub(r"^(?:rw|ro)\s+", "", str(raw).strip()).strip()
        if path.startswith("external_executor/expr/"):
            return "external_executor/expr"
    for raw in paths:
        path = re.sub(r"^(?:rw|ro)\s+", "", str(raw).strip()).strip()
        if path.rstrip("/") == "external_executor/workdir":
            return "external_executor/workdir"
    for raw in paths:
        path = re.sub(r"^(?:rw|ro)\s+", "", str(raw).strip()).strip()
        if path.startswith("external_executor/workdir/"):
            return "external_executor/workdir"
    return MISSING


def _ablation_switch_requirements(method_intent: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for ablation in _object_list(_first_resolved(method_intent.get("mechanism_to_ablation"), method_intent.get("mechanism_to_ablation_plan"))):
        ablation_id = str(_first_resolved(ablation.get("ablation_id"), ablation.get("id"), ablation.get("mechanism"), "ablation"))
        planned = _first_resolved(ablation.get("planned_test"), ablation.get("test"), ablation.get("mechanism"))
        module_ids = ", ".join(_string_list(ablation.get("module_ids")))
        if planned is not MISSING:
            suffix = f" for modules {module_ids}" if module_ids else ""
            items.append(f"{ablation_id}: expose a reproducible switch/test for {planned}{suffix}.")
    return _dedupe_strings(items)


def _implementation_acceptance(context_reboost: Mapping[str, Any], writer_handoff: Mapping[str, Any]) -> list[str]:
    items = _string_list(_get(context_reboost, "project_goal.success_criteria"))
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        items.append(f"Produce {artifact.get('artifact_type')} with audit status and provenance.")
    return _dedupe_strings(items)


def _scope_change_triggers(execution_contract: Mapping[str, Any], claim_boundaries: Mapping[str, Any]) -> list[str]:
    items = _string_list(claim_boundaries.get("narrowing_triggers"))
    policy = _mapping(execution_contract.get("scope_change_policy"))
    if policy:
        request = policy.get("request_artifact")
        if not is_empty_value(request):
            items.append(f"Major scope changes require {request}.")
        if policy.get("silent_changes_forbidden") is True:
            items.append("Silent scope changes are forbidden.")
    for rule in _object_list(execution_contract.get("authority_rules")):
        if str(rule.get("authority") or "").lower() in {"human_approval", "forbidden"}:
            items.append(f"{rule.get('action')}: {rule.get('authority')}")
    return _dedupe_strings(items)


def _attribution_requirements(method_intent: Mapping[str, Any], claim_matrix: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for ablation in _object_list(_first_resolved(method_intent.get("mechanism_to_ablation"), method_intent.get("mechanism_to_ablation_plan"))):
        planned = _first_resolved(ablation.get("planned_test"), ablation.get("test"), ablation.get("mechanism"))
        if planned is not MISSING:
            items.append(f"Ablation requirement: {planned}")
    for claim in claim_matrix:
        related = ", ".join(_string_list(claim.get("related_module_ids")))
        if related:
            items.append(f"{claim.get('claim_id')} attribution must trace evidence to modules: {related}.")
    return _dedupe_strings(items)


def _human_review_triggers(execution_contract: Mapping[str, Any], claim_boundaries: Mapping[str, Any]) -> list[str]:
    items = _scope_change_triggers(execution_contract, claim_boundaries)
    for rule in _object_list(execution_contract.get("authority_rules")):
        if str(rule.get("authority") or "").lower() == "human_approval":
            items.append(f"Human approval required before: {rule.get('action')}.")
    return _dedupe_strings(items)


def _isolation_requirements(execution_contract: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    allowed = _string_list(execution_contract.get("allowed_paths"))
    prohibited = _string_list(execution_contract.get("prohibited_paths"))
    if allowed:
        items.append("Write only within declared allowed/write paths.")
    if prohibited:
        items.append(f"Do not modify prohibited paths: {', '.join(prohibited)}.")
    policy = _mapping(execution_contract.get("resource_policy"))
    if policy.get("authenticated_resources_allowed") is False:
        items.append("Do not use authenticated resources.")
    return _dedupe_strings(items)


def _allowed_run_levels(execution_contract: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    write_paths = _string_list(execution_contract.get("write_paths"))
    if write_paths:
        items.append("Preflight, design, resource preparation, implementation, and run artifacts are allowed only under declared write paths.")
    policy = _mapping(execution_contract.get("resource_policy"))
    if policy.get("public_resources_allowed") is True:
        items.append("Public-resource experiment runs are allowed after executor mode selection, within the resource policy.")
    if policy.get("authenticated_resources_allowed") is False:
        items.append("Authenticated-resource runs are not allowed.")
    return _dedupe_strings(items)


def _resume_policy(iteration_budget: Mapping[str, Any], execution_contract: Mapping[str, Any]) -> list[str]:
    items = _string_list(iteration_budget.get("stop_conditions"))
    plateau = iteration_budget.get("plateau_definition")
    if not is_empty_value(plateau):
        items.append(f"Stop or resume decisions must respect plateau definition: {plateau}")
    write_paths = _string_list(execution_contract.get("write_paths"))
    for path in ("external_executor/executor_status.json", "external_executor/report/run_manifest.json", "external_executor/job_state.json"):
        if path in write_paths:
            items.append(f"Resume state must preserve and update {path}.")
    return _dedupe_strings(items)


def _required_artifact_types(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any] | None = None) -> list[str]:
    items = [str(item.get("artifact_type")) for item in _object_list(writer_handoff.get("required_artifacts")) if not is_empty_value(item.get("artifact_type"))]
    if expected_outputs:
        items.extend(_string_list(expected_outputs.get("required_files")))
    return _dedupe_strings(items)


def _realized_method_requirements(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any] | None = None) -> list[str]:
    items: list[str] = []
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        if artifact.get("artifact_type") == "realized_method_package":
            items.append(str(_first_resolved(artifact.get("description"), "Produce realized_method_package.")))
            fields = ", ".join(_string_list(artifact.get("required_fields")))
            if fields:
                items.append(f"realized_method_package requires fields: {fields}.")
    items.extend(item for item in _string_list(writer_handoff.get("must_include")) if "method" in item.lower())
    if expected_outputs:
        semantics = _mapping(expected_outputs.get("field_semantics"))
        text = semantics.get("realized_method_package")
        if not is_empty_value(text):
            items.append(str(text))
    return _dedupe_strings(items)


def _framework_figure_requirements(method_intent: Mapping[str, Any], writer_handoff: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(_first_resolved(method_intent.get("initial_framework_figure_intent"), method_intent.get("initial_framework_figure_sketch")))
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        if artifact.get("artifact_type") == "final_framework_figure":
            result["required_artifact"] = artifact
    return result


def _required_figure_types(writer_handoff: Mapping[str, Any]) -> list[str]:
    return _dedupe_strings(
        item.get("artifact_type")
        for item in _object_list(writer_handoff.get("required_artifacts"))
        if "figure" in str(item.get("artifact_type") or "").lower()
    )


def _required_table_types(writer_handoff: Mapping[str, Any]) -> list[str]:
    return _dedupe_strings(
        item.get("artifact_type")
        for item in _object_list(writer_handoff.get("required_artifacts"))
        if "table" in str(item.get("artifact_type") or "").lower() or "inventory" in str(item.get("artifact_type") or "").lower()
    )


def _visual_traceability_requirements(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any] | None = None) -> list[str]:
    items: list[str] = []
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        fields = ", ".join(_string_list(artifact.get("required_fields")))
        if fields:
            items.append(f"{artifact.get('artifact_type')} traceability fields: {fields}.")
    if expected_outputs:
        fields = ", ".join(_string_list(expected_outputs.get("artifact_required")))
        if fields:
            items.append(f"Every artifact entry requires: {fields}.")
    return _dedupe_strings(items)


def _writer_handoff_requirements(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any] | None = None) -> list[str]:
    items = _string_list(writer_handoff.get("must_include"))
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        description = _first_resolved(artifact.get("description"), artifact.get("artifact_type"))
        if description is not MISSING:
            items.append(str(description))
    for forbidden in _string_list(writer_handoff.get("must_not_use_as_final_fact_source")):
        items.append(f"Do not use {forbidden} as final fact source.")
    if expected_outputs:
        semantics = _mapping(expected_outputs.get("field_semantics"))
        text = semantics.get("writer_handoff")
        if not is_empty_value(text):
            items.append(str(text))
    return _dedupe_strings(items)


def _t7_requirements(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any] | None = None) -> list[str]:
    items: list[str] = []
    for artifact in _object_list(writer_handoff.get("required_artifacts")):
        legacy_audit_key = "requires_" + "t7_audit"
        if artifact.get("requires_handoff_validation") is True or artifact.get(legacy_audit_key) is True:
            fields = ", ".join(_string_list(artifact.get("required_fields")))
            suffix = f" with fields {fields}" if fields else ""
            items.append(f"{artifact.get('artifact_type')} requires final T8 handoff validation{suffix}.")
    if expected_outputs:
        for key in ("result_to_claim", "writer_handoff", "realized_method_package", "final_framework_figure"):
            text = _mapping(expected_outputs.get("field_semantics")).get(key)
            if not is_empty_value(text):
                items.append(str(text))
    return _dedupe_strings(items)


def _handoff_readiness_requirements(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any] | None = None) -> list[str]:
    items: list[str] = []
    required = _object_list(writer_handoff.get("required_artifacts"))
    if required:
        items.append("All writer_handoff_contract.required_artifacts must be present before downstream writing.")
    items.extend(_writer_handoff_requirements(writer_handoff, expected_outputs))
    return _dedupe_strings(items)


def _evidence_ceiling_policy(claim_matrix: list[dict[str, Any]], claim_boundaries: Mapping[str, Any], paper_card_policy: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for claim in claim_matrix:
        claim_id = str(claim.get("claim_id") or "")
        ceiling = claim.get("initial_strength_ceiling")
        if claim_id and not is_empty_value(ceiling):
            result[f"claim_{claim_id}"] = str(ceiling)
    for key in ("allowed_uses", "prohibited_uses"):
        values = _string_list(paper_card_policy.get(key))
        if values:
            result[f"paper_card_{key}"] = "; ".join(values)
    must_not = _string_list(claim_boundaries.get("must_not_claim"))
    if must_not:
        result["must_not_claim"] = "; ".join(must_not)
    return result


def _metric_output_contract(expected_outputs: Mapping[str, Any]) -> list[str]:
    fields = _string_list(expected_outputs.get("metric_required"))
    if fields:
        return [f"Each metric record must include: {', '.join(fields)}."]
    return []


def _logging_requirements(expected_outputs: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for path in _string_list(expected_outputs.get("required_files")):
        if any(token in path for token in ("logs", "configs", "raw_results", "run_manifest")):
            items.append(f"Write and preserve {path}.")
    return _dedupe_strings(items)


def _testing_requirements(expected_outputs: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    status_fields = _string_list(expected_outputs.get("status_required"))
    manifest_fields = _string_list(expected_outputs.get("run_manifest_required"))
    if status_fields:
        items.append(f"executor_status must include: {', '.join(status_fields)}.")
    if manifest_fields:
        items.append(f"run_manifest must include: {', '.join(manifest_fields)}.")
    if "result_pack" in _string_list(expected_outputs.get("required")):
        items.append("Validate result_pack against the expected output sections before handoff.")
    return items


def _review_requirements(writer_handoff: Mapping[str, Any], expected_outputs: Mapping[str, Any]) -> list[str]:
    items = _t7_requirements(writer_handoff, expected_outputs)
    artifact_fields = _string_list(expected_outputs.get("artifact_required"))
    if artifact_fields:
        items.append(f"Review artifact inventory for required fields: {', '.join(artifact_fields)}.")
    return _dedupe_strings(items)


def _experiments_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _object_list(exp_plan.get("experiments"))


def _task_from_exp_plan(exp_plan: Mapping[str, Any]) -> str | object:
    names = [str(_first_resolved(item.get("name"), item.get("title"), item.get("id"), "")) for item in _experiments_from_exp_plan(exp_plan)]
    names = [name for name in names if name]
    return "; ".join(names) if names else MISSING


def _benchmarks_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for idx, experiment in enumerate(_experiments_from_exp_plan(exp_plan), start=1):
        result.append(
            {
                "id": str(_first_resolved(experiment.get("id"), f"E{idx}")),
                "name": str(_first_resolved(experiment.get("name"), experiment.get("title"), f"experiment_{idx}")),
                "version": str(_first_resolved(experiment.get("hypothesis_ref"), "exp_plan")),
            }
        )
    return result


def _datasets_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for exp_idx, experiment in enumerate(_experiments_from_exp_plan(exp_plan), start=1):
        exp_id = str(_first_resolved(experiment.get("id"), f"exp{exp_idx}"))
        for data_idx, dataset in enumerate(_object_list(experiment.get("datasets")), start=1):
            payload = dict(dataset)
            payload.setdefault("dataset_id", f"{exp_id}_D{data_idx}")
            payload.setdefault("version", exp_id)
            payload["experiment_id"] = exp_id
            result.append(payload)
    return _dedupe_objects(result, ["dataset_id", "name", "experiment_id"])


def _splits_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for dataset in _datasets_from_exp_plan(exp_plan):
        split = dataset.get("split")
        if is_empty_value(split):
            continue
        dataset_id = str(dataset.get("dataset_id"))
        result.append(
            {
                "split_id": f"{dataset_id}_split",
                "dataset_id": dataset_id,
                "description": str(split),
                "experiment_id": str(dataset.get("experiment_id") or ""),
            }
        )
    return result


def _preprocessing_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for experiment in _experiments_from_exp_plan(exp_plan):
        for step in _object_list(experiment.get("steps")):
            action = str(step.get("action") or "")
            details = str(step.get("details") or "")
            if any(token in f"{action} {details}".lower() for token in ("prepare", "preprocess", "data", "embedding", "cache")):
                items.append(f"{action}: {details}".strip(": "))
    return _dedupe_strings(items)


def _metrics_from_exp_plan(exp_plan: Mapping[str, Any], *, primary: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for experiment in _experiments_from_exp_plan(exp_plan):
        exp_id = str(experiment.get("id") or "")
        for idx, metric in enumerate(_object_list(experiment.get("metrics")), start=1):
            is_primary = bool(metric.get("primary"))
            if is_primary != primary:
                continue
            payload = dict(metric)
            payload.setdefault("name", metric.get("metric") or f"{exp_id}_metric_{idx}")
            payload.setdefault("direction", "unknown")
            payload.setdefault("aggregation", "unspecified")
            payload["experiment_id"] = exp_id
            result.append(payload)
    return _dedupe_objects(result, ["experiment_id", "name"])


def _seed_policy_from_exp_plan(exp_plan: Mapping[str, Any]) -> dict[str, Any] | object:
    text_parts: list[str] = []
    for experiment in _experiments_from_exp_plan(exp_plan):
        text_parts.extend(_string_list(experiment.get("notes")))
        for step in _object_list(experiment.get("steps")):
            text_parts.extend([str(step.get("action") or ""), str(step.get("details") or "")])
    for text in text_parts:
        if "seed" not in text.lower():
            continue
        seeds = [int(item) for item in re.findall(r"\b\d+\b", text)]
        if seeds:
            return {"seeds": seeds, "source": text}
    return MISSING


def _minimum_loop_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for experiment in _experiments_from_exp_plan(exp_plan):
        name = _first_resolved(experiment.get("name"), experiment.get("title"), experiment.get("id"))
        if name is not MISSING:
            result.append(str(name))
    return result


def _required_experiment_types_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[str]:
    return _minimum_loop_from_exp_plan(exp_plan)


def _comparison_axes_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for experiment in _experiments_from_exp_plan(exp_plan):
        exp_id = str(experiment.get("id") or "")
        for baseline in _object_list(experiment.get("baselines")):
            if not is_empty_value(baseline.get("name")):
                items.append(f"{exp_id}: compare against {baseline.get('name')}.")
        for criterion in _object_list(experiment.get("success_criteria")):
            metric = criterion.get("metric")
            threshold = criterion.get("threshold")
            comparison = criterion.get("comparison")
            if not is_empty_value(metric):
                items.append(f"{exp_id}: {metric} {comparison or ''} {threshold or ''}".strip())
    return _dedupe_strings(items)


def _practical_thresholds_from_exp_plan(exp_plan: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for experiment in _experiments_from_exp_plan(exp_plan):
        for criterion in _object_list(experiment.get("success_criteria")):
            metric = criterion.get("metric")
            threshold = criterion.get("threshold")
            if is_empty_value(metric) or is_empty_value(threshold):
                continue
            match = re.search(r"[-+]?\d+(?:\.\d+)?", str(threshold))
            if match:
                result[str(metric)] = float(match.group(0))
    return result


def _important_subsets_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for metric in [*_metrics_from_exp_plan(exp_plan, primary=True), *_metrics_from_exp_plan(exp_plan, primary=False)]:
        text = " ".join(str(metric.get(key) or "") for key in ("name", "target"))
        if any(token in text.lower() for token in ("subgroup", "few-shot", "zero-shot", "10%", "20%", "full target")):
            items.append(text)
    return _dedupe_strings(items)


def _protocol_constraints_from_exp_plan(exp_plan: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    for split in _splits_from_exp_plan(exp_plan):
        items.append(f"{split.get('dataset_id')}: {split.get('description')}")
    return _dedupe_strings(items)


def _dedupe_objects(values: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        payload = dict(value)
        identity = tuple(str(payload.get(key) or "") for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(payload)
    return result


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
        "bridge_plan": "literature/bridge_domain_plan.json",
        "bridge_catalog_index": "literature/cross_domain_catalogs/index.json",
        "bridge_catalog_root": "literature/cross_domain_catalogs",
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
    if isinstance(value, Mapping):
        selected = _mapping_text(value)
        return [selected] if selected else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                result.append(_mapping_text(item))
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


def _mapping_text(item: Mapping[str, Any]) -> str:
    condition = item.get("condition")
    trigger = item.get("trigger")
    action = item.get("required_action")
    if not is_empty_value(condition) and not is_empty_value(trigger):
        suffix = f" -> {action}" if not is_empty_value(action) else ""
        return f"{condition}: {trigger}{suffix}".strip()
    authority_action = item.get("action")
    authority = item.get("authority")
    if not is_empty_value(authority_action) and not is_empty_value(authority):
        return f"{authority_action}: {authority}".strip()
    selected = _first_resolved(
        item.get("boundary"),
        item.get("claim"),
        item.get("statement"),
        item.get("description"),
        item.get("artifact_type"),
        item.get("name"),
        item.get("trigger"),
        item.get("action"),
        item.get("value"),
    )
    if selected is MISSING:
        selected = json.dumps(dict(item), ensure_ascii=False, sort_keys=True)
    return str(selected).strip()


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
            if question is not MISSING and not is_empty_value(question):
                questions.append(str(question))
                continue
            claim_id = item.get("claim_id")
            for requirement in _object_list(item.get("evidence_requirements")):
                evidence_type = requirement.get("evidence_type")
                metric = requirement.get("metric_or_observation")
                setting = requirement.get("dataset_or_setting")
                if not is_empty_value(evidence_type) or not is_empty_value(metric):
                    questions.append(f"{claim_id}: verify {evidence_type or 'evidence'} using {metric or 'declared metrics'} on {setting or 'declared setting'}.")
    return questions


def _max_iterations(value: Any) -> int | None:
    if value is MISSING:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        for key in ("max_iterations", "iterations", "iteration_budget", "max_rounds"):
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
