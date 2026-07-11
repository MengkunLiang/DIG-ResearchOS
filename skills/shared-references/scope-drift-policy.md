# Scope Drift Policy

`handoff_pack.json#method_intent` is draft intent only. It constrains the
external executor but is not the final Method source for T8.

Allowed refinements:

- translate an abstract idea into concrete modules
- add necessary training details
- add ablation switches
- repair engineering details that do not change the contribution
- narrow claims based on evidence

Forbidden silent drift:

- changing the central hypothesis
- dropping a required baseline
- changing benchmark or task without review
- changing contribution type
- replacing the method with another paper's method
- presenting unsupported modules as core contributions

When a forbidden or contribution-changing edit is needed, write
`scope_change_requests` with the change, reason, affected claims, contribution
drift level, and required action. Major drift requires human review or a
post-experiment novelty check before T8.
