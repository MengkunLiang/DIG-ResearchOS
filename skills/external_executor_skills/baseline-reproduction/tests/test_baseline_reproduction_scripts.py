from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(name: str, *args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        text=True,
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def load_prepare_module():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("prepare_attempt_for_test", SCRIPTS / "prepare_attempt.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BaselineReproductionTests(unittest.TestCase):
    def make_workspace(self) -> Path:
        ws = Path(tempfile.mkdtemp(prefix="baseline-repro-test-"))
        (ws / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
        ext = ws / "external_executor"
        ext.mkdir()
        (ext / "AGENTS.md").write_text("controlled test\n", encoding="utf-8")
        (ext / "allowed_paths.txt").write_text("external_executor/\nresources/\n", encoding="utf-8")
        (ext / "expected_outputs_schema.json").write_text(
            json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8"
        )
        source = ws / "resources" / "local" / "cand-a"
        source.mkdir(parents=True)
        (source / "train.py").write_text(
            "import json\n"
            "with open('metrics.json', 'w', encoding='utf-8') as f:\n"
            "    json.dump({'accuracy': 0.81}, f)\n"
            "print('accuracy=0.81')\n",
            encoding="utf-8",
        )
        result = {
            "schema_version": "external_executor_result.v1",
            "context_alignment": {"status": "pass"},
            "resource_readiness": {"status": "ready", "minimum_loop_feasible": True},
            "resource_requirement_matrix": {
                "items": [{"requirement_id": "REQ-A", "required": True}]
            },
            "baseline_candidates": {
                "status": "complete",
                "items": [
                    {
                        "candidate_id": "CAND-A",
                        "baseline_id": "BASE-A",
                        "baseline_name": "Baseline A",
                        "requirement_ids": ["REQ-A"],
                        "local_path": "resources/local/cand-a",
                        "source_class": "official_author_repo",
                        "manifest_sha256": "sourcehash",
                        "approved_for": ["baseline_reproduction"],
                        "reproduction_argv": [sys.executable, "train.py"],
                        "expected_outputs": ["metrics.json"],
                        "working_directory": ".",
                        "seeds": [1],
                        "repeats": 1,
                    }
                ],
            },
            "experiment_plan": {
                "status": "complete",
                "protocol_fingerprint": "PROTO-1",
                "fairness_fingerprint": "FAIR-1",
                "protocol": {
                    "dataset": {"name": "Data", "version": "1", "split": "test"},
                    "metrics": [
                        {
                            "name": "accuracy",
                            "primary": True,
                            "direction": "higher",
                            "aggregation": "mean",
                            "extractor": {
                                "type": "json",
                                "path": "metrics.json",
                                "selector": "accuracy",
                            },
                            "reference": {
                                "type": "absolute_tolerance",
                                "value": 0.8,
                                "tolerance": 0.02,
                                "source_refs": ["paper"],
                            },
                        }
                    ],
                    "seeds": [1],
                    "repeats": 1,
                },
            },
            "current_iteration_plan": {
                "iteration_id": "ITER-1",
                "status": "active",
                "baseline_ids": ["BASE-A"],
                "actions": ["baseline reproduction"],
            },
            "unrelated": {"keep": True},
        }
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        return ws

    def test_end_to_end_reproduction_and_narrow_apply(self) -> None:
        ws = self.make_workspace()
        run("preflight_reproduction.py", "--workspace", str(ws))
        run("build_reproduction_plan.py", "--workspace", str(ws))
        run("initialize_reproduction_report.py", "--workspace", str(ws))

        plan = json.loads((ws / "external_executor/report/phase_D/baseline_reproduction_plan.json").read_text())
        reproduction_id = plan["items"][0]["reproduction_id"]
        run(
            "prepare_attempt.py",
            "--workspace",
            str(ws),
            "--reproduction-id",
            reproduction_id,
            "--attempt",
            "1",
        )
        attempt = next(
            (ws / "external_executor/expr/baselines").glob(
                f"*/{reproduction_id}/attempt-1"
            )
        )
        fragment = json.loads((attempt / "plan_fragment.json").read_text())
        result_dir = ws / fragment["result_dir"]
        evidence_dir = ws / fragment["evidence_dir"]

        old_secret = os.environ.get("SUPER_SECRET_TOKEN")
        os.environ["SUPER_SECRET_TOKEN"] = "do-not-record"
        try:
            run(
                "capture_environment.py",
                "--path",
                str(evidence_dir / "environment.json"),
                "--source",
                str(attempt / "source"),
                "--env-name",
                "SUPER_SECRET_TOKEN",
            )
        finally:
            if old_secret is None:
                os.environ.pop("SUPER_SECRET_TOKEN", None)
            else:
                os.environ["SUPER_SECRET_TOKEN"] = old_secret
        self.assertNotIn("do-not-record", (evidence_dir / "environment.json").read_text())

        proc = run(
            "run_reproduction.py",
            "--workspace",
            str(ws),
            "--reproduction-id",
            reproduction_id,
            "--attempt",
            "1",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((result_dir / "stdout.log").exists())
        self.assertTrue((result_dir / "stderr.log").exists())
        self.assertFalse((result_dir / "run_record.json").exists())
        self.assertTrue((evidence_dir / "run_record.json").exists())
        run(
            "extract_metrics.py",
            "--attempt-dir",
            str(attempt),
            "--spec",
            str(attempt / "plan_fragment.json"),
            "--output",
            str(evidence_dir / "metrics.json"),
        )
        metrics = json.loads((evidence_dir / "metrics.json").read_text())
        raw_csv_path = ws / metrics["items"][0]["raw_csv_path"]
        self.assertTrue(raw_csv_path.exists())
        with raw_csv_path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["dataset_name"], "Data")
        self.assertEqual(rows[0]["dataset_split"], "test")
        self.assertEqual(rows[0]["metric_name"], "accuracy")
        self.assertEqual(float(rows[0]["value"]), 0.81)
        eval_proc = run(
            "evaluate_reproduction.py",
            "--plan-fragment",
            str(attempt / "plan_fragment.json"),
            "--run-record",
            str(evidence_dir / "run_record.json"),
            "--metrics",
            str(evidence_dir / "metrics.json"),
            "--environment",
            str(evidence_dir / "environment.json"),
            "--output",
            str(evidence_dir / "reproduction_evaluation.json"),
            check=False,
        )
        self.assertEqual(eval_proc.returncode, 0, eval_proc.stderr)
        evaluation = json.loads((evidence_dir / "reproduction_evaluation.json").read_text())
        self.assertEqual(evaluation["technical_outcome"], "reproduced_within_tolerance")
        self.assertEqual(evaluation["comparability_status"], "formal_review_candidate")

        report_path = ws / "external_executor/report/phase_D/baseline_reproduction_report.json"
        report = json.loads(report_path.read_text())
        run_record = json.loads((evidence_dir / "run_record.json").read_text())
        item = report["items"][0]
        item.update(
            {
                "status": "reproduced",
                "technical_outcome": evaluation["technical_outcome"],
                "comparability_status": evaluation["comparability_status"],
                "attempts": [
                    {
                        "attempt_id": run_record["run_id"],
                        "run_id": run_record["run_id"],
                        "run_record_ref": str(
                            (evidence_dir / "run_record.json").relative_to(ws).as_posix()
                        ),
                    }
                ],
                "selected_attempt_id": run_record["run_id"],
            }
        )
        item["review"] = {
            "review_id": "REV-1",
            "verdict": "pass",
            "identity_fidelity": "exact",
            "mechanism_fidelity": "high",
            "protocol_fidelity": "exact",
            "fairness_risk": "low",
            "provenance_completeness": "complete",
            "approximation_level": "none",
            "findings": [],
            "required_fixes": [],
            "evidence_refs": [item["attempts"][0]["run_record_ref"]],
            "approved_for": "formal_review_candidate",
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

        run("compute_reproduction_gate.py", "--report", str(report_path), "--write-back")
        run("validate_reproduction_report.py", "--workspace", str(ws))
        run("apply_reproduction_report.py", "--workspace", str(ws))
        result = json.loads((ws / "external_executor/result_pack.json").read_text())
        self.assertEqual(result["baseline_reproduction"]["reproduction_gate"]["status"], "pass")
        self.assertTrue(result["unrelated"]["keep"])

    def test_failure_classifier(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="fail-class-test-"))
        (root / "project.yaml").write_text("project_id: fail\n", encoding="utf-8")
        report = root / "external_executor" / "report" / "phase_D" / "baseline_reproduction" / "case"
        report.mkdir(parents=True)
        (report / "run.json").write_text(
            json.dumps(
                {
                    "run_id": "RUN-1",
                    "reproduction_id": "R",
                    "status": "failed",
                    "output_checks": [],
                }
            ),
            encoding="utf-8",
        )
        (report / "stdout.log").write_text("", encoding="utf-8")
        (report / "stderr.log").write_text("CUDA out of memory", encoding="utf-8")
        run(
            "classify_failure.py",
            "--run-record",
            str(report / "run.json"),
            "--stdout",
            str(report / "stdout.log"),
            "--stderr",
            str(report / "stderr.log"),
            "--output",
            str(report / "failure.json"),
        )
        data = json.loads((report / "failure.json").read_text())
        self.assertEqual(data["primary_category"], "out_of_memory")

    def test_prepare_rejects_escaping_symlink(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="symlink-test-"))
        try:
            (root / "escape").symlink_to("/tmp")
        except OSError:
            self.skipTest("symlinks unavailable")
        module = load_prepare_module()
        with self.assertRaises(ValueError):
            module.reject_symlinks(root)

    def test_next_attempt_inherits_deployed_debug_repairs(self) -> None:
        ws = self.make_workspace()
        run("preflight_reproduction.py", "--workspace", str(ws))
        run("build_reproduction_plan.py", "--workspace", str(ws))
        plan = json.loads((ws / "external_executor/report/phase_D/baseline_reproduction_plan.json").read_text())
        reproduction_id = plan["items"][0]["reproduction_id"]

        run(
            "prepare_attempt.py", "--workspace", str(ws),
            "--reproduction-id", reproduction_id, "--attempt", "1",
        )
        attempt_1 = next((ws / "external_executor/expr/baselines").glob(f"*/{reproduction_id}/attempt-1"))
        repaired = attempt_1 / "source/train.py"
        repaired.write_text(repaired.read_text(encoding="utf-8") + "# compatibility repair\n", encoding="utf-8")
        (attempt_1 / "configs/runtime.json").write_text('{"device": "cpu"}\n', encoding="utf-8")
        (attempt_1 / "patches/compatibility.patch").write_text("compatibility repair\n", encoding="utf-8")

        run(
            "prepare_attempt.py", "--workspace", str(ws),
            "--reproduction-id", reproduction_id, "--attempt", "2",
        )
        attempt_2 = attempt_1.parent / "attempt-2"
        self.assertIn("# compatibility repair", (attempt_2 / "source/train.py").read_text(encoding="utf-8"))
        self.assertEqual((attempt_2 / "configs/runtime.json").read_text(encoding="utf-8"), '{"device": "cpu"}\n')
        self.assertEqual((attempt_2 / "patches/compatibility.patch").read_text(encoding="utf-8"), "compatibility repair\n")
        provenance = json.loads((attempt_2 / "attempt_provenance.json").read_text())
        self.assertEqual(provenance["parent_attempt"], 1)
        self.assertTrue(provenance["inherits_deployed_repairs"])
        self.assertEqual(provenance["prepared_from_path"], str((attempt_1 / "source").relative_to(ws)))


if __name__ == "__main__":
    unittest.main()
