# Source Authority and Uncertainty

## Scope

These rules control static project-context construction only. They do not perform scientific audit, readiness review, or scope approval.

## Read order

Start from:

```text
external_executor/handoff_pack.json
```

Then confirm or replace execution-critical values from their designated source artifacts:

```text
project.yaml
ideation/hypotheses.md
ideation/exp_plan.yaml
novelty/novelty_audit.md
literature/synthesis.md
ideation/idea_scorecard.yaml
ideation/risks.md
external_executor/expected_outputs_schema.json
external_executor/AGENTS.md
external_executor/allowed_paths.txt
```

Read deeper literature, seed, resource, or expression-material files only for a targeted missing field and only when the compiler has a deterministic reader for that source.

## Source-over-handoff rule

For each configured Context field:

### Handoff and source agree

- write the value;
- set field metadata to `confirmed`;
- record both sources when appropriate.

### Handoff and source conflict

- write the designated source-artifact value;
- set field metadata to `confirmed_from_source`;
- record the source artifact;
- preserve the ignored handoff value in metadata when the Schema allows it;
- do not create a separate conflict audit.

Examples:

- hypothesis fields come from `ideation/hypotheses.md` when they differ from handoff;
- task, benchmark, dataset, split, preprocessing, metric, seed, and experiment-policy fields come from `ideation/exp_plan.yaml` when explicitly defined there;
- required baselines and novelty boundaries come from `novelty/novelty_audit.md` when explicitly defined there;
- writable paths come from `allowed_paths.txt`;
- output shape and version come from `expected_outputs_schema.json`.

### Source cannot determine the value

- write the Schema-permitted empty value;
- set metadata to `uncertain`;
- record available source paths;
- write a specific note explaining what is missing or unparseable;
- do not use domain knowledge, repository defaults, third-party README defaults, or LLM inference.

### Source file is absent

Use the same `uncertain` treatment. File absence is not a reason to promote the handoff candidate into a source-confirmed value unless the field's configured authority is the handoff itself.

## Meaning of field status

```text
confirmed
  The configured authority supports the value and no different authoritative value was found.

confirmed_from_source
  A designated source artifact supplied the final value, replacing or clarifying handoff.

uncertain
  The current artifacts do not determine a reliable value.
```

These statuses describe provenance and determinability only. They are not:

- context-alignment verdicts;
- protocol-review verdicts;
- novelty verdicts;
- resource-readiness verdicts;
- evidence or claim levels.

## Runtime handling

Specialization preserves uncertain information. Runtime Skills decide its operational effect:

- `context-alignment` checks whether it is blocking;
- `experiment-design` resolves or blocks protocol-critical uncertainty;
- `resource-and-baseline-preparation` resolves resource uncertainty within policy;
- `research-execution` routes material scope or authority questions to a gate.
