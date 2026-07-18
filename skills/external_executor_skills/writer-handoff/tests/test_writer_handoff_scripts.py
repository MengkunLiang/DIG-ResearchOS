from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(ws: Path, rel: str, artifact_id: str) -> dict[str, object]:
    path = ws / rel
    return {
        "artifact_id": artifact_id,
        "path": rel,
        "sha256": sha(path),
        "size_bytes": path.stat().st_size,
        "producer": "test",
    }


def run(script: str, *args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"{script} failed\nOUT:{completed.stdout}\nERR:{completed.stderr}")
    return completed


def make_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="writer-handoff-v2-"))
    ext = ws / "external_executor"
    ext.mkdir()
    (ws / "project.yaml").write_text("project_id: handoff-test\n", encoding="utf-8")
    (ext / "AGENTS.md").write_text("Use allowed paths.\n", encoding="utf-8")
    (ext / "allowed_paths.txt").write_text("rw external_executor/\n", encoding="utf-8")
    (ext / "handoff_pack.json").write_text(json.dumps({
        "schema_version": "external_executor_handoff.v1",
        "context_reboost": {
            "research_question": "Can a learned gate improve prediction under the locked benchmark?",
            "hypotheses": ["H1 predicts higher accuracy under the locked protocol."],
            "expected_contributions": [{"contribution_id": "CONTR-1", "statement": "A gated fusion method."}],
        },
    }), encoding="utf-8")
    (ext / "expected_outputs_schema.json").write_text(
        json.dumps({"schema_version": "external_executor_schema.v1"}), encoding="utf-8"
    )
    (ext / "executor_status.json").write_text(json.dumps({
        "schema_version": "external_executor_status.v1",
        "executor_status": "completed",
        "accepted": False,
    }), encoding="utf-8")

    files = {
        "external_executor/expr/ITER-01/worktree/model.py": "class GateModel: pass\n",
        "external_executor/expr/ITER-01/config.json": '{"seed": 1}\n',
        "external_executor/raw_results/main/ours.csv": "seed,accuracy\n1,0.82\n2,0.80\n",
        "external_executor/raw_results/main/baseline.csv": "seed,accuracy\n1,0.75\n2,0.76\n",
        "external_executor/raw_results/main/ours.log": "completed\n",
        "external_executor/raw_results/main/baseline.log": "completed\n",
        "external_executor/figure/framework_figure.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>\n",
        "external_executor/figure/main_dataset-a_test_accuracy_p1.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>\n",
        "external_executor/table/main_comparison.csv": (
            "table_kind,dataset,split,metric,metric_direction,method_id,method_role,variant,baseline_id,n,mean,std,min,max,run_ids,experiment_ids,protocol_fingerprint,source_files\n"
            "main,dataset-a,test,accuracy,higher,GateModel,ours,,,2,0.81,0.0141,0.80,0.82,RUN-O,E-MAIN,P1,external_executor/raw_results/main/ours.csv\n"
            "main,dataset-a,test,accuracy,higher,BaselineA,baseline,,BaselineA,2,0.755,0.0071,0.75,0.76,RUN-B,E-MAIN,P1,external_executor/raw_results/main/baseline.csv\n"
        ),
    }
    for rel, content in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = {
        "schema_version": "external_executor_result.v1",
        "executor_status": "completed",
        "context_alignment": {"status": "pass", "research_question": "Can a learned gate improve prediction under the locked benchmark?"},
        "resource_readiness": {"status": "ready"},
        "baseline_reproduction": {"status": "complete", "items": [{"baseline_id": "BaselineA", "status": "completed"}]},
        "claim_evidence_matrix": {"status": "complete", "items": [{
            "claim_id": "C1",
            "claim": "The realized method improves accuracy under the locked protocol.",
            "experiment_id": "E-MAIN",
            "hypothesis_id": "H1",
            "contribution_id": "CONTR-1",
            "must_not_claim": ["Do not claim universal superiority."],
        }]},
        "experiment_plan": {"status": "complete", "experiments": [{
            "experiment_id": "E-MAIN",
            "objective": "Compare the realized method with the required baseline.",
            "hypothesis_id": "H1",
            "contribution_id": "CONTR-1",
            "dataset": "dataset-a",
        }]},
        "experiment_runs": {"status": "complete", "items": [
            {
                "run_id": "RUN-O", "experiment_id": "E-MAIN", "method_id": "GateModel", "method_role": "ours",
                "run_type": "formal", "status": "completed", "dataset": "dataset-a", "split": "test", "seed": 1,
                "metrics": {"accuracy": 0.82}, "protocol_fingerprint": "P1",
                "config_path": "external_executor/expr/ITER-01/config.json",
                "raw_result_path": "external_executor/raw_results/main/ours.csv",
                "raw_log_path": "external_executor/raw_results/main/ours.log",
            },
            {
                "run_id": "RUN-B", "experiment_id": "E-MAIN", "method_id": "BaselineA", "method_role": "baseline",
                "baseline_id": "BaselineA", "run_type": "formal", "status": "completed", "dataset": "dataset-a",
                "split": "test", "seed": 1, "metrics": {"accuracy": 0.75}, "protocol_fingerprint": "P1",
                "config_path": "external_executor/expr/ITER-01/config.json",
                "raw_result_path": "external_executor/raw_results/main/baseline.csv",
                "raw_log_path": "external_executor/raw_results/main/baseline.log",
            },
        ]},
        "implementations": {"status": "complete", "active_implementation_id": "IMPL-1", "items": [{
            "implementation_id": "IMPL-1", "implementation_root": "external_executor/expr/ITER-01/worktree",
            "code_entrypoint": "external_executor/expr/ITER-01/worktree/model.py",
        }]},
        "implementation_reviews": {"status": "complete", "items": [{"review_id": "REV-1", "review_status": "pass"}]},
        "result_diagnoses": {"status": "complete", "items": [{"diagnosis_id": "DIAG-1", "status": "complete"}]},
        "module_attributions": {"status": "complete", "items": [{"attribution_id": "ATTR-1", "status": "complete"}]},
        "realized_method_package": {
            "status": "complete", "final_method_name": "GateModel",
            "one_sentence_method": "GateModel uses a learned gate to combine two representations.",
            "implemented_modules": [{
                "module_id": "M1", "name": "Learned gate", "actual_role": "Combine representations.",
                "code_paths": ["external_executor/expr/ITER-01/worktree/model.py"],
                "config_keys": ["gate.enabled"], "empirical_support": {"status": "supported"},
            }],
            "claim_boundary": {"must_not_claim": ["Universal superiority outside dataset-a."]},
        },
        "framework_figure": {
            "status": "ready_for_T7_audit",
            "rendered_files": [{"path": "external_executor/figure/framework_figure.svg"}],
        },
        "figure_table_inventory": {"status": "complete", "items": [
            {"kind": "figure", "path": "external_executor/figure/main_dataset-a_test_accuracy_p1.svg"},
            {"kind": "table", "path": "external_executor/table/main_comparison.csv"},
        ]},
        "evidence_packaging": {"status": "ready", "result_tables": {"tables": [{"path": "external_executor/table/main_comparison.csv"}]}},
        "claim_boundary": {"must_not_claim": ["Universal superiority outside dataset-a."]},
        "verified_literature_additions": {"items": [{
            "title": "A Verified Method Paper", "authors": ["A. Author"], "year": 2024,
            "venue": "Journal of Verified Research", "doi": "10.1234/verified.2024.1",
            "supported_point": "Defines the baseline estimator used in implementation.",
            "used_material": "Method definition", "access_level": "full text",
            "citation": "Author, A. (2024). A Verified Method Paper.",
        }]},
    }
    (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
    manifest_paths = list(files)
    artifacts = [artifact(ws, rel, f"ART-{index}") for index, rel in enumerate(manifest_paths, start=1)]
    (ext / "report").mkdir(exist_ok=True)
    (ext / "report/run_manifest.json").write_text(json.dumps({
        "schema_version": "external_executor_manifest.v1",
        "artifacts": artifacts,
    }), encoding="utf-8")
    return ws


def pipeline(ws: Path, *, validate: bool = True) -> subprocess.CompletedProcess[str] | None:
    run("preflight_handoff.py", "--workspace", ws)
    run("build_handoff_snapshot.py", "--workspace", ws)
    run("build_research_report_facts.py", "--workspace", ws)
    run("render_executor_research_report.py", "--workspace", ws)
    return run("validate_writer_handoff.py", "--workspace", ws, check=False) if validate else None


class WriterHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = make_workspace()

    def tearDown(self) -> None:
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_complete_pipeline_builds_auditable_report(self) -> None:
        completed = pipeline(self.ws)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        ext = self.ws / "external_executor"
        report = (ext / "executor_research_report.md").read_text(encoding="utf-8")
        for heading in (
            "## 1. Project Summary", "## 2. Implementation Summary", "## 3. Experiment Inventory",
            "## 4. Comprehensive Results", "## 5. Claim Support Table",
            "## 6. Verified Literature Additions", "## 7. Limitations and Open Issues", "## 8. Artifact Index",
        ):
            self.assertIn(heading, report)
        self.assertIn("E-MAIN", report)
        self.assertIn("0.81", report)
        self.assertIn("0.755", report)
        self.assertIn("external_executor/raw_results/main/ours.csv", report)
        self.assertIn("10.1234/verified.2024.1", report)
        facts = json.loads((ext / "report/writer_handoff_facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts["coverage"]["experiment_count"], 1)
        self.assertEqual(facts["coverage"]["result_record_count"], 1)
        validation = json.loads((ext / "report/writer_handoff_validation.json").read_text(encoding="utf-8"))
        self.assertEqual(validation["status"], "ready")
        for old in (
            "writer_handoff_inventory.json", "writer_handoff_claim_map.json", "writer_handoff_t7_index.json",
            "writer_handoff_integrity.json", "writer_handoff_report.json",
        ):
            self.assertFalse((ext / old).exists())

    def test_status_mismatch_is_blocking(self) -> None:
        status_path = self.ws / "external_executor/executor_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["executor_status"] = "partial"
        status_path.write_text(json.dumps(status), encoding="utf-8")
        completed = pipeline(self.ws)
        self.assertNotEqual(completed.returncode, 0)
        validation = json.loads((self.ws / "external_executor/report/writer_handoff_validation.json").read_text())
        self.assertTrue(any(item["code"] == "terminal_status_mismatch" for item in validation["errors"]))

    def test_matching_partial_outcome_produces_constrained_handoff(self) -> None:
        ext = self.ws / "external_executor"
        status_path = ext / "executor_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["executor_status"] = "partial"
        status_path.write_text(json.dumps(status), encoding="utf-8")
        result_path = ext / "result_pack.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["executor_status"] = "partial"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        completed = pipeline(self.ws)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        validation = json.loads((ext / "report/writer_handoff_validation.json").read_text())
        self.assertEqual(validation["status"], "partial")

    def test_unfavorable_comparison_is_preserved_and_weakens_claim(self) -> None:
        ext = self.ws / "external_executor"
        table_path = ext / "table/main_comparison.csv"
        with table_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "main,dataset-a,test,accuracy,higher,StrongerBaseline,baseline,,StrongerBaseline,2,0.85,0.01,0.84,0.86,RUN-SB,E-MAIN,P1,external_executor/raw_results/main/baseline.csv\n"
            )
        manifest_path = ext / "report/run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(item for item in manifest["artifacts"] if item["path"] == "external_executor/table/main_comparison.csv")
        record["sha256"] = sha(table_path)
        record["size_bytes"] = table_path.stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        completed = pipeline(self.ws)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        facts = json.loads((ext / "report/writer_handoff_facts.json").read_text())
        self.assertEqual(facts["coverage"]["result_record_count"], 2)
        self.assertEqual(facts["claim_support"][0]["strength"], "Partially supported candidate")
        report = (ext / "executor_research_report.md").read_text(encoding="utf-8")
        self.assertIn("StrongerBaseline=0.85", report)
        self.assertIn("unfavorable", report)

    def test_unregistered_final_asset_is_blocking(self) -> None:
        extra = self.ws / "external_executor/figure/unregistered.svg"
        extra.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>\n", encoding="utf-8")
        completed = pipeline(self.ws)
        self.assertNotEqual(completed.returncode, 0)
        validation = json.loads((self.ws / "external_executor/report/writer_handoff_validation.json").read_text())
        self.assertTrue(any(item["code"] == "final_asset_unregistered" for item in validation["errors"]))

    def test_snapshot_detects_core_mutation(self) -> None:
        pipeline(self.ws, validate=False)
        result_path = self.ws / "external_executor/result_pack.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["claim_boundary"]["must_not_claim"].append("Post-snapshot mutation")
        result_path.write_text(json.dumps(result), encoding="utf-8")
        completed = run("validate_writer_handoff.py", "--workspace", self.ws, check=False)
        self.assertNotEqual(completed.returncode, 0)
        validation = json.loads((self.ws / "external_executor/report/writer_handoff_validation.json").read_text())
        self.assertTrue(any(item["code"] == "core_file_changed_after_snapshot" for item in validation["errors"]))

    def test_promotional_authority_language_is_rejected(self) -> None:
        pipeline(self.ws, validate=False)
        report_path = self.ws / "external_executor/executor_research_report.md"
        report_path.write_text(report_path.read_text(encoding="utf-8") + "\nThe method proves that it is state-of-the-art.\n", encoding="utf-8")
        completed = run("validate_writer_handoff.py", "--workspace", self.ws, check=False)
        self.assertNotEqual(completed.returncode, 0)
        validation = json.loads((self.ws / "external_executor/report/writer_handoff_validation.json").read_text())
        self.assertTrue(any(item["code"] == "forbidden_authority_or_promotional_phrase" for item in validation["errors"]))


if __name__ == "__main__":
    unittest.main()
