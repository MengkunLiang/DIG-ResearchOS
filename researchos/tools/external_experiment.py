from __future__ import annotations

"""External experiment handoff and evidence tools.

ResearchOS owns protocol, provenance, integrity checks, and claim mapping.
External executors such as Codex CLI, Claude Code, or a manual runner own code
implementation and experiment execution in an isolated workspace. These tools
only read/write workspace artifacts and provide a mock dry-run path for tests.
"""

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _artifact_record(workspace: Path, rel_path: str, *, role: str, kind: str = "file") -> dict[str, Any]:
    path = workspace / rel_path
    record: dict[str, Any] = {"path": rel_path, "kind": kind, "role": role, "exists": path.exists()}
    if path.exists() and path.is_file():
        record.update({"sha256": _sha256(path), "bytes": path.stat().st_size})
    return record


def _extract_exp_plan_metrics(exp_plan: dict[str, Any]) -> list[str]:
    metrics: list[str] = []
    for exp in exp_plan.get("experiments", []) or []:
        if not isinstance(exp, dict):
            continue
        for metric in exp.get("metrics", []) or []:
            if isinstance(metric, dict) and metric.get("name"):
                metrics.append(str(metric["name"]))
            elif isinstance(metric, str):
                metrics.append(metric)
    return list(dict.fromkeys(metrics)) or ["task_score"]


def _extract_exp_plan_seeds(project: dict[str, Any]) -> list[int]:
    seed_ensemble = project.get("seed_ensemble") if isinstance(project, dict) else {}
    seeds: list[int] = []
    if isinstance(seed_ensemble, dict):
        for key in ("tier1_seeds", "tier2_seeds", "tier3_seeds"):
            for value in seed_ensemble.get(key, []) or []:
                try:
                    seeds.append(int(value))
                except Exception:
                    continue
    return list(dict.fromkeys(seeds)) or [42]


class BuildExperimentHandoffPackParams(BaseModel):
    executor: Literal["mock_dry_run", "codex_cli", "claude_code_window", "manual_external"] = Field(
        default="mock_dry_run",
        description="External executor selected for this handoff. Tests should use mock_dry_run.",
    )
    output_path: str = Field(default="external_executor/handoff_pack.json")
    prompt_output_path: str = Field(default="external_executor/executor_prompt.md")
    expected_schema_path: str = Field(default="external_executor/expected_outputs_schema.json")
    allowed_paths_path: str = Field(default="external_executor/allowed_paths.txt")
    executor_selection_path: str = Field(default="external_executor/executor_selection.json")
    input_manifest_path: str = Field(default="external_executor/input_manifest.json")
    codex_prompt_path: str = Field(default="external_executor/codex_prompt.md")
    claude_prompt_path: str = Field(default="external_executor/claude_code_prompt.md")
    manual_instructions_path: str = Field(default="external_executor/manual_instructions.md")


class MockExternalDryRunParams(BaseModel):
    handoff_pack_path: str = Field(default="external_executor/handoff_pack.json")
    output_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")


class IngestExternalResultsParams(BaseModel):
    result_pack_path: str = Field(default="external_executor/result_pack.json")
    results_summary_path: str = Field(default="experiments/results_summary.json")
    run_records_path: str = Field(default="experiments/run_records.jsonl")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    ingest_report_path: str = Field(default="experiments/ingest_report.json")


class AuditExperimentIntegrityParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    output_path: str = Field(default="experiments/integrity_audit.json")


class MapResultsToClaimsParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    output_path: str = Field(default="experiments/experimental_claims.json")
    draft_output_path: str = Field(default="drafts/result_to_claim.json")


class BuildExperimentEvidencePackParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    experimental_claims_path: str = Field(default="experiments/experimental_claims.json")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    output_path: str = Field(default="drafts/experiment_evidence_pack.json")


class AuditPaperClaimsParams(BaseModel):
    paper_path: str = Field(default="drafts/paper.tex")
    evidence_pack_path: str = Field(default="drafts/experiment_evidence_pack.json")
    result_to_claim_path: str = Field(default="drafts/result_to_claim.json")
    output_path: str = Field(default="drafts/paper_claim_audit.md")


class BuildExperimentHandoffPackTool(Tool):
    name = "build_experiment_handoff_pack"
    description = "Compile a protocol pack and executor prompt for external experiment execution."
    parameters_schema = BuildExperimentHandoffPackParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildExperimentHandoffPackParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            project = _read_yaml(ws / "project.yaml")
            exp_plan = _read_yaml(ws / "ideation" / "exp_plan.yaml")
            hypotheses = (ws / "ideation" / "hypotheses.md").read_text(encoding="utf-8", errors="replace") if (ws / "ideation" / "hypotheses.md").exists() else ""
            metrics = _extract_exp_plan_metrics(exp_plan)
            seeds = _extract_exp_plan_seeds(project)
            source_artifacts = [
                _artifact_record(ws, "project.yaml", role="project"),
                _artifact_record(ws, "ideation/hypotheses.md", role="hypotheses"),
                _artifact_record(ws, "ideation/exp_plan.yaml", role="experiment_plan"),
                _artifact_record(ws, "ideation/novelty_audit.md", role="novelty_audit"),
                _artifact_record(ws, "literature/synthesis.md", role="literature_synthesis"),
                _artifact_record(ws, "literature/comparison_table.csv", role="comparison_table"),
            ]
            handoff = {
                "version": "1.0",
                "semantics": "external_experiment_handoff_pack_not_execution_result",
                "executor": params.executor,
                "execution_mode": "dry_run" if params.executor == "mock_dry_run" else "external",
                "accepted": False,
                "status": "handoff_compiled",
                "project": {
                    "project_id": project.get("project_id") or project.get("name") or "unknown",
                    "target_venue": project.get("target_venue", ""),
                },
                "experiment_contract": {
                    "metrics": metrics,
                    "seeds": seeds,
                    "experiments": exp_plan.get("experiments", []) if isinstance(exp_plan, dict) else [],
                    "acceptance": {
                        "must_write_result_pack": params.executor != "manual_external",
                        "dry_run_results_must_be_marked_mock_only": True,
                    },
                },
                "source_artifacts": source_artifacts,
                "executor_outputs": {
                    "result_pack": "external_executor/result_pack.json",
                    "status": "external_executor/executor_status.json",
                    "run_manifest": "external_executor/run_manifest.json",
                    "raw_results": "external_executor/raw_results/",
                    "configs": "external_executor/configs/",
                    "logs_dir": "external_executor/logs",
                },
                "allowed_paths": [
                    "external_executor/",
                    "experiments/external_runs/",
                    "experiments/raw_results/",
                    "experiments/figures/",
                    "experiments/tables/",
                ],
                "hypotheses_preview": hypotheses[:1200],
            }
            output_path = self.policy.resolve_write(params.output_path)
            _write_json(output_path, handoff)
            input_manifest = {
                "version": "1.0",
                "semantics": "external_executor_input_manifest",
                "handoff_pack": params.output_path,
                "source_artifacts": source_artifacts,
                "required_executor_outputs": handoff["executor_outputs"],
            }
            _write_json(self.policy.resolve_write(params.input_manifest_path), input_manifest)
            executor_selection = {
                "version": "1.0",
                "semantics": "external_executor_selection",
                "selected_executor": params.executor,
                "fallback_executors": [
                    item
                    for item in ["mock_dry_run", "codex_cli", "claude_code_window", "manual_external"]
                    if item != params.executor
                ],
                "selection_reason": (
                    "mock_dry_run is the default safe protocol test; real experiments must be selected by a later gate."
                    if params.executor == "mock_dry_run"
                    else "Executor selected by caller for external, artifact-protocol execution."
                ),
                "requires_human_confirmation_for_real_run": params.executor != "mock_dry_run",
            }
            _write_json(self.policy.resolve_write(params.executor_selection_path), executor_selection)
            schema = {
                "version": "1.0",
                "semantics": "expected_external_executor_outputs_schema",
                "required": ["semantics", "run_id", "executor", "dry_run", "metrics", "artifacts"],
                "metric_required": ["metric_id", "name", "value", "source_artifact"],
                "artifact_required": ["path", "kind", "role", "sha256"],
                "status_required": ["semantics", "run_id", "status", "accepted", "dry_run"],
                "run_manifest_required": ["semantics", "run_id", "executor", "raw_results", "configs", "logs"],
            }
            _write_json(self.policy.resolve_write(params.expected_schema_path), schema)
            _write_text(
                self.policy.resolve_write(params.allowed_paths_path),
                "\n".join(handoff["allowed_paths"]) + "\n",
            )
            prompt = _render_executor_prompt(handoff, executor=params.executor)
            _write_text(self.policy.resolve_write(params.prompt_output_path), prompt)
            _write_text(self.policy.resolve_write(params.codex_prompt_path), _render_executor_prompt(handoff, executor="codex_cli"))
            _write_text(self.policy.resolve_write(params.claude_prompt_path), _render_executor_prompt(handoff, executor="claude_code_window"))
            _write_text(self.policy.resolve_write(params.manual_instructions_path), _render_manual_instructions(handoff))
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"handoff pack failed: {exc}", error="handoff_failed")
        return ToolResult(
            ok=True,
            content=f"Wrote external experiment handoff pack to {params.output_path}.",
            data={"path": params.output_path, "executor": params.executor},
        )


class MockExternalDryRunTool(Tool):
    name = "mock_external_dry_run"
    description = "Generate a schema-compatible mock external result pack without running real experiments."
    parameters_schema = MockExternalDryRunParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MockExternalDryRunParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            handoff_path = self.policy.resolve_read(params.handoff_pack_path)
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            raw_result_rel = "external_executor/raw_results/mock_results.json"
            config_rel = "external_executor/configs/mock_config.json"
            log_rel = "external_executor/logs/mock_dry_run.log"
            manifest_rel = "external_executor/run_manifest.json"
            heartbeat_rel = "external_executor/heartbeat.json"
            raw_result = {
                "version": "1.0",
                "semantics": "mock_raw_result_file_not_scientific_evidence",
                "run_id": "mock_dry_run",
                "metrics": [],
            }
            metrics = []
            for idx, name in enumerate((handoff.get("experiment_contract") or {}).get("metrics", []) or ["task_score"], start=1):
                metric = (
                    {
                        "metric_id": f"mock_metric_{idx}",
                        "experiment_id": "mock_dry_run",
                        "name": str(name),
                        "value": round(0.7 + idx * 0.01, 4),
                        "unit": "score",
                        "dataset": "mock_dataset",
                        "seed": ((handoff.get("experiment_contract") or {}).get("seeds") or [42])[0],
                        "source_artifact": raw_result_rel,
                        "mock_only": True,
                    }
                )
                metrics.append(metric)
                raw_result["metrics"].append(metric)
            _write_json(self.policy.resolve_write(raw_result_rel), raw_result)
            config = {
                "version": "1.0",
                "semantics": "mock_external_executor_config",
                "run_id": "mock_dry_run",
                "executor": handoff.get("executor", "mock_dry_run"),
                "seeds": (handoff.get("experiment_contract") or {}).get("seeds") or [42],
                "mock_only": True,
            }
            _write_json(self.policy.resolve_write(config_rel), config)
            log_path = self.policy.resolve_write(log_rel)
            _write_text(log_path, "mock dry-run completed; no real experiment executed\n")
            heartbeat = {
                "version": "1.0",
                "semantics": "external_executor_heartbeat",
                "run_id": "mock_dry_run",
                "status": "done",
                "dry_run": True,
                "mock_only": True,
            }
            _write_json(self.policy.resolve_write(heartbeat_rel), heartbeat)
            artifacts = [
                _artifact_record(ws, raw_result_rel, role="mock_raw_results", kind="raw_results"),
                _artifact_record(ws, config_rel, role="mock_config", kind="config"),
                _artifact_record(ws, log_rel, role="mock_log", kind="log"),
            ]
            run_manifest = {
                "version": "1.0",
                "semantics": "external_executor_run_manifest",
                "run_id": "mock_dry_run",
                "executor": handoff.get("executor", "mock_dry_run"),
                "dry_run": True,
                "mock_only": True,
                "raw_results": [raw_result_rel],
                "configs": [config_rel],
                "logs": [log_rel],
                "artifacts": artifacts,
            }
            _write_json(self.policy.resolve_write(manifest_rel), run_manifest)
            result_pack = {
                "version": "1.0",
                "semantics": "external_executor_result_pack",
                "run_id": "mock_dry_run",
                "executor": handoff.get("executor", "mock_dry_run"),
                "dry_run": True,
                "mock_only": True,
                "metrics": metrics,
                "artifacts": artifacts,
                "run_manifest": manifest_rel,
                "logs": [{"path": log_rel, "level": "info"}],
                "limitations": ["mock_dry_run: not evidence for paper claims"],
            }
            _write_json(self.policy.resolve_write(params.output_path), result_pack)
            status = {
                "version": "1.0",
                "semantics": "external_executor_status",
                "run_id": "mock_dry_run",
                "status": "done",
                "accepted": False,
                "dry_run": True,
                "mock_only": True,
                "heartbeat": heartbeat_rel,
                "run_manifest": manifest_rel,
            }
            _write_json(self.policy.resolve_write(params.status_path), status)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"mock dry-run failed: {exc}", error="mock_dry_run_failed")
        return ToolResult(ok=True, content=f"Wrote mock result pack to {params.output_path}.", data={"path": params.output_path})


class IngestExternalResultsTool(Tool):
    name = "ingest_external_results"
    description = "Normalize an external executor result pack into ResearchOS result artifacts."
    parameters_schema = IngestExternalResultsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = IngestExternalResultsParams(**kwargs)
        try:
            result_pack_path = self.policy.resolve_read(params.result_pack_path)
            result_pack = json.loads(result_pack_path.read_text(encoding="utf-8"))
            if result_pack.get("semantics") != "external_executor_result_pack":
                return ToolResult(ok=False, content="result_pack semantics invalid", error="invalid_result_pack")
            metrics = result_pack.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                return ToolResult(ok=False, content="result_pack metrics missing", error="missing_metrics")
            experiments = [
                {
                    "experiment_id": str(metric.get("experiment_id") or result_pack.get("run_id") or "external_run"),
                    "metrics": {str(metric.get("name")): metric.get("value")},
                    "seed": metric.get("seed"),
                    "source_artifact": metric.get("source_artifact"),
                    "mock_only": bool(metric.get("mock_only") or result_pack.get("mock_only")),
                }
                for metric in metrics
                if isinstance(metric, dict) and metric.get("name") and metric.get("value") is not None
            ]
            summary = {
                "version": "1.0",
                "semantics": "external_executor_results_summary",
                "source": "external_executor",
                "run_id": result_pack.get("run_id"),
                "dry_run": bool(result_pack.get("dry_run")),
                "mock_only": bool(result_pack.get("mock_only")),
                "ingest_report_ref": params.ingest_report_path,
                "experiments": experiments,
                "metrics": metrics,
                "quality_status": "mock_only" if result_pack.get("mock_only") else "ingested_unverified",
            }
            _write_json(self.policy.resolve_write(params.results_summary_path), summary)
            run_records = self.policy.resolve_write(params.run_records_path)
            run_records.parent.mkdir(parents=True, exist_ok=True)
            run_records.write_text(json.dumps(result_pack, ensure_ascii=False) + "\n", encoding="utf-8")
            evidence_index = {
                "version": "1.0",
                "semantics": "external_experiment_evidence_index",
                "result_pack": params.result_pack_path,
                "run_manifest": result_pack.get("run_manifest"),
                "metrics": metrics,
                "artifacts": result_pack.get("artifacts", []),
                "logs": result_pack.get("logs", []),
            }
            _write_json(self.policy.resolve_write(params.evidence_index_path), evidence_index)
            report = {
                "version": "1.0",
                "semantics": "external_result_ingest_report",
                "ok": True,
                "dry_run": bool(result_pack.get("dry_run")),
                "mock_only": bool(result_pack.get("mock_only")),
                "metric_count": len(metrics),
                "results_summary": params.results_summary_path,
            }
            _write_json(self.policy.resolve_write(params.ingest_report_path), report)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"ingest failed: {exc}", error="ingest_failed")
        return ToolResult(ok=True, content=f"Ingested result pack to {params.results_summary_path}.", data=report)


class AuditExperimentIntegrityTool(Tool):
    name = "audit_experiment_integrity"
    description = "Audit ingested external results for provenance, dry-run status, seed and metric issues."
    parameters_schema = AuditExperimentIntegrityParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditExperimentIntegrityParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            evidence = _read_json(self.policy.resolve_read(params.evidence_index_path))
            issues: list[dict[str, Any]] = []
            if summary.get("mock_only"):
                issues.append({"level": "WARN", "code": "mock_only", "detail": "Dry-run result is not publishable evidence."})
            if not summary.get("metrics"):
                issues.append({"level": "FAIL", "code": "missing_metrics", "detail": "No metrics in results summary."})
            evidence_artifacts = [
                item for item in (evidence.get("artifacts", []) or []) if isinstance(item, dict)
            ]
            artifact_by_path = {str(item.get("path")): item for item in evidence_artifacts if item.get("path")}
            seen_metric_ids: set[str] = set()
            for metric in summary.get("metrics", []) or []:
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("metric_id") or "")
                if metric_id in seen_metric_ids:
                    issues.append({"level": "WARN", "code": "duplicate_metric_id", "detail": metric_id})
                seen_metric_ids.add(metric_id)
                value = metric.get("value")
                if not isinstance(value, (int, float)):
                    issues.append({"level": "FAIL", "code": "non_numeric_metric", "detail": metric_id})
                source_artifact = str(metric.get("source_artifact") or "")
                if not source_artifact:
                    issues.append({"level": "FAIL", "code": "missing_source_artifact", "detail": metric_id})
                elif source_artifact not in artifact_by_path:
                    issues.append({"level": "FAIL", "code": "metric_source_not_indexed", "detail": f"{metric_id}: {source_artifact}"})
            for artifact in evidence_artifacts:
                rel_path = str(artifact.get("path") or "")
                if not rel_path:
                    issues.append({"level": "FAIL", "code": "artifact_missing_path", "detail": str(artifact)})
                    continue
                path = self.policy.workspace_dir / rel_path
                if not path.exists():
                    issues.append({"level": "FAIL", "code": "artifact_missing_on_disk", "detail": rel_path})
                    continue
                expected_hash = artifact.get("sha256")
                if expected_hash and path.is_file() and expected_hash != _sha256(path):
                    issues.append({"level": "FAIL", "code": "artifact_hash_mismatch", "detail": rel_path})
            manifest_rel = evidence.get("run_manifest")
            if manifest_rel:
                manifest_path = self.policy.workspace_dir / str(manifest_rel)
                if not manifest_path.exists():
                    issues.append({"level": "FAIL", "code": "missing_run_manifest", "detail": str(manifest_rel)})
                else:
                    manifest = _read_json(manifest_path)
                    if manifest.get("semantics") != "external_executor_run_manifest":
                        issues.append({"level": "FAIL", "code": "run_manifest_semantics", "detail": str(manifest_rel)})
            audit = {
                "version": "1.0",
                "semantics": "external_experiment_integrity_audit",
                "status": "fail" if any(item["level"] == "FAIL" for item in issues) else ("mock_only" if summary.get("mock_only") else "pass"),
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "issues": issues,
                "evidence_index": params.evidence_index_path,
                "artifact_count": len(evidence.get("artifacts", []) or []),
                "checked_artifacts": len(evidence_artifacts),
            }
            _write_json(self.policy.resolve_write(params.output_path), audit)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"integrity audit failed: {exc}", error="integrity_audit_failed")
        return ToolResult(ok=True, content=f"Wrote experiment integrity audit to {params.output_path}.", data=audit)


class MapResultsToClaimsTool(Tool):
    name = "map_results_to_claims"
    description = "Map audited result metrics to conservative experimental claims for T8 writing."
    parameters_schema = MapResultsToClaimsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MapResultsToClaimsParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            mappings = []
            for metric in summary.get("metrics", []) or []:
                if not isinstance(metric, dict):
                    continue
                status = "unsupported_mock_only" if summary.get("mock_only") else ("supported" if audit.get("status") == "pass" else "weak")
                mappings.append(
                    {
                        "claim_id": f"claim_{metric.get('metric_id') or len(mappings)+1}",
                        "support_status": status,
                        "metric_refs": [metric.get("metric_id")],
                        "evidence_refs": [metric.get("source_artifact")],
                        "allowed_wording": (
                            "Dry-run only; do not use as a paper result."
                            if summary.get("mock_only")
                            else f"Reports {metric.get('name')}={metric.get('value')} under audited external execution."
                        ),
                        "forbidden_wording": ["state-of-the-art", "validated", "proves"] if summary.get("mock_only") else ["proves"],
                        "limitations": ["mock_only"] if summary.get("mock_only") else [],
                    }
                )
            result = {
                "version": "1.0",
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "source": "external_executor",
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "integrity_audit": params.integrity_audit_path,
                "claim_mappings": mappings,
            }
            _write_json(self.policy.resolve_write(params.output_path), result)
            _write_json(self.policy.resolve_write(params.draft_output_path), result)
            iteration_log = self.policy.workspace_dir / "experiments" / "iteration_log.md"
            iteration_log.parent.mkdir(parents=True, exist_ok=True)
            iteration_log.write_text(_format_iteration_log(summary, audit, result), encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"result-to-claim failed: {exc}", error="result_to_claim_failed")
        return ToolResult(ok=True, content=f"Wrote result-to-claim map to {params.output_path}.", data={"claim_count": len(mappings)})


class BuildExperimentEvidencePackTool(Tool):
    name = "build_experiment_evidence_pack"
    description = "Build a normalized experiment evidence pack for manuscript writing."
    parameters_schema = BuildExperimentEvidencePackParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildExperimentEvidencePackParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            claims = _read_json(self.policy.resolve_read(params.experimental_claims_path))
            evidence = _read_json(self.policy.resolve_read(params.evidence_index_path))
            pack = {
                "version": "1.0",
                "semantics": "normalized_experiment_evidence_pack",
                "source": "external_executor",
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "source_packs": [{"path": params.results_summary_path}, {"path": params.integrity_audit_path}],
                "artifacts": evidence.get("artifacts", []),
                "metrics": summary.get("metrics", []),
                "claims": claims.get("claim_mappings", []),
                "integrity": {
                    "status": audit.get("status"),
                    "issues": audit.get("issues", []),
                },
                "limitations": ["mock_only"] if summary.get("mock_only") else [],
            }
            _write_json(self.policy.resolve_write(params.output_path), pack)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"evidence pack failed: {exc}", error="evidence_pack_failed")
        return ToolResult(ok=True, content=f"Wrote experiment evidence pack to {params.output_path}.", data={"metric_count": len(pack.get("metrics", []))})


class AuditPaperClaimsTool(Tool):
    name = "audit_paper_claims"
    description = "Audit manuscript numeric claims against normalized experiment evidence pack."
    parameters_schema = AuditPaperClaimsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditPaperClaimsParams(**kwargs)
        try:
            paper = self.policy.resolve_read(params.paper_path).read_text(encoding="utf-8", errors="replace")
            pack = _read_json(self.policy.resolve_read(params.evidence_pack_path))
            result_to_claim = _read_json(self.policy.resolve_read(params.result_to_claim_path))
            known_values = {
                str(metric.get("value"))
                for metric in pack.get("metrics", []) or []
                if isinstance(metric, dict) and metric.get("value") is not None
            }
            issues = []
            for number in _extract_substantive_numbers(paper):
                if number not in known_values:
                    issues.append({"level": "WARN", "number": number, "issue": "number_not_in_evidence_pack"})
            if pack.get("mock_only"):
                issues.append({"level": "FAIL", "issue": "mock_only_evidence_pack", "detail": "Dry-run evidence cannot support paper claims."})
            audit = {
                "version": "1.0",
                "semantics": "paper_claim_audit_against_experiment_evidence_pack",
                "summary": {
                    "fail_count": sum(1 for item in issues if item["level"] == "FAIL"),
                    "warn_count": sum(1 for item in issues if item["level"] == "WARN"),
                },
                "mock_only": bool(pack.get("mock_only")),
                "known_metric_values": sorted(known_values),
                "claim_mappings_count": len(result_to_claim.get("claim_mappings", []) or []),
                "issues": issues,
            }
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(_format_paper_claim_audit_markdown(audit), encoding="utf-8")
            output_path.with_suffix(".json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"paper claim audit failed: {exc}", error="paper_claim_audit_failed")
        return ToolResult(ok=True, content=f"Wrote paper claim audit to {params.output_path}.", data=audit["summary"])


def _render_executor_prompt(handoff: dict[str, Any], *, executor: str) -> str:
    executor_label = {
        "codex_cli": "Codex CLI",
        "claude_code_window": "Claude Code",
        "manual_external": "Manual External Executor",
        "mock_dry_run": "Mock Dry-Run Executor",
    }.get(executor, executor)
    return (
        f"# External Experiment Executor Prompt ({executor_label})\n\n"
        "You are an external executor for ResearchOS. Do not modify the ResearchOS main repo outside allowed paths.\n\n"
        "## Required Output\n"
        "- Write `external_executor/result_pack.json` matching `external_executor/expected_outputs_schema.json`.\n"
        "- Write `external_executor/run_manifest.json` with raw result/config/log artifact paths and hashes.\n"
        "- Write `external_executor/executor_status.json` with `status: done` and `accepted: false`.\n"
        "- Write raw logs under `external_executor/logs/`.\n"
        "- Mark dry-run outputs with `mock_only: true`.\n\n"
        "## Integrity Rules\n"
        "- Do not summarize results without raw files.\n"
        "- Do not write outside `external_executor/` or other allowed paths listed in the handoff pack.\n"
        "- Executor completion is only `done`; ResearchOS ingest/audit decides whether evidence is accepted.\n\n"
        "## Handoff Pack\n\n"
        "```json\n"
        + json.dumps(handoff, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def _render_manual_instructions(handoff: dict[str, Any]) -> str:
    return (
        "# Manual External Executor Instructions\n\n"
        "Use this file when a human or non-integrated external agent runs the experiment outside ResearchOS.\n\n"
        "1. Read `external_executor/handoff_pack.json` and `external_executor/expected_outputs_schema.json`.\n"
        "2. Implement/run only inside the allowed paths.\n"
        "3. Place raw metrics in `external_executor/raw_results/` and configs in `external_executor/configs/`.\n"
        "4. Write `external_executor/run_manifest.json`, `external_executor/result_pack.json`, and "
        "`external_executor/executor_status.json`.\n"
        "5. Keep `executor_status.accepted=false`; ResearchOS will set acceptance through ingest/audit.\n\n"
        "Allowed paths:\n\n"
        + "\n".join(f"- `{path}`" for path in handoff.get("allowed_paths", []))
        + "\n"
    )


def _format_iteration_log(summary: dict[str, Any], audit: dict[str, Any], claims: dict[str, Any]) -> str:
    lines = [
        "# External Experiment Iteration Log",
        "",
        f"- source: {summary.get('source')}",
        f"- run_id: {summary.get('run_id')}",
        f"- dry_run: {summary.get('dry_run')}",
        f"- mock_only: {summary.get('mock_only')}",
        f"- integrity_status: {audit.get('status')}",
        f"- claim_mappings: {len(claims.get('claim_mappings', []) or [])}",
        "",
        "Dry-run results are protocol tests only and must not be used as paper evidence.",
    ]
    return "\n".join(lines) + "\n"


def _extract_substantive_numbers(text: str) -> list[str]:
    import re

    numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text)
    result: list[str] = []
    for num in numbers:
        try:
            value = float(num)
        except Exception:
            continue
        if value in {0.0, 1.0} or 1900 <= value <= 2100:
            continue
        if value.is_integer() and 1 <= value <= 20:
            continue
        result.append(num)
    return result


def _format_paper_claim_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Paper Claim Audit",
        "",
        f"- semantics: `{audit.get('semantics')}`",
        f"- fail_count: {audit.get('summary', {}).get('fail_count')}",
        f"- warn_count: {audit.get('summary', {}).get('warn_count')}",
        f"- mock_only: {audit.get('mock_only')}",
        "",
        "## Issues",
    ]
    for issue in audit.get("issues", []) or []:
        lines.append(f"- **{issue.get('level')}** {issue.get('issue')}: {issue.get('number', '')} {issue.get('detail', '')}".strip())
    return "\n".join(lines) + "\n"
