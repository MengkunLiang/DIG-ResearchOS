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
                    "raw_log_ref": f"logs/{mid}-{seed}.log",
                    "metric_output_ref": f"raw/{mid}-{seed}.json",
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
    run(
        "build_evidence_snapshot.py",
        "--workspace",
        str(ws),
        "--iteration-id",
        "iter-1",
    )
    work = ext / "workdir/result_diagnosis/iter-1"
    work.mkdir(parents=True, exist_ok=True)
    obs = work / "metric_observations.json"
    aggs = work / "metric_aggregates.json"
    comps = work / "method_comparisons.json"
    anoms = work / "anomalies.json"
    run(
        "normalize_run_metrics.py",
        "--snapshot",
        str(ext / "diagnosis_evidence_snapshot.json"),
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
        str(ext / "diagnosis_evidence_snapshot.json"),
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
        str(ext / "diagnosis_evidence_snapshot.json"),
        "--observations",
        str(obs),
        "--aggregates",
        str(aggs),
        "--comparisons",
        str(comps),
        "--anomalies",
        str(anoms),
        "--output",
        str(ext / "diagnosis_statistics.json"),
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
        snapshot = json.loads((ext / "diagnosis_evidence_snapshot.json").read_text())
        self.assertEqual(len(snapshot["included_run_ids"]), 9)
        work = ext / "workdir/result_diagnosis/iter-1"
        aggregates = json.loads((work / "metric_aggregates.json").read_text())
        comparisons = json.loads((work / "method_comparisons.json").read_text())
        stats = json.loads((ext / "diagnosis_statistics.json").read_text())
        self.assertEqual(len(aggregates["items"]), 3)
        self.assertEqual(len(comparisons["items"]), 2)
        self.assertTrue(all(x["numeric_outcome"] == "win" for x in comparisons["items"]))
        self.assertEqual(
            stats["strongest_baselines"]["items"][0]["baseline_method_id"], "base-b"
        )

        # Exercise the insufficient-repeat detector without rebuilding the whole pipeline.
        ws2 = self.clone()
        work2 = ws2 / "external_executor/workdir/result_diagnosis/iter-1"
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
            str(ws2 / "external_executor/diagnosis_evidence_snapshot.json"),
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


if __name__ == "__main__":
    unittest.main()
