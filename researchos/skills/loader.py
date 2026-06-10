from __future__ import annotations

"""Skill 包加载与发现逻辑。"""

from dataclasses import dataclass, field
import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import yaml

from ..runtime.errors import ConfigurationError
from ..tools.base import Tool
from ..tools.registry import ToolRegistry


@dataclass
class Skill:
    """一个可运行 skill 的内存表示。"""

    name: str
    description: str
    body: str
    allowed_tools: list[str]
    skill_dir: Path
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """把 SKILL.md 拆成 frontmatter 和正文。"""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise ConfigurationError("SKILL.md frontmatter is not closed with '---'")
    raw_meta, body = parts
    meta = yaml.safe_load(raw_meta.removeprefix("---\n")) or {}
    if not isinstance(meta, dict):
        raise ConfigurationError("SKILL.md frontmatter must be a YAML object")
    return meta, body


def load_skill(skill_dir: Path) -> Skill:
    """从 skill 目录读取 Skill 对象。"""
    if not skill_dir.is_dir():
        raise ConfigurationError(f"Skill dir not found: {skill_dir}")
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ConfigurationError(f"SKILL.md missing in {skill_dir}")
    meta, body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = meta.get("name") or skill_dir.name
    tools = meta.get("tools")
    if tools is None:
        # 支持 hyphen (allowed-tools) 和 underscore (allowed_tools) 两种格式
        tools = meta.get("allowed-tools")
        if tools is None:
            tools = meta.get("allowed_tools", [])
    # 支持逗号分隔的字符串格式（如 "Bash(*), Read, Write"）
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    if not isinstance(tools, list):
        raise ConfigurationError(f"Skill tools must be a list: {skill_md}")
    return Skill(
        name=name,
        description=meta.get("description", ""),
        body=body.strip(),
        allowed_tools=[str(item) for item in tools],
        skill_dir=skill_dir,
        metadata=meta,
    )


def discover_skills(skills_root: Path) -> dict[str, Skill]:
    """扫描一个 skills 根目录。"""
    if not skills_root.exists():
        return {}
    discovered: dict[str, Skill] = {}
    for child in sorted(skills_root.iterdir()):
        if not child.is_dir() or not (child / "SKILL.md").exists():
            continue
        skill = load_skill(child)
        if skill.name in discovered:
            raise ConfigurationError(f"Duplicate skill name '{skill.name}' in {skills_root}")
        discovered[skill.name] = skill
    return discovered


def discover_skills_from_roots(skill_roots: Iterable[Path]) -> dict[str, Skill]:
    """把多个 roots 合并成一个 skill 名称映射。"""
    merged: dict[str, Skill] = {}
    for root in skill_roots:
        for name, skill in discover_skills(root).items():
            if name in merged:
                raise ConfigurationError(
                    f"Duplicate skill name '{name}' found in {merged[name].skill_dir} and {skill.skill_dir}"
                )
            merged[name] = skill
    return merged


def register_skill_tools(
    registry: ToolRegistry,
    skill_roots: Iterable[Path],
    *,
    discovered_skills: dict[str, Skill] | None = None,
) -> None:
    """自动注册 `skills/*/tools/*.py` 中导出的 TOOL 实例。"""
    skills = discovered_skills if discovered_skills is not None else discover_skills_from_roots(skill_roots)
    for skill in skills.values():
        tools_dir = skill.skill_dir / "tools"
        if not tools_dir.is_dir():
            continue
        for py_file in sorted(tools_dir.glob("*.py")):
            module = _load_python_module(py_file)
            tool = getattr(module, "TOOL", None)
            if tool is None:
                continue
            if not isinstance(tool, Tool):
                raise ConfigurationError(f"{py_file} exports TOOL but it is not a Tool instance")
            if registry.has(tool.name):
                raise ConfigurationError(
                    f"Skill tool '{tool.name}' already registered. File: {py_file}"
                )
            registry.register_instance(tool)


def resolve_skill(specifier: str, skill_roots: Iterable[Path]) -> Skill:
    """按名称或路径解析 skill。

    支持两种形式：
    - `research-lit`：在 roots 中按名称查找
    - `/abs/path/to/skill` 或 `./skills/research-lit`：直接按路径读取
    """
    candidate = Path(specifier)
    if candidate.exists():
        return load_skill(candidate.resolve())
    discovered = discover_skills_from_roots(skill_roots)
    if specifier not in discovered:
        raise ConfigurationError(f"Skill '{specifier}' not found in roots: {list(skill_roots)}")
    return discovered[specifier]


def _load_python_module(py_file: Path) -> ModuleType:
    """用唯一路径 hash 生成模块名，避免不同 skill 的同名模块冲突。"""
    digest = hashlib.sha1(str(py_file.resolve()).encode("utf-8")).hexdigest()[:12]
    module_name = f"researchos_skill_tool_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec is None or spec.loader is None:
        raise ConfigurationError(f"Cannot load Python module from {py_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
