from __future__ import annotations

"""每个 task 的输入/输出 artifact 契约。

说明：
- 该文件主要服务于 single-task 调试模式、前置输入校验和 artifact 拷贝；
- 契约以 Agent Dev Spec 的模式 B I/O 表为主，同时遵守当前 runtime 使用 `state.yaml`
  的实现现实；
- HELLO 不是正式 T-stage，而是当前仓库已经跑通的最小 runtime 调试任务，因此也保留一份
  专用契约方便测试与 smoke run。
"""

from pathlib import Path


TASK_IO_CONTRACTS: dict[str, dict[str, object]] = {
    "HELLO": {
        "inputs": {},
        "outputs": {"hello_file": "hello.txt"},
        "required_inputs": [],
    },
    "T1": {
        "inputs": {},
        "outputs": {
            "project": "project.yaml",
            "state": "state.yaml",
            "seed_papers": "user_seeds/seed_papers.jsonl",
            "seed_ideas": "user_seeds/seed_ideas.md",
            "seed_constraints": "user_seeds/seed_constraints.md",
        },
        "required_inputs": [],
    },
    "T2": {
        "inputs": {
            "project": "project.yaml",
            "seed_papers": "user_seeds/seed_papers.jsonl",
            "seed_constraints": "user_seeds/seed_constraints.md",
            "seed_ideas": "user_seeds/seed_ideas.md",
        },
        "outputs": {
            "papers_raw": "literature/papers_raw.jsonl",
            "papers_dedup": "literature/papers_dedup.jsonl",
            "search_log": "literature/search_log.md",
            "missing_areas": "literature/missing_areas.md",
        },
        "required_inputs": ["project"],
    },
    "T3": {
        "inputs": {
            "project": "project.yaml",
            "papers_dedup": "literature/papers_dedup.jsonl",
            "missing_areas": "literature/missing_areas.md",
        },
        "outputs": {
            "paper_notes_dir": "literature/paper_notes",
            "comparison_table": "literature/comparison_table.csv",
            "related_work_bib": "literature/related_work.bib",
        },
        "required_inputs": ["project", "papers_dedup"],
    },
    "T3.5": {
        "inputs": {
            "project": "project.yaml",
            "paper_notes_dir": "literature/paper_notes",
            "comparison_table": "literature/comparison_table.csv",
            "missing_areas": "literature/missing_areas.md",
        },
        "outputs": {
            "synthesis": "literature/synthesis.md",
        },
        "required_inputs": ["project", "paper_notes_dir", "comparison_table"],
    },
    "T4": {
        "inputs": {
            "project": "project.yaml",
            "synthesis": "literature/synthesis.md",
            "comparison_table": "literature/comparison_table.csv",
            "missing_areas": "literature/missing_areas.md",
            "seed_ideas": "user_seeds/seed_ideas.md",
            "seed_constraints": "user_seeds/seed_constraints.md",
        },
        "outputs": {
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "risks": "ideation/risks.md",
        },
        "required_inputs": ["project", "synthesis"],
    },
    "T5": {
        "inputs": {
            "project": "project.yaml",
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "risks": "ideation/risks.md",
        },
        "outputs": {
            "pilot_plan": "pilot/pilot_plan.yaml",
            "pilot_code": "pilot/pilot_code",
            "pilot_results": "pilot/pilot_results.json",
            "motivation_validation": "pilot/motivation_validation.md",
        },
        "required_inputs": ["project", "hypotheses", "exp_plan"],
    },
    "T6": {
        "inputs": {
            "hypotheses": "ideation/hypotheses.md",
            "exp_plan": "ideation/exp_plan.yaml",
            "pilot_results": "pilot/pilot_results.json",
            "motivation_validation": "pilot/motivation_validation.md",
            "comparison_table": "literature/comparison_table.csv",
        },
        "outputs": {
            "novelty_report": "novelty/novelty_report.md",
            "collision_cases": "novelty/collision_cases.md",
            "must_add_baselines": "novelty/must_add_baselines.md",
        },
        "required_inputs": ["hypotheses", "exp_plan", "pilot_results"],
    },
    "T7": {
        "inputs": {
            "project": "project.yaml",
            "exp_plan": "ideation/exp_plan.yaml",
            "pilot_code": "pilot/pilot_code",
            "novelty_report": "novelty/novelty_report.md",
            "must_add_baselines": "novelty/must_add_baselines.md",
        },
        "outputs": {
            "results_summary": "experiments/results_summary.json",
            "runs_dir": "experiments/runs",
            "configs_dir": "experiments/configs",
            "iteration_log": "experiments/iteration_log.md",
            "ablations": "experiments/ablations.csv",
        },
        "required_inputs": ["project", "exp_plan"],
    },
    "T7.5": {
        "inputs": {
            "results_summary": "experiments/results_summary.json",
            "iteration_log": "experiments/iteration_log.md",
            "exp_plan": "ideation/exp_plan.yaml",
        },
        "outputs": {
            "evaluation_decision": "evaluation/evaluation_decision.md",
        },
        "required_inputs": ["results_summary"],
    },
    "T8": {
        "inputs": {
            "project": "project.yaml",
            "synthesis": "literature/synthesis.md",
            "results_summary": "experiments/results_summary.json",
            "related_work_bib": "literature/related_work.bib",
            "hypotheses": "ideation/hypotheses.md",
            "novelty_report": "novelty/novelty_report.md",
            "ablations": "experiments/ablations.csv",
        },
        "outputs": {
            "outline": "drafts/outline.md",
            "paper": "drafts/paper.tex",
            "self_review": "reviews/self_review.md",
        },
        "required_inputs": [
            "project",
            "synthesis",
            "related_work_bib",
            "hypotheses",
            "results_summary",
        ],
    },
    "T9": {
        "inputs": {
            "paper": "drafts/paper.tex",
            "related_work_bib": "literature/related_work.bib",
        },
        "outputs": {
            "bundle_dir": "submission/bundle",
            "migration_report": "submission/migration_report.md",
        },
        "required_inputs": ["paper", "related_work_bib"],
    },
}


def get_task_io(task_id: str) -> dict[str, object]:
    """读取 task 的 I/O 契约。"""
    if task_id not in TASK_IO_CONTRACTS:
        raise KeyError(f"Unknown task I/O contract: {task_id}")
    return TASK_IO_CONTRACTS[task_id]


def resolve_outputs(workspace: Path, task_id: str) -> dict[str, Path]:
    """把 output 相对路径解析成 workspace 内绝对路径。"""
    outputs = get_task_io(task_id)["outputs"]
    return {name: workspace / rel for name, rel in outputs.items()}  # type: ignore[union-attr]


def resolve_inputs(workspace: Path, task_id: str) -> dict[str, Path]:
    """把 input 相对路径解析成 workspace 内绝对路径。"""
    inputs = get_task_io(task_id)["inputs"]
    return {name: workspace / rel for name, rel in inputs.items()}  # type: ignore[union-attr]


def required_input_names(task_id: str) -> list[str]:
    """返回某个 task 的必需前置输入 key 列表。

    兼容策略：
    - 若契约显式声明了 `required_inputs`，则严格使用它；
    - 若未来某个调试 task 只定义了 `inputs` 没定义 `required_inputs`，则保守退回成
      “所有输入都必需”，避免 single-task 模式误判可运行。
    """

    contract = get_task_io(task_id)
    required = contract.get("required_inputs")
    if required is None:
        inputs = contract.get("inputs", {})
        return list(inputs.keys())  # type: ignore[union-attr]
    return list(required)  # type: ignore[arg-type]
