# ResearchOS Runtime

## Project Overview

ResearchOS prioritizes **runtime infrastructure** over a fully completed T1-T9 research agent product at this stage.

Core capabilities already in place:

- `AgentRunner` main loop: message protocol, LLM calls, tool execution, finish validation, budget, trace.
- `StateMachine` orchestration layer: `state.yaml`, gates, resume, iteration, task override.
- `ToolRegistry` and workspace permissions: tool factory registration, task-specific tool instances, read/write boundary control.
- `cli_runners`: supports both complete pipeline mode and single task debug mode.
- `skills` runtime: supports `SKILL.md`, skill tool auto-discovery, standalone skill execution.
- Key runtime tools: `search_papers`, `fetch_paper_metadata`, `docker_exec`, `latex_compile`, `extract_paper_sections`, `MCPTool` adapter.
- Testing infrastructure: `MockLLMClient`, `MockHumanInterface`, runtime test doubles, pytest fixtures.

### Implemented Agents

- `HelloAgent`: debug agent
- `PIAgent` (T1/T7.5): project initialization and evaluation, supports init and evaluate modes
- `ScoutAgent` (T2): literature retrieval, multi-source paper search
- `ReaderAgent` (T3/T3.5): deep reading and literature synthesis, supports read and synthesize modes
- `IdeationAgent` (T4): hypothesis generation via two-round Gate interaction
- `NoveltyAuditorAgent` (T4.5): novelty audit, evaluates hypothesis novelty and feasibility
- `ExperimenterAgent` (T5/T7): experiment execution, T5 is pilot, T7 is full mode
- `NoveltyAgent` (T6): final novelty verification based on pilot results
- `WriterAgent` (T8-WRITE/T8-DRAFT/T8-REVISE-*): paper writing, supports outline, draft, revision phases
- `ReviewerAgent` (T8-REVIEW-*): paper review, supports multiple review rounds
- `SubmissionAgent` (T9): submission preparation, handles template migration, anonymization, compile verification

See [researchos/agents/registry.py](./researchos/agents/registry.py).

### LLM Routing Support

Multi-provider support: SiliconFlow, OpenRouter, OpenAI, Anthropic

- Config file: `config/model_routing.yaml`
- Default: SiliconFlow DeepSeek
- API keys configurable via environment variables

### Robustness Enhancements

Based on `ResearchOS_Agent_Dev_Spec_Addendum_Robustness.md`, the following enhancements are implemented:

1. **T4 Hypothesis Pre-mortem (§4.1)**: Counter-intuitive verification between Gate1 and Gate2
2. **Runtime Budget Drift Warning (§7.1)**: Budget drift warning (70%/90% thresholds)
3. **T1 Ethical Screening (§8.1)**: Sensitive direction interception
4. **T1 External Resources Management (§10.1-10.2)**: External resource management
5. **Iteration Deadlock Detection (Phase 2.3)**: Prevents infinite loops

All features have corresponding unit tests in `tests/unit/test_robustness_enhancements.py`.

## 5-Minute Quick Start

All commands are executed from the **repository root** by default.

### Path A: Docker Mode (Recommended)

**Use case**: Production deployment, paper reproduction, quick demo

Docker mode uses **unified image** that supports:
- T5/T7 experiment execution
- T9 TeX compilation and PDF generation

```bash
# 1. Build image
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh

# 2. Set environment variables
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.example.com"

# 3. Initialize workspace
bash infra/docker/run.sh init-workspace --workspace /workspace

# 4. Run tasks
bash infra/docker/run.sh run-task T1 --workspace /workspace --topic "your research topic"
```

**Documentation**: [Docker Usage Guide](docs/docker-usage.md)

### Path B: Host Mode (Development/Debug)

**Use case**: Development, code modification

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
pip install -e '.[dev]'
python scripts/debug_hello_agent.py --mock --workspace ./workspace/demo_hello
```

### Path C: Initialize Standard Workspace

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "runtime smoke test"
```

## Environment Requirements

- Linux or compatible Unix-like environment
- Python 3.11
- Conda
- Optional: Docker (for experiment execution and TeX compilation)
- Optional: Real LLM provider API key
- Included in base requirements: `pdfplumber` for `extract_paper_sections`
- Optional: `PyMuPDF` for richer seed PDF metadata extraction

This repository does not depend on any author-specific absolute paths. Shared environment files:

- [environment.yml](./environment.yml)
- [requirements.txt](./requirements.txt)
- [requirements-optional-pdf.txt](./requirements-optional-pdf.txt)
- [requirements-dev.txt](./requirements-dev.txt)
- [requirements-llm.txt](./requirements-llm.txt)
- [pyproject.toml](./pyproject.toml)

## Docker Usage

### Unified Image Concept

ResearchOS uses **unified image** `researchos/system:latest`:

- **Image size**: 9.08GB
- **Contains**:
  - Python 3.11
  - ML dependencies (PyTorch, CUDA 12.4)
  - LaTeX environment (for T9 paper compilation)
  - MCP support
- **Purpose**: One image supports both T5/T7 experiment execution and T9 TeX compilation

### Build Image

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh
```

### Run Commands

```bash
# Basic run
bash infra/docker/run.sh [command]

# Example: initialize workspace
bash infra/docker/run.sh init-workspace --workspace /workspace

# Example: run T1
bash infra/docker/run.sh run-task T1 --workspace /workspace --topic "your topic"

# Example: full pipeline
bash infra/docker/run.sh run --workspace /workspace
```

### File Persistence

The `/workspace` directory inside Docker container mounts to host, ensuring results persist:

- All agent output files saved in workspace
- Trace and log files in workspace's `_runtime/` subdirectory
- Files remain after container exits

## Configuration

### Key Configuration Files

| File | Purpose | Active |
| --- | --- | --- |
| [config/model_routing.yaml](./config/model_routing.yaml) | endpoint, profile, model routing, context/truncation, rate limit | Yes |
| [config/state_machine.yaml](./config/state_machine.yaml) | workflow nodes, agents, I/O, success/failure transitions | Yes |
| [config/gates.yaml](./config/gates.yaml) | gate options and display content | Yes |
| [config/runtime.yaml](./config/runtime.yaml) | runtime shared defaults | Yes |
| [config/agent_params.yaml](./config/agent_params.yaml) | Agent params (model_tier, budget, timeout) | Reference |
| [config/mcp.example.yaml](./config/mcp.example.yaml) | MCP server config template | Template |

### Agent Parameters

[config/agent_params.yaml](./config/agent_params.yaml) centralizes all agent parameters:

- **Model selection**: model_tier (heavy/medium/light)
- **Budget limits**: max_steps, timeout, budget_hint
- **Output expectations**: expected_outputs, expected_sections
- **Docker/GPU requirements**: docker_required, gpu_required
- **Retry policy**: retry_policy

See [config/agent_params.yaml](./config/agent_params.yaml) for details.

### Environment Variables

- Copy [`.env.example`](./.env.example) to `.env` and fill in your variables.
- Variable names follow [config/model_routing.yaml](./config/model_routing.yaml).
- `search_papers` / `fetch_paper_metadata` can use `S2_API_KEY`.

## Repository Structure

```
ResearchOS/
|-- config/                 # Config: state machine / gates / model routing / runtime / MCP templates
|-- docs/                   # Detailed documentation
|-- infra/                  # Infrastructure
|   |-- docker/             # Docker build and run scripts
|-- researchos/
|   |-- agents/             # Agent classes and registry
|   |-- cli_runners/        # Complete pipeline / single task modes
|   |-- orchestration/      # state machine / gate presenter / task I/O contract
|   |-- prompts/            # Agent prompt templates
|   |-- runtime/            # AgentRunner, LLMClient, trace, logger, workspace helper
|   |-- schemas/            # state schema, artifact validator
|   |-- skills/             # skill loader / skill runner / tool aliases
|   |-- testing/            # MockLLMClient, MockHumanInterface, fixtures
|   `-- tools/              # builtin tools, MCP adapter, paper processing
|-- scripts/                # Debug scripts and dev utilities
|-- tests/
|   |-- unit/               # Unit tests (336)
|   |-- integration/        # Integration tests (removed, empty)
|   |-- e2e/                # E2E tests (removed, empty)
|   `-- real/              # Real tests (113)
|-- workspace/              # Default workspace directory
|-- environment.yml
|-- requirements.txt
|-- requirements-dev.txt
|-- requirements-llm.txt
`-- pyproject.toml
```

## Running

Two invocation styles after installation:

- `researchos ...`
- `python -m researchos.cli ...`

### 1. Initialize Workspace

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "test topic"
```

### 2. Full Pipeline Mode

```bash
researchos run --workspace ./workspace/demo
```

### 3. Resume Paused Pipeline

```bash
researchos resume --workspace ./workspace/demo
```

### 4. Single Task Debug Mode

```bash
researchos run-task HELLO --workspace ./workspace/demo
```

Available tasks: `HELLO`, `T1`, `T1.5`, `T2`, `T3`, `T3.5`, `T4`, `T4.5`, `T5`, `T6`, `T7`, `T8-WRITE`, `T8-DRAFT`, `T8-REVIEW-1`, `T8-REVISE-1`, `T8-REVIEW-2`, `T8-REVISE-2`, `T9`.

### 5. Agent Usage Examples

#### T1 PI Agent

```bash
researchos run-task T1 --workspace ./workspace/my-research --topic "discrete diffusion language models"
```

#### T2 Scout Agent

```bash
researchos run-task T2 --workspace ./workspace/my-research
```

#### T3/T3.5 Reader Agent

```bash
researchos run-task T3 --workspace ./workspace/my-research
researchos run-task T3.5 --workspace ./workspace/my-research
```

#### T4 Ideation Agent

```bash
researchos run-task T4 --workspace ./workspace/my-research
```

Note: T4 requires human interaction (two-round Gate).

#### T5/T7 Experimenter Agent

```bash
# T5: pilot experiment
researchos run-task T5 --workspace ./workspace/my-research

# T7: full experiment
researchos run-task T7 --workspace ./workspace/my-research
```

Note: requires Docker and possibly GPU.

#### T9 Submission Agent

```bash
researchos run-task T9 --workspace ./workspace/my-research
```

Note: requires Docker (for TeX compilation).

### 6. Status, Trace, Artifact Validation

```bash
researchos status --workspace ./workspace/demo
researchos trace hello_debug_run --workspace ./workspace/demo
researchos validate --workspace ./workspace/demo --task HELLO
researchos validate-config
```

## Testing

### Full Test Suite

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
python -m pytest -q
```

### Targeted Tests

```bash
# Unit tests (336)
python -m pytest -q tests/unit/

# Real tests (113)
python -m pytest -q tests/real/

# Specific files
python -m pytest -q tests/unit/test_cli_runners.py
```

### Bytecode Compilation Check

```bash
python -m compileall researchos scripts
```

## Debugging & Troubleshooting

Recommended order:

1. `researchos selftest`
2. `python scripts/debug_hello_agent.py --mock --workspace ...`
3. `researchos run-task HELLO --workspace ...`
4. `researchos trace <run_id> --workspace ...`
5. `researchos validate --workspace ... --task ...`
6. `researchos validate-config`
7. `python -m pytest -q`
8. `python -m compileall researchos scripts`

Key locations for troubleshooting:

- `workspace/state.yaml`
- `workspace/<runtime_dir>/traces/*.jsonl`
- `workspace/<runtime_dir>/logs/researchos.log`
- `project.yaml`

## Known Limitations

- Default [config/state_machine.yaml](./config/state_machine.yaml) has `initial_state: HELLO`, change to `T1` for full flow.
- Docker experiments (T5/T7) and TeX compilation (T9) require GPU environment.
- MCP runtime interface ready but no default `config/mcp.yaml` and connector provided.
- `extract_paper_sections` integrated but requires `pdfplumber` which is not installed by default.
- No formal `new-agent` scaffold command; new agents require manual file modifications.

## Test Statistics

**Total tests: 449**

| Type | Directory | Count |
|------|-----------|-------|
| Unit tests | `tests/unit/` | 336 |
| Real tests | `tests/real/` | 113 |
| Integration tests | `tests/integration/` | Removed (was empty) |
| E2E tests | `tests/e2e/` | Removed (was empty) |

All tests pass.

## Next Steps

- Enable full T1-T9 workflow (change `initial_state` from `HELLO` to `T1`)
- E2E test critical agents (T4/T5/T8) with real LLM
- Provide built-in MCP connector or recommended connector package
- Continue improving `config/runtime.yaml` as runtime configuration center

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md) - Get started in 5 minutes
- [Docker Usage Guide](docs/docker-usage.md) - Detailed Docker mode usage
- [Configuration Guide](docs/configuration.md) - Configuration file reference
- [Agent Documentation](docs/agents/README.md) - Agent implementation details

## Contact

- GitHub Issues: https://github.com/MengkunLiang/DIG-ResearchOS/issues
- Documentation: [docs/](docs/)

---

**Maintainer**: ResearchOS Development Team  
**Last Updated**: 2026-04-23

For Chinese documentation, see [README.zh-CN.md](./README.zh-CN.md).
