# ResearchOS Runtime

## Project Overview

ResearchOS is currently focused on delivering the **runtime infrastructure**, not a fully completed T1-T9 research agent product.

This repository provides the following core capabilities:

- `AgentRunner` main loop: handles message protocol, LLM calls, tool execution, finish validation, budget tracking, and tracing
- `StateMachine` orchestration layer: manages `state.yaml`, gates, resume, iteration, and task override
- `ToolRegistry` with workspace permissions: handles tool factory registration, per-task tool instance construction, and read/write boundary control
- `cli_runners`: supports both full pipeline mode and single task debugging mode
- `skills` runtime: supports `SKILL.md`, automatic skill tool discovery, and independent skill execution
- Runtime key tools: `search_papers`, `fetch_paper_metadata`, `docker_exec`, `latex_compile`, `extract_paper_sections`, `MCPTool` adapter layer
- Testing infrastructure: `MockLLMClient`, `MockHumanInterface`, runtime test doubles, pytest fixtures

Current implementation status:

- Implemented agents:
  - `HelloAgent`: debugging agent
  - `PIAgent` (T1/T7.5): project initialization and evaluation agent, supports init and evaluate modes
  - `ScoutAgent` (T2): literature retrieval agent, supports multi-source paper search
  - `ReaderAgent` (T3/T3.5): deep reading and literature synthesis agent, supports read and synthesize modes
  - `IdeationAgent` (T4): hypothesis generation agent, generates research hypotheses and experiment plans through two-round Gate interaction
  - `NoveltyAuditorAgent` (T4.5): novelty audit agent, evaluates novelty and feasibility of research hypotheses
  - `ExperimenterAgent` (T5/T7): experiment execution agent, T5 for pilot experiments, T7 for full experiments
  - `NoveltyAgent` (T6): final novelty verification agent, validates novelty based on pilot results
  - `WriterAgent` (T8-WRITE/T8-DRAFT/T8-REVISE-*): paper writing agent, supports outline, draft, self-check, and revision phases
  - `ReviewerAgent` (T8-REVIEW-*): paper review agent, supports multi-round reviews
  - `SubmissionAgent` (T9): submission preparation agent, handles template migration, anonymization checks, and compilation verification
  - See [researchos/agents/registry.py](./researchos/agents/registry.py)
- [config/state_machine.yaml](./config/state_machine.yaml) defines the complete T1-T9 workflow

### Robustness Enhancements

According to `ResearchOS_Agent_Dev_Spec_Addendum_Robustness.md`, the following robustness enhancements have been implemented:

1. **T4 Hypothesis Pre-mortem (§4.1)**: Adds counter-intuitive verification between Gate1 and Gate2
   - Performs three-dimensional checks on selected research directions: physical/mathematical constraints, known counterexamples, resource feasibility
   - Prompts user to reselect direction if High risk is found without mitigation
   - Implementation: `researchos/prompts/ideation.j2` (stage A.5)

2. **Runtime Budget Drift Warning (§7.1)**: Budget drift warning
   - Checks cumulative spending after each task
   - Logs warning when exceeding 70% of budget
   - Logs severe warning and writes warning file when exceeding 90% of budget
   - Implementation: `researchos/orchestration/state_machine.py` (`_check_budget_drift` method)

3. **T1 Ethical Screening (§8.1)**: Sensitive direction interception
   - Checks for sensitive keywords in T1's `validate_outputs`
   - Detects sensitive areas such as weapons, surveillance, manipulation, privacy invasion, discrimination
   - Returns warning and requires user confirmation if sensitive keywords detected
   - Implementation: `researchos/agents/pi.py` (`_check_ethical_concerns` method)

4. **T1 External Resources Management (§10.1-10.2)**: External resource management
   - Asks users about existing external resources in T1's three-round dialogue
   - Supports 7 resource types: dataset, baseline_repo, pretrained_model, docker_image, tool, script, other
   - Generates `user_seeds/seed_external_resources.jsonl` file
   - Validates resource format and source prefix
   - Implementation: `researchos/prompts/pi.j2` (round 2.5) and `researchos/agents/pi.py` (`_validate_external_resources` method)

5. **Iteration Deadlock Detection (Phase 2.3)**: Prevents infinite loops
   - Detects repeated execution of tasks with the same parameters
   - Automatically triggers error and fails fast when the same parameter combination is attempted more than 3 times
   - Records parameter hash, timestamp, and parameter content for each iteration in `state.yaml`
   - Implementation: `researchos/orchestration/state_machine.py` (`_check_iteration_deadlock` method)

All features have corresponding unit tests, see `tests/unit/test_robustness_enhancements.py`.

## 5-Minute Quick Start

All commands below are executed in the **repository root directory**.

### Path A: Docker Mode (Recommended, Zero Configuration)

**Use Case**: Production deployment, paper reproduction, quick experience

```bash
# 1. Build image
cd /path/to/ResearchOS
bash infra/docker/build.sh

# 2. Set environment variables
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"

# 3. Run
bash infra/docker/run.sh --help
bash infra/docker/run.sh init-workspace --workspace /workspace
bash infra/docker/run.sh run --workspace /workspace
```

**Detailed Documentation**: [Docker Usage Guide](docs/docker-usage.md)

### Path B: Host Mode (Development and Debugging)

**Use Case**: Development debugging, code modification

```bash
cd ResearchOS
conda env create -f environment.yml || conda env update -f environment.yml --prune
conda activate researchos
pip install -e '.[dev]'
python scripts/debug_hello_agent.py --mock --workspace ./workspace/demo_hello
```

### Path C: Initialize a Standard Workspace

```bash
cd ResearchOS
conda activate researchos
researchos init-workspace --workspace ./workspace/demo --project-id demo-project --topic "runtime smoke test"
```

If you haven't installed the console script, you can also use:

```bash
python -m researchos.cli init-workspace --workspace ./workspace/demo --project-id demo-project
```

### Path D: View CLI Capabilities and Current Runtime Status

```bash
cd ResearchOS
conda activate researchos
researchos --help
researchos run-task --help
researchos selftest
```

## Environment Requirements

- Linux or compatible Unix-like environment
- Python 3.11
- Conda
- Optional: Docker
- Optional: Real LLM provider API key
- Optional: `pdfplumber` for `extract_paper_sections`

This repository no longer depends on any author's local absolute paths. The shared environment is based on these files in the repository:

- [environment.yml](./environment.yml)
- [requirements.txt](./requirements.txt)
- [requirements-dev.txt](./requirements-dev.txt)
- [requirements-llm.txt](./requirements-llm.txt)
- [pyproject.toml](./pyproject.toml)

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md) - Get started in 5 minutes
- [Docker Usage Guide](docs/docker-usage.md) - Detailed Docker mode usage
- [Configuration Guide](docs/configuration.md) - Configuration file reference
- [Agent Documentation](docs/agents/README.md) - Agent implementation details
- [Troubleshooting Guide](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [Development Guide](docs/AGENT_DEVELOPMENT_GUIDE.md) - How to develop new agents

## Architecture

ResearchOS uses a unified Docker environment architecture:

- **Docker Mode**: Zero-configuration deployment, fully reproducible
- **Host Mode**: Direct debugging, fast iteration
- **Container Detection**: Automatically detects container environment and adapts execution mode

All agents run in a unified Docker environment, automatically detecting the container environment and adapting the execution mode.

## Workflow

```
T1 (PI Agent - init)
  ↓ project config, seed data
T2 (Scout Agent)
  ↓ paper retrieval, deduplication
T3 (Reader Agent - read)
  ↓ paper notes
T3.5 (Reader Agent - synthesize)
  ↓ literature review
T4 (Ideation Agent)
  ↓ research hypotheses, experiment plan
T4.5 (NoveltyAuditor Agent)
  ↓ novelty pre-audit
T5 (Experimenter Agent - pilot)
  ↓ pilot experiment results
T6 (Novelty Agent)
  ↓ final novelty verification
T7 (Experimenter Agent - full)
  ↓ full experiment results
T7.5 (PI Agent - evaluate)
  ↓ evaluation decision
T8 (Writer + Reviewer Agents)
  ↓ paper draft
T9 (Submission Agent)
  ↓ submission package
```

## Known Limitations

1. **MCP Integration**: T2 Scout Agent's MCP tools are currently commented out, waiting for MCP configuration completion
2. **T8/T9**: Writer/Reviewer Agent and Submission Agent code not yet implemented, only design documents available
3. **Container Environment**: All agents run in a unified Docker environment, automatically detecting container environment and adapting execution mode

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## License

See [LICENSE](LICENSE) for details.

## Contact

- GitHub Issues: https://github.com/MengkunLiang/DIG-ResearchOS/issues
- Documentation: [docs/](docs/)

---

**Maintainer**: ResearchOS Development Team  
**Last Updated**: 2026-04-21

For Chinese documentation, see [README.zh-CN.md](./README.zh-CN.md).
