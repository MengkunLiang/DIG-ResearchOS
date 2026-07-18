from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"


def run(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=str(SCRIPTS),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"{script} failed\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def make_workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="method-refinement-test-")) / "workspace"
    ext = root / "external_executor"
    ext.mkdir(parents=True)
    (root / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    (ext / "AGENTS.md").write_text("test controls\n", encoding="utf-8")
    (ext / "allowed_paths.txt").write_text("external_executor/\n", encoding="utf-8")
    (ext / "expected_outputs_schema.json").write_text(
        json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8"
    )

    handoff = {
        "schema_version": "external_executor_handoff.v1",
        "context_reboost": {
            "central_hypothesis": "Semantic gated fusion improves transfer under heterogeneous feature spaces.",
            "contribution_type": "method",
            "claim_boundaries": ["heterogeneous feature transfer under the official benchmark protocol"],
            "required_baselines": [{"name": "Baseline A", "required": True}],
        },
        "method_intent": {
            "status": "draft_intent_only",
            "not_final_method_source": True,
            "contribution_type": "method",
            "central_mechanism_hypothesis": "Semantic gated fusion aligns heterogeneous features before prediction.",
            "must_preserve_components": [
                {
                    "module_id": "M1",
                    "name": "Semantic Encoder",
                    "intended_role": "encode heterogeneous inputs into a shared semantic representation",
                    "expected_input": [{"name": "heterogeneous_features", "type": "tensor", "semantics": "source-specific features"}],
                    "expected_output": [{"name": "semantic_codes", "type": "tensor", "semantics": "shared semantic representation"}],
                    "invariants": ["must preserve sample identity", "must not use test labels"],
                    "mechanism_ref": "semantic alignment",
                    "planned_ablation": "replace semantic codes with a matched-capacity random codebook",
                    "required": True,
                },
                {
                    "module_id": "M2",
                    "name": "Adaptive Gate",
                    "intended_role": "weight semantic evidence before prediction",
                    "expected_input": [{"name": "semantic_codes", "type": "tensor", "semantics": "shared semantic representation"}],
                    "expected_output": [{"name": "gated_representation", "type": "tensor", "semantics": "weighted representation"}],
                    "invariants": ["gate weights are normalized"],
                    "mechanism_ref": "semantic alignment",
                    "planned_ablation": "replace adaptive gate with uniform weights",
                    "required": True,
                },
            ],
            "candidate_modules": [
                {
                    "module_id": "C1",
                    "name": "Consistency Regularizer",
                    "intended_role": "stabilize semantic codes without changing the core mechanism",
                    "expected_input": [{"name": "semantic_codes", "type": "tensor", "semantics": "semantic codes"}],
                    "expected_output": [{"name": "regularization_loss", "type": "scalar", "semantics": "supporting loss"}],
                    "invariants": ["disabled by default in the minimum implementation"],
                    "mechanism_ref": "semantic alignment",
                    "planned_ablation": "set regularizer coefficient to zero",
                }
            ],
            "expected_algorithm_flow": [
                {"step": 1, "description": "Encode source-specific features into semantic codes.", "related_module": "M1", "inputs": ["heterogeneous_features"], "outputs": ["semantic_codes"]},
                {"step": 2, "description": "Apply adaptive gating to semantic codes.", "related_module": "M2", "inputs": ["semantic_codes"], "outputs": ["gated_representation"]},
            ],
            "allowed_refinements": ["choose a concrete encoder architecture", "add non-claim-changing numerical stabilization", "add ablation switches"],
            "forbidden_silent_changes": ["replace_core_mechanism", "change_task_or_benchmark", "change_contribution_type_without_review"],
            "mechanism_to_ablation_plan": [
                {
                    "mechanism": "semantic alignment",
                    "planned_test": "compare full method, random-codebook control, and uniform-gate control",
                    "expected_observation_if_supported": "full method outperforms both controlled variants",
                    "expected_observation_if_not_supported": "controlled variants are indistinguishable from full method",
                    "related_claim": "CLM-main",
                }
            ],
        },
    }
    (ext / "handoff_pack.json").write_text(json.dumps(handoff), encoding="utf-8")

    protocol_fp = "a" * 64
    result = {
        "schema_version": "external_executor_result.v1",
        "context_alignment": {
            "status": "pass",
            "confirmed_execution_scope": {
                "central_hypothesis": handoff["context_reboost"]["central_hypothesis"],
                "contribution_type": "method",
                "core_mechanism": handoff["method_intent"]["central_mechanism_hypothesis"],
                "must_preserve_components": ["M1", "M2"],
                "claim_boundaries": handoff["context_reboost"]["claim_boundaries"],
                "must_not_claim": ["universal transfer improvement"],
                "required_baselines": [{"name": "Baseline A", "required": True}],
                "executor_capabilities": {"framework": "pytorch", "network_allowed": False},
            },
        },
        "resource_readiness": {"status": "ready", "minimum_loop_feasible": True},
        "baseline_candidates": {
            "status": "complete",
            "items": [
                {
                    "candidate_id": "BASE-A",
                    "baseline_id": "BASE-A",
                    "name": "Baseline A",
                    "path": "resources/baseline-a",
                    "approved_for": ["baseline_reproduction", "formal_comparison"],
                }
            ],
        },
        "experiment_plan": {
            "schema_version": "experiment_plan.v1",
            "status": "complete",
            "plan_version": 1,
            "protocol_fingerprint": {"fingerprint": protocol_fp},
            "protocol_snapshot": {
                "protocol": {
                    "benchmark": {"name": "Bench", "task": "classification"},
                    "dataset": {"name": "Data", "version": "1.0", "split": "official", "preprocessing": "official preprocessing"},
                    "metrics": {"primary": {"name": "accuracy", "direction": "higher_is_better", "aggregation": "mean_over_seeds"}},
                    "randomness": {"seeds": [1, 2, 3], "repeats": 1},
                    "evaluation": {"entry_point": "external_executor/expr/evaluation/evaluate.py"},
                    "tuning": {"fairness_rule": "same tuning opportunity"},
                }
            },
            "experiments": [
                {"experiment_id": "EXP-main", "experiment_kind": "main_comparison", "claim_ids": ["CLM-main"], "run_type": "formal"},
                {"experiment_id": "EXP-ablation", "experiment_kind": "mechanism_ablation", "claim_ids": ["CLM-main"], "run_type": "ablation", "mechanism_ref": "semantic alignment"},
            ],
        },
        "current_iteration_plan": {
            "iteration_id": "ITER-001",
            "status": "active",
            "trigger": "initial method engineering",
            "approved_changes": ["translate approved method intent into an implementation specification"],
            "decision_ref": "DEC-001",
            "evidence_refs": ["external_executor/result_pack.json#experiment_plan"],
        },
        "iteration_decisions": [
            {"decision_id": "DEC-001", "decision": "continue_same_idea", "status": "approved", "evidence_refs": []}
        ],
        "sentinel_sibling_section": {"preserve": True},
    }
    (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
    return root


def build_base(ws: Path) -> None:
    run("preflight_method_refinement.py", "--workspace", str(ws))
    run("normalize_method_intent.py", "--workspace", str(ws))
    run("build_method_implementation_spec.py", "--workspace", str(ws))
    run("fingerprint_method_spec.py", "--workspace", str(ws), "--write-back")


def finish_review(ws: Path, *, expect_pass: bool) -> subprocess.CompletedProcess[str]:
    run("compare_method_specs.py", "--workspace", str(ws))
    scope_proc = run("assess_scope_change.py", "--workspace", str(ws), check=expect_pass)
    validation_proc = run("validate_method_implementation_spec.py", "--workspace", str(ws), check=expect_pass)
    review_proc = run("review_method_refinement.py", "--workspace", str(ws), check=expect_pass)
    run("render_implementation_brief.py", "--workspace", str(ws))
    run("assemble_method_refinement_report.py", "--workspace", str(ws))
    run("validate_method_refinement_report.py", "--workspace", str(ws))
    return review_proc


class MethodRefinementScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_ws = make_workspace()
        build_base(cls.base_ws)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base_ws, ignore_errors=True)

    def clone_workspace(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="method-refinement-clone-")) / "workspace"
        shutil.copytree(self.base_ws, target)
        return target

    def test_initial_refinement_passes_and_applies_narrowly(self) -> None:
        ws = self.clone_workspace()
        finish_review(ws, expect_pass=True)
        review = json.loads((ws / "external_executor/report/phase_D/method_refinement_review.json").read_text())
        self.assertEqual(review["review_status"], "pass", review)
        self.assertEqual(review["approved_for"], "implementation")
        report = json.loads((ws / "external_executor/report/phase_D/method_refinement_report.json").read_text())
        self.assertEqual(report["refinement_status"], "ready")
        run("apply_method_refinement_report.py", "--workspace", str(ws))
        result = json.loads((ws / "external_executor/result_pack.json").read_text())
        self.assertEqual(len(result["method_refinements"]), 1)
        self.assertEqual(result["method_refinements"][0]["approved_for"], "implementation")
        self.assertEqual(result["sentinel_sibling_section"], {"preserve": True})
        snapshot = ws / result["method_refinements"][0]["snapshot_ref"]
        self.assertTrue(snapshot.exists())

    def test_major_core_mechanism_drift_is_blocked(self) -> None:
        ws = self.clone_workspace()
        spec_path = ws / "external_executor/method_implementation_spec.json"
        spec = json.loads(spec_path.read_text())
        spec["research_contract"]["core_mechanism"] = "A generic larger encoder replaces semantic gated fusion."
        spec["scope_boundary"]["core_mechanism"] = spec["research_contract"]["core_mechanism"]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run("fingerprint_method_spec.py", "--workspace", str(ws), "--write-back")
        finish_review(ws, expect_pass=False)
        scope = json.loads((ws / "external_executor/report/phase_D/method_scope_assessment.json").read_text())
        review = json.loads((ws / "external_executor/report/phase_D/method_refinement_review.json").read_text())
        self.assertEqual(scope["drift_level"], "major")
        self.assertTrue(scope["requires_human_review"])
        self.assertEqual(review["review_status"], "blocked")
        self.assertEqual(review["approved_for"], "none")
        request = json.loads((ws / "external_executor/report/phase_D/scope_change_request.json").read_text())
        self.assertTrue(request["implementation_must_pause"])
        run("apply_method_refinement_report.py", "--workspace", str(ws))
        result = json.loads((ws / "external_executor/result_pack.json").read_text())
        self.assertEqual(result["scope_change_requests"][0]["status"], "pending_human_review")
        self.assertEqual(result["sentinel_sibling_section"], {"preserve": True})

    def test_minor_engineering_refinement_creates_version_two(self) -> None:
        ws = self.clone_workspace()
        first = json.loads((ws / "external_executor/report/phase_D/method_spec_fingerprint.json").read_text())
        previous = first["snapshot_ref"]
        run(
            "build_method_implementation_spec.py",
            "--workspace", str(ws),
            "--previous", previous,
        )
        spec_path = ws / "external_executor/method_implementation_spec.json"
        spec = json.loads(spec_path.read_text())
        spec["non_contribution_engineering"].append({
            "engineering_id": "ENG-AMP",
            "description": "Use mixed precision for runtime efficiency.",
            "claim_role": "none",
            "fairness_rule": "record and apply comparable precision where baseline supports it",
        })
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run("fingerprint_method_spec.py", "--workspace", str(ws), "--write-back")
        run(
            "compare_method_specs.py",
            "--workspace", str(ws),
            "--previous", previous,
        )
        run("assess_scope_change.py", "--workspace", str(ws))
        run("validate_method_implementation_spec.py", "--workspace", str(ws))
        run("review_method_refinement.py", "--workspace", str(ws))
        delta = json.loads((ws / "external_executor/report/phase_D/method_delta.json").read_text())
        current = json.loads(spec_path.read_text())
        review = json.loads((ws / "external_executor/report/phase_D/method_refinement_review.json").read_text())
        self.assertEqual(current["spec_version"], 2)
        self.assertEqual(delta["delta_level"], "minor")
        self.assertFalse(delta["requires_human_review"])
        self.assertEqual(review["review_status"], "pass", review)
        second = json.loads((ws / "external_executor/report/phase_D/method_spec_fingerprint.json").read_text())
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(first["snapshot_ref"], second["snapshot_ref"])

    def test_protocol_fingerprint_mismatch_prevents_approval(self) -> None:
        ws = self.clone_workspace()
        spec_path = ws / "external_executor/method_implementation_spec.json"
        spec = json.loads(spec_path.read_text())
        spec["protocol_fingerprint"] = "b" * 64
        spec["data_and_protocol_interfaces"]["protocol_fingerprint"] = "b" * 64
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run("fingerprint_method_spec.py", "--workspace", str(ws), "--write-back")
        run("compare_method_specs.py", "--workspace", str(ws))
        run("assess_scope_change.py", "--workspace", str(ws))
        run("validate_method_implementation_spec.py", "--workspace", str(ws), check=False)
        run("review_method_refinement.py", "--workspace", str(ws), check=False)
        review = json.loads((ws / "external_executor/report/phase_D/method_refinement_review.json").read_text())
        self.assertEqual(review["review_status"], "blocked")
        self.assertIn("protocol_fingerprint_mismatch", review["blocking_issues"])


if __name__ == "__main__":
    unittest.main()
