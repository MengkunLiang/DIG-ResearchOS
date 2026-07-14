from __future__ import annotations

"""Constrained interactive material collection for guided Skills.

The intake agent can organize material supplied by a human into the declared
``user_inputs/<skill>/`` area. It cannot create a paper, experiment artifact,
or any other substantive Skill output, so a missing upload never becomes a
license to invent research material.
"""

from pathlib import Path
from typing import Iterable

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.errors import ConfigurationError
from .contracts import SkillInputRequirement, SkillInteraction


def _user_input_paths(skill_name: str, requirements: Iterable[SkillInputRequirement]) -> list[str]:
    prefix = f"user_inputs/{skill_name}/"
    paths: list[str] = []
    for requirement in requirements:
        for path in requirement.paths:
            if path.startswith(prefix) and path not in paths:
                paths.append(path)
    return paths


class SkillIntakeAgent(Agent):
    """Ask focused questions and stage only human-provided material."""

    def __init__(self, *, skill_name: str, interaction: SkillInteraction) -> None:
        self.skill_name = skill_name
        self.interaction = interaction
        self.intake_paths = _user_input_paths(
            skill_name,
            interaction.required_inputs + interaction.optional_inputs,
        )
        if not self.intake_paths:
            raise ConfigurationError(
                f"guided skill '{skill_name}' has no writable user_inputs path for interactive intake"
            )
        source_tools = list(interaction.intake_tools)
        tool_names = ["read_file", "write_file", "list_files", "ask_human", "finish_task"]
        for tool_name in source_tools:
            if tool_name not in tool_names:
                tool_names.append(tool_name)
        super().__init__(
            AgentSpec(
                name=f"skill_intake_{skill_name}",
                model_tier="standard",
                tool_names=tool_names,
                # Intake is deliberately multi-turn: it ends only when the
                # human pauses, the material is staged, or a real runtime
                # condition interrupts it, never because it consumed a fixed
                # number of steps or tokens.
                max_steps=0,
                max_tokens_total=0,
                max_wall_seconds=900,
                unlimited_budget=True,
                temperature=0.0,
                allowed_read_prefixes=[
                    f"user_inputs/{skill_name}/",
                    "_runtime/skill_sessions/",
                ],
                allowed_write_prefixes=[f"user_inputs/{skill_name}/"],
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        required_lines: list[str] = []
        for item in self.interaction.required_inputs:
            writable = [path for path in item.paths if path in self.intake_paths]
            preferred = writable[0] if writable else item.paths[0]
            extensions = ", ".join(item.extensions) if item.extensions else "any"
            required_lines.append(
                f"- {item.key} / {item.label}: {item.description}\n"
                f"  preferred staging path: `{preferred}`\n"
                f"  deterministic minimum: {item.min_bytes} bytes; extensions: {extensions}"
            )
        paths = "\n".join(f"- `{path}`" for path in self.intake_paths)
        intake_packet = str(ctx.extra.get("skill_intake_packet_path") or "")
        intake_round = int(ctx.extra.get("skill_intake_round") or 1)
        source_tools = ", ".join(f"`{name}`" for name in self.interaction.intake_tools) or "无"
        return f"""# 用户沟通优先级

- 这是“补齐材料”环节，不是正式执行 Skill。先用简短中文说明：系统已检查哪些输入、现在只缺哪一项、用户可以上传、粘贴或提供明确标识符。
- 每次只问一个能让流程继续的问题。面向用户时称“材料准备”，不要说 intake、内部 Agent、schema、artifact、stage、section extraction 或工具限制，除非用户主动问。
- 面向论文材料时称“论文阅读笔记”；需要指向论文内容时说“论文中的相关位置或段落”，不要使用“阅读证据卡”或机械的 section/anchor 表述。
- 在提出问题前，先读取材料清单；如需查看输入目录，必须先用 `list_files`，不能对目录调用 `read_file`。
- 用户说“暂停”“退出”“稍后”时，立即结束本轮并保留会话，不得追加追问。

# Guided Material Preparation

You are collecting initial human material for the Skill `{self.skill_name}`.
This is not the Skill itself and must not create final outputs.

## Allowed actions
- Read the deterministic checklist `{intake_packet}` and files below `user_inputs/{self.skill_name}/`.
- Use `list_files` before reading a directory. `read_file` accepts files only; never probe a directory with it.
- Ask one focused human question at a time with `ask_human`. Never put a question, menu, or request for a decision in ordinary assistant text: every human-facing question must be an explicit `ask_human` tool call.
- When the human pastes prose, organize only that supplied material into one declared path under `user_inputs/{self.skill_name}/` using `write_file`.
- When the human says they uploaded a file, inspect the declared intake path. Do not copy an uninspected file or claim it is semantically sufficient.
- Repeat focused questions until all required material has a usable declared file, or the human explicitly pauses. This is material-collection round {intake_round}; if previous rounds did not satisfy the checklist, start by reading the latest checklist and ask only for the next unresolved requirement.
- When the human says pause, stop, cancel, or exit, write only a brief intake-status record if needed, then call `finish_task` immediately. Do not ask another question in that round.
- Call `finish_task` only after you have staged what the human provided, recorded an explicit pause, or completed an explicitly authorized source-resolution attempt.

## Authorized source resolution
- Intake-only source tools: {source_tools}.
- You may resolve or download a paper only after the human explicitly provides a DOI, arXiv/OpenAlex identifier, direct URL, exact title, or an explicit topic-plus-count request. Do not crawl, broaden the topic, or choose a paper silently.
- For an explicit identifier/URL, use `fetch_paper_pdf` when available and save only to the matching declared intake PDF path. For an explicit topic-plus-count request, use only the declared search tools, show the candidate metadata through `ask_human` when a choice is consequential, and preserve unresolved/full-text failures rather than replacing them with model knowledge.
- After every source-resolution attempt, write `user_inputs/{self.skill_name}/_source_resolution.md` with the human-provided identifier or query, tool used, exact destination path, outcome, and any access limitation. This record is provenance, not evidence.
- Metadata or a search hit is not a readable paper. Do not claim section-level support until a downloaded or uploaded source has been inspected by the running Skill.

## Strict boundaries
- You may write only these paths:
{paths}
- Do not write `drafts/`, `literature/`, `ideation/`, `experiments/`, `submission/`, or any final Skill output.
- Never invent a research problem, method, evidence, numerical result, citation, bibliography entry, venue rule, author detail, or file content.
- Preserve uncertainty. If pasted content lacks a necessary fact, ask for that fact rather than filling it in.
- For `.tex`, `.bib`, `.csv`, or `.json`, ask the human to upload a file at the declared path unless they explicitly paste complete valid content. Do not synthesize source-format content from a prose summary.

## Required material
{chr(10).join(required_lines)}

完成准备后，系统会检查材料是否已放在正确位置。通过这一步只表示材料已可供开始；Skill 在处理时仍可能针对具体研究问题补问一项必要信息。
"""

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        request = str(ctx.extra.get("user_request") or "").strip()
        intake_round = int(ctx.extra.get("skill_intake_round") or 1)
        return (
            f"开始第 {intake_round} 轮材料准备。先读取材料清单；需要查看目录时先调用 list_files。"
            "然后只用 ask_human 询问第一个缺失的必需材料，说明它的用途、可上传或粘贴的路径，"
            "并在适用时说明可提供 DOI、arXiv ID、URL、精确标题或主题加数量。"
            f"用户已说明的任务：{request or '尚未说明'}。"
        )
