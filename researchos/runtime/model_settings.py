from __future__ import annotations

"""The single user-maintained model connection.

``config/model_settings.yaml`` is deliberately small.  It contains the one
provider connection used by every Agent and Skill, plus the retry behaviour a
researcher may reasonably want to adjust.  Workflow topology, prompt policy,
context fallback, and other runtime internals remain in ``system_config``.
"""

from collections.abc import Mapping
import os
from pathlib import Path
import re
from typing import Any

import yaml

from .system_config import REPO_ROOT, config_file_path, system_config_path


DEFAULT_MODEL_SETTINGS_PATH = REPO_ROOT / "config" / "model_settings.yaml"
LEGACY_USER_SETTINGS_PATH = REPO_ROOT / "config" / "user_settings.yaml"
DEFAULT_LLM_RUNTIME_PATH = system_config_path("llm_runtime.yaml")
MODEL_FIELDS = ("provider", "api_base", "api_key", "model")
PROVIDER_DEFAULTS = {
    # Native LiteLLM providers.
    "openai": {"runtime_provider": "openai", "api_base": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "openrouter": {"runtime_provider": "openrouter", "api_base": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "anthropic": {"runtime_provider": "anthropic", "api_base": "", "api_key_env": "ANTHROPIC_API_KEY"},
    # Hosted APIs exposing an OpenAI-compatible chat-completions endpoint.
    # Keeping one internal adapter preserves the one-provider/one-model
    # contract while still supporting the providers researchers actually use.
    "deepseek": {"runtime_provider": "openai", "api_base": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
    "siliconflow": {"runtime_provider": "openai", "api_base": "https://api.siliconflow.cn/v1", "api_key_env": "SILICONFLOW_API_KEY"},
    "google": {"runtime_provider": "openai", "api_base": "https://generativelanguage.googleapis.com/v1beta/openai/", "api_key_env": "GEMINI_API_KEY"},
    "groq": {"runtime_provider": "openai", "api_base": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "together": {"runtime_provider": "openai", "api_base": "https://api.together.xyz/v1", "api_key_env": "TOGETHER_API_KEY"},
    "fireworks": {"runtime_provider": "openai", "api_base": "https://api.fireworks.ai/inference/v1", "api_key_env": "FIREWORKS_API_KEY"},
    "mistral": {"runtime_provider": "openai", "api_base": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    "cohere": {"runtime_provider": "openai", "api_base": "https://api.cohere.ai/compatibility/v1", "api_key_env": "COHERE_API_KEY"},
    "xai": {"runtime_provider": "openai", "api_base": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY"},
    "perplexity": {"runtime_provider": "openai", "api_base": "https://api.perplexity.ai", "api_key_env": "PERPLEXITY_API_KEY"},
    "cerebras": {"runtime_provider": "openai", "api_base": "https://api.cerebras.ai/v1", "api_key_env": "CEREBRAS_API_KEY"},
    "nvidia_nim": {"runtime_provider": "openai", "api_base": "https://integrate.api.nvidia.com/v1", "api_key_env": "NVIDIA_API_KEY"},
    "moonshot": {"runtime_provider": "openai", "api_base": "https://api.moonshot.cn/v1", "api_key_env": "MOONSHOT_API_KEY"},
    "zhipu": {"runtime_provider": "openai", "api_base": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPUAI_API_KEY"},
    "qwen": {"runtime_provider": "openai", "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "DASHSCOPE_API_KEY"},
    "minimax": {"runtime_provider": "openai", "api_base": "https://api.minimax.chat/v1", "api_key_env": "MINIMAX_API_KEY"},
    # Local OpenAI-compatible runtimes normally do not require a credential.
    "ollama": {"runtime_provider": "openai", "api_base": "http://localhost:11434/v1", "api_key_env": "OLLAMA_API_KEY", "api_key_required": False},
    "lm_studio": {"runtime_provider": "openai", "api_base": "http://localhost:1234/v1", "api_key_env": "LM_STUDIO_API_KEY", "api_key_required": False},
    "vllm": {"runtime_provider": "openai", "api_base": "http://localhost:8000/v1", "api_key_env": "VLLM_API_KEY", "api_key_required": False},
    # Use this for a provider or gateway not listed above. Its URL is required.
    "openai_compatible": {"runtime_provider": "openai", "api_base": "", "api_key_env": "RESEARCHOS_API_KEY"},
}
_PROVIDER_ALIASES = {
    "compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "gemini": "google",
    "google_gemini": "google",
    "grok": "xai",
    "together_ai": "together",
    "fireworks_ai": "fireworks",
    "nvidia": "nvidia_nim",
    "nim": "nvidia_nim",
    "kimi": "moonshot",
    "zhipuai": "zhipu",
    "bigmodel": "zhipu",
    "alibaba": "qwen",
    "dashscope": "qwen",
    "lmstudio": "lm_studio",
    "localai": "openai_compatible",
}
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_model_settings_path(default_path: Path | None = None) -> Path:
    """Return the local model-settings path, with narrow legacy compatibility."""

    for env_name in ("RESEARCHOS_MODEL_SETTINGS", "RESEARCHOS_CONFIG", "RESEARCHOS_USER_SETTINGS"):
        if env_name not in os.environ:
            continue
        value = os.environ.get(env_name, "").strip()
        if not value:
            return Path("__researchos_model_settings_disabled__")
        return Path(value)
    return default_path or DEFAULT_MODEL_SETTINGS_PATH


def load_dotenv_for_model_settings(settings_path: Path | None = None) -> Path | None:
    """Load a project ``.env`` without overriding explicit shell variables.

    This is intentionally explicit rather than an invisible side effect of a
    provider SDK.  The caller can use the returned path for non-secret status
    output.  ``.env`` is only a convenient secret store; direct values in
    ``model_settings.yaml`` continue to work.
    """

    explicit = os.getenv("RESEARCHOS_DOTENV_PATH", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    path = settings_path or resolve_model_settings_path()
    if path.name != "__researchos_model_settings_disabled__":
        candidates.append(path.parent.parent / ".env")
    candidates.extend((Path.cwd() / ".env", REPO_ROOT / ".env"))

    seen: set[Path] = set()
    loaded: Path | None = None
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate in seen or not candidate.exists() or not candidate.is_file():
            continue
        seen.add(candidate)
        for raw_line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
        loaded = loaded or candidate
    return loaded


def load_model_settings(path: Path | None = None) -> dict[str, Any]:
    """Load, normalize, and expand the public model settings.

    ``api_key`` may be a direct key or a placeholder such as
    ``${DEEPSEEK_API_KEY}``.  A blank key still permits the conventional
    provider environment variable as a fallback.
    """

    settings_path = resolve_model_settings_path(path)
    dotenv_path = load_dotenv_for_model_settings(settings_path)
    raw: dict[str, Any] = {}
    if settings_path.exists():
        try:
            parsed = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            parsed = {}
        if isinstance(parsed, dict):
            raw = parsed
    elif settings_path == DEFAULT_MODEL_SETTINGS_PATH and LEGACY_USER_SETTINGS_PATH.exists():
        # A user upgrading an existing checkout should not lose a working
        # connection merely because the public filename changed. The next
        # ``configure-llm`` run writes the new local file.
        try:
            parsed = yaml.safe_load(LEGACY_USER_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            parsed = {}
        if isinstance(parsed, dict):
            raw = parsed

    # ``llm`` was the prior compact format. ``connection`` is accepted for a
    # short transition period; new files use simple root-level fields.
    nested = raw.get("connection") if isinstance(raw.get("connection"), dict) else raw.get("llm")
    connection = dict(nested) if isinstance(nested, dict) else dict(raw)
    provider = normalize_provider(connection.get("provider"))
    provider_defaults = PROVIDER_DEFAULTS[provider]
    fallback = connection.get("fallback") if isinstance(connection.get("fallback"), dict) else {}
    configured_api_base = _expand_env_value(str(connection.get("api_base") or "").strip())
    settings = {
        "provider": provider,
        "runtime_provider": provider_defaults["runtime_provider"],
        # Known providers use their official endpoint unless the researcher
        # explicitly supplies a relay. Only ``openai_compatible`` has no safe
        # default and therefore requires a URL from the user.
        "api_base": configured_api_base or str(provider_defaults["api_base"]),
        "api_key": _expand_env_value(str(connection.get("api_key") or "").strip()),
        "model": _expand_env_value(str(connection.get("model") or "").strip()),
        "fallback": {
            "max_attempts": _positive_int(fallback.get("max_attempts"), 3, minimum=1, maximum=10),
            "initial_wait_seconds": _positive_float(fallback.get("initial_wait_seconds"), 3.0, minimum=0.0, maximum=60.0),
            "max_wait_seconds": _positive_float(fallback.get("max_wait_seconds"), 20.0, minimum=0.0, maximum=300.0),
            "retry_after_timeout": _as_bool(fallback.get("retry_after_timeout"), True),
        },
        "api_key_env": str(provider_defaults["api_key_env"]),
        "api_key_required": bool(provider_defaults.get("api_key_required", True)),
        "dotenv_path": str(dotenv_path) if dotenv_path else "",
        "settings_path": str(settings_path),
    }
    return settings


def inspect_model_settings_source(path: Path | None = None) -> dict[str, Any]:
    """Read declared connection values without expanding credential references.

    Runtime loading deliberately expands ``${ENV_VAR}`` references. The setup
    wizard must instead know what was declared on disk so completing a missing
    model cannot replace a secure environment reference with a literal key.
    """

    settings_path = resolve_model_settings_path(path)
    source_path = settings_path
    raw: dict[str, Any] = {}
    if settings_path.exists():
        try:
            parsed = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            parsed = {}
        if isinstance(parsed, dict):
            raw = parsed
    elif settings_path == DEFAULT_MODEL_SETTINGS_PATH and LEGACY_USER_SETTINGS_PATH.exists():
        source_path = LEGACY_USER_SETTINGS_PATH
        try:
            parsed = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            parsed = {}
        if isinstance(parsed, dict):
            raw = parsed

    nested = raw.get("connection") if isinstance(raw.get("connection"), dict) else raw.get("llm")
    connection = dict(nested) if isinstance(nested, dict) else dict(raw)
    return {
        "settings_path": str(settings_path),
        "source_path": str(source_path),
        "source_exists": bool(raw),
        "provider": str(connection.get("provider") or "").strip(),
        "api_base": str(connection.get("api_base") or "").strip(),
        "api_key": str(connection.get("api_key") or "").strip(),
        "model": str(connection.get("model") or "").strip(),
    }


def load_llm_runtime_defaults(path: Path | None = None) -> dict[str, Any]:
    """Load internal defaults that users should not need to tune."""

    runtime_path = path or DEFAULT_LLM_RUNTIME_PATH
    raw: dict[str, Any] = {}
    if runtime_path.exists():
        try:
            loaded = yaml.safe_load(runtime_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            loaded = {}
        if isinstance(loaded, dict):
            raw = loaded
    context = _positive_int(raw.get("context_window_fallback"), 131_072, minimum=4_096, maximum=10_000_000)
    truncation = raw.get("truncation") if isinstance(raw.get("truncation"), dict) else {}
    return {
        "context_window_fallback": context,
        "truncation": {
            "trigger_ratio": _positive_float(truncation.get("trigger_ratio"), 0.90, minimum=0.01, maximum=0.99),
            "target_ratio": _positive_float(truncation.get("target_ratio"), 0.72, minimum=0.01, maximum=0.99),
        },
    }


def build_single_model_runtime_config(path: Path | None = None) -> dict[str, Any]:
    """Adapt the public one-connection file to the stable client internals."""

    connection = load_model_settings(path)
    settings_path = resolve_model_settings_path(path)
    local_runtime = settings_path.parent / "system_config" / "llm_runtime.yaml"
    defaults = load_llm_runtime_defaults(local_runtime if local_runtime.exists() else None)
    return {
        "_simple_llm_mode": True,
        "_simple_llm": connection,
        "default_profile": "default",
        "endpoints": {
            "default": {
                "provider": connection["runtime_provider"],
                "api_key": connection["api_key"] or None,
                "api_key_env": connection["api_key_env"],
                "api_base": connection["api_base"] or None,
            }
        },
        "profiles": {
            "default": {
                "standard": {
                    "primary": {
                        "model": connection["model"],
                        "endpoint": "default",
                        "max_context": defaults["context_window_fallback"],
                    }
                }
            }
        },
        "truncation": defaults["truncation"],
    }


def write_model_settings(
    *,
    provider: str,
    api_base: str,
    api_key: str,
    model: str,
    fallback: Mapping[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    """Write the single local user configuration with private permissions."""

    settings_path = resolve_model_settings_path(path)
    if settings_path.name == "__researchos_model_settings_disabled__":
        raise ValueError("RESEARCHOS_MODEL_SETTINGS/RESEARCHOS_CONFIG disables model settings")
    current = load_model_settings(settings_path)
    recovery = dict(current.get("fallback") or {})
    if fallback:
        recovery.update(dict(fallback))
    payload = {
        "provider": normalize_provider(provider),
        "api_base": str(api_base).strip(),
        "api_key": str(api_key).strip(),
        "model": str(model).strip(),
        "fallback": {
            "max_attempts": _positive_int(recovery.get("max_attempts"), 3, minimum=1, maximum=10),
            "initial_wait_seconds": _positive_float(recovery.get("initial_wait_seconds"), 3.0, minimum=0.0, maximum=60.0),
            "max_wait_seconds": _positive_float(recovery.get("max_wait_seconds"), 20.0, minimum=0.0, maximum=300.0),
            "retry_after_timeout": _as_bool(recovery.get("retry_after_timeout"), True),
        },
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        settings_path.chmod(0o600)
    except OSError:
        pass
    return settings_path


def write_api_key_to_dotenv(*, provider: str, api_key: str, settings_path: Path | None = None) -> tuple[Path, str]:
    """Store a key in the project ``.env`` and return its file and variable."""

    path = resolve_model_settings_path(settings_path)
    env_path = path.parent.parent / ".env"
    env_name = provider_api_key_env(provider)
    existing = env_path.read_text(encoding="utf-8", errors="replace") if env_path.exists() else ""
    lines = existing.splitlines()
    rendered = f"{env_name}={api_key.strip()}"
    matcher = re.compile(rf"^(?:export\s+)?{re.escape(env_name)}\s*=")
    replaced = False
    for index, line in enumerate(lines):
        if matcher.match(line.strip()):
            lines[index] = rendered
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(rendered)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    # The user explicitly replaced this key in the interactive setup. Keep the
    # current process aligned with the file so its immediate connection check
    # tests the value just written rather than a stale inherited variable.
    os.environ[env_name] = api_key.strip()
    return env_path, env_name


def provider_api_key_env(provider: str) -> str:
    return str(PROVIDER_DEFAULTS[normalize_provider(provider)]["api_key_env"])


def provider_default_api_base(provider: str) -> str:
    return str(PROVIDER_DEFAULTS[normalize_provider(provider)]["api_base"])


def provider_requires_api_base(provider: str) -> bool:
    """Return whether a provider has no safe default endpoint URL."""

    return normalize_provider(provider) == "openai_compatible"


def provider_requires_api_key(provider: str) -> bool:
    """Return whether a provider normally needs an API key to be usable."""

    return bool(PROVIDER_DEFAULTS[normalize_provider(provider)].get("api_key_required", True))


def supported_provider_names() -> tuple[str, ...]:
    """Return stable public provider names for CLI/help/documentation."""

    return tuple(PROVIDER_DEFAULTS)


def normalize_provider(value: Any) -> str:
    normalized = str(value or "openai_compatible").strip().casefold().replace("-", "_")
    normalized = _PROVIDER_ALIASES.get(normalized, normalized)
    if normalized in PROVIDER_DEFAULTS:
        return normalized
    raise ValueError(
        f"Unsupported provider {value!r}. Use one of: {', '.join(supported_provider_names())}, "
        "or choose openai_compatible and provide its API URL."
    )


def _expand_env_value(value: str) -> str:
    return _ENV_PLACEHOLDER.sub(lambda match: os.getenv(match.group(1), ""), value)


def _positive_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _positive_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}
