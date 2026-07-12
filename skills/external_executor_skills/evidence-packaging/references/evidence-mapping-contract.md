# Evidence Mapping Contract

## Purpose

Mappings make package assertions inspectable and support T7's later audits. They are not claim verdicts.

## Module mapping

```text
module ID
↔ realized definition
↔ code refs
↔ config keys
↔ implementation evidence
↔ attribution evidence/type/confidence
↔ framework nodes
↔ result visuals
↔ claim candidate IDs
```

Rules:

- every implemented module has code/config mapping;
- supported modules have empirical evidence refs;
- unsupported/unassessed modules remain visible in the mapping;
- dropped modules are mapped to the intent delta, not the final framework nodes.

## Visual mapping

```text
visual artifact ID
↔ source result
↔ source data/table
↔ metric output
↔ config/log
↔ plot script
↔ rendered file
↔ protocol
↔ claim candidates
```

A ready result visual must have all lineage links.

## Claim-candidate mapping

```text
claim candidate
↔ planned experiment
↔ active formal run records
↔ relevant modules
↔ ready/missing visuals
↔ must-not-claim boundary
```

Use `audit_status=not_audited_by_T7`. Evidence packaging must not infer paper approval from record counts or upstream labels.

## Bidirectional checks

- every claim ID named by a visual exists in the claim matrix;
- every active main claim candidate has a visual or explicit missing entry;
- every ready visual maps back to a source result and protocol;
- every supported mechanism maps to controlled evidence;
- every framework node maps to a realized module;
- every realized module maps to code and config;
- no stale record supports an active candidate.
