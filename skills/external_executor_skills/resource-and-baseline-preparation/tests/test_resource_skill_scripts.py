from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def run(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], text=True, capture_output=True, check=check)


class ResourceSkillScriptTests(unittest.TestCase):
    def make_workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="resource-skill-test-"))
        (root / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
        ext = root / "external_executor"
        ext.mkdir()
        (ext / "report").mkdir()
        (ext / "AGENTS.md").write_text("test controls\n", encoding="utf-8")
        (ext / "allowed_paths.txt").write_text("external_executor/\nresources/\n", encoding="utf-8")
        (ext / "expected_outputs_schema.json").write_text(json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8")
        handoff = {
            "schema_version": "external_executor_handoff.v1",
            "method_intent": {"required_checkpoints": []},
        }
        (ext / "handoff_pack.json").write_text(json.dumps(handoff), encoding="utf-8")
        scope = {
            "required_baselines": [{"name": "Baseline A"}],
            "benchmark_protocol_summary": {"benchmark": "Bench", "dataset": "Data", "split": "official", "metric": "accuracy"},
            "minimum_experiment_loop": ["baseline", "ours"],
            "claim_boundaries": ["no superiority without Baseline A"],
            "resource_acquisition_policy": {
                "mode": "local_only",
                "network_allowed": False,
                "allowed_domains": [],
                "dataset_download_allowed": False,
                "baseline_reimplementation_allowed": False,
                "replacement_requires_review": True,
            },
        }
        result = {
            "schema_version": "external_executor_result.v1",
            "context_alignment": {"status": "pass", "confirmed_execution_scope": scope},
        }
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        (ext / "expr").mkdir()
        local = root / "resources" / "baseline_a"
        local.mkdir(parents=True)
        (local / "README.md").write_text("baseline\n", encoding="utf-8")
        (local / "LICENSE").write_text("MIT\n", encoding="utf-8")
        return root

    def test_preflight_and_matrix(self) -> None:
        ws = self.make_workspace()
        run("preflight_resources.py", "--workspace", str(ws))
        preflight = json.loads((ws / "external_executor/report/resource_preflight.json").read_text())
        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(preflight["policy_snapshot"]["effective_mode"], "local_only")
        self.assertFalse(preflight["policy_snapshot"]["effective_network_allowed"])
        run("build_requirement_matrix.py", "--workspace", str(ws))
        matrix = json.loads((ws / "external_executor/resource_requirement_matrix.json").read_text())
        self.assertTrue(any(i["resource_type"] == "baseline_implementation" for i in matrix["items"]))
        self.assertTrue(any(i["resource_type"] == "dataset" for i in matrix["items"]))

    def test_local_inventory(self) -> None:
        ws = self.make_workspace()
        run("inventory_local_resources.py", "--workspace", str(ws))
        inventory = json.loads((ws / "external_executor/report/resource_local_inventory.json").read_text())
        self.assertGreaterEqual(len(inventory["items"]), 1)
        self.assertTrue(any("LICENSE" in item["license_files"] for item in inventory["items"]))

    def test_initialize_report_preserves_sections(self) -> None:
        ws = self.make_workspace()
        run("preflight_resources.py", "--workspace", str(ws))
        run("build_requirement_matrix.py", "--workspace", str(ws))
        run("inventory_local_resources.py", "--workspace", str(ws))
        run("initialize_resource_report.py", "--workspace", str(ws))
        path = ws / "external_executor/report/resource_preparation_report.json"
        report = json.loads(path.read_text())
        report["notes"] = ["preserve-me"]
        path.write_text(json.dumps(report), encoding="utf-8")
        run("initialize_resource_report.py", "--workspace", str(ws))
        refreshed = json.loads(path.read_text())
        self.assertEqual(refreshed["notes"], ["preserve-me"])
        self.assertEqual(refreshed["resource_requirement_matrix"]["schema_version"], "resource_requirement_matrix.v1")
        self.assertEqual(refreshed["remote_search_records"]["status"], "not_needed")

    def test_report_validation_and_apply(self) -> None:
        ws = self.make_workspace()
        run("preflight_resources.py", "--workspace", str(ws))
        run("build_requirement_matrix.py", "--workspace", str(ws))
        matrix = json.loads((ws / "external_executor/resource_requirement_matrix.json").read_text())
        candidates = []
        reviews = []
        for req in matrix["items"]:
            cid = "CAND-" + req["requirement_id"]
            candidates.append({"candidate_id": cid, "requirement_ids": [req["requirement_id"]]})
            approval = {
                "baseline_implementation": "baseline_reproduction",
                "dataset": "dataset_use",
                "dataset_split": "dataset_use",
                "benchmark_definition": "experiment_design",
                "metric_implementation": "metric_use",
                "preprocessing": "preprocessing_use",
            }.get(req["resource_type"], "experiment_design")
            review = {
                "review_id": "REV-" + req["requirement_id"], "candidate_id": cid, "requirement_ids": [req["requirement_id"]],
                "verdict": "pass", "approved_for": [approval], "approximation_level": "none",
                "fairness_risk": "low", "license_risk": "low",
            }
            if req["resource_type"] == "baseline_implementation":
                review["executable_baseline_criteria"] = {
                    "accessible_code_or_model": True,
                    "revision_locked": True,
                    "license_clear": True,
                    "environment_or_dependencies": True,
                    "dataset_version_and_split": True,
                    "metric_implementation": True,
                    "traceable_result_record": True,
                }
            reviews.append(review)
        report = {
            "schema_version": "resource_preparation_report.v1", "child_skill": "resource-and-baseline-preparation",
            "status": "complete", "generated_at": "2026-01-01T00:00:00Z", "input_fingerprint": "x", "policy_snapshot": {},
            "resource_requirement_matrix": matrix,
            "local_inventory": {"status": "complete", "items": []},
            "remote_search_records": {"status": "not_needed", "items": []},
            "staged_resources": {"status": "complete", "items": candidates},
            "acquired_resources": {"status": "not_needed", "items": []},
            "baseline_candidates": {"status": "complete", "items": candidates},
            "dataset_inventory": {"status": "complete", "items": []},
            "reimplementations": {"status": "not_needed", "items": []},
            "resource_source_report": {
                "status": "complete",
                "json_path": "external_executor/report/resource_source_report.json",
                "markdown_path": "external_executor/report/resource_source_report.md",
                "source_roots": ["resources"],
                "counts": {"byhand": 0, "Remote_acquisition": 0, "reproduction": 0},
                "categories": {"byhand": [], "Remote_acquisition": [], "reproduction": []},
            },
            "resource_reviews": {"status": "complete", "items": reviews},
            "material_gaps": {"status": "complete", "items": []},
            "resource_risks": {"status": "complete", "items": []},
            "resource_readiness": {"status": "ready", "minimum_loop_feasible": True, "approved_requirement_ids": [], "constrained_requirement_ids": [], "blocking_requirement_ids": [], "claim_constraints": [], "blocking_issues": [], "next_action": "continue_to_experiment_design"},
            "artifact_refs": [], "notes": [],
        }
        report_path = ws / "external_executor/report/resource_preparation_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        run("compute_resource_readiness.py", "--workspace", str(ws), "--report", str(report_path), "--write-back")
        run("validate_resource_report.py", "--workspace", str(ws))
        run("apply_resource_report.py", "--workspace", str(ws))
        result = json.loads((ws / "external_executor/result_pack.json").read_text())
        self.assertEqual(result["resource_readiness"]["status"], "ready")
        self.assertIn("context_alignment", result)

    def test_static_review_blocks_destructive_pattern(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="static-review-test-"))
        (root / "project.yaml").write_text("project_id: static\n", encoding="utf-8")
        (root / "external_executor").mkdir()
        (root / "external_executor" / "report").mkdir()
        (root / "external_executor/allowed_paths.txt").write_text(".\n", encoding="utf-8")
        (root / "bad.sh").write_text("#!/bin/sh\nrm -rf /\n", encoding="utf-8")
        output = root / "external_executor" / "report" / "static_review.json"
        proc = run("static_review_repository.py", "--workspace", str(root), "--path", str(root), check=False)
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(output.read_text())
        self.assertEqual(report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
