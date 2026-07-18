#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import dump_json_atomic, listify, load_json, match_any, relpath, resolve_in_workspace, resolve_workspace, utc_now

VALID_STATUS = {"implemented", "partial", "not_implemented", "blocked", "adapter_only", "diagnostic_only"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate implementation module-to-code/config/test/ablation mapping.")
    parser.add_argument("--workspace")
    parser.add_argument("--contract", default="external_executor/report/phase_D/implementation_change_contract.json")
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    contract_path = resolve_in_workspace(workspace, args.contract)
    mapping_path = resolve_in_workspace(workspace, args.mapping)
    output = resolve_in_workspace(workspace, args.output)
    contract = load_json(contract_path)
    mapping_data = load_json(mapping_path)
    items = mapping_data.get("items", []) if isinstance(mapping_data, dict) else []
    worktree = resolve_in_workspace(workspace, contract["implementation_root"]) / "worktree"
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    known = {item.get("module_id"): item for item in items if isinstance(item, dict) and item.get("module_id")}

    for module in contract.get("module_contracts", []):
        module_id = module.get("module_id")
        mapped = known.get(module_id)
        if not mapped:
            errors.append({"id": "missing_module_mapping", "module_id": module_id})
            continue
        status = mapped.get("implementation_status")
        if status not in VALID_STATUS:
            errors.append({"id": "invalid_implementation_status", "module_id": module_id, "value": status})
        if mapped.get("empirical_support_claimed") is True:
            errors.append({"id": "builder_claims_empirical_support", "module_id": module_id})
        code_paths = [str(value) for value in listify(mapped.get("code_paths")) if value]
        test_paths = [str(value) for value in listify(mapped.get("test_paths")) if value]
        config_keys = [str(value) for value in listify(mapped.get("config_keys")) if value]
        if status in {"implemented", "adapter_only", "diagnostic_only"} and not code_paths:
            errors.append({"id": "implemented_module_without_code_path", "module_id": module_id})
        for path in code_paths + test_paths:
            target = (worktree / path).resolve(strict=False)
            try:
                target.relative_to(worktree.resolve())
            except ValueError:
                errors.append({"id": "mapping_path_escape", "module_id": module_id, "path": path})
                continue
            if not target.exists():
                errors.append({"id": "mapping_path_missing", "module_id": module_id, "path": path})
        expected_patterns = module.get("code_path_patterns", [])
        if expected_patterns and code_paths and not all(any(match_any(path, [pattern]) for path in code_paths) for pattern in expected_patterns):
            warnings.append({"id": "expected_code_pattern_unmatched", "module_id": module_id, "patterns": expected_patterns})
        expected_config = set(str(value) for value in module.get("config_keys", []))
        if expected_config - set(config_keys):
            warnings.append({"id": "expected_config_keys_unmapped", "module_id": module_id, "keys": sorted(expected_config - set(config_keys))})
        ablation_required = bool(module.get("ablation_switch", {}).get("required", False))
        mapped_ablation = mapped.get("ablation_switch")
        if ablation_required and (not isinstance(mapped_ablation, dict) or not mapped_ablation.get("config_key") or not mapped_ablation.get("off_semantics")):
            errors.append({"id": "required_ablation_mapping_missing", "module_id": module_id})
        if status == "implemented" and not test_paths:
            warnings.append({"id": "implemented_module_without_test_path", "module_id": module_id})

    extra_ids = sorted(set(known) - {module.get("module_id") for module in contract.get("module_contracts", [])})
    if extra_ids:
        warnings.append({"id": "uncontracted_module_mapping", "module_ids": extra_ids})
    status = "fail" if errors else ("warning" if warnings else "pass")
    report = {
        "schema_version": "implementation_module_mapping_validation.v1",
        "generated_at": utc_now(),
        "implementation_id": contract["implementation_id"],
        "iteration_id": contract["iteration_id"],
        "status": status,
        "contract_ref": relpath(workspace, contract_path),
        "mapping_ref": relpath(workspace, mapping_path),
        "errors": errors,
        "warnings": warnings,
        "mapped_module_ids": sorted(known),
    }
    dump_json_atomic(output, report)
    print(f"{status}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 2 if errors else (1 if warnings else 0)


if __name__ == "__main__":
    raise SystemExit(main())
