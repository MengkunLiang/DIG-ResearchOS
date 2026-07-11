from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import json
import re
from typing import Any


_READLINE_CONFIGURED = False


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
    return input(prompt)


def _compact_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


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
        "candidate_pool_fingerprints": "候选池校验",
        "input_fingerprints": "输入校验",
        "survey_summary": "综述摘要",
        "synthesis_preview": "T3.5 综合摘要",
        "weak_evidence_preview": "弱证据提示",
        "survey_compile_report": "编译报告",
        "survey_insights": "综述洞察",
        "how_to_choose": "如何选择",
        "task_id": "任务",
        "run_id": "运行",
        "failures": "失败次数",
        "retry_limit": "自动修复上限",
        "last_error": "最近错误",
        "existing_outputs": "已有输出",
    }
    if key in labels:
        return labels[key]
    return key.replace("_", " ")


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
    SEPARATOR_WIDTH = 80

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        print("\n" + "═" * 60)
        print(f"工具请求批准: {tool_name}")
        print("原因：该工具可能执行高风险或外部副作用操作，需要用户显式确认。")
        print(f"输入摘要：{_summarize_arguments(arguments)}")
        print("═" * 60)
        try:
            answer = _read_cli_line("批准执行? [y/N]: ").strip().lower()
        except EOFError as exc:
            raise HumanInputUnavailable(f"{tool_name} 需要用户批准，但当前输入不可用。") from exc
        return answer in {"y", "yes"}

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        print("\n" + "═" * self.SEPARATOR_WIDTH)
        print("需要人工输入")
        print("═" * self.SEPARATOR_WIDTH)
        print(question)
        if suggestions:
            print("\n参考选项 / 建议：")
            for idx, item in enumerate(suggestions, start=1):
                print(f"- [{idx}] {_compact_text(item, 180)}")
        print("-" * self.SEPARATOR_WIDTH)
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
                print("-" * self.SEPARATOR_WIDTH)
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
        print("\n" + "═" * 60)
        print(f"GATE {gate_id}")
        if title:
            print(title)
        if description:
            print(description)
        print("请选择后继续；ResearchOS 会把选择写入 workspace，并按该选择推进。")
        print("═" * 60)
        for key, value in presentation.items():
            if key.startswith("_"):
                continue
            rendered = self._format_presentation_value(key, value, gate_id=gate_id)
            if not rendered.strip():
                continue
            print(f"\n【{_humanize_presentation_key(key)}】")
            print(rendered)
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
        selected = None
        while selected is None:
            try:
                raw_answer = _read_cli_line("请选择: ").strip()
            except EOFError:
                raise HumanInputUnavailable(f"Gate {gate_id} 需要用户选择，但当前输入不可用。") from None
            inline_result = self._parse_inline_gate_customization(gate_id, raw_answer, options)
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
                print(f"无效选择: {raw_answer!r}。请输入 1-{len(options)}。")
                continue
            selected = options[answer]
        captured: dict[str, str] = {}
        for field_name in selected.get("collect_input", []):
            prompt = self._collect_input_prompt(selected, field_name)
            try:
                captured[field_name] = _read_cli_line(f"{prompt}: ").strip()
            except EOFError as exc:
                raise HumanInputUnavailable(f"Gate {gate_id} 需要输入 {field_name}，但当前输入不可用。") from exc
        defaults = selected.get("captured_defaults")
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                captured.setdefault(str(key), str(value))
        option_id = selected.get("id") or selected.get("key")
        if gate_id == "t5_executor_gate" and option_id == "codex_cli":
            print(
                "codex_cli 将允许在 external_executor/workdir 内运行真实实验，"
                "可能消耗较多算力/时间。"
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

    @staticmethod
    def _format_presentation_value(key: str, value: Any, *, gate_id: str = "") -> str:
        """Render gate presentation values for humans instead of dumping JSON by default."""

        if gate_id == "t2_coverage_gate":
            rendered = _format_t2_coverage_gate_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t36_survey_gate":
            rendered = _format_t36_survey_gate_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t2_literature_param_confirm_gate" and key == "selected_parameters":
            if isinstance(value, str):
                return _format_t2_selected_parameters_summary(
                    {"path": "literature/literature_params.json", "summary": value}
                )
            if _is_path_summary(value):
                return _format_t2_selected_parameters_summary(value)
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
        lines = ["-" * CLIHumanInterface.SEPARATOR_WIDTH, f"已确认选择: {label} ({option_id})"]
        if captured:
            compact = "; ".join(f"{key}={value}" for key, value in captured.items() if value not in (None, ""))
            if compact:
                lines.append(f"记录的补充输入: {compact}")
        if gate_id == "t2_literature_param_gate":
            lines.append("将写入: literature/literature_params.json；下一步: 参数最终确认 Gate")
        elif gate_id == "t2_literature_param_confirm_gate":
            if option_id == "confirm_start_t2":
                lines.append("将写入: literature/literature_params_confirmation.json；下一步: T2 文献检索")
            elif option_id == "revise_params":
                lines.append("不会启动 T2；下一步: 返回 T2 参数选择 Gate")
            elif option_id == "stop_project":
                lines.append("将结束当前项目，不启动 T2")
        elif gate_id == "t4_gate1_selection_gate":
            lines.append("将写入: ideation/_gate1_user_selection.json 和 ideation/selected_idea_brief.md；下一步: T4 后半段")
        elif gate_id == "t36_post_survey_gate":
            lines.append("将写入: drafts/survey/post_survey_decision.json")
        elif gate_id in {"t36_template_gate", "t8_style_template_gate"}:
            target = "drafts/survey/writing_template.json" if gate_id == "t36_template_gate" else "drafts/writing_style.json"
            lines.append(f"将写入: {target}")
        lines.append("-" * CLIHumanInterface.SEPARATOR_WIDTH)
        return "\n".join(lines)

    @staticmethod
    def _parse_inline_gate_customization(gate_id: str, raw_answer: str, options: list[dict]) -> dict | None:
        if gate_id in {"t36_template_gate", "t8_style_template_gate"}:
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

    @staticmethod
    def _parse_t4_gate1_text(raw_answer: str, options: list[dict]) -> dict | None:
        text = str(raw_answer or "").strip()
        if not text:
            return None
        normalized = text.replace("，", ",").replace("＋", "+").strip()
        lowered = normalized.casefold()
        option_ids = {str(option.get("id") or option.get("key") or "") for option in options}
        candidate_pattern = r"\b[DS]\d+\b"
        candidates = [item.upper() for item in re.findall(candidate_pattern, normalized, flags=re.IGNORECASE)]
        unique_candidates = list(dict.fromkeys(candidates))

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
        elif any(token in normalized for token in ("ccf", "neurips", "kdd", "conference", "会议", "ccf-a", "ccf_a")):
            template_id = "kdd" if "kdd" in normalized else "neurips"
            captured.update({"template_family": "ccf", "template_id": template_id, "writing_language": "en"})
            option_id = "ccf_neurips"
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

        if gate_id == "t8_style_template_gate":
            if "venue_style" not in captured:
                if option_id == "is_informs":
                    captured["venue_style"] = "is"
                elif option_id == "both_basic_en":
                    captured["venue_style"] = "both"
                elif option_id:
                    captured["venue_style"] = "ccf_a"

        if not captured:
            return None
        if not option_id or option_id not in option_ids:
            option_id = "custom" if "custom" in option_ids else next(iter(option_ids), "")
        if not option_id:
            return None
        return {"option_id": option_id, "captured": captured}

    @staticmethod
    def _parse_t2_literature_param_text(raw_answer: str) -> dict[str, str]:
        text = str(raw_answer or "").strip()
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
        elif any(token in normalized for token in ("允许中文论文", "允许中文文献", "检索中文", "包含中文", "包括中文")):
            captured["include_chinese_literature"] = "true"
        if any(token in normalized for token in ("不粗读", "不要粗读", "不略读", "不要略读", "不做粗读", "不做摘要轻读")):
            captured["abstract_sweep_target"] = "0"

        deep_triplet = re.search(
            r"\bdeep[_\s-]*read\b\s*(?:=|:|为)?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if not deep_triplet:
            deep_triplet = re.search(
                r"(?:精读|深读)\s*(?:=|:|为)?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
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
                r"(?:保留候选数|候选池|候选数|保留候选|active\s*pool)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
            ],
            "deep_read_target": [
                r"\bdeep[_\s-]*read(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:精读目标|精读|深读目标|深读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
            ],
            "abstract_sweep_target": [
                r"\babstract[_\s-]*sweep(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)",
                r"(?:摘要轻读|轻读|略读|粗读|摘要阅读|浅读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)",
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
            return "mock_dry_run"
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


def _format_t36_survey_gate_field(key: str, value: Any) -> str | None:
    if key == "synthesis_preview":
        return _format_t36_synthesis_preview(value)
    if key == "weak_evidence_preview":
        return _format_t36_weak_evidence_preview(value)
    return None


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
        "T3.5 已完成 literature synthesis。它会继续作为 T4 idea fuel；是否写综述论文是额外分支选择。",
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
    lines.append("现在只需判断：是否额外撰写 taxonomy-driven survey。选择“不写综述”会直接进入 T4，不会丢弃 synthesis。")
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
    dedup_count = _find_first_int(text, r"去重后:\s*([0-9,]+)")
    retained = _find_first_int(text, r"\bretained=([0-9,]+)")
    backlog = _find_first_int(text, r"\bbacklog=([0-9,]+)")
    deep_target = _find_first_int(text, r"\bdeep_read_target=([0-9,]+)")

    lines = [f"文件: {path}", "T2 已完成检索、去重、保留候选切分和 deep-read queue 构建。"]
    metrics = []
    if raw_count is not None:
        metrics.append(f"原始结果 {raw_count}")
    if dedup_count is not None:
        metrics.append(f"去重后 {dedup_count}")
    if retained is not None:
        metrics.append(f"保留候选 {retained}")
    if backlog is not None:
        metrics.append(f"backlog {backlog}")
    if deep_target is not None:
        metrics.append(f"精读目标 {deep_target}")
    if metrics:
        lines.append("- " + "；".join(metrics))

    bucket_rows = _extract_markdown_table_rows(text, "Bucket 覆盖")
    bucket_bits = []
    for row in bucket_rows[:6]:
        if len(row) >= 4:
            bucket_bits.append(f"{row[0]}: {row[3]} retained")
    if bucket_bits:
        lines.append("- 覆盖桶: " + "；".join(bucket_bits))

    bridge_rows = _extract_markdown_table_rows(text, "Bridge Domain Plan 覆盖")
    if bridge_rows:
        status_counts: dict[str, int] = {}
        for row in bridge_rows:
            status = row[-1] if row else "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
        lines.append(
            "- Bridge plan: "
            + "；".join(f"{status} {count}" for status, count in sorted(status_counts.items()))
        )

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
    lines = [
        f"检测到的任务类型: {value.get('detected_profile', 'unknown')}",
        f"当前推荐: {value.get('recommended_label') or value.get('recommended_option')}",
    ]
    if value.get("recommended_human_summary"):
        lines.append("默认回车将写入:")
        for line in str(value["recommended_human_summary"]).splitlines():
            if line.strip():
                lines.append(f"- {line.strip()}")
    meanings = value.get("parameter_meanings_short")
    if isinstance(meanings, dict) and meanings:
        lines.append("")
        lines.append("关键参数含义:")
        for item in meanings.values():
            lines.append(f"- {item}")
    options = value.get("options")
    if isinstance(options, dict) and options:
        lines.append("")
        lines.append("各档位实际数值:")
        for option_id, option in options.items():
            if not isinstance(option, dict):
                continue
            summary = option.get("summary") if isinstance(option.get("summary"), dict) else {}
            marker = "（推荐）" if option.get("recommended") else ""
            explained = option.get("explained_preview") or _format_t2_explained_summary(summary)
            lines.append(f"- {option.get('label') or option_id}{marker}:")
            for explained_line in str(explained).splitlines():
                if explained_line.strip():
                    lines.append(f"  - {explained_line.strip()}")
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
    }
    lines.extend(_format_t2_explained_summary_lines(explained_summary))
    captured = data.get("captured") if isinstance(data.get("captured"), dict) else {}
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


def _format_t2_explained_summary_lines(summary: dict[str, Any]) -> list[str]:
    deep_min = summary.get("deep_read_min")
    deep_target = summary.get("deep_read_target")
    deep_max = summary.get("deep_read_max")
    require = summary.get("require_deep_read_target")
    require_text = "未达目标不进入 T3.5" if require is True else "达到最低线即可继续" if require is False else "按系统默认判断"
    total_target = _t2_summary_total_read_target(summary)
    return [
        f"总阅读覆盖：约 {total_target} 篇（total=deep_read_target+abstract_sweep；可选：total=30 或 总共30）",
        f"保留候选：{summary.get('active_pool_max')} 篇（active_pool_max={summary.get('active_pool_max')}；可选：120/180/240 或自定义）",
        f"深入阅读：目标 {deep_target} 篇（deep_read={deep_min}/{deep_target}/{deep_max}；格式：min/target/max）",
        f"读满目标门槛：{require_text}（require_target={require}；可选：true/false）",
        f"摘要轻读：{summary.get('abstract_sweep_target')} 篇（abstract_sweep={summary.get('abstract_sweep_target')}；别名：粗读/略读/rough；可选：数字或 all_readable）",
        f"稿件语言：{summary.get('manuscript_language', 'auto')}（manuscript_language={summary.get('manuscript_language', 'auto')}；可选：auto/en/zh/mixed）",
        f"中文文献：{summary.get('include_chinese_literature', 'auto')}（include_zh={summary.get('include_chinese_literature', 'auto')}；可选：auto/true/false；策略={summary.get('chinese_literature_policy', 'review_flag_only')}）",
    ]


def _t2_summary_total_read_target(summary: dict[str, Any]) -> int | str | None:
    abstract_target = summary.get("abstract_sweep_target")
    if str(abstract_target).strip().casefold() in {"all", "all_readable", "unlimited", "全部"}:
        return summary.get("active_pool_max")
    try:
        return int(summary.get("deep_read_target") or 0) + int(abstract_target or 0)
    except (TypeError, ValueError):
        return summary.get("active_pool_max")
