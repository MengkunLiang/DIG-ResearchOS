# Source Ranking and Search

## Purpose

Remote discovery is a targeted search for unsatisfied requirement IDs, not a general code hunt.

## Search record

Create one record per query/candidate decision:

```json
{
  "search_id": "SEARCH-...",
  "requirement_ids": [],
  "query": "",
  "query_is_public_safe": true,
  "searched_at": "",
  "domains": [],
  "results": [],
  "selected_candidate_ids": [],
  "rejected_candidates": [],
  "exhaustiveness": "targeted|broad|exhausted",
  "notes": []
}
```

## Source tiers

| Tier | Source class | Typical evidence |
| --- | --- | --- |
| 1 | official paper/author/project repository | paper/project page links repo; author organization; release/commit |
| 1 | official benchmark/dataset organization | canonical docs, schema, split, metric, version, terms |
| 2 | author-recognized implementation | linked by author/project issue, docs, or release notes |
| 3 | high-confidence reproduction | explicit paper-to-code map, protocol match, reproducibility evidence |
| 4 | other third-party implementation | useful lead; requires strong identity/protocol review |

Popularity is not source authority. Stars, forks, and recent commits may inform maintenance risk but cannot prove fidelity.

## Query construction

For a baseline:

```text
"<exact paper title>" code
"<baseline name>" official github
"<author surname>" "<method name>" github
site:github.com "<method name>" "<dataset>"
```

For benchmark/data:

```text
"<benchmark name>" official dataset
"<dataset name>" split evaluation metric
"<benchmark paper title>" repository
```

For metrics/protocol:

```text
"<metric name>" official implementation
"<benchmark name>" evaluation script
"<paper title>" supplementary protocol
```

Do not search with private manuscript prose.

## Candidate ranking

Score or explicitly assess:

1. identity confidence;
2. mechanism fidelity;
3. task and dataset compatibility;
4. split/preprocessing/metric/protocol fidelity;
5. immutable version availability;
6. license/access compatibility;
7. static security risk;
8. dependency/runtime risk;
9. compute feasibility;
10. maintenance/documentation quality.

Identity and protocol fidelity are gate dimensions, not tradeable points. A maintained but different implementation cannot outrank an exact required implementation merely through a higher aggregate score.

## Candidate record

Record:

```json
{
  "candidate_id": "CAND-...",
  "requirement_ids": [],
  "name": "",
  "resource_type": "",
  "source_class": "official_author_repo|official_benchmark_repo|author_recognized|third_party_reproduction|executor_reimplementation|other",
  "source_url": "",
  "resolved_revision": "",
  "local_path": "",
  "identity_evidence": [],
  "paper_match": {},
  "protocol_compatibility": {},
  "license": {},
  "security_review_ref": null,
  "dependency_risk": "low|medium|high|unknown",
  "compute_risk": "low|medium|high|unknown",
  "status": "discovered|acquired|rejected|reviewed|stale",
  "selection_reason": "",
  "rejection_reason": ""
}
```

## Search completion

Search can be marked `exhausted` only when:

- official/author channels were checked;
- benchmark/dataset official channels were checked where applicable;
- high-confidence third-party implementations were considered;
- search queries and rejections are preserved;
- remaining uncertainty is documented.

“First repository found” is not exhausted search.
