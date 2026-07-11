# Baseline Reproduction Record

Baseline reproduction must happen before implementing the new method.

For each attempt, record:

- baseline id and source
- command
- config path
- dataset split
- seed
- metric name and direction
- raw log path
- raw result path
- result value when available
- failure reason when unavailable
- decision: `reproduce`, `repair`, `replace`, or `mark_unavailable`

Reviewer questions for failed baselines:

- Is this an environment problem?
- Is the code too old or dependency-locked?
- Is data unavailable?
- Is configuration underspecified?
- Is the baseline incompatible with this dataset or metric?

Missing or failed required baselines must update claim risk.
