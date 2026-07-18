from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from researchos.tools.external_experiment import _allowed_path_rules_for_external_executor


ROOT_SKILL = Path(__file__).resolve().parents[1]
ROOT_SCRIPTS = ROOT_SKILL / "scripts"
SKILLS_ROOT = ROOT_SKILL.parent


def run_script(skill: str, script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    path = SKILLS_ROOT / skill / "scripts" / script
    completed = subprocess.run(
        [sys.executable, str(path), *args],
        text=True,
        capture_output=True,
        timeout=60,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"{skill}/{script} failed ({completed.returncode})\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FullExternalExecutionFlowTests(unittest.TestCase):
    def make_workspace(self) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix="researchos-full-external-flow-"))
        (workspace / "project.yaml").write_text("project_id: full-flow\n", encoding="utf-8")
        ext = workspace / "external_executor"
        ext.mkdir()
        (ext / "AGENTS.md").write_text("Use the project skill suite.\n", encoding="utf-8")
        (ext / "allowed_paths.txt").write_text(
            "\n".join(_allowed_path_rules_for_external_executor()) + "\n", encoding="utf-8"
        )
        (ext / "handoff_pack.json").write_text(
            json.dumps({"schema_version": "external_executor_handoff.v1"}), encoding="utf-8"
        )
        (ext / "expected_outputs_schema.json").write_text(
            json.dumps({"required": ["schema_version", "executor_status"]}), encoding="utf-8"
        )
        run_script("research-execution", "initialize_executor.py", "--workspace", str(workspace))
        return workspace

    def load_core(self, workspace: Path) -> tuple[dict, dict]:
        ext = workspace / "external_executor"
        return (
            json.loads((ext / "result_pack.json").read_text(encoding="utf-8")),
            json.loads((ext / "executor_status.json").read_text(encoding="utf-8")),
        )

    def save_core(self, workspace: Path, result: dict, status: dict) -> None:
        ext = workspace / "external_executor"
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        (ext / "executor_status.json").write_text(json.dumps(status), encoding="utf-8")

    def route(self, workspace: Path) -> dict:
        completed = run_script("research-execution", "route_next_skill.py", "--workspace", str(workspace))
        return json.loads(completed.stdout)

    def test_root_routes_every_child_without_stale_action_conflicts(self) -> None:
        workspace = self.make_workspace()
        result, status = self.load_core(workspace)
        self.assertEqual(self.route(workspace)["action"], "context-alignment")

        status["next_action"] = None
        result["context_alignment"] = {"status": "complete"}
        self.save_core(workspace, result, status)
        self.assertEqual(self.route(workspace)["action"], "resource-and-baseline-preparation")

        result["resource_readiness"] = {"status": "ready"}
        self.save_core(workspace, result, status)
        self.assertEqual(self.route(workspace)["action"], "experiment-design")

        result["experiment_plan"] = {"status": "complete", "experiments": []}
        for step, expected in (
            ("D1", "baseline-reproduction"),
            ("D2R", "method-refinement"),
            ("D2I", "implementation"),
            ("D3", "code-and-protocol-review"),
            ("D4", "experiment-run"),
        ):
            status["current_step"] = step
            self.save_core(workspace, result, status)
            self.assertEqual(self.route(workspace)["action"], expected)

        status.update({"iteration_id": "ITER-01", "next_action": "experiment-run"})
        result["experiment_runs"] = {"status": "partial", "items": [{
            "run_id": "RUN-FAILED-01",
            "iteration_id": "ITER-01",
            "method_role": "ours",
            "run_type": "formal",
            "run_status": "failed",
        }]}
        self.save_core(workspace, result, status)
        route = self.route(workspace)
        self.assertEqual(route["action"], "result-diagnosis")
        self.assertEqual(route["run_ids"], ["RUN-FAILED-01"])

        diagnosis = {
            "diagnosis_id": "DIAG-01",
            "iteration_id": "ITER-01",
            "evidence_snapshot": {"included_run_ids": ["RUN-FAILED-01"], "excluded_run_ids": []},
        }
        result["result_diagnoses"] = {"status": "complete", "items": [diagnosis]}
        self.save_core(workspace, result, status)
        self.assertEqual(self.route(workspace)["action"], "root-iteration-decision")

        result["iteration_decisions"] = {
            "status": "complete",
            "items": [{"decision_id": "DEC-01", "iteration_id": "ITER-01", "diagnosis_id": "DIAG-01"}],
        }
        status["next_action"] = "module-attribution"
        self.save_core(workspace, result, status)
        self.assertEqual(self.route(workspace)["action"], "module-attribution")

        result["module_attributions"] = {"status": "complete", "items": [{
            "attribution_id": "ATTR-01",
            "iteration_id": "ITER-01",
            "attribution_gate": {"status": "ready_for_iteration_decision", "next_action": "stop_and_report"},
        }]}
        self.save_core(workspace, result, status)
        self.assertEqual(self.route(workspace)["action"], "evidence-packaging")

        result["evidence_packaging"] = {"status": "ready"}
        result["executor_status"] = "completed"
        status["executor_status"] = "completed"
        self.save_core(workspace, result, status)
        self.assertEqual(self.route(workspace)["action"], "writer-handoff")

        ext = workspace / "external_executor"
        (ext / "executor_research_report.md").write_text("# Executor Research Report\n", encoding="utf-8")
        (ext / "report/phase_F").mkdir(parents=True, exist_ok=True)
        (ext / "report/phase_F/writer_handoff_validation.json").write_text(
            json.dumps({"status": "ready"}), encoding="utf-8"
        )
        route = self.route(workspace)
        self.assertEqual(route["action"], "launch-t8")
        self.assertEqual(route["primary_input"], "external_executor/executor_research_report.md")
        self.assertEqual(route["command"][3:5], ["run-task", "T8"])

        (workspace / "drafts").mkdir()
        (workspace / "drafts/t5_t8_handoff.json").write_text("{}\n", encoding="utf-8")
        (workspace / "state.yaml").write_text(
            "project_id: full-flow\ncurrent_task: T8-STYLE-GATE\nstatus: PAUSED\n"
            "pending_gate: null\nhistory: []\niteration_count: {}\niteration_history: {}\n"
            "budget_cumulative: {}\ntask_context:\n  t5_t8_handoff_receipt: drafts/t5_t8_handoff.json\n",
            encoding="utf-8",
        )
        self.assertEqual(self.route(workspace)["action"], "stop")

    def test_method_versions_and_raw_outputs_follow_cross_skill_contracts(self) -> None:
        workspace = self.make_workspace()
        ext = workspace / "external_executor"
        resources = workspace / "resources"
        resources.mkdir()
        base = resources / "method-template"
        base.mkdir()
        (base / "model.py").write_text("VERSION = 1\n", encoding="utf-8")

        first_root = ext / "expr/implementation/ITER-01/IMPL-01"
        first_worktree = first_root / "worktree"
        first_worktree.mkdir(parents=True)
        (first_worktree / "model.py").write_text("VERSION = 1\nTUNING = 'first'\n", encoding="utf-8")

        result, status = self.load_core(workspace)
        plan = {"iteration_id": "ITER-01", "iteration_number": 1, "status": "active"}
        result.update({
            "current_iteration_plan": plan,
            "iteration_plans": {"status": "complete", "items": [plan], "active_iteration_id": "ITER-01"},
            "implementations": {"status": "complete", "active_implementation_id": "IMPL-01", "items": [{
                "implementation_id": "IMPL-01",
                "iteration_id": "ITER-01",
                "implementation_root": first_root.relative_to(workspace).as_posix(),
                "final_worktree_fingerprint": "fp-1",
            }]},
            "experiment_runs": {"status": "complete", "items": [{
                "run_id": "RUN-01", "experiment_id": "EXP-MAIN", "iteration_id": "ITER-01",
                "method_role": "ours", "run_type": "formal", "run_status": "completed",
            }]},
        })
        diagnosis = {
            "diagnosis_id": "DIAG-01",
            "iteration_id": "ITER-01",
            "baseline_performance": {"all_required_baselines_beaten": False},
            "method_change_assessment": {
                "status": "complete",
                "change_required": True,
                "change_kind": "implementation_debug",
                "rationale": "The first implementation failed the reviewed acceptance behavior.",
                "failure_or_underperformance_causes": ["incorrect numerical branch"],
                "proposed_changes": [{
                    "summary": "Repair the numerical branch",
                    "change_type": "bug_fix",
                    "target_paths": ["model.py"],
                }],
                "must_preserve": ["core mechanism"],
                "prior_iteration_lessons": ["Keep the stable data path."],
                "evidence_refs": ["RUN-01"],
            },
        }
        (ext / "result_diagnosis_report.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        result["result_diagnoses"] = {"status": "complete", "items": [diagnosis]}
        status.update({
            "iteration_id": "ITER-01",
            "active_blockers": [],
            "budget": {"remaining": {"runs": 20, "wall_clock_seconds": 1000, "gpu_hours": 0, "cost": 0}},
        })
        self.save_core(workspace, result, status)

        decision = run_script(
            "research-execution", "decide_iteration.py", "--workspace", str(workspace)
        )
        decision_data = json.loads(decision.stdout)
        next_plan = decision_data["next_iteration_plan"]
        self.assertEqual(next_plan["iteration_id"], "ITER-02")
        self.assertTrue(next_plan["copy_previous_method"])
        self.assertEqual(next_plan["base_source"], first_worktree.relative_to(workspace).as_posix())
        self.assertEqual(next_plan["iteration_history_summary"][0]["diagnosis_id"], "DIAG-01")

        second_root = ext / "expr/implementation/ITER-02/IMPL-02"
        contract = {
            "status": "ready",
            "implementation_id": "IMPL-02",
            "iteration_id": "ITER-02",
            "input_fingerprint": "contract-fp-2",
            "base_source": {"path": next_plan["base_source"]},
            "implementation_root": second_root.relative_to(workspace).as_posix(),
        }
        contract_path = ext / "report/phase_D/implementation_change_contract.json"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        run_script(
            "implementation", "prepare_worktree.py", "--workspace", str(workspace),
            "--contract", contract_path.relative_to(workspace).as_posix(),
        )
        second_worktree = second_root / "worktree"
        self.assertEqual(
            (second_worktree / "model.py").read_text(encoding="utf-8"),
            (first_worktree / "model.py").read_text(encoding="utf-8"),
        )
        (second_worktree / "model.py").write_text("VERSION = 2\nTUNING = 'repaired'\n", encoding="utf-8")
        self.assertIn("VERSION = 1", (first_worktree / "model.py").read_text(encoding="utf-8"))

        runner = second_worktree / "run.py"
        runner.write_text(
            "import json, os, pathlib, sys\n"
            "raw = pathlib.Path(os.environ['RESEARCHOS_RAW_RESULTS_DIR'])\n"
            "pathlib.Path(sys.argv[1]).write_text(json.dumps({'accuracy': {'value': 0.91}}))\n"
            "(raw / 'predictions.csv').write_text('row,prediction\\n1,1\\n')\n"
            "(raw / 'model_output.json').write_text(json.dumps({'checkpoint': 'model-v2'}))\n"
            "(raw / 'training.log').write_text('epoch=1 loss=0.1\\n')\n"
            "print('formal run completed')\n",
            encoding="utf-8",
        )
        config = second_worktree / "config.json"
        config.write_text("{}\n", encoding="utf-8")
        evaluator = second_worktree / "evaluator.py"
        evaluator.write_text("def score(value): return value\n", encoding="utf-8")
        isolation = second_worktree / "isolation.json"
        isolation.write_text("{}\n", encoding="utf-8")
        dataset = resources / "data.csv"
        dataset.write_text("x,y\n1,1\n", encoding="utf-8")
        experiment_plan = {
            "experiments": [{
                "experiment_id": "EXP-MAIN", "run_type": "formal", "analysis_role": "confirmatory",
                "protocol_fingerprint": "PROTO-1", "dataset": "Data", "dataset_version": "v1",
                "split": "test", "seed": 1, "repeat_index": 0,
            }]
        }
        experiment_plan_path = ext / "experiment_plan.json"
        experiment_plan_path.write_text(json.dumps(experiment_plan), encoding="utf-8")
        iteration_plan_path = ext / "report/phase_D/iteration_plans/ITER-02.json"
        persisted_plan = json.loads(iteration_plan_path.read_text(encoding="utf-8"))
        persisted_plan["runs_to_execute"] = ["RUN-OUR-02"]
        iteration_plan_path.write_text(json.dumps(persisted_plan), encoding="utf-8")
        review = {
            "review_id": "REVIEW-02", "review_status": "pass", "approved_for": "formal",
            "input_fingerprint": "review-fp-2", "review_scope": {"protocol_fingerprint": "PROTO-1"},
        }
        review_path = ext / "reviews/ITER-02/review_report.json"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(json.dumps(review), encoding="utf-8")
        raw = ext / "raw_results/ours/ITER-02/RUN-OUR-02"
        rel = lambda path: path.relative_to(workspace).as_posix()
        request = {
            "schema_version": "external_executor_run_request.v1",
            "run_id": "RUN-OUR-02", "experiment_id": "EXP-MAIN", "iteration_id": "ITER-02",
            "run_type": "formal", "execution_level": "formal", "analysis_role": "confirmatory",
            "method_id": "our-method-v2", "method_role": "ours", "implementation_id": "IMPL-02",
            "command": [sys.executable, str(runner), str(raw / "metrics.json")],
            "cwd": rel(second_worktree), "timeout_seconds": 20,
            "experiment_plan_ref": rel(experiment_plan_path), "iteration_plan_ref": rel(iteration_plan_path),
            "review_ref": rel(review_path), "review_id": "REVIEW-02", "input_fingerprint": "review-fp-2",
            "protocol_fingerprint": "PROTO-1", "config_ref": rel(config),
            "raw_log_path": rel(raw / "run.log"), "metric_output_path": rel(raw / "metrics.json"),
            "run_record_path": rel(raw / "record.json"), "checkpoint_path": rel(raw / "checkpoint.json"),
            "declared_outputs": [rel(raw / name) for name in ("predictions.csv", "model_output.json", "training.log")],
            "dependencies": [
                {"kind": "code", "path": rel(runner), "sha256": sha256(runner)},
                {"kind": "config", "path": rel(config), "sha256": sha256(config)},
                {"kind": "dataset", "path": rel(dataset), "sha256": sha256(dataset)},
                {"kind": "evaluator", "path": rel(evaluator), "sha256": sha256(evaluator)},
            ],
            "dataset": {"id": "Data", "version": "v1", "split": "test"},
            "seed": 1, "repeat_index": 0, "resources": {"gpu_count": 0},
            "budget": {
                "remaining": {"runs": 2, "wall_clock_seconds": 60, "gpu_hours": 0, "cost": 0},
                "estimated": {"runs": 1, "wall_clock_seconds": 20, "gpu_hours": 0, "cost": 0},
            },
            "environment": {"allowed_env": [], "overrides": {}, "network_required": False},
            "isolation": {"filesystem": "enforced", "network": "enforced", "evidence_ref": rel(isolation)},
            "data_kind": "real",
        }
        request_path = ext / "runs/RUN-OUR-02/request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(request), encoding="utf-8")
        run_script(
            "experiment-run", "execute_run.py", "--workspace", str(workspace),
            "--request", rel(request_path),
        )
        for name in ("run.log", "metrics.json", "record.json", "predictions.csv", "model_output.json", "training.log"):
            self.assertTrue((raw / name).is_file(), name)

    def test_tenth_iteration_exits_without_creating_iteration_eleven(self) -> None:
        workspace = self.make_workspace()
        ext = workspace / "external_executor"
        worktree = ext / "expr/implementation/ITER-10/IMPL-10/worktree"
        worktree.mkdir(parents=True)
        (worktree / "model.py").write_text("VERSION = 10\n", encoding="utf-8")
        result, status = self.load_core(workspace)
        plan = {"iteration_id": "ITER-10", "iteration_number": 10, "status": "active"}
        result.update({
            "current_iteration_plan": plan,
            "iteration_plans": {"items": [plan], "active_iteration_id": "ITER-10"},
            "implementations": {"items": [{
                "implementation_id": "IMPL-10", "iteration_id": "ITER-10",
                "implementation_root": worktree.parent.relative_to(workspace).as_posix(),
            }]},
            "experiment_runs": {"items": [{
                "run_id": "RUN-10", "experiment_id": "EXP-MAIN", "iteration_id": "ITER-10",
                "method_role": "ours", "run_type": "formal", "run_status": "completed",
            }]},
        })
        diagnosis = {
            "diagnosis_id": "DIAG-10", "iteration_id": "ITER-10",
            "baseline_performance": {"all_required_baselines_beaten": False},
            "method_change_assessment": {
                "status": "complete", "change_required": True, "change_kind": "method_refinement",
                "rationale": "Still below one required baseline.",
                "proposed_changes": [{"summary": "Another bounded change", "target_paths": ["model.py"]}],
            },
        }
        (ext / "result_diagnosis_report.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        status.update({
            "iteration_id": "ITER-10", "active_blockers": [],
            "budget": {"remaining": {"runs": 2, "wall_clock_seconds": 60, "gpu_hours": 0, "cost": 0}},
        })
        self.save_core(workspace, result, status)
        output = json.loads(run_script(
            "research-execution", "decide_iteration.py", "--workspace", str(workspace)
        ).stdout)
        self.assertEqual(output["decision"]["loop_outcome"], "max_iterations_reached")
        self.assertEqual(output["decision"]["next_action"], "evidence-packaging")
        self.assertIsNone(output["next_iteration_plan"])
        persisted = json.loads((ext / "result_pack.json").read_text(encoding="utf-8"))
        self.assertFalse(any(
            item.get("iteration_id") == "ITER-11"
            for item in persisted["iteration_plans"]["items"]
            if isinstance(item, dict)
        ))


if __name__ == "__main__":
    unittest.main()
