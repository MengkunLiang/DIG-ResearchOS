# T7 Consumer Contract

T7 must be able to operate without hidden chat memory.

## T7-INGEST needs

- result-pack and handoff schema versions;
- run records and artifact locations;
- raw logs, configs, metric outputs, split, seed/repeat, code/resource/environment/protocol identity;
- failed, stale, unusable, smoke, small-scale, diagnostic, and formal classes;
- figure/table source lineage;
- method package and module/code/config mappings.

## T7-AUDIT needs

- baseline coverage and reproduction status;
- fairness/protocol review status;
- raw provenance for every formal candidate;
- missing provenance and integrity failures;
- failed trials, exclusions, seed coverage, and anti-cherry-pick context;
- mock/toy/synthetic/dry-run labels.

## T7-METHOD-AUDIT / POST-NOVELTY needs

- method intent reference;
- realized method and delta from intent;
- implemented/dropped/added modules;
- code/config mapping;
- attribution status and unsupported mechanisms;
- final framework figure mapping and `must_not_show`;
- scope-change and contribution-drift records.

## T7-CLAIMS needs

- claim IDs and reviewer questions;
- support and counterevidence references;
- evidence ceiling before audit;
- limitations, risks, unavailable baselines, approximations, and must-not-claim boundaries;
- candidate figures/tables linked to source evidence.

The handoff does not generate `drafts/result_to_claim.json` or any other T7 output.
