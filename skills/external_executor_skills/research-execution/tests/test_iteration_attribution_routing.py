from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


def run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )


def ablation_run(variant_id: str, enabled: bool) -> dict:
    return {
        "run_id": f"RUN-{variant_id}",
        "experiment_id": "EXP-ABL",
        "iteration_id": "ITER-1",
        "run_type": "ablation",
        "method_role": "ours",
        "method_id": "ours",
        "implementation_id": "IMPL-1",
        "run_status": "completed",
        "variant_id": variant_id,
        "reference_variant_id": "full",
        "pair_id": "PAIR-1",
        "target_module_ids": ["M1"],
        "module_states": {"M1": enabled},
        "intervention": {"type": "none" if enabled else "module_ablation", "controlled": True, "module_ids": ["M1"]},
        "metric_directions": {"accuracy": "higher_is_better"},
        "protocol_fingerprint": "proto-1",
        "dataset": {"id": "Data", "version": "v1", "split": "test"},
        "preprocessing_fingerprint": "prep-1",
        "fairness_fingerprint": "fair-1",
        "setting": "default",
        "subset": "all",
        "seed": 1,
        "repeat_index": 0,
    }


class IterationAttributionRoutingTests(unittest.TestCase):
    def make_workspace(self, *, complete_ablation: bool) -> Path:
        root = Path(tempfile.mkdtemp(prefix="iteration-attribution-routing-"))
        (root / "project.yaml").write_text("project_id: routing-test\n", encoding="utf-8")
        ext = root / "external_executor"
        ext.mkdir()
        contract = {
            "target_module_ids": ["M1"],
            "reference_variant_id": "full",
            "variant_contracts": [
                {"variant_id": "full", "reference_variant_id": "full", "module_states": {"M1": True}},
                {"variant_id": "no-m1", "reference_variant_id": "full", "module_states": {"M1": False}},
            ],
        }
        plan = {
            "schema_version": "external_executor_iteration_plan.v1",
            "iteration_id": "ITER-1",
            "iteration_number": 1,
            "status": "active",
            "runs_to_execute": [],
        }
        runs = [{
            "run_id": "RUN-MAIN",
            "experiment_id": "EXP-MAIN",
            "iteration_id": "ITER-1",
            "run_type": "formal",
            "method_role": "ours",
            "run_status": "completed",
        }, ablation_run("full", True)]
        if complete_ablation:
            runs.append(ablation_run("no-m1", False))
        result = {
            "schema_version": "external_executor_result.v1",
            "executor_status": "running",
            "experiment_plan": {"experiments": [{
                "experiment_id": "EXP-ABL",
                "run_type": "ablation",
                "required": True,
                "seeds": [1],
                "repeats": 1,
                "attribution_contract": contract,
            }]},
            "iteration_plans": {"status": "complete", "items": [plan], "active_iteration_id": "ITER-1"},
            "current_iteration_plan": plan,
            "experiment_runs": {"status": "complete", "items": runs},
            "result_diagnoses": {"status": "complete", "items": []},
            "iteration_decisions": {"status": "not_started", "items": []},
        }
        diagnosis = {
            "diagnosis_id": "DIAG-1",
            "iteration_id": "ITER-1",
            "baseline_performance": {"all_required_baselines_beaten": True},
            "method_change_assessment": {"status": "complete", "change_required": False, "rationale": "target reached"},
        }
        status = {
            "schema_version": "external_executor_status.v1",
            "executor_status": "running",
            "iteration_id": "ITER-1",
            "active_blockers": [],
            "budget": {"remaining": {"runs": 10, "wall_clock_seconds": 1000, "gpu_hours": 1, "cost": 1}},
        }
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        (ext / "result_diagnosis_report.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")
        return root

    def test_complete_pair_routes_to_module_attribution(self) -> None:
        root = self.make_workspace(complete_ablation=True)
        proc = run("decide_iteration.py", "--workspace", str(root))
        output = json.loads(proc.stdout)
        self.assertEqual(output["decision"]["next_action"], "module-attribution")
        self.assertEqual(output["decision"]["ablation_completeness_issues"], [])

    def test_incomplete_pair_returns_to_experiment_run(self) -> None:
        root = self.make_workspace(complete_ablation=False)
        proc = run("decide_iteration.py", "--workspace", str(root))
        output = json.loads(proc.stdout)
        self.assertEqual(output["decision"]["next_action"], "experiment-run")
        self.assertTrue(output["decision"]["ablation_completeness_issues"])

    def test_partial_attribution_is_not_treated_as_complete(self) -> None:
        root = self.make_workspace(complete_ablation=True)
        ext = root / "external_executor"
        result = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        diagnosis = {
            "diagnosis_id": "DIAG-1",
            "iteration_id": "ITER-1",
            "evidence_snapshot": {
                "included_run_ids": [run["run_id"] for run in result["experiment_runs"]["items"]],
                "excluded_run_ids": [],
            },
        }
        result["result_diagnoses"] = {"items": [diagnosis]}
        result["iteration_decisions"] = {"items": [{"iteration_id": "ITER-1", "diagnosis_id": "DIAG-1"}]}
        result["module_attributions"] = {"items": [{
            "attribution_id": "ATTR-1",
            "iteration_id": "ITER-1",
            "attribution_gate": {"status": "partial", "next_action": "add_controlled_evidence"},
        }]}
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        status = json.loads((ext / "executor_status.json").read_text(encoding="utf-8"))
        status["next_action"] = "module-attribution"
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")
        proc = run("route_next_skill.py", "--workspace", str(root))
        self.assertEqual(json.loads(proc.stdout)["action"], "experiment-design")

    def test_undiagnosed_terminal_run_overrides_stale_run_action(self) -> None:
        root = self.make_workspace(complete_ablation=False)
        ext = root / "external_executor"
        status = json.loads((ext / "executor_status.json").read_text(encoding="utf-8"))
        status["next_action"] = "experiment-run"
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")

        proc = run("route_next_skill.py", "--workspace", str(root))
        output = json.loads(proc.stdout)
        self.assertEqual(output["action"], "result-diagnosis")
        self.assertIn("RUN-MAIN", output["run_ids"])

        result = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        result["result_diagnoses"] = {"items": [{
            "diagnosis_id": "DIAG-COVERED",
            "iteration_id": "ITER-1",
            "evidence_snapshot": {
                "included_run_ids": [run["run_id"] for run in result["experiment_runs"]["items"]],
                "excluded_run_ids": [],
            },
        }]}
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        proc = run("route_next_skill.py", "--workspace", str(root))
        self.assertEqual(json.loads(proc.stdout)["action"], "root-iteration-decision")

    def test_terminal_packaged_state_routes_through_writer_handoff_then_launches_t8_once(self) -> None:
        root = self.make_workspace(complete_ablation=True)
        ext = root / "external_executor"
        result = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        result["executor_status"] = "completed"
        result["evidence_packaging"] = {"status": "ready"}
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        status = json.loads((ext / "executor_status.json").read_text(encoding="utf-8"))
        status["executor_status"] = "completed"
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")

        proc = run("route_next_skill.py", "--workspace", str(root))
        self.assertEqual(json.loads(proc.stdout)["action"], "writer-handoff")

        (ext / "executor_research_report.md").write_text("# Executor Research Report\n", encoding="utf-8")
        (ext / "report/phase_F").mkdir(parents=True, exist_ok=True)
        (ext / "report/phase_F/writer_handoff_validation.json").write_text(
            json.dumps({"status": "ready"}), encoding="utf-8"
        )
        proc = run("route_next_skill.py", "--workspace", str(root))
        route = json.loads(proc.stdout)
        self.assertEqual(route["action"], "launch-t8")
        self.assertEqual(route["command"][3:5], ["run-task", "T8"])

        (root / "drafts").mkdir()
        (root / "drafts/t5_t8_handoff.json").write_text("{}\n", encoding="utf-8")
        (root / "state.yaml").write_text(
            "project_id: test\ncurrent_task: T8-STYLE-GATE\nstatus: PAUSED\n"
            "pending_gate: null\nhistory: []\niteration_count: {}\niteration_history: {}\n"
            "budget_cumulative: {}\ntask_context:\n  t5_t8_handoff_receipt: drafts/t5_t8_handoff.json\n",
            encoding="utf-8",
        )
        proc = run("route_next_skill.py", "--workspace", str(root))
        self.assertEqual(json.loads(proc.stdout)["action"], "stop")

        status["executor_status"] = "blocked"
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")
        result["executor_status"] = "blocked"
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        proc = run("route_next_skill.py", "--workspace", str(root))
        route = json.loads(proc.stdout)
        self.assertEqual(route["action"], "human-review")
        self.assertTrue(route["requires_human"])
        self.assertIn("cannot launch T8", route["reason"])


if __name__ == "__main__":
    unittest.main()
