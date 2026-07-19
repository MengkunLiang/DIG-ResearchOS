from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import io
import json
import re
import shutil
import unicodedata
from typing import Any, Awaitable, Callable

from rich.console import Console, Group
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..latex_templates import ccf_template_ids, ccf_template_option_id, normalize_ccf_template_id
from ..ui.candidate_cards import CandidateCardRenderer, CandidateViewModel


_READLINE_CONFIGURED = False


_T2_LLM_CAPTURE_FIELDS = {
    "coverage_total",
    "active_pool_max",
    "deep_read_min",
    "deep_read_target",
    "deep_read_max",
    "abstract_sweep_target",
    "require_deep_read_target",
    "manuscript_language",
    "include_chinese_literature",
    "base_option",
}

_T4_LLM_DIRECTIVE_FIELDS = {
    "action",
    "target_candidate_ids",
    "target_family_ids",
    "component_refs",
    "preserve_genes",
    "donor_genes",
    "requested_rounds",
    "requested_route",
    "constraints",
}


def _strip_terminal_control_sequences(value: str) -> str:
    """Remove terminal replies/paste artifacts before parsing a human answer.

    Some terminal emulators paste OSC colour queries such as
    ``]10;rgb:cccc/cccc/cccc\\`` into stdin.  They are neither user intent nor
    a parameter, so remove both well-formed ANSI/OSC sequences and the common
    visible fragment before the answer is echoed or interpreted.
    """

    text = str(value or "")
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\]?\d{1,3};rgb:[0-9A-Fa-f/]{3,64}\\?", "", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text


def _configure_readline_once() -> None:
    """Enable predictable line editing for CLI gates when readline is available."""

    global _READLINE_CONFIGURED
    if _READLINE_CONFIGURED:
        return
    _READLINE_CONFIGURED = True
    try:
        import readline  # type: ignore
    except Exception:
        return
    for binding in (
        '"\\C-h": backward-delete-char',
        '"\\C-?": backward-delete-char',
        '"\\e[3~": delete-char',
    ):
        try:
            readline.parse_and_bind(binding)
        except Exception:
            continue


def _read_cli_line(prompt: str) -> str:
    """Read one terminal line with best-effort readline editing support."""

    _configure_readline_once()
    return _strip_terminal_control_sequences(input(prompt))


def _read_cli_multiline(
    *,
    prompt: str = "> ",
    continuation_prompt: str | None = None,
    submit_hint: str | None = None,
) -> str:
    """Collect a terminal text block, where Enter remains a newline.

    T4 is a research dialogue: a directive often needs a constraint, an
    evidence concern, and a requested action in one turn.  Requiring the
    researcher to encode that in a single shell line is both fragile and
    unnecessarily unlike the rest of the human interface.  ``Ctrl+D`` ends
    the block on POSIX terminals; a standalone ``END`` is a discoverable
    fallback for terminals where EOF is intercepted by an IDE.

    ``EOFError`` is intentionally consumed when at least one line was typed:
    it is the normal submit gesture.  With no content it is re-raised so the
    caller can persist a waiting Gate rather than silently selecting a
    default.
    """

    lines: list[str] = []
    current_prompt = prompt
    hint_printed = False
    try:
        while True:
            line = _read_cli_line(current_prompt)
            if line.strip().casefold() == "end":
                break
            lines.append(line)
            if continuation_prompt is not None:
                current_prompt = continuation_prompt
            if line.strip() and not hint_printed and submit_hint:
                print(submit_hint)
                hint_printed = True
    except EOFError:
        if not lines:
            raise
    return "\n".join(lines).strip()


def _parse_json_object_from_llm_content(value: Any) -> dict[str, Any] | None:
    """Extract one JSON object from a short structured LLM response."""

    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        decoded, _ = json.JSONDecoder().raw_decode(text[start:])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _sanitize_t2_llm_capture(value: Any) -> dict[str, str]:
    """Keep only the bounded T2 parameter contract from an LLM response."""

    if not isinstance(value, dict):
        return {}
    captured: dict[str, str] = {}
    for key in _T2_LLM_CAPTURE_FIELDS:
        item = value.get(key)
        if item in (None, ""):
            continue
        if isinstance(item, bool):
            captured[key] = "true" if item else "false"
        elif isinstance(item, (str, int, float)):
            captured[key] = _strip_terminal_control_sequences(str(item)).strip()
    return {key: item for key, item in captured.items() if item}


def _sanitize_t4_llm_directive(value: Any) -> dict[str, Any]:
    """Keep only the bounded proposal contract for a T4 Gate1 directive."""

    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    action = value.get("action")
    if isinstance(action, str) and action.strip():
        result["action"] = action.strip()
    for key in ("target_candidate_ids", "target_family_ids", "component_refs", "preserve_genes", "constraints"):
        raw = value.get(key)
        if isinstance(raw, list):
            result[key] = [str(item).strip() for item in raw if str(item).strip()]
    raw_map = value.get("donor_genes")
    if isinstance(raw_map, dict):
        result["donor_genes"] = {
            str(key).strip(): str(item).strip()
            for key, item in raw_map.items()
            if str(key).strip() and str(item).strip()
        }
    requested_rounds = value.get("requested_rounds")
    if isinstance(requested_rounds, int) and 0 <= requested_rounds <= 3:
        result["requested_rounds"] = requested_rounds
    requested_route = value.get("requested_route") or value.get("route")
    if isinstance(requested_route, str) and requested_route.strip():
        result["requested_route"] = requested_route.strip()
    return result


def build_t2_parameter_llm_interpreter(
    llm_client: Any,
) -> Callable[[str], Awaitable[dict[str, str]]]:
    """Build a narrow LLM adapter for one-line T2 parameter intent parsing.

    The model only proposes a small JSON object.  ``CLIHumanInterface`` applies
    deterministic parsing and StateMachine-level range/language validation
    afterwards, so a malformed or overreaching model response cannot change
    search boundaries directly.
    """

    async def interpret(raw_answer: str) -> dict[str, str]:
        prompt = """You parse one user's T2 literature-coverage request for a research CLI.
Return exactly one JSON object and no Markdown. Do not explain your reasoning.
Only include values explicitly stated or unambiguously requested by the user.
Allowed keys: coverage_total, active_pool_max, deep_read_min, deep_read_target,
deep_read_max, abstract_sweep_target, require_deep_read_target,
manuscript_language, include_chinese_literature, base_option.

Rules:
- Numeric values must be decimal strings. abstract_sweep_target may also be all_readable.
- manuscript_language must be one of auto, en, zh, mixed when stated.
- include_chinese_literature must be one of true, false, auto when stated.
- English manuscript means non-seed Chinese literature will be excluded by a later
  deterministic policy. Do not invent a language when the user did not state one.
- Do not infer omitted numeric fields.

User input:
""" + _strip_terminal_control_sequences(raw_answer)
        response = await llm_client.chat(
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict JSON parser. Return only the requested object.",
                },
                {"role": "user", "content": prompt},
            ],
            tools=None,
            temperature=0.0,
            tier="light",
            profile=None,
            timeout=25,
            max_retries_per_model=1,
            retry_base_delay=0.0,
        )
        choice = response.raw.choices[0].message
        parsed = _parse_json_object_from_llm_content(getattr(choice, "content", ""))
        return _sanitize_t2_llm_capture(parsed)

    return interpret


def build_t4_directive_llm_interpreter(
    llm_client: Any,
) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """Build a narrow semantic parser for natural-language T4 Gate1 input.

    It proposes a directive only.  Candidate IDs, component references,
    fingerprints, confirmation, and every state change remain subject to the
    deterministic validation in ``researchos.ideation.directives``.
    """

    async def interpret(raw_answer: str) -> dict[str, Any]:
        prompt = """Parse one ResearchOS T4 Gate1 instruction. Return exactly one JSON object, no Markdown and no explanation.
Allowed action values: select_candidate, keep_parallel, compose_from_components, continue_evolution, focus_candidate, merge_candidates, show_more, show_archive, inspect_score, inspect_evidence, inspect_lineage, inspect_hypotheses, inspect_contributions, inspect_genome, inspect_files, compare_candidates, regenerate_route, rollback, pause, cancel.
Allowed keys: action, target_candidate_ids, target_family_ids, component_refs, preserve_genes, donor_genes, requested_rounds, requested_route, constraints.
Do not invent an ID, research claim, score, or mechanism. Use an empty list for an omitted list field rather than guessing.
For multiple complete Candidates, use keep_parallel unless the instruction explicitly asks for a unified composition or crossover. For partial hypotheses/contributions/genes, use compose_from_components.
If the user says 查看 / view / inspect / show a Candidate and does not also request a mutation, selection, evolution, merge, or confirmation, return an inspect_* action. Never interpret a read-only request as select_candidate.
If the user explicitly says 推进 / 选择 / proceed exactly one Candidate and does not ask to optimize, evolve, merge, compose, or regenerate, return select_candidate. That action enters T4.5 only after human confirmation.
User input:
""" + _strip_terminal_control_sequences(raw_answer)
        response = await llm_client.chat(
            messages=[
                {"role": "system", "content": "You are a strict JSON intent parser. Return only the requested object."},
                {"role": "user", "content": prompt},
            ],
            tools=None,
            temperature=0.0,
            tier="light",
            profile=None,
            timeout=25,
            max_retries_per_model=1,
            retry_base_delay=0.0,
        )
        choice = response.raw.choices[0].message
        return _sanitize_t4_llm_directive(_parse_json_object_from_llm_content(getattr(choice, "content", "")))

    return interpret


def _compact_text(value: Any, limit: int = 220) -> str:
    """Normalize terminal text without discarding a decision-critical suffix."""

    del limit
    return " ".join(str(value or "").split())


def _t4_card_excerpt(value: Any, *, max_chars: int, max_sentences: int = 2) -> str:
    """Project stored T4 prose into a compact, non-inferential card preview.

    Gate1 is a decision surface, not an export of every LLM-authored field.
    The underlying Final Idea Card remains unchanged and is available through
    the read-only detail commands.  This helper only keeps complete leading
    sentences where possible, then makes a visibly truncated preview; it never
    rewrites scientific content, supplies a missing explanation, or changes
    evidence strength.
    """

    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?\.])\s+", text) if item.strip()]
    selected: list[str] = []
    current_length = 0
    for sentence in sentences:
        proposed_length = current_length + (1 if selected else 0) + len(sentence)
        if selected and (len(selected) >= max_sentences or proposed_length > max_chars):
            break
        if not selected and len(sentence) > max_chars:
            break
        selected.append(sentence)
        current_length = proposed_length
    if selected:
        preview = " ".join(selected)
        if len(preview) < len(text):
            return preview.rstrip("。；;，, ") + "…"
        return preview
    cutoff = text[:max_chars].rstrip()
    for boundary in ("。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " "):
        position = cutoff.rfind(boundary)
        if position >= max(24, max_chars // 2):
            cutoff = cutoff[: position + (1 if boundary not in {" ", "，", ","} else 0)].rstrip()
            break
    return cutoff.rstrip("。；;，, ") + "…"


def _t4_card_list_excerpt(values: Any, *, max_items: int, max_chars: int) -> str:
    """Render a bounded list without merging separate risks into one claim."""

    if not isinstance(values, list):
        return ""
    excerpts = [
        _t4_card_excerpt(item, max_chars=max_chars, max_sentences=1)
        for item in values[:max_items]
        if str(item or "").strip()
    ]
    return "\n".join(f"{index}. {item}" for index, item in enumerate(excerpts, start=1))


def _t4_evidence_summary(value: Any) -> str:
    """Deduplicate internal evidence-level tokens for the Gate decision view."""

    raw = " ".join(str(value or "").split())
    if not raw:
        return "当前未标注；选择前请查看论文阅读笔记。"
    labels = {
        "FULL_TEXT": "全文依据",
        "PARTIAL_TEXT": "部分全文依据",
        "ABSTRACT_ONLY": "摘要级线索",
        "USER_PROVIDED": "人工提供材料",
        "SYNTHESIS": "综合材料",
    }
    entries: list[str] = []
    for token in re.split(r"[;,，；]+", raw):
        normalized = token.strip().upper()
        label = labels.get(normalized, token.strip())
        if label and label not in entries:
            entries.append(label)
    return "；".join(entries) if entries else raw


def _t4_portfolio_role_label(value: Any) -> str:
    return {
        "lead": "主线",
        "alternative": "备选",
        "high_upside": "高潜力",
        "supporting": "支撑模块",
        "parallel": "并行候选",
    }.get(str(value or "").strip().lower(), "待定位")


def _t4_origin_label(value: Any) -> str:
    """Translate controller Route names without reinterpreting the Idea."""

    return {
        "evidence_routed_literature": "文献线索发散",
        "informed_brainstorm": "知情头脑风暴",
        "mechanism_challenge": "机制挑战",
        "reverse_operation": "逆向操作",
        "subgroup_failure": "子群失效",
        "gap_exploration": "研究缺口探索",
        "cross_domain_bridge": "跨领域候选方向（Cross-domain）",
        "bridge_synthesis": "跨领域候选方向（Cross-domain）",
        "human_composition": "人工指定的组件组合",
    }.get(str(value or "").strip(), str(value or "未标注"))


def _t4_candidate_stage_label(value: Any) -> str:
    return {
        "seed": "Idea Seed（待富化）",
        "idea_seed": "Idea Seed（待富化）",
        "evolved": "Evolved Candidate",
        "evolved_candidate": "Evolved Candidate",
        "selected": "Selected Candidate",
        "selected_candidate": "Selected Candidate",
        "legacy_partial": "历史候选（部分迁移）",
    }.get(str(value or "").strip().lower(), str(value or "未标注"))


def _t4_contribution_type_label(value: Any) -> str:
    return {
        "invention": "方法/系统",
        "improvement": "方法改进",
        "exaptation": "跨域迁移",
        "measurement": "测量/评估",
        "mechanism": "机制解释",
        "theory": "理论",
        "design": "研究设计",
        "benchmark": "基准",
        "algorithm": "算法",
        "evaluation": "评测",
        "empirical": "实证",
    }.get(str(value or "").strip().lower(), str(value or "未由模型归类"))


def _t4_complete_final_card(value: Any) -> dict[str, Any] | None:
    """Return a typed LLM Final Card or no card at all.

    Gate1 uses this helper in both Rich and plain-terminal rendering.  A
    dictionary with a title or a recommendation is not sufficient: every
    researcher-facing explanation must satisfy the complete LLM card contract.
    The caller then presents an operational repair state rather than borrowing
    prose from ``gate1_card``, a Candidate pitch, a score rationale, or a
    local template.
    """

    if not isinstance(value, dict):
        return None
    try:
        from ..ideation.models import FinalIdeaCardTranslation

        return FinalIdeaCardTranslation.model_validate(value).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _summarize_arguments(arguments: dict[str, Any]) -> str:
    fields = []
    for key in ("path", "query", "tool_name", "action", "mode", "summary", "question"):
        value = arguments.get(key)
        if value not in (None, ""):
            fields.append(f"{key}={_compact_text(value, 120)}")
    if fields:
        return "; ".join(fields)
    return f"{len(arguments)} 个参数（完整参数写入 trace，不在 CLI 展开）"


def _humanize_presentation_key(key: str) -> str:
    labels = {
        "current_parameter_preview": "当前参数",
        "selected_parameters": "已选参数",
        "gate1_candidate_cards": "候选 idea 卡片",
        "gate1_selection_brief": "选择建议",
        "candidate_overview": "候选方向（中文决策面板）",
        "candidate_pool_fingerprints": "候选池校验",
        "input_fingerprints": "输入校验",
        "survey_summary": "综述摘要",
        "synthesis_preview": "T3.5 综合摘要",
        "weak_evidence_preview": "弱证据提示",
        "survey_compile_report": "编译报告",
        "survey_insights": "综述洞察",
        "supplement_recommendation": "补充检索建议",
        "how_to_choose": "如何选择",
        "task_id": "任务",
        "run_id": "运行",
        "failures": "失败次数",
        "retry_limit": "自动修复上限",
        "last_error": "最近错误",
        "existing_outputs": "已有输出",
        "error_summary": "当前问题",
        "audit_path": "审计文件",
        "repair_guidance": "定向修复信息",
        "compile_report_path": "编译报告",
        "artifacts_present": "已保存文件",
        "runtime_recovery": "恢复摘要",
        "resume_state_path": "恢复状态",
        "external_executor_launch": "外部执行启动与回传",
    }
    if key in labels:
        return labels[key]
    return key.replace("_", " ")


def _format_recovery_error(value: Any) -> str:
    """Keep an operational failure readable without hiding the original signal."""

    text = " ".join(str(value or "").split())
    return text or "未记录更具体的错误；请先查看保存的诊断文件。"


def _format_t36_assemble_recovery_field(key: str, value: Any) -> str | None:
    """Render a Survey assembly recovery decision without dumping audit JSON.

    The content is entirely deterministic diagnostic information.  It must not
    manufacture a prose-repair recommendation or pretend that a quality
    warning is evidence of a citation error.
    """

    if key == "error_summary":
        return _format_recovery_error(value)
    if key == "audit_path":
        return f"请先查看 `{value or 'drafts/survey/survey_audit.json'}`；其中保存了本次审计的完整检查和原始证据。"
    if key != "repair_guidance":
        return None
    if not isinstance(value, dict):
        return "未保存结构化修复信息；请先查看 survey_audit.json，再决定是否继续定向修复。"

    diversity = value.get("citation_diversity")
    if not isinstance(diversity, dict):
        return "审计没有提供可自动定位的来源文件；继续时只应处理审计明确指出的问题。"

    lines = [
        "引用分布只是一项质量诊断，不代表引用失实，也不会要求用无关文献填充。",
    ]
    total = diversity.get("citation_use_count")
    repeat_limit = diversity.get("repeat_limit")
    concentration = diversity.get("concentration_limit")
    facts: list[str] = []
    if isinstance(total, (int, float)):
        facts.append(f"当前引用出现次数：{int(total)}")
    if isinstance(repeat_limit, (int, float)):
        facts.append(f"单一来源提示阈值：{int(repeat_limit)}")
    if isinstance(concentration, (int, float)):
        facts.append(f"集中度提示阈值：{float(concentration):.0%}")
    if facts:
        lines.append("；".join(facts) + "。")
    offenders = diversity.get("over_repeated")
    if isinstance(offenders, list) and offenders:
        lines.append("需人工判断的集中使用来源：")
        for item in offenders[:6]:
            if not isinstance(item, dict):
                continue
            source = str(item.get("key") or "未标注来源")
            count = item.get("count")
            ratio = item.get("ratio")
            suffix: list[str] = []
            if isinstance(count, (int, float)):
                suffix.append(f"出现 {int(count)} 次")
            if isinstance(ratio, (int, float)):
                suffix.append(f"占 {float(ratio):.0%}")
            section_counts = item.get("section_counts")
            if isinstance(section_counts, dict) and section_counts:
                sections = "、".join(
                    f"{section} {amount} 次"
                    for section, amount in list(section_counts.items())[:5]
                )
                suffix.append(f"涉及 {sections}")
            lines.append(f"- {source}" + ("：" + "；".join(suffix) if suffix else ""))
    else:
        lines.append("未发现单篇来源异常集中使用；若仍需修复，请以 audit 中的具体失败项为准。")
    policy = " ".join(str(diversity.get("repair_policy") or "").split())
    if policy:
        lines.append("审计策略：" + policy)
    return "\n".join(lines)


def _format_t36_compile_recovery_field(key: str, value: Any) -> str | None:
    """Render deterministic compilation recovery facts in a compact form."""

    if key == "error_summary":
        return _format_recovery_error(value)
    if key == "compile_report_path":
        return f"请先查看 `{value or 'drafts/survey/survey_compile_report.json'}`；重试编译不会改写正文。"
    if key != "artifacts_present":
        return None
    if not isinstance(value, dict):
        return "未保存文件清单；请检查 survey 目录和 compile report。"
    present = [str(path) for path, exists in value.items() if exists]
    missing = [str(path) for path, exists in value.items() if not exists]
    lines: list[str] = []
    if present:
        lines.append("已保存：" + "、".join(present))
    if missing:
        lines.append("尚未生成：" + "、".join(missing))
    return "\n".join(lines) or "未发现可确认的编译产物。"


def _format_runtime_recovery_field(key: str, value: Any) -> str | None:
    """Summarize a generic runtime recovery without exposing implementation JSON."""

    if key == "error_summary":
        return _format_recovery_error(value)
    if key == "existing_outputs":
        if not isinstance(value, list) or not value:
            return "本次没有可确认的阶段输出；恢复仍会保留日志和状态。"
        return "已保存且可复用的输出：\n" + "\n".join(f"- {item}" for item in value[:20])
    if key == "resume_state_path":
        return f"恢复状态会写入 `{value}`，用于下一次定向续跑。"
    if key != "runtime_recovery":
        return None
    if not isinstance(value, dict):
        return "未保存结构化恢复摘要；请查看运行日志后再决定是否重试。"
    kind = str(value.get("kind") or "runtime").strip().lower()
    kind_label = {
        "validation": "输出或格式修复窗口耗尽",
        "artifact_validation": "产物校验需要定向修复",
        "budget": "旧版资源窗口恢复记录",
        "max_steps": "旧版步骤窗口恢复记录",
        "provider": "模型服务暂时不可用",
        "environment": "运行环境需要修复",
        "human_input": "当前调用未能取得必要人工输入",
        "runtime": "可恢复的运行时中断",
    }.get(kind, "可恢复的运行时中断")
    task = str(value.get("target_task") or "当前任务")
    lines = [f"任务：{task}", f"恢复类型：{kind_label}"]
    details = value.get("details")
    if isinstance(details, dict):
        retry_count = details.get("retry_count")
        retry_limit = details.get("retry_limit")
        if isinstance(retry_count, int) and isinstance(retry_limit, int):
            lines.append(f"已使用修复尝试：{retry_count}/{retry_limit}")
        provider = details.get("provider")
        if provider:
            lines.append(f"Provider：{provider}")
    return "\n".join(lines)


def _format_external_executor_launch(value: Any) -> str:
    """Render the T5 external wait handoff as operational instructions."""

    if not isinstance(value, dict):
        return "未读取到外部执行器交接信息；请检查 external_executor/report/executor_selection.json。"
    selected = str(value.get("selected_executor") or "unknown")
    root = str(value.get("workspace_root") or "<workspace>")
    lines = [
        f"当前执行器：{selected}",
        f"选择记录：{value.get('selection_path') or 'external_executor/report/executor_selection.json'}",
        f"workspace 根目录：{root}",
        "",
        str(value.get("launch_summary") or ""),
    ]
    commands = value.get("command_lines")
    if isinstance(commands, list) and commands:
        lines.extend(["", "在外部终端执行：", "```bash"])
        lines.extend(str(command) for command in commands if str(command).strip())
        lines.append("```")
    prompt = str(value.get("executor_prompt") or "").strip()
    if prompt:
        lines.extend(["", "向执行器输入：", "```text", prompt, "```"])
    artifacts = value.get("required_artifacts")
    if isinstance(artifacts, list):
        lines.extend(["", "T8 前必须回传："])
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            path = str(artifact.get("path") or "")
            status = str(artifact.get("status") or "待检查")
            if path:
                lines.append(f"- {status}：`{path}`")
    concurrency = str(value.get("concurrency_boundary") or "").strip()
    if concurrency:
        lines.extend(["", "并发边界：", concurrency])
    completion = str(value.get("completion_boundary") or "").strip()
    if completion:
        lines.extend(["", "完成后：", completion])
    return "\n".join(line for line in lines if line is not None)


class HumanInputUnavailable(RuntimeError):
    """Raised when the CLI cannot obtain required human input."""


class HumanInterface(ABC):
    """runtime 与外部用户交互的统一接口。"""

    @abstractmethod
    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        ...

    @abstractmethod
    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        ...

    @abstractmethod
    async def present_gate(
        self, *, gate_id: str, presentation: dict, options: list[dict]
    ) -> dict:
        ...


class CLIHumanInterface(HumanInterface):
    """最小命令行版本的人机接口实现。"""

    CLARIFICATION_EMPTY_RETRIES = 3
    def __init__(
        self,
        *,
        t2_parameter_interpreter: Callable[[str], Awaitable[dict[str, str]]] | None = None,
        t4_directive_interpreter: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        no_color: bool = False,
    ) -> None:
        self._t2_parameter_interpreter = t2_parameter_interpreter
        self._t4_directive_interpreter = t4_directive_interpreter
        self._no_color = bool(no_color)

    def _render_panel(self, *, title: str, lines: list[str], border_style: str) -> None:
        """Render gate context without turning interactive input into a TUI."""

        width = max(80, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "40"},
        )
        console.print(Panel(Text("\n".join(line for line in lines if line)), title=title, border_style=border_style, expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_section(self, title: str) -> None:
        if self._no_color:
            print(f"【{title}】")
            return
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=True,
            color_system="truecolor",
            width=max(80, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns)),
            highlight=False,
            _environ={
                "COLUMNS": str(max(80, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns))),
                "LINES": "40",
            },
        )
        console.print(Text(f"【{title}】", style="bold magenta"))
        print(buffer.getvalue().rstrip())

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        self._render_panel(
            title=f"需要操作确认 · {tool_name}",
            border_style="bright_yellow",
            lines=[
                "该工具可能执行高风险或外部副作用操作，需要用户显式确认。",
                f"输入摘要：{_summarize_arguments(arguments)}",
            ],
        )
        try:
            answer = _read_cli_line("批准执行? [y/N]: ").strip().lower()
        except EOFError as exc:
            raise HumanInputUnavailable(f"{tool_name} 需要用户批准，但当前输入不可用。") from exc
        return answer in {"y", "yes"}

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        lines = [question]
        if suggestions:
            lines.append("参考选项 / 建议：")
            for idx, item in enumerate(suggestions, start=1):
                lines.append(f"  [{idx}] {_compact_text(item, 180)}")
        self._render_panel(title="需要你的输入", border_style="bright_yellow", lines=lines)
        for attempt in range(1, self.CLARIFICATION_EMPTY_RETRIES + 1):
            print("请输入回答（输入完成后，在最后输入单独一行 END，或按 Ctrl+D 提交）:")

            lines: list[str] = []
            try:
                while True:
                    line = _read_cli_line("> ")
                    if line.strip() == "END":
                        break
                    lines.append(line)
            except EOFError:
                pass  # Ctrl+D 正常提交

            answer = "\n".join(lines).strip()
            if answer:
                print("已收到输入，继续处理...")
                return answer

            if attempt < self.CLARIFICATION_EMPTY_RETRIES:
                print("未收到有效输入，请重新输入；如需主动中断请按 Ctrl+C。")

        print("连续多次未收到有效输入，任务将暂停等待明确输入。")
        raise HumanInputUnavailable("ask_human 连续收到空回答，任务已暂停等待明确输入。")

    async def present_gate(
        self, *, gate_id: str, presentation: dict, options: list[dict]
    ) -> dict:
        title = presentation.get("_title")
        description = presentation.get("_description")
        self._render_panel(
            title="需要你确认",
            border_style="bright_yellow",
            lines=[
                str(title or "人工决策"),
                str(description or ""),
                "请选择后继续；系统会保存你的选择，并按该选择继续。",
            ],
        )
        for key, value in presentation.items():
            if key.startswith("_"):
                continue
            if not self._should_render_presentation_field(gate_id, key):
                continue
            if gate_id == "t5_protocol_gate" and key == "protocol_readiness":
                self._render_t5_protocol_readiness(value)
                continue
            if gate_id == "t4_gate1_selection_gate" and key == "candidate_overview":
                self._render_section(_humanize_presentation_key(key))
                self._render_t4_candidate_overview(value)
                continue
            if gate_id == "t4_gate1_selection_gate" and key == "t4_artifact_guide":
                self._render_t4_artifact_guide(value)
                continue
            if gate_id == "t4_gate1_selection_gate" and key == "t4_directive_result":
                self._render_t4_directive_result(value)
                continue
            if gate_id == "t4_gate1_selection_gate" and key == "t4_directive_confirmation":
                self._render_t4_directive_confirmation(value)
                continue
            if gate_id == "t4_prerun_gate" and key == "t4_prerun":
                self._render_t4_prerun_overview(value)
                continue
            rendered = self._format_presentation_value(key, value, gate_id=gate_id)
            if not rendered.strip():
                continue
            self._render_section(_humanize_presentation_key(key))
            print(rendered)
        if gate_id == "t4_gate1_selection_gate":
            self._render_t4_action_options(options)
        else:
            for idx, option in enumerate(options, start=1):
                default_marker = " [默认]" if option.get("is_default") else ""
                print(f"[{idx}] {option['label']}{default_marker}")
                if option.get("parameter_preview"):
                    preview = str(option["parameter_preview"])
                    if "\n" in preview:
                        print("    参数:")
                        for line in preview.splitlines():
                            if line.strip():
                                print(f"      - {line.strip()}")
                    else:
                        print(f"    参数: {preview}")
                if option.get("description"):
                    print(f"    作用: {option['description']}")
        if gate_id == "t4_gate1_selection_gate":
            print("直接输入即可：`推进 D1`、`优化 D2`、`再探索一轮`、`暂停`。也可以输入：`查看 D1`、`对比 D1 和 D3`、`更多操作`。只输入 `D1` 时系统会先追问，不会直接改变候选。")
            print("这是持续对话：可输入多行研究说明；Enter 只换行，输入完成后按 Ctrl+D 提交（或单独一行 `END`）。确认、取消和只读查看也按同样方式提交。")
        selected = None
        while selected is None:
            try:
                if gate_id == "t4_gate1_selection_gate":
                    raw_answer = _read_cli_multiline(
                        prompt="T4> ",
                        continuation_prompt="T4... ",
                        submit_hint="已记录这一行；可以继续补充说明，提交请按 Ctrl+D，或单独输入 END。",
                    )
                    if raw_answer:
                        print(
                            f"[T4] 已提交 {len(raw_answer.splitlines())} 行输入；正在判断是查看、比较还是研究操作。"
                        )
                else:
                    raw_answer = _read_cli_line("请选择: ").strip()
            except EOFError:
                raise HumanInputUnavailable(f"确认步骤 {gate_id} 需要你的选择，但当前输入不可用。") from None
            if gate_id == "t2_literature_param_gate":
                menu_answer = self._parse_option_index(raw_answer, options)
                if menu_answer is not None:
                    selected = options[menu_answer]
                    break
            inline_result = await self._parse_inline_gate_customization_async(
                gate_id,
                raw_answer,
                options,
            )
            if inline_result is not None:
                print(self._format_gate_selection_confirmation(gate_id, inline_result, options))
                return inline_result
            answer = self._parse_option_index(raw_answer, options)
            if answer is None:
                if not raw_answer:
                    default_id = self._default_option_id(gate_id, options)
                    if default_id:
                        selected = next(
                            (option for option in options if (option.get("id") or option.get("key")) == default_id),
                            None,
                        )
                    if selected is None:
                        print(f"请输入 1-{len(options)}，或输入选项别名。")
                        continue
                    break
                if gate_id == "t4_gate1_selection_gate":
                    print("未识别。请直接输入例如：`推进 D1`、`优化 D2`、`查看 D1`、`对比 D1 和 D3`、`更多操作` 或 `暂停`。")
                else:
                    print(f"无效选择: {raw_answer!r}。请输入 1-{len(options)}，或直接输入一句参数修改要求。")
                continue
            selected = options[answer]
        captured: dict[str, str] = {}
        option_id = selected.get("id") or selected.get("key")
        if gate_id == "t2_literature_param_gate" and option_id == "custom":
            captured = await self._collect_t2_customization_line(options)
        else:
            for field_name in selected.get("collect_input", []):
                if gate_id == "t36_corpus_gate" and field_name == "supplement_target_papers":
                    captured[field_name] = self._collect_t36_supplement_target(presentation)
                    continue
                prompt = self._collect_input_prompt(selected, field_name)
                try:
                    captured[field_name] = _read_cli_line(f"{prompt}: ").strip()
                except EOFError as exc:
                    raise HumanInputUnavailable(f"确认步骤 {gate_id} 需要补充 {field_name}，但当前输入不可用。") from exc
        defaults = selected.get("captured_defaults")
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                captured.setdefault(str(key), str(value))
        if gate_id == "t5_executor_gate" and option_id == "codex_cli":
            print(
                "codex_cli 将在 workspace 根目录作为外部执行器启动；"
                "external_executor/expr 仅用于部署 our method 和 baseline，可能消耗较多算力/时间。"
            )
            try:
                confirm = _read_cli_line(
                    "确认允许真实实验？输入 yes 继续，其它任意输入降级为 Claude Code 窗口: "
                ).strip()
            except EOFError as exc:
                raise HumanInputUnavailable("codex_cli 真实执行需要二次确认，但当前输入不可用。") from exc
            if confirm.lower() != "yes":
                option_id = "claude_code_window"
                captured["downgraded_from"] = "codex_cli"
                captured["downgrade_reason"] = "codex_cli confirmation was not yes"
        result = {"option_id": option_id, "captured": captured}
        print(self._format_gate_selection_confirmation(gate_id, result, options))
        return result

    def _render_t4_prerun_overview(self, value: Any) -> None:
        """Render the T4 confirmation from a typed ViewModel, never raw JSON."""

        if not isinstance(value, dict):
            print("暂时无法读取 T4 的输入准备状态；检查 workspace artifact 后再 resume。")
            return
        try:
            from ..ideation.models import T4RunConfig
            from ..ideation.prerun import T4InputInspection
            from ..ui.idea_prerun_renderer import render_t4_prerun

            inspection = T4InputInspection.model_validate(value.get("inspection") or {})
            config = T4RunConfig.model_validate(value.get("run_config") or {})
        except Exception:
            print("暂时无法读取 T4 的输入准备状态；检查 workspace artifact 后再 resume。")
            return
        width = max(80, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "48"},
        )
        render_t4_prerun(inspection, config, console=console)
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_t4_action_options(self, options: list[dict]) -> None:
        """Render Gate1 actions as a compact Rich decision guide."""

        width = max(80, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "48"},
        )
        table = Table(
            expand=True,
            show_header=True,
            show_lines=True,
            box=box.SQUARE,
            header_style="bold bright_yellow",
            border_style="bright_yellow",
        )
        table.add_column("#", width=4, justify="right")
        table.add_column("下一步操作", width=24, overflow="fold")
        table.add_column("会发生什么", ratio=3, overflow="fold")
        for index, option in enumerate(options, start=1):
            option_id = str(option.get("id") or option.get("key") or "")
            if option.get("advanced") or option_id in {"select_or_reframe", "merge", "new_idea", "reanalyze"}:
                continue
            label = {
                "confirm": "确认执行",
                "cancel": "取消并返回",
            }.get(option_id, str(option.get("label") or option_id))
            description = " ".join(str(option.get("description") or "").split())
            table.add_row(str(index), label, description)
        console.print(Panel(table, title="推荐操作", border_style="bright_yellow", expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_t4_artifact_guide(self, value: Any) -> None:
        """Render the durable T4 materials as a researcher-facing file guide."""

        entries = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        if not entries:
            return
        width = max(80, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "48"},
        )
        table = Table(
            expand=True,
            show_header=True,
            show_lines=True,
            box=box.SQUARE,
            header_style="bold bright_cyan",
            border_style="bright_cyan",
        )
        table.add_column("用途", width=16, no_wrap=True, overflow="fold")
        table.add_column("保存位置", ratio=2, overflow="fold")
        table.add_column("包含什么 / 何时打开", ratio=3, overflow="fold")
        for entry in entries:
            label = " ".join(str(entry.get("label") or "研究材料").split())
            path = " ".join(str(entry.get("path") or "").split())
            purpose = " ".join(str(entry.get("purpose") or "").split())
            when_to_open = " ".join(str(entry.get("when_to_open") or "").split())
            detail = "\n".join(part for part in (purpose, when_to_open) if part)
            if path:
                table.add_row(label, Text(path, style="cyan", overflow="fold"), Text(detail, overflow="fold"))
        if not table.row_count:
            return
        note = Text("所有位置均相对当前 workspace 根目录；这些文件已保存，可在暂停或 resume 后继续查看。", style="dim", overflow="fold")
        console.print(Panel(Group(table, note), title="本轮已保存的研究材料", border_style="bright_cyan", expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_t5_protocol_readiness(self, value: Any) -> None:
        """Render a T5 research decision, never the internal handoff JSON."""

        if not isinstance(value, dict):
            print("暂时无法读取 T5 协议状态；请重新编译 T5 交接后再继续。")
            return

        status = str(value.get("status") or "blocked").strip().lower()
        status_copy = {
            "ready": (
                "协议已完整，可进入材料确认",
                "T5 已获得足以约束真实实验的研究设置。下一步只需确认数据、代码、benchmark 或权重等材料。",
                "green",
            ),
            "protocol_decision_required": (
                "研究方案已整理，仍需明确实验设置",
                "这不是运行错误。T4.5 有意保留了部分实验决定，外部执行器不能替研究者猜测这些条件。",
                "bright_yellow",
            ),
            "blocked": (
                "尚缺最小实验定义",
                "T5 已保留现有研究材料，但数据/benchmark、指标、baseline 或主张验证关系中至少有一项尚未被正式定义。",
                "red",
            ),
        }
        status_label, summary, border_style = status_copy.get(
            status,
            ("T5 状态需要检查", "无法识别当前协议状态；请重新编译 T5 交接后再继续。", "red"),
        )
        compiled = value.get("already_compiled") if isinstance(value.get("already_compiled"), dict) else {}
        datasets = [" ".join(str(item).split()) for item in compiled.get("settings_or_datasets") or [] if str(item).strip()]
        metrics = [" ".join(str(item).split()) for item in compiled.get("metrics") or [] if str(item).strip()]
        baselines = [" ".join(str(item).split()) for item in compiled.get("required_baselines") or [] if str(item).strip()]
        claim_count = compiled.get("claim_count")

        def compact(items: list[str], *, empty: str = "尚未记录") -> str:
            if not items:
                return empty
            visible = items[:3]
            suffix = f"；另有 {len(items) - len(visible)} 项" if len(items) > len(visible) else ""
            return "；".join(visible) + suffix

        def decision_detail(raw: Any) -> tuple[str, str, str]:
            text = " ".join(str(raw or "").replace("_", " ").split())
            lowered = text.casefold()
            if "seed" in lowered or "随机种子" in text:
                return ("随机种子策略", "保证不同方法在同一可复现条件下比较", "在实验计划中写明固定种子或 seed ensemble")
            if any(token in lowered for token in ("framework", "simulat", "environment")) or "仿真" in text:
                return ("仿真环境或实验框架", "决定研究对象、可观测变量和结论边界", "在实验计划中写明环境、版本和配置来源")
            if "benchmark" in lowered or "数据集" in text:
                return ("benchmark 或数据集", "决定比较对象和结论能外推到哪里", "在实验计划中写明名称、版本、划分和获取来源")
            if any(token in lowered for token in ("backbone", "model", "agent")) or "骨干" in text:
                return ("模型或 agent backbone", "执行器不能自行决定要使用的基础模型", "在实验计划中写明模型、版本和许可/访问条件")
            if any(token in lowered for token in ("scale", "sample", "episode", "rollout", "budget")) or any(token in text for token in ("规模", "预算", "样本")):
                return ("样本规模、轮次或预算", "决定统计解释范围、资源消耗和停止条件", "在实验计划中写明规模、重复次数和资源上限")
            return (text or "待定实验设置", "该设置会改变实验条件或结论范围，不能由执行器自行推定", "在实验计划或来源明确的项目材料中补充决定")

        width = max(88, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "48"},
        )
        overview = Table.grid(expand=True, padding=(0, 1))
        overview.add_column(style="bold", width=15, no_wrap=True)
        overview.add_column(ratio=1, overflow="fold")
        overview.add_row("当前状态", Text(status_label, style="bold"))
        overview.add_row("这意味着", Text(summary, overflow="fold"))
        overview.add_row("已确定的数据/设置", Text(compact(datasets), overflow="fold"))
        overview.add_row("已确定的指标", Text(compact(metrics), overflow="fold"))
        overview.add_row("已确定的 baseline", Text(compact(baselines), overflow="fold"))
        overview.add_row("已绑定的主张", Text(f"{claim_count if claim_count not in (None, '') else 0} 条主张已有验证关系", overflow="fold"))
        renderables: list[Any] = [overview]

        decisions = value.get("required_decisions") if isinstance(value.get("required_decisions"), list) else []
        requirements = value.get("missing_requirements") if isinstance(value.get("missing_requirements"), list) else []
        if status == "protocol_decision_required" and decisions:
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold bright_yellow", border_style="bright_yellow")
            table.add_column("仍需决定什么", width=24, overflow="fold")
            table.add_column("为什么必须决定", ratio=2, overflow="fold")
            table.add_column("应补充到哪里", ratio=2, overflow="fold")
            for decision in decisions:
                label, why, destination = decision_detail(decision)
                table.add_row(label, why, destination)
            renderables.append(table)
        elif status == "blocked":
            fields: list[Any] = []
            for record in requirements:
                if isinstance(record, dict):
                    raw_fields = record.get("affected_fields")
                    if isinstance(raw_fields, list):
                        fields.extend(raw_fields)
            table = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold red", border_style="red")
            table.add_column("需要补齐", width=28, overflow="fold")
            table.add_column("为什么需要它", ratio=2, overflow="fold")
            table.add_column("修复位置", ratio=2, overflow="fold")
            for field in list(dict.fromkeys(str(item) for item in fields if str(item).strip())) or ["最低实验协议"]:
                label, why, destination = decision_detail(field)
                table.add_row(label, why, destination)
            renderables.append(table)

        next_steps = Table(expand=True, show_header=True, show_lines=True, box=box.SQUARE, header_style="bold cyan", border_style="cyan")
        next_steps.add_column("你的情况", width=28, overflow="fold")
        next_steps.add_column("应选择", width=20, overflow="fold")
        next_steps.add_column("系统接下来会做什么", ratio=2, overflow="fold")
        if status == "ready":
            next_steps.add_row("协议已经完整", "先准备实验材料", "进入资源确认页；核对数据、代码、benchmark 和权重，再选择外部执行器。")
        elif status == "protocol_decision_required":
            next_steps.add_row("已经补完上表设置", "协议已补充，重新编译", "只重新整理和校验 T5 交接，不会重做 T4/T4.5。")
            next_steps.add_row("已有数据、代码或权重可先放入", "先准备实验材料", "允许准备资源；正式运行仍会回到这里核对协议。")
            next_steps.add_row("暂时不准备决定", "暂停协议确认", "保存全部材料；下次 resume 仍从这里开始。")
        else:
            next_steps.add_row("实验计划缺少必要字段", "协议已补充，重新编译", "补充实验计划后重新编译 T5；不会丢失 proposal 或文献材料。")
            next_steps.add_row("研究问题、机制或贡献本身需要改变", "回到 T4 重构", "返回研究方向阶段，保留现有版本供对照。")
        renderables.append(next_steps)
        settings_file = str(value.get("settings_file") or "ideation/exp_plan.yaml")
        proposal_file = str(value.get("proposal_file") or "ideation/proposal/research_proposal.md")
        renderables.append(
            Text(
                f"补充实验设置：{settings_file}  |  查看完整研究方案：{proposal_file}。所有路径均相对当前 workspace 根目录。",
                style="dim",
                overflow="fold",
            )
        )
        console.print(Panel(Group(*renderables), title="T5 研究方案状态", border_style=border_style, expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_t4_directive_confirmation(self, value: Any) -> None:
        """Show a high-signal confirmation without exposing the directive JSON."""

        if not isinstance(value, dict):
            return
        width = max(80, min(150, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "42"},
        )
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(style="bold bright_yellow", width=18, no_wrap=True)
        grid.add_column(ratio=1, overflow="fold")
        labels = (
            ("操作", "action"),
            ("系统会做什么", "what_happens"),
            ("预计时间", "estimated_time"),
            ("版本保留", "version_policy"),
            ("完成后", "next_stage"),
        )
        for label, key in labels:
            text = " ".join(str(value.get(key) or "").split())
            if text:
                grid.add_row(label, Text(text, overflow="fold"))
        identifiers = [str(item) for item in value.get("candidate_ids", []) if str(item).strip()]
        components = [str(item) for item in value.get("component_refs", []) if str(item).strip()]
        if identifiers:
            grid.add_row("Candidate", ", ".join(identifiers))
        if components:
            grid.add_row("所选部分", ", ".join(components))
        console.print(Panel(grid, title="确认此操作", border_style="bright_yellow", expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_t4_directive_result(self, value: Any) -> None:
        """Render a read-only Gate1 result in the same user-facing language."""

        if not isinstance(value, dict):
            return
        width = max(80, min(150, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "42"},
        )
        # A read-only Candidate request must reuse the validated Gate card,
        # not fall back to a separate genome summary. Render it before the
        # narrow response panel below so cards are never nested inside cards.
        candidate_cards = value.get("candidate_cards") if isinstance(value.get("candidate_cards"), list) else []
        candidate = value.get("candidate") if isinstance(value.get("candidate"), dict) else {}
        single_card = candidate.get("candidate_card") if isinstance(candidate.get("candidate_card"), dict) else None
        if single_card is not None:
            candidate_cards = [single_card, *candidate_cards]
        rendered_ids: set[str] = set()
        for card in candidate_cards:
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("id") or "")
            if card_id in rendered_ids:
                continue
            try:
                console.print(self._t4_rich_candidate_card(card))
                rendered_ids.add(card_id)
            except ValueError:
                # Gate1 validation owns missing-card recovery. A stale detail
                # result must remain read-only and cannot invent a fallback.
                continue
        items: list[Any] = [Text(" ".join(str(value.get("summary") or "").split()), overflow="fold")]
        candidates = value.get("candidates") if isinstance(value.get("candidates"), list) else []
        if candidates and str(value.get("kind") or "") != "compare_candidates":
            table = Table(expand=True, show_header=True, header_style="bold cyan", border_style="cyan")
            table.add_column("候选", width=13)
            table.add_column("一句话核心", ratio=3, overflow="fold")
            table.add_column("主要风险", ratio=2, overflow="fold")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                table.add_row(
                    str(candidate.get("candidate_id") or ""),
                    str(candidate.get("one_line_thesis") or ""),
                    str(candidate.get("main_risk") or ""),
                )
            items.append(table)
        if candidate:
            grid = Table.grid(expand=True, padding=(0, 1))
            grid.add_column(style="bold cyan", width=14, no_wrap=True)
            grid.add_column(ratio=1, overflow="fold")
            for label, key in (("候选", "candidate_id"), ("核心主张", "one_line_thesis"), ("主要风险", "main_risk")):
                text = " ".join(str(candidate.get(key) or "").split())
                if text:
                    grid.add_row(label, Text(text, overflow="fold"))
            items.append(grid)
        detail = value.get("detail") if isinstance(value.get("detail"), dict) else {}
        detail_rows = detail.get("rows") if isinstance(detail.get("rows"), list) else []
        if detail_rows:
            row_table = Table(
                expand=True,
                show_header=True,
                show_lines=True,
                box=box.SQUARE,
                header_style="bold cyan",
                border_style="cyan",
            )
            row_table.add_column("项目", width=16, overflow="fold")
            row_table.add_column("简述", ratio=3, overflow="fold")
            row_table.add_column("来源 / 备注", ratio=2, overflow="fold")
            for row in detail_rows:
                if not isinstance(row, dict):
                    continue
                row_table.add_row(
                    " ".join(str(row.get("label") or row.get("field") or "").split()),
                    " ".join(str(row.get("summary") or row.get("content") or "").split()),
                    " ".join(str(row.get("source") or row.get("note") or "").split()),
                )
            if row_table.row_count:
                items.append(row_table)
        detail_items = detail.get("items") if isinstance(detail.get("items"), list) else []
        if detail_items:
            item_table = Table(
                expand=True,
                show_header=True,
                show_lines=True,
                box=box.SQUARE,
                header_style="bold cyan",
                border_style="cyan",
            )
            item_table.add_column("#", width=4, justify="right", no_wrap=True)
            item_table.add_column("内容", ratio=1, overflow="fold")
            for index, item in enumerate(detail_items, start=1):
                item_table.add_row(str(index), " ".join(str(item).split()))
            items.append(item_table)
        detail_path = " ".join(str(detail.get("path") or "").split())
        if detail_path:
            extra_paths = detail.get("paths") if isinstance(detail.get("paths"), list) else []
            detail_paths = [detail_path, *[str(item) for item in extra_paths if str(item).strip()]]
        else:
            detail_paths = detail.get("paths") if isinstance(detail.get("paths"), list) else []
        if detail_paths:
            paths_table = Table(
                expand=True,
                show_header=False,
                show_lines=True,
                box=box.SQUARE,
                border_style="cyan",
                padding=(0, 1),
            )
            paths_table.add_column(style="bold cyan", width=14, no_wrap=True)
            paths_table.add_column(ratio=1, overflow="fold")
            for path in detail_paths:
                normalized = " ".join(str(path or "").split())
                if normalized:
                    paths_table.add_row("产物路径", Text(normalized, overflow="fold"))
            items.append(paths_table)
        advanced_operations = value.get("advanced_operations") if isinstance(value.get("advanced_operations"), list) else []
        if advanced_operations:
            advanced_table = Table(expand=True, show_header=True, header_style="bold yellow", border_style="yellow")
            advanced_table.add_column("操作", ratio=1, overflow="fold")
            advanced_table.add_column("何时使用", ratio=3, overflow="fold")
            for item in advanced_operations:
                if not isinstance(item, dict):
                    continue
                advanced_table.add_row(
                    " ".join(str(item.get("label") or "").split()),
                    " ".join(str(item.get("description") or "").split()),
                )
            items.append(advanced_table)
        comparison = value.get("comparison") if isinstance(value.get("comparison"), dict) else {}
        comparison_ids = comparison.get("candidate_ids") if isinstance(comparison.get("candidate_ids"), list) else []
        if comparison_ids:
            comparison_table = Table(expand=True, show_header=True, header_style="bold magenta", border_style="magenta")
            comparison_table.add_column("候选", width=12)
            comparison_table.add_column("核心命题", ratio=2, overflow="fold")
            comparison_table.add_column("机制", ratio=2, overflow="fold")
            comparison_table.add_column("主要风险", ratio=2, overflow="fold")
            theses = comparison.get("core_theses") if isinstance(comparison.get("core_theses"), list) else []
            mechanisms = comparison.get("mechanisms") if isinstance(comparison.get("mechanisms"), list) else []
            risks = comparison.get("risks") if isinstance(comparison.get("risks"), list) else []
            for index, public_id in enumerate(comparison_ids):
                comparison_table.add_row(
                    str(public_id),
                    " ".join(str(theses[index] if index < len(theses) else "").split()),
                    " ".join(str(mechanisms[index] if index < len(mechanisms) else "").split()),
                    " ".join(str(risks[index] if index < len(risks) else "").split()),
                )
            items.append(comparison_table)
        composition = value.get("composition") if isinstance(value.get("composition"), dict) else {}
        if composition:
            composition_table = Table.grid(expand=True, padding=(0, 1))
            composition_table.add_column(style="bold magenta", width=19, no_wrap=True)
            composition_table.add_column(ratio=1, overflow="fold")
            for label, key in (
                ("组合编号", "composition_id"),
                ("组合方式", "composition_type"),
                ("系统建议", "recommended_action"),
                ("Compatibility", "explanation"),
            ):
                text = " ".join(str(composition.get(key) or "").split())
                if text:
                    composition_table.add_row(label, Text(text, overflow="fold"))
            donors = composition.get("gene_donor_map") if isinstance(composition.get("gene_donor_map"), dict) else {}
            if donors:
                composition_table.add_row("Gene Donor Map", Text(", ".join(f"{key}: {item}" for key, item in donors.items()), overflow="fold"))
            repairs = composition.get("required_repairs") if isinstance(composition.get("required_repairs"), list) else []
            if repairs:
                composition_table.add_row("需要调整", Text("；".join(str(item) for item in repairs), overflow="fold"))
            items.extend([Text("Compatibility Check", style="bold magenta"), composition_table])
        path = str(value.get("artifact") or "")
        if path:
            items.append(Text(f"已保存记录：{path}", style="dim", overflow="fold"))
        console.print(Panel(Group(*items), title=str(value.get("title") or "T4 更新"), border_style="bright_cyan", expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    def _render_t4_candidate_overview(self, value: Any) -> None:
        """Render a decision-first Gate1 overview; details stay available on demand."""

        if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
            self._render_section("候选方向（中文决策面板）")
            print("候选方向概览暂不可用；请检查 ideation/_candidate_directions.json。")
            return
        candidates = [item for item in value["candidates"] if isinstance(item, dict)]
        width = max(80, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "48"},
        )
        remaining_count = value.get("remaining_candidate_count")
        guide_text = "先比较 Portfolio 中最成熟的 1-3 个 Candidate；完整 Active Population 和所有历史版本均已保留。"
        if isinstance(remaining_count, int) and remaining_count:
            guide_text += f" 还有 {remaining_count} 个 Active Candidate 未在首屏展开，可按需查看。"
        guide = Text(guide_text, overflow="fold")
        console.print(Panel(guide, title="研究方向选择", border_style="bright_cyan", expand=True))

        # The comparison table is intentionally only a decision index.  The
        # complete LLM-authored explanation belongs in the card immediately
        # below; forcing every semantic field into a wide table made the
        # terminal hard to scan and hid the actual differences between Ideas.
        index = Table(expand=True, show_header=True, header_style="bold cyan", border_style="cyan")
        index.add_column("ID", style="bold", width=5, no_wrap=True)
        index.add_column("短标题", ratio=3, overflow="fold")
        index.add_column("角色", width=10, overflow="fold")
        index.add_column("主要判断", ratio=3, overflow="fold")
        missing_final_cards = []
        for item in candidates:
            evolution_score = item.get("evolution_score") if isinstance(item.get("evolution_score"), dict) else {}
            final_card = _t4_complete_final_card(item.get("final_idea_card"))
            if not final_card:
                missing_final_cards.append(str(item.get("id") or "?"))
                continue
            index.add_row(
                str(item.get("id") or "?"),
                str(final_card.get("short_title") or ""),
                _t4_portfolio_role_label(item.get("portfolio_role")),
                str(final_card.get("recommendation") or final_card.get("innovation_delta") or ""),
            )
        if missing_final_cards:
            console.print(
                Panel(
                    Text(
                        "当前 Portfolio 尚未拥有完整的 LLM Idea Card，不能显示半成品科研解释。"
                        "请在 T4 恢复决策中选择继续 LLM 卡片修复。"
                        f"缺少卡片：{'、'.join(missing_final_cards)}",
                        overflow="fold",
                    ),
                    title="T4 卡片修复",
                    border_style="yellow",
                    expand=True,
                )
            )
            rendered = buffer.getvalue().rstrip()
            if rendered:
                print(rendered)
            return
        # Validate every visible Card before rendering any one of them. A
        # partial deck would make the Human Gate look selectable despite a
        # missing LLM explanation, so this defensive path fails closed into an
        # operational repair panel instead of showing a mix of full and
        # fallback cards.
        try:
            rendered_cards = [self._t4_rich_candidate_summary(item) for item in candidates]
        except Exception as exc:
            console.print(
                Panel(
                    Text(
                        "Portfolio Idea Card 未能完整渲染；不会用旧字段或固定模板补全科研解释。"
                        "请返回 T4 恢复决策并选择继续 LLM 卡片修复。"
                        f"诊断：{' '.join(str(exc).split())[:500]}",
                        overflow="fold",
                    ),
                    title="T4 卡片修复",
                    border_style="yellow",
                    expand=True,
                )
            )
            rendered = buffer.getvalue().rstrip()
            if rendered:
                print(rendered)
            return
        for card in rendered_cards:
            console.print(card)

        # The cards establish what each Candidate means. Only then does a
        # compact comparison index help the researcher make a final choice.
        console.print(Panel(Text("先阅读上方 Idea Cards；下表仅汇总差异，便于最后比较与选择。", overflow="fold"), title="候选比较摘要", border_style="cyan", expand=True))
        console.print(index)

        hint = str(value.get("input_hint") or "")
        if hint:
            console.print(Panel(Text(hint, overflow="fold"), title="选择", border_style="bright_yellow", expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print(rendered)

    @staticmethod
    def _t4_card_render_failure_panel(item: dict[str, Any]) -> Panel:
        candidate_id = str(item.get("id") or "?")
        title = str(item.get("title") or item.get("full_title") or "未命名候选")
        diagnostics = item.get("projection_diagnostics") if isinstance(item.get("projection_diagnostics"), list) else []
        detail = "；".join(str(value).strip() for value in diagnostics if str(value).strip())
        body = Text(
            "该 Candidate 的一个展示组件未能渲染。候选、谱系和已保存的评分没有被删除；"
            "可查看其结构化文件，或选择继续演化以补全该卡片。"
            + ("\n已记录诊断：" + detail if detail else ""),
            overflow="fold",
        )
        return Panel(body, title=f"[bold]{candidate_id} · {title}[/bold]", border_style="yellow", expand=True)

    @staticmethod
    def _t4_legacy_rich_candidate_summary(item: dict[str, Any]) -> Panel:
        """Render the normal, decision-first Candidate card for Gate1.

        This surface intentionally excludes artifact paths, lineage IDs, raw
        enums and aggregate readiness.  Those are useful in Debug/Trace and
        explicit read-only inspection, but they should not be prerequisites
        for a researcher deciding whether to advance, refine, compare or pause
        a candidate.
        """

        candidate_id = str(item.get("id") or "?")
        lane = str(item.get("lane") or "候选方向")
        evolution_score = item.get("evolution_score") if isinstance(item.get("evolution_score"), dict) else {}
        final_card = _t4_complete_final_card(item.get("final_idea_card"))
        if not final_card:
            raise ValueError("Final Idea Card is required for a Gate1 candidate summary")
        title = str(final_card.get("short_title") or "").strip()
        if not title:
            raise ValueError("Final Idea Card short_title is required for a Gate1 candidate summary")
        final_risks = final_card.get("risks_and_boundaries") if isinstance(final_card.get("risks_and_boundaries"), list) else []
        implications = final_card.get("implications") if isinstance(final_card.get("implications"), list) else []
        stakeholders = final_card.get("affected_stakeholders_or_processes") if isinstance(final_card.get("affected_stakeholders_or_processes"), list) else []

        def text(raw: Any) -> Text:
            return Text(" ".join(str(raw or "").split()), overflow="fold")

        # First screen: a researcher should be able to answer “what is it,
        # why now, who benefits, and what do I do next” without reading every
        # retained LLM paragraph or internal runtime label.  The full Card is
        # intentionally preserved behind the read-only detail commands.
        overview = Table(
            expand=True,
            show_header=False,
            show_lines=True,
            box=box.SQUARE,
            border_style="cyan",
            padding=(0, 1),
        )
        overview.add_column(style="bold cyan", width=19, no_wrap=True)
        overview.add_column(ratio=1, overflow="fold")
        overview.add_row("研究命题", text(_t4_card_excerpt(final_card.get("plain_language_summary"), max_chars=190)))
        overview.add_row("研究价值", text(_t4_card_excerpt(final_card.get("why_it_matters"), max_chars=145)))
        overview.add_row("现实 / 应用意义", text(_t4_card_excerpt(final_card.get("real_world_significance"), max_chars=140)))
        if stakeholders:
            overview.add_row("影响对象", text(_t4_card_list_excerpt(stakeholders, max_items=2, max_chars=46)))
        overview.add_row("建议下一步", text(_t4_card_excerpt(final_card.get("recommendation"), max_chars=135)))

        components: list[Any] = [Text("研究摘要", style="bold cyan"), overview]
        rationales = evolution_score.get("rationales") if isinstance(evolution_score.get("rationales"), dict) else {}
        bottleneck = str(evolution_score.get("dominant_bottleneck") or "").strip()

        contributions = item.get("contributions") if isinstance(item.get("contributions"), list) else []
        if contributions:
            contribution_table = Table(
                expand=True, show_header=True, show_lines=True, box=box.SQUARE,
                header_style="bold green", border_style="green",
            )
            contribution_table.add_column("核心贡献", width=14, no_wrap=True)
            contribution_table.add_column("提出什么", ratio=3, overflow="fold")
            contribution_table.add_column("若成立的改变", ratio=2, overflow="fold")
            for position, contribution in enumerate(contributions[:2], start=1):
                if not isinstance(contribution, dict):
                    continue
                kind = _t4_contribution_type_label(contribution.get("type"))
                statement = " ".join(str(contribution.get("statement") or "").split())
                change = " ".join(str(contribution.get("what_changes_if_true") or "").split())
                change_preview = (
                    "与提出内容相同，完整表述见详情。"
                    if change and change == statement
                    else _t4_card_excerpt(change, max_chars=100, max_sentences=1)
                )
                contribution_table.add_row(
                    f"贡献 {position} · {kind}",
                    text(_t4_card_excerpt(statement, max_chars=120, max_sentences=1)),
                    text(change_preview),
                )
            if contribution_table.row_count:
                components.extend([Text("核心贡献", style="bold green"), contribution_table])

        hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
        if hypotheses:
            hypothesis_table = Table(
                expand=True, show_header=True, show_lines=True, box=box.SQUARE,
                header_style="bold yellow", border_style="yellow",
            )
            hypothesis_table.add_column("可检验假设", width=12, no_wrap=True)
            hypothesis_table.add_column("主张", ratio=3, overflow="fold")
            hypothesis_table.add_column("观察信号 / 判据", ratio=2, overflow="fold")
            for position, hypothesis in enumerate(hypotheses[:2], start=1):
                if not isinstance(hypothesis, dict):
                    continue
                statement = " ".join(str(hypothesis.get("statement") or "").split())
                signal = " ".join(
                    str(hypothesis.get("prediction") or hypothesis.get("observable_prediction") or "").split()
                )
                signal_preview = (
                    "未提供独立观察信号，完整判别测试见详情。"
                    if not signal or signal == statement
                    else _t4_card_excerpt(signal, max_chars=100, max_sentences=1)
                )
                hypothesis_table.add_row(
                    f"H{position}",
                    text(_t4_card_excerpt(statement, max_chars=120, max_sentences=1)),
                    text(signal_preview),
                )
            if hypothesis_table.row_count:
                components.extend([Text("关键假设与验证信号", style="bold yellow"), hypothesis_table])

        score_table = Table(
            expand=True, show_header=True, show_lines=True, box=box.SQUARE,
            header_style="bold blue", border_style="blue",
        )
        score_table.add_column("评分维度", width=15, no_wrap=True)
        score_table.add_column("分数", width=7, justify="center", no_wrap=True)
        score_table.add_column("为什么得到这个分数", ratio=3, overflow="fold")
        for key, label in (
            ("research_value", "研究价值"),
            ("mechanism_integrity", "机制完整性"),
            ("contribution_distinctiveness", "贡献差异性"),
        ):
            value = (evolution_score.get("dimensions") or {}).get(key) if isinstance(evolution_score.get("dimensions"), dict) else None
            if value is None:
                continue
            rationale = " ".join(str(rationales.get(key) or "").split())
            score_table.add_row(
                label,
                f"{value}/5",
                text(
                    _t4_card_excerpt(rationale, max_chars=130, max_sentences=1)
                    if rationale
                    else "评分理由尚未形成；请查看该候选的完整评分。"
                ),
            )
        if score_table.row_count:
            components.extend([Text("三项决策评分", style="bold blue"), score_table])

        evidence_risk = Table(
            expand=True,
            show_header=False,
            show_lines=True,
            box=box.SQUARE,
            border_style="red",
            padding=(0, 1),
        )
        evidence_risk.add_column(style="bold red", width=14, no_wrap=True)
        evidence_risk.add_column(ratio=1, overflow="fold")
        if bottleneck:
            evidence_risk.add_row("当前扣分点", text(_t4_card_excerpt(bottleneck, max_chars=135, max_sentences=1)))
        if final_risks:
            evidence_risk.add_row("主要风险", text(_t4_card_list_excerpt(final_risks, max_items=2, max_chars=100)))
        if evidence_risk.row_count:
            components.extend([Text("风险与下一步", style="bold red"), evidence_risk])

        components.append(
            Text(
                f"上方为简述。查看详情：`查看 {candidate_id}`（完整说明）｜`查看 {candidate_id} 的证据 / 假设 / 贡献 / 谱系`。"
                "所有查看均为只读：不会调用模型、确认操作或改变当前版本。",
                style="dim",
                overflow="fold",
            )
        )
        return Panel(
            Group(*components),
            title=f"[bold]{candidate_id} · {lane} · {title}[/bold]",
            border_style="bright_cyan" if lane == "主方向" else "cyan",
            expand=True,
        )

    @staticmethod
    def _t4_legacy_rich_candidate_card(item: dict[str, Any]) -> Panel:
        """Render a complete LLM Final Card plus durable non-prose facts.

        Gate1 admits a visible Portfolio Candidate only after the runtime has
        validated a full ``FinalIdeaCardTranslation``.  This renderer therefore
        never falls back to ``gate1_card.selection_advice``, a score rationale,
        or a locally composed sentence when a researcher-facing explanation is
        missing.  Candidate IDs, route, lineage, source paths, and numeric
        scores remain controller-owned facts; all scientific interpretation is
        copied from an LLM-authored Candidate, ScoreReport, or Final Card.
        """

        candidate_id = str(item.get("id") or "?")
        lane = str(item.get("lane") or "候选方向")
        internal_id = str(item.get("internal_id") or "").strip()
        final_card = _t4_complete_final_card(item.get("final_idea_card"))
        if final_card is None:
            raise ValueError("Final Idea Card requires LLM repair before rendering")
        required_card_fields = (
            "short_title",
            "plain_language_summary",
            "why_it_matters",
            "representative_scenario",
            "real_world_significance",
            "current_failure",
            "scientific_technical_core",
            "contribution_type_label",
            "innovation_type",
            "innovation_delta",
            "non_routine_explanation",
            "relationship_to_portfolio",
            "composition_guidance",
            "recommendation",
            "bottleneck_explanation",
            "evidence_status_summary",
        )
        missing = [field for field in required_card_fields if not " ".join(str(final_card.get(field) or "").split())]
        if missing:
            raise ValueError("Final Idea Card requires LLM repair before rendering: " + ", ".join(missing))
        title = str(final_card["short_title"]).strip()

        def text(raw: Any) -> Text:
            return Text(" ".join(str(raw or "").split()), overflow="fold")

        def detail_table(*, style: str, border_style: str, label_width: int = 16) -> Table:
            table = Table(
                expand=True,
                show_header=False,
                show_lines=True,
                box=box.SQUARE,
                border_style=border_style,
                padding=(0, 1),
            )
            table.add_column(style=style, width=label_width, no_wrap=True)
            table.add_column(ratio=1, overflow="fold")
            return table

        def add_if_present(table: Table, label: str, value: Any) -> None:
            if str(value or "").strip():
                table.add_row(label, text(value))

        overview = detail_table(style="bold cyan", border_style="cyan")
        for label, value in (
            ("组合角色", _t4_portfolio_role_label(item.get("portfolio_role"))),
            ("来源通道", _t4_origin_label(item.get("origin"))),
            ("候选阶段", _t4_candidate_stage_label(item.get("candidate_stage") or item.get("maturity"))),
            ("主贡献类型", final_card["contribution_type_label"]),
            ("Idea Family", item.get("mechanism_family")),
            ("内部谱系 ID", internal_id),
            ("父候选", "、".join(str(value) for value in item.get("parent_ids") or [])),
            ("一句话命题", final_card["plain_language_summary"]),
            ("核心命题", final_card.get("core_thesis")),
            ("为何值得研究", final_card["why_it_matters"]),
            ("当前问题", final_card["current_failure"]),
            ("科学 / 技术核心", final_card["scientific_technical_core"]),
            ("代表性场景", final_card["representative_scenario"]),
            ("现实意义", final_card["real_world_significance"]),
            ("选择建议", final_card["recommendation"]),
        ):
            add_if_present(overview, label, value)
        components: list[Any] = [overview]

        evolution_score = item.get("evolution_score") if isinstance(item.get("evolution_score"), dict) else {}
        dimensions = evolution_score.get("dimensions") if isinstance(evolution_score.get("dimensions"), dict) else {}
        if dimensions:
            readiness = evolution_score.get("overall_readiness")
            uncertainty = evolution_score.get("uncertainty")
            components.append(
                Text(
                    f"候选成熟度：{_t4_candidate_stage_label(item.get('maturity') or 'evolved')} · 当前就绪度：{readiness}/5 · 不确定性：{uncertainty}",
                    style="bold blue",
                    overflow="fold",
                )
            )
            evolution_table = Table(
                expand=True,
                show_header=True,
                show_lines=True,
                box=box.SQUARE,
                header_style="bold blue",
                border_style="blue",
            )
            evolution_table.add_column("独立评分维度", width=22, overflow="fold")
            evolution_table.add_column("评分", width=8, justify="center")
            for key, label in (
                ("research_value", "研究价值"),
                ("mechanism_integrity", "机制完整性"),
                ("contribution_distinctiveness", "贡献差异性"),
                ("evidence_calibration", "证据校准"),
                ("validation_tractability", "验证可实施性"),
            ):
                if dimensions.get(key) is not None:
                    evolution_table.add_row(label, f"{dimensions[key]}/5")
            components.extend([Text("独立科研评分", style="bold blue"), evolution_table])
            score_summary = detail_table(style="bold blue", border_style="blue")
            add_if_present(score_summary, "主要优势", evolution_score.get("dominant_strength"))
            add_if_present(score_summary, "主要瓶颈", evolution_score.get("dominant_bottleneck"))
            add_if_present(score_summary, "科研上行空间", evolution_score.get("scientific_upside_rationale"))
            if score_summary.row_count:
                components.append(score_summary)

        profile_fit = evolution_score.get("profile_fit") if isinstance(evolution_score.get("profile_fit"), dict) else {}
        if profile_fit:
            profile_table = detail_table(style="bold magenta", border_style="magenta", label_width=18)
            add_if_present(profile_table, "投稿取向", profile_fit.get("profile_type"))
            if profile_fit.get("overall_fit") is not None:
                profile_table.add_row("取向适配度", text(f"{profile_fit.get('overall_fit')}/5"))
            add_if_present(profile_table, "模型解释", profile_fit.get("rationale"))
            if profile_table.row_count:
                components.extend([Text("论文取向适配", style="bold magenta"), profile_table])

        innovation_table = detail_table(style="bold magenta", border_style="magenta")
        innovation_table.add_row("创新性质", text(final_card["innovation_type"]))
        innovation_table.add_row("相对变化", text(final_card["innovation_delta"]))
        innovation_table.add_row("非惯例理由", text(final_card["non_routine_explanation"]))
        components.extend([Text("核心创新", style="bold magenta"), innovation_table])

        contributions = item.get("contributions") if isinstance(item.get("contributions"), list) else []
        if contributions:
            contribution_table = Table(
                expand=True,
                show_header=True,
                show_lines=True,
                box=box.SQUARE,
                header_style="bold green",
                border_style="green",
            )
            contribution_table.add_column("贡献", width=10, no_wrap=True)
            contribution_table.add_column("类型", width=14, overflow="fold")
            contribution_table.add_column("贡献命题", ratio=2, overflow="fold")
            contribution_table.add_column("若成立会改变什么", ratio=2, overflow="fold")
            for contribution in contributions:
                if not isinstance(contribution, dict):
                    continue
                contribution_table.add_row(
                    str(contribution.get("id") or ""),
                    _t4_contribution_type_label(contribution.get("type")),
                    text(contribution.get("statement")),
                    text(contribution.get("what_changes_if_true")),
                )
            components.extend([Text("贡献包", style="bold green"), contribution_table])

        hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
        if hypotheses:
            hypothesis_table = Table(
                expand=True,
                show_header=True,
                show_lines=True,
                box=box.SQUARE,
                header_style="bold green",
                border_style="green",
            )
            hypothesis_table.add_column("假设", width=9, no_wrap=True)
            hypothesis_table.add_column("命题", ratio=2, overflow="fold")
            hypothesis_table.add_column("机制", ratio=2, overflow="fold")
            hypothesis_table.add_column("预测 / 判别测试", ratio=3, overflow="fold")
            hypothesis_table.add_column("证据状态", width=14, overflow="fold")
            for hypothesis in hypotheses:
                if not isinstance(hypothesis, dict):
                    continue
                prediction = " ".join(str(hypothesis.get("prediction") or hypothesis.get("observable_prediction") or "").split())
                test = " ".join(str(hypothesis.get("test") or hypothesis.get("discriminating_test") or "").split())
                hypothesis_table.add_row(
                    str(hypothesis.get("id") or ""),
                    text(hypothesis.get("statement")),
                    text(hypothesis.get("mechanism")),
                    Text("预测：" + prediction + "\n测试：" + test, overflow="fold"),
                    text(hypothesis.get("evidence_status")),
                )
            components.extend([Text("候选假设", style="bold green"), hypothesis_table])

        minimum = item.get("minimum_validation") if isinstance(item.get("minimum_validation"), dict) else {}
        minimum_table = detail_table(style="bold yellow", border_style="yellow")
        for label, key in (("数据 / 任务", "dataset"), ("基线", "baseline"), ("指标", "metric"), ("预期信号", "expected_signal")):
            add_if_present(minimum_table, label, minimum.get(key))
        add_if_present(minimum_table, "协议证据状态", minimum.get("evidence_status"))
        refs = minimum.get("source_refs") if isinstance(minimum.get("source_refs"), list) else []
        if refs:
            minimum_table.add_row("协议来源", text("；".join(str(ref) for ref in refs if str(ref).strip())))
        if minimum_table.row_count:
            components.extend([Text("最小验证", style="bold yellow"), minimum_table])

        impact_table = detail_table(style="bold bright_cyan", border_style="bright_cyan")
        implications = final_card.get("implications") if isinstance(final_card.get("implications"), list) else []
        for implication in implications:
            if not isinstance(implication, dict):
                continue
            conditions = implication.get("conditions") if isinstance(implication.get("conditions"), list) else []
            condition_text = "；".join(str(value) for value in conditions if str(value).strip())
            statement = str(implication.get("statement") or "").strip()
            if statement:
                label = f"{implication.get('implication_type', '影响')} [{implication.get('evidence_status', 'unknown')}]"
                impact_table.add_row(label, text(statement + (f"（条件：{condition_text}）" if condition_text else "")))
        impact_conditions = final_card.get("conditions_for_impact") if isinstance(final_card.get("conditions_for_impact"), list) else []
        if impact_conditions:
            impact_table.add_row("影响条件", text("；".join(str(value) for value in impact_conditions if str(value).strip())))
        if impact_table.row_count:
            components.extend([Text("研究意义与影响", style="bold bright_cyan"), impact_table])

        relationship = detail_table(style="bold bright_magenta", border_style="bright_magenta")
        relationship.add_row("与其他候选的关系", text(final_card["relationship_to_portfolio"]))
        dependencies = item.get("dependency_display_ids") if isinstance(item.get("dependency_display_ids"), list) else []
        if dependencies:
            relationship.add_row("明确依赖", text("、".join(str(value) for value in dependencies if str(value).strip())))
        relationship.add_row("组合建议", text(final_card["composition_guidance"]))
        relationship.add_row("针对性建议", text(final_card["recommendation"]))
        relationship.add_row("瓶颈解释", text(final_card["bottleneck_explanation"]))
        components.extend([Text("候选关系与下一步", style="bold bright_magenta"), relationship])

        lineage = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
        if lineage or item.get("parent_ids"):
            lineage_table = detail_table(style="bold cyan", border_style="cyan")
            for label, value in (
                ("来源 Route", _t4_origin_label(lineage.get("route") or item.get("origin"))),
                ("形成方式", lineage.get("created_by")),
                ("父候选", "、".join(str(value) for value in lineage.get("parent_ids") or item.get("parent_ids") or [])),
                ("演化计划", lineage.get("evolution_plan_id")),
                ("复杂度诊断", lineage.get("complexity_inflation")),
            ):
                add_if_present(lineage_table, label, value)
            if lineage_table.row_count:
                components.extend([Text("演化谱系", style="bold cyan"), lineage_table])

        cross_domain_sources = item.get("cross_domain_sources") if isinstance(item.get("cross_domain_sources"), list) else []
        cross_domain_relation = str(item.get("cross_domain_relation") or "").strip()
        if cross_domain_sources or cross_domain_relation:
            bridge_table = detail_table(style="bold bright_magenta", border_style="bright_magenta")
            if cross_domain_sources:
                bridge_table.add_row("桥接来源", text("、".join(str(value) for value in cross_domain_sources if str(value).strip())))
            add_if_present(bridge_table, "可迁移关系", cross_domain_relation)
            components.extend([Text("跨领域信息", style="bold bright_magenta"), bridge_table])

        evidence_chain = item.get("evidence_chain") if isinstance(item.get("evidence_chain"), list) else []
        if evidence_chain:
            chain_table = Table(
                expand=True, show_header=True, show_lines=True, box=box.SQUARE,
                header_style="bold red", border_style="red",
            )
            chain_table.add_column("来源", ratio=1, overflow="fold")
            chain_table.add_column("论文 / 材料观察", ratio=2, overflow="fold")
            chain_table.add_column("导向当前设计的含义", ratio=2, overflow="fold")
            chain_table.add_column("等级", width=12, overflow="fold")
            for link in evidence_chain:
                if isinstance(link, dict):
                    chain_table.add_row(
                        str(link.get("ref") or ""),
                        text(link.get("observation")),
                        text(link.get("implication")),
                        str(link.get("evidence_level") or ""),
                    )
            components.extend([Text("证据链", style="bold red"), chain_table])

        support = item.get("supporting_papers") if isinstance(item.get("supporting_papers"), list) else []
        if support:
            sources = Table(
                expand=True, show_header=True, show_lines=True, box=box.SQUARE,
                header_style="bold cyan", border_style="cyan",
            )
            sources.add_column("论文与引用", ratio=2, overflow="fold")
            sources.add_column("论文阅读笔记", ratio=2, overflow="fold")
            sources.add_column("已用证据", ratio=3, overflow="fold")
            for paper in support:
                if isinstance(paper, dict):
                    sources.add_row(
                        f"{paper.get('title') or ''}\n{paper.get('citation') or ''}",
                        str(paper.get("note_path") or paper.get("source_file") or ""),
                        str(paper.get("claim_used") or paper.get("claim") or ""),
                    )
            components.extend([Text("参考论文与阅读笔记", style="bold cyan"), sources])

        artifact_index = item.get("artifact_index") if isinstance(item.get("artifact_index"), dict) else {}
        artifact_paths = item.get("artifact_paths") if isinstance(item.get("artifact_paths"), list) else []
        if artifact_index or artifact_paths:
            artifacts = detail_table(style="bold dim", border_style="dim")
            labels = {"candidate": "Candidate", "score": "评分", "lineage": "谱系", "round": "演化轮次", "population": "Population"}
            for key, value in artifact_index.items():
                if str(value).strip():
                    artifacts.add_row(labels.get(str(key), str(key)), text(value))
            for value in artifact_paths:
                if str(value).strip():
                    artifacts.add_row("附加产物", text(value))
            components.extend([Text("产物路径", style="bold dim"), artifacts])

        components.append(
            Text(
                f"可输入：选择 {candidate_id}；继续演化 {candidate_id}；只优化 {candidate_id}；"
                f"查看 {candidate_id} 的评分 / 证据 / 谱系 / 假设 / 贡献。读取操作不调用模型；进化会创建新版本并保留当前候选。",
                style="dim",
                overflow="fold",
            )
        )
        return Panel(
            Group(*components),
            title=f"[bold]{candidate_id} · {title}[/bold]",
            border_style="bright_cyan" if lane == "主方向" else "cyan",
            expand=True,
        )

    @staticmethod
    def _t4_rich_candidate_summary(item: dict[str, Any]) -> Panel:
        """Render every Gate overview through the shared CandidateCardRenderer."""

        return CandidateCardRenderer.summary(CandidateViewModel.from_mapping(item))

    @staticmethod
    def _t4_rich_candidate_card(item: dict[str, Any]) -> Panel:
        """Render explicit Candidate inspection through the shared full-card view."""

        return CandidateCardRenderer.detail(CandidateViewModel.from_mapping(item))

    @staticmethod
    def _should_render_presentation_field(gate_id: str, key: str) -> bool:
        """Keep interactive gates focused on a single decision surface."""

        if gate_id == "t4_gate1_selection_gate":
            return key in {"candidate_overview", "t4_artifact_guide", "t4_directive_result", "t4_directive_confirmation"}
        if gate_id == "t2_literature_param_gate":
            return key == "current_parameter_preview"
        if gate_id == "t5_protocol_gate":
            # Old paused workspaces may still persist full handoff/report
            # previews. Resume refreshes ``protocol_readiness`` from current
            # artifacts; every other field is internal audit detail and must
            # not bury the actual research decision under JSON.
            return key == "protocol_readiness"
        return True

    async def _collect_t2_customization_line(self, options: list[dict]) -> dict[str, str]:
        """Collect T2/T3 coverage changes once instead of field by field."""

        default_id = self._default_option_id("t2_literature_param_gate", options)
        print(
            "一次输入覆盖数字、写作语言和中文文献策略；未提到的字段保持当前推荐。\n"
            "系统会先用 LLM 解释意图，再用本地规则校验数值和检索边界。\n"
            "示例：英文稿，候选30篇，精读15篇，粗读15篇；或：中文稿，允许中文文献，精读30篇。"
        )
        for attempt in range(1, 4):
            try:
                raw = _read_cli_line("自定义参数: ").strip()
            except EOFError as exc:
                raise HumanInputUnavailable("T2 自定义参数需要一行输入，但当前输入不可用。") from exc
            captured = await self._interpret_t2_literature_param_text(raw)
            has_parameters = any(
                key not in {"parser_source", "parser_fallback_reason"}
                for key in captured
            )
            if has_parameters or not raw:
                if default_id and default_id != "custom":
                    captured.setdefault("base_option", str(default_id))
                return captured
            if attempt < 3:
                print("未识别可调整的参数。请直接写数字或语言，例如：精读10篇，粗读20篇。")
        if default_id and default_id != "custom":
            return {"base_option": str(default_id)}
        return {}

    @staticmethod
    def _collect_t36_supplement_target(presentation: dict[str, Any]) -> str:
        """Collect a bounded retrieval target with the computed default visible.

        The corpus gate asks a researcher for an exploration *record target*,
        not a citation quota.  Making the recommendation executable prevents
        an empty Enter from silently falling back to an unrelated tool default
        while preserving the researcher's explicit retrieval target.
        """

        recommendation = presentation.get("supplement_recommendation")
        recommendation = recommendation if isinstance(recommendation, dict) else {}
        suggested = recommendation.get("suggested_target_records")
        try:
            default = int(suggested)
        except (TypeError, ValueError):
            default = 18
        default = max(1, default)

        prompt = f"目标补充记录数（直接回车采用建议 {default}；也可输入任意正整数）"
        for attempt in range(1, 4):
            try:
                raw = _read_cli_line(f"{prompt}: ").strip()
            except EOFError as exc:
                raise HumanInputUnavailable("确认步骤 t36_corpus_gate 需要补充检索目标数，但当前输入不可用。") from exc
            if not raw:
                return str(default)
            try:
                value = int(raw)
            except ValueError:
                value = -1
            if value >= 1:
                return str(value)
            if attempt < 3:
                print(f"请输入正整数，或直接回车使用建议值 {default}。")
        print(f"未收到有效目标数，已采用建议值 {default}。")
        return str(default)

    async def _interpret_t2_literature_param_text(self, raw_answer: str) -> dict[str, str]:
        """Interpret free text through an LLM, then enforce deterministic parsing.

        Explicit local matches take precedence over an LLM proposal.  This is
        deliberate: the model makes natural-language input ergonomic, while
        exact numeric and language policy enforcement remains auditable.
        """

        cleaned = _strip_terminal_control_sequences(raw_answer).strip()
        deterministic = self._parse_t2_literature_param_text(cleaned)
        if not cleaned:
            return deterministic
        if self._t2_parameter_interpreter is None:
            deterministic["parser_source"] = "deterministic_fallback"
            print("[参数解析] 当前未配置 LLM 解释器，已使用本地规则解析并会在确认页展示最终策略。")
            return deterministic

        print("[参数解析] 正在用 LLM 解释本句参数意图，并执行本地规则校验...")
        try:
            llm_capture = _sanitize_t2_llm_capture(
                await self._t2_parameter_interpreter(cleaned)
            )
        except Exception as exc:
            deterministic["parser_source"] = "llm_fallback"
            deterministic["parser_fallback_reason"] = type(exc).__name__
            print("[参数解析] LLM 暂不可用，已降级为本地规则解析；最终参数仍需在下一页确认。")
            return deterministic

        llm_capture.update(deterministic)
        llm_capture["parser_source"] = "llm_validated"
        print("[参数解析] 已完成 LLM 意图解析；本地规则已校验显式数字、语言与中文文献策略。")
        return llm_capture

    @staticmethod
    def _format_presentation_value(key: str, value: Any, *, gate_id: str = "") -> str:
        """Render gate presentation values for humans instead of dumping JSON by default."""

        if gate_id == "t36_assemble_recovery_gate":
            rendered = _format_t36_assemble_recovery_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t36_compile_recovery_gate":
            rendered = _format_t36_compile_recovery_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "runtime_recovery_gate":
            if key == "external_executor_launch":
                return _format_external_executor_launch(value)
            rendered = _format_runtime_recovery_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t2_coverage_gate":
            rendered = _format_t2_coverage_gate_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t36_survey_gate":
            rendered = _format_t36_survey_gate_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t36_corpus_gate" and key == "supplement_recommendation":
            return _format_t36_supplement_recommendation(value)
        if gate_id == "t2_literature_param_confirm_gate" and key == "selected_parameters":
            if isinstance(value, str):
                return _format_t2_selected_parameters_summary(
                    {"path": "literature/literature_params.json", "summary": value}
                )
            if _is_path_summary(value):
                return _format_t2_selected_parameters_summary(value)
        if gate_id == "t4_gate1_selection_gate" and key == "candidate_overview":
            return _format_t4_candidate_overview(value)
        if isinstance(value, str):
            return value
        if _is_path_summary(value):
            path = str(value.get("path") or "")
            size_chars = value.get("size_chars")
            summary = str(value.get("summary") or "").rstrip()
            header = [f"文件: {path}"]
            if size_chars not in (None, ""):
                header.append(f"字符数: {size_chars}")
            if summary:
                return "\n".join(header + ["摘要:", summary])
            return "\n".join(header)
        if key in {"candidate_pool_fingerprints", "input_fingerprints"} and isinstance(value, dict):
            if not CLIHumanInterface._show_machine_gate_field(gate_id, key):
                existing_count = sum(
                    1
                    for item in value.values()
                    if isinstance(item, dict) and item.get("exists")
                )
                missing_count = sum(
                    1
                    for item in value.values()
                    if isinstance(item, dict) and not item.get("exists")
                )
                if missing_count:
                    return f"机器校验信息已记录；{existing_count} 个文件已锁定，{missing_count} 个可选文件缺失。"
                return f"机器校验信息已记录；{existing_count} 个文件已锁定。"
            existing = [
                str(item.get("path") or label)
                for label, item in value.items()
                if isinstance(item, dict) and item.get("exists")
            ]
            missing = [
                str(item.get("path") or label)
                for label, item in value.items()
                if isinstance(item, dict) and not item.get("exists")
            ]
            lines = ["机器校验信息已记录在 state.yaml；一般不需要手动阅读。"]
            if existing:
                lines.append("已锁定候选文件: " + ", ".join(existing[:8]))
            if missing:
                lines.append("缺失/可选文件: " + ", ".join(missing[:8]))
            return "\n".join(lines)
        if gate_id == "t2_literature_param_gate" and key == "current_parameter_preview" and isinstance(value, dict):
            return _format_t2_parameter_preview(value)
        if isinstance(value, list):
            if not value:
                return "(空)"
            return "\n".join(f"- {item}" for item in value)
        return json.dumps(value, indent=2, ensure_ascii=False)

    @staticmethod
    def _show_machine_gate_field(gate_id: str, key: str) -> bool:
        return gate_id not in {
            "t4_gate1_selection_gate",
            "t2_literature_param_gate",
            "t2_literature_param_confirm_gate",
            "t36_post_survey_gate",
        }

    @staticmethod
    def _format_gate_selection_confirmation(gate_id: str, result: dict[str, Any], options: list[dict]) -> str:
        option_id = str(result.get("option_id") or result.get("key") or "")
        option = next((item for item in options if str(item.get("id") or item.get("key") or "") == option_id), {})
        label = str(option.get("label") or option_id)
        captured = result.get("captured") if isinstance(result.get("captured"), dict) else {}
        directive = captured.get("parsed_directive") if isinstance(captured.get("parsed_directive"), dict) else {}
        action = str(directive.get("action") or "")
        read_only_actions = {
            "show_more", "show_archive", "inspect_score", "inspect_evidence", "inspect_lineage",
            "inspect_hypotheses", "inspect_contributions", "inspect_genome", "inspect_files", "compare_candidates",
        }
        is_t4_read_only = gate_id == "t4_gate1_selection_gate" and option_id == "t4_directive" and action in read_only_actions
        is_t4_directive = gate_id == "t4_gate1_selection_gate" and option_id == "t4_directive"
        action_label = _t4_action_public_label(action)
        if is_t4_read_only:
            lines = [f"已收到只读请求：{action_label}。不会确认操作、调用模型或改变 Candidate。"]
        elif is_t4_directive and action == "pause":
            lines = ["已收到暂停请求；系统会保存当前 Gate 状态。不会调用模型、生成新 Candidate 或改变 Population。"]
        elif is_t4_directive:
            lines = ["已收到研究指令；系统尚未调用模型、生成新 Candidate 或改变 Population。"]
        else:
            lines = [f"已确认选择：{label}（{option_id}）"]
        if captured and not is_t4_read_only:
            if is_t4_directive:
                directive_text = " ".join(str(captured.get("directive") or "").split())
                target_ids = directive.get("target_candidate_ids") if isinstance(directive.get("target_candidate_ids"), list) else []
                parts: list[str] = []
                if directive_text:
                    parts.append(f"原始输入：{directive_text}")
                if action:
                    parts.append(f"识别操作：{action_label}")
                if target_ids:
                    parts.append("目标候选：" + "、".join(str(item) for item in target_ids))
                requested_route = str(directive.get("requested_route") or "").strip()
                if requested_route:
                    parts.append(f"指定路线：{requested_route}")
                if parts:
                    lines.append("；".join(parts))
            else:
                compact = "; ".join(f"{key}={value}" for key, value in captured.items() if value not in (None, ""))
                if compact:
                    lines.append(f"记录的补充输入：{compact}")
        if gate_id == "t2_literature_param_gate":
            lines.append("将写入：literature/literature_params.json；下一步：确认检索参数")
        elif gate_id == "t2_literature_param_confirm_gate":
            if option_id == "confirm_start_t2":
                lines.append("将写入：literature/literature_params_confirmation.json；下一步：开始文献检索")
            elif option_id == "revise_params":
                lines.append("不会启动文献检索；下一步：返回参数选择")
            elif option_id == "stop_project":
                lines.append("将结束当前项目，不启动 T2")
        elif gate_id == "t4_gate1_selection_gate":
            if is_t4_read_only:
                lines.append("正在打开已保存的 Candidate 详情；完成后将回到当前决策页。")
            elif option_id == "t4_directive" and action == "pause":
                lines.append("本次会暂停在 T4 决策页；下次 resume 会回到这里，不重复已完成的 T4 模型调用。")
            elif option_id == "t4_directive":
                if action:
                    lines.append("已识别为研究操作；系统现在只会保存操作计划并进入清晰的二次确认，尚未执行该操作。")
                else:
                    lines.append("系统将解析这条指令；若涉及推进、优化、组合或再探索，会先展示二次确认，不会直接改写当前版本。")
            elif option_id == "confirm":
                lines.append("已确认操作；系统将按上方计划执行。若涉及演化，会生成新版本、独立重评，并保留当前 Population 以便回滚。")
            elif option_id == "cancel":
                lines.append("已取消操作；当前 Population 与所有历史版本保持不变。")
            else:
                lines.append("将记录该选择并按其类型继续；选择 Candidate 会生成 Pre-Novelty brief，演化类操作会先保存新版本再重新评分。")
        elif gate_id == "t36_post_survey_gate":
            lines.append("将写入：drafts/survey/post_survey_decision.json")
        elif gate_id in {"t36_template_gate", "t36_ccf_template_gate", "t8_style_template_gate", "t8_ccf_template_gate"}:
            target = "drafts/survey/writing_template.json" if gate_id == "t36_template_gate" else "drafts/writing_style.json"
            if gate_id == "t36_ccf_template_gate":
                target = "drafts/survey/writing_template.json"
            lines.append(f"将写入：{target}")
        return "\n".join(lines)

    @staticmethod
    def _parse_inline_gate_customization(gate_id: str, raw_answer: str, options: list[dict]) -> dict | None:
        if gate_id in {"t36_template_gate", "t36_ccf_template_gate", "t8_style_template_gate", "t8_ccf_template_gate"}:
            return CLIHumanInterface._parse_template_gate_text(gate_id, raw_answer, options)
        if gate_id == "t4_gate1_selection_gate":
            return CLIHumanInterface._parse_t4_gate1_text(raw_answer, options)
        if gate_id != "t2_literature_param_gate":
            return None
        captured = CLIHumanInterface._parse_t2_literature_param_text(raw_answer)
        if not captured:
            return None
        if not any((option.get("id") or option.get("key")) == "custom" for option in options):
            return None
        default_id = CLIHumanInterface._default_option_id(gate_id, options)
        if default_id and default_id != "custom":
            captured.setdefault("base_option", str(default_id))
        return {"option_id": "custom", "captured": captured}

    async def _parse_inline_gate_customization_async(
        self,
        gate_id: str,
        raw_answer: str,
        options: list[dict],
    ) -> dict | None:
        """Async gate parser used by CLI so T2 can call its LLM interpreter."""

        if gate_id == "t4_gate1_selection_gate":
            parsed = self._parse_t4_gate1_text(raw_answer, options)
            if parsed is None or str(parsed.get("option_id") or "") != "t4_directive":
                return parsed
            captured = parsed.get("captured") if isinstance(parsed.get("captured"), dict) else {}
            if captured.get("parser_source") == "deterministic_read_only":
                print("[T4] 已识别为只读查看；将直接读取已保存的 Candidate 信息，不调用模型、不创建操作计划。")
                return parsed
            if captured.get("parser_source") == "deterministic_control":
                print("[T4] 已识别为控制指令；不会调用模型。")
                return parsed
            return await self._interpret_t4_gate1_text(raw_answer, parsed)
        if gate_id != "t2_literature_param_gate":
            return self._parse_inline_gate_customization(gate_id, raw_answer, options)
        captured = await self._interpret_t2_literature_param_text(raw_answer)
        parameter_capture = {
            key: value
            for key, value in captured.items()
            if key not in {"parser_source", "parser_fallback_reason"}
        }
        if not parameter_capture:
            return None
        if not any((option.get("id") or option.get("key")) == "custom" for option in options):
            return None
        default_id = self._default_option_id(gate_id, options)
        if default_id and default_id != "custom":
            captured.setdefault("base_option", str(default_id))
        return {"option_id": "custom", "captured": captured}

    async def _interpret_t4_gate1_text(self, raw_answer: str, fallback: dict[str, Any]) -> dict[str, Any]:
        """Use semantic parsing for Gate1 wording, then leave validation to T4."""

        if self._t4_directive_interpreter is None:
            return fallback
        cleaned = _strip_terminal_control_sequences(raw_answer).strip()
        if not cleaned:
            return fallback
        print("[T4] 正在理解这条研究指令；系统只会先生成操作计划，不会直接修改 Candidate 或 Population。")
        try:
            proposal = await self._t4_directive_interpreter(cleaned)
        except Exception:
            return fallback
        if not proposal:
            return fallback
        captured = dict(fallback.get("captured") or {})
        captured["parsed_directive"] = proposal
        return {"option_id": "t4_directive", "captured": captured}

    @staticmethod
    def _parse_t4_gate1_text(raw_answer: str, options: list[dict]) -> dict | None:
        text = str(raw_answer or "").strip()
        if not text:
            return None
        normalized = text.replace("，", ",").replace("＋", "+").strip()
        lowered = normalized.casefold()
        if normalized.isdigit():
            # Let the normal menu parser resolve numbered Rich options.
            return None
        option_ids = {str(option.get("id") or option.get("key") or "") for option in options}
        if lowered in {"confirm", "yes", "y", "确认", "继续"} and "confirm" in option_ids:
            return {"option_id": "confirm", "captured": {}}
        if lowered in {"cancel", "no", "n", "取消"} and "cancel" in option_ids:
            return {"option_id": "cancel", "captured": {}}
        if lowered in {"pause", "暂停", "暂不决定", "先暂停"}:
            return {
                "option_id": "t4_directive",
                "captured": {
                    "directive": normalized,
                    "parsed_directive": {"action": "pause"},
                    "parser_source": "deterministic_control",
                },
            }
        if lowered in {"更多操作", "more", "more actions", "advanced", "高级操作"} and "more_actions" in option_ids:
            return {"option_id": "more_actions", "captured": {}}
        read_only_action = _t4_deterministic_read_only_action(normalized)
        if read_only_action:
            return {
                "option_id": "t4_directive",
                "captured": {
                    "directive": normalized,
                    "parsed_directive": {"action": read_only_action},
                    "parser_source": "deterministic_read_only",
                },
            }
        # Native Evolution Gate1 is LLM-first for every natural-language
        # instruction.  Menu indices and the explicit confirm/cancel controls
        # above remain deterministic.  Preserve all other wording (including
        # multiline constraints) for the interpreter and StateMachine-level
        # validation instead of prematurely collapsing it into legacy menu
        # branches.
        if any(
            option_id in option_ids
            for option_id in {
                "proceed_candidate",
                "another_generation",
                "focus_evolution",
                "create_crossover",
                "compose",
                "show_population",
                "inspect",
                "regenerate_route",
                "rollback",
            }
        ):
            return {"option_id": "t4_directive", "captured": {"directive": normalized}}
        # ``\b`` treats Chinese characters as word characters, so a normal
        # answer such as "选D1作为主线" used to miss the candidate code.
        candidate_pattern = r"(?<![A-Za-z0-9])[DS]\d+(?![A-Za-z0-9])"
        candidates = [item.upper() for item in re.findall(candidate_pattern, normalized, flags=re.IGNORECASE)]
        unique_candidates = list(dict.fromkeys(candidates))
        if unique_candidates and "t4_directive" in option_ids:
            return {"option_id": "t4_directive", "captured": {"directive": normalized}}

        if "reanalyze" in lowered or "重新分析" in normalized or "重跑" in normalized:
            feedback = re.sub(r"(?i)\breanalyze\b\s*[:：-]?", "", normalized).strip()
            feedback = feedback.replace("重新分析", "").replace("重跑", "").strip(" ：:-")
            return {"option_id": "reanalyze", "captured": {"feedback": feedback or normalized}}

        if lowered.startswith(("new:", "new idea:", "idea:")) or normalized.startswith(("新想法", "补充")):
            new_idea = re.sub(r"(?i)^(new|new idea|idea)\s*[:：-]?", "", normalized).strip()
            new_idea = re.sub(r"^(新想法|补充)\s*[:：-]?", "", new_idea).strip()
            return {"option_id": "new_idea", "captured": {"new_idea": new_idea or normalized}}

        explicit_merge = "merge" in lowered or "合并" in normalized or "+" in normalized or len(unique_candidates) > 1
        if candidates and explicit_merge and "merge" in option_ids:
            return {"option_id": "merge", "captured": {"merge_plan": normalized}}
        if candidates and "select_or_reframe" in option_ids:
            return {"option_id": "select_or_reframe", "captured": {"selection": normalized}}
        return None

    @staticmethod
    def _parse_template_gate_text(gate_id: str, raw_answer: str, options: list[dict]) -> dict | None:
        text = str(raw_answer or "").strip()
        if not text:
            return None
        normalized = text.casefold().replace("，", ",").replace("；", ";").replace("：", ":")
        option_ids = {str(option.get("id") or option.get("key") or "") for option in options}
        captured: dict[str, str] = {}
        option_id = ""

        if any(token in normalized for token in ("中文", "chinese", "basic_zh", " zh")) or normalized == "zh":
            captured.update({"template_family": "basic_zh", "template_id": "basic_zh", "writing_language": "zh"})
            option_id = "basic_zh"
        elif any(token in normalized for token in (
            "informs",
            "utd",
            "management science",
            "information systems research",
            "isr",
            "misq",
            "cds",
            "commerce data science",
            "informs journal on data science",
        )):
            captured.update({"template_family": "utd", "template_id": "informs", "writing_language": "en"})
            option_id = "utd_informs" if gate_id == "t36_template_gate" else "is_informs"
        elif any(token in normalized for token in ("ccf", "conference", "会议", "ccf-a", "ccf_a", *ccf_template_ids())):
            detected_template = next(
                (
                    template_id
                    for template_id in ccf_template_ids()
                    if template_id in normalized
                    or (template_id == "kdd" and "sigkdd" in normalized)
                ),
                "",
            )
            first_level_gate = gate_id in {"t36_template_gate", "t8_style_template_gate"}
            if first_level_gate:
                option_id = "ccf"
                captured.update({"template_family": "ccf", "writing_language": "en"})
                if detected_template in ccf_template_ids():
                    captured["template_id"] = detected_template
            else:
                template_id = normalize_ccf_template_id(detected_template)
                if template_id not in ccf_template_ids():
                    return None
                option_id = ccf_template_option_id(template_id)
                captured.update({"template_family": "ccf", "template_id": template_id, "writing_language": "en"})
        elif any(token in normalized for token in ("英文", "english", "basic_en", "不用模板", "不要模板", "no template")) or normalized == "en":
            captured.update({"template_family": "basic_en", "template_id": "basic_en", "writing_language": "en"})
            option_id = "basic_en"
        elif any(token in normalized for token in ("both", "两套", "双线")) and gate_id == "t8_style_template_gate":
            captured.update({"template_family": "basic_en", "template_id": "basic_en", "writing_language": "en", "venue_style": "both"})
            option_id = "both_basic_en"

        key_aliases = {
            "venue_style": "venue_style",
            "style": "venue_style",
            "template_family": "template_family",
            "family": "template_family",
            "template_type": "template_family",
            "template_id": "template_id",
            "template": "template_id",
            "writing_language": "writing_language",
            "language": "writing_language",
            "lang": "writing_language",
        }
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_\-\s]*?)\s*[=:]\s*([A-Za-z0-9_\-]+)", normalized):
            canonical = key_aliases.get(re.sub(r"[\s-]+", "_", key.strip().lower()))
            if canonical:
                captured[canonical] = value.strip()

        if gate_id in {"t8_style_template_gate", "t8_ccf_template_gate"}:
            if "venue_style" not in captured:
                if option_id == "is_informs":
                    captured["venue_style"] = "is"
                elif option_id == "both_basic_en":
                    captured["venue_style"] = "both"
                elif option_id:
                    captured["venue_style"] = "ccf_a"

        if not captured:
            return None
        # Older/custom gate definitions may expose only the generic CCF option.
        # Preserve the requested concrete template id, but route through that
        # generic option rather than incorrectly declaring the input invalid.
        if option_id == "ccf" and option_id not in option_ids and "ccf_neurips" in option_ids:
            option_id = "ccf_neurips"
        if not option_id or option_id not in option_ids:
            option_id = "custom" if "custom" in option_ids else next(iter(option_ids), "")
        if not option_id:
            return None
        return {"option_id": option_id, "captured": captured}

    @staticmethod
    def _parse_t2_literature_param_text(raw_answer: str) -> dict[str, str]:
        text = _strip_terminal_control_sequences(str(raw_answer or "")).strip()
        if not text:
            return {}
        normalized = text.replace("，", ",").replace("；", ";").replace("：", ":")
        captured: dict[str, str] = {}
        if re.search(r"\b(en|english)\b", normalized, flags=re.IGNORECASE) or any(
            token in normalized for token in ("英文稿", "英文论文", "英文")
        ):
            captured["manuscript_language"] = "英文"
        elif (
            re.search(r"\b(zh|chinese)\b", normalized, flags=re.IGNORECASE)
            or any(token in normalized for token in ("中文稿", "中文论文", "中文"))
        ) and "不要中文论文" not in normalized:
            captured["manuscript_language"] = "中文"
        if any(token in normalized for token in ("不要中文论文", "不要中文文献", "不检索中文", "不引用中文", "排除中文")):
            captured["include_chinese_literature"] = "false"
        elif any(token in normalized for token in ("允许中文论文", "允许中文文献", "中文论文允许", "中文文献允许", "检索中文", "包含中文", "包括中文")):
            captured["include_chinese_literature"] = "true"
        if any(token in normalized for token in ("稿件中文", "论文中文", "写作中文", "中文写作")):
            captured["manuscript_language"] = "中文"
        if any(token in normalized for token in ("不必读满目标", "无需读满目标", "达到最低即可", "读到最低线即可")):
            captured["require_deep_read_target"] = "false"
        elif any(token in normalized for token in ("必须读满目标", "一定读满目标")):
            captured["require_deep_read_target"] = "true"
        if any(token in normalized for token in ("不粗读", "不要粗读", "不略读", "不要略读", "不做粗读", "不做摘要轻读")):
            captured["abstract_sweep_target"] = "0"

        deep_triplet = re.search(
            r"\bdeep[_\s-]*read\b\s*(?:=|:|为)?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if not deep_triplet:
            deep_triplet = re.search(
                r"(?:精读|深读|深入阅读)\s*(?:=|:|为)?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
                normalized,
                flags=re.IGNORECASE,
            )
        if deep_triplet:
            captured["deep_read_min"] = deep_triplet.group(1)
            captured["deep_read_target"] = deep_triplet.group(2)
            captured["deep_read_max"] = deep_triplet.group(3)

        patterns = {
            "coverage_total": [
                r"\b(?:total|coverage[_\s-]*total|total[_\s-]*coverage|reading[_\s-]*total)\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:总共|一共|总计|总量|总覆盖|覆盖总数|阅读总数|总阅读量)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
            ],
            "active_pool_max": [
                r"\bactive[_\s-]*pool(?:[_\s-]*max)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:保留候选数|候选池|候选数|保留候选|候选|active\s*pool)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)\s*(?:篇)?",
            ],
            "deep_read_target": [
                r"\bdeep[_\s-]*read(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:精读目标|精读|深读目标|深读|深入阅读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)\s*(?:篇)?",
            ],
            "abstract_sweep_target": [
                r"\babstract[_\s-]*sweep(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)",
                r"(?:摘要轻读|轻读|略读|粗读|粗略阅读|摘要阅读|浅读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)\s*(?:篇)?",
            ],
            "require_deep_read_target": [
                r"\brequire(?:[_\s-]*deep)?(?:[_\s-]*read)?(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|是|否|需要|不需要)",
                r"(?:必须读满|读满目标|必须达到目标|达到最低即可)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|是|否|需要|不需要)?",
            ],
            "manuscript_language": [
                r"\b(?:manuscript|writing)?[_\s-]*language\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(en|english|zh|chinese|mixed|bilingual|auto)",
                r"(?:写作语言|论文语言|稿件语言)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(英文|中文|双语|en|english|zh|chinese|mixed|bilingual|auto)",
            ],
            "include_chinese_literature": [
                r"\b(?:include[_\s-]*chinese|include[_\s-]*zh|chinese[_\s-]*literature)\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|auto|include|exclude)",
                r"(?:中文文献|中文论文|检索中文|包含中文|include\s*zh)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|auto|允许|不允许|不要|包括|不包括|是|否)?",
            ],
        }
        for field_name, field_patterns in patterns.items():
            if field_name in captured:
                continue
            for pattern in field_patterns:
                match = re.search(pattern, normalized, flags=re.IGNORECASE)
                if match:
                    value = (match.group(1) or "").strip()
                    if field_name == "require_deep_read_target" and not value:
                        value = "false" if "最低" in match.group(0) else "true"
                    if field_name == "include_chinese_literature" and not value:
                        value = "false" if any(token in match.group(0) for token in ("不", "不要", "exclude")) else "true"
                    if value:
                        captured[field_name] = value
                    break

        key_aliases = {
            "active_pool": "active_pool_max",
            "active_pool_max": "active_pool_max",
            "pool": "active_pool_max",
            "total": "coverage_total",
            "coverage_total": "coverage_total",
            "total_coverage": "coverage_total",
            "reading_total": "coverage_total",
            "deep": "deep_read_target",
            "deep_read": "deep_read_target",
            "deep_read_min": "deep_read_min",
            "deep_read_target": "deep_read_target",
            "deep_read_max": "deep_read_max",
            "abstract": "abstract_sweep_target",
            "abstract_sweep": "abstract_sweep_target",
            "abstract_sweep_target": "abstract_sweep_target",
            "rough": "abstract_sweep_target",
            "rough_read": "abstract_sweep_target",
            "lite": "abstract_sweep_target",
            "lite_read": "abstract_sweep_target",
            "shallow": "abstract_sweep_target",
            "shallow_read": "abstract_sweep_target",
            "require": "require_deep_read_target",
            "require_target": "require_deep_read_target",
            "require_deep_read_target": "require_deep_read_target",
            "language": "manuscript_language",
            "manuscript_language": "manuscript_language",
            "writing_language": "manuscript_language",
            "include_zh": "include_chinese_literature",
            "include_chinese": "include_chinese_literature",
            "include_chinese_literature": "include_chinese_literature",
            "chinese_literature": "include_chinese_literature",
        }
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_\-\s]*?)\s*[=:]\s*([A-Za-z0-9_\/\-]+|全部)", normalized):
            canonical = key_aliases.get(re.sub(r"[\s-]+", "_", key.strip().lower()))
            if canonical:
                value = value.strip()
                if canonical == "deep_read_target" and re.fullmatch(r"\d+/\d+/\d+", value):
                    min_v, target_v, max_v = value.split("/")
                    captured["deep_read_min"] = min_v
                    captured["deep_read_target"] = target_v
                    captured["deep_read_max"] = max_v
                    continue
                captured[canonical] = value

        return captured

    @staticmethod
    def _default_option_id(gate_id: str, options: list[dict] | None = None) -> str | None:
        for option in options or []:
            if option.get("is_default"):
                return option.get("id") or option.get("key")
        if gate_id == "t2_literature_param_gate":
            return "survey_balanced"
        if gate_id == "t2_literature_param_confirm_gate":
            return "confirm_start_t2"
        if gate_id == "t5_executor_gate":
            return "codex_cli"
        return None

    @staticmethod
    def _collect_input_prompt(option: dict, field_name: str) -> str:
        prompts = option.get("input_prompts") or {}
        if isinstance(prompts, dict) and prompts.get(field_name):
            return f"{field_name}（{prompts[field_name]}）"
        return field_name

    @staticmethod
    def _parse_option_index(raw_answer: str, options: list[dict] | int) -> int | None:
        """解析 CLI gate 选择。

        某些终端会把快捷键或 ANSI 控制字符混进 input，例如 `\x1ba\x1ba1`。
        优先提取数字，同时支持 option id/key、label 和常用中文/英文确认别名。
        """

        if isinstance(options, int):
            option_list: list[dict] = [{"id": str(index + 1), "label": ""} for index in range(options)]
        else:
            option_list = list(options)
        option_count = len(option_list)
        cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw_answer)
        cleaned = cleaned.replace("\x1b", "")
        match = re.search(r"\d+", cleaned)
        if not match:
            return CLIHumanInterface._parse_option_alias(cleaned, option_list)
        idx = int(match.group(0)) - 1
        if 0 <= idx < option_count:
            return idx
        return CLIHumanInterface._parse_option_alias(cleaned, option_list)

    @staticmethod
    def _parse_option_alias(raw_answer: str, options: list[dict]) -> int | None:
        normalized_answer = CLIHumanInterface._normalize_answer(raw_answer)
        if not normalized_answer:
            return None

        positive_aliases = {
            "确认",
            "确定",
            "同意",
            "继续",
            "扩限",
            "增加",
            "是",
            "好",
            "yes",
            "y",
            "ok",
            "okay",
            "confirm",
            "continue",
            "extend",
            "approve",
        }
        negative_aliases = {
            "停止",
            "停",
            "取消",
            "否",
            "不",
            "no",
            "n",
            "stop",
            "cancel",
            "reject",
            "abort",
        }

        for idx, option in enumerate(options):
            tokens = {
                str(option.get("id") or ""),
                str(option.get("key") or ""),
                str(option.get("label") or ""),
            }
            tokens.update(str(alias) for alias in option.get("aliases") or [])
            normalized_tokens = {CLIHumanInterface._normalize_answer(token) for token in tokens}
            normalized_tokens = {token for token in normalized_tokens if token}
            if normalized_answer in normalized_tokens:
                return idx
            if any(normalized_answer in token for token in normalized_tokens):
                return idx

        for idx, option in enumerate(options):
            option_id = str(option.get("id") or option.get("key") or "").lower()
            label = CLIHumanInterface._normalize_answer(str(option.get("label") or ""))
            if option_id == "extend" and normalized_answer in positive_aliases:
                return idx
            if option_id == "stop" and normalized_answer in negative_aliases:
                return idx
            if any(alias in normalized_answer for alias in positive_aliases) and (
                "继续" in label or "扩限" in label or "增加" in label or "continue" in label or "extend" in label
            ):
                return idx
            if any(alias in normalized_answer for alias in negative_aliases) and (
                "停止" in label or "取消" in label or "stop" in label or "cancel" in label
            ):
                return idx
        return None

    @staticmethod
    def _normalize_answer(value: str) -> str:
        return re.sub(r"[\s\[\]（）()。.!！,，:：;；\"'`]+", "", value.strip().lower())


def _t4_deterministic_read_only_action(raw_answer: str) -> str:
    """Recognize common Chinese/English inspection wording without an LLM.

    This is intentionally conservative: a turn containing a mutation verb is
    left to the semantic parser and confirmation flow. It covers natural
    phrases such as “我想看一下 D1 的详情”, which should feel immediate in a
    terminal conversation rather than wait silently for a model call.
    """

    normalized = " ".join(str(raw_answer or "").casefold().split())
    if not normalized:
        return ""
    mutation_tokens = (
        "推进", "选择", "选定", "确认", "优化", "修改", "重构", "组合", "合并", "交叉", "演化", "进化",
        "重新生成", "重跑", "保留", "提交", "proceed", "select", "confirm", "refine", "merge", "compose",
        "crossover", "evolve", "regenerate", "rollback",
    )
    if any(token in normalized for token in mutation_tokens):
        return ""
    inspection_signal = any(
        token in normalized
        for token in ("查看", "看一下", "想看", "看看", "详情", "view", "inspect", "show")
    )
    comparison_signal = any(token in normalized for token in ("对比", "比较", "compare"))
    if not (inspection_signal or comparison_signal):
        return ""
    candidate_count = len(re.findall(r"(?<![A-Za-z0-9])[DS]\d+(?![A-Za-z0-9])", normalized, flags=re.IGNORECASE))
    if comparison_signal:
        return "compare_candidates" if candidate_count >= 2 else "show_more"
    if any(token in normalized for token in ("文件", "产物", "artifact", "files", "路径")):
        return "inspect_files" if candidate_count else "show_more"
    if any(token in normalized for token in ("证据", "evidence")):
        return "inspect_evidence" if candidate_count else "show_more"
    if any(token in normalized for token in ("谱系", "lineage")):
        return "inspect_lineage" if candidate_count else "show_more"
    if any(token in normalized for token in ("假设", "hypotheses")):
        return "inspect_hypotheses" if candidate_count else "show_more"
    if any(token in normalized for token in ("贡献", "contributions")):
        return "inspect_contributions" if candidate_count else "show_more"
    if any(token in normalized for token in ("基因", "genome")):
        return "inspect_genome" if candidate_count else "show_more"
    return "inspect_score" if candidate_count else "show_more"


def _t4_action_public_label(action: str) -> str:
    """Translate internal T4 directive actions for the normal CLI surface."""

    return {
        "select_candidate": "推进候选进入 T4.5",
        "select_multiple": "多候选选择，需要进一步澄清",
        "keep_parallel": "并行保留多个候选",
        "compose_from_components": "组合指定组件",
        "continue_evolution": "再探索一轮",
        "focus_candidate": "定向优化候选",
        "merge_candidates": "合并或交叉候选",
        "show_more": "查看更多候选",
        "show_archive": "查看历史候选",
        "inspect_score": "查看候选详情",
        "inspect_evidence": "查看候选证据",
        "inspect_lineage": "查看演化谱系",
        "inspect_hypotheses": "查看假设",
        "inspect_contributions": "查看贡献",
        "inspect_genome": "查看完整结构",
        "inspect_files": "查看关联文件",
        "compare_candidates": "对比候选",
        "regenerate_route": "重跑指定路线",
        "rollback": "回到上一代",
        "pause": "暂停",
        "cancel": "取消",
    }.get(str(action or "").strip(), str(action or "待解析"))


def _is_path_summary(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("path"), str)
        and "summary" in value
        and "size_chars" in value
    )


def _path_summary_text(value: Any, *, default_path: str = "") -> tuple[str, str]:
    if isinstance(value, dict):
        return str(value.get("path") or default_path), str(value.get("summary") or "")
    return default_path, str(value or "")


def _path_summary_size(value: Any) -> int | None:
    if isinstance(value, dict):
        size = value.get("size_chars")
        if isinstance(size, int):
            return size
        try:
            return int(size)
        except Exception:
            return None
    return None


def _strip_gate_truncation_marker(text: str) -> str:
    return re.sub(r"\n*\[open .+? for full content; truncated from \d+ chars\]\s*$", "", text).rstrip()


def _t4_terminal_columns(value: str) -> int:
    """Return a conservative terminal-column width for mixed CJK/ASCII text."""

    columns = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        columns += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return columns


def _t4_wrap_terminal_text(value: str, *, columns: int) -> list[str]:
    """Wrap normalized prose by display columns, including double-width CJK glyphs."""

    if columns <= 0:
        return [value]
    lines: list[str] = []
    current = ""
    current_columns = 0
    for char in value:
        char_columns = _t4_terminal_columns(char)
        if current and current_columns + char_columns > columns:
            lines.append(current.rstrip())
            current = "" if char.isspace() else char
            current_columns = 0 if char.isspace() else char_columns
            continue
        if not current and char.isspace():
            continue
        current += char
        current_columns += char_columns
    if current or not lines:
        lines.append(current.rstrip())
    return lines


def _t4_wrap_terminal_prose(value: Any, *, indent: int = 0, width: int = 88) -> list[str]:
    """Wrap a non-field terminal line by display columns."""

    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    prefix = " " * indent
    available = max(1, width - _t4_terminal_columns(prefix))
    return [prefix + part for part in _t4_wrap_terminal_text(normalized, columns=available)]


def _t4_truncate_terminal_title(value: str, *, columns: int) -> str:
    """Return the full candidate title; wrapping belongs to the renderer."""

    del columns
    return re.sub(r"\s+", " ", str(value or "未命名候选")).strip()


def _t4_wrap_terminal_field(label: str, value: Any, *, indent: int = 0, width: int = 88) -> list[str]:
    """Render one T4 field without hiding CJK content beyond terminal width."""

    normalized = re.sub(r"\s+", " ", str(value or "待补充")).strip()
    prefix = " " * indent + f"{label}："
    continuation = " " * (indent + 2)
    first_columns = max(1, width - _t4_terminal_columns(prefix))
    continuation_columns = max(1, width - _t4_terminal_columns(continuation))
    parts = _t4_wrap_terminal_text(normalized, columns=first_columns)
    if len(parts) <= 1:
        return [prefix + parts[0]]

    wrapped = [prefix + parts[0]]
    for part in parts[1:]:
        wrapped.extend(continuation + fragment for fragment in _t4_wrap_terminal_text(part, columns=continuation_columns))
    return wrapped


def _t4_terminal_lane_guide() -> list[str]:
    """Return public Gate1 lane semantics for the terminal decision panel."""

    return [
        "通道说明（候选角色，不是模型内部推理）：",
        "  D 主线：面向论文主贡献的候选路线；可被选择、合并或重构。",
        "  跨域桥接：由已确认的跨领域机制导出；必须回看对应论文阅读笔记后才能形成最终论断。",
        "  证据不足：保留在面板中以便人工判断，但在补足明确证据前不应作为最终主张。",
        "  S 补充：用于证伪、失败分析或消融；默认不应单独承担一篇论文的主贡献。",
        "补充通道：",
        "  S1 机制挑战：检查替代解释或机制失效的边界。",
        "  S2 反向操作：移除、反转或关闭机制成分，形成消融/反事实检验。",
        "  S3 条件失效：定位子群、状态或数据条件下的失败模式。",
        "  S4 空白探索：探索已确认的空白；先补证据，再决定是否升级为主线。",
    ]


def _t4_candidate_lane_description(item: dict[str, Any], candidate_id: str) -> str:
    """Explain the candidate's decision role from persisted fields only."""

    lane = str(item.get("lane") or item.get("constraint_status") or "").strip().lower()
    origin = str(item.get("origin") or item.get("idea_origin") or "").strip().lower()
    if "not_supported" in lane or "evidence" in lane and "not" in lane:
        return "证据不足候选：可讨论，但必须补足论文阅读笔记中的机制依据后才可选择为主方向。"
    if "bridge" in lane or "bridge" in origin:
        return "跨域桥接候选：来自跨领域机制迁移；确定方向后必须核验源领域对应的论文阅读笔记。"
    if candidate_id.upper().startswith("S") or "supplement" in lane:
        return "补充候选：服务于机制挑战、反向操作、子群失败或缺口探索，默认作为主线的验证/反证模块。"
    return "主线候选：面向论文主贡献的可选路线；确定方向前仍须定向回查证据。"


def _format_t4_candidate_overview(value: Any) -> str:
    """Render no-colour Gate1 output through the shared Candidate card surface."""

    if not isinstance(value, dict):
        return "候选方向概览暂不可用；请检查 ideation/_candidate_directions.json。"
    candidates = value.get("candidates") if isinstance(value.get("candidates"), list) else []
    if not candidates:
        return "候选方向概览暂不可用；请检查 ideation/_candidate_directions.json。"
    cards: list[str] = []
    missing_ids: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        try:
            cards.append(CandidateCardRenderer.plain_summary(CandidateViewModel.from_mapping(item), width=96))
        except ValueError:
            missing_ids.append(str(item.get("id") or "?"))
    if missing_ids:
        return "\n".join(
            (
                "T4 卡片修复",
                "当前 Portfolio 尚未拥有完整的 LLM Idea Card；不会退回旧字段或固定模板。",
                "缺少卡片：" + "、".join(missing_ids),
            )
        )
    if not cards:
        return "T4 卡片修复\n当前 Portfolio 没有可展示的完整 LLM Idea Card。"
    comparison_hint = "先阅读上方简述卡；需要逐项判断时输入“查看 D1”。对比 D1 和 D3 只读且不会改变当前版本。"
    return "\n\n".join([*cards, comparison_hint])

    # Kept below temporarily as a source-compatibility reference for older
    # integrations. The Gate1 path returns above so it can never render the
    # historic template prose or fill missing research content.
    width = 88
    divider = "=" * width
    lines = [
        divider,
        "T4 候选方向完整卡片",
        *_t4_wrap_terminal_prose("请比较创新、假设/机制、可证伪预测、最小验证、证据基础和风险。", width=width),
        *_t4_wrap_terminal_prose("以下仅展示可核验候选、评分和文献依据，不展示模型内部推理。确定方向后必须重新打开列出的论文阅读笔记。", width=width),
    ]
    for guide_line in _t4_terminal_lane_guide():
        indent = len(guide_line) - len(guide_line.lstrip())
        lines.extend(_t4_wrap_terminal_prose(guide_line.strip(), indent=indent, width=width))
    lines.append(divider)
    score_labels = (
        ("novelty", "新颖性"),
        ("feasibility", "可行性"),
        ("impact", "影响力"),
        ("evaluability", "可评估性"),
        ("differentiation", "差异化"),
        ("cost", "资源/实施成本"),
        ("contribution_strength", "贡献强度"),
    )
    lane_counts: dict[str, int] = {}
    for item in candidates:
        if isinstance(item, dict):
            lane = str(item.get("lane") or "候选方向")
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
    lines.extend(
        [
            "候选快速总览（先比较，再阅读下方完整卡片）：",
            *_t4_wrap_terminal_prose(
                "总数 " + str(len(candidates)) + "；" + "；".join(
                    f"{lane} {count}" for lane, count in lane_counts.items()
                ),
                indent=2,
                width=width,
            ),
        ]
    )
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("id") or "?")
        title = _t4_truncate_terminal_title(
            str(item.get("title") or "未命名候选"),
            columns=42,
        )
        innovation = item.get("innovation") if isinstance(item.get("innovation"), dict) else {}
        hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
        quick_label = f"{candidate_id} · {item.get('lane') or '候选方向'} · {title}"
        quick_detail = (
            f"创新：{innovation.get('type') or '待界定'}；"
            f"候选假设 {len(hypotheses)} 条；"
            f"建议：{item.get('selection_recommendation') or '待复核'}"
        )
        lines.extend(_t4_wrap_terminal_field(quick_label, quick_detail, indent=2, width=width))
    lines.append(divider)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("id") or "?")
        lane = str(item.get("lane") or "候选方向")
        title = _t4_truncate_terminal_title(
            str(item.get("display_title") or item.get("title") or "未命名候选"),
            columns=width - _t4_terminal_columns(f"| {candidate_id} | {lane} | "),
        )
        full_title = str(item.get("full_title") or title)
        original = str(item.get("original_title") or "")
        value_text = str(item.get("value") or "待补充")
        mechanism = str(item.get("mechanism") or "待补充")
        minimum = item.get("minimum_validation") if isinstance(item.get("minimum_validation"), dict) else {}
        dataset = str(minimum.get("dataset") or "unknown")
        baseline = str(minimum.get("baseline") or "unknown")
        raw_metric = minimum.get("metric")
        metric = ", ".join(str(value) for value in raw_metric) if isinstance(raw_metric, list) else str(raw_metric or "unknown")
        signal = str(minimum.get("expected_signal") or "unknown")
        protocol_status = str(minimum.get("evidence_status") or "legacy_unverified").strip().lower()
        protocol_labels = {
            "supported": "已由可追溯材料支持",
            "user_provided": "由人工明确提供",
            "proposed_not_verified": "待验证的候选提议，不是既有协议",
            "unknown": "当前未知，必须补充材料后确定",
            "legacy_unverified": "遗留候选未声明来源；不得视为既定协议",
        }
        raw_refs = minimum.get("source_refs")
        protocol_refs = ", ".join(str(value).strip() for value in raw_refs if str(value).strip()) if isinstance(raw_refs, list) else ""
        if protocol_status == "legacy_unverified":
            # Old candidate files can contain plausible-looking protocol text
            # that predates source-bound proposal records. Do not repeat that
            # text to a researcher: its provenance cannot be established.
            dataset = "未验证；需由项目材料或人工输入确定"
            baseline = "未验证；需由最近工作和项目约束确定"
            metric = "未验证；需由可追溯协议确定"
            signal = "未验证；需在 T4 后半段形成可证伪预测"
        evidence = str(item.get("evidence") or "需回看论文阅读笔记")
        count = item.get("support_count")
        scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        warning = str(item.get("warning") or "选择后需回查证据。")
        innovation = item.get("innovation") if isinstance(item.get("innovation"), dict) else {}
        hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
        merges = item.get("merge_opportunities") if isinstance(item.get("merge_opportunities"), list) else []
        score_rationale = item.get("score_rationale") if isinstance(item.get("score_rationale"), dict) else {}
        lines.extend(
            [
                "",
                divider,
                f"| {candidate_id} | {lane} | {title}",
            ]
        )
        lines.extend(_t4_wrap_terminal_field("候选定位", _t4_candidate_lane_description(item, candidate_id), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("研究问题", item.get("target_problem"), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("候选来源", item.get("origin") or "未标注", indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("机制家族", item.get("mechanism_family") or "未标注", indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("核心主张", value_text, indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("技术机制", mechanism, indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("可检验预测", item.get("prediction"), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("反事实/证伪", item.get("counterfactual"), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("完整方向描述", full_title, indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("实践/管理含义", item.get("practical_implication"), indent=2, width=width))
        lines.append("  核心创新：")
        lines.extend(_t4_wrap_terminal_field("创新是什么", innovation.get("summary"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("创新类型", innovation.get("type"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("相对最近工作的变化", innovation.get("delta") or innovation.get("novelty_delta"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("为何非普通增量", innovation.get("non_incremental") or innovation.get("non_incremental_reason"), indent=4, width=width))
        lines.append("  候选假设链（仅供本轮选择，尚未写入最终假设文件）：")
        if hypotheses:
            for hypothesis in hypotheses:
                if not isinstance(hypothesis, dict):
                    continue
                hypothesis_id = str(hypothesis.get("id") or candidate_id + "-H?")
                lines.extend(
                    [
                        f"    {hypothesis_id}",
                    ]
                )
                lines.extend(_t4_wrap_terminal_field("命题", hypothesis.get("statement"), indent=6, width=width))
                lines.extend(_t4_wrap_terminal_field("机制", hypothesis.get("mechanism"), indent=6, width=width))
                lines.extend(_t4_wrap_terminal_field("可观测预测", hypothesis.get("prediction") or hypothesis.get("observable_prediction"), indent=6, width=width))
                lines.extend(_t4_wrap_terminal_field("判别测试", hypothesis.get("test") or hypothesis.get("discriminating_test"), indent=6, width=width))
        else:
            lines.append("    当前未提供模型生成的假设链；展示层不会补写 H1/H2/H3，请重新分析或定向回查。")
        if len(hypotheses) < 2:
            lines.append("    注：候选假设不足两条，当前不能确定方向；需要模型补全不同且可证伪的假设链。")
        lines.append("  可组合关系：")
        if merges:
            for merge in merges:
                if not isinstance(merge, dict):
                    continue
                lines.extend(_t4_wrap_terminal_field(
                    str(merge.get("combine") or "未提供组合") + f"（与 {merge.get('with') or '未指定'}）",
                    merge.get("rationale") or "未提供组合理由",
                    indent=4,
                    width=width,
                ))
        else:
            lines.append("    当前未提供；可输入“合并 D1-H1 + D3-H1”并说明保留哪条机制。")
        lines.append("  最小验证：")
        lines.extend(_t4_wrap_terminal_field("数据/任务", dataset, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("对照基线", baseline, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("指标", metric, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("预期信号", signal, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("协议证据状态", protocol_labels.get(protocol_status, f"未识别：{protocol_status}"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("协议来源", protocol_refs or "无；需要补充证据或人工决策", indent=4, width=width))
        lines.append("  评分与依据（1-5）：")
        if scores:
            for key, label in score_labels:
                if scores.get(key) is None:
                    continue
                lines.extend(_t4_wrap_terminal_field(f"{label} {scores[key]}/5", score_rationale.get(key) or "模型未提供独立评分依据；当前不能据此确定方向。", indent=4, width=width))
        else:
            lines.append("    当前未评分；不能据此自动排序。")
        lines.append("  证据与风险：")
        evidence_text = evidence + (f"；关联论文阅读笔记 {count} 篇" if count else "")
        lines.extend(_t4_wrap_terminal_field("证据基础", evidence_text, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("接地摘要", item.get("basis_summary"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("选择建议", item.get("selection_recommendation"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("反事实复核", item.get("counterfactual_check"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("最近先例", item.get("nearest_prior_work"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("新颖性信号", item.get("novelty_signal"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("风险/Kill criteria", warning, indent=4, width=width))
        evidence_chain = item.get("evidence_chain") if isinstance(item.get("evidence_chain"), list) else []
        lines.append("  证据链（来源观察 -> 当前设计含义）：")
        if not evidence_chain:
            lines.append("    当前未提供模型证据链；不能把来源路径替代为研究论证。")
        for link in evidence_chain:
            if not isinstance(link, dict):
                continue
            lines.extend(_t4_wrap_terminal_field("来源", link.get("ref"), indent=4, width=width))
            lines.extend(_t4_wrap_terminal_field("观察", link.get("observation"), indent=4, width=width))
            lines.extend(_t4_wrap_terminal_field("含义", link.get("implication"), indent=4, width=width))
        if original:
            lines.extend(_t4_wrap_terminal_field("英文原题", original, indent=2, width=width))
        support = item.get("supporting_papers") if isinstance(item.get("supporting_papers"), list) else []
        lines.append("  支撑文献与对应的论文阅读笔记：")
        if not support:
            lines.append("    当前候选未附带支撑论文；选择前需要补证据。")
        for index, paper in enumerate(support, start=1):
            if not isinstance(paper, dict):
                continue
            lines.extend(
                _t4_wrap_terminal_prose(
                    f"{index}. {paper.get('title') or '未命名论文'}",
                    indent=4,
                    width=width,
                )
            )
            lines.extend(_t4_wrap_terminal_field("引用", paper.get("citation"), indent=6, width=width))
            lines.extend(_t4_wrap_terminal_field("证据等级", paper.get("evidence_level"), indent=6, width=width))
            lines.extend(_t4_wrap_terminal_field("笔记路径", paper.get("note_path") or paper.get("source_file") or paper.get("path"), indent=6, width=width))
            lines.extend(_t4_wrap_terminal_field("该候选使用的证据", paper.get("claim_used") or paper.get("claim"), indent=6, width=width))
        lines.append(divider)
    hint = str(value.get("input_hint") or "")
    if hint:
        lines.extend(["", divider, "如何提交选择", *_t4_wrap_terminal_prose(hint, width=width)])
    detail_path = str(value.get("detail_path") or "")
    if detail_path:
        lines.extend(_t4_wrap_terminal_field("完整卡片（含原始证据摘录）", detail_path, width=width))
    navigation = value.get("file_navigation") if isinstance(value.get("file_navigation"), list) else []
    if navigation:
        lines.extend(["", divider, "文件导航（可直接打开核验）"])
        for item in navigation:
            if not isinstance(item, dict):
                continue
            lines.extend(_t4_wrap_terminal_field(f"- {item.get('path')}", item.get("purpose"), width=width))
    lines.append(divider)
    return "\n".join(lines)


def _format_t4_candidate_overview_model_authored(value: dict[str, Any]) -> str:
    """Plain-terminal view with the same Final Card boundary as Rich.

    Redirected/no-colour output must not get a weaker scientific contract. If
    complete LLM Cards are unavailable, this function returns only an
    operational recovery message and never substitutes legacy presentation
    fields as a research explanation.
    """

    width = 96
    divider = "=" * width
    candidates = [item for item in value.get("candidates", []) if isinstance(item, dict)]
    incomplete = [
        str(item.get("id") or "?")
        for item in candidates
        if _t4_complete_final_card(item.get("final_idea_card")) is None
    ]
    if incomplete:
        return "\n".join(
            [
                divider,
                "T4 卡片修复",
                "当前 Portfolio 尚未拥有完整的 LLM Idea Card；不会显示旧字段或固定模板形成的半成品解释。",
                "请在 T4 恢复决策中选择继续 LLM 卡片修复。",
                "缺少卡片：" + "、".join(incomplete),
                divider,
            ]
        )
    lines = [divider, f"Gate1 · 研究方向比较（{len(candidates)} 项）"]
    for item in candidates:
        final_card = _t4_complete_final_card(item.get("final_idea_card"))
        if final_card is None:
            # ``incomplete`` above normally covers this.  Retain the guard so
            # a mutable caller cannot race a plain-terminal render into an
            # old-field fallback.
            return "\n".join(
                [
                    divider,
                    "T4 卡片修复",
                    "当前 Portfolio 的 LLM Idea Card 在渲染前变为不完整；不会显示旧 Candidate 字段。",
                    "请在 T4 恢复决策中选择继续 LLM 卡片修复。",
                    divider,
                ]
            )
        candidate_id = str(item.get("id") or "")
        lane = str(item.get("lane") or "")
        title = str(final_card.get("short_title") or "")
        lines.extend([divider, f"{candidate_id} · {lane} · {title}"])
        for label, field in (
            ("一句话命题", "plain_language_summary"),
            ("核心命题", "core_thesis"),
            ("为何值得研究", "why_it_matters"),
            ("当前问题", "current_failure"),
            ("科学 / 技术核心", "scientific_technical_core"),
            ("代表性场景", "representative_scenario"),
            ("现实 / 应用意义", "real_world_significance"),
            ("创新性质", "innovation_type"),
            ("相对变化", "innovation_delta"),
            ("非惯例理由", "non_routine_explanation"),
            ("关系", "relationship_to_portfolio"),
            ("组合建议", "composition_guidance"),
            ("选择建议", "recommendation"),
            ("瓶颈解释", "bottleneck_explanation"),
        ):
            lines.extend(_t4_wrap_terminal_field(label, final_card.get(field), indent=2, width=width))
        hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            lines.extend(_t4_wrap_terminal_field(str(hypothesis.get("id") or ""), hypothesis.get("statement"), indent=2, width=width))
            lines.extend(_t4_wrap_terminal_field("机制", hypothesis.get("mechanism"), indent=4, width=width))
            lines.extend(_t4_wrap_terminal_field("预测", hypothesis.get("prediction") or hypothesis.get("observable_prediction"), indent=4, width=width))
            lines.extend(_t4_wrap_terminal_field("判别测试", hypothesis.get("test") or hypothesis.get("discriminating_test"), indent=4, width=width))
        for label, entries in (
            ("主要风险", final_card.get("risks_and_boundaries")),
        ):
            if isinstance(entries, list):
                lines.extend(_t4_wrap_terminal_field(label, "；".join(str(entry) for entry in entries if str(entry).strip()), indent=2, width=width))
    hint = str(value.get("input_hint") or "")
    if hint:
        lines.extend([divider, *_t4_wrap_terminal_prose(hint, width=width)])
    lines.extend(
        [
            divider,
            "如需核验文件路径或完整证据链，请输入“查看 D1 的证据 / 谱系 / 文件”。默认决策页不展开内部路径。",
        ]
    )
    lines.append(divider)
    return "\n".join(lines)


def _format_t36_survey_gate_field(key: str, value: Any) -> str | None:
    if key == "synthesis_preview":
        return _format_t36_synthesis_preview(value)
    if key == "weak_evidence_preview":
        return _format_t36_weak_evidence_preview(value)
    return None


def _format_t36_supplement_recommendation(value: Any) -> str:
    """Turn the corpus-gap calculation into a researcher decision aid.

    This is deliberately operational prose only.  It reports the existing
    taxonomy/coverage calculation and does not claim that a particular record
    will be relevant, citable, or sufficient evidence.
    """

    if not isinstance(value, dict):
        return "暂时无法计算补充检索建议；选择补检后仍可输入任意正整数作为目标。"
    suggested = value.get("suggested_target_records")
    basis = value.get("basis") if isinstance(value.get("basis"), dict) else {}
    purposes = value.get("coverage_purpose") if isinstance(value.get("coverage_purpose"), list) else []
    lines = [
        f"建议补充记录数：{suggested if suggested is not None else '未计算'}",
        "这是覆盖建议，不是配额或可接受范围；你可输入任意正整数，实际检索仍受本次运行与检索服务可用资源约束。",
    ]
    if basis:
        lines.append(
            "依据：已有全文阅读 {} 篇；taxonomy 类 {} 个；计划章节 {} 个；明确薄弱类 {} 个。".format(
                basis.get("deep_note_count", 0),
                basis.get("taxonomy_class_count", 0),
                basis.get("outline_section_count", 0),
                basis.get("explicit_weak_class_count", 0),
            )
        )
    if purposes:
        lines.append("优先覆盖：" + "、".join(str(item) for item in purposes if str(item).strip()) + "。")
    boundary = " ".join(str(value.get("boundary") or "").split())
    if boundary:
        lines.append("边界：" + boundary)
    return "\n".join(lines)


def _format_t36_synthesis_preview(value: Any) -> str:
    path, raw_text = _path_summary_text(value, default_path="literature/synthesis.md")
    text = _strip_gate_truncation_marker(raw_text)
    size = _path_summary_size(value)
    headings = _extract_markdown_headings(text, limit=7)
    bullets = _extract_first_markdown_bullets(text, limit=5)
    note_refs = len(set(re.findall(r"\[note:([^\]\s]+)\]", text)))
    citation_keys = {
        key.strip()
        for group in re.findall(r"\\cite(?:t|p)?\{([^}]+)\}", text)
        for key in group.split(",")
        if key.strip()
    }

    lines = [
        f"文件: {path}",
        "T3.5 已完成 literature synthesis。它会继续作为 T4 idea fuel；Survey 是额外分支。请选择使用当前语料，或先针对薄弱 taxonomy 类做一次定向补检。",
    ]
    if size is not None:
        lines.append(f"规模: 约 {size} 字符")
    signal_bits = []
    if headings:
        signal_bits.append("章节 " + str(len(headings)))
    if note_refs:
        signal_bits.append(f"note 引用 {note_refs}")
    if citation_keys:
        signal_bits.append(f"BibTeX 引用键 {len(citation_keys)}")
    if signal_bits:
        lines.append("结构信号: " + "；".join(signal_bits))
    if headings:
        lines.append("主要章节:")
        for item in headings:
            lines.append(f"- {item}")
    if bullets:
        lines.append("关键摘录:")
        for item in bullets:
            lines.append(f"- {_compact_text(item.lstrip('- ').strip(), 160)}")
    lines.append(
        "现在请判断：是否额外撰写 taxonomy-driven survey；可选不写 Survey、使用当前语料进入 Survey 规划，"
        "或先定向补检再进入规划。选择“不写综述”会直接进入 T4，不会丢弃 synthesis。"
    )
    return "\n".join(lines)


def _format_t36_weak_evidence_preview(value: Any) -> str:
    path, raw_text = _path_summary_text(value, default_path="literature/metadata_triage.md")
    text = _strip_gate_truncation_marker(raw_text)
    size = _path_summary_size(value)
    bullets = _extract_first_markdown_bullets(text, limit=5)
    lines = [
        f"文件: {path}",
        "作用: 标记 abstract-only / metadata-only / 低证据材料。它们可用于补资源或覆盖提示，不能直接支撑强 claim。",
    ]
    if size is not None:
        lines.append(f"规模: 约 {size} 字符")
    if bullets:
        lines.append("提示摘录:")
        for item in bullets:
            lines.append(f"- {_compact_text(item.lstrip('- ').strip(), 150)}")
    else:
        lines.append("当前没有可压缩展示的条目；详情见文件。")
    return "\n".join(lines)


def _format_t2_coverage_gate_field(key: str, value: Any) -> str | None:
    if key == "search_log":
        return _format_t2_search_log_summary(value)
    if key == "missing_areas":
        return _format_t2_missing_areas_summary(value)
    if key == "domain_map":
        return _format_t2_domain_map_summary(value)
    if key == "access_audit":
        return _format_t2_access_audit_summary(value)
    if key == "deep_read_queue_preview":
        return _format_t2_deep_read_queue_summary(value)
    return None


def _format_t2_search_log_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/search_log.md")
    raw_count = _find_first_int(text, r"原始结果:\s*([0-9,]+)")
    active_count = _find_first_int(text, r"当前阅读候选:\s*([0-9,]+)")
    if active_count is None:
        active_count = _find_first_int(text, r"去重后:\s*([0-9,]+)")
    retained = _find_first_int(text, r"\bretained=([0-9,]+)")
    backlog = _find_first_int(text, r"\bbacklog=([0-9,]+)")
    deep_target = _find_first_int(text, r"\bdeep_read_target=([0-9,]+)")

    lines = [f"文件: {path}", "本轮检索、信息核验和阅读安排已完成。"]
    metrics = []
    if raw_count is not None:
        metrics.append(f"检索记录 {raw_count}")
    if active_count is not None:
        metrics.append(f"当前阅读候选 {active_count}")
    if retained is not None:
        metrics.append(f"本轮保留 {retained}")
    if backlog is not None:
        metrics.append(f"后续可回看 {backlog}")
    if deep_target is not None:
        metrics.append(f"优先精读 {deep_target}")
    if metrics:
        lines.append("- " + "；".join(metrics))

    bucket_rows = _extract_markdown_table_rows(text, "Bucket 覆盖")
    bucket_bits = []
    for row in bucket_rows[:6]:
        if len(row) >= 4:
            bucket_bits.append(f"{_display_t2_retrieval_topic(row[0])} {row[3]} 篇")
    if bucket_bits:
        lines.append("- 检索主题覆盖: " + "；".join(bucket_bits))

    bridge_rows = _extract_markdown_table_rows(text, "Bridge Domain Plan 覆盖")
    if bridge_rows:
        status_counts: dict[str, int] = {}
        for row in bridge_rows:
            status = row[-1] if row else "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
        status_labels = {"covered": "已覆盖", "missing": "待补充", "skipped_by_user": "已跳过"}
        lines.append("- 跨领域检索计划: " + "；".join(f"{status_labels.get(status, status)} {count} 项" for status, count in sorted(status_counts.items())))

    for label in (
        "Active 切分前轻量补全",
        "多源摘要回填",
        "OpenAlex citation snowball 补全",
        "Crossref citation snowball 补全",
        "T2 raw 元数据缓存回写",
    ):
        line = _find_bullet_starting_with(text, label)
        if line:
            lines.append("- " + _format_t2_diagnostic_line(label, line))

    lines.append("详情保存在该文件；CLI 只展示摘要，不展开完整检索表。")
    return "\n".join(lines)


def _display_t2_retrieval_topic(value: Any) -> str:
    labels = {
        "core": "核心主题",
        "baseline": "基线方法",
        "evaluation": "评估设计",
        "adjacent_field": "相邻领域",
        "theory_bridge": "理论桥接",
        "snowball": "引用扩展",
        "seed": "种子论文",
    }
    normalized = str(value or "").strip().casefold()
    return labels.get(normalized, str(value or "未标注"))


def _format_t2_diagnostic_line(label: str, line: str) -> str:
    values = _parse_key_values(line)
    if label == "Active 切分前轻量补全":
        return (
            "资源轻量补全: "
            f"候选 {values.get('candidate', '?')}/{values.get('input', '?')}；"
            f"摘要线索 {values.get('abstract_after', '?')}；"
            f"PDF 线索 {values.get('pdf_hint_after', '?')}；"
            f"引用线索 {values.get('reference_hint_after', '?')}"
        )
    if label == "多源摘要回填":
        return (
            "摘要补全: "
            f"尝试 {values.get('attempted_single', values.get('attempted', '?'))}；"
            f"填充 {values.get('filled', '?')}；"
            f"仍缺 {values.get('remaining_missing_abstract', '?')}"
        )
    if label == "OpenAlex citation snowball 补全":
        return (
            "OpenAlex 引用扩展: "
            f"来源 {values.get('sources_used', '?')}；"
            f"看到引用 {values.get('reference_items_seen', '?')}；"
            f"新增/合并 {values.get('raw_persisted_or_merged', values.get('added', '?'))}；"
            f"失败 {values.get('failed', '?')}"
        )
    if label == "Crossref citation snowball 补全":
        return (
            "Crossref 引用扩展: "
            f"来源 {values.get('sources_used', '?')}；"
            f"看到引用 {values.get('reference_items_seen', '?')}；"
            f"title 解析 {values.get('title_references_resolved', '?')}；"
            f"新增 {values.get('raw_persisted', values.get('added', '?'))}；"
            f"失败 {values.get('failed', '?')}"
        )
    if label == "T2 raw 元数据缓存回写":
        return (
            "raw 元数据缓存: "
            f"总记录 {values.get('records_after', '?')}；"
            f"合并 {values.get('merged', '?')}；"
            f"新增 {values.get('appended', '?')}"
        )
    return _compact_text(line.lstrip("- ").strip(), 180)


def _parse_key_values(line: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^,;。]+)", line):
        values[match.group(1)] = match.group(2).strip()
    return values


def _format_t2_missing_areas_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/missing_areas.md")
    lines = [
        f"文件: {path}",
        "这是检索覆盖提示，不是最终研究缺口结论；进入 T3 后还需要结合阅读笔记复核。",
    ]
    good = _extract_bullets_under_heading(text, "覆盖较好的主题", limit=3)
    weak = _extract_bullets_under_heading(text, "覆盖不足的主题", limit=5)
    hints = _extract_hint_titles(text, limit=5)
    if good:
        lines.append("覆盖较好: " + "；".join(item.lstrip("- ").strip() for item in good))
    if weak:
        lines.append("建议关注/补检:")
        for item in weak:
            lines.append(f"- {item.lstrip('- ').strip()}")
    elif hints:
        lines.append("建议关注/补检:")
        for item in hints:
            lines.append(f"- {item}")
    lines.append("如果这些提示与你的研究边界不符，可以选择回到 T2 扩检/调整 query。")
    return "\n".join(lines)


def _format_t2_domain_map_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/domain_map.json")
    lines = [f"文件: {path}"]
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        counts = []
        for key, label in (
            ("core", "core"),
            ("theory_bridge", "theory_bridge"),
            ("adjacent", "adjacent"),
            ("citation_edges", "citation_edges"),
        ):
            value = data.get(key)
            if isinstance(value, list):
                counts.append(f"{label} {len(value)}")
        if counts:
            lines.append("Domain map: " + "；".join(counts))
    else:
        lines.append("Domain map 已生成，用于 T3.5 synthesis 和 T4 idea；CLI 不展开原始 JSON。")
    lines.append("重点看它是否覆盖 core / theory_bridge / adjacent 三类角色；详情见文件。")
    return "\n".join(lines)


def _format_t2_access_audit_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/access_audit.md")
    lines = [f"文件: {path}", "可读性与证据级别摘要:"]
    wanted_prefixes = (
        "候选论文总数",
        "`literature/pdfs/` 本地 PDF",
        "`user_seeds/pdfs/` 可匹配的 seed PDF",
        "`FULL_TEXT`",
        "`ABSTRACT_ONLY`",
        "`METADATA_ONLY`",
        "Access hint `POSSIBLE_FULL_TEXT`",
    )
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if any(body.startswith(prefix) for prefix in wanted_prefixes):
            found.append(body)
    for item in found[:8]:
        lines.append(f"- {item}")
    lines.append("Top candidate 表保存在文件中；CLI 不展开完整表格。")
    return "\n".join(lines)


def _format_t2_deep_read_queue_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/deep_read_queue.jsonl")
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            entries.append(item)
        if len(entries) >= 5:
            break
    lines = [f"文件: {path}", "T3 将优先按这个 deep-read queue 阅读。前几条预览:"]
    if entries:
        for item in entries:
            rank = item.get("queue_rank") or item.get("rank") or "?"
            title = _compact_text(item.get("title") or item.get("paper_id") or "untitled", 100)
            bucket = item.get("target_bucket") or item.get("search_bucket") or item.get("queue_reason") or "unknown"
            evidence = item.get("evidence_level") or item.get("access_level_hint") or "unknown"
            lines.append(f"- #{rank} {title}（{bucket}; {evidence}）")
    else:
        lines.append("- 暂无法解析预览；请打开文件查看。")
    lines.append("如果队列方向明显不对，选择回到 T2 扩检/调整 query。")
    return "\n".join(lines)


def _find_first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except Exception:
        return None


def _find_bullet_starting_with(text: str, label: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- " + label):
            return stripped
    return None


def _extract_markdown_headings(text: str, *, limit: int) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        match = re.match(r"^#{1,3}\s+(.+)$", stripped)
        if not match:
            continue
        title = match.group(1).strip()
        if title and title not in headings:
            headings.append(_compact_text(title, 140))
        if len(headings) >= limit:
            break
    return headings


def _extract_first_markdown_bullets(text: str, *, limit: int) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("- ", "* ")):
            continue
        body = stripped[2:].strip()
        if not body or set(body) <= {"-", "=", ":"}:
            continue
        bullets.append(body)
        if len(bullets) >= limit:
            break
    return bullets


def _extract_markdown_table_rows(text: str, heading: str) -> list[list[str]]:
    rows: list[list[str]] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = heading in stripped
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or cells[0] in {"---", "Bucket", "Bridge", "#"} or set(cells[0]) <= {"-", ":"}:
            continue
        if len(cells) >= 2 and all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _extract_bullets_under_heading(text: str, heading: str, *, limit: int) -> list[str]:
    bullets: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = heading in stripped
            continue
        if in_section and stripped.startswith("- "):
            bullets.append(stripped)
            if len(bullets) >= limit:
                break
    return bullets


def _extract_hint_titles(text: str, *, limit: int) -> list[str]:
    hints: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^###\s+提示\s+\d+\s*:\s*(.+)$", stripped)
        if match:
            hints.append(match.group(1).strip())
            if len(hints) >= limit:
                break
    return hints


def _format_t2_parameter_preview(value: dict[str, Any]) -> str:
    """Render a compact preset comparison plus explicit language policy."""

    lines = [
        f"任务类型：{value.get('detected_profile', 'unknown')}",
        f"当前推荐：{value.get('recommended_label') or value.get('recommended_option')}",
    ]
    recommended_summary = value.get("recommended_summary") if isinstance(value.get("recommended_summary"), dict) else {}
    if recommended_summary:
        lines.append("回车将采用：" + _format_t2_compact_summary(recommended_summary))
    lines.extend(
        [
            "自定义输入使用 LLM 意图解析，并由本地规则验证数值和语言策略；LLM 不可用时会明确降级提示。",
            "可直接输入：英文稿，候选30篇，精读15篇，粗读15篇；或：中文稿，允许中文文献，精读30篇。",
            "",
            "写作语言与检索边界：",
            "- 英文稿（en）：自动排除所有非 seed 中文文献，不检索、不主动引用；中文 seed 仅保留为上下文线索。",
            "- 中文稿（zh）：中文和英文文献均可进入候选池，中文来源会标记权威性复核状态。",
            "- 双语稿（mixed）：中文和英文均可保留，并在后续引用审计中标识语言与证据等级。",
            "- 自动（auto）：根据项目和你的输入推断；最终确认页会展示实际生效语言和中文文献动作。",
        ]
    )
    options = value.get("options")
    if isinstance(options, dict) and options:
        lines.append("")
        lines.append("档位比较：")
        for option_id, option in options.items():
            if not isinstance(option, dict):
                continue
            summary = option.get("summary") if isinstance(option.get("summary"), dict) else {}
            marker = "（推荐）" if option.get("recommended") else ""
            compact = option.get("compact_preview") or _format_t2_compact_summary(summary)
            lines.append(f"- {option.get('label') or option_id}{marker}：{compact}")
    lines.append("选“自定义”后只需输入一次整句；未写字段保留当前推荐，确认页会显示 LLM/规则解析来源。")
    return "\n".join(lines)


def _format_t2_selected_parameters_summary(value: dict[str, Any]) -> str:
    path = str(value.get("path") or "literature/literature_params.json")
    raw = str(value.get("summary") or "")
    data = _parse_t2_params_summary(raw)
    if data is None:
        return "\n".join(
            [
                f"文件: {path}",
                "关键参数摘要暂无法解析；完整参数仍已写入该文件。",
                "请打开文件检查，或选择“返回重选参数”。",
            ]
        )
    if not isinstance(data, dict):
        return f"文件: {path}"

    summary = data.get("selected_summary") if isinstance(data.get("selected_summary"), dict) else {}
    reader = data.get("reader") if isinstance(data.get("reader"), dict) else {}
    abstract_sweep = reader.get("abstract_sweep") if isinstance(reader.get("abstract_sweep"), dict) else {}
    quality = data.get("literature_quality") if isinstance(data.get("literature_quality"), dict) else {}
    lines = [
        f"文件: {path}",
        f"已选择档位: {data.get('selected_label') or data.get('selected_option')}",
        "关键参数:",
    ]
    explained_summary = {
        "active_pool_max": summary.get("active_pool_max") or (data.get("t2_finalize") or {}).get("active_pool_max"),
        "deep_read_min": reader.get("deep_read_min"),
        "deep_read_target": reader.get("deep_read_target"),
        "deep_read_max": reader.get("deep_read_max"),
        "require_deep_read_target": reader.get("require_deep_read_target"),
        "abstract_sweep_target": abstract_sweep.get("lite_paper_num"),
        "manuscript_language": quality.get("manuscript_language", "auto"),
        "include_chinese_literature": quality.get("include_chinese_literature", "auto"),
        "chinese_literature_policy": quality.get("chinese_literature_policy", "review_flag_only"),
        "effective_non_seed_chinese_action": quality.get("effective_non_seed_chinese_action"),
    }
    lines.extend(_format_t2_explained_summary_lines(explained_summary))
    coverage_adjustment = data.get("coverage_adjustment")
    if isinstance(coverage_adjustment, dict):
        requested = coverage_adjustment.get("requested_active_pool_max")
        effective = coverage_adjustment.get("effective_active_pool_max")
        human_summary = str(coverage_adjustment.get("human_summary") or "").strip()
        lines.extend(
            [
                "候选数已按阅读分配调整:",
                human_summary
                or (
                    f"精读与摘要轻读之和超过原候选 {requested} 篇；"
                    f"本轮候选已调整为 {effective} 篇。"
                ),
                "这是在开始 T2 前扩大本轮候选范围，不是从后备清单静默追加阅读。",
            ]
        )
    captured = data.get("captured") if isinstance(data.get("captured"), dict) else {}
    parser_source = str(captured.get("parser_source") or "").strip()
    if parser_source:
        parser_labels = {
            "llm_validated": "LLM 意图解析 + 本地规则校验",
            "llm_fallback": "LLM 不可用后已降级为本地规则解析",
            "deterministic_fallback": "本次运行未配置 LLM，已使用本地规则解析",
        }
        lines.append(f"参数解析：{parser_labels.get(parser_source, parser_source)}")
    if captured:
        compact = "; ".join(f"{key}={value}" for key, value in captured.items() if value not in (None, ""))
        if compact:
            lines.append(f"用户自定义输入: {compact}")
    lines.append("确认后才会启动 T2；如果这里不符合预期，请选择返回重选参数。")
    return "\n".join(lines)


def _parse_t2_params_summary(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        data = {}
        for key in (
            "selected_label",
            "selected_option",
            "confirmation_summary",
            "selected_summary",
            "t2_finalize",
            "reader",
            "literature_quality",
            "captured",
        ):
            value = _extract_json_value_for_key(text, key)
            if value is not None:
                data[key] = value
        if not data:
            return None
    return data if isinstance(data, dict) else None


def _extract_json_value_for_key(text: str, key: str) -> Any | None:
    marker = f'"{key}"'
    start = text.find(marker)
    if start < 0:
        return None
    colon = text.find(":", start + len(marker))
    if colon < 0:
        return None
    payload = text[colon + 1 :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except Exception:
        return None
    return value


def _format_t2_explained_summary(summary: dict[str, Any]) -> str:
    return "；".join(_format_t2_explained_summary_lines(summary))


def _format_t2_compact_summary(summary: dict[str, Any]) -> str:
    total_target = _t2_summary_total_read_target(summary)
    require = "读满目标" if summary.get("require_deep_read_target") is True else "达到最低线可继续"
    return (
        f"阅读候选 {total_target} 篇 = 精读 {summary.get('deep_read_target')} + "
        f"摘要轻读 {summary.get('abstract_sweep_target')} | {require}"
    )


def _format_t2_explained_summary_lines(summary: dict[str, Any]) -> list[str]:
    deep_min = summary.get("deep_read_min")
    deep_target = summary.get("deep_read_target")
    deep_max = summary.get("deep_read_max")
    require = summary.get("require_deep_read_target")
    require_text = "未达目标不进入 T3.5" if require is True else "达到最低线即可继续" if require is False else "按系统默认判断"
    total_target = _t2_summary_total_read_target(summary)
    manuscript_language = str(summary.get("manuscript_language", "auto"))
    include_chinese = str(summary.get("include_chinese_literature", "auto"))
    effective_action = str(summary.get("effective_non_seed_chinese_action") or "")
    pool = summary.get("active_pool_max")
    shallow_target = summary.get("abstract_sweep_target")
    try:
        split_total = int(deep_target or 0) + int(shallow_target or 0)
    except (TypeError, ValueError):
        split_total = None
    split_note = (
        f"阅读分配：{pool} 篇不同论文 = {deep_target} 篇精读 + {shallow_target} 篇摘要轻读。"
        if split_total is not None and str(pool) == str(split_total)
        else (
            "阅读分配：当前精读与摘要轻读目标和候选数不一致；请返回重选参数。"
            if split_total is not None and pool not in (None, "")
            else "阅读分配：摘要轻读使用 all_readable，覆盖范围由保留候选数决定。"
        )
    )
    lines = [
        f"本轮阅读覆盖：最多 {total_target} 篇不同论文。候选数不是额外的阅读数量。",
        f"保留候选：{pool} 篇。T2 从检索结果中保留这些论文进入本轮阅读；其余保留在后备清单，可追溯但不会默认额外阅读。",
        split_note,
        f"深入阅读：目标 {deep_target} 篇（最低 {deep_min}，最多 {deep_max}）。",
        f"读满目标门槛：{require_text}（require_target={require}；可选：true/false）",
        f"摘要轻读：目标 {shallow_target} 篇。仅记录题目、摘要和元数据层面的证据，不能单独支持强结论。",
        f"稿件语言：{manuscript_language}（manuscript_language={manuscript_language}；可选：auto/en/zh/mixed）",
        f"中文文献：{include_chinese}（include_zh={include_chinese}；可选：auto/true/false；策略={summary.get('chinese_literature_policy', 'review_flag_only')}）",
    ]
    if manuscript_language == "en" or effective_action == "exclude":
        lines.append("生效检索策略：英文稿，自动排除非 seed 中文文献；不会将其加入检索候选或主动引用。")
    elif manuscript_language == "zh":
        lines.append("生效检索策略：中文和英文文献均可进入候选池；中文来源会进行权威性复核标记。")
    elif manuscript_language == "mixed":
        lines.append("生效检索策略：中文和英文文献均可进入候选池；后续引用审计会保留语言和证据等级。")
    else:
        lines.append("生效检索策略：尚为自动推断；开始 T2 前请确认中文文献准入设置。")
    return lines


def _t2_summary_total_read_target(summary: dict[str, Any]) -> int | str | None:
    active = summary.get("active_pool_max")
    try:
        if active not in (None, ""):
            return max(0, int(active))
    except (TypeError, ValueError):
        pass
    abstract_target = summary.get("abstract_sweep_target")
    if str(abstract_target).strip().casefold() in {"all", "all_readable", "unlimited", "全部"}:
        return summary.get("active_pool_max")
    try:
        return int(summary.get("deep_read_target") or 0) + int(abstract_target or 0)
    except (TypeError, ValueError):
        return summary.get("active_pool_max")
