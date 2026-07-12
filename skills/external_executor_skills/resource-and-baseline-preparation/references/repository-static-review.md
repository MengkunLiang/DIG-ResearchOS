# Repository Static Review

## Purpose

Static review identifies risk before any third-party content is executed. It cannot certify safety or correctness.

## Inspect

- symlinks and path escapes;
- Git submodules and LFS pointers;
- executable files and shell scripts;
- package manager lifecycle hooks;
- `setup.py`, `pyproject.toml` build backends, `package.json` scripts;
- Makefiles, task runners, Dockerfiles, compose files;
- CI workflows;
- download/install commands;
- subprocess and shell invocation;
- destructive filesystem commands;
- privilege escalation;
- network listeners/reverse-shell patterns;
- embedded credentials/private keys;
- `.env` files and token-like files;
- generated binaries or large opaque assets;
- licenses and notices.

## Severity

- `critical`: path escape, credential/private key, destructive root command, reverse shell, explicit exfiltration, or code designed to bypass controls.
- `high`: install/lifecycle hook with network or shell execution, privileged container, hidden downloader, dynamic eval of remote content, or unresolved executable bootstrap.
- `medium`: submodule, LFS dependency, unpinned network fetch, opaque binary, broad shell use, or unsupported package build step.
- `low`: ordinary scripts/configuration that still require later review.

## Gate

- any unresolved `critical` -> `blocked`;
- unresolved `high` -> `needs_review` and no execution approval;
- `medium` -> constraints and targeted manual review;
- no finding -> `pass` for static inspection only.

Never translate static `pass` directly into approval for installation, training, evaluation, or formal comparison.

## Non-goals

Static review does not establish:

- algorithm fidelity;
- absence of all malicious behavior;
- dependency safety;
- license compatibility;
- reproducibility;
- fairness;
- correctness of metrics or data splits.
