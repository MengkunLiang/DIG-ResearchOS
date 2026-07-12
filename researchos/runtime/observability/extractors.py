from __future__ import annotations

"""Stage-specific summaries derived from existing artifacts.

These functions deliberately report bounded descriptive facts.  They do not
upgrade retrieval hints, score signals, or unverified metadata into scientific
conclusions.
"""

from collections import Counter
import csv
import json
from pathlib import Path
import re
from typing import Any


def extract_stage_insights(task_id: str, workspace: Path, *, detailed: bool = False) -> list[dict[str, Any]]:
    if task_id == "T1":
        return _t1_insights(workspace)
    if task_id.startswith("T2"):
        return _t2_insights(workspace, detailed=detailed)
    if task_id == "T3":
        return _t3_insights(workspace, detailed=detailed)
    if task_id == "T3.5":
        return _t35_insights(workspace)
    if task_id.startswith("T3.6"):
        return _survey_insights(workspace, task_id=task_id)
    if task_id == "T4":
        return _t4_insights(workspace, detailed=detailed)
    if task_id == "T4.5":
        return _t45_insights(workspace)
    if task_id.startswith("T5"):
        return _t5_insights(workspace, task_id=task_id)
    if task_id.startswith("T7"):
        return _t7_insights(workspace, task_id=task_id)
    if task_id.startswith("T8"):
        return _t8_insights(workspace, task_id=task_id)
    if task_id == "T9":
        return _t9_insights(workspace)
    return []


def _t1_insights(workspace: Path) -> list[dict[str, Any]]:
    project = _load_json_or_yaml(workspace / "project.yaml")
    bridge = _load_json(workspace / "literature" / "bridge_domain_plan.json")
    topic = _first_text(project, "research_topic", "topic", "title", "research_question")
    bridges = _list_count(bridge, "bridges", "bridge_domains", "domains")
    rows = []
    if topic:
        rows.append(("研究范围", topic))
    rows.append(("跨域计划", f"{bridges} 个已声明 bridge" if bridges is not None else "未声明或为空"))
    return [_insight("范围与输入约束", "T1 已把人工输入转为下游可用的研究边界。", rows)]


def _t2_insights(workspace: Path, *, detailed: bool) -> list[dict[str, Any]]:
    raw = _jsonl(workspace / "literature" / "papers_raw.jsonl")
    dedup = _jsonl(workspace / "literature" / "papers_dedup.jsonl")
    verified = _jsonl(workspace / "literature" / "papers_verified.jsonl")
    backlog = _jsonl(workspace / "literature" / "papers_backlog.jsonl")
    failures = _jsonl(workspace / "literature" / "verification_failures.jsonl")
    queue = _jsonl(workspace / "literature" / "deep_read_queue.jsonl")
    domain = _load_json(workspace / "literature" / "domain_map.json")
    queue_meta = _load_json(workspace / "literature" / "deep_read_queue_meta.json")
    search_log = _read(workspace / "literature" / "search_log.md")
    insights: list[dict[str, Any]] = []
    if raw or dedup or verified:
        dedup_rate = _pct(len(raw) - len(dedup), len(raw)) if raw else None
        rows = [
            ("原始检索命中", str(len(raw))),
            ("去重后候选", str(len(dedup))),
            ("已核验 metadata", str(len(verified))),
            ("Backlog", str(len(backlog))),
            ("核验失败", str(len(failures))),
        ]
        if dedup_rate is not None:
            rows.append(("去重率", dedup_rate))
        insights.append(_insight("检索、去重与核验", "这些数字说明文献池如何从召回结果收敛为可阅读候选；核验失败不等于论文不存在。", rows))
    query_rows = _query_portfolio_rows(raw, detailed=detailed)
    query_rows.extend(_query_audit_rows(search_log, detailed=detailed))
    if query_rows:
        insights.append(_insight("Query Portfolio", "展示持久化 query 的召回、来源与 bucket。词面重叠只用于发现重复风险；没有持久化 merge action 时，不会声称系统已合并某条 query。", _dedupe_rows(query_rows, limit=18 if detailed else 8), kind="table"))
    source_rows = _paper_distribution_rows(verified or dedup, detailed=detailed)
    if source_rows:
        insights.append(_insight("Source, Year & Venue Distribution", "分布来自候选元数据，用于检查检索覆盖与时间/venue 偏置，不代表论文质量。", source_rows, kind="table"))
    score_rows = _top_score_rows(dedup, detailed=detailed)
    if score_rows:
        insights.append(_insight("候选评分与阅读处置", "排名由工具信号和已持久化字段组成，是阅读优先级提示，不是最终学术判断。", score_rows, kind="table"))
    if isinstance(domain, dict):
        rows = []
        for key, label in (("core", "Core"), ("theory_bridge", "Theory bridge"), ("adjacent", "Adjacent"), ("boundary", "Boundary")):
            value = domain.get(key)
            if isinstance(value, list):
                rows.append((label, str(len(value))))
        edges = domain.get("citation_edges")
        if isinstance(edges, list):
            rows.append(("Citation edges", str(len(edges))))
        warnings = domain.get("warnings")
        if isinstance(warnings, list) and warnings:
            rows.append(("图谱警告", "; ".join(str(item) for item in warnings[:3])))
        if rows:
            insights.append(_insight("Citation Graph & Domain Map", "引用结构用于发现语义排序可能漏掉的结构节点，不直接决定论文重要性。", rows))
    hub_rows = _citation_hub_rows(queue, detailed=detailed)
    if hub_rows:
        insights.append(_insight("Citation-structure Priority Hints", "这些论文因 seed 邻居、bridge 或 citation hub 被提升阅读优先级；进入 T3 后仍必须以实际证据卡复核。", hub_rows, kind="table"))
    if queue or isinstance(queue_meta, dict):
        reasons = Counter(_first_text(item, "queue_reason", "disposition", "reading_reason", "queue_bucket") or "未标注" for item in queue)
        rows = [("Deep-read queue", str(len(queue))), *[(reason, str(count)) for reason, count in reasons.most_common(5)]]
        for key in ("deep_read_target", "probe_pool", "protected_count", "bridge_shortfall_count"):
            if isinstance(queue_meta, dict) and queue_meta.get(key) is not None:
                rows.append((key, str(queue_meta[key])))
        insights.append(_insight("Reading Queue", "队列展示进入精读的结构性原因；seed、bridge 或 citation hub 保护必须在后续阅读中接受证据复核。", rows))
    return insights


def _t3_insights(workspace: Path, *, detailed: bool) -> list[dict[str, Any]]:
    notes = sorted((workspace / "literature" / "paper_notes").glob("*.md")) if (workspace / "literature" / "paper_notes").is_dir() else []
    abstract_notes = sorted((workspace / "literature" / "paper_notes_abstract").glob("*.md")) if (workspace / "literature" / "paper_notes_abstract").is_dir() else []
    manifest = _load_json(workspace / "literature" / "notes_manifest.json")
    evidence = Counter()
    mechanism_evidence = Counter()
    tension_count = 0
    boundary_count = 0
    pages_read_total = 0
    pages_total = 0
    extraction_calls = 0
    truncation_resolved = 0
    previews: list[tuple[str, str]] = []
    for path in notes:
        text = _read(path)
        level = _evidence_level(text)
        evidence[level] += 1
        mechanism_type = _note_field(text, "13", "Mechanism Claim", "Evidence type")
        mechanism_evidence[mechanism_type or "未标注"] += 1
        tension_count += int(bool(re.search(r"(?im)^.*(?:tension|张力).*$", text)))
        boundary_count += int(bool(re.search(r"(?im)^.*(?:boundary|边界|limitation|局限).*$", text)))
        pages_read, page_count = _note_page_coverage(text)
        pages_read_total += pages_read
        pages_total += page_count
        extraction_calls += _to_int(_note_field(text, "12", "Reading Coverage", "Extraction calls"))
        truncation_resolved += int(_truncation_is_resolved(_note_field(text, "12", "Reading Coverage", "Truncation")))
        if detailed and len(previews) < 10:
            preview = (
                f"{level}；页码 {pages_read}/{page_count or '?'}；"
                f"抽取 {_to_int(_note_field(text, '12', 'Reading Coverage', 'Extraction calls'))} 次；"
                f"机制证据: {mechanism_type or '未标注'}"
            )
            previews.append((path.stem, preview))
    if not notes and not abstract_notes:
        return []
    rows = [(level, str(count)) for level, count in evidence.most_common()]
    rows.extend([
        ("Abstract-only notes", str(len(abstract_notes))),
        ("页码覆盖", f"{pages_read_total}/{pages_total}" if pages_total else "未在 note 中解析到完整页码"),
        ("Extraction calls", str(extraction_calls)),
        ("Truncation resolved", str(truncation_resolved)),
        ("边界/局限提示", str(boundary_count)),
        ("跨论文张力提示", str(tension_count)),
    ])
    if isinstance(manifest, dict):
        for key, label in (
            ("complete_count", "Manifest complete"),
            ("incomplete_count", "Manifest incomplete"),
            ("missing_count", "Manifest missing"),
            ("invalid_note_file_count", "Invalid note files"),
        ):
            if manifest.get(key) is not None:
                rows.append((label, str(manifest[key])))
    insights = [_insight("Reading & Evidence Coverage", "每篇卡片只允许在其实际阅读覆盖与证据等级范围内支持后续综合和引用。", rows)]
    evidence_rows = [(label, str(count)) for label, count in mechanism_evidence.most_common()]
    if evidence_rows:
        insights.append(_insight("Mechanism Evidence Types", "Mechanism Claim 的 evidence type 来自每张 paper card 的 §13；它描述证据性质，不自动确认机制为真。", evidence_rows[:12 if detailed else 6], kind="table"))
    if previews:
        insights.append(_insight("已完成阅读卡", "Detailed 模式显示最多 10 篇；完整 note 路径保留在 workspace。", previews, kind="table"))
    return insights


def _t35_insights(workspace: Path) -> list[dict[str, Any]]:
    workbench = _load_json(workspace / "literature" / "synthesis_workbench.json")
    if not isinstance(workbench, dict):
        return []
    rows = []
    for key, label in (
        ("method_families", "Method families"),
        ("contribution_space", "Contribution space"),
        ("mechanism_claim_clusters", "Mechanism clusters"),
        ("cross_paper_tensions", "Cross-paper tensions"),
        ("adjacent_transfers", "Adjacent transfers"),
        ("bridge_transfer_drafts", "Bridge transfer drafts"),
        ("weak_evidence", "Weak-evidence items"),
    ):
        value = workbench.get(key)
        if isinstance(value, (list, dict)):
            rows.append((label, str(len(value))))
    insights = [_insight("Synthesis Workbench", "综合工作台将跨论文结构组织为候选证据，不自动把 tool cluster 变成学术结论。", rows)] if rows else []
    contribution_rows = _workbench_distribution_rows(workbench.get("contribution_space"), "contribution")
    if contribution_rows:
        insights.append(_insight("Contribution Space", "贡献空间显示当前语料中已记录的贡献类型和位置；它不能单独证明研究空白。", contribution_rows, kind="table"))
    mechanism_rows = _workbench_mechanism_rows(workbench.get("mechanism_claim_clusters"))
    if mechanism_rows:
        insights.append(_insight("Mechanism Clusters", "cluster 是跨卡片的组织结果；仅当相应 paper card 的 evidence type 足够时，才可作为后续候选的接地材料。", mechanism_rows, kind="table"))
    tension_rows = _workbench_tension_rows(workbench.get("cross_paper_tensions"))
    if tension_rows:
        insights.append(_insight("Cross-paper Tensions & Transfers", "张力和迁移是需要验证的综合线索，不能直接升级为最终理论结论。", tension_rows, kind="table"))
    return insights


def _survey_insights(workspace: Path, *, task_id: str) -> list[dict[str, Any]]:
    plan = _load_json(workspace / "drafts" / "survey" / "survey_plan.json")
    state = _load_json(workspace / "drafts" / "survey" / "survey_state.json")
    visual = _load_json(workspace / "drafts" / "survey" / "figures" / "survey_visual_manifest.json")
    rows = [("当前节点", task_id)]
    for payload, label, keys in (
        (plan, "Taxonomy branches", ("taxonomy", "branches", "themes")),
        (state, "Section states", ("sections", "section_states")),
        (visual, "Survey visuals", ("figures", "items", "artifacts")),
    ):
        count = _list_count(payload, *keys)
        if count is not None:
            rows.append((label, str(count)))
    if isinstance(visual, dict) and str(visual.get("status") or "").lower() == "skipped":
        rows.append(("Visual status", "skipped：数据不足时不生成装饰图"))
    return [_insight("Survey Branch", "Survey 分支独立评估语料与 taxonomy 覆盖，不会替代主线 Idea 流程。", rows)]


def _t4_insights(workspace: Path, *, detailed: bool) -> list[dict[str, Any]]:
    candidates = _load_json(workspace / "ideation" / "_candidate_directions.json")
    pass_one = _load_json(workspace / "ideation" / "_pass1_forward_candidates.json")
    pass_two = _load_json(workspace / "ideation" / "_pass2_grounding_review.json")
    bridge = _load_json(workspace / "ideation" / "bridge_coverage_review.json")
    records = _candidate_records(candidates)
    pass_one_records = _candidate_records(pass_one)
    pass_two_records = _review_records(pass_two)
    rows = []
    if records:
        origin = Counter(str(item.get("idea_origin") or item.get("origin") or item.get("origin_type") or "未标注") for item in records)
        family = Counter(str(item.get("mechanism_family") or item.get("family") or "未标注") for item in records)
        mainline_total = sum(1 for item in records if str(item.get("constraint_status") or "") == "mainline")
        bridge_total = sum(1 for item in records if str(item.get("constraint_status") or "") == "bridge")
        supplement_total = sum(1 for item in records if str(item.get("constraint_status") or "") == "supplement")
        unsupported_total = sum(1 for item in records if str(item.get("constraint_status") or "") == "not_supported_by_current_evidence")
        rows.extend([
            ("候选总数", str(len(records))),
            ("主线 / bridge / supplement", f"{mainline_total} / {bridge_total} / {supplement_total}"),
            ("证据不足但保持可见", str(unsupported_total)),
            *[(f"origin: {key}", str(value)) for key, value in origin.most_common(8)],
        ])
        rows.extend([(f"family: {key}", str(value)) for key, value in family.most_common(5)])
    for payload, label in ((pass_one, "Pass 1"), (pass_two, "Pass 2")):
        count = _list_count(payload, "candidates", "reviews", "items")
        if count is not None:
            rows.append((label, str(count)))
    insights = [_insight("Mainline Candidate Governance", "主线候选来自综合、seed、证据和 bridge；四类 supplement 只用于覆盖检查，不替代主线推理。", rows)] if rows else []
    if pass_one_records or pass_two_records:
        pass_rows = _pass_transition_rows(pass_one_records, pass_two_records)
        if pass_rows:
            insights.append(_insight("Pass 1 -> Pass 2 Grounding", "Pass 2 只改变推荐与风险说明，不应静默删除 Pass 1 候选；prior_art=none 表示高不确定性，而非自动证明新颖。", pass_rows, kind="table"))
    supplement_rows = _supplement_channel_rows(records)
    if supplement_rows:
        insights.append(_insight("Coverage Supplements", "Coverage supplements do not replace mainline reasoning. 每行是可追溯的覆盖检查，不是强制生成 idea 的模板。", supplement_rows, kind="table"))
    cross_domain_rows = _cross_domain_rows(records)
    if cross_domain_rows:
        insights.append(_insight("Cross-domain Sources", "bridge/cross-domain 候选显示持久化的迁移来源和风险状态；类比本身不构成机制证据。", cross_domain_rows, kind="table"))
    if isinstance(bridge, dict):
        reviews = bridge.get("bridge_reviews") or bridge.get("reviews")
        bridge_rows = [("Bridge reviews", str(len(reviews))) if isinstance(reviews, list) else ("Bridge reviews", "未记录")]
        warnings = bridge.get("warnings")
        if isinstance(warnings, list) and warnings:
            bridge_rows.append(("Unsupported / warnings", str(len(warnings))))
        for review in reviews[:8] if isinstance(reviews, list) else []:
            if not isinstance(review, dict):
                continue
            bridge_id = str(review.get("bridge_id") or "bridge")
            escape = review.get("escape_hatch") if isinstance(review.get("escape_hatch"), dict) else {}
            bridge_rows.append((bridge_id, f"{escape.get('status') or 'visible'} · { _short(str(escape.get('reason') or review.get('decision_summary') or ''), 72)}"))
        insights.append(_insight("Bridge Coverage Review", "Bridge 只在证据足够时形成候选；deferred/no_candidate_available 是显式结果，不是失败后被隐藏。", bridge_rows, kind="table"))
    if detailed and records:
        table = []
        for item in records[:12]:
            score = item.get("scores") if isinstance(item.get("scores"), dict) else {}
            hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
            title = _short(_first_text(item, "display_title", "title", "title_short_zh") or "未命名", 52)
            innovation = item.get("innovation") if isinstance(item.get("innovation"), dict) else {}
            table.append((str(item.get("id") or "?"), f"{title} | {item.get('idea_origin') or item.get('origin') or '-'} | H={len(hypotheses)} | 新颖={score.get('novelty', '-')} | 创新={_short(str(innovation.get('type') or '未标注'), 22)}"))
        insights.append(_insight("Candidate Top List", "Detailed 模式显示前 12 个持久化候选。完整的创新、H1/H2/H3、组合关系、证据锚点与评分依据在 ideation/_gate1_candidate_cards.md。", table, kind="table"))
    return insights


def _t45_insights(workspace: Path) -> list[dict[str, Any]]:
    text = _read(workspace / "ideation" / "novelty_audit.md") or _read(workspace / "novelty" / "novelty_audit.md")
    scorecard = _read(workspace / "ideation" / "idea_scorecard.yaml")
    scorecard_data = _load_yaml(workspace / "ideation" / "idea_scorecard.yaml")
    if not text and not scorecard:
        return []
    verdict = _match(text, r"(?im)(?:verdict|判定)\s*[:：]\s*([^\n]+)") or "需阅读审计文件确认"
    baseline_count = len(re.findall(r"(?im)(?:required baseline|必须基线|baseline)\b", text))
    rows = [("Verdict", verdict), ("Baseline mentions", str(baseline_count)), ("Scorecard", "available" if scorecard else "missing")]
    if isinstance(scorecard_data, dict):
        selected = scorecard_data.get("selected_idea") if isinstance(scorecard_data.get("selected_idea"), dict) else scorecard_data
        for key, label in (("collision_axis", "Collision axis"), ("ambition_axis", "Ambition axis"), ("novelty_verdict", "Novelty verdict")):
            if selected.get(key) not in (None, ""):
                rows.append((label, _short(str(selected[key]), 100)))
        cdr = selected.get("cdr_tuple") if isinstance(selected.get("cdr_tuple"), dict) else {}
        if cdr:
            rows.append(("CDR tuple", _short(str(cdr.get("design_rationale") or cdr.get("artifact") or "recorded"), 100)))
    return [_insight("Novelty & Collision Audit", "tuple、collision 与 baseline 是审计约束；它们限制可主张范围，而不是自动证明新颖性。", rows)]


def _t5_insights(workspace: Path, *, task_id: str) -> list[dict[str, Any]]:
    handoff = _load_json(workspace / "external_executor" / "handoff_pack.json")
    report = _load_json(workspace / "external_executor" / "skill_specialization_report.json")
    status = _load_json(workspace / "external_executor" / "executor_status.json")
    rows = [("当前节点", task_id)]
    if isinstance(handoff, dict):
        context = handoff.get("context_reboost") if isinstance(handoff.get("context_reboost"), dict) else handoff
        rows.append(("Central hypothesis", _short(_first_text(context, "central_hypothesis", "project_goal") or "未记录", 110)))
        for key, label in (("required_baselines", "Required baselines"), ("claim_boundaries", "Claim boundaries"), ("minimum_experiment_loop", "Minimum loop")):
            if isinstance(context.get(key), list):
                rows.append((label, str(len(context[key]))))
        baseline_matrix = context.get("baseline_matrix") or handoff.get("baseline_matrix")
        if isinstance(baseline_matrix, list):
            required = sum(1 for item in baseline_matrix if isinstance(item, dict) and bool(item.get("required")))
            rows.append(("Baseline matrix", f"{len(baseline_matrix)} entries; required={required}"))
        claim_matrix = context.get("claim_evidence_matrix") or handoff.get("claim_evidence_matrix")
        if isinstance(claim_matrix, list):
            rows.append(("Claim/evidence matrix", str(len(claim_matrix))))
    if isinstance(report, dict):
        skills = report.get("skills") or report.get("generated_skills")
        if isinstance(skills, (list, dict)):
            rows.append(("Project-specific skills", str(len(skills))))
    if isinstance(status, dict):
        rows.append(("Executor status", str(status.get("status") or status.get("state") or "recorded")))
    return [_insight("External Execution Contract", "T5 重新组织研究意图和执行边界；外部 executor 仍必须回传可审计的原始结果。", rows)]


def _t7_insights(workspace: Path, *, task_id: str) -> list[dict[str, Any]]:
    results = _load_json(workspace / "experiments" / "results_summary.json")
    integrity = _load_json(workspace / "experiments" / "integrity_audit.json")
    claims = _load_json(workspace / "experiments" / "experimental_claims.json")
    mapping = _load_json(workspace / "drafts" / "result_to_claim.json")
    rows = [("当前节点", task_id)]
    for payload, label, keys in (
        (results, "Runs", ("runs", "run_records", "results")),
        (integrity, "Integrity checks", ("checks", "findings", "issues")),
        (claims, "Experimental claims", ("claims", "items")),
        (mapping, "Claim mappings", ("claims", "mappings", "items")),
    ):
        count = _list_count(payload, *keys)
        if count is not None:
            rows.append((label, str(count)))
    if isinstance(integrity, dict):
        rows.append(("Integrity status", str(integrity.get("status") or integrity.get("verdict") or "recorded")))
        baseline = integrity.get("required_baseline_coverage") if isinstance(integrity.get("required_baseline_coverage"), dict) else {}
        if baseline:
            rows.append(("Baseline coverage", str(baseline.get("status") or "unknown")))
            missing = baseline.get("missing_baselines")
            if isinstance(missing, list) and missing:
                rows.append(("Missing baselines", ", ".join(str(item) for item in missing[:6])))
    must_not = _read(workspace / "drafts" / "must_not_claim.md")
    if must_not:
        rows.append(("Must-not-claim", str(len([line for line in must_not.splitlines() if line.lstrip().startswith(("-", "*"))]))))
    insights = [_insight("Experiment Evidence & Claims", "实验结果只有在 run/config/log、完整性和 baseline 条件满足时才可进入 claim 映射。", rows)]
    run_rows = _experiment_run_rows(results)
    if run_rows:
        insights.append(_insight("Run Inventory", "状态来自外部执行器回传；OOM、NaN 或缺失 provenance 需要保留可见，不能被成功结果掩盖。", run_rows, kind="table"))
    claim_rows = _claim_support_rows(mapping, claims)
    if claim_rows:
        insights.append(_insight("Claim Support Levels", "Claim mapping 是允许写作的边界，不是对研究结论强度的自动背书。", claim_rows, kind="table"))
    return insights


def _t8_insights(workspace: Path, *, task_id: str) -> list[dict[str, Any]]:
    index = _load_json(workspace / "drafts" / "manuscript_resource_index.json")
    state = _load_json(workspace / "drafts" / "paper_state.json")
    alignment = _load_json(workspace / "drafts" / "alignment_matrix.json")
    rows = [("当前节点", task_id)]
    for payload, label, keys in ((index, "Indexed resources", ("artifacts", "resources", "items")), (state, "Section states", ("sections", "section_states")), (alignment, "Alignment rows", ("rows", "items"))):
        count = _list_count(payload, *keys)
        if count is not None:
            rows.append((label, str(count)))
    if task_id.startswith("T8-SEC-"):
        section_id = task_id.removeprefix("T8-SEC-").lower()
        section = workspace / "drafts" / "sections" / f"{section_id}.tex"
        rows.append(("Section artifact", "created" if section.exists() else "pending"))
    audit = _load_json(workspace / "drafts" / "paper_claim_audit.json")
    if isinstance(audit, dict):
        rows.append(("Claim audit findings", str(_list_count(audit, "findings", "issues", "checks") or 0)))
    insights = [_insight("Manuscript Evidence Alignment", "Writer 使用资源索引和 alignment matrix 维持可追溯性；章节文字本身仍需要审稿与人工判断。", rows)]
    section_rows = _section_evidence_rows(state, alignment, detailed=task_id.startswith("T8-SEC-"))
    if section_rows:
        insights.append(_insight("Section Evidence Use", "展示已持久化章节状态和 evidence/alignment 记录；Writer 不能把未绑定证据升级为实证事实。", section_rows, kind="table"))
    return insights


def _t9_insights(workspace: Path) -> list[dict[str, Any]]:
    report = _load_json(workspace / "submission" / "compile_report.json")
    manifest = _load_json(workspace / "submission" / "bundle" / "bundle_manifest.json")
    pdf = workspace / "submission" / "bundle" / "main.pdf"
    rows = [("Submission PDF", "available" if pdf.exists() else "missing")]
    if isinstance(report, dict):
        rows.extend([
            ("Compile success", str(report.get("success"))),
            ("Backend", str(report.get("selected_backend") or report.get("engine") or "unknown")),
            ("Attempts", str(len(report.get("attempts") or []))),
            ("PDF hash", _short(str(report.get("pdf_sha256") or "not recorded"), 24)),
        ])
        warnings = report.get("warnings") or report.get("errors")
        if isinstance(warnings, list):
            rows.append(("Warnings / errors", str(len(warnings))))
        current_pdf = report.get("pdf_path") or report.get("output_pdf")
        if current_pdf:
            rows.append(("Compiled PDF path", _short(str(current_pdf), 80)))
    if isinstance(manifest, dict):
        rows.append(("Bundle fingerprint", _short(_first_text(manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}, "main_tex_sha256") or "recorded", 24)))
    audit = _load_json(workspace / "drafts" / "paper_claim_audit.json")
    if isinstance(audit, dict):
        rows.append(("Current claim audit", str(audit.get("status") or audit.get("verdict") or "available")))
    return [_insight("Submission Bundle & Compilation", "T9 接受的是当前 source、PDF、log 和依赖 fingerprint 一致的真实编译结果；编译失败绝不能由旧 PDF 掩盖。", rows)]


def _query_portfolio_rows(records: list[dict[str, Any]], *, detailed: bool) -> list[tuple[str, str]]:
    values = Counter()
    for record in records:
        bucket = _first_text(record, "query_bucket", "search_bucket", "retrieval_intent") or "未标注"
        source = _first_text(record, "source", "source_tool") or "unknown"
        values[(bucket, source)] += 1
    limit = 12 if detailed else 6
    return [(f"{bucket} / {source}", str(count)) for (bucket, source), count in values.most_common(limit)]


def _query_audit_rows(text: str, *, detailed: bool) -> list[tuple[str, str]]:
    """Render persisted query records and a bounded lexical-overlap warning.

    T2 does not persist a semantic similarity model or merge-decision artifact.
    We therefore calculate only a transparent token-overlap hint from the
    persisted query log and never describe it as a completed merge decision.
    """

    rows = _markdown_table_rows(text, heading_contains=("检索式", "query"))
    if not rows:
        return []
    result: list[tuple[str, str]] = []
    queries: list[str] = []
    limit = 10 if detailed else 5
    for index, row in enumerate(rows[:limit], start=1):
        # Expected columns: #, Query, Bucket, Bridge, Tool/Source, Calls,
        # Results, Persisted. Older logs may contain fewer fields.
        query = row[1] if len(row) > 1 else row[0]
        bucket = row[2] if len(row) > 2 else "未标注"
        source = row[4] if len(row) > 4 else "unknown"
        result_count = row[6] if len(row) > 6 else "?"
        persisted = row[7] if len(row) > 7 else "?"
        result.append((f"q{index} · {_short(query, 44)}", f"bucket={bucket}; source={source}; results={result_count}; persisted={persisted}"))
        queries.append(query)
    overlap = _top_query_overlap(queries)
    if overlap is not None:
        left, right, score = overlap
        result.append(("词面重叠提示", f"q{left} / q{right}: {score:.2f}；这是重复风险提示，未持久化 merge action。"))
    return result


def _paper_distribution_rows(records: list[dict[str, Any]], *, detailed: bool) -> list[tuple[str, str]]:
    sources = Counter()
    years = Counter()
    venues = Counter()
    for record in records:
        sources[_first_text(record, "source", "source_tool", "retrieval_source", "provider") or "unknown"] += 1
        year = _first_text(record, "year", "publication_year", "published_year")
        if year:
            years[year] += 1
        venue = _first_text(record, "venue", "journal", "conference", "primary_location")
        if venue:
            venues[venue] += 1
    rows: list[tuple[str, str]] = []
    per_kind = 5 if detailed else 3
    rows.extend((f"source: {_short(name, 30)}", str(count)) for name, count in sources.most_common(per_kind))
    rows.extend((f"year: {year}", str(count)) for year, count in years.most_common(per_kind))
    rows.extend((f"venue: {_short(venue, 30)}", str(count)) for venue, count in venues.most_common(per_kind))
    return rows


def _citation_hub_rows(queue: list[dict[str, Any]], *, detailed: bool) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in queue:
        if not (item.get("is_citation_hub") or item.get("protected_slot") or item.get("citation_hub_protected_slot")):
            continue
        title = _short(_first_text(item, "title", "paper_id", "canonical_id") or "未命名论文", 50)
        reason = _first_text(item, "queue_reason", "read_disposition_reason") or "protected priority"
        role = _first_text(item, "hub_type", "semantic_role") or "hub/protected"
        rows.append((title, f"{role}; {reason}"))
    return rows[:12 if detailed else 5]


def _markdown_table_rows(text: str, *, heading_contains: tuple[str, ...]) -> list[list[str]]:
    if not text:
        return []
    wanted = tuple(item.casefold() for item in heading_contains)
    active = False
    header_seen = False
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().casefold()
            if active:
                break
            active = any(item in title for item in wanted)
            header_seen = False
            continue
        if not active or not stripped.startswith("|"):
            continue
        values = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not header_seen:
            header_seen = True
            continue
        if values and all(re.fullmatch(r":?-{3,}:?", value.replace(" ", "")) for value in values):
            continue
        if values:
            rows.append(values)
    return rows


def _top_query_overlap(queries: list[str]) -> tuple[int, int, float] | None:
    best: tuple[int, int, float] | None = None
    token_sets = [set(re.findall(r"[A-Za-z0-9]+", value.casefold())) for value in queries]
    for left, lhs in enumerate(token_sets):
        if not lhs:
            continue
        for right in range(left + 1, len(token_sets)):
            rhs = token_sets[right]
            if not rhs:
                continue
            score = len(lhs & rhs) / len(lhs | rhs)
            if best is None or score > best[2]:
                best = (left + 1, right + 1, score)
    return best if best is not None and best[2] >= 0.6 else None


def _dedupe_rows(rows: list[tuple[str, str]], *, limit: int) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for row in rows:
        if row in seen:
            continue
        seen.add(row)
        unique.append(row)
        if len(unique) >= limit:
            break
    return unique


def _top_score_rows(records: list[dict[str, Any]], *, detailed: bool) -> list[tuple[str, str]]:
    candidates = []
    for record in records:
        score = record.get("final_score", record.get("score", record.get("relevance_score")))
        try:
            numeric = float(score)
        except (TypeError, ValueError):
            continue
        title = _first_text(record, "title") or "未命名论文"
        disposition = _first_text(record, "disposition", "reading_disposition", "queue_bucket") or "candidate"
        candidates.append((numeric, title, disposition))
    candidates.sort(reverse=True)
    limit = 12 if detailed else 5
    return [(f"{index}. {_short(title, 54)}", f"score {score:.3f} · {disposition}") for index, (score, title, disposition) in enumerate(candidates[:limit], start=1)]


def _note_section(text: str, number: str, title: str) -> str:
    match = re.search(
        rf"(?ims)^##\s*{re.escape(number)}\.\s*{re.escape(title)}\s*(.*?)(?=^##\s+\d+\.|\Z)",
        text,
    )
    return match.group(1) if match else ""


def _note_field(text: str, number: str, title: str, field: str) -> str:
    section = _note_section(text, number, title)
    if not section:
        return ""
    pattern = rf"(?im)^\s*-\s*\*\*{re.escape(field)}\*\*\s*:\s*(.+)$"
    match = re.search(pattern, section)
    return match.group(1).strip() if match else ""


def _note_page_coverage(text: str) -> tuple[int, int]:
    value = _note_field(text, "12", "Reading Coverage", "Pages read")
    if not value:
        return 0, 0
    total_match = re.search(r"/\s*(\d+)\b", value)
    total = _to_int(total_match.group(1)) if total_match else 0
    ranges = re.findall(r"(\d+)\s*(?:-|--|to|至)\s*(\d+)", value, flags=re.IGNORECASE)
    if ranges:
        read = sum(max(0, _to_int(end) - _to_int(start) + 1) for start, end in ranges)
    else:
        pages = [int(item) for item in re.findall(r"\b\d+\b", value)]
        read = 1 if pages else 0
    return read, total


def _truncation_is_resolved(value: str) -> bool:
    lowered = value.casefold()
    return bool(value) and not any(marker in lowered for marker in ("still truncated", "仍截断", "未解决", "yes"))


def _to_int(value: Any) -> int:
    try:
        match = re.search(r"-?\d+", str(value))
        return int(match.group(0)) if match else 0
    except (TypeError, ValueError):
        return 0


def _workbench_distribution_rows(value: Any, field: str) -> list[tuple[str, str]]:
    entries = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    counter = Counter()
    for item in entries:
        if isinstance(item, dict):
            label = _first_text(item, "contribution_type", "type", "label", "name", field) or "未标注"
        else:
            label = str(item or "未标注")
        counter[label] += 1
    return [(label, str(count)) for label, count in counter.most_common(8)]


def _workbench_mechanism_rows(value: Any) -> list[tuple[str, str]]:
    entries = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    rows: list[tuple[str, str]] = []
    for index, item in enumerate(entries, start=1):
        if not isinstance(item, dict):
            continue
        label = _first_text(item, "mechanism", "label", "cluster_label", "name") or f"cluster {index}"
        papers = item.get("papers") or item.get("supporting_papers") or item.get("items")
        evidence = item.get("evidence_type") or item.get("evidence_types")
        paper_count = len(papers) if isinstance(papers, (list, dict)) else 0
        detail = f"papers={paper_count}" if paper_count else "paper count 未记录"
        if evidence:
            detail += f"; evidence={_short(str(evidence), 46)}"
        rows.append((_short(label, 52), detail))
    return rows[:8]


def _workbench_tension_rows(value: Any) -> list[tuple[str, str]]:
    entries = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    rows: list[tuple[str, str]] = []
    for index, item in enumerate(entries, start=1):
        if isinstance(item, dict):
            label = _first_text(item, "title", "tension", "label", "id") or f"tension {index}"
            detail = _first_text(item, "summary", "description", "why_it_matters", "transfer") or "详情见 synthesis_workbench.json"
        else:
            label, detail = f"tension {index}", str(item)
        rows.append((_short(label, 52), _short(detail, 92)))
    return rows[:8]


def _review_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("reviews", "items", "candidates"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _pass_transition_rows(pass_one: list[dict[str, Any]], pass_two: list[dict[str, Any]]) -> list[tuple[str, str]]:
    ids_one = {str(item.get("id") or item.get("idea_id") or "") for item in pass_one}
    ids_one.discard("")
    by_id = {str(item.get("idea_id") or item.get("id") or ""): item for item in pass_two}
    missing = sorted(identifier for identifier in ids_one if identifier not in by_id)
    recommendations = Counter(_first_text(item, "screening_recommendation", "recommendation") or "未标注" for item in pass_two)
    routine = sum(
        1
        for item in pass_two
        if isinstance(item.get("contribution_check"), dict) and bool(item["contribution_check"].get("routine_risk"))
    )
    uncertain = sum(
        1
        for item in pass_two
        if isinstance(item.get("novelty_check"), dict) and item["novelty_check"].get("prior_art") == "none"
    )
    rows = [("Pass1 candidates", str(len(ids_one))), ("Pass2 reviews", str(len(pass_two)))]
    rows.extend((f"recommendation: {key}", str(value)) for key, value in recommendations.most_common())
    rows.append(("routine-risk flags", str(routine)))
    rows.append(("prior_art=none", f"{uncertain}（高不确定性，不是新颖性证明）"))
    rows.append(("Pass1 IDs absent from Pass2", ", ".join(missing[:6]) if missing else "0；未静默删除"))
    return rows


def _supplement_channel_rows(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    channels = (
        ("mechanism_challenge", "机制质疑", "mechanism_claim_clusters / paper note §13"),
        ("reverse_operation", "反向操作", "comparison table / paper note §2 / design rationale"),
        ("subgroup_failure", "子群失败", "paper note §5 / boundary conditions / comparison table"),
        ("missing_area_exploration", "缺口探索", "missing_areas.md / retrieval coverage hints"),
    )
    rows: list[tuple[str, str]] = []
    for origin, label, sources in channels:
        matching = [
            item
            for item in records
            if str(item.get("idea_origin") or item.get("origin") or "") == origin
        ]
        if not matching:
            rows.append((label, f"evidence sources checked: {sources}; durable channel result 未记录。"))
            continue
        ids = ", ".join(str(item.get("id") or "?") for item in matching)
        unsupported = [item for item in matching if str(item.get("constraint_status") or "") == "not_supported_by_current_evidence"]
        if unsupported:
            reason = _short(str(unsupported[0].get("basis_summary") or unsupported[0].get("selection_warning") or "当前证据不足"), 74)
            rows.append((label, f"candidate={ids}; unsupported: {reason}; artifact=_candidate_directions.json"))
        else:
            evidence_count = sum(len(item.get("basis_sources") or []) for item in matching if isinstance(item.get("basis_sources"), list))
            rows.append((label, f"candidate={ids}; evidence anchors={evidence_count}; artifact=_candidate_directions.json"))
    return rows


def _cross_domain_rows(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in records:
        origin = str(item.get("idea_origin") or item.get("origin") or "")
        sources = item.get("cross_domain_sources") if isinstance(item.get("cross_domain_sources"), list) else []
        if origin not in {"cross_domain_analogy", "bridge_synthesis"} and not sources:
            continue
        title = _short(_first_text(item, "display_title", "title") or str(item.get("id") or "candidate"), 46)
        relation = str(item.get("cross_domain_relation") or "未记录")
        status = str(item.get("constraint_status") or "未记录")
        source_text = ", ".join(str(value) for value in sources[:4]) or "未记录具体 bridge"
        rows.append((title, f"origin={origin}; bridge={source_text}; relation={relation}; status={status}"))
    return rows[:8]


def _experiment_run_rows(results: Any) -> list[tuple[str, str]]:
    runs = []
    if isinstance(results, dict):
        runs = results.get("experiment_runs") or results.get("runs") or results.get("run_records") or results.get("results") or []
    if not isinstance(runs, list):
        return []
    status = Counter()
    oom = 0
    nan = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        value = _first_text(run, "status", "state") or "unknown"
        status[value] += 1
        joined = " ".join(str(run.get(key) or "") for key in ("status", "error", "error_message", "failure_reason")).casefold()
        oom += int("oom" in joined or "out of memory" in joined)
        nan += int("nan" in joined)
    rows = [(f"run status: {key}", str(value)) for key, value in status.most_common(8)]
    if oom:
        rows.append(("OOM", str(oom)))
    if nan:
        rows.append(("NaN", str(nan)))
    return rows


def _claim_support_rows(mapping: Any, claims: Any) -> list[tuple[str, str]]:
    entries = []
    for payload in (mapping, claims):
        if isinstance(payload, dict):
            value = payload.get("claims") or payload.get("mappings") or payload.get("items") or []
            if isinstance(value, list):
                entries.extend(item for item in value if isinstance(item, dict))
    levels = Counter()
    for item in entries:
        level = _first_text(item, "support_level", "evidence_level", "status", "disposition") or "未标注"
        levels[level] += 1
    return [(f"{level}", str(count)) for level, count in levels.most_common(10)]


def _section_evidence_rows(state: Any, alignment: Any, *, detailed: bool) -> list[tuple[str, str]]:
    entries: list[tuple[str, Any]] = []
    if isinstance(state, dict):
        sections = state.get("sections") or state.get("section_states") or []
        if isinstance(sections, dict):
            entries = list(sections.items())
        elif isinstance(sections, list):
            entries = [(str(item.get("section_id") or item.get("id") or index), item) for index, item in enumerate(sections, start=1) if isinstance(item, dict)]
    rows: list[tuple[str, str]] = []
    for section_id, section in entries[:12 if detailed else 6]:
        if isinstance(section, dict):
            status = _first_text(section, "status", "state") or "未记录"
            evidence = section.get("evidence_ids") or section.get("evidence") or section.get("source_artifacts") or []
            count = len(evidence) if isinstance(evidence, (list, dict)) else 0
            rows.append((_short(section_id, 36), f"status={status}; bound evidence={count}"))
    if not rows and isinstance(alignment, dict):
        count = _list_count(alignment, "rows", "items")
        if count is not None:
            rows.append(("alignment_matrix", f"{count} 条章节/证据对齐记录；详情见 Artifact。"))
    return rows


def _candidate_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "directions", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _insight(title: str, explanation: str, rows: list[tuple[str, str]], *, kind: str = "metrics") -> dict[str, Any]:
    return {"title": title, "explanation": explanation, "rows": [(str(left), str(right)) for left, right in rows if left or right], "kind": kind}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(item)
    except (OSError, json.JSONDecodeError):
        return records
    return records


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except OSError:
        return ""


def _first_text(data: Any, *keys: str) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _list_count(data: Any, *keys: str) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if isinstance(value, (list, dict)):
            return len(value)
    return None


def _pct(numerator: int, denominator: int) -> str | None:
    return f"{(100 * numerator / denominator):.1f}%" if denominator else None


def _short(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(1, limit - 3)] + "..."


def _match(text: str, pattern: str) -> str:
    found = re.search(pattern, text)
    return found.group(1).strip() if found else ""


def _evidence_level(text: str) -> str:
    for level in ("FULL-TEXT", "PARTIAL-TEXT", "ABSTRACT-ONLY", "METADATA-ONLY"):
        if level in text:
            return level
    return "未标注"


def _field_hint(text: str, label: str) -> str:
    match = re.search(rf"(?im)^\s*(?:#+\s*)?{re.escape(label)}\s*[:：]\s*(.+)$", text)
    return _short(match.group(1), 56) if match else ""
