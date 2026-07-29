# Configuration

> [English](../en/config.md) | [中文](../cn/config.md)

ResearchOS has one user-facing LLM configuration. Every Agent and Skill uses the same provider and model. The system no longer exposes heavy/medium/light tiers, per-Agent model routing, token-budget tuning, or fallback model chains as ordinary setup work.

## Set Up the Model

Run this once from the repository root:

```bash
python -m researchos.cli configure-llm
```

The interactive flow asks for:

| Field | Meaning |
| --- | --- |
| `provider` | A named provider preset, or `openai_compatible` for another OpenAI-compatible relay. The preset list is below. |
| `api_base` | Optional URL override for a named provider; required only for `openai_compatible`. Known providers use their official default when it is blank. |
| `api_key` | Your provider credential. |
| `model` | The one model used throughout the workflow. |
| `fallback` | Per-request deadline and retry behaviour for the same provider/model after temporary failures. It is not an alternate-model route. |
| `context_window_fallback` | Total context capacity in tokens to use only when the provider cannot report the model's real capacity. Provider metadata takes priority. |
| `truncation` | History-compaction thresholds before the effective total capacity. Its optional `max_input_tokens` is only for a gateway that requires a stricter history cap. |
| `rate_limit` | Optional local TPM token bucket. It is disabled by default and is unrelated to model context capacity. |

The command writes the active file at `config/model_settings.yaml`, offers to store the key either in that local file or in `.env`, and sends a minimal request to check the connection. The file is ignored by Git and is written with owner-only permissions where the platform supports them. It also writes or preserves `context_window_fallback`, `truncation`, and the optional `rate_limit` block in that same file, so model connection and context settings never need a second configuration file.

A noninteractive setup is also available:

```bash
python -m researchos.cli configure-llm \
  --provider deepseek \
  --api-base https://api.deepseek.com \
  --api-key "$DEEPSEEK_API_KEY" \
  --model your-model-name \
  --key-storage env
```

## Provider Presets

`configure-llm` accepts the following public provider names. Aliases such as `gemini`, `grok`, `kimi`, `dashscope`, `lmstudio`, and `nvidia` resolve to the matching preset. Model names remain provider-owned values, so ResearchOS does not silently rewrite them.

| Group | Provider presets | Connection behaviour |
| --- | --- | --- |
| Primary APIs | `openai`, `anthropic`, `openrouter` | Official endpoint preset; `anthropic` uses its native adapter. |
| Global OpenAI-compatible APIs | `deepseek`, `siliconflow`, `google`, `groq`, `together`, `fireworks`, `mistral`, `cohere`, `xai`, `perplexity`, `cerebras`, `nvidia_nim` | Official OpenAI-compatible URL preset and provider-specific environment-variable name. |
| China-hosted APIs | `moonshot`, `zhipu`, `qwen`, `minimax` | Official compatible URL preset and provider-specific environment-variable name. |
| Local runtimes | `ollama`, `lm_studio`, `vllm` | Local URL preset; an API key is normally optional. |
| Other gateway | `openai_compatible` | Requires an explicit `api_base`; uses `RESEARCHOS_API_KEY` unless `api_key` is written directly. |

## Edit or Configure Interactively

When `run`, `resume`, `run-task`, or `run-skill` finds a missing connection, it stops before creating an Agent and shows a Rich setup card. A complete real `config/model_settings.yaml` skips this guide, so setup is never repeated unnecessarily. If only one field is missing, for example `model` while the provider, API URL, and API key already work, choosing setup asks only for `model`; an existing API-key environment reference is preserved and is never redisplayed, re-entered, or written back as a literal secret. When the provider changes, ResearchOS requests that provider's API key and model rather than carrying over the old provider's credential or model. The choices appear only when the file is missing, the example has not been copied, or a required field such as `provider`, `api_key`, or `model` is absent:

1. Configure now: enter the values in the terminal and immediately test them. A passing test returns to the original `run`/`resume`/`run-task`/`run-skill` invocation without issuing a duplicate startup request to the provider.
2. Edit `config/model_settings.yaml`: ResearchOS shows the exact active path, template path, required fields, and a copyable validation command; make the change, then let it reload that file and its matching `.env` before the ordinary startup check.
3. Exit: leave the workspace untouched.

This avoids a later failure halfway through T2, T3, or T4. A failed connection check keeps the saved settings so the user can correct the URL, key, provider, or model and retry; it is reported as an incomplete model-configuration step, never as a generic workspace/runtime outage, and the original command does not advance the workspace. API-key entry deliberately does not echo the secret. The confirmation shows a mask, character count, final-character check, and the saved location, so a hidden prompt never looks like a lost value.

## `model_settings.yaml`

The active output is `config/model_settings.yaml`. The nearby `config/model_settings.example.yaml` is a template only; it is never loaded by the runtime. Use it as the starting point:

```bash
cp config/model_settings.example.yaml config/model_settings.yaml
```

```yaml
provider: deepseek
api_base: https://api.deepseek.com
api_key: "${DEEPSEEK_API_KEY}"
model: your-model-name
context_window_fallback: 262144
truncation:
  trigger_ratio: 0.90
  target_ratio: 0.72
rate_limit:
  enabled: false
  tokens_per_minute: 200000
  burst: 200000
fallback:
  request_timeout_seconds: 120
  max_attempts: 3
  initial_wait_seconds: 3
  max_wait_seconds: 20
  retry_after_timeout: true
```

`api_key` accepts a direct value or an environment placeholder. A blank value still checks the conventional provider variable, such as `DEEPSEEK_API_KEY`. `.env` is loaded from the repository or current project without replacing values already supplied by the shell or Docker environment. `openai_compatible` must provide its exact `api_base`; known providers use their official endpoint when this field is blank. `model_settings.example.yaml` is only a template and is never loaded; only its sibling `model_settings.yaml` takes effect. With a custom target, use the same command after the subcommand: `python -m researchos.cli selftest --model-settings /absolute/path/model_settings.yaml`.

The `fallback` settings govern one connection only. `request_timeout_seconds` is the deadline for every normal research-model request; temporary timeouts and overloads then retry according to `max_attempts`, `initial_wait_seconds`, `max_wait_seconds`, and `retry_after_timeout`. Authentication failures and invalid URLs are reported immediately because retries cannot repair configuration. If recovery is exhausted, the runtime preserves the workspace and offers the normal retry/wait/pause decision instead of silently switching models. After all provider attempts finish, SDK/HTTP cleanup is allowed up to the smaller of 60 seconds and half the request deadline (60 seconds for the default 120-second call). This cleanup deadline is internal rather than a second setting: it is bounded solely to prevent a broken client transport from waiting forever.

## Context Capacity Fallback

`context_window_fallback: 262144` is a field in the same active `config/model_settings.yaml` file as the provider, URL, key, and model. It is used only when the configured provider/model does not expose a verifiable real context window through its model metadata. When the provider reports a matched real capacity, that capacity takes precedence. Manually configured fallback values support 4,096 through 100,000,000 tokens, the same plausibility range used for provider metadata.

The value is a total context-capacity estimate in tokens. It is shared by the system prompt, research material, conversation history, Tool calls and results, and room reserved for the model response. It is therefore not a raw user-input limit, not a fixed file-read size, and not a statement of the provider's public API limit. The runtime derives file pages, context compaction, and abstract batching from the effective capacity. Researchers normally keep this default; maintainers should change the fallback only for a provider or gateway that cannot report its capacity and whose total context capacity is known.

By default, `truncation` follows the provider-reported context capacity or `context_window_fallback`; you only maintain that one capacity value. The optional positive `truncation.max_input_tokens` is an advanced compatibility override for a gateway that requires a smaller retained history input despite reporting a larger context. Its effective limit is the smaller of that override and the provider/fallback capacity. It only removes older conversation and Tool turns, never PDF page extraction, paper-note coverage, or stored evidence.

`rate_limit` is intentionally disabled by default. Account TPM/RPM quotas are provider-, model-, plan-, and gateway-specific, so ResearchOS does not guess them or derive them from `context_window_fallback`. With the default `enabled: false`, requests go directly to the provider and normal provider rate-limit responses use the existing retry/recovery flow. Enable it only when the actual token-per-minute quota is known and local concurrency should be smoothed. `burst` must be at least as large as the largest request you intend to admit. An oversized request is sent to the provider rather than waiting forever in a local bucket.

## MCP Tools

`config/mcp.yaml` is the single optional MCP configuration. A configured stdio server starts automatically when ResearchOS starts, its Tool schemas are discovered, and the connection remains open for the run. No `--mcp-connector` is needed for normal stdio servers. Add `allowed_agents` only when a server should be restricted; otherwise its discovered Tools are available to all Agents and Skills.

```yaml
servers:
  - name: github
    enabled: true
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    allowed_agents: ["scout", "experimenter", "reviewer"]
```

Use `.env` or the shell for an MCP server's credentials. The GitHub preset passes the general `GITHUB_TOKEN` to the server's required `GITHUB_PERSONAL_ACCESS_TOKEN` variable. A preset with `enabled: false` does not read its environment variables; after it is enabled, a missing `${ENV_VAR}` is reported before the server is spawned. Built-in `arxiv_search`, `openalex_search`, `semantic_scholar_search`, and `fetch_paper_pdf` already cover paper discovery and PDF acquisition, so no arXiv MCP server is required. `--mcp-connector` is retained only for a custom non-stdio transport.

## Configuration Ownership

| Path | Owner | Use |
| --- | --- | --- |
| `config/model_settings.yaml` | Researcher | The only model/provider, retry, context-capacity, and compaction configuration. Local and ignored by Git. |
| `config/mcp.yaml` | Researcher, optional | Additional MCP stdio servers, discovered automatically at startup. Built-in paper and PDF tools do not depend on it. |
| `config/system_config/runtime.yaml` | Runtime | Workspace, logging, UI, web-fetch, and LaTeX/Docker defaults. |
| `config/system_config/agent_params.yaml` | Runtime | Agent capabilities, Tool permissions, prompts, and mechanical reading behaviour. |
| `config/system_config/state_machine.yaml` and related files | Runtime | Workflow topology, gates, schemas, and writing profiles. |

The system files are versioned contracts. They are not a second configuration interface for choosing models or changing routine execution limits. `venue_writing_profiles.yaml` now contains venue aliases, internal style hints, and template suggestions in one place; there is no separate `venue_style_map.yaml` that can drift from it.

## Validation and Docker

```bash
python -m researchos.cli selftest
python -m researchos.cli validate-config
python -m researchos.cli doctor
```

`selftest` checks the current LLM connection and local dependencies. `validate-config` checks the configuration tree without printing secrets. Docker mounts `config/` read-only, so run `configure-llm` on the host before invoking `deploy/researchos.sh` or `researchos.ps1`.

Existing deployments with the former `user_settings.yaml` or legacy endpoint/profile routing remain readable during migration. New configuration is always written to `model_settings.yaml` and does not use the old files.
