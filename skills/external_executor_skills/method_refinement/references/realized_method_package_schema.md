# Realized Method Package Schema

`realized_method_package` is generated from actual implementation and
post-result diagnosis. It is not a restatement of T5 `method_intent`.

Required fields:

- `final_method_name`
- `one_sentence_method`
- `actual_core_mechanism`
- `implemented_modules`
- `dropped_modules`
- `added_modules`
- `actual_algorithm_flow`
- `actual_losses`
- `module_attribution_summary`
- `supported_mechanisms`
- `unsupported_mechanisms`
- `claim_boundary`
- `delta_from_method_intent`

Each implemented module should include:

- module id and name
- purpose
- input and output
- code paths
- config keys
- ablation or diagnostic support
- evidence refs

Each delta from method intent must state whether it affects contribution and
whether post-experiment novelty review is required.
