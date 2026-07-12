# Safety and Environment Policy

Use this reference immediately before launching a process.

## Process launch

- Execute an argument vector with `shell=False`.
- Use the reviewed working directory and command exactly.
- Do not interpret pipes, redirects, substitutions, globbing, or shell control operators.
- Set a finite timeout.
- Start the process in its own process group so timeout and cancellation can terminate descendants.
- Capture stdout and stderr in the declared raw log from process start.

## Filesystem

Path validation protects the runner's own reads and writes. It cannot prove that arbitrary experiment code will not write elsewhere. The executor sandbox must enforce the declared filesystem boundary.

Formal execution requires `isolation.filesystem=enforced` with an evidence reference. A claim in the request is not itself evidence; it must reflect an executor capability or sandbox record supplied by the root.

## Network

- Default to no network requirement.
- If project policy forbids network, use an executor-level network sandbox where available.
- If the run requires network, the handoff and root dispatch must explicitly authorize it.
- Record `enforced`, `authorized`, or `unknown`; never claim isolation from an absence of observed traffic.
- A new credential, domain, download, or remote service requirement returns to `research-execution`.

## Environment variables and secrets

The runner passes a minimal safe environment plus only the variable names authorized in `environment.allowed_env` and non-secret literal overrides. It must:

- reject secret-like override names or values stored in the request;
- never serialize allowed secret values;
- redact secret-like environment values in diagnostic output;
- avoid inheriting unrelated API keys, tokens, cloud credentials, or proxy credentials.

Credential injection remains an executor responsibility. If a run cannot execute without new authority, block instead of embedding a secret.

## Third-party code

Third-party installation, download, shell, or system-modification code must already have passed resource and code review. `experiment-run` does not approve it. A reviewed wrapper or sandbox does not make an unpinned dependency reproducible; record exact dependency identities.

## Runtime observations

Resource monitoring may observe process duration and exit state. Do not silently add profilers, change thread counts, alter CUDA visibility, or inject determinism flags. Such changes affect the protocol and require review unless already present in the request.
