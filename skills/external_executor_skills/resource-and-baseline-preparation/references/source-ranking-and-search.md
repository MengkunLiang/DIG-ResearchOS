# Source Ranking and Search

## Purpose

Remote discovery is a targeted search for unsatisfied requirement IDs, not a general code hunt. It is the second step after local `resources/` review, and it runs only for requirements still unsatisfied by local by-hand material.

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
  "source_classes_checked": [],
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

Use multiple relevant public source classes before declaring a required resource unavailable. For baselines this normally includes the paper/project page, author organization, official or author-recognized repositories, and high-confidence reproduction leads. For datasets, benchmarks, checkpoints, and metrics this includes official benchmark or dataset sites, public archives, model/data hubs, supplement pages, and repository links when authorized by policy.

## First-platform order

Search these platform groups first for the corresponding resource kind:

| Resource kind | Platforms |
| --- | --- |
| Baseline | Hugging Face; OpenReview; GitLab; Bitbucket; ModelScope; Zenodo |
| Dataset | Hugging Face; OpenML; Kaggle; UCI; Zenodo; Dataverse; DataCite |
| Benchmark | OpenML; Hugging Face Leaderboards; Codabench; EvalAI; HELM; OGB, MTEB, OpenCompass or another field-specific benchmark platform |

If the first group cannot satisfy a requirement, record that outcome before broadening to other public sources.

## Query construction

For a baseline:

```text
"<baseline name>" site:huggingface.co
"<baseline name>" site:openreview.net
"<baseline name>" site:gitlab.com
"<baseline name>" site:bitbucket.org
"<baseline name>" site:modelscope.cn
"<baseline name>" site:zenodo.org
"<exact paper title>" code
"<baseline name>" official github
"<author surname>" "<method name>" github
site:github.com "<method name>" "<dataset>"
```

For benchmark/data:

```text
"<dataset name>" site:huggingface.co
"<dataset name>" site:openml.org
"<dataset name>" site:kaggle.com
"<dataset name>" site:archive.ics.uci.edu
"<dataset name>" site:zenodo.org
"<dataset name>" site:dataverse.harvard.edu
"<dataset name>" site:datacite.org
"<benchmark name>" official dataset
"<dataset name>" split evaluation metric
"<benchmark paper title>" repository
"<dataset name>" official benchmark
"<dataset name>" zenodo figshare openml huggingface
```

For metrics/protocol:

```text
"<metric name>" official implementation
"<benchmark name>" evaluation script
"<paper title>" supplementary protocol
"<benchmark name>" codabench evalai helm leaderboard
"<benchmark name>" OGB MTEB OpenCompass
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
  "executable_baseline_criteria": {},
  "status": "discovered|acquired|rejected|reviewed|stale",
  "selection_reason": "",
  "rejection_reason": ""
}
```

## Search completion

Search can be marked `exhausted` only when:

- official/author channels were checked;
- benchmark/dataset official channels were checked where applicable;
- at least two relevant allowed public source classes were checked when available;
- high-confidence third-party implementations were considered;
- search queries and rejections are preserved;
- remaining uncertainty is documented.

“First repository found” is not exhausted search.

After each accepted remote candidate receives static and protocol review, update the affected requirement status. Do not continue to baseline reimplementation for a requirement that has already met its acceptance criteria.

For a baseline candidate, do not mark it executable unless the record can support all executable-baseline criteria: accessible code or model, locked revision, clear license, environment/dependency information, dataset version and split, metric implementation, and at least one traceable result record.
