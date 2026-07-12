from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
                "status": "running",
                "current_phase": "F",
                "current_step": "evidence_packaging",
                "iteration": 2,
            }),
            encoding="utf-8",
        )

        work = ext / "workdir"
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
        ]:
            artifacts.append({
                "artifact_id": artifact_id,
                "path": rel(p),
                "sha256": sha256(p),
                "size_bytes": p.stat().st_size,
                "producer": producer,
                "evidence_level": level,
            })
        (ext / "run_manifest.json").write_text(
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
        gate = json.loads((ext / "evidence_packaging_gate.json").read_text())
        self.assertEqual(gate["status"], "ready")
        method = json.loads((ext / "evidence_package/realized_method_package.json").read_text())
        self.assertEqual(method["final_method_name"], "RealizedGateNet")
        support = {m["module_id"]: m["empirical_support"]["status"] for m in method["implemented_modules"]}
        self.assertEqual(support["M1"], "supported")
        self.assertNotEqual(support["M2"], "supported")
        framework = json.loads((ext / "evidence_package/framework_figure_spec.json").read_text())
        self.assertEqual(framework["status"], "ready_for_T7_audit")
        self.assertTrue(framework["rendered_files"])
        result = json.loads((ext / "result_pack.json").read_text())
        self.assertTrue(result["sibling_section"]["preserve"])
        for key in ("realized_method_package", "framework_figure", "figure_table_inventory", "evidence_mapping", "evidence_packaging"):
            self.assertIn(key, result)
        self.assertNotIn("writer_handoff", result)

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
        validation = json.loads((ws / "external_executor/final_evidence_snapshot_validation.json").read_text())
        self.assertEqual(validation["status"], "blocked")
        self.assertTrue(any("snapshot_source_changed" in item for item in validation["errors"]))

    def test_stale_run_is_not_active_evidence(self) -> None:
        ws = self.make_workspace(with_stale_run=True)
        run("preflight_evidence_packaging.py", "--workspace", str(ws))
        run("build_evidence_snapshot.py", "--workspace", str(ws))
        snapshot = json.loads((ws / "external_executor/final_evidence_snapshot.json").read_text())
        active_ids = {item["record_id"] for item in snapshot["active_formal_records"]}
        stale_ids = {item["record_id"] for item in snapshot["inactive_or_stale_records"]}
        self.assertIn("RUN-1", active_ids)
        self.assertNotIn("RUN-OLD", active_ids)
        self.assertIn("RUN-OLD", stale_ids)

    def test_report_fingerprint_mismatch_is_blocked(self) -> None:
        ws = self.make_workspace()
        self.execute_pipeline(ws)
        ext = ws / "external_executor"
        report_path = ext / "evidence_packaging_report.json"
        report = json.loads(report_path.read_text())
        report["framework_figure"]["snapshot_fingerprint"] = "different-snapshot"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        proc = run("validate_evidence_packaging_report.py", "--workspace", str(ws), check=False)
        self.assertNotEqual(proc.returncode, 0)
        validation = json.loads((ext / "evidence_packaging_report_validation.json").read_text())
        self.assertEqual(validation["status"], "blocked")
        self.assertTrue(any("snapshot" in item for item in validation["errors"]))


if __name__ == "__main__":
    unittest.main()
