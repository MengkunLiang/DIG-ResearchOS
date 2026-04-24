"""9个agent共享的helper函数，避免重复实现。

参考：Agent Dev Spec §1.2
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

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
