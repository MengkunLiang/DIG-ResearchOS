from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import json
import re


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
        print(json.dumps(arguments, indent=2, ensure_ascii=False))
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
            print(json.dumps(suggestions, indent=2, ensure_ascii=False))
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
        print("═" * 60)
        for key, value in presentation.items():
            if key.startswith("_"):
                continue
            print(f"\n【{key}】")
            if isinstance(value, str):
                print(value)
            else:
                print(json.dumps(value, indent=2, ensure_ascii=False))
        for idx, option in enumerate(options, start=1):
            default_marker = " [默认]" if option.get("is_default") else ""
            print(f"[{idx}] {option['label']}{default_marker}")
            if option.get("parameter_preview"):
                print(f"    参数: {option['parameter_preview']}")
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
        return {"option_id": option_id, "captured": captured}

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
