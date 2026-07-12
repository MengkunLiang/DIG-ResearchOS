from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    return read_json(path)


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def read_yaml_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    return read_yaml(path)


def read_text(path: Path) -> str:
    if not path.exists() or path.stat().st_size <= 0:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_allowed_paths(path: Path) -> list[str]:
    text = read_text(path)
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def read_markdown_sections(path: Path) -> dict[str, str]:
    text = read_text(path)
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            current = match.group(2).strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}
