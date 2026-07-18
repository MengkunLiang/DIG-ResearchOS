# Executor Research Report Contract

## Required sections

The report contains exactly eight major sections.

### Project Summary

Record the research question, formal hypotheses, expected contributions, actual completed work, and changes relative to T4.5/T5. Give a reason for every recorded change. Never silently alter the research question, benchmark, hypothesis, contribution type, or protocol.

### Implementation Summary

Record implemented methods and modules, main code entry points, core configuration, runtime environment, dependency versions, data processing, design deltas, and unfinished work. Every implementation fact includes a real path when one exists.

### Experiment Inventory

Include every planned or executed experiment with these fields:

```text
Experiment ID
Objective
Hypothesis
Contribution
Dataset
Method
Baseline
Configuration
Random Seeds
Metrics
Status
Result Files
Log Files
Figures
Tables
```

Normalize status to `success`, `failed`, `partial`, or `invalid`. Do not omit failed, unfavorable, stale, or incomplete work.

### Comprehensive Results

Cover all completed structured results. Each record states the observed values, comparator, dataset/condition, metric direction, protocol, statistical-test status, raw files, figures/tables, supported scope, and unsupported scope.

If multiple files were aggregated, retain the aggregate table, raw source files, run directories where represented, configs, logs, and processing or plotting scripts. Never claim statistical significance unless a linked test exists.

### Claim Support Table

Include Claim ID, proposed Claim, supporting experiment, supporting file, strength, and limitation. Strength is preliminary and limited to `Supported candidate`, `Partially supported candidate`, or `Unsupported`. T8 performs final adjudication.

### Verified Literature Additions

Include only additions with title, authors, year, venue, at least one verifiable identifier, the exact supported point, material used, access level, and BibTeX or standard reference. A title or search-result summary alone is insufficient.

### Limitations and Open Issues

Cover data and method limitations, insufficient experimental coverage, compute restrictions, untested generalization, confounds, unresolved human judgment, failed work, and prohibited over-claims.

### Artifact Index

List source code, experiment configs, raw results, logs, generated figures, generated tables, verified references, processing scripts, and the T8 report path. Every empirical observation must resolve to an original path.

## Missing information

Write `Not recorded` for an unresolved field. Do not use a nearby experiment, similarly named file, or narrative summary to fill it.
