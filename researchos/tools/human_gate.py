from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import json
import re
from typing import Any


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
            answer = input("批准执行? [y/N]: ").strip().lower()
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
                    line = input("> ")
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
                raw_answer = input("请选择: ").strip()
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
                captured[field_name] = input(f"{prompt}: ").strip()
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
                confirm = input("确认允许真实实验？输入 yes 继续，其它任意输入降级为 Claude Code 窗口: ").strip()
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

        if isinstance(value, str):
            return value
        if gate_id == "t2_literature_param_confirm_gate" and _is_path_summary(value):
            return _format_t2_selected_parameters_summary(value)
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
                r"(?:摘要轻读|轻读|摘要阅读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)",
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
            "deep": "deep_read_target",
            "deep_read": "deep_read_target",
            "deep_read_min": "deep_read_min",
            "deep_read_target": "deep_read_target",
            "deep_read_max": "deep_read_max",
            "abstract": "abstract_sweep_target",
            "abstract_sweep": "abstract_sweep_target",
            "abstract_sweep_target": "abstract_sweep_target",
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
    try:
        data = json.loads(raw)
    except Exception:
        header = [f"文件: {path}"]
        if raw:
            header.extend(["摘要:", raw])
        return "\n".join(header)
    if not isinstance(data, dict):
        return f"文件: {path}"

    summary = data.get("selected_summary") if isinstance(data.get("selected_summary"), dict) else {}
    reader = data.get("reader") if isinstance(data.get("reader"), dict) else {}
    abstract_sweep = reader.get("abstract_sweep") if isinstance(reader.get("abstract_sweep"), dict) else {}
    quality = data.get("literature_quality") if isinstance(data.get("literature_quality"), dict) else {}
    lines = [
        f"文件: {path}",
        f"已选择档位: {data.get('selected_label') or data.get('selected_option')}",
    ]
    if data.get("confirmation_summary"):
        lines.append(f"确认摘要: {data['confirmation_summary']}")
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


def _format_t2_explained_summary(summary: dict[str, Any]) -> str:
    return "；".join(_format_t2_explained_summary_lines(summary))


def _format_t2_explained_summary_lines(summary: dict[str, Any]) -> list[str]:
    deep_min = summary.get("deep_read_min")
    deep_target = summary.get("deep_read_target")
    deep_max = summary.get("deep_read_max")
    require = summary.get("require_deep_read_target")
    require_text = "未达目标不进入 T3.5" if require is True else "达到最低线即可继续" if require is False else "按系统默认判断"
    return [
        f"保留候选：{summary.get('active_pool_max')} 篇（active_pool_max={summary.get('active_pool_max')}；可选：120/180/240 或自定义）",
        f"深入阅读：目标 {deep_target} 篇（deep_read={deep_min}/{deep_target}/{deep_max}；格式：min/target/max）",
        f"读满目标门槛：{require_text}（require_target={require}；可选：true/false）",
        f"摘要轻读：{summary.get('abstract_sweep_target')} 篇（abstract_sweep={summary.get('abstract_sweep_target')}；可选：数字或 all_readable）",
        f"稿件语言：{summary.get('manuscript_language', 'auto')}（manuscript_language={summary.get('manuscript_language', 'auto')}；可选：auto/en/zh/mixed）",
        f"中文文献：{summary.get('include_chinese_literature', 'auto')}（include_zh={summary.get('include_chinese_literature', 'auto')}；可选：auto/true/false；策略={summary.get('chinese_literature_policy', 'review_flag_only')}）",
    ]
