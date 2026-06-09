"""9个agent共享的helper函数，避免重复实现。

参考：Agent Dev Spec §1.2
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..literature_identity import is_placeholder_text, is_workspace_guide_or_template
from ..runtime.system_config import system_config_path
from ..tools.seed_outline import (
    build_seed_outline_profile,
    looks_like_seed_outline,
    _merge_external_resources,
    _merge_markdown_seed_file,
    _seed_constraints_markdown,
    _seed_ideas_markdown,
)

if TYPE_CHECKING:
    from ..runtime.agent import ExecutionContext


# ══════════════════════════════════════════════════════
# 1. Artifact 读取 helper
# ══════════════════════════════════════════════════════

def load_project(ctx: "ExecutionContext") -> dict:
    """读 workspace/project.yaml，所有agent都用。"""
    project_path = ctx.workspace_dir / "project.yaml"
    if not project_path.exists():
        return {}
    return yaml.safe_load(project_path.read_text(encoding="utf-8"))


def repo_root() -> Path:
    """Return the repository root for reading versioned config files."""

    return Path(__file__).resolve().parents[2]


def load_cdr_schema() -> dict:
    """Load the shared CDR schema used by Pre-T5 and manuscript agents."""

    schema_path = system_config_path("cdr_schema.yaml")
    if not schema_path.exists():
        return {}
    return yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}


def cdr_schema_prompt_summary() -> str:
    """Compact schema summary for prompts without embedding long docs."""

    schema = load_cdr_schema()
    fields = schema.get("design_tuple_fields") or {}
    origins = schema.get("idea_origins") or {}
    contribution = fields.get("contribution_type") or {}
    responsibilities = schema.get("section_cdr_responsibilities") or {}
    lines = [
        "## Shared CDR Schema",
        "",
        f"- semantics: {schema.get('semantics', 'not loaded')}",
        "- design_tuple fields:",
    ]
    for name, meta in fields.items():
        description = meta.get("description", "") if isinstance(meta, dict) else ""
        lines.append(f"  - `{name}`: {description}")
    if contribution.get("enum"):
        lines.append(f"- contribution_type enum: {', '.join(contribution['enum'])}")
    if origins:
        lines.append(
            "- mainline idea origins: "
            + ", ".join(str(item) for item in origins.get("mainline", []))
        )
        lines.append(
            "- supplement idea origins: "
            + ", ".join(str(item) for item in origins.get("supplement", []))
        )
        if origins.get("bridge"):
            lines.append(
                "- bridge idea origins: "
                + ", ".join(str(item) for item in origins.get("bridge", []))
            )
    if responsibilities:
        lines.append("- manuscript section CDR responsibilities:")
        for section, responsibility in responsibilities.items():
            lines.append(f"  - `{section}`: {responsibility}")
    principles = schema.get("principles") or []
    if principles:
        lines.append("- principles:")
        for item in principles:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def load_jsonl(path: Path) -> list[dict]:
    """读JSONL格式artifact（papers_raw, papers_dedup等）。"""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    results = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            # 清理无效控制字符（如\x00）
            cleaned_line = line.replace('\x00', '')
            obj = json.loads(cleaned_line)

            # 处理错误格式：如果整行是一个空数组[]，跳过
            # 这是因为有些LLM可能生成JSON数组而不是JSONL格式
            if isinstance(obj, list) and len(obj) == 0:
                continue

            # 如果是字典，正常添加
            if isinstance(obj, dict):
                results.append(obj)
            else:
                print(f"Warning: Line {i} is not a dict, skipping: {type(obj)}")
                continue
        except json.JSONDecodeError as e:
            # 记录错误但继续处理其他行
            print(f"Warning: Failed to parse line {i}: {e}")
            continue
    return results


def normalize_text_key(value: str) -> str:
    """把标题/ID 归一化，便于跨来源做弱匹配。"""
    if not value:
        return ""
    normalized = value.casefold()
    normalized = " ".join(normalized.split())
    return normalized


def append_jsonl(path: Path, records: list[dict]) -> None:
    """追加到JSONL（agent产出期间用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, records: list[dict]) -> None:
    """覆盖写入JSONL。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════
# 2. 标准 validate_outputs helper
# ══════════════════════════════════════════════════════

def validate_files_exist(
    ctx: "ExecutionContext", required: list[str]
) -> tuple[bool, str | None]:
    """检查workspace中必需文件存在。返回(ok, err_msg)。"""
    missing = []
    for rel in required:
        p = ctx.workspace_dir / rel
        if not p.exists():
            missing.append(rel)
    if missing:
        return False, f"缺少必需产出: {missing}"
    return True, None


def validate_jsonl_schema(
    path: Path,
    schema_name: str,
    min_count: int = 0,
    max_count: int | None = None,
) -> tuple[bool, str | None]:
    """校验JSONL的每行符合schema + 数量约束。

    Args:
        path: JSONL文件路径
        schema_name: 对应schemas/{schema_name}.schema.json
        min_count: 最少记录数
        max_count: 最多记录数（None表示不限）

    Returns:
        (ok, err_msg)
    """
    from ..schemas.validator import validate_record

    records = load_jsonl(path)
    if len(records) < min_count:
        return False, f"{path.name} 只有 {len(records)} 条，至少需要 {min_count} 条"
    if max_count and len(records) > max_count:
        return False, f"{path.name} 有 {len(records)} 条，超过上限 {max_count}"

    for i, rec in enumerate(records):
        ok, err = validate_record(rec, schema_name)
        if not ok:
            return False, f"{path.name}:第 {i+1} 条不合schema: {err}"

    return True, None


# ══════════════════════════════════════════════════════
# 3. State.yaml 轻量读写（agent只读用）
# ══════════════════════════════════════════════════════

def read_state(ctx: "ExecutionContext") -> dict:
    """Agent读state.yaml。注意agent不写state，由StateMachine统一管。"""
    state_path = ctx.workspace_dir / "state.yaml"
    if not state_path.exists():
        return {}
    return yaml.safe_load(state_path.read_text(encoding="utf-8"))


def read_iteration_count(ctx: "ExecutionContext", key: str) -> int:
    """读iteration_count[key]，用于T5重做、T7多轮实验。"""
    state = read_state(ctx)
    return state.get("iteration_count", {}).get(key, 0)


# ══════════════════════════════════════════════════════
# 4. 其他常用helper
# ══════════════════════════════════════════════════════

def ensure_dir(path: Path) -> None:
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


def read_text_file(path: Path, default: str = "") -> str:
    """安全读取文本文件，不存在返回默认值。"""
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def write_text_file(path: Path, content: str) -> None:
    """写入文本文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_seed_outline_profile(workspace_dir: Path) -> dict | None:
    """Deterministically normalize a real Markdown seed outline if needed.

    This is a runtime safety net for T1/T2/T3.6 prompts. Agents are still told to
    call `normalize_seed_outline`, but a model miss should not make a user's
    substantial Chinese survey outline invisible to literature search/writing.
    Representative literature directions remain query/taxonomy priors only.
    """

    user_seeds = workspace_dir / "user_seeds"
    profile_path = user_seeds / "seed_outline_profile.json"
    if profile_path.exists():
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            outline_path = _profile_source_outline_path(data, workspace_dir)
            if outline_path is None:
                outline_path = find_seed_outline_markdown(user_seeds)
            if outline_path is None or not _seed_outline_profile_is_stale(data, outline_path, workspace_dir):
                _ensure_seed_outline_derived_files(user_seeds, data)
                return data

    outline_path = find_seed_outline_markdown(user_seeds)
    if outline_path is None:
        return None

    text = outline_path.read_text(encoding="utf-8", errors="replace")
    rel_path = outline_path.relative_to(workspace_dir).as_posix()
    profile = build_seed_outline_profile(text, source_path=rel_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _ensure_seed_outline_derived_files(user_seeds, profile)
    return profile


def _seed_outline_profile_is_stale(data: dict, outline_path: Path, workspace_dir: Path) -> bool:
    """Return true when the stored profile no longer matches the source outline."""

    try:
        rel_path = outline_path.relative_to(workspace_dir).as_posix()
    except ValueError:
        rel_path = outline_path.as_posix()
    source_path = str(data.get("source_path") or "")
    if source_path and source_path != rel_path:
        return True
    source_hash = str(data.get("source_sha256") or "")
    if not source_hash:
        return False
    text = outline_path.read_text(encoding="utf-8", errors="replace")
    current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return current_hash != source_hash


def _ensure_seed_outline_derived_files(user_seeds: Path, profile: dict) -> None:
    """Ensure seed-outline-derived helper files exist without duplicating them."""

    _merge_seed_outline_markdown(user_seeds / "seed_ideas.md", _seed_ideas_markdown(profile))
    _merge_seed_outline_markdown(user_seeds / "seed_constraints.md", _seed_constraints_markdown(profile))
    _merge_external_resources(user_seeds / "seed_external_resources.jsonl", profile.get("external_resources") or [])


def find_seed_outline_markdown(user_seeds_dir: Path) -> Path | None:
    """Find the first real Markdown seed outline under `user_seeds/`."""

    if not user_seeds_dir.exists():
        return None
    candidates: list[Path] = []
    for path in sorted(user_seeds_dir.glob("*.md")):
        if is_workspace_guide_or_template(path) or path.name.endswith(".example"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if is_placeholder_text(text):
            continue
        if "seed_outline_profile: derived" in text:
            continue
        if looks_like_seed_outline(text):
            candidates.append(path)
    return candidates[0] if candidates else None


def _profile_source_outline_path(data: dict, workspace_dir: Path) -> Path | None:
    source_path = str(data.get("source_path") or "").strip()
    if not source_path:
        return None
    path = (workspace_dir / source_path).resolve()
    if not path.exists() or path.suffix.lower() != ".md":
        return None
    try:
        path.relative_to(workspace_dir.resolve())
    except ValueError:
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if is_placeholder_text(text) or "seed_outline_profile: derived" in text:
        return None
    if not looks_like_seed_outline(text):
        return None
    return path


def _merge_seed_outline_markdown(path: Path, addition: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _merge_markdown_seed_file(path, addition, marker="seed_outline_profile")


def build_resume_prefix(ctx: "ExecutionContext") -> str:
    """为所有 Agent 生成统一的恢复提示前缀。

    设计原则：
    - 先让 Agent 看到“已有内容不要浪费”；
    - 再告诉它“还缺哪些声明产物”；
    - 最后明确要求增量补全，而不是默认整份重写。
    """

    if not ctx.extra.get("resume_mode"):
        return ""

    existing_outputs = list(ctx.extra.get("resume_existing_outputs", []))
    missing_outputs = list(ctx.extra.get("resume_missing_outputs", []))
    existing_artifacts = list(ctx.extra.get("resume_existing_artifacts", []))
    resume_state_path = str(ctx.extra.get("resume_state_path", "")).strip()
    resume_reason = str(ctx.extra.get("resume_reason", "")).strip()

    lines = [
        "[恢复运行] 这是一次续跑。必须先读取已有产物，在现有内容基础上增量完成，不要忽略之前已经做出的结果。",
    ]
    if resume_state_path:
        lines.append(f"- 恢复状态文件：`{resume_state_path}`")
    if resume_reason:
        lines.append(f"- 恢复原因：`{resume_reason}`")
    if existing_outputs:
        lines.append(f"- 已有输出：`{'`, `'.join(existing_outputs)}`")
    if missing_outputs:
        lines.append(f"- 待补输出：`{'`, `'.join(missing_outputs)}`")
    if existing_artifacts:
        lines.append(
            "- 已存在的可复用文件（节选）：`"
            + "`, `".join(existing_artifacts[:8])
            + "`"
        )

    lines.extend(
        [
            "- 先检查已有文件内容是否可复用；只有现有内容明显损坏、为空或与当前任务冲突时，才允许重写。",
            "- 对已存在且内容合理的文件，优先补充、修订或续写，不要为了“重来一遍”而覆盖已有工作。",
            "",
        ]
    )
    return "\n".join(lines)


def prepend_resume_prefix(ctx: "ExecutionContext", message: str) -> str:
    """把通用恢复提示拼到 Agent 的初始用户消息前面。"""

    prefix = build_resume_prefix(ctx)
    if not prefix:
        return message
    return prefix + "\n" + message


# ══════════════════════════════════════════════════════
# 5. Findings.md 模式（Token 优化）
# ══════════════════════════════════════════════════════

def generate_findings_summary(
    ctx: "ExecutionContext",
    findings: list[str],
    output_dir: str,
) -> Path:
    """生成 findings.md，关键发现持久化供后续 Agent 复用。

    来源: AI-Research-SKILLs - findings.md pattern
    Token 节省: ~200 tokens/agent（避免重复上下文）

    Args:
        ctx: ExecutionContext
        findings: 关键发现列表
        output_dir: Agent 输出目录（如 "ideation/"、"pilot/"）

    Returns:
        findings.md 的路径
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"""# Key Findings

生成时间: {timestamp}
Agent: {ctx.task_id}

## 关键发现

"""
    for i, finding in enumerate(findings, 1):
        content += f"{i}. {finding}\n"

    findings_path = ctx.workspace_dir / output_dir / "findings.md"
    write_text_file(findings_path, content)
    return findings_path


# ══════════════════════════════════════════════════════
# 6. Research Log 模式（Token 优化）
# ══════════════════════════════════════════════════════

def generate_research_log(
    ctx: "ExecutionContext",
    decision: str,
    rationale: str,
    metadata: dict | None = None,
) -> Path:
    """追加决策到 research-log.md，记录关键决策时间线。

    来源: AI-Research-SKILLs - research-log.md pattern
    Token 节省: ~150 tokens/agent（避免重复推理上下文）

    Args:
        ctx: ExecutionContext
        decision: 决策描述
        rationale: 决策原因
        metadata: 额外元数据（如 hypotheses_validated, risks 等）

    Returns:
        research-log.md 的路径
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = ctx.workspace_dir / "research-log.md"

    # 读取现有 log（如果存在）
    existing_content = ""
    if log_path.exists():
        existing_content = read_text_file(log_path)

    # 构建新条目
    new_entry = f"""
## {timestamp} {ctx.task_id}

- **决策**: {decision}
- **原因**: {rationale}
"""
    if metadata:
        for key, value in metadata.items():
            new_entry += f"- **{key}**: {value}\n"

    # 追加或创建
    if existing_content:
        # 如果已存在，找到最后一个 ## 块的位置，在其前插入
        last_two_headers = existing_content.rfind("\n## ")
        if last_two_headers > 0:
            content = existing_content[:last_two_headers] + new_entry + existing_content[last_two_headers:]
        else:
            content = existing_content + new_entry
    else:
        content = f"# Research Log\n\n本文件记录研究过程中的关键决策时间线。\n用于后续 Agent 复用决策上下文，避免重复推理。\n{new_entry}"

    write_text_file(log_path, content)
    return log_path


# ══════════════════════════════════════════════════════
# 7. Material Passport（来源：academic-research-skills）
# ══════════════════════════════════════════════════════

def generate_manifest(
    ctx: "ExecutionContext",
    output_dir: str,
    artifacts: list[dict],
    inputs: list[dict] | None = None,
) -> Path:
    """生成 Material Passport (manifest.yaml)，记录制品来源和元数据。

    来源: academic-research-skills - Material Passport
    用途: 跨会话追踪制品版本、依赖输入、时间戳

    Args:
        ctx: ExecutionContext
        output_dir: Agent 输出目录（如 "ideation/"、"pilot/"）
        artifacts: 产出的文件列表
            [{"path": "hypotheses.md", "type": "markdown"}, ...]
        inputs: 输入文件列表（可选）
            [{"path": "synthesis.md", "required": true}, ...]

    Returns:
        manifest.yaml 的路径
    """
    import hashlib

    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = {
        "manifest_version": "1.0",
        "created_at": timestamp,
        "agent": getattr(ctx, "agent_name", getattr(ctx, "task_id", "unknown")),
        "task_id": ctx.task_id,
        "artifacts": [],
        "inputs": inputs or [],
    }

    # 计算 artifacts 的校验和
    workspace = ctx.workspace_dir / output_dir
    for artifact in artifacts:
        artifact_path = artifact.get("path", "")
        full_path = ctx.workspace_dir / output_dir / artifact_path

        checksum = None
        if full_path.exists() and full_path.is_file():
            try:
                content_bytes = full_path.read_bytes()
                checksum = f"sha256:{hashlib.sha256(content_bytes).hexdigest()[:16]}"
            except Exception:
                pass

        manifest["artifacts"].append({
            "path": artifact_path,
            "type": artifact.get("type", "unknown"),
            "checksum": checksum,
        })

    manifest_path = workspace / "manifest.yaml"

    # 写入 manifest.yaml
    import yaml
    with manifest_path.open("w", encoding="utf-8") as f:
        yaml.dump(manifest, f, allow_unicode=True, default_flow_style=False)

    return manifest_path


def load_manifest(ctx: "ExecutionContext", output_dir: str) -> dict | None:
    """读取指定目录的 manifest.yaml（如果存在）。"""
    manifest_path = ctx.workspace_dir / output_dir / "manifest.yaml"
    if not manifest_path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
