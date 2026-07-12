# Static Risk Rules

The scanner emits candidates, not verdicts. Inspect context before creating a finding.

| Rule | Candidate signal | Typical axis |
| --- | --- | --- |
| `SECRET_LITERAL` | token/key/password assignment with a literal | security_and_paths |
| `SHELL_EXECUTION` | `shell=True`, `os.system`, dynamic shell command | security_and_paths |
| `DYNAMIC_EXECUTION` | `eval` or `exec` | security_and_paths / code_correctness |
| `UNSAFE_DESERIALIZATION` | pickle/joblib or unrestricted model load | security_and_paths |
| `INCOMPLETE_CODE` | TODO/FIXME, `NotImplemented`, placeholder `pass` | spec_alignment / code_correctness |
| `TEST_DATA_FIT` | fit/fit_transform using test/eval data | data_integrity |
| `TEST_SELECTION` | test metric used for best checkpoint/tuning | data_integrity / protocol_fairness |
| `UNSEEDED_RANDOMNESS` | random operation without visible seed in reviewed scope | reproducibility |
| `HARDCODED_PATH` | machine-specific absolute paths | reproducibility / security_and_paths |
| `DEBUG_OUTPUT` | debug print/dump in training/evaluation path | reproducibility |

False positives are expected. The report must cite actual behavior, not merely the scanner rule.
