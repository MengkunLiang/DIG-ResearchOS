from __future__ import annotations

"""单 task 调试运行器。"""

import shutil
import uuid
from pathlib import Path

import yaml

from ..agents.registry import TASK_TO_AGENT_MAP
from ..orchestration.task_io_contract import get_task_io, resolve_inputs, resolve_outputs
from ..runtime.agent import ExecutionContext, LLMConfigOverride
from ..runtime.llm_client import LLMClient
from ..runtime.logger import get_logger
from ..runtime.orchestrator import AgentRunner
from ..schemas.validator import register_builtin_task_checkers, validate_prerequisites, validate_task_artifacts
from ..tools.human_gate import CLIHumanInterface
from ..tools.registry import ToolRegistry


_LOG = get_logger("single_task")


class SingleTaskRunner:
    """模式 B：只运行一个 task，不推进到下一个 task。"""

    def __init__(
        self,
        *,
        workspace: Path,
        task_id: str,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        from_workspace: Path | None = None,
        override_profile: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.task_id = task_id
        self.llm = llm_client
        self.tools = tool_registry
        self.from_workspace = from_workspace
        self.override_profile = override_profile
        register_builtin_task_checkers()

    async def run(self) -> int:
        """执行单次 task 调试。"""
        # runner 作为独立 Python API 使用时，也要自己保证 runtime 目录存在，
        # 不能把这个前提完全交给 CLI 调用方。
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "_runtime" / "traces").mkdir(parents=True, exist_ok=True)
        (self.workspace / "_runtime" / "logs").mkdir(parents=True, exist_ok=True)

        if self.from_workspace:
            self._copy_prerequisites()

        ok, err = validate_prerequisites(self.workspace, self.task_id)
        if not ok:
            print(f"Prerequisites not met for {self.task_id}: {err}")
            print("Hint: use --from <other-workspace> to copy upstream artifacts.")
            return 3

        agent_cls = TASK_TO_AGENT_MAP.get(self.task_id)
        if agent_cls is None:
            print(f"Unknown or unimplemented task: {self.task_id}")
            return 4
        agent = agent_cls()

        ctx = ExecutionContext(
            workspace_dir=self.workspace.resolve(),
            project_id=self._load_or_fake_project_id(),
            task_id=self.task_id,
            run_id=f"{self.task_id}_single_{uuid.uuid4().hex[:8]}",
            inputs=resolve_inputs(self.workspace, self.task_id),
            outputs_expected=resolve_outputs(self.workspace, self.task_id),
        )
        if self.override_profile:
            ctx.llm_override = LLMConfigOverride(profile=self.override_profile)

        human = CLIHumanInterface()
        runner = AgentRunner(agent, self.tools, self.llm, human)
        result = await runner.run(ctx)

        io_spec = get_task_io(self.task_id)
        ok, errors = validate_task_artifacts(
            self.workspace,
            self.task_id,
            declared_outputs=io_spec["outputs"],
        )
        if result.ok and not ok:
            result.ok = False
            result.stop_reason = result.STOP_ERROR
            result.error = "Runtime artifact validation failed: " + "; ".join(errors)
            result.message = result.error

        self._print_result(result)
        return 0 if result.ok else 5

    def _copy_prerequisites(self) -> None:
        """从另一个 workspace 复制输入 artifact。"""
        io_spec = get_task_io(self.task_id)
        for rel_path in io_spec["inputs"].values():
            src = self.from_workspace / rel_path
            dst = self.workspace / rel_path
            if not src.exists() or dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"copied: {rel_path}")

    def _load_or_fake_project_id(self) -> str:
        """尽量从 project.yaml 读 project_id，没有则生成一个临时值。"""
        project_path = self.workspace / "project.yaml"
        if project_path.exists():
            try:
                data = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
                return data.get("project_id", f"single-task-{self.task_id}")
            except Exception:
                pass
        return f"single-task-{self.task_id}"

    @staticmethod
    def _print_result(result) -> None:
        print("=" * 60)
        print(f"stop_reason: {result.stop_reason}")
        print(f"steps: {result.steps_used}")
        print(f"tokens: {result.tokens_in} in / {result.tokens_out} out")
        print(f"cost: ${result.cost_usd:.4f}")
        print(f"duration: {result.duration_seconds:.1f}s")
        print(f"outputs: {list(result.outputs_produced.keys())}")
        if result.error:
            print(f"error: {result.error}")
        print("=" * 60)
