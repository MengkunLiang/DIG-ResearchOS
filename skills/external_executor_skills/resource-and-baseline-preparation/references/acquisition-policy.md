# Acquisition Policy

## Purpose

This policy governs how Phase B obtains experiment resources. It never grants authority; authority must already exist in `handoff_pack.json`, `context_alignment.confirmed_execution_scope`, `AGENTS.md`, and `allowed_paths.txt`.

## Modes

| Mode | Local inspection | GitHub search/acquisition | Dataset download | Baseline reimplementation |
| --- | --- | --- | --- | --- |
| `local_only` | allowed | forbidden | forbidden unless the data already exists locally | forbidden |
| `github_allowed` | allowed | allowed only when `network_allowed=true` and domain is allowed | allowed only when `dataset_download_allowed=true` | forbidden |
| `github_and_reimplementation` | allowed | same as `github_allowed` | same as `github_allowed` | allowed only when `baseline_reimplementation_allowed=true` and all preconditions pass |

A boolean flag cannot broaden a stricter mode. A mode cannot override `AGENTS.md`, path policy, restricted-data terms, or license restrictions.

## Ordered acquisition path

For every requirement:

```text
local verified material
  -> authorized remote discovery and immutable acquisition
  -> authorized reimplementation
  -> unavailable / blocker
```

Move to the next path only for the still-unsatisfied requirement. Record why every prior path failed.

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
