"""T9 Submission Agent — 投稿准备

将论文迁移到目标会议格式，匿名化检查，编译验证，打包。
输入: drafts/paper.tex, project.yaml
输出: submission/bundle/, submission/migration_report.md
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec, get_agent_params
from ..runtime.prompts import render_prompt
from ..tools.docker_exec import check_docker_environment, get_default_image, load_project_config
from ..tools.latex_compile import _compile_dependency_fingerprint
from ..tools.manuscript import extract_bibliography_stems
from ._common import load_project, prepend_resume_prefix, read_text_file
from .writer import _validate_paper_claim_audit_if_needed


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, f"{path.name} 不存在"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"{path.name} JSON 无效: {exc}"
    if not isinstance(data, dict):
        return None, f"{path.name} 顶层必须是对象"
    return data, None


def _migration_report_declares_current_compile_success(report_text: str) -> bool:
    """Only accept the current summary line, not historical attempt prose."""

    for line in report_text.splitlines():
        stripped = line.strip()
        if re.match(r"^-?\s*编译状态[:：]\s*成功\s*$", stripped):
            return True
    return False


def check_anonymization(ctx: ExecutionContext) -> tuple[bool, str | None]:
    """Pre-hook: 检查论文匿名化。"""
    paper_path = ctx.workspace_dir / "submission" / "bundle" / "main.tex"
    if not paper_path.exists():
        paper_path = ctx.workspace_dir / "drafts" / "paper.tex"

    if not paper_path.exists():
        return True, None

    paper_text = paper_path.read_text(encoding="utf-8")

    # 匿名化检查模式
    PATTERNS = {
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "github": r"github\.com/[a-zA-Z0-9_-]+",
        "url": r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "acknowledgments": r"\\section\*?\{Acknowledgments?\}|\\section\*?\{致谢\}",
    }

    issues = []

    for name, pattern in PATTERNS.items():
        matches = re.findall(pattern, paper_text, re.IGNORECASE)
        if matches:
            issues.append(f"{name}: {matches[:3]}")  # 只显示前3个

    if issues:
        return False, f"匿名化检查失败:\n" + "\n".join(issues)

    return True, None


def check_submission_compile_environment(ctx: ExecutionContext) -> tuple[bool, str | None]:
    """Pre-hook: ensure T9 has either native TeX or Docker before LLM work."""

    if shutil.which("latexmk"):
        return True, None

    project_config = load_project_config(ctx.workspace_dir)
    ok, err, details = check_docker_environment(
        project_config=project_config,
        image=get_default_image(),
        require_gpu=False,
    )
    if not ok:
        ctx.extra["environment_blocker"] = details
        return False, (
            (err or "WAITING_ENVIRONMENT: LaTeX 编译环境不可用")
            + " T9 需要本机 latexmk 或可用的 ResearchOS Docker 统一镜像；"
            "请安装 TeX Live/latexmk 或构建 Docker 镜像后 resume。"
        )
    return True, None


class SubmissionAgent(Agent):
    """投稿准备Agent，处理模板迁移、匿名化检查、编译验证。"""

    def __init__(self):
        params = get_agent_params("submission")
        self._params = params
        # 匿名化前置检查改为显式开关，便于本地调试/非匿名投稿场景按需关闭。
        enforce_anonymization_precheck = bool(
            params.get("enforce_anonymization_precheck", False)
        )
        super().__init__(
            build_agent_spec(
                "submission",
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "list_files",
                        "bash_run",
                        "docker_exec",
                        "latex_compile",
                        "prepare_submission_bundle",
                        "finish_task",
                    ],
                    "max_steps": 40,
                    "max_tokens_total": 80_000,
                    "max_wall_seconds": 300,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    "allowed_read_prefixes": ["", "drafts/", "literature/", "experiments/"],
                    "allowed_write_prefixes": ["submission/"],
                    "prompt_template": "submission.j2",
                    "pre_hooks": [check_submission_compile_environment]
                    + ([check_anonymization] if enforce_anonymization_precheck else []),
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt。"""
        project = load_project(ctx)
        target_venue = project.get("target_venue", "neurips2026")
        # 编译重试上限用于指导 T9 在“诊断-修复-重试”循环里及时收敛。
        max_compile_attempts = int(self._params.get("max_compile_attempts", 4))

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            target_venue=target_venue,
            max_compile_attempts=max_compile_attempts,
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """生成投稿任务消息。"""
        project = load_project(ctx)
        target_venue = project.get("target_venue", "neurips")

        return prepend_resume_prefix(
            ctx,
            (
            f"请执行 T9 Submission Agent。\n\n"
            f"将 drafts/paper.tex 迁移到 {target_venue} 会议格式，"
            "执行匿名化检查，验证LaTeX编译，生成投稿包。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验投稿包。"""
        ws = ctx.workspace_dir

        # 检查bundle目录存在
        bundle_dir = ws / "submission" / "bundle"
        if not bundle_dir.exists():
            return False, "submission/bundle/ 目录不存在"

        # 检查必需文件
        required_files = ["main.tex", "references.bib"]
        missing = [f for f in required_files if not (bundle_dir / f).exists()]
        if missing:
            return False, f"bundle缺少必需文件: {missing}"

        ok, err = _validate_bundle_manifest(ws)
        if not ok:
            return False, err

        # 编译成功后必须留下 PDF，避免“只写报告不真正编译通过”的假成功。
        main_tex = bundle_dir / "main.tex"
        bibliography_stems = extract_bibliography_stems(read_text_file(main_tex))
        if bibliography_stems:
            missing_bib = [f"{stem}.bib" for stem in bibliography_stems if not (bundle_dir / f"{stem}.bib").exists()]
            if missing_bib:
                return False, f"main.tex 引用的 BibTeX 文件不在 bundle 中: {missing_bib}"
            if "references" not in bibliography_stems:
                return False, "main.tex 应使用 bundle 内 references.bib：请调用 prepare_submission_bundle 重写 bibliography"
        pdf_path = bundle_dir / "main.pdf"
        if not pdf_path.exists():
            return False, "bundle缺少 main.pdf，说明投稿包尚未编译成功"
        pdf_bytes = pdf_path.read_bytes()[:8]
        if not pdf_bytes.startswith(b"%PDF"):
            return False, "main.pdf 不是有效 PDF（缺少 %PDF 文件头）"
        if pdf_path.stat().st_size < 20:
            return False, "main.pdf 文件过小，疑似占位文件"

        if pdf_path.stat().st_mtime < main_tex.stat().st_mtime:
            return False, "main.pdf 早于 main.tex，需重新编译投稿包"

        log_path = bundle_dir / "main.log"
        if not log_path.exists():
            return False, "submission/bundle/main.log 不存在，缺少真实 LaTeX 编译日志证据"

        compile_report_path = ws / "submission" / "compile_report.json"
        compile_report, compile_report_err = _load_json(compile_report_path)
        if compile_report_err:
            return False, f"submission/compile_report.json 校验失败: {compile_report_err}"
        ok, err = _validate_compile_report(compile_report or {}, ws)
        if not ok:
            return False, err

        # 检查migration_report.md
        report_path = ws / "submission" / "migration_report.md"
        if not report_path.exists():
            return False, "migration_report.md 不存在"

        report_text = read_text_file(report_path)
        if len(report_text) < 100:
            return False, f"migration_report.md 过短({len(report_text)}字符)"

        # 检查报告包含关键内容
        required_content = ["迁移状态", "编译状态", "匿名化检查"]
        for content in required_content:
            if content not in report_text:
                return False, f"migration_report.md 缺少: {content}"

        ok, err = _validate_evidence_audit_trace(report_text, ws)
        if not ok:
            return False, err
        ok, err = _validate_paper_claim_audit_if_needed(ws)
        if not ok:
            return False, err

        # 报告必须明确声明编译成功，避免把失败尝试误判为通过。
        if not _migration_report_declares_current_compile_success(report_text):
            return False, "migration_report.md 未声明“编译状态: 成功”"

        ok, err = _validate_latex_log(log_path)
        if not ok:
            return False, err

        return True, None


def _validate_evidence_audit_trace(report_text: str, ws: Path) -> tuple[bool, str | None]:
    evidence_files = [
        "drafts/paper_claim_audit.md",
        "drafts/paper_claim_audit.json",
        "drafts/result_to_claim.json",
        "drafts/experiment_evidence_pack.json",
    ]
    existing = [rel for rel in evidence_files if (ws / rel).exists()]
    if not existing:
        return True, None
    lowered = report_text.lower()
    for rel in existing:
        filename = Path(rel).name.lower()
        if rel.lower() not in lowered and filename not in lowered:
            return False, f"migration_report.md 必须记录 evidence audit artifact: {rel}"
    return True, None


def _validate_bundle_manifest(ws: Path) -> tuple[bool, str | None]:
    manifest_path = ws / "submission" / "bundle" / "bundle_manifest.json"
    manifest, manifest_err = _load_json(manifest_path)
    if manifest_err:
        return False, f"submission/bundle/bundle_manifest.json 校验失败: {manifest_err}"
    if (manifest or {}).get("semantics") != "submission_bundle_source_fingerprint":
        return False, "submission/bundle/bundle_manifest.json semantics 不正确"

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    for label, rel_key, hash_key in (
        ("源论文", "paper_path", "paper_sha256"),
        ("源参考文献", "bib_path", "bib_sha256"),
    ):
        rel = str(source.get(rel_key) or "").strip()
        expected_hash = str(source.get(hash_key) or "").strip()
        if not rel or not expected_hash:
            return False, f"bundle_manifest.source 缺少 {rel_key}/{hash_key}"
        path = ws / rel
        if not path.exists():
            return False, f"bundle_manifest 指向的{label}不存在: {rel}"
        if _sha256_file(path) != expected_hash:
            return False, f"bundle_manifest 中的{label} hash 与当前文件不一致，需重新准备投稿包"

    for label, rel_key, hash_key in (
        ("bundle main.tex", "main_tex_path", "main_tex_sha256"),
        ("bundle references.bib", "references_bib_path", "references_bib_sha256"),
    ):
        rel = str(bundle.get(rel_key) or "").strip()
        expected_hash = str(bundle.get(hash_key) or "").strip()
        if not rel or not expected_hash:
            return False, f"bundle_manifest.bundle 缺少 {rel_key}/{hash_key}"
        path = ws / rel
        if not path.exists():
            return False, f"bundle_manifest 指向的 {label} 不存在: {rel}"
        if _sha256_file(path) != expected_hash:
            return False, f"bundle_manifest 中的 {label} hash 与当前文件不一致"

    figures = bundle.get("copied_figures") or []
    if isinstance(figures, list):
        for item in figures:
            if not isinstance(item, dict):
                continue
            source_rel = str(item.get("source_path") or "").strip()
            source_hash = str(item.get("source_sha256") or "").strip()
            if source_rel and source_hash:
                source_path = ws / source_rel
                if not source_path.exists():
                    return False, f"bundle_manifest 指向的 source figure 不存在: {source_rel}"
                if _sha256_file(source_path) != source_hash:
                    return False, f"bundle_manifest 中的 source figure hash 与当前文件不一致: {source_rel}"
            rel = str(item.get("dest_path") or item.get("path") or "").strip()
            expected_hash = str(item.get("dest_sha256") or item.get("sha256") or "").strip()
            if not rel or not expected_hash:
                continue
            path = ws / rel
            if not path.exists():
                return False, f"bundle_manifest 指向的 figure 不存在: {rel}"
            if _sha256_file(path) != expected_hash:
                return False, f"bundle_manifest 中的 figure hash 与当前文件不一致: {rel}"
    return True, None


def _validate_compile_report(report: dict, ws: Path) -> tuple[bool, str | None]:
    if report.get("semantics") != "latex_compile_attempt_report":
        return False, "submission/compile_report.json semantics 不正确"
    if report.get("success") is not True:
        return False, "submission/compile_report.json 未记录最终编译成功"
    if report.get("tex_path") != "submission/bundle/main.tex":
        return False, "compile_report.tex_path 必须是 submission/bundle/main.tex"
    if report.get("pdf_path") != "submission/bundle/main.pdf":
        return False, "compile_report.pdf_path 必须是 submission/bundle/main.pdf"
    if report.get("log_path") != "submission/bundle/main.log":
        return False, "compile_report.log_path 必须是 submission/bundle/main.log"

    attempts = report.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return False, "compile_report.attempts 缺失"
    last_attempt = attempts[-1]
    if not isinstance(last_attempt, dict) or last_attempt.get("success") is not True:
        return False, "compile_report 最后一次 attempt 未成功"
    if last_attempt.get("exit_code") not in (0, None):
        return False, "compile_report 最后一次 attempt exit_code 非 0"

    main_tex = ws / "submission" / "bundle" / "main.tex"
    main_pdf = ws / "submission" / "bundle" / "main.pdf"
    main_log = ws / "submission" / "bundle" / "main.log"
    for path in [main_tex, main_pdf, main_log]:
        if not path.exists():
            return False, f"compile_report 指向的文件不存在: {path.relative_to(ws).as_posix()}"

    if report.get("main_tex_sha256") != _sha256_file(main_tex):
        return False, "compile_report.main_tex_sha256 与当前 main.tex 不一致，需重新编译"
    dependency = _compile_dependency_fingerprint(ws, main_tex)
    report_dependency = report.get("dependency_fingerprint") if isinstance(report.get("dependency_fingerprint"), dict) else {}
    report_dependency_hash = str(report_dependency.get("hash") or report.get("dependency_fingerprint_hash") or "").strip()
    if not report_dependency_hash:
        return False, "compile_report.dependency_fingerprint 缺失，需重新编译"
    if report_dependency_hash != dependency.get("hash"):
        return False, "compile_report.dependency_fingerprint 与当前 bundle 依赖不一致，需重新编译"
    attempt_dependency_hash = str(last_attempt.get("dependency_fingerprint_hash") or "").strip()
    if attempt_dependency_hash and attempt_dependency_hash != dependency.get("hash"):
        return False, "compile_report 最后一次 attempt 的 dependency_fingerprint_hash 过期，需重新编译"
    if report.get("pdf_sha256") != _sha256_file(main_pdf):
        return False, "compile_report.pdf_sha256 与当前 main.pdf 不一致"
    log_hash = report.get("log_sha256")
    if log_hash and log_hash != _sha256_file(main_log):
        return False, "compile_report.log_sha256 与当前 main.log 不一致"

    if float(report.get("pdf_mtime") or 0) < main_tex.stat().st_mtime:
        return False, "compile_report.pdf_mtime 早于当前 main.tex，需重新编译"
    if float(report.get("log_mtime") or 0) < main_tex.stat().st_mtime:
        return False, "compile_report.log_mtime 早于当前 main.tex，需重新编译"
    if int(report.get("pdf_size") or 0) != main_pdf.stat().st_size:
        return False, "compile_report.pdf_size 与当前 main.pdf 不一致"
    if int(report.get("log_size") or 0) and int(report.get("log_size") or 0) != main_log.stat().st_size:
        return False, "compile_report.log_size 与当前 main.log 不一致"
    return True, None


def _validate_latex_log(log_path: Path) -> tuple[bool, str | None]:
    log_text = read_text_file(log_path)
    fatal_markers = [
        "Fatal error occurred",
        "! Emergency stop.",
        "==> Fatal error occurred",
        "LaTeX Warning: There were undefined references",
        "LaTeX Warning: Citation `",
        "Citation `",
        "Reference `",
        "undefined citations",
        "Undefined control sequence",
    ]
    for marker in fatal_markers:
        if marker in log_text:
            return False, f"main.log 仍包含致命编译错误: {marker}"
    return True, None
