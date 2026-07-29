"""Researcher-facing routes for Skills that are not direct CLI sessions.

Pipeline-owned Skills and external-executor templates need different entry
points from ordinary ``run-skill`` sessions.  Keeping their route resolution
in one small, read-only module prevents the command line, descriptions, and
suite audit from drifting into contradictory instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex

from ..schemas.state import StateYaml


@dataclass(frozen=True)
class ManagedSkillRoute:
    """A safe next action for a non-standalone Skill package."""

    scope: str
    owner: str
    summary: str
    next_action: str
    command: str | None = None

    def render(self) -> str:
        """Return compact copy suitable for CLI and Rich presentation."""

        parts = [self.summary, self.next_action]
        if self.command:
            parts.append(f"建议命令：{self.command}")
        return "\n".join(parts)


def managed_skill_route(
    *,
    skill_name: str,
    execution_scope: str,
    execution_owner: str,
    workspace: Path,
) -> ManagedSkillRoute:
    """Resolve a read-only, scope-preserving route for one managed Skill.

    The function never changes ``state.yaml`` and deliberately never proposes
    ``resume --from-task <owner>``.  Jumping directly to a T5 stage could skip
    the verified upstream artifacts that give a handoff or executor template
    its authority.
    """

    owner = execution_owner or "其声明的运行时所有者"
    workspace_text = shlex.quote(str(workspace))
    state = _load_workspace_state(workspace)

    if execution_scope == "state_machine":
        if state is None:
            return ManagedSkillRoute(
                scope=execution_scope,
                owner=owner,
                summary=(
                    f"`{skill_name}` 不是独立 Skill；它只由工作流阶段 `{owner}` "
                    "在具备上游研究材料时调用。"
                ),
                next_action="当前目录没有可恢复的 pipeline 状态。请先启动完整研究流程，系统会在正确阶段自动调用它。",
                command=f"python -m researchos.cli run --workspace {workspace_text}",
            )
        current = str(state.current_task or "未知步骤")
        status = str(state.status or "未知状态")
        relation = (
            "当前正处于该 Skill 的所有者阶段；恢复后会继续其可恢复工作。"
            if current == owner
            else f"当前工作流位于 `{current}`（{status}）；请按既有顺序恢复，不要跳过上游步骤强行运行 `{owner}`。"
        )
        return ManagedSkillRoute(
            scope=execution_scope,
            owner=owner,
            summary=f"`{skill_name}` 由工作流阶段 `{owner}` 管理，不能通过 `run-skill` 直接启动。",
            next_action=relation,
            command=f"python -m researchos.cli resume --workspace {workspace_text}",
        )

    if execution_scope == "executor_template":
        state_note = ""
        if state is not None:
            state_note = (
                f"当前工作流位于 `{state.current_task}`（{state.status}）。"
            )
        return ManagedSkillRoute(
            scope=execution_scope,
            owner=owner,
            summary=(
                f"`{skill_name}` 是 `{owner}` 发布后的外部执行器模板，"
                "不是 ResearchOS CLI 的独立会话。"
            ),
            next_action=(
                "先让 T5 完成 handoff 和项目专属化；之后由选定的外部执行器在 "
                "`external_executor/skills/` 中按其控制文件调用。"
                + (f" {state_note}" if state_note else "")
            ),
            command=f"python -m researchos.cli resume --workspace {workspace_text}",
        )

    return ManagedSkillRoute(
        scope=execution_scope,
        owner=owner,
        summary=f"`{skill_name}` 是内部实现模块，不构成用户可启动的 ResearchOS Skill。",
        next_action="请使用公开的独立 Skill 或对应的工作流入口；不要尝试通过 `run-skill` 调用该模块。",
        command=None,
    )


def _load_workspace_state(workspace: Path) -> StateYaml | None:
    """Read a valid workspace state when one exists, without surfacing noise."""

    state_path = workspace / "state.yaml"
    if not state_path.is_file():
        return None
    try:
        return StateYaml.load_yaml(state_path)
    except Exception:  # An invalid state must not become a false routing fact.
        return None
