from __future__ import annotations

"""Deterministic reference-project mining tools.

The tool extracts transferable system-design patterns from local reference
repositories. It intentionally does not judge scientific content; it only
creates structured cards and a transfer matrix that later LLM agents can use as
methodology hints.
"""

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


DEFAULT_REFERENCE_REPOS = [
    "/mnt/data/reference/整体/Auto-claude-code-research-in-sleep-main",
    "/mnt/data/reference/整体/autoresearch-master",
    "/mnt/data/reference/整体/AutoResearchClaw-main",
]


PATTERN_RULES = [
    {
        "pattern_id": "ARIS_RESULT_TO_CLAIM",
        "keywords": ["result-to-claim", "claim", "evidence"],
        "mechanism": "Map experiment outputs to explicitly supported, weak, or unsupported claims before writing.",
        "researchos_target_stage": ["T8-RESOURCE", "T8-SELF-CHECK"],
        "adaptation": "Use validator-backed result_to_claim and evidence pack artifacts rather than free-form prose.",
        "required_artifacts": ["experiments/results_summary.json", "drafts/result_to_claim.json"],
        "acceptance_tests": ["mock-only result cannot become paper evidence", "metric mismatch is reported by paper-claim-audit"],
    },
    {
        "pattern_id": "ARIS_PAPER_CLAIM_AUDIT",
        "keywords": ["paper-claim-audit", "claim audit", "paper claim"],
        "mechanism": "Zero-context paper audit checks manuscript numbers and claims against raw results.",
        "researchos_target_stage": ["T8-DRAFT", "T8-REVISE", "T9"],
        "adaptation": "Generate paper_claim_audit.md/json and require T9 migration report to record the audit chain.",
        "required_artifacts": ["drafts/paper.tex", "drafts/experiment_evidence_pack.json", "drafts/paper_claim_audit.json"],
        "acceptance_tests": ["unsupported number is flagged", "submission report records audit artifacts"],
    },
    {
        "pattern_id": "ARIS_EXTERNAL_EXECUTOR_BRIDGE",
        "keywords": ["experiment-bridge", "external", "executor", "handoff"],
        "mechanism": "A protocol bridge hands a constrained experiment task to an external coding executor.",
        "researchos_target_stage": ["T5-HANDOFF", "T5-DRY-RUN"],
        "adaptation": "ResearchOS writes handoff_pack, AGENTS/CLAUDE control instructions, expected schema, allowed paths, and does not run real experiments in dry-run.",
        "required_artifacts": ["external_executor/handoff_pack.json", "external_executor/expected_outputs_schema.json"],
        "acceptance_tests": ["allowed paths are present", "executor done is not accepted"],
    },
    {
        "pattern_id": "CLAUDE_RESUMABLE_RUN_STATE",
        "keywords": ["resumable", "checkpoint", "heartbeat", "accepted", "done"],
        "mechanism": "Separate executor completion from verifier acceptance and persist resumable run status.",
        "researchos_target_stage": ["T5-DRY-RUN", "T5-EXTERNAL-WAIT", "runtime-resume"],
        "adaptation": "Keep executor_status.accepted=false until external-executor handoff validation confirms required artifacts.",
        "required_artifacts": ["external_executor/executor_status.json", "external_executor/report/run_manifest.json"],
        "acceptance_tests": ["status done with accepted true is rejected in dry-run", "run manifest is present"],
    },
    {
        "pattern_id": "AUTORESEARCH_FIXED_BUDGET_LOOP",
        "keywords": ["budget", "keep", "discard", "metric", "run.log", "results.tsv"],
        "mechanism": "Run constrained edit-run-eval iterations with an objective metric and keep/discard decisions.",
        "researchos_target_stage": ["T5-HANDOFF", "T5-EXTERNAL-WAIT", "T8-RESOURCE"],
        "adaptation": "Encode objective metrics and acceptance criteria in handoff; validate raw metric provenance before T8 writing.",
        "required_artifacts": ["external_executor/result_pack.json", "experiments/iteration_log.md"],
        "acceptance_tests": ["missing metric fails ingest", "iteration log records dry-run boundary"],
    },
    {
        "pattern_id": "RESEARCHCLAW_STAGE_CONTRACT",
        "keywords": ["stagecontract", "contract", "checkpoint", "stage"],
        "mechanism": "Every stage has declared inputs, outputs, definition-of-done, retry and error semantics.",
        "researchos_target_stage": ["task_io_contract", "state_machine", "validators"],
        "adaptation": "Keep ResearchOS state machine and task_io_contract in exact sync, then validate artifacts after each node.",
        "required_artifacts": ["config/system_config/state_machine.yaml", "researchos/orchestration/task_io_contract.py"],
        "acceptance_tests": ["validate-config catches drift", "single-task prerequisite validation catches missing input"],
    },
    {
        "pattern_id": "RESEARCHCLAW_BENCHMARK_REPO_MINING",
        "keywords": ["benchmark", "dataset", "baseline", "code search", "repo"],
        "mechanism": "Mine benchmark, dataset and baseline implementation resources before experiment execution.",
        "researchos_target_stage": ["T2", "T3", "T3.5", "T4"],
        "adaptation": "Use resource candidates and artifact feasibility maps as LLM guidance, not hardcoded scientific claims.",
        "required_artifacts": ["literature/resource_candidates.jsonl", "literature/artifact_feasibility_map.json"],
        "acceptance_tests": ["resource audit explains no-resource cases", "T4 can see benchmark feasibility hints"],
    },
]


class MineReferenceProjectsParams(BaseModel):
    reference_roots: list[str] = Field(default_factory=lambda: list(DEFAULT_REFERENCE_REPOS))
    output_dir: str = Field(default="researchos_reference")
    review_output_path: str = Field(default="docs/reference_project_review.md")
    max_files_per_repo: int = Field(default=80, ge=1, le=500)


class MineReferenceProjectsTool(Tool):
    name = "mine_reference_projects"
    description = "Extract transferable workflow, artifact, executor, audit, resume, and writing patterns from local reference repos."
    parameters_schema = MineReferenceProjectsParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MineReferenceProjectsParams(**kwargs)
        try:
            cards, inventories = _mine_reference_roots(params.reference_roots, params.max_files_per_repo)
            output_dir = self.policy.resolve_write(params.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            pattern_path = output_dir / "pattern_cards.jsonl"
            pattern_path.write_text(
                "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in cards),
                encoding="utf-8",
            )
            transfer_path = output_dir / "transfer_matrix.csv"
            _write_transfer_matrix(transfer_path, cards)
            (output_dir / "skill_import_plan.md").write_text(_format_skill_import_plan(cards), encoding="utf-8")
            (output_dir / "pipeline_comparison.md").write_text(_format_pipeline_comparison(inventories), encoding="utf-8")
            (output_dir / "anti_patterns.md").write_text(_format_anti_patterns(), encoding="utf-8")
            review_path = self.policy.resolve_write(params.review_output_path)
            review_path.parent.mkdir(parents=True, exist_ok=True)
            review_path.write_text(_format_reference_review(inventories, cards), encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"reference mining failed: {exc}", error="reference_mining_failed")
        return ToolResult(
            ok=True,
            content=f"Mined {len(cards)} transferable reference patterns into {params.output_dir}.",
            data={
                "pattern_cards": f"{params.output_dir}/pattern_cards.jsonl",
                "transfer_matrix": f"{params.output_dir}/transfer_matrix.csv",
                "review": params.review_output_path,
                "pattern_count": len(cards),
            },
        )


def _mine_reference_roots(reference_roots: list[str], max_files_per_repo: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventories: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for raw_root in reference_roots:
        root = Path(raw_root)
        inventory = _inventory_reference_repo(root, max_files_per_repo)
        inventories.append(inventory)
        cards.extend(_cards_for_inventory(inventory))
    if not cards:
        cards = [_missing_reference_card(inventories)]
    return cards, inventories


def _inventory_reference_repo(root: Path, max_files: int) -> dict[str, Any]:
    name = root.name or str(root)
    inventory: dict[str, Any] = {
        "name": name,
        "path": str(root),
        "exists": root.exists() and root.is_dir(),
        "reference_missing": not (root.exists() and root.is_dir()),
        "scanned_files": [],
        "matched_terms": {},
    }
    if not inventory["exists"]:
        return inventory

    candidate_files = []
    for pattern in ("README*", "*.md", "*.py", "*.yaml", "*.yml", "*.json"):
        candidate_files.extend(root.rglob(pattern))
    unique_files = []
    seen = set()
    for path in sorted(candidate_files):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        unique_files.append(path)
        if len(unique_files) >= max_files:
            break

    term_hits: dict[str, set[str]] = {}
    for path in unique_files:
        rel = path.relative_to(root).as_posix()
        inventory["scanned_files"].append(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            continue
        for rule in PATTERN_RULES:
            if any(keyword.lower() in text or keyword.lower() in rel.lower() for keyword in rule["keywords"]):
                term_hits.setdefault(rule["pattern_id"], set()).add(rel)
    inventory["matched_terms"] = {key: sorted(value) for key, value in term_hits.items()}
    return inventory


def _cards_for_inventory(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    if inventory.get("reference_missing"):
        return [
            {
                "pattern_id": f"{_safe_id(inventory.get('name', 'missing'))}_REFERENCE_MISSING",
                "source_project": inventory.get("name"),
                "source_files": [],
                "mechanism": "Local reference repository was not available for deterministic mining.",
                "why_it_matters": "The runtime must record missing references instead of inventing transfer patterns.",
                "researchos_target_stage": ["R0-REFERENCE-MINING"],
                "adaptation": "Keep the missing-reference marker and optionally rerun with a valid --reference-roots path.",
                "required_artifacts": ["docs/reference_project_review.md"],
                "risks": ["incomplete reference transfer"],
                "acceptance_tests": ["reference_missing is visible in the review"],
            }
        ]

    cards: list[dict[str, Any]] = []
    matched_terms = inventory.get("matched_terms") or {}
    for rule in PATTERN_RULES:
        files = matched_terms.get(rule["pattern_id"]) or []
        if not files:
            continue
        cards.append(
            {
                "pattern_id": f"{_safe_id(str(inventory.get('name')))}_{rule['pattern_id']}",
                "source_project": inventory.get("name"),
                "source_files": files[:12],
                "mechanism": rule["mechanism"],
                "why_it_matters": _why_pattern_matters(rule["pattern_id"]),
                "researchos_target_stage": rule["researchos_target_stage"],
                "adaptation": rule["adaptation"],
                "required_artifacts": rule["required_artifacts"],
                "risks": _risks_for_pattern(rule["pattern_id"]),
                "acceptance_tests": rule["acceptance_tests"],
            }
        )
    return cards


def _missing_reference_card(inventories: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pattern_id": "REFERENCE_PROJECTS_MISSING",
        "source_project": "all",
        "source_files": [],
        "mechanism": "No local reference repository could be scanned.",
        "why_it_matters": "ResearchOS must fail transparently when reference material is unavailable.",
        "researchos_target_stage": ["R0-REFERENCE-MINING"],
        "adaptation": "Provide valid local paths or document web fallback separately.",
        "required_artifacts": ["docs/reference_project_review.md"],
        "risks": [f"missing: {item.get('path')}" for item in inventories],
        "acceptance_tests": ["pattern_cards.jsonl contains REFERENCE_PROJECTS_MISSING"],
    }


def _write_transfer_matrix(path: Path, cards: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pattern_id", "source_project", "target_stages", "required_artifacts", "acceptance_tests"],
        )
        writer.writeheader()
        for card in cards:
            writer.writerow(
                {
                    "pattern_id": card.get("pattern_id"),
                    "source_project": card.get("source_project"),
                    "target_stages": ";".join(card.get("researchos_target_stage", [])),
                    "required_artifacts": ";".join(card.get("required_artifacts", [])),
                    "acceptance_tests": ";".join(card.get("acceptance_tests", [])),
                }
            )


def _format_reference_review(inventories: list[dict[str, Any]], cards: list[dict[str, Any]]) -> str:
    lines = [
        "# Reference Project Review",
        "",
        "本报告由 `mine_reference_projects` 确定性生成，只记录可迁移工程机制，不替代 LLM 的研究判断。",
        "",
        "## Scanned Projects",
    ]
    for item in inventories:
        lines.extend(
            [
                f"- **{item.get('name')}**",
                f"  - path: `{item.get('path')}`",
                f"  - exists: `{item.get('exists')}`",
                f"  - reference_missing: `{item.get('reference_missing')}`",
                f"  - scanned_files: `{len(item.get('scanned_files', []))}`",
            ]
        )
        if item.get("matched_terms"):
            lines.append("  - matched_patterns: `" + ", ".join(sorted(item["matched_terms"].keys())) + "`")
    lines.extend(["", "## Transferable Pattern Cards"])
    for card in cards:
        lines.extend(
            [
                f"### {card.get('pattern_id')}",
                f"- source_project: `{card.get('source_project')}`",
                f"- source_files: `{', '.join(card.get('source_files', []))}`",
                f"- mechanism: {card.get('mechanism')}",
                f"- target_stage: `{', '.join(card.get('researchos_target_stage', []))}`",
                f"- adaptation: {card.get('adaptation')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Anti-Patterns",
            "- 不把 reference repo 的自然语言规范当成已验证结果。",
            "- 不让外部执行器自我验收；`done` 与 `accepted` 必须分离。",
            "- 不把 dry-run/mock 数字写成论文实证 claim。",
            "- 不用硬编码知识替代 LLM 对领域、文献和实验语境的判断。",
        ]
    )
    return "\n".join(lines) + "\n"


def _format_skill_import_plan(cards: list[dict[str, Any]]) -> str:
    stages = sorted({stage for card in cards for stage in card.get("researchos_target_stage", [])})
    lines = [
        "# Skill Import Plan",
        "",
        "这些 pattern 作为 skill/shared-reference 的指导材料进入 ResearchOS；机械校验由 tool/validator 完成，知识判断仍由 LLM 负责。",
        "",
        "## Target Stages",
    ]
    lines.extend(f"- `{stage}`" for stage in stages)
    lines.extend(["", "## Cards"])
    lines.extend(f"- `{card.get('pattern_id')}` -> {', '.join(card.get('researchos_target_stage', []))}" for card in cards)
    return "\n".join(lines) + "\n"


def _format_pipeline_comparison(inventories: list[dict[str, Any]]) -> str:
    lines = [
        "# Pipeline Comparison",
        "",
        "| Reference | Exists | Scanned Files | Matched Patterns |",
        "| --- | --- | ---: | --- |",
    ]
    for item in inventories:
        lines.append(
            f"| {item.get('name')} | {item.get('exists')} | {len(item.get('scanned_files', []))} | "
            f"{', '.join(sorted((item.get('matched_terms') or {}).keys()))} |"
        )
    return "\n".join(lines) + "\n"


def _format_anti_patterns() -> str:
    return (
        "# Anti-Patterns\n\n"
        "- 不把执行器 summary 当成 raw evidence。\n"
        "- 不把 validator 写成领域知识硬编码；validator 只检查文件协议、来源、hash、mock 标记和 traceability。\n"
        "- 不把 T5 外部执行恢复逻辑导回旧的内部实验代码生成路径。\n"
        "- 不让 section writer 直接消费未经 result-to-claim 的实验数字。\n"
    )


def _why_pattern_matters(pattern_id: str) -> str:
    if "RESULT_TO_CLAIM" in pattern_id:
        return "It prevents weak metrics from becoming overconfident paper claims."
    if "PAPER_CLAIM_AUDIT" in pattern_id:
        return "It catches claim and number drift after manuscript writing."
    if "EXTERNAL_EXECUTOR" in pattern_id:
        return "It separates protocol ownership from isolated code execution."
    if "RESUMABLE" in pattern_id:
        return "It makes external execution recoverable and auditable."
    if "FIXED_BUDGET" in pattern_id:
        return "It keeps experiment loops bounded and metric-driven."
    if "STAGE_CONTRACT" in pattern_id:
        return "It keeps pipeline transitions inspectable and testable."
    return "It gives LLM agents reusable methodology without hardcoding scientific conclusions."


def _risks_for_pattern(pattern_id: str) -> list[str]:
    if "RESULT_TO_CLAIM" in pattern_id:
        return ["same executor self-judges its own result", "claim wording exceeds evidence"]
    if "PAPER_CLAIM_AUDIT" in pattern_id:
        return ["numeric heuristics miss paraphrased claims", "mock evidence contaminates abstract"]
    if "EXTERNAL_EXECUTOR" in pattern_id:
        return ["executor writes outside allowed paths", "missing raw artifacts"]
    if "RESUMABLE" in pattern_id:
        return ["done treated as accepted", "stale heartbeat mistaken for progress"]
    if "BENCHMARK" in pattern_id:
        return ["resource discovery overfits popular repos", "license/access assumptions are wrong"]
    return ["pattern transferred without validator-backed artifact"]


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.upper()).strip("_") or "REFERENCE"
