from __future__ import annotations

import json
from pathlib import Path
import textwrap

import yaml

from researchos.cli_runners.single_task import SingleTaskRunner
from researchos.runtime.config import LoggingSettings, RuntimeSettings, WorkspaceSettings, load_runtime_settings
from researchos.schemas import validator
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def _hello_llm() -> MockLLMClient:
    return MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={"path": "hello.txt", "content": "Hello, Runtime!"},
                            id="tc_write",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="finish_task",
                            arguments={"summary": "hello finished"},
                            id="tc_finish",
                        )
                    ]
                )
            ),
        ]
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def test_load_runtime_settings_reads_shared_runtime_options(tmp_path: Path):
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            workspace:
              default_root: "./shared-workspace"
              runtime_dir: ".runtime"
            logging:
              level: "DEBUG"
              json: false
            human_interface:
              backend: "cli"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(config_path)

    assert settings.workspace.default_root == "./shared-workspace"
    assert settings.workspace.runtime_dir == ".runtime"
    assert settings.logging == LoggingSettings(level="DEBUG", json=False)


async def test_single_task_runner_respects_custom_runtime_dir(tmp_workspace: Path):
    settings = RuntimeSettings(
        workspace=WorkspaceSettings(default_root="./workspace", runtime_dir=".runtime"),
    )
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="HELLO",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
        runtime_settings=settings,
    )

    exit_code = await runner.run()

    assert exit_code == 0
    trace_dir = tmp_workspace / ".runtime" / "traces"
    assert trace_dir.exists()
    assert any(trace_dir.glob("*.jsonl"))


def test_validate_t2_artifacts_with_builtin_checker(tmp_path: Path):
    validator.register_builtin_task_checkers()
    workspace = tmp_path / "workspace"
    (workspace / "literature").mkdir(parents=True)
    paper = {
        "id": "paper-1",
        "source": "semantic_scholar",
        "title": "A Runtime Paper",
        "authors": [{"name": "Ada"}],
        "year": 2025,
        "abstract": "demo",
        "venue": "Conf",
        "citationCount": 1,
        "externalIds": {},
        "url": "https://example.com/paper-1",
    }
    for file_name in ("papers_raw.jsonl", "papers_dedup.jsonl"):
        (workspace / "literature" / file_name).write_text(
            json.dumps(paper, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    (workspace / "literature" / "search_log.md").write_text("# Search Log\n", encoding="utf-8")
    (workspace / "literature" / "missing_areas.md").write_text("- none\n", encoding="utf-8")

    ok, errors = validator.validate_task_artifacts(workspace, "T2")

    assert ok
    assert errors == []


def test_validate_t4_artifacts_reports_bad_hypothesis_ref(tmp_path: Path):
    validator.register_builtin_task_checkers()
    workspace = tmp_path / "workspace"
    (workspace / "ideation").mkdir(parents=True)
    (workspace / "ideation" / "hypotheses.md").write_text(
        "# H1 First Hypothesis\n\n" + ("x" * 600),
        encoding="utf-8",
    )
    (workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.safe_dump(
            {
                "experiments": [
                    {
                        "id": "exp-1",
                        "hypothesis_ref": "H2",
                    }
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "risks.md").write_text("risk\n", encoding="utf-8")

    ok, errors = validator.validate_task_artifacts(workspace, "T4")

    assert not ok
    assert any("hypothesis_ref" in item for item in errors)


def test_validate_t7_artifacts_happy_path(tmp_path: Path):
    validator.register_builtin_task_checkers()
    workspace = tmp_path / "workspace"
    (workspace / "experiments" / "runs" / "run_001").mkdir(parents=True)
    (workspace / "experiments" / "configs").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        yaml.safe_dump(
            {"project_id": "demo", "compute_budget": {"max_gpu_hours": 10}},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (workspace / "experiments" / "results_summary.json").write_text(
        json.dumps({"summary": "ok", "total_gpu_hours": 2.5}, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace / "experiments" / "runs" / "run_001" / "record.json").write_text(
        json.dumps({"run_id": "run_001", "status": "done"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace / "experiments" / "iteration_log.md").write_text("iter-1\n", encoding="utf-8")
    (workspace / "experiments" / "ablations.csv").write_text("name,value\nbase,1\n", encoding="utf-8")

    ok, errors = validator.validate_task_artifacts(workspace, "T7")

    assert ok
    assert errors == []
