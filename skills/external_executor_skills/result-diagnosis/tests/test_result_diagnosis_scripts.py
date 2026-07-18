from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ROOT_DECISION_SCRIPT = ROOT.parent / "research-execution" / "scripts" / "decide_iteration.py"


def run(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        text=True,
        capture_output=True,
        check=check,
        timeout=30,
    )


def make_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="result-diagnosis-test-"))
    (ws / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    ext = ws / "external_executor"
    ext.mkdir()
    (ext / "AGENTS.md").write_text("controls\n", encoding="utf-8")
    (ext / "allowed_paths.txt").write_text("external_executor/\n", encoding="utf-8")
    (ext / "expected_outputs_schema.json").write_text(
        json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8"
    )
    claims = {"status": "complete", "items": [{"claim_id": "C1", "experiment_ids": ["E1"]}]}
    runs = []
    for role, mid, values in (
        ("ours", "ours", [0.82, 0.84, 0.83]),
        ("baseline", "base-a", [0.78, 0.79, 0.77]),
        ("baseline", "base-b", [0.80, 0.81, 0.80]),
    ):
        for seed, value in enumerate(values, 1):
            runs.append(
                {
                    "run_id": f"{mid}-{seed}",
                    "iteration_id": "iter-1",
                    "experiment_id": "E1",
                    "method_id": mid,
                    "method_role": role,
                    "run_type": "formal",
                    "analysis_role": "confirmatory",
                    "status": "completed",
                    "setting": "default",
                    "dataset": "D",
                    "dataset_version": "1",
                    "dataset_split": "test",
                    "protocol_fingerprint": "proto-1",
                    "fairness_fingerprint": "fair-1",
                    "seed": seed,
                    "code_version": "code-1",
                    "config_ref": f"configs/{mid}-{seed}.json",
                    "raw_log_ref": f"external_executor/raw_results/logs/{mid}-{seed}.log",
                    "metric_output_ref": f"external_executor/raw_results/raw/{mid}-{seed}.json",
                    "environment_ref": "env.json",
                    "approved_for": ["formal"],
                    "metrics": {
                        "accuracy": {
                            "value": value,
                            "direction": "higher_is_better",
                            "aggregation": "mean",
                        }
                    },
                }
            )
    result = {
        "schema_version": "external_executor_result.v1",
        "context_alignment": {
            "status": "pass",
            "confirmed_execution_scope": {
                "required_baselines": [{"name": "base-a"}, {"name": "base-b"}]
            },
        },
        "experiment_plan": {
            "status": "complete",
            "protocol_fingerprint": "proto-1",
            "metrics": [{"name": "accuracy", "direction": "higher_is_better"}],
        },
        "claim_evidence_matrix": claims,
        "baseline_reproduction": {"status": "complete", "items": []},
        "implementation_reviews": {"status": "complete", "items": []},
        "iteration_plans": {
            "status": "complete",
            "items": [{"iteration_id": "iter-1", "status": "active"}],
        },
        "experiment_runs": {"status": "complete", "items": runs},
        "unrelated": {"keep": True},
    }
    (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
    return ws


def build_pipeline(ws: Path) -> None:
    ext = ws / "external_executor"
    run("preflight_diagnosis.py", "--workspace", str(ws))
    assert (ext / "report/phase_E/result_diagnosis_preflight.json").is_file()
    assert not (ext / "result_diagnosis_preflight.json").exists()
    run(
        "build_evidence_snapshot.py",
        "--workspace",
        str(ws),
        "--iteration-id",
        "iter-1",
    )
    work = ext / "result_diagnosis/iter-1"
    work.mkdir(parents=True, exist_ok=True)
    obs = work / "metric_observations.json"
    aggs = work / "metric_aggregates.json"
    comps = work / "method_comparisons.json"
    anoms = work / "anomalies.json"
    run(
        "normalize_run_metrics.py",
        "--snapshot",
        str(ext / "report/phase_E/diagnosis_evidence_snapshot.json"),
        "--output",
        str(obs),
    )
    run("aggregate_results.py", "--observations", str(obs), "--output", str(aggs))
    run(
        "compare_methods.py",
        "--aggregates",
        str(aggs),
        "--observations",
        str(obs),
        "--output",
        str(comps),
    )
    run(
        "detect_anomalies.py",
        "--snapshot",
        str(ext / "report/phase_E/diagnosis_evidence_snapshot.json"),
        "--observations",
        str(obs),
        "--aggregates",
        str(aggs),
        "--comparisons",
        str(comps),
        "--output",
        str(anoms),
        check=False,
    )
    run(
        "build_diagnosis_facts.py",
        "--snapshot",
        str(ext / "report/phase_E/diagnosis_evidence_snapshot.json"),
        "--observations",
        str(obs),
        "--aggregates",
        str(aggs),
        "--comparisons",
        str(comps),
        "--anomalies",
        str(anoms),
        "--output",
        str(ext / "report/phase_E/diagnosis_statistics.json"),
    )
    run("initialize_diagnosis_report.py", "--workspace", str(ws))


class ResultDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = make_workspace()
        build_pipeline(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base, ignore_errors=True)

    def clone(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="result-diagnosis-clone-")) / "workspace"
        shutil.copytree(self.base, target)
        return target

    def test_snapshot_statistics_and_anomaly_primitives(self) -> None:
        ws = self.base
        ext = ws / "external_executor"
        snapshot = json.loads((ext / "report/phase_E/diagnosis_evidence_snapshot.json").read_text())
        self.assertFalse((ext / "diagnosis_evidence_snapshot.json").exists())
        self.assertEqual(len(snapshot["included_run_ids"]), 9)
        work = ext / "result_diagnosis/iter-1"
        aggregates = json.loads((work / "metric_aggregates.json").read_text())
        comparisons = json.loads((work / "method_comparisons.json").read_text())
        stats = json.loads((ext / "report/phase_E/diagnosis_statistics.json").read_text())
        self.assertFalse((ext / "diagnosis_statistics.json").exists())
        self.assertEqual(len(aggregates["items"]), 3)
        self.assertEqual(len(comparisons["items"]), 2)
        self.assertTrue(all(x["numeric_outcome"] == "win" for x in comparisons["items"]))
        self.assertEqual(
            stats["strongest_baselines"]["items"][0]["baseline_method_id"], "base-b"
        )

        # Exercise the insufficient-repeat detector without rebuilding the whole pipeline.
        ws2 = self.clone()
        work2 = ws2 / "external_executor/result_diagnosis/iter-1"
        aggs2 = json.loads((work2 / "metric_aggregates.json").read_text())
        for item in aggs2["items"]:
            item["n"] = 1
            item["values"] = item["values"][:1]
            item["observation_ids"] = item["observation_ids"][:1]
            item["stddev"] = None
        (work2 / "single_aggregates.json").write_text(json.dumps(aggs2), encoding="utf-8")
        single_anoms = work2 / "single_anomalies.json"
        run(
            "detect_anomalies.py",
            "--snapshot",
            str(ws2 / "external_executor/report/phase_E/diagnosis_evidence_snapshot.json"),
            "--observations",
            str(work2 / "metric_observations.json"),
            "--aggregates",
            str(work2 / "single_aggregates.json"),
            "--comparisons",
            str(work2 / "method_comparisons.json"),
            "--output",
            str(single_anoms),
            check=False,
        )
        categories = {x["category"] for x in json.loads(single_anoms.read_text())["items"]}
        self.assertIn("insufficient_repeats", categories)
        shutil.rmtree(ws2.parent, ignore_errors=True)

    def test_snapshot_materializes_reviewed_baseline_reproduction_metrics(self) -> None:
        ws = make_workspace()
        ext = ws / "external_executor"
        result_path = ext / "result_pack.json"
        result = json.loads(result_path.read_text())
        result["experiment_runs"]["items"] = [
            run for run in result["experiment_runs"]["items"] if run["method_role"] == "ours"
        ]
        result["context_alignment"]["confirmed_execution_scope"]["required_baselines"] = [{"baseline_id": "base-a"}]
        evidence = ext / "report" / "phase_D" / "baseline_reproduction" / "base-a" / "attempt-1"
        raw = ext / "raw_results" / "baseline_reproduction" / "base-a" / "attempt-1"
        evidence.mkdir(parents=True)
        raw.mkdir(parents=True)
        (raw / "stdout.log").write_text("baseline completed\n", encoding="utf-8")
        run_record = {
            "run_id": "BASE-RUN-1",
            "status": "completed",
            "dataset": {"name": "D", "version": "1", "split": "test"},
            "protocol_fingerprint": "proto-1",
            "fairness_fingerprint": "fair-1",
            "deployment_dir": "external_executor/expr/baselines/base-a",
            "stdout_path": "external_executor/raw_results/baseline_reproduction/base-a/attempt-1/stdout.log",
            "environment_path": "external_executor/report/phase_D/baseline_reproduction/base-a/attempt-1/environment.json",
            "source_manifest_sha256": "source-a",
        }
        (evidence / "run_record.json").write_text(json.dumps(run_record), encoding="utf-8")
        metrics = {
            "status": "complete",
            "items": [{
                "name": "accuracy",
                "value": 0.80,
                "direction": "higher_is_better",
                "aggregation": "mean",
                "raw_csv_path": "external_executor/raw_results/baseline_reproduction/base-a/attempt-1/raw_metrics/D/accuracy.csv",
            }],
        }
        (evidence / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        result["baseline_reproduction"] = {
            "status": "complete",
            "items": [{
                "baseline_id": "base-a",
                "required": True,
                "status": "reproduced",
                "reproduction_id": "REPRO-A",
                "protocol_fingerprint": "proto-1",
                "fairness_fingerprint": "fair-1",
                "selected_attempt_id": "BASE-RUN-1",
                "attempts": [{
                    "attempt_id": "BASE-RUN-1",
                    "run_id": "BASE-RUN-1",
                    "run_record_ref": "external_executor/report/phase_D/baseline_reproduction/base-a/attempt-1/run_record.json",
                }],
                "review": {"verdict": "pass", "approved_for": "formal_review_candidate"},
            }],
        }
        result_path.write_text(json.dumps(result), encoding="utf-8")
        run("build_evidence_snapshot.py", "--workspace", str(ws), "--iteration-id", "iter-1")
        snapshot = json.loads((ext / "report/phase_E/diagnosis_evidence_snapshot.json").read_text())
        baseline = [item for item in snapshot["runs"] if item["method_role"] == "baseline"]
        self.assertEqual(len(baseline), 1)
        self.assertEqual(baseline[0]["method_id"], "base-a")
        self.assertEqual(baseline[0]["metrics"]["accuracy"]["value"], 0.80)
        self.assertEqual(baseline[0]["eligibility"], "formal_candidate")
        shutil.rmtree(ws, ignore_errors=True)

    def test_report_gate_validation_and_narrow_apply(self) -> None:
        ws = self.clone()
        ext = ws / "external_executor"
        report_path = ext / "result_diagnosis_report.json"
        report = json.loads(report_path.read_text())
        comps = report["method_comparisons"]["items"]
        report["setting_diagnostics"] = {
            "status": "complete",
            "items": [
                {
                    "setting_diagnosis_id": "SETDIAG-1",
                    "setting_key": {"setting": "default"},
                    "primary_metric": "accuracy",
                    "finding": "ours_wins",
                    "comparison_ids": [x["comparison_id"] for x in comps],
                    "summary": "Ours is higher than both eligible baselines in the recorded setting.",
                    "interpretation_level": "descriptive_inference",
                    "confidence": "medium",
                    "evidence_refs": [x["comparison_id"] for x in comps],
                    "limitations": [],
                    "causal_claim": False,
                }
            ],
        }
        report["confound_assessments"] = {
            "status": "complete",
            "items": [
                {
                    "confound_id": "CONF-1",
                    "family": "random_seed",
                    "status": "controlled",
                    "summary": "Seeds are paired.",
                    "confidence": "high",
                    "evidence_refs": [comps[0]["comparison_id"]],
                    "causal_claim": False,
                }
            ],
        }
        report["claim_implications"] = {
            "status": "complete",
            "items": [
                {
                    "claim_implication_id": "CLIMP-1",
                    "claim_id": "C1",
                    "status": "supported",
                    "pre_audit_strength": "moderate",
                    "summary": "Current formal-candidate evidence supports the claim in the tested setting.",
                    "evidence_refs": [x["comparison_id"] for x in comps],
                    "counterevidence_refs": [],
                    "confidence": "medium",
                    "conditions": ["tested setting only"],
                    "must_not_infer": ["causal mechanism"],
                    "required_evidence": [],
                    "causal_claim": False,
                }
            ],
        }
        report["evidence_requests"] = {"status": "complete", "items": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        run("compute_diagnosis_gate.py", "--report", str(report_path), "--write-back")
        updated = json.loads(report_path.read_text())
        self.assertEqual(updated["diagnosis_gate"]["status"], "ready_for_attribution")
        run("validate_diagnosis_report.py", "--workspace", str(ws))
        run("apply_diagnosis_report.py", "--workspace", str(ws))
        result = json.loads((ext / "result_pack.json").read_text())
        self.assertTrue(result["unrelated"]["keep"])
        self.assertEqual(
            result["result_diagnoses"]["items"][0]["diagnosis_id"], updated["diagnosis_id"]
        )
        shutil.rmtree(ws.parent, ignore_errors=True)

    def test_validator_rejects_causal_claim_and_unknown_ref(self) -> None:
        ws = self.clone()
        ext = ws / "external_executor"
        report_path = ext / "result_diagnosis_report.json"
        report = json.loads(report_path.read_text())
        report["setting_diagnostics"] = {
            "status": "complete",
            "items": [
                {
                    "setting_diagnosis_id": "bad",
                    "finding": "ours_wins",
                    "interpretation_level": "observed_fact",
                    "confidence": "high",
                    "evidence_refs": ["NOPE"],
                    "causal_claim": True,
                }
            ],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        proc = run("validate_diagnosis_report.py", "--workspace", str(ws), check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("causal_claim", proc.stdout)
        self.assertIn("unknown evidence refs", proc.stdout)
        shutil.rmtree(ws.parent, ignore_errors=True)

    def test_root_decision_versions_an_underperforming_method(self) -> None:
        ws = self.clone()
        ext = ws / "external_executor"
        worktree = ext / "expr/implementation/ITER-01/IMPL-01/worktree"
        worktree.mkdir(parents=True)
        (worktree / "method.py").write_text("VERSION = 1\n", encoding="utf-8")
        status = {
            "schema_version": "external_executor_status.v1", "executor_status": "running",
            "iteration_id": "ITER-01", "current_phase": "E", "current_step": "E1",
            "next_action": "result-diagnosis", "active_blockers": [], "budget": {},
        }
        result = {
            "schema_version": "external_executor_result.v1", "executor_status": "running",
            "experiment_plan": {"status": "complete", "experiments": [{"experiment_id": "MAIN", "run_type": "formal"}]},
            "iteration_plans": {"status": "complete", "items": [{"iteration_id": "ITER-01", "iteration_number": 1, "status": "active"}]},
            "implementations": {"status": "complete", "items": [{"implementation_id": "IMPL-01", "iteration_id": "ITER-01", "implementation_root": "external_executor/expr/implementation/ITER-01/IMPL-01"}]},
            "experiment_runs": {"status": "complete", "items": [{"run_id": "RUN-1", "iteration_id": "ITER-01", "experiment_id": "MAIN", "method_id": "ours-v1", "method_role": "ours", "implementation_id": "IMPL-01", "run_type": "formal", "run_status": "completed"}]},
            "result_diagnoses": {"status": "partial", "items": []},
            "iteration_decisions": {"status": "not_started", "items": []},
        }
        diagnosis = {
            "diagnosis_id": "DIAG-LOOP-1", "iteration_id": "ITER-01",
            "baseline_performance": {"all_required_baselines_beaten": False, "required_baseline_ids": ["B1"]},
            "method_change_assessment": {
                "status": "complete", "change_required": True, "change_kind": "method_refinement",
                "rationale": "The method remains below baseline B1.",
                "failure_or_underperformance_causes": ["optimization instability"],
                "proposed_changes": [{"summary": "Stabilize optimization", "target_paths": ["method.py"]}],
                "must_preserve": ["core mechanism"], "prior_iteration_lessons": [], "evidence_refs": ["RUN-1"],
            },
        }
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        (ext / "result_diagnosis_report.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ROOT_DECISION_SCRIPT), "--workspace", str(ws)], text=True, capture_output=True, check=True)
        output = json.loads(proc.stdout)
        self.assertEqual(output["decision"]["next_action"], "method-refinement")
        self.assertEqual(output["next_iteration_plan"]["iteration_number"], 2)
        self.assertTrue(output["next_iteration_plan"]["base_source"].endswith("/worktree"))
        self.assertTrue((ws / output["next_iteration_plan"]["plan_ref"]).is_file())

        result = json.loads((ext / "result_pack.json").read_text())
        final_plan = result["iteration_plans"]["items"][-1]
        final_plan.update(iteration_id="ITER-10", iteration_number=10)
        result["current_iteration_plan"] = final_plan
        status = json.loads((ext / "executor_status.json").read_text())
        status["iteration_id"] = "ITER-10"
        diagnosis["iteration_id"] = "ITER-10"
        diagnosis["diagnosis_id"] = "DIAG-LOOP-10"
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")
        (ext / "result_diagnosis_report.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ROOT_DECISION_SCRIPT), "--workspace", str(ws)], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(proc.stdout)["decision"]["loop_outcome"], "max_iterations_reached")
        shutil.rmtree(ws.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
