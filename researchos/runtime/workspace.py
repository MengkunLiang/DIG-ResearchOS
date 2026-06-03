from __future__ import annotations

"""workspace 初始化与说明辅助。

Runtime Spec 明确要求 workspace 是 artifact-first 的唯一事实来源。
因此除了 `_runtime/` 本身，这里还把后续 T1-T9 常用目录的“标准树”固定下来，
便于：
- CLI 从 0 初始化一个可调试 workspace；
- README 给出稳定目录结构；
- 后续 agent 开发在同一套路径约定上协作，而不是每个人各建一套目录。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

import yaml


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


STANDARD_WORKSPACE_DIRS = [
    "user_seeds",
    "user_seeds/pdfs",
    "literature",
    "literature/pdfs",
    "literature/paper_notes",
    "literature/paper_notes_abstract",
    "resources",
    "resources/repos",
    "resources/datasets",
    "resources/benchmarks",
    "resources/baselines",
    "ideation",
    "ideation/_mechanism_tuples",
    "novelty",
    "external_executor",
    "external_executor/workdir",
    "external_executor/raw_results",
    "external_executor/configs",
    "external_executor/logs",
    "external_executor/patches",
    "experiments",
    "experiments/runs",
    "experiments/configs",
    "experiments/logs",
    "evaluation",
    "drafts",
    "drafts/survey",
    "drafts/survey/sections",
    "drafts/survey/section_outlines",
    "drafts/sections",
    "drafts/section_outlines",
    "drafts/review_rounds",
    "drafts/patches",
    "drafts/figures",
    "drafts/is",
    "drafts/ccf_a",
    "submission",
    "submission/bundle",
    "submission/bundle/figures",
]


def build_standard_workspace_dirs(runtime_dir_name: str = "_runtime") -> list[str]:
    """返回标准 workspace 目录列表。

    `runtime_dir_name` 默认仍是 `_runtime`，这样对已有 workspace 和测试保持兼容；
    但如果团队想统一改成 `.runtime` 一类名字，也只需要改 `config/runtime.yaml`。
    """

    return [
        f"{runtime_dir_name}/resume",
        f"{runtime_dir_name}/traces",
        f"{runtime_dir_name}/logs",
        *STANDARD_WORKSPACE_DIRS,
    ]


@dataclass
class WorkspaceInitResult:
    """初始化 workspace 后返回的摘要。"""

    workspace_dir: Path
    created_dirs: list[str]
    project_file: Path | None


def initialize_workspace(
    workspace_dir: Path,
    *,
    create_project_file: bool = True,
    project_id: str | None = None,
    topic: str | None = None,
    force_project_file: bool = False,
    runtime_dir_name: str = "_runtime",
) -> WorkspaceInitResult:
    """创建标准 workspace 树。

    约定：
    - 永远不会删除已有文件；
    - `project.yaml` 仅在不存在或显式 `force_project_file=True` 时写入；
    - 目录初始化是幂等操作，适合在 CLI / 测试 / 脚本里反复调用。
    """

    workspace_dir = workspace_dir.resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    created_dirs: list[str] = []

    for rel_dir in build_standard_workspace_dirs(runtime_dir_name):
        candidate = workspace_dir / rel_dir
        if not candidate.exists():
            created_dirs.append(rel_dir)
        candidate.mkdir(parents=True, exist_ok=True)

    project_file: Path | None = None
    if create_project_file:
        project_file = write_project_stub(
            workspace_dir,
            project_id=project_id or "demo-project",
            topic=topic or "",
            force=force_project_file,
        )

    # 创建 user_seeds 示例文件
    create_user_seeds_examples(workspace_dir)
    create_directory_guides(workspace_dir, runtime_dir_name=runtime_dir_name)

    return WorkspaceInitResult(
        workspace_dir=workspace_dir,
        created_dirs=created_dirs,
        project_file=project_file,
    )


def write_project_stub(
    workspace_dir: Path,
    *,
    project_id: str,
    topic: str,
    force: bool = False,
) -> Path:
    """写入最小 `project.yaml` 模板。"""

    project_path = workspace_dir / "project.yaml"
    if project_path.exists() and not force:
        return project_path

    payload: dict[str, Any] = {
        "project_id": project_id,
        "topic": topic,
        "created_at": _now_iso(),
        "status": "draft",
        "notes": (
            "该文件是由 runtime 初始化生成的最小模板。"
            "后续 T1/T7.5 等 agent 落地后，可在此基础上补业务字段。"
        ),
    }
    project_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return project_path


def create_directory_guides(workspace_dir: Path, *, runtime_dir_name: str = "_runtime") -> None:
    """Write lightweight `_DIR_GUIDE.md` files for major artifact directories."""

    guides = {
        ".": {
            "purpose": "ResearchOS 单个项目 workspace 的根目录，是该项目所有 artifact、状态和恢复信息的事实源。",
            "produced_by": "init-workspace, run, resume, run-task, and user-provided seeds.",
            "consumed_by": "ResearchOS runtime, agents, tools, external executors, and the user.",
            "key_files": "project.yaml, state.yaml, user_seeds/, literature/, ideation/, external_executor/, experiments/, drafts/, submission/, _runtime/.",
            "human_editable": "Yes for user seeds and explicit corrections; avoid editing runtime or audited evidence by hand.",
            "agent_editable": "Only through workspace-policy-governed tools and declared task outputs.",
            "do_not_put": "API keys, unrelated downloads, large caches, or source-code changes unrelated to this project.",
            "validation": "Progress must be recoverable from files here; do not rely on model memory for resume.",
        },
        "user_seeds": {
            "purpose": "用户放入种子论文、想法、约束、外部资源的入口目录。",
            "produced_by": "User, init-workspace.",
            "consumed_by": "T1, T2, T4.",
            "key_files": "seed_papers.jsonl, seed_ideas.md, seed_constraints.md, seed_external_resources.jsonl, pdfs/.",
            "human_editable": "Yes.",
            "agent_editable": "Only examples or upload helper outputs.",
            "do_not_put": "Runtime logs, generated experiment results, API keys.",
            "validation": "Seed files should be valid JSONL/Markdown when present.",
        },
        "literature": {
            "purpose": "论文检索、验证、精读笔记、综合和引用库。",
            "produced_by": "T2, T3, T3.5.",
            "consumed_by": "T3, T3.5, T4, T4.5, T5-HANDOFF, T8.",
            "key_files": "papers_raw.jsonl, papers_verified.jsonl, paper_notes/, synthesis.md, related_work.bib, baseline_map.json.",
            "human_editable": "Only for corrections with provenance.",
            "agent_editable": "Scout/Reader and synthesis tools.",
            "do_not_put": "External executor code, final paper bundle.",
            "validation": "Notes and citation files must be traceable to verified papers.",
        },
        "resources": {
            "purpose": "代码仓库、数据集、benchmark、baseline 和复现资源候选。",
            "produced_by": "T2 resource scout, future resource tools, user-provided seeds.",
            "consumed_by": "T3.5, T4.5, T5-HANDOFF, external executor, T8.",
            "key_files": "baseline_candidates.jsonl, datasets_verified.jsonl, benchmarks.jsonl, reproducibility_matrix.csv, resource_search_log.md.",
            "human_editable": "Yes, when adding known repos/datasets.",
            "agent_editable": "Resource mining tools may append structured candidates.",
            "do_not_put": "Downloaded datasets or cloned repos; use external_executor/workdir or configured caches.",
            "validation": "Candidates should include provenance, license/access notes, and runnability status.",
        },
        "ideation": {
            "purpose": "研究假设、实验计划、风险、新颖性预审输入和候选 idea 记录。",
            "produced_by": "T4, T4.5.",
            "consumed_by": "T4.5, T5-HANDOFF, T7-POST-NOVELTY, T8.",
            "key_files": "hypotheses.md, exp_plan.yaml, risks.md, idea_scorecard.yaml, novelty_audit.md.",
            "human_editable": "Only for explicit corrections or gate decisions.",
            "agent_editable": "Ideation and novelty auditor agents.",
            "do_not_put": "Raw experiment outputs or manuscript drafts.",
            "validation": "exp_plan.yaml must stay parseable and tied to hypotheses.",
        },
        "novelty": {
            "purpose": "实验后 novelty/collision 复核和 required baseline 结构化要求。",
            "produced_by": "T5-HANDOFF, T7-POST-NOVELTY, legacy T6.",
            "consumed_by": "T5-HANDOFF, T7-AUDIT, T7-CLAIMS, T7.5, T8.",
            "key_files": "required_baselines.json, post_experiment_novelty_check.json, post_experiment_collision_cases.md.",
            "human_editable": "Review notes only; structured files should keep schema.",
            "agent_editable": "Novelty and experimenter agents.",
            "do_not_put": "Paper claims unsupported by evidence.",
            "validation": "Required baselines must not be silently dropped downstream.",
        },
        "external_executor": {
            "purpose": "ResearchOS 与 Codex/Claude/manual 外部实验执行器的边界目录。",
            "produced_by": "T5-HANDOFF, T5-EXECUTOR-GATE, external executor, T5-DRY-RUN.",
            "consumed_by": "T5-EXTERNAL-WAIT, T7-INGEST, T7-AUDIT, T7-POST-NOVELTY, T7-CLAIMS.",
            "key_files": "AGENTS.md, CLAUDE.md, handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt, result_pack.json, executor_status.json, run_manifest.json.",
            "human_editable": "Manual executor outputs only.",
            "agent_editable": "External executor may write only paths allowed by allowed_paths.txt.",
            "do_not_put": "Final paper text, API keys, unrelated notebooks, ResearchOS source edits.",
            "validation": "Every metric must trace to raw result, config, log, run id, and sha256.",
        },
        "experiments": {
            "purpose": "ResearchOS 摄取和审计后的实验结果、证据索引和公平性审计。",
            "produced_by": "T7-INGEST, T7-AUDIT, T7-CLAIMS, legacy T7.",
            "consumed_by": "T7.5, T8, T9.",
            "key_files": "results_summary.json, evidence_index.json, integrity_audit.json, experiment_fairness_review.md, iteration_log.md.",
            "human_editable": "Only review notes; do not hand-edit audited metrics without provenance.",
            "agent_editable": "Experimenter audit/ingest tools.",
            "do_not_put": "External executor raw files that belong in external_executor/.",
            "validation": "Results must preserve mock_only/evidence_grade and artifact hashes.",
        },
        "evaluation": {
            "purpose": "PI 对实验结果是否足够进入写作的决策。",
            "produced_by": "T7.5.",
            "consumed_by": "T7.5 human gate, T8.",
            "key_files": "evaluation_decision.md.",
            "human_editable": "Gate decisions are persisted separately; manual notes are allowed.",
            "agent_editable": "PIAgent.",
            "do_not_put": "Raw results or manuscript source.",
            "validation": "Decision should contain Situation, Options, and next_task.",
        },
        "drafts": {
            "purpose": "论文写作资源索引、章节草稿、claim ledger、审计和 paper.tex。",
            "produced_by": "T8 writer/reviewer tools.",
            "consumed_by": "T8, T9.",
            "key_files": "paper_state.json, sections/, paper.tex, result_to_claim.json, experiment_evidence_pack.json, must_not_claim.md, paper_claim_audit.json.",
            "human_editable": "User corrections may be added in user_corrections.md.",
            "agent_editable": "Writer/Reviewer agents.",
            "do_not_put": "External executor raw experiment outputs.",
            "validation": "Every empirical claim must trace to result_to_claim/evidence pack.",
        },
        "submission": {
            "purpose": "投稿 bundle、LaTeX 编译报告和最终 PDF。",
            "produced_by": "T9.",
            "consumed_by": "User/submission process.",
            "key_files": "bundle/main.tex, bundle/main.pdf, compile_report.json, migration_report.md.",
            "human_editable": "Only after T9 completion or explicit override.",
            "agent_editable": "SubmissionAgent.",
            "do_not_put": "Draft-only sections or raw experiments.",
            "validation": "PDF must be compiled from current main.tex and migration report must record evidence chain.",
        },
        runtime_dir_name: {
            "purpose": "运行状态、trace、日志和恢复元数据。",
            "produced_by": "Runtime.",
            "consumed_by": "Runtime, diagnostics.",
            "key_files": "traces/, logs/, resume snapshots.",
            "human_editable": "Usually no; inspect for debugging.",
            "agent_editable": "Runtime only.",
            "do_not_put": "Research artifacts or user data.",
            "validation": "Do not delete while a project is active unless intentionally resetting runtime metadata.",
        },
    }
    all_guide_dirs = _workspace_dirs_requiring_guides(workspace_dir, runtime_dir_name=runtime_dir_name)
    for rel_dir in all_guide_dirs:
        guide = guides.get(rel_dir) or _default_dir_guide(rel_dir, runtime_dir_name=runtime_dir_name)
        path = workspace_dir / rel_dir / "_DIR_GUIDE.md" if rel_dir != "." else workspace_dir / "_DIR_GUIDE.md"
        if path.exists() and not _looks_like_generated_dir_guide(path):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_dir_guide(guide), encoding="utf-8")


def _workspace_dirs_requiring_guides(workspace_dir: Path, *, runtime_dir_name: str) -> list[str]:
    """Return standard and known dynamic artifact directories that need guides.

    The generator deliberately avoids recursively writing guides inside places
    that may contain external code or datasets, such as `external_executor/workdir`
    and `resources/repos`. Dynamic ResearchOS-owned artifact shards like
    `experiments/runs/<run_id>` and `drafts/review_rounds/<round>_sections`
    are still covered because users routinely inspect them during resume/debug.
    """

    guide_dirs: set[str] = {".", runtime_dir_name, *build_standard_workspace_dirs(runtime_dir_name)}
    prune_rel_dirs = {
        "external_executor/workdir",
        "resources/repos",
        "resources/datasets",
        "user_seeds/pdfs",
        "literature/pdfs",
        "submission/bundle/figures",
    }
    for root, dirnames, _filenames in os.walk(workspace_dir):
        root_path = Path(root)
        try:
            rel = root_path.relative_to(workspace_dir).as_posix()
        except ValueError:  # pragma: no cover - defensive guard for symlink oddities
            continue
        if rel == ".":
            rel = ""

        if rel in prune_rel_dirs:
            dirnames[:] = []
        else:
            dirnames[:] = [name for name in dirnames if name not in {".git", "__pycache__", ".pytest_cache"}]

        if not rel:
            continue
        if _should_generate_existing_dir_guide(rel, runtime_dir_name=runtime_dir_name):
            guide_dirs.add(rel)

    return sorted(guide_dirs, key=lambda item: (item.count("/"), item))


def _should_generate_existing_dir_guide(rel_dir: str, *, runtime_dir_name: str) -> bool:
    normalized = rel_dir.rstrip("/")
    if normalized in {
        "app_exp",
        "literature/temp",
        f"{runtime_dir_name}/resume",
        "pilot",
        "pilot/pilot_code",
        "reviews",
        "reviews/review_rounds",
        "skills",
    }:
        return True
    dynamic_prefixes = (
        "app_exp/",
        "pilot/",
        "reviews/",
        "drafts/review_rounds/",
        "experiments/runs/",
        f"{runtime_dir_name}/resume/",
    )
    return normalized.startswith(dynamic_prefixes)


def _render_dir_guide(guide: dict[str, str]) -> str:
    key_files = guide["key_files"]
    rows = _guide_file_rows(key_files)
    if rows:
        file_rows = "\n".join(rows)
    else:
        file_rows = f"| 关键文件/子目录 | 说明 |\n|---|---|\n| `{key_files}` | 见阶段契约和上游/下游说明。 |"
    return (
        "# Workspace Directory Guide\n\n"
        "| 项目 | 说明 |\n"
        "|---|---|\n"
        f"| 目录用途 | {guide['purpose']} |\n"
        f"| 生成阶段/来源 | {guide['produced_by']} |\n"
        f"| 下游使用方 | {guide['consumed_by']} |\n"
        f"| 人工可编辑范围 | {guide['human_editable']} |\n"
        f"| Agent 可写范围 | {guide['agent_editable']} |\n"
        f"| 不应放入 | {guide['do_not_put']} |\n"
        f"| 校验/恢复规则 | {guide['validation']} |\n\n"
        "## Key Files\n\n"
        f"{file_rows}\n\n"
        "Generated by ResearchOS workspace initialization.\n"
    )


def _guide_file_rows(key_files: str) -> list[str]:
    """Render key files as a compact table.

    The descriptions are intentionally mechanical. Stage-specific scientific
    meaning remains in docs/agent_pipeline.md and the task validators.
    """

    rows = ["| 文件/子目录 | 内容与用途 |", "|---|---|"]
    items = [item.strip() for item in key_files.split(",") if item.strip()]
    if len(items) <= 1:
        return []
    for item in items:
        label = item.rstrip().rstrip(".")
        rows.append(f"| `{label}` | {_describe_key_file(item)} |")
    return rows


def _describe_key_file(item: str) -> str:
    normalized = item.strip().rstrip(".")
    name = normalized.rstrip("/").split("/")[-1]
    descriptions = {
        "project.yaml": "T1 生成的项目配置，是研究方向、约束和 seed ensemble 的入口。",
        "state.yaml": "状态机进度、当前节点、暂停/恢复信息和历史记录。",
        "user_seeds": "用户提供的论文、想法、约束和外部资源入口。",
        "literature": "T2/T3/T3.5 生成的文献检索、笔记和综合材料。",
        "ideation": "T4/T4.5 生成的候选 idea、假设、实验计划和 novelty 审计输入。",
        "external_executor": "T5 handoff、外部执行器 prompt、result pack 和运行证据边界。",
        "experiments": "T7 摄取/审计后的标准化实验结果和 evidence index。",
        "drafts": "T8 写作资源、章节草稿、审计、result-to-claim 和 paper.tex。",
        "submission": "T9 投稿 bundle、编译报告和最终 PDF。",
        "_runtime": "runtime 日志、trace、人机交互记录和 resume 快照。",
        "pdfs": "用户或文献阶段提供的 PDF 原始材料。",
        "paper_notes": "T3 精读生成的逐篇结构化笔记。",
        "paper_notes_abstract": "T3 abstract sweep 生成的轻量笔记，不等同全文证据。",
        "synthesis.md": "T3.5 分阶段综合后的 idea fuel，不是直接投稿综述。",
        "related_work.bib": "T3/T8/T9 复用的 BibTeX 引用库。",
        "baseline_map.json": "文献与 baseline/resource 的结构化映射。",
        "seed_papers.jsonl": "用户种子论文条目，每行一条 JSON。",
        "seed_ideas.md": "用户初步想法和偏好。",
        "seed_constraints.md": "预算、资源、伦理、截止日期等硬约束。",
        "seed_external_resources.jsonl": "用户已知数据集、代码仓库、benchmark、模型等资源。",
        "baseline_candidates.jsonl": "候选 baseline/resource 记录，供 T5 handoff 与 T8 写作复用。",
        "datasets_verified.jsonl": "已核验数据集资源候选。",
        "benchmarks.jsonl": "benchmark/协议候选。",
        "reproducibility_matrix.csv": "资源可复现性、许可和执行状态矩阵。",
        "resource_search_log.md": "资源检索过程和 provenance 记录。",
        "hypotheses.md": "T4 选定假设与机制解释。",
        "exp_plan.yaml": "T4 生成的实验协议输入，供外部执行器使用。",
        "risks.md": "T4 风险、失败模式和边界条件。",
        "idea_scorecard.yaml": "候选 idea 的证据链、风险和选择记录。",
        "novelty_audit.md": "T4.5 新颖性预审报告。",
        "required_baselines.json": "实验后必须覆盖或说明的 baseline 要求。",
        "post_experiment_novelty_check.json": "T7 后基于实现/结果的 novelty 复核。",
        "post_experiment_collision_cases.md": "实验后潜在撞车/claim 降级说明。",
        "AGENTS.md": "外部执行器给 Codex/agent 的工作约束。",
        "CLAUDE.md": "外部执行器给 Claude Code 的工作约束。",
        "handoff_pack.json": "T5 编译的实验任务、协议、证据契约和 allowed paths。",
        "expected_outputs_schema.json": "外部执行器必须写回的 result pack/status/manifest schema。",
        "allowed_paths.txt": "外部执行器可读写路径边界。",
        "result_pack.json": "外部执行器写回的核心结果包，T7 只从这里摄取实验结果。",
        "executor_status.json": "外部执行器状态、accepted/mock/dry-run 标记。",
        "run_manifest.json": "运行记录、raw/config/log 路径和 provenance。",
        "results_summary.json": "T7 标准化后的实验结果摘要。",
        "evidence_index.json": "指标、raw result、config、log、hash 的证据索引。",
        "integrity_audit.json": "实验诚信和 provenance 审计。",
        "experiment_fairness_review.md": "baseline、公平性和 claim 边界审阅。",
        "iteration_log.md": "实验迭代与决策日志。",
        "evaluation_decision.md": "T7.5 PI 对是否进入写作或回退的决策。",
        "paper_state.json": "T8 逐章节写作共享状态和事实源。",
        "sections": "T8 每个 section 的独立 LaTeX 草稿。",
        "section_outlines": "T8 每个 section 的局部大纲。",
        "paper.tex": "T8 拼装后的整篇主稿源码。",
        "result_to_claim.json": "实验结果到论文 claim 的保守映射。",
        "experiment_evidence_pack.json": "T8 可引用的实验 evidence pack。",
        "must_not_claim.md": "实验和 novelty 审计禁止写入论文的强 claim。",
        "paper_claim_audit.json": "论文 claim 对 evidence pack 的审计结果。",
        "bundle": "T9 生成的投稿编译目录。",
        "main.tex": "T9 bundle 中用于编译的主 TeX。",
        "main.pdf": "T9 编译出的 PDF。",
        "compile_report.json": "T9 编译尝试、hash、日志和成功状态报告。",
        "migration_report.md": "T9 从 drafts 到 submission bundle 的迁移说明。",
        "traces": "LLM/tool 消息 trace。",
        "logs": "runtime 日志。",
        "resume snapshots": "可恢复运行的输出存在性和 pending queue 快照。",
        "pilot_plan.yaml": "legacy T5 pilot 的旧试点实验计划；新主链不读取。",
        "pilot_results.json": "legacy T5 pilot 的旧试点结果；若要进入新写作链，需通过外部结果摄取重新标准化。",
        "motivation_validation.md": "legacy pilot 对动机是否成立的旧判断记录。",
        "pilot_code": "legacy pilot 代码目录；新实验实现应放在 external_executor/workdir 并产出 result_pack。",
        "smoke_test_passed.marker": "legacy pilot 烟测通过标记。",
        "docker_digests.txt": "legacy pilot 记录的 Docker 镜像 digest。",
        "experiment_audit.json": "legacy pilot 多次改代码时的修改审计。",
        "review_rounds": "legacy 顶层 review 分组；当前 T8 review 使用 drafts/review_rounds。",
        "reviewer_notes.md": "legacy/manual reviewer 备注。",
        "manual_feedback.md": "legacy/manual 反馈记录。",
        "SKILL.md": "workspace-local skill 说明；默认主链不会自动加载。",
        "shared-references": "workspace-local skill 的共享参考材料。",
        "tools": "workspace-local skill 的可选工具代码目录。",
    }
    if normalized.endswith("/"):
        return descriptions.get(name, "子目录，存放该阶段的结构化 artifact。")
    if name in descriptions:
        return descriptions[name]
    if "*" in normalized:
        return "匹配的一组文件，具体含义见所属阶段契约。"
    if "." in name:
        return "结构化或文本 artifact；下游会按任务契约读取/校验。"
    return "子目录或 artifact 分组；具体文件由对应阶段生成。"


def _looks_like_generated_dir_guide(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    headings = ["# Directory Purpose", "# Produced By", "# Consumed By", "# Validation Rules"]
    return (
        "Generated by ResearchOS workspace initialization." in text
        or all(item in text for item in headings)
        or "# Workspace Directory Guide" in text
    )


def _default_dir_guide(rel_dir: str, *, runtime_dir_name: str) -> dict[str, str]:
    """Return a concise guide for standard subdirectories not needing bespoke prose."""

    normalized = rel_dir.rstrip("/")
    if normalized in {"", "."}:
        return {
            "purpose": "ResearchOS project workspace root.",
            "produced_by": "init-workspace and runtime commands.",
            "consumed_by": "Runtime, agents, tools, external executors, and users.",
            "key_files": "project.yaml, state.yaml, standard artifact directories, and _runtime/.",
            "human_editable": "Yes, but prefer editing user_seeds/ and explicit correction files.",
            "agent_editable": "Only via workspace-policy-governed tools.",
            "do_not_put": "Secrets, unrelated caches, or files that belong outside this project.",
            "validation": "The workspace must remain recoverable from artifact files.",
        }
    if normalized.startswith(f"{runtime_dir_name}/resume"):
        return {
            "purpose": "Resume snapshots and output-existence summaries refreshed when a run exits or pauses.",
            "produced_by": "Runtime recovery hooks.",
            "consumed_by": "resume, status/debug flows, and developers diagnosing partial runs.",
            "key_files": "resume_state.json, output summaries, pending queue snapshots, and task-specific recovery metadata.",
            "human_editable": "Inspect only; editing active recovery metadata can make resume misleading.",
            "agent_editable": "Runtime only.",
            "do_not_put": "Research artifacts, paper drafts, raw experiment results, or secrets.",
            "validation": "These files describe recovery state; the source of scientific truth remains the stage artifacts.",
        }
    if normalized.startswith(f"{runtime_dir_name}/"):
        return {
            "purpose": "Runtime metadata subdirectory used for logs, traces, resume state, or diagnostics.",
            "produced_by": "Runtime.",
            "consumed_by": "Runtime, CLI status/trace commands, tests, and debugging tools.",
            "key_files": "Runtime-managed JSONL/log/trace files.",
            "human_editable": "Inspect only; editing active runtime metadata can break resume.",
            "agent_editable": "Runtime only.",
            "do_not_put": "Research artifacts, paper drafts, external executor outputs, or secrets.",
            "validation": "Files here are diagnostic evidence, not scientific evidence for paper claims.",
        }
    if normalized.startswith("drafts/survey"):
        return {
            "purpose": "Optional T3.6 survey-paper branch artifacts for taxonomy planning, section drafts, review, and TeX compilation.",
            "produced_by": "SurveyWriterAgent and survey tools.",
            "consumed_by": "SurveyWriterAgent, latex_compile, T4 via survey insights.",
            "key_files": "decision.json, survey_plan.json, survey_state.json, sections/*.tex, section_outlines/*.md, survey.tex, survey.pdf, survey_audit.json.",
            "human_editable": "Only gate decisions or explicit corrections; do not hand-edit compiled reports.",
            "agent_editable": "SurveyWriterAgent within drafts/survey/.",
            "do_not_put": "Main paper T8 section drafts or external executor results.",
            "validation": "Survey sections must be written section by section and compiled with survey_compile_report.json.",
        }
    if normalized.startswith("drafts/section_outlines"):
        return {
            "purpose": "Per-section writing briefs for T8 manuscript drafting.",
            "produced_by": "initialize_manuscript_state in T8-SECTION-PLAN.",
            "consumed_by": "WriterAgent T8-SEC-* nodes.",
            "key_files": "abstract.md, introduction.md, related_work.md, methodology.md, experiments.md, analysis.md, conclusion.md.",
            "human_editable": "Usually no; add corrections in drafts/user_corrections.md.",
            "agent_editable": "WriterAgent section-planning tools.",
            "do_not_put": "Section prose or whole-paper LaTeX.",
            "validation": "Each outline must contain purpose, required inputs, claim slots, visual slots, and writing rules.",
        }
    if normalized.startswith("drafts/sections"):
        return {
            "purpose": "Section-by-section T8 manuscript drafts.",
            "produced_by": "WriterAgent T8-SEC-* nodes.",
            "consumed_by": "assemble_manuscript, ReviewerAgent, Writer revise nodes.",
            "key_files": "methodology.tex, experiments.tex, related_work.tex, analysis.tex, introduction.tex, conclusion.tex, abstract.tex.",
            "human_editable": "Only explicit corrections; prefer preserving provenance in revision notes.",
            "agent_editable": "WriterAgent.",
            "do_not_put": "Whole-document wrappers, submission bundle files, or raw experiment outputs.",
            "validation": "Each file is one section only; no documentclass/begin{document}/end{document}.",
        }
    if normalized.startswith("drafts/review_rounds/"):
        return {
            "purpose": "Per-section review shards for one manuscript review round.",
            "produced_by": "ReviewerAgent section-aware review calls.",
            "consumed_by": "Review synthesis and WriterAgent revise phases.",
            "key_files": "One Markdown review per section, grouped by review round.",
            "human_editable": "Allowed for explicit reviewer notes, while preserving section/issue structure.",
            "agent_editable": "ReviewerAgent.",
            "do_not_put": "Section source TeX, patch JSON, or compiled PDFs.",
            "validation": "Each shard should target one section and avoid introducing unsupported new claims.",
        }
    if normalized.startswith("drafts/review_rounds"):
        return {
            "purpose": "Section-aware manuscript review reports.",
            "produced_by": "ReviewerAgent.",
            "consumed_by": "WriterAgent revise phase and patch builder.",
            "key_files": "round_N.md, round_N_sections/*.md.",
            "human_editable": "Allowed for explicit reviewer notes, but keep issue structure.",
            "agent_editable": "ReviewerAgent.",
            "do_not_put": "Revised section prose or compiled PDFs.",
            "validation": "Reviews should separate major/minor issues, numeric checks, citation checks, and CDR verdicts.",
        }
    if normalized.startswith("drafts/patches"):
        return {
            "purpose": "Mechanical patch lists derived from manuscript reviews.",
            "produced_by": "build_manuscript_revision_patches.",
            "consumed_by": "WriterAgent revise phase.",
            "key_files": "round_1_patches.json, round_2_patches.json.",
            "human_editable": "Only to clarify target section or severity before revise.",
            "agent_editable": "WriterAgent/tools.",
            "do_not_put": "Free-form review prose or section drafts.",
            "validation": "Patch entries must target a section/file and issue type; they do not replace LLM judgment.",
        }
    if normalized.startswith("drafts/figures"):
        return {
            "purpose": "Figures generated or selected for the manuscript draft.",
            "produced_by": "WriterAgent/tools, future figure-generation modules, user-provided assets.",
            "consumed_by": "T8 manuscript and T9 submission bundle.",
            "key_files": "*.pdf, *.png, figure_registry.json references.",
            "human_editable": "Yes for curated visual assets with provenance.",
            "agent_editable": "Figure tools and WriterAgent.",
            "do_not_put": "Raw experiment logs or unrelated images.",
            "validation": "Referenced figures should be listed in figure_registry.json or copied into submission/bundle/figures.",
        }
    if normalized in {"drafts/is", "drafts/ccf_a"}:
        return {
            "purpose": "Venue-style manuscript variants when T8 writing_style is both.",
            "produced_by": "assemble_manuscript and WriterAgent style revision.",
            "consumed_by": "audit_writing_craft and final writing review.",
            "key_files": "paper.tex, craft_audit.json, style_revision_notes.md.",
            "human_editable": "Only for explicit style edits.",
            "agent_editable": "WriterAgent.",
            "do_not_put": "Primary section source files or submission bundle outputs.",
            "validation": "Variants must be substantive style revisions, not only the main paper plus comments.",
        }
    if normalized.startswith("external_executor/workdir"):
        return {
            "purpose": "Sandboxed working directory for real external executor implementation and experiment runs.",
            "produced_by": "Codex CLI, Claude Code, or manual external executor.",
            "consumed_by": "External executor only; ResearchOS consumes result_pack/raw/config/log files, not arbitrary workdir state.",
            "key_files": "Executor-owned repos, scripts, notebooks, temporary run files.",
            "human_editable": "Yes when running manual mode.",
            "agent_editable": "External executor only, subject to allowed_paths.txt.",
            "do_not_put": "ResearchOS source edits, final paper text, or secrets.",
            "validation": "Publishable evidence must be copied into result_pack-linked raw_results/configs/logs with hashes.",
        }
    if normalized.startswith("external_executor/raw_results"):
        return {
            "purpose": "Raw metric/result files emitted by the external executor.",
            "produced_by": "External executor or mock dry-run.",
            "consumed_by": "T5-EXTERNAL-WAIT, T7-INGEST, T7-AUDIT.",
            "key_files": "JSON/CSV raw results referenced by result_pack metrics.",
            "human_editable": "No after result_pack is written unless rerunning executor.",
            "agent_editable": "External executor only.",
            "do_not_put": "Paper prose or unreferenced scratch output.",
            "validation": "Every file must be referenced by result_pack/run_manifest and have matching sha256 when declared.",
        }
    if normalized.startswith("external_executor/configs"):
        return {
            "purpose": "Configuration files used by external experiment runs.",
            "produced_by": "External executor.",
            "consumed_by": "T7-AUDIT and result provenance checks.",
            "key_files": "YAML/JSON configs referenced by run_manifest.",
            "human_editable": "Only before executor run; after run they are evidence.",
            "agent_editable": "External executor only.",
            "do_not_put": "Unrelated environment files or credentials.",
            "validation": "Configs should link to run ids and metrics in result_pack.",
        }
    if normalized.startswith("external_executor/logs"):
        return {
            "purpose": "Execution logs for external experiment runs.",
            "produced_by": "External executor.",
            "consumed_by": "T7-AUDIT and debugging.",
            "key_files": "*.log referenced by run_manifest.",
            "human_editable": "No after run completion.",
            "agent_editable": "External executor only.",
            "do_not_put": "ResearchOS runtime logs; those belong in _runtime/logs.",
            "validation": "Logs should be referenced in run_manifest and available for audit.",
        }
    if normalized.startswith("external_executor/patches"):
        return {
            "purpose": "External executor code patches or implementation diffs.",
            "produced_by": "External executor.",
            "consumed_by": "T7-AUDIT, human debugging, future replication.",
            "key_files": "*.patch or patch metadata.",
            "human_editable": "Only when manually documenting external implementation changes.",
            "agent_editable": "External executor only.",
            "do_not_put": "ResearchOS runtime patches unrelated to the experiment.",
            "validation": "Patch records should correspond to run_manifest/code provenance when used.",
        }
    if normalized.startswith("experiments/runs/"):
        return {
            "purpose": "One normalized experiment run or run-family directory after external result ingestion.",
            "produced_by": "T7-INGEST/T7-AUDIT or legacy experiment modes.",
            "consumed_by": "T7-CLAIMS, T7.5, T8 methodology/experiments writing, and debugging.",
            "key_files": "Run-specific metrics, copied configs, logs, provenance, or audit notes.",
            "human_editable": "No; change the source result_pack or external executor artifacts and re-ingest.",
            "agent_editable": "Experimenter tools only.",
            "do_not_put": "Untracked scratch notebooks or unreferenced raw files.",
            "validation": "Run artifacts must remain traceable to result_pack, run_manifest, and evidence_index.",
        }
    if normalized.startswith("experiments/runs"):
        return {
            "purpose": "Normalized run records after ResearchOS ingests external results.",
            "produced_by": "T7-INGEST/T7-AUDIT or legacy experiment modes.",
            "consumed_by": "T7-CLAIMS, T7.5, T8.",
            "key_files": "Run-level normalized records.",
            "human_editable": "No; edit source result_pack and re-ingest instead.",
            "agent_editable": "Experimenter tools.",
            "do_not_put": "External executor raw files.",
            "validation": "Records must preserve run ids, seeds, configs, logs, and mock_only/evidence_grade fields.",
        }
    if normalized.startswith("experiments/configs"):
        return {
            "purpose": "Normalized or copied experiment configs used for audit and writing.",
            "produced_by": "T7-INGEST/T7-AUDIT.",
            "consumed_by": "T8 methodology/experiments sections.",
            "key_files": "Config snapshots tied to run records.",
            "human_editable": "No after ingestion.",
            "agent_editable": "Experimenter tools.",
            "do_not_put": "Executor scratch configs not referenced by evidence.",
            "validation": "Configs must stay consistent with evidence_index.json and result metrics.",
        }
    if normalized.startswith("experiments/logs"):
        return {
            "purpose": "Normalized experiment logs after ingestion.",
            "produced_by": "T7-INGEST/T7-AUDIT.",
            "consumed_by": "T8 and debugging.",
            "key_files": "Logs linked to run records.",
            "human_editable": "No after ingestion.",
            "agent_editable": "Experimenter tools.",
            "do_not_put": "ResearchOS runtime logs.",
            "validation": "Logs must be traceable to external_executor/logs or run_manifest entries.",
        }
    if normalized.startswith("resources/repos"):
        purpose = "Curated references to baseline or support repositories, not arbitrary clones."
    elif normalized.startswith("resources/datasets"):
        purpose = "Curated dataset references, access notes, licenses, and small metadata records."
    elif normalized.startswith("resources/benchmarks"):
        purpose = "Benchmark definitions, metrics, and resource notes."
    elif normalized.startswith("resources/baselines"):
        purpose = "Baseline method/resource records used by novelty and external executor handoff."
    elif normalized == "literature/temp":
        purpose = "Transient literature processing scratch area retained only for debugging parser/search recovery."
    elif normalized.startswith("app_exp"):
        purpose = "Legacy application-experiment scratch area kept for old workspace inspection; new experiments should use external_executor/ and experiments/."
    else:
        if normalized.startswith("pilot"):
            return {
                "purpose": "Legacy internal-pilot experiment area kept only for explicit legacy T5 run-task compatibility; the main workflow now uses external_executor/ and experiments/.",
                "produced_by": "Legacy T5 pilot mode only when explicitly run with legacy compatibility.",
                "consumed_by": "Legacy T6/T7 compatibility paths and old workspace inspection; current main chain does not consume it.",
                "key_files": "pilot_plan.yaml, pilot_results.json, motivation_validation.md, pilot_code/, smoke_test_passed.marker, docker_digests.txt, experiment_audit.json",
                "human_editable": "Normally no; use external_executor/ for new experiment execution.",
                "agent_editable": "Only legacy ExperimenterAgent pilot mode.",
                "do_not_put": "New external executor outputs, result_pack.json, paper drafts, or current T7 evidence.",
                "validation": "If present, files are legacy evidence only and must not be treated as current external executor evidence unless re-ingested.",
            }
        if normalized.startswith("reviews"):
            return {
                "purpose": "Legacy top-level review scratch area; active T8 manuscript review artifacts belong in drafts/review_rounds/.",
                "produced_by": "Legacy/manual review workflows.",
                "consumed_by": "Humans inspecting old workspaces; current T8 reviewer consumes drafts/review_rounds/.",
                "key_files": "review_rounds/, reviewer_notes.md, manual_feedback.md",
                "human_editable": "Yes for archived manual notes.",
                "agent_editable": "No in the current main workflow.",
                "do_not_put": "Current T8 section review shards or revision patch JSON.",
                "validation": "Current manuscript revisions should be based on drafts/review_rounds/ and drafts/patches/.",
            }
        if normalized == "skills":
            return {
                "purpose": "Optional workspace-local skill notes if a project explicitly uses them; built-in ResearchOS skills live in researchos/skills/.",
                "produced_by": "User or explicit skill-development workflow.",
                "consumed_by": "Only custom project workflows that explicitly point to this directory.",
                "key_files": "SKILL.md, shared-references/, tools/",
                "human_editable": "Yes for project-local skill notes.",
                "agent_editable": "Only when a task explicitly allows workspace-local skills.",
                "do_not_put": "Built-in ResearchOS runtime skills or unrelated code repositories.",
                "validation": "Default pipeline does not load this directory automatically as a source of truth.",
            }
        purpose = f"Standard ResearchOS workspace subdirectory `{normalized}`."
    return {
        "purpose": purpose,
        "produced_by": "ResearchOS agents/tools or user-provided artifacts according to the pipeline stage.",
        "consumed_by": "Downstream ResearchOS stages declared in state_machine.yaml and task_io_contract.py.",
        "key_files": "See docs/agent_pipeline.md and this directory's parent guide for stage-specific files.",
        "human_editable": "Only when adding explicit user corrections or external resources with provenance.",
        "agent_editable": "Only agents/tools with workspace policy permission for this prefix.",
        "do_not_put": "API keys, unrelated scratch files, or artifacts belonging to another standard directory.",
        "validation": "Files should be structured, traceable, and consistent with the task contract that consumes them.",
    }


def create_user_seeds_examples(workspace_dir: Path) -> None:
    """在 user_seeds 目录下创建示例文件和空模板，指导用户如何放置种子数据。

    策略：
    1. 创建 .example 示例文件（仅作参考）
    2. 如果实际 seed 文件不存在，创建空模板（避免 Agent 读取时报错）
    """

    user_seeds_dir = workspace_dir / "user_seeds"

    # 1. README.md - 使用说明
    readme_path = user_seeds_dir / "README.md"
    if not readme_path.exists():
        readme_content = """# User Seeds 目录说明

这个目录用于存放项目的种子数据，T1 Agent 会在初始化时收集这些信息。

## 目录结构

```
user_seeds/
├── README.md                        # 本说明文件
├── seed_papers.jsonl.example        # 种子论文示例
├── seed_ideas.md.example            # 初步想法示例
├── seed_constraints.md.example      # 硬约束清单示例
├── seed_external_resources.jsonl.example  # 外部资源示例
└── pdfs/                            # 存放 PDF 文件
```

## 使用方式

### 1. 提供种子论文（推荐方式）

**🎯 推荐方式：直接放入 PDF 文件（自动识别）**
- **将 PDF 文件放入 `pdfs/` 目录**
- **T1 Agent 会自动扫描并识别所有 PDF 文件**
- **无需手动提供路径或编辑配置文件**
- 支持批量：一次性放入多个 PDF，T1 会逐个处理

**其他方式（在 T1 对话中提供）：**

**方式 2：提供 arXiv ID**
- 在 T1 对话中直接提供 arXiv ID：`2601.03192`
- 或 arXiv DOI：`10.48550/arXiv.2601.03192`

**方式 3：提供 DOI**
- 在 T1 对话中提供 DOI：`10.1145/3534678.3539147`

**方式 4：手动编辑 seed_papers.jsonl**
- 复制 `seed_papers.jsonl.example` 为 `seed_papers.jsonl`
- 按照示例格式填写论文信息

### 2. 提供初步想法（可选）

- 复制 `seed_ideas.md.example` 为 `seed_ideas.md`
- 填写你的研究想法和假设
- **用途**：T4 Ideation Agent 会将其作为候选研究方向之一

### 3. 提供硬约束（可选）

- 复制 `seed_constraints.md.example` 为 `seed_constraints.md`
- 填写必须遵守的技术或方法约束
- **用途**：T2 Scout Agent 会在文献检索时考虑这些约束

### 4. 提供外部资源（可选）

- 复制 `seed_external_resources.jsonl.example` 为 `seed_external_resources.jsonl`
- 填写已有的数据集、代码仓库、预训练模型等资源
- **用途**：T5 Experimenter Agent 等后续阶段会使用这些资源

## 注意事项

1. `.example` 文件仅作为示例，不会被 T1 Agent 读取
2. 实际使用时，去掉 `.example` 后缀
3. **推荐做法**：将 PDF 放入 `pdfs/` 目录，其他信息在 T1 对话中提供
4. 也可以手动创建这些文件，T1 Agent 会读取并使用

## 各文件的使用阶段

| 文件 | 使用阶段 | 用途 |
|------|---------|------|
| `seed_papers.jsonl` | T1 生成，T2 使用 | 种子论文列表 |
| `seed_ideas.md` | T4 Ideation Agent | 作为候选研究方向 |
| `seed_constraints.md` | T2 Scout Agent | 文献检索约束 |
| `seed_external_resources.jsonl` | T5+ | 外部资源清单 |
| `pdfs/` | T1 自动扫描 | 存放 PDF 文件 |
"""
        readme_path.write_text(readme_content, encoding="utf-8")

    # 2. seed_papers.jsonl.example
    papers_example_path = user_seeds_dir / "seed_papers.jsonl.example"
    if not papers_example_path.exists():
        papers_example = """{"title": "Attention Is All You Need", "authors": ["Vaswani, Ashish", "Shazeer, Noam"], "year": 2017, "role": "anchor", "why_relevant": "Transformer 架构的开创性论文，是我们研究的核心参考"}
{"title": "BERT: Pre-training of Deep Bidirectional Transformers", "authors": ["Devlin, Jacob", "Chang, Ming-Wei"], "year": 2019, "role": "reference", "why_relevant": "预训练语言模型的重要参考"}
"""
        papers_example_path.write_text(papers_example, encoding="utf-8")

    # 3. seed_ideas.md.example
    ideas_example_path = user_seeds_dir / "seed_ideas.md.example"
    if not ideas_example_path.exists():
        ideas_example = """# 初步研究想法

## 核心假设

我们假设通过改进注意力机制的计算方式，可以在保持模型性能的同时显著降低计算复杂度。

## 初步方案

1. **稀疏注意力**：只计算最相关的 token 之间的注意力
2. **局部注意力**：限制注意力窗口大小
3. **分层注意力**：在不同层使用不同的注意力模式

## 预期效果

- 计算复杂度从 O(n²) 降低到 O(n log n)
- 在长文本任务上性能提升 20%
- 训练速度提升 2-3 倍

## 需要验证的问题

1. 稀疏注意力是否会损失重要的长距离依赖？
2. 如何自动学习最优的注意力模式？
3. 在不同任务上的泛化能力如何？
"""
        ideas_example_path.write_text(ideas_example, encoding="utf-8")

    # 4. seed_constraints.md.example
    constraints_example_path = user_seeds_dir / "seed_constraints.md.example"
    if not constraints_example_path.exists():
        constraints_example = """# 硬约束清单

## 技术约束

1. **必须使用 PyTorch**：团队熟悉 PyTorch，不考虑其他框架
2. **必须兼容 Hugging Face Transformers**：便于复用预训练模型
3. **不使用外部 API**：所有计算必须在本地完成

## 方法约束

1. **不使用知识蒸馏**：我们关注架构改进，不依赖教师模型
2. **必须保持端到端训练**：不使用多阶段训练

## 资源约束

1. **GPU 限制**：最多使用 4 张 A100 GPU
2. **时间限制**：单次实验不超过 24 小时
3. **存储限制**：模型大小不超过 10GB

## 评估约束

1. **必须在 GLUE 基准上评估**：便于与现有工作比较
2. **必须报告推理速度**：不仅关注准确率，也关注效率
"""
        constraints_example_path.write_text(constraints_example, encoding="utf-8")

    # 5. seed_external_resources.jsonl.example
    resources_example_path = user_seeds_dir / "seed_external_resources.jsonl.example"
    if not resources_example_path.exists():
        resources_example = """{"type": "dataset", "name": "GLUE", "source": "huggingface:glue", "access": "auto", "purpose": "主要评估基准"}
{"type": "baseline_repo", "name": "Transformers", "source": "github:huggingface/transformers", "commit": "v4.30.0", "purpose": "baseline 实现和预训练模型"}
{"type": "pretrained_model", "name": "BERT-base", "source": "huggingface:bert-base-uncased", "purpose": "预训练编码器"}
{"type": "docker_image", "name": "pytorch-env", "source": "docker:pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime", "purpose": "实验环境"}
{"type": "tool", "name": "wandb", "source": "pip:wandb", "purpose": "实验跟踪"}
"""
        resources_example_path.write_text(resources_example, encoding="utf-8")

    # 6. 创建空模板文件（如果实际文件不存在）
    # 这样 Agent 读取时不会因为文件不存在而报错
    _create_empty_seed_files_if_missing(user_seeds_dir)


def _create_empty_seed_files_if_missing(user_seeds_dir: Path) -> None:
    """如果 seed 文件不存在，创建空模板。

    这样 Agent 读取时不会因为文件不存在而报错，
    同时也不会覆盖用户已经创建的文件。
    """

    # seed_papers.jsonl - 空文件（JSONL 格式，每行一个 JSON 对象）
    papers_path = user_seeds_dir / "seed_papers.jsonl"
    if not papers_path.exists():
        papers_path.write_text("", encoding="utf-8")

    # seed_ideas.md - 空文件
    ideas_path = user_seeds_dir / "seed_ideas.md"
    if not ideas_path.exists():
        ideas_path.write_text("# 初步研究想法\n\n（暂无）\n", encoding="utf-8")

    # seed_constraints.md - 空文件
    constraints_path = user_seeds_dir / "seed_constraints.md"
    if not constraints_path.exists():
        constraints_path.write_text("# 硬约束清单\n\n（暂无）\n", encoding="utf-8")

    # seed_external_resources.jsonl - 空文件（可选，不强制创建）
    # 这个文件是可选的，所以不创建空模板


def render_workspace_tree(runtime_dir_name: str = "_runtime") -> str:
    """返回 README / CLI 可复用的标准 workspace 树说明。"""

    return "\n".join(
        [
            "workspace/",
            "|-- project.yaml",
            "|-- state.yaml",
            "|-- user_seeds/",
            "|   `-- pdfs/",
            "|-- literature/",
            "|   |-- pdfs/",
            "|   |-- paper_notes/",
            "|   `-- paper_notes_abstract/",
            "|-- resources/",
            "|   |-- baselines/",
            "|   |-- benchmarks/",
            "|   |-- datasets/",
            "|   `-- repos/",
            "|-- ideation/",
            "|-- novelty/",
            "|-- external_executor/",
            "|   |-- workdir/",
            "|   |-- raw_results/",
            "|   |-- configs/",
            "|   |-- logs/",
            "|   `-- patches/",
            "|-- experiments/",
            "|   |-- runs/",
            "|   |-- configs/",
            "|   `-- logs/",
            "|-- evaluation/",
            "|-- drafts/",
            "|   |-- survey/",
            "|   |   |-- sections/",
            "|   |   `-- section_outlines/",
            "|   |-- sections/",
            "|   |-- section_outlines/",
            "|   |-- review_rounds/",
            "|   |-- patches/",
            "|   |-- figures/",
            "|   |-- is/",
            "|   `-- ccf_a/",
            "|-- pilot/                  # legacy-only compatibility",
            "|   `-- pilot_code/",
            "|-- reviews/",
            "|   `-- review_rounds/",
            "|-- submission/",
            "|   `-- bundle/",
            "|       `-- figures/",
            "|-- skills/",
            f"`-- {runtime_dir_name}/",
            "    |-- resume/",
            "    |-- traces/",
            "    `-- logs/",
        ]
    )
