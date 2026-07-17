# Evidence and Verification Policy

## Fresh evidence rule

No approval claim without evidence generated against the current input snapshot fingerprint.

For each claim:

1. State what check proves it.
2. Run the complete authorized check.
3. Save command, purpose, start/finish time, exit code, full log reference, and snapshot fingerprint.
4. Read the output and count failures/warnings.
5. Make only the claim the evidence supports.

Builder summaries, earlier runs, partial output, or “should pass” are not fresh evidence.

Run-derived evidence accepted for review must be read from `external_executor/raw_results/`. Review-only logs may remain under `external_executor/reviews/`, but they do not replace raw experiment logs, metrics, run records, or checkpoints.

## Evidence classes

```text
static_inspection
unit_test
integration_test
protocol_comparison
config_validation
data_integrity_check
smoke_run
manual_review
```

Each axis needs suitable evidence. Examples:

- Spec alignment: line-by-line implementation/spec mapping and changed-code inspection.
- Code correctness: targeted tests plus relevant integration checks.
- Protocol fairness: normalized protocol comparison and config/evaluation inspection.
- Data integrity: split/preprocessing tests or explicit leakage checks.
- Reproducibility: command/config/log/manifest validation.
- Security/path: path/secret/subprocess inspection and policy checks.
- Contribution drift: method intent/spec/delta/code mapping.

## Approval levels

### Smoke

Requires pinned code/config, basic static inspection, runnable entrypoint evidence, safe paths, and no blocking issue. Scientific fairness may remain incomplete if smoke output cannot become a formal claim.

### Small-scale

Additionally requires relevant unit/integration checks, stable split/metric wiring, usable logs/configs, and no major defect affecting the small-scale question.

### Formal

Requires all mandatory axes, fresh verification, fair normalized protocols, baseline comparability, data-integrity checks, correct ablation switches, complete provenance/logging, and no unresolved fairness/provenance warning.

## Evidence bundle

Each record contains:

```text
evidence_id
evidence_type
purpose
command_or_check
input_fingerprint
started_at
finished_at
exit_code
log_ref
scope
result = pass | fail | inconclusive
```

The log must exist inside the workspace and be registered or registerable. An evidence record with mismatched fingerprint or time before the snapshot is stale.

## Inconclusive evidence

Inconclusive is not pass. Route to a targeted additional check, lower the approval level, or block when the missing evidence is mandatory.
