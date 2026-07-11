# Code And Protocol Review Checklist

Review before formal runs and after every implementation change.

Check:

- implemented code matches method intent or recorded realized method
- baseline code/configs were not weakened
- metric direction is correct
- seed and split are consistent across baseline and ours
- no data leakage
- ablation switches exist and are wired
- raw logs and configs are sufficient for reproduction
- dry-run or mock-only artifacts are not used as formal evidence
- result tables and figures can be traced to source artifacts

Output:

```json
{
  "review_status": "pass | needs_fix | blocked",
  "findings": [],
  "required_fixes": [],
  "claim_risks": []
}
```
