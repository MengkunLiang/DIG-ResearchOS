from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


def run(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], text=True, capture_output=True, check=check, timeout=30)


def make_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="module-attribution-test-"))
    (ws / "project.yaml").write_text("project_id: attribution-test\n", encoding="utf-8")
    ext = ws / "external_executor"
    ext.mkdir()
    (ext / "AGENTS.md").write_text("controls\n", encoding="utf-8")
    (ext / "allowed_paths.txt").write_text("external_executor/\n", encoding="utf-8")
    handoff = {
        "schema_version": "external_executor_handoff.v1",
        "method_intent": {
            "candidate_modules": [
                {"module_id": "M1", "name": "alignment", "intended_role": "align representations", "mechanism_id": "MECH-align", "planned_ablation": "use_m1"},
                {"module_id": "M2", "name": "regularizer", "intended_role": "stabilize training", "mechanism_id": "MECH-stable", "planned_ablation": "use_m2"},
            ],
            "mechanism_to_ablation_plan": [
                {"mechanism_id": "MECH-align", "mechanism": "alignment improves transfer", "related_module": "M1", "expected_observation_if_supported": "M1 ablation hurts accuracy"},
                {"mechanism_id": "MECH-stable", "mechanism": "regularizer improves stability", "related_module": "M2", "expected_observation_if_supported": "M2 ablation increases variance"},
            ],
        },
    }
    (ext / "handoff_pack.json").write_text(json.dumps(handoff), encoding="utf-8")
    runs = []
    values = {
        "full": [0.90, 0.92, 0.91],
        "no_m1": [0.84, 0.85, 0.83],
        "no_m2": [0.89, 0.90, 0.90],
        "none": [0.80, 0.81, 0.79],
    }
    states = {
        "full": {"M1": True, "M2": True},
        "no_m1": {"M1": False, "M2": True},
        "no_m2": {"M1": True, "M2": False},
        "none": {"M1": False, "M2": False},
    }
    for seed in range(3):
        for variant, series in values.items():
            runs.append({
                "run_id": f"run-{variant}-{seed}", "iteration_id": "iter-1", "experiment_id": "EXP-abl", "claim_ids": ["C1"],
                "method_id": "ours", "method_role": "ours", "implementation_id": "IMPL-1", "variant_id": variant,
                "reference_variant_id": "full", "pair_id": f"pair-{seed}", "target_module_ids": ["M1", "M2"],
                "run_type": "ablation", "analysis_role": "diagnostic", "run_status": "completed",
                "module_states": states[variant], "intervention": {"type": "module_ablation" if variant != "full" else "none", "controlled": True, "module_ids": ["M1", "M2"]},
                "setting": "default", "subset": "all", "dataset": {"id": "Data", "version": "v1", "split": "test"},
                "preprocessing_fingerprint": "prep-1", "protocol_fingerprint": "proto-1", "fairness_fingerprint": "fair-1",
                "seed": seed, "repeat_index": seed, "metric_directions": {"accuracy": "higher_is_better"},
                "metrics": {"accuracy": {"value": series[seed], "aggregation": "mean"}},
                "artifact_refs": [{"path": f"external_executor/logs/{variant}-{seed}.log"}],
            })
    diagnosis = {
        "schema_version": "result_diagnosis_report.v1", "diagnosis_id": "DIAG-1", "iteration_id": "iter-1", "input_fingerprint": "diagfp",
        "diagnosis_gate": {"status": "ready_for_attribution"}, "anomalies": {"status": "complete", "items": []},
        "confound_assessments": {"status": "complete", "items": []}, "evidence_requests": {"status": "complete", "items": []},
    }
    result = {
        "schema_version": "external_executor_result.v1",
        "context_alignment": {"status": "pass"},
        "claim_evidence_matrix": {"status": "complete", "items": [{"claim_id": "C1", "experiment_id": "EXP-abl"}]},
        "experiment_plan": {"status": "complete", "protocol_fingerprint": "proto-1", "experiments": [{"experiment_id": "EXP-abl", "metrics": ["accuracy"], "metric_directions": {"accuracy": "higher_is_better"}}]},
        "implementations": {"status": "complete", "active_implementation_id": "IMPL-1", "items": [
            {"implementation_id": "IMPL-old", "iteration_id": "iter-0", "module_mappings": {"items": [
                {"module_id": "M1", "owner_method_id": "ours", "name": "stale alignment", "code_paths": ["src/old.py"], "config_keys": ["old_m1"], "ablation_switch": "old_m1", "mechanism_ids": ["MECH-align"]},
            ]}},
            {"implementation_id": "IMPL-1", "iteration_id": "iter-1", "module_mappings": {"items": [
            {"module_id": "M1", "owner_method_id": "ours", "name": "alignment", "code_paths": ["src/m1.py"], "config_keys": ["use_m1"], "ablation_switch": "use_m1", "mechanism_ids": ["MECH-align"]},
            {"module_id": "M2", "owner_method_id": "ours", "name": "regularizer", "code_paths": ["src/m2.py"], "config_keys": ["use_m2"], "ablation_switch": "use_m2", "mechanism_ids": ["MECH-stable"]},
        ]}}]},
        "baseline_reproduction": {"status": "complete", "items": []},
        "iteration_plans": {"status": "complete", "items": [{"iteration_id": "iter-1", "status": "active"}]},
        "experiment_runs": {"status": "complete", "items": runs},
        "result_diagnoses": {"status": "complete", "items": [diagnosis], "current_by_iteration": {"iter-1": "DIAG-1"}},
        "unrelated": {"keep": True},
    }
    (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
    return ws


def build_pipeline(ws: Path) -> None:
    ext = ws / "external_executor"
    run("preflight_attribution.py", "--workspace", str(ws))
    run("build_attribution_snapshot.py", "--workspace", str(ws), "--iteration-id", "iter-1")
    work = ext / "report/module_attribution/iter-1"
    work.mkdir(parents=True, exist_ok=True)
    registry = work / "module_registry.json"
    obs = work / "intervention_observations.json"
    effects = work / "ablation_effects.json"
    interactions = work / "interaction_and_confounds.json"
    run("inventory_modules.py", "--snapshot", str(ext / "report/module_attribution_snapshot.json"), "--output", str(registry))
    run("normalize_attribution_evidence.py", "--snapshot", str(ext / "report/module_attribution_snapshot.json"), "--module-registry", str(registry), "--output", str(obs))
    run("compute_ablation_effects.py", "--observations", str(obs), "--output", str(effects))
    run("analyze_interactions.py", "--observations", str(obs), "--ablation-effects", str(effects), "--output", str(interactions))
    run("build_attribution_facts.py", "--snapshot", str(ext / "report/module_attribution_snapshot.json"), "--module-registry", str(registry), "--ablation-effects", str(effects), "--interaction-analysis", str(interactions), "--output", str(ext / "report/module_attribution_facts.json"))
    run("initialize_attribution_report.py", "--workspace", str(ws))


class ModuleAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = make_workspace()
        build_pipeline(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base, ignore_errors=True)

    def clone(self) -> Path:
        parent = Path(tempfile.mkdtemp(prefix="module-attribution-clone-"))
        target = parent / "workspace"
        shutil.copytree(self.base, target)
        return target

    def test_effects_and_interaction(self) -> None:
        ext = self.base / "external_executor"
        work = ext / "report/module_attribution/iter-1"
        effects = json.loads((work / "ablation_effects.json").read_text())
        by_module = {x["module_id"]: x for x in effects["items"]}
        self.assertGreater(by_module["M1"]["mean_effect"], by_module["M2"]["mean_effect"])
        self.assertEqual(by_module["M1"]["paired_n"], 3)
        interaction = json.loads((work / "interaction_and_confounds.json").read_text())
        self.assertEqual(len(interaction["interaction_effects"]["items"]), 1)
        facts = json.loads((ext / "report/module_attribution_facts.json").read_text())
        facts_by_module = {x["module_id"]: x for x in facts["module_facts"]["items"]}
        self.assertEqual(facts_by_module["M1"]["empirical_status"], "beneficial")
        snapshot = json.loads((ext / "report/module_attribution_snapshot.json").read_text())
        self.assertEqual(snapshot["implementation_id"], "IMPL-1")
        self.assertEqual(snapshot["runs"][0]["dataset"], "Data")
        self.assertEqual(snapshot["runs"][0]["dataset_version"], "v1")
        self.assertNotIn("src/old.py", {path for module in snapshot["modules"] for path in module.get("code_paths", [])})
        self.assertTrue((ext / "report/module_attribution_preflight.json").is_file())
        self.assertTrue((ext / "report/module_attribution_facts.json").is_file())
        self.assertTrue((ext / "module_attribution_report.json").is_file())
        self.assertTrue((ext / "result_pack.json").is_file())
        self.assertFalse((ext / "module_attribution_preflight.json").exists())
        self.assertFalse((ext / "module_attribution_snapshot.json").exists())
        self.assertFalse((ext / "module_attribution_facts.json").exists())
        self.assertFalse((ext / "module_attribution").exists())

    def test_report_gate_validation_and_apply(self) -> None:
        ws = self.clone()
        ext = ws / "external_executor"
        report_path = ext / "module_attribution_report.json"
        report = json.loads(report_path.read_text())
        facts = json.loads((ext / "report/module_attribution_facts.json").read_text())
        module_items = []
        for fact in facts["module_facts"]["items"]:
            refs = fact["effect_ids"] or fact["evidence_refs"]
            module_items.append({
                "module_attribution_id": "MAT-" + fact["module_id"], "module_id": fact["module_id"], "owner_method_id": fact["owner_method_id"],
                "empirical_status": fact["empirical_status"], "evidence_type": fact["evidence_type"], "tested_settings": fact["tested_settings"],
                "summary": "Bounded paired ablation evidence.", "effect_refs": fact["effect_ids"], "evidence_refs": refs,
                "counterevidence_refs": [], "confound_ids": [], "interaction_ids": [],
                "causal_status": "local_intervention_effect", "confidence": "high", "limitations": ["tested setting only"],
            })
        report["module_attributions"] = {"status": "complete", "items": module_items}
        report["mechanism_attributions"] = {"status": "complete", "items": [
            {"mechanism_attribution_id": "MA-MECH-align", "mechanism_id": "MECH-align", "status": "consistent", "linked_module_ids": ["M1"], "evidence_type": "direct_ablation", "summary": "M1 evidence is consistent with the alignment mechanism but does not isolate all alternatives.", "alternative_explanations": ["capacity"], "evidence_refs": module_items[0]["evidence_refs"], "counterevidence_refs": [], "confidence": "medium", "causal_status": "mechanism_consistent", "required_evidence": ["capacity-matched diagnostic"]},
            {"mechanism_attribution_id": "MA-MECH-stable", "mechanism_id": "MECH-stable", "status": "consistent", "linked_module_ids": ["M2"], "evidence_type": "direct_ablation", "summary": "M2 has a small local effect.", "alternative_explanations": [], "evidence_refs": module_items[1]["evidence_refs"], "counterevidence_refs": [], "confidence": "medium", "causal_status": "mechanism_consistent", "required_evidence": []},
        ]}
        report["baseline_module_attributions"] = {"status": "complete", "items": []}
        report["recommendations"] = {"status": "complete", "items": [
            {"recommendation_id": "REC-M1", "target_type": "module", "target_id": "M1", "action": "keep", "summary": "Retain M1 for the next local iteration.", "conditions": ["same protocol"], "evidence_refs": module_items[0]["evidence_refs"], "counterevidence_refs": [], "confidence": "high", "root_review_required": False},
            {"recommendation_id": "REC-M2", "target_type": "module", "target_id": "M2", "action": "keep", "summary": "Retain pending broader evidence.", "conditions": [], "evidence_refs": module_items[1]["evidence_refs"], "counterevidence_refs": [], "confidence": "medium", "root_review_required": False},
        ]}
        report["unsupported_questions"] = {"status": "complete", "items": []}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        run("compute_attribution_gate.py", "--report", str(report_path), "--write-back")
        updated = json.loads(report_path.read_text())
        self.assertEqual(updated["attribution_gate"]["status"], "ready_for_iteration_decision")
        run("validate_attribution_report.py", "--workspace", str(ws))
        run("apply_attribution_report.py", "--workspace", str(ws))
        result = json.loads((ext / "result_pack.json").read_text())
        self.assertTrue(result["unrelated"]["keep"])
        self.assertEqual(result["module_attributions"]["current_by_iteration"]["iter-1"], updated["attribution_id"])
        shutil.rmtree(ws.parent, ignore_errors=True)

    def test_validator_rejects_causal_upgrade_and_root_authority(self) -> None:
        ws = self.clone()
        ext = ws / "external_executor"
        report_path = ext / "module_attribution_report.json"
        report = json.loads(report_path.read_text())
        report["module_attributions"] = {"status": "complete", "items": [{
            "module_attribution_id": "bad", "module_id": "M1", "owner_method_id": "ours", "empirical_status": "beneficial",
            "evidence_type": "correlational_hint", "tested_settings": [], "summary": "bad", "effect_refs": [],
            "evidence_refs": ["M1"], "counterevidence_refs": [], "confound_ids": [], "interaction_ids": [],
            "causal_status": "local_intervention_effect", "confidence": "high", "limitations": [], "iteration_decision": "continue_same_idea",
        }]}
        report_path.write_text(json.dumps(report), encoding="utf-8")
        proc = run("validate_attribution_report.py", "--workspace", str(ws), check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("correlational_hint", proc.stdout)
        self.assertIn("forbidden authority field", proc.stdout)
        shutil.rmtree(ws.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
