"""Artifact schema validation.

Provides JSON Schema validation for all ResearchOS artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft7Validator, ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


# Schema目录
SCHEMA_DIR = Path(__file__).parent / "json_schemas"
_SCHEMAS_DIR = SCHEMA_DIR  # 向后兼容别名


def load_schema(schema_name: str) -> dict:
    """加载JSON Schema定义。

    Args:
        schema_name: schema名称，如"papers_dedup"

    Returns:
        Schema字典

    Raises:
        FileNotFoundError: schema文件不存在
    """
    schema_path = SCHEMA_DIR / f"{schema_name}.schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    return json.loads(schema_path.read_text(encoding="utf-8"))


# 向后兼容别名
_load_schema = load_schema


def validate_record(record: dict, schema_name: str) -> tuple[bool, str | None]:
    """校验单条记录是否符合schema。

    Args:
        record: 待校验的记录
        schema_name: schema名称

    Returns:
        (ok, error_message)
    """
    if not HAS_JSONSCHEMA:
        # 如果没有安装jsonschema，只做基本检查
        return True, None

    try:
        schema = load_schema(schema_name)
    except FileNotFoundError as e:
        return False, f"Schema not found: {e}"

    try:
        diagnostics = validate_record_diagnostics(record, schema_name, schema=schema)
        if diagnostics:
            return False, "Validation error: " + "; ".join(
                _format_validation_diagnostic(item) for item in diagnostics
            )
        return True, None
    except ValidationError as e:
        return False, f"Validation error: {e.message}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def validate_record_diagnostics(
    record: dict[str, Any],
    schema_name: str,
    *,
    schema: dict[str, Any] | None = None,
    max_errors: int = 12,
) -> list[dict[str, str]]:
    """Return bounded, field-addressable JSON Schema diagnostics.

    ``validate_record`` predates the structured writer and intentionally keeps
    its compact ``(ok, message)`` API.  Tools and the CLI need more than a
    concatenated list of jsonschema messages, though: a model must be able to
    see which candidate and which field failed without guessing from a long
    flattened error string.  This helper keeps the underlying validator as the
    source of truth while exposing a stable, serialisable diagnosis.
    """

    if not HAS_JSONSCHEMA:
        return []
    schema = schema if schema is not None else load_schema(schema_name)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(record), key=lambda item: (list(item.path), item.message))
    diagnostics = [_validation_error_to_diagnostic(error) for error in errors[:max_errors]]
    if len(errors) > max_errors:
        diagnostics.append(
            {
                "path": "$",
                "rule": "additional_errors",
                "message": f"另有 {len(errors) - max_errors} 个 schema 问题；请先修复以上字段后重新校验。",
            }
        )
    return diagnostics


def _validation_error_to_diagnostic(error: Any) -> dict[str, str]:
    """Convert a ``jsonschema.ValidationError`` into a concise repair item."""

    path = _format_instance_path(error.path)
    if error.validator == "required":
        missing = str(error.message).split("'", 2)[1] if "'" in str(error.message) else "required field"
        return {
            "path": f"{path}.{missing}" if path != "$" else f"$.{missing}",
            "rule": "required",
            "message": f"缺少字段（必填）`{missing}`。",
        }
    if error.validator == "type":
        expected = _compact_schema_value(error.validator_value)
        actual = type(error.instance).__name__
        return {
            "path": path,
            "rule": "type",
            "message": f"类型不匹配：需要 {expected}，当前为 {actual}。",
        }
    if error.validator == "enum":
        expected = _compact_schema_value(error.validator_value)
        return {
            "path": path,
            "rule": "enum",
            "message": f"值 `{_compact_schema_value(error.instance)}` 不在允许集合 {expected} 中。",
        }
    if error.validator in {"minItems", "minLength", "minimum", "maximum", "pattern"}:
        return {
            "path": path,
            "rule": str(error.validator),
            "message": str(error.message),
        }
    return {
        "path": path,
        "rule": str(error.validator or "schema"),
        "message": str(error.message),
    }


def _format_instance_path(parts: Any) -> str:
    value = "$"
    for part in parts:
        if isinstance(part, int):
            value += f"[{part}]"
        else:
            value += f".{part}"
    return value


def _compact_schema_value(value: Any, *, limit: int = 180) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 3] + "..."


def _format_validation_diagnostic(diagnostic: dict[str, str]) -> str:
    return f"{diagnostic['path']}: {diagnostic['message']}"


def validate_task_artifacts(
    task_id_or_workspace: str | Path,
    workspace_dir_or_task_id: Path | str | None = None,
    declared_outputs: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """校验task的所有输出artifacts。

    根据task_io_contract定义的outputs，检查每个artifact是否符合schema。

    支持两种调用方式：
    1. validate_task_artifacts(task_id, workspace_dir) - 旧版本，用于agent.py
    2. validate_task_artifacts(workspace, task_id, declared_outputs=...) - 新版本，用于CLI

    Args:
        task_id_or_workspace: Task ID 或 Workspace路径
        workspace_dir_or_task_id: Workspace路径 或 Task ID
        declared_outputs: 可选的声明输出映射

    Returns:
        (ok, error_message)
    """
    # 判断调用方式
    if isinstance(task_id_or_workspace, Path):
        # 新版本调用: validate_task_artifacts(workspace, task_id, declared_outputs=...)
        workspace_dir = task_id_or_workspace
        task_id = str(workspace_dir_or_task_id)
    else:
        # 旧版本调用: validate_task_artifacts(task_id, workspace_dir)
        task_id = task_id_or_workspace
        workspace_dir = workspace_dir_or_task_id
        if workspace_dir is None:
            return False, "workspace_dir is required"

    from ..orchestration.task_io_contract import get_task_io

    try:
        task_io = get_task_io(task_id)
    except KeyError:
        return False, f"Unknown task: {task_id}"

    # task_io是dict，不是对象。CLI 可传入状态机声明的 outputs，以避免 contract
    # 与 state_machine.yaml 漂移时校验错对象；schema/optional 仍来自 contract。
    outputs = declared_outputs or task_io.get("outputs", {})
    if not outputs:
        # 没有定义outputs，跳过校验
        return True, None
    optional_outputs = set(task_io.get("optional_outputs", []))
    if declared_outputs:
        outputs = {name: path for name, path in outputs.items() if name not in optional_outputs}

    errors = []

    for output_name, output_path in outputs.items():
        if output_name in optional_outputs:
            continue
        if isinstance(output_path, Path):
            file_path = output_path
            output_display = output_path.as_posix()
        else:
            output_display = str(output_path)
            file_path = workspace_dir / output_display
        # output_path是字符串，如"hello.txt"或"literature/papers_dedup.jsonl"
        # 检查文件存在
        if not file_path.exists():
            errors.append(f"Missing output: {output_name} ({output_display})")
            continue

        # 检查schema（如果定义）
        schemas = task_io.get("schemas", {})
        schema_name = schemas.get(output_name)

        if schema_name and HAS_JSONSCHEMA:
            # 根据文件类型验证
            if file_path.suffix == ".jsonl":
                ok, err = _validate_jsonl_file(file_path, schema_name)
            elif file_path.suffix == ".json":
                ok, err = _validate_json_file(file_path, schema_name)
            elif file_path.suffix in [".yaml", ".yml"]:
                ok, err = _validate_yaml_file(file_path, schema_name)
            else:
                # 未知格式，跳过schema验证
                continue

            if not ok:
                errors.append(f"Schema验证失败 {output_name}: {err}")

    if not errors:
        checker = get_task_checker(task_id)
        if checker is not None:
            ok, err = checker(workspace_dir)
            if not ok:
                errors.append(f"Task checker failed for {task_id}: {err}")

    if errors:
        return False, "; ".join(errors)

    return True, None


def validate_prerequisites(workspace_dir: Path, task_id: str) -> tuple[bool, str | None]:
    """校验task的前置条件（输入artifacts）是否满足。

    根据task_io_contract定义的inputs，检查每个输入artifact是否存在且有效。

    Args:
        workspace_dir: Workspace根目录
        task_id: Task ID（如"T1", "T2"）

    Returns:
        (ok, error_message)
    """
    from ..orchestration.task_io_contract import get_task_io

    try:
        task_io = get_task_io(task_id)
    except KeyError:
        return False, f"Unknown task: {task_id}"

    # task_io是dict，不是对象
    inputs = task_io.get("inputs", {})
    required_inputs = task_io.get("required_inputs", [])

    if not inputs:
        # 没有定义inputs，跳过校验
        return True, None

    errors = []

    for input_name, input_path in inputs.items():
        file_path = workspace_dir / input_path

        # 检查文件存在
        if not file_path.exists():
            if input_name in required_inputs:
                errors.append(f"Missing required input: {input_name} ({input_path})")
            continue

    if errors:
        return False, "; ".join(errors)

    return True, None


def build_declared_outputs_from_state_machine(state_machine_config: Path, task_id: str) -> dict[str, str]:
    """从状态机配置中提取task的声明输出。

    Args:
        state_machine_config: 状态机配置文件路径
        task_id: Task ID

    Returns:
        输出名称到相对路径的映射
    """
    from ..orchestration.state_machine import StateMachine

    # 加载状态机配置
    state_machine = StateMachine(state_machine_config)

    if task_id not in state_machine.nodes:
        return {}

    node = state_machine.nodes[task_id]
    return dict(node.outputs or {})


def validate_declared_outputs(workspace_dir: Path, declared_outputs: dict[str, str]) -> tuple[bool, str | None]:
    """校验声明的输出文件是否都存在。

    Args:
        workspace_dir: Workspace根目录
        declared_outputs: 输出名称到相对路径的映射

    Returns:
        (ok, error_message)
    """
    missing = []
    for name, rel_path in declared_outputs.items():
        file_path = workspace_dir / rel_path
        if not file_path.exists():
            missing.append(f"{name} ({rel_path})")

    if missing:
        return False, f"Missing declared outputs: {', '.join(missing)}"

    return True, None


def validate_structured_outputs(
    workspace_dir: Path,
    structured_outputs: dict[str, str],
) -> tuple[bool, str | None]:
    """Validate agent-declared structured output files.

    `structured_outputs` maps a workspace-relative file path to a schema name,
    for example `ideation/exp_plan.yaml: exp_plan`.
    """

    errors: list[str] = []
    for rel_path, schema_name in structured_outputs.items():
        file_path = workspace_dir / rel_path
        if not file_path.exists():
            errors.append(f"Missing structured output: {rel_path}")
            continue
        if not schema_name:
            continue

        suffix = file_path.suffix.lower()
        if suffix == ".jsonl":
            ok, err = _validate_jsonl_file(file_path, schema_name)
        elif suffix == ".json":
            ok, err = _validate_json_file(file_path, schema_name)
        elif suffix in {".yaml", ".yml"}:
            ok, err = _validate_yaml_file(file_path, schema_name)
        else:
            errors.append(f"Unsupported structured output format: {rel_path}")
            continue

        if not ok:
            errors.append(_format_structured_output_error(rel_path, schema_name, err))

    if errors:
        return False, "; ".join(errors)

    return True, None


def _format_structured_output_error(rel_path: str, schema_name: str, err: str | None) -> str:
    """Make schema errors useful to agents and compatible with legacy validators."""

    detail = err or "unknown error"
    hints: list[str] = []
    lowered = detail.lower()
    if "invalid json" in lowered:
        hints.append("解析失败")
    if "is a required property" in detail:
        hints.append("缺少字段")
        match = re.search(r"'([^']+)' is a required property", detail)
        if match:
            field = match.group(1)
            hints.append(f"缺少字段: {field}")
            if field == "experiments":
                hints.append("实验")
    if "seed" in lowered:
        hints.append("seed policy")
        hints.append("种子")
    hint_text = "；".join(dict.fromkeys(hints))
    if hint_text:
        return f"{rel_path} ({schema_name}): {hint_text}; {detail}"
    return f"{rel_path} ({schema_name}): {detail}"


def _validate_jsonl_file(path: Path, schema_name: str) -> tuple[bool, str | None]:
    """校验JSONL文件的每一行。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                return False, f"Line {i}: Invalid JSON: {e}"

            ok, err = validate_record(record, schema_name)
            if not ok:
                return False, f"Line {i}: {err}"

        return True, None
    except Exception as e:
        return False, f"Failed to read file: {e}"


def _validate_json_file(path: Path, schema_name: str) -> tuple[bool, str | None]:
    """校验JSON文件。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_record(data, schema_name)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, f"Failed to read file: {e}"


def _normalize_dates_for_validation(obj: Any) -> Any:
    """递归转换date/datetime对象为ISO 8601字符串，用于JSON Schema验证。

    YAML解析器会自动将"2024-06-01"转换为Python的date对象，
    但JSON Schema期望的是字符串格式。

    Args:
        obj: 待转换的对象（可以是dict, list, 或基本类型）

    Returns:
        转换后的对象
    """
    from datetime import date, datetime

    if isinstance(obj, datetime):
        # datetime对象转换为ISO 8601格式（带时间）
        return obj.isoformat()
    elif isinstance(obj, date):
        # date对象转换为ISO 8601格式（仅日期）
        # 为了符合date-time格式，添加时间部分
        return f"{obj.isoformat()}T00:00:00Z"
    elif isinstance(obj, dict):
        return {k: _normalize_dates_for_validation(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_dates_for_validation(item) for item in obj]
    else:
        return obj


def _validate_yaml_file(path: Path, schema_name: str) -> tuple[bool, str | None]:
    """校验YAML文件。"""
    try:
        import yaml
        from datetime import date, datetime

        data = yaml.safe_load(path.read_text(encoding="utf-8"))

        # 递归转换date/datetime对象为ISO 8601字符串
        # 这是因为YAML解析器会自动将"2024-06-01"转换为date对象
        # 但JSON Schema期望的是字符串
        data = _normalize_dates_for_validation(data)

        return validate_record(data, schema_name)
    except yaml.YAMLError as e:
        return False, f"Invalid YAML: {e}"
    except Exception as e:
        return False, f"Failed to read file: {e}"


# ══════════════════════════════════════════════════════
# 内置checker函数（用于特定task的复杂校验）
# ══════════════════════════════════════════════════════

_TASK_CHECKERS: dict[str, Any] = {}


def register_task_checker(task_id: str, checker_fn):
    """注册task专属的checker函数。

    Checker函数签名: (workspace_dir: Path) -> tuple[bool, str | None]
    """
    _TASK_CHECKERS[task_id] = checker_fn


def get_task_checker(task_id: str):
    """获取task的checker函数。"""
    return _TASK_CHECKERS.get(task_id)


def register_builtin_task_checkers():
    """注册内置的task checker。"""

    def check_hello(workspace_dir: Path) -> tuple[bool, str | None]:
        """HELLO task checker。"""
        hello_file = workspace_dir / "hello.txt"
        if not hello_file.exists():
            return False, "hello.txt not found"
        content = hello_file.read_text(encoding="utf-8")
        if "Hello" not in content:
            return False, "hello.txt does not contain 'Hello'"
        return True, None

    def check_t3(workspace_dir: Path) -> tuple[bool, str | None]:
        """T3 note checker：复用 Reader 的单篇笔记结构和证据锚点规则。"""
        notes_dir = workspace_dir / "literature" / "deep_read_notes"
        bridge_notes_dir = workspace_dir / "literature" / "bridge_notes"
        if not notes_dir.exists() and not bridge_notes_dir.exists():
            return False, "literature/deep_read_notes or literature/bridge_notes not found"

        from ..literature_identity import is_paper_note_file

        note_files = []
        if notes_dir.exists():
            note_files.extend(path for path in notes_dir.glob("*.md") if is_paper_note_file(path))
        if bridge_notes_dir.exists():
            note_files.extend(path for path in bridge_notes_dir.glob("**/*.md") if is_paper_note_file(path))
        if not note_files:
            return False, "T3 has no real markdown paper notes; guides/templates/placeholders are ignored"

        from ..agents.reader import ReaderAgent
        from ..runtime.agent import ExecutionContext

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validation",
            task_id="T3",
            run_id="artifact-check",
            mode="read",
            outputs_expected={
                "deep_read_notes_dir": workspace_dir / "literature" / "deep_read_notes",
                "comparison_table": workspace_dir / "literature" / "comparison_table.csv",
                "related_work_bib": workspace_dir / "literature" / "related_work.bib",
            },
        )
        return ReaderAgent(mode="read").validate_outputs(ctx)

    def check_writer_phase(workspace_dir: Path, task_id: str) -> tuple[bool, str | None]:
        """T8 writer checker：复用 WriterAgent 的阶段级校验。"""
        from ..agents.writer import WriterAgent
        from ..runtime.agent import ExecutionContext

        mode = None
        extra: dict[str, object] = {}
        section_map = {
            "T8-STYLE-GATE": ("style_gate", None),
            "T8-SECTION-PLAN": ("section_plan", None),
            "T8-SEC-METHOD": ("section_draft", "methodology"),
            "T8-SEC-EXPERIMENTS": ("section_draft", "experiments"),
            "T8-SEC-RELATED": ("section_draft", "related_work"),
            "T8-SEC-ANALYSIS": ("section_draft", "analysis"),
            "T8-SEC-INTRO": ("section_draft", "introduction"),
            "T8-SEC-CONCLUSION": ("section_draft", "conclusion"),
            "T8-SEC-ABSTRACT": ("section_draft", "abstract"),
            "T8-SECTIONS": ("section_drafts", None),
            "T8-DRAFT": ("draft", None),
            "T8-SELF-CHECK": ("self_check", None),
        }
        if task_id == "T8-RESOURCE":
            mode = "resource_index"
        elif task_id == "T8-WRITE":
            mode = "outline"
        elif task_id in {"T8-REVISE-1", "T8-REVISE-2"}:
            mode = "revise"
            extra["round"] = 1 if task_id.endswith("-1") else 2
        elif task_id in section_map:
            mode, section_id = section_map[task_id]
            if section_id:
                extra["section_id"] = section_id
        if mode is None:
            return True, None

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validator",
            task_id=task_id,
            run_id="validator",
            mode=mode,
            extra=extra,
        )
        return WriterAgent(mode=mode).validate_outputs(ctx)

    def check_experimenter_phase(workspace_dir: Path, task_id: str) -> tuple[bool, str | None]:
        from ..agents.experimenter import ExperimenterAgent
        from ..runtime.agent import ExecutionContext

        if task_id == "T5-REBOOST-GATE":
            mode = "reboost"
        else:
            mode = "pilot" if task_id == "T5" else "full"
        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validator",
            task_id=task_id,
            run_id="validator",
            mode=mode,
            extra={"artifact_validation": True},
        )
        return ExperimenterAgent(mode=mode).validate_outputs(ctx)

    def check_ideation_phase(workspace_dir: Path) -> tuple[bool, str | None]:
        """T4 checker：复用 IdeationAgent 的 schema、anchor、Gate 和 bridge 条件校验。"""
        from ..agents.ideation import IdeationAgent
        from ..orchestration.task_io_contract import resolve_outputs
        from ..runtime.agent import ExecutionContext

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validator",
            task_id="T4",
            run_id="validator",
            outputs_expected=resolve_outputs(workspace_dir, "T4"),
        )
        return IdeationAgent().validate_outputs(ctx)

    def check_t4_gate1_phase(workspace_dir: Path) -> tuple[bool, str | None]:
        from ..agents.ideation import validate_t4_gate1_ready

        ok, err = validate_t4_gate1_ready(workspace_dir)
        if not ok:
            return False, err
        decision_path = workspace_dir / "ideation" / "_gate1_user_selection.json"
        if not decision_path.exists() or decision_path.stat().st_size <= 0:
            return True, None
        from ..orchestration.state_machine import validate_t4_gate1_selection_file

        return validate_t4_gate1_selection_file(workspace_dir)

    def check_reviewer_phase(workspace_dir: Path, task_id: str) -> tuple[bool, str | None]:
        from ..agents.reviewer import ReviewerAgent
        from ..runtime.agent import ExecutionContext

        round_num = 1 if task_id.endswith("-1") else 2
        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validator",
            task_id=task_id,
            run_id="validator",
            extra={"round": round_num},
        )
        return ReviewerAgent().validate_outputs(ctx)

    def check_submission_phase(workspace_dir: Path) -> tuple[bool, str | None]:
        from ..agents.submission import SubmissionAgent
        from ..runtime.agent import ExecutionContext

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validator",
            task_id="T9",
            run_id="validator",
        )
        return SubmissionAgent().validate_outputs(ctx)

    def check_survey_writer_phase(workspace_dir: Path, task_id: str) -> tuple[bool, str | None]:
        from ..agents.survey_writer import SurveyWriterAgent
        from ..runtime.agent import ExecutionContext

        mode = None
        section_id = None
        section_modes = {
            "T3.6-SEC-BACKGROUND": "background",
            "T3.6-SEC-TAXONOMY": "taxonomy",
            "T3.6-SEC-THEME-1": "theme_1",
            "T3.6-SEC-THEME-2": "theme_2",
            "T3.6-SEC-THEME-3": "theme_3",
            "T3.6-SEC-THEME-4": "theme_4",
            "T3.6-SEC-COMPARISON": "comparison",
            "T3.6-SEC-CHALLENGES": "challenges",
            "T3.6-SEC-FUTURE": "future",
            "T3.6-SEC-INTRO": "introduction",
            "T3.6-SEC-CONCLUSION": "conclusion",
            "T3.6-SEC-ABSTRACT": "abstract",
        }
        if task_id == "T3.6-GATE-SURVEY":
            mode = "survey_gate"
        elif task_id == "T3.6-PLAN":
            mode = "survey_plan"
        elif task_id == "T3.6-GATE-OUTLINE":
            mode = "outline_gate"
        elif task_id == "T3.6-GATE-CORPUS":
            mode = "corpus_gate"
        elif task_id == "T3.6-EXPAND":
            mode = "survey_expand"
        elif task_id == "T3.6-STATE":
            mode = "survey_state"
        elif task_id == "T3.6-VISUALS":
            mode = "survey_visuals"
        elif task_id in section_modes:
            mode = "survey_section"
            section_id = section_modes[task_id]
        elif task_id == "T3.6-ASSEMBLE":
            mode = "survey_assemble"
        elif task_id == "T3.6-REVIEW":
            mode = "survey_review"
        elif task_id == "T3.6-COMPILE":
            mode = "survey_compile"
        elif task_id == "T3.6-FEED":
            mode = "survey_feed"
        if mode is None:
            return True, None

        extra: dict[str, object] = {}
        if section_id:
            extra["section_id"] = section_id
        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id="validator",
            task_id=task_id,
            run_id="validator",
            mode=mode,
            extra=extra,
        )
        return SurveyWriterAgent(mode=mode).validate_outputs(ctx)

    register_task_checker("HELLO", check_hello)
    register_task_checker("T3", check_t3)
    register_task_checker("T4", check_ideation_phase)
    register_task_checker("T4-GATE1", check_t4_gate1_phase)
    register_task_checker(
        "T5-REBOOST-GATE",
        lambda workspace_dir: check_experimenter_phase(workspace_dir, "T5-REBOOST-GATE"),
    )
    register_task_checker("T5", lambda workspace_dir: check_experimenter_phase(workspace_dir, "T5"))
    register_task_checker("T7", lambda workspace_dir: check_experimenter_phase(workspace_dir, "T7"))
    register_task_checker("T8-REVIEW-1", lambda workspace_dir: check_reviewer_phase(workspace_dir, "T8-REVIEW-1"))
    register_task_checker("T8-REVIEW-2", lambda workspace_dir: check_reviewer_phase(workspace_dir, "T8-REVIEW-2"))
    register_task_checker("T9", check_submission_phase)
    for task_id in [
        "T8-STYLE-GATE",
        "T8-RESOURCE",
        "T8-WRITE",
        "T8-SECTION-PLAN",
        "T8-SEC-METHOD",
        "T8-SEC-EXPERIMENTS",
        "T8-SEC-RELATED",
        "T8-SEC-ANALYSIS",
        "T8-SEC-INTRO",
        "T8-SEC-CONCLUSION",
        "T8-SEC-ABSTRACT",
        "T8-SECTIONS",
        "T8-DRAFT",
        "T8-SELF-CHECK",
        "T8-REVISE-1",
        "T8-REVISE-2",
    ]:
        register_task_checker(task_id, lambda workspace_dir, task_id=task_id: check_writer_phase(workspace_dir, task_id))

    for task_id in [
        "T3.6-GATE-SURVEY",
        "T3.6-PLAN",
        "T3.6-GATE-OUTLINE",
        "T3.6-GATE-CORPUS",
        "T3.6-EXPAND",
        "T3.6-STATE",
        "T3.6-VISUALS",
        "T3.6-SEC-BACKGROUND",
        "T3.6-SEC-TAXONOMY",
        "T3.6-SEC-THEME-1",
        "T3.6-SEC-THEME-2",
        "T3.6-SEC-THEME-3",
        "T3.6-SEC-THEME-4",
        "T3.6-SEC-COMPARISON",
        "T3.6-SEC-CHALLENGES",
        "T3.6-SEC-FUTURE",
        "T3.6-SEC-INTRO",
        "T3.6-SEC-CONCLUSION",
        "T3.6-SEC-ABSTRACT",
        "T3.6-ASSEMBLE",
        "T3.6-REVIEW",
        "T3.6-COMPILE",
        "T3.6-FEED",
    ]:
        register_task_checker(
            task_id,
            lambda workspace_dir, task_id=task_id: check_survey_writer_phase(workspace_dir, task_id),
        )

    # TODO: 为T1-T9添加更多checker


# 启动时自动注册
register_builtin_task_checkers()
