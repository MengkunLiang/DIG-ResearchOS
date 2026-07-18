# Acquisition Policy

## Purpose

This policy governs how Phase B obtains experiment resources. The deterministic Phase B scripts read authority from `handoff_pack.json`, `result_pack.json#context_alignment.confirmed_execution_scope`, `AGENTS.md`, `expected_outputs_schema.json`, and `allowed_paths.txt`; executor selection may already be reflected in `AGENTS.md`, but these scripts do not read `external_executor/report/executor_selection.json` directly. For ResearchOS T5 external execution, the default authority allows public remote platform access, public dataset download, and baseline reimplementation inside allowed paths, subject to license and security review.

## Modes

| Mode | Local inspection | Public remote search/acquisition | Dataset download | Baseline reimplementation |
| --- | --- | --- | --- | --- |
| `local_only` | allowed | forbidden | forbidden unless the data already exists locally | forbidden |
| `github_allowed` | allowed | allowed only when `network_allowed=true` and domain is allowed | allowed only when `dataset_download_allowed=true` | forbidden |
| `github_and_reimplementation` | allowed | same as `github_allowed` | same as `github_allowed` | allowed only when `baseline_reimplementation_allowed=true` and all preconditions pass |

A boolean flag cannot broaden a stricter mode. A mode cannot override `AGENTS.md`, path policy, restricted-data terms, or license restrictions.

When a legacy handoff omits the acquisition policy, use the ResearchOS default mode `github_and_reimplementation` with `network_allowed=true`, `dataset_download_allowed=true`, and `baseline_reimplementation_allowed=true`. Resource candidates, public remote acquisitions, and baseline reimplementations for this skill must be placed under `resources/`.

## Ordered acquisition path

For every requirement:

```text
local verified material under resources/
  -> authorized remote discovery and immutable acquisition from public sources
  -> authorized baseline reimplementation
  -> unavailable / blocker
```

Move to the next path only for the still-unsatisfied requirement. After each path, review candidates against the requirement matrix and stop for that requirement if it is satisfied. Record why every prior path failed before proceeding. If the final reimplementation path is unavailable or still insufficient for a minimum-loop required resource, block and request human supplementation or scope review.

## Remote-search privacy

Search using public-safe terms:

- public paper title or DOI;
- public method/baseline name;
- public benchmark/dataset name;
- public task and metric terms;
- author or project organization names.

Do not send:

- unpublished draft sentences;
- private hypothesis wording;
- unpublished numerical results;
- private repository names or paths;
- credentials, tokens, signed URLs, or restricted-data identifiers.

When a public-safe query cannot be formed without revealing private material, record a search blocker or ask the root for human review.

## Source and domain rules

- Prefer HTTPS.
- Allow only domains listed by policy. `github.com` permission does not imply permission for arbitrary release mirrors, cloud drives, package indexes, model hubs, or dataset hosts.
- Follow redirects only when the final domain is also allowed.
- Never embed credentials in repository URLs.
- Do not acquire Git submodules, LFS objects, release assets, or external datasets unless separately authorized and recorded.
- Pin Git resources to a commit SHA. A tag may be used for discovery, but store the resolved commit.
- Pin data and checkpoint resources by published version, immutable revision, content hash, or archived snapshot.
- Search across multiple relevant allowed public source classes when local resources are insufficient: paper/project pages, official repositories, benchmark/dataset organizations, model or dataset hubs, public archives, and supplement pages. Do not stop at the first convenient repository when official or protocol-bearing sources remain unchecked.

## First remote platforms

Search and attempt acquisition on these platform groups before broadening:

| Resource kind | First platforms |
| --- | --- |
| Baseline | Hugging Face; OpenReview; GitLab; Bitbucket; ModelScope; Zenodo |
| Dataset | Hugging Face; OpenML; Kaggle; UCI; Zenodo; Dataverse; DataCite |
| Benchmark | OpenML; Hugging Face Leaderboards; Codabench; EvalAI; HELM; relevant domain platforms such as OGB, MTEB, OpenCompass |

Other public platforms are fallback sources only after the relevant first-platform group cannot satisfy the requirement or cannot provide enough provenance.

## Dataset access

Dataset download requires all of:

1. `dataset_download_allowed=true`;
2. source domain is allowed;
3. access terms permit the intended research use;
4. storage path is authorized;
5. personally identifiable, sensitive, or restricted data handling is supported;
6. provenance, version, split, and checksum can be recorded.

Do not bypass logins, click-through terms, data-use agreements, institutional access, geographic controls, or rate limits. Do not synthesize a replacement for a required formal dataset. Synthetic/toy data may be prepared only for smoke use and must be labeled as such.

## License and redistribution

Record separately:

- code license;
- dataset license or terms of use;
- checkpoint/model license;
- paper/supplement access terms;
- redistribution constraints;
- attribution requirements;
- commercial/research-only or field-of-use restrictions.

`license_unknown` is a risk, not permission. A repository without a license is not automatically reusable. Do not copy restricted assets into the handoff package when only local use is allowed.

## Third-party code safety

Acquisition is not execution. After download:

- do not run setup/install scripts;
- do not run notebook cells;
- do not build containers;
- do not invoke Makefiles or task runners;
- do not initialize submodules;
- do not enable package-manager lifecycle hooks;
- perform static review before any later execution decision.

## Replacement

A replacement is allowed only when the confirmed scope explicitly permits it and states the equivalence criteria. Otherwise record:

- unavailable required baseline/resource;
- proposed replacement;
- non-equivalence risks;
- affected claims;
- required human or scope review.

Never convert “closest available” into “equivalent” by wording.
