"""把外部 skill 包装成 ResearchOS Agent。"""

from __future__ import annotations


import json
import re

from jinja2 import StrictUndefined, Template

from ..runtime.errors import ConfigurationError
from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from .loader import Skill
from .tool_aliases import translate_tool_names
from .contracts import parse_skill_tool_call_budget
from .workflow import parse_skill_workflow, workflow_prompt_block


class SkillAgent(Agent):
    """Skill 的运行时适配器。

    设计目标：
    - 尽量不改 skill 原始 prompt；
    - 只在 runtime 侧补最小上下文与工具翻译；
    - 让 skill 能像普通 Agent 一样被 AgentRunner 驱动。
    """

    def __init__(
        self,
        *,
        skill: Skill,
        available_tools: set[str],
        llm_profile: str | None = None,
    ):
        translated, warnings = translate_tool_names(skill.allowed_tools, available_tools=available_tools)
        if skill.metadata.get("strict_tools") and warnings:
            raise ConfigurationError(
                f"Skill '{skill.name}' declares strict_tools but has unavailable tools: "
                + "; ".join(warnings)
            )
        if "finish_task" in available_tools and "finish_task" not in translated:
            translated.append("finish_task")
        metadata = skill.metadata
        interaction = metadata.get("interaction") if isinstance(metadata.get("interaction"), dict) else {}
        guided = str(interaction.get("mode") or "guided") == "guided"
        workflow = parse_skill_workflow(metadata)
        # Public Skills share the workspace's single LLM connection. Metadata
        # tiers are accepted only as legacy input and no longer select a model.
        model_tier = "standard"
        if guided and "ask_human" in available_tools and "ask_human" not in translated:
            # Guided Skills need one safe channel for a semantic evidence gap
            # discovered after deterministic file checks have passed.
            translated.append("ask_human")
        if workflow and "update_skill_workflow" in available_tools and "update_skill_workflow" not in translated:
            translated.append("update_skill_workflow")
        allowed_write_prefixes = list(metadata.get("allowed_write_prefixes", [""]))
        if guided:
            intake_prefix = f"user_inputs/{skill.name}/"
            if intake_prefix not in allowed_write_prefixes:
                allowed_write_prefixes.append(intake_prefix)
        super().__init__(
            AgentSpec(
                name=f"skill_{skill.name}",
                model_tier=model_tier,
                tool_names=translated,
                # A guided Skill may need additional evidence checks or several
                # rounds of human follow-up.  Its lifecycle must never stop
                # because an arbitrary per-SKILL token/step ceiling was reached.
                # Provider/context failures, cancellation, human pauses, and
                # output validation still remain explicit, recoverable stops.
                max_steps=0,
                max_tokens_total=0,
                max_wall_seconds=int(metadata.get("max_wall_seconds", 1800)),
                unlimited_budget=True,
                temperature=float(metadata.get("temperature", 0.2)),
                llm_profile=None,
                prompt_template=None,
                allowed_read_prefixes=list(metadata.get("allowed_read_prefixes", [""])),
                allowed_write_prefixes=allowed_write_prefixes,
            )
        )
        self.skill = skill
        self.use_jinja = bool(metadata.get("use-jinja", False))
        self.translation_warnings = warnings
        self.workflow = workflow
        self.tool_call_budget = parse_skill_tool_call_budget(metadata)

    def _bundled_reference_block(self) -> str:
        """Expose repo-bundled Skill references without granting workspace file access."""

        references_dir = self.skill.skill_dir / "references"
        if not references_dir.is_dir():
            return ""
        try:
            max_chars = int(self.skill.metadata.get("reference_prompt_max_chars", 60000))
        except (TypeError, ValueError):
            max_chars = 60000
        if max_chars <= 0:
            return ""
        allowed_suffixes = {".md", ".txt", ".json", ".yaml", ".yml"}
        parts: list[str] = []
        remaining = max_chars
        for path in sorted(item for item in references_dir.rglob("*") if item.is_file()):
            if path.suffix.lower() not in allowed_suffixes:
                continue
            rel_path = path.relative_to(self.skill.skill_dir).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > remaining:
                text = text[:remaining] + "\n\n[truncated by runtime reference_prompt_max_chars]\n"
            parts.append(f"## {rel_path}\n\n{text}")
            remaining -= len(text)
            if remaining <= 0:
                break
        if not parts:
            return ""
        return "# Bundled Skill References\n" + "\n\n".join(parts) + "\n\n"

    def system_prompt(self, ctx: ExecutionContext) -> str:
        body = self.skill.body
        if self.use_jinja:
            # 少数 skill 会显式声明 use-jinja，此时才对正文做模板渲染。
            body = Template(body, undefined=StrictUndefined).render(
                project_id=ctx.project_id,
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                workspace_dir=str(ctx.workspace_dir),
                inputs={k: str(v) for k, v in ctx.inputs.items()},
                outputs_expected={k: str(v) for k, v in ctx.outputs_expected.items()},
                mode=ctx.mode,
                extra=ctx.extra,
            )
        warning_block = ""
        if self.translation_warnings:
            warning_block = "\n".join(f"- {warning}" for warning in self.translation_warnings)
            warning_block = f"## Skill Translation Warnings\n{warning_block}\n\n"
        header = (
            "# Runtime Context\n"
            f"- workspace_dir: {ctx.workspace_dir}\n"
            f"- skill_dir: {self.skill.skill_dir}\n"
            f"- task_id: {ctx.task_id}\n"
            "- Edit is mapped to Write; provide full file content when editing.\n\n"
        )
        readable = ", ".join(self.spec.allowed_read_prefixes) or "(none)"
        writable = ", ".join(self.spec.allowed_write_prefixes) or "(none)"
        header += (
            "# Workspace Capability Boundary\n"
            f"- Read only these declared workspace areas: {readable}\n"
            f"- Write only these declared workspace areas: {writable}\n"
            f"- Enabled capability profiles: {', '.join(self.skill.capability_profiles) or 'workspace_navigation'}\n"
            f"- Available tools for this session: {', '.join(self.spec.tool_names)}\n"
            "- The Skill body and bundled reference files are already loaded into this prompt. "
            "The `read_file` tool is workspace-scoped; do not call it on `skill_dir`, repository `skills/...`, "
            "`references/...`, or `scripts/...` paths unless those paths are explicit workspace inputs. "
            "Use the embedded Skill/reference text instead.\n"
            "- Start with the verified inputs listed below. Do not probe unrelated workspace paths just because they are conventional names. "
            "When a needed material is absent, request it through the guided follow-up protocol instead of attempting an unauthorized read.\n\n"
        )
        if self.tool_call_budget.per_tool or self.tool_call_budget.groups:
            budget_lines = []
            for name, limit in self.tool_call_budget.per_tool.items():
                budget_lines.append(f"- `{name}`: at most {limit} call(s)")
            for group in self.tool_call_budget.groups:
                budget_lines.append(
                    f"- `{group.label}`: at most {group.max_calls} call(s) shared by "
                    + ", ".join(f"`{name}`" for name in group.tools)
                )
            header += (
                "# Runtime Tool Budget\n"
                + "\n".join(budget_lines)
                + "\n- When a tool reports `SKILL_TOOL_BUDGET_REACHED`, do not retry any tool in that budget. "
                "Use already returned source data, write the required outputs honestly (an empty retained-record list is allowed), and call finish_task.\n\n"
            )
        if self.tool_call_budget.stop_remote_on_rate_limit:
            header += (
                "# Remote Retrieval Failure Boundary\n"
                "- If any declared remote tool reports a rate limit, do not call another remote retrieval tool in this run. "
                "The runtime will enforce this boundary. Preserve the successful source records, record the unavailable sources, "
                "write the partial declared outputs, and call finish_task.\n\n"
            )
        resume_validation_error = str(ctx.extra.get("skill_resume_validation_error") or "").strip()
        if resume_validation_error:
            header += (
                "# Resumed Output Repair\n"
                "- The existing declared outputs failed their current validation before this resumed run: "
                f"{resume_validation_error}\n"
                "- This is a focused repair, not a fresh discovery pass. Read the named existing output first, preserve valid source-backed material, "
                "repair the smallest affected content, then call finish_task so the full current validation reruns.\n"
                "- Do not call remote retrieval merely to repair an output-contract or consistency error.\n\n"
            )
        session_path = ctx.extra.get("skill_session_path")
        selected_inputs = ctx.extra.get("skill_selected_inputs")
        if session_path:
            header += (
                "# Guided Skill Session\n"
                f"- session state: {session_path}\n"
                "- The runtime checked the declared required inputs before this LLM turn.\n"
                "- Read the session state when prior-turn decisions or input provenance matter.\n"
            )
            if selected_inputs:
                header += "- verified inputs:\n" + "\n".join(
                    f"  - {key}: {value}" for key, value in selected_inputs.items()
                ) + "\n"
            header += "\n"
        workspace_mode = str(ctx.extra.get("skill_workspace_mode") or "standalone")
        intake_packet = str(ctx.extra.get("skill_intake_packet_path") or "").strip()
        if intake_packet:
            header += (
                "# Material Intake Protocol\n"
                f"- workspace mode: {workspace_mode}\n"
                f"- deterministic intake packet: {intake_packet}\n"
                "- Read the intake packet and the selected inputs before substantive work. Existing project files are candidates, not proof that their claims are sufficient.\n"
                "- If a source, result, citation, venue decision, or constraint is semantically missing, write "
                f"`user_inputs/{self.skill.name}/_followup_request.md` with the exact gap, why it matters, and a preferred answer/file path. Then call ask_human and wait for the response.\n"
                "- Do not create final deliverables by guessing missing material. Record the resolved answer in the follow-up file before continuing.\n\n"
            )
        header += (
            "# Experimental-Detail Integrity\n"
            "- A concrete dataset, benchmark, split, baseline, metric, seed, compute budget, implementation command, or performance number may be used only when an allowed input or audited workspace artifact explicitly identifies it. Record the source path and section/field whenever that detail affects a plan or claim.\n"
            "- This is a provenance rule, not a metric ban: AUUC, Qini, accuracy, F1, and any other metric are valid when the current project's allowed inputs or audited artifacts explicitly declare them.\n"
            "- If the user asks for a plan but the detail is not yet sourced, describe it to the user as “待验证提议” or “暂未确定”; reserve raw values such as `proposed_not_verified` and `unknown` for structured files. State what material would resolve it. Do not turn a plausible convention into an existing protocol.\n"
            "- Never infer experimental details from the project topic, a method name, an adjacent paper, a generic benchmark convention, or an earlier example. Missing protocol inputs require a focused human question, not a fabricated default.\n\n"
        )
        if ctx.outputs_expected:
            output_lines = "\n".join(
                f"  - {name}: {path.relative_to(ctx.workspace_dir)}"
                for name, path in ctx.outputs_expected.items()
            )
            header += (
                "# Required Output Contract\n"
                "- Before calling finish_task, write every required deliverable at exactly these paths:\n"
                f"{output_lines}\n"
                "- Do not substitute generic, legacy, or similarly named files for these paths. "
                "An auxiliary log is allowed only after the required deliverables exist and must not replace them.\n"
                "- If evidence is partial or a remote source fails, preserve the successful evidence and state the limitation inside the declared outputs; do not defer their creation while searching for an ideal result.\n\n"
            )
        header += (
            "# 面向用户的沟通规则\n"
            "- 默认使用清楚、自然的中文。先说明已经检查了什么、当前能做什么、还需要什么，再给出下一步。\n"
            "- 不把 `schema`、`artifact`、`stage`、`section`、内部 Agent 名称或工具限制直接当作解释；只有文件路径、证据边界或用户需要采取的动作确有必要时才提及。\n"
            "- 用“材料准备”而不是 intake，用“论文阅读笔记”而不是 evidence card/note card，用“论文中的相关位置或段落”而不是 section anchor，用“输出文件”而不是 artifact。专业的学术术语（例如 taxonomy、baseline、ablation、claim、Related Work）可以保留英文。\n"
            "- 首次处理前先读材料清单和已验证输入。若材料不完整，准确指出缺少的内容以及可上传、粘贴或提供的标识符，不要泛泛地说“材料不足”。\n"
            "- 保留完整用户可读信息，不使用 `...` 截断字段，不用连续空行制造视觉间隔；长文本由终端按当前宽度自然换行。\n\n"
            "# Interaction And Output Style\n"
            "- Produce a compact, readable research interaction: state the decision, the evidence boundary, and the next action. Use a short paragraph, a table, or a flat list when it improves scanning; do not simulate a human-input panel in prose.\n"
            "- Ask a real question only through ask_human. If that tool is unavailable, persist the named blocker and finish or pause according to the Skill contract; never emit a faux question that the runtime must guess how to handle.\n"
            "- Keep Markdown structurally clean. Use one blank line between real blocks, no repeated empty spacer lines, no decorative separator walls, no character-level truncation, and no copy-pasted feature narration.\n"
            "- For candidate comparisons, use concise tables or labeled sections with complete values. Preserve source paths, uncertainty, and next actions; do not hide material text behind ellipses.\n\n"
        )
        workflow_block = workflow_prompt_block(self.workflow) if self.workflow else ""
        reference_block = self._bundled_reference_block()
        return header + workflow_block + warning_block + reference_block + body

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        # CLI run-skill 时，用户请求会放在 ctx.extra["user_request"]。
        resume_validation_error = str(ctx.extra.get("skill_resume_validation_error") or "").strip()
        if resume_validation_error:
            return (
                "这是一次已存在输出的定向恢复修复。先读取当前声明的输出文件，"
                "只修复以下校验问题，不要重新开始检索或重做无关工作："
                + resume_validation_error
            )
        user_request = ctx.extra.get("user_request")
        if user_request:
            return str(user_request)
        return f"Execute the '{self.skill.name}' skill per your instructions."

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """Require a durable evidence-aware manifest from integrated Skills."""

        ok, error = super().validate_outputs(ctx)
        if not ok:
            return ok, error
        semantic_error = _validate_skill_semantic_outputs(self.skill.name, ctx)
        if semantic_error:
            return False, semantic_error
        if self.workflow is None:
            return True, None
        interaction = self.skill.metadata.get("interaction")
        outputs = interaction.get("outputs") if isinstance(interaction, dict) else []
        manifest_path = ""
        for output in outputs if isinstance(outputs, list) else []:
            if not isinstance(output, dict):
                continue
            output_id = str(output.get("id") or "")
            path = str(output.get("path") or "")
            if output_id == "workflow_manifest" or path.endswith("_manifest.json"):
                manifest_path = path
                break
        if not manifest_path:
            return False, "integrated Skill must declare a JSON workflow_manifest output"
        path = ctx.workspace_dir / manifest_path
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"workflow manifest is not readable JSON: {manifest_path}: {exc}"
        if not isinstance(manifest, dict):
            return False, f"workflow manifest must be a JSON object: {manifest_path}"
        phases = manifest.get("phases")
        if not isinstance(phases, list):
            return False, f"workflow manifest must include a phases list: {manifest_path}"
        by_id = {
            str(item.get("id")): item
            for item in phases
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        missing = [phase.phase_id for phase in self.workflow.phases if phase.phase_id not in by_id]
        if missing:
            return False, "workflow manifest is missing declared phases: " + ", ".join(missing)
        unresolved: list[str] = []
        for phase in self.workflow.phases:
            item = by_id[phase.phase_id]
            status = str(item.get("status") or "").strip()
            if status not in {"completed", "skipped"}:
                unresolved.append(f"{phase.phase_id}={status or 'missing'}")
                continue
            if not str(item.get("summary") or "").strip():
                unresolved.append(f"{phase.phase_id}=missing_summary")
            if "evidence_boundary" not in item:
                unresolved.append(f"{phase.phase_id}=missing_evidence_boundary")
        if unresolved:
            return False, "workflow manifest has unresolved phases: " + ", ".join(unresolved)
        return True, None


_STABLE_IDENTIFIER_KINDS = frozenset({"doi", "arxiv", "openalex", "semantic_scholar", "url"})
_UNRESOLVED_IDENTIFIER_RE = re.compile(
    r"(?i)\b(?:needs?\s+(?:manual\s+)?(?:resolution|lookup)|unresolved|missing\s+(?:doi|arxiv|identifier)|not\s+resolved|requires?\s+(?:manual\s+)?resolution)\b"
)


def _validate_skill_semantic_outputs(skill_name: str, ctx: ExecutionContext) -> str | None:
    """Apply narrow deterministic contracts where filename checks are unsafe.

    The evidence scout promises source-identifiable literature evidence. A
    JSON file of title-only leads therefore cannot be accepted merely because
    it exists; this compact validator prevents that false completion without
    imposing unrelated schemas on other standalone Skills.
    """

    if skill_name != "literature-evidence-scout":
        return None
    records_path = ctx.workspace_dir / "literature" / "skill_evidence_records.json"
    try:
        records = json.loads(records_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"literature-evidence-scout records must be readable JSON: {exc}"
    if not isinstance(records, list):
        return "literature-evidence-scout records must be a JSON list"
    if len(records) > 20:
        return "literature-evidence-scout records exceed the declared 20-record retrieval boundary"
    report_path = ctx.workspace_dir / "literature" / "skill_evidence_scout.md"
    try:
        report = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"literature-evidence-scout report must be readable text: {exc}"
    count_match = re.search(r"(?im)^\s*-\s*`?retained_record_count`?\s*[:：]\s*(\d+)\s*$", report)
    if count_match is None:
        return (
            "literature-evidence-scout report must state '- retained_record_count: N' "
            "in its retrieval delivery status"
        )
    if int(count_match.group(1)) != len(records):
        return (
            "literature-evidence-scout report retained_record_count does not match "
            "literature/skill_evidence_records.json"
        )
    reported_counts: list[int] = []
    for pattern in (
        r"(?im)^\|\s*Retained with stable identifiers\s*\|\s*(\d+)\b",
        r"(?i)\b(\d+)\s+papers retained with verified arXiv IDs\b",
        r"(?i)\bAll\s+(\d+)\s+papers have confirmed arXiv IDs\b",
    ):
        reported_counts.extend(int(match.group(1)) for match in re.finditer(pattern, report))
    inconsistent = sorted({value for value in reported_counts if value != len(records)})
    if inconsistent:
        return (
            "literature-evidence-scout report has retained-record counts that conflict with "
            "literature/skill_evidence_records.json: "
            + ", ".join(str(value) for value in inconsistent)
        )
    errors: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"records[{index}] is not an object")
            continue
        if not str(record.get("title") or "").strip():
            errors.append(f"records[{index}] has no title")
        stable = record.get("stable_identifier")
        if not isinstance(stable, dict):
            errors.append(f"records[{index}] lacks stable_identifier")
            continue
        kind = str(stable.get("kind") or "").strip().casefold()
        value = str(stable.get("value") or "").strip()
        source = str(stable.get("source") or "").strip()
        if kind not in _STABLE_IDENTIFIER_KINDS or not value or not source:
            errors.append(f"records[{index}] has an invalid stable_identifier")
            continue
        if kind == "doi" and not re.search(r"10\.\d{4,9}/\S+", value, flags=re.IGNORECASE):
            errors.append(f"records[{index}] has an invalid DOI identifier")
        elif kind == "arxiv" and not re.fullmatch(r"(?:arXiv:)?\d{4}\.\d{4,5}(?:v\d+)?", value, flags=re.IGNORECASE):
            errors.append(f"records[{index}] has an invalid arXiv identifier")
        elif kind == "openalex" and not re.fullmatch(r"W\d+", value, flags=re.IGNORECASE):
            errors.append(f"records[{index}] has an invalid OpenAlex identifier")
        elif kind == "semantic_scholar" and not re.fullmatch(
            r"(?:[0-9a-f]{40}|CorpusId:\d+)", value, flags=re.IGNORECASE
        ):
            errors.append(f"records[{index}] has an invalid Semantic Scholar identifier")
        elif kind == "url" and not _is_canonical_paper_url(value):
            errors.append(f"records[{index}] has a non-canonical paper URL")
        status = str(record.get("identifier_status") or "")
        if _UNRESOLVED_IDENTIFIER_RE.search(status):
            errors.append(f"records[{index}] retains an unresolved identifier status")
    if errors:
        return "literature-evidence-scout identifier contract failed: " + "; ".join(errors[:8])
    return None


def _is_canonical_paper_url(value: str) -> bool:
    """Accept only a stable paper landing page, never a search result URL."""

    normalized = value.strip()
    patterns = (
        r"https?://(?:dx\.)?doi\.org/10\.\d{4,9}/\S+$",
        r"https?://arxiv\.org/(?:abs|pdf)/\d{4}\.\d{4,5}(?:v\d+)?(?:\.pdf)?$",
        r"https?://openalex\.org/W\d+$",
        r"https?://(?:www\.)?semanticscholar\.org/paper/[A-Za-z0-9-]+$",
    )
    return any(re.fullmatch(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)
