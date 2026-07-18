from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from researchos.tools.external_experiment import _build_method_and_figure_audits, _current_result_section

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def run(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidencePackagingScriptTests(unittest.TestCase):
    def make_workspace(self, *, with_stale_run: bool = False) -> Path:
        root = Path(tempfile.mkdtemp(prefix="evidence-packaging-test-"))
        (root / "project.yaml").write_text("project_id: evidence-test\n", encoding="utf-8")
        ext = root / "external_executor"
        ext.mkdir()
        (ext / "AGENTS.md").write_text("Use only allowed paths.\n", encoding="utf-8")
        (ext / "allowed_paths.txt").write_text("external_executor/\n", encoding="utf-8")
        (ext / "expected_outputs_schema.json").write_text(
            json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8"
        )

        handoff = {
            "schema_version": "external_executor_handoff.v1",
            "method_intent": {
                "status": "draft_intent_only",
                "not_final_method_source": True,
                "central_mechanism_hypothesis": "A learned gate aligns two representations.",
                "candidate_modules": [
                    {"module_id": "M1", "name": "Encoder", "intended_role": "Encode inputs."},
                    {"module_id": "M2", "name": "Gate", "intended_role": "Fuse representations."},
                    {"module_id": "M3", "name": "Unused branch", "intended_role": "Candidate only."},
                ],
            },
            "context_reboost": {
                "claim_boundaries": ["Do not claim universal superiority."],
                "writer_handoff_contract": ["realized method", "traceable figures"],
            },
        }
        (ext / "handoff_pack.json").write_text(json.dumps(handoff), encoding="utf-8")
        (ext / "executor_status.json").write_text(
            json.dumps({
                "schema_version": "external_executor_status.v1",
                "executor_status": "running",
                "current_phase": "F",
                "current_step": "evidence_packaging",
                "iteration": 2,
            }),
            encoding="utf-8",
        )

        implementation_root = ext / "expr" / "implementation" / "ITER-2" / "IMPL-2"
        work = implementation_root / "worktree"
        (work / "src").mkdir(parents=True)
        (work / "config").mkdir()
        (work / "logs").mkdir()
        (work / "results").mkdir()
        (work / "plots").mkdir()
        (work / "figures").mkdir()
        code = work / "src/model.py"
        config = work / "config/final.json"
        log = work / "logs/run-1.log"
        metric = work / "results/run-1-metrics.json"
        data = work / "results/run-1-source.csv"
        plot = work / "plots/main_figure.py"
        figure = work / "figures/main.svg"
        code.write_text("class Encoder: pass\nclass Gate: pass\n", encoding="utf-8")
        config.write_text(json.dumps({"encoder": {"dim": 32}, "gate": {"temperature": 1.0}}), encoding="utf-8")
        log.write_text("completed\n", encoding="utf-8")
        metric.write_text(json.dumps({"accuracy": {"mean": 0.8, "std": 0.01}}), encoding="utf-8")
        data.write_text("method,accuracy\nbaseline,0.75\nours,0.80\n", encoding="utf-8")
        plot.write_text("# deterministic plot reads run-1-source.csv\n", encoding="utf-8")
        figure.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>main</text></svg>\n', encoding="utf-8")
        raw_dir = ext / "raw_results"
        raw_dir.mkdir()
        raw_main = raw_dir / "main_results.csv"
        raw_ablation = raw_dir / "ablation_results.csv"
        raw_robustness = raw_dir / "robustness_results.json"
        raw_main.write_text(
            "dataset,split,metric,direction,value,method_id,method_role,seed,run_id,experiment_id,run_type,protocol_fingerprint\n"
            "benchmark,test,accuracy,higher,0.80,RealizedGateNet,ours,1,RUN-1,EXP-MAIN,formal,PROTO-1\n"
            "benchmark,test,accuracy,higher,0.82,RealizedGateNet,ours,2,RUN-1,EXP-MAIN,formal,PROTO-1\n"
            "benchmark,test,accuracy,higher,0.75,StrongBaseline,baseline,1,RUN-B1,EXP-MAIN,formal,PROTO-1\n"
            "benchmark,test,accuracy,higher,0.76,StrongBaseline,baseline,2,RUN-B1,EXP-MAIN,formal,PROTO-1\n",
            encoding="utf-8",
        )
        raw_ablation.write_text(
            "dataset,split,metric,direction,value,method_id,method_role,variant,seed,run_id,experiment_id,run_type,protocol_fingerprint\n"
            "benchmark,test,accuracy,higher,0.81,RealizedGateNet,ours,full,1,RUN-A1,EXP-ABL,ablation,PROTO-1\n"
            "benchmark,test,accuracy,higher,0.72,RealizedGateNet,ours,no_gate,1,RUN-A2,EXP-ABL,ablation,PROTO-1\n",
            encoding="utf-8",
        )
        raw_robustness.write_text(
            json.dumps({
                "dataset": "benchmark", "split": "stress", "method_id": "ours",
                "run_type": "robustness", "run_id": "RUN-R1", "experiment_id": "EXP-ROBUST",
                "protocol_fingerprint": "PROTO-1", "metrics": {"accuracy": {"mean": 0.79}},
            }),
            encoding="utf-8",
        )

        rel = lambda p: p.relative_to(root).as_posix()
        run_record = {
            "record_id": "RUN-1",
            "run_id": "RUN-1",
            "experiment_id": "EXP-MAIN",
            "status": "completed",
            "usable": True,
            "formal": True,
            "run_type": "formal",
            "analysis_role": "confirmatory",
            "protocol_fingerprint": "PROTO-1",
            "claim_ids": ["CLM-1"],
            "artifact_kind": "figure",
            "title": "Main comparison",
            "caption": "Ours and the required baseline under the locked protocol.",
            "source_result_ref": rel(metric),
            "source_data_ref": rel(data),
            "config_ref": rel(config),
            "log_ref": rel(log),
            "metric_output_ref": rel(metric),
            "plot_script_ref": rel(plot),
            "figure_path": rel(figure),
            "evidence_level": "confirmatory",
        }
        runs = [run_record]
        if with_stale_run:
            stale = dict(run_record)
            stale.update({
                "record_id": "RUN-OLD",
                "run_id": "RUN-OLD",
                "status": "stale",
                "stale": True,
                "claim_ids": ["CLM-OLD"],
                "title": "Old result",
            })
            runs.append(stale)

        result = {
            "schema_version": "external_executor_result.v1",
            "context_alignment": {"status": "pass"},
            "resource_readiness": {"status": "ready", "minimum_loop_feasible": True},
            "claim_evidence_matrix": {
                "status": "complete",
                "items": [{
                    "claim_id": "CLM-1",
                    "statement": "The final method improves accuracy under the locked benchmark.",
                    "required": True,
                    "status": "planned",
                    "must_not_claim": ["universal superiority"],
                }],
            },
            "experiment_plan": {
                "status": "complete",
                "protocol_fingerprint": {"fingerprint": "PROTO-1"},
                "experiments": [{
                    "experiment_id": "EXP-MAIN",
                    "analysis_role": "confirmatory",
                    "run_type": "formal",
                    "claim_ids": ["CLM-1"],
                    "protocol_fingerprint": "PROTO-1",
                }],
            },
            "implementation_spec": {
                "status": "complete",
                "final_method_name": "RealizedGateNet",
                "one_sentence_method": "RealizedGateNet encodes two inputs and fuses them with a learned gate.",
                "actual_core_mechanism": "A learned gate controls representation fusion.",
                "actual_algorithm_flow": [
                    {"step": 1, "module_id": "M1", "description": "Encode inputs."},
                    {"step": 2, "module_id": "M2", "description": "Fuse encoded representations."},
                ],
                "losses": [{"name": "classification_loss", "definition": "cross entropy"}],
                "modules": [
                    {
                        "module_id": "M1",
                        "name": "Encoder",
                        "status": "implemented",
                        "actual_role": "Encode inputs.",
                        "inputs": ["raw input"],
                        "outputs": ["embedding"],
                        "code_refs": [rel(code)],
                        "config_keys": ["encoder.dim"],
                    },
                    {
                        "module_id": "M2",
                        "name": "Gate",
                        "status": "implemented",
                        "actual_role": "Fuse representations.",
                        "inputs": ["embedding A", "embedding B"],
                        "outputs": ["fused embedding"],
                        "code_refs": [rel(code)],
                        "config_keys": ["gate.temperature"],
                    },
                ],
            },
            "implementation_records": {
                "status": "complete",
                "items": [{"record_id": "IMPL-1", "status": "completed", "code_refs": [rel(code)], "config_refs": [rel(config)]}],
            },
            "code_and_protocol_review": {"status": "pass", "approved_for": ["formal"]},
            "experiment_runs": {"status": "complete", "items": runs},
            "result_diagnosis": {
                "status": "complete",
                "items": [{"record_id": "DIAG-1", "status": "completed", "claim_ids": ["CLM-1"], "evidence_refs": [rel(metric)]}],
            },
            "module_attribution": {
                "status": "complete",
                "items": [
                    {
                        "attribution_id": "ATTR-M1",
                        "module_id": "M1",
                        "module_name": "Encoder",
                        "status": "supported",
                        "evidence_type": "direct_ablation",
                        "confidence": "high",
                        "evidence_refs": [rel(metric)],
                        "recommendation": "keep",
                    },
                    {
                        "attribution_id": "ATTR-M2",
                        "module_id": "M2",
                        "module_name": "Gate",
                        "status": "present",
                        "evidence_type": "implementation_fact",
                        "confidence": "high",
                        "evidence_refs": [rel(code)],
                        "recommendation": "keep_but_do_not_overclaim",
                    },
                ],
            },
            "iteration_decisions": {
                "status": "complete",
                "items": [{
                    "iteration": 2,
                    "decision": "stop_and_report",
                    "rationale": "Required evidence is complete within budget.",
                    "evidence_refs": [rel(metric)],
                }],
            },
            "claim_boundary": {
                "status": "complete",
                "supported_scope": ["locked benchmark and split"],
                "must_not_claim": ["universal superiority"],
            },
            "sibling_section": {"preserve": True},
        }
        legacy_spec = result.pop("implementation_spec")
        method_spec = {
            "schema_version": "method_implementation_spec.v1",
            "spec_id": "SPEC-2",
            "spec_version": 2,
            "spec_fingerprint": "SPEC-FP-2",
            "protocol_fingerprint": "PROTO-1",
            "final_method_name": legacy_spec["final_method_name"],
            "one_sentence_method": legacy_spec["one_sentence_method"],
            "research_contract": {"core_mechanism": legacy_spec["actual_core_mechanism"]},
            "system_boundary": {"inputs": ["raw input"], "outputs": ["prediction"]},
            "modules": legacy_spec["modules"],
            "training_flow": [legacy_spec["actual_algorithm_flow"][0]],
            "inference_flow": [legacy_spec["actual_algorithm_flow"][1]],
            "objectives_and_losses": [{
                "objective_id": "LOSS-CLASSIFICATION",
                "name": "classification_loss",
                "definition": "cross entropy",
                "coefficient_config_key": "encoder.dim",
                "implementation_target": rel(code),
            }],
            "configuration_contract": {"parameters": [{"key": "encoder.dim"}, {"key": "gate.temperature"}]},
            "evidence_traceability": [{
                "claim_id": "CLM-1",
                "mechanism_ref": "learned gate",
                "module_ids": ["M1", "M2"],
                "experiment_ids": ["EXP-MAIN"],
                "expected_artifacts": [rel(metric)],
                "interpretation_boundary": "locked benchmark only",
            }],
        }
        spec_path = ext / "method_specs/method-spec-v002.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(json.dumps(method_spec), encoding="utf-8")
        result["method_refinements"] = [{
            "refinement_id": "REF-2", "iteration_id": "ITER-1", "status": "ready",
            "spec_id": "SPEC-2", "spec_version": 2, "spec_fingerprint": "SPEC-FP-2",
            "snapshot_ref": rel(spec_path),
        }]
        result["implementations"] = {
            "status": "complete", "active_implementation_id": "IMPL-2", "items": [{
                "implementation_id": "IMPL-2", "iteration_id": "ITER-2",
                "implementation_root": rel(implementation_root),
                "final_worktree_fingerprint": "WORKTREE-FP-2",
                "method_spec_fingerprint": "SPEC-FP-2", "protocol_fingerprint": "PROTO-1",
                "module_mapping": {"status": "complete", "items": legacy_spec["modules"]},
            }],
        }
        result["implementation_reviews"] = {"status": "complete", "items": [{
            "review_id": "REV-2", "iteration_id": "ITER-2", "implementation_id": "IMPL-2",
            "review_status": "pass", "approved_for": "formal",
            "review_scope": {"protocol_fingerprint": "PROTO-1"},
        }]}
        for run in result["experiment_runs"]["items"]:
            run["iteration_id"] = "ITER-2"
            run["implementation_id"] = "IMPL-2"
        legacy_diagnosis = result.pop("result_diagnosis")
        diagnosis = legacy_diagnosis["items"][0]
        diagnosis.update({"diagnosis_id": "DIAG-2", "iteration_id": "ITER-2"})
        result["result_diagnoses"] = {
            "status": "complete", "current_by_iteration": {"ITER-2": "DIAG-2"}, "items": [diagnosis],
        }
        legacy_attributions = result.pop("module_attribution")["items"]
        for item in legacy_attributions:
            item["empirical_status"] = "beneficial" if item["module_id"] == "M1" else "implementation_only"
            item["causal_status"] = "local_intervention_effect" if item["module_id"] == "M1" else "implementation_only"
        result["module_attributions"] = {
            "status": "complete", "current_by_iteration": {"ITER-2": "ATTR-REPORT-2"}, "items": [{
                "attribution_id": "ATTR-REPORT-2", "iteration_id": "ITER-2",
                "module_attributions": {"status": "complete", "items": legacy_attributions},
                "mechanism_attributions": {"status": "complete", "items": []},
                "interaction_effects": {"status": "complete", "items": []},
                "baseline_module_attributions": {"status": "complete", "items": []},
                "confounds": {"status": "complete", "items": []},
                "recommendations": {"status": "complete", "items": []},
                "unsupported_questions": {"status": "complete", "items": []},
                "risks": {"status": "complete", "items": []},
                "attribution_gate": {"status": "ready_for_iteration_decision"},
            }],
        }
        result["iteration_decisions"]["items"][-1].update({"decision_id": "DEC-2", "iteration_id": "ITER-2"})
        result["iteration_decisions"]["current_by_iteration"] = {"ITER-2": "DEC-2"}
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")

        artifacts = []
        for artifact_id, p, producer, level in [
            ("ART-CODE", code, "implementation", "method_definition"),
            ("ART-CONFIG", config, "implementation", "config"),
            ("ART-LOG", log, "experiment-run", "formal_log"),
            ("ART-METRIC", metric, "experiment-run", "formal_result"),
            ("ART-DATA", data, "experiment-run", "source_table"),
            ("ART-PLOT", plot, "experiment-run", "plot_script"),
            ("ART-FIG", figure, "experiment-run", "result_figure"),
            ("ART-RAW-MAIN", raw_main, "experiment-run", "raw_result"),
            ("ART-RAW-ABLATION", raw_ablation, "experiment-run", "raw_result"),
            ("ART-RAW-ROBUSTNESS", raw_robustness, "experiment-run", "raw_result"),
        ]:
            artifacts.append({
                "artifact_id": artifact_id,
                "path": rel(p),
                "sha256": sha256(p),
                "size_bytes": p.stat().st_size,
                "producer": producer,
                "evidence_level": level,
            })
        (ext / "report").mkdir(exist_ok=True)
        (ext / "report" / "run_manifest.json").write_text(
            json.dumps({"schema_version": "external_executor_manifest.v1", "artifacts": artifacts}),
            encoding="utf-8",
        )
        return root

    def execute_pipeline(self, ws: Path) -> None:
        run("preflight_evidence_packaging.py", "--workspace", str(ws))
        run("build_evidence_snapshot.py", "--workspace", str(ws))
        run("validate_evidence_snapshot.py", "--workspace", str(ws))
        run("build_realized_method_package.py", "--workspace", str(ws))
        run("build_framework_figure_spec.py", "--workspace", str(ws))
        run("render_framework_figure.py", "--workspace", str(ws), "--write-back")
        run("build_result_tables.py", "--workspace", str(ws))
        run("render_result_figures.py", "--workspace", str(ws))
        run("build_figure_table_inventory.py", "--workspace", str(ws))
        run("build_evidence_mapping.py", "--workspace", str(ws))
        run("build_package_manifest.py", "--workspace", str(ws))
        run("compute_packaging_gate.py", "--workspace", str(ws))
        run("assemble_evidence_packaging_report.py", "--workspace", str(ws))
        run("validate_evidence_packaging_report.py", "--workspace", str(ws))
        run("apply_evidence_packaging_report.py", "--workspace", str(ws))

    def test_end_to_end_ready_and_narrow_apply(self) -> None:
        ws = self.make_workspace()
        self.execute_pipeline(ws)
        ext = ws / "external_executor"
        gate = json.loads((ext / "report/phase_F/evidence_packaging_gate.json").read_text())
        self.assertEqual(gate["status"], "ready")
        method = json.loads((ext / "evidence_package/realized_method_package.json").read_text())
        self.assertEqual(method["final_method_name"], "RealizedGateNet")
        self.assertEqual(method["final_version"]["implementation_id"], "IMPL-2")
        self.assertEqual(method["final_version"]["method_spec"]["spec_fingerprint"], "SPEC-FP-2")
        self.assertTrue(method["training_flow"])
        self.assertTrue(method["inference_flow"])
        self.assertEqual(method["actual_losses"][0]["implementation_validation"], "verified")
        support = {m["module_id"]: m["empirical_support"]["status"] for m in method["implemented_modules"]}
        self.assertEqual(support["M1"], "supported")
        self.assertNotEqual(support["M2"], "supported")
        framework = json.loads((ext / "report/phase_F/framework_figure_spec.json").read_text())
        self.assertEqual(framework["status"], "ready_for_T7_audit")
        self.assertTrue(framework["rendered_files"])
        self.assertEqual(framework["rendered_files"][0]["path"], "external_executor/figure/framework_figure.svg")
        self.assertTrue((ext / "table/main_comparison.csv").is_file())
        self.assertTrue((ext / "table/ablation_results.csv").is_file())
        self.assertTrue((ext / "table/other_experiments.csv").is_file())
        self.assertTrue(list((ext / "figure").glob("main_*.svg")))
        self.assertTrue(list((ext / "figure").glob("ablation_*.svg")))
        self.assertTrue(list((ext / "figure").glob("other_*.svg")))
        for process_file in (
            "evidence_packaging_preflight.json", "final_evidence_snapshot.json",
            "final_evidence_snapshot_validation.json", "framework_figure_spec.json",
            "framework_figure.mmd", "result_table_build_report.json", "result_figure_build_report.json",
            "figure_table_inventory.json", "evidence_mapping.json", "evidence_package_manifest.json",
            "evidence_packaging_gate.json", "evidence_packaging_report.json",
            "evidence_packaging_report_validation.json",
        ):
            self.assertTrue((ext / "report" / "phase_F" / process_file).is_file())
            self.assertFalse((ext / process_file).exists())
        result = json.loads((ext / "result_pack.json").read_text())
        self.assertTrue(result["sibling_section"]["preserve"])
        for key in ("realized_method_package", "framework_figure", "figure_table_inventory", "evidence_mapping", "evidence_packaging"):
            self.assertIn(key, result)
        mapping = json.loads((ext / "report/phase_F/evidence_mapping.json").read_text())
        module_claims = {item["module_id"]: item["claim_ids"] for item in mapping["module_mappings"]}
        self.assertEqual(module_claims["M1"], ["CLM-1"])
        self.assertEqual(module_claims["M2"], ["CLM-1"])

    def test_historical_implementation_is_not_merged_into_final_method(self) -> None:
        ws = self.make_workspace()
        ext = ws / "external_executor"
        old_implementation_root = ext / "expr/implementation/ITER-1/IMPL-OLD"
        old_root = old_implementation_root / "worktree"
        old_root.mkdir(parents=True)
        (old_root / "old.py").write_text("OLD = True\n", encoding="utf-8")
        result = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        result["implementations"]["items"].insert(0, {
            "implementation_id": "IMPL-OLD", "iteration_id": "ITER-1",
            "implementation_root": old_implementation_root.relative_to(ws).as_posix(),
            "final_worktree_fingerprint": "OLD-FP", "method_spec_fingerprint": "OLD-SPEC",
            "protocol_fingerprint": "PROTO-1",
            "module_mapping": {"status": "complete", "items": [{
                "module_id": "OLD", "name": "Historical module", "implementation_status": "implemented",
                "code_paths": ["old.py"], "config_keys": ["old.enabled"],
            }]},
        })
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")

        self.execute_pipeline(ws)

        method = json.loads((ext / "evidence_package/realized_method_package.json").read_text())
        self.assertEqual(method["final_version"]["implementation_id"], "IMPL-2")
        self.assertNotIn("OLD", {item["module_id"] for item in method["implemented_modules"]})
        self.assertFalse(any("old.py" in ref for item in method["implemented_modules"] for ref in item["code_refs"]))

    def test_legacy_workdir_cannot_be_selected_as_final_method(self) -> None:
        ws = self.make_workspace()
        ext = ws / "external_executor"
        result = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        active = result["implementations"]["items"][-1]
        current_root = ws / active["implementation_root"]
        legacy_root = ext / "workdir/final-method"
        shutil.copytree(current_root, legacy_root)
        active["implementation_root"] = legacy_root.relative_to(ws).as_posix()
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")

        run("preflight_evidence_packaging.py", "--workspace", str(ws))
        run("build_evidence_snapshot.py", "--workspace", str(ws))
        run("validate_evidence_snapshot.py", "--workspace", str(ws))
        run("build_realized_method_package.py", "--workspace", str(ws))
        method = json.loads((ext / "evidence_package/realized_method_package.json").read_text())
        self.assertEqual(method["status"], "unavailable")
        self.assertIn(
            "active_implementation_root_outside_expr_implementation",
            method["source_validation"]["errors"],
        )

    def test_realized_method_expands_nested_module_attribution_report(self) -> None:
        ws = self.make_workspace()
        ext = ws / "external_executor"
        result = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        flat = result["module_attributions"]["items"][0]["module_attributions"]["items"]
        nested_items = []
        for item in flat:
            nested_items.append({
                "module_attribution_id": item["attribution_id"],
                "module_id": item["module_id"],
                "empirical_status": "beneficial" if item["module_id"] == "M1" else "implementation_only",
                "evidence_type": item["evidence_type"],
                "confidence": item["confidence"],
                "evidence_refs": item["evidence_refs"],
                "summary": "nested attribution",
            })
        result["module_attributions"]["items"][0]["module_attributions"]["items"] = nested_items
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        self.execute_pipeline(ws)
        method = json.loads((ext / "evidence_package/realized_method_package.json").read_text(encoding="utf-8"))
        support = {module["module_id"]: module["empirical_support"]["status"] for module in method["implemented_modules"]}
        self.assertEqual(support["M1"], "supported")
        self.assertEqual(method["module_attribution"]["all_attribution_record_count"], 2)

    def test_snapshot_detects_live_mutation(self) -> None:
        ws = self.make_workspace()
        run("preflight_evidence_packaging.py", "--workspace", str(ws))
        run("build_evidence_snapshot.py", "--workspace", str(ws))
        result_path = ws / "external_executor/result_pack.json"
        result = json.loads(result_path.read_text())
        result["claim_boundary"]["must_not_claim"].append("post-snapshot mutation")
        result_path.write_text(json.dumps(result), encoding="utf-8")
        proc = run("validate_evidence_snapshot.py", "--workspace", str(ws), check=False)
        self.assertNotEqual(proc.returncode, 0)
        validation = json.loads((ws / "external_executor/report/phase_F/final_evidence_snapshot_validation.json").read_text())
        self.assertEqual(validation["status"], "blocked")
        self.assertTrue(any("snapshot_source_changed" in item for item in validation["errors"]))

    def test_stale_run_is_not_active_evidence(self) -> None:
        ws = self.make_workspace(with_stale_run=True)
        run("preflight_evidence_packaging.py", "--workspace", str(ws))
        run("build_evidence_snapshot.py", "--workspace", str(ws))
        snapshot = json.loads((ws / "external_executor/report/phase_F/final_evidence_snapshot.json").read_text())
        active_ids = {item["record_id"] for item in snapshot["active_formal_records"]}
        stale_ids = {item["record_id"] for item in snapshot["inactive_or_stale_records"]}
        self.assertIn("RUN-1", active_ids)
        self.assertNotIn("RUN-OLD", active_ids)
        self.assertIn("RUN-OLD", stale_ids)

    def test_report_fingerprint_mismatch_is_blocked(self) -> None:
        ws = self.make_workspace()
        self.execute_pipeline(ws)
        ext = ws / "external_executor"
        report_path = ext / "report/phase_F/evidence_packaging_report.json"
        report = json.loads(report_path.read_text())
        report["framework_figure"]["snapshot_fingerprint"] = "different-snapshot"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        proc = run("validate_evidence_packaging_report.py", "--workspace", str(ws), check=False)
        self.assertNotEqual(proc.returncode, 0)
        validation = json.loads((ext / "report/phase_F/evidence_packaging_report_validation.json").read_text())
        self.assertEqual(validation["status"], "blocked")
        self.assertTrue(any("snapshot" in item for item in validation["errors"]))

    def test_result_figures_separate_protocols_and_remove_stale_outputs(self) -> None:
        ws = self.make_workspace()
        ext = ws / "external_executor"
        run("preflight_evidence_packaging.py", "--workspace", str(ws))
        run("build_evidence_snapshot.py", "--workspace", str(ws))
        run("build_result_tables.py", "--workspace", str(ws))

        main_table = ext / "table/main_comparison.csv"
        with main_table.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fields = list(rows[0])
        protocol_two_rows = []
        for row in rows:
            clone = dict(row)
            clone["protocol_fingerprint"] = "PROTO-2"
            clone["mean"] = "0.70" if clone["method_role"] == "ours" else "0.69"
            protocol_two_rows.append(clone)
        with main_table.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows + protocol_two_rows)

        framework = ext / "figure/framework_figure.svg"
        framework.parent.mkdir(parents=True, exist_ok=True)
        framework.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>\n", encoding="utf-8")
        run("render_result_figures.py", "--workspace", str(ws))

        report = json.loads((ext / "report/phase_F/result_figure_build_report.json").read_text())
        main_figures = [item for item in report["figures"] if item["kind"] == "main"]
        self.assertEqual({item["protocol_fingerprint"] for item in main_figures}, {"PROTO-1", "PROTO-2"})
        self.assertEqual(len(main_figures), 2)

        (ext / "raw_results/ablation_results.csv").unlink()
        with (ext / "raw_results/main_results.csv").open("a", encoding="utf-8") as handle:
            handle.write(
                "benchmark,test,accuracy,higher,0.99,mutated,ours,3,RUN-X,EXP-MAIN,formal,PROTO-1\n"
            )
        run("build_result_tables.py", "--workspace", str(ws))
        run("render_result_figures.py", "--workspace", str(ws))
        self.assertFalse((ext / "table/ablation_results.csv").exists())
        self.assertFalse((ext / "table/main_comparison.csv").exists())
        self.assertFalse(list((ext / "figure").glob("ablation_*.svg")))
        self.assertFalse(list((ext / "figure").glob("main_*.svg")))
        self.assertTrue(framework.is_file())

    def test_current_plural_section_and_partial_t7_method_audit(self) -> None:
        selected = _current_result_section(
            {
                "module_attribution": {"attribution_id": "legacy"},
                "module_attributions": {
                    "current_by_iteration": {"ITER-2": "ATTR-2"},
                    "items": [{"attribution_id": "ATTR-1"}, {"attribution_id": "ATTR-2"}],
                },
            },
            "module_attributions",
            "module_attribution",
            id_keys=("attribution_id",),
        )
        self.assertEqual(selected["attribution_id"], "ATTR-2")

        ws = self.make_workspace()
        method_audit, _ = _build_method_and_figure_audits(
            workspace=ws,
            summary={"mock_only": False},
            evidence={"realized_method_package": {
                "status": "partial", "unresolved_fields": ["actual_losses"],
                "source_validation": {"status": "partial", "errors": ["loss_unverified"]},
                "implemented_modules": [], "training_flow": [{"step": 1}],
                "inference_flow": [{"step": 1}], "actual_losses": [],
            }},
        )
        self.assertEqual(method_audit["status"], "warn")
        self.assertTrue(any(item["code"] == "realized_method_partial" for item in method_audit["issues"]))


if __name__ == "__main__":
    unittest.main()
