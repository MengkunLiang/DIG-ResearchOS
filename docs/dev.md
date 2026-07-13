# Development

## Local Setup

```bash
conda env create -f environment.yml
conda activate researchos
pip install -e .
python -m researchos.cli validate-config
```

Use `.env` for local provider credentials. Never add it to a commit. Run
commands from the repository root; before editable installation use
`PYTHONPATH="$PWD" python -m researchos.cli ...`.

## Change Discipline

1. Read the relevant stage, tool, schema, prompt, and validator before editing.
2. Preserve workspace artifact schema and state-machine semantics unless the
   change explicitly updates all consumers.
3. Keep console observability in the common renderer/reporter rather than
   adding ad hoc `print` statements.
4. Do not turn a domain convention into a project protocol default. Concrete
   metrics/datasets/baselines/seeds need source-bound current-project evidence.
5. Update the user-facing README and the affected document in `docs/` with any
   CLI, environment, artifact, recovery, or behavior change.
6. For a public integrated Skill, declare a validated `workflow` in `SKILL.md`,
   call `update_skill_workflow` at phase boundaries, and keep every suggested
   artifact inside the declared workspace policy.

## Validation

```bash
PYTHONPATH=. python -m compileall -q researchos
PYTHONPATH=. python -m researchos.cli validate-config --no-banner --no-color
pytest -q tests/unit
pytest -q tests/real
git diff --check
```

Add focused tests for changed logic. At minimum, behavior changes should cover
their validator/tool/CLI path and one real or snapshot-style integration path
where the blast radius reaches user-visible runtime behavior.

For context-adaptive batching, test both a multi-paper provider-context batch
and a malformed/partial batch fallback. Assert separate note artifacts and
`ABSTRACT_ONLY` boundaries; never test only the provider call count.

The local `tests/` directory is ignored in this checkout by repository policy;
run it locally but do not accidentally stage ignored fixtures merely to make a
change appear tested.

## Useful Commands

```bash
python -m researchos.cli doctor --workspace /tmp/researchos-dev
python -m researchos.cli run-task HELLO --workspace /tmp/researchos-dev
python -m researchos.cli list-skills --workspace /tmp/researchos-dev
python -m researchos.cli describe-skill domain-synthesis-studio --workspace /tmp/researchos-dev
python -m researchos.cli validate --task T3.6-SEC-INTRO --workspace ./workspace/project-a
```

For Compose regression:

```bash
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Compose is a normal execution environment, not Docker-in-Docker. Do not mount
the Docker socket into the runtime merely to make LaTeX work.

## Documentation And Release Checklist

- Validate configuration and all affected tests.
- Run `doctor` for native/Docker/TeX changes.
- Inspect generated Survey PDFs with a renderer when graphics change; check
  nonblank pixels, readable typography, and factual source basis.
- Run a TTY or no-color CLI smoke when console behavior changes.
- Confirm `git diff --check`, `git status --short`, and the staged file list.
- Do not commit `.env`, workspace output, PDFs, logs, ignored local notes, or
  unrelated user changes.
