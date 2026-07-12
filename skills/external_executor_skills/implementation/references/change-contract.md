# Implementation Change Contract

## Purpose

The change contract is the immutable boundary between root-approved intent and Builder execution. It is compiled before code edits. Candidate code is evaluated against the contract; the contract is not rewritten to fit candidate code.

## Contract envelope

```json
{
  "schema_version": "implementation_change_contract.v1",
  "status": "ready|draft|blocked|stale",
  "implementation_id": "IMPL-...",
  "iteration_id": "ITER-...",
  "generated_at": "",
  "input_fingerprint": "",
  "base_source": {
    "path": "external_executor/...",
    "source_kind": "ours|baseline_adapter|mixed|repair",
    "fingerprint": "",
    "read_only": true
  },
  "implementation_root": "external_executor/workdir/implementation/...",
  "protocol_fingerprint": "",
  "fairness_fingerprint": "",
  "approved_changes": [],
  "module_contracts": [],
  "verification_plan": [],
  "protected_paths": [],
  "allowed_dependency_changes": [],
  "forbidden_changes": [],
  "affected_experiment_ids": [],
  "baseline_reproduction_dependencies": [],
  "source_refs": [],
  "warnings": [],
  "blocking_issues": []
}
```

## Approved change item

```json
{
  "change_id": "CHG-...",
  "change_type": "module|loss|training_flow|inference_flow|adapter|entrypoint|config|ablation_switch|diagnostic_switch|logging|metric_output|seed|checkpoint|test|compatibility_fix|bug_fix|instrumentation|other",
  "summary": "",
  "rationale": "",
  "target_paths": ["src/..."],
  "allowed_operations": ["add", "modify", "delete"],
  "spec_item_ids": [],
  "module_ids": [],
  "affected_experiment_ids": [],
  "must_preserve": [],
  "acceptance_criteria": [],
  "required_tests": [],
  "protocol_impact_expected": "none|nonmaterial",
  "fairness_impact_expected": "none|controlled",
  "baseline_reproduction_impact_expected": "none|adapter_only|invalidates_selected",
  "notes": []
}
```

Target paths must be precise enough to distinguish approved code from unrelated files. Broad targets such as `**/*` require an explicit justification and should be rare.

## Module contract

```json
{
  "module_id": "M1",
  "name": "",
  "status_expected": "implemented|modified|adapter_only|diagnostic_only",
  "input_contract": {},
  "output_contract": {},
  "code_path_patterns": [],
  "config_keys": [],
  "test_path_patterns": [],
  "ablation_switch": {
    "required": true,
    "config_key": "",
    "off_semantics": ""
  },
  "diagnostic_switches": [],
  "linked_change_ids": [],
  "linked_experiment_ids": [],
  "must_preserve": [],
  "forbidden_shortcuts": []
}
```

A module contract defines implementation traceability, not empirical support.

## Verification item

```json
{
  "verification_id": "VERIFY-...",
  "name": "",
  "command": ["python", "-m", "pytest", "tests/test_module.py", "-q"],
  "working_directory": ".",
  "verification_class": "unit|interface|config|import|shape|serialization|type|lint|build|integration",
  "mandatory": true,
  "tdd_behavior_id": "BEHAVIOR-...",
  "timeout_seconds": 120,
  "allowed_environment_keys": [],
  "expected_outputs": [],
  "linked_change_ids": [],
  "linked_module_ids": []
}
```

The command is an argv array, not a shell string.

## Contract readiness

`ready` requires:

- a unique implementation and iteration ID;
- a valid base source;
- non-empty approved changes or explicit authorized no-op;
- target paths and allowed operations for every change;
- protocol and fairness fingerprints;
- at least one verification obligation for behavior-changing work;
- module contracts for research modules;
- forbidden changes and protected paths;
- no blocking issue.

`draft` means semantic completion is still required. Do not edit code against a draft contract.

## Stable identity

The implementation ID should be derived from iteration ID, semantic delta, implementation spec version, and relevant fingerprints. Do not derive it from array position or current time alone.
