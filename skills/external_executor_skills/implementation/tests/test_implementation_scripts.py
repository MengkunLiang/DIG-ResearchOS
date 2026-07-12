from __future__ import annotations

import json
import os
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


class ImplementationSkillTests(unittest.TestCase):
    def make_workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="implementation-skill-test-"))
        (root / "project.yaml").write_text("project_id: implementation-test\n", encoding="utf-8")
        ext = root / "external_executor"
        ext.mkdir()
        (ext / "AGENTS.md").write_text("Implementation tests.\n", encoding="utf-8")
        (ext / "allowed_paths.txt").write_text("external_executor/\n", encoding="utf-8")
        (ext / "handoff_pack.json").write_text(json.dumps({"schema_version": "external_executor_handoff.v1"}), encoding="utf-8")
        (ext / "expected_outputs_schema.json").write_text(json.dumps({"schema_version": "external_executor_result.v1"}), encoding="utf-8")

        source = ext / "workdir" / "base_source"
        (source / "src").mkdir(parents=True)
        (source / "tests").mkdir()
        (source / "src" / "__init__.py").write_text("", encoding="utf-8")
        (source / "src" / "model.py").write_text(
            "def transform(value: int) -> int:\n    return value\n",
            encoding="utf-8",
        )
        (source / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (source / "tests" / "test_model.py").write_text(
            "import unittest\nfrom src.model import transform\n\n"
            "class ModelTest(unittest.TestCase):\n"
            "    def test_transform(self):\n"
            "        self.assertEqual(transform(1), 2)\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n",
            encoding="utf-8",
        )

        result = {
            "schema_version": "external_executor_result.v1",
            "sentinel": {"preserve": True},
            "context_alignment": {"status": "pass", "confirmed_execution_scope": {}},
            "resource_readiness": {"status": "ready", "minimum_loop_feasible": True},
            "experiment_plan": {
                "status": "complete",
                "protocol_fingerprint": "protocol-abc",
                "fairness_fingerprint": "fairness-abc",
            },
            "iteration_plan": {
                "iteration_id": "ITER-001",
                "status": "approved",
                "implementation_required": True,
                "planned_changes": ["Implement transform vertical slice"],
                "base_source": "external_executor/workdir/base_source",
            },
            "implementation_spec": {
                "implementation_spec_id": "SPEC-001",
                "iteration_id": "ITER-001",
                "status": "ready",
                "base_source": "external_executor/workdir/base_source",
                "source_kind": "ours",
                "approved_changes": [{
                    "change_id": "CHG-transform",
                    "change_type": "module",
                    "summary": "Implement transform behavior",
                    "target_paths": ["src/model.py", "tests/test_model.py"],
                    "allowed_operations": ["modify"],
                    "module_ids": ["M1"],
                    "acceptance_criteria": ["transform(1) returns 2"],
                    "required_tests": ["VERIFY-model"],
                }],
                "module_contracts": [{
                    "module_id": "M1",
                    "name": "transform",
                    "code_path_patterns": ["src/model.py"],
                    "test_path_patterns": ["tests/test_model.py"],
                    "config_keys": ["model.transform_enabled"],
                    "ablation_switch": {
                        "required": True,
                        "config_key": "model.transform_enabled",
                        "off_semantics": "Return the untransformed value",
                    },
                }],
                "verification_plan": [{
                    "verification_id": "VERIFY-model",
                    "name": "transform unit test",
                    "command": ["python", "-m", "unittest", "tests.test_model"],
                    "working_directory": ".",
                    "verification_class": "unit",
                    "mandatory": True,
                    "tdd_behavior_id": "BEHAVIOR-transform",
                    "timeout_seconds": 30,
                    "linked_change_ids": ["CHG-transform"],
                    "linked_module_ids": ["M1"],
                }],
                "protected_paths": ["protocol/**"],
            },
            "scope_change_requests": {"status": "not_started", "items": []},
        }
        (ext / "result_pack.json").write_text(json.dumps(result), encoding="utf-8")
        return root

    def prepare(self, ws: Path) -> tuple[dict, Path]:
        run("preflight_implementation.py", "--workspace", str(ws))
        run("build_change_contract.py", "--workspace", str(ws))
        contract = json.loads((ws / "external_executor/implementation_change_contract.json").read_text())
        self.assertEqual(contract["status"], "ready")
        run("prepare_worktree.py", "--workspace", str(ws))
        impl_root = ws / contract["implementation_root"]
        return contract, impl_root

    def test_00_red_green_patch_mapping_gate_and_narrow_apply(self) -> None:
        ws = self.make_workspace()
        contract, impl_root = self.prepare(ws)
        self.assertTrue((impl_root / "before/src/model.py").exists())
        self.assertEqual((impl_root / "before/src/model.py").stat().st_mode & 0o222, 0)
        self.assertEqual(
            (ws / "external_executor/workdir/base_source/src/model.py").read_text(),
            "def transform(value: int) -> int:\n    return value\n",
        )

        run(
            "run_verification.py", "--workspace", str(ws),
            "--verification-id", "VERIFY-model", "--phase", "red", "--expect", "failure",
        )
        worktree_model = impl_root / "worktree/src/model.py"
        worktree_model.write_text("def transform(value: int) -> int:\n    return value + 1\n", encoding="utf-8")
        run(
            "run_verification.py", "--workspace", str(ws),
            "--verification-id", "VERIFY-model", "--phase", "green", "--expect", "success",
        )
        run(
            "run_verification.py", "--workspace", str(ws),
            "--verification-id", "VERIFY-model", "--phase", "final", "--expect", "success",
        )
        run(
            "record_tdd_cycle.py",
            "--red", str(impl_root / "verification/VERIFY-model/red.json"),
            "--green", str(impl_root / "verification/VERIFY-model/green.json"),
            "--output", str(impl_root / "verification/tdd-transform.json"),
        )

        mapping = {
            "schema_version": "implementation_module_mapping.v1",
            "items": [{
                "module_id": "M1",
                "implementation_status": "implemented",
                "code_paths": ["src/model.py"],
                "public_interfaces": ["src.model.transform"],
                "config_keys": ["model.transform_enabled"],
                "test_paths": ["tests/test_model.py"],
                "ablation_switch": {
                    "config_key": "model.transform_enabled",
                    "off_semantics": "Return the untransformed value",
                },
                "diagnostic_switches": [],
                "affected_experiment_ids": [],
                "limitations": [],
                "empirical_support_claimed": False,
            }],
        }
        mapping_path = impl_root / "mappings/module_mapping.json"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        run(
            "validate_module_mapping.py", "--workspace", str(ws),
            "--mapping", str(mapping_path),
            "--output", str(impl_root / "mappings/module_mapping_validation.json"),
        )
        run("generate_patch_bundle.py", "--workspace", str(ws))
        run(
            "scan_change_scope.py", "--workspace", str(ws),
            "--output", str(impl_root / "patches/scope_scan.json"),
        )
        run("initialize_implementation_report.py", "--workspace", str(ws))

        report_path = ws / "external_executor/implementation_report.json"
        report = json.loads(report_path.read_text())
        report["implemented_changes"]["status"] = "complete"
        report["implemented_changes"]["items"][0].update({
            "status": "implemented",
            "changed_paths": ["src/model.py"],
            "summary": "Implemented the approved transform behavior.",
            "tests": ["VERIFY-model"],
            "evidence_refs": ["external_executor/implementation_report.json"],
        })
        report_path.write_text(json.dumps(report), encoding="utf-8")
        run("compute_implementation_gate.py", "--report", str(report_path), "--write-back")
        report = json.loads(report_path.read_text())
        self.assertEqual(report["implementation_gate"]["status"], "ready_for_review")
        run("validate_implementation_report.py", "--workspace", str(ws))
        run("apply_implementation_report.py", "--workspace", str(ws))
        result = json.loads((ws / "external_executor/result_pack.json").read_text())
        self.assertTrue(result["sentinel"]["preserve"])
        self.assertEqual(result["implementations"]["active_implementation_id"], contract["implementation_id"])
        self.assertEqual(result["implementations"]["status"], "complete")

    def test_10_secret_and_scope_primitives(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        from scan_change_scope import SECRET_PATTERNS
        from _common import match_any
        text = 'API_KEY = "super-secret-token-123456"'
        self.assertTrue(any(pattern.search(text) for _, pattern in SECRET_PATTERNS))
        self.assertTrue(match_any("src/model.py", ["src/*.py"]))
        self.assertFalse(match_any("protocol/locked.json", ["src/*.py"]))

    def test_20_symlink_detection_primitive(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        from _common import reject_symlinks
        root = Path(tempfile.mkdtemp(prefix="implementation-symlink-test-"))
        target = root / "target.py"
        target.write_text("value = 1\n", encoding="utf-8")
        link = root / "link.py"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        self.assertIn(str(link), reject_symlinks(root))


if __name__ == "__main__":
    unittest.main()
