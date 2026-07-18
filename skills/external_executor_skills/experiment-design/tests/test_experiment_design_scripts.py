from __future__ import annotations

import json
import shutil
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


def make_workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="experiment-design-test-"))
    (root / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    ext = root / "external_executor"
    ext.mkdir()
    (ext / "AGENTS.md").write_text("test controls\n", encoding="utf-8")
    (ext / "allowed_paths.txt").write_text("external_executor/\n", encoding="utf-8")
    (ext / "expected_outputs_schema.json").write_text(
        json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8"
    )
    handoff = {
        "schema_version": "external_executor_handoff.v1",
        "context_reboost": {
            "central_hypothesis": "Mechanism M improves accuracy under the official split.",
            "claim_evidence_matrix": [{
                "claim_id": "CLM-main",
                "claim": "Mechanism M improves accuracy under the official split.",
                "required": True,
                "reviewer_question": "Does M improve accuracy over Baseline A under the same protocol?",
                "evidence_needed": ["main comparison", "mechanism ablation"],
            }],
            "minimum_experiment_loop": ["baseline_reproduction", "ours_smoke", "main_comparison", "mechanism_ablation"],
            "iteration_budget": {
                "max_rounds": 2,
                "max_total_runs": 20,
                "max_gpu_hours": 40,
                "stop_conditions": ["budget_exhausted", "improvement_plateau"],
            },
        },
        "method_intent": {
            "mechanism_to_ablation_plan": [{
                "mechanism": "M",
                "related_module": "M",
                "planned_test": "replace M with a matched-capacity neutral module",
                "expected_observation_if_supported": "Ours exceeds the matched control.",
                "expected_observation_if_not_supported": "The matched control is indistinguishable from ours.",
                "related_claim": "CLM-main",
            }]
        },
    }
    (ext / "handoff_pack.json").write_text(json.dumps(handoff), encoding="utf-8")
    scope = {
        "central_hypothesis": handoff["context_reboost"]["central_hypothesis"],
        "claim_evidence_matrix": handoff["context_reboost"]["claim_evidence_matrix"],
        "required_baselines": [{"name": "Baseline A", "required": True}],
        "benchmark_protocol_summary": {
            "benchmark": "Bench",
            "task": "classification",
            "dataset": "Data",
            "dataset_version": "1.0",
            "split": "official",
            "preprocessing": "official preprocessing",
            "primary_metric": "accuracy",
            "metric_direction": "higher_is_better",
            "aggregation": "mean_over_seeds",
            "seed_policy": {"seeds": [1, 2, 3], "seed_count": 3, "repeats": 1},
            "evaluation_script": "external_executor/workdir/eval.py",
            "statistics": {"uncertainty_strategy": "standard_deviation"},
            "hyperparameter_search_policy": "fixed_published_config",
            "hyperparameter_fairness_rule": "same tuning opportunity",
        },
        "minimum_experiment_loop": handoff["context_reboost"]["minimum_experiment_loop"],
        "claim_boundaries": ["No superiority claim without Baseline A"],
        "iteration_budget": handoff["context_reboost"]["iteration_budget"],
    }
    result = {
        "schema_version": "external_executor_result.v1",
        "context_alignment": {"status": "pass", "confirmed_execution_scope": scope},
        "resource_requirement_matrix": {"status": "complete", "items": []},
        "resources": {"status": "complete", "items": []},
        "baseline_candidates": {"status": "complete", "items": [{
            "candidate_id": "BASE-A",
            "baseline_id": "BASE-A",
            "name": "Baseline A",
            "path": "external_executor/workdir/resources/baseline-a",
            "required": True,
            "approximation_level": "none",
        }]},
        "dataset_inventory": {"status": "complete", "items": [{
            "dataset_id": "DATA-1", "path": "external_executor/workdir/resources/data"
        }]},
        "material_gaps": {"status": "complete", "items": []},
        "resource_risks": {"status": "complete", "items": []},
        "resource_readiness": {"status": "ready", "minimum_loop_feasible": True, "claim_constraints": [], "blocking_issues": []},
        "sentinel_sibling_section": {"preserve": True},
    }
    (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
    return root


def build_components(ws: Path) -> None:
    run("preflight_experiment_design.py", "--workspace", str(ws))
    run("build_claim_evidence_matrix.py", "--workspace", str(ws))
    run("build_protocol_snapshot.py", "--workspace", str(ws))
    run("fingerprint_protocol.py", "--workspace", str(ws), "--write-back")
    run("build_experiment_plan.py", "--workspace", str(ws))


class ExperimentDesignScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_ws = make_workspace()
        build_components(cls.base_ws)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base_ws, ignore_errors=True)

    def clone_workspace(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="experiment-design-clone-")) / "workspace"
        shutil.copytree(self.base_ws, target)
        return target

    def test_end_to_end_plan_gate_and_narrow_apply(self) -> None:
        ws = self.clone_workspace()
        preflight = json.loads((ws / "external_executor/report/experiment_design_preflight.json").read_text())
        self.assertEqual(preflight["status"], "pass")
        claims = json.loads((ws / "external_executor/report/claim_evidence_matrix.json").read_text())
        self.assertEqual(claims["required_claim_ids"], ["CLM-main"])
        fp1 = json.loads((ws / "external_executor/report/protocol_fingerprint.json").read_text())["fingerprint"]
        run("fingerprint_protocol.py", "--workspace", str(ws), "--write-back")
        fp2 = json.loads((ws / "external_executor/report/protocol_fingerprint.json").read_text())["fingerprint"]
        self.assertEqual(fp1, fp2)

        plan_path = ws / "external_executor/experiment_plan.json"
        plan = json.loads(plan_path.read_text())
        kinds = {e["experiment_kind"] for e in plan["experiments"]}
        self.assertTrue({"baseline_reproduction", "ours_smoke", "main_comparison", "mechanism_ablation"}.issubset(kinds))
        main = next(e for e in plan["experiments"] if e["experiment_kind"] == "main_comparison")
        self.assertEqual(main["claim_ids"], ["CLM-main"])
        ablation = next(e for e in plan["experiments"] if e["run_type"] == "ablation")
        self.assertEqual(ablation["mechanism_ref"], "M")
        self.assertEqual(ablation["attribution_contract"]["target_module_ids"], ["M"])
        self.assertEqual(len(ablation["attribution_contract"]["variant_contracts"]), 2)
        self.assertTrue(ablation["preprocessing_fingerprint"])
        self.assertTrue(ablation["fairness_fingerprint"])

        plan["estimated_budget"]["total_runs"] = 12
        for exp in plan["experiments"]:
            exp["estimated_cost"]["runs"] = 3 if exp["run_type"] != "smoke" else 1
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        run("validate_plan_dag.py", "--workspace", str(ws))
        run("validate_experiment_plan.py", "--workspace", str(ws))
        validation = json.loads((ws / "external_executor/report/experiment_plan_validation.json").read_text())
        self.assertEqual(validation["status"], "pass", validation)
        run("compute_design_gate.py", "--workspace", str(ws), "--write-back")
        gate = json.loads((ws / "external_executor/report/experiment_design_gate.json").read_text())
        self.assertEqual(gate["status"], "ready", gate)
        run("assemble_experiment_design_report.py", "--workspace", str(ws))
        run("validate_experiment_design_report.py", "--workspace", str(ws))
        run("apply_experiment_design_report.py", "--workspace", str(ws))
        result = json.loads((ws / "external_executor/result_pack.json").read_text())
        self.assertIn("claim_evidence_matrix", result)
        self.assertIn("experiment_plan", result)
        self.assertEqual(result["sentinel_sibling_section"], {"preserve": True})

    def test_cycle_is_blocked(self) -> None:
        ws = self.clone_workspace()
        path = ws / "external_executor/experiment_plan.json"
        plan = json.loads(path.read_text())
        first, second = plan["experiments"][0], plan["experiments"][1]
        first["depends_on"] = [second["experiment_id"]]
        second["depends_on"] = [first["experiment_id"]]
        plan["execution_dag"]["edges"] += [
            {"from": second["experiment_id"], "to": first["experiment_id"], "type": "requires"},
            {"from": first["experiment_id"], "to": second["experiment_id"], "type": "requires"},
        ]
        path.write_text(json.dumps(plan), encoding="utf-8")
        proc = run("validate_plan_dag.py", "--workspace", str(ws), check=False)
        self.assertNotEqual(proc.returncode, 0)
        dag = json.loads((ws / "external_executor/report/experiment_plan_dag_validation.json").read_text())
        self.assertEqual(dag["status"], "blocked")
        self.assertTrue(any(x.startswith("cycle_detected") for x in dag["errors"]))

    def test_material_protocol_change_is_detected(self) -> None:
        ws = self.clone_workspace()
        old_path = ws / "external_executor/report/protocol_old.json"
        new_path = ws / "external_executor/report/protocol_new.json"
        old = json.loads((ws / "external_executor/report/protocol_snapshot.json").read_text())
        new = json.loads(json.dumps(old))
        new["protocol_version"] = old["protocol_version"] + 1
        new["protocol"]["dataset"]["split"] = "new_split"
        old_path.write_text(json.dumps(old), encoding="utf-8")
        new_path.write_text(json.dumps(new), encoding="utf-8")
        run(
            "compare_protocol_versions.py",
            "--workspace", str(ws),
            "--old", "external_executor/report/protocol_old.json",
            "--new", "external_executor/report/protocol_new.json",
            "--plan", "external_executor/experiment_plan.json",
        )
        impact = json.loads((ws / "external_executor/report/protocol_change_impact.json").read_text())
        self.assertTrue(impact["material_change"])
        self.assertEqual(impact["required_action"], "version_and_mark_affected_results_stale")


if __name__ == "__main__":
    unittest.main()
