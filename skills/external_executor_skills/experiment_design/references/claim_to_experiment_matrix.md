# Claim-To-Experiment Matrix

Experiments are designed from claims, not table slots.

For each candidate claim, write:

- claim id
- reviewer question
- evidence needed
- experiment id
- run type
- dataset and split
- metric and direction
- baselines
- ours config
- ablation or diagnostic switch
- fairness constraints
- expected raw artifacts
- claim boundary if the experiment cannot run

Every experiment must answer a reviewer question. Ablations must test method
mechanisms, not arbitrary component removal.
