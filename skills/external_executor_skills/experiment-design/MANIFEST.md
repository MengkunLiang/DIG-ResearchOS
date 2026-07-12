# experiment-design File Manifest

## Main instruction

| File | Responsibility |
| --- | --- |
| `SKILL.md` | Phase C workflow, boundaries, command sequence, gate, and return contract. |

## References

| File | Responsibility |
| --- | --- |
| `references/claim-evidence-design.md` | Claim-first experiment reasoning, evidence roles, reviewer questions, and mechanism-linked ablations. |
| `references/protocol-and-fingerprint-policy.md` | Protocol contents, canonical hashing, versions, material changes, and result comparability. |
| `references/experiment-plan-contract.md` | Plan and experiment records, minimum evidence package, and versioning. |
| `references/budget-and-dag-policy.md` | Budget dimensions, prioritization, dependencies, parallel groups, and early stop. |
| `references/statistical-and-reporting-policy.md` | Metrics, seeds, uncertainty, tuning fairness, failures, and predeclared interpretation. |
| `references/plan-review-checklist.md` | Independent review axes and pass/fix/block conditions. |
| `references/output-contract.md` | Standalone artifacts, narrow result-pack ownership, report shape, and root return. |

## Scripts

| File | Responsibility |
| --- | --- |
| `scripts/_common.py` | Atomic JSON, workspace/path controls, canonical hashing, IDs, and shared utilities. |
| `scripts/preflight_experiment_design.py` | Check Phase A/B prerequisites, schema, write paths, claims, protocol, and budget inputs. |
| `scripts/build_claim_evidence_matrix.py` | Normalize claim obligations and preserve project-specific completions on rebuild. |
| `scripts/build_protocol_snapshot.py` | Assemble and version the complete comparison protocol. |
| `scripts/fingerprint_protocol.py` | Hash the canonical protocol and its components. |
| `scripts/build_experiment_plan.py` | Scaffold baseline, smoke, main comparison, and mechanism-ablation experiments plus DAG. |
| `scripts/validate_plan_dag.py` | Check IDs, dependencies, cycles, edges, and parallel groups. |
| `scripts/validate_experiment_plan.py` | Check claim coverage, formal completeness, mechanism mapping, risks, and budget. |
| `scripts/compare_protocol_versions.py` | Classify protocol changes and affected evidence. |
| `scripts/compute_design_gate.py` | Derive ready/partial/blocked and write plan review/gate. |
| `scripts/assemble_experiment_design_report.py` | Assemble the durable child report. |
| `scripts/validate_experiment_design_report.py` | Validate report and gate consistency. |
| `scripts/apply_experiment_design_report.py` | Narrowly apply only claim matrix and experiment plan to result pack. |

## Tests

| File | Responsibility |
| --- | --- |
| `tests/test_experiment_design_scripts.py` | Preflight, claims, fingerprint stability, plan/DAG, protocol impact, validation/gate, and narrow apply regression tests. |
