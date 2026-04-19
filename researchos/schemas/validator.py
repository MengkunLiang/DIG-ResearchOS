from __future__ import annotations

"""ResearchOS artifact 校验器。"""

import argparse
import csv
import json
from functools import lru_cache
from pathlib import Path
import re
from typing import Callable, Mapping

import jsonschema
import yaml


_SCHEMAS_DIR = Path(__file__).parent / "json_schemas"
_TASK_CHECKERS: dict[str, Callable[[Path], tuple[bool, list[str]]]] = {}


@lru_cache(maxsize=64)
def _load_schema(schema_name: str) -> dict:
    """按名称加载 schema 文件，并做进程级缓存。"""
    path = _SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_record(record: object, schema_name: str) -> tuple[bool, str | None]:
    """校验单条对象是否符合指定 schema。"""
    schema = _load_schema(schema_name)
    try:
        jsonschema.validate(instance=record, schema=schema)
        return True, None
    except jsonschema.ValidationError as exc:
        location = " > ".join(str(part) for part in exc.absolute_path) or "<root>"
        return False, f"{location}: {exc.message}"


def validate_against_schema(data: object, schema_name: str) -> tuple[bool, str | None]:
    return validate_record(data, schema_name)


def validate_jsonl_file(
    path: Path,
    schema_name: str,
    *,
    min_count: int = 0,
    max_count: int | None = None,
) -> tuple[bool, list[str]]:
    """校验一个 JSONL 文件里的每一行。"""
    if not path.exists():
        return False, [f"File not found: {path}"]
    errors: list[str] = []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < min_count:
        errors.append(f"{path.name} has {len(lines)} records, expected at least {min_count}")
    if max_count is not None and len(lines) > max_count:
        errors.append(f"{path.name} has {len(lines)} records, expected at most {max_count}")
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{index} invalid JSON: {exc}")
            continue
        ok, err = validate_record(record, schema_name)
        if not ok and err:
            errors.append(f"{path.name}:{index} {err}")
    return not errors, errors


def register_task_checker(task_id: str, checker: Callable[[Path], tuple[bool, list[str]]]) -> None:
    """注册某个 task 的专用 checker。"""
    _TASK_CHECKERS[task_id] = checker


def _register_once(task_id: str, checker: Callable[[Path], tuple[bool, list[str]]]) -> None:
    """仅当还未注册时写入内置 checker。"""

    if task_id not in _TASK_CHECKERS:
        register_task_checker(task_id, checker)


def validate_declared_outputs(
    workspace: Path,
    declared_outputs: Mapping[str, str] | Mapping[str, Path],
) -> tuple[bool, list[str]]:
    """按状态机声明的 outputs 做最基础的“文件是否存在”校验。"""
    errors: list[str] = []
    for name, rel_path in declared_outputs.items():
        candidate = rel_path if isinstance(rel_path, Path) else workspace / str(rel_path)
        if not candidate.exists():
            errors.append(f"Missing output '{name}': {candidate.relative_to(workspace)}")
    return not errors, errors


def validate_prerequisites(workspace: Path, task_id: str) -> tuple[bool, str | None]:
    """校验 single-task 模式运行前的前置 artifact 是否就绪。

    设计说明：
    - 这里不做“内容级” schema 校验，只检查 task 契约声明的必需输入是否存在；
    - 这样可以把 single-task 的 fast-fail 放在真正启动 agent 之前，避免刚起 run 就因为
      缺输入而走到一半才失败；
    - `required_inputs` 与 `inputs` 的来源统一来自 `task_io_contract.py`，保持 CLI、文档和
      调试模式使用同一份契约。
    """

    from ..orchestration.task_io_contract import get_task_io, required_input_names

    try:
        contract = get_task_io(task_id)
    except KeyError as exc:
        return False, str(exc)

    inputs = contract.get("inputs", {})
    required = required_input_names(task_id)
    missing: list[str] = []
    for input_name in required:
        rel_path = inputs.get(input_name)
        if rel_path is None:
            missing.append(f"{input_name} (contract missing path)")
            continue
        candidate = workspace / str(rel_path)
        if not candidate.exists():
            missing.append(f"{input_name} -> {rel_path}")

    if missing:
        return False, "缺少前置输入: " + ", ".join(missing)
    return True, None


def validate_task_artifacts(
    workspace: Path,
    task_id: str,
    *,
    declared_outputs: Mapping[str, str] | Mapping[str, Path] | None = None,
) -> tuple[bool, list[str]]:
    """统一 task 校验入口。

    优先级：
    1. 若 task_id 已注册专用 checker，用专用 checker；
    2. 否则若提供了 declared_outputs，则退回到“检查声明输出存在”；
    3. 两者都没有时返回失败，让上层知道当前 task 还没有校验器。
    """
    checker = _TASK_CHECKERS.get(task_id)
    if checker is not None:
        return checker(workspace)
    if declared_outputs is not None:
        return validate_declared_outputs(workspace, declared_outputs)
    return False, [f"No checker registered for task {task_id}"]


def register_builtin_task_checkers() -> None:
    """注册 runtime 内置的 per-task checker。

    这里尽量对齐 Agent Dev Spec 的 T1-T9 artifact 契约，但保持一个现实原则：
    - schema 尽量只校验“对 runtime 最重要的结构”；
    - 更细的业务语义，后续仍可由各 agent 的 `validate_outputs` 继续加严。
    """

    builtin = {
        "HELLO": _check_hello,
        "T1": _check_t1,
        "T2": _check_t2,
        "T3": _check_t3,
        "T3.5": _check_t3_5,
        "T4": _check_t4,
        "T5": _check_t5,
        "T6": _check_t6,
        "T7": _check_t7,
        "T7.5": _check_t7_5,
        "T8": _check_t8,
        "T9": _check_t9,
    }
    for task_id, checker in builtin.items():
        _register_once(task_id, checker)


def _check_hello(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    hello = workspace / "hello.txt"
    if not hello.exists():
        errors.append("hello.txt missing")
    elif hello.read_text(encoding="utf-8").strip() != "Hello, Runtime!":
        errors.append("hello.txt content must be 'Hello, Runtime!'")
    return not errors, errors


def _check_t1(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    project = _load_yaml(workspace / "project.yaml", errors, "project.yaml missing")
    if project is not None:
        ok, err = validate_against_schema(project, "project")
        if not ok and err:
            errors.append(f"project.yaml: {err}")
    _append_missing_file(workspace / "state.yaml", errors, "state.yaml missing")
    for relative in (
        "user_seeds/seed_papers.jsonl",
        "user_seeds/seed_ideas.md",
        "user_seeds/seed_constraints.md",
    ):
        _append_missing_file(workspace / relative, errors, f"{relative} missing")
    return not errors, errors


def _check_t2(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for file_name in ("papers_raw.jsonl", "papers_dedup.jsonl"):
        ok, file_errors = validate_jsonl_file(
            workspace / "literature" / file_name,
            "papers_raw",
            min_count=1,
        )
        if not ok:
            errors.extend(file_errors[:3])
    for relative in ("literature/search_log.md", "literature/missing_areas.md"):
        _append_missing_file(workspace / relative, errors, f"{relative} missing")
    return not errors, errors


def _check_t3(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    dedup_records = _load_jsonl_records(workspace / "literature" / "papers_dedup.jsonl", errors)
    notes_dir = workspace / "literature" / "paper_notes"
    if not notes_dir.is_dir():
        errors.append("literature/paper_notes/ missing")
        return False, errors

    missing_notes: list[str] = []
    for record in dedup_records:
        paper_id = str(record.get("id", "")).strip()
        if not paper_id:
            continue
        note_path = notes_dir / f"{_safe_artifact_stem(paper_id)}.md"
        if not note_path.exists():
            missing_notes.append(paper_id)
    if missing_notes:
        errors.append(f"missing paper notes: {', '.join(missing_notes[:5])}")

    comparison_table = workspace / "literature" / "comparison_table.csv"
    if not comparison_table.exists():
        errors.append("literature/comparison_table.csv missing")
    elif not _csv_has_header(comparison_table):
        errors.append("literature/comparison_table.csv missing header row")

    related_work = workspace / "literature" / "related_work.bib"
    if not related_work.exists():
        errors.append("literature/related_work.bib missing")
    elif "@" not in related_work.read_text(encoding="utf-8"):
        errors.append("literature/related_work.bib is empty")
    return not errors, errors


def _check_t3_5(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    synthesis = workspace / "literature" / "synthesis.md"
    text = _read_text(synthesis, errors, "literature/synthesis.md missing")
    if text is None:
        return False, errors

    _require_markdown_sections(
        text,
        errors,
        [
            ("方法家族", "Method Families"),
            ("共同假设", "Shared Assumptions"),
            ("前沿", "Frontier"),
            ("趋势", "Trends"),
            ("研究问题", "Open Questions"),
        ],
        label="literature/synthesis.md",
    )
    return not errors, errors


def _check_t4(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    hypotheses_path = workspace / "ideation" / "hypotheses.md"
    hypotheses_text = _read_text(hypotheses_path, errors, "ideation/hypotheses.md missing")
    if hypotheses_text is None:
        return False, errors
    if len(hypotheses_text.strip()) < 500:
        errors.append("ideation/hypotheses.md too short")

    plan = _load_yaml(workspace / "ideation" / "exp_plan.yaml", errors, "ideation/exp_plan.yaml missing")
    if plan is not None:
        ok, err = validate_against_schema(plan, "exp_plan")
        if not ok and err:
            errors.append(f"ideation/exp_plan.yaml: {err}")
        anchors = set(re.findall(r"(?im)^#+\s*(H\d+)\b", hypotheses_text))
        for experiment in _iter_plan_experiments(plan):
            hypothesis_ref = experiment.get("hypothesis_ref")
            if hypothesis_ref and anchors and hypothesis_ref not in anchors:
                errors.append(
                    f"ideation/exp_plan.yaml hypothesis_ref '{hypothesis_ref}' not found in hypotheses.md"
                )

    _append_missing_file(workspace / "ideation" / "risks.md", errors, "ideation/risks.md missing")
    return not errors, errors


def _check_t5(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    pilot_plan = _load_yaml(workspace / "pilot" / "pilot_plan.yaml", errors, "pilot/pilot_plan.yaml missing")
    if pilot_plan is not None:
        ok, err = validate_against_schema(pilot_plan, "pilot_plan")
        if not ok and err:
            errors.append(f"pilot/pilot_plan.yaml: {err}")

    pilot_results = _load_json(
        workspace / "pilot" / "pilot_results.json",
        errors,
        "pilot/pilot_results.json missing",
    )
    if pilot_results is not None:
        ok, err = validate_against_schema(pilot_results, "pilot_results")
        if not ok and err:
            errors.append(f"pilot/pilot_results.json: {err}")

    pilot_code_dir = workspace / "pilot" / "pilot_code"
    if not pilot_code_dir.is_dir():
        errors.append("pilot/pilot_code/ missing")
    elif not any(pilot_code_dir.iterdir()):
        errors.append("pilot/pilot_code/ is empty")

    motivation_text = _read_text(
        workspace / "pilot" / "motivation_validation.md",
        errors,
        "pilot/motivation_validation.md missing",
    )
    if motivation_text is not None and not _contains_any(motivation_text, ("PASS", "REVISE", "FAIL")):
        errors.append("pilot/motivation_validation.md missing PASS/REVISE/FAIL decision")
    return not errors, errors


def _check_t6(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    novelty_text = _read_text(
        workspace / "novelty" / "novelty_report.md",
        errors,
        "novelty/novelty_report.md missing",
    )
    if novelty_text is None:
        return False, errors

    if not _contains_any(novelty_text, ("PASS", "REVISE", "FAIL")):
        errors.append("novelty/novelty_report.md missing PASS/REVISE/FAIL decision")
    option_count = novelty_text.lower().count("option") + novelty_text.count("方案")
    if "REVISE" in novelty_text.upper() and option_count < 2:
        errors.append("novelty/novelty_report.md revise decision should include at least 2 options")

    for relative in ("novelty/collision_cases.md", "novelty/must_add_baselines.md"):
        _append_missing_file(workspace / relative, errors, f"{relative} missing")
    return not errors, errors


def _check_t7(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    summary = _load_json(
        workspace / "experiments" / "results_summary.json",
        errors,
        "experiments/results_summary.json missing",
    )
    if summary is not None:
        ok, err = validate_against_schema(summary, "results_summary")
        if not ok and err:
            errors.append(f"experiments/results_summary.json: {err}")

    runs_dir = workspace / "experiments" / "runs"
    if not runs_dir.is_dir():
        errors.append("experiments/runs/ missing")
    else:
        run_dirs = [item for item in runs_dir.iterdir() if item.is_dir()]
        if not run_dirs:
            errors.append("experiments/runs/ is empty")
        for run_dir in run_dirs:
            record = _load_json(run_dir / "record.json", errors, f"{run_dir.name}/record.json missing")
            if record is None:
                continue
            ok, err = validate_against_schema(record, "run_record")
            if not ok and err:
                errors.append(f"{run_dir.name}/record.json: {err}")

    configs_dir = workspace / "experiments" / "configs"
    if not configs_dir.is_dir():
        errors.append("experiments/configs/ missing")
    iteration_log = _read_text(
        workspace / "experiments" / "iteration_log.md",
        errors,
        "experiments/iteration_log.md missing",
    )
    if iteration_log is not None and not iteration_log.strip():
        errors.append("experiments/iteration_log.md is empty")
    _append_missing_file(workspace / "experiments" / "ablations.csv", errors, "experiments/ablations.csv missing")

    project = _load_yaml(workspace / "project.yaml", [], "project.yaml missing")
    if isinstance(project, dict) and isinstance(summary, dict):
        max_gpu = float(project.get("compute_budget", {}).get("max_gpu_hours", 0) or 0)
        used_gpu = float(summary.get("total_gpu_hours", 0) or 0)
        if max_gpu > 0 and used_gpu > max_gpu:
            errors.append(f"experiments/results_summary.json total_gpu_hours {used_gpu} > max {max_gpu}")
    return not errors, errors


def _check_t7_5(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    decision = _read_text(
        workspace / "evaluation" / "evaluation_decision.md",
        errors,
        "evaluation/evaluation_decision.md missing",
    )
    if decision is None:
        return False, errors

    if "Situation" not in decision:
        errors.append("evaluation/evaluation_decision.md missing 'Situation' header")
    if "Option 1" not in decision:
        errors.append("evaluation/evaluation_decision.md missing 'Option 1' section")
    return not errors, errors


def _check_t8(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    _append_missing_file(workspace / "drafts" / "outline.md", errors, "drafts/outline.md missing")

    paper_text = _read_text(workspace / "drafts" / "paper.tex", errors, "drafts/paper.tex missing")
    if paper_text is not None:
        if r"\begin{document}" not in paper_text:
            errors.append("drafts/paper.tex missing \\begin{document}")
        if not _contains_any(paper_text, (r"\cite{", r"\bibliography{", r"\printbibliography")):
            errors.append("drafts/paper.tex missing bibliography or citation command")

    review_text = _read_text(
        workspace / "reviews" / "self_review.md",
        errors,
        "reviews/self_review.md missing",
    )
    if review_text is not None and not _contains_any(review_text, ("PASS", "REVISE", "FAIL")):
        errors.append("reviews/self_review.md missing PASS/REVISE/FAIL decision")
    return not errors, errors


def _check_t9(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    bundle_dir = workspace / "submission" / "bundle"
    if not bundle_dir.is_dir():
        errors.append("submission/bundle/ missing")
    elif not any(bundle_dir.iterdir()):
        errors.append("submission/bundle/ is empty")

    _append_missing_file(
        workspace / "submission" / "migration_report.md",
        errors,
        "submission/migration_report.md missing",
    )
    return not errors, errors


def _append_missing_file(path: Path, errors: list[str], message: str) -> None:
    """若文件不存在，则把约定的错误文案追加到 errors。"""

    if not path.exists():
        errors.append(message)


def _read_text(path: Path, errors: list[str], missing_message: str) -> str | None:
    """安全读取文本文件，失败时把错误信息落到 errors。"""

    if not path.exists():
        errors.append(missing_message)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        errors.append(f"{path.name} load failed: {exc}")
        return None


def _load_yaml(path: Path, errors: list[str], missing_message: str) -> object | None:
    if not path.exists():
        errors.append(missing_message)
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{path.name} load failed: {exc}")
        return None


def _load_json(path: Path, errors: list[str], missing_message: str) -> object | None:
    if not path.exists():
        errors.append(missing_message)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{path.name} load failed: {exc}")
        return None


def _load_jsonl_records(path: Path, errors: list[str]) -> list[dict]:
    """读取 JSONL，遇到语法错误时记录并返回已经成功解析的部分。"""

    if not path.exists():
        errors.append(f"{path.name} missing")
        return []
    records: list[dict] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{index} invalid JSON: {exc}")
            continue
        if isinstance(data, dict):
            records.append(data)
        else:
            errors.append(f"{path.name}:{index} expected JSON object")
    return records


def _require_markdown_sections(
    text: str,
    errors: list[str],
    required_groups: list[tuple[str, ...]],
    *,
    label: str,
) -> None:
    """校验 Markdown 至少包含每组候选标题中的一个。"""

    missing: list[str] = []
    lowered = text.lower()
    for candidates in required_groups:
        if any(candidate.lower() in lowered for candidate in candidates):
            continue
        missing.append("/".join(candidates))
    if missing:
        errors.append(f"{label} missing sections: {missing}")


def _contains_any(text: str, candidates: tuple[str, ...]) -> bool:
    upper_text = text.upper()
    return any(candidate.upper() in upper_text for candidate in candidates)


def _safe_artifact_stem(value: str) -> str:
    """把 paper id 之类的业务标识转成稳定文件名。"""

    cleaned = re.sub(r"[^\w.-]+", "_", value.strip())
    return cleaned.strip("_") or "artifact"


def _csv_has_header(path: Path) -> bool:
    """做一个轻量 CSV 头部检查，避免把空文件当成有效表格。"""

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
    except Exception:
        return False
    return bool(header and any(cell.strip() for cell in header))


def _iter_plan_experiments(plan: object) -> list[dict]:
    """兼容多种 `exp_plan.yaml` 组织方式，抽取 experiment 列表。"""

    if not isinstance(plan, dict):
        return []
    experiments = plan.get("experiments")
    if not isinstance(experiments, list):
        return []
    return [item for item in experiments if isinstance(item, dict)]


def build_declared_outputs_from_state_machine(
    state_machine_path: Path,
    task_id: str,
) -> dict[str, str] | None:
    """从 state_machine.yaml 中提取某个 task 声明的 outputs。"""
    raw = yaml.safe_load(state_machine_path.read_text(encoding="utf-8")) or {}
    source = raw.get("states") or raw.get("nodes") or {}
    if isinstance(source, list):
        for node in source:
            if node.get("id") == task_id:
                return dict(node.get("outputs") or {})
        return None
    node = source.get(task_id)
    if not node:
        return None
    return dict(node.get("outputs") or {})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m researchos.schemas.validator")
    parser.add_argument("workspace")
    parser.add_argument("--task", required=True)
    parser.add_argument("--state-machine", default="config/state_machine.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    register_builtin_task_checkers()
    args = build_arg_parser().parse_args(argv)
    workspace = Path(args.workspace).resolve()
    declared_outputs = build_declared_outputs_from_state_machine(
        Path(args.state_machine).resolve(),
        args.task,
    )
    ok, errors = validate_task_artifacts(
        workspace,
        args.task,
        declared_outputs=declared_outputs,
    )
    payload = {"ok": ok, "task": args.task, "errors": errors}
    print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
