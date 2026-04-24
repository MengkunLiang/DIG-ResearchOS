from __future__ import annotations

"""脚本级环境初始化辅助。"""

import os
from pathlib import Path


def ensure_script_llm_env(repo_root: Path) -> None:
    """为开发脚本加载 `.env`，并确保至少存在一组可用的 LLM 凭据。"""

    _try_load_dotenv(repo_root / ".env")

    key_names = (
        "SILICONFLOW_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
    )
    if any(os.getenv(name) for name in key_names):
        return

    raise RuntimeError(
        "未检测到任何 LLM API 密钥。请先在项目根目录 `.env` 或当前 shell 中设置 "
        "SILICONFLOW_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY。"
    )


def _try_load_dotenv(env_path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    if env_path.exists():
        load_dotenv(env_path, override=False)
